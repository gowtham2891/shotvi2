"""Blind A/B review: mix the top clips of two runs of the same episode, hide which run
each came from (and the pipeline's score / rank / tier), and build the usual review page.

usage: python tools/blind_review.py <video_id> runs/<A> runs/<B> [--top 8] [--out r10_blind] [--seed 7]
writes runs/previews/<out>/{review.html, NN_xx.mp4/.txt, ratings.csv}
and the answer key to runs/previews/<out>/key.json (don't open it before rating).
"""
import argparse
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preview_clips import clip_meta, padded_segments, render, speaker_labels, transcript_lines, transcript_text  # noqa: E402
from preview_page import build_page  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("run_a")
    ap.add_argument("run_b")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--out", default="r10_blind")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    vid = args.video_id
    vdir = Path("scribe_dataset") / vid
    words = json.loads((vdir / f"{vid}_scribe_transcript.json").read_text(encoding="utf-8"))["word_timestamps"]
    labels = speaker_labels(vid)
    picks = []
    for tag, run in (("A", args.run_a), ("B", args.run_b)):
        doc = json.loads((Path(run) / f"{vid}.json").read_text(encoding="utf-8"))
        for c in sorted(doc["proposals"], key=lambda c: c["rank"])[:args.top]:
            picks.append((tag, Path(run).name, c))
    random.Random(args.seed).shuffle(picks)

    out_root = Path("runs/previews") / args.out
    out_dir = out_root / vid
    out_dir.mkdir(parents=True, exist_ok=True)
    clips, rows, key = [], [], []
    for n, (tag, run, c) in enumerate(picks, 1):
        out = out_dir / f"{n:02d}_blind.mp4"
        segs = padded_segments(c["pieces"], words)
        if not out.exists():
            render(vdir / f"{vid}.mp4", segs, out)
        meta = clip_meta(c)
        # blind: hide everything that would reveal the version or the pipeline's opinion
        meta.update({"rank": n, "id": f"x{n:02d}", "score": None, "scores": {}, "tier": "", "flags": [],
                     "edit_notes": []})
        lines = transcript_lines(c, segs, words, labels)
        out.with_suffix(".txt").write_text(transcript_text(meta, lines, out.name), encoding="utf-8")
        clips.append({**meta, "video": out.relative_to(out_root).as_posix(), "lines": lines})
        rows.append({"video_id": vid, "rank": n, "clip_id": f"x{n:02d}", "title": c["title"],
                     "file": out.relative_to(out_root).as_posix(), "pipeline_overall": "",
                     "hook_1to5": "", "standalone_1to5": "", "payoff_1to5": "", "overall_1to5": "",
                     "would_post": "", "notes": ""})
        key.append({"blind": f"x{n:02d}", "version": tag, "run": run, "clip_id": c["id"], "rank_in_run": c["rank"],
                    "overall": (c.get("scores") or {}).get("overall"), "title": c["title"]})
        print(f"x{n:02d}  <- hidden  ({len(c['pieces'])} pieces, {c['edited_duration']:.0f}s)")
    page = [{"video_id": f"{vid} · blind test ({len(clips)} clips, mixed)", "n_total": len(clips), "clips": clips}]
    (out_root / "review.html").write_text(build_page(args.out, page), encoding="utf-8")
    (out_root / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    sheet = out_root / "ratings.csv"
    if not sheet.exists():
        with open(sheet, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    print(f"review page: {out_root / 'review.html'}\nanswer key (open after rating): {out_root / 'key.json'}")


if __name__ == "__main__":
    main()
