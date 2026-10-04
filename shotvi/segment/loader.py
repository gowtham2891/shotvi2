"""Load and validate a Scribe transcript JSON.

Word indices are *never* changed: ``Transcript.words[i]`` is
``word_timestamps[i]`` of the input file, so every index produced downstream
maps back exactly. Problems are repaired in place where safe and reported in
``Transcript.warnings``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .text import nfc

UNKNOWN_SPEAKER = "speaker_unknown"
GHOST_SHARE = 0.02  # same rule as transcribe_scribe.py: <2% of talk time


class TranscriptError(ValueError):
    pass


@dataclass
class Word:
    i: int
    text: str
    start: float
    end: float
    speaker: str
    logprob: float | None = None

    @property
    def dur(self) -> float:
        return self.end - self.start


@dataclass
class Event:
    text: str
    start: float
    end: float
    kind: str  # laughter | censored | music | applause | silence | other


@dataclass
class Transcript:
    words: list[Word]
    events: list[Event]
    duration: float
    speakers: list[str]          # real speakers, most talk first
    ghost_speakers: list[str]
    talk_time: dict[str, float]
    has_logprob: bool
    video_id: str | None = None
    source_path: str | None = None
    asr_model: str | None = None
    language: str | None = None
    warnings: list[str] = field(default_factory=list)

    def text(self, i0: int, i1: int) -> str:
        return " ".join(w.text for w in self.words[i0:i1 + 1])


def classify_event(text: str) -> str:
    from . import lexicon as L
    t = text.lower()
    for kind, pats in (("laughter", L.LAUGHTER_PATTERNS), ("censored", L.CENSORED_PATTERNS),
                       ("music", L.MUSIC_PATTERNS), ("applause", L.APPLAUSE_PATTERNS),
                       ("silence", L.SILENCE_PATTERNS)):
        if any(p in t for p in pats):
            return kind
    return "other"


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN -> None


def from_dict(d: dict, video_id: str | None = None, source_path: str | None = None) -> Transcript:
    if not isinstance(d, dict):
        raise TranscriptError("transcript must be a JSON object")
    raw = d.get("word_timestamps")
    if raw is None and isinstance(d.get("words"), list):
        raw = d["words"]  # tolerate a raw Scribe response
    if not isinstance(raw, list) or not raw:
        raise TranscriptError("transcript has no word_timestamps")
    warnings: list[str] = []
    words: list[Word] = []
    n_missing_time = n_swapped = n_nonmono = n_no_lp = n_no_spk = 0
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            raise TranscriptError(f"word {i} is not an object")
        text = nfc(str(r.get("word", r.get("text", "")) or "").strip())
        s, e = _f(r.get("start")), _f(r.get("end"))
        if s is None or e is None:
            n_missing_time += 1
        if e is not None and s is not None and e < s:
            s, e = e, s
            n_swapped += 1
        spk = r.get("speaker", r.get("speaker_id"))
        if spk is None or spk == "":
            spk = UNKNOWN_SPEAKER
            n_no_spk += 1
        lp = _f(r.get("logprob"))
        if lp is None:
            n_no_lp += 1
        words.append(Word(i, text, s if s is not None else -1.0, e if e is not None else -1.0,
                          str(spk), lp))
    # interpolate missing times from neighbours (indices stay put)
    if n_missing_time:
        for k, w in enumerate(words):
            if w.start < 0 or w.end < 0:
                prev_end = next((words[j].end for j in range(k - 1, -1, -1) if words[j].end >= 0), 0.0)
                nxt = next((words[j].start for j in range(k + 1, len(words)) if words[j].start >= 0),
                           prev_end)
                w.start = prev_end if w.start < 0 else w.start
                w.end = max(w.start, nxt if w.end < 0 else w.end)
        warnings.append(f"{n_missing_time} words had missing start/end; interpolated")
    for k in range(1, len(words)):
        if words[k].start < words[k - 1].start - 1e-6:
            n_nonmono += 1
    if n_swapped:
        warnings.append(f"{n_swapped} words had end<start; swapped")
    if n_nonmono:
        warnings.append(f"{n_nonmono} words start before the previous word (overlap / unsorted); kept order")
    if n_no_spk:
        warnings.append(f"{n_no_spk} words had no speaker; assigned '{UNKNOWN_SPEAKER}'")
    has_lp = n_no_lp < len(words)
    if n_no_lp and has_lp:
        warnings.append(f"{n_no_lp} words have no logprob")
    if not has_lp:
        warnings.append("no logprobs in transcript; confidence signals disabled")

    events: list[Event] = []
    for r in d.get("audio_events") or []:
        if not isinstance(r, dict):
            continue
        s, e = _f(r.get("start")), _f(r.get("end"))
        if s is None:
            continue
        e = s if e is None or e < s else e
        t = nfc(str(r.get("text", "") or ""))
        events.append(Event(t, s, e, classify_event(t)))
    events.sort(key=lambda ev: ev.start)

    talk: dict[str, float] = {}
    for w in words:
        talk[w.speaker] = talk.get(w.speaker, 0.0) + max(0.0, w.dur)
    total = sum(talk.values()) or 1.0
    declared_ghosts = set(d.get("ghost_speakers") or [])
    ghosts = {s for s, t in talk.items() if t / total < GHOST_SHARE} | (declared_ghosts & set(talk))
    real = sorted((s for s in talk if s not in ghosts), key=lambda s: -talk[s])
    if not real:  # everything tiny? then nobody is a ghost
        real, ghosts = sorted(talk, key=lambda s: -talk[s]), set()
    dur = _f(d.get("audio_duration_secs")) or 0.0
    last = max(w.end for w in words)
    if dur < last:
        if dur:
            warnings.append(f"audio_duration_secs {dur:.1f} < last word end {last:.1f}; using the latter")
        dur = last
    return Transcript(words=words, events=events, duration=dur, speakers=real,
                      ghost_speakers=sorted(ghosts), talk_time=talk, has_logprob=has_lp,
                      video_id=video_id, source_path=source_path,
                      asr_model=d.get("asr_model"), language=d.get("language"), warnings=warnings)


def video_id_from_path(path: str | Path) -> str:
    stem = Path(path).name
    for suf in ("_scribe_transcript.json", ".json"):
        if stem.endswith(suf):
            return stem[: -len(suf)]
    return stem


def load(path: str | Path) -> Transcript:
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return from_dict(d, video_id=video_id_from_path(path), source_path=str(path))
