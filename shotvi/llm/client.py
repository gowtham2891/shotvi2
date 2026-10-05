"""LLM client: structured JSON output, retries, response cache, per-run token/cost ledger.

Usage::

    ledger = Ledger(max_usd=1.5)
    llm = LLMClient(ledger, cache_dir="runs/cache")
    obj = llm.generate_json("scorer", ModelSpec("gemini", "gemini-3.8-flash", {"thinking_level": "low"}),
                            system=..., prompt=..., schema=...)

Cache key = sha256 of (provider, model, params, system, prompt, schema). Only responses that parse and validate are
cached, so an eval rerun with unchanged prompts costs nothing. The ledger records every call with its *full* cost
(what an uncached production run pays) and the billed cost (0 on a cache hit).
"""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from pathlib import Path

from .base import BudgetExceeded, LLMError, ModelSpec, Provider, RawResponse, RetryableError
from .pricing import SEARCH_USD_PER_QUERY, cost_usd

try:
    import jsonschema
except ImportError:  # pragma: no cover - jsonschema is in requirements.txt
    jsonschema = None


def _load_provider(name: str) -> Provider:
    if name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider()
    raise LLMError(f"unknown LLM provider {name!r} (implemented: gemini; add others in shotvi/llm/)")


def _media_key(m: dict) -> dict:
    """Cache identity of one media part: everything but the expiring upload uri and the inline bytes. Inline bytes
    without a caller-supplied "key" enter by their hash, so two different snippets never share a cache entry."""
    k = {a: b for a, b in m.items() if a not in ("file_uri", "data")}
    if "data" in m and "key" not in m:
        k["data_sha"] = hashlib.sha256(str(m["data"]).encode()).hexdigest()
    return k


PROVIDERS: dict[str, Provider] = {}
_prov_lock = threading.Lock()


def get_provider(name: str) -> Provider:
    with _prov_lock:
        if name not in PROVIDERS:
            PROVIDERS[name] = _load_provider(name)
        return PROVIDERS[name]


class Ledger:
    """Thread-safe per-run token and cost accounting with an optional spend cap (on billed USD)."""

    def __init__(self, max_usd: float | None = None, price_overrides: dict | None = None):
        self.max_usd = max_usd
        self.price_overrides = price_overrides
        self.calls: list[dict] = []
        self._lock = threading.Lock()
        self.t0 = time.time()

    def billed_usd(self) -> float:
        with self._lock:
            return sum(c["usd_billed"] for c in self.calls)

    def check_budget(self):
        if self.max_usd is not None and self.billed_usd() >= self.max_usd:
            raise BudgetExceeded(f"run spend cap ${self.max_usd:.2f} reached")

    def record(self, role: str, spec: ModelSpec, raw: RawResponse, cached: bool, latency: float, ok: bool = True):
        out_billable = raw.output_tokens + raw.thought_tokens
        usd = cost_usd(spec.model, raw.input_tokens, out_billable, self.price_overrides, raw.cached_tokens)
        n_q = len((raw.grounding or {}).get("queries") or [])
        if usd is not None and n_q:
            usd += n_q * SEARCH_USD_PER_QUERY
        with self._lock:
            self.calls.append({
                "role": role, "provider": spec.provider, "model": spec.model, "cached": cached, "ok": ok,
                "input_tokens": raw.input_tokens, "output_tokens": raw.output_tokens,
                "thought_tokens": raw.thought_tokens, "cached_tokens": raw.cached_tokens, "usd": usd or 0.0,
                "usd_billed": 0.0 if cached else (usd or 0.0), "latency_s": round(latency, 2),
                "priced": usd is not None,
            })

    def summary(self) -> dict:
        with self._lock:
            calls = list(self.calls)

        def agg(cs):
            return {
                "n_llm_calls": len(cs),
                "n_cache_hits": sum(c["cached"] for c in cs),
                "n_failed_attempts": sum(not c["ok"] for c in cs),
                "input_tokens": sum(c["input_tokens"] for c in cs),
                "output_tokens": sum(c["output_tokens"] + c["thought_tokens"] for c in cs),
                "thought_tokens": sum(c["thought_tokens"] for c in cs),
                "cached_input_tokens": sum(c.get("cached_tokens", 0) for c in cs),
                "usd": round(sum(c["usd"] for c in cs), 5),
                "usd_billed": round(sum(c["usd_billed"] for c in cs), 5),
            }
        by_role: dict[str, dict] = {}
        for r in sorted({c["role"] for c in calls}):
            cs = [c for c in calls if c["role"] == r]
            by_role[r] = {**agg(cs), "models": sorted({c["model"] for c in cs})}
        out = agg(calls)
        out["wall_time_secs"] = round(time.time() - self.t0, 1)
        out["by_role"] = by_role
        out["unpriced_models"] = sorted({c["model"] for c in calls if not c["priced"]})
        return out


class LLMClient:
    def __init__(self, ledger: Ledger, cache_dir: str | Path | None = "runs/cache", max_retries: int = 4,
                 schema_retries: int = 2, use_cache: bool = True):
        self.ledger = ledger
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.max_retries = max_retries
        self.schema_retries = schema_retries
        self.use_cache = use_cache and self.cache_dir is not None
        self.cache_only = False   # dry run: never call the provider; record misses and raise LLMError
        self.misses: list[dict] = []

    # ------------------------------------------------------------------ cache
    @staticmethod
    def cache_key(spec: ModelSpec, system: str, prompt: str, schema: dict | None, salt: str = "") -> str:
        blob = json.dumps({"provider": spec.provider, "model": spec.model, "params": spec.params,
                           "system": system, "prompt": prompt, "schema": schema, "salt": salt},
                          ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _cache_path(self, spec: ModelSpec, key: str) -> Path:
        return self.cache_dir / spec.provider / spec.model / key[:2] / f"{key}.json"

    def _cache_get(self, spec, key):
        if not self.use_cache:
            return None
        p = self._cache_path(spec, key)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        return None

    def _cache_put(self, spec, key, raw: RawResponse, meta: dict):
        if not self.use_cache:
            return
        p = self._cache_path(spec, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"text": raw.text, "input_tokens": raw.input_tokens,
                                   "output_tokens": raw.output_tokens, "thought_tokens": raw.thought_tokens,
                                   "cached_tokens": raw.cached_tokens, "grounding": raw.grounding,
                                   "model_version": raw.model_version, "meta": meta,
                                   "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    # --------------------------------------------------------------- generate
    def _parse(self, text: str, schema: dict | None, text_ok: bool = False):
        if text_ok and schema is None:  # free-text answer requested (e.g. a grounded research call)
            if not text.strip():
                raise RetryableError("empty text response")
            return text
        t = text.strip()
        if not t.startswith("{"):  # free-text JSON (schema_in_text): strip fences / prose around the object
            a, b = t.find("{"), t.rfind("}")
            if a >= 0 and b > a:
                text = t[a:b + 1]
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            raise RetryableError(f"response is not JSON: {e}; head={text[:120]!r}") from e
        if schema is not None and jsonschema is not None:
            errs = sorted(jsonschema.Draft202012Validator(schema).iter_errors(obj), key=lambda e: list(e.path))
            if errs:
                e = errs[0]
                raise RetryableError(f"response violates schema at {'/'.join(map(str, e.path))}: {e.message[:200]}")
        return obj

    def generate_json(self, role: str, spec: ModelSpec, system: str, prompt: str, schema: dict | None,
                      meta: dict | None = None, want_raw: bool = False, text_ok: bool = False,
                      media: list[dict] | None = None):
        """Return the parsed JSON object (``(obj, RawResponse)`` if want_raw, e.g. for grounding metadata).
        Raises LLMError after retries, BudgetExceeded at the cap."""
        prov = get_provider(spec.provider)
        last_err = None
        for attempt in range(self.schema_retries + 1):
            # attempt > 0 uses a salted key so a cached-but-invalid answer is never replayed
            # media enters the cache key by its stable "key" (file content id + offsets + fps), never by the
            # expiring upload uri; inline media ("data") enter by their "key" too, never by the bytes
            mkey = json.dumps([_media_key(m) for m in media or []], sort_keys=True) if media else ""
            key = self.cache_key(spec, system, prompt + mkey, schema,
                                 salt="" if attempt == 0 else f"retry{attempt}")
            hit = self._cache_get(spec, key)
            if hit is not None and spec.params.get("google_search") and not hit.get("grounding"):
                hit = None  # a grounded answer without its grounding metadata cannot be verified: call again
            if hit is not None:
                raw = RawResponse(hit["text"], hit["input_tokens"], hit["output_tokens"], hit["thought_tokens"],
                                  hit.get("model_version"), cached_tokens=hit.get("cached_tokens", 0),
                                  grounding=hit.get("grounding"))
                try:
                    obj = self._parse(raw.text, schema, text_ok)
                    self.ledger.record(role, spec, raw, cached=True, latency=0.0)
                    return (obj, raw) if want_raw else obj
                except RetryableError:
                    pass  # stale cache entry (schema changed): fall through and call
            if self.cache_only:
                est = (len(system) + len(prompt)) // 3
                self.misses.append({"role": role, "model": spec.model, "est_input_tokens": est})
                raise LLMError(f"{role}/{spec.model}: cache miss (cache-only dry run)")
            raw = self._call_with_retries(role, prov, spec, system, prompt, schema, media)
            try:
                obj = self._parse(raw.text, schema, text_ok)
            except RetryableError as e:
                last_err = e
                continue
            self._cache_put(spec, key, raw, {"role": role, **(meta or {})})
            return (obj, raw) if want_raw else obj
        raise LLMError(f"{role}/{spec.model}: no valid JSON after {self.schema_retries + 1} attempts: {last_err}")

    def _call_with_retries(self, role, prov, spec, system, prompt, schema, media=None) -> RawResponse:
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            self.ledger.check_budget()
            t = time.time()
            try:
                raw = prov.generate(spec, system, prompt, schema, media) if media else \
                    prov.generate(spec, system, prompt, schema)
            except RetryableError as e:
                billed = getattr(e, "response", None)
                if billed is not None:
                    self.ledger.record(role, spec, billed, cached=False, latency=time.time() - t, ok=False)
                if attempt == self.max_retries:
                    raise LLMError(f"{role}/{spec.model}: gave up after {attempt + 1} attempts: {e}") from e
                time.sleep(delay + random.uniform(0, delay / 2))
                delay = min(delay * 2, 60.0)
                continue
            self.ledger.record(role, spec, raw, cached=False, latency=time.time() - t)
            return raw
        raise LLMError("unreachable")

    def count_tokens(self, spec: ModelSpec, text: str) -> int:
        return get_provider(spec.provider).count_tokens(spec, text)
