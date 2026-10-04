"""Payoff editor: move a clip's ending onto its verdict / result (prompt ``ending``).

Triggered when the judge says the ending is weak (a tip list, moral, framework, hedge, trailing line, or a payoff
<= 3.5). One narrow call names the opening question and the verdict line and picks: keep, end_at (cut the tail or
extend into the source right after the clip), or close_on_verdict (move an earlier verdict line to the end with the
spec's reorder rules). Code validates and builds the new cut; the caller re-scores and keeps the better version.
"""
from __future__ import annotations

from shotvi.llm import ModelSpec

from .clip import Candidate, code_checks, kept_words, metrics
from .joins import rebuild_notes
from .opener import _grow_to_sentence, _subtract
from .prompts import load_prompt
from .score import STRONG_ENDINGS
from .window import build_stream

AFTER_S = 45.0
MAX_EXTEND_WORDS = 120

_SPAN = {"type": "object", "properties": {"first": {"type": "integer"}, "last": {"type": "integer"}},
         "required": ["first", "last"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "opening_question": {"type": "string"},
        "last_sentence": {"type": "string"},
        "last_sentence_kind": {"type": "string", "enum": ["verdict", "result", "concrete_example", "punchline",
                                                           "emotional_peak", "tip_list", "moral", "framework",
                                                           "trailing_metaphor", "hedge", "side_tip", "mid_thought",
                                                           "other"]},
        "verdict_quote": {"type": "string"},
        "verdict": {**_SPAN, "description": "the verdict / result line (indices), or first=-1"},
        "action": {"type": "string", "enum": ["keep", "end_at", "close_on_verdict"]},
        "end_at": {"type": "integer", "description": "new last word (end_at) or where the body stops (close)"},
        "reason": {"type": "string"},
    },
    "required": ["opening_question", "last_sentence", "last_sentence_kind", "verdict_quote", "verdict", "action",
                 "end_at", "reason"],
}


def wants_ending_fix(cfg, c: Candidate) -> bool:
    e = cfg.get("ending_fix") or {}
    j = c.judge
    if not e.get("enabled") or not j or not c.pieces:
        return False
    if [r for r in c.reject if not r.endswith("(judge)")]:
        return False
    if float(j.get("value_emotion", 0)) < e.get("min_value", 3.0):
        return False
    if e.get("always_for_kinds") and j.get("clip_kind") in e["always_for_kinds"]:
        return True  # the judge misses weak endings too often; the payoff editor decides (keep is an option)
    weak = j.get("ending_kind") not in STRONG_ENDINGS or j.get("ending_problem", "none") != "none"
    return weak or (c.scores or {}).get("payoff", 5) <= e.get("max_payoff", 3.5)


def fix_ending(ctx, cfg, llm, cand: Candidate) -> tuple[Candidate | None, dict]:
    j = cand.judge or {}
    P = cand.pieces
    ws = ctx.words
    clip = build_stream(ctx, [tuple(p) for p in P])
    e = P[-1][1]
    a_end = min(ctx.n - 1, ctx.time_idx(ws[e].end + AFTER_S), e + MAX_EXTEND_WORDS)
    after = build_stream(ctx, [(e + 1, a_end)], offset=len(clip.globals)) if a_end > e else None
    glob = clip.globals + (after.globals if after else [])
    n_clip = len(clip.globals)
    p = load_prompt("ending")
    spec = ModelSpec.from_dict(cfg["models"].get("ender") or cfg["models"]["scorer"])
    # no judge labels here: the judge often calls a moral or a framework a "verdict", and would bias the editor
    prompt = p.render(opening=j.get("opening_question") or j.get("first_words", ""),
                      clip=clip.text, after=after.text if after else "(end of episode)")
    obj = llm.generate_json("ender", spec, p.system, prompt, SCHEMA, meta={"video": ctx.video_id, "cand": cand.id})
    tr = {"picker": obj}
    act = obj.get("action")
    if act not in ("end_at", "close_on_verdict"):
        return None, tr
    x = int(obj.get("end_at", -1))
    if not 0 <= x < len(glob):
        tr["error"] = "end_at out of range"
        return None, tr
    g = glob[x]
    # position of g in playback order
    k_piece = next((k for k, (a, b) in enumerate(P) if a <= g <= b), None) if x < n_clip else None
    if act == "end_at":
        if x < n_clip:
            if k_piece is None:
                return None, {**tr, "error": "end word not in clip"}
            newP = [list(q) for q in P[:k_piece]] + [[P[k_piece][0], g]]
        else:
            newP = [list(q) for q in P[:-1]] + [[P[-1][0], g]]
        note = None
    else:
        v = obj.get("verdict") or {}
        va, vb = sorted((int(v.get("first", -1)), int(v.get("last", -1))))
        if not (0 <= va <= vb < n_clip) or x >= n_clip or k_piece is None:
            return None, {**tr, "error": "verdict/end_at must be inside the clip"}
        a, b = glob[va], glob[vb]
        piece = next((q for q in P if q[0] <= a <= q[1]), None)
        if piece is None:
            return None, {**tr, "error": "verdict not in a kept piece"}
        a, b = _grow_to_sentence(ctx, a, b, max(piece[0] - 6, 0), piece[1] + 6)
        body = [list(q) for q in P[:k_piece]] + [[P[k_piece][0], g]]
        body = _subtract(body, a, b)
        if not body or body[-1][1] <= b:
            return None, {**tr, "error": "moved verdict must come from before the body end"}
        newP = body + [[a, b]]
        note = {"type": "reorder", "reason": " ".join(str(obj.get("reason", "")).split())[:199]
                or "Verdict line moved to the end so the clip closes on its answer."}
    newP = [q for q in newP if q[0] <= q[1]]
    if not newP or newP == P or any(i in ctx.excluded for i in kept_words(newP)):
        return None, {**tr, "error": "no change or excluded words"}
    new = Candidate(cand.id, cand.moment, newP, info={**cand.info, "ending_fix": act,
                                                       "ending_reason": obj.get("reason")})
    new.edit_notes = rebuild_notes(ctx, cfg, cand.edit_notes + ([note] if note else []), newP)
    code_checks(ctx, new)
    tr["new_pieces"] = newP
    tr["reject"] = new.reject
    if new.reject:
        return None, tr
    return new, tr
