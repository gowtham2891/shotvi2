"""Find a channel Short's range in the long episode by TEXT: transcribe the Short with Scribe (same settings as
the episode, via ``transcribe_scribe.transcribe``), then align its words to the episode words with the existing
``eval.map_shorts`` matcher (seed n-grams + timing-aware Viterbi). Fallback for Shorts that have no matched span in
data/channel_shorts/<id>.json; the audio-fingerprint spans there are preferred when they exist.

    python -m selection_v2.eval.match_short <episode_id> <short_id> [--transcribe] [--write]

Transcribing costs ElevenLabs credits (~67 per minute of Short audio), so it only happens with ``--transcribe``;
without it a missing Short transcript is an error that says so. ``--write`` adds the result to golden_set.yaml as a
source of kind channel_short (``matched_by: text``).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config import load_config
from ..paths import EpisodePaths, load_json
from . import golden_set

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # repo root: transcribe_scribe.py, eval/


def short_transcript_path(p: EpisodePaths, short_id: str) -> Path:
    return p.channel_shorts_dir / f"{short_id}_scribe_transcript.json"


def ensure_short_transcript(p: EpisodePaths, short_id: str, transcribe: bool, log=print) -> Path:
    dst = short_transcript_path(p, short_id)
    if dst.exists():
        return dst
    wav = p.channel_shorts_dir / f"{short_id}_audio.wav"
    mp4 = p.channel_shorts_dir / f"{short_id}.mp4"
    if not wav.exists() and mp4.exists():
        from download_youtube import extract_audio
        extract_audio(mp4, wav)
    if not wav.exists():
        raise FileNotFoundError(f"no Short media for {short_id} under {p.channel_shorts_dir}")
    if not transcribe:
        raise FileNotFoundError(f"{dst} does not exist; pass --transcribe to make it (ElevenLabs credits)")
    from transcribe_scribe import save, transcribe as scribe
    save(scribe(wav, log=log), dst)
    return dst


def match(p: EpisodePaths, short_id: str) -> dict:
    """Align the Short transcript to the episode. Returns {short_id, coverage, pieces: [{start, end, ...}],
    span: [start, end], confidence, flags}."""
    from eval import map_shorts as M
    tpath = p.transcript
    E, _ = M.load_words(tpath)
    ep = M.EpisodeIndex(E, demote_ranges=M.teaser_ranges(tpath))
    S, sd = M.load_words(short_transcript_path(p, short_id))
    res = M.map_short(short_id, S, ep, M.Sim())
    core = M.core_pieces(res)
    pieces = [M.span(E, pc["j_lo"], pc["j_hi"]) for pc in core]
    out = {"short_id": short_id, "coverage": round(res["coverage"], 4), "n_pieces": len(pieces), "pieces": pieces,
           "short_duration": sd.get("audio_duration_secs"), "time_scale": res.get("time_scale"),
           "matched_by": "text"}
    if pieces:
        out["span"] = [min(x["start"] for x in pieces), max(x["end"] for x in pieces)]
    out["ok"] = bool(pieces) and res["coverage"] >= M.MIN_COVERAGE_TO_WRITE
    if not out["ok"]:
        out["reason"] = (f"coverage {res['coverage']:.0%} below {M.MIN_COVERAGE_TO_WRITE:.0%}: probably not from this "
                         f"episode, or the Short is mostly non-speech")
    return out


def to_sources(m: dict, meta: dict | None = None) -> list[dict]:
    """One golden-set source per block of pieces (pieces closer than 30 s join)."""
    from .import_sources import _blocks
    meta = meta or {}
    out = []
    for k, (a, b) in enumerate(_blocks(m["pieces"])):
        out.append({"kind": "channel_short", "short_id": m["short_id"], "url": meta.get("url") or
                    f"https://www.youtube.com/shorts/{m['short_id']}", "views": meta.get("views"),
                    "matched_by": "text", "coverage": m["coverage"], "segment": k, "start": round(a, 2),
                    "end": round(b, 2), "label": meta.get("title") or ""})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m selection_v2.eval.match_short")
    ap.add_argument("video_id")
    ap.add_argument("short_id")
    ap.add_argument("--transcribe", action="store_true", help="transcribe the Short if needed (ElevenLabs credits)")
    ap.add_argument("--write", action="store_true", help="add the match to golden_set.yaml as a channel_short source")
    a = ap.parse_args(argv)
    cfg = load_config()
    p = EpisodePaths(a.video_id, cfg)
    ensure_short_transcript(p, a.short_id, a.transcribe)
    m = match(p, a.short_id)
    print(f"{a.short_id}: coverage {m['coverage']:.0%}, {m['n_pieces']} pieces, span {m.get('span')}, ok={m['ok']}"
          + (f" ({m['reason']})" if not m["ok"] else ""))
    for pc in m["pieces"]:
        print(f"   {pc['start']:8.2f} - {pc['end']:8.2f}  words {pc['start_word']}-{pc['end_word']}")
    if a.write and m["ok"]:
        gs = golden_set.load()
        ep = gs["episodes"].setdefault(a.video_id, {"moments": []})
        meta = {}
        if p.channel_shorts_data.exists():
            for s in load_json(p.channel_shorts_data).get("accepted", []) + load_json(p.channel_shorts_data).get("rejected", []):
                if s.get("short_id") == a.short_id:
                    meta = s
        srcs = [s for mm in ep.get("moments", []) for s in mm["sources"]
                if not (s["kind"] == "channel_short" and s.get("short_id") == a.short_id and s.get("matched_by") == "text")]
        srcs += [dict(s, manual=True) for s in to_sources(m, meta)]
        ep["moments"] = golden_set.build_moments(srcs)
        golden_set.save(gs)
        print(f"wrote {golden_set.DEFAULT_PATH}")
    return 0 if m["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
