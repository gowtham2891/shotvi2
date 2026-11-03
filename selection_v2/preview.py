"""Preview renders of Stage 3 candidates for review (founder, 2026-10-27: "if video is also there it will be good").

Not the product renderer (no reframing, captions or polish): each kept segment is cut from the source mp4 with
ffmpeg (re-encoded so cuts are frame-accurate, 480p, AAC) and the segments are joined in order, so the preview plays
exactly the cut Stage 3 proposes, trims included. Output: ``runs/selection_v2/<id>/clips/<candidate_id>.mp4`` plus a
``.json`` sidecar with the segments it was built from (a changed candidate is re-rendered, an unchanged one reused).
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


def time_map(segments: list) -> list[dict]:
    """clip_time <-> episode_time: one row per kept segment with its offset inside the clip."""
    rows, t = [], 0.0
    for a, b in segments:
        a, b = float(a), float(b)
        rows.append({"clip_start": round(t, 3), "clip_end": round(t + (b - a), 3), "ep_start": a, "ep_end": b})
        t += b - a
    return rows


def to_clip_time(tmap: list[dict], ep_t: float) -> float | None:
    """Episode time -> clip time (None when the moment was cut out)."""
    for r in tmap:
        if r["ep_start"] - 1e-3 <= ep_t <= r["ep_end"] + 1e-3:
            return round(r["clip_start"] + (ep_t - r["ep_start"]), 3)
    return None


def to_episode_time(tmap: list[dict], clip_t: float) -> float | None:
    for r in tmap:
        if r["clip_start"] - 1e-3 <= clip_t <= r["clip_end"] + 1e-3:
            return round(r["ep_start"] + (clip_t - r["clip_start"]), 3)
    return None


def clip_path(run_dir: Path, cand_id: str) -> Path:
    return Path(run_dir) / "clips" / f"{cand_id}.mp4"


def is_current(run_dir: Path, c: dict) -> bool:
    p = clip_path(run_dir, c["id"])
    side = p.with_suffix(".json")
    if not p.exists() or p.stat().st_size == 0 or not side.exists():
        return False
    try:
        return json.loads(side.read_text(encoding="utf-8")).get("segments") == c["segments"]
    except Exception:
        return False


def _segment_cmd(src: Path, dst: Path, a: float, b: float, max_height: int) -> list[str]:
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(src),
            "-vf", f"scale=-2:'min({int(max_height)},ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2", "-video_track_timescale", "90000", str(dst)]


def render(src: Path, run_dir: Path, c: dict, max_height: int = 480, log=None) -> Path:
    """Render one candidate; returns the mp4 path. Raises with ffmpeg's stderr on failure."""
    dst = clip_path(run_dir, c["id"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    if is_current(run_dir, c):
        return dst
    segs = [(float(a), float(b)) for a, b in c["segments"] if float(b) - float(a) > 0.02]
    if not segs:
        raise ValueError(f"{c['id']}: no segments to render")
    with tempfile.TemporaryDirectory(prefix=f"clip_{c['id']}_") as td:
        parts = []
        for i, (a, b) in enumerate(segs):
            part = Path(td) / f"seg{i:02d}.mp4"
            cmd = _segment_cmd(src, part, a, b, max_height)
            if log:
                log("ffmpeg: " + " ".join(cmd))
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0 or not part.exists() or part.stat().st_size == 0:
                raise RuntimeError(f"ffmpeg failed (exit {r.returncode}) on {c['id']} segment {a:.1f}-{b:.1f}:\n{r.stderr[-1500:]}")
            parts.append(part)
        if len(parts) == 1:
            parts[0].replace(dst)
        else:
            lst = Path(td) / "list.txt"
            lst.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                   "-movflags", "+faststart", str(dst)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
                raise RuntimeError(f"ffmpeg concat failed (exit {r.returncode}) on {c['id']}:\n{r.stderr[-1500:]}")
    dst.with_suffix(".json").write_text(json.dumps({"id": c["id"], "segments": c["segments"], "start": c["start"], "end": c["end"], "time_map": time_map(c["segments"]),
                                                    "final_duration": c["final_duration"], "removed": c["removed"]}, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    return dst


def render_all(src: Path, run_dir: Path, cands: list[dict], max_height: int = 480, log=None, progress=None) -> dict:
    """Render every candidate; returns {id: path | error string}. ``progress(i, n, cand_id)`` is called before each."""
    out = {}
    for i, c in enumerate(cands):
        if progress:
            progress(i, len(cands), c["id"])
        try:
            out[c["id"]] = str(render(src, run_dir, c, max_height, log))
        except Exception as e:  # noqa: BLE001 - one bad clip must not stop the rest; the error is shown per clip
            out[c["id"]] = f"ERROR {type(e).__name__}: {e}"
    return out
