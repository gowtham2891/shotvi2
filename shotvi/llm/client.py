"""LLM client: structured JSON output, retries, response cache, per-run token/cost ledger.

Usage::

    ledger = Ledger(max_usd=1.5)
    llm = LLMClient(ledger, cache_dir="runs/cache")
    obj = llm.generate_json("scorer", ModelSpec("gemini", "gemini-3.8-flash", {"thinking_level": "low"}),
                            system=..., prompt=..., schema=...)

Cache key = sha256 of (provider, model, params, system, prompt, schema). Only responses that parse and validate are
cached, so an eval rerun with unchanged prompts costs nothing. A prompt the provider refuses on safety grounds
(``SafetyBlocked``: never retried) is remembered too, as a ``{"blocked": reason}`` entry: a rerun raises the same
SafetyBlocked without a call. Opt-in per role (``LLMClient.remember_max_tokens``): a call that gave up on
``finishReason=MAX_TOKENS`` is remembered as a ``{"failed": message}`` entry, so a rerun / cache-only replay raises the
same LLMError (same message) for free; the caller decides whether to retry with a larger limit (a different key).
Roles not opted in ignore such entries (treated as a miss). The ledger records every call with its *full* cost
(what an uncached production run pays) and the billed cost (0 on a cache hit).
"""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from pathlib import Path

from .base import BudgetExceeded, LLMError, ModelSpec, Provider, RawResponse, RetryableError, SafetyBlocked
from .pricing import SEARCH_USD_PER_QUERY, cost_usd

try:
    import jsonschema
except ImportError:  # pragma: no cover - jsonschema is in requirements.txt
    jsonschema = None


def _load_provider(name: str) -> Provider:
    if name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider()
    if name == "sarvam":
        from .sarvam import SarvamProvider
        return SarvamProvider()
    raise LLMError(f"unknown LLM provider {name!r} (implemented: gemini, sarvam; add others in shotvi/llm/)")


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

    def record_fixed(self, role: str, spec: ModelSpec, usd: float, note: str = "", tokens: int = 0):
        """A billed item that is not a generate call (explicit cache storage for its TTL)."""
        with self._lock:
            self.calls.append({"role": role, "provider": spec.provider, "model": spec.model, "cached": False,
                               "ok": True, "input_tokens": 0, "output_tokens": 0, "thought_tokens": 0,
                               "cached_tokens": 0, "usd": usd, "usd_billed": usd, "latency_s": 0.0, "priced": True,
                               "note": note, "cache_tokens": tokens})

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
        # called as before_call(role, spec) right before EVERY billed provider attempt (retries and schema
        # retries included, cache hits never): a spend guard raises here to stop before money is spent
        self.before_call = None
        # roles whose MAX_TOKENS give-up is remembered in the response cache (see the module docstring); empty =
        # every role behaves as before
        self.remember_max_tokens: set[str] = set()

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

    def _cache_put_block(self, spec, key, reason: str, input_tokens: int, meta: dict):
        """Remember a safety block of this exact prompt (attempt 0 key), so a rerun / replay / dry run raises the same
        SafetyBlocked for free instead of paying for the same refusal again. Delete the file to try the prompt
        again."""
        if not self.use_cache:
            return
        p = self._cache_path(spec, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"blocked": reason, "input_tokens": input_tokens, "meta": meta,
                                   "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    def _cache_put_failed(self, spec, key, message: str, meta: dict):
        """Remember that this exact prompt (attempt 0 key) gave up on MAX_TOKENS (``remember_max_tokens`` roles)."""
        if not self.use_cache:
            return
        p = self._cache_path(spec, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"failed": message, "meta": meta,
                                   "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    @staticmethod
    def is_max_tokens(err: BaseException) -> bool:
        """The call gave up because the answer hit the output-token limit (Gemini finishReason=MAX_TOKENS)."""
        return "finishReason=MAX_TOKENS" in str(err)

    def remember_failure(self, role: str, spec: ModelSpec, system: str, prompt: str, schema: dict | None,
                         message: str, meta: dict | None = None, cached_content: dict | None = None) -> str:
        """Write the ``{"failed": message}`` entry for this call's attempt-0 key (same key as generate_json) and return
        the key. Used to backfill a MAX_TOKENS give-up of a run made before failures were remembered (its message is
        in that run's debug file), so the run replays byte-for-byte. Never overwrites a cached answer."""
        ckey = f"\n<cached_content {cached_content['key']}>" if cached_content else ""
        key = self.cache_key(spec, system, prompt + ckey, schema)
        if self._cache_get(spec, key) is None:
            self._cache_put_failed(spec, key, message, {"role": role, **(meta or {})})
        return key

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
                      media: list[dict] | None = None, cached_content: dict | None = None):
        """Return the parsed JSON object (``(obj, RawResponse)`` if want_raw, e.g. for grounding metadata).
        Raises LLMError after retries, BudgetExceeded at the cap.
        ``cached_content`` = ``{"name": <provider cache name>, "key": <hash of the cached system + prefix>}``: the
        provider gets the name; the response cache is keyed by ``key`` (the cached CONTENT), never by the
        expiring cache name, so a rerun of the same prompts over the same transcript is free."""
        prov = get_provider(spec.provider)
        last_err = None
        for attempt in range(self.schema_retries + 1):
            # attempt > 0 uses a salted key so a cached-but-invalid answer is never replayed
            # media enters the cache key by its stable "key" (file content id + offsets + fps), never by the
            # expiring upload uri; inline media ("data") enter by their "key" too, never by the bytes
            mkey = json.dumps([_media_key(m) for m in media or []], sort_keys=True) if media else ""
            ckey = f"\n<cached_content {cached_content['key']}>" if cached_content else ""
            key = self.cache_key(spec, system, prompt + mkey + ckey, schema,
                                 salt="" if attempt == 0 else f"retry{attempt}")
            hit = self._cache_get(spec, key)
            if hit is not None and hit.get("blocked"):
                # a remembered safety block of this prompt: no call (a retry would be refused and billed again)
                self.ledger.record(role, spec, RawResponse("", int(hit.get("input_tokens") or 0), 0), cached=True,
                                   latency=0.0, ok=False)
                raise SafetyBlocked(f"{role}/{spec.model}: prompt blocked by the provider's safety check "
                                    f"(blockReason {hit['blocked']}; cached block)", reason=str(hit["blocked"]))
            if hit is not None and hit.get("failed") is not None:
                if role in self.remember_max_tokens and attempt == 0:
                    # a remembered MAX_TOKENS give-up of this prompt: the same error, no call
                    self.ledger.record(role, spec, RawResponse("", 0, 0), cached=True, latency=0.0, ok=False)
                    raise LLMError(str(hit["failed"]))
                hit = None  # not opted in: a miss, as before
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
            try:
                raw = self._call_with_retries(role, prov, spec, system, prompt, schema, media,
                                              (cached_content or {}).get("name"))
            except SafetyBlocked as e:
                if attempt == 0:
                    resp = e.response
                    self._cache_put_block(spec, key, e.reason, int(getattr(resp, "input_tokens", 0) or 0),
                                          {"role": role, **(meta or {})})
                raise SafetyBlocked(f"{role}/{spec.model}: prompt blocked by the provider's safety check "
                                    f"(blockReason {e.reason})", reason=e.reason, response=e.response) from e
            except LLMError as e:
                if attempt == 0 and role in self.remember_max_tokens and self.is_max_tokens(e):
                    self._cache_put_failed(spec, key, str(e), {"role": role, **(meta or {})})
                raise
            try:
                obj = self._parse(raw.text, schema, text_ok)
            except RetryableError as e:
                last_err = e
                continue
            self._cache_put(spec, key, raw, {"role": role, **(meta or {})})
            return (obj, raw) if want_raw else obj
        raise LLMError(f"{role}/{spec.model}: no valid JSON after {self.schema_retries + 1} attempts: {last_err}")

    def _call_with_retries(self, role, prov, spec, system, prompt, schema, media=None,
                           cached_name: str | None = None) -> RawResponse:
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            self.ledger.check_budget()
            if self.before_call is not None:
                self.before_call(role, spec)
            t = time.time()
            try:
                if cached_name:
                    raw = prov.generate(spec, system, prompt, schema, media, cached_content=cached_name)
                else:
                    raw = prov.generate(spec, system, prompt, schema, media) if media else \
                        prov.generate(spec, system, prompt, schema)
            except SafetyBlocked as e:
                # never retried (the same prompt is refused again); the refused prompt may still be billed
                if e.response is not None:
                    self.ledger.record(role, spec, e.response, cached=False, latency=time.time() - t, ok=False)
                raise
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
        """Provider token count. This is a network call (Gemini countTokens), so cache-only mode refuses it:
        callers fall back to an estimate."""
        if self.cache_only:
            raise LLMError("count_tokens: cache-only mode makes no provider calls")
        return get_provider(spec.provider).count_tokens(spec, text)
