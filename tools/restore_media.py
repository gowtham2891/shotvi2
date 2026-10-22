"""Re-download the episode / Short videos listed in youtube_links.txt and put them back where they were
(<id>.mp4 + <id>_audio.wav, 16 kHz mono), e.g. after copying the project to a new machine without media.

    python tools/restore_media.py                 # episodes first, then Shorts
    python tools/restore_media.py --episodes-only
    python tools/restore_media.py --dry-run       # list what would be downloaded where

Each URL in youtube_links.txt follows a comment "# N file(s), X MB, was in: <dir> ; <dir> ...". A scribe_dataset
dir ending in creator_shorts / channel_shorts gets the Short's files directly in it; any other video is a full episode
and goes to scribe_dataset/<id>/ (preview clips under runs/previews are rebuilt from it by tools/preview_clips.py).
Files already present are skipped.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from download_youtube import download_youtube, extract_video_id  # noqa: E402


def plan(links: Path) -> list[dict]:
    items, where = [], ""
    for raw in links.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            where = line.split("was in:", 1)[1] if "was in:" in line else ""
            continue
        if not line.startswith("http"):
            continue
        vid = extract_video_id(line)
        dirs = [d.strip().replace("\\", "/") for d in where.split(";") if d.strip()]
        short_dir = next((d for d in dirs if d.startswith("scribe_dataset/")
                          and d.rstrip("/").endswith(("creator_shorts", "channel_shorts"))), None)
        if short_dir:
            items.append({"url": line, "id": vid, "kind": "short", "dest": ROOT / short_dir})
        else:
            items.append({"url": line, "id": vid, "kind": "episode", "dest": ROOT / "scribe_dataset" / vid})
        where = ""
    return items


def present(it: dict) -> bool:
    d, v = it["dest"], it["id"]
    return (d / f"{v}.mp4").exists() and (d / f"{v}_audio.wav").exists()


def fetch(it: dict) -> None:
    if it["kind"] == "episode":
        download_youtube(it["url"], str(it["dest"].parent))      # -> scribe_dataset/<id>/<id>.mp4 + _audio.wav
        return
    it["dest"].mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        r = download_youtube(it["url"], tmp)
        for key in ("video_path", "audio_path"):
            src = Path(r[key])
            shutil.move(str(src), str(it["dest"] / src.name))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--links", default=str(ROOT / "youtube_links.txt"))
    ap.add_argument("--episodes-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    items = plan(Path(a.links))
    items.sort(key=lambda x: x["kind"] != "episode")
    if a.episodes_only:
        items = [x for x in items if x["kind"] == "episode"]
    failed = []
    for k, it in enumerate(items, 1):
        tag = f"[{k}/{len(items)}] {it['kind']} {it['id']} -> {it['dest'].relative_to(ROOT).as_posix()}"
        if present(it):
            print(f"{tag}: already present")
            continue
        if a.dry_run:
            print(f"{tag}: would download")
            continue
        print(f"{tag}: downloading", flush=True)
        try:
            fetch(it)
        except Exception as e:  # noqa: BLE001 (keep going; report at the end)
            print(f"{tag}: FAILED: {str(e)[:300]}", flush=True)
            failed.append(it["id"])
    print(f"done: {len(items) - len(failed)} ok or present, {len(failed)} failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
