"""Per-unit signals. Cheap, deterministic, transcript-only (no audio decode).

``LOW_LOGPROB`` is a probability statement (token probability < 0.5), not a
per-video percentile, so a clean transcript is not forced to have "bad" words.
"""
from __future__ import annotations

import math
import statistics

from .loader import Event, Transcript
from .pauses import PauseStats
from .text import QUESTION_THRESHOLD, latin_fraction
from .units import Unit

LOW_LOGPROB = math.log(0.5)
LAUGH_AFTER_S = 2.0     # laughter this soon after a unit ends is a reaction to it


def _overlap(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _events_in(events: list[Event], t0: float, t1: float, kinds=None) -> list[Event]:
    out = []
    for e in events:
        if e.start > t1:
            break
        if kinds and e.kind not in kinds:
            continue
        # zero-length events count if they fall inside
        if _overlap(t0, t1, e.start, e.end) > 0 or t0 <= e.start <= t1:
            out.append(e)
    return out


def unit_signals(tr: Transcript, ps: PauseStats, u: Unit) -> dict:
    ws = tr.words
    main = u.main
    n = len(main)
    dur = max(u.dur, 1e-3)
    gaps = [max(0.0, ws[b].start - ws[a].end) for a, b in zip(main, main[1:])
            if ws[a].speaker == ws[b].speaker]
    pauses = [g for g in gaps if g >= ps.minor]
    laughs = _events_in(tr.events, u.start, u.end, {"laughter"})
    laugh_after = any(e.kind == "laughter" and u.end - 0.3 < e.start <= u.end + LAUGH_AFTER_S
                      for e in tr.events)
    cens = _events_in(tr.events, u.start, u.end, {"censored"})
    cens_s = sum(_overlap(u.start, u.end, e.start, e.end) for e in cens)
    other = [e for e in _events_in(tr.events, u.start, u.end)
             if e.kind not in ("laughter", "censored")]
    lps = [ws[i].logprob for i in main if ws[i].logprob is not None]
    q = [c.q for c in u.clauses]
    return {
        "duration": round(u.dur, 2),
        "n_words": n,
        "n_backchannels": len(u.backchannels),
        "wps": round(n / dur, 3),
        "n_pauses": len(pauses),
        "pause_max": round(max(gaps), 2) if gaps else 0.0,
        "pause_frac": round(sum(pauses) / dur, 3),
        "laughter": len(laughs),
        "laughter_times": [round(e.start, 2) for e in laughs],
        "laugh_after": laugh_after,
        "censored": bool(cens),
        "censored_s": round(cens_s, 2),
        "events": [{"kind": e.kind, "text": e.text, "start": round(e.start, 2), "end": round(e.end, 2)}
                   for e in other],
        "logprob_mean": round(statistics.fmean(lps), 3) if lps else None,
        "logprob_min": round(min(lps), 3) if lps else None,
        "low_conf_frac": round(sum(lp < LOW_LOGPROB for lp in lps) / len(lps), 3) if lps else None,
        "latin_frac": latin_fraction([ws[i].text for i in main]),
        "question": any(x >= QUESTION_THRESHOLD for x in q),
        "ends_with_question": bool(q) and q[-1] >= QUESTION_THRESHOLD,
        "question_score_max": round(max(q), 3) if q else 0.0,
        "n_speakers": len(u.speaker_share),
        "dominant_share": max(u.speaker_share.values()) if u.speaker_share else 1.0,
    }


def add_signals(tr: Transcript, ps: PauseStats, units: list[Unit]) -> dict:
    """Fill ``unit.signals``; add video-relative fields. Returns video-level baselines."""
    for u in units:
        u.signals = unit_signals(tr, ps, u)
    rates = [u.signals["wps"] for u in units if u.dur >= 3.0] or [u.signals["wps"] for u in units]
    med = statistics.median(rates) if rates else 0.0
    for u in units:
        u.signals["speech_rate_rel"] = round(u.signals["wps"] / med, 3) if med else None
    lps = [w.logprob for w in tr.words if w.logprob is not None]
    return {
        "median_wps": round(med, 3),
        "logprob_mean": round(statistics.fmean(lps), 3) if lps else None,
        "low_conf_frac": round(sum(lp < LOW_LOGPROB for lp in lps) / len(lps), 4) if lps else None,
        "latin_frac": latin_fraction([w.text for w in tr.words]),
        "n_laughter": sum(e.kind == "laughter" for e in tr.events),
        "n_censored": sum(e.kind == "censored" for e in tr.events),
        "event_kinds": sorted({e.kind for e in tr.events}),
    }
