"""Join checker: read across every cut of a clip (start, inner cuts, end) and repair bad joins at word level.

One LLM call per clip checks all joins (prompt ``joins``). Repairs name exact word indices ("move_left_end",
"move_right_start") or structural actions ("restore_all", "end_here", "start_here"). Code validates each repair,
applies it, drops teaser/censored words, rebuilds edit notes, re-runs the spec checks, and (if anything changed)
checks the joins once more; second-round repairs are applied without a third check.
"""
from __future__ import annotations

from shotvi.llm import ModelSpec

from .clip import Candidate, code_checks, metrics
from .prompts import load_prompt

CTX_WORDS = 8
MAX_CUT_SHOWN = 20
MAX_MOVE = 40          # words an edge may move in one repair

PROBLEMS = ["natural", "thought_incomplete", "dangling_reference", "orphan_answer", "question_without_answer",
            "topic_shift", "weak_opener_left", "meaning_changed", "other"]
ACTIONS = ["move_left_end", "move_right_start", "restore_all", "end_here", "start_here"]
SCHEMA = {
    "type": "object",
    "properties": {"fixes": {"type": "array", "items": {
        "type": "object",
        "properties": {"join": {"type": "string", "description": "START, END, or Jn"},
                       "problem": {"type": "string", "enum": PROBLEMS},
                       "note": {"type": "string"},
                       "action": {"type": "string", "enum": ACTIONS},
                       "to": {"type": "integer", "description": "word index for move_* actions; -1 otherwise"}},
        "required": ["join", "problem", "note", "action", "to"]}}},
    "required": ["fixes"],
}


def _w(ctx, idx, spk=None):
    out = []
    for i in idx:
        w = ctx.words[i]
        if w.speaker != spk:
            out.append(f"{ctx.label(i)}:")
            spk = w.speaker
        out.append(f"{i} {w.text}")
    return " ".join(out)


def render(ctx, P, weak_opener: dict | None = None) -> tuple[str, str, list[str]]:
    kept = [(k, i) for k, (a, b) in enumerate(P) for i in range(a, b + 1)]
    ws = ctx.words
    # clip text with join markers
    parts, spk = [], None
    for k, (a, b) in enumerate(P):
        seg = []
        for i in range(a, b + 1):
            if ws[i].speaker != spk:
                seg.append(f"{ctx.label(i)}:")
                spk = ws[i].speaker
            seg.append(ws[i].text)
        parts.append(" ".join(seg))
        if k < len(P) - 1:
            parts.append(f"⟨J{k + 1}⟩")
    clip = " ".join(parts)
    flat = [i for _, i in kept]
    lines, ids = [], []
    s0 = P[0][0]
    # show the whole previous speaker turn (up to 30 words) so an orphan answer is visible
    b = s0 - CTX_WORDS
    seen_other = False
    k = s0 - 1
    while k >= 0 and s0 - k <= 30:
        if ws[k].speaker != ws[s0].speaker and k not in ctx.bc_kind:
            seen_other = True
        elif seen_other:
            break
        k -= 1
    b = min(b, k + 1)
    lines.append(f"START: before (not heard): {_w(ctx, range(max(0, b), s0)) or '(episode start)'} "
                 f"| RIGHT: {_w(ctx, flat[:CTX_WORDS])}{reply_hint(ctx, s0)}")
    ids.append("START")
    pos = 0
    for k in range(len(P) - 1):
        pos += P[k][1] - P[k][0] + 1
        left = flat[max(0, pos - CTX_WORDS):pos]
        right = flat[pos:pos + CTX_WORDS]
        a1, b0 = P[k][1], P[k + 1][0]
        if b0 > a1:
            gap = list(range(a1 + 1, b0))
            dur = ws[b0].start - ws[a1].end
            if not gap:
                cut = f"(pause only, {dur:.1f}s)"
            elif len(gap) <= MAX_CUT_SHOWN:
                cut = _w(ctx, gap)
            else:
                cut = (f"{_w(ctx, gap[:8])} ... ({len(gap) - 16} more words, {dur:.0f}s) ... "
                       f"{_w(ctx, gap[-8:])}")
        else:
            moved = "cold open plays first" if k == 0 else "a moved line"
            cut = f"(jump back in the source: {moved}; words after {a1}: {_w(ctx, range(a1 + 1, a1 + 6))} ... " \
                  f"words before {b0}: {_w(ctx, range(max(0, b0 - 6), b0))})"
        note = ""
        if weak_opener and k == 0 and b0 <= a1:
            wo = weak_opener.get("start_word")
            note = (f"  NOTE (code): a cold open was added because the story's original opening "
                    f"(starting at word {wo}: \"{weak_opener.get('first_words', '')}\") was a weak hook. If RIGHT "
                    f"still plays that weak opening (or a question that only led into it), strike it with "
                    f"move_right_start to the first word that carries the story; keep only what the story needs.")
        if note:
            right = flat[pos:pos + 24]
        lines.append(f"J{k + 1}: LEFT: {_w(ctx, left)} | CUT: {cut} | RIGHT: {_w(ctx, right)}{note}")
        ids.append(f"J{k + 1}")
    e = P[-1][1]
    lines.append(f"END: LEFT: {_w(ctx, flat[-CTX_WORDS:])} | after (not heard): "
                 f"{_w(ctx, range(e + 1, min(ctx.n, e + 1 + CTX_WORDS))) or '(episode end)'}")
    ids.append("END")
    return clip, "\n".join(lines), ids


def reply_hint(ctx, s0: int) -> str:
    """Code hint: the clip starts a few words into a reply to the other speaker's turn (likely orphan answer)."""
    ws = ctx.words
    if ctx.seg.host is not None and ws[s0].speaker == ctx.seg.host:
        return ""  # the host opening on his own line is a question or setup, not an orphan answer
    k, own = s0 - 1, 0
    while k >= 0 and (ws[k].speaker == ws[s0].speaker or k in ctx.bc_kind):
        if k not in ctx.bc_kind:
            own += 1
        k -= 1
        if own > 12:
            return ""
    if k < 0:
        return ""
    other = 0
    j = k
    while j >= 0 and (ws[j].speaker == ws[k].speaker or j in ctx.bc_kind) and other < 5:
        if j not in ctx.bc_kind:
            other += 1
        j -= 1
    if other < 4 or ws[s0].start - ws[k].end > 8.0:
        return ""
    return (f"  NOTE (code): the clip starts {own} words into {ctx.label(s0)}'s reply to {ctx.label(k)}'s line "
            f"just before; unless the first heard sentence stands on its own for a stranger, this is an orphan_answer.")


def apply_fixes(ctx, P, fixes) -> tuple[list[list[int]], list[dict]]:
    """Apply validated repairs; returns (new pieces, applied fixes)."""
    P = [list(p) for p in P]
    n = len(P)
    applied = []

    def jnum(f):
        j = str(f.get("join", "")).upper()
        if j == "START":
            return -1
        if j == "END":
            return n - 1
        try:
            return int(j.lstrip("J")) - 1   # join between piece k and k+1
        except ValueError:
            return None

    fx = [(jnum(f), f) for f in fixes]
    fx = [(k, f) for k, f in fx if k is not None and -1 <= k <= n - 1]
    # structural first: earliest end_here, latest start_here
    ends = [k for k, f in fx if f["action"] == "end_here" and 0 <= k < n - 1]
    starts = [k for k, f in fx if f["action"] == "start_here" and 0 <= k < n - 1]
    lo, hi = 0, n - 1
    if ends:
        hi = min(ends)
        applied += [f for k, f in fx if f["action"] == "end_here" and k == hi]
    if starts and max(starts) < hi:
        lo = max(starts) + 1
        applied += [f for k, f in fx if f["action"] == "start_here" and k == lo - 1]
    for k, f in sorted(fx, key=lambda t: -t[0]):   # right to left keeps indices stable
        act, to = f["action"], int(f.get("to", -1))
        if act in ("end_here", "start_here"):
            continue
        if act == "restore_all":
            if lo <= k < hi and P[k] is not None and P[k + 1] is not None and P[k + 1][0] > P[k][1] and ctx.words[P[k + 1][0]].start - ctx.words[P[k][1]].end < 30:
                P[k][1] = P[k + 1][0] - 1
                applied.append(f)
            continue
        if act == "move_left_end":
            if k < lo or k > hi or P[k] is None or (k < hi and P[k + 1] is None):
                continue
            a, b = P[k]
            limit = P[k + 1][0] - 1 if (k < hi and P[k + 1][0] > b) else (ctx.n - 1 if k == hi else b)
            if a <= to <= limit and abs(to - b) <= MAX_MOVE and to != b:
                P[k][1] = to
                applied.append(f)
        elif act == "move_right_start":
            r = k + 1
            if r < lo or r > hi:
                continue
            if P[r] is None:
                continue
            a, b = P[r]
            limit = P[k][1] + 1 if (k >= lo and a > P[k][1]) else (0 if k == -1 or r == lo else a)
            if limit <= to <= b and abs(to - a) <= MAX_MOVE and to != a:
                P[r][0] = to
                applied.append(f)
            elif to > b and to - a <= MAX_MOVE:
                # strike whole following pieces (forward in the source) up to the target word
                rr = r
                while rr < hi and P[rr + 1] is not None and P[rr][1] < to and P[rr + 1][0] > P[rr][1]:
                    rr += 1
                if rr > r and P[rr] is not None and to <= P[rr][1]:
                    for x in range(r, rr):
                        P[x] = None
                    P[rr][0] = max(to, P[rr][0])
                    applied.append(f)
    P = [p for p in P[lo:hi + 1] if p is not None]
    # merge forward-touching pieces
    out = []
    for p in P:
        if out and out[-1][0] <= p[0] <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], p[1])
        else:
            out.append(p)
    # drop teaser and censored words
    ws = ctx.words
    clean = []
    for a, b in out:
        cur = None
        for i in range(a, b + 1):
            bad = i in ctx.excluded or ctx.overlaps(ctx.censored, ws[i].start, ws[i].end)
            if not bad:
                cur = [i, i] if cur is None else [cur[0], i]
            elif cur:
                clean.append(cur)
                cur = None
        if cur:
            clean.append(cur)
    return clean, applied


def rebuild_notes(ctx, cfg, old_notes, pieces) -> list[dict]:
    m = metrics(ctx, pieces)
    back = [x for x in old_notes if x["type"] in ("cold_open", "reorder")]
    skips = [x for x in old_notes if x["type"] == "skip"]
    notes, kb, ks = [], 0, 0
    for j in m["jumps"]:
        if j["kind"] == "backward":
            base = back[kb] if kb < len(back) else {"type": "reorder", "reason": "Line moved."}
            kb += 1
            notes.append({"type": base["type"], "after_piece": j["after_piece"], "reason": base["reason"]})
        else:
            reason = skips[ks]["reason"] if ks < len(skips) else \
                f"Skipped {j['dist']:.0f}s of source between two parts of the same moment."
            ks += 1
            notes.append({"type": "skip", "after_piece": j["after_piece"], "reason": reason})
    if m["edited"] > cfg["guardrails"]["soft_max_s"]:
        old = next((x for x in old_notes if x["type"] == "length_over_soft_max"), None)
        notes.append(old or {"type": "length_over_soft_max",
                             "reason": "The story needs its full setup and payoff; no further cut keeps both."})
    return notes


def check_once(ctx, cfg, llm, cand: Candidate, round_: int) -> tuple[Candidate, dict]:
    p = load_prompt("joins")
    spec = ModelSpec.from_dict(cfg["models"].get("joiner") or cfg["models"]["scorer"])
    clip, joins, ids = render(ctx, cand.pieces, cand.info.get("weak_opener"))
    obj = llm.generate_json("joiner", spec, p.system, p.render(clip=clip, joins=joins), SCHEMA,
                            meta={"video": ctx.video_id, "cand": cand.id, "round": round_})
    fixes = [f for f in obj.get("fixes", []) if f.get("action") in ACTIONS]
    tr = {"n_joins": len(ids), "flagged": fixes}
    if not fixes:
        tr["applied"] = []
        return cand, tr
    new_p, applied = apply_fixes(ctx, cand.pieces, fixes)
    tr["applied"] = applied
    if not applied or not new_p or new_p == cand.pieces:
        return cand, tr
    new = Candidate(cand.id, cand.moment, new_p, info=dict(cand.info))
    new.edit_notes = rebuild_notes(ctx, cfg, cand.edit_notes, new_p)
    code_checks(ctx, new)
    tr["new_pieces"] = new_p
    tr["reject"] = new.reject
    return new, tr


def over_soft_budget(cfg, llm, frac: float | None = None) -> bool:
    """True once the run has billed more than ``frac`` (default soft_budget_frac) of its cap: optional steps are
    then skipped."""
    cap = cfg.get("budget_usd")
    frac = frac if frac is not None else cfg.get("soft_budget_frac")
    return bool(cap and frac and llm.ledger.billed_usd() >= frac * cap)


def check_joins(ctx, cfg, llm, cand: Candidate) -> tuple[Candidate, dict]:
    """Check + repair, then re-check once if anything changed. Never returns a candidate that breaks a hard rule
    the original passed."""
    if not cand.pieces or cand.reject:
        return cand, {"skipped": True}
    c1, t1 = check_once(ctx, cfg, llm, cand, 1)
    trace = {"round1": t1}
    if c1 is cand:
        return cand, trace
    if c1.reject:
        trace["reverted"] = "round-1 repair broke a hard rule"
        return cand, trace
    if over_soft_budget(cfg, llm):
        trace["recheck_skipped"] = "soft budget"
        c1.info["join_repairs"] = len(t1["applied"])
        return c1, trace
    c2, t2 = check_once(ctx, cfg, llm, c1, 2)
    trace["round2"] = t2
    if c2 is not c1 and c2.reject:
        trace["reverted_round2"] = True
        c2 = c1
    c2.info["join_repairs"] = len(t1["applied"]) + (len(t2["applied"]) if c2 is not c1 else 0)
    return c2, trace
