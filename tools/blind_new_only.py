"""Blind review of ONLY the clips not rated before: pool the shown clips of several runs, drop every clip that plays
(almost) the same footage as a clip the founder already rated (its old rating is carried over in the key), merge
identical clips across runs, shuffle, hide every field that could reveal the source (as tools/blind_ab.py does).

    python tools/blind_new_only.py --runs runs/speaker_ab/sarvam runs/speaker_ab/llm_fix \
        --rated runs/blind_review --ratings data/human/ratings_blind_ab_r1.csv \
        --out runs/blind_review_spk --key runs/speaker_ab/key.json
    python tools/preview_clips.py runs/blind_review_spk --top 100

"Same footage" = overlap >= --same (default 0.9) of BOTH clips' play time (as blind_ab.py).
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

from blind_ab import KEEP, dur, overlap, segs


def same(a, b, th) -> bool:
    return overlap(a, b) >= th * max(dur(a), dur(b))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--rated", required=True, help="blind run dir whose clips were rated (B-ids)")
    ap.add_argument("--ratings", required=True, help="founder ratings CSV for --rated")
    ap.add_argument("--out", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--same", type=float, default=0.9)
    ap.add_argument("--seed", type=int, default=20261023)
    a = ap.parse_args(argv)
    rng = random.Random(a.seed)
    rat = {(r["video_id"], r["clip_id"]): r for r in csv.DictReader(open(a.ratings, encoding="utf-8-sig"))}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    key = {"runs": a.runs, "rated": a.rated, "ratings": a.ratings, "same_threshold": a.same, "seed": a.seed,
           "carried_over": [], "clips": []}
    vids = sorted(set.intersection(*[{f.stem for f in Path(r).glob("*.json")
                                      if not f.stem.startswith("dry_run")} for r in a.runs]))
    for vid in vids:
        rated = json.loads((Path(a.rated) / f"{vid}.json").read_text(encoding="utf-8"))["proposals"]
        pool = []
        for run in a.runs:
            d = json.loads((Path(run) / f"{vid}.json").read_text(encoding="utf-8"))
            for c in d["proposals"]:
                sc = segs(c)
                old = next((r for r in rated if same(sc, segs(r), a.same)), None)
                if old is not None:
                    rr = rat.get((vid, old["id"]), {})
                    key["carried_over"].append({"video_id": vid, "run": run, "clip_id": c["id"],
                                                "title": c.get("title", ""), "rated_blind_id": old["id"],
                                                "would_post": rr.get("would_post"),
                                                "overall_1to5": rr.get("overall_1to5"),
                                                "hook_1to5": rr.get("hook_1to5")})
                    continue
                hit = next((p for p in pool if same(sc, segs(p["clip"]), a.same)), None)
                src = {"run": run, "clip_id": c["id"], "title": c.get("title", "")}
                if hit is not None:
                    hit["sources"].append(src)
                else:
                    pool.append({"clip": c, "sources": [src]})
        rng.shuffle(pool)
        props = []
        for k, p in enumerate(pool, 1):
            bid = f"N{k:02d}"
            c = {f: p["clip"][f] for f in KEEP if f in p["clip"]}
            c.update(id=bid, rank=k, title=f"Clip {k:02d}", hook_line="", why_it_works="", scores={})
            props.append(c)
            key["clips"].append({"video_id": vid, "blind_id": bid, "rank": k, "sources": p["sources"],
                                 "shared": len({s["run"] for s in p["sources"]}) > 1})
        doc = {"schema_version": "1.1", "kind": "proposals", "video_id": vid, "run_id": out.name, "proposals": props}
        (out / f"{vid}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        n_co = sum(1 for x in key["carried_over"] if x["video_id"] == vid)
        print(f"{vid}: {len(props)} new clips to rate; {n_co} run clips match an already-rated clip")
    Path(a.key).parent.mkdir(parents=True, exist_ok=True)
    Path(a.key).write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"key (do not open before rating): {a.key}")


if __name__ == "__main__":
    main()
