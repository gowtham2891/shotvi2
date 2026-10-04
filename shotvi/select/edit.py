"""Stages 2-3: region shaping and tightening into an ordered word-range keep list.

One LLM call per moment picks the words (in a word-indexed source window); code then cleans the keep list
(excluded/teaser words, pure acknowledgements, censor beeps, dead air), snaps the clip's outer edges to natural
boundaries, assembles playback order (optional cold open / moved conclusion), and runs the spec's code checks.
If a repairable hard rule fails (length, blocks, reorder size, span), the tightener is re-asked once with the problem.
"""
from __future__ import annotations

import json

from shotvi.llm import ModelSpec
from shotvi.segment.render import fmt_ts

from .clip import Candidate, code_checks, metrics
from .prompts import load_prompt
from .window import Window, build_stream

RANGE = {"type": "object", "properties": {"first": {"type": "integer"}, "last": {"type": "integer"}},
         "required": ["first", "last"]}

SCHEMA = {  # tighten v8: region + struck word ranges (flow editing)
    "type": "object",
    "properties": {
        "plan": {"type": "string", "description": "<= 60 words: hook, payoff, needed setup/referents."},
        "verdict": {"type": "string", "enum": ["clip", "reject"]},
        "reject_reason": {"type": "string"},
        "point": {"type": "string", "description": "One English sentence: what this clip is."},
        "region": RANGE,
        "cuts": {"type": "array", "items": {
            "type": "object", "properties": {"first": {"type": "integer"}, "last": {"type": "integer"},
                                             "why": {"type": "string"}}, "required": ["first", "last", "why"]}},
        "cold_open": {"type": "object", "properties": {
            "first": {"type": "integer"}, "last": {"type": "integer"}, "replay": {"type": "boolean"},
            "reason": {"type": "string"}}, "required": ["first", "last", "replay", "reason"]},
        "end_line": {"type": "object", "properties": {
            "first": {"type": "integer"}, "last": {"type": "integer"}, "reason": {"type": "string"}},
            "required": ["first", "last", "reason"]},
        "length_reason": {"type": "string"},
    },
    "required": ["plan", "verdict", "point", "region", "cuts"],
}


def to_keep(obj: dict, n_local: int) -> dict:
    """tighten v8 output (region minus cuts) -> keep ranges + skip notes in local indices (the form assemble uses)."""
    out = dict(obj)
    r = obj.get("region") or {}
    try:
        r0, r1 = sorted((int(r["first"]), int(r["last"])))
    except (KeyError, TypeError, ValueError):
        out["keep"] = []
        return out
    r0, r1 = max(r0, 0), min(r1, n_local - 1)
    struck = [False] * n_local
    whys = []
    for c in obj.get("cuts") or []:
        a, b = sorted((int(c.get("first", -1)), int(c.get("last", -1))))
        for k in range(max(a, r0), min(b, r1) + 1):
            struck[k] = True
        whys.append((a, b, str(c.get("why", ""))))
    keep, cur = [], None
    for k in range(r0, r1 + 1):
        if not struck[k]:
            if cur is None:
                cur = [k, k]
            else:
                cur[1] = k
        elif cur is not None:
            keep.append(cur)
            cur = None
    if cur is not None:
        keep.append(cur)
    out["keep"] = [{"first": a, "last": b} for a, b in keep]
    notes = []
    for i, (ka, kb) in enumerate(zip(keep, keep[1:])):
        gap = (ka[1] + 1, kb[0] - 1)
        why = [w for a, b, w in whys if a <= gap[1] and b >= gap[0]]
        if why:
            notes.append({"after_keep": i, "reason": "; ".join(dict.fromkeys(why))})
    out["skip_notes"] = notes
    out["dropped"] = "; ".join(dict.fromkeys(w for _, _, w in whys))[:300]
    return out


REPAIRABLE = ("length:", "S1:", "O1:", "O2:", "O4:", "S5:", "R2:", "empty")


# ------------------------------------------------------------------ window
def moment_blocks(ctx, cfg, m) -> list[tuple[int, int]]:
    e = cfg["edit"]
    units = ctx.seg.units
    u0, u1 = units[m["start_unit"]], units[m["end_unit"]]
    t0 = u0.start - e["pre_context_s"]
    t1 = u1.end + e["post_context_s"]
    over = (t1 - t0) - e["max_window_s"]
    if over > 0:  # trim context first (keep more before: setups come first), then the nominated span's tail
        cut_pre = min(over * 0.4, e["pre_context_s"])
        t0 += cut_pre
        t1 -= over - cut_pre
    i0, i1 = ctx.time_idx(max(t0, 0.0)), ctx.time_idx(t1)
    while i0 > 0 and not ctx.start_ok(i0):  # begin on a line start
        i0 -= 1
    while i1 < ctx.n - 1 and not ctx.end_ok(i1):
        i1 += 1
    blocks = [(i0, i1)]
    su = m.get("setup_unit", -1)
    if su is not None and su >= 0:
        us = units[su]
        s0 = us.i0
        s1 = min(us.i1, ctx.time_idx(us.start + e["max_setup_window_s"]))
        while s1 < ctx.n - 1 and not ctx.end_ok(s1):
            s1 += 1
        if s1 < i0 - 1:
            gap = ctx.words[i0].start - ctx.words[s1].end
            if gap <= 30.0:
                blocks = [(s0, i1)]
            else:
                blocks = [(s0, s1), (i0, i1)]
        elif s0 < i0:
            blocks = [(s0, i1)]
    return blocks


# ---------------------------------------------------------------- cleanup
def _runs(sorted_idx: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for i in sorted_idx:
        if out and i == out[-1][1] + 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    return out


def clean_ranges(ctx, cfg, words: set[int]) -> tuple[list[list[int]], list[str]]:
    """Kept-word set -> source-order ranges after removing excluded / ack / censored words and dead air."""
    ws = ctx.words
    g = cfg["guardrails"]
    notes = []
    drop = set()
    for i in words:
        if i in ctx.excluded:
            drop.add(i)
        elif cfg["edit"]["drop_ack_backchannels"] and ctx.bc_kind.get(i) == "lexicon":
            drop.add(i)
        elif ctx.overlaps(ctx.censored, ws[i].start, ws[i].end):
            drop.add(i)
    if drop:
        notes.append(f"code dropped {len(drop)} words (teaser/ack/censored)")
    runs = _runs(sorted(words - drop))
    out = []
    for a, b in runs:
        cur = a
        for i in range(a, b):
            t0, t1 = ws[i].end, ws[i + 1].start
            split = ctx.overlaps(ctx.censored, t0, t1)
            if (not split and cfg["edit"]["split_dead_air"] and t1 - t0 > g["dead_air_s"]
                    and not ctx.overlaps(ctx.laughter, t0, t1)):
                split = True
            if split:
                out.append([cur, i])
                cur = i + 1
        out.append([cur, b])
    return out, notes


def _usable(ctx, i: int) -> bool:
    return 0 <= i < ctx.n and i not in ctx.excluded and ctx.bc_kind.get(i) != "lexicon"


def snap_start(ctx, cfg, s: int, lo: int, hi: int) -> tuple[int, str | None]:
    """Move a clip start to a natural boundary if its lead-in is too long. lo..hi = allowed index range."""
    g = cfg["guardrails"]
    if ctx.lead_in(s) <= g["lead_in_max_words"]:
        return s, None
    ws = ctx.words
    best = None
    for j in range(max(lo, 0), hi + 1):
        if j == s or not ctx.start_ok(j) or not _usable(ctx, j):
            continue
        d = abs(ws[j].start - ws[s].start)
        if d <= g["max_boundary_shift_s"] and (best is None or d < best[0]):
            best = (d, j)
    if best is None:
        return s, None
    j = best[1]
    return j, f"start moved {'back' if j < s else 'forward'} {best[0]:.1f}s to a natural boundary"


def snap_end(ctx, cfg, e: int, lo: int, hi: int) -> tuple[int, str | None]:
    g = cfg["guardrails"]
    if ctx.tail(e) <= g["tail_max_words"]:
        return e, None
    ws = ctx.words
    best = None
    for j in range(max(lo, 0), min(hi, ctx.n - 1) + 1):
        if j == e or not ctx.end_ok(j) or not _usable(ctx, j):
            continue
        d = abs(ws[j].end - ws[e].end)
        if d <= g["max_boundary_shift_s"] and (best is None or d < best[0]):
            best = (d, j)
    if best is None:
        return e, None
    j = best[1]
    return j, f"end moved {'forward' if j > e else 'back'} {best[0]:.1f}s to a natural boundary"


# ---------------------------------------------------------------- assembly
def _local_set(win: Window, r: dict) -> set[int]:
    a, b = int(r["first"]), int(r["last"])
    if a > b:
        a, b = b, a
    a, b = max(a, 0), min(b, len(win.globals) - 1)
    return {win.globals[k] for k in range(a, b + 1)} if a <= b else set()


def assemble(ctx, cfg, win: Window, obj: dict, cand_id: str, moment: dict) -> Candidate:
    info = {k: obj.get(k) for k in ("point", "dropped", "length_reason", "reject_reason")}
    info["repairs"] = []
    body: set[int] = set()
    for r in obj.get("keep") or []:
        body |= _local_set(win, r)
    cold = _local_set(win, obj["cold_open"]) if obj.get("cold_open") else set()
    endl = _local_set(win, obj["end_line"]) if obj.get("end_line") else set()
    win_lo, win_hi = win.globals[0], win.globals[-1]

    # a "cold open" that is not later than the body start is just part of the body (no reorder); same for an end
    # line that is not earlier than the body's end
    if cold and body and min(cold) <= min(body):
        body |= cold
        cold = set()
    if endl and body and max(endl) >= max(body):
        body |= endl
        endl = set()
    replay = bool(obj.get("cold_open", {}).get("replay")) if cold else False
    if cold and not replay:
        body -= cold
    if endl:
        body -= endl
    body -= cold & endl
    ranges, notes = clean_ranges(ctx, cfg, body)
    info["repairs"] += notes
    cold_r = clean_ranges(ctx, cfg, cold)[0] if cold else []
    end_r = clean_ranges(ctx, cfg, endl)[0] if endl else []
    cand = Candidate(cand_id, moment, [], info=info)
    if not ranges and not cold_r:
        cand.reject.append("empty: no words kept")
        return cand

    # snap outer edges: first played word and last played word
    pieces = cold_r + ranges + end_r
    first = pieces[0]
    s_new, note = (snap_start(ctx, cfg, first[0], max(win_lo, first[0] - 40), first[1])
                   if cfg["edit"].get("snap_start", True) else (first[0], None))
    if note:
        first[0] = s_new
        info["repairs"].append(note)
    last = pieces[-1]
    e_new, note = snap_end(ctx, cfg, last[1], last[0], min(win_hi, last[1] + 40))
    if note:
        last[1] = e_new
        info["repairs"].append(note)
    # merge pieces that overlap or touch going forward in source order (never across a reorder): a cut that
    # removes no words is not a cut. Exception: a censor beep between two adjacent words must stay cut (A1).
    merged: list[list[int]] = []
    ws = ctx.words
    for p in pieces:
        if merged and merged[-1][0] <= p[0] <= merged[-1][1] + 1 and not (
                p[0] == merged[-1][1] + 1 and ctx.overlaps(ctx.censored, ws[merged[-1][1]].end, ws[p[0]].start)):
            merged[-1][1] = max(merged[-1][1], p[1])
        else:
            merged.append(list(p))
    cand.pieces = [list(p) for p in merged]

    # edit notes for jumps
    m = metrics(ctx, cand.pieces)
    skip_notes = obj.get("skip_notes") or []
    first_backward = True
    for j in m["jumps"]:
        k = j["after_piece"]
        if j["kind"] == "backward":
            is_cold = bool(cold_r) and first_backward
            first_backward = False
            if is_cold:
                cand.edit_notes.append({"type": "cold_open", "after_piece": k,
                                        "reason": _one_line(obj["cold_open"].get("reason") or "Punchy line lifted to the front as the hook.")})
            else:
                reason = (obj.get("end_line") or {}).get("reason") or (obj.get("cold_open") or {}).get("reason") \
                    or "Conclusion moved to the end so the clip closes on it."
                cand.edit_notes.append({"type": "reorder", "after_piece": k, "reason": _one_line(reason)})
        else:
            reason = _match_skip_reason(ctx, cand.pieces, k, skip_notes, ranges) or \
                f"Skipped {j['dist']:.0f}s of source between two parts of the same moment."
            if j["dist"] > cfg["guardrails"]["long_range_s"]:
                reason = f"Long-range ({j['dist'] / 60:.0f} min): " + reason
            cand.edit_notes.append({"type": "skip", "after_piece": k, "reason": _one_line(reason)})
    if m["edited"] > cfg["guardrails"]["soft_max_s"]:
        cand.edit_notes.append({"type": "length_over_soft_max", "reason": _one_line(
            obj.get("length_reason") or "The story needs its full setup and payoff; no further cut keeps both.")})
    return cand


def _one_line(s: str, n: int = 200) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _match_skip_reason(ctx, pieces, k, skip_notes, ranges):
    """Find the tightener's note for the gap after pieces[k] (its after_keep indexes its own keep list)."""
    if not skip_notes:
        return None
    end_word = pieces[k][1]
    best = None
    for sn in skip_notes:
        ak = sn.get("after_keep", -1)
        # compare by position: the tightener's keep list is roughly our body ranges
        idx = min(max(ak, 0), len(ranges) - 1) if ranges else 0
        d = abs(ranges[idx][1] - end_word) if ranges else 0
        if best is None or d < best[0]:
            best = (d, sn.get("reason"))
    return best[1] if best else None


# ----------------------------------------------------------------- driver
def _feedback(cand: Candidate, m: dict, win: Window, obj: dict) -> str:
    """Repair request after code-checked hard-rule failures."""
    nl = "\n"
    prev = json.dumps({k: obj.get(k) for k in ("region", "cuts", "cold_open", "end_line")}, ensure_ascii=False)
    return nl.join([
        "", "Your previous edit broke hard rules. Previous answer:", prev,
        f"Problems (edited duration was {m.get('edited', 0):.1f}s):",
        nl.join(f"- {r}" for r in cand.reject),
        "Fix them (shorten, lengthen, drop a stitch or a reorder, or reject if the moment cannot work).", ""])


def review_feedback(prev_obj: dict, judge: dict, cand: Candidate) -> str:
    """Revision request built from the scorer's review of the previous cut."""
    probs = []
    for k, label in (("meaning_changed", "a cut changes what the speaker meant"),
                     ("garbled_join", "an inner cut produces a broken join"),
                     ("starts_mid_thought", "the clip starts mid-thought"),
                     ("ends_mid_thought", "the clip ends mid-thought"),
                     ("padded", "the clip is padded and should be tighter")):
        if judge.get(k):
            probs.append(label)
    op, ep = judge.get("opener_problem", "none"), judge.get("ending_problem", "none")
    if op != "none":
        probs.append(f"the opening \"{judge.get('first_words', '')}\" does not work as a hook ({op}). Fix it: start "
                     "earlier at the line that introduces who/what (only if that line is itself interesting), or lift "
                     "this moment's strongest line (punchline, the host's sharp question, a bold claim or number; one "
                     "complete sentence, at most ~10 s) to the front as cold_open and then play the story")
    for u in judge.get("unresolved") or []:
        probs.append(f"unresolved for a stranger: {u}. Keep the line that makes it clear, or cut what depends on it")
    if ep != "none":
        probs.append(f"the ending \"{judge.get('last_words', '')}\" is {ep}. End on the strongest line or its direct "
                     "resolution; cut hedges, asides and restatements after it, or extend to the missing point")
    s = (cand.scores if cand is not None and cand.scores else
         {kk: judge.get(kk) for kk in ("hook", "standalone", "payoff", "value_emotion")})
    nl = "\n"
    lines = nl.join(f"- {p}" for p in probs) or "- (see review)"
    prev = json.dumps({k: prev_obj.get(k) for k in ("region", "cuts", "cold_open", "end_line")}, ensure_ascii=False)
    return nl.join([
        "", "A reviewing editor watched your previous cut and sent it back. Previous answer:", prev,
        f"Review scores: hook {s.get('hook')}, standalone {s.get('standalone')}, payoff {s.get('payoff')}, "
        f"value {s.get('value_emotion')}.",
        f"Review: {judge.get('critique', '')}", "Problems:", lines,
        f"Suggested fix: {judge.get('fix_hint', '')}",
        "Re-cut the clip to fix these problems (you may change the start, the end and the cuts), or reject it "
        "if it cannot work.", ""])


def tighten(ctx, cfg, llm, moment: dict, cand_id: str, feedback: str = "") -> tuple[Candidate, dict]:
    p = load_prompt("tighten")
    spec = ModelSpec.from_dict(cfg["models"]["tightener"])
    win = build_stream(ctx, moment_blocks(ctx, cfg, moment))
    base = dict(point=moment.get("point", ""), mtype=moment.get("type", ""),
                hook_quote=moment.get("hook_quote", ""), payoff_quote=moment.get("payoff_quote", ""),
                risk=moment.get("risk") or "none", episode_len=fmt_ts(ctx.seg.transcript.duration),
                n_words=len(win.globals), last_index=len(win.globals) - 1, window=win.text)
    trace = {"window_blocks": win.blocks, "attempts": []}
    cand = None
    for attempt in range(cfg["edit"]["repair_rounds"] + 1):
        prompt = p.render(**base, feedback=feedback)
        obj = llm.generate_json("tightener", spec, p.system, prompt, SCHEMA,
                                meta={"video": ctx.video_id, "moment": moment.get("id"), "attempt": attempt})
        trace["attempts"].append(obj)
        obj = to_keep(obj, len(win.globals))
        if obj.get("verdict") == "reject" or not obj.get("keep"):
            cand = Candidate(cand_id, moment, [], info={"point": obj.get("point"),
                                                        "reject_reason": obj.get("reject_reason")})
            cand.reject.append("tightener: " + _one_line(obj.get("reject_reason") or "no clip in this moment"))
            return cand, trace
        cand = assemble(ctx, cfg, win, obj, cand_id, moment)
        if cand.pieces:
            m = code_checks(ctx, cand)
        else:
            m = {}
        if not any(r.startswith(REPAIRABLE) for r in cand.reject):
            break
        if attempt < cfg["edit"]["repair_rounds"]:
            feedback = _feedback(cand, m, win, obj)
    return cand, trace
