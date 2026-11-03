"""Recall of golden-set moments in the Stage 3 candidate set and in the final set, boundary IoU of the matches, and
the pipeline picks that no source flagged (listed for review, never counted as errors).

    python -m selection_v2.eval.recall <video_id> [--iou 0.3] [--tiers gold,silver] [--json out.json]

Reads ``runs/selection_v2/<id>/episode_facts.json``: ``candidates[]`` (Stage 3: {id, start, end, segments?}) and
``final.clips[]`` (Stage 6). Until those stages exist the report says so and shows the moments only.

A moment is FOUND by a pick when IoU >= ``--iou`` or the pick covers >= ``--coverage`` of the moment (a longer
untrimmed candidate still "finds" a short moment). Boundary IoU is reported for the best match of each found moment.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config import load_config
from ..paths import EpisodePaths, load_json
from . import golden_set
from .golden_set import iou

COVERAGE = 0.5


def _span(pick: dict) -> tuple[float, float]:
    segs = pick.get("segments")
    if segs:
        return float(min(s[0] for s in segs)), float(max(s[1] for s in segs))
    return float(pick["start"]), float(pick["end"])


def match_moments(moments: list[dict], picks: list[dict], iou_thr: float, coverage: float = COVERAGE) -> list[dict]:
    rows = []
    for m in moments:
        best = None
        for pk in picks:
            a, b = _span(pk)
            inter = max(0.0, min(b, m["end"]) - max(a, m["start"]))
            cov = inter / (m["end"] - m["start"]) if m["end"] > m["start"] else 0.0
            j = iou(a, b, m["start"], m["end"])
            found = j >= iou_thr or cov >= coverage
            score = (found, j)
            if best is None or score > best[0]:
                best = (score, pk, j, cov)
        if best is None:
            rows.append({**{k: m[k] for k in ("id", "start", "end", "type", "tier", "label")}, "found": False,
                         "pick": None, "iou": 0.0, "coverage": 0.0})
        else:
            (found, _), pk, j, cov = best
            rows.append({**{k: m[k] for k in ("id", "start", "end", "type", "tier", "label")}, "found": found,
                         "pick": pk.get("id"), "pick_span": list(_span(pk)), "iou": round(j, 3), "coverage": round(cov, 3)})
    return rows


def unflagged_picks(moments: list[dict], picks: list[dict], iou_thr: float, coverage: float = COVERAGE) -> list[dict]:
    out = []
    for pk in picks:
        a, b = _span(pk)
        hit = False
        for m in moments:
            inter = max(0.0, min(b, m["end"]) - max(a, m["start"]))
            cov = inter / (m["end"] - m["start"]) if m["end"] > m["start"] else 0.0
            if iou(a, b, m["start"], m["end"]) >= iou_thr or cov >= coverage:
                hit = True
                break
        if not hit:
            out.append({"id": pk.get("id"), "start": a, "end": b, "type": pk.get("type"), "note": "no source flagged this"})
    return out


def report(vid: str, facts: dict | None, gs: dict, iou_thr: float = 0.3, tiers=("gold", "silver")) -> dict:
    moments = golden_set.moments_of(gs, vid, tuple(tiers))
    cands = list((facts or {}).get("candidates") or [])
    final = list(((facts or {}).get("final") or {}).get("clips") or [])
    out = {"video_id": vid, "iou_threshold": iou_thr, "tiers": list(tiers), "n_moments": len(moments),
           "n_gold": sum(m["tier"] == "gold" for m in moments), "n_silver": sum(m["tier"] == "silver" for m in moments),
           "n_candidates": len(cands), "n_final": len(final), "stages_present": sorted((facts or {}).get("stages", {})),
           "notes": []}
    if facts is None:
        out["notes"].append("no episode_facts.json yet")
    if not cands:
        out["notes"].append("no Stage 3 candidates in episode_facts.json (stage not built or not run)")
    if not final:
        out["notes"].append("no Stage 6 final set in episode_facts.json (stage not built or not run)")
    for name, picks in (("candidates", cands), ("final", final)):
        rows = match_moments(moments, picks, iou_thr) if picks else []
        for tier in ("gold", "silver", "all"):
            sub = [r for r in rows if tier == "all" or r["tier"] == tier]
            n = len(sub)
            out[f"recall_{name}_{tier}"] = round(sum(r["found"] for r in sub) / n, 3) if (n and picks) else None
        found = [r for r in rows if r["found"]]
        out[f"mean_iou_{name}"] = round(sum(r["iou"] for r in found) / len(found), 3) if found else None
        out[f"matches_{name}"] = rows
        out[f"unflagged_{name}"] = unflagged_picks(moments, picks, iou_thr) if picks else []
    return out


def format_report(r: dict) -> str:
    L = [f"# recall report {r['video_id']}", f"moments: {r['n_moments']} ({r['n_gold']} gold, {r['n_silver']} silver); "
         f"candidates: {r['n_candidates']}; final: {r['n_final']}; IoU >= {r['iou_threshold']}"]
    L += [f"note: {n}" for n in r["notes"]]
    for name in ("candidates", "final"):
        L.append(f"\n## {name}: recall gold {r[f'recall_{name}_gold']}, silver {r[f'recall_{name}_silver']}, "
                 f"all {r[f'recall_{name}_all']}; mean boundary IoU of found {r[f'mean_iou_{name}']}")
        for m in r[f"matches_{name}"]:
            L.append(f"  {'FOUND ' if m['found'] else 'missed'} {m['id']} {m['tier']:6s} {m['type']:9s} "
                     f"{m['start']:7.1f}-{m['end']:7.1f}  pick={m['pick']} iou={m['iou']} cov={m['coverage']}  {m['label'][:50]}")
        if r[f"unflagged_{name}"]:
            L.append(f"  picks no source flagged (review, not errors): "
                     + ", ".join(f"{u['id']} {u['start']:.1f}-{u['end']:.1f}" for u in r[f"unflagged_{name}"]))
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m selection_v2.eval.recall")
    ap.add_argument("video_id")
    ap.add_argument("--iou", type=float, default=0.3)
    ap.add_argument("--tiers", default="gold,silver")
    ap.add_argument("--golden", default=str(golden_set.DEFAULT_PATH))
    ap.add_argument("--json", default=None, help="also write the full report as JSON")
    a = ap.parse_args(argv)
    cfg = load_config()
    p = EpisodePaths(a.video_id, cfg)
    facts = load_json(p.facts) if p.facts.exists() else None
    r = report(a.video_id, facts, golden_set.load(Path(a.golden)), a.iou, tuple(a.tiers.split(",")))
    print(format_report(r))
    if a.json:
        Path(a.json).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
