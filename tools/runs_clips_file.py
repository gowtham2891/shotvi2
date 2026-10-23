"""One readable file with ONLY the selected clips of several runs of the same episode, grouped by run, each run
introduced by a note on what it is. Each clip: title, feeling, length, reason, and its lines as played with the
speaker labels THAT run used (Scribe labels, or the run's speaker layer). No full transcript, no timestamps.

    python tools/runs_clips_file.py p3XTZlZ6-xw --out emotion_first_episodes/surya_speaker_test_clips.md \
        --run "A|runs/emotion3|Speaker labels from Scribe (as ElevenLabs gives them)" \
        --run "B|runs/speaker_ab_surya/sarvam|Speaker labels from Sarvam" \
        --run "C|runs/speaker_ab_surya/llmfix|Speaker labels from the Gemini speaker fix"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from episode_dump import SERIES, as_played  # noqa: E402
from shotvi.segment import speaker_layer  # noqa: E402
from shotvi.select import webseries as ws  # noqa: E402


def episode_as_run_saw(doc: dict, vid: str):
    ep = ws.load_episode(vid, with_loudness=False, speaker_names=False)[0]   # Scribe labels, never names
    layer = speaker_layer.for_run(doc, vid)
    if layer is not None:
        speaker_layer.apply(ep.lines, ep.words, layer)
    return ep


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("--out", required=True)
    ap.add_argument("--run", action="append", required=True, help='"TAG|RUN_DIR|description"')
    ap.add_argument("--intro", default="")
    a = ap.parse_args(argv)
    vid = a.video_id
    runs = [r.split("|", 2) for r in a.run]
    L = [f"# {SERIES.get(vid, vid)} ({vid}): selected clips by run", ""]
    if a.intro:
        L += [a.intro, ""]
    L += ["| Run | What it is | Clips |", "|---|---|---|"]
    docs = {}
    for tag, rdir, desc in runs:
        docs[tag] = json.loads((ROOT / rdir / f"{vid}.json").read_text(encoding="utf-8"))
        L.append(f"| {tag} | {desc} | {len(docs[tag]['proposals'])} |")
    L.append("")
    for tag, rdir, desc in runs:
        doc = docs[tag]
        ep = episode_as_run_saw(doc, vid)
        L += [f"## Run {tag}: {desc}", ""]
        for n, c in enumerate(doc["proposals"], 1):
            L += [f"### {tag}{n}: {c.get('title', '')}",
                  f"- Feeling: {c.get('feeling') or '-'}; length {c.get('edited_duration', 0):.0f} s; "
                  f"lines {c.get('start_line')} to {c.get('end_line')}",
                  f"- Reason: {c.get('reason') or c.get('why_it_works') or '-'}", "", "```"]
            L += as_played(ep, c) + ["```", ""]
    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
