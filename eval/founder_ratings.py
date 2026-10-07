"""Write the founder's ratings of GT clips into the gold files.

    python -m eval.founder_ratings data/human/ratings_gt_gh2_r1.csv [--gold-dir data/gold] [--dry-run]

CSV columns (data/human/ratings_gt_*.csv): video_id, clip_id, hook_1to5, standalone_1to5, payoff_1to5,
overall_1to5, would_post (yes | maybe | no), optional notes. For each row the gold clip gets
`founder_would_post` and `founder_scores` {hook, standalone, payoff, overall}, the same layout the Hello Madam
gold (data/gold/epZ0lbXkgWA.json) carries; the file's `founder_review` points at the CSV. Clips not in the CSV are
left as they are (e.g. `held` = hidden by the judge gate, never shown to the founder). A CSV row whose clip does
not exist in the gold file is an error (the file is not written). No LLM, no network.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from .common import ROOT, load_json, resolve, utf8_stdout, write_json

RATED = ("yes", "maybe", "no")
SCORE_COLS = {"hook": "hook_1to5", "standalone": "standalone_1to5", "payoff": "payoff_1to5", "overall": "overall_1to5"}


def _num(x):
    x = (x or "").strip()
    if not x:
        return None
    f = float(x)
    return int(f) if f.is_integer() else f


def read_ratings(csv_path):
    """{video_id: {clip_id: {would_post, scores{hook,standalone,payoff,overall}, notes}}}."""
    out = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            vid, cid = row["video_id"].strip(), row["clip_id"].strip()
            wp = (row.get("would_post") or "").strip().lower()
            if wp not in RATED:
                raise ValueError(f"{csv_path} line {i}: would_post={row.get('would_post')!r} not in {RATED}")
            if cid in out.setdefault(vid, {}):
                raise ValueError(f"{csv_path} line {i}: duplicate rating for {vid} {cid}")
            out[vid][cid] = {"would_post": wp, "scores": {k: _num(row.get(c)) for k, c in SCORE_COLS.items()},
                             "notes": (row.get("notes") or "").strip()}
    return out


def apply_to_gold(gold, ratings, source):
    """Write one video's ratings into a loaded gold dict. Returns {verdict: count} for the rated clips."""
    by_id = {c.get("id"): c for c in gold.get("clips") or []}
    missing = [cid for cid in ratings if cid not in by_id]
    if missing:
        raise KeyError(f"{gold.get('video_id')}: rated clips not in the gold file: {', '.join(missing)}")
    counts = {}
    for cid, r in ratings.items():
        c = by_id[cid]
        c["founder_would_post"] = r["would_post"]
        c["founder_scores"] = dict(r["scores"])
        if r["notes"]:
            c["founder_notes"] = r["notes"]
        counts[r["would_post"]] = counts.get(r["would_post"], 0) + 1
    gold["founder_review"] = source
    return counts


def run(csv_path, gold_dir=None, dry_run=False):
    csv_path = resolve(csv_path)
    gold_dir = resolve(gold_dir) if gold_dir else ROOT / "data" / "gold"
    src = str(csv_path.relative_to(ROOT)).replace("\\", "/") if csv_path.is_relative_to(ROOT) else str(csv_path)
    report = {}
    for vid, rs in read_ratings(csv_path).items():
        gp = gold_dir / f"{vid}.json"
        if not gp.exists():
            raise FileNotFoundError(f"no gold file for {vid}: {gp}")
        gold = load_json(gp)
        counts = apply_to_gold(gold, rs, src)
        verdicts = {}
        for c in gold.get("clips") or []:
            v = str(c.get("founder_would_post"))
            verdicts[v] = verdicts.get(v, 0) + 1
        report[vid] = {"rated": counts, "file": verdicts}
        if not dry_run:
            write_json(gp, gold)
    return report


def main(argv=None):
    utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv")
    ap.add_argument("--gold-dir")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rep = run(a.csv, a.gold_dir, a.dry_run)
    for vid, r in rep.items():
        print(f"{vid}: wrote {r['rated']}; file now {r['file']}" + (" (dry run)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
