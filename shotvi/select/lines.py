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
"""
from __future__ import annotations

import bisect
import json
from pathlib import Path

SNAP_S = 1.5
SPLIT_S = 6.0       # silent stretches longer than this get shot lines
MAX_BEAT_S = 12.0   # cap on an imprecise beat / hold
MIN_HOLD_S = 2.0    # a capped span ends on a shot cut at least this far in
MIN_SHOT_S = 1.0    # shot lines shorter than this merge into a neighbour
MAX_TALK_LINES = 2  # a "silent" beat containing more dialogue lines than this is rejected


def fmt(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def build_lines(video_id: str) -> list[dict]:
    tr = json.loads(Path(f"scribe_dataset/{video_id}/{video_id}_scribe_transcript.json").read_text(encoding="utf-8"))
    rows = [{"start": s["start"], "end": s["end"], "speaker": s.get("speaker", "?"), "text": s["text"],
             "kind": "speech", "words": s.get("words")} for s in tr["sentences"]]
    rows += [{"start": e["start"], "end": e["end"], "speaker": "event", "text": e["text"], "kind": "event"}
             for e in tr.get("audio_events", [])]
    rows.sort(key=lambda r: (r["start"], r["kind"] != "event"))
    for k, r in enumerate(rows):
        r["id"] = f"L{k + 1:04d}"
    return rows


def render(lines: list[dict], tags: dict[str, str] | None = None) -> str:
    out = []
    for r in lines:
        tag = f" <{tags[r['id']]}>" if tags and r["id"] in tags else ""
        who = {"event": "(audio event)", "shot": "(shot, no dialogue)"}.get(r["kind"], f"[{r['speaker']}]")
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
                 lines: list[dict] | None = None, cuts: list[float] | None = None):
        self.lines = lines if lines is not None else build_lines(video_id)
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
