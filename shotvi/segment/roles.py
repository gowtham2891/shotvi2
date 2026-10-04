"""Speaker roles: infer the host from behaviour, never from speaker ids.

Host evidence, per real (non-ghost) speaker:
- share of all question turns that hand the floor to someone else (hosts ask),
- 1 - share of talk time (hosts usually speak less),
- share of prompts: turns followed by another speaker talking >= 15s.

With one real speaker there is no host (monologue, role "solo", label S).
With 2+ speakers the top scorer becomes host only if it is clearly ahead:
its margin over the runner-up is >= ``MIN_HOST_MARGIN`` and it actually asked
or prompted at least ``MIN_HOST_ASKS`` times. Otherwise there is no host (a
panel, co-hosts, a debate): every real speaker gets role "speaker" and labels
S1, S2, ... by talk time. With a host, the others are guests (G, or G1, G2, ...
by talk time). Ghost speakers get role "other". ``confidence`` is the score
margin between the top two (reported even when it is too small for a host).

Turns in ``exclude`` (opening teaser / music: out-of-context excerpts, often
mis-diarized) are ignored for every piece of evidence, including talk share.
Reported ``talk_s`` / ``talk_share`` stay whole-episode figures.
"""
from __future__ import annotations

from dataclasses import dataclass

from .loader import Transcript
from .turns import Turn

PROMPT_ANSWER_S = 15.0
# Each score is the sum of three shares in [0, 1] (questions asked, prompts
# given, 1 - talk share), so the margin / 3 is in [0, 1]. 0.1 means the leader
# is ahead by 0.3 share-points in total, e.g. a 57/43 split on all three
# behaviours or a 65/35 split on one; anything flatter looks like peers
# talking (panel, co-hosts), where naming a host would invent H/G roles and
# "prompt" Q->A links.
MIN_HOST_MARGIN = 0.1
MIN_HOST_ASKS = 1   # question turns + prompts: a host must have handed the floor over at least once


@dataclass
class SpeakerInfo:
    id: str
    role: str       # host | guest | solo | speaker | other
    label: str      # H, G, G1, G2, S (solo), S1, S2 (no clear host), O
    talk_s: float
    talk_share: float
    n_turns: int
    n_question_turns: int
    host_score: float
    is_ghost: bool

    def to_dict(self):
        return {"id": self.id, "role": self.role, "label": self.label, "talk_s": round(self.talk_s, 1),
                "talk_share": round(self.talk_share, 3), "n_turns": self.n_turns,
                "n_question_turns": self.n_question_turns, "host_score": round(self.host_score, 3),
                "is_ghost": self.is_ghost}


def infer_roles(tr: Transcript, turns: list[Turn],
                exclude=frozenset()) -> tuple[dict[str, SpeakerInfo], str | None, float]:
    real = list(tr.speakers)
    all_talk = sum(tr.talk_time.values()) or 1.0
    talk = dict(tr.talk_time)
    if exclude:
        for t in turns:
            if t.id in exclude:
                for i in range(t.i0, t.i1 + 1):
                    w = tr.words[i]
                    talk[w.speaker] = talk.get(w.speaker, 0.0) - max(0.0, w.dur)
    total_talk = sum(max(talk.get(s, 0.0), 0.0) for s in real) or 1.0
    n_turns = {s: 0 for s in tr.talk_time}
    q_turns = {s: 0 for s in tr.talk_time}
    prompts = {s: 0 for s in tr.talk_time}
    for k, t in enumerate(turns):
        n_turns[t.speaker] = n_turns.get(t.speaker, 0) + 1
        if t.id in exclude:
            continue
        nxt = turns[k + 1] if k + 1 < len(turns) else None
        if nxt is None or nxt.speaker == t.speaker or nxt.id in exclude:
            continue
        if t.asking_clause(tr.words) is not None:
            q_turns[t.speaker] = q_turns.get(t.speaker, 0) + 1
        # how long does the other side talk before this speaker takes a real turn again?
        resp = 0.0
        for u in turns[k + 1:]:
            if u.speaker == t.speaker or u.id in exclude:
                break
            resp += u.dur
        if resp >= PROMPT_ANSWER_S:
            prompts[t.speaker] = prompts.get(t.speaker, 0) + 1

    tq = sum(q_turns[s] for s in real) or 1
    tp = sum(prompts[s] for s in real) or 1
    scores = {}
    for s in real:
        share = max(talk.get(s, 0.0), 0.0) / total_talk
        scores[s] = (q_turns[s] / tq) + (prompts[s] / tp) + (1.0 - share)

    host = None
    conf = 0.0
    if len(real) >= 2:
        ranked = sorted(real, key=lambda s: -scores[s])
        conf = (scores[ranked[0]] - scores[ranked[1]]) / 3.0  # each score is in [0, 3]
        if conf >= MIN_HOST_MARGIN and q_turns[ranked[0]] + prompts[ranked[0]] >= MIN_HOST_ASKS:
            host = ranked[0]
    info: dict[str, SpeakerInfo] = {}
    guests = [s for s in real if s != host]  # already ordered by talk time
    for s in tr.talk_time:
        ghost = s not in real
        if ghost:
            role, label = "other", "O"
        elif len(real) == 1:
            role, label = "solo", "S"
        elif host is None:
            role, label = "speaker", f"S{real.index(s) + 1}"
        elif s == host:
            role, label = "host", "H"
        else:
            role = "guest"
            label = "G" if len(guests) == 1 else f"G{guests.index(s) + 1}"
        info[s] = SpeakerInfo(s, role, label, tr.talk_time.get(s, 0.0),
                              tr.talk_time.get(s, 0.0) / all_talk,
                              n_turns.get(s, 0), q_turns.get(s, 0), scores.get(s, 0.0), ghost)
    return info, host, round(conf, 3)
