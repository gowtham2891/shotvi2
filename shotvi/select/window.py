"""Word-indexed source windows for the tightening call.

One line per stretch between natural boundaries (spec section 0), so a line start is always a clean clip start and a
line end a clean clip end. Every word carries a local index (0..n-1 inside the window); the model answers in local
indices and ``Window.to_global`` maps them back. Listener backchannels are their own lines, tagged ``ack`` (pure
acknowledgement) or ``aside`` (other short interjection). Excluded words (teaser copies) never appear.

    [12:05.3] G: 0 ఎక్కడికి 1 వెళ్ళినా 2 ఈ 3 సమాజం ...
    [12:09.8] H ack: 14 ఉమ్
    ... [source skipped: 3m12s] ...
"""
from __future__ import annotations

from dataclasses import dataclass

EVENT_TAG = {"laughter": "(laugh)", "censored": "(censored)", "music": "(music)", "applause": "(applause)"}


def fmt_t(t: float) -> str:
    m, s = divmod(max(t, 0.0), 60)
    return f"{int(m)}:{s:04.1f}"


@dataclass
class Window:
    globals: list[int]      # local index -> global word index
    text: str
    blocks: list[tuple[int, int]]

    def to_global(self, k: int) -> int | None:
        return self.globals[k] if 0 <= k < len(self.globals) else None

    def local_of(self, g: int) -> int | None:
        try:
            return self.globals.index(g)
        except ValueError:
            return None


def build_window(ctx, blocks: list[tuple[int, int]], offset: int = 0) -> Window:
    """blocks: sorted, non-overlapping global word ranges (inclusive). ``offset`` shifts the displayed indices only
    (to number two windows in one index space); ``globals`` stays 0-based."""
    ws = ctx.words
    evs = [(e.start, EVENT_TAG[e.kind]) for e in ctx.seg.transcript.events if e.kind in EVENT_TAG]
    evs.sort()
    glob: list[int] = []
    lines: list[str] = []
    prev_end_i = None
    for b0, b1 in blocks:
        if prev_end_i is not None:
            gap = ws[b0].start - ws[prev_end_i].end
            m, s = divmod(int(gap), 60)
            lines.append(f"... [source skipped: {m}m{s:02d}s] ...")
        cur: list[str] = []
        head = None
        ev_k = 0
        while ev_k < len(evs) and evs[ev_k][0] < ws[b0].start - 0.5:
            ev_k += 1
        for i in range(b0, b1 + 1):
            if i in ctx.excluded:
                continue
            while ev_k < len(evs) and evs[ev_k][0] <= ws[i].start:
                cur.append(evs[ev_k][1])
                ev_k += 1
            if head is None:
                kind = ctx.bc_kind.get(i)
                tag = "" if kind is None else (" ack" if kind == "lexicon" else " aside")
                head = f"[{fmt_t(ws[i].start)}] {ctx.label(i)}{tag}:"
            cur.append(f"{offset + len(glob)} {ws[i].text}")
            glob.append(i)
            if ctx.natural_after[i] or i == b1:
                lines.append(head + " " + " ".join(cur))
                cur, head = [], None
        prev_end_i = b1
    return Window(glob, "\n".join(lines), blocks)


def build_stream(ctx, blocks: list[tuple[int, int]], offset: int = 0) -> Window:
    """Word-stream rendering for flow editing (tighten v8+): one paragraph per speaker turn, every word numbered,
    pauses marked inline ("/" >= 0.3 s, "//" >= 1.0 s), listener acknowledgements and short asides inline in
    brackets so they do not break the speaker's flow. Sentence punctuation is kept only as a reading aid."""
    ws = ctx.words
    evs = sorted((e.start, EVENT_TAG[e.kind]) for e in ctx.seg.transcript.events if e.kind in EVENT_TAG)
    glob: list[int] = []
    paras: list[str] = []
    prev_end_i = None
    for b0, b1 in blocks:
        if prev_end_i is not None:
            gap = ws[b0].start - ws[prev_end_i].end
            m, s = divmod(int(abs(gap)), 60)
            paras.append(f"... [source skipped: {m}m{s:02d}s] ..." if gap >= 0 else
                         f"... [jump back {m}m{s:02d}s in the source: a moved line] ...")
        cur: list[str] = []
        spk = None
        ev_k = 0
        while ev_k < len(evs) and evs[ev_k][0] < ws[b0].start - 0.5:
            ev_k += 1
        last_i = None
        for i in range(b0, b1 + 1):
            if i in ctx.excluded:
                continue
            while ev_k < len(evs) and evs[ev_k][0] <= ws[i].start:
                cur.append(evs[ev_k][1])
                ev_k += 1
            if last_i is not None and cur:
                gap = ws[i].start - ws[last_i].end
                if gap >= 1.0:
                    cur.append("//")
                elif gap >= 0.3:
                    cur.append("/")
            kind = ctx.bc_kind.get(i)
            tok = f"{offset + len(glob)} {ws[i].text}"
            glob.append(i)
            if kind is not None and spk is not None and ws[i].speaker != spk:
                tag = "ack" if kind == "lexicon" else "aside"
                # group consecutive listener words into one bracket
                if cur and cur[-1].startswith(f"[{ctx.label(i)} {tag}:") and cur[-1].endswith("]"):
                    cur[-1] = cur[-1][:-1] + " " + tok + "]"
                else:
                    cur.append(f"[{ctx.label(i)} {tag}: {tok}]")
            else:
                if ws[i].speaker != spk:
                    if cur:
                        paras.append(" ".join(cur))
                    cur = [f"[{fmt_t(ws[i].start)}] {ctx.label(i)}:"]
                    spk = ws[i].speaker
                cur.append(tok)
            last_i = i
        if cur:
            paras.append(" ".join(cur))
        prev_end_i = b1
    return Window(glob, "\n".join(paras), blocks)
