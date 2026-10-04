"""Thin provider-agnostic LLM client (structured JSON, retries, cache, cost ledger).

Providers implemented: gemini (REST). Add Claude / OpenAI-compatible by implementing ``base.Provider`` and
registering it in ``client._load_provider``. Model choice always comes from config (``ModelSpec``).
"""
from .base import BudgetExceeded, LLMError, ModelSpec, Provider, RawResponse, RetryableError
from .client import Ledger, LLMClient, get_provider
from .pricing import PRICES, cost_usd

__all__ = ["BudgetExceeded", "LLMError", "ModelSpec", "Provider", "RawResponse", "RetryableError", "Ledger",
           "LLMClient", "get_provider", "PRICES", "cost_usd"]
