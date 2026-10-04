"""Speaker turns with listener backchannels absorbed (recorded, not deleted).

A *run* is a maximal stretch of consecutive words with the same diarized
speaker. A run is absorbed into the surrounding turn as a backchannel when it
is short and sits inside another speaker's speech:

- ``lexicon``: only acknowledgement tokens ("ఉమ్", "right", "makes sense"),
  surrounded by (or overlapping) another speaker;
- ``interjection``: any short (<=3 words, <=1.5s), non-question run surrounded by
  one other speaker (echoes like "ఇది కావాలి", "Super hit");
- ``overlap``: a short run whose timing overlaps a neighbour (not produced by
  Scribe today, but other ASR does);
- ``ghost``: a run of a ghost speaker (<2% talk time) surrounded by one speaker.

Shape rules do the work; the lexicon only relaxes the limits. A turn is a
contiguous inclusive word range ``[i0, i1]``: every word in it is either the
turn speaker's or belongs to one of ``turn.backchannels``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .loader import Transcript
from .pauses import PauseStats
from .text import QUESTION_THRESHOLD, ends_sentence, is_backchannel_text, question_score

# Shape limits (generic properties of backchannels, not tuned per video).
LEX_MAX_WORDS = 6
LEX_MAX_DUR = 2.5
INTERJ_MAX_WORDS = 3
INTERJ_MAX_DUR = 1.5
OVERLAP_MAX_WORDS = 4
OVERLAP_MAX_DUR = 2.0
GHOST_MAX_DUR = 10.0
OVERLAP_TOL = 0.05
QUESTION_TAIL_S = 8.0  # a question this close to the end of a turn is what the turn asks


@dataclass
class Run:
    speaker: str
    i0: int
    i1: int
    start: float
    end: float

    @property
    def n(self):
        return self.i1 - self.i0 + 1

    @property
    def dur(self):
        return self.end - self.start


@dataclass
class Backchannel:
    i0: int
    i1: int
    speaker: str
    text: str
    start: float
    end: float
    kind: str  # lexicon | interjection | overlap | ghost

    def to_dict(self):
        return {"words": [self.i0, self.i1], "speaker": self.speaker, "text": self.text,
                "start": round(self.start, 3), "end": round(self.end, 3), "kind": self.kind}


@dataclass
class Clause:
    """Stretch of the turn speaker's words between clause pauses / end punctuation."""
    words: list[int]  # main-speaker word indices (backchannel words excluded)
    q: float = 0.0    # question score

    @property
    def i0(self):
        return self.words[0]

    @property
    def i1(self):
        return self.words[-1]


@dataclass
class Turn:
    id: int
    speaker: str
    i0: int
    i1: int
    start: float
    end: float
    main: list[int] = field(default_factory=list)
    backchannels: list[Backchannel] = field(default_factory=list)
    clauses: list[Clause] = field(default_factory=list)

    @property
    def dur(self):
        return self.end - self.start

    @property
    def is_question(self) -> bool:
        return any(c.q >= QUESTION_THRESHOLD for c in self.clauses)

    @property
    def ends_with_question(self) -> bool:
        return bool(self.clauses) and self.clauses[-1].q >= QUESTION_THRESHOLD

    def asking_clause(self, words) -> Clause | None:
        """The question clause that hands the floor over: the last question
        clause, if it ends within QUESTION_TAIL_S of the end of the turn."""
        c = self.last_question_clause()
        if c is not None and self.end - words[c.i1].end <= QUESTION_TAIL_S:
            return c
        return None

    def last_question_clause(self) -> Clause | None:
        for c in reversed(self.clauses):
            if c.q >= QUESTION_THRESHOLD:
                return c
        return None


def speaker_runs(tr: Transcript, cuts=frozenset()) -> list[Run]:
    """Maximal same-speaker stretches; a run also ends before any index in ``cuts``."""
    runs: list[Run] = []
    ws = tr.words
    s0 = 0
    for i in range(1, len(ws) + 1):
        if i == len(ws) or ws[i].speaker != ws[s0].speaker or i in cuts:
            seg = ws[s0:i]
            runs.append(Run(ws[s0].speaker, s0, i - 1, min(w.start for w in seg), max(w.end for w in seg)))
            s0 = i
    return runs


def _classify_runs(tr: Transcript, runs: list[Run], ps: PauseStats) -> list[str | None]:
    ws = tr.words
    max_gap = max(ps.long, 2.0)
    ghosts = set(tr.ghost_speakers)
    toks = [[w.text for w in ws[r.i0:r.i1 + 1]] for r in runs]
    lex = [is_backchannel_text(t) for t in toks]
    lex_short = [lex[k] and runs[k].n <= LEX_MAX_WORDS and runs[k].dur <= LEX_MAX_DUR
                 for k in range(len(runs))]

    def neighbour(k, step):
        """Nearest run in direction `step` that is not a short lexicon backchannel of a third speaker."""
        j = k + step
        while 0 <= j < len(runs):
            if not (lex_short[j] and runs[j].speaker != runs[k].speaker):
                return runs[j]
            # a chain of backchannels from several listeners: skip over them
            j += step
        return None

    kinds: list[str | None] = [None] * len(runs)
    for k, r in enumerate(runs):
        P, N = neighbour(k, -1), neighbour(k, 1)
        # "surrounded" means the other speaker is talking right before and after,
        # not merely the next one to speak after a long silence or a song
        surrounded = (P is not None and N is not None and P.speaker == N.speaker != r.speaker
                      and r.start - P.end <= max_gap and N.start - r.end <= max_gap)
        prev_r = runs[k - 1] if k > 0 else None
        next_r = runs[k + 1] if k + 1 < len(runs) else None
        overlapping = ((prev_r is not None and r.start < prev_r.end - OVERLAP_TOL)
                       or (next_r is not None and r.end > next_r.start + OVERLAP_TOL))
        is_q = question_score(toks[k]) >= QUESTION_THRESHOLD
        if r.speaker in ghosts and surrounded and r.dur <= GHOST_MAX_DUR:
            kinds[k] = "ghost"
        elif lex_short[k] and (surrounded or overlapping):
            kinds[k] = "lexicon"
        elif overlapping and not is_q and r.n <= OVERLAP_MAX_WORDS and r.dur <= OVERLAP_MAX_DUR \
                and P is not None:
            kinds[k] = "overlap"
        elif surrounded and not is_q and r.n <= INTERJ_MAX_WORDS and r.dur <= INTERJ_MAX_DUR:
            kinds[k] = "interjection"
    return kinds


def _clauses(tr: Transcript, main: list[int], ps: PauseStats, turn_final_q: bool) -> list[Clause]:
    ws = tr.words
    out: list[Clause] = []
    cur: list[int] = []
    for n, i in enumerate(main):
        cur.append(i)
        last = n == len(main) - 1
        if last:
            break
        gap = ws[main[n + 1]].start - ws[i].end
        bc_between = main[n + 1] - i > 1  # a listener spoke here
        if ends_sentence(ws[i].text) or gap >= ps.minor or bc_between:
            out.append(Clause(cur))
            cur = []
    if cur:
        out.append(Clause(cur))
    for k, c in enumerate(out):
        final = turn_final_q and k == len(out) - 1
        c.q = question_score([ws[i].text for i in c.words], turn_final=final)
    return out


def build_turns(tr: Transcript, ps: PauseStats, cuts=frozenset()) -> list[Turn]:
    """``cuts``: word indices where a new turn must start (e.g. the edges of an
    opening teaser), so no turn or absorbed backchannel spans them."""
    runs = speaker_runs(tr, cuts)
    kinds = _classify_runs(tr, runs, ps)
    ws = tr.words
    turns: list[Turn] = []
    cur: Turn | None = None
    pending: list[tuple[Run, str]] = []  # backchannels waiting for the turn speaker to resume

    def add_main(r: Run):
        """Append run r as main speech: extend the current turn or open a new one."""
        nonlocal cur
        if cur is not None and r.speaker == cur.speaker:
            cur.i1 = r.i1
            cur.end = max(cur.end, r.end)
            cur.main.extend(range(r.i0, r.i1 + 1))
        else:
            cur = Turn(len(turns), r.speaker, r.i0, r.i1, r.start, r.end, list(range(r.i0, r.i1 + 1)))
            turns.append(cur)

    for r, kind in zip(runs, kinds):
        if r.i0 in cuts:
            for b, _ in pending:
                add_main(b)
            pending.clear()
            cur = None
        if kind is not None and cur is not None:
            pending.append((r, kind))
            continue
        if cur is not None and r.speaker == cur.speaker:
            for b, bk in pending:
                cur.backchannels.append(Backchannel(b.i0, b.i1, b.speaker, tr.text(b.i0, b.i1),
                                                    b.start, b.end, bk))
            pending.clear()
            add_main(r)
            continue
        # Speaker changed for real: backchannels not followed by the turn speaker
        # resuming are not inside a turn, so they become (short) turns themselves.
        for b, _ in pending:
            add_main(b)
        pending.clear()
        add_main(r)
    for b, _ in pending:  # trailing backchannels at the end of the file
        add_main(b)
    for k, t in enumerate(turns):
        # the last clause gets a "turn-final" bonus only if another speaker takes over
        handover = k + 1 < len(turns) and turns[k + 1].speaker != t.speaker
        t.clauses = _clauses(tr, t.main, ps, handover)
        t.start = min(ws[t.i0].start, t.start)
    return turns
