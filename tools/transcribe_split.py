"""Transcribe a long episode in parts when no single ElevenLabs key has enough credits, then merge.

ElevenLabs bills a whole file to one key, so a 64-min episode can fail on keys that only have half the credits.
This cuts the wav at the given times (pick quiet gaps, e.g. with ffmpeg silencedetect), transcribes the parts
largest-first (so the biggest part lands on the key with the most credits), and merges them into one Scribe-format
transcript with corrected timestamps.

Speaker labels are NOT comparable across parts (diarization is per file), so part k>1 speakers are renamed
`p<k>_speaker_N`. The merged file records the parts under "parts".

usage: python tools/transcribe_split.py scribe_dataset/<id>/<id>_audio.wav --cuts 1769.3 [1769.3 3100 ...]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import transcribe_scribe as ts  # noqa: E402


def cut(src: Path, start: float, end: float | None, dst: Path):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(src)]
    if end is not None:
        cmd += ["-t", f"{end - start:.3f}"]
    cmd += ["-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(dst)]
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--cuts", type=float, nargs="+", required=True)
    args = ap.parse_args()
    src = Path(args.wav)
    total = ts.duration_s(src)
    bounds = [0.0] + sorted(args.cuts) + [total]
    parts = []
    for k in range(len(bounds) - 1):
        p = src.with_name(f"{src.stem}.part{k + 1}.wav")
        if not p.exists():
            cut(src, bounds[k], bounds[k + 1] if k + 1 < len(bounds) - 1 else None, p)
        parts.append({"k": k + 1, "path": p, "offset": bounds[k], "end": bounds[k + 1]})
    results = {}
    for part in sorted(parts, key=lambda x: -(x["end"] - x["offset"])):      # largest first
        out = part["path"].with_name(part["path"].stem + "_scribe.json")
        if out.exists():
            results[part["k"]] = json.loads(out.read_text(encoding="utf-8"))
            print(f"part {part['k']}: already transcribed")
            continue
        print(f"part {part['k']}: {part['offset']:.1f}-{part['end']:.1f}s")
        results[part["k"]] = ts.transcribe(part["path"])
        ts.save(results[part["k"]], out)

    words, events, meta = [], [], []
    for part in parts:
        r, off, k = results[part["k"]], part["offset"], part["k"]
        rename = (lambda s: s) if k == 1 else (lambda s, k=k: f"p{k}_{s}" if s else s)
        for w in r["word_timestamps"]:
            words.append({**w, "start": round(w["start"] + off, 3), "end": round(w["end"] + off, 3),
                          "speaker": rename(w.get("speaker"))})
        for e in r["audio_events"]:
            events.append({**e, "start": round(e["start"] + off, 3), "end": round(e["end"] + off, 3)})
        meta.append({"part": k, "offset": off, "end": part["end"], "language": r.get("language"),
                     "speakers": [rename(s) for s in r.get("speakers", [])]})
    for w in words:                                    # build_sentences expects a clean word list
        w.pop("_i", None)
    sents = ts.build_sentences(words)
    real, talk = ts.real_speakers(words)
    merged = {
        "asr_model": f"elevenlabs/{ts.MODEL}",
        "language": results[1].get("language"),
        "sentences": sents, "word_timestamps": words, "audio_events": events,
        "n_speakers": len(real), "speakers": real,
        "ghost_speakers": [s for s in talk if s is not None and s not in real],
        "total_sentences": len(sents), "total_words": len(words),
        "audio_duration_secs": total,
        "text": " ".join(results[p["k"]].get("text", "") for p in parts),
        "parts": meta,
        "note": "transcribed in parts; speaker ids are per part (p<k>_ prefix for part k>1)",
    }
    dst = src.with_name(src.stem.replace("_audio", "") + "_scribe_transcript.json")
    ts.save(merged, dst)
    print(f"merged {len(parts)} parts -> {dst} ({len(words):,} words, {len(sents):,} sentences, "
          f"{len(real)} speakers, {len(events)} events)")


if __name__ == "__main__":
    main()
