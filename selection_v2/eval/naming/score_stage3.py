"""Stage 3 against the founder-approved clips data/gold/<vid>.json (founder, 2026-11-04).

A gold clip counts as FOUND by a moment when the moment's payoff line falls inside the gold clip's played pieces
(or its region when the payoff sits in a cut gap); by a clip when the clip's played time covers at least half of the
gold clip's played time. Gold clips with founder_would_post "yes" and "maybe" are counted separately.

Usage: python -m selection_v2.eval.naming.score_stage3 <vid> ...   (reads runs/name_test/<vid>/stage3.<STAGE3_TAG, default v4>.json)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from selection_v2.eval.naming.name_test import OUT, S3_TAG, load
from selection_v2.stage1_facts import fmt_ts


def gold(vid: str) -> list[dict]:
    return json.loads(Path(f"data/gold/{vid}.json").read_text(encoding="utf-8"))["clips"]


def overlap(a: list[tuple], b: list[tuple]) -> float:
    return sum(max(0.0, min(x1, y1) - max(x0, y0)) for x0, x1 in a for y0, y1 in b)


def score(vid: str) -> dict:
    s3 = load(OUT / vid / f"stage3.{S3_TAG}.json")
    f = load(OUT / vid / "episode_facts.v4_scenes_find.json")
    L = {r["id"]: r for r in f["lines"]}
    out = {"gold": []}
    for g in gold(vid):
        gp = [(p["start"], p["end"]) for p in g["pieces"]]
        g_len = sum(b - a for a, b in gp)
        ms = [m["id"] for m in (s3.get("moments") or {}).get("moments") or []
              if any(a - 0.5 <= L[m["payoff"]]["start"] <= b + 0.5 for a, b in gp)
              or g["region"]["start"] <= L[m["payoff"]]["start"] <= g["region"]["end"]]
        cs = [c["id"] for c in (s3.get("clips") or {}).get("clips") or []
              if overlap([(p["start"], p["end"]) for p in c["pieces"]], gp) >= 0.5 * g_len]
        out["gold"].append({"id": g["id"], "post": g.get("founder_would_post"), "title": g.get("title"),
                            "time": f"{fmt_ts(g['region']['start'])}-{fmt_ts(g['region']['end'])}", "moments": ms, "clips": cs})
    out["n_moments"] = len((s3.get("moments") or {}).get("moments") or [])
    out["n_clips"] = len((s3.get("clips") or {}).get("clips") or [])
    return out


if __name__ == "__main__":
    for vid in sys.argv[1:]:
        r = score(vid)
        print(f"{vid}: {r['n_moments']} moments, {r['n_clips']} clips")
        for post in ("yes", "maybe"):
            gs = [g for g in r["gold"] if g["post"] == post]
            if gs:
                print(f"  founder '{post}': found by a moment {sum(1 for g in gs if g['moments'])}/{len(gs)}"
                      + (f", by a clip {sum(1 for g in gs if g['clips'])}/{len(gs)}" if r["n_clips"] else ""))
        for g in r["gold"]:
            print(f"   {g['id']} [{g['post']}] {g['time']} {g['title']} -> moments {g['moments']} clips {g['clips']}")
