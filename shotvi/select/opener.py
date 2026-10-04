"""Auto cold open: repair a clip whose first words do not work as a hook (judge opener_problem != none).

One narrow LLM call picks either a complete line from inside the clip to play first (cold open, removed from its
original place per spec O4 default) or a line from just before the clip to prepend (earlier start). Code builds the new
piece list, snaps the moved line to natural boundaries, runs the spec checks, and the caller re-scores and keeps the
better version.
"""
from __future__ import annotations

from shotvi.llm import ModelSpec
from shotvi.segment.text import norm

from .clip import Candidate, code_checks, kept_words, metrics
from .context import START_SKIPPABLE
from .prompts import load_prompt
from .window import build_window

_OPT = {"type": "object", "properties": {
    "first": {"type": "integer", "description": "-1 if you have no such line"}, "last": {"type": "integer"},
    "strike_until": {"type": "integer", "description": "cold_open only: last word of the weak opener, or -1"},
    "reason": {"type": "string"}}, "required": ["first", "last", "strike_until", "reason"]}
SCHEMA = {  # hook v3: both options; code builds each, join-checks and re-scores them, and keeps the best
    "type": "object",
    "properties": {"cold_open": _OPT, "earlier_start": _OPT},
    "required": ["cold_open", "earlier_start"],
}

PRE_S = 45.0
MAX_COLD_S = 12.0


def _grow_to_sentence(ctx, a: int, b: int, lo: int, hi: int) -> tuple[int, int]:
    """Extend [a, b] to natural boundaries within [lo, hi] (a few words at most)."""
    for _ in range(12):
        if a <= lo or ctx.start_ok(a):
            break
        a -= 1
    for _ in range(12):
        if b >= hi or ctx.end_ok(b):
            break
        b += 1
    return a, b


def _subtract(pieces, a, b):
    out = []
    for p0, p1 in pieces:
        if p1 < a or p0 > b:
            out.append([p0, p1])
            continue
        if p0 < a:
            out.append([p0, a - 1])
        if p1 > b:
            out.append([b + 1, p1])
    return out


def fix_openers(ctx, cfg, llm, cand: Candidate) -> tuple[list[Candidate], dict]:
    """One picker call; returns (candidates for each valid option, trace)."""
    j = cand.judge or {}
    P = cand.pieces
    if metrics(ctx, P)["reordered"]:
        return [], {"skipped": "already reordered"}
    ws = ctx.words
    s0 = P[0][0]
    pre_i = ctx.time_idx(max(ws[s0].start - PRE_S, 0.0))
    while pre_i > 0 and not ctx.start_ok(pre_i):
        pre_i -= 1
    before = build_window(ctx, [(pre_i, s0 - 1)]) if pre_i < s0 else None
    off = len(before.globals) if before else 0
    clip = build_window(ctx, [tuple(p) for p in P], offset=off)  # one index space for before + clip
    p = load_prompt("hook")
    spec = ModelSpec.from_dict(cfg["models"].get("opener") or cfg["models"]["scorer"])
    prompt = p.render(problem=f"{j.get('opener_problem')}: the clip opens \"{j.get('first_words', '')}\"",
                      unresolved="; ".join(j.get("unresolved") or []) or "none",
                      before=before.text if before else "(nothing: the clip starts at the beginning of the talk)",
                      clip=clip.text)
    obj = llm.generate_json("opener", spec, p.system, prompt, SCHEMA, meta={"video": ctx.video_id, "cand": cand.id})
    trace = {"picker": obj, "options": {}}
    glob = (before.globals if before else []) + clip.globals
    out = []
    for mode in ("cold_open", "earlier_start"):
        opt = obj.get(mode) or {}
        if int(opt.get("first", -1)) < 0:
            continue
        new, tr = _build(ctx, cand, {**opt, "mode": mode}, glob, off, pre_i)
        trace["options"][mode] = tr
        if new is not None:
            out.append(new)
    return out, trace


def _build(ctx, cand, obj, glob, off, pre_i):
    ws = ctx.words
    P = cand.pieces
    s0 = P[0][0]
    mode = obj["mode"]
    trace = {}
    a_l, b_l = sorted((int(obj["first"]), int(obj["last"])))
    if not (0 <= a_l <= b_l < len(glob)):
        trace["error"] = "indices out of range"
        return None, trace
    a, b = glob[a_l], glob[b_l]
    if mode == "cold_open":
        if a_l < off or a <= P[0][0]:
            trace["error"] = "cold open not inside the clip after its start"
            return None, trace
        piece = next((q for q in P if q[0] <= a <= q[1]), None)
        lo, hi = (piece[0], piece[1]) if piece else (a, b)
        a, b = _grow_to_sentence(ctx, a, b, max(lo - 6, 0), hi + 6)
        k = 0
        while a < b and k < 3 and norm(ws[a].text) in START_SKIPPABLE:  # "and ...", "so ..." (T4 lead-in <= 4)
            a += 1
            k += 1
        if ws[b].end - ws[a].start > MAX_COLD_S:
            trace["error"] = f"cold open too long ({ws[b].end - ws[a].start:.1f}s)"
            return None, trace
        body = _subtract(P, a, b)
        su = int(obj.get("strike_until", -1))
        if 0 <= su < len(glob) and su >= off:
            g_su = glob[su]
            b0 = body[0][0] if body else None
            # strike only a short opening stretch of the first body piece (<= 25 words, <= 12 s)
            if b0 is not None and body[0][0] <= g_su <= body[0][1] and g_su - b0 <= 25                     and ws[g_su].end - ws[b0].start <= 12.0 and g_su < a:
                body = _subtract(body, b0, g_su)
                trace["struck_opener"] = [b0, g_su]
        new_pieces = [[a, b]] + body
        note = {"type": "cold_open", "after_piece": 0, "reason": _one(obj.get("reason") or "Strongest line first.")}
    else:
        if a_l >= off:
            trace["error"] = "earlier_start line is inside the clip"
            return None, trace
        a, b = _grow_to_sentence(ctx, a, min(b, s0 - 1), pre_i, s0 - 1)
        new_pieces = [[a, b]] + [list(q) for q in P]
        if b + 1 >= P[0][0]:
            new_pieces = [[a, P[0][1]]] + [list(q) for q in P[1:]]
        note = None
    new = Candidate(cand.id, cand.moment, new_pieces, info={**cand.info, "opener_fix": mode,
                                                            "opener_reason": obj.get("reason")})
    if mode == "cold_open":
        # tell the join check which opening the cold open replaces, so it strikes whatever is left of it
        new.info["weak_opener"] = {"first_words": (cand.judge or {}).get("first_words", ""),
                                   "start_word": P[0][0]}
    new.edit_notes = [n for n in cand.edit_notes if n["type"] == "length_over_soft_max"]
    m = metrics(ctx, new_pieces)
    # rebuild jump notes: keep the old skip reasons by position, add the cold-open note
    old_skips = [n for n in cand.edit_notes if n["type"] == "skip"]
    k_skip = 0
    for jmp in m["jumps"]:
        if jmp["kind"] == "backward":
            new.edit_notes.append({**(note or {"type": "reorder", "reason": "Line moved."}),
                                   "after_piece": jmp["after_piece"]})
        else:
            reason = old_skips[k_skip]["reason"] if k_skip < len(old_skips) else \
                f"Skipped {jmp['dist']:.0f}s of source between two parts of the same moment."
            k_skip += 1
            new.edit_notes.append({"type": "skip", "after_piece": jmp["after_piece"], "reason": reason})
    code_checks(ctx, new)
    trace["new_pieces"] = new_pieces
    trace["reject"] = new.reject
    if any(i in ctx.excluded for i in kept_words(new_pieces)):
        return None, trace
    return new, trace


def _one(s, n=200):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"
