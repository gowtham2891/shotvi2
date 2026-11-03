"""Scene media for Stage 2: cut from the 720p mp4 with ffmpeg at the scene's start / end, downscaled (audio kept),
or audio only (16 kHz mono mp3) for podcasts; uploaded once through the existing Files API helper
(``shotvi.llm.gemini.upload_file``, cached ~46 h). Command construction is pure (testable); running it is not.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def ffmpeg_cmd(src: Path, dst: Path, start: float, end: float, mode: str = "video", max_height: int = 480) -> list[str]:
    """``mode`` video: H.264 mp4 downscaled to <= max_height (even dimensions), AAC audio; audio: 16 kHz mono mp3."""
    a, b = float(start), float(end)
    if b <= a:
        raise ValueError(f"end {b} <= start {a}")
    base = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(src)]
    if mode == "audio":
        return base + ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-q:a", "4", str(dst)]
    if mode != "video":
        raise ValueError(f"unknown media mode {mode!r}")
    vf = f"scale=-2:'min({int(max_height)},ih)'"
    return base + ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(dst)]


def scene_media_path(media_dir: Path, scene_id, start: float, end: float, mode: str) -> Path:
    ext = "mp3" if mode == "audio" else "mp4"
    return media_dir / f"scene_{int(scene_id):02d}_{start:.1f}-{end:.1f}_{mode}.{ext}"


def cut_scene(src: Path, media_dir: Path, scene_id, start: float, end: float, mode: str = "video",
              max_height: int = 480, log=None) -> Path:
    """Cut (cached: the file is reused when it exists and is non-empty). Raises with ffmpeg's stderr on failure."""
    media_dir.mkdir(parents=True, exist_ok=True)
    dst = scene_media_path(media_dir, scene_id, start, end, mode)
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    cmd = ffmpeg_cmd(src, dst, start, end, mode, max_height)
    if log:
        log("ffmpeg: " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed (exit {r.returncode}) cutting scene {scene_id} {start:.1f}-{end:.1f}:\n{r.stderr[-1500:]}")
    return dst


def upload(path: Path, mode: str = "video") -> dict:
    """Files API upload (resumable, cached by path/size/mtime). Returns {uri, name, mime_type}."""
    from shotvi.llm.gemini import upload_file
    mime = "audio/mpeg" if mode == "audio" else "video/mp4"
    return upload_file(str(path), mime_type=mime)


def content_key(path: Path) -> str:
    """sha256 of the file bytes: a cache identity that survives re-uploads (the Files API name changes every time a
    deleted file is uploaded again, so a stage that deletes its uploads must key its media on the content)."""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:24]


def media_part(uploaded: dict, mode: str, fps: float | None = None, key: str | None = None) -> dict:
    """``key`` is the response-cache identity of this media; default = the upload name (stable while the upload is
    kept). Pass ``content_key(path)`` when the upload is deleted after use, so a rerun replays from cache for free."""
    part = {"file_uri": uploaded["uri"], "mime_type": uploaded.get("mime_type") or ("audio/mpeg" if mode == "audio" else "video/mp4"),
            "key": f"{key or uploaded.get('name')}|{mode}|fps={fps}"}
    if mode == "video" and fps:
        part["fps"] = float(fps)
    return part


def frame_cmd(src: Path, dst: Path, t: float, width: int = 320) -> list[str]:
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{float(t):.3f}", "-i", str(src),
            "-frames:v", "1", "-vf", f"scale={int(width)}:-2", "-q:v", "4", str(dst)]


def extract_frame(src: Path, media_dir: Path, key: str, t: float, width: int = 320, log=None) -> Path:
    """One JPEG frame at time t (cached by key and time)."""
    media_dir.mkdir(parents=True, exist_ok=True)
    dst = media_dir / f"ref_{key}_{t:.1f}.jpg"
    if dst.exists() and dst.stat().st_size > 0:
        return dst
    cmd = frame_cmd(src, dst, t, width)
    if log:
        log("ffmpeg: " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed extracting a frame at {t:.1f} s:\n{r.stderr[-800:]}")
    return dst


def image_part(path: Path, key: str) -> dict:
    """Inline JPEG part for the Gemini client ({data: base64, mime_type}); ``key`` keeps the response cache stable."""
    import base64
    return {"data": base64.b64encode(Path(path).read_bytes()).decode("ascii"), "mime_type": "image/jpeg", "key": key}


def delete_upload(uploaded: dict, cache_dir: str = "runs/cache/files") -> bool:
    """Delete an uploaded file on Google's side right after use (it would expire after 48 h anyway) and forget the
    local upload record so the next run re-uploads. Returns True when Google confirmed the delete."""
    import json as _json
    from shotvi.llm.gemini import GeminiProvider
    name = (uploaded or {}).get("name")
    if not name:
        return False
    prov = GeminiProvider()
    ok = prov.delete_cache(name)        # same DELETE endpoint shape: /v1beta/files/<id>
    d = Path(cache_dir)
    if d.exists():
        for cf in d.glob("*.json"):
            try:
                if _json.loads(cf.read_text(encoding="utf-8")).get("name") == name:
                    cf.unlink()
            except (OSError, ValueError):
                pass
    return ok


def delete_local_media(media_dir: Path) -> int:
    """Remove every cut file and frame under the episode's media directory. Returns the number of files removed."""
    n = 0
    if Path(media_dir).exists():
        for f in Path(media_dir).iterdir():
            if f.is_file():
                f.unlink(); n += 1
    return n
