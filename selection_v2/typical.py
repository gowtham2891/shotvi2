"""The channel's typical Short length (founder, 2026-10-30), shared by Stage 5 (which variant of a moment to judge) and
Stage 6 (which version is the primary). Scripted series only: channel Shorts of episodes whose Stage 1B content_type is
``scripted_series``; podcasts / vodcasts / talking heads never count."""
from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT


def typical_length(facts: dict, cfg: dict, root: Path = ROOT) -> dict:
    """founder, 2026-10-30: the channel's typical Short length = the median duration of its matched channel Shorts from
    SCRIPTED-SERIES episodes only (data/channel_shorts, episode content_type from its Stage 1B), clamped to the band;
    ``default_s`` when the channel has none. {target_s, band_s, median_s, n, channel, source}."""
    tl = (cfg.get("stage6") or {}).get("typical_length") or {}
    default, band = float(tl.get("default_s", 65)), [float(x) for x in (tl.get("band_s") or [45, 90])]
    chan = " ".join(str((facts.get("episode") or {}).get("channel") or "").split()).casefold()
    durs = []
    for p in sorted((root / "data" / "channel_shorts").glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        vid = d.get("episode_id") or p.stem
        fp = root / cfg["paths"]["runs_dir"] / vid / "episode_facts.json"
        if not fp.exists() or (json.loads(fp.read_text(encoding="utf-8")).get("episode_pass") or {}).get("content_type") != "scripted_series":
            continue
        durs += [float(s["duration"]) for s in d.get("accepted") or [] if s.get("duration") and " ".join(str(s.get("channel") or "").split()).casefold() == chan]
    if len(durs) >= int(tl.get("min_shorts", 1)) and durs:
        med = sorted(durs)[len(durs) // 2] if len(durs) % 2 else (sorted(durs)[len(durs) // 2 - 1] + sorted(durs)[len(durs) // 2]) / 2
        # founder, 2026-10-30: length is never a rule but the platform maximum: the median is used as is (band reported only)
        return {"target_s": med, "band_s": band, "median_s": med, "n": len(durs), "channel": chan, "source": "channel Shorts"}
    return {"target_s": default, "band_s": band, "median_s": None, "n": 0, "channel": chan, "source": "default (no matched channel Shorts)"}
