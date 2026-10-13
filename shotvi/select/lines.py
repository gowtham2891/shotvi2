"""Transcript lines with stable ids for video work: the model answers ONLY with line ids; code converts to times.

Lines = Scribe ``sentences[]`` plus ``audio_events[]`` (each on its own line), in time order, ids L0001...
Rendered as ``L0123 [7:52] [speaker_2]: text`` (the time is only an orientation aid for the model).
Positions the model may return:
  - a line id: the line's own span;
  - "between L_a and L_b": the gap from L_a's end to L_b's start (visual beats with no dialogue);
  - the same id on both sides: that line's own span (a beat that is exactly one shot line).
Optional shot lines (``shot_lines=True``): every stretch with no dialogue longer than ``SPLIT_S`` (music, song,
silence) is also listed shot by shot as ``S0131 [8:08.5] (shot, no dialogue)``, numbered in time order, so the
model can place a silent beat on the exact shots instead of "somewhere inside a 44 s song line".
Times are snapped to shot cuts (runs/video/<id>/shots.json) when a cut lies within ``SNAP_S``. A span is imprecise
without shot lines, or when an edge is a long event line or missing; it is capped at ``MAX_BEAT_S`` (ending on the last
shot cut inside the cap) and flagged ``capped`` with the ``full`` span kept for the reviewer. A "silent" beat that
spans more than ``MAX_TALK_LINES`` dialogue lines is returned as ``{"invalid": reason}``.
ASR layer (``asr_fix=True``, opt-in, off by default): speech lines with an audio-confirmed correction or a leftover
low-confidence word show ``{fixed: corrected words}`` / ``word(?)`` (``ASR_LEGEND`` explains both to a model);
line ids and times never change. Off, the text is Scribe's, byte-identical to before the option existed.
Speaker names (``names`` in ``render``, opt-in, off by default): ``speaker_name_map`` reads the speaker correction layer
(``runs/speaker_fix/<id>.json``, schema speaker_fix/2, ``shotvi.segment.speaker_fix``) and maps each speech line to its
corrected name, so a line renders as ``[Prasad]`` instead of ``[speaker_3]``. Lines the layer has no name for keep
Scribe's label; the line dicts (ids, times, ``speaker``) never change, only the rendered label. Off (``names=None``),
``render`` is byte-identical to before the option existed.
"""
from __future__ import annotations

import bisect
import json
import sys
from pathlib import Path

from shotvi.segment.asr_fix import ASR_LEGEND  # noqa: F401  (for prompts that turn asr_fix on)

SNAP_S = 1.5
SPLIT_S = 6.0       # silent stretches longer than this get shot lines
MAX_BEAT_S = 12.0   # cap on an imprecise beat / hold
MIN_HOLD_S = 2.0    # a capped span ends on a shot cut at least this far in
MIN_SHOT_S = 1.0    # shot lines shorter than this merge into a neighbour
MAX_TALK_LINES = 2  # a "silent" beat containing more dialogue lines than this is rejected


def fmt(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def asr_marks(video_id: str, words: list, asr_fix) -> dict | None:
    """Word display marks from the ASR correction layer (``asr_fix``: True = runs/asr_fix/<id>/corrections.json,
    or the corrections dict itself). None when off; a missing layer warns and renders like off."""
    if not asr_fix:
        return None
    from shotvi.segment import asr_fix as af
    if isinstance(asr_fix, dict):
        corr = asr_fix
    elif af.corrections_path(video_id).exists():
        corr = af.load_corrections(video_id)
    else:
        print(f"warning: {video_id}: asr_fix requested but {af.corrections_path(video_id)} does not exist; "
              f"lines are Scribe's text", file=sys.stderr)
        return None
    stale = af.stale_corrections(words, corr)
    if stale:
        print(f"warning: {video_id}: {len(stale)} ASR corrections no longer match the transcript; skipped",
              file=sys.stderr)
    return af.word_marks(words, corr)


def _speech_text(s: dict, words: list, marks: dict | None) -> str:
    """Scribe's sentence text, or (ASR layer on, and the line has a marked word) the corrected, marked text:
    ``{fixed: ...}`` for corrected words, ``word(?)`` for leftover low-confidence words."""
    if not marks or not s.get("words"):
        return s["text"]
    idxs = range(s["words"][0], s["words"][1] + 1)
    if not any(i in marks for i in idxs):
        return s["text"]
    from shotvi.segment.asr_fix import marked_text
    return marked_text(words, marks, idxs)


def build_lines(video_id: str, asr_fix: bool | dict = False, transcript: dict | None = None) -> list[dict]:
    """``asr_fix`` (opt-in, default off): speech text shows the ASR correction layer (see ``ASR_LEGEND``); ids,
    times and every other field are the same. Off = Scribe's text, byte-identical to before the option existed.
    ``transcript``: the Scribe dict (default: read scribe_dataset/<id>/<id>_scribe_transcript.json)."""
    tr = transcript if transcript is not None else json.loads(
        Path(f"scribe_dataset/{video_id}/{video_id}_scribe_transcript.json").read_text(encoding="utf-8"))
    marks = asr_marks(video_id, tr["word_timestamps"], asr_fix)
    rows = [{"start": s["start"], "end": s["end"], "speaker": s.get("speaker", "?"),
             "text": _speech_text(s, tr["word_timestamps"], marks),
             "kind": "speech", "words": s.get("words")} for s in tr["sentences"]]
    rows += [{"start": e["start"], "end": e["end"], "speaker": "event", "text": e["text"], "kind": "event"}
             for e in tr.get("audio_events", [])]
    rows.sort(key=lambda r: (r["start"], r["kind"] != "event"))
    for k, r in enumerate(rows):
        r["id"] = f"L{k + 1:04d}"
    return rows


def speaker_label(r: dict, names: dict[str, str] | None = None) -> str:
    """The speaker as shown to a model: the corrected name when ``names`` (line id -> name) has one, else Scribe's
    label (``speaker_3``)."""
    return (names or {}).get(r["id"]) or r["speaker"]


def speaker_fix_path(video_id: str) -> Path:
    return Path(__file__).resolve().parents[2] / "runs" / "speaker_fix" / f"{video_id}.json"


def clean_name(name) -> str | None:
    """One-line display name; brackets would break the ``[name]`` label, so they become parentheses."""
    s = " ".join(str(name or "").split()).replace("[", "(").replace("]", ")")
    return s or None


def speaker_name_map(video_id: str, lines: list[dict], fix: dict | None = None,
                     log=lambda m: print(m, file=sys.stderr)) -> dict[str, str]:
    """Line id -> corrected speaker name from the speaker correction layer (``fix``: the speaker_fix/2 document,
    default ``runs/speaker_fix/<id>.json``; a missing file raises FileNotFoundError). A speech line is matched to the
    layer's line with the same Scribe word range [i0, i1] and start time, so a layer made from another transcript
    version matches nothing (and warns). Lines with no name (``X`` / missing) are left out: they keep Scribe's label."""
    if fix is None:
        p = speaker_fix_path(video_id)
        if not p.exists():
            raise FileNotFoundError(f"{video_id}: speaker names requested but {p} does not exist "
                                    f"(run python -m shotvi.segment.speaker_fix {video_id})")
        fix = json.loads(p.read_text(encoding="utf-8"))
    if fix.get("schema") != "speaker_fix/2":
        raise ValueError(f"{video_id}: speaker_fix schema {fix.get('schema')!r}, expected 'speaker_fix/2'")
    by_words = {tuple(ln["words"]): ln for ln in fix.get("lines") or [] if ln.get("words")}
    out, unmatched = {}, 0
    for r in lines:
        if r.get("kind") != "speech" or not r.get("words"):
            continue
        ln = by_words.get(tuple(r["words"]))
        if ln is None or abs(float(ln.get("start", r["start"])) - float(r["start"])) > 0.01:
            unmatched += 1
            continue
        name = clean_name(ln.get("name"))
        if name:
            out[r["id"]] = name
    if unmatched:
        log(f"warning: {video_id}: {unmatched} speech lines have no matching speaker_fix line "
            f"(stale layer?); they keep Scribe's label")
    return out


def render(lines: list[dict], tags: dict[str, str] | None = None, names: dict[str, str] | None = None) -> str:
    """``names`` (opt-in, default None): line id -> corrected speaker name (``speaker_name_map``); None = Scribe's
    labels, byte-identical to before the option existed."""
    out = []
    for r in lines:
        tag = f" <{tags[r['id']]}>" if tags and r["id"] in tags else ""
        who = {"event": "(audio event)", "shot": "(shot, no dialogue)"}.get(r["kind"], f"[{speaker_label(r, names)}]")
        out.append(f"{r['id']} [{fmt(r['start'])}]{tag} {who}" + (f": {r['text']}" if r["text"] else ""))
    return "\n".join(out)


def load_cuts(video_id: str) -> list[float]:
    return json.loads((Path("runs/video") / video_id / "shots.json").read_text(encoding="utf-8"))["cuts"]


def snap(t: float, cuts: list[float], lo: float | None = None, hi: float | None = None) -> tuple[float, bool]:
    k = bisect.bisect_left(cuts, t)
    cand = [c for c in cuts[max(0, k - 2):k + 2] if abs(c - t) <= SNAP_S
            and (lo is None or c >= lo - 0.01) and (hi is None or c <= hi + 0.01)]
    if not cand:
        return t, False
    return min(cand, key=lambda c: abs(c - t)), True


def shot_rows(lines: list[dict], cuts: list[float]) -> list[dict]:
    """Shot lines for every stretch without speech longer than SPLIT_S (gaps and event-only time alike)."""
    speech = sorted((r["start"], r["end"]) for r in lines if r["kind"] == "speech")
    events = [r for r in lines if r["kind"] == "event"]
    end = max((r["end"] for r in lines), default=0.0)
    silent, t = [], 0.0
    for a, b in speech + [(end, end)]:
        if a - t > SPLIT_S:
            silent.append((t, a))
        t = max(t, b)
    rows = []
    for s0, s1 in silent:
        bounds = [s0]
        for c in cuts[bisect.bisect_right(cuts, s0):]:
            if c >= s1 - MIN_SHOT_S:
                break
            if c - bounds[-1] >= MIN_SHOT_S:   # slivers merge into the neighbouring shot
                bounds.append(c)
        bounds.append(s1)
        if len(bounds) < 3:     # one shot only: nothing finer than the stretch itself
            continue
        for j in range(len(bounds) - 1):
            a, b = bounds[j], bounds[j + 1]
            ev = [e["text"] for e in events if e["start"] < b and e["end"] > a]
            rows.append({"id": f"S{len(rows) + 1:04d}", "start": a, "end": b, "speaker": "shot", "kind": "shot",
                         "text": " ".join(dict.fromkeys(ev))})
    return rows


class LineIndex:
    def __init__(self, video_id: str | None = None, shot_lines: bool = False,
                 lines: list[dict] | None = None, cuts: list[float] | None = None, asr_fix: bool | dict = False):
        """``asr_fix`` (opt-in, default off): line text shows the ASR correction layer (``build_lines``)."""
        self.lines = lines if lines is not None else build_lines(video_id, asr_fix=asr_fix)
        self.cuts = cuts if cuts is not None else load_cuts(video_id)
        if shot_lines:
            self.lines = sorted(self.lines + shot_rows(self.lines, self.cuts),
                                key=lambda r: (r["start"], {"event": 0, "speech": 1, "shot": 2}[r["kind"]]))
        self.by_id = {r["id"]: r for r in self.lines}
        self.shot_lines = shot_lines

    def norm(self, x) -> str | None:
        if x is None:
            return None
        s = str(x).strip().upper()
        if not s or s in ("NONE", "NULL", "-"):
            return None
        pre = "S" if s.startswith("S") else "L"
        s = s.lstrip("LS")
        try:
            return f"{pre}{int(s):04d}"
        except ValueError:
            return None

    def line_span(self, lid) -> tuple[float, float] | None:
        r = self.by_id.get(self.norm(lid))
        return (r["start"], r["end"]) if r else None

    def gap_span(self, after_id, before_id, hold_s: float = 3.0) -> dict | None:
        """Time span of a beat placed between two lines (either side may be missing), snapped to shot cuts."""
        a = self.by_id.get(self.norm(after_id))
        b = self.by_id.get(self.norm(before_id))
        if not a and not b:
            return None
        if a and b and a is b:     # the beat IS this line (one shot line, or an event)
            t0, t1, precise = a["start"], a["end"], a["kind"] != "event" or a["end"] - a["start"] <= SPLIT_S
        else:
            # an audio-event line (music, laughter) can span the silent beat itself: the beat then covers the event
            t0 = (a["start"] if a["kind"] == "event" else a["end"]) if a else max(0.0, b["start"] - hold_s)
            t1 = (min(b["end"], b["start"] + 15.0) if b["kind"] == "event" else b["start"]) if b else a["end"] + hold_s
            # a long span is a guess unless the model placed it on shot lines (it can ignore them and answer with
            # the dialogue lines around a whole song), or bounded it by a long event line / nothing
            precise = (any(r is not None and r["kind"] == "shot" for r in (a, b))
                       and all(r is not None and (r["kind"] != "event" or r["end"] - r["start"] <= SPLIT_S)
                               for r in (a, b)))
        if t1 < t0:
            t0, t1 = t1, t0
        talk = [r["id"] for r in self.lines if r["kind"] == "speech" and r["start"] >= t0 - 0.05
                and r["end"] <= t1 + 0.05]
        if len(talk) > MAX_TALK_LINES:   # not a silent beat: the model bridged two distant lines
            return {"invalid": f"spans {len(talk)} dialogue lines ({fmt(t0)}-{fmt(t1)})",
                    "raw": [round(t0, 2), round(t1, 2)]}
        full = None
        if not precise and t1 - t0 > MAX_BEAT_S:
            full = [round(t0, 2), round(t1, 2)]
            inside = [c for c in self.cuts if t0 + MIN_HOLD_S <= c <= t0 + MAX_BEAT_S]
            t1 = inside[-1] if inside else t0 + MAX_BEAT_S
        s0, on0 = snap(t0, self.cuts, lo=t0 - SNAP_S, hi=t1)
        s1, on1 = snap(t1, self.cuts, lo=t0, hi=t1 + SNAP_S)
        out = {"start": round(s0, 2), "end": round(s1, 2), "raw": [round(t0, 2), round(t1, 2)],
               "snapped": [on0, on1], "label": f"{fmt(s0)}-{fmt(s1)}"}
        if full:
            out.update(capped=True, full=full)
        return out

    def in_range(self, t0: float, t1: float) -> list[dict]:
        return [r for r in self.lines if r["end"] > t0 and r["start"] < t1]
