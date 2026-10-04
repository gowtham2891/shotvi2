"""Question -> answer links, including long answers.

A link starts at a turn that asks something (a question clause near its end)
or, for the host, a declarative *prompt* ("మీరే background score కూడా
పెట్టారంట") followed by a long reply. The answer runs from the next speaker's
turn through their following turns, absorbing short non-question interjections
from others, and stops at the next question / substantial turn by someone
else, a long silence, or ``MAX_ANSWER_S``.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import lexicon as L
from .loader import Transcript
from .pauses import PauseStats
from .roles import PROMPT_ANSWER_S
from .text import norm
from .turns import Turn
SETUP_MAX_S = 30.0          # how much of the asking turn counts as setup
INTERJECTION_MAX_S = 6.0    # asker comments shorter than this don't end the answer
MIN_ANSWER_S = 2.0
MIN_PROMPT_S = 3.0
MAX_ANSWER_S = 600.0


@dataclass
class QALink:
    id: int
    kind: str                # question | prompt
    asker: str
    responder: str
    q_turn: int
    q_i0: int                # setup + question (inclusive word range)
    q_core_i0: int           # the question clause itself .. q_i1
    q_i1: int
    q_start: float
    q_core_start: float
    q_end: float
    a_i0: int
    a_i1: int
    a_start: float
    a_end: float
    a_turns: list[int]
    q_score: float
    end_reason: str
    q_units: list[int] = None
    a_units: list[int] = None

    def to_dict(self, roles=None):
        r = lambda s: roles[s].role if roles and s in roles else None  # noqa: E731
        return {
            "id": self.id, "kind": self.kind, "q_score": self.q_score,
            "question": {"speaker": self.asker, "role": r(self.asker),
                         "words": [self.q_i0, self.q_i1], "core_words": [self.q_core_i0, self.q_i1],
                         "start": round(self.q_start, 3), "core_start": round(self.q_core_start, 3),
                         "end": round(self.q_end, 3), "turn_id": self.q_turn, "unit_ids": self.q_units},
            "answer": {"speaker": self.responder, "role": r(self.responder),
                       "words": [self.a_i0, self.a_i1], "start": round(self.a_start, 3),
                       "end": round(self.a_end, 3), "duration": round(self.a_end - self.a_start, 2),
                       "turn_ids": self.a_turns, "unit_ids": self.a_units},
            "end_reason": self.end_reason,
        }


def _addresses_listener(tr: Transcript, t: Turn) -> bool:
    return any(norm(tr.words[i].text) in L.SECOND_PERSON for i in t.main)


def build_links(tr: Transcript, ps: PauseStats, turns: list[Turn], host: str | None,
                exclude=frozenset()) -> list[QALink]:
    """``exclude``: turn ids (opening teaser / music) that may neither ask,
    answer, nor be absorbed into an answer; an answer stops before them."""
    ws = tr.words
    real = set(tr.speakers)
    links: list[QALink] = []
    for k, t in enumerate(turns[:-1]):
        nxt = turns[k + 1]
        if t.speaker not in real or nxt.speaker not in real or nxt.speaker == t.speaker:
            continue
        if t.id in exclude or nxt.id in exclude:
            continue
        qc = t.asking_clause(ws)
        kind = None
        if qc is not None:
            kind = "question"
        elif host is not None and t.speaker == host and t.dur >= MIN_PROMPT_S:
            kind = "prompt"
        if kind is None:
            continue
        responder = nxt.speaker
        a_turns = [nxt.id]
        last = nxt
        reason = "end_of_transcript"
        m = k + 2
        while m < len(turns):
            u = turns[m]
            if u.id in exclude:
                reason = "intro_boundary"
                break
            if u.start - last.end > ps.long * 2:
                reason = "long_silence"
                break
            if u.end - nxt.start > MAX_ANSWER_S:
                reason = "max_length"
                break
            if u.speaker == responder:
                a_turns.append(u.id)
                last = u
                m += 1
                continue
            if u.is_question:
                reason = "next_question"
                break
            if u.dur > INTERJECTION_MAX_S or (u.dur >= MIN_PROMPT_S and _addresses_listener(tr, u)):
                reason = "other_speaker_turn"  # a new prompt, not a passing comment
                break
            # short comment by someone else: keep going only if the responder resumes
            if m + 1 < len(turns) and turns[m + 1].speaker == responder:
                a_turns.append(u.id)
                m += 1
                continue
            reason = "other_speaker_turn"
            break
        a_i0, a_i1 = nxt.i0, last.i1
        a_start, a_end = nxt.start, last.end
        resp_talk = sum(max(ws[i].dur, 0.0) for tid in a_turns for i in turns[tid].main
                        if ws[i].speaker == responder)
        if resp_talk < MIN_ANSWER_S:
            continue
        if kind == "prompt" and a_end - a_start < PROMPT_ANSWER_S:
            continue
        core = qc if kind == "question" else t.clauses[-1]
        setup = next((c for c in t.clauses if t.end - ws[c.i0].start <= SETUP_MAX_S), core)
        q_i0 = t.i0 if setup is t.clauses[0] else setup.i0
        links.append(QALink(len(links), kind, t.speaker, responder, t.id, q_i0, core.i0, t.i1,
                            ws[q_i0].start, ws[core.i0].start, t.end, a_i0, a_i1, a_start, a_end,
                            a_turns, round(core.q, 3), reason))
    return links
