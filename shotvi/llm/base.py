"""Provider-neutral types: model spec, raw response, provider interface, errors."""
from __future__ import annotations

from dataclasses import dataclass, field


class LLMError(RuntimeError):
    """Non-retryable failure (bad request, auth, schema never satisfied)."""


class SafetyBlocked(LLMError):
    """The provider refused the PROMPT on safety grounds (Gemini: no candidates, ``promptFeedback.blockReason``, e.g.
    PROHIBITED_CONTENT; a non-adjustable protection that safetySettings cannot turn off). Not retryable: the same
    prompt is blocked again. ``reason`` = the block reason; ``response`` = the billed usage, if any."""

    def __init__(self, msg: str, reason: str, response=None):
        super().__init__(msg)
        self.reason = reason
        self.response = response


class RetryableError(RuntimeError):
    """Transient failure (rate limit, 5xx, timeout, truncated or unparsable output)."""


class BudgetExceeded(RuntimeError):
    """The run's spend cap was reached; no further calls are made."""


class CacheUnavailable(LLMError):
    """Explicit context caching is not available (provider has none, the model rejects cachedContents, the content
    is under the model's minimum, or no storage price is known). Callers fall back to sending the same prefix
    in every call (implicit caching) and must say so."""


@dataclass(frozen=True)
class ModelSpec:
    """Which model plays a role. Lives in config, never in pipeline code.

    params (provider-neutral where possible):
      thinking_level: "minimal" | "low" | "medium" | "high"  (provider maps it)
      temperature: float | None (None = provider default; Gemini 3 recommends leaving it at default)
      max_output_tokens: int
    """
    provider: str
    model: str
    params: dict = field(default_factory=dict, hash=False, compare=False)

    @classmethod
    def from_dict(cls, d: dict) -> "ModelSpec":
        return cls(d["provider"], d["model"], dict(d.get("params") or {}))

    def to_dict(self) -> dict:
        return {"provider": self.provider, "model": self.model, "params": dict(self.params)}


@dataclass
class RawResponse:
    text: str
    input_tokens: int
    output_tokens: int          # visible output only
    thought_tokens: int = 0     # billed as output
    model_version: str | None = None
    finish_reason: str | None = None
    cached_tokens: int = 0      # part of input_tokens served from Gemini's implicit cache (billed at the cache rate)
    grounding: dict | None = None  # google_search: {queries: [...], chunks: [{uri, title}], supports: [...]}
    prompt_modalities: dict | None = None  # input tokens by modality (TEXT / VIDEO / AUDIO / IMAGE)


class Provider:
    """Implement ``generate`` (and optionally ``count_tokens``) for a new provider and register it in
    ``shotvi.llm.client.PROVIDERS``. ``generate`` must raise RetryableError for transient problems."""

    name = "base"

    def generate(self, spec: ModelSpec, system: str, prompt: str, schema: dict | None,
                 media: list[dict] | None = None, cached_content: str | None = None) -> RawResponse:
        """``media``: optional list of {file_uri, mime_type, start_s?, end_s?, fps?} parts placed before the text.
        ``cached_content``: name of an explicit context cache (``create_cache``) holding the system instruction
        and the shared prefix; ``system`` must then be empty (it lives in the cache)."""
        raise NotImplementedError

    def count_tokens(self, spec: ModelSpec, text: str) -> int:
        raise NotImplementedError

    def create_cache(self, spec: ModelSpec, system: str, prefix: str, ttl_s: int = 3600,
                     display_name: str = "") -> dict:
        """Explicit context cache of (system instruction + one user text part) for ``ttl_s`` seconds. Returns
        ``{name, tokens, ttl_s, expire_time}``. Providers without it raise CacheUnavailable."""
        raise CacheUnavailable(f"{self.name}: explicit context caching is not implemented")

    def delete_cache(self, name: str) -> bool:
        return False
