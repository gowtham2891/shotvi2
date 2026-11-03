"""YouTube most-replayed peaks, entered by hand for now in ``heatmap_peaks.yaml``::

    episodes:
      j14Om1SaFYE:
        - {start: "1:10", end: "1:32", note: "first peak"}
        - {start: 480.0, end: 505.5}

Times are m:ss(.s) or seconds. Returns golden-set source entries of kind ``heatmap``.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ..stage1_facts import parse_ts

DEFAULT_PATH = Path(__file__).resolve().parent / "heatmap_peaks.yaml"


def load_peaks(vid: str, path: Path = DEFAULT_PATH) -> list[dict]:
    if not Path(path).exists():
        return []
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out = []
    for i, p in enumerate((d.get("episodes") or {}).get(vid) or [], 1):
        a, b = parse_ts(p["start"])[0], parse_ts(p["end"])[0]
        if b <= a:
            raise ValueError(f"heatmap peak {i} of {vid}: end {p['end']} <= start {p['start']}")
        out.append({"kind": "heatmap", "start": round(a, 2), "end": round(b, 2), "note": p.get("note") or "",
                    "label": p.get("note") or ""})
    return out
