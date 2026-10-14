"""Project-wide LLM spend meter with a hard cap in rupees.

Every billed API call from any part of Shotvi (pipeline, judge, scripts) is
recorded in ``spend/llm_spend.json`` and refused once the total reaches the cap.
The cap applies to the running total since the project started, not per run.

Config (env or .env):
  SHOTVI_SPEND_CAP_INR   total cap in rupees (default 200)
  SHOTVI_USD_INR         conversion rate used for the cap (default 90, on the
                         high side so the cap trips early rather than late)

Costs are estimates from token counts x ``pricing.PRICES``. The real bill is in
Google AI Studio / Cloud billing; if it differs, fix the price table.

CLI: python -m shotvi.llm.spend   (prints the total and the last calls)
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .base import BudgetExceeded

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "spend" / "llm_spend.json"
_lock = threading.Lock()


def _cfg(name: str, default: float) -> float:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    return float(os.environ.get(name, default))


def cap_inr() -> float:
    return _cfg("SHOTVI_SPEND_CAP_INR", 200)


def usd_inr() -> float:
    return _cfg("SHOTVI_USD_INR", 90)


def _load() -> dict:
    if LEDGER.exists():
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    return {"total_usd": 0.0, "calls": 0, "entries": []}


def _save(d: dict):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    # On Windows the replace fails while another process (a reader, antivirus, an editor) has the
    # ledger open. Retry briefly; losing a record would under-count spend, so never give up silently.
    for attempt in range(40):
        try:
            tmp.replace(LEDGER)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    raise RuntimeError(f"could not update spend ledger {LEDGER} (file locked); spend not recorded safely")


def total_usd() -> float:
    return _load()["total_usd"]


def total_inr() -> float:
    return total_usd() * usd_inr()


def check():
    """Raise BudgetExceeded if the next call would start at or above the cap."""
    spent, cap = total_inr(), cap_inr()
    if spent >= cap:
        raise BudgetExceeded(f"LLM spend cap reached: ₹{spent:.0f} spent of ₹{cap:.0f} "
                             f"(raise SHOTVI_SPEND_CAP_INR to continue)")


def record(model: str, input_tokens: int, output_tokens: int, source: str = "", search_queries: int = 0,
           cached_tokens: int = 0):
    """Record one billed call. output_tokens must include thinking tokens. ``search_queries`` (Google Search
    grounding) are charged at pricing.SEARCH_USD_PER_QUERY each, even inside the free tier. ``cached_tokens``
    (part of input_tokens; explicit or implicit context-cache hits) are priced at the model's cached-input rate."""
    from .pricing import SEARCH_USD_PER_QUERY, cost_usd
    usd = cost_usd(model, input_tokens, output_tokens, cached_tokens=cached_tokens)
    if usd is None:   # unknown price: record tokens, count it expensive so the cap still bites
        usd = (input_tokens * 5 + output_tokens * 20) / 1e6
    usd += search_queries * SEARCH_USD_PER_QUERY
    _append({"model": model, "in": input_tokens, "out": output_tokens, "usd": round(usd, 6), "source": source,
             **({"search_queries": search_queries} if search_queries else {}),
             **({"cached_in": cached_tokens} if cached_tokens else {})})
    return usd


def record_fixed(model: str, usd: float, source: str = "", note: str = "", tokens: int = 0):
    """Record a billed item that is not a generate call (explicit cache storage for its TTL, a cache write)."""
    _append({"model": model, "in": tokens, "out": 0, "usd": round(usd, 6), "source": source, "note": note})
    return usd


def _append(entry: dict):
    with _lock:
        d = _load()
        d["total_usd"] = round(d["total_usd"] + entry["usd"], 6)
        d["calls"] += 1
        d["entries"].append({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry})
        d["entries"] = d["entries"][-2000:]
        _save(d)


if __name__ == "__main__":
    d, rate = _load(), usd_inr()
    print(f"total: ${d['total_usd']:.3f} ≈ ₹{d['total_usd'] * rate:.0f} of ₹{cap_inr():.0f} cap "
          f"({d['calls']} calls, rate ₹{rate:.0f}/$)")
    for e in d["entries"][-10:]:
        print(f"  {e['t']}  {e['model']:24} ${e['usd']:.4f}  {e.get('source', '')}")
