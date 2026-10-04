"""Rank correlation between a run's order and the founder's overall ratings (IN-SAMPLE calibration signal).

usage: python -m shotvi.select.rankcorr runs/<target_run> [runs/<other_run> ...]

Every rated clip in data/human/ratings_sel_*.csv is located in the run it was rated from (its source time span),
then matched to the clip of the target run that covers the same moment (time overlap >= 50% of the shorter span).
If a moment was rated in several rounds, the latest rating is used. Matched clips that the target run does not show
share the position after its last shown clip. Reports Spearman(rank position, human overall) per video (higher =
the run puts the clips the founder liked nearer the top).
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

from .calibrate import spearman

HUMAN = Path("data/human")


def spans(doc):
    out = {}
    for key in ("proposals", "rejected"):
        for p in doc.get(key) or []:
            out[p["id"]] = (p["region"]["start"], p["region"]["end"], p.get("rank"))
    return out


def ratings():
    rows = []
    for f in sorted(HUMAN.glob("ratings_sel_*.csv")):
        m = re.match(r"ratings_(sel_r(\d+)_\w+)\.csv", f.name)
        if not m:
            continue
        run, rnd = m.group(1), int(m.group(2))
        with open(f, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                rows.append({"run": run, "round": rnd, "video": r["video_id"], "clip": r["clip_id"],
                             "overall": float(r["overall_1to5"])})
    return rows


def overlap(a, b):
    inter = min(a[1], b[1]) - max(a[0], b[0])
    return inter / max(1e-6, min(a[1] - a[0], b[1] - b[0])) if inter > 0 else 0.0


def evaluate(target: str) -> dict:
    res = {}
    rated = ratings()
    by_video: dict[str, list] = {}
    for r in rated:
        f = Path("runs") / r["run"] / f"{r['video']}.json"
        if not f.exists():
            continue
        sp = spans(json.loads(f.read_text(encoding="utf-8"))).get(r["clip"])
        if sp:
            by_video.setdefault(r["video"], []).append({**r, "span": sp[:2]})
    for vid, rs in by_video.items():
        tf = Path(target) / f"{vid}.json"
        if not tf.exists():
            continue
        tdoc = json.loads(tf.read_text(encoding="utf-8"))
        tsp = spans(tdoc)
        n_shown = len(tdoc["proposals"])
        matched: dict[str, dict] = {}
        for r in sorted(rs, key=lambda r: r["round"]):   # later rounds overwrite earlier ones
            best = max(tsp.items(), key=lambda kv: overlap(r["span"], kv[1][:2]), default=None)
            if best and overlap(r["span"], best[1][:2]) >= 0.5:
                cid, (s, e, rank) = best
                matched[cid] = {"human": r["overall"], "pos": rank if rank else n_shown + 1,
                                "rated": f"{r['run']}:{r['clip']}"}
        if len(matched) >= 3:
            h = [m["human"] for m in matched.values()]
            pos = [-m["pos"] for m in matched.values()]
            rho = spearman(pos, h)
        else:
            rho = None
        res[vid] = {"n_matched": len(matched), "spearman": None if rho is None else round(rho, 3),
                    "clips": dict(sorted(matched.items(), key=lambda kv: kv[1]["pos"]))}
    return res


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for run in argv:
        for vid, r in evaluate(run).items():
            print(run, vid, "n", r["n_matched"], "spearman", r["spearman"],
                  [(cid, m["pos"], m["human"]) for cid, m in r["clips"].items()])


if __name__ == "__main__":
    main()
