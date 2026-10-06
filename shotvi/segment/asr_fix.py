"""ASR correction layer: find words Scribe misheard and propose corrections, WITHOUT touching the transcript.

Output: ``runs/asr_fix/<video_id>/corrections.json`` (a separate, additive layer). Word indices index the Scribe
``word_timestamps``; timings are never changed. Use :func:`load_corrections` + :func:`apply_corrections` /
:func:`display_text` to get display text (prompts, captions) with the accepted corrections applied.

Layers (each one optional):
  1. ``flag``     free: words with confidence exp(logprob) < ``threshold`` are suspects.
  2a. ``context`` one text call per episode: the model reads all lines (flagged words marked ``word(?)``) and lists
                  (a) flagged words it thinks are wrong and (b) unflagged words that make no sense in context, with
                  a text-only guess. Answers are line ids + word positions; code converts them to word indices.
  2b. ``relisten`` per suspect line, only that line's audio (cut from the episode WAV, padded) + 3 lines of text
                  context on each side; several lines per call. The model writes what it hears and fixes only the
                  suspect words. A fix is accepted when confidence >= ``min_conf``, it stays on the suspect span
                  (+-1 word), the corrected words appear in the model's own ``heard_line``, and the rest of the
                  line matches Scribe's (so the fix aligns to the same word span).

Audio is sent INLINE (base64 WAV, ~32 tokens/s): a 3 s snippet costs ~100 tokens, while the Files API has no audio
clipping, so it would bill the whole episode (~40k tokens) on every call. Inline needs no upload and no expiry.

corrections.json::

    {"video_id", "schema": "asr_fix/1", "params": {...}, "lines": n,
     "corrections": [{start_word, end_word, heard, corrected, reason, source: "relisten"|"context", confidence,
                      applied, found_by: ["flag","context"], line, start, end, context_guess, relisten}],
     "uncertain":   [{start_word, end_word, heard, source: "flag"|"context", confidence, verdict}],
     "rejected":    [{..., why}],            # answers that failed validation (audit trail)
     "threshold_report": {...}, "cost": {...}}

``source`` names the evidence for ``corrected`` ("relisten" = audio, "context" = text only). ``applied`` corrections
are the ones :func:`apply_corrections` uses by default (relisten-accepted only; text-only guesses are recorded but
not applied unless asked). ``uncertain`` keeps low-confidence words no layer corrected (useful for captions).

CLI::

    python -m shotvi.segment.asr_fix <video_id> --dry-run          # projected tokens and rupees, no call
    SHOTVI_SPEND_SOURCE=asrfix python -m shotvi.segment.asr_fix <video_id> --max-ledger-inr 1253
"""
from __future__ import annotations

import argparse
import base64
import difflib
import io
import json
import math
import os
import re
import subprocess
import sys
import unicodedata
import wave
from dataclasses import dataclass, field
from pathlib import Path
from string import Template

from .render import estimate_tokens, fmt_ts

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "asr_fix/1"
CONTEXT_PROMPT = ROOT / "prompts" / "asr_context.md"
RELISTEN_PROMPT = ROOT / "prompts" / "asr_relisten.md"

DEFAULT_THRESHOLD = 0.5
REPORT_THRESHOLDS = (0.5, 0.6, 0.7)
MIN_CONF = 0.8            # relisten fixes below this are recorded, not applied
PAD_S = 0.4               # audio padding around a suspect line
MIN_CLIP_S = 1.5          # very short lines are widened to at least this
CTX_LINES = 2             # text lines of context on each side
BATCH_ITEMS = 8           # suspect lines per relisten call
BATCH_AUDIO_S = 60.0      # audio seconds per relisten call
MAX_EXTEND = 1            # a fix may extend this many words beyond its suspect span
MIN_LINE_RATIO = 0.6      # fixed Scribe line vs model's heard_line, character similarity
AUDIO_TOKENS_PER_S = 32
# Audio input is billed above the text rate on Gemini Flash models (2x for Gemini 3 Flash on the public price page);
# pricing.PRICES is text-only, so the surcharge is projected and recorded in the rupee ledger explicitly.
AUDIO_PRICE_FACTOR = 2.0
TEXT_CAL = 1.25           # estimate_tokens under-counts these prompts (checked with countTokens, 2026-10-05)
MODEL = {"provider": "gemini", "model": "gemini-3.8-flash", "params": {"thinking_level": "low",
                                                                        "max_output_tokens": 8192}}

CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {"suspects": {"type": "array", "items": {
        "type": "object",
        "properties": {"line": {"type": "string"}, "first": {"type": "integer"}, "last": {"type": "integer"},
                       "heard": {"type": "string"}, "guess": {"type": "string"},
                       "kind": {"type": "string", "enum": ["flagged", "context"]}, "why": {"type": "string"}},
        "required": ["line", "first", "last", "heard", "guess", "kind", "why"]}}},
    "required": ["suspects"],
}
RELISTEN_SCHEMA = {
    "type": "object",
    "properties": {"clips": {"type": "array", "items": {
        "type": "object",
        "properties": {"clip": {"type": "integer"}, "heard_line": {"type": "string"},
                       "fixes": {"type": "array", "items": {
                           "type": "object",
                           "properties": {"first": {"type": "integer"}, "last": {"type": "integer"},
                                          "corrected": {"type": "string"}, "confidence": {"type": "number"},
                                          "reason": {"type": "string"}},
                           "required": ["first", "last", "corrected", "confidence", "reason"]}}},
        "required": ["clip", "heard_line", "fixes"]}}},
    "required": ["clips"],
}


class BudgetStop(RuntimeError):
    """A projected call would push the project ledger past the caller's limit."""


# ------------------------------------------------------------------ words, lines
def wtext(w) -> str:
    return w["word"] if isinstance(w, dict) else w.text


def wconf(w) -> float | None:
    lp = w.get("logprob") if isinstance(w, dict) else getattr(w, "logprob", None)
    if lp is None:
        return None
    try:
        return math.exp(float(lp))
    except (TypeError, ValueError, OverflowError):
        return None


_ZW = "‌‍﻿"
_PUNCT = set(" \t\n\"'`“”‘’.,!?;:।॥…()[]{}<>-–—*/\\|~" + _ZW)


def squash(s: str) -> str:
    """Comparison form: NFC, lowercase, no spaces / punctuation / zero-width joiners."""
    s = unicodedata.normalize("NFC", s or "").lower()
    return "".join(ch for ch in s if ch not in _PUNCT)


_TRAIL_RE = re.compile(r"[.?!,।…]+$")


@dataclass
class Line:
    id: str
    kind: str                 # "speech" | "event"
    start: float
    end: float
    speaker: str
    text: str
    i0: int | None = None     # inclusive word range (speech lines)
    i1: int | None = None

    @property
    def n(self) -> int:
        return 0 if self.i0 is None else self.i1 - self.i0 + 1

    def pos(self, i: int) -> int:
        return i - self.i0 + 1

    def idx(self, p: int) -> int:
        return self.i0 + p - 1


def build_lines(tr: dict) -> list[Line]:
    """Scribe ``sentences[]`` (falls back to speaker change / 0.7 s gap / 30 words) plus ``audio_events[]`` as their
    own lines, in time order, ids L0001..."""
    ws = tr["word_timestamps"]
    rows: list[Line] = []
    sents = tr.get("sentences") or []
    spans = [(s["words"][0], s["words"][1]) for s in sents if s.get("words")]
    if not spans and ws:
        a = 0
        for i in range(1, len(ws) + 1):
            if (i == len(ws) or ws[i].get("speaker") != ws[a].get("speaker") or ws[i]["start"] - ws[i - 1]["end"] > 0.7
                    or i - a >= 30):
                spans.append((a, i - 1))
                a = i
    for a, b in spans:
        rows.append(Line("", "speech", ws[a]["start"], ws[b]["end"], ws[a].get("speaker") or "?",
                         " ".join(w["word"] for w in ws[a:b + 1]), a, b))
    for e in tr.get("audio_events") or []:
        rows.append(Line("", "event", float(e["start"]), float(e["end"]), "event", e.get("text", "")))
    rows.sort(key=lambda r: (r.start, r.kind != "event"))
    for k, r in enumerate(rows):
        r.id = f"L{k + 1:04d}"
    return rows


def line_of_word(lines: list[Line]) -> dict[int, Line]:
    out = {}
    for ln in lines:
        if ln.kind == "speech":
            for i in range(ln.i0, ln.i1 + 1):
                out[i] = ln
    return out


def flag_words(tr: dict, threshold: float = DEFAULT_THRESHOLD, lo: float = 0.0) -> list[int]:
    """Word indices with ``lo`` <= confidence < ``threshold`` (words without a logprob are never flagged)."""
    out = []
    for i, w in enumerate(tr["word_timestamps"]):
        c = wconf(w)
        if c is not None and lo <= c < threshold and squash(wtext(w)):
            out.append(i)
    return out


def threshold_report(tr: dict, thresholds=REPORT_THRESHOLDS) -> dict:
    ws = tr["word_timestamps"]
    lines = build_lines(tr)
    low = line_of_word(lines)
    rep = {"n_words": len(ws), "has_logprob": any(wconf(w) is not None for w in ws), "by_threshold": {}}
    for th in thresholds:
        f = flag_words(tr, th)
        rep["by_threshold"][str(th)] = {"words": len(f), "lines": len({low[i].id for i in f if i in low})}
    return rep


# ------------------------------------------------------------------ suspects
@dataclass
class Suspect:
    line: str
    i0: int
    i1: int
    found_by: set = field(default_factory=set)   # {"flag", "context"}
    guess: str = ""                               # layer 2a text-only guess
    why: str = ""
    min_conf: float | None = None
    context_says_wrong: bool = False              # 2a listed it (flagged or context)

    def to_dict(self, tr: dict) -> dict:
        ws = tr["word_timestamps"]
        return {"line": self.line, "start_word": self.i0, "end_word": self.i1,
                "heard": " ".join(wtext(w) for w in ws[self.i0:self.i1 + 1]), "found_by": sorted(self.found_by),
                "guess": self.guess, "why": self.why,
                "min_conf": None if self.min_conf is None else round(self.min_conf, 3)}


def flag_suspects(tr: dict, lines: list[Line], flagged: list[int]) -> list[Suspect]:
    """Contiguous flagged words in one line become one suspect span."""
    low = line_of_word(lines)
    ws = tr["word_timestamps"]
    out: list[Suspect] = []
    for i in sorted(flagged):
        ln = low.get(i)
        if ln is None:
            continue
        c = wconf(ws[i])
        if out and out[-1].line == ln.id and out[-1].i1 == i - 1:
            out[-1].i1 = i
            out[-1].min_conf = min(out[-1].min_conf, c)
        else:
            out.append(Suspect(ln.id, i, i, {"flag"}, min_conf=c))
    return out


def merge_suspects(*groups: list[Suspect]) -> list[Suspect]:
    """Union of suspect spans; spans in the same line that overlap or touch merge (found_by / guesses combined)."""
    allx = sorted((s for g in groups for s in g), key=lambda s: (s.i0, s.i1))
    out: list[Suspect] = []
    for s in allx:
        if out and out[-1].line == s.line and s.i0 <= out[-1].i1 + 1:
            m = out[-1]
            m.i1 = max(m.i1, s.i1)
            m.found_by |= s.found_by
            m.guess = m.guess or s.guess
            m.why = m.why or s.why
            m.context_says_wrong = m.context_says_wrong or s.context_says_wrong
            if s.min_conf is not None:
                m.min_conf = s.min_conf if m.min_conf is None else min(m.min_conf, s.min_conf)
        else:
            out.append(Suspect(s.line, s.i0, s.i1, set(s.found_by), s.guess, s.why, s.min_conf,
                               s.context_says_wrong))
    return out


# ------------------------------------------------------------------ prompts
def _load_prompt(path: Path) -> tuple[str, str, str]:
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.S)
    meta = {k.strip(): v.strip() for k, v in (ln.split(":", 1) for ln in m.group(1).strip().splitlines())}
    sm = re.search(r"### SYSTEM\n(.*?)\n### USER\n(.*)", m.group(2), re.S)
    return meta.get("version", "?"), sm.group(1).strip(), sm.group(2).strip()


def _numbered(tr: dict, ln: Line, marks: set[int] | None = None, mark: str = "(?)") -> str:
    ws = tr["word_timestamps"]
    return " ".join(f"[{ln.pos(i)}]{ws[i]['word']}{mark if marks and i in marks else ''}"
                    for i in range(ln.i0, ln.i1 + 1))


def render_lines(tr: dict, lines: list[Line], flagged: set[int]) -> str:
    """Plain text lines (numbering every word costs ~50% more tokens); the model counts word positions inside a
    line (lines are short) and echoes the words, and code checks the echo."""
    out = []
    for ln in lines:
        if ln.kind == "event":
            out.append(f"{ln.id} [{fmt_ts(ln.start)}] (audio event) {ln.text}")
        else:
            ws = tr["word_timestamps"]
            out.append(f"{ln.id} [{fmt_ts(ln.start)}] {ln.speaker}: "
                       + " ".join(ws[i]["word"] + ("(?)" if i in flagged else "") for i in range(ln.i0, ln.i1 + 1)))
    return "\n".join(out)


def context_request(tr: dict, lines: list[Line], flagged: list[int], prompt_path: Path = CONTEXT_PROMPT):
    _, system, user = _load_prompt(prompt_path)
    body = render_lines(tr, lines, set(flagged))
    return system, Template(user).substitute(lines=body, n_lines=len(lines), n_flagged=len(flagged))


def parse_context(ans: dict, tr: dict, lines: list[Line], flagged: list[int]) -> tuple[list[Suspect], list[dict]]:
    """Validate the 2a answer: known speech line, positions inside it, ``heard`` echo matches those words (if not,
    the echo is searched for once in the line). Returns (suspects, rejected)."""
    byid = {ln.id: ln for ln in lines}
    ws = tr["word_timestamps"]
    fl = set(flagged)
    out, rej = [], []
    for a in ans.get("suspects") or []:
        ln = byid.get(str(a.get("line", "")).strip())
        if ln is None or ln.kind != "speech":
            rej.append({**a, "why_rejected": "unknown or non-speech line"})
            continue
        f, l = int(a.get("first", 0)), int(a.get("last", 0))
        heard = squash(a.get("heard", ""))
        ok = 1 <= f <= l <= ln.n
        if not ok or (heard and squash(" ".join(wtext(w) for w in ws[ln.idx(f):ln.idx(l) + 1])) != heard):
            hit = _find_span(tr, ln, heard, near=f)
            if hit is None:
                rej.append({**a, "why_rejected": "positions do not match the heard words"})
                continue
            f, l = hit
        i0, i1 = ln.idx(f), ln.idx(l)
        confs = [c for c in (wconf(ws[i]) for i in range(i0, i1 + 1)) if c is not None]
        out.append(Suspect(ln.id, i0, i1, {"context"} | ({"flag"} if fl & set(range(i0, i1 + 1)) else set()),
                           guess=str(a.get("guess", "")).strip(), why=str(a.get("why", "")).strip(),
                           min_conf=min(confs) if confs else None, context_says_wrong=True))
    return out, rej


def _find_span(tr: dict, ln: Line, heard: str, near: int = 0) -> tuple[int, int] | None:
    """Positions of ``heard`` in the line; with several occurrences, the one nearest to the claimed position (a
    miscount by one is common), unless two are equally near."""
    if not heard:
        return None
    ws = tr["word_timestamps"]
    hits = []
    for a in range(1, ln.n + 1):
        for b in range(a, min(ln.n, a + 5) + 1):
            if squash(" ".join(wtext(w) for w in ws[ln.idx(a):ln.idx(b) + 1])) == heard:
                hits.append((a, b))
    if len(hits) <= 1:
        return hits[0] if hits else None
    hits.sort(key=lambda h: abs(h[0] - near))
    return hits[0] if abs(hits[0][0] - near) < abs(hits[1][0] - near) else None


# ------------------------------------------------------------------ relisten
@dataclass
class Item:
    line: Line
    suspects: list[Suspect]
    t0: float
    t1: float

    @property
    def dur(self) -> float:
        return self.t1 - self.t0


def interjection_only(tr: dict, s: Suspect) -> bool:
    """A flag-only suspect made of interjections / backchannels ("ఆ", "ఊ", "ఉమ్", "యా"): never carries a joke or plot
    word, so it is not re-listened (one the context read listed explicitly is kept)."""
    ws = tr["word_timestamps"]
    return "context" not in s.found_by and all(is_interjection(wtext(ws[i])) for i in range(s.i0, s.i1 + 1))


def plan_items(tr: dict, lines: list[Line], suspects: list[Suspect], pad: float = PAD_S) -> list[Item]:
    byid = {ln.id: ln for ln in lines}
    dur = float(tr.get("audio_duration_secs") or max((ln.end for ln in lines), default=0.0))
    items: dict[str, Item] = {}
    for s in suspects:
        ln = byid[s.line]
        if ln.id not in items:
            t0, t1 = ln.start - pad, ln.end + pad
            if t1 - t0 < MIN_CLIP_S:
                mid = (t0 + t1) / 2
                t0, t1 = mid - MIN_CLIP_S / 2, mid + MIN_CLIP_S / 2
            items[ln.id] = Item(ln, [], max(0.0, t0), min(dur, t1) if dur else t1)
        items[ln.id].suspects.append(s)
    return sorted(items.values(), key=lambda it: it.t0)


def batches(items: list[Item], max_items: int = BATCH_ITEMS, max_audio_s: float = BATCH_AUDIO_S) -> list[list[Item]]:
    out, cur, secs = [], [], 0.0
    for it in items:
        if cur and (len(cur) >= max_items or secs + it.dur > max_audio_s):
            out.append(cur)
            cur, secs = [], 0.0
        cur.append(it)
        secs += it.dur
    if cur:
        out.append(cur)
    return out


def cut_audio(path: str | Path, t0: float, t1: float) -> bytes:
    """WAV bytes of [t0, t1]. A .wav is sliced with the stdlib; anything else goes through ffmpeg (16 kHz mono)."""
    path = Path(path)
    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "rb") as r:
            sr, nch, sw = r.getframerate(), r.getnchannels(), r.getsampwidth()
            a = max(0, int(t0 * sr))
            r.setpos(min(a, r.getnframes()))
            frames = r.readframes(max(0, int(t1 * sr) - a))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(nch)
            w.setsampwidth(sw)
            w.setframerate(sr)
            w.writeframes(frames)
        return buf.getvalue()
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", str(path),
           "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def find_audio(video_id: str) -> Path | None:
    d = ROOT / "scribe_dataset" / video_id
    for name in (f"{video_id}_audio.wav", f"{video_id}_audio.upload.mp3"):
        if (d / name).exists():
            return d / name
    return None


def _ctx_line(tr: dict, ln: Line) -> str:
    if ln.kind == "event":
        return f"  (audio event) {ln.text}"
    return f"  {ln.speaker}: {ln.text}"


def relisten_request(tr: dict, lines: list[Line], batch: list[Item], audio: str | Path | None, key_prefix: str,
                     prompt_path: Path = RELISTEN_PROMPT):
    """(system, user, media). Media alternate a text label and the clip's inline WAV."""
    _, system, user = _load_prompt(prompt_path)
    pos = {ln.id: k for k, ln in enumerate(lines)}
    blocks, media = [], []
    for k, it in enumerate(batch, 1):
        ln = it.line
        j = pos[ln.id]
        before = [_ctx_line(tr, x) for x in lines[max(0, j - CTX_LINES):j]]
        after = [_ctx_line(tr, x) for x in lines[j + 1:j + 1 + CTX_LINES]]
        sus = set()
        for s in it.suspects:
            sus |= set(range(s.i0, s.i1 + 1))
        spans = ", ".join(f"[{ln.pos(s.i0)}]" if s.i0 == s.i1 else f"[{ln.pos(s.i0)}]-[{ln.pos(s.i1)}]"
                          for s in it.suspects)
        blocks.append("\n".join([
            f"## Clip {k} ({fmt_ts(it.t0)}, {it.dur:.1f} s of audio)",
            "Before (context only):", *(before or ["  (start)"]),
            f"LINE ({ln.speaker}): {_numbered(tr, ln, sus, mark='<SUSPECT>')}",
            f"Suspect words: {spans}",
            "After (context only):", *(after or ["  (end)"]),
        ]))
        media.append({"text": f"Clip {k}:"})
        m = {"mime_type": "audio/wav", "key": f"{key_prefix}:{it.t0:.2f}-{it.t1:.2f}"}
        if audio is not None:
            m["data"] = base64.b64encode(cut_audio(audio, it.t0, it.t1)).decode("ascii")
        media.append(m)
    return system, Template(user).substitute(clips="\n\n".join(blocks)), media


# ------------------------------------------------------------------ style-only changes (not mishearings)
# non-lexical vocalizations: bare vowels, హ-syllables (laughs, "ఉహ్"), "మ్" (hum); Latin um / uh / hmm / haha
_VOCAL_RE = re.compile(r"^(?:[అఆఇఈఉఊఎఏఒఓ]|హ[ాిీుూో్]?|మ్)+ఁ?$|^(?:um+|uh+|hm+|mm+|ah+|oh+|(?:ha)+h?|(?:he)+h?|(?:hi)+h?)$")
_TE_V = {"అ": "a", "ఆ": "a", "ఇ": "i", "ఈ": "i", "ఉ": "u", "ఊ": "u", "ఋ": "ru", "ఎ": "e", "ఏ": "e", "ఐ": "ai",
         "ఒ": "o", "ఓ": "o", "ఔ": "au"}
_TE_SIGN = {"ా": "a", "ి": "i", "ీ": "i", "ు": "u", "ూ": "u", "ృ": "ru", "ె": "e", "ే": "e", "ై": "ai", "ొ": "o",
            "ో": "o", "ౌ": "au"}
_TE_C = dict(zip("కఖగఘఙచఛజఝఞటఠడఢణతథదధనపఫబభమయరలవశషసహళఱ",
                 ["k", "k", "g", "g", "n", "c", "c", "j", "j", "n", "t", "t", "d", "d", "n", "t", "t", "d", "d", "n",
                  "p", "f", "b", "b", "m", "y", "r", "l", "v", "s", "s", "s", "h", "l", "r"]))


def _romanize_te(s: str) -> str:
    """Rough, lossy Telugu -> Latin (only to tell a transliteration from a different word)."""
    out, pend = [], False   # pend: a consonant waiting for its vowel (inherent "a")
    for ch in s:
        if ch in _TE_C:
            if pend:
                out.append("a")
            out.append(_TE_C[ch])
            pend = True
        elif ch in _TE_SIGN:
            out.append(_TE_SIGN[ch])
            pend = False
        elif ch == "్":
            pend = False
        else:
            if pend:
                out.append("a")
            pend = False
            if ch in _TE_V:
                out.append(_TE_V[ch])
            elif ch in "ంఁ":
                out.append("n")
            elif ch == "ః":
                out.append("h")
            else:
                out.append(ch)
    if pend:
        out.append("a")
    return "".join(out)


def _phon(s: str) -> str:
    """Comparable sound skeleton for Latin or Telugu text: romanized, lowercased, common English spellings mapped,
    double letters and final vowels collapsed."""
    s = _romanize_te(unicodedata.normalize("NFC", s)).lower()
    s = "".join(ch for ch in s if ch.isalpha())
    for a, b in (("ch", "c"), ("ph", "f"), ("sh", "s"), ("th", "t"), ("dh", "d"), ("kh", "k"), ("gh", "g"),
                 ("bh", "b"), ("oo", "u"), ("ee", "i"), ("ck", "k"), ("q", "k"), ("w", "v"), ("x", "ks"),
                 ("z", "j"), ("y", "i"), ("c", "k")):
        s = s.replace(a, b)
    s = re.sub(r"(.)\1+", r"\1", s)
    return s


def _script(s: str) -> str:
    te = sum(1 for ch in s if "ఀ" <= ch <= "౿")
    la = sum(1 for ch in s if ch.isascii() and ch.isalpha())
    return "te" if te and not la else "latin" if la and not te else "mixed" if te and la else "none"


def is_interjection(tok: str) -> bool:
    from . import lexicon as L
    from .text import norm
    t = norm(tok)
    return bool(t) and (t in L.FILLERS or (t in L.BACKCHANNEL_TOKENS and t not in L.BACKCHANNEL_WEAK)
                        or bool(_VOCAL_RE.match(squash(tok))))


def style_only_change(heard: str, corrected: str, translit_ratio: float = 0.75) -> str | None:
    """Reason string if heard -> corrected is not a mishearing: an interjection / vocalization swapped for another,
    or the same word written in the other script. Shared leading / trailing words are ignored first."""
    h, c = heard.split(), corrected.split()
    while h and c and squash(h[0]) == squash(c[0]):
        h, c = h[1:], c[1:]
    while h and c and squash(h[-1]) == squash(c[-1]):
        h, c = h[:-1], c[:-1]
    if not h or not c:
        return None
    hs, cs = _script(" ".join(h)), _script(" ".join(c))
    if {hs, cs} == {"te", "latin"}:
        r = difflib.SequenceMatcher(None, _phon(" ".join(h)), _phon(" ".join(c)), autojunk=False).ratio()
        if r >= translit_ratio:
            return f"script change only (transliteration, {r:.2f})"
    if all(is_interjection(t) for t in h) and all(is_interjection(t) for t in c):
        return "interjection / vocalization change only"
    return None


def _apply_fix_line(tr: dict, ln: Line, f: int, l: int, corrected: str) -> str:
    ws = tr["word_timestamps"]
    toks = [wtext(ws[i]) for i in range(ln.i0, ln.i1 + 1)]
    return " ".join(toks[:f - 1] + [corrected] + toks[l:])


def parse_relisten(ans: dict, tr: dict, batch: list[Item], min_conf: float = MIN_CONF) -> tuple[list[dict], list[dict], set]:
    """Returns (fixes, rejected, checked_lines). A fix dict carries its verdict: ``accepted`` True only when it passes
    every check and confidence >= min_conf."""
    ws = tr["word_timestamps"]
    fixes, rej, seen = [], [], set()
    for c in ans.get("clips") or []:
        k = int(c.get("clip", 0))
        if not 1 <= k <= len(batch) or k in seen:
            rej.append({"clip": k, "why_rejected": "unknown or repeated clip number"})
            continue
        seen.add(k)
        it = batch[k - 1]
        ln = it.line
        heard_line = str(c.get("heard_line", ""))
        for fx in c.get("fixes") or []:
            f, l = int(fx.get("first", 0)), int(fx.get("last", 0))
            corrected = str(fx.get("corrected", "")).strip()
            conf = float(fx.get("confidence", 0) or 0)
            base = {"line": ln.id, "heard_line": heard_line, "corrected": corrected, "confidence": conf,
                    "reason": str(fx.get("reason", "")).strip()}
            if not 1 <= f <= l <= ln.n:
                rej.append({**base, "first": f, "last": l, "why_rejected": "positions outside the line"})
                continue
            i0, i1 = ln.idx(f), ln.idx(l)
            heard = " ".join(wtext(w) for w in ws[i0:i1 + 1])
            base.update(start_word=i0, end_word=i1, heard=heard)
            sus = next((s for s in it.suspects if i0 <= s.i1 and i1 >= s.i0), None)
            if sus is None or i0 < sus.i0 - MAX_EXTEND or i1 > sus.i1 + MAX_EXTEND:
                rej.append({**base, "why_rejected": "not on a suspect span"})
                continue
            base["suspect"] = sus
            if not squash(corrected):
                rej.append({**base, "why_rejected": "empty correction"})
                continue
            if squash(corrected) == squash(heard):
                continue   # same words (spacing / punctuation only): the transcript is right
            if squash(corrected) not in squash(heard_line):
                rej.append({**base, "why_rejected": "corrected words not in the model's heard_line"})
                continue
            # span edges: a corrected text that ends with the next word (starts with the previous one) of the line
            # already covers that word, so the span grows over it; one that is just a piece of a neighbouring word
            # (across lines too: padded audio hears the neighbours) is the neighbour, not the suspect
            sc = squash(corrected)
            edge = None
            for side in (1, -1):
                j = (i1 + 1) if side == 1 else (i0 - 1)
                if not 0 <= j < len(ws):
                    continue
                nb = squash(wtext(ws[j]))
                if not nb or abs((ws[j]["start"] if side == 1 else ws[j]["end"])
                                 - (ws[i1]["end"] if side == 1 else ws[i0]["start"])) > 1.5:
                    continue
                inside = ln.i0 <= j <= ln.i1
                pc, pn = _phon(corrected), _phon(wtext(ws[j]))
                absorbs = (len(sc) > len(nb) >= 2 and (sc.endswith(nb) if side == 1 else sc.startswith(nb))) or \
                    (len(pc) > len(pn) >= 3 and (pc.endswith(pn) if side == 1 else pc.startswith(pn)))
                if absorbs and inside and (sus.i0 - MAX_EXTEND <= j <= sus.i1 + MAX_EXTEND):
                    i0, i1 = min(i0, j), max(i1, j)
                    continue
                if absorbs:
                    edge = "corrected text repeats a neighbouring word outside the span"
                elif (nb.startswith(sc) if side == 1 else nb.endswith(sc)) or \
                        difflib.SequenceMatcher(None, sc, nb, autojunk=False).ratio() >= 0.8:
                    edge = "corrected text is the neighbouring word (audio bleed)"
            if edge:
                rej.append({**base, "why_rejected": edge})
                continue
            if (i0, i1) != (base["start_word"], base["end_word"]):
                heard = " ".join(wtext(w) for w in ws[i0:i1 + 1])
                base.update(start_word=i0, end_word=i1, heard=heard, extended=True)
                f, l = ln.pos(i0), ln.pos(i1)
            style = style_only_change(heard, corrected)
            if style:
                rej.append({**base, "why_rejected": style})
                continue
            ratio = difflib.SequenceMatcher(None, squash(_apply_fix_line(tr, ln, f, l, corrected)),
                                            squash(heard_line), autojunk=False).ratio()
            base["line_ratio"] = round(ratio, 3)
            if ratio < MIN_LINE_RATIO:
                rej.append({**base, "why_rejected": f"heard_line does not align with the transcript line ({ratio:.2f})"})
                continue
            base["accepted"] = conf >= min_conf
            fixes.append(base)
    return fixes, rej, {batch[k - 1].line.id for k in seen}


# ------------------------------------------------------------------ assemble
def _keep_trailing_punct(heard: str, corrected: str) -> str:
    m = _TRAIL_RE.search(heard.strip())
    if m and not _TRAIL_RE.search(corrected.strip()):
        return corrected.strip() + m.group(0)
    return corrected.strip()


def assemble(tr: dict, lines: list[Line], suspects: list[Suspect], fixes: list[dict], checked: set,
             apply_context: bool = False, skipped: set | None = None) -> tuple[list[dict], list[dict]]:
    """Corrections (one per suspect span with any corrected text) and the uncertain list."""
    byid = {ln.id: ln for ln in lines}
    ws = tr["word_timestamps"]
    corr, unc = [], []
    used = set()
    for fx in sorted(fixes, key=lambda x: -x["confidence"]):
        s = fx["suspect"]
        key = (s.line, s.i0, s.i1)
        if key in used:
            continue
        used.add(key)
        ln = byid[s.line]
        if fx["accepted"]:
            corr.append({
                "start_word": fx["start_word"], "end_word": fx["end_word"], "heard": fx["heard"],
                "corrected": _keep_trailing_punct(fx["heard"], fx["corrected"]), "reason": fx["reason"],
                "source": "relisten", "confidence": round(fx["confidence"], 3), "applied": True,
                "found_by": sorted(s.found_by), "line": s.line, "start": ws[fx["start_word"]]["start"],
                "end": ws[fx["end_word"]]["end"], "context_guess": s.guess or None,
                "relisten": {"heard_line": fx["heard_line"], "line_ratio": fx.get("line_ratio")},
            })
        else:
            fx["_low"] = True
    for s in suspects:
        key = (s.line, s.i0, s.i1)
        if key in used and any(c["line"] == s.line and c["start_word"] <= s.i1 and c["end_word"] >= s.i0
                               for c in corr):
            continue
        heard = " ".join(wtext(w) for w in ws[s.i0:s.i1 + 1])
        low = next((fx for fx in fixes if fx["suspect"] is s and not fx["accepted"]), None)
        if s.line in checked:
            verdict = "relisten_low_confidence" if low else "relisten_kept"
        else:
            verdict = "skipped_interjection" if skipped and (s.i0, s.i1) in skipped else "not_relistened"
        if s.guess and squash(s.guess) != squash(heard):
            corr.append({
                "start_word": s.i0, "end_word": s.i1, "heard": heard,
                "corrected": _keep_trailing_punct(heard, s.guess), "reason": s.why, "source": "context",
                "confidence": None, "applied": bool(apply_context and verdict != "relisten_kept"),
                "found_by": sorted(s.found_by), "line": s.line, "start": ws[s.i0]["start"], "end": ws[s.i1]["end"],
                "context_guess": s.guess, "relisten": {"verdict": verdict, **({
                    "corrected": low["corrected"], "confidence": low["confidence"], "reason": low["reason"],
                    "heard_line": low["heard_line"]} if low else {})},
            })
        else:
            unc.append({"start_word": s.i0, "end_word": s.i1, "heard": heard,
                        "source": "context" if "context" in s.found_by else "flag",
                        "confidence": None if s.min_conf is None else round(s.min_conf, 3), "verdict": verdict,
                        "line": s.line, "start": ws[s.i0]["start"],
                        **({"relisten_low": {"corrected": low["corrected"], "confidence": low["confidence"],
                                             "reason": low["reason"]}} if low else {})})
    corr.sort(key=lambda c: c["start_word"])
    unc.sort(key=lambda c: c["start_word"])
    return corr, unc


# ------------------------------------------------------------------ loader helpers (for prompts and captions)
def corrections_path(video_id: str, out_dir: str | Path = ROOT / "runs" / "asr_fix") -> Path:
    return Path(out_dir) / video_id / "corrections.json"


def load_corrections(src: str | Path) -> dict:
    """``src`` = a video id (runs/asr_fix/<id>/corrections.json) or a path. Missing file -> empty layer."""
    p = Path(src)
    if not p.suffix:
        p = corrections_path(str(src))
    if not p.exists():
        return {"corrections": [], "uncertain": []}
    return json.loads(p.read_text(encoding="utf-8"))


def _chosen(corrections, sources) -> list[dict]:
    rows = corrections.get("corrections", []) if isinstance(corrections, dict) else list(corrections)
    if sources is None:
        pick = [c for c in rows if c.get("applied")]
    else:
        pick = [c for c in rows if c.get("source") in sources]
    out, taken = [], set()
    for c in sorted(pick, key=lambda c: -(c.get("confidence") or 0)):
        span = set(range(int(c["start_word"]), int(c["end_word"]) + 1))
        if span & taken:
            continue
        taken |= span
        out.append(c)
    return sorted(out, key=lambda c: c["start_word"])


def apply_corrections(words: list, corrections, sources: tuple | None = None) -> list[dict]:
    """Display words with corrections applied; timings and indices unchanged.

    ``words``: Scribe ``word_timestamps`` dicts or ``loader.Word`` objects. ``corrections``: the corrections.json dict
    or its list. Default: entries with ``applied`` true; ``sources=("relisten", "context")`` picks by source instead.
    A corrected span shows the corrected text on its first word and "" on the rest (``corrected_by`` marks them), so
    word ``i`` is still word ``i``. Overlapping corrections: the more confident one wins."""
    out = []
    for i, w in enumerate(words):
        out.append({"i": i, "text": wtext(w), "start": w["start"] if isinstance(w, dict) else w.start,
                    "end": w["end"] if isinstance(w, dict) else w.end, "corrected_by": None})
    for k, c in enumerate(_chosen(corrections, sources)):
        a, b = int(c["start_word"]), int(c["end_word"])
        if not (0 <= a <= b < len(out)):
            continue
        out[a]["text"] = c["corrected"]
        for i in range(a, b + 1):
            out[i]["corrected_by"] = k
            if i > a:
                out[i]["text"] = ""
    return out


def display_text(words: list, corrections, i0: int = 0, i1: int | None = None, sources: tuple | None = None) -> str:
    """Text of words i0..i1 (inclusive) with corrections applied. A correction that starts before i0 shows its
    corrected text only if it starts inside the range (spans are never split)."""
    dw = apply_corrections(words, corrections, sources)
    i1 = len(dw) - 1 if i1 is None else i1
    return " ".join(d["text"] for d in dw[i0:i1 + 1] if d["text"])


# ------------------------------------------------------------------ display marks (review page, opt-in LLM text)
# Corrected words are shown corrected AND marked; leftover low-confidence words get "(?)". Scribe's transcript is
# never modified: these helpers only change what a reader (person or model) sees.
FIXED_FMT = "{fixed: %s}"      # LLM text: a corrected span
UNSURE_FMT = "%s(?)"           # LLM text: a low-confidence word with no applied correction
ASR_LEGEND = ("{fixed: ...} marks words the speech-to-text first misheard and that were corrected by re-listening "
              "to the audio; word(?) marks a low-confidence word that may be misheard.")

_VERDICT_NOTE = {
    "relisten_kept": "re-listened: the audio agrees with the transcript",
    "relisten_low_confidence": "re-listened: unclear",
    "skipped_interjection": "interjection, not re-listened",
    "not_relistened": "not re-listened",
}


def stale_corrections(words: list, corrections) -> list[dict]:
    """Corrections whose ``heard`` no longer matches the transcript words at their indices (the transcript was
    re-made after the layer was built). :func:`word_marks` skips them; callers should warn."""
    rows = corrections.get("corrections", []) if isinstance(corrections, dict) else list(corrections or [])
    out = []
    for c in rows:
        a, b = int(c["start_word"]), int(c["end_word"])
        if not (0 <= a <= b < len(words)):
            out.append(c)
        elif c.get("heard") is not None and squash(c["heard"]) != squash(" ".join(wtext(w) for w in words[a:b + 1])):
            out.append(c)
    return out


def word_marks(words: list, corrections, threshold: float = DEFAULT_THRESHOLD, sources: tuple | None = None
               ) -> dict[int, dict]:
    """Per-word display marks, only for words that have one (word index -> mark).

    - ``{"kind": "fixed", "first", "last", "corrected", "heard", "reason", "source", "confidence"}`` on every word
      of a chosen correction (default: ``applied`` ones, as :func:`apply_corrections`); stale ones are skipped.
    - ``{"kind": "unsure", "p", "note"}`` on a word with confidence exp(logprob) < ``threshold`` that no chosen
      correction covers (words without a logprob or with no letters are never marked). ``note`` says what the
      correction layer knows about it (re-listen verdict, an unapplied guess), "" if nothing."""
    stale = {id(c) for c in stale_corrections(words, corrections)}
    marks: dict[int, dict] = {}
    for c in _chosen(corrections, sources):
        if id(c) in stale:
            continue
        a, b = int(c["start_word"]), int(c["end_word"])
        m = {"kind": "fixed", "first": a, "last": b, "corrected": c["corrected"],
             "heard": " ".join(wtext(w) for w in words[a:b + 1]), "reason": c.get("reason") or "",
             "source": c.get("source"), "confidence": c.get("confidence")}
        for i in range(a, b + 1):
            marks[i] = m
    notes: dict[int, str] = {}
    if isinstance(corrections, dict):
        for u in corrections.get("uncertain") or []:
            low = u.get("relisten_low")
            note = (f"re-listened: maybe \"{low['corrected']}\" (confidence {low['confidence']}, not applied)"
                    if low else _VERDICT_NOTE.get(u.get("verdict"), ""))
            for i in range(int(u["start_word"]), int(u["end_word"]) + 1):
                notes[i] = note
        for c in corrections.get("corrections") or []:
            if c.get("start_word") in marks and marks[c["start_word"]]["corrected"] == c["corrected"]:
                continue
            note = f"text-only guess: \"{c['corrected']}\" (not applied)"
            v = (c.get("relisten") or {}).get("verdict")
            if v in _VERDICT_NOTE:
                note += f"; {_VERDICT_NOTE[v]}"
            for i in range(int(c["start_word"]), int(c["end_word"]) + 1):
                notes.setdefault(i, note)
    for i, w in enumerate(words):
        if i in marks:
            continue
        p = wconf(w)
        if p is not None and p < threshold and squash(wtext(w)):
            marks[i] = {"kind": "unsure", "p": round(p, 3), "note": notes.get(i, "")}
    return marks


def marked_tokens(words: list, marks: dict[int, dict], idxs) -> list[dict]:
    """Display tokens for the word indices ``idxs`` (in order; e.g. ``range(i0, i1 + 1)`` or a unit's words without
    backchannels): ``{"i", "j", "text", "kind": "word"|"fixed"|"unsure", "mark"}``, ``i..j`` the words it covers.
    A correction is shown only when all its words are in ``idxs`` (one token, on its first word); a span cut by the
    range shows its words as Scribe heard them, each ``unsure`` with ``mark["partial"]`` set."""
    idxs = list(idxs)
    shown = set(idxs)
    out = []
    for i in idxs:
        m = marks.get(i)
        if m and m["kind"] == "fixed":
            if all(j in shown for j in range(m["first"], m["last"] + 1)):
                if i == m["first"]:
                    out.append({"i": i, "j": m["last"], "text": m["corrected"], "kind": "fixed", "mark": m})
                continue
            out.append({"i": i, "j": i, "text": wtext(words[i]), "kind": "unsure", "mark": {**m, "partial": True}})
            continue
        out.append({"i": i, "j": i, "text": wtext(words[i]), "kind": "unsure" if m else "word", "mark": m})
    return out


def format_token(t: dict, fixed_fmt: str = FIXED_FMT, unsure_fmt: str = UNSURE_FMT) -> str:
    return (fixed_fmt if t["kind"] == "fixed" else unsure_fmt if t["kind"] == "unsure" else "%s") % t["text"]


def marked_text(words: list, marks: dict[int, dict], idxs, fixed_fmt: str = FIXED_FMT,
                unsure_fmt: str = UNSURE_FMT) -> str:
    """Text of ``idxs`` with corrections applied and marked (LLM form by default: ``{fixed: ...}``, ``word(?)``)."""
    return " ".join(format_token(t, fixed_fmt, unsure_fmt) for t in marked_tokens(words, marks, idxs))


def display_map(words: list, marks: dict[int, dict], idxs, fixed_fmt: str = FIXED_FMT,
                unsure_fmt: str = UNSURE_FMT) -> dict[int, str]:
    """word index -> what to print in its place ("" for the 2nd.. words of a shown correction); unmarked words are
    left out (print the word itself)."""
    out: dict[int, str] = {}
    for t in marked_tokens(words, marks, idxs):
        if t["kind"] == "word":
            continue
        out[t["i"]] = format_token(t, fixed_fmt, unsure_fmt)
        for j in range(t["i"] + 1, t["j"] + 1):
            out[j] = ""
    return out


def mark_tip(t: dict) -> str:
    """Hover / tap text for a marked token (review page)."""
    m = t["mark"] or {}
    if t["kind"] == "fixed":
        how = "audio re-listen" if m.get("source") == "relisten" else f"{m.get('source')}"
        conf = f", confidence {m['confidence']}" if m.get("confidence") is not None else ""
        return "\n".join(x for x in (f"ASR fix ({how}{conf})", f"Scribe heard: {m.get('heard', '')}",
                                     f"Why: {m['reason']}" if m.get("reason") else "") if x)
    if m.get("partial"):
        return (f"Part of an ASR fix that crosses this cut: Scribe heard \"{m.get('heard', '')}\", "
                f"audio says \"{m.get('corrected', '')}\"")
    return "\n".join(x for x in (f"Low ASR confidence (p = {m.get('p')})", m.get("note") or "") if x)


# ------------------------------------------------------------------ cost
def _inr(model: str, tin: int, tout: int, audio_tokens: int = 0) -> float:
    from shotvi.llm.pricing import cost_usd
    from shotvi.llm.spend import usd_inr
    usd = cost_usd(model, tin + int(audio_tokens * (AUDIO_PRICE_FACTOR - 1)), tout) or 0.0
    return usd * usd_inr()


def project(tr: dict, lines: list[Line], flagged: list[int], layers: set, n_context_extra: int = 15,
            model: str = MODEL["model"], skip_interjections: bool = True) -> dict:
    """Projected tokens and rupees per layer, no call. Conservative: Telugu text at 1.5 chars/token, 2a assumed to
    add ``n_context_extra`` unflagged suspect lines, ~120 output tokens per relisten clip + 600 per call."""
    out = {"model": model, "layers": {}}
    if "context" in layers:
        system, user = context_request(tr, lines, flagged)
        tin = int(TEXT_CAL * (estimate_tokens(system) + estimate_tokens(user)))
        tout = 600 + 90 * (len(flagged) // 2 + n_context_extra)
        out["layers"]["context"] = {"calls": 1, "input_tokens": tin, "output_tokens": tout,
                                    "inr": round(_inr(model, tin, tout), 2)}
    if "relisten" in layers:
        sus = flag_suspects(tr, lines, flagged) if "flag" in layers else []
        if skip_interjections:
            sus = [x for x in sus if not interjection_only(tr, x)]
        items = plan_items(tr, lines, sus)
        speech = [ln for ln in lines if ln.kind == "speech" and ln.id not in {it.line.id for it in items}]
        avg = (sum(ln.end - ln.start for ln in speech) / len(speech) + 2 * PAD_S) if speech else 3.0
        n_items = len(items) + (n_context_extra if "context" in layers else 0)
        audio_s = sum(it.dur for it in items) + (n_context_extra if "context" in layers else 0) * avg
        bs = batches(items) if items else []
        n_calls = max(1, math.ceil(n_items / BATCH_ITEMS), len(bs)) if n_items else 0
        system, user, _ = relisten_request(tr, lines, items[:1], None, "proj") if items else ("", "", [])
        per_item_text = (estimate_tokens(user) if items else 300)
        _, sys_only, _u = _load_prompt(RELISTEN_PROMPT)
        tin_text = int(TEXT_CAL * (n_calls * estimate_tokens(sys_only) + n_items * per_item_text))
        audio_tokens = int(audio_s * AUDIO_TOKENS_PER_S)
        tout = n_items * 100 + n_calls * 400
        out["layers"]["relisten"] = {
            "calls": n_calls, "items": n_items, "audio_s": round(audio_s, 1), "audio_tokens": audio_tokens,
            "input_tokens": tin_text + audio_tokens, "output_tokens": tout,
            "inr": round(_inr(model, tin_text + audio_tokens, tout, audio_tokens), 2)}
    out["inr"] = round(sum(v["inr"] for v in out["layers"].values()), 2)
    return out


class Guard:
    """Refuses a call whose projected cost would push the project ledger past ``limit_inr`` (None = no guard)."""

    def __init__(self, limit_inr: float | None):
        self.limit = limit_inr

    def check(self, projected_inr: float, what: str):
        if self.limit is None:
            return
        from shotvi.llm.spend import total_inr
        now = total_inr()
        if now + projected_inr > self.limit:
            raise BudgetStop(f"{what}: ledger ₹{now:.2f} + projected ₹{projected_inr:.2f} > limit ₹{self.limit:.2f}")


# ------------------------------------------------------------------ run
def run(tr: dict, video_id: str, client=None, spec=None, layers=("flag", "context", "relisten"),
        threshold: float = DEFAULT_THRESHOLD, audio: str | Path | None = None, min_conf: float = MIN_CONF,
        guard: Guard | None = None, apply_context: bool = False, skip_interjections: bool = True, band_from: float = 0.0,
        log=print) -> dict:
    """Run the requested layers and return the corrections dict (not written). ``client`` = LLMClient-like
    (``generate_json(role, spec, system, prompt, schema, meta=, want_raw=, media=)``); ``spec`` = ModelSpec."""
    layers = set(layers)
    guard = guard or Guard(None)
    lines = build_lines(tr)
    flagged = flag_words(tr, threshold, band_from) if "flag" in layers else []
    flag_s = flag_suspects(tr, lines, flagged)
    proj = project(tr, lines, flagged, layers, model=getattr(spec, "model", MODEL["model"]),
                   skip_interjections=skip_interjections)
    cost = {"projected": proj, "actual": {}}
    ctx_s, rejected, stopped = [], [], None
    fixes, checked, skipped = [], set(), set()
    model = getattr(spec, "model", MODEL["model"])

    def billed(fn, role, est_inr, est_audio=0):
        guard.check(est_inr, role)
        led = getattr(client, "ledger", None)
        b0 = led.billed_usd() if led else 0.0
        obj, raw = fn()
        audio_tok = 0
        if led and led.billed_usd() > b0:   # a real (uncached) call: add the audio surcharge to the rupee ledger
            audio_tok = int((getattr(raw, "prompt_modalities", None) or {}).get("AUDIO", 0))
            if audio_tok and AUDIO_PRICE_FACTOR > 1:
                from shotvi.llm import spend
                spend.record(model, int(audio_tok * (AUDIO_PRICE_FACTOR - 1)), 0,
                             source=os.environ.get("SHOTVI_SPEND_SOURCE", "asrfix") + "-audio-surcharge")
        a = cost["actual"].setdefault(role, {"calls": 0, "cached": 0, "input_tokens": 0, "output_tokens": 0,
                                             "audio_tokens": 0, "inr": 0.0, "inr_full": 0.0})
        a["calls"] += 1
        fresh = bool(led and led.billed_usd() > b0)
        a["cached"] += 0 if fresh else 1
        if raw is not None:
            a["input_tokens"] += raw.input_tokens
            a["output_tokens"] += raw.output_tokens + raw.thought_tokens
            a["audio_tokens"] += audio_tok
            # inr_full: what an uncached run pays (cache hits do not store the audio count: estimated from duration)
            a["inr_full"] = round(a["inr_full"] + _inr(model, raw.input_tokens, raw.output_tokens + raw.thought_tokens,
                                                       audio_tok if fresh else est_audio), 3)
            if fresh:
                a["inr"] = round(a["inr"] + _inr(model, raw.input_tokens, raw.output_tokens + raw.thought_tokens,
                                                 audio_tok), 3)
        return obj

    try:
        if "context" in layers and client is not None:
            system, user = context_request(tr, lines, flagged)
            pc = proj["layers"]["context"]["inr"]

            def call():
                r = client.generate_json("asr_context", spec, system, user, CONTEXT_SCHEMA,
                                         meta={"video_id": video_id}, want_raw=True)
                return r if isinstance(r, tuple) else (r, None)
            ans = billed(call, "context", pc)
            ctx_s, rj = parse_context(ans, tr, lines, flagged)
            rejected += [{"layer": "context", **x} for x in rj]
            log(f"[asr_fix] {video_id}: context read -> {len(ctx_s)} suspects ({len(rj)} rejected)")
        suspects = merge_suspects(flag_s, ctx_s)
        if "relisten" in layers and client is not None and suspects:
            if audio is None:
                raise FileNotFoundError(f"{video_id}: no audio file for relisten")
            rel = [x for x in suspects if not (skip_interjections and interjection_only(tr, x))]
            skipped = {(x.i0, x.i1) for x in suspects if x not in rel}
            items = plan_items(tr, lines, rel)
            bs = batches(items)
            for bi, b in enumerate(bs):
                system, user, media = relisten_request(tr, lines, b, audio, f"{video_id}:{Path(audio).name}")
                tin = int(TEXT_CAL * (estimate_tokens(system) + estimate_tokens(user)))
                atok = int(sum(it.dur for it in b) * AUDIO_TOKENS_PER_S)
                est = _inr(model, tin + atok, 100 * len(b) + 400, atok)

                def call(system=system, user=user, media=media):
                    r = client.generate_json("asr_relisten", spec, system, user, RELISTEN_SCHEMA,
                                             meta={"video_id": video_id, "batch": bi}, want_raw=True, media=media)
                    return r if isinstance(r, tuple) else (r, None)
                ans = billed(call, "relisten", est, atok)
                fx, rj, ck = parse_relisten(ans, tr, b, min_conf)
                fixes += fx
                checked |= ck
                rejected += [{"layer": "relisten", **{k: v for k, v in x.items() if k != "suspect"}} for x in rj]
                log(f"[asr_fix] {video_id}: relisten batch {bi + 1}/{len(bs)}: {len(b)} lines, "
                    f"{sum(f['accepted'] for f in fx)} accepted, {len(fx) - sum(f['accepted'] for f in fx)} low-conf, "
                    f"{len(rj)} rejected")
    except BudgetStop as e:
        stopped = str(e)
        log(f"[asr_fix] STOP: {e}")
        suspects = merge_suspects(flag_s, ctx_s)
    corr, unc = assemble(tr, lines, suspects, fixes, checked, apply_context=apply_context, skipped=skipped)
    return {
        "video_id": video_id, "schema": SCHEMA_VERSION,
        "params": {"layers": sorted(layers), "threshold": threshold, "min_conf": min_conf, "model": model,
                   "pad_s": PAD_S, "ctx_lines": CTX_LINES, "batch_items": BATCH_ITEMS,
                   "context_prompt": _load_prompt(CONTEXT_PROMPT)[0], "relisten_prompt": _load_prompt(RELISTEN_PROMPT)[0],
                   "apply_context": apply_context, "skip_interjections": skip_interjections,
                   **({"band_from": band_from} if band_from else {})},
        "lines": len(lines), "n_flagged_words": len(flagged), "n_suspects": len(suspects),
        "suspects": [s.to_dict(tr) for s in suspects],
        "corrections": corr, "uncertain": unc, "rejected": rejected,
        "threshold_report": threshold_report(tr), "cost": cost, "stopped": stopped,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="ASR correction layer (never edits the transcript).")
    ap.add_argument("video_id")
    ap.add_argument("--transcript", help="default scribe_dataset/<id>/<id>_scribe_transcript.json")
    ap.add_argument("--audio", help="default scribe_dataset/<id>/<id>_audio.wav (or .upload.mp3)")
    ap.add_argument("--layers", default="flag,context,relisten")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--min-conf", type=float, default=MIN_CONF)
    ap.add_argument("--band-from", type=float, default=0.0,
                    help="experiment: flag only words with band-from <= p < threshold (use with --layers flag,relisten)")
    ap.add_argument("--dry-run", action="store_true", help="print the projection; no API call")
    ap.add_argument("--relisten-interjections", action="store_true",
                    help="also re-listen suspects made only of interjections / backchannels (skipped by default)")
    ap.add_argument("--max-ledger-inr", type=float, default=None,
                    help="stop before any call whose projection would push the project ledger past this")
    ap.add_argument("--out", default=str(ROOT / "runs" / "asr_fix"))
    a = ap.parse_args(argv)
    vid = a.video_id
    tp = Path(a.transcript or ROOT / "scribe_dataset" / vid / f"{vid}_scribe_transcript.json")
    tr = json.loads(tp.read_text(encoding="utf-8"))
    layers = {x.strip() for x in a.layers.split(",") if x.strip()}
    lines = build_lines(tr)
    flagged = flag_words(tr, a.threshold, a.band_from)
    rep = threshold_report(tr)
    print(f"{vid}: {len(tr['word_timestamps'])} words, {len(lines)} lines; flagged words/lines by threshold: "
          + ", ".join(f"p<{k}: {v['words']}/{v['lines']}" for k, v in rep["by_threshold"].items()))
    proj = project(tr, lines, flagged, layers, skip_interjections=not a.relisten_interjections)
    for k, v in proj["layers"].items():
        print(f"  projected {k:9}: {json.dumps(v)}")
    print(f"  projected total: ₹{proj['inr']:.2f}")
    if a.dry_run:
        return 0
    os.environ.setdefault("SHOTVI_SPEND_SOURCE", "asrfix")
    from shotvi.llm import Ledger, LLMClient, ModelSpec
    client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"), max_retries=2, schema_retries=1)
    spec = ModelSpec.from_dict(MODEL)
    audio = Path(a.audio) if a.audio else find_audio(vid)
    res = run(tr, vid, client, spec, layers, a.threshold, audio, a.min_conf, Guard(a.max_ledger_inr),
              skip_interjections=not a.relisten_interjections, band_from=a.band_from)
    res["transcript"] = str(tp)
    out = corrections_path(vid, a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    act = res["cost"]["actual"]
    print(f"  actual: " + ", ".join(f"{k} ₹{v['inr']:.2f} ({v['calls']} calls, {v['cached']} cached)"
                                   for k, v in act.items()))
    print(f"  {len(res['corrections'])} corrections ({sum(c['applied'] for c in res['corrections'])} applied), "
          f"{len(res['uncertain'])} uncertain, {len(res['rejected'])} rejected -> {out}")
    return 2 if res["stopped"] else 0


if __name__ == "__main__":
    sys.exit(main())
