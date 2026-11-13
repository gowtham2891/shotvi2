"""Changing where a clip starts and ends (the clip editor's "Clip timing", founder 2026-11-13).

A clip is a list of pieces of the episode ({start, end, lines}: source seconds and the transcript lines inside). Only the
outer edges move: the first piece's start and the last piece's end; cuts inside the clip stay. The creator decides the
new edges; the code only checks them (inside the episode, at least 3 s, at most MAX_CLIP_S long) and recomputes which
transcript lines the edge pieces contain. Caption edits made in the editor survive: edited words keep their place on
the clip's new clock, words of newly added parts come from the transcript, words now outside the clip are dropped.
"""
from __future__ import annotations

from selection_v2.vertical import PAD_IN, PAD_OUT

MAX_CLIP_S = 180.0      # the product's hard maximum for a clip (CLAUDE.md / flow decisions)
MIN_CLIP_S = 3.0
EDGE_MIN_S = 1.0        # an edge piece keeps at least this much
CONTEXT_S = 60.0        # transcript shown around the clip in the editor


def speech_lines(flow: dict) -> list[dict]:
    return [r for r in flow.get("lines") or [] if r.get("kind") == "speech"]


def context_lines(flow: dict, pieces: list[dict], window: float = CONTEXT_S) -> list[dict]:
    """Transcript lines from `window` s before the clip to `window` s after it, each marked whether it is played."""
    a, b = float(pieces[0]["start"]), float(pieces[-1]["end"])
    played = {lid for p in pieces for lid in p.get("lines", [])}
    out = []
    for r in speech_lines(flow):
        s, e = float(r["start"]), float(r.get("end", r["start"]))
        if e < a - window or s > b + window:
            continue
        out.append({"id": r["id"], "start": round(s, 2), "end": round(e, 2), "text": r.get("text", ""),
                    "in": r["id"] in played or (a <= s and e <= b and len(pieces) == 1)})
    return out


def _lines_in(flow: dict, a: float, b: float) -> list[str]:
    """Speech lines that overlap [a, b] (source seconds), in order."""
    return [r["id"] for r in speech_lines(flow) if float(r.get("end", r["start"])) > a and float(r["start"]) < b]


def retime(flow: dict, pieces: list[dict], start: float, end: float, episode_s: float) -> list[dict]:
    """New pieces with the first piece starting at `start` and the last ending at `end` (``ValueError`` if not
    allowed). The edge pieces' line lists are recomputed so their words (and captions) follow."""
    if not pieces:
        raise ValueError("this clip has no pieces")
    start, end = round(float(start), 2), round(float(end), 2)
    new = [dict(p) for p in pieces]
    if start < 0 or end > episode_s + 0.01:
        raise ValueError("the clip must stay inside the episode")
    if start > float(new[0]["end"]) - EDGE_MIN_S:
        raise ValueError("the start is past the clip's first part; trim inside the clip with the transcript instead")
    if end < float(new[-1]["start"]) + EDGE_MIN_S:
        raise ValueError("the end is before the clip's last part")
    new[0]["start"] = start
    new[-1]["end"] = end
    length = sum(float(p["end"]) - float(p["start"]) for p in new)
    if length < MIN_CLIP_S:
        raise ValueError(f"a clip must be at least {MIN_CLIP_S:g} s")
    if length > MAX_CLIP_S:
        raise ValueError(f"a clip can be at most {MAX_CLIP_S:g} s long; this would be {length:.0f} s")
    for i in {0, len(new) - 1}:
        new[i]["lines"] = _lines_in(flow, float(new[i]["start"]), float(new[i]["end"]))
    return new


MIN_PIECE_S = 0.2       # a part of the clip (between two cuts) must play at least this long
MERGE_GAP_S = 0.05      # a cut smaller than this is no cut: the two parts are joined
MAX_PIECES = 60         # creator Shorts use 8-26 pieces; more only makes every re-render slower and costlier


def repiece(flow: dict, pieces: list[dict], spans: list, episode_s: float) -> list[dict]:
    """New pieces from the stretches that should play, [[start, end], ...] in episode seconds (the creator's edit:
    edges moved, cuts made bigger or smaller, brought back or added). ``ValueError`` if not allowed. A piece that is
    unchanged keeps its line list; changed ones are recomputed so their words (and captions) follow."""
    if len(spans or []) > MAX_PIECES:
        raise ValueError(f"a clip can have at most {MAX_PIECES} parts")
    try:
        spans = sorted((round(float(a), 2), round(float(b), 2)) for a, b in spans)
    except (TypeError, ValueError):
        raise ValueError("each part needs a start and an end")
    if not spans:
        raise ValueError("a clip needs at least one part")
    merged: list[list[float]] = []
    for a, b in spans:
        if b - a < MIN_PIECE_S:
            raise ValueError(f"each part of the clip must play at least {MIN_PIECE_S:g} s")
        if merged and a <= merged[-1][1] + MERGE_GAP_S:          # overlapping or touching: one part
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    if merged[0][0] < 0 or merged[-1][1] > episode_s + 0.01:
        raise ValueError("the clip must stay inside the episode")
    length = sum(b - a for a, b in merged)
    if length < MIN_CLIP_S:
        raise ValueError(f"a clip must be at least {MIN_CLIP_S:g} s")
    if length > MAX_CLIP_S:
        raise ValueError(f"a clip can be at most {MAX_CLIP_S:g} s long; this would be {length:.0f} s")
    same = {(round(float(p["start"]), 2), round(float(p["end"]), 2)): p for p in pieces}
    out = []
    for a, b in merged:
        old = same.get((a, b))
        out.append(dict(old) if old else {"start": a, "end": b, "lines": _lines_in(flow, a, b)})
    return out


def _spans(pieces: list[dict]) -> list[tuple[float, float, float]]:
    """(source from, source to, clip-clock offset) of each piece as played (padded), in play order."""
    out, off = [], 0.0
    for p in pieces:
        a, b = max(0.0, float(p["start"]) - PAD_IN), float(p["end"]) + PAD_OUT
        out.append((a, b, off))
        off += b - a
    return out


def _to_source(t: float, spans) -> float | None:
    for i, (a, b, off) in enumerate(spans):
        if off - 1e-6 <= t < off + (b - a) or (i == len(spans) - 1 and t <= off + (b - a) + 0.05):
            return a + (t - off)
    return None


def _to_clock(src: float, spans) -> tuple[float, float] | None:
    """(clip-clock time, clock time where that piece ends) of a source moment, or None if it is not played."""
    for a, b, off in spans:
        if a - 1e-6 <= src < b:
            return off + (src - a), off + (b - a)
    return None


def clip_origin(pieces: list[dict]) -> float:
    """Where the clip's own clock starts, in source seconds (the first piece starts PAD_IN early)."""
    return max(0.0, float(pieces[0]["start"]) - PAD_IN)


def clock_length(pieces: list[dict]) -> float:
    """The clip's played length: every piece plays PAD_IN before and PAD_OUT after its own edges."""
    return sum(float(p["end"]) - float(p["start"]) + PAD_IN + PAD_OUT for p in pieces)


def rebase_draft(draft: dict | None, old: list[dict], new: list[dict], transcript_words: list[dict]) -> dict | None:
    """The editor draft on the new clock: edited words where the clip was before, transcript words in new parts.
    `transcript_words` = the new clip's words from the transcript (clip clock). Line splits follow their words.
    Words are matched through episode time, so this holds for moved edges and for cuts changed inside the clip."""
    if not draft or draft.get("words") is None:
        return draft
    so, sn = _spans(old), _spans(new)
    merged: list[tuple[dict, int | None]] = []
    for i, w in enumerate(draft["words"]):                      # edited words: still played -> their new time
        src = _to_source(float(w["start"]), so)
        hit = _to_clock(src, sn) if src is not None else None
        if hit:
            a, piece_end = hit
            b = min(a + max(0.0, float(w["end"]) - float(w["start"])), piece_end)
            merged.append(({**w, "start": round(a, 3), "end": round(b, 3)}, i))
    for w in transcript_words:                                  # words of the parts the clip did not have before
        s, e = _to_source(float(w["start"]), sn), _to_source(float(w["end"]), sn)
        if s is None:
            continue
        e = e if e is not None and e >= s else s + (float(w["end"]) - float(w["start"]))
        if all(min(e, b) - max(s, a) <= 0.02 for a, b, _ in so):
            merged.append(({"tok": w["tok"], "start": w["start"], "end": w["end"]}, None))
    merged.sort(key=lambda x: (x[0]["start"], x[0]["end"]))
    where = {old_i: n for n, (_, old_i) in enumerate(merged) if old_i is not None}
    breaks = sorted({where[b] for b in draft.get("breaks") or [] if b in where and where[b] > 0})
    return {**draft, "words": [w for w, _ in merged], "breaks": breaks}


# ----------------------------------------------------------------------------------------------- the timeline strip
def window(pieces: list[dict], episode_s: float, around: float = CONTEXT_S) -> tuple[float, float]:
    """The stretch of the episode the timeline shows: the clip plus `around` seconds each side."""
    a, b = float(pieces[0]["start"]), float(pieces[-1]["end"])
    return round(max(0.0, a - around), 2), round(min(episode_s or b + around, b + around), 2)


def peaks(wav_path, w0: float, w1: float, n: int = 480) -> list[float]:
    """The sound's loudness in `n` steps over [w0, w1] (0..1), for the timeline's waveform (pauses and reactions
    show). Reads only that stretch of the 16 kHz mono WAV."""
    import wave
    from pathlib import Path
    import numpy as np
    p = Path(wav_path)
    if not p.exists() or w1 <= w0:
        return []
    with wave.open(str(p)) as w:
        rate, width, ch = w.getframerate(), w.getsampwidth(), w.getnchannels()
        w.setpos(min(w.getnframes(), int(w0 * rate)))
        raw = w.readframes(int((w1 - w0) * rate))
    if width != 2 or not raw:
        return []
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if len(x) < n:
        return []
    rms = np.sqrt(np.mean(np.square(x[: len(x) // n * n].reshape(n, -1)), axis=1))
    top = float(np.percentile(rms, 98)) or 1.0
    return [round(float(v), 3) for v in np.clip(rms / top, 0, 1)]


def filmstrip(src, w0: float, w1: float, dst, frames: int = 24, height: int = 72, box=None) -> None:
    """`frames` stills spread over [w0, w1] side by side in one JPEG (the timeline's picture strip). `box` = the
    picture area (x, y, w, h) so baked-in black bars are trimmed."""
    import subprocess
    from pathlib import Path
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    step = (w1 - w0) / frames
    args, chains = [], []
    for i in range(frames):
        args += ["-ss", f"{w0 + step * (i + 0.5):.2f}", "-i", str(src)]
        trim = f"crop={box[2]}:{box[3]}:{box[0]}:{box[1]}," if box else ""
        chains.append(f"[{i}:v]{trim}scale=-2:{height},setsar=1[f{i}]")
    graph = ";".join(chains) + ";" + "".join(f"[f{i}]" for i in range(frames)) + f"hstack=inputs={frames}[v]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-filter_complex", graph, "-map", "[v]", "-frames:v", "1",
                    "-q:v", "5", str(dst)], check=True)
