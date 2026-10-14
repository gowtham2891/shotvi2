"""Web-series clip selection v3 ("ws3"): the founder's three-call design (2026-10-14). Additive: the v1 pipeline
(``shotvi.select.webseries``) and its defaults are untouched; ws3 only imports its episode loader and cut builder.

Design (per episode):
  context  the WHOLE transcript as numbered lines (LineIndex, ASR correction layer on, corrected speaker names from
           runs/speaker_fix/<id>.json when present) is sent ONCE and held in a Gemini explicit context cache
           (cachedContents, TTL ``cache.ttl_s`` = 1 h; ``GeminiProvider.create_cache``). Every call then sends only
           its task text and references the cache: cached tokens are billed at the cached-input rate
           (gemini-3.8-flash: $0.075 per 1M vs $0.75 uncached) plus storage ($0.50 per 1M token-hours). If the
           provider refuses the cache (CacheUnavailable: model without explicit caching, content under the minimum
           4,096 tokens, no storage price) the run FALLS BACK to implicit caching: the identical prefix (system +
           transcript) is sent in every call and the run document says ``cache.mode = "implicit_fallback"``.
  call 1   ws3_payoffs (thinking HIGH, once per episode): EVERY good payoff beat: line, type, emotion, intensity
           1-5, reason, lands_on_screen. Line ids only. High because a beat missed here can never become a clip
           and the call runs once; v1's whole-episode discover at high measured 25-32k output tokens.
  call 2   ws3_hook (thinking MEDIUM, one call per payoff): hook_line, needs_from_line, hook_emotion,
           would_stop_scrolling, reason. Medium: weak hooks were the first GT round's main problem and the choice
           weighs several candidate lines against the setup the payoff needs; the call is local and bounded.
  call 3   ws3_clip (thinking LOW, one call per (hook, payoff)): start_line, end_line, <= 3 whole-chunk drops,
           optional cold_open_line, title, emotion_arc, watch_whole_clip, reason. Low: the ends are mostly fixed
           by the hook and the payoff; what remains is assembly and a title.
  code     each clip is built with the EXISTING cut builder (webseries.build_clip: first-word start <= 0.3 s, shot
           snapping, <= 3 drops on shot changes, reaction hold, no replayed cold open) and checked by the existing
           hard rules; clips whose source spans overlap > 50% are de-duplicated (higher intensity wins); ranking is
           intensity, then watch_whole_clip, then would_stop_scrolling, then source order. The run document uses the
           shared schema (1.1, kind proposals) plus the ws3 fields (payoff, hook, emotion, intensity,
           would_stop_scrolling, watch_whole_clip, reasons) and an ``episode_summary``.
  spend    one paid attempt per call (no retries, no schema retries); the spend guard (``--max-ledger-inr``) is
           checked once up front (cache + call 1 + one payoff's calls 2-3, worst case: never pay for call 1 when
           nothing could follow it) and before every billed call with that call's worst-case rupees; after call 1
           the payoff list is cut (lowest intensity first) to what the limit still allows.

CLI::

    python -m shotvi.select.ws3 5HqkHAZfzos qIdAPG8UulE --out runs/ws3 --dry-run           # free projection
    SHOTVI_SPEND_SOURCE=ws3 python -m shotvi.select.ws3 5HqkHAZfzos --out runs/ws3 --max-ledger-inr <limit>
    python tools/preview_clips.py runs/ws3 --top 50                                        # review page

The dry run makes NO network call (no countTokens either): token counts come from the rendered prompt sizes with
chars-per-token calibrated on runs/v1_test (measured discover input tokens vs its prompt length, per episode when
that episode was in v1_test), and output sizes from v1_test's measured discover / review outputs.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

from shotvi.llm.base import BudgetExceeded, CacheUnavailable, LLMError, SafetyBlocked
from shotvi.select import webseries as ws

ROOT = ws.ROOT
PIPELINE_VERSION = "ws3/0.1"
ROLES = ("ws3_payoffs", "ws3_hook", "ws3_clip")
CONTEXT_ROLE = "ws3_context"
EXIT_SAFETY_BLOCKED = 4

_FLASH = {"provider": "gemini", "model": "gemini-3.8-flash"}
DEFAULTS: dict = {
    "models": {
        # thinking levels: see the module docstring. max_output_tokens = worst-case billed output (visible + thinking)
        "ws3_payoffs": {**_FLASH, "params": {"thinking_level": "high", "max_output_tokens": 32768}},
        "ws3_hook": {**_FLASH, "params": {"thinking_level": "medium", "max_output_tokens": 8192}},
        "ws3_clip": {**_FLASH, "params": {"thinking_level": "low", "max_output_tokens": 4096}},
    },
    "prompts": {CONTEXT_ROLE: "ws3_context", "ws3_payoffs": "ws3_payoffs", "ws3_hook": "ws3_hook",
                "ws3_clip": "ws3_clip"},
    "cache": {"mode": "explicit", "ttl_s": 3600},      # "explicit" (fallback to implicit on refusal) | "implicit"
    "payoffs": {"min_intensity": 3, "max_payoffs": 20,  # spend bound: highest intensity first (transcript order ties)
                "max_end_after_payoff_lines": 3, "max_end_after_payoff_s": 10.0},
    "cut": copy.deepcopy(ws.DEFAULTS["cut"]),           # the v1 cut builder's settings, unchanged
    "guardrails": copy.deepcopy(ws.DEFAULTS["guardrails"]),
    "rank": {"strong_min_intensity": 4, "verdict_strong_min": 3},
    "speaker_names": True,                              # corrected names when runs/speaker_fix/<id>.json exists
    "budget_usd": 1.0,
    "calibrate_from": "runs/v1_test",
}

# ---------------------------------------------------------------------------------------------------- projection
# Expected billed output (visible + thinking) per call, from v1_test (2026-10-13, 4 episodes, gemini-3.8-flash):
# whole-episode discover at HIGH: 25.2k / 28.8k / 31.5k / 32.2k (22-30k of it thinking) -> call 1 ~28k, worst = its
# max_output_tokens; review at MEDIUM: 3.2k-9.3k for 4-11 clips (~0.6-0.85k per clip + base) -> a one-item medium
# call ~2k; LOW: no web-series measurement, the podcast pipeline's low calls ran 0.3-1.5k -> 1.2k.
PROJ_OUT = {"ws3_payoffs": 28000, "ws3_hook": 2000, "ws3_clip": 1200}
# chars per token of the rendered transcript (Telugu lines with ASCII ids/times/names), fallback when the episode has
# no v1_test calibration: v1_test gave 1.99 / 1.97 / 1.98 / 1.90 (prompt tokens minus the English system part, over
# the user part's chars); English prose ~4 chars per token
TRANSCRIPT_CHARS_PER_TOKEN = 1.95
TELUGU_CHARS_PER_TOKEN = 1.2
ENGLISH_CHARS_PER_TOKEN = 4.0
PAYOFFS_PER_SEC = 1.0 / 105.0        # founder: ~1 payoff per 1.5-2 min of episode
PROJ_N = (8, 12, 16)                 # the N the projection table always shows
EMOTION_CATEGORY = {"laugh": "humour", "aww": "emotional", "tears": "emotional", "shock": "reveal",
                    "tension": "story", "other": "other"}


class EpisodeStop(RuntimeError):
    """The episode cannot continue (call 1 failed / was blocked)."""


# ---------------------------------------------------------------------------------------------------- schemas
_STR = {"type": "string"}
_YN = {"type": "string", "enum": ["yes", "no"]}
PAYOFFS_SCHEMA = {
    "type": "object",
    "properties": {
        "episode_notes": _STR,
        "payoffs": {"type": "array", "items": {"type": "object", "properties": {
            "line": _STR,
            "type": {"type": "string", "enum": ["punchline", "reveal", "quotable_line", "emotional_peak", "other"]},
            "emotion": {"type": "string", "enum": ["laugh", "aww", "shock", "tension", "tears", "other"]},
            "intensity": {"type": "integer", "minimum": 1, "maximum": 5},
            "reason": _STR,
            "lands_on_screen": {"type": "boolean"},
        }, "required": ["line", "type", "emotion", "intensity", "reason", "lands_on_screen"]}},
    },
    "required": ["payoffs"],
}
HOOK_SCHEMA = {
    "type": "object",
    "properties": {
        "hook_line": _STR, "needs_from_line": _STR,
        "hook_emotion": {"type": "string", "enum": ["curiosity", "laugh", "tension", "aww", "shock", "none"]},
        "would_stop_scrolling": _YN, "reason": _STR,
    },
    "required": ["hook_line", "needs_from_line", "hook_emotion", "would_stop_scrolling", "reason"],
}
CLIP_SCHEMA = {
    "type": "object",
    "properties": {
        "start_line": _STR, "end_line": _STR,
        "drops": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {
            "first_line": _STR, "last_line": _STR, "why": _STR}, "required": ["first_line", "last_line", "why"]}},
        "cold_open_line": _STR, "title": _STR, "emotion_arc": _STR, "watch_whole_clip": _YN, "reason": _STR,
    },
    "required": ["start_line", "end_line", "drops", "cold_open_line", "title", "emotion_arc", "watch_whole_clip",
                 "reason"],
}
SCHEMAS = {"ws3_payoffs": PAYOFFS_SCHEMA, "ws3_hook": HOOK_SCHEMA, "ws3_clip": CLIP_SCHEMA}


# ---------------------------------------------------------------------------------------------------- helpers
def est_tokens(text: str, transcript_ratio: float | None = None) -> int:
    """Offline token estimate. ``transcript_ratio``: chars per token for a rendered transcript (calibrated);
    otherwise Telugu-script chars at TELUGU_CHARS_PER_TOKEN and the rest at ENGLISH_CHARS_PER_TOKEN."""
    if transcript_ratio:
        return int(len(text) / transcript_ratio) + 1
    te = sum(1 for ch in text if "ఀ" <= ch <= "౿")
    return int(te / TELUGU_CHARS_PER_TOKEN + (len(text) - te) / ENGLISH_CHARS_PER_TOKEN) + 1


def spec_of(cfg: dict, role: str):
    from shotvi.llm.base import ModelSpec
    return ModelSpec.from_dict(cfg["models"][role])


def prompt_name(cfg: dict, role: str) -> str:
    return (cfg.get("prompts") or {}).get(role) or role


def load_p(cfg: dict, role: str):
    from shotvi.select.prompts import load_prompt
    return load_prompt(prompt_name(cfg, role))


def context_prompt(ep: ws.Episode, cfg: dict) -> tuple[str, str]:
    """(system, prefix) of the cached context: the editor persona and the whole transcript as numbered lines."""
    from shotvi.select.lines import ASR_LEGEND, render
    p = load_p(cfg, CONTEXT_ROLE)
    names = ep.names if cfg.get("speaker_names") else None
    return p.system, p.render(episode_len=ws.fmt(ep.duration), n_lines=len(ep.lines), asr_legend=ASR_LEGEND,
                              transcript=render(ep.lines, None, names))


def task_text(cfg: dict, role: str, **kw) -> str:
    """The per-call text: the task prompt's instruction (its SYSTEM section) followed by its rendered USER part. In
    explicit-cache mode this is the whole request; the persona and transcript come from the cache."""
    p = load_p(cfg, role)
    return p.system.strip() + "\n\n" + p.render(**kw).strip()


def line_text(ep: ws.Episode, lid: str | None) -> str:
    r = ep.by_id.get(lid) if lid else None
    return ws.raw_text(ep, r) if r is not None and r.get("words") else (r["text"] if r else "")


def speech_pos(ep: ws.Episode, lid) -> int | None:
    """Position (ep.lines) of a spoken line id, None when unknown or not speech."""
    n = ws.norm_id(lid)
    r = ep.by_id.get(n) if n else None
    return ep.pos[n] if r is not None and ep.is_speech(r) else None


def prev_speech(ep: ws.Episode, pos: int):
    for k in range(pos, -1, -1):
        if ep.is_speech(ep.lines[k]):
            return ep.lines[k]
    return None


# ---------------------------------------------------------------------------------------------------- cache handle
class ContextCache:
    """The cached (system, prefix) and how calls reference it: ``mode`` explicit (a provider cache, created lazily
    before the first uncached call), implicit (prefix sent in every call) or implicit_fallback (explicit refused).
    ``key`` = hash of the cached content: the response cache is keyed by it, never by the provider's cache name."""

    def __init__(self, system: str, prefix: str, spec, cfg: dict, log=print):
        self.system, self.prefix, self.spec, self.cfg, self.log = system, prefix, spec, cfg, log
        self.key = hashlib.sha256(f"{spec.model}\n{system}\n{prefix}".encode("utf-8")).hexdigest()[:24]
        self.mode = cfg["cache"].get("mode", "explicit")
        self.name: str | None = None
        self.info: dict = {}
        self.fallback_reason: str | None = None
        self.created_at: float | None = None

    def ensure(self, llm) -> None:
        """Create the provider cache if this run uses explicit caching and none exists yet."""
        if self.mode != "explicit" or self.name:
            return
        from shotvi.llm.client import get_provider
        prov = get_provider(self.spec.provider)
        try:
            self.info = prov.create_cache(self.spec, self.system, self.prefix, int(self.cfg["cache"]["ttl_s"]),
                                          display_name=f"shotvi ws3 {self.key[:8]}")
        except CacheUnavailable as e:
            self.mode, self.fallback_reason = "implicit_fallback", str(e)
            self.log(f"explicit context cache unavailable ({e}); FALLING BACK to implicit caching: the identical "
                     f"prefix (system + transcript) is sent in every call")
            return
        self.name, self.created_at = self.info["name"], time.time()
        llm.ledger.record_fixed("ws3_cache", self.spec, float(self.info.get("storage_usd") or 0.0),
                                note=f"explicit cache storage, {self.info['tokens']} tokens x {self.info['ttl_s']} s",
                                tokens=int(self.info["tokens"]))
        self.log(f"explicit context cache {self.name}: {self.info['tokens']} tokens, TTL {self.info['ttl_s']} s, "
                 f"storage ${self.info.get('storage_usd', 0):.4f}")

    def close(self) -> dict:
        out = {"mode": self.mode, "key": self.key, **({"fallback_reason": self.fallback_reason}
                                                       if self.fallback_reason else {})}
        if self.name:
            from shotvi.llm.client import get_provider
            ok = get_provider(self.spec.provider).delete_cache(self.name)
            out.update(name=self.name, tokens=self.info.get("tokens"), ttl_s=self.info.get("ttl_s"),
                       storage_usd=self.info.get("storage_usd"), deleted=bool(ok),
                       lived_s=round(time.time() - (self.created_at or time.time()), 1))
            self.name = None
        return out

    # what a call sends
    def request(self, task: str) -> dict:
        if self.mode == "explicit":
            return {"system": "", "prompt": task, "cached_content": {"name": self.name, "key": self.key}}
        return {"system": self.system, "prompt": self.prefix + "\n\n" + task, "cached_content": None}

    def is_hit(self, llm, spec, task: str, schema: dict) -> bool:
        """Is this call's first attempt in the response cache (free)? Mirrors LLMClient.generate_json's key."""
        rq = self.request(task)
        ckey = f"\n<cached_content {self.key}>" if self.mode == "explicit" else ""
        try:
            return llm._cache_get(spec, llm.cache_key(spec, rq["system"], rq["prompt"] + ckey, schema)) is not None
        except Exception:   # noqa: BLE001
            return False


def ask(llm, cache: ContextCache, cfg: dict, role: str, task: str, meta: dict):
    """One call through the context cache: creates the provider cache lazily before the first uncached call."""
    spec = spec_of(cfg, role)
    schema = SCHEMAS[role]
    if not cache.is_hit(llm, spec, task, schema) and not llm.cache_only:
        cache.ensure(llm)
    rq = cache.request(task)
    return llm.generate_json(role, spec, rq["system"], rq["prompt"], schema, meta=meta,
                             cached_content=rq["cached_content"])


# ---------------------------------------------------------------------------------------------------- validation
def validate_payoffs(ep: ws.Episode, disc: dict, cfg: dict) -> tuple[list[dict], list[dict]]:
    """Call 1 answer -> (payoffs kept, rejected). Ids outside the transcript are rejected; an audio-event id is
    mapped to the spoken line before it (flagged); duplicates keep the higher intensity; below min_intensity out;
    capped at max_payoffs (highest intensity first, transcript order within an intensity)."""
    pc = cfg["payoffs"]
    kept, rejected, seen = [], [], {}
    for k, p in enumerate(disc.get("payoffs") or []):
        lid = ws.norm_id(p.get("line"))
        r = ep.by_id.get(lid) if lid else None
        flags = []
        if r is None:
            rejected.append({"payoff": p, "reason": f"unknown line id {p.get('line')!r}"})
            continue
        if not ep.is_speech(r):
            r2 = prev_speech(ep, ep.pos[lid])
            if r2 is None:
                rejected.append({"payoff": p, "reason": f"{lid} is not a spoken line and nothing is spoken before it"})
                continue
            flags.append(f"{lid} is an audio event: mapped to {r2['id']}")
            lid, r = r2["id"], r2
        try:
            inten = int(p.get("intensity"))
        except (TypeError, ValueError):
            rejected.append({"payoff": p, "reason": "intensity is not a number"})
            continue
        inten = max(1, min(5, inten))
        if inten < pc["min_intensity"]:
            rejected.append({"payoff": p, "reason": f"intensity {inten} < {pc['min_intensity']}"})
            continue
        item = {"line": lid, "text": line_text(ep, lid), "type": p.get("type") or "other",
                "emotion": p.get("emotion") or "other", "intensity": inten, "reason": str(p.get("reason") or ""),
                "lands_on_screen": bool(p.get("lands_on_screen")), "order": k, "flags": flags}
        if lid in seen:
            other = seen[lid]
            if inten > other["intensity"]:
                rejected.append({"payoff": other, "reason": f"duplicate of {lid} (lower intensity)"})
                kept[kept.index(other)] = item
                seen[lid] = item
            else:
                rejected.append({"payoff": p, "reason": f"duplicate of {lid}"})
            continue
        seen[lid] = item
        kept.append(item)
    kept.sort(key=lambda x: (-x["intensity"], x["order"]))
    cap = int(pc["max_payoffs"])
    if len(kept) > cap:
        for x in kept[cap:]:
            rejected.append({"payoff": x, "reason": f"beyond max_payoffs {cap} (intensity {x['intensity']})"})
        kept = kept[:cap]
    kept.sort(key=lambda x: x["order"])
    for n, x in enumerate(kept):
        x["id"] = f"p{n + 1:02d}"
    return kept, rejected


def validate_hook(ep: ws.Episode, payoff: dict, h: dict) -> dict:
    """Call 2 answer -> hook dict, or raises ValueError. Both ids must be spoken lines before the payoff;
    needs_from_line <= hook_line (a later needs_from is clamped to the hook with a flag)."""
    pp = ep.pos[payoff["line"]]
    ph, pn = speech_pos(ep, h.get("hook_line")), speech_pos(ep, h.get("needs_from_line"))
    if ph is None:
        raise ValueError(f"hook_line {h.get('hook_line')!r} is not a spoken line of the transcript")
    if pn is None:
        raise ValueError(f"needs_from_line {h.get('needs_from_line')!r} is not a spoken line of the transcript")
    if ph >= pp:
        raise ValueError(f"hook_line {ep.lines[ph]['id']} is not before the payoff {payoff['line']}")
    flags = []
    if pn > ph:
        flags.append(f"needs_from_line {ep.lines[pn]['id']} came after the hook; clamped to the hook")
        pn = ph
    return {"hook_line": ep.lines[ph]["id"], "hook_text": line_text(ep, ep.lines[ph]["id"]),
            "needs_from_line": ep.lines[pn]["id"], "needs_from_text": line_text(ep, ep.lines[pn]["id"]),
            "hook_emotion": h.get("hook_emotion") or "none",
            "would_stop_scrolling": "yes" if str(h.get("would_stop_scrolling")).lower() == "yes" else "no",
            "reason": str(h.get("reason") or ""), "flags": flags}


def validate_clip(ep: ws.Episode, payoff: dict, hook: dict, c: dict, cfg: dict) -> dict:
    """Call 3 answer -> moment for the cut builder (+ the ws3 fields), or raises ValueError. start_line: a spoken
    line at or before the hook; end_line: the payoff line or a spoken line within a few lines / seconds after it
    (later ends are pulled back to the payoff with a flag); drops and the cold open are left to the builder."""
    pc = cfg["payoffs"]
    pp, ph = ep.pos[payoff["line"]], ep.pos[hook["hook_line"]]
    ps, pe = speech_pos(ep, c.get("start_line")), speech_pos(ep, c.get("end_line"))
    flags = []
    if ps is None:
        raise ValueError(f"start_line {c.get('start_line')!r} is not a spoken line of the transcript")
    if pe is None:
        raise ValueError(f"end_line {c.get('end_line')!r} is not a spoken line of the transcript")
    if ps > ph:
        flags.append(f"start_line {ep.lines[ps]['id']} came after the hook {hook['hook_line']}; moved to the hook")
        ps = ph
    if pe < pp:
        flags.append(f"end_line {ep.lines[pe]['id']} came before the payoff; moved to the payoff")
        pe = pp
    else:
        n_lines = sum(1 for r in ep.lines[pp + 1:pe + 1] if ep.is_speech(r))
        gap_s = ep.w1(ep.lines[pe]) - ep.w1(ep.lines[pp])
        if n_lines > pc["max_end_after_payoff_lines"] or gap_s > pc["max_end_after_payoff_s"]:
            flags.append(f"end_line {ep.lines[pe]['id']} is {n_lines} lines / {gap_s:.0f} s after the payoff; "
                         f"moved to the payoff")
            pe = pp
    drops = []
    for d in c.get("drops") or []:
        a, b = ws.norm_id(d.get("first_line")), ws.norm_id(d.get("last_line") or d.get("first_line"))
        if a not in ep.by_id or b not in ep.by_id:
            flags.append(f"drop {d.get('first_line')}..{d.get('last_line')}: unknown line id; ignored")
            continue
        drops.append({"first_line": a, "last_line": b, "why": str(d.get("why") or "")})
    col = ws.norm_id(c.get("cold_open_line")) if str(c.get("cold_open_line") or "").strip() else None
    if col is not None and col not in ep.by_id:
        flags.append(f"cold_open_line {c.get('cold_open_line')!r}: unknown line id; ignored")
        col = None
    title = " ".join(str(c.get("title") or "").split())
    moment = {"purpose": "standalone", "start_line": ep.lines[ps]["id"], "end_line": ep.lines[pe]["id"],
              "cold_open_lines": [col] if col else [], "drops": drops[:3], "strength": payoff["intensity"],
              "payoff_line": payoff["text"], "payoff_type": payoff["type"]}
    return {"moment": moment, "title": title, "emotion_arc": str(c.get("emotion_arc") or ""),
            "watch_whole_clip": "yes" if str(c.get("watch_whole_clip")).lower() == "yes" else "no",
            "reason": str(c.get("reason") or ""), "flags": flags}


# ---------------------------------------------------------------------------------------------------- rank / dedupe
def rank_key(x: dict):
    return (-x["payoff"]["intensity"], x["clip3"]["watch_whole_clip"] != "yes",
            x["hook"]["would_stop_scrolling"] != "yes", x["clip"]["time_segments"][-1]["start"])


def dedupe(cands: list[dict], dup_overlap: float) -> tuple[list[dict], list[dict]]:
    """Clips whose source spans overlap more than ``dup_overlap`` of the shorter: the higher intensity stays (ties:
    watch_whole_clip yes, then would_stop_scrolling yes, then the earlier one)."""
    kept, rejected = [], []
    for x in sorted(cands, key=rank_key):
        dup = next((y for y in kept if ws.overlap_frac(x["clip"], y["clip"]) > dup_overlap), None)
        if dup:
            rejected.append({**x, "reject_reasons": [f"D1: overlaps {dup['id']} "
                                                     f"({ws.overlap_frac(x['clip'], dup['clip']):.0%} of the shorter; "
                                                     f"intensity {x['payoff']['intensity']} vs "
                                                     f"{dup['payoff']['intensity']})"]})
            continue
        kept.append(x)
    return kept, rejected


def rank(kept: list[dict]) -> list[dict]:
    return sorted(kept, key=rank_key)


def tier_of(x: dict, cfg: dict) -> str:
    strong = (x["payoff"]["intensity"] >= cfg["rank"]["strong_min_intensity"]
              and x["clip3"]["watch_whole_clip"] == "yes" and x["hook"]["would_stop_scrolling"] == "yes"
              and not [f for f in x["clip"].get("flashes") or [] if not f["fixed"]])
    return "recommended" if strong else "worth_a_look"


def derived_scores(x: dict) -> dict:
    """Schema scores derived from the three calls' judgements (there is NO review call in ws3): payoff and
    value_emotion = intensity; hook from would_stop_scrolling; standalone from watch_whole_clip."""
    inten = float(x["payoff"]["intensity"])
    hook = 4.5 if x["hook"]["would_stop_scrolling"] == "yes" else 2.5
    stand = 4.0 if x["clip3"]["watch_whole_clip"] == "yes" else 3.0
    return {"hook": hook, "standalone": stand, "payoff": inten, "value_emotion": inten,
            "overall": round((hook + stand + inten + inten) / 4, 2)}


# ---------------------------------------------------------------------------------------------------- projection
def calibration_ratio(vid: str, ep: ws.Episode, ix, cfg: dict, log=print) -> tuple[float, str]:
    """Chars per token of this episode's rendered transcript, from a v1 run's measured discover input tokens (its
    prompt is rebuilt offline: tokens minus the English system part, over the user part's chars). Fallback: the
    v1_test mean."""
    d = cfg.get("calibrate_from")
    p = ROOT / d / f"{vid}.json" if d else None
    if p and p.exists():
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            tok = int(doc["cost"]["by_role"]["ws_discover"]["input_tokens"])
            pf = (doc.get("pipeline") or {}).get("prompt_files") or {}
            v1cfg = ws.deep_merge(ws.DEFAULTS, {"prompts": {k: v for k, v in pf.items() if k in ws.PROMPTS},
                                                "speaker_names": bool(((doc.get("pipeline") or {}).get("config") or {})
                                                                      .get("speaker_names"))})
            s, u = ws.discover_prompt(ep, ix, v1cfg)
            ratio = len(u) / max(1, tok - est_tokens(s))
            if 1.0 <= ratio <= 4.0:
                return ratio, f"{d}/{vid}.json: {tok} tokens for its {len(s) + len(u)} chars discover prompt"
        except Exception as e:   # noqa: BLE001 (projection only)
            log(f"calibration from {p} failed ({e}); using the default ratio")
    return TRANSCRIPT_CHARS_PER_TOKEN, "default (v1_test mean)"


def project(ep: ws.Episode, ix, cfg: dict, system: str, prefix: str, log=print, n_known: int | None = None,
            vid: str | None = None) -> dict:
    """Offline rupee projection of one episode: the cache, call 1, calls 2 and 3 per payoff; a table for N in
    PROJ_N plus the length-based estimate (or the known N when call 1 is already cached)."""
    from shotvi.llm.pricing import cache_storage_usd, cost_usd
    ratio, src = calibration_ratio(vid or ep.video_id, ep, ix, cfg, log)
    t_sys = est_tokens(system)
    t_prefix = est_tokens(prefix, ratio)
    t_cache = t_sys + t_prefix
    m1 = spec_of(cfg, "ws3_payoffs")
    explicit = cfg["cache"].get("mode", "explicit") == "explicit"
    ttl_h = float(cfg["cache"]["ttl_s"]) / 3600.0
    storage = (cache_storage_usd(m1.model, t_cache, ttl_h) or 0.0) if explicit else 0.0
    # the pricing page lists no charge for creating a cache; the worst case still allows one write at the input rate
    write_worst = (cost_usd(m1.model, t_cache, 0) or 0.0) if explicit else 0.0
    # sample task texts (ids / quoted lines are small; the longest plausible line is used)
    long_line = max((line_text(ep, r["id"]) for r in ep.lines if ep.is_speech(r)), key=len, default="")
    tasks = {"ws3_payoffs": task_text(cfg, "ws3_payoffs", episode_len=ws.fmt(ep.duration), n_lines=len(ep.lines)),
             "ws3_hook": task_text(cfg, "ws3_hook", payoff_line="L0000", payoff_type="punchline", emotion="laugh",
                                   intensity=5, payoff_text=long_line, payoff_reason="x" * 200),
             "ws3_clip": task_text(cfg, "ws3_clip", payoff_line="L0000", payoff_type="punchline", emotion="laugh",
                                   intensity=5, payoff_text=long_line, payoff_reason="x" * 200, hook_line="L0000",
                                   hook_text=long_line, hook_emotion="curiosity", would_stop_scrolling="yes",
                                   needs_from_line="L0000", hook_reason="x" * 200)}
    calls = {}
    for role in ROLES:
        spec = spec_of(cfg, role)
        t_task = est_tokens(tasks[role])
        in_tok = t_task + t_cache
        exp_out, max_out = PROJ_OUT[role], int(spec.params.get("max_output_tokens", 8192))
        # the prefix is billed at the cached rate (explicit: always; implicit: when the hit lands, which is the
        # expected case); the implicit worst case pays the prefix at the full input rate
        usd_exp = cost_usd(spec.model, in_tok, exp_out, cached_tokens=t_cache) or 0.0
        usd_max = cost_usd(spec.model, in_tok, max_out, cached_tokens=t_cache if explicit else 0) or 0.0
        calls[role] = {"model": spec.model, "thinking_level": spec.params.get("thinking_level"),
                       "in_uncached_tokens": t_task, "in_cached_tokens": t_cache, "out_expected": exp_out,
                       "out_max": max_out, "inr_expected": round(ws.inr(usd_exp), 3),
                       "inr_worst": round(ws.inr(usd_max), 3)}
    n_est = n_known if n_known is not None else max(1, int(round(ep.duration * PAYOFFS_PER_SEC)))
    cap = int(cfg["payoffs"]["max_payoffs"])
    c1, c2, c3 = calls["ws3_payoffs"], calls["ws3_hook"], calls["ws3_clip"]
    fixed_exp = ws.inr(storage) + c1["inr_expected"]
    fixed_worst = ws.inr(storage + write_worst) + c1["inr_worst"]

    def row(n):
        n_eff = min(n, cap)
        return {"n": n, "n_processed": n_eff,
                "inr_expected": round(fixed_exp + n_eff * (c2["inr_expected"] + c3["inr_expected"]), 2),
                "inr_worst": round(fixed_worst + n_eff * (c2["inr_worst"] + c3["inr_worst"]), 2)}
    table = [row(n) for n in PROJ_N]
    est_row = {**row(n_est), "label": "known from the cached call 1" if n_known is not None else
               f"estimate: ~1 payoff per {1 / PAYOFFS_PER_SEC / 60:.2f} min"}
    cap_row = row(cap)
    return {"tokens": {"cache_system": t_sys, "cache_transcript": t_prefix, "cache_total": t_cache,
                       "chars_per_token": round(ratio, 3), "calibration": src,
                       "transcript_chars": len(prefix), "n_lines": len(ep.lines)},
            "cache": {"mode": "explicit" if explicit else "implicit", "ttl_s": cfg["cache"]["ttl_s"],
                      "storage_inr": round(ws.inr(storage), 3), "write_inr_worst_case": round(ws.inr(write_worst), 3),
                      "min_tokens": 4096, "under_minimum": t_cache < 4096},
            "calls": calls, "table": table, "n_estimate": est_row, "n_cap": cap_row,
            "episode": {"inr_expected": est_row["inr_expected"], "inr_worst": cap_row["inr_worst"],
                        "inr_worst_at_16": table[-1]["inr_worst"]}}


def projection_text(vid: str, proj: dict) -> str:
    t, c = proj["tokens"], proj["cache"]
    out = [f"{vid}: cache {t['cache_total']} tokens ({t['cache_transcript']} transcript + {t['cache_system']} "
           f"persona; {t['chars_per_token']} chars/token, {t['calibration']}); {c['mode']} cache, TTL {c['ttl_s']} s, "
           f"storage ₹{c['storage_inr']:.2f}" + (" [UNDER the 4,096-token minimum: would fall back to implicit]"
                                                  if c["under_minimum"] else "")]
    for role, x in proj["calls"].items():
        out.append(f"  {role:12} {x['thinking_level']:6} in {x['in_uncached_tokens']} + {x['in_cached_tokens']} cached"
                   f" | out {x['out_expected']} exp / {x['out_max']} max | ₹{x['inr_expected']:.2f} exp / "
                   f"₹{x['inr_worst']:.2f} worst per call")
    out.append("  N payoffs | expected ₹ | worst ₹")
    for r in proj["table"] + [proj["n_estimate"], proj["n_cap"]]:
        lab = r.get("label") or ("cap" if r is proj["n_cap"] else "")
        out.append(f"  {r['n']:>9} | {r['inr_expected']:>10.2f} | {r['inr_worst']:>7.2f}  {lab}")
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------------- the episode
def run_episode(vid: str, cfg: dict, llm, guard: ws.Guard, out_dir: Path, log=print, dry_run: bool = False) -> dict:
    t0 = time.time()
    ep, ix, tr, tp = ws.load_episode(vid, with_loudness=not dry_run, speaker_names=bool(cfg.get("speaker_names")),
                                     require_names=False)
    labels = "names" if ep.names else "scribe"
    system, prefix = context_prompt(ep, cfg)
    spec1 = spec_of(cfg, "ws3_payoffs")
    cache = ContextCache(system, prefix, spec1, cfg, log)
    task1 = task_text(cfg, "ws3_payoffs", episode_len=ws.fmt(ep.duration), n_lines=len(ep.lines))
    c1_cached = cache.is_hit(llm, spec1, task1, PAYOFFS_SCHEMA)
    n_known = None
    if c1_cached:
        try:
            rq = cache.request(task1)
            hit = llm._cache_get(spec1, llm.cache_key(spec1, rq["system"], rq["prompt"] +
                                                      (f"\n<cached_content {cache.key}>" if cache.mode == "explicit"
                                                       else ""), PAYOFFS_SCHEMA))
            n_known = len(validate_payoffs(ep, json.loads(hit["text"]), cfg)[0])
        except Exception:   # noqa: BLE001
            n_known = None
    proj = project(ep, ix, cfg, system, prefix, log, n_known=n_known, vid=vid)
    log(projection_text(vid, proj))
    if dry_run:
        res = {"video_id": vid, "dry_run": True, "projection": proj, "call1_cached": c1_cached,
               "speaker_labels": labels, "would_bill_inr_expected": proj["episode"]["inr_expected"],
               "would_bill_inr_worst": proj["episode"]["inr_worst"],
               "prompt_versions": {r: load_p(cfg, r).tag for r in (CONTEXT_ROLE,) + ROLES}}
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"dry_run_{vid}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"{vid}: DRY RUN: would bill ₹{res['would_bill_inr_expected']:.2f} expected "
            f"(worst ₹{res['would_bill_inr_worst']:.2f} at the {cfg['payoffs']['max_payoffs']}-payoff cap); "
            f"call 1 {'cached (free)' if c1_cached else 'not cached'}; nothing was called")
        return res

    # spend guard: before every billed attempt, with that call's worst case; once up front for the episode
    worst = {r: proj["calls"][r]["inr_worst"] for r in ROLES}
    llm.before_call = lambda role, _spec: guard.check(f"{vid} {role}", worst.get(role, max(worst.values())))
    if not c1_cached:
        # never pay for call 1 when not even one payoff could then be processed; after call 1 the payoff list is cut
        # to what the limit still allows (the N estimate is only a projection, not a reason to refuse)
        one = proj["cache"]["storage_inr"] + proj["cache"]["write_inr_worst_case"] + worst["ws3_payoffs"] \
            + worst["ws3_hook"] + worst["ws3_clip"]
        guard.check(f"{vid} episode (cache + call 1 + one payoff's calls 2-3, worst case)", one)
    debug = {"video_id": vid, "projection": proj, "speaker_labels": labels, "warnings": [], "payoff_calls": []}
    try:
        # ------------------------------------------------------------------ call 1: payoffs
        try:
            disc = ask(llm, cache, cfg, "ws3_payoffs", task1, {"video": vid})
        except SafetyBlocked as e:
            raise EpisodeStop(f"{vid}: ws3_payoffs was blocked by Gemini's safety check (blockReason {e.reason}); "
                              f"no clips for this episode") from e
        except LLMError as e:
            raise EpisodeStop(f"{vid}: ws3_payoffs failed ({e}); one attempt per call, no retry") from e
        debug["payoffs_raw"] = disc
        payoffs, rej_payoffs = validate_payoffs(ep, disc, cfg)
        log(f"{vid}: {len(disc.get('payoffs') or [])} payoffs listed, {len(payoffs)} kept "
            f"(by intensity: {dict(sorted(Counter(p['intensity'] for p in payoffs).items(), reverse=True))})")
        # after call 1 the count is known: cut the list to what the limit still allows (highest intensity first)
        per = worst["ws3_hook"] + worst["ws3_clip"]
        if guard.limit is not None and payoffs:
            from shotvi.llm.spend import total_inr
            room = guard.limit - total_inr()
            n_fit = max(0, int(room // per)) if per > 0 else len(payoffs)
            if n_fit < len(payoffs):
                by_int = sorted(payoffs, key=lambda x: (-x["intensity"], x["order"]))
                drop = by_int[n_fit:]
                for x in drop:
                    rej_payoffs.append({"payoff": x, "reason": f"spend limit: only {n_fit} payoffs fit "
                                                               f"(₹{per:.2f} worst case each)"})
                payoffs = [x for x in payoffs if x not in drop]
                debug["warnings"].append(f"spend limit cut the payoff list to {n_fit}")
                log(f"{vid}: spend limit: processing {n_fit} of {len(by_int)} payoffs")
        # ------------------------------------------------------------------ calls 2 and 3 per payoff
        cands, rejected = [], []
        for p in payoffs:
            rec = {"payoff": p}
            debug["payoff_calls"].append(rec)
            try:
                h_raw = ask(llm, cache, cfg, "ws3_hook", task_text(cfg, "ws3_hook", payoff_line=p["line"],
                                                                   payoff_type=p["type"], emotion=p["emotion"],
                                                                   intensity=p["intensity"], payoff_text=p["text"],
                                                                   payoff_reason=p["reason"]),
                            {"video": vid, "payoff": p["line"]})
                rec["hook_raw"] = h_raw
                hook = validate_hook(ep, p, h_raw)
            except (LLMError, ValueError) as e:   # SafetyBlocked is an LLMError: this payoff is skipped
                rec["error"] = f"ws3_hook: {e}"
                rejected.append({"id": p["id"], "payoff": p, "reject_reasons": [f"H1: {e}"]})
                log(f"  {p['id']} {p['line']}: no hook: {e}")
                continue
            rec["hook"] = hook
            try:
                c_raw = ask(llm, cache, cfg, "ws3_clip", task_text(cfg, "ws3_clip", payoff_line=p["line"],
                                                                   payoff_type=p["type"], emotion=p["emotion"],
                                                                   intensity=p["intensity"], payoff_text=p["text"],
                                                                   payoff_reason=p["reason"],
                                                                   hook_line=hook["hook_line"],
                                                                   hook_text=hook["hook_text"],
                                                                   hook_emotion=hook["hook_emotion"],
                                                                   would_stop_scrolling=hook["would_stop_scrolling"],
                                                                   needs_from_line=hook["needs_from_line"],
                                                                   hook_reason=hook["reason"]),
                            {"video": vid, "payoff": p["line"], "hook": hook["hook_line"]})
                rec["clip_raw"] = c_raw
                c3 = validate_clip(ep, p, hook, c_raw, cfg)
            except (LLMError, ValueError) as e:
                rec["error"] = f"ws3_clip: {e}"
                rejected.append({"id": p["id"], "payoff": p, "hook": hook, "reject_reasons": [f"C1: {e}"]})
                log(f"  {p['id']} {p['line']}: no clip: {e}")
                continue
            rec["clip3"] = c3
            try:
                clip = ws.build_clip(ep, c3["moment"], cfg["cut"])
            except ws.Reject as e:
                rejected.append({"id": p["id"], "payoff": p, "hook": hook, "clip3": c3,
                                 "reject_reasons": [f"B1: {e}"]})
                log(f"  {p['id']} {p['line']}: rejected by the cut builder: {e}")
                continue
            rr = ws.hard_rules(ep, clip, cfg)
            if rr:
                rejected.append({"id": p["id"], "payoff": p, "hook": hook, "clip3": c3, "clip": clip,
                                 "reject_reasons": rr})
                log(f"  {p['id']} {p['line']}: rejected: {rr}")
                continue
            cands.append({"id": p["id"], "payoff": p, "hook": hook, "clip3": c3, "clip": clip})
            log(f"  {p['id']} {p['line']}: clip {clip['start_line']}..{clip['end_line']} plays "
                f"{clip['video_duration']:.1f} s" + (", cold open" if clip["cold_open"] else ""))
    finally:
        debug["cache"] = cache.close()
    kept, dups = dedupe(cands, cfg["guardrails"]["dup_overlap"])
    rejected += dups
    shown = rank(kept)
    line_of_word = {r["words"][0]: r for r in ep.lines if ep.is_speech(r)}
    props = [proposal_doc(ep, x, k + 1, cfg, line_of_word) for k, x in enumerate(shown)]
    rej_docs = [rejected_doc(ep, x, cfg) for x in rejected]
    debug["rejected"] = rejected
    debug["rejected_payoffs"] = rej_payoffs
    doc = run_document(ep, tp, props, rej_docs, cfg, llm, payoffs, rej_payoffs, t0, debug, labels, len(cands))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{vid}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "debug").mkdir(exist_ok=True)
    (out_dir / "debug" / f"{vid}.json").write_text(json.dumps(debug, ensure_ascii=False, indent=1, default=str),
                                                   encoding="utf-8")
    (out_dir / f"{vid}.md").write_text(clips_md(ep, doc), encoding="utf-8")
    s = doc["episode_summary"]
    log(f"{vid}: {s['n_payoffs_listed']} payoffs -> {s['n_clips_built']} clips built -> {s['n_shown']} shown "
        f"({s['n_strong']} strong); verdict {s['verdict']['verdict']}; cache {s['cache']['mode']}; "
        f"${doc['cost']['usd_billed']:.4f} billed -> {out_dir / (vid + '.json')}")
    return doc


# ---------------------------------------------------------------------------------------------------- documents
def proposal_doc(ep: ws.Episode, x: dict, rank_: int, cfg: dict, line_of_word: dict) -> dict:
    c, p, h, c3 = x["clip"], x["payoff"], x["hook"], x["clip3"]
    g = cfg["guardrails"]
    core = ws.core_fields(ep, c["pieces"], g["skip_s"])
    tier = tier_of(x, cfg)
    notes, flags = [], []
    if c["cold_open"]:
        notes.append({"type": "cold_open", "after_piece": 0,
                      "reason": ws.one_line(f"Lifted {', '.join(c['cold_open']['lines'])} "
                                            f"({c['cold_open']['duration']:.1f} s) to the front; skipped in place, "
                                            f"not replayed.")})
    W = ep.words
    for k, (a, b) in enumerate(zip(c["pieces"], c["pieces"][1:])):
        if b["start_word"] > a["end_word"] and W[b["start_word"]]["start"] - W[a["end_word"]]["end"] > g["skip_s"]:
            notes.append({"type": "skip", "after_piece": k,
                          "reason": ws.one_line(f"skips {W[b['start_word']]['start'] - W[a['end_word']]['end']:.0f} s")})
    if core["edited_duration"] > g["soft_max_s"]:
        notes.append({"type": "length_over_soft_max", "reason": f"{core['edited_duration']:.0f} s edited"})
        flags.append("over_soft_max")
    if core["n_blocks"] > g["blocks_flag_above"]:
        flags.append("many_jumps")
    if any(any(t in ev["text"] for t in ws.SONG_TOKENS) and sum(
            max(0.0, min(ev["end"], s["end"]) - max(ev["start"], s["start"])) for s in c["time_segments"]) > 5.0
           for ev in ep.events):
        flags.append("song_overlap")
    title = c3["title"].strip() or ws.one_line(p["text"], 60)
    if not any("ఀ" <= ch <= "౿" for ch in title):
        title = (title + " | తెలుగు")[:100]
    why = c3["reason"] or p["reason"]
    return {"id": x["id"], "pieces": c["pieces"], **core, "rank": rank_, "tier": tier,
            "tier_label": ws.TIER_LABEL.get(tier, tier), "scores": derived_scores(x),
            "scores_source": "ws3: derived from intensity / would_stop_scrolling / watch_whole_clip (no review call)",
            "title": title[:100], "hook_line": ws.hook_line(ep, c["pieces"], line_of_word),
            "payoff_line": ws.one_line(p["text"], 300), "payoff_type": p["type"], "payoff_line_source": "ws3_payoffs",
            "why_it_works": ws.one_line(why), "category": EMOTION_CATEGORY.get(p["emotion"], "other"),
            "standalone_risks": [], "edit_notes": notes, "flags": flags,
            # web-series fields (as in v1)
            "content_type": "web_series", "clip_purpose": "standalone",
            "cold_open": bool(c["cold_open"]), "cold_open_detail": c["cold_open"],
            "cold_open_rejected": c.get("lift_rejected"),
            "time_segments": [{"start": s["start"], "end": s["end"], "kind": s["kind"]} for s in c["time_segments"]],
            "video_duration": c["video_duration"], "start_line": c["start_line"], "end_line": c["end_line"],
            "end_hold": c["end"],
            "chunk_drops": [{"after_line": cu["after_line"], "before_line": cu["before_line"],
                             "removed_lines": cu["removed_lines"], "lift_skip": cu["lift_skip"],
                             "start": cu["start"], "end": cu["end"], "removed_s": cu["removed_s"],
                             "why": cu["why"]} for cu in c["cuts"]],
            "shot_flags": [ws.flash_text(f) for f in c.get("flashes") or [] if not f["fixed"]],
            "build_warnings": c["warnings"],
            # ws3 fields
            "payoff": {k: p[k] for k in ("line", "text", "type", "emotion", "intensity", "reason", "lands_on_screen")},
            "hook": {k: h[k] for k in ("hook_line", "hook_text", "needs_from_line", "needs_from_text", "hook_emotion",
                                       "would_stop_scrolling", "reason")},
            "intensity": p["intensity"], "emotion": p["emotion"], "would_stop_scrolling": h["would_stop_scrolling"],
            "watch_whole_clip": c3["watch_whole_clip"], "emotion_arc": c3["emotion_arc"],
            "clip_reason": c3["reason"],
            "validation_flags": p.get("flags", []) + h.get("flags", []) + c3.get("flags", [])}


def rejected_doc(ep: ws.Episode, x: dict, cfg: dict) -> dict | None:
    base = {"id": x["id"], "tier": "rejected", "reject_reasons": x["reject_reasons"],
            "payoff": {k: x["payoff"].get(k) for k in ("line", "text", "type", "emotion", "intensity", "reason")}}
    if x.get("hook"):
        base["hook"] = {k: x["hook"].get(k) for k in ("hook_line", "needs_from_line", "would_stop_scrolling")}
    c = x.get("clip")
    if c:
        return {**base, "pieces": c["pieces"], **ws.core_fields(ep, c["pieces"], cfg["guardrails"]["skip_s"]),
                "time_segments": c["time_segments"], "video_duration": c["video_duration"],
                "title": (x.get("clip3") or {}).get("title")}
    m = (x.get("clip3") or {}).get("moment")
    sid = ws.norm_id((m or {}).get("start_line")) or x["payoff"]["line"]
    eid = ws.norm_id((m or {}).get("end_line")) or x["payoff"]["line"]
    if sid not in ep.pos or eid not in ep.pos:
        return base
    sp = [r for r in ep.lines[ep.pos[sid]:ep.pos[eid] + 1] if ep.is_speech(r)]
    if not sp:
        return base
    W = ep.words
    pieces = [{"start_word": sp[0]["words"][0], "end_word": sp[-1]["words"][1],
               "start": round(W[sp[0]["words"][0]]["start"], 3), "end": round(W[sp[-1]["words"][1]]["end"], 3)}]
    return {**base, "pieces": pieces, **ws.core_fields(ep, pieces, cfg["guardrails"]["skip_s"])}


def episode_summary(props: list[dict], payoffs: list[dict], rej_payoffs: list[dict], n_built: int, cfg: dict,
                    cache_info: dict, labels: str, notes: str) -> dict:
    th = {"verdict_strong_min": cfg["rank"]["verdict_strong_min"]}
    v = ws.episode_verdict(props, th)
    return {"n_payoffs_listed": len(payoffs) + len(rej_payoffs), "n_payoffs_used": len(payoffs),
            "n_payoffs_rejected": len(rej_payoffs),
            "payoffs_by_type": dict(Counter(p["type"] for p in payoffs)),
            "payoffs_by_emotion": dict(Counter(p["emotion"] for p in payoffs)),
            "payoffs_by_intensity": {str(k): n for k, n in sorted(Counter(p["intensity"] for p in payoffs).items(),
                                                                  reverse=True)},
            "n_clips_built": n_built, "n_shown": len(props),
            "n_strong": sum(p["tier"] == "recommended" for p in props),
            "shown_by_emotion": dict(Counter(p["emotion"] for p in props)),
            "verdict": v, "cache": cache_info, "speaker_labels": labels, "episode_notes": notes}


def run_document(ep, tp, props, rejected, cfg, llm, payoffs, rej_payoffs, t_start, debug, labels,
                 n_built: int) -> dict:
    """``n_built``: clips that passed the cut builder and the hard rules (before de-duplication)."""
    from shotvi.select.context import file_sha256
    summ = llm.ledger.summary()
    notes = ((debug.get("payoffs_raw") or {}).get("episode_notes") or "")
    cache_info = debug.get("cache") or {}
    tpr = Path(tp).resolve()
    try:
        tpath = tpr.relative_to(ROOT).as_posix()
    except ValueError:      # a transcript outside the repo (tests): keep the absolute path
        tpath = tpr.as_posix()
    return {
        "schema_version": "1.1", "kind": "proposals", "video_id": ep.video_id,
        "transcript": {"path": tpath, "sha256": file_sha256(tp),
                       "n_words": len(ep.words), "audio_duration_secs": float(json.loads(
                           Path(tp).read_text(encoding="utf-8")).get("audio_duration_secs") or ep.duration)},
        "derived_params": {"skip_s": cfg["guardrails"]["skip_s"]},
        "run_id": cfg.get("run_id", "ws3"), "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "content_type": "web_series",
        "pipeline": {"version": PIPELINE_VERSION, "git_commit": None,
                     "prompt_versions": {r: load_p(cfg, r).tag for r in (CONTEXT_ROLE,) + ROLES},
                     "prompt_files": {r: prompt_name(cfg, r) for r in (CONTEXT_ROLE,) + ROLES}, "config": cfg,
                     "design": "founder 2026-10-14: payoffs -> hook per payoff -> clip per (hook, payoff); "
                               "transcript sent once and context-cached"},
        "models": [{"role": k, **v} for k, v in cfg["models"].items()],
        "cost": {"input_tokens": summ["input_tokens"], "output_tokens": summ["output_tokens"],
                 "cached_input_tokens": summ["cached_input_tokens"],
                 "usd": summ["usd"], "usd_billed": summ["usd_billed"], "wall_time_secs": round(time.time() - t_start, 1),
                 "n_llm_calls": summ["n_llm_calls"], "by_role": summ["by_role"], "projection": debug["projection"],
                 "cache": cache_info},
        "cutoff": {"show_rule": "ws3_all_built", "strong_min_intensity": cfg["rank"]["strong_min_intensity"],
                   "n_candidates": len(payoffs), "n_passed_hard_rules": n_built, "n_shown": len(props),
                   "n_recommended": sum(p["tier"] == "recommended" for p in props),
                   "n_worth_a_look": sum(p["tier"] == "worth_a_look" for p in props),
                   "n_rejected": len([r for r in rejected if r]), "warnings": debug.get("warnings", [])},
        "episode_summary": episode_summary(props, payoffs, rej_payoffs, n_built, cfg, cache_info, labels, notes),
        "episode_verdict": ws.episode_verdict(props, {"verdict_strong_min": cfg["rank"]["verdict_strong_min"]}),
        "episode_notes": notes,
        "proposals": props,
        "rejected": [r for r in rejected if r],
    }


def clips_md(ep: ws.Episode, doc: dict) -> str:
    s = doc["episode_summary"]
    v = s["verdict"]
    out = [f"# {doc['video_id']} web-series clips, ws3 ({doc['run_id']})\n",
           f"{s['n_payoffs_listed']} payoff beats listed (used {s['n_payoffs_used']}; by intensity "
           f"{s['payoffs_by_intensity']}; by emotion {s['payoffs_by_emotion']}), {s['n_clips_built']} clips built, "
           f"{s['n_shown']} shown, {s['n_strong']} strong candidates. Verdict: {v['verdict'].upper()}. {v['note']}",
           f"Context cache: {s['cache'].get('mode')}"
           + (f" ({s['cache'].get('tokens')} tokens, TTL {s['cache'].get('ttl_s')} s)" if s["cache"].get("tokens") else "")
           + (f"; fallback reason: {s['cache']['fallback_reason']}" if s["cache"].get("fallback_reason") else "")
           + f". Speaker labels: {s['speaker_labels']}. Cost ${doc['cost']['usd']:.4f} "
             f"(${doc['cost']['usd_billed']:.4f} billed this run, {doc['cost']['cached_input_tokens']} cached input "
             f"tokens).\n",
           f"Ranked by payoff intensity, then watch_whole_clip, then would_stop_scrolling. Scores are derived from "
           f"those judgements (no review call).\n"]
    if s.get("episode_notes"):
        out.append(f"Episode notes: {s['episode_notes']}\n")
    for p in doc["proposals"]:
        segs = " + ".join(f"{ws.fmt(x['start'])}-{ws.fmt(x['end'])}" for x in p["time_segments"])
        out.append(f"\n## #{p['rank']} {p['id']} {p['title']} ({p['tier_label']}; intensity {p['intensity']}, "
                   f"{p['emotion']}; stop scrolling {p['would_stop_scrolling']}; watch whole {p['watch_whole_clip']})\n")
        out.append(f"- plays {p['video_duration']:.1f} s: {segs}")
        out.append(f"- hook ({p['hook']['hook_emotion']}): {p['hook']['hook_line']} {p['hook']['hook_text']}")
        if p["hook"]["needs_from_line"] != p["hook"]["hook_line"]:
            out.append(f"- needs from: {p['hook']['needs_from_line']} {p['hook']['needs_from_text']}")
        out.append(f"- payoff ({p['payoff']['type']}, {p['payoff']['emotion']}"
                   f"{', lands on screen' if p['payoff']['lands_on_screen'] else ''}): {p['payoff']['line']} "
                   f"{p['payoff']['text']}")
        out.append(f"- why the payoff lands: {p['payoff']['reason']}")
        out.append(f"- why this hook: {p['hook']['reason']}")
        out.append(f"- why the clip ({p['emotion_arc']}): {p['clip_reason']}")
        if p["cold_open"]:
            out.append(f"- cold open: {p['cold_open_detail']['text']} ({p['cold_open_detail']['duration']} s)")
        elif p.get("cold_open_rejected"):
            out.append(f"- cold open rejected: {p['cold_open_rejected']}")
        eh = p["end_hold"]
        out.append(f"- end: payoff ends {ws.fmt(eh['payoff_end'])}, hold {eh['hold_s']} s "
                   f"({'on a shot cut' if eh['on_shot_cut'] else 'not on a shot cut'}; rule {eh.get('rule', '-')})"
                   + (f"; plays the reaction {eh['reaction']['line']} '{eh['reaction']['text']}'"
                      if eh.get("reaction") else ""))
        for cu in p["chunk_drops"]:
            out.append(f"- cut {ws.fmt(cu['start'])}-{ws.fmt(cu['end'])} ({cu['removed_s']} s): "
                       f"{'cold-open skip ' if cu['lift_skip'] else ''}{cu['why']}")
        for f in p.get("shot_flags") or []:
            out.append(f"- shot flag: {f}")
        if p.get("validation_flags"):
            out.append(f"- validation: {'; '.join(p['validation_flags'])}")
        if p["build_warnings"]:
            out.append(f"- warnings: {'; '.join(p['build_warnings'])}")
    out.append("\n## Not shown\n")
    for r in doc["rejected"]:
        out.append(f"- {r['id']} (payoff {r['payoff']['line']}, intensity {r['payoff']['intensity']}): "
                   f"{'; '.join(r['reject_reasons'])}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description="Web-series clip selection v3 (payoffs -> hook -> clip, cached context)")
    ap.add_argument("video_ids", nargs="+")
    ap.add_argument("--out", default="runs/ws3")
    ap.add_argument("--dry-run", action="store_true", help="offline projection only: no network call of any kind")
    ap.add_argument("--max-ledger-inr", type=float, default=None,
                    help="stop before any call whose worst case would push the project ledger past this")
    ap.add_argument("--config", default=None, help="JSON deep-merged over DEFAULTS")
    ap.add_argument("--cache-mode", choices=("explicit", "implicit"), default=None,
                    help="context cache: explicit (cachedContents; falls back to implicit if refused) or implicit")
    ap.add_argument("--cache-only", action="store_true",
                    help="never call the provider: rebuild outputs from cached responses (free); a miss fails")
    ap.add_argument("--no-retry", action="store_true", help="(always on in ws3: one paid attempt per call)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    os.chdir(ROOT)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from shotvi.llm import Ledger, LLMClient
    cfg = ws.deep_merge(DEFAULTS, json.loads(Path(a.config).read_text(encoding="utf-8")) if a.config else {})
    if a.cache_mode:
        cfg["cache"]["mode"] = a.cache_mode
    out = Path(a.out)
    cfg["run_id"] = out.name
    guard = ws.Guard(a.max_ledger_inr)
    from shotvi.llm.spend import total_inr
    led0 = total_inr()
    if a.dry_run:
        print(f"DRY RUN (no provider call, countTokens included): ledger ₹{led0:.2f}"
              + (f", limit ₹{a.max_ledger_inr:.2f}" if a.max_ledger_inr is not None else ""))
    results, stopped = [], []
    for vid in a.video_ids:
        llm = LLMClient(Ledger(max_usd=cfg["budget_usd"]), cache_dir="runs/cache", max_retries=0, schema_retries=0)
        llm.cache_only = a.cache_only or a.dry_run
        try:
            results.append(run_episode(vid, cfg, llm, guard, out, dry_run=a.dry_run))
        except ws.BudgetStop as e:
            print(f"STOP: {e}")
            return 2
        except BudgetExceeded as e:
            print(f"STOP: {e}")
            return 2
        except EpisodeStop as e:
            print(f"STOPPED EPISODE: {e}")
            stopped.append(vid)
    if a.dry_run:
        exp = sum(r["would_bill_inr_expected"] for r in results)
        wst = sum(r["would_bill_inr_worst"] for r in results)
        w16 = sum(r["projection"]["episode"]["inr_worst_at_16"] for r in results)
        print(f"DRY RUN total: ₹{exp:.2f} expected; worst ₹{w16:.2f} at 16 payoffs per episode, "
              f"₹{wst:.2f} at the cap; ledger would go ₹{led0:.2f} -> ~₹{led0 + exp:.2f}"
              + (f" (limit ₹{a.max_ledger_inr:.2f}: "
                 f"{'fits even the worst case' if led0 + wst <= a.max_ledger_inr else ('fits the expected case; the guard may cut the payoff list' if led0 + exp <= a.max_ledger_inr else 'does NOT fit')})"
                 if a.max_ledger_inr is not None else ""))
        return 0
    print(f"ledger now ₹{total_inr():.2f} (this run ₹{total_inr() - led0:.2f})")
    if stopped:
        print(f"NO CLIPS (call 1 failed or blocked): {', '.join(stopped)}")
        return EXIT_SAFETY_BLOCKED
    return 0


if __name__ == "__main__":
    sys.exit(main())
