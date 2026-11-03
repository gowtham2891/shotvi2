"""Verified reference faces per SERIES (founder, 2026-10-26): after the founder reviews the Reference-faces row and pins
corrections, the final set for the episode is saved to ``data/series_faces/<series_key>/`` (faces.json + the JPEGs,
keyed by character NAME, since speaker keys are per episode). A later episode of the same series uses the bank first
and falls back to automatic selection only for names not in the bank.

faces.json::

    {"series_key": "surya", "updated": "...",
     "faces": {"<norm name>": {"name": "<Name>", "frames": [{"file": "<norm name>_<video_id>_<t>.jpg", "video_id", "t", "t_str",
                                                     "line", "why", "verified": true, "saved_at"}]}}}
"""
from __future__ import annotations

import json
import re
import shutil
import time
import unicodedata
from pathlib import Path

from .config import ROOT


def bank_dir(series_key: str | None, cfg: dict) -> Path:
    base = ROOT / ((cfg.get("stage2") or {}).get("faces_bank_dir") or "data/series_faces")
    return base / (series_key or "unknown_series")


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKC", str(name or "")).lower().replace("’", "'")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^\w' ఀ-౿]+", " ", s)
    return " ".join(s.split())


def load_bank(series_key: str | None, cfg: dict) -> dict:
    p = bank_dir(series_key, cfg) / "faces.json"
    if not p.exists():
        return {"series_key": series_key, "faces": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def save_bank(series_key: str | None, bank: dict, cfg: dict) -> Path:
    d = bank_dir(series_key, cfg)
    d.mkdir(parents=True, exist_ok=True)
    bank["series_key"] = series_key
    bank["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (d / "faces.json").write_text(json.dumps(bank, ensure_ascii=False, indent=1), encoding="utf-8")
    return d / "faces.json"


def save_verified(series_key: str | None, video_id: str, manifest: list[dict], cfg: dict, max_per_name: int = 4,
                  looks: dict | None = None) -> dict:
    """Store the episode's reviewed reference set in the series bank: per character name, this episode's frames
    replace older frames of the same episode and are kept alongside frames from other episodes (newest first, at
    most ``max_per_name``). Copies the JPEGs into the bank directory. Returns the saved bank."""
    bank = load_bank(series_key, cfg)
    d = bank_dir(series_key, cfg)
    d.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    by_name: dict[str, list[dict]] = {}
    for m in manifest:
        if not m.get("name") or not m.get("path") or not Path(m["path"]).exists():
            continue
        by_name.setdefault(norm(m["name"]), []).append(m)
    for key, frames in by_name.items():
        entry = bank["faces"].get(key) or {"name": frames[0]["name"], "frames": []}
        kept = [f for f in entry["frames"] if f.get("video_id") != video_id]        # replace this episode's old frames
        new = []
        for m in frames:
            fname = f"{re.sub(r'[^a-z0-9]+', '_', key).strip('_')}_{video_id}_{float(m['t']):.1f}.jpg"
            shutil.copyfile(m["path"], d / fname)
            new.append({"file": fname, "video_id": video_id, "t": float(m["t"]), "t_str": m.get("t_str"), "line": m.get("line"),
                        "why": m.get("why"), "verified": True, "saved_at": now})
        entry["frames"] = (new + kept)[:max_per_name]
        entry["name"] = frames[0]["name"]
        lk = (looks or {}).get(frames[0].get("key")) or (looks or {}).get(key)
        if lk and lk.get("look") and "not seen" not in str(lk.get("look")).lower():
            entry["look"] = lk["look"]                      # refreshed by every verified cast sheet (the look may change)
            entry["look_from"] = video_id
        bank["faces"][key] = entry
    save_bank(series_key, bank, cfg)
    return bank


def lookup(series_key: str | None, names_by_key: dict[str, str], cfg: dict) -> dict[str, list[dict]]:
    """speaker key -> verified frames from the bank, matched by character name (normalised, exact)."""
    bank = load_bank(series_key, cfg)
    d = bank_dir(series_key, cfg)
    out: dict[str, list[dict]] = {}
    for k, name in names_by_key.items():
        e = bank["faces"].get(norm(name))
        if not e:
            continue
        frames = [{"path": str(d / f["file"]), "t": f["t"], "t_str": f.get("t_str"), "line": f.get("line"),
                   "video_id": f.get("video_id"), "why": f"verified in episode {f.get('video_id')}", "bank": True,
                   "look": e.get("look")}
                  for f in e["frames"] if (d / f["file"]).exists()]
        if frames:
            out[k] = frames
    return out


def looks_for(series_key: str | None, names_by_key: dict[str, str], cfg: dict) -> dict[str, str]:
    """speaker key -> verified look description from the bank."""
    bank = load_bank(series_key, cfg)
    return {k: e["look"] for k, name in names_by_key.items() if (e := bank["faces"].get(norm(name))) and e.get("look")}
