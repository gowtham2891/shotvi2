"""Score scene starts (founder, 2026-11-03) against the founder's key data/human/scenes_key_r1_2026-11-03.csv
(columns vid, new_scene_starts_at, soft): a clear change must be found on the same line; a "soft" change (the founder:
"can be continuous") counts neither as missed nor as wrong. Systems: the text step (1B) and every video-step run with
scenes in runs/name_test/<vid>/episode_facts.*scenes*.json.

Usage: python -m selection_v2.eval.naming.score_scenes [<key.csv>] <vid> ...
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from selection_v2.eval.naming.name_test import FACTS_1B, OUT, load

KEY = Path("data/human/scenes_key_r1_2026-11-03.csv")


def key(path: Path, vid: str) -> tuple[set, set]:
    with open(path, encoding="utf-8-sig") as fh:
        rows = [r for r in csv.DictReader(fh) if r["vid"] == vid]
    return {r["new_scene_starts_at"] for r in rows if not r["soft"]}, {r["new_scene_starts_at"] for r in rows if r["soft"]}


def systems(vid: str) -> dict[str, list[str]]:
    f = load(OUT / vid / FACTS_1B.get(vid, "facts_1b.json"))
    out = {"text step (1B)": [s["first_line"] for s in f["episode_pass"]["derived"]["scenes"]][1:]}
    for p in sorted((OUT / vid).glob("episode_facts.*scenes*.json")):
        out[p.name.removeprefix("episode_facts.").removesuffix(".json")] = [s["first_line"] for s in load(p)["identity_pass"]["scenes"]][1:]
    return out


def score(path: Path, vid: str) -> dict:
    hard, soft = key(path, vid)
    res = {}
    for name, starts in systems(vid).items():
        st = set(starts)
        res[name] = {"proposed": len(st), "found": len(st & hard), "of": len(hard), "missed": sorted(hard - st),
                     "wrong": sorted(st - hard - soft)}
    return res


if __name__ == "__main__":
    args = sys.argv[1:]
    path = Path(args.pop(0)) if args and args[0].endswith(".csv") else KEY
    for vid in args:
        print(vid)
        for name, r in score(path, vid).items():
            print(f"  {name:22s} found {r['found']}/{r['of']} · missed {r['missed']} · wrong {r['wrong']}")
