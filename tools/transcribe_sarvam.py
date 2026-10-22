"""Transcribe an episode with Sarvam AI's batch speech-to-text WITH speaker diarization, to compare its speaker
labels with Scribe's (founder, 2026-10-22). Sarvam returns diarized segments (text + start/end + speaker_id), no
word-level times.

    python tools/transcribe_sarvam.py p3XTZlZ6-xw --dry-run       # cost estimate, no network
    python tools/transcribe_sarvam.py p3XTZlZ6-xw                 # needs SARVAM_API_KEY in .env

Writes (never overwrites unless --force):
  scribe_dataset/<id>/<id>_sarvam_raw.json         Sarvam's output as downloaded
  scribe_dataset/<id>/<id>_sarvam_transcript.json  Scribe-like shape for eval.speakers: "word_timestamps" made by
      spreading each diarized segment's words evenly over the segment's time (APPROXIMATE word times; only for
      time-overlap scoring of speakers), speaker = "sarvam_<speaker_id>"; plus "segments" = the raw entries.

Score both against the frame-based key:
  python -m eval.speakers p3XTZlZ6-xw --system sarvam=scribe_dataset/p3XTZlZ6-xw/p3XTZlZ6-xw_sarvam_transcript.json

Price (sarvam.ai/api-pricing, checked 2026-10-22): batch STT about Rs 0.49/min + diarization Rs 0.10/min.
Billed by Sarvam, NOT part of the Gemini spend ledger.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INR_PER_MIN = 0.49 + 0.10


def duration_s(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True)
    return float(out.stdout.strip())


def to_scribe_like(raw: dict, vid: str, model: str) -> dict:
    entries = ((raw.get("diarized_transcript") or {}).get("entries")) or []
    words = []
    for e in entries:
        toks = (e.get("transcript") or "").split()
        s, t = float(e["start_time_seconds"]), float(e["end_time_seconds"])
        if not toks or t <= s:
            continue
        step = (t - s) / len(toks)
        spk = f"sarvam_{e.get('speaker_id')}"
        for k, w in enumerate(toks):
            words.append({"word": w, "start": round(s + k * step, 3), "end": round(s + (k + 1) * step, 3),
                          "speaker": spk, "approx_time": True})
    return {"source": "sarvam", "model": model, "video_id": vid, "language": raw.get("language_code"),
            "n_speakers": len({e.get("speaker_id") for e in entries}), "word_times": "approximate (segment spread)",
            "word_timestamps": words, "segments": entries, "text": raw.get("transcript", "")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("--model", default="saaras:v4", help="saaras:v4 = latest, recommended (docs 2026-10-22); saaras:v3")
    ap.add_argument("--language", default="te-IN")
    ap.add_argument("--mode", default="codemix",
                    help="codemix = Telugu in Telugu script, English words in Latin (Tenglish, like Scribe)")
    ap.add_argument("--upload", choices=["wav", "mp3"], default="wav",
                    help="wav = the original 16 kHz WAV, no re-encoding (default); mp3 = 64 kbps (smaller)")
    ap.add_argument("--num-speakers", type=int, default=None, help="default: Sarvam detects the count")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--timeout-s", type=int, default=3600)
    a = ap.parse_args(argv)
    vid = a.video_id
    vdir = ROOT / "scribe_dataset" / vid
    wav = vdir / f"{vid}_audio.wav"
    raw_p, out_p = vdir / f"{vid}_sarvam_raw.json", vdir / f"{vid}_sarvam_transcript.json"
    if not wav.exists():
        sys.exit(f"missing {wav}")
    if out_p.exists() and not a.force:
        sys.exit(f"{out_p} exists (use --force to redo)")
    mins = duration_s(wav) / 60
    params = {"model": a.model, "language_code": a.language, "mode": a.mode, "with_diarization": True,
              "with_timestamps": True,
              **({"num_speakers": a.num_speakers} if a.num_speakers else {})}
    print(f"{vid}: {mins:.1f} min audio; job {params}; estimated cost Rs {mins * INR_PER_MIN:.2f} (Sarvam, "
          f"not in the Gemini ledger)")
    if a.dry_run:
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        sys.exit("SARVAM_API_KEY is not set in .env")
    from sarvamai import SarvamAI
    client = SarvamAI(api_subscription_key=key)
    with tempfile.TemporaryDirectory() as tmp:
        up = wav
        if a.upload == "mp3":
            up = Path(tmp) / f"{vid}.mp3"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-ac", "1", "-ar", "16000", "-b:a", "64k",
                            str(up)], check=True)
        job = client.speech_to_text_job.create_job(**params)
        print(f"job created; uploading {up.name} ({up.stat().st_size / 1e6:.1f} MB)", flush=True)
        job.upload_files(file_paths=[str(up)], timeout=1800.0)
        job.start()
        print("job started; waiting", flush=True)
        st = job.wait_until_complete(poll_interval=15, timeout=a.timeout_s)
        state = getattr(st, "job_state", None)
        print(f"job state: {state}", flush=True)
        outdir = Path(tmp) / "out"
        outdir.mkdir()
        if not job.download_outputs(output_dir=str(outdir)):
            sys.exit(f"download failed (state {state})")
        outs = sorted(outdir.glob("*.json"))
        if not outs:
            sys.exit("no output file")
        raw = json.loads(outs[0].read_text(encoding="utf-8"))
    raw_p.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    conv = to_scribe_like(raw, vid, a.model)
    conv["job_parameters"] = {**params, "upload": a.upload}
    out_p.write_text(json.dumps(conv, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(conv['segments'])} diarized segments, {conv['n_speakers']} speakers -> {out_p}")


if __name__ == "__main__":
    main()
