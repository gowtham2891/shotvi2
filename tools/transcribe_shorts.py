"""Transcribe a podcast's creator Shorts with Scribe (same settings as the episode).

usage: python tools/transcribe_shorts.py scribe_dataset/<video_id>
writes creator_shorts/<short_id>_scribe_transcript.json; skips ones already done.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transcribe_scribe import save, transcribe  # noqa: E402


def main(vid_dir):
    shorts_dir = Path(vid_dir) / "creator_shorts"
    for audio in sorted(shorts_dir.glob("*_audio.wav")):
        short_id = audio.name[:-len("_audio.wav")]
        dst = shorts_dir / f"{short_id}_scribe_transcript.json"
        if dst.exists():
            print(f"{short_id}: already transcribed")
            continue
        print(f"{short_id}: transcribing")
        save(transcribe(audio), dst)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python tools/transcribe_shorts.py scribe_dataset/<video_id>")
        sys.exit(1)
    main(sys.argv[1])
