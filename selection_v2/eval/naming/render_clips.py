"""Cut every Stage 3 clip into its own mp4 (founder, 2026-11-04: "cut the clip and build html"): the played pieces in
order (the cold-open line first, drops left out) from the episode's source video, joined into one file.
review/clips/<vid>/<clip id>.mp4. Each piece gets PAD_IN before its first word and PAD_OUT after its last, so a word
is never clipped (render safety, not an editing choice).

Usage: python -m selection_v2.eval.naming.render_clips <vid> ...   (reads runs/name_test/<vid>/stage3.<STAGE3_TAG, default v4>.json)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from selection_v2.eval.naming.name_test import OUT, S3_TAG, load

PAD_IN, PAD_OUT = 0.1, 0.3
DEST = Path("review/clips" if S3_TAG == "v4" else f"review/clips_{S3_TAG}")


def render(src: Path, pieces: list[dict], dst: Path) -> None:
    args, filt = ["ffmpeg", "-y", "-loglevel", "error"], []
    for i, p in enumerate(pieces):
        a, b = max(0.0, p["start"] - PAD_IN), p["end"] + PAD_OUT
        args += ["-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(src)]
        filt.append(f"[{i}:v:0]scale=-2:720,setsar=1,fps=30[v{i}];[{i}:a:0]aresample=48000[a{i}]")
    n = len(pieces)
    graph = ";".join(filt) + ";" + "".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    args += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)]
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(args, check=True)


def run(vid: str, tag: str = S3_TAG) -> list[Path]:
    s3 = load(OUT / vid / f"stage3.{tag}.json")
    from selection_v2.media import source_video
    src = source_video(vid)
    out = []
    for c in s3["clips"]["clips"]:
        dst = DEST / vid / f"{c['id']}.mp4"
        render(src, c["pieces"], dst)
        out.append(dst)
        print(vid, c["id"], f"{c['duration']:.0f} s ->", dst, flush=True)
    return out


if __name__ == "__main__":
    for v in sys.argv[1:]:
        run(v)
