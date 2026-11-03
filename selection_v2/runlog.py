"""runs.csv: one line per stage run (and per Stage 2 scene call): episode, stage, status, duration, tokens, rupees."""
from __future__ import annotations

import csv
import time
from pathlib import Path

from .config import ROOT, inr_per_usd

LOG_COLUMNS = ["ts", "episode", "stage", "status", "duration_s", "input_tokens", "output_tokens", "cached_tokens",
               "cost_inr_est", "ledger_delta_inr", "inr_per_usd", "note"]


def append_log(cfg: dict, row: dict) -> Path:
    path = ROOT / cfg["paths"]["runs_log"]
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "inr_per_usd": inr_per_usd(cfg), **row}
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLUMNS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in LOG_COLUMNS})
    return path
