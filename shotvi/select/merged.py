"""Merged web-series pipeline (founder, 2026-10-15): v1 (``shotvi.select.webseries``) + the ws3 payoff list.

Why: the founder rated v1's clips "6-7 of 10 good" and ws3's (payoff -> hook -> clip) "not good at all": ws3 cut
fragments (median ~31-33 s vs v1 42-66 s; founder-yes median 49 s) because its call 2 walked back only "as far as the
payoff needs". v1's single discover call sees a whole moment and picks start and end together, but nominates far
fewer beats than ws3's call 1 lists (Queen 7 vs 11, Krishna Leelalu 12 vs 48). The merge keeps v1 as is and adds a
v1-style clip for every listed payoff v1 missed. Additive: webseries.py and ws3.py behaviour and defaults are
untouched (this module only imports them).

Per episode:
  A  ws3 call 1 (``ws3_payoffs``) exactly as ws3 runs it: same prompts (ws3_context + ws3_payoffs), same model and
     thinking, same explicit context cache (persona + whole transcript, ``ws3.ContextCache``), same validation
     (``ws3.validate_payoffs``: ids checked, audio events mapped, intensity >= 3) but NO list cap (matching is free;
     the cap applies to call C below). Cached ws3 answers replay at Rs 0.
  B  v1 exactly as the v1 default config runs it (``ws.run_episode`` with ``ws.DEFAULTS``: discover v1 payoff-first,
     cut builder, review, candidates rule) -> ``<out>/v1/<id>.json`` (the unchanged v1 document). Cached v1 answers
     (runs/v1_test = runs/v1_candidates) replay at Rs 0.
  match (code) a payoff is COVERED by a shown v1 clip when the clip PLAYS the payoff line (>= half of its words in
     the clip's pieces; a cold-open lift counts at its original place) AND either the line ends in the last
     ``match.tail_frac`` (25%) of the clip's scene playback, or it is within ``match.end_lines`` (3) spoken lines of
     the clip's end (payoff) line. Principle: a clip "has" a beat when it builds to it and lands on it; 25% of a
     40-60 s clip is 10-15 s = the payoff, its reaction hold (2-8 s) and a short follow-up exchange; the 3-line rule
     covers short clips and a payoff answered by 1-3 reaction lines, where 25% is only a few seconds. A payoff
     played early in a v1 clip (not landing there) is NOT covered (``inside_v1`` notes the clip; a new clip around
     it usually overlaps that clip > 50% and is dropped by the dedupe). Covered payoffs are attached to the clip
     (``ws3_payoffs`` list, primary = highest intensity: payoff_emotion / payoff_intensity / payoff_reason /
     lands_on_screen / ws3_payoff_type). v1's own fields are never changed.
  C  one call per MISSED payoff (``merged_moment``, thinking MEDIUM, one paid attempt): ONE whole moment around the
     payoff in v1 discover's style, answered in v1 discover's moment schema (``moment_schema``) so v1's cut builder
     and review consume it unchanged. Sent through the same context cache (only the small task is billed in full).
     The task lists the ``<lift-ok>`` lines near the payoff (the cached transcript carries no lift tags). Missed
     payoffs are called highest intensity first; ``missed.max_calls`` caps the count (None = no cap).
  build v1's cut builder + hard rules; NO start extension for length: a clip under guardrails.min_s is rejected with
     its reason (never padded).
  dedupe a new clip is dropped when its source time overlaps a SHOWN v1 clip by > guardrails.dup_overlap (50% of
     the shorter; v1 wins); among new clips the higher payoff intensity wins. Every drop is reported.
  review one ``ws_review`` call (v1's prompt) for the batch of new clips, then v1's apply_review (cold-open veto,
     bad-cut restore). New clips are never hidden on their review score (only hard-rule failures are): a clip the
     v1 rule would hide (overall <= 2.5 or a part <= 2) is shown last in its section, flagged
     ``v1_rule_would_hide``.
  output ``<out>/<id>.json``: v1's proposals unchanged (plus the attached payoff fields and ``source: "v1"``), then
     the new clips: tier ``extra``, tier_label / badge "beat v1 missed", ``source: "payoff_list"``, with the ws3
     payoff (line, type, emotion, intensity, reason, lands_on_screen) and v1-style title / hook / why; an
     ``episode_summary`` (payoffs listed, covered, missed, calls, built, dropped, shown) and the per-payoff table.

Opt-in ``review_context`` (founder, 2026-10-15; default OFF so the v1 part replays free and stays identical): the
review call (v1 clips and new clips) is sent through the same explicit context cache as A and C (persona + whole
transcript) with only its task (the review prompt ``ws_review_ctx`` = ``ws_review`` + one sentence saying the whole
transcript is reference, followed by the clips as played) and cachedContent; the review is then a cache miss (a
paid call per episode for v1's review and for the new clips' review).

CLI::

    python -m shotvi.select.merged 5HqkHAZfzos qIdAPG8UulE --out runs/merged --dry-run      # free, no call
    SHOTVI_SPEND_SOURCE=merged python -m shotvi.select.merged 5HqkHAZfzos qIdAPG8UulE --out runs/merged \
        --max-ledger-inr 1735
    python tools/preview_clips.py runs/merged --top 80 --reuse runs/v1_candidates
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

from shotvi.llm.base import BudgetExceeded, LLMError, SafetyBlocked
from shotvi.select import webseries as ws
from shotvi.select import ws3

ROOT = ws.ROOT
PIPELINE_VERSION = "merged/0.1"
ROLE_C = "merged_moment"
REVIEW_CTX_PROMPT = "ws_review_ctx"
EXTRA_TIER = "extra"
EXTRA_LABEL = "beat v1 missed"
EXIT_SAFETY_BLOCKED = 4

_FLASH = {"provider": "gemini", "model": "gemini-3.8-flash"}
DEFAULTS: dict = {
    "v1": {},                                   # deep-merged over ws.DEFAULTS: empty = the v1 default config
    "ws3": {"payoffs": {"max_payoffs": 10 ** 6}},  # deep-merged over ws3.DEFAULTS: call A as ws3 runs it, no list cap
    "models": {ROLE_C: {**_FLASH, "params": {"thinking_level": "medium", "max_output_tokens": 8192}}},
    "prompts": {ROLE_C: "merged_moment"},
    "match": {"tail_frac": 0.25, "end_lines": 3, "played_min_frac": 0.5},
    # call C: highest intensity first; max_calls None = every missed payoff. The lift-ok list in the task covers
    # lines from lift_before_s before the payoff to lift_after_s after it.
    "missed": {"max_calls": 20, "lift_before_s": 150.0, "lift_after_s": 20.0},
    "moment": {"max_end_after_payoff_lines": 3},
    "review_context": False,
    "budget_usd": 2.0,                          # per-episode USD cap of the merged part (A + C + new review); the
                                                # rupee guard (--max-ledger-inr) is the real limit
    "calibrate_from": {"ws3": "runs/ws3"},      # measured cached-context tokens per episode (projection only)
}

# ---------------------------------------------------------------------------------------------------- projection
# Expected billed output (visible + thinking) of one call C at MEDIUM thinking: no call C has run. Basis: ws3_hook
# (medium, one payoff, chooses lines) measured 1.6k per call (Queen 17.9k / 11, Krishna 31.3k / 20); call C writes a
# whole v1 moment (v1 discover: ~300-350 visible tokens per moment) and weighs start, end, drops and a lift, so ~2x.
C_OUT_EXPECTED = 3000
# review of new clips, expected: v1_test measured 4.5k in / 4.2k out (Queen) and 5.7k in / 9.3k out (Krishna, 11
# clips) -> ~1200 + 450 per clip in, ~1000 + 800 per clip out (the guard uses ws.PROJ_REVIEW / REVIEW_IN_PER_CLIP)
REVIEW_IN_EXP = (1200, 450)
REVIEW_OUT_EXP = (1000, 800)
REVIEW_TIER_ORDER = {"recommended": 0, "worth_a_look": 1, "unchecked": 2, "unreviewed": 3, "clear_failure": 4}
REVIEW_TIER_LABEL = {"recommended": "strong candidate", "worth_a_look": "candidate", "unchecked": "unchecked",
                     "unreviewed": "no review", "clear_failure": "clear failure by the v1 rule"}


class EpisodeStop(RuntimeError):
    """The episode cannot continue (call A or v1 discover failed / was blocked)."""


# ---------------------------------------------------------------------------------------------------- config
def cfg_a(cfg: dict) -> dict:
    return ws.deep_merge(ws3.DEFAULTS, cfg.get("ws3") or {})


def cfg_b(cfg: dict) -> dict:
    v = ws.deep_merge(ws.DEFAULTS, cfg.get("v1") or {})
    if cfg.get("review_context"):
        v["prompts"]["ws_review"] = REVIEW_CTX_PROMPT
    return v


def spec_c(cfg: dict):
    from shotvi.llm.base import ModelSpec
    return ModelSpec.from_dict(cfg["models"][ROLE_C])


def moment_schema(vcfg: dict) -> dict:
    """One moment object of v1 discover's answer schema (the configured discover prompt's, payoff_line /
    payoff_type included), required fields as v1's: the cut builder and the review consume it unchanged."""
    return copy.deepcopy(ws.discover_schema(vcfg)["properties"]["moments"]["items"])


# ---------------------------------------------------------------------------------------------------- matching
def _spoken_index(ep: ws.Episode) -> dict:
    return {r["id"]: k for k, r in enumerate([r for r in ep.lines if ep.is_speech(r)])}


def _played(c: dict) -> set:
    return {i for p in c["pieces"] for i in range(p["start_word"], p["end_word"] + 1)}


def _end_spoken(ep: ws.Episode, lid: str):
    """The spoken line a clip ends on (an audio-event end line maps to the spoken line before it)."""
    r = ep.by_id.get(lid)
    if r is None:
        return None
    return r if ep.is_speech(r) else ws3.prev_speech(ep, ep.pos[lid])


def payoff_position(ep: ws.Episode, line: dict, c: dict, sp_idx: dict, mc: dict) -> dict | None:
    """Where payoff ``line`` sits in clip ``c`` (a proposal doc or a built clip): None when the clip does not play
    it; else {frac: end of the line in the scene playback (0..1, a lifted line at its original place),
    lines_from_end: spoken lines from the clip's end line (negative = before it), in_tail, near_end, covered}."""
    a, b = line["words"]
    played = _played(c)
    n_in = sum(1 for i in range(a, b + 1) if i in played)
    if n_in < mc["played_min_frac"] * (b - a + 1):
        return None
    scene = [s for s in c["time_segments"] if s.get("kind", "scene") == "scene"]
    tot = sum(s["end"] - s["start"] for s in scene)
    t = ep.w1(line)
    off = sum(max(0.0, min(s["end"], t) - s["start"]) for s in scene)
    frac = off / tot if tot > 0 else 1.0
    end = _end_spoken(ep, c["end_line"])
    d = (sp_idx[line["id"]] - sp_idx[end["id"]]) if end is not None and line["id"] in sp_idx else None
    in_tail = frac >= 1.0 - mc["tail_frac"] - 1e-9
    near_end = d is not None and abs(d) <= mc["end_lines"]
    return {"frac": round(frac, 3), "lines_from_end": d, "in_tail": in_tail, "near_end": near_end,
            "covered": in_tail or near_end}


def match_payoffs(ep: ws.Episode, payoffs: list[dict], props: list[dict], mc: dict,
                  hidden: list[dict] | None = None) -> tuple[dict, list[dict]]:
    """(covered {payoff id: {clip, frac, lines_from_end, rule}}, missed [{payoff, inside_v1, in_hidden_v1}]).
    A payoff covered by several clips goes to the one it is nearest the end of (fewest lines from the end, then the
    later position)."""
    sp_idx = _spoken_index(ep)
    covered, missed = {}, []
    for p in payoffs:
        line = ep.by_id[p["line"]]
        hits, inside = [], []
        for c in props:
            pos = payoff_position(ep, line, c, sp_idx, mc)
            if pos is None:
                continue
            rec = {"clip": c["id"], **{k: pos[k] for k in ("frac", "lines_from_end")},
                   "rule": "+".join(r for r, on in (("tail", pos["in_tail"]), ("end_lines", pos["near_end"])) if on)}
            (hits if pos["covered"] else inside).append(rec)
        if hits:
            hits.sort(key=lambda h: (abs(h["lines_from_end"]) if h["lines_from_end"] is not None else 99, -h["frac"]))
            covered[p["id"]] = {**hits[0], **({"also": [h["clip"] for h in hits[1:]]} if hits[1:] else {})}
            continue
        hid = []
        for c in hidden or []:
            if c.get("time_segments") and c.get("pieces") and c.get("end_line"):
                pos = payoff_position(ep, line, c, sp_idx, mc)
                if pos is not None:
                    hid.append({"clip": c["id"], "frac": pos["frac"], "covered_if_shown": pos["covered"],
                                "hidden_because": "; ".join(c.get("reject_reasons") or [])[:200]})
        missed.append({"payoff": p, "inside_v1": inside, "in_hidden_v1": hid})
    return covered, missed


def attach_payoffs(props: list[dict], payoffs: list[dict], covered: dict) -> None:
    """Add the covered payoffs to their v1 proposals in place (v1's own fields untouched)."""
    by_id = {p["id"]: p for p in payoffs}
    per: dict = {}
    for pid, cov in covered.items():
        per.setdefault(cov["clip"], []).append((by_id[pid], cov))
    for prop in props:
        prop["source"] = "v1"
        items = sorted(per.get(prop["id"], []), key=lambda t: (-t[0]["intensity"], t[0]["order"]))
        prop["ws3_payoffs"] = [{"id": p["id"], "line": p["line"], "text": p["text"], "payoff_type": p["type"],
                                "payoff_emotion": p["emotion"], "payoff_intensity": p["intensity"],
                                "payoff_reason": p["reason"], "lands_on_screen": p["lands_on_screen"],
                                "match": {k: cov[k] for k in ("frac", "lines_from_end", "rule")}}
                               for p, cov in items]
        if items:
            p0 = items[0][0]
            prop.update(payoff_emotion=p0["emotion"], payoff_intensity=p0["intensity"], payoff_reason=p0["reason"],
                        lands_on_screen=p0["lands_on_screen"], ws3_payoff_type=p0["type"])


# ---------------------------------------------------------------------------------------------------- call C
def lift_ok_near(ep: ws.Episode, lift_ids: list[str], p: dict, cfg: dict, window: dict | None = None) -> str:
    """The <lift-ok> lines from ``lift_before_s`` before payoff ``p`` to ``lift_after_s`` after it, quoted for a
    task. ``window``: those two keys (default ``cfg["missed"]``; shotvi.select.emotion_pick passes its own)."""
    t = ep.w0(ep.by_id[p["line"]])
    mc = window if window is not None else cfg["missed"]
    near = [lid for lid in lift_ids
            if t - mc["lift_before_s"] <= ep.w0(ep.by_id[lid]) <= t + mc["lift_after_s"]]
    if not near:
        return "none"
    return "; ".join(f'{lid} ("{ws.one_line(ws3.line_text(ep, lid), 80)}")' for lid in near)


def task_c(cfg: dict, ep: ws.Episode, p: dict, lift_ids: list[str]) -> str:
    from shotvi.select.prompts import load_prompt
    pr = load_prompt(cfg["prompts"][ROLE_C])
    return pr.system.strip() + "\n\n" + pr.render(
        episode_len=ws.fmt(ep.duration), payoff_line=p["line"], payoff_type=p["type"], emotion=p["emotion"],
        intensity=p["intensity"],
        on_screen="the transcript shows the reaction" if p["lands_on_screen"] else "no reaction shown in the transcript",
        payoff_text=ws.one_line(p["text"], 400), payoff_reason=ws.one_line(p["reason"], 400),
        lift_ok=lift_ok_near(ep, lift_ids, p, cfg)).strip()


def ask_cached(llm, cache: ws3.ContextCache, role: str, spec, task: str, schema: dict, meta: dict):
    """One call through the context cache (ws3.ask with an explicit schema): the provider cache is created lazily
    before the first uncached call."""
    if not cache.is_hit(llm, spec, task, schema) and not llm.cache_only:
        cache.ensure(llm)
    rq = cache.request(task)
    return llm.generate_json(role, spec, rq["system"], rq["prompt"], schema, meta=meta,
                             cached_content=rq["cached_content"])


def validate_moment(ep: ws.Episode, p: dict, ans: dict, cfg: dict) -> tuple[dict, list[str]]:
    """Call C answer -> (moment for the v1 cut builder, flags). Raises ValueError when unusable. The moment is the
    model's object; only the ends are checked: start must be at or before the payoff; an end before the payoff, or
    more than ``moment.max_end_after_payoff_lines`` spoken lines after it, is moved to the payoff (flagged)."""
    m = copy.deepcopy(ans)
    flags: list[str] = []
    sid, eid = ws.norm_id(m.get("start_line")), ws.norm_id(m.get("end_line"))
    if sid not in ep.by_id:
        raise ValueError(f"start_line {m.get('start_line')!r} is not a line of the transcript")
    if eid not in ep.by_id:
        raise ValueError(f"end_line {m.get('end_line')!r} is not a line of the transcript")
    pp = ep.pos[p["line"]]
    if ep.pos[sid] > pp:
        raise ValueError(f"start_line {sid} comes after the payoff {p['line']}")
    pe = ep.pos[eid]
    if pe < pp:
        flags.append(f"end_line {eid} came before the payoff {p['line']}; moved to the payoff")
        eid = p["line"]
    else:
        n_after = sum(1 for r in ep.lines[pp + 1:pe + 1] if ep.is_speech(r))
        if n_after > cfg["moment"]["max_end_after_payoff_lines"]:
            flags.append(f"end_line {eid} is {n_after} spoken lines after the payoff; moved to the payoff")
            eid = p["line"]
    m["start_line"], m["end_line"] = sid, eid
    m["cold_open_lines"] = [x for x in (ws.norm_id(v) for v in m.get("cold_open_lines") or []) if x]
    return m, flags


# ---------------------------------------------------------------------------------------------------- review context
class ReviewContextLLM:
    """An LLMClient view that sends every ``ws_review`` call through the episode's context cache: system "" and the
    review's system + user as the task, with cachedContent (explicit) or the cached prefix (implicit fallback).
    ``cache_key`` mirrors the rewritten request, so ``ws.is_cached`` stays exact. Everything else (other roles,
    ledger, before_call, cache_only) is the wrapped client's."""

    _OWN = ("_inner", "_cache", "_cache_llm", "_ctx_tokens")

    def __init__(self, inner, cache: ws3.ContextCache, cache_llm=None, ctx_tokens: int = 0):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_cache", cache)
        object.__setattr__(self, "_cache_llm", cache_llm or inner)
        object.__setattr__(self, "_ctx_tokens", ctx_tokens)

    def __getattr__(self, k):
        return getattr(self._inner, k)

    def __setattr__(self, k, v):
        if k in self._OWN:
            object.__setattr__(self, k, v)
        else:
            setattr(self._inner, k, v)

    @staticmethod
    def task(system: str, prompt: str) -> str:
        return system.strip() + "\n\n" + prompt.strip()

    def _is_review(self, schema) -> bool:
        return schema is ws.REVIEW_SCHEMA or schema == ws.REVIEW_SCHEMA

    def cache_key(self, spec, system, prompt, schema, salt: str = ""):
        if not self._is_review(schema):
            return self._inner.cache_key(spec, system, prompt, schema, salt)
        rq = self._cache.request(self.task(system, prompt))
        ck = f"\n<cached_content {self._cache.key}>" if rq["cached_content"] else ""
        return self._inner.cache_key(spec, rq["system"], rq["prompt"] + ck, schema, salt)

    def count_tokens(self, spec, text):
        # conservative: the cached transcript is part of every review request's input
        return self._inner.count_tokens(spec, text) + self._ctx_tokens

    def generate_json(self, role, spec, system, prompt, schema, **kw):
        if role != "ws_review":
            return self._inner.generate_json(role, spec, system, prompt, schema, **kw)
        task = self.task(system, prompt)
        if not self._cache.is_hit(self._inner, spec, task, schema) and not self._inner.cache_only:
            self._cache.ensure(self._cache_llm)
        rq = self._cache.request(task)
        kw.pop("cached_content", None)
        return self._inner.generate_json(role, spec, rq["system"], rq["prompt"], schema,
                                          cached_content=rq["cached_content"], **kw)


# ---------------------------------------------------------------------------------------------------- new clips
def build_new(ep: ws.Episode, x: dict, vcfg: dict, bpos: int) -> dict:
    """v1's cut builder on a call-C moment; no length extension. Raises ws.Reject (builder) or returns the clip with
    its hard-rule failures in ``x['reject_reasons']`` (L1 = under guardrails.min_s: rejected, never padded)."""
    c = ws.build_clip(ep, x["moment"], vcfg["cut"], barrier_pos=bpos)
    x["clip"] = c
    x["barrier_pos"] = bpos
    return c


def dedupe_new(cands: list[dict], v1props: list[dict], dup: float) -> tuple[list[dict], list[dict]]:
    """Drop a new clip whose source time overlaps a shown v1 clip by > dup (of the shorter; v1 wins); among new clips
    the higher payoff intensity wins (ties: higher call-C strength, then the earlier payoff)."""
    kept, dropped = [], []
    for x in cands:
        ov = sorted(((ws.overlap_frac(x["clip"], v), v["id"]) for v in v1props), reverse=True)
        if ov and ov[0][0] > dup:
            dropped.append({**x, "reject_reasons": [f"D2: overlaps v1 clip {ov[0][1]} ({ov[0][0]:.0%} of the shorter); "
                                                    f"v1 wins"], "dup_of": ov[0][1]})
            continue
        kept.append(x)
    out, d3 = dedupe_among(kept, dup, key=lambda x: (-x["payoff"]["intensity"],
                                                     -int(x["moment"].get("strength") or 0), x["payoff"]["order"]))
    dropped += d3
    out.sort(key=lambda x: x["payoff"]["order"])
    return out, dropped


def dedupe_among(cands: list[dict], dup: float, key, code: str = "D3", what: str = "new clip") -> tuple[list, list]:
    """Greedy dedupe in ``key`` order (best first): a clip whose source time overlaps an already kept one by > dup
    (of the shorter) is dropped with the reason ``<code>: overlaps <what> <id> (..; intensity a vs b)``. Returns
    (kept in key order, dropped). Shared by dedupe_new and shotvi.select.emotion_pick."""
    out, dropped = [], []
    for x in sorted(cands, key=key):
        d = next((y for y in out if ws.overlap_frac(x["clip"], y["clip"]) > dup), None)
        if d:
            dropped.append({**x, "reject_reasons": [
                f"{code}: overlaps {what} {d['id']} ({ws.overlap_frac(x['clip'], d['clip']):.0%} of the shorter; "
                f"intensity {x['payoff']['intensity']} vs {d['payoff']['intensity']})"], "dup_of": d["id"]})
            continue
        out.append(x)
    return out, dropped


def review_cost_usd(spec, in_tok: int, out_tok: int, cached: int = 0) -> float:
    from shotvi.llm.pricing import cost_usd
    usd = cost_usd(spec.model, in_tok + cached, out_tok, cached_tokens=cached)
    return usd if usd is not None else ws.project_usd(in_tok + cached, out_tok)


def review_new(ep: ws.Episode, kept: list[dict], vcfg: dict, llm, guard: ws.Guard, call_proj: dict, vid: str,
               ctx_tokens: int, log=print) -> dict:
    """v1's review stage (3b) for the new clips: one combined ws_review call (split fallback on a safety block),
    then v1's apply_review. Returns debug info."""
    texts = []
    for x in kept:
        texts.append(ws.play_text(ep, x["id"], x["moment"], x["clip"]))
        x["review_saw"] = x["clip"]
    rs, ru = ws.review_prompt(texts, vcfg)
    rspec = ws.spec_of(vcfg, "ws_review")
    rin = ws3.est_tokens(rs + "\n" + ru)
    rout = ws.review_out_guard(rspec.params, len(kept))
    call_proj["ws_review"] = ws.inr(review_cost_usd(rspec, rin, rout, ctx_tokens))
    fb = ws.inr(ws.review_fallback_usd(rspec, len(kept), rin))
    dbg: dict = {"review_input": texts}
    if not ws.is_cached(llm, rspec, rs, ru, ws.REVIEW_SCHEMA):
        guard.check(f"{vid} ws_review of new clips (+ split fallback if blocked)", call_proj["ws_review"] + fb)
    reviews = {}
    try:
        rv = llm.generate_json("ws_review", rspec, rs, ru, ws.REVIEW_SCHEMA, meta={"video": vid, "merged": "new"})
        dbg["review"] = rv
        reviews = {str(r["id"]).strip(): r for r in rv["clips"]}
    except SafetyBlocked as e:
        log(f"{vid}: review of new clips blocked ({e.reason}): reviewing in halves")
        calls: list = []
        reviews, blocked = ws.review_split(llm, rspec, vcfg, [(x["id"], t) for x, t in zip(kept, texts)], e.reason,
                                           vid, guard, call_proj, fb, calls, log)
        dbg["review_fallback"] = {"combined_block": e.reason, "calls": calls, "blocked_clips": blocked}
        for x in kept:
            if x["id"] in blocked:
                x["review_blocked"] = blocked[x["id"]]
    except LLMError as e:
        log(f"{vid}: review of new clips failed ({e}); they are shown without a review")
        dbg["review_error"] = str(e)
    ws.apply_review(ep, kept, reviews, vcfg)
    return dbg


def rank_new(kept: list[dict], vcfg: dict) -> list[dict]:
    """v1's scoring rule on the new clips, but nothing is hidden: a clip v1 would hide (clear failure) or one without
    a review stays, ranked last in the section. Order: review tier, overall, payoff, intensity, transcript order."""
    tmp: list = []
    shown = ws.rank_clips(kept, tmp, vcfg)
    for x in shown:
        x["review_tier"] = x["tier"]
    by_id = {x["id"]: x for x in kept}
    for r in tmp:
        x = by_id[r["id"]]
        if r["reject_reasons"][0].startswith("R0"):
            x.update(review_tier="unreviewed", scores={}, flaws=ws.clip_flaws(x), tier="unchecked",
                     review_blocked="no review returned")
        else:
            x["review_tier"] = "clear_failure"
            x["v1_hide_reason"] = r["reject_reasons"][0]
        shown.append(x)
    shown.sort(key=lambda x: (REVIEW_TIER_ORDER[x["review_tier"]], -(x.get("scores") or {}).get("overall", 0.0),
                              -(x.get("scores") or {}).get("payoff", 0.0), -x["payoff"]["intensity"],
                              x["payoff"]["order"]))
    return shown


def payoff_public(p: dict) -> dict:
    return {"id": p["id"], "line": p["line"], "text": p["text"], "payoff_type": p["type"],
            "payoff_emotion": p["emotion"], "payoff_intensity": p["intensity"], "payoff_reason": p["reason"],
            "lands_on_screen": p["lands_on_screen"]}


def new_proposal_doc(ep: ws.Episode, x: dict, rank: int, vcfg: dict, line_of_word: dict) -> dict:
    d = ws.proposal_doc(ep, x, rank, vcfg, line_of_word)
    p = x["payoff"]
    rt = x["review_tier"]
    d.update(tier=EXTRA_TIER, tier_label=EXTRA_LABEL, badge=EXTRA_LABEL, section=EXTRA_LABEL, source="payoff_list",
             review_tier=rt, review_tier_label=REVIEW_TIER_LABEL[rt], ws3_payoff=payoff_public(p),
             payoff_emotion=p["emotion"], payoff_intensity=p["intensity"], payoff_reason=p["reason"],
             lands_on_screen=p["lands_on_screen"], ws3_payoff_type=p["type"],
             call_c={"strength": x["moment"].get("strength"), "flags": x.get("flags") or []})
    if rt == "clear_failure":
        d["flags"].append("v1_rule_would_hide")
        d["v1_hide_reason"] = x.get("v1_hide_reason")
    if rt == "unreviewed":
        d["review_status"] = "missing"
        d["review_note"] = "no review returned for this clip; review it manually"
        d["flags"] = [f for f in d["flags"] if f != "review_blocked"] + ["no_review"]
        d["edit_notes"] = [n for n in d["edit_notes"] if n.get("type") != "review_blocked"]
    return d


def new_rejected_doc(ep: ws.Episode, x: dict, vcfg: dict) -> dict:
    p = x["payoff"]
    base = {"id": x["id"], "tier": "rejected", "source": "payoff_list", "reject_reasons": x["reject_reasons"],
            "ws3_payoff": payoff_public(p)}
    if x.get("dup_of"):
        base["dup_of"] = x["dup_of"]
    c = x.get("clip")
    if c:
        base.update(pieces=c["pieces"], **ws.core_fields(ep, c["pieces"], vcfg["guardrails"]["skip_s"]),
                    time_segments=c["time_segments"], video_duration=c["video_duration"],
                    start_line=c["start_line"], end_line=c["end_line"])
    if x.get("moment"):
        m = x["moment"]
        base["moment"] = {k: m.get(k) for k in ("purpose", "start_line", "end_line", "what_happens", "strength")}
    return base


# ---------------------------------------------------------------------------------------------------- projection
def context_tokens(vid: str, ep: ws.Episode, ix, acfg: dict, cfg: dict, system: str, prefix: str,
                   log=print) -> tuple[int, str]:
    """Cached-context tokens of this episode: measured by a ws3 run of the same episode when one exists (its calls'
    cached_input_tokens), else ws3's offline estimate."""
    d = (cfg.get("calibrate_from") or {}).get("ws3")
    f = ROOT / d / f"{vid}.json" if d else None
    if f and f.exists():
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
            br = doc["cost"]["by_role"]
            for role in ("ws3_hook", "ws3_clip", "ws3_payoffs"):
                r = br.get(role) or {}
                if r.get("cached_input_tokens") and r.get("n_llm_calls"):
                    return int(r["cached_input_tokens"] / r["n_llm_calls"]), f"measured ({d}/{vid}.json {role})"
        except Exception as e:   # noqa: BLE001 (projection only)
            log(f"context token calibration from {f} failed ({e})")
    ratio, src = ws3.calibration_ratio(vid, ep, ix, acfg, log)
    return ws3.est_tokens(system) + ws3.est_tokens(prefix, ratio), f"estimate ({src})"


def project(cfg: dict, acfg: dict, vcfg: dict, t_cache: int, tasks_c: list[tuple[str, bool]], n_cap: int | None,
            v1doc: dict | None, a_cached: bool, a_task: str) -> dict:
    """Rupees of the paid part, per cap setting: call C per missed payoff (exact task sizes, cached ones free), the
    review of the new clips (assumes every called payoff yields a reviewed clip), the explicit cache storage (once,
    when anything is uncached); plus the separate cost of the review_context option."""
    from shotvi.llm.pricing import cache_storage_usd, cost_usd
    sc, sa = spec_c(cfg), ws3.spec_of(acfg, "ws3_payoffs")
    rspec = ws.spec_of(vcfg, "ws_review")
    ttl_h = float(acfg["cache"]["ttl_s"]) / 3600.0
    explicit = acfg["cache"].get("mode", "explicit") == "explicit"
    storage = ws.inr(cache_storage_usd(sc.model, t_cache, ttl_h) or 0.0) if explicit else 0.0
    write_worst = ws.inr(cost_usd(sc.model, t_cache, 0) or 0.0) if explicit else 0.0
    max_c = int(sc.params.get("max_output_tokens", 8192))

    def c_cost(task, out):
        tin = ws3.est_tokens(task)
        return ws.inr(cost_usd(sc.model, tin + t_cache, out, cached_tokens=t_cache if explicit else 0) or 0.0)

    def c_cost_exp(task):
        tin = ws3.est_tokens(task)
        return ws.inr(cost_usd(sc.model, tin + t_cache, C_OUT_EXPECTED, cached_tokens=t_cache) or 0.0)

    ctx = bool(cfg.get("review_context"))

    def review(n, with_ctx):
        if n <= 0:
            return 0.0, 0.0
        cached = t_cache if with_ctx else 0
        e_in = REVIEW_IN_EXP[0] + REVIEW_IN_EXP[1] * n
        e_out = REVIEW_OUT_EXP[0] + REVIEW_OUT_EXP[1] * n
        g_in = ws.REVIEW_IN_BASE + ws.REVIEW_IN_PER_CLIP * n
        g_out = ws.review_out_guard(rspec.params, n)
        exp = ws.inr(review_cost_usd(rspec, e_in, e_out, cached))
        worst = ws.inr(review_cost_usd(rspec, g_in, g_out, cached)) + ws.inr(ws.review_fallback_usd(rspec, n, g_in))
        return exp, worst

    a_exp = a_worst = 0.0
    if not a_cached:
        tin = ws3.est_tokens(a_task)
        a_exp = ws.inr(cost_usd(sa.model, tin + t_cache, ws3.PROJ_OUT["ws3_payoffs"], cached_tokens=t_cache) or 0.0)
        a_worst = ws.inr(cost_usd(sa.model, tin + t_cache, int(sa.params.get("max_output_tokens", 32768)),
                                  cached_tokens=t_cache if explicit else 0) or 0.0)

    def row(cap):
        sel = tasks_c if cap is None else tasks_c[:cap]
        unc = [t for t, hit in sel if not hit]
        ce = sum(c_cost_exp(t) for t in unc)
        cw = sum(c_cost(t, max_c) for t in unc)
        re_, rw = review(len(sel), ctx)
        anything = bool(unc) or not a_cached
        st_e, st_w = (storage, storage + write_worst) if anything else (0.0, 0.0)
        return {"cap": cap, "n_calls": len(sel), "n_uncached": len(unc),
                "call_c_inr_expected": round(ce, 2), "call_c_inr_worst": round(cw, 2),
                "review_new_inr_expected": round(re_, 2), "review_new_inr_worst": round(rw, 2),
                "storage_inr": round(st_e, 2), "call_a_inr_expected": round(a_exp, 2),
                "inr_expected": round(a_exp + ce + re_ + st_e, 2), "inr_worst": round(a_worst + cw + rw + st_w, 2)}

    rows = {"no_cap": row(None), "cap": row(n_cap) if n_cap is not None else None}
    # review_context option: v1's review re-run through the cache (a miss) + the new clips' review with the cached
    # transcript as input + the cache storage when nothing else needs it
    rc = None
    if v1doc is not None:
        br = (v1doc.get("cost") or {}).get("by_role", {}).get("ws_review") or {}
        n_v1 = int((v1doc.get("cutoff") or {}).get("n_passed_hard_rules") or len(v1doc.get("proposals") or []))
        sent = 40   # the one added sentence
        if br.get("input_tokens"):
            vin, vout = int(br["input_tokens"]) + sent, int(br["output_tokens"])
            v_exp = ws.inr(review_cost_usd(rspec, vin, vout, t_cache))
            basis = "v1 review as measured, plus the cached transcript"
        else:
            v_exp = review(n_v1, True)[0]
            basis = "defaults"
        g_in = ws.REVIEW_IN_BASE + ws.REVIEW_IN_PER_CLIP * max(1, n_v1)
        v_worst = ws.inr(review_cost_usd(rspec, g_in, ws.review_out_guard(rspec.params, n_v1), t_cache)) \
            + ws.inr(ws.review_fallback_usd(rspec, n_v1, g_in))
        n_new = len(tasks_c if n_cap is None else tasks_c[:n_cap])
        d_exp = review(n_new, True)[0] - review(n_new, False)[0]
        d_w = review(n_new, True)[1] - review(n_new, False)[1]
        rc = {"n_v1_reviewed": n_v1, "basis": basis, "v1_review_inr_expected": round(v_exp, 2),
              "v1_review_inr_worst": round(v_worst, 2), "new_review_delta_inr_expected": round(d_exp, 2),
              "new_review_delta_inr_worst": round(d_w, 2),
              "storage_if_alone_inr": round(storage, 2),
              "inr_expected": round(v_exp + d_exp, 2), "inr_worst": round(v_worst + d_w + write_worst, 2)}
    return {"context_tokens": t_cache, "storage_inr": round(storage, 2), "write_inr_worst": round(write_worst, 2),
            "call_c": {"model": sc.model, "thinking_level": sc.params.get("thinking_level"),
                       "out_expected": C_OUT_EXPECTED, "out_max": max_c,
                       "inr_expected_each": round(c_cost_exp(tasks_c[0][0]), 2) if tasks_c else None,
                       "inr_worst_each": round(c_cost(tasks_c[0][0], max_c), 2) if tasks_c else None},
            "rows": rows, "review_context": rc, "review_context_on": ctx}


# ---------------------------------------------------------------------------------------------------- the episode
def run_episode(vid: str, cfg: dict, llm_a, llm_b, guard: ws.Guard, out_dir: Path, log=print,
                dry_run: bool = False) -> dict:
    t0 = time.time()
    acfg, vcfg = cfg_a(cfg), cfg_b(cfg)
    vcfg["run_id"] = f"{cfg.get('run_id', 'merged')}/v1"
    ctx_on = bool(cfg.get("review_context"))
    ep_ctx, ix, _tr, tp = ws.load_episode(vid, with_loudness=False, speaker_names=bool(acfg.get("speaker_names")),
                                          require_names=False)
    system, prefix = ws3.context_prompt(ep_ctx, acfg)
    spec_a = ws3.spec_of(acfg, "ws3_payoffs")
    cache = ws3.ContextCache(system, prefix, spec_a, acfg, log)
    task_a = ws3.task_text(acfg, "ws3_payoffs", episode_len=ws.fmt(ep_ctx.duration), n_lines=len(ep_ctx.lines))
    a_cached = cache.is_hit(llm_a, spec_a, task_a, ws3.PAYOFFS_SCHEMA)
    t_cache, t_src = context_tokens(vid, ep_ctx, ix, acfg, cfg, system, prefix, log)
    sc = spec_c(cfg)
    schema_c = moment_schema(vcfg)
    cap = cfg["missed"].get("max_calls")
    debug: dict = {"video_id": vid, "warnings": [], "context_tokens": [t_cache, t_src]}
    if dry_run:
        llm_a.cache_only = True
        llm_b.cache_only = True

    # worst case per billed role of the merged client (the spend guard checks it before every billed attempt)
    from shotvi.llm.pricing import cost_usd
    c_worst = ws.inr(cost_usd(sc.model, 4000 + t_cache, int(sc.params.get("max_output_tokens", 8192)),
                              cached_tokens=t_cache) or 0.0)
    a_worst = ws.inr(cost_usd(spec_a.model, 4000 + t_cache, int(spec_a.params.get("max_output_tokens", 32768)),
                              cached_tokens=t_cache) or 0.0)
    call_proj = {"ws3_payoffs": a_worst, ROLE_C: c_worst, "ws_review": 0.0}
    llm_a.before_call = lambda role, _spec: guard.check(f"{vid} {role}", call_proj.get(role, max(call_proj.values())))
    if not a_cached and not dry_run:
        from shotvi.llm.pricing import cache_storage_usd
        st = ws.inr((cache_storage_usd(spec_a.model, t_cache, float(acfg["cache"]["ttl_s"]) / 3600) or 0.0)
                    + (cost_usd(spec_a.model, t_cache, 0) or 0.0))
        guard.check(f"{vid} episode (cache + call A + one call C, worst case)", st + a_worst + c_worst)
    try:
        # ------------------------------------------------------------------ A: the payoff list (ws3 call 1)
        if dry_run and not a_cached:
            debug["dry"] = "call A not cached"
            payoffs = rej_payoffs = None
            disc = {}
        else:
            try:
                disc = ask_cached(llm_a, cache, "ws3_payoffs", spec_a, task_a, ws3.PAYOFFS_SCHEMA, {"video": vid})
            except SafetyBlocked as e:
                raise EpisodeStop(f"{vid}: ws3_payoffs blocked by Gemini's safety check ({e.reason})") from e
            except LLMError as e:
                raise EpisodeStop(f"{vid}: ws3_payoffs failed ({e}); one attempt per call") from e
            payoffs, rej_payoffs = ws3.validate_payoffs(ep_ctx, disc, acfg)
            debug["payoffs_raw"] = disc
            log(f"{vid}: A: {len(disc.get('payoffs') or [])} payoffs listed, {len(payoffs)} valid "
                f"(by intensity {dict(sorted(Counter(p['intensity'] for p in payoffs).items(), reverse=True))})")

        # ------------------------------------------------------------------ B: v1 as is
        llm_bb = ReviewContextLLM(llm_b, cache, llm_a, t_cache) if ctx_on else llm_b
        b_dir = out_dir / "v1"
        v1doc, b_dry = None, None
        # the dry run replays v1 exactly as v1 runs it (review_context off): that replay is free and gives the
        # exact v1 clips the missed count is computed against; the review_context option is projected separately
        vcfg_b = vcfg if not dry_run else cfg_b({**cfg, "review_context": False})
        vcfg_b["run_id"] = vcfg["run_id"]
        try:
            v1doc = ws.run_episode(vid, copy.deepcopy(vcfg_b), llm_b if dry_run else llm_bb, guard, b_dir, log=log,
                                   dry_run=False)
        except SafetyBlocked as e:
            raise EpisodeStop(f"{vid}: v1 discover blocked ({e.reason})") from e
        except LLMError as e:
            if not dry_run:
                raise EpisodeStop(f"{vid}: v1 failed ({e})") from e
            # dry run: v1 is not (fully) cached; project it with v1's own dry run (no provider call)
            b_dry = ws.run_episode(vid, copy.deepcopy(vcfg_b), llm_b, guard, b_dir, log=log, dry_run=True)
            debug["b_miss"] = str(e)
        if v1doc is None:
            return dry_doc(vid, out_dir, payoffs, None, None, None, None, b_dry, debug, log, a_cached)

        ep = ws.load_episode(vid)[0]          # v1's episode (loudness for the lift checks, Scribe labels)
        props_v1 = v1doc["proposals"]
        hidden = [r for r in v1doc.get("rejected") or [] if r.get("time_segments")]
        if payoffs is None:
            return dry_doc(vid, out_dir, None, None, None, None, v1doc, None, debug, log, a_cached)
        covered, missed = match_payoffs(ep, payoffs, props_v1, cfg["match"], hidden)
        order = sorted(missed, key=lambda m: (-m["payoff"]["intensity"], m["payoff"]["order"]))
        called = order if cap is None else order[:cap]
        beyond = [] if cap is None else order[cap:]
        lift_ids = ws.liftable_lines(ep, vcfg["cut"])
        tasks = [(task_c(cfg, ep, m["payoff"], lift_ids), None) for m in order]
        tasks = [(t, ask_hit(llm_a, cache, sc, t, schema_c)) for t, _ in tasks]
        log(f"{vid}: match: {len(payoffs)} payoffs, {len(covered)} covered by v1, {len(missed)} missed "
            f"({sum(1 for m in missed if m['inside_v1'])} played early inside a v1 clip); call C for "
            f"{len(called)}" + (f" (cap {cap}; {len(beyond)} beyond it)" if beyond else ""))
        proj = project(cfg, acfg, vcfg, t_cache, tasks, cap, v1doc, a_cached, task_a)
        debug["projection"] = proj
        if dry_run:
            return dry_doc(vid, out_dir, payoffs, rej_payoffs, covered, missed, v1doc, None, debug, log, a_cached,
                           proj=proj, cap=cap, n_called=len(called))

        # spend: cut the call-C list to what the limit still allows (lowest intensity dropped first)
        if guard.limit is not None and called:
            from shotvi.llm.spend import total_inr
            room = guard.limit - total_inr() - proj["storage_inr"] - proj["write_inr_worst"]
            n_fit = len(called)
            while n_fit > 0:
                rv = project(cfg, acfg, vcfg, t_cache, tasks[:n_fit], None, None, True, task_a)["rows"]["no_cap"]
                if rv["call_c_inr_worst"] + rv["review_new_inr_worst"] <= room:
                    break
                n_fit -= 1
            if n_fit < len(called):
                beyond = called[n_fit:] + beyond
                called = called[:n_fit]
                debug["warnings"].append(f"spend limit: call C for {n_fit} of {len(order)} missed payoffs")
                log(f"{vid}: spend limit: call C for {n_fit} missed payoffs")

        # ------------------------------------------------------------------ C: one whole moment per missed payoff
        cands, rejected, calls = [], [], []
        new_moments: list[dict] = []
        task_of = {m["payoff"]["id"]: t for m, (t, _) in zip(order, tasks)}
        for mm in called:
            p = mm["payoff"]
            x = {"id": "m" + p["id"][1:], "payoff": p, "inside_v1": mm["inside_v1"]}
            rec = {"payoff": p["id"], "line": p["line"]}
            calls.append(rec)
            try:
                ans = ask_cached(llm_a, cache, ROLE_C, sc, task_of[p["id"]], schema_c,
                                 {"video": vid, "payoff": p["line"], "merged": 1})
                rec["answer"] = ans
                x["moment"], x["flags"] = validate_moment(ep, p, ans, cfg)
            except (LLMError, ValueError) as e:     # SafetyBlocked is an LLMError: this payoff is skipped
                rec["error"] = str(e)
                rejected.append({**x, "reject_reasons": [f"C1: {e}"]})
                log(f"  {x['id']} {p['line']}: no moment: {e}")
                continue
            new_moments.append(x["moment"])
            bpos = ws.barrier_for(ep, [{"end_line": v["end_line"]} for v in props_v1] + new_moments, x["moment"])
            try:
                c = build_new(ep, x, vcfg, bpos)
            except ws.Reject as e:
                rejected.append({**x, "reject_reasons": [f"B1: {e}"]})
                log(f"  {x['id']} {p['line']}: rejected by the cut builder: {e}")
                continue
            rr = ws.hard_rules(ep, c, vcfg)
            if rr:
                rejected.append({**x, "reject_reasons": rr})
                log(f"  {x['id']} {p['line']}: rejected: {rr}")
                continue
            cands.append(x)
            log(f"  {x['id']} {p['line']}: clip {c['start_line']}..{c['end_line']} plays {c['video_duration']:.1f} s"
                + (", cold open" if c["cold_open"] else ""))
        debug["calls_c"] = calls
        n_built = len(cands)
        kept, dups = dedupe_new(cands, props_v1, vcfg["guardrails"]["dup_overlap"])
        for d in dups:
            log(f"  {d['id']}: dropped: {d['reject_reasons'][0]}")
        rejected += dups

        # ------------------------------------------------------------------ review the new clips (v1's review)
        if kept:
            llm_r = ReviewContextLLM(llm_a, cache, llm_a, t_cache) if ctx_on else llm_a
            debug["review_new"] = review_new(ep, kept, vcfg, llm_r, guard, call_proj, vid,
                                             t_cache if ctx_on else 0, log)
        shown_new = rank_new(kept, vcfg) if kept else []
    finally:
        debug["cache"] = cache.close()

    # ------------------------------------------------------------------ output
    attach_payoffs(props_v1, payoffs, covered)
    line_of_word = {r["words"][0]: r for r in ep.lines if ep.is_speech(r)}
    n1 = len(props_v1)
    new_docs = [new_proposal_doc(ep, x, n1 + k + 1, vcfg, line_of_word) for k, x in enumerate(shown_new)]
    rej_docs = [new_rejected_doc(ep, x, vcfg) for x in rejected]
    status = payoff_table(payoffs, covered, missed, called, beyond, rejected, shown_new)
    summary = episode_summary(disc, payoffs, rej_payoffs, covered, missed, called, beyond, n_built, rejected,
                              shown_new, cap, debug)
    doc = run_document(v1doc, props_v1 + new_docs, rej_docs, cfg, acfg, vcfg, llm_a, summary, status, t0, debug)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{vid}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "debug").mkdir(exist_ok=True)
    debug["rejected_payoffs"] = rej_payoffs
    (out_dir / "debug" / f"{vid}.json").write_text(json.dumps(debug, ensure_ascii=False, indent=1, default=str),
                                                   encoding="utf-8")
    (out_dir / f"{vid}.md").write_text(clips_md(ep, doc), encoding="utf-8")
    s = doc["episode_summary"]
    log(f"{vid}: {s['n_payoffs_valid']} payoffs: {s['n_covered_by_v1']} covered by v1, {s['n_missed']} missed -> "
        f"{s['n_call_c']} calls -> {s['n_new_built']} built -> {s['n_new_shown']} new shown; "
        f"${doc['cost']['usd_billed']:.4f} billed -> {out_dir / (vid + '.json')}")
    return doc


def ask_hit(llm, cache: ws3.ContextCache, spec, task: str, schema: dict) -> bool:
    return cache.is_hit(llm, spec, task, schema)


def payoff_table(payoffs, covered, missed, called, beyond, rejected, shown_new) -> list[dict]:
    """One row per valid payoff: covered (by which v1 clip, how) or missed and what became of it."""
    called_ids = {m["payoff"]["id"] for m in called}
    beyond_ids = {m["payoff"]["id"] for m in beyond}
    rej = {x["payoff"]["id"]: x["reject_reasons"][0] for x in rejected}
    shown = {x["payoff"]["id"]: x["id"] for x in shown_new}
    miss = {m["payoff"]["id"]: m for m in missed}
    out = []
    for p in payoffs:
        row = {"id": p["id"], "line": p["line"], "intensity": p["intensity"], "type": p["type"],
               "emotion": p["emotion"], "text": ws.one_line(p["text"], 120)}
        if p["id"] in covered:
            row.update(status="covered", by=covered[p["id"]]["clip"], match=covered[p["id"]])
        else:
            m = miss[p["id"]]
            if m["inside_v1"]:
                row["inside_v1"] = m["inside_v1"]
            if m["in_hidden_v1"]:
                row["in_hidden_v1"] = m["in_hidden_v1"]
            if p["id"] in shown:
                row.update(status="new_clip", clip=shown[p["id"]])
            elif p["id"] in rej:
                row.update(status="new_clip_dropped", reason=rej[p["id"]])
            elif p["id"] in beyond_ids:
                row.update(status="missed_not_called", reason="beyond the call-C cap / spend limit")
            elif p["id"] in called_ids:
                row.update(status="missed_called")
            else:
                row.update(status="missed")
        out.append(row)
    return out


def episode_summary(disc, payoffs, rej_payoffs, covered, missed, called, beyond, n_built, rejected, shown_new, cap,
                    debug) -> dict:
    rr = Counter(x["reject_reasons"][0].split(":")[0] for x in rejected)
    return {"n_payoffs_listed": len(disc.get("payoffs") or []), "n_payoffs_valid": len(payoffs),
            "n_payoffs_rejected": len(rej_payoffs),
            "payoffs_rejected": [{"line": (r["payoff"] or {}).get("line"), "reason": r["reason"]} for r in rej_payoffs],
            "n_covered_by_v1": len(covered), "n_missed": len(missed),
            "n_missed_played_early_in_v1": sum(1 for m in missed if m["inside_v1"]),
            "n_missed_in_hidden_v1": sum(1 for m in missed if m["in_hidden_v1"] and not m["inside_v1"]),
            "missed_by_intensity": {str(k): n for k, n in sorted(Counter(m["payoff"]["intensity"]
                                                                         for m in missed).items(), reverse=True)},
            "call_c_cap": cap, "n_call_c": len(called), "n_missed_not_called": len(beyond),
            "n_call_c_failed": rr.get("C1", 0), "n_new_rejected_builder": rr.get("B1", 0),
            "n_new_rejected_hard_rules": sum(n for k, n in rr.items() if k not in ("C1", "B1", "D2", "D3")),
            "n_new_built": n_built, "n_new_dropped_dup_v1": rr.get("D2", 0), "n_new_dropped_dup_new": rr.get("D3", 0),
            "n_new_shown": len(shown_new),
            "new_by_review_tier": dict(Counter(x["review_tier"] for x in shown_new)),
            "warnings": debug.get("warnings", []), "episode_notes": disc.get("episode_notes", "")}


def run_document(v1doc, props, rej_docs, cfg, acfg, vcfg, llm_a, summary, table, t0, debug) -> dict:
    from shotvi.select.prompts import load_prompt
    doc = copy.deepcopy(v1doc)
    sa = llm_a.ledger.summary()
    vc = v1doc.get("cost") or {}
    doc.update({
        "run_id": cfg.get("run_id", "merged"), "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pipeline": {"version": PIPELINE_VERSION, "git_commit": None,
                     "design": "founder 2026-10-15: v1 as is + ws3 payoff list; a v1-style whole moment for every "
                               "payoff v1 missed (one call each, cached context)",
                     "v1": v1doc.get("pipeline"),
                     "prompt_versions": {**{r: ws3.load_p(acfg, r).tag for r in (ws3.CONTEXT_ROLE, "ws3_payoffs")},
                                         **(v1doc.get("pipeline") or {}).get("prompt_versions", {}),
                                         ROLE_C: load_prompt(cfg["prompts"][ROLE_C]).tag},
                     "prompt_files": {ws3.CONTEXT_ROLE: ws3.prompt_name(acfg, ws3.CONTEXT_ROLE),
                                      "ws3_payoffs": ws3.prompt_name(acfg, "ws3_payoffs"),
                                      **(v1doc.get("pipeline") or {}).get("prompt_files", {}),
                                      ROLE_C: cfg["prompts"][ROLE_C]},
                     "config": cfg, "review_context": bool(cfg.get("review_context"))},
        "models": (v1doc.get("models") or []) + [{"role": "ws3_payoffs", **acfg["models"]["ws3_payoffs"]},
                                                 {"role": ROLE_C, **cfg["models"][ROLE_C]}],
        "cost": {"usd": round((vc.get("usd") or 0.0) + sa["usd"], 5),
                 "usd_billed": round((vc.get("usd_billed") or 0.0) + sa["usd_billed"], 5),
                 "input_tokens": (vc.get("input_tokens") or 0) + sa["input_tokens"],
                 "output_tokens": (vc.get("output_tokens") or 0) + sa["output_tokens"],
                 "cached_input_tokens": sa["cached_input_tokens"],
                 "n_llm_calls": (vc.get("n_llm_calls") or 0) + sa["n_llm_calls"],
                 "wall_time_secs": round(time.time() - t0, 1),
                 "v1": vc, "merged": {"by_role": sa["by_role"], "usd": sa["usd"], "usd_billed": sa["usd_billed"]},
                 "by_role": {**(vc.get("by_role") or {}), **sa["by_role"]},
                 "projection": debug.get("projection"), "cache": debug.get("cache")},
        "episode_summary": summary, "payoff_table": table,
        "proposals": props,
        "rejected": (v1doc.get("rejected") or []) + rej_docs,
    })
    cut = dict(doc.get("cutoff") or {})
    n_new = sum(p.get("source") == "payoff_list" for p in props)
    cut.update(n_v1_shown=len(props) - n_new, n_extra=n_new, n_shown=len(props),
               extra_rule="new clips (beats v1 missed) are shown unless a hard rule fails; v1's clear-failure rule "
                          "only flags them (v1_rule_would_hide)")
    doc["cutoff"] = cut
    return doc


def clips_md(ep: ws.Episode, doc: dict) -> str:
    s = doc["episode_summary"]
    head = [f"# {doc['video_id']} merged web-series clips ({doc['run_id']}): v1 + beats v1 missed\n",
            f"Payoff list: {s['n_payoffs_listed']} listed, {s['n_payoffs_valid']} valid; {s['n_covered_by_v1']} "
            f"covered by v1 clips, {s['n_missed']} missed ({s['n_missed_played_early_in_v1']} played early inside a "
            f"v1 clip; by intensity {s['missed_by_intensity']}). Call C for {s['n_call_c']}"
            + (f" (cap {s['call_c_cap']}, {s['n_missed_not_called']} not called)" if s["n_missed_not_called"] else "")
            + f": {s['n_new_built']} built, {s['n_call_c_failed']} failed, {s['n_new_rejected_builder']} rejected by "
              f"the builder, {s['n_new_rejected_hard_rules']} by hard rules (e.g. under 15 s), "
              f"{s['n_new_dropped_dup_v1']} dropped as duplicates of v1 clips, {s['n_new_dropped_dup_new']} of other "
              f"new clips; {s['n_new_shown']} new clips shown (section 'beat v1 missed', after v1's list).\n",
            f"Review context: {'ON (review sees the whole transcript)' if doc['pipeline']['review_context'] else 'off'}."
            f" Cost ${doc['cost']['usd']:.4f} (${doc['cost']['usd_billed']:.4f} billed this run).\n"]
    body = ws.clips_md(ep, doc).split("\n", 1)[1]
    pt = ["\n## Payoff list\n"]
    for r in doc["payoff_table"]:
        how = (f"covered by {r['by']} ({r['match']['rule']}, at {r['match']['frac']:.0%}, "
               f"{r['match']['lines_from_end']} lines from its end)" if r["status"] == "covered" else
               f"{r['status']}" + (f" {r['clip']}" if r.get("clip") else "") + (f": {r['reason']}" if r.get("reason")
                                                                                 else ""))
        extra = (f"; played early in {', '.join(x['clip'] for x in r['inside_v1'])}" if r.get("inside_v1") else "")
        pt.append(f"- {r['id']} {r['line']} (intensity {r['intensity']}, {r['type']}, {r['emotion']}): {how}{extra}. "
                  f"{r['text']}")
    new = [p for p in doc["proposals"] if p.get("source") == "payoff_list"]
    nx = ["\n## Beats v1 missed: payoff details\n"]
    for p in new:
        w = p["ws3_payoff"]
        nx.append(f"- #{p['rank']} {p['id']} ({p['review_tier_label']}): payoff {w['line']} {w['payoff_type']}, "
                  f"{w['payoff_emotion']}, intensity {w['payoff_intensity']}"
                  f"{', lands on screen' if w['lands_on_screen'] else ''}: {w['payoff_reason']}"
                  + (f" [v1 rule would hide: {p.get('v1_hide_reason')}]" if p.get("v1_hide_reason") else ""))
    return "\n".join(head) + body + "\n".join(nx) + "\n" + "\n".join(pt) + "\n"


def dry_doc(vid, out_dir, payoffs, rej_payoffs, covered, missed, v1doc, b_dry, debug, log, a_cached, proj=None,
            cap=None, n_called=None) -> dict:
    res = {"video_id": vid, "dry_run": True, "call_a_cached": a_cached, "v1_cached": v1doc is not None,
           "projection": proj, "context_tokens": debug.get("context_tokens")}
    if b_dry is not None:
        res["v1_dry_run"] = {k: b_dry.get(k) for k in ("first_paid_stage", "would_bill_inr_expected",
                                                        "would_bill_inr_guard")}
    if v1doc is not None:
        res["v1_shown"] = len(v1doc["proposals"])
        res["v1_billed_usd"] = (v1doc.get("cost") or {}).get("usd_billed")
    if payoffs is not None and missed is not None:
        res.update(n_payoffs_valid=len(payoffs), n_payoffs_rejected=len(rej_payoffs or []),
                   n_covered=len(covered), n_missed=len(missed),
                   n_missed_played_early_in_v1=sum(1 for m in missed if m["inside_v1"]),
                   missed_by_intensity={str(k): n for k, n in sorted(Counter(m["payoff"]["intensity"]
                                                                             for m in missed).items(), reverse=True)},
                   call_c_cap=cap, n_call_c_at_cap=n_called,
                   covered=covered, missed=[{"id": m["payoff"]["id"], "line": m["payoff"]["line"],
                                             "intensity": m["payoff"]["intensity"],
                                             "inside_v1": [x["clip"] for x in m["inside_v1"]],
                                             "in_hidden_v1": [x["clip"] for x in m["in_hidden_v1"]]}
                                            for m in missed])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"dry_run_{vid}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str),
                                                 encoding="utf-8")
    if proj:
        log(projection_text(vid, res))
    else:
        log(f"{vid}: DRY RUN: call A {'cached' if a_cached else 'NOT cached'}; v1 "
            f"{'cached' if v1doc is not None else 'NOT cached: ' + json.dumps(res.get('v1_dry_run'))}; "
            f"missed payoffs cannot be counted without both")
    return res


def projection_text(vid: str, res: dict) -> str:
    pr = res["projection"]
    out = [f"{vid}: payoffs {res['n_payoffs_valid']} valid ({res['n_payoffs_rejected']} rejected by ws3's checks); "
           f"covered by v1 {res['n_covered']}; missed {res['n_missed']} (played early inside a v1 clip "
           f"{res['n_missed_played_early_in_v1']}; by intensity {res['missed_by_intensity']}); call A "
           f"{'cached (Rs 0)' if res['call_a_cached'] else 'NOT cached'}; v1 {'cached (Rs 0)' if res['v1_cached'] else 'NOT cached'}",
           f"  context {pr['context_tokens']} tokens; call C {pr['call_c']['thinking_level']}: "
           f"Rs {pr['call_c']['inr_expected_each']} expected / Rs {pr['call_c']['inr_worst_each']} worst each; "
           f"cache storage Rs {pr['storage_inr']}",
           "  setting  | calls | C exp | C worst | review exp | review worst | total exp | total worst"]
    for k, r in pr["rows"].items():
        if r is None:
            continue
        out.append(f"  {('no cap' if r['cap'] is None else 'cap ' + str(r['cap'])):8} | {r['n_calls']:5} | "
                   f"{r['call_c_inr_expected']:5.2f} | {r['call_c_inr_worst']:7.2f} | {r['review_new_inr_expected']:10.2f} | "
                   f"{r['review_new_inr_worst']:12.2f} | {r['inr_expected']:9.2f} | {r['inr_worst']:11.2f}")
    rc = pr.get("review_context")
    if rc:
        out.append(f"  review_context option (separate): v1 review re-run with the transcript Rs {rc['v1_review_inr_expected']}"
                   f" exp / Rs {rc['v1_review_inr_worst']} worst ({rc['n_v1_reviewed']} clips, {rc['basis']}); new-clip "
                   f"review +Rs {rc['new_review_delta_inr_expected']} exp; total +Rs {rc['inr_expected']} exp / "
                   f"+Rs {rc['inr_worst']} worst (storage Rs {rc['storage_if_alone_inr']} if nothing else creates "
                   f"the cache)")
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description="Merged web-series pipeline: v1 + the ws3 payoff list")
    ap.add_argument("video_ids", nargs="+")
    ap.add_argument("--out", default="runs/merged")
    ap.add_argument("--dry-run", action="store_true",
                    help="free: replays cached A and v1 (cache only), counts missed payoffs, projects call C")
    ap.add_argument("--max-ledger-inr", type=float, default=None,
                    help="stop before any call whose worst case would push the project ledger past this")
    ap.add_argument("--max-missed", type=int, default=None,
                    help="cap on call C (missed payoffs, highest intensity first); 0 = no cap; default: config (20)")
    ap.add_argument("--review-context", action="store_true",
                    help="opt-in: the review calls see the whole transcript through the context cache (paid re-review)")
    ap.add_argument("--config", default=None, help="JSON deep-merged over DEFAULTS")
    ap.add_argument("--cache-only", action="store_true",
                    help="never call the provider: rebuild from cached responses (free); a miss fails")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    os.chdir(ROOT)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from shotvi.llm import Ledger, LLMClient
    cfg = ws.deep_merge(DEFAULTS, json.loads(Path(a.config).read_text(encoding="utf-8")) if a.config else {})
    if a.max_missed is not None:
        cfg["missed"]["max_calls"] = None if a.max_missed <= 0 else a.max_missed
    if a.review_context:
        cfg["review_context"] = True
    out = Path(a.out)
    cfg["run_id"] = out.name
    guard = ws.Guard(a.max_ledger_inr)
    from shotvi.llm.spend import total_inr
    led0 = total_inr()
    if a.dry_run:
        print(f"DRY RUN (cache-only replays, no provider call of any kind): ledger Rs {led0:.2f}"
              + (f", limit Rs {a.max_ledger_inr:.2f}" if a.max_ledger_inr is not None else ""))
    results, stopped = [], []
    for vid in a.video_ids:
        llm_a = LLMClient(Ledger(max_usd=cfg["budget_usd"]), cache_dir="runs/cache", max_retries=0, schema_retries=0)
        llm_b = LLMClient(Ledger(max_usd=ws.DEFAULTS["budget_usd"]), cache_dir="runs/cache", max_retries=0,
                          schema_retries=0)
        llm_a.cache_only = llm_b.cache_only = a.cache_only or a.dry_run
        try:
            results.append(run_episode(vid, cfg, llm_a, llm_b, guard, out, dry_run=a.dry_run))
        except (ws.BudgetStop, BudgetExceeded) as e:
            print(f"STOP: {e}")
            return 2
        except EpisodeStop as e:
            print(f"STOPPED EPISODE: {e}")
            stopped.append(vid)
    if a.dry_run:
        rs = [r for r in results if r.get("projection")]
        for key in ("no_cap", "cap"):
            rows = [r["projection"]["rows"][key] for r in rs if r["projection"]["rows"].get(key)]
            if rows:
                lab = "no cap" if key == "no_cap" else f"cap {rows[0]['cap']}"
                exp, wst = sum(x["inr_expected"] for x in rows), sum(x["inr_worst"] for x in rows)
                print(f"DRY RUN total ({lab}): {sum(x['n_calls'] for x in rows)} calls C; Rs {exp:.2f} expected, "
                      f"Rs {wst:.2f} worst; ledger Rs {led0:.2f} -> ~Rs {led0 + exp:.2f}"
                      + (f" (limit Rs {a.max_ledger_inr:.2f}: "
                         f"{'fits the worst case' if led0 + wst <= a.max_ledger_inr else 'fits the expected case only' if led0 + exp <= a.max_ledger_inr else 'does NOT fit'})"
                         if a.max_ledger_inr is not None else ""))
        rcs = [r["projection"]["review_context"] for r in rs if r["projection"].get("review_context")]
        if rcs:
            print(f"DRY RUN review_context option (separate, on top): +Rs {sum(x['inr_expected'] for x in rcs):.2f} "
                  f"expected, +Rs {sum(x['inr_worst'] for x in rcs):.2f} worst")
        return 0
    print(f"ledger now Rs {total_inr():.2f} (this run Rs {total_inr() - led0:.2f})")
    if stopped:
        print(f"NO CLIPS: {', '.join(stopped)}")
        return EXIT_SAFETY_BLOCKED
    return 0


if __name__ == "__main__":
    sys.exit(main())
