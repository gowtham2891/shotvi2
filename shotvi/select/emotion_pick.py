"""Emotion-first web-series clip selection (founder, 2026-10-16): clips are SELECTED by the feeling they trigger in a
stranger, and that reason (what makes a stranger stop, stay and feel) drives the pick and the cut. Two focused calls,
no review / judging call after building. Additive: v1 (``webseries``), ws3 and merged keep their behaviour and
defaults; this module only imports them (two behaviour-preserving helpers were factored out of merged.py:
``lift_ok_near(window=...)`` and ``dedupe_among``).

Why two calls: v1's single discover call already used 25-32k of its 32,768 output cap and found far fewer moments
than a focused finder (Queen 7 vs 11, Krishna Leelalu 12 vs 48); quality drops as items per call grow. But ws3 split
hook and clip into separate calls and cut fragments, so stop / stay / land are decided TOGETHER, per moment.

Per episode (the whole transcript sits in ONE Gemini explicit context cache: ws3.ContextCache with the ws3_context
persona, exactly as ws3 and merged do it; each call sends only its small task):
  1 FIND   (``ws_emotion_find``, thinking HIGH, once): the moments a stranger with zero context would FEEL. Per moment:
           land line id, feeling (laugh / aww / shock / tension / tears), intensity, one-sentence why (plus ws3's
           type and lands_on_screen). Nothing about starts, cuts or hooks. Strict, no quota. Validated by
           ``ws3.validate_payoffs`` (ids checked, an audio-event id mapped to the spoken line before it, duplicates
           merged, intensity >= find.min_intensity) with NO list cap. ``find_prompt: "ws3_payoffs"`` (CLI
           ``--find ws3_payoffs``) runs ws3's call 1 unchanged instead (its cached answers replay at Rs 0).
  2 BUILD  (``ws_emotion_build``, thinking MEDIUM, one call per moment, highest intensity first, ``--max-moments``
           caps the count): given ONE moment, answer together: stop (the first line heard: start_line or a liftable
           cold-open line), stay (the question that keeps them watching), land (line + reaction id or "hold"),
           start_line, end_line, <= 3 whole-chunk drops, purpose, spoiler, characters, Telugu title, reason ("Viewer
           stops because ..., stays to find out ..., and it ends on ... (feeling)."), or no_clip with why. No scores.
  code     validate: the land must stay within ``build.land_window_lines`` spoken lines of the found line; start must
           not come after the land; an end before the land, or more than 3 spoken lines after it, is moved to the land
           (merged.validate_moment); stop must be start_line or a line inside the clip.
           build: v1's cut builder (``ws.build_clip``). stop == start_line: the clip starts there; else stop is the
           cold-open lift and must pass v1's lift checks (a failed lift = REJECT). v1's hard rules apply (under
           guardrails.min_s rejected, never padded; hard max; censored; far stitch; replay).
           check on the clip as played (``emotion_select.render_clip``): the stop line starts within
           ``check.stop_window_s`` (3.0 s); the land line is among the last ``check.land_last_lines`` (3) spoken
           lines. Else REJECT with the reason.
           dedupe: a clip overlapping another by > guardrails.dup_overlap (50% of the shorter) loses to the higher
           intensity, then the earlier land line.
  output   v1 run-document format (schema 1.1, kind proposals; tools/preview_clips.py works), proposals in EPISODE
           order (no score ranking), each with feeling, intensity, stop, stay, land, reason, title and its source
           moment; ``episode_summary`` (moments found, built, rejected with reasons, deduped, shown) and a per-moment
           table.

emotion_pick/0.2 (founder's fix spec, 2026-10-16; the DEFAULTS below). Every change sits behind a config key, so
``configs/emotion_pick_v1.json`` reproduces 0.1 byte-for-byte from the response cache:
  prompts  ``ws_emotion_find`` / ``ws_emotion_build`` version 2 (v1 kept as ``*_v1``); the find schema's type enum
           gains ``cliffhanger`` (``find.extra_types``); the context renders Scribe's speaker labels
           (``ctx.speaker_names`` False; 0.1 used ws3's default True).
  lift-ok  (``lift_ok.rule`` "strict") a line is offered only if it passes every later lift check: speech
           1.0-3.6 s, v1's ``build_lift`` against its episode neighbours WITH the quiet-tail loudness check (mean_db,
           disk-cached under runs/cache/loudness), and a continuation guard: not when the next spoken line opens with a
           dependent connective (``lift_ok.continuations``) or is a 1-2 word acknowledgement (``lift_ok.acks``).
           ``lift_ok.enforce``: a cold open on a line that was not offered is refused (-> the fallback below).
  cluster  (``cluster.enabled``) between FIND and BUILD, consecutive payoffs (transcript order) join one cluster when
           within ``max_lines`` spoken lines or ``max_s`` seconds of the previous payoff and no scene transition lies
           between them (a music / song event >= ``music_min_s`` with no spoken word during it; optionally a silent gap
           >= ``silence_gap_s``). One build call per cluster: target = the LAST payoff with intensity >= cluster max -
           ``target_within``; the earlier payoffs go into the task as $cluster_context; sort strength = cluster max;
           ``--max-moments`` caps clusters.
  land     the land may lie ``build.land_window_lines`` spoken lines before to ``build.land_after_lines`` after the
           found line. ``build.end_rule`` "clamp": end_line is clamped so the land is among the last 3 spoken lines
           (<= 2 spoken lines after land, <= 1 after effective_land = max(land, reaction)), never moved to the land or
           rejected; ``check.land_late`` "trim": a played clip whose land is still not among the last 3 (the end hold
           played a reaction line) is trimmed line by line; K2 remains only when the land is not played at the end.
  stop     ``cold_open.rule`` "fallback": stop != start_line -> Case A (stop is start+1/+2 spoken lines and starts
           within 3.0 s of start_line: no lift, play from start_line) or Case B (a lift; refused -> play from
           start_line, or advance start_line to stop when start_line is weak filler and stop is within 4 spoken
           lines). ``check.stop_late`` "flag": a stop that does not play within 3 s flags ``stop_not_first`` instead of
           rejecting. The case is recorded in ``stop.case``.
  dedupe   ``dedupe.rule`` "scene": duplicates share > ``dedupe.overlap`` (35%) of the shorter clip, OR their
           start..end line spans overlap and both land lines lie in the same scene; keep the higher strength, then
           the one playing 30-75 s, then the one ending on the later line.
The internal-cut cap (v1 builder, cut.max_cuts 3) already counts the lift's in-place skip as one cut.

Rejection codes: N0 the build call said no_clip; X1 Gemini safety block (the moment is skipped); C1 call failed /
invalid answer; V1 answer ids invalid; B1 v1 cut builder; L1/L2/A1/S1/S2 v1 hard rules; K1 stop-first check (incl. a
failed cold-open lift; 0.2: only an invalid stop id); K2 land-last check; D1 duplicate.

Spend: one paid attempt per call (no retries); the spend guard (``--max-ledger-inr``) is checked up front (cache
storage + one call, worst case) and before EVERY billed call with that call's worst case (``before_call``); after
FIND the build list is cut to what the limit still allows (lowest intensity first). The project ledger records with
SHOTVI_SPEND_SOURCE. Cached answers replay at Rs 0. ``--dry-run`` makes no generate call: it replays a cached FIND,
counts input tokens with Gemini countTokens (free; ``--offline`` = estimate) and prints Rs expected / worst per
episode, with and without the moment cap.

CLI::

    python -m shotvi.select.emotion_pick 5HqkHAZfzos Sy8yZ4DgzrU --out runs/emotion1 --dry-run
    SHOTVI_SPEND_SOURCE=emotion1 python -m shotvi.select.emotion_pick 5HqkHAZfzos --out runs/emotion1 \\
        --max-ledger-inr 1700 --max-moments 15
    python tools/preview_clips.py runs/emotion1 --top 50
    # 0.1 replayed from the cache (Rs 0):
    python -m shotvi.select.emotion_pick <ids> --config shotvi/select/configs/emotion_pick_v1.json --cache-only
    # 0.2 dry run before its find has run: the find count estimated from an earlier run's find answers
    python -m shotvi.select.emotion_pick <ids> --out runs/emotion2 --dry-run --find-estimate runs/emotion1
"""
from __future__ import annotations

import argparse
import bisect
import copy
import json
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path

from shotvi.llm.base import BudgetExceeded, LLMError, SafetyBlocked
from shotvi.select import merged as M
from shotvi.select import webseries as ws
from shotvi.select import ws3

ROOT = ws.ROOT
PIPELINE_VERSION = "emotion_pick/0.2"
ROLE_FIND = "ws_emotion_find"
ROLE_BUILD = "ws_emotion_build"
FEELINGS = ["laugh", "aww", "shock", "tension", "tears"]
TIER = "emotion_pick"
TIER_LABEL = "emotion pick"
EXIT_SAFETY_BLOCKED = 4
PROJ_CAP = 15                 # the dry run always also shows this cap (founder's test: <= 15 build calls per episode)

_FLASH = {"provider": "gemini", "model": "gemini-3.8-flash"}
DEFAULTS: dict = {
    "pipeline_version": PIPELINE_VERSION,
    # a prompts/ws_emotion_find*.md file (the strict stranger test: ws_emotion_find = v2, ws_emotion_find_v1 = 0.1's)
    # or "ws3_payoffs" (ws3's call 1 unchanged)
    "find_prompt": ROLE_FIND,
    "models": {
        "find": {**_FLASH, "params": {"thinking_level": "high", "max_output_tokens": 32768}},     # = ws3_payoffs
        "build": {**_FLASH, "params": {"thinking_level": "medium", "max_output_tokens": 8192}},   # = merged call C
    },
    "prompts": {ROLE_BUILD: "ws_emotion_build"},
    # deep-merged over ws3.DEFAULTS: persona prompt, explicit cache (TTL 1 h), speaker_names. 0.2 renders Scribe's
    # labels (founder, 2026-10-16); 0.1 ran with ws3's default (corrected names): configs/emotion_pick_v1.json
    "ctx": {"speaker_names": False},
    "v1": {},        # deep-merged over ws.DEFAULTS: the cut builder's settings and the guardrails, unchanged
    # extra_types: added to the find schema's type enum (0.1: none)
    "find": {"min_intensity": 3, "extra_types": ["cliffhanger"]},
    # lift-ok lines offered to the build call. rule "v1" = webseries.liftable_lines; "strict" = also speech >=
    # min_speech_s, the quiet-tail loudness check and the continuation guard. enforce: a cold open on a line not
    # offered is refused (the stop fallback then applies)
    "lift_ok": {"rule": "strict", "enforce": True, "min_speech_s": 1.0, "max_speech_s": 3.6,
                "continuations": ["అందుకే", "కానీ", "సో", "మరి", "అలాగే", "అయినా", "అయితే", "ఎందుకంటే",
                                  "so", "but", "and", "because"],
                "acks": ["అవునా", "అవును", "ఊ", "ఊం", "ఉమ్", "సరే", "హ్మ్", "హా", "ఆ", "ఓ", "ఓకే", "అలాగా",
                         "ok", "okay", "hmm", "hm", "yes", "yeah"],
                "ack_max_words": 2},
    # same-scene clustering of the found payoffs before the build calls (0.1: off)
    "cluster": {"enabled": True, "max_lines": 35, "max_s": 50.0, "music_min_s": 4.0, "silence_gap_s": None,
                "target_within": 1},
    # build: max_moments None = a call for every found moment (cluster). The lift-ok list in the task covers lines
    # from lift_before_s before the land line to lift_after_s after it (as merged call C). The land may lie
    # land_window_lines before / land_after_lines after the found line (None = land_window_lines). end_rule "v1":
    # an end more than max_end_after_payoff_lines after the land moves to the land; "clamp": see the docstring
    "build": {"max_moments": None, "lift_before_s": 150.0, "lift_after_s": 20.0, "land_window_lines": 3,
              "land_after_lines": 15, "max_end_after_payoff_lines": 3,
              "end_rule": "clamp", "end_after_land_lines": 2, "end_after_reaction_lines": 1},
    # stop != start_line. rule "v1": always a lift (refused = K1 reject); "fallback": Case A / B (docstring)
    "cold_open": {"rule": "fallback", "near_lines": 2, "near_s": 3.0, "weak_advance_lines": 4,
                  "weak_max_words": 2, "weak_min_s": 1.0,
                  "greetings": ["hello", "hi", "hey", "హలో", "హాయ్", "నమస్తే", "namaste", "thank", "thanks", "you",
                                "bye", "good", "morning", "evening", "night"]},
    # stop_late / land_late: "reject" (0.1: K1 / K2) or "flag" / "trim" (0.2)
    "check": {"stop_window_s": 3.0, "land_last_lines": 3, "stop_late": "flag", "land_late": "trim"},
    # rule "v1": > guardrails.dup_overlap (50%) of the shorter, higher intensity then earlier land wins; "scene": see
    # the docstring
    "dedupe": {"rule": "scene", "overlap": 0.35, "prefer_play_s": [30.0, 75.0]},
    "budget_usd": 3.0,          # per-episode USD cap of the LLM client; the rupee guard (--max-ledger-inr) is the limit
    "calibrate_from": {"ws3": "runs/ws3"},   # measured find counts (projection of N before FIND has run)
}

# ---------------------------------------------------------------------------------------------------- projection
# Expected billed output (visible + thinking). FIND: ws3_payoffs (same task shape, HIGH) measured 7.9k (Queen, 11
# payoffs) and 15.0k (Krishna Leelalu, 48) -> the larger. BUILD: merged call C (medium, one whole v1 moment) is
# projected at 3.0k; the build answer adds stop / stay / land / title / reason (~300 visible tokens) -> 3.5k.
FIND_OUT_EXPECTED = 15000
BUILD_OUT_EXPECTED = 3500
TYPE_TO_V1 = {"punchline": "punchline", "reveal": "reveal", "quotable_line": "conclusion",
              "emotional_peak": "emotional_peak"}

_STR = {"type": "string"}
BUILD_SCHEMA = {
    "type": "object",
    "properties": {
        "no_clip": {"type": "boolean"},
        "no_clip_why": _STR,
        "stop": {"type": "object", "properties": {
            "line": {"type": "string", "description": "the first line heard: start_line or a cold-open line id"},
            "why": {"type": "string", "description": "why a stranger stops scrolling; quote the words"}},
            "required": ["line", "why"]},
        "stay": {"type": "string", "description": "the question that keeps the viewer watching to the landing"},
        "land": {"type": "object", "properties": {
            "line": {"type": "string", "description": "the line id where the feeling hits"},
            "reaction": {"type": "string", "description": "the reaction line id after it, or hold"}},
            "required": ["line", "reaction"]},
        "start_line": _STR, "end_line": _STR,
        "drops": {"type": "array", "items": {"type": "object", "properties": {
            "first_line": _STR, "last_line": _STR, "why": _STR}, "required": ["first_line", "last_line", "why"]}},
        "purpose": {"type": "string", "enum": ["standalone", "promo"]},
        "spoiler": {"type": "boolean"},
        "characters": {"type": "array", "items": _STR},
        "title": _STR,
        "reason": _STR,
    },
    "required": ["no_clip", "no_clip_why", "stop", "stay", "land", "start_line", "end_line", "drops", "purpose",
                 "spoiler", "characters", "title", "reason"],
}


class EpisodeStop(RuntimeError):
    """The episode cannot continue (FIND failed / was blocked)."""


class Rejected(Exception):
    def __init__(self, code: str, msg: str):
        self.code = code
        super().__init__(f"{code}: {msg}")


# ---------------------------------------------------------------------------------------------------- config
def find_role(cfg: dict) -> str:
    """The FIND call's role: "ws3_payoffs" for ws3's call 1, else ``ws_emotion_find`` whichever version of the
    prompt file runs (the role is not part of the response-cache key; the prompt text is)."""
    return "ws3_payoffs" if cfg["find_prompt"] == "ws3_payoffs" else ROLE_FIND


def find_schema(cfg: dict) -> dict:
    """ws3_payoffs: ws3's schema object itself (the response-cache key needs it byte-identical). The new prompt: the
    same shape, feelings limited to the founder's five (no "other"); ``find.extra_types`` (0.2: cliffhanger) are added
    to the type enum before "other" (ws3's own schema is not touched)."""
    if find_role(cfg) == "ws3_payoffs":
        return ws3.PAYOFFS_SCHEMA
    s = copy.deepcopy(ws3.PAYOFFS_SCHEMA)
    item = s["properties"]["payoffs"]["items"]["properties"]
    item["emotion"]["enum"] = list(FEELINGS)
    extra = [t for t in (cfg.get("find") or {}).get("extra_types") or [] if t not in item["type"]["enum"]]
    if extra:
        en = [t for t in item["type"]["enum"] if t != "other"]
        item["type"]["enum"] = en + extra + (["other"] if "other" in item["type"]["enum"] else [])
    return s


def ctx_cfg(cfg: dict) -> dict:
    """ws3's config (persona, cache, speaker names, validation) with the FIND role, its prompt and model; no list
    cap (``--max-moments`` caps the build calls instead)."""
    a = ws.deep_merge(ws3.DEFAULTS, cfg.get("ctx") or {})
    r = find_role(cfg)
    a["prompts"][r] = cfg["find_prompt"]
    a["models"][r] = copy.deepcopy(cfg["models"]["find"])
    a["payoffs"]["min_intensity"] = int(cfg["find"]["min_intensity"])
    a["payoffs"]["max_payoffs"] = 10 ** 6
    return a


def v1_cfg(cfg: dict) -> dict:
    return ws.deep_merge(ws.DEFAULTS, cfg.get("v1") or {})


def spec_build(cfg: dict):
    from shotvi.llm.base import ModelSpec
    return ModelSpec.from_dict(cfg["models"]["build"])


# ---------------------------------------------------------------------------------------------------- call 2 task
def task_build(cfg: dict, ep: ws.Episode, p: dict, lift_ids: list[str], cluster_context: str = "") -> str:
    """The build task for moment ``p``. ``cluster_context`` fills $cluster_context (v2 prompt; a v1 prompt has no such
    placeholder and renders exactly as 0.1 did)."""
    from shotvi.select.prompts import load_prompt
    pr = load_prompt(cfg["prompts"][ROLE_BUILD])
    return pr.system.strip() + "\n\n" + pr.render(
        episode_len=ws.fmt(ep.duration), land_line=p["line"], feeling=p["emotion"], intensity=p["intensity"],
        on_screen="the transcript shows the reaction" if p["lands_on_screen"] else "no reaction shown in the transcript",
        land_text=ws.one_line(p["text"], 400), why=ws.one_line(p["reason"], 400), cluster_context=cluster_context,
        lift_ok=M.lift_ok_near(ep, lift_ids, p, cfg, window=cfg["build"])).strip()


# ---------------------------------------------------------------------------------------------------- 0.2 lift-ok
def _tok(w: str) -> str:
    return w.strip(" \t.,!?…-–—\"'“”‘’()[]:;").lower()


def _tokens(text: str) -> list[str]:
    return [t for t in (_tok(w) for w in text.split()) if t]


def opens_with_continuation(text: str, conts: list[str]) -> str | None:
    """The connective ``text`` opens with (a dependent continuation of the line before), or None."""
    toks = _tokens(text)
    for c in conts:
        ct = _tokens(c)
        if ct and toks[:len(ct)] == ct:
            return c
    return None


def is_ack(text: str, acks: list[str], max_words: int) -> bool:
    """A 1-``max_words`` word acknowledgement ("అవునా", "సరే", "ok"): every token an ack word or a filler."""
    toks = _tokens(text)
    aset = {_tok(a) for a in acks}
    return 0 < len(toks) <= max_words and all(t in aset or ws._only_fillers(t) for t in toks)


class Loudness:
    """mean_db (ffmpeg volumedetect, ws.mean_db) of the episode audio with a disk cache
    (runs/cache/loudness/<vid>.json), so the strict lift-ok filter is computed once per episode. ``fallback``: the
    episode's own loudness function when the audio file is missing (tests inject one)."""

    def __init__(self, vid: str, fallback=None):
        self.wav = ws.ROOT / "scribe_dataset" / vid / f"{vid}_audio.wav"
        self.path = ws.ROOT / "runs" / "cache" / "loudness" / f"{vid}.json"
        self.fallback = fallback
        self.data: dict = {}
        self.dirty = False
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.data = {}

    @property
    def available(self) -> bool:
        return self.wav.exists() or self.fallback is not None

    def __call__(self, a: float, b: float):
        if not self.wav.exists():
            return self.fallback(a, b) if self.fallback else None
        k = f"{a:.3f}-{b:.3f}"
        if k not in self.data:
            self.data[k] = ws.mean_db(self.wav, a, b)
            self.dirty = True
        return self.data[k]

    def save(self) -> None:
        if self.dirty:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, sort_keys=True), encoding="utf-8")
            self.dirty = False


def strict_liftable_lines(ep: ws.Episode, vcut: dict, lc: dict, loud=None) -> list[str]:
    """0.2 lift-ok: a spoken line is offered only if it would pass every later lift check (see the docstring):
    speech min_speech_s..max_speech_s; v1's build_lift against its episode neighbours (the next two spoken lines kept,
    the line before kept) WITH the quiet-tail check; the next spoken line neither opens with a continuation nor is a
    1-2 word acknowledgement."""
    loud = loud if loud is not None else Loudness(ep.video_id, ep.loudness)
    ep2 = ws.Episode(ep.video_id, ep.lines, ep.words, ep.cuts, ep.duration,
                     loud if getattr(loud, "available", True) else None)
    sp = [r for r in ep.lines if ep.is_speech(r)]
    lo, hi = float(lc["min_speech_s"]), float(lc["max_speech_s"])
    out = []
    for k in range(1, len(sp) - 1):
        d = ep.w1(sp[k]) - ep.w0(sp[k])
        if d < lo or d > hi:
            continue
        nxt = ws.raw_text(ep, sp[k + 1])
        if opens_with_continuation(nxt, lc["continuations"]) or is_ack(nxt, lc["acks"], int(lc["ack_max_words"])):
            continue
        S = sp[k - 1:k + 3] if k + 2 < len(sp) else sp[k - 1:k + 2] + [sp[k + 1]]
        try:
            ws.build_lift(ep2, S, [1], set(range(len(S))) - {1}, vcut, "standalone")
        except ws.Reject:
            continue
        out.append(sp[k]["id"])
    if hasattr(loud, "save"):
        loud.save()
    return out


def lift_ok_lines(ep: ws.Episode, vcut: dict, cfg: dict) -> list[str]:
    lc = cfg.get("lift_ok") or {}
    if lc.get("rule", "v1") == "strict":
        return strict_liftable_lines(ep, vcut, lc)
    return ws.liftable_lines(ep, vcut)


# ---------------------------------------------------------------------------------------------------- 0.2 clusters
def scene_break(ep: ws.Episode, pa: int, pb: int, ccfg: dict) -> str | None:
    """A scene transition between ep.lines positions pa < pb, or None: a music / song audio event lasting >=
    music_min_s during which no word is spoken; with silence_gap_s set, also a silent gap that long between two
    consecutive spoken lines."""
    if pb < pa:
        pa, pb = pb, pa
    mn = float(ccfg.get("music_min_s") or 4.0)
    starts = getattr(ep, "_ep2_w_starts", None)
    if starts is None:
        starts = [w["start"] for w in ep.words]
        ep._ep2_w_starts = starts
    for r in ep.lines[pa + 1:pb]:
        if r["kind"] != "event" or not ws._is_music(r.get("text", "")) or r["end"] - r["start"] < mn - 1e-6:
            continue
        k = bisect.bisect_left(starts, r["start"]) - 1
        spoken = False
        for i in range(max(0, k), len(ep.words)):
            w = ep.words[i]
            if w["start"] >= r["end"]:
                break
            if w["end"] > r["start"] and w["start"] < r["end"]:
                spoken = True
                break
        if not spoken:
            return f"music: {r['id']} {r['text']} ({r['end'] - r['start']:.1f} s, no speech)"
    gap = ccfg.get("silence_gap_s")
    if gap:
        sp = [r for r in ep.lines[pa:pb + 1] if ep.is_speech(r)]
        for a, b in zip(sp, sp[1:]):
            if ep.w0(b) - ep.w1(a) >= float(gap):
                return f"silence: {ep.w0(b) - ep.w1(a):.1f} s before {b['id']}"
    return None


def cluster_moments(ep: ws.Episode, moments: list[dict], cfg: dict) -> list[dict]:
    """Payoffs -> clusters (transcript order). Disabled: one cluster per payoff (0.1). A payoff joins the previous
    one's cluster when it is within max_lines spoken lines OR max_s seconds of the previous payoff AND no scene
    transition lies between them. Per cluster: target = the LAST payoff with intensity >= max - target_within;
    ``earlier`` = the payoffs before it (they fill $cluster_context); ``absorbed`` = the ones after it;
    strength = the cluster's max intensity."""
    cc = cfg.get("cluster") or {}
    ps = sorted(moments, key=lambda p: ep.pos[p["line"]])
    if not cc.get("enabled"):
        groups = [[p] for p in ps]
        breaks = [None] * len(ps)
    else:
        sp_idx = M._spoken_index(ep)
        groups, breaks = [], []
        for p in ps:
            if groups:
                q = groups[-1][-1]
                dl = sp_idx[p["line"]] - sp_idx[q["line"]]
                dt = ep.w0(ep.by_id[p["line"]]) - ep.w0(ep.by_id[q["line"]])
                br = scene_break(ep, ep.pos[q["line"]], ep.pos[p["line"]], cc)
                if (dl <= int(cc["max_lines"]) or dt <= float(cc["max_s"])) and br is None:
                    groups[-1].append(p)
                    continue
                breaks.append(br or f"{dl} spoken lines / {dt:.0f} s after {q['line']} (> {cc['max_lines']} lines "
                                     f"and > {cc['max_s']:g} s)")
            else:
                breaks.append(None)
            groups.append([p])
    out = []
    for g, br in zip(groups, breaks):
        mx = max(p["intensity"] for p in g)
        within = int(cc.get("target_within", 1)) if cc.get("enabled") else 0
        t = max((k for k, p in enumerate(g) if p["intensity"] >= mx - within), default=len(g) - 1)
        earlier, target = g[:t], g[t]
        ctx = ("Earlier beats in this same scene to include as setup/escalation: "
               + ", ".join(f'{p["line"]} ("{ws.one_line(p["text"], 80)}")' for p in earlier)) if earlier else ""
        out.append({"target": target, "members": g, "earlier": earlier, "absorbed": g[t + 1:], "strength": mx,
                    "context": ctx, "break_before": br})
    return out


def cluster_public(k: dict) -> dict:
    return {"target": k["target"]["id"], "target_line": k["target"]["line"], "strength": k["strength"],
            "members": [{"id": p["id"], "line": p["line"], "intensity": p["intensity"]} for p in k["members"]],
            "earlier": [p["id"] for p in k["earlier"]], "absorbed": [p["id"] for p in k["absorbed"]],
            "break_before": k["break_before"], "cluster_context": k["context"]}


# ---------------------------------------------------------------------------------------------------- validation
def _sid(x) -> str | None:
    if isinstance(x, dict):
        x = x.get("line")
    s = str(x or "").strip()
    return None if s.lower() in ("", "none", "null", "-", "hold") else ws.norm_id(s)


def validate_build(ep: ws.Episode, p: dict, ans: dict, cfg: dict) -> tuple[dict, dict, list[str]]:
    """Build answer -> (v1 moment for the cut builder, emotion fields, flags). Raises Rejected (N0 / V1 / K1)."""
    if ans.get("no_clip"):
        raise Rejected("N0", f"no clip: {ws.one_line(ans.get('no_clip_why') or '-', 300)}")
    flags: list[str] = []
    sp_idx = M._spoken_index(ep)
    # land: a spoken line near the found one (an audio-event id maps to the spoken line before it)
    land_raw = _sid(ans.get("land"))
    pos, _ = ws3.event_mapped(ep, land_raw, "land", flags, forward=False)
    if pos is None:
        raise Rejected("V1", f"land line {(ans.get('land') or {}).get('line')!r} is not a line of the transcript")
    land = ep.lines[pos]["id"]
    d = sp_idx[land] - sp_idx[p["line"]]
    bc = cfg["build"]
    win_b = int(bc["land_window_lines"])
    win_a = int(bc["land_window_lines"] if bc.get("land_after_lines") is None else bc["land_after_lines"])
    win = win_a if d > 0 else win_b
    if abs(d) > win:
        raise Rejected("V1", f"land {land} is {abs(d)} spoken lines {'after' if d > 0 else 'before'} the found "
                             f"moment {p['line']} (> {win}): a different moment")
    if land != p["line"]:
        flags.append(f"land moved from the found line {p['line']} to {land}")
    # start: a spoken line (an audio-event id maps to the next spoken line)
    pos_s, _ = ws3.event_mapped(ep, _sid(ans.get("start_line")), "start", flags, forward=True)
    if pos_s is None:
        raise Rejected("V1", f"start_line {ans.get('start_line')!r} is not a line of the transcript")
    raw = {"start_line": ep.lines[pos_s]["id"], "end_line": ans.get("end_line"), "cold_open_lines": [],
           "drops": ans.get("drops") or []}
    clamp = bc.get("end_rule", "v1") == "clamp"
    try:
        m, f2 = M.validate_moment(ep, {"line": land}, raw,
                                  {"moment": {"max_end_after_payoff_lines":
                                              10 ** 6 if clamp else bc["max_end_after_payoff_lines"]}})
    except ValueError as e:
        raise Rejected("V1", str(e)) from e
    flags += f2
    reaction = str((ans.get("land") or {}).get("reaction") or "").strip()
    rid = _sid(reaction)
    if clamp:
        m["end_line"], f3 = clamp_end(ep, land, rid, m["end_line"], bc)
        flags += f3
    # stop: start_line itself, or a spoken line inside the clip (the cold-open lift)
    stop = _sid(ans.get("stop"))
    r = ep.by_id.get(stop) if stop else None
    if not ep.is_speech(r):
        raise Rejected("K1", f"stop line {(ans.get('stop') or {}).get('line')!r} is not a spoken line")
    ps, pe, pst = ep.pos[m["start_line"]], ep.pos[m["end_line"]], ep.pos[stop]
    lift = stop != m["start_line"]
    if lift and not ps < pst <= pe:
        raise Rejected("K1", f"stop line {stop} is outside the clip {m['start_line']}..{m['end_line']}")
    case = None
    if (cfg.get("cold_open") or {}).get("rule", "v1") == "fallback":
        case = stop_case(ep, m["start_line"], stop, cfg["cold_open"])
        lift = case == "B_lift"
    stop_why = str((ans.get("stop") or {}).get("why") or "")
    m.update(purpose=ans.get("purpose") or "standalone", spoiler=bool(ans.get("spoiler")),
             characters=list(ans.get("characters") or []), cold_open_lines=[stop] if lift else [],
             cold_open_why=stop_why if lift else "", payoff_line=ws3.line_text(ep, land),
             payoff_type=TYPE_TO_V1.get(p["type"]), hook=stop_why, payoff=ws3.line_text(ep, land),
             what_happens=str(ans.get("stay") or ""), category=ws3.EMOTION_CATEGORY.get(p["emotion"], "other"))
    emo = {"stop": {"line": stop, "text": ws3.line_text(ep, stop), "why": stop_why, "cold_open": lift,
                    **({"case": case} if case else {})},
           "stay": str(ans.get("stay") or ""),
           "land": {"line": land, "text": ws3.line_text(ep, land),
                    "reaction": rid if rid else ("hold" if reaction.lower() == "hold" else (reaction or "hold")),
                    **({"reaction_text": ws3.line_text(ep, rid)} if rid and rid in ep.by_id else {})},
           "title": str(ans.get("title") or "").strip(), "reason": str(ans.get("reason") or "").strip()}
    return m, emo, flags


def clamp_end(ep: ws.Episode, land: str, rid: str | None, end: str, bc: dict) -> tuple[str, list[str]]:
    """0.2 end clamp: the land must be among the last 3 spoken lines, so end_line may lie at most
    ``end_after_land_lines`` (2) spoken lines after the land and at most ``end_after_reaction_lines`` (1) after the
    effective land = max(land, reaction) when the reaction is a spoken line after the land inside the answered clip.
    A later end is moved back (never to the land unless that is the limit), never rejected."""
    sp_idx = M._spoken_index(ep)
    pl, pe = ep.pos[land], ep.pos[end]
    if pe <= pl:
        return end, []
    n_after = sum(1 for r in ep.lines[pl + 1:pe + 1] if ep.is_speech(r))
    eff = 0
    rr = ep.by_id.get(rid) if rid else None
    if ep.is_speech(rr) and pl < ep.pos[rid] <= pe:
        eff = sp_idx[rid] - sp_idx[land]
    lim = min(int(bc["end_after_land_lines"]), eff + int(bc["end_after_reaction_lines"]))
    if n_after <= lim:
        return end, []
    sp = [r for r in ep.lines if ep.is_speech(r)]
    new = sp[sp_idx[land] + lim]["id"]
    return new, [f"end_line {end} is {n_after} spoken lines after the land {land}; clamped to {new} "
                 f"({lim} after the land{f', reaction {rid}' if eff else ''})"]


def _weak_start(ep: ws.Episode, lid: str, cc: dict) -> str | None:
    """Why spoken line ``lid`` is a weak opener (filler-only, a greeting, <= weak_max_words words or under
    weak_min_s of speech), or None."""
    r = ep.by_id[lid]
    text = ws.raw_text(ep, r)
    toks = _tokens(text)
    if ws._only_fillers(text):
        return "filler-only"
    if toks and all(t in {_tok(g) for g in cc["greetings"]} for t in toks):
        return "greeting"
    if len(toks) <= int(cc["weak_max_words"]):
        return f"{len(toks)} word(s)"
    if ep.w1(r) - ep.w0(r) < float(cc["weak_min_s"]):
        return f"{ep.w1(r) - ep.w0(r):.1f} s of speech"
    return None


def stop_case(ep: ws.Episode, start: str, stop: str, cc: dict) -> str:
    """0.2: "start_line" (stop == start_line), "A_near_start" (stop is start+1..near_lines spoken lines and starts
    within near_s of start_line's first word: no lift) or "B_lift" (a cold-open lift)."""
    if stop == start:
        return "start_line"
    sp_idx = M._spoken_index(ep)
    ds = sp_idx[stop] - sp_idx[start]
    dt = ep.w0(ep.by_id[stop]) - ep.w0(ep.by_id[start])
    if 0 < ds <= int(cc["near_lines"]) and dt <= float(cc["near_s"]) + 1e-9:
        return "A_near_start"
    return "B_lift"


def build_v2(ep: ws.Episode, x: dict, vcfg: dict, cfg: dict, lift_ok: set) -> dict:
    """0.2 build (cold_open.rule "fallback" / check.land_late "trim"): v1's cut builder with the stop fallback and the
    land-last trim. Updates x["moment"], x["emo"]["stop"] (case, cold_open) and x["flags"]; raises Rejected (B1)."""
    m, emo, flags = x["moment"], x["emo"], x["flags"]
    cc, ck = cfg.get("cold_open") or {}, cfg["check"]
    stop, land = emo["stop"]["line"], emo["land"]["line"]
    n = int(ck["land_last_lines"])
    sp_idx = M._spoken_index(ep)
    from shotvi.select.emotion_select import render_clip

    def once(m0: dict):
        case = emo["stop"].get("case")
        if case is None:            # cold_open.rule "v1": the moment as answered (check_built judges the lift)
            return ws.build_clip(ep, m0, vcfg["cut"]), m0, None, None
        if case != "B_lift":
            return ws.build_clip(ep, {**m0, "cold_open_lines": []}, vcfg["cut"]), m0, case, None
        refuse = None
        if (cfg.get("lift_ok") or {}).get("enforce") and stop not in lift_ok:
            refuse = "not offered as a liftable line"
        else:
            c0 = ws.build_clip(ep, m0, vcfg["cut"])
            if c0["cold_open"]:
                return c0, m0, case, None
            refuse = c0.get("lift_rejected") or "lift not built"
        start = m0["start_line"]
        weak = _weak_start(ep, start, cc)
        if weak and 0 < sp_idx[stop] - sp_idx[start] <= int(cc["weak_advance_lines"]) \
                and ep.pos[stop] <= ep.pos[land]:
            m1 = {**m0, "start_line": stop, "cold_open_lines": []}
            note = (f"cold open {stop} refused ({refuse}); start_line {start} is weak ({weak}): the clip starts on "
                    f"{stop}")
            return ws.build_clip(ep, m1, vcfg["cut"], no_lift=True), m1, "B_refused_advance_start", note
        m1 = {**m0, "cold_open_lines": []}
        note = f"cold open {stop} refused ({refuse}); played in place, the clip starts on {start}"
        return ws.build_clip(ep, m1, vcfg["cut"], no_lift=True), m1, "B_refused_play_start", note

    cur = m
    for _ in range(50):
        try:
            c, used, case, note = once(cur)
        except ws.Reject as e:
            raise Rejected("B1", str(e)) from e
        if ck.get("land_late", "reject") != "trim":
            break
        rd = render_clip(ep, {"time_segments": c["time_segments"], "cold_open": bool(c["cold_open"])}, "C1")
        if land in rd.last_ids(n) or land not in rd.ids():
            break
        end_sp = M._end_spoken(ep, cur["end_line"])
        if end_sp is None or ep.pos[end_sp["id"]] <= ep.pos[land]:
            break
        prev = ws3.prev_speech(ep, ep.pos[end_sp["id"]] - 1)
        flags.append(f"land {land} not among the last {n} spoken lines as played (last: "
                     f"{', '.join(rd.last_ids(n))}); end_line {cur['end_line']} trimmed to {prev['id']}")
        cur = {**cur, "end_line": prev["id"]}
    if note:
        flags.append(note)
    x["moment"] = used
    if case is not None:
        emo["stop"]["case"] = case
        emo["stop"]["cold_open"] = bool(c["cold_open"])
    return c


def check_built(ep: ws.Episode, c: dict, emo: dict, cfg: dict) -> dict:
    """The clip as played: the stop line must open it (a requested lift must have passed v1's lift checks) and start
    within check.stop_window_s; the land line must be among the last check.land_last_lines spoken lines. Raises
    Rejected (K1 / K2); returns the measured positions."""
    from shotvi.select.emotion_select import render_clip
    win, n = float(cfg["check"]["stop_window_s"]), int(cfg["check"]["land_last_lines"])
    flag_late = cfg["check"].get("stop_late", "reject") == "flag"
    stop, land = emo["stop"]["line"], emo["land"]["line"]
    if emo["stop"]["cold_open"] and not c["cold_open"]:
        raise Rejected("K1", f"stop line {stop} could not be lifted to the front (v1 lift check: "
                             f"{c.get('lift_rejected') or 'lift not built'})")
    rd = render_clip(ep, {"time_segments": c["time_segments"], "cold_open": bool(c["cold_open"])}, "C1")
    t = rd.first_start(stop)
    late = None
    if t is None:
        late = f"stop line {stop} is not played"
    elif t > win + 1e-6:
        late = f"stop line {stop} starts at {t:.1f} s of playback (> {win:g} s)"
    if late and not flag_late:
        raise Rejected("K1", late)
    last = rd.last_ids(n)
    if land not in last:
        raise Rejected("K2", f"land line {land} is not among the last {n} spoken lines (last: {', '.join(last)})")
    rid = emo["land"].get("reaction")
    note = None
    if rid and rid != "hold" and rid not in rd.ids():
        note = f"reaction {rid} is not played (the end hold shows the reaction)"
    return {"stop_play_s": t, "land_play_s": rd.last_start(land), "last_lines": last, "plays_s": rd.duration,
            **({"note": note} if note else {}), **({"stop_not_first": late} if late else {})}


def _spans_overlap(ep: ws.Episode, a: dict, b: dict) -> bool:
    a0, a1 = ep.pos[a["start_line"]], ep.pos[a["end_line"]]
    b0, b1 = ep.pos[b["start_line"]], ep.pos[b["end_line"]]
    return a0 <= b1 and b0 <= a1


def dedupe_scene(ep: ws.Episode, cands: list[dict], cfg: dict, strength: dict) -> tuple[list, list]:
    """0.2 dedupe (``dedupe.rule`` "scene"): two clips are duplicates when they share > ``overlap`` of the shorter
    clip's source time, OR their start..end line spans overlap and both land lines lie in the same scene (no scene
    transition, ``scene_break``, between them). Greedy, best first: higher strength (cluster max intensity), then a
    play length inside ``prefer_play_s``, then the clip ending on the later line, then the earlier land line."""
    dc = cfg["dedupe"]
    lo, hi = (float(v) for v in dc["prefer_play_s"])
    ov = float(dc["overlap"])
    cc = cfg.get("cluster") or {}

    def key(x):
        d = x["clip"]["video_duration"]
        return (-strength[x["payoff"]["id"]], 0 if lo <= d <= hi else 1, -ep.pos[x["clip"]["end_line"]],
                ep.pos[x["emo"]["land"]["line"]])

    def dup(x, y):
        f = ws.overlap_frac(x["clip"], y["clip"])
        if f > ov:
            return f"{f:.0%} of the shorter"
        if _spans_overlap(ep, x["clip"], y["clip"]):
            la, lb = ep.pos[x["emo"]["land"]["line"]], ep.pos[y["emo"]["land"]["line"]]
            if scene_break(ep, min(la, lb), max(la, lb), cc) is None:
                return (f"same scene: spans {x['clip']['start_line']}..{x['clip']['end_line']} and "
                        f"{y['clip']['start_line']}..{y['clip']['end_line']} overlap; {f:.0%} of the shorter")
        return None

    out, dropped = [], []
    for x in sorted(cands, key=key):
        hit = next(((y, why) for y in out for why in [dup(x, y)] if why), None)
        if hit:
            y, why = hit
            dropped.append({**x, "reject_reasons": [
                f"D1: duplicate of clip {y['id']} ({why}; strength {strength[x['payoff']['id']]} vs "
                f"{strength[y['payoff']['id']]}, plays {x['clip']['video_duration']:.0f} s vs "
                f"{y['clip']['video_duration']:.0f} s)"], "dup_of": y["id"]})
            continue
        out.append(x)
    return out, dropped


# ---------------------------------------------------------------------------------------------------- tokens + Rs
class Tokens:
    """Input-token counts for the projection: Gemini countTokens (free, no generation) unless ``offline``; an
    offline estimate (ws3.est_tokens) on failure."""

    def __init__(self, spec, offline: bool = False, log=print):
        self.spec, self.offline, self.log, self.src = spec, offline, log, "countTokens"
        if offline:
            self.src = "offline estimate"

    def _prov(self):
        from shotvi.llm.client import get_provider
        return get_provider(self.spec.provider)

    def text(self, text: str) -> int:
        if not self.offline:
            try:
                return int(self._prov().count_tokens(self.spec, text))
            except Exception as e:   # noqa: BLE001 (projection only)
                self.log(f"countTokens unavailable ({str(e)[:120]}); estimating offline")
                self.offline, self.src = True, "offline estimate (countTokens failed)"
        return ws3.est_tokens(text)

    def context(self, system: str, prefix: str, ratio: float | None = None) -> int:
        if not self.offline:
            try:
                prov = self._prov()
                if hasattr(prov, "count_tokens_request"):
                    return int(prov.count_tokens_request(self.spec, system, prefix))
                return int(prov.count_tokens(self.spec, system + "\n" + prefix))
            except Exception as e:   # noqa: BLE001
                self.log(f"countTokens unavailable ({str(e)[:120]}); estimating offline")
                self.offline, self.src = True, "offline estimate (countTokens failed)"
        return ws3.est_tokens(system) + ws3.est_tokens(prefix, ratio)


def find_rate(cfg: dict) -> dict:
    """Moments per second of episode for the projection before FIND has run: ws3's measured call-1 counts
    (runs/ws3/<id>.json: payoffs listed / episode length). expected = pooled rate, worst = the highest episode."""
    d = (cfg.get("calibrate_from") or {}).get("ws3")
    rows = []
    for f in sorted((ROOT / d).glob("*.json")) if d and (ROOT / d).exists() else []:
        if f.name.startswith("dry_run"):
            continue
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
            n = int(doc["episode_summary"]["n_payoffs_listed"])
            dur = float(doc["transcript"]["audio_duration_secs"])
            rows.append((doc["video_id"], n, dur))
        except Exception:   # noqa: BLE001 (projection only)
            continue
    if not rows:
        r = ws3.PAYOFFS_PER_SEC
        return {"expected": r, "worst": r, "basis": "default ~1 moment per 1.75 min (ws3.PAYOFFS_PER_SEC)",
                "per_episode": {}}
    pooled = sum(n for _, n, _ in rows) / sum(d_ for _, _, d_ in rows)
    worst = max(n / d_ for _, n, d_ in rows)
    basis = "; ".join(f"{v}: {n} in {d_ / 60:.1f} min" for v, n, d_ in rows)
    return {"expected": pooled, "worst": worst, "per_episode": {v: n for v, n, _ in rows},
            "basis": f"ws3 call 1 measured ({basis}): pooled {pooled * 60:.2f}/min expected, {worst * 60:.2f}/min "
                     f"worst"}


def project(cfg: dict, acfg: dict, spec_f, spec_b, t_cache: int, t_find: int, f_hit: bool,
            build_tasks: list[tuple[int, bool]] | None, n_est: dict | None, t_build_sample: int | None,
            cap: int | None) -> dict:
    """Rupees of one episode. build_tasks = [(input tokens, cached)] in call order (FIND cached: exact), else
    n_est = {"expected": n, "worst": n} moments with t_build_sample tokens each. Rows: no cap and the cap."""
    from shotvi.llm.pricing import cache_storage_usd, cost_usd
    explicit = acfg["cache"].get("mode", "explicit") == "explicit"
    ttl_h = float(acfg["cache"]["ttl_s"]) / 3600.0
    storage = ws.inr(cache_storage_usd(spec_f.model, t_cache, ttl_h) or 0.0) if explicit else 0.0
    write_worst = ws.inr(cost_usd(spec_f.model, t_cache, 0) or 0.0) if explicit else 0.0
    max_f = int(spec_f.params.get("max_output_tokens", 32768))
    max_b = int(spec_b.params.get("max_output_tokens", 8192))

    def call(spec, t_in, out):
        return ws.inr(cost_usd(spec.model, t_in + t_cache, out, cached_tokens=t_cache if explicit else 0) or 0.0)

    f_exp = 0.0 if f_hit else call(spec_f, t_find, min(FIND_OUT_EXPECTED, max_f))
    f_worst = 0.0 if f_hit else call(spec_f, t_find, max_f)

    def row(c):
        if build_tasks is not None:
            sel = build_tasks if c is None else build_tasks[:c]
            unc = [t for t, hit in sel if not hit]
            be = sum(call(spec_b, t, BUILD_OUT_EXPECTED) for t in unc)
            bw = sum(call(spec_b, t, max_b) for t in unc)
            n_e = n_w = len(sel)
            n_unc_e = n_unc_w = len(unc)
        else:
            n_e = n_est["expected"] if c is None else min(c, n_est["expected"])
            n_w = n_est["worst"] if c is None else min(c, n_est["worst"])
            n_unc_e, n_unc_w = n_e, n_w
            be = n_e * call(spec_b, t_build_sample, BUILD_OUT_EXPECTED)
            bw = n_w * call(spec_b, t_build_sample, max_b)
        any_e = n_unc_e > 0 or not f_hit
        any_w = n_unc_w > 0 or not f_hit
        st_e = storage if any_e else 0.0
        st_w = storage + write_worst if any_w else 0.0
        return {"cap": c, "n_build_expected": n_e, "n_build_worst": n_w, "n_build_uncached": n_unc_e,
                "find_inr_expected": round(f_exp, 2), "find_inr_worst": round(f_worst, 2),
                "build_inr_expected": round(be, 2), "build_inr_worst": round(bw, 2),
                "storage_inr": round(st_e, 2),
                "inr_expected": round(f_exp + be + st_e, 2), "inr_worst": round(f_worst + bw + st_w, 2)}

    return {"context_tokens": t_cache, "find_task_tokens": t_find, "find_cached": f_hit,
            "build_task_tokens": [t for t, _ in build_tasks] if build_tasks is not None else None,
            "build_task_tokens_sample": t_build_sample, "n_moments": n_est,
            "storage_inr": round(storage, 2), "write_inr_worst": round(write_worst, 2),
            "find": {"model": spec_f.model, "thinking_level": spec_f.params.get("thinking_level"),
                     "out_expected": FIND_OUT_EXPECTED, "out_max": max_f,
                     "inr_expected": round(call(spec_f, t_find, min(FIND_OUT_EXPECTED, max_f)), 2),
                     "inr_worst": round(call(spec_f, t_find, max_f), 2)},
            "build": {"model": spec_b.model, "thinking_level": spec_b.params.get("thinking_level"),
                      "out_expected": BUILD_OUT_EXPECTED, "out_max": max_b,
                      "inr_worst_each": round(call(spec_b, max([t for t, _ in build_tasks] or [0])
                                                   if build_tasks is not None else t_build_sample, max_b), 2)},
            "rows": {"no_cap": row(None), "cap": row(cap) if cap is not None else None}}


def build_worst_inr(spec_b, t_cache: int, t_task: int = 4000) -> float:
    from shotvi.llm.pricing import cost_usd
    return ws.inr(cost_usd(spec_b.model, t_task + t_cache, int(spec_b.params.get("max_output_tokens", 8192)),
                           cached_tokens=t_cache) or 0.0)


# ---------------------------------------------------------------------------------------------------- the episode
def run_episode(vid: str, cfg: dict, llm, guard: ws.Guard, out_dir: Path, log=print, dry_run: bool = False,
                offline: bool = False, find_estimate: str | None = None) -> dict:
    t0 = time.time()
    acfg, vcfg = ctx_cfg(cfg), v1_cfg(cfg)
    ep, ix, _tr, tp = ws.load_episode(vid, with_loudness=not dry_run, speaker_names=bool(acfg.get("speaker_names")),
                                      require_names=False)
    system, prefix = ws3.context_prompt(ep, acfg)
    rf = find_role(cfg)
    spec_f, spec_b = ws3.spec_of(acfg, rf), spec_build(cfg)
    schema_f = find_schema(cfg)
    cache = ws3.ContextCache(system, prefix, spec_f, acfg, log)
    task_f = ws3.task_text(acfg, rf, episode_len=ws.fmt(ep.duration), n_lines=len(ep.lines))
    f_hit = cache.is_hit(llm, spec_f, task_f, schema_f)
    cap = cfg["build"].get("max_moments")
    debug: dict = {"video_id": vid, "warnings": []}
    if dry_run:
        llm.cache_only = True
        tok = Tokens(spec_f, offline, log)
        ratio = None if not offline else ws3.calibration_ratio(vid, ep, ix, acfg, log)[0]
        t_cache = tok.context(system, prefix, ratio)
        t_find = tok.text(task_f)
    else:
        tok = None
        t_cache, _src = M.context_tokens(vid, ep, ix, acfg, cfg, system, prefix, log)
        t_find = ws3.est_tokens(task_f)

    from shotvi.llm.pricing import cache_storage_usd, cost_usd
    f_worst = ws.inr(cost_usd(spec_f.model, t_find + t_cache, int(spec_f.params.get("max_output_tokens", 32768)),
                              cached_tokens=t_cache) or 0.0)
    b_worst = build_worst_inr(spec_b, t_cache)
    storage = ws.inr((cache_storage_usd(spec_f.model, t_cache, float(acfg["cache"]["ttl_s"]) / 3600) or 0.0)
                     + (cost_usd(spec_f.model, t_cache, 0) or 0.0))
    call_proj = {rf: f_worst, ROLE_BUILD: b_worst}
    llm.before_call = lambda role, _spec: guard.check(f"{vid} {role}", call_proj.get(role, max(call_proj.values())))
    if not f_hit and not dry_run:
        guard.check(f"{vid} episode (cache + find + one build, worst case)", storage + f_worst + b_worst)

    lift_ids = lift_ok_lines(ep, vcfg["cut"], cfg)
    lift_set = set(lift_ids)
    v2_build = (cfg.get("cold_open") or {}).get("rule", "v1") == "fallback" or \
        cfg["check"].get("land_late", "reject") == "trim"
    try:
        # ------------------------------------------------------------------ 1 FIND
        disc, moments, rej_moments = {}, None, []
        est_basis = None
        if not (dry_run and not f_hit):
            try:
                disc = M.ask_cached(llm, cache, rf, spec_f, task_f, schema_f, {"video": vid, "emotion_pick": "find"})
            except SafetyBlocked as e:
                raise EpisodeStop(f"{vid}: {rf} blocked by Gemini's safety check ({e.reason}); no moments") from e
            except LLMError as e:
                raise EpisodeStop(f"{vid}: {rf} failed ({e}); one attempt per call") from e
            moments, rej_moments = ws3.validate_payoffs(ep, disc, acfg)
            debug["find_raw"] = disc
            log(f"{vid}: FIND: {len(disc.get('payoffs') or [])} moments listed, {len(moments)} valid (by feeling "
                f"{dict(Counter(p['emotion'] for p in moments))}; by intensity "
                f"{dict(sorted(Counter(p['intensity'] for p in moments).items(), reverse=True))})")
        elif find_estimate:
            # dry run, find not cached: an earlier run's find answer (debug/<vid>.json find_raw) stands in for the
            # count and the lines, so the build tasks are rendered and counted exactly for THAT list
            fp = Path(find_estimate) / "debug" / f"{vid}.json"
            if fp.exists():
                raw = json.loads(fp.read_text(encoding="utf-8")).get("find_raw") or {}
                moments, rej_moments = ws3.validate_payoffs(ep, raw, acfg)
                est_basis = f"find answer of {fp.parent.parent.name} ({len(moments)} valid moments) as the estimate"
                log(f"{vid}: dry run: find not cached; estimating with {est_basis}")
        clusters = cluster_moments(ep, moments or [], cfg)
        strength = {k["target"]["id"]: k["strength"] for k in clusters}
        ctx_of = {k["target"]["id"]: k["context"] for k in clusters}
        order = [k["target"] for k in sorted(clusters, key=lambda k: (-k["strength"], ep.pos[k["target"]["line"]]))]
        tasks = {p["id"]: task_build(cfg, ep, p, lift_ids, ctx_of[p["id"]]) for p in order}
        if (cfg.get("cluster") or {}).get("enabled"):
            debug["clusters"] = [cluster_public(k) for k in clusters]
            if moments is not None:
                log(f"{vid}: {len(moments)} moments -> {len(clusters)} clusters "
                    f"({sum(1 for k in clusters if len(k['members']) > 1)} with several payoffs)")

        if dry_run:
            if moments is not None:
                bt = [(tok.text(tasks[p["id"]]), cache.is_hit(llm, spec_b, tasks[p["id"]], BUILD_SCHEMA))
                      for p in order]
                proj = project(cfg, acfg, spec_f, spec_b, t_cache, t_find, f_hit, bt, None, None,
                               cap if cap is not None else PROJ_CAP)
                tdir = out_dir / "tasks" / vid
                tdir.mkdir(parents=True, exist_ok=True)
                for p in order:
                    (tdir / f"{p['id']}_{p['line']}.txt").write_text(tasks[p["id"]] + "\n", encoding="utf-8")
            else:
                rate = find_rate(cfg)
                if vid in rate["per_episode"]:     # ws3's call 1 on this episode (the "be complete" list)
                    n0 = rate["per_episode"][vid]
                    n_est = {"expected": n0, "worst": n0,
                             "basis": f"ws3 call 1 listed {n0} on this episode (a strict find should list fewer)"}
                else:
                    n_est = {"expected": int(math.ceil(ep.duration * rate["expected"])),
                             "worst": int(math.ceil(ep.duration * rate["worst"])), "basis": rate["basis"]}
                long_line = max((r for r in ep.lines if ep.is_speech(r)), key=lambda r: len(ws3.line_text(ep, r["id"])))
                sample = {"line": long_line["id"], "text": ws3.line_text(ep, long_line["id"]), "emotion": "laugh",
                          "intensity": 5, "lands_on_screen": True, "reason": "x" * 300, "type": "punchline"}
                t_sample = tok.text(task_build(cfg, ep, sample, lift_ids))
                proj = project(cfg, acfg, spec_f, spec_b, t_cache, t_find, f_hit, None, n_est, t_sample,
                               cap if cap is not None else PROJ_CAP)
            proj["token_source"] = tok.src
            extra: dict = {"n_lift_ok": len(lift_ids)}
            if est_basis:
                extra["moments_basis"] = est_basis
            if (cfg.get("cluster") or {}).get("enabled") and moments is not None:
                extra["n_clusters"] = len(clusters)
                extra["clusters"] = debug["clusters"]
            return dry_doc(vid, out_dir, proj, moments, rej_moments, f_hit, rf, log, extra)

        # ------------------------------------------------------------------ the build list (cap, spend limit)
        called = order if cap is None else order[:cap]
        beyond = {p["id"]: "beyond --max-moments cap" for p in ([] if cap is None else order[cap:])}
        hits = {p["id"]: cache.is_hit(llm, spec_b, tasks[p["id"]], BUILD_SCHEMA) for p in called}
        if guard.limit is not None and called:
            from shotvi.llm.spend import total_inr
            room = guard.limit - total_inr() - (storage if cache.name is None and cache.mode == "explicit" else 0.0)
            n_fit, spent = 0, 0.0
            for p in called:
                add = 0.0 if hits[p["id"]] else build_worst_inr(spec_b, t_cache, ws3.est_tokens(tasks[p["id"]]))
                if spent + add > room:
                    break
                spent += add
                n_fit += 1
            if n_fit < len(called):
                for p in called[n_fit:]:
                    beyond[p["id"]] = "spend limit (--max-ledger-inr)"
                debug["warnings"].append(f"spend limit: build calls for {n_fit} of {len(called)} moments")
                log(f"{vid}: spend limit: build calls for {n_fit} of {len(called)} moments")
                called = called[:n_fit]

        if any(not hits[p["id"]] for p in called) and cache.mode == "explicit" and cache.name is None:
            # the first uncached build creates the provider cache (storage is billed at creation)
            guard.check(f"{vid} cache + first build call (worst case)", storage + b_worst)

        # ------------------------------------------------------------------ 2 BUILD, one call per moment
        cands, rejected, calls = [], [], []
        for p in called:
            x = {"id": "e" + p["id"][1:], "payoff": p}
            rec = {"moment": p["id"], "line": p["line"]}
            calls.append(rec)
            try:
                ans = M.ask_cached(llm, cache, ROLE_BUILD, spec_b, tasks[p["id"]], BUILD_SCHEMA,
                                   {"video": vid, "moment": p["line"], "emotion_pick": "build"})
            except SafetyBlocked as e:
                rec["error"] = str(e)
                rejected.append({**x, "reject_reasons": [f"X1: Gemini's safety check blocked this moment's build "
                                                         f"call ({e.reason}); skipped"]})
                log(f"  {x['id']} {p['line']}: blocked ({e.reason}); skipped")
                continue
            except LLMError as e:
                rec["error"] = str(e)
                rejected.append({**x, "reject_reasons": [f"C1: {e}"]})
                log(f"  {x['id']} {p['line']}: call failed: {e}")
                continue
            rec["answer"] = ans
            x["answer"] = ans
            try:
                x["moment"], x["emo"], x["flags"] = validate_build(ep, p, ans, cfg)
                if v2_build:
                    c = build_v2(ep, x, vcfg, cfg, lift_set)
                else:
                    try:
                        c = ws.build_clip(ep, x["moment"], vcfg["cut"])
                    except ws.Reject as e:
                        raise Rejected("B1", str(e)) from e
                x["clip"] = c
                rr = ws.hard_rules(ep, c, vcfg)
                if rr:
                    rejected.append({**x, "reject_reasons": rr})
                    log(f"  {x['id']} {p['line']}: rejected: {rr}")
                    continue
                x["check"] = check_built(ep, c, x["emo"], cfg)
                if x["check"].get("stop_not_first"):
                    x["flags"].append(f"stop_not_first: {x['check']['stop_not_first']}")
            except Rejected as e:
                rejected.append({**x, "reject_reasons": [str(e)]})
                log(f"  {x['id']} {p['line']}: rejected: {e}")
                continue
            cands.append(x)
            log(f"  {x['id']} {p['line']}: clip {c['start_line']}..{c['end_line']} plays {c['video_duration']:.1f} s"
                + (", cold open" if c["cold_open"] else "")
                + (f", stop {x['emo']['stop']['case']}" if x["emo"]["stop"].get("case") else ""))
        debug["calls"] = calls
        n_built = len(cands)
        if (cfg.get("dedupe") or {}).get("rule", "v1") == "scene":
            kept, dups = dedupe_scene(ep, cands, cfg, strength)
        else:
            kept, dups = M.dedupe_among(cands, vcfg["guardrails"]["dup_overlap"],
                                        key=lambda x: (-x["payoff"]["intensity"], ep.pos[x["emo"]["land"]["line"]]),
                                        code="D1", what="clip")
        rejected += dups
        kept.sort(key=lambda x: scene_start(x["clip"]))
    finally:
        debug["cache"] = cache.close()

    # ------------------------------------------------------------------ output
    line_of_word = {r["words"][0]: r for r in ep.lines if ep.is_speech(r)}
    props = [proposal_doc(ep, x, k + 1, vcfg, line_of_word) for k, x in enumerate(kept)]
    rej_docs = [rejected_doc(ep, x, vcfg) for x in rejected]
    table = moment_table(moments, called, beyond, rejected, kept, clusters)
    summary = episode_summary(disc, moments, rej_moments, order, called, beyond, n_built, rejected, kept, cap, debug)
    doc = run_document(ep, tp, props, rej_docs, cfg, acfg, vcfg, llm, summary, table, t0, debug)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{vid}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "debug").mkdir(exist_ok=True)
    debug["rejected_moments"] = rej_moments
    (out_dir / "debug" / f"{vid}.json").write_text(json.dumps(debug, ensure_ascii=False, indent=1, default=str),
                                                   encoding="utf-8")
    (out_dir / f"{vid}.md").write_text(clips_md(ep, doc), encoding="utf-8")
    s = doc["episode_summary"]
    log(f"{vid}: {s['n_moments_valid']} moments -> {s['n_build_calls']} build calls -> {s['n_built']} built -> "
        f"{s['n_shown']} shown; ${doc['cost']['usd_billed']:.4f} billed -> {out_dir / (vid + '.json')}")
    return doc


def scene_start(c: dict) -> float:
    sc = [s["start"] for s in c["time_segments"] if s.get("kind", "scene") == "scene"]
    return min(sc) if sc else c["time_segments"][0]["start"]


# ---------------------------------------------------------------------------------------------------- documents
def moment_public(p: dict) -> dict:
    return {"id": p["id"], "line": p["line"], "text": p["text"], "feeling": p["emotion"], "intensity": p["intensity"],
            "why": p["reason"], "type": p["type"], "lands_on_screen": p["lands_on_screen"]}


def proposal_doc(ep: ws.Episode, x: dict, rank: int, vcfg: dict, line_of_word: dict) -> dict:
    """v1's proposal document (ws.proposal_doc: pieces, time segments, cold open, cuts, end hold, flags) with no
    review: title and why come from the build call; plus the emotion fields."""
    p, emo = x["payoff"], x["emo"]
    title = emo["title"] or ws.one_line(emo["land"]["text"], 60)
    review = {"title": title, "why_it_works": emo["reason"] or p["reason"],
              "category": ws3.EMOTION_CATEGORY.get(p["emotion"], "other")}
    d = ws.proposal_doc(ep, {**x, "review": review, "scores": {}, "flaws": [], "tier": TIER}, rank, vcfg,
                        line_of_word)
    ck = x.get("check") or {}
    # stop / land carry the keys tools/preview_clips.py renders (via, plays_at_s, reaction, reaction_text)
    d.update(tier_label=TIER_LABEL, source="emotion_pick", feeling=p["emotion"], intensity=p["intensity"],
             stop={**emo["stop"], "via": "cold_open" if emo["stop"]["cold_open"] else "start_line",
                   "plays_at_s": ck.get("stop_play_s")}, stay=emo["stay"],
             land={**emo["land"], "plays_at_s": ck.get("land_play_s")}, reason=emo["reason"],
             moment=moment_public(p), build_flags=x.get("flags") or [],
             **({"check_note": ck["note"]} if ck.get("note") else {}))
    d.pop("review", None)
    return d


def rejected_doc(ep: ws.Episode, x: dict, vcfg: dict) -> dict:
    p = x["payoff"]
    base = {"id": x["id"], "tier": "rejected", "source": "emotion_pick", "reject_reasons": x["reject_reasons"],
            "moment": moment_public(p)}
    if x.get("dup_of"):
        base["dup_of"] = x["dup_of"]
    if x.get("emo"):
        base.update(stop=x["emo"]["stop"], stay=x["emo"]["stay"], land=x["emo"]["land"], reason=x["emo"]["reason"],
                    title=x["emo"]["title"])
    elif x.get("answer"):
        base["answer"] = x["answer"]
    c = x.get("clip")
    if c:
        base.update(pieces=c["pieces"], **ws.core_fields(ep, c["pieces"], vcfg["guardrails"]["skip_s"]),
                    time_segments=c["time_segments"], video_duration=c["video_duration"],
                    start_line=c["start_line"], end_line=c["end_line"], cold_open=bool(c["cold_open"]))
    else:   # nothing built: the land line as one piece (the schema needs pieces)
        r = ep.by_id[p["line"]]
        W = ep.words
        pieces = [{"start_word": r["words"][0], "end_word": r["words"][1],
                   "start": round(W[r["words"][0]]["start"], 3), "end": round(W[r["words"][1]]["end"], 3)}]
        base.update(pieces=pieces, **ws.core_fields(ep, pieces, vcfg["guardrails"]["skip_s"]))
    if x.get("moment"):
        base["build_moment"] = {k: x["moment"].get(k) for k in ("purpose", "start_line", "end_line",
                                                                "cold_open_lines", "drops")}
    return base


def moment_table(moments, called, beyond, rejected, kept, clusters=None) -> list[dict]:
    called_ids = {p["id"] for p in called}
    # 0.2: a payoff merged into another payoff's cluster is built by that cluster's call
    merged_into = {q["id"]: k["target"]["id"] for k in clusters or [] for q in k["members"]
                   if q["id"] != k["target"]["id"]}
    rej = {x["payoff"]["id"]: x["reject_reasons"][0] for x in rejected}
    shown = {x["payoff"]["id"]: x["id"] for x in kept}
    out = []
    for p in moments or []:
        row = {"id": p["id"], "line": p["line"], "feeling": p["emotion"], "intensity": p["intensity"],
               "text": ws.one_line(p["text"], 120), "why": ws.one_line(p["reason"], 240)}
        if p["id"] in shown:
            row.update(status="shown", clip=shown[p["id"]])
        elif p["id"] in rej:
            row.update(status="rejected", reason=rej[p["id"]])
        elif p["id"] in beyond:
            row.update(status="not_built", reason=beyond[p["id"]])
        elif p["id"] in called_ids:
            row.update(status="called")
        elif p["id"] in merged_into:
            row.update(status="in_cluster", cluster_target=merged_into[p["id"]])
        else:
            row.update(status="not_built")
        out.append(row)
    return out


def episode_summary(disc, moments, rej_moments, order, called, beyond, n_built, rejected, kept, cap, debug) -> dict:
    code = Counter(x["reject_reasons"][0].split(":")[0] for x in rejected)
    hard = sum(n for k, n in code.items() if k in ("L1", "L2", "A1", "S1", "S2"))
    return {"n_moments_listed": len(disc.get("payoffs") or []), "n_moments_valid": len(moments or []),
            "n_moments_rejected": len(rej_moments),
            "moments_rejected": [{"line": (r["payoff"] or {}).get("line"), "reason": r["reason"]} for r in rej_moments],
            "moments_by_feeling": dict(Counter(p["emotion"] for p in moments or [])),
            "moments_by_intensity": {str(k): n for k, n in sorted(Counter(p["intensity"] for p in moments or [])
                                                                  .items(), reverse=True)},
            "max_moments": cap, "n_build_calls": len(called),
            "n_not_built_cap": sum(1 for v in beyond.values() if v.startswith("beyond")),
            "n_not_built_spend": sum(1 for v in beyond.values() if v.startswith("spend")),
            "n_no_clip": code.get("N0", 0), "n_blocked": code.get("X1", 0), "n_call_failed": code.get("C1", 0),
            "n_invalid": code.get("V1", 0), "n_builder_rejected": code.get("B1", 0), "n_hard_rules": hard,
            "n_stop_check_failed": code.get("K1", 0), "n_land_check_failed": code.get("K2", 0),
            "n_built": n_built, "n_deduped": code.get("D1", 0), "n_shown": len(kept),
            "shown_by_feeling": dict(Counter(x["payoff"]["emotion"] for x in kept)),
            "rejected": [{"clip": x["id"], "moment": x["payoff"]["id"], "line": x["payoff"]["line"],
                          "reason": x["reject_reasons"][0]} for x in rejected],
            "warnings": debug.get("warnings", []), "episode_notes": disc.get("episode_notes", ""),
            **({"n_clusters": len(debug["clusters"]), "clusters": debug["clusters"]} if "clusters" in debug else {})}


def run_document(ep, tp, props, rej_docs, cfg, acfg, vcfg, llm, summary, table, t0, debug) -> dict:
    """v1's run document (ws.run_document: schema 1.1, kind proposals, transcript, cost) with this pipeline's
    version, prompts, models, cutoff and summary; no episode verdict (no tiers)."""
    from shotvi.select.prompts import load_prompt
    rf = find_role(cfg)
    doc = ws.run_document(ep, tp, props, rej_docs, vcfg, llm, summary["n_moments_valid"], summary["n_built"], t0,
                          {"projection": None, "warnings": debug.get("warnings", [])})
    doc.pop("episode_verdict", None)
    doc.update({
        "run_id": cfg.get("run_id", "emotion_pick"),
        "pipeline": {"version": cfg.get("pipeline_version") or PIPELINE_VERSION, "git_commit": None,
                     "design": "founder 2026-10-16: emotion-first, two focused calls (find the moments a stranger "
                               "feels; build stop / stay / land + the cut per moment); no review call",
                     "prompt_versions": {ws3.CONTEXT_ROLE: ws3.load_p(acfg, ws3.CONTEXT_ROLE).tag,
                                         rf: ws3.load_p(acfg, rf).tag,
                                         ROLE_BUILD: load_prompt(cfg["prompts"][ROLE_BUILD]).tag},
                     "prompt_files": {ws3.CONTEXT_ROLE: ws3.prompt_name(acfg, ws3.CONTEXT_ROLE), rf: cfg["find_prompt"],
                                      ROLE_BUILD: cfg["prompts"][ROLE_BUILD]},
                     "config": cfg},
        "models": [{"role": rf, **cfg["models"]["find"]}, {"role": ROLE_BUILD, **cfg["models"]["build"]}],
        "cutoff": {"show_rule": "emotion_pick: every clip that passes the build checks (stop first, land last, v1 "
                                "hard rules) and the dedupe; no review, no score",
                   "order": "episode order", "n_candidates": summary["n_moments_valid"],
                   "n_passed_hard_rules": summary["n_built"], "n_shown": len(props),
                   "n_rejected": len(rej_docs), "warnings": debug.get("warnings", [])},
        "episode_notes": summary.get("episode_notes", ""),
        "episode_note": {"text": episode_note_text(summary)},
        "episode_summary": summary, "moment_table": table,
    })
    doc["cost"]["cache"] = debug.get("cache")
    return doc


def episode_note_text(s: dict) -> str:
    rej = Counter(r["reason"].split(":")[0] for r in s["rejected"] if not r["reason"].startswith("D1"))
    return (f"{s['n_moments_valid']} moments a stranger would feel; {s['n_build_calls']} built by the build call"
            + (f" ({s['n_not_built_cap']} over the cap, {s['n_not_built_spend']} over the spend limit not built)"
               if s["n_not_built_cap"] or s["n_not_built_spend"] else "")
            + f"; {sum(rej.values())} rejected" + (f" ({', '.join(f'{k} {n}' for k, n in sorted(rej.items()))})"
                                                   if rej else "")
            + f"; {s['n_deduped']} duplicates dropped; {s['n_shown']} shown, in episode order.")


def clips_md(ep: ws.Episode, doc: dict) -> str:
    s = doc["episode_summary"]
    out = [f"# {doc['video_id']} emotion-first clips ({doc['run_id']})\n",
           f"Moments a stranger would feel: {s['n_moments_listed']} listed, {s['n_moments_valid']} valid (by feeling "
           f"{s['moments_by_feeling']}). Build calls {s['n_build_calls']}"
           + (f" (cap {s['max_moments']}: {s['n_not_built_cap']} not built)" if s["n_not_built_cap"] else "")
           + (f" ({s['n_not_built_spend']} not built: spend limit)" if s["n_not_built_spend"] else "")
           + f": {s['n_no_clip']} no clip, {s['n_blocked']} blocked, {s['n_call_failed']} failed, {s['n_invalid']} "
             f"invalid, {s['n_builder_rejected']} rejected by the cut builder, {s['n_hard_rules']} by hard rules, "
             f"{s['n_stop_check_failed']} failed stop-first, {s['n_land_check_failed']} failed land-last; "
             f"{s['n_built']} built, {s['n_deduped']} duplicates dropped, {s['n_shown']} shown (episode order, no "
             f"review, no score).\n",
           f"Cost ${doc['cost']['usd']:.4f} (${doc['cost']['usd_billed']:.4f} billed this run).\n"]
    for p in doc["proposals"]:
        segs = " + ".join(f"{ws.fmt(x['start'])}-{ws.fmt(x['end'])}" for x in p["time_segments"])
        st, ld = p["stop"], p["land"]
        out.append(f"\n## #{p['rank']} {p['id']} {p['title']} ({p['feeling']}, intensity {p['intensity']}, "
                   f"{p['clip_purpose']})\n")
        out.append(f"- plays {p['video_duration']:.1f} s: {segs}")
        out.append(f"- reason: {p['reason']}")
        out.append(f"- stop ({st['line']}{', cold open' if st['cold_open'] else ''}"
                   f"{', ' + st['case'] if st.get('case') else ''}, at {st.get('plays_at_s')} s): "
                   f"{st['text']} | {st['why']}")
        out.append(f"- stay: {p['stay']}")
        out.append(f"- land ({ld['line']}, reaction {ld['reaction']}): {ld['text']}")
        out.append(f"- moment: {p['moment']['line']} {p['moment']['why']}")
        eh = p["end_hold"]
        out.append(f"- end: payoff ends {ws.fmt(eh['payoff_end'])}, hold {eh['hold_s']} s")
        for cu in p["chunk_drops"]:
            out.append(f"- cut {ws.fmt(cu['start'])}-{ws.fmt(cu['end'])} ({cu['removed_s']} s): "
                       f"{'cold-open skip ' if cu['lift_skip'] else ''}{cu['why']}")
        if p["build_warnings"]:
            out.append(f"- warnings: {'; '.join(p['build_warnings'])}")
    out.append("\n## Not shown\n")
    for r in doc["rejected"]:
        out.append(f"- {r['id']} ({r['moment']['line']}, {r['moment']['feeling']} {r['moment']['intensity']}): "
                   f"{'; '.join(r['reject_reasons'])}")
    out.append("\n## Moments\n")
    for r in doc["moment_table"]:
        out.append(f"- {r['id']} {r['line']} ({r['feeling']} {r['intensity']}): {r['status']}"
                   + (f" {r['clip']}" if r.get("clip") else "") + (f": {r['reason']}" if r.get("reason") else "")
                   + f". {r['text']}")
    return "\n".join(out) + "\n"


def dry_doc(vid, out_dir, proj, moments, rej_moments, f_hit, rf, log, extra: dict | None = None) -> dict:
    res = {"video_id": vid, "dry_run": True, "find_prompt": rf, "find_cached": f_hit, "projection": proj,
           **(extra or {})}
    if moments is not None:
        res.update(n_moments_valid=len(moments), n_moments_rejected=len(rej_moments),
                   moments=[{"id": p["id"], "line": p["line"], "feeling": p["emotion"], "intensity": p["intensity"]}
                            for p in moments])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"dry_run_{vid}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str),
                                                 encoding="utf-8")
    log(projection_text(vid, res))
    return res


def projection_text(vid: str, res: dict) -> str:
    pr = res["projection"]
    n = (f"{res['n_moments_valid']} moments ({res.get('moments_basis') or 'cached ' + res['find_prompt'] + ' answer'})"
         + (f" -> {res['n_clusters']} clusters (= build calls)" if res.get("n_clusters") is not None else "")
         if res.get("n_moments_valid") is not None else f"moments estimated {pr['n_moments']['expected']} expected / {pr['n_moments']['worst']} "
                          f"worst ({pr['n_moments']['basis']})")
    out = [f"{vid}: find {res['find_prompt']} {'cached (Rs 0)' if res['find_cached'] else 'NOT cached'}; {n}",
           f"  context {pr['context_tokens']} tokens, find task {pr['find_task_tokens']} tokens ({pr['token_source']});"
           f" find Rs {pr['find']['inr_expected']} exp / Rs {pr['find']['inr_worst']} worst; build Rs "
           f"{pr['build']['inr_worst_each']} worst each; cache storage Rs {pr['storage_inr']}",
           "  setting | builds (exp/worst) | find exp | build exp | build worst | total exp | total worst"]
    for r in pr["rows"].values():
        if r is None:
            continue
        lab = "no cap" if r["cap"] is None else f"cap {r['cap']}"
        out.append(f"  {lab:7} | {r['n_build_expected']:>5} / {r['n_build_worst']:<5}     | {r['find_inr_expected']:8.2f}"
                   f" | {r['build_inr_expected']:9.2f} | {r['build_inr_worst']:11.2f} | {r['inr_expected']:9.2f} | "
                   f"{r['inr_worst']:11.2f}")
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description="Emotion-first web-series clip selection (find -> build per moment)")
    ap.add_argument("video_ids", nargs="+")
    ap.add_argument("--out", default="runs/emotion1")
    ap.add_argument("--dry-run", action="store_true",
                    help="free: no generate call; replays a cached find, countTokens for input, prints Rs")
    ap.add_argument("--offline", action="store_true", help="dry run without countTokens (offline token estimate)")
    ap.add_argument("--max-ledger-inr", type=float, default=None,
                    help="stop before any call whose worst case would push the project ledger past this")
    ap.add_argument("--max-moments", type=int, default=None,
                    help="cap on build calls per episode (highest intensity first); 0 = no cap; default: config")
    ap.add_argument("--find", default=None,
                    help="find prompt: ws_emotion_find (default, v2), ws_emotion_find_v1 or ws3_payoffs (ws3's call 1 "
                         "unchanged)")
    ap.add_argument("--config", default=None, help="JSON deep-merged over DEFAULTS")
    ap.add_argument("--find-estimate", default=None, metavar="RUN_DIR",
                    help="dry run with the find not cached: use RUN_DIR/debug/<id>.json's find answer as the "
                         "moment list estimate (build tasks rendered and counted exactly for that list)")
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
    if a.max_moments is not None:
        cfg["build"]["max_moments"] = None if a.max_moments <= 0 else a.max_moments
    if a.find:
        cfg["find_prompt"] = a.find
    out = Path(a.out)
    cfg["run_id"] = out.name
    guard = ws.Guard(a.max_ledger_inr)
    from shotvi.llm.spend import total_inr
    led0 = total_inr()
    if a.dry_run:
        print(f"DRY RUN (no generate call; countTokens {'off' if a.offline else 'on'}): ledger Rs {led0:.2f}"
              + (f", limit Rs {a.max_ledger_inr:.2f}" if a.max_ledger_inr is not None else ""))
    results, stopped = [], []
    for vid in a.video_ids:
        llm = LLMClient(Ledger(max_usd=cfg["budget_usd"]), cache_dir="runs/cache", max_retries=0, schema_retries=0)
        llm.cache_only = a.cache_only or a.dry_run
        try:
            results.append(run_episode(vid, cfg, llm, guard, out, dry_run=a.dry_run, offline=a.offline,
                                       find_estimate=a.find_estimate))
        except (ws.BudgetStop, BudgetExceeded) as e:
            print(f"STOP: {e}")
            return 2
        except EpisodeStop as e:
            print(f"STOPPED EPISODE: {e}")
            stopped.append(vid)
    if a.dry_run:
        for key in ("no_cap", "cap"):
            rows = [r["projection"]["rows"][key] for r in results if r["projection"]["rows"].get(key)]
            if rows:
                lab = "no cap" if key == "no_cap" else f"cap {rows[0]['cap']}"
                exp, wst = sum(x["inr_expected"] for x in rows), sum(x["inr_worst"] for x in rows)
                print(f"DRY RUN total ({lab}): builds {sum(x['n_build_expected'] for x in rows)} expected / "
                      f"{sum(x['n_build_worst'] for x in rows)} worst; Rs {exp:.2f} expected, Rs {wst:.2f} worst; "
                      f"ledger Rs {led0:.2f} -> ~Rs {led0 + exp:.2f}"
                      + (f" (limit Rs {a.max_ledger_inr:.2f}: "
                         f"{'fits the worst case' if led0 + wst <= a.max_ledger_inr else 'fits the expected case only' if led0 + exp <= a.max_ledger_inr else 'does NOT fit'})"
                         if a.max_ledger_inr is not None else ""))
        return 0
    print(f"ledger now Rs {total_inr():.2f} (this run Rs {total_inr() - led0:.2f})")
    if stopped:
        print(f"NO CLIPS: {', '.join(stopped)}")
        return EXIT_SAFETY_BLOCKED
    return 0


if __name__ == "__main__":
    sys.exit(main())
