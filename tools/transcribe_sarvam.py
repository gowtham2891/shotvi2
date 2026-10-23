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
Spend ledger (2026-10-23): Sarvam is billed by Sarvam, but every job is recorded in the PROJECT spend ledger
(shotvi/llm/spend.py, ``record_fixed``, source "sarvam", Rs = minutes x 0.59) once the job has completed, so
SHOTVI_SPEND_CAP_INR covers it. Before the job: refused (exit 2) when ledger + this job's cost > the limit
(``--max-ledger-inr``, default SHOTVI_SPEND_CAP_INR). ``--dry-run`` prints the cost and the guard verdict (no network).

    python tools/transcribe_sarvam.py <id> --max-ledger-inr 1860
    # a job run before the ledger covered Sarvam (records once; a second call finds it and does nothing):
    python tools/transcribe_sarvam.py <id> --record-past-job --model saaras:v3 --mode transcribe --job-date 2026-10-22
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
sys.path.insert(0, str(ROOT))
INR_PER_MIN = 0.49 + 0.10
LEDGER_SOURCE = "sarvam"
EXIT_GUARD = 2


def job_key(vid: str, model: str, mode: str) -> str:
    return f"{vid} {model} {mode}"


def job_inr(mins: float) -> float:
    return round(mins * INR_PER_MIN, 2)


def ledger_has(key: str) -> bool:
    """A Sarvam ledger entry for this job key already exists (the ledger keeps its last 2000 entries)."""
    from shotvi.llm.spend import _load
    return any(e.get("source") == LEDGER_SOURCE and key in str(e.get("note", "")) for e in _load().get("entries", []))


def guard(inr: float, limit: float | None) -> tuple[bool, str]:
    """(fits, message): ledger + this job's cost against ``limit`` (None = SHOTVI_SPEND_CAP_INR)."""
    from shotvi.llm.spend import cap_inr, total_inr
    lim = cap_inr() if limit is None else float(limit)
    now = total_inr()
    fits = now + inr <= lim + 1e-9
    return fits, (f"ledger Rs {now:.2f} + Sarvam Rs {inr:.2f} = Rs {now + inr:.2f} {'<=' if fits else '>'} limit "
                  f"Rs {lim:.2f}" + ("" if limit is not None else " (SHOTVI_SPEND_CAP_INR)"))


def record_job(vid: str, model: str, mode: str, mins: float, note: str = "") -> float:
    """Record one Sarvam job in the project ledger (USD = Rs / SHOTVI_USD_INR, so the ledger's rupees are exact)."""
    from shotvi.llm.spend import record_fixed, usd_inr
    inr = job_inr(mins)
    record_fixed(f"sarvam/{model}", inr / usd_inr(), source=LEDGER_SOURCE,
                 note=f"{job_key(vid, model, mode)}: {mins:.2f} min x Rs {INR_PER_MIN:.2f} = Rs {inr:.2f}"
                      + (f"; {note}" if note else ""))
    return inr


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
    ap.add_argument("--max-ledger-inr", type=float, default=None,
                    help="refuse the job when the project ledger + its cost would exceed this "
                         "(default SHOTVI_SPEND_CAP_INR)")
    ap.add_argument("--record-past-job", action="store_true",
                    help="no Sarvam call: record a job that already ran (before the ledger covered Sarvam) once")
    ap.add_argument("--job-date", default=None, help="with --record-past-job: when that job ran (for the note)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    vid = a.video_id
    vdir = ROOT / "scribe_dataset" / vid
    wav = vdir / f"{vid}_audio.wav"
    raw_p, out_p = vdir / f"{vid}_sarvam_raw.json", vdir / f"{vid}_sarvam_transcript.json"
    if not wav.exists():
        sys.exit(f"missing {wav}")
    if a.record_past_job:
        mins = duration_s(wav) / 60
        key = job_key(vid, a.model, a.mode)
        if ledger_has(key):
            print(f"{key}: already in the ledger; nothing recorded")
            return 0
        inr = record_job(vid, a.model, a.mode, mins, f"past job ({a.job_date or 'date not given'}), recorded "
                                                     f"after the fact")
        print(f"{key}: recorded Rs {inr:.2f} (source {LEDGER_SOURCE})")
        return 0
    if out_p.exists() and not a.force:
        sys.exit(f"{out_p} exists (use --force to redo)")
    mins = duration_s(wav) / 60
    params = {"model": a.model, "language_code": a.language, "mode": a.mode, "with_diarization": True,
              "with_timestamps": True,
              **({"num_speakers": a.num_speakers} if a.num_speakers else {})}
    inr = job_inr(mins)
    fits, msg = guard(inr, a.max_ledger_inr)
    print(f"{vid}: {mins:.1f} min audio; job {params}; estimated cost Rs {inr:.2f} (Sarvam; recorded in the project "
          f"ledger, source {LEDGER_SOURCE}); {msg}")
    if a.dry_run:
        print(f"DRY RUN: no Sarvam call; the job {'would run' if fits else 'would be REFUSED'}")
        return 0
    if not fits:
        print(f"REFUSED: {msg} (raise --max-ledger-inr after the founder approves)")
        return EXIT_GUARD
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
        recorded = False
        if "completed" in str(state).lower():
            # billed once the job has run: record before the download, so a failed download is still counted
            rec = record_job(vid, a.model, a.mode, mins, f"job {getattr(job, 'job_id', '')}".strip())
            recorded = True
            print(f"ledger: recorded Rs {rec:.2f} (source {LEDGER_SOURCE})", flush=True)
        outdir = Path(tmp) / "out"
        outdir.mkdir()
        if not job.download_outputs(output_dir=str(outdir)):
            sys.exit(f"download failed (state {state})")
        outs = sorted(outdir.glob("*.json"))
        if not outs:
            sys.exit("no output file")
        raw = json.loads(outs[0].read_text(encoding="utf-8"))
        if not recorded:     # an output exists, so the job ran (state reported differently): it is billed
            rec = record_job(vid, a.model, a.mode, mins, f"job {getattr(job, 'job_id', '')} state {state}".strip())
            print(f"ledger: recorded Rs {rec:.2f} (source {LEDGER_SOURCE})", flush=True)
    raw_p.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    conv = to_scribe_like(raw, vid, a.model)
    conv["job_parameters"] = {**params, "upload": a.upload}
    out_p.write_text(json.dumps(conv, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(conv['segments'])} diarized segments, {conv['n_speakers']} speakers -> {out_p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
