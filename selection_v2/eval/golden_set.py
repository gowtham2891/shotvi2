"""golden_set.yaml: hand-checkable moments per episode, each backed by one or more SOURCES.

    episodes:
      <video_id>:
        series: Couple Goals
        duration: 1167.3
        moments:
          - id: m01
            start: 69.4            # episode seconds (the span of the highest-priority source)
            end: 125.6
            type: comedy           # comedy | emotional | hook | loop | unknown
            label: "..."
            tier: gold             # derived: gold = two different source KINDS agree, silver = one
            sources:
              - {kind: channel_short, short_id: ..., url: ..., views: 197160, start: 69.4, end: 125.6}
              - {kind: heatmap, start: ..., end: ..., note: ...}            # YouTube most-replayed peak
              - {kind: comment, start: ..., end: ..., text: ...}           # timestamped viewer comment
              - {kind: human, confidence: definite|maybe, start: ..., end: ..., clip_id: w01}

Two source entries describe the same moment when their spans overlap by IoU >= ``merge_iou`` or one lies mostly
inside the other (containment >= ``merge_containment``). The moment's span is taken from the highest-priority
source (human > channel_short > heatmap > comment). Sources carrying ``manual: true`` were typed by hand and are
kept by the importer; everything else is rebuilt from disk on every import.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "eval" / "golden_set.yaml"   # episode data, not code (2026-10-29)
KINDS = ("channel_short", "heatmap", "comment", "human")
PRIORITY = {"human": 0, "channel_short": 1, "heatmap": 2, "comment": 3}
MERGE_IOU = 0.3
MERGE_CONTAINMENT = 0.6
TYPES = ("comedy", "emotional", "hook", "loop", "unknown")


def iou(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    union = max(a1, b1) - min(a0, b0)
    return inter / union if union > 0 else 0.0


def containment(a0: float, a1: float, b0: float, b1: float) -> float:
    """Share of the SHORTER span that lies inside the other."""
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    short = min(a1 - a0, b1 - b0)
    return inter / short if short > 0 else 0.0


def same_moment(a: dict, b: dict, merge_iou: float = MERGE_IOU, merge_cont: float = MERGE_CONTAINMENT) -> bool:
    return (iou(a["start"], a["end"], b["start"], b["end"]) >= merge_iou
            or containment(a["start"], a["end"], b["start"], b["end"]) >= merge_cont)


def tier_of(sources: list[dict], rule: str = "kinds") -> str:
    """gold when two sources agree: ``rule='kinds'`` counts distinct source kinds (default), ``'entries'`` counts
    source entries (two different Shorts of the same moment would then make gold)."""
    n = len({s["kind"] for s in sources}) if rule == "kinds" else len(sources)
    return "gold" if n >= 2 else "silver"


def build_moments(sources: list[dict], rule: str = "kinds") -> list[dict]:
    """Cluster source entries into moments (greedy, in priority then time order)."""
    srcs = sorted(sources, key=lambda s: (PRIORITY.get(s["kind"], 9), s["start"]))
    moments: list[dict] = []
    for s in srcs:
        for m in moments:
            if same_moment(m, s):
                m["sources"].append(s)
                break
        else:
            moments.append({"start": float(s["start"]), "end": float(s["end"]), "type": s.get("type") or "unknown",
                            "label": s.get("label") or "", "sources": [s]})
    moments.sort(key=lambda m: m["start"])
    for i, m in enumerate(moments, 1):
        m["id"] = f"m{i:02d}"
        m["tier"] = tier_of(m["sources"], rule)
        # a type stated by a human source wins over unknown
        for s in m["sources"]:
            if s.get("type") and s["type"] != "unknown" and m["type"] == "unknown":
                m["type"] = s["type"]
        for s in m["sources"]:
            if s.get("label") and not m["label"]:
                m["label"] = s["label"]
    return moments


def load(path: Path = DEFAULT_PATH) -> dict:
    if not Path(path).exists():
        return {"episodes": {}}
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    d.setdefault("episodes", {})
    return d


def save(d: dict, path: Path = DEFAULT_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")


def moments_of(d: dict, vid: str, tiers: tuple[str, ...] = ("gold", "silver")) -> list[dict]:
    return [m for m in (d["episodes"].get(vid) or {}).get("moments", []) if m.get("tier") in tiers]


def validate(d: dict) -> list[str]:
    errs = []
    for vid, ep in (d.get("episodes") or {}).items():
        for m in ep.get("moments") or []:
            if not (isinstance(m.get("start"), (int, float)) and isinstance(m.get("end"), (int, float))):
                errs.append(f"{vid}/{m.get('id')}: start/end must be numbers")
            elif m["end"] <= m["start"]:
                errs.append(f"{vid}/{m.get('id')}: end <= start")
            if m.get("type") not in TYPES:
                errs.append(f"{vid}/{m.get('id')}: type {m.get('type')!r} not in {TYPES}")
            for s in m.get("sources") or []:
                if s.get("kind") not in KINDS:
                    errs.append(f"{vid}/{m.get('id')}: source kind {s.get('kind')!r} not in {KINDS}")
                if s.get("kind") == "human" and s.get("confidence") not in ("definite", "maybe"):
                    errs.append(f"{vid}/{m.get('id')}: human source needs confidence definite|maybe")
    return errs
