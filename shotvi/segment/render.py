"""Compact transcript rendering for LLMs, plus overlapping chunks.

One line per unit::

    U42 [12:05] G: మీరు అడిగారు కదా ... (laugh) ... [H: ఇది కావాలి] ...
    U43 [12:31] H: how was that stage sir? | G: నాకు అదే stage ...

- ``[m:ss]`` is minutes from the start (minutes can exceed 59: ``[134:07]``).
- Labels: H host, G guest (G1, G2 with several guests), S solo speaker,
  S1, S2 speakers when no clear host (panel / co-hosts), O other/ghost. ``|`` separates speakers inside an exchange unit.
- Acknowledgement backchannels ("ఉమ్", "right") are dropped; other absorbed
  interjections stay inline in brackets because they can carry meaning.
- Audio events appear inline where they happen: (laugh), (censored), (music).

Token counts are an *estimate* (no tokenizer dependency): Telugu script
is expensive for most LLM tokenizers, so it is counted conservatively.
Calibrate ``TE_CHARS_PER_TOKEN`` against the provider's token counter.
"""
from __future__ import annotations

from dataclasses import dataclass

from .loader import Transcript
from .roles import SpeakerInfo
from .text import is_latin_char, is_telugu_char
from .units import Unit

TE_CHARS_PER_TOKEN = 1.5
OTHER_CHARS_PER_TOKEN = 4.0
EVENT_TAGS = {"laughter": "(laugh)", "censored": "(censored)", "music": "(music)",
              "applause": "(applause)", "silence": "(silence)"}

LEGEND = ("Each line is one thought unit: U<id> [minutes:seconds] SPEAKER: text. "
          "H=host, G=guest (G1/G2 for several guests), S=solo speaker, "
          "S1/S2=speakers when there is no clear host, O=other. "
          "'|' marks a speaker change inside a unit; [X: ...] is a short interjection by X; "
          "(laugh)/(censored)/(music) are audio events. Listener acknowledgements are omitted.")


def estimate_tokens(text: str) -> int:
    te = sum(1 for ch in text if is_telugu_char(ch))
    other = len(text) - te
    return int(te / TE_CHARS_PER_TOKEN + other / OTHER_CHARS_PER_TOKEN) + 1


def fmt_ts(t: float) -> str:
    t = max(0.0, t)
    m, s = divmod(int(t), 60)
    return f"{m}:{s:02d}"


@dataclass
class RenderedUnit:
    unit_id: int
    line: str
    tokens: int


INTRO_TAG = "(intro)"


def render_unit(tr: Transcript, u: Unit, roles: dict[str, SpeakerInfo], events=(), unit_tag: str = "") -> str:
    ws = tr.words
    label = lambda s: roles[s].label if s in roles else "O"  # noqa: E731
    bc_at = {b.i0: b for b in u.backchannels}
    bc_words = {i for b in u.backchannels for i in range(b.i0, b.i1 + 1)}
    ev = sorted(events, key=lambda e: e.start)
    ev_k = 0
    parts: list[str] = []
    cur_spk = None
    i = u.i0
    while i <= u.i1:
        while ev_k < len(ev) and ev[ev_k].start <= ws[i].start:
            tag = EVENT_TAGS.get(ev[ev_k].kind)
            if tag and (not parts or parts[-1] != tag):
                parts.append(tag)
            ev_k += 1
        if i in bc_at:
            b = bc_at[i]
            if b.kind != "lexicon":
                parts.append(f"[{label(b.speaker)}: {b.text}]")
            i = b.i1 + 1
            continue
        if i in bc_words:  # defensive; bc ranges are handled above
            i += 1
            continue
        w = ws[i]
        if w.speaker != cur_spk:
            parts.append(("| " if cur_spk is not None else "") + f"{label(w.speaker)}:")
            cur_spk = w.speaker
        parts.append(w.text)
        i += 1
    for e in ev[ev_k:]:
        tag = EVENT_TAGS.get(e.kind)
        if tag and (not parts or parts[-1] != tag):
            parts.append(tag)
    return f"U{u.id} [{fmt_ts(u.start)}] " + (unit_tag + " " if unit_tag else "") + " ".join(parts)


def _events_by_unit(tr: Transcript, units: list[Unit]) -> dict[int, list]:
    out: dict[int, list] = {u.id: [] for u in units}
    if not units:
        return out
    starts = [u.start for u in units]
    import bisect
    for e in tr.events:
        k = max(0, bisect.bisect_right(starts, e.start) - 1)
        out[units[k].id].append(e)
    return out


def render_units(tr: Transcript, units: list[Unit], roles: dict[str, SpeakerInfo]) -> list[RenderedUnit]:
    """All units, including ones inside opening ranges; greeting / host-intro
    units are tagged ``(intro)``. Hiding teaser units is the caller's choice
    (``Segmentation.render`` / ``render_chunks`` do it by default)."""
    evs = _events_by_unit(tr, units)
    out = []
    for u in units:
        tag = INTRO_TAG if getattr(u, "intro_kind", None) == "intro" else ""
        line = render_unit(tr, u, roles, evs[u.id], tag)
        out.append(RenderedUnit(u.id, line, estimate_tokens(line) + 1))
    return out


@dataclass
class Chunk:
    id: int
    first_unit: int
    last_unit: int
    start: float
    end: float
    est_tokens: int
    overlap_units: list[int]   # units also present in the previous chunk
    text: str

    def to_dict(self, with_text=True):
        d = {"id": self.id, "unit_ids": [self.first_unit, self.last_unit],
             "start": round(self.start, 2), "end": round(self.end, 2),
             "est_tokens": self.est_tokens, "overlap_unit_ids": self.overlap_units}
        if with_text:
            d["text"] = self.text
        return d


def chunk_rendered(rendered: list[RenderedUnit], units: list[Unit], max_tokens: int = 12000,
                   overlap_tokens: int = 1500) -> list[Chunk]:
    """Pack consecutive unit lines into chunks of <= max_tokens (a single
    oversized unit still gets its own chunk), each starting with ~overlap_tokens
    of the previous chunk's tail so moments on a chunk edge are seen whole."""
    if overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be smaller than max_tokens")
    chunks: list[Chunk] = []
    n = len(rendered)
    i = 0
    prev_last = -1
    while i < n:
        tot = 0
        j = i
        while j < n and (j == i or tot + rendered[j].tokens <= max_tokens):
            tot += rendered[j].tokens
            j += 1
        last = j - 1
        ov = list(range(i, prev_last + 1)) if prev_last >= i else []
        chunks.append(Chunk(len(chunks), rendered[i].unit_id, rendered[last].unit_id,
                            units[i].start, units[last].end, tot,
                            [rendered[k].unit_id for k in ov],
                            "\n".join(r.line for r in rendered[i:j])))
        if j >= n:
            break
        # next chunk starts overlap_tokens back from the end, but always makes progress
        back, k = 0, last
        while k > i + 1 and back + rendered[k].tokens <= overlap_tokens:
            back += rendered[k].tokens
            k -= 1
        if back == 0 and last > i:
            k = last - 1  # a single tail unit bigger than the overlap budget: still overlap by one unit
        i = max(k + 1, i + 1)
        prev_last = last
    return chunks
