"""Gemini via the REST generateContent endpoint (plain ``requests``, no SDK)."""
from __future__ import annotations

import os

import requests

from . import spend
from .base import LLMError, ModelSpec, Provider, RawResponse, RetryableError, SafetyBlocked

API = "https://generativelanguage.googleapis.com/v1beta/models"
RETRY_STATUS = {408, 429, 500, 502, 503, 504}


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, api_key: str | None = None, timeout: float = 300.0):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise LLMError("GEMINI_API_KEY is not set (put it in .env)")
        self.timeout = timeout
        self.session = requests.Session()

    def _post(self, url: str, body: dict) -> dict:
        try:
            r = self.session.post(url, headers={"x-goog-api-key": self.api_key}, json=body, timeout=self.timeout)
        except (requests.Timeout, requests.ConnectionError) as e:
            raise RetryableError(f"gemini network error: {e}") from e
        if r.status_code in RETRY_STATUS:
            raise RetryableError(f"gemini HTTP {r.status_code}: {r.text[:300]}")
        if r.status_code != 200:
            raise LLMError(f"gemini HTTP {r.status_code}: {r.text[:500]}")
        return r.json()

    @staticmethod
    def check_params(spec: ModelSpec):
        """Enforce Google's documented settings (ai.google.dev/gemini-api/docs/gemini-3, checked 2026-09-29):
        Gemini 3.x must stay at the default temperature 1.0 ("setting it below 1.0 may lead to ...
        looping or degraded performance"), and only some models accept thinking_level "minimal"."""
        p, m = spec.params, spec.model
        if m.startswith("gemini-3"):
            t = p.get("temperature")
            if t is not None and float(t) != 1.0:
                raise LLMError(f"{m}: Google recommends the default temperature 1.0 for Gemini 3 models; "
                               f"got {t}. Remove 'temperature' from the config.")
            lvl = p.get("thinking_level")
            if lvl and lvl not in ("minimal", "low", "medium", "high"):
                raise LLMError(f"{m}: unknown thinking_level {lvl!r}")
            if lvl == "minimal" and not any(k in m for k in ("flash-lite", "3-flash", "3.5-flash", "3.6-flash")):
                raise LLMError(f"{m}: thinking_level 'minimal' is not supported by this model; use 'low'")

    def generate(self, spec: ModelSpec, system: str, prompt: str, schema: dict | None,
                 media: list[dict] | None = None) -> RawResponse:
        self.check_params(spec)
        p = spec.params
        gen: dict = {"maxOutputTokens": int(p.get("max_output_tokens", 16384))}
        if p.get("temperature") is not None:
            gen["temperature"] = float(p["temperature"])
        # schema_in_text: the caller puts the schema in the prompt and the client validates the text. Needed with
        # google_search: on generateContent a response schema silently disables the search (checked 2026-10-02).
        if schema is not None and not p.get("schema_in_text"):
            gen["responseMimeType"] = "application/json"
            gen["responseJsonSchema"] = schema
        lvl = p.get("thinking_level")
        if lvl:
            if spec.model.startswith("gemini-2."):
                budgets = {"minimal": 0, "low": 1024, "medium": 4096, "high": 16384}
                gen["thinkingConfig"] = {"thinkingBudget": budgets.get(lvl, 4096)}
            else:
                gen["thinkingConfig"] = {"thinkingLevel": lvl}
        res = p.get("media_resolution")
        if res:  # "low" | "medium" | "high"; unset = model default
            gen["mediaResolution"] = "MEDIA_RESOLUTION_" + str(res).upper()
        parts = [_media_part(m) for m in media or []] + [{"text": prompt}]
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if p.get("google_search"):
            # Gemini 3 allows structured output together with built-in tools (docs: structured-output, preview)
            body["tools"] = [{"google_search": {}}]
        spend.check()   # project-wide rupee cap: refuse before anything is billed
        d = self._post(f"{API}/{spec.model}:generateContent", body)
        um = d.get("usageMetadata") or {}
        cands = d.get("candidates") or []
        grounding = _grounding(cands[0]) if cands else None
        n_queries = len([q for q in (grounding or {}).get("queries", []) if q and str(q).strip()])
        spend.record(spec.model, int(um.get("promptTokenCount", 0)),
                     int(um.get("candidatesTokenCount", 0)) + int(um.get("thoughtsTokenCount", 0)),
                     source=os.environ.get("SHOTVI_SPEND_SOURCE", ""), search_queries=n_queries)
        if not cands:
            fb = d.get("promptFeedback")
            if isinstance(fb, dict) and fb.get("blockReason"):
                # the prompt itself was blocked: retrying the same prompt is blocked (and billed) again
                raise SafetyBlocked(f"gemini blocked the prompt: blockReason {fb['blockReason']} (promptFeedback={fb})",
                                    reason=str(fb["blockReason"]),
                                    response=RawResponse(text="", input_tokens=int(um.get("promptTokenCount", 0)),
                                                         output_tokens=0, model_version=d.get("modelVersion")))
            raise RetryableError(f"gemini returned no candidates (promptFeedback={fb})")
        c = cands[0]
        parts = (c.get("content") or {}).get("parts") or []
        text = "".join(pt.get("text", "") for pt in parts if not pt.get("thought"))
        fin = c.get("finishReason")
        resp = RawResponse(text=text, input_tokens=int(um.get("promptTokenCount", 0)),
                           output_tokens=int(um.get("candidatesTokenCount", 0)),
                           thought_tokens=int(um.get("thoughtsTokenCount", 0)),
                           cached_tokens=int(um.get("cachedContentTokenCount", 0)),
                           model_version=d.get("modelVersion"), finish_reason=fin, grounding=grounding,
                           prompt_modalities={x.get("modality"): x.get("tokenCount", 0)
                                              for x in um.get("promptTokensDetails") or []})
        if fin not in (None, "STOP"):
            err = RetryableError(f"gemini finishReason={fin}")
            err.response = resp  # still billed: the client records the usage
            raise err
        return resp

    def count_tokens(self, spec: ModelSpec, text: str) -> int:
        d = self._post(f"{API}/{spec.model}:countTokens", {"contents": [{"parts": [{"text": text}]}]})
        return int(d["totalTokens"])

    def count_tokens_request(self, spec: ModelSpec, system: str, prompt: str, media: list[dict] | None = None,
                             details: bool = False):
        """countTokens (free, no generation) for a full request: system + media parts (video windows with offsets
        and fps) + prompt, with the spec's media resolution. ``details``: also return promptTokensDetails."""
        gen = {}
        res = spec.params.get("media_resolution")
        if res:
            gen["mediaResolution"] = "MEDIA_RESOLUTION_" + str(res).upper()
        req = {"model": f"models/{spec.model}",
               "contents": [{"role": "user", "parts": [_media_part(m) for m in media or []] + [{"text": prompt}]}]}
        if system:
            req["systemInstruction"] = {"parts": [{"text": system}]}
        if gen:
            req["generationConfig"] = gen
        d = self._post(f"{API}/{spec.model}:countTokens", {"generateContentRequest": req})
        n = int(d["totalTokens"])
        if details:
            return n, {x.get("modality"): x.get("tokenCount", 0) for x in d.get("promptTokensDetails") or []}
        return n


def _grounding(cand: dict) -> dict | None:
    gm = cand.get("groundingMetadata")
    if not gm:
        return None
    chunks = [{"uri": (c.get("web") or {}).get("uri", ""), "title": (c.get("web") or {}).get("title", "")}
              for c in gm.get("groundingChunks") or []]
    supports = [{"text": (x.get("segment") or {}).get("text", ""),
                 "start": (x.get("segment") or {}).get("startIndex", 0),
                 "end": (x.get("segment") or {}).get("endIndex", 0),
                 "chunks": list(x.get("groundingChunkIndices") or [])} for x in gm.get("groundingSupports") or []]
    return {"queries": list(gm.get("webSearchQueries") or []), "chunks": chunks, "supports": supports}


def _media_part(m: dict) -> dict:
    """``{file_uri, ...}`` -> a Files API part (with optional video clipping);
    ``{data, mime_type}`` -> an inline part (``data`` = base64 str, for short audio snippets; < 20 MB per request);
    ``{text}`` -> a text part (labels interleaved between media parts)."""
    if "data" in m:
        return {"inline_data": {"mime_type": m["mime_type"], "data": m["data"]}}
    if "text" in m and "file_uri" not in m:
        return {"text": m["text"]}
    part = {"file_data": {"mime_type": m.get("mime_type", "video/mp4"), "file_uri": m["file_uri"]}}
    vm = {}
    if m.get("start_s") is not None:
        vm["start_offset"] = f"{float(m['start_s']):.2f}s"
    if m.get("end_s") is not None:
        vm["end_offset"] = f"{float(m['end_s']):.2f}s"
    if m.get("fps"):
        vm["fps"] = float(m["fps"])
    if vm:
        part["video_metadata"] = vm
    return part


# ------------------------------------------------------------------ Files API (resumable upload, 48 h retention)
FILE_CACHE_HOURS = 46   # the API keeps files 48 h; re-upload a little early


def upload_file(path: str, mime_type: str = "video/mp4", cache_dir: str = "runs/cache/files") -> dict:
    """Upload a local file once (resumable), wait until ACTIVE, and cache {name, uri, uploaded_at} keyed by the
    file's path, size and mtime so reruns within ~46 h reuse it. Uploading and storage are free (no tokens)."""
    import hashlib
    import json
    import time
    from pathlib import Path
    prov = GeminiProvider()
    fp = Path(path).resolve()
    st = fp.stat()
    key = hashlib.sha256(f"{fp}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:24]
    cdir = Path(cache_dir)
    cdir.mkdir(parents=True, exist_ok=True)
    cf = cdir / f"{key}.json"
    if cf.exists():
        c = json.loads(cf.read_text(encoding="utf-8"))
        if time.time() - c.get("uploaded_at", 0) < FILE_CACHE_HOURS * 3600:
            r = prov.session.get(f"https://generativelanguage.googleapis.com/v1beta/{c['name']}",
                                 headers={"x-goog-api-key": prov.api_key}, timeout=60)
            if r.status_code == 200 and r.json().get("state") == "ACTIVE":
                return c
    start = prov.session.post(
        "https://generativelanguage.googleapis.com/upload/v1beta/files",
        headers={"x-goog-api-key": prov.api_key, "X-Goog-Upload-Protocol": "resumable",
                 "X-Goog-Upload-Command": "start", "X-Goog-Upload-Header-Content-Length": str(st.st_size),
                 "X-Goog-Upload-Header-Content-Type": mime_type, "Content-Type": "application/json"},
        json={"file": {"display_name": fp.name}}, timeout=120)
    if start.status_code != 200:
        raise LLMError(f"upload start failed: HTTP {start.status_code}: {start.text[:300]}")
    url = start.headers.get("x-goog-upload-url") or start.headers.get("X-Goog-Upload-URL")
    with open(fp, "rb") as fh:
        up = prov.session.post(url, headers={"Content-Length": str(st.st_size), "X-Goog-Upload-Offset": "0",
                                             "X-Goog-Upload-Command": "upload, finalize"}, data=fh, timeout=1800)
    if up.status_code != 200:
        raise LLMError(f"upload failed: HTTP {up.status_code}: {up.text[:300]}")
    f = up.json().get("file", {})
    for _ in range(120):   # poll until the file is processed
        if f.get("state") == "ACTIVE":
            break
        if f.get("state") == "FAILED":
            raise LLMError(f"file processing failed: {f}")
        time.sleep(5)
        f = prov.session.get(f"https://generativelanguage.googleapis.com/v1beta/{f['name']}",
                             headers={"x-goog-api-key": prov.api_key}, timeout=60).json()
    if f.get("state") != "ACTIVE":
        raise LLMError(f"file not ACTIVE after polling: {f.get('state')}")
    c = {"name": f["name"], "uri": f["uri"], "mime_type": f.get("mimeType", mime_type), "uploaded_at": time.time(),
         "size": st.st_size, "path": str(fp)}
    cf.write_text(json.dumps(c, indent=1), encoding="utf-8")
    return c
