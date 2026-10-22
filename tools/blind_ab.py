"""Blind A/B review for web-series runs (any episodes, cut = time segments): pool the clips two pipeline runs showed for the same episodes into one unlabeled run, so the
founder rates them without knowing which pipeline made which clip.

    python tools/blind_ab.py runs/blind_v1 runs/blind_v2 --out runs/blind_review --key runs/blind/key.json
    python tools/preview_clips.py runs/blind_review --top 100      # -> runs/previews/blind_review/review.html

- Only clips each run SHOWED are pooled (run documents' "proposals").
- A clip from A and a clip from B that play (almost) the same footage (overlap >= --same of BOTH clips' play time)
  are shown once; the key records it as shared.
- Order is shuffled per episode with a fixed seed; ids are neutral (B01, B02, ...), titles are hidden ("Clip 01"),
  and every field that could reveal the source (title, tier, scores, reason, feeling, stop/stay/land, why, hook,
  category, badges, verdicts, episode notes) is dropped. Only the cut itself (pieces, time segments, region,
  cold open) is kept.
- The key (blind id -> run, clip id, title) is written separately and must not be opened before rating.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

KEEP = ("pieces", "region", "edited_duration", "time_segments", "video_duration", "cold_open", "reordered",
        "n_blocks", "text", "start_line", "end_line", "content_type")


def segs(c: dict) -> list[tuple[float, float]]:
    ts = c.get("time_segments") or [{"start": p["start"], "end": p["end"]} for p in c["pieces"]]
    return [(float(s["start"]), float(s["end"])) for s in ts]


def overlap(a, b) -> float:
    return sum(max(0.0, min(e1, e2) - max(s1, s2)) for s1, e1 in a for s2, e2 in b)


def dur(a) -> float:
    return sum(e - s for s, e in a)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("run_a")
    ap.add_argument("run_b")
    ap.add_argument("--out", default="runs/blind_review")
    ap.add_argument("--key", default="runs/blind/key.json")
    ap.add_argument("--same", type=float, default=0.9, help="overlap share of BOTH clips to count as the same cut")
    ap.add_argument("--seed", type=int, default=20261022)
    a = ap.parse_args(argv)
    ra, rb, out = Path(a.run_a), Path(a.run_b), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)
    key = {"run_a": ra.as_posix(), "run_b": rb.as_posix(), "same_threshold": a.same, "seed": a.seed, "clips": []}
    vids = sorted({f.stem for f in ra.glob("*.json")} & {f.stem for f in rb.glob("*.json")})
    for vid in vids:
        docs = {}
        for tag, run in (("A", ra), ("B", rb)):
            d = json.loads((run / f"{vid}.json").read_text(encoding="utf-8"))
            if not isinstance(d, dict) or d.get("kind") != "proposals":
                break
            docs[tag] = d
        if len(docs) < 2:
            continue
        pool = []      # [{"clip": c, "sources": [(tag, id, title)]}]
        for tag in ("A", "B"):
            for c in docs[tag]["proposals"]:
                sc = segs(c)
                hit = next((p for p in pool if p["sources"][0][0] != tag
                            and overlap(sc, segs(p["clip"])) >= a.same * max(dur(sc), dur(segs(p["clip"])))), None)
                if hit is not None:
                    hit["sources"].append((tag, c["id"], c.get("title", "")))
                else:
                    pool.append({"clip": c, "sources": [(tag, c["id"], c.get("title", ""))]})
        rng.shuffle(pool)
        props = []
        for k, p in enumerate(pool, 1):
            bid = f"B{k:02d}"
            c = {f: p["clip"][f] for f in KEEP if f in p["clip"]}
            c.update(id=bid, rank=k, title=f"Clip {k:02d}", hook_line="", why_it_works="", scores={})
            props.append(c)
            key["clips"].append({"video_id": vid, "blind_id": bid, "rank": k,
                                 "sources": [{"run": {"A": ra.as_posix(), "B": rb.as_posix()}[t], "clip_id": i,
                                              "title": ti} for t, i, ti in p["sources"]],
                                 "shared": len(p["sources"]) > 1})
        doc = {"schema_version": "1.1", "kind": "proposals", "video_id": vid, "run_id": out.name, "proposals": props}
        (out / f"{vid}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        n_sh = sum(1 for x in key["clips"] if x["video_id"] == vid and x["shared"])
        print(f"{vid}: A {len(docs['A']['proposals'])} + B {len(docs['B']['proposals'])} shown -> "
              f"{len(props)} blind clips ({n_sh} shared)")
    Path(a.key).parent.mkdir(parents=True, exist_ok=True)
    Path(a.key).write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"blind run: {out}\nkey (do not open before rating): {a.key}")


if __name__ == "__main__":
    main()
