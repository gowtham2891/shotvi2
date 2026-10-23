"""One review page comparing several runs of the SAME episode: each run is its own section (heading = the run's
description), each with its own speaker labels (the run's speaker layer when it used one) and its own rendered clips.
Not blind: the section says which run made the clip.

    python tools/compare_runs_page.py p3XTZlZ6-xw --name surya_speaker_compare \
        --run "A|runs/emotion3|Run A: speaker labels from Scribe" \
        --run "B|runs/speaker_ab_surya/sarvam|Run B: speaker labels from Sarvam" \
        --run "C|runs/speaker_ab_surya/llmfix|Run C: speaker labels from the Gemini speaker fix"
    -> runs/previews/<name>/review.html (+ ratings.csv)
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from preview_clips import (asr_marks, clip_meta, clip_segments, render, speaker_labels,  # noqa: E402
                           transcript_lines, transcript_text)
from preview_page import build_page  # noqa: E402
from shotvi.segment import speaker_layer  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("--name", required=True)
    ap.add_argument("--run", action="append", required=True, help='"TAG|RUN_DIR|section heading"')
    ap.add_argument("--skip-existing", action="store_true")
    a = ap.parse_args(argv)
    vid = a.video_id
    out_root = ROOT / "runs" / "previews" / a.name
    vdir = ROOT / "scribe_dataset" / vid
    page, rows = [], []
    for spec in a.run:
        tag, rdir, desc = spec.split("|", 2)
        doc = json.loads((ROOT / rdir / f"{vid}.json").read_text(encoding="utf-8"))
        words = json.loads((vdir / f"{vid}_scribe_transcript.json").read_text(encoding="utf-8"))["word_timestamps"]
        labels = speaker_labels(vid)
        layer = speaker_layer.for_run(doc, vid)
        if layer is not None:
            speaker_layer.apply([], words, layer)
            labels = {}
        marks = asr_marks(vid, words, True)
        out_dir = out_root / tag
        out_dir.mkdir(parents=True, exist_ok=True)
        entry = {"video_id": desc, "n_total": len(doc["proposals"]), "clips": []}
        for n, c in enumerate(doc["proposals"], 1):
            c = {**c, "rank": n, "id": f"{tag}{n}", "title": f"{tag}{n} · {c.get('title', '')}"}
            out = out_dir / f"{n:02d}_{tag}{n}.mp4"
            segs = clip_segments(c, words)
            if not (out.exists() and a.skip_existing):
                render(vdir / f"{vid}.mp4", segs, out)
            meta, lines = clip_meta(c), transcript_lines(c, segs, words, labels, marks)
            out.with_suffix(".txt").write_text(transcript_text(meta, lines, out.name), encoding="utf-8")
            entry["clips"].append({**meta, "video": out.relative_to(out_root).as_posix(), "lines": lines})
            rows.append({"run": tag, "clip_id": f"{tag}{n}", "title": c["title"], "file": out.relative_to(out_root).as_posix(),
                         "hook_1to5": "", "standalone_1to5": "", "payoff_1to5": "", "overall_1to5": "",
                         "would_post": "", "notes": ""})
            print(f"{tag}{n}: {len(segs)} segments -> {out.relative_to(ROOT)}")
        page.append(entry)
    (out_root / "review.html").write_text(build_page(a.name, page), encoding="utf-8")
    sheet = out_root / "ratings.csv"
    if not sheet.exists():
        with open(sheet, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    print(f"review page: {(out_root / 'review.html').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
