"""Sarvam chat completions (``sarvam-105b``, Sarvam's Indic LLM) via the REST endpoint (plain ``requests``, no SDK).

Text only: no media, no explicit context cache, no search. Facts below are from docs.sarvam.ai, checked 2026-11-02:
  - endpoint POST https://api.sarvam.ai/v1/chat/completions (OpenAI-compatible); auth header ``api-subscription-key``;
    the chat endpoint "additionally accepts ``Authorization: Bearer <key>``" (api/chat-completion/overview) while the
    v1 reference lists Bearer under auth, so both headers are sent (same key, same host).
  - models ``sarvam-105b`` (128K context) and ``sarvam-105b-conversations`` (32K).
  - ``response_format`` {"type": "json_schema", "json_schema": {name, schema, strict}} = structured output;
    {"type": "json_object"} = valid JSON only. The client still validates against the schema (docs say to).
  - ``reasoning_effort``: the docs disagree on values and default (model page: default "low"; overview: low / medium /
    high; v1 reference: none / low / high / max, default "medium"). Disable with JSON null. Reasoning comes back in
    ``message.reasoning_content`` (not in content), billed as completion tokens and counted inside ``max_tokens``.
  - ``max_tokens`` default 2048; plan limit Starter 4096 / Pro 16384 / Business 128000. ``finish_reason`` "length"
    with empty content = the budget went to reasoning.
  - usage: prompt_tokens, completion_tokens (reasoning included), completion_tokens_details.reasoning_tokens,
    prompt_tokens_details.cached_tokens (null object on a cache miss). Errors 400/403/422 fatal, 429/500/503 retry.
  - rate limit 60 req/min pay-as-you-go (Pro 200, Growth 300).

Config (``ModelSpec.params``):
  thinking_level   "minimal" (= reasoning off) | "low" | "medium" | "high"  -> reasoning_effort
  reasoning_effort  Sarvam's value as is (overrides thinking_level; null = reasoning off)
  max_output_tokens -> max_tokens (default 4096, the smallest plan limit; reasoning counts inside it)
  temperature       optional; unset = Sarvam's default (0.5 with reasoning, 0.2 without)
  json_mode         "schema" (default: response_format json_schema) | "object" (json_object) | "text" (no
                    response_format); with "object" / "text" the schema is appended to the user message
  schema_in_text    true = the caller already put the schema in the prompt (same as Gemini): no response_format
"""
from __future__ import annotations

import json
import os
import re

import requests

from . import spend
from .base import LLMError, ModelSpec, Provider, RawResponse, RetryableError

API = "https://api.sarvam.ai/v1/chat/completions"
RETRY_STATUS = {408, 429, 500, 502, 503, 504}
DEFAULT_MAX_TOKENS = 4096
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```$", re.S)


def clean_text(text: str) -> str:
    """The answer without a reasoning block or a code fence. The docs put reasoning in ``reasoning_content``, but an
    OpenAI-compatible server may still inline ``<think>...</think>`` (or only the closing tag, when the opening one
    was in the chat template); either would break JSON parsing, so it is cut here."""
    t = _THINK.sub("", text or "")
    if "</think>" in t.lower():   # an unopened block: everything up to the last closing tag is reasoning
        t = t[t.lower().rfind("</think>") + len("</think>"):]
    t = t.strip()
    m = _FENCE.match(t)
    return m.group(1).strip() if m else t


def _schema_name(schema: dict) -> str:
    """``json_schema.name``: alphanumerics, underscores and dashes only (v1 reference)."""
    n = re.sub(r"[^A-Za-z0-9_-]+", "_", str(schema.get("title") or "response")).strip("_")
    return (n or "response")[:64]


class SarvamProvider(Provider):
    name = "sarvam"

    def __init__(self, api_key: str | None = None, timeout: float = 300.0):
        self.api_key = api_key or os.environ.get("SARVAM_API_KEY")
        if not self.api_key:
            try:   # Gemini relies on the caller loading .env; load it here too so a config switch alone works
                from dotenv import load_dotenv
                load_dotenv(spend.ROOT / ".env")
            except ImportError:
                pass
            self.api_key = os.environ.get("SARVAM_API_KEY")
        if not self.api_key:
            raise LLMError("SARVAM_API_KEY is not set (put it in .env)")
        self.timeout = timeout
        self.session = requests.Session()

    def _post(self, body: dict) -> dict:
        headers = {"api-subscription-key": self.api_key, "Authorization": f"Bearer {self.api_key}"}
        try:
            r = self.session.post(API, headers=headers, json=body, timeout=self.timeout)
        except (requests.Timeout, requests.ConnectionError) as e:
            raise RetryableError(f"sarvam network error: {e}") from e
        if r.status_code in RETRY_STATUS:
            raise RetryableError(f"sarvam HTTP {r.status_code}: {r.text[:300]}")
        if r.status_code != 200:
            raise LLMError(f"sarvam HTTP {r.status_code}: {r.text[:500]}")
        return r.json()

    @staticmethod
    def build_body(spec: ModelSpec, system: str, prompt: str, schema: dict | None) -> dict:
        p = spec.params
        if p.get("google_search"):
            raise LLMError(f"{spec.model}: google_search is a Gemini tool; Sarvam has no search in this client")
        mode = p.get("json_mode", "schema")
        if mode not in ("schema", "object", "text"):
            raise LLMError(f"{spec.model}: unknown json_mode {mode!r} (schema | object | text)")
        user = prompt
        body: dict = {"model": spec.model, "max_tokens": int(p.get("max_output_tokens", DEFAULT_MAX_TOKENS))}
        if schema is not None and not p.get("schema_in_text"):
            if mode == "schema":
                s = {k: v for k, v in schema.items() if k != "$schema"}   # the draft URI is not part of the shape
                body["response_format"] = {"type": "json_schema",
                                           "json_schema": {"name": _schema_name(schema), "schema": s,
                                                           "strict": bool(p.get("strict", False))}}
            else:
                if mode == "object":
                    body["response_format"] = {"type": "json_object"}
                user = (prompt + "\n\nReturn only one JSON object (no prose, no code fence) that matches this JSON "
                        "Schema:\n" + json.dumps(schema, ensure_ascii=False))
        body["messages"] = ([{"role": "system", "content": system}] if system else []) + \
            [{"role": "user", "content": user}]
        if "reasoning_effort" in p:
            body["reasoning_effort"] = p["reasoning_effort"]   # None -> JSON null = reasoning off
        elif p.get("thinking_level"):
            lvl = p["thinking_level"]
            if lvl not in ("minimal", "low", "medium", "high"):
                raise LLMError(f"{spec.model}: unknown thinking_level {lvl!r}")
            body["reasoning_effort"] = None if lvl == "minimal" else lvl
        if p.get("temperature") is not None:
            body["temperature"] = float(p["temperature"])
        return body

    def generate(self, spec: ModelSpec, system: str, prompt: str, schema: dict | None,
                 media: list[dict] | None = None, cached_content: str | None = None) -> RawResponse:
        if media:
            raise LLMError(f"sarvam/{spec.model}: text only, media parts are not supported")
        if cached_content:
            raise LLMError("sarvam: no explicit context cache (create_cache raises CacheUnavailable)")
        body = self.build_body(spec, system, prompt, schema)
        spend.check()   # project-wide rupee cap: refuse before anything is billed
        d = self._post(body)
        resp = parse_response(d, spec.model)
        spend.record(spec.model, resp.input_tokens, resp.output_tokens + resp.thought_tokens,
                     source=os.environ.get("SHOTVI_SPEND_SOURCE", ""), cached_tokens=resp.cached_tokens)
        if resp.finish_reason == "length":
            # same wording as Gemini so LLMClient.is_max_tokens / remember_max_tokens work unchanged
            err = RetryableError(f"sarvam finishReason=MAX_TOKENS (finish_reason=length, max_tokens="
                                 f"{body['max_tokens']}; reasoning counts inside it)")
            err.response = resp  # still billed: the client records the usage
            raise err
        if resp.finish_reason not in (None, "stop"):
            err = RetryableError(f"sarvam finish_reason={resp.finish_reason}")
            err.response = resp
            raise err
        if not resp.text.strip():
            err = RetryableError("sarvam returned empty content")
            err.response = resp
            raise err
        return resp

    def count_tokens(self, spec: ModelSpec, text: str) -> int:
        """ESTIMATE, not Sarvam's tokenizer: Sarvam has no free token-count endpoint, so this is the project's
        character-based estimate (shotvi.segment.render.estimate_tokens). Real counts come back in ``usage``."""
        from shotvi.segment.render import estimate_tokens
        return estimate_tokens(text)


def parse_response(d: dict, model: str) -> RawResponse:
    """Chat-completion JSON -> RawResponse. ``completion_tokens`` includes reasoning (docs: "reasoning tokens count
    toward completion tokens"), so visible output = completion - reasoning. If a reply ever reports more reasoning
    than completion tokens, reasoning was evidently NOT included: both are billed in full (never under-count)."""
    u = d.get("usage") or {}
    comp = int(u.get("completion_tokens") or 0)
    reas = int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
    out = comp - reas if reas <= comp else comp
    cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    choices = d.get("choices") or []
    msg = (choices[0].get("message") or {}) if choices else {}
    return RawResponse(text=clean_text(msg.get("content") or ""), input_tokens=int(u.get("prompt_tokens") or 0),
                       output_tokens=out, thought_tokens=reas, cached_tokens=cached,
                       model_version=d.get("model") or model,
                       finish_reason=(choices[0].get("finish_reason") if choices else "no_choices"))
