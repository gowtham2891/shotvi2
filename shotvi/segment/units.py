"""Thought units: coherent ~5-40s chunks built from turns.

1. Inside each turn, choose split points between clauses with a small dynamic
   program: every boundary has a strength (pause relative to this video's pause
   distribution, end punctuation, a question starting next, a listener
   backchannel in the gap, discourse openers, minus "sentence clearly
   continues" cues). A split is taken when its strength beats a threshold,
   and segment length outside [min_s, max_s] is penalized so long monologues
   are split at their best available boundaries.
2. Pieces shorter than min_s are merged with a neighbour: same turn first, a
   short question joins the answer that follows, a short answer joins its
   question, otherwise the side with the smaller gap. This turns rapid
   back-and-forth into one "exchange" unit instead of many 1-second units.

Units tile the transcript: every word index belongs to exactly one unit, and
backchannel words stay inside the unit whose speech they interrupt.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from . import lexicon as L
from .loader import Transcript
from .pauses import PauseStats
from .text import QUESTION_THRESHOLD, ends_sentence, is_cutoff, norm, question_score
from .turns import Backchannel, Clause, Turn


MIN_MERGE_SCORE = -1.5
LEAD_Q_S = 20.0  # a question this early in a piece means the piece opens with a question


@dataclass
class UnitParams:
    min_s: float = 6.0
    soft_max_s: float = 30.0
    max_s: float = 40.0
    split_threshold: float = 1.6
    short_penalty: float = 2.0     # at 0s, linear to 0 at min_s
    soft_penalty_per_s: float = 0.03
    long_penalty_per_s: float = 0.3


@dataclass
class Unit:
    id: int
    i0: int
    i1: int
    start: float
    end: float
    turn_ids: list[int]
    main: list[int]                     # non-backchannel word indices
    backchannels: list[Backchannel]
    clauses: list[Clause]
    speaker: str                        # dominant speaker by talk time
    speaker_share: dict[str, float]
    boundary_before: float = 0.0        # strength of the cut at the unit start
    signals: dict = field(default_factory=dict)
    intro_kind: str | None = None       # teaser | music | intro if inside an opening range
    intro_id: int | None = None         # id of that IntroRange

    @property
    def dur(self):
        return self.end - self.start

    @property
    def in_intro(self) -> bool:
        return self.intro_kind is not None

    @property
    def kind(self):
        return "monologue" if len(self.speaker_share) == 1 else "exchange"


def _boundary_strength(tr: Transcript, ps: PauseStats, a: Clause, b: Clause | None,
                       bc_between: bool, in_quote: bool = False) -> float:
    """Strength of cutting between clause a and the following clause b (same turn)."""
    ws = tr.words
    last = ws[a.i1]
    if b is None:
        return 3.0
    gap = max(0.0, ws[b.i0].start - last.end)
    s = min(gap / ps.major, 2.0)
    if _event_between(tr, last.end, ws[b.i0].start):
        s += 2.0  # a song / music break is a hard boundary (a dramatic silence is not)
    if ends_sentence(last.text):
        s += 0.6
    if b.q >= QUESTION_THRESHOLD:
        s += 0.4
    if a.q >= QUESTION_THRESHOLD:
        s -= 0.3  # a (rhetorical) question is followed by its own answer
    if bc_between:
        s += 0.3
    if norm(ws[b.i0].text) in L.OPENERS:
        s += 0.2
    if norm(last.text) in L.CONTINUATION_END or is_cutoff(last.text):
        s -= 0.5
    if last.text.rstrip().endswith((",", "،")):
        s -= 0.5  # comma: the sentence goes on
    if in_quote:
        s -= 1.0  # inside reported speech "..."
    return s


_QUOTES = ('"', "“", "”")


def _quote_open_after(tr: Transcript, cl: list[Clause]) -> list[bool]:
    """For each clause, whether a double quote is still open after it (within the turn)."""
    out, open_ = [], False
    for c in cl:
        for i in c.words:
            t = tr.words[i].text
            open_ ^= (sum(t.count(q) for q in _QUOTES) % 2 == 1)
        out.append(open_)
    return out


def _event_between(tr: Transcript, t0: float, t1: float, kinds=("music",)) -> bool:
    return any(e.kind in kinds and e.start < t1 and e.end > t0 for e in tr.events)


def _penalty(d: float, p: UnitParams) -> float:
    pen = 0.0
    if d < p.min_s:
        pen += p.short_penalty * (p.min_s - d) / p.min_s
    if d > p.soft_max_s:
        pen += p.soft_penalty_per_s * (d - p.soft_max_s)
    if d > p.max_s:
        pen += p.long_penalty_per_s * (d - p.max_s)
    return pen


def _split_turn(tr: Transcript, ps: PauseStats, t: Turn, p: UnitParams) -> list[list[Clause]]:
    ws = tr.words
    cl = t.clauses
    n = len(cl)
    if n <= 1:
        return [cl]
    strength = []
    quote_open = _quote_open_after(tr, cl)
    for k in range(n - 1):
        bc_between = cl[k + 1].i0 - cl[k].i1 > 1
        strength.append(_boundary_strength(tr, ps, cl[k], cl[k + 1], bc_between, quote_open[k]))
    # best[k]: best score for clauses[0..k-1] with a segment ending right before k
    NEG = float("-inf")
    best = [NEG] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0.0
    horizon = 3 * p.max_s
    for k in range(1, n + 1):
        seg_end = ws[cl[k - 1].i1].end
        gain = (strength[k - 1] - p.split_threshold) if k < n else 0.0
        for j in range(k - 1, -1, -1):
            d = seg_end - ws[cl[j].i0].start
            if d > horizon and j < k - 1:
                break
            if best[j] == NEG:
                continue
            sc = best[j] + gain - _penalty(d, p)
            if sc > best[k]:
                best[k], back[k] = sc, j
    segs = []
    k = n
    while k > 0:
        j = back[k]
        segs.append(cl[j:k])
        k = j
    return segs[::-1]


@dataclass
class _Piece:
    i0: int
    i1: int
    start: float
    end: float
    turn_ids: list[int]
    clauses: list[Clause]
    speakers: set
    first_turn_speaker: str
    last_turn_speaker: str
    boundary_before: float

    @property
    def dur(self):
        return self.end - self.start

    @property
    def ends_q(self):
        return bool(self.clauses) and self.clauses[-1].q >= QUESTION_THRESHOLD

    lead_q: bool = False  # the piece's leading speech (first turn segment) asks something

    @property
    def starts_q(self):
        return self.lead_q


def _pieces(tr: Transcript, ps: PauseStats, turns: list[Turn], p: UnitParams) -> list[_Piece]:
    ws = tr.words
    out: list[_Piece] = []
    for t in turns:
        segs = _split_turn(tr, ps, t, p)
        for si, seg in enumerate(segs):
            i0 = t.i0 if si == 0 else out[-1].i1 + 1
            # backchannel words right after a cut stay with the speech they react to
            i1 = t.i1 if si == len(segs) - 1 else segs[si + 1][0].i0 - 1
            if si == 0:
                bstrength = 3.0 if not out else 2.0  # speaker change
            else:
                prev = segs[si - 1]
                bstrength = _boundary_strength(tr, ps, prev[-1], seg[0], seg[0].i0 - prev[-1].i1 > 1)
            out.append(_Piece(i0, i1, min(w.start for w in ws[i0:i1 + 1]),
                              max(w.end for w in ws[i0:i1 + 1]), [t.id], list(seg), {t.speaker},
                              t.speaker, t.speaker, bstrength,
                              any(c.q >= QUESTION_THRESHOLD
                                  and ws[c.i0].start - ws[seg[0].i0].start <= LEAD_Q_S for c in seg)))
    return out


def _clauses_in(tr: Transcript, clauses: list[Clause], a: int, b: int) -> list[Clause]:
    """Clauses restricted to words [a, b]. A clause cut by the range keeps its
    part: the part holding the clause's last word keeps its question score
    (Telugu marks questions at the end); an earlier part is re-scored alone."""
    out = []
    for c in clauses:
        wds = [i for i in c.words if a <= i <= b]
        if not wds:
            continue
        if len(wds) == len(c.words):
            out.append(c)
        elif wds[-1] == c.words[-1]:
            out.append(Clause(wds, c.q))
        else:
            out.append(Clause(wds, question_score([tr.words[i].text for i in wds])))
    return out


def _split_at_cuts(tr: Transcript, pieces: list[_Piece], cuts) -> list[_Piece]:
    """Split pieces so that every index in ``cuts`` starts a piece."""
    if not cuts:
        return pieces
    ws = tr.words
    out: list[_Piece] = []
    for pc in pieces:
        inner = sorted(c for c in cuts if pc.i0 < c <= pc.i1)
        if not inner:
            out.append(pc)
            continue
        edges = [pc.i0] + inner + [pc.i1 + 1]
        for k in range(len(edges) - 1):
            a, b = edges[k], edges[k + 1] - 1
            cl = _clauses_in(tr, pc.clauses, a, b)
            seg = ws[a:b + 1]
            spk = {w.speaker for w in seg}
            out.append(_Piece(a, b, min(w.start for w in seg), max(w.end for w in seg), pc.turn_ids, cl,
                              spk, ws[a].speaker if k else pc.first_turn_speaker,
                              ws[b].speaker if k < len(edges) - 2 else pc.last_turn_speaker,
                              pc.boundary_before if k == 0 else 3.0, pc.lead_q if k == 0 else False))
    return out


def _merge_short(tr: Transcript, pieces: list[_Piece], ps: PauseStats, p: UnitParams,
                 cuts=frozenset()) -> list[_Piece]:
    def _event_between_pieces(x: _Piece, y: _Piece) -> bool:
        a, b = (x, y) if x.start <= y.start else (y, x)
        return _event_between(tr, a.end, b.start)

    k = 0
    while k < len(pieces):
        pc = pieces[k]
        if pc.dur >= p.min_s or len(pieces) == 1:
            k += 1
            continue
        best, best_side = None, None
        for side in (-1, 1):
            j = k + side
            if not 0 <= j < len(pieces):
                continue
            nb = pieces[j]
            a, b = (nb, pc) if side < 0 else (pc, nb)
            if b.i0 in cuts:
                continue  # never merge across a hard cut (teaser / intro edge)
            gap = b.start - a.end
            merged = b.end - a.start
            same_turn = bool(set(a.turn_ids) & set(b.turn_ids))
            if gap > ps.long * (2 if same_turn else 1) or merged > p.max_s:
                continue
            score = 0.0
            if same_turn:
                score += 2.0
            if a.last_turn_speaker == b.first_turn_speaker:
                score += 1.0
            if side > 0 and pc.ends_q:
                score += 1.5          # a short question leads into its answer
            if side < 0 and nb.ends_q and nb.last_turn_speaker != pc.first_turn_speaker:
                score += 1.5          # a short answer belongs with its question
            if side > 0 and pc.starts_q and not pc.ends_q:
                score += 1.0          # opens with a question: still belongs with what follows
            if side < 0 and pc.starts_q and nb.last_turn_speaker != pc.first_turn_speaker:
                score -= 2.0          # don't hang a new question on the previous speaker's tail
            if side > 0 and nb.starts_q and nb.first_turn_speaker != pc.last_turn_speaker                     and not (pc.starts_q or pc.ends_q):
                score -= 3.0          # never glue a tail onto someone's new question
            if _event_between_pieces(nb, pc):
                continue
            score -= gap / max(ps.major, 1e-6) * 0.5
            if best is None or score > best:
                best, best_side = score, side
        if best_side is None or best < MIN_MERGE_SCORE:
            k += 1  # no acceptable neighbour: keep the short piece on its own
            continue
        j = k + best_side
        a, b = (pieces[j], pc) if best_side < 0 else (pc, pieces[j])
        m = _Piece(a.i0, b.i1, min(a.start, b.start), max(a.end, b.end),
                   sorted(set(a.turn_ids) | set(b.turn_ids)), a.clauses + b.clauses,
                   a.speakers | b.speakers, a.first_turn_speaker, b.last_turn_speaker,
                   a.boundary_before, a.lead_q)
        lo = min(j, k)
        pieces[lo:lo + 2] = [m]
        k = lo if best_side > 0 else max(lo, 0)
    return pieces


def _force_split(tr: Transcript, pieces: list[_Piece], p: UnitParams) -> list[_Piece]:
    """Fallback for stretches with no usable clause boundary (no punctuation and
    uniform gaps): split pieces longer than max_s at their largest same-speaker
    word gap, recursively, keeping each side >= min_s where possible."""
    ws = tr.words
    out: list[_Piece] = []
    stack = list(reversed(pieces))
    while stack:
        pc = stack.pop()
        if pc.dur <= p.max_s or pc.i1 - pc.i0 < 2:
            out.append(pc)
            continue
        best, best_i = None, None
        for i in range(pc.i0, pc.i1):
            a, b = ws[i], ws[i + 1]
            if a.speaker != b.speaker:
                continue
            left, right = a.end - pc.start, pc.end - b.start
            if min(left, right) < min(p.min_s, pc.dur / 3):
                continue
            sc = (b.start - a.end) + (0.5 if ends_sentence(a.text) else 0.0)                 - 0.001 * abs(left - right)  # prefer balanced splits on ties
            if best is None or sc > best:
                best, best_i = sc, i
        if best_i is None:
            out.append(pc)
            continue
        L_ = _Piece(pc.i0, best_i, pc.start, max(w.end for w in ws[pc.i0:best_i + 1]), pc.turn_ids,
                    _clauses_in(tr, pc.clauses, pc.i0, best_i), pc.speakers, pc.first_turn_speaker,
                    ws[best_i].speaker, pc.boundary_before, pc.lead_q)
        R_ = _Piece(best_i + 1, pc.i1, min(w.start for w in ws[best_i + 1:pc.i1 + 1]), pc.end,
                    pc.turn_ids, _clauses_in(tr, pc.clauses, best_i + 1, pc.i1), pc.speakers,
                    ws[best_i + 1].speaker, pc.last_turn_speaker, 0.0, False)
        stack.extend([R_, L_])
    return out


def build_units(tr: Transcript, ps: PauseStats, turns: list[Turn],
                p: UnitParams | None = None, cuts=frozenset()) -> list[Unit]:
    """``cuts``: word indices where a new unit must start (opening-range edges)."""
    p = p or UnitParams()
    ws = tr.words
    pieces = _split_at_cuts(tr, _pieces(tr, ps, turns, p), cuts)
    pieces = _merge_short(tr, pieces, ps, p, cuts)
    pieces = _force_split(tr, pieces, p)
    bc_by_turn = {t.id: t.backchannels for t in turns}
    turn_starts = [t.i0 for t in turns]
    units: list[Unit] = []
    for pc in pieces:
        k0 = max(0, bisect.bisect_right(turn_starts, pc.i0) - 1)
        k1 = max(0, bisect.bisect_right(turn_starts, pc.i1) - 1)
        pc.turn_ids = [turns[k].id for k in range(k0, k1 + 1)]
        bcs = [b for tid in pc.turn_ids for b in bc_by_turn[tid] if pc.i0 <= b.i0 and b.i1 <= pc.i1]
        bc_words = {i for b in bcs for i in range(b.i0, b.i1 + 1)}
        main = [i for i in range(pc.i0, pc.i1 + 1) if i not in bc_words]
        talk: dict[str, float] = {}
        for i in main:
            talk[ws[i].speaker] = talk.get(ws[i].speaker, 0.0) + max(ws[i].dur, 0.01)
        tot = sum(talk.values()) or 1.0
        share = {s: round(v / tot, 3) for s, v in sorted(talk.items(), key=lambda kv: -kv[1])}
        dom = next(iter(share)) if share else ws[pc.i0].speaker
        units.append(Unit(len(units), pc.i0, pc.i1, pc.start, pc.end, pc.turn_ids, main, bcs,
                          pc.clauses, dom, share, round(pc.boundary_before, 3)))
    return units
