"""Gemini via the REST generateContent endpoint (plain ``requests``, no SDK)."""
from __future__ import annotations

import os

import requests

from . import spend
from .base import LLMError, ModelSpec, Provider, RawResponse, RetryableError

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

    def generate(self, spec: ModelSpec, system: str, prompt: str, schema: dict | None) -> RawResponse:
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
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen}
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
            raise RetryableError(f"gemini returned no candidates (promptFeedback={fb})")
        c = cands[0]
        parts = (c.get("content") or {}).get("parts") or []
        text = "".join(pt.get("text", "") for pt in parts if not pt.get("thought"))
        fin = c.get("finishReason")
        resp = RawResponse(text=text, input_tokens=int(um.get("promptTokenCount", 0)),
                           output_tokens=int(um.get("candidatesTokenCount", 0)),
                           thought_tokens=int(um.get("thoughtsTokenCount", 0)),
                           cached_tokens=int(um.get("cachedContentTokenCount", 0)),
                           model_version=d.get("modelVersion"), finish_reason=fin, grounding=grounding)
        if fin not in (None, "STOP"):
            err = RetryableError(f"gemini finishReason={fin}")
            err.response = resp  # still billed: the client records the usage
            raise err
        return resp

    def count_tokens(self, spec: ModelSpec, text: str) -> int:
        d = self._post(f"{API}/{spec.model}:countTokens", {"contents": [{"parts": [{"text": text}]}]})
        return int(d["totalTokens"])


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
