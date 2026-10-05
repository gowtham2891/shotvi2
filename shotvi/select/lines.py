"""Transcript lines with stable ids for video work: the model answers ONLY with line ids; code converts to times.

Lines = Scribe ``sentences[]`` plus ``audio_events[]`` (each on its own line), in time order, ids L0001...
Rendered as ``L0123 [7:52] [speaker_2]: text`` (the time is only an orientation aid for the model).
Positions the model may return:
  - a line id: the line's own span;
  - "between L_a and L_b": the gap from L_a's end to L_b's start (visual beats with no dialogue);
Times are snapped to shot cuts (runs/video/<id>/shots.json) when a cut lies within ``SNAP_S``.
"""
from __future__ import annotations

import bisect
import json
from pathlib import Path

SNAP_S = 1.5


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
        who = "(audio event)" if r["kind"] == "event" else f"[{r['speaker']}]"
        out.append(f"{r['id']} [{fmt(r['start'])}]{tag} {who}: {r['text']}")
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


class LineIndex:
    def __init__(self, video_id: str):
        self.lines = build_lines(video_id)
        self.by_id = {r["id"]: r for r in self.lines}
        self.cuts = load_cuts(video_id)

    def norm(self, x) -> str | None:
        if x is None:
            return None
        s = str(x).strip().upper()
        if not s or s in ("NONE", "NULL", "-"):
            return None
        if not s.startswith("L"):
            s = "L" + s
        try:
            return f"L{int(s[1:]):04d}"
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
        # an audio-event line (music, laughter) can span the silent beat itself: the beat then covers the event
        t0 = (a["start"] if a["kind"] == "event" else a["end"]) if a else max(0.0, b["start"] - hold_s)
        t1 = (min(b["end"], b["start"] + 15.0) if b["kind"] == "event" else b["start"]) if b else a["end"] + hold_s
        if t1 < t0:
            t0, t1 = t1, t0
        s0, on0 = snap(t0, self.cuts, lo=t0 - SNAP_S, hi=t1)
        s1, on1 = snap(t1, self.cuts, lo=t0, hi=t1 + SNAP_S)
        return {"start": round(s0, 2), "end": round(s1, 2), "raw": [round(t0, 2), round(t1, 2)],
                "snapped": [on0, on1], "label": f"{fmt(s0)}-{fmt(s1)}"}

    def in_range(self, t0: float, t1: float) -> list[dict]:
        return [r for r in self.lines if r["end"] > t0 and r["start"] < t1]
