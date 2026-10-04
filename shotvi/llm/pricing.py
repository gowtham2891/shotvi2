"""USD prices per 1M tokens. Thinking tokens are billed as output.

Source: https://ai.google.dev/gemini-api/docs/pricing (checked 2026-09-27, paid tier, standard, text input).
Prices change: override per run with ``pricing`` in the select config instead of editing pipeline code.
``long`` applies when the prompt exceeds ``long_threshold`` input tokens.
"""
from __future__ import annotations

# Google Search grounding on Gemini 3.x: 5,000 free queries / month, then $14 per 1,000. Shotvi always records the
# paid rate per executed query (conservative: keeps the rupee cap safe even inside the free tier).
SEARCH_USD_PER_QUERY = 0.014

PRICES: dict[str, dict] = {
    # Gemini 3.x Flash: promotional price through 2026-12-31, list price after (1.50 / 7.50).
    "gemini-3.8-flash": {"input": 0.75, "output": 3.75, "cached": 0.075, "note": "promo to 2026-12-31; list 1.50/7.50"},
    "gemini-3.7-flash": {"input": 0.75, "output": 3.75, "note": "promo to 2026-12-31; list 1.50/7.50"},
    "gemini-3.6-flash": {"input": 0.75, "output": 3.75, "note": "promo to 2026-12-31; list 1.50/7.50"},
    "gemini-3.5-flash": {"input": 1.50, "output": 9.00},
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50, "cached": 0.03},
    "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00, "long_threshold": 200_000,
                               "long": {"input": 4.00, "output": 18.00}},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "long_threshold": 200_000,
                       "long": {"input": 2.50, "output": 15.00}},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
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
