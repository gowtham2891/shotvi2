"""USD prices per 1M tokens. Thinking tokens are billed as output.

Source: https://ai.google.dev/gemini-api/docs/pricing (checked 2026-09-27, paid tier, standard, text input);
Sarvam: docs.sarvam.ai/api/getting-started/pricing (native rupees, converted below).
Prices change: override per run with ``pricing`` in the select config instead of editing pipeline code.
``long`` applies when the prompt exceeds ``long_threshold`` input tokens.
"""
from __future__ import annotations

# Google Search grounding on Gemini 3.x: 5,000 free queries / month, then $14 per 1,000. Shotvi always records the
# paid rate per executed query (conservative: keeps the rupee cap safe even inside the free tier).
SEARCH_USD_PER_QUERY = 0.014

# Rupee-priced providers (Sarvam) are converted at this rate; keep it equal to SHOTVI_USD_INR in .env.
SARVAM_INR_PER_USD = 96

PRICES: dict[str, dict] = {
    # Gemini 3.x Flash: promotional price through 2026-12-31, list price after (1.50 / 7.50).
    # "cached": explicit (cachedContents) and implicit cache hits, per 1M cached input tokens;
    # "cache_storage_hour": explicit cache storage per 1M tokens per hour (checked 2026-10-14 on the pricing page:
    # 3.8 Flash / 3.6 Flash $0.075 cached, $0.50 storage, promo to 2026-12-31; list $0.15 / $1.00).
    "gemini-3.8-flash": {"input": 0.75, "output": 3.75, "cached": 0.075, "cache_storage_hour": 0.50,
                         "note": "promo to 2026-12-31; list 1.50/7.50"},
    "gemini-3.7-flash": {"input": 0.75, "output": 3.75, "note": "promo to 2026-12-31; list 1.50/7.50"},
    "gemini-3.6-flash": {"input": 0.75, "output": 3.75, "cached": 0.075, "cache_storage_hour": 0.50,
                         "note": "promo to 2026-12-31; list 1.50/7.50"},
    "gemini-3.5-flash": {"input": 1.50, "output": 9.00},
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50, "cached": 0.03},
    "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00, "long_threshold": 200_000,
                               "long": {"input": 4.00, "output": 18.00}},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "long_threshold": 200_000,
                       "long": {"input": 2.50, "output": 15.00}},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
    # Sarvam bills in rupees: sarvam-105b Rs 15 input / Rs 5 cached input / Rs 60 output per 1M tokens, excluding GST,
    # same on every plan (docs.sarvam.ai/api/getting-started/pricing and sarvam.ai/api-pricing, checked 2026-11-02).
    # Reasoning tokens are billed as output. Converted at SARVAM_INR_PER_USD (= SHOTVI_USD_INR in .env) so the
    # ledger's rupees match Sarvam's; if SHOTVI_USD_INR changes, change it here too.
    "sarvam-105b": {"input": 15.0 / SARVAM_INR_PER_USD, "output": 60.0 / SARVAM_INR_PER_USD,
                    "cached": 5.0 / SARVAM_INR_PER_USD, "note": "native INR 15 / 60 / cached 5 per 1M, excl. GST"},
    "sarvam-105b-conversations": {"input": 15.0 / SARVAM_INR_PER_USD, "output": 60.0 / SARVAM_INR_PER_USD,
                                  "cached": 5.0 / SARVAM_INR_PER_USD, "note": "same rates as sarvam-105b"},
}


def price_of(model: str, overrides: dict | None = None) -> dict | None:
    if overrides and model in overrides:
        return overrides[model]
    return PRICES.get(model)


def cost_usd(model: str, input_tokens: int, output_tokens: int, overrides: dict | None = None,
             cached_tokens: int = 0) -> float | None:
    """Output tokens must already include thinking tokens; ``cached_tokens`` (part of input_tokens) are billed at the
    implicit-cache rate when the model has one. None if the model has no known price."""
    p = price_of(model, overrides)
    if p is None:
        return None
    if p.get("long") and input_tokens > p.get("long_threshold", 1 << 62):
        p = {**p, **p["long"]}
    cached = min(cached_tokens, input_tokens) if "cached" in p else 0
    return ((input_tokens - cached) * p["input"] + cached * p.get("cached", 0) + output_tokens * p["output"]) / 1e6


def cache_storage_usd(model: str, tokens: int, hours: float, overrides: dict | None = None) -> float | None:
    """Explicit-cache storage for ``tokens`` kept ``hours`` (billed per token-hour). None if the model has no
    known storage price (explicit caching is then unpriced: callers should treat it as unavailable)."""
    p = price_of(model, overrides)
    if p is None or "cache_storage_hour" not in p:
        return None
    return tokens * hours * p["cache_storage_hour"] / 1e6
