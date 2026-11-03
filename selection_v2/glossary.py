"""Per-series glossary persistence (spec 4.2 / 10): ``data/series_glossary/<series_key>.json``. The episode pass of a
later episode of the same series gets the stored glossary in its cached prefix as a prior. Merge only appends
(new nicknames, running bits, catchphrases, relationships); nothing is ever deleted; the premise is replaced only
when the stored one is empty."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .config import ROOT


def glossary_path(series_key: str | None, cfg: dict) -> Path:
    d = ROOT / ((cfg.get("stage1b") or {}).get("glossary_dir") or "data/series_glossary")
    return d / f"{series_key or 'unknown_series'}.json"


def load_glossary(series_key: str | None, cfg: dict) -> dict | None:
    p = glossary_path(series_key, cfg)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _norm(s: str) -> str:
    return " ".join(str(s).lower().split())


def merge_glossary(old: dict | None, new: dict, video_id: str) -> dict:
    out = {"premise": "", "nicknames": [], "running_bits": [], "catchphrases": [], "relationships": [],
           "episodes": [], **(old or {})}
    for k in ("nicknames", "running_bits", "catchphrases", "relationships", "episodes"):
        out[k] = list(out.get(k) or [])
    if not out.get("premise") and new.get("premise"):
        out["premise"] = new["premise"]
    seen = {(_norm(x.get("name", "")), _norm(x.get("nickname", ""))) for x in out["nicknames"] if isinstance(x, dict)}
    for x in new.get("nicknames") or []:
        if not isinstance(x, dict):
            continue
        k = (_norm(x.get("name", "")), _norm(x.get("nickname", "")))
        if k not in seen:
            out["nicknames"].append({**x, "first_seen": video_id})
            seen.add(k)
    for key in ("running_bits", "catchphrases", "relationships"):
        have = {_norm(x) for x in out[key]}
        for x in new.get(key) or []:
            if _norm(x) and _norm(x) not in have:
                out[key].append(str(x))
                have.add(_norm(x))
    if video_id not in out["episodes"]:
        out["episodes"].append(video_id)
    out["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return out


def save_glossary(series_key: str | None, g: dict, cfg: dict) -> Path:
    p = glossary_path(series_key, cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
    return p
