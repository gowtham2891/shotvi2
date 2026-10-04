import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp


def extract_video_id(url):
    try:
        parsed = urlparse(url)
        if parsed.netloc == "youtu.be":
            return parsed.path.lstrip("/").split("/")[0] or None
        if "youtube.com" in parsed.netloc:
            qs = parse_qs(parsed.query)
            if "v" in qs:
                return qs["v"][0]
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in ("shorts", "embed", "v"):
                return parts[1]
    except Exception:
        pass
    return None


def extract_audio(video_path, audio_path):
    cmd = ["ffmpeg", "-i", str(video_path), "-vn", "-acodec", "pcm_s16le",
           "-ar", "16000", "-ac", "1", "-y", str(audio_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed:\n{result.stderr}")
    return Path(audio_path)


def download_youtube(url, out_dir="."):
    vid = extract_video_id(url)
    out = Path(out_dir) / (vid or "download")
    out.mkdir(parents=True, exist_ok=True)

    opts_base = {
        "format": "bestvideo[height<=720][vcodec^=avc]+bestaudio/best[height<=720]",
        "merge_output_format": "mp4",
        "outtmpl": str(out / "%(id)s.%(ext)s"),
        "quiet": False,
        "no_warnings": False,
        "js_runtimes": {"node": {}},
        "remote_components": {"ejs": "github"},
        "sleep_interval": 1,
        "max_sleep_interval": 3,
        "retries": 3,
        "fragment_retries": 3,
    }
    if Path("cookies.txt").exists():
        opts_base["cookiefile"] = "cookies.txt"

    info = None
    last_msg = ""
    for client in [None, "android", "ios", "tv_embedded", "web_safari"]:
        opts = dict(opts_base)
        if client:
            opts["extractor_args"] = {"youtube": {"player_client": [client]}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except yt_dlp.utils.DownloadError as e:
            last_msg = str(e)
            if ("Sign in to confirm" in last_msg or "LOGIN_REQUIRED" in last_msg
                    or "not a bot" in last_msg or "consent" in last_msg.lower()):
                raise RuntimeError("YouTube blocked the download (bot check / "
                                   "sign-in required).") from e
            print(f"client={client or 'default'} failed, trying next")
            for part in out.glob("*.part"):
                try:
                    part.unlink()
                except OSError:
                    pass
    if info is None:
        raise RuntimeError(f"YouTube download failed: {last_msg[-300:]}")

    video_id = info["id"]
    video_path = out / f"{video_id}.mp4"
    audio_path = out / f"{video_id}_audio.wav"
    extract_audio(video_path, audio_path)
    return {"video_id": video_id, "title": info.get("title", ""),
            "duration": info.get("duration", 0),
            "video_path": str(video_path), "audio_path": str(audio_path)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python download_youtube.py <youtube_url> [out_dir]")
        sys.exit(1)
    r = download_youtube(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ".")
    print(f"title:    {r['title']}")
    print(f"duration: {r['duration']}s")
    print(f"video:    {r['video_path']}")
    print(f"audio:    {r['audio_path']}")
