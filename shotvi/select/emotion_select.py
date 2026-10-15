"""Emotion select: a retention check on built clips (founder, 2026-10-16). Additive; OFF in the web-series pipeline by
default (``cfg["emotion_select"]["enabled"]``), so v1 replays are unchanged.

Why: yes/no and 1-5 answers do not separate good clips from bad ones (ws3's "would stop scrolling / watch the whole
clip" was yes for ~100% of clips the founder rated bad; v1's review gave 4.5/5 to a founder "no"). So this stage makes
Gemini POINT AT LINES for each part of retention, with a categorical level under strict definitions, and code (not the
model) checks the pointers and decides the verdict.

Input: clips as they PLAY: ``{video_id, clip_id, time_segments: [{start, end, ...}] in play order, cold_open: bool,
...extra fields}``. A cold-open lift is simply the first segment (later in source time than the next). Extra fields are
copied to the results untouched and never reach the prompt; even ``clip_id`` stays out (the prompt names clips C1..Cn).

Render (``render_clip``, code): every spoken line whose words play in a segment, in play order, with its clip-relative
play time ([0:00.0] = the first frame), episode line id and speaker label; audio events inline; a marker at every jump
between segments ("[cut: 12 s removed]", "[cold open ends; scene starts]"); the picture hold after the last line.
Text is the episode's line text with the ASR layer's corrections applied (marks stripped); a line only partly played
shows its played words with "…" where words are cut.

Call: one ``ws_emotion`` call (gemini-3.8-flash, thinking medium, default temperature) per batch of up to
``batch_size`` (8) clips of ONE episode, balanced (9 clips -> 5 + 4). Each clip: stop {line, feeling, why, level},
stay {question, level}, land {line, feeling, reaction, why, level}, reason. No scores.

Code checks (``check_answer``), after the call:
  stop  line must be in the clip and START within the first ``stop_window_s`` (3.0) s of playback, else level none
  land  line must be in the clip and be among the last ``land_last_lines`` (3) spoken lines of playback, else none;
        a reaction id that is not in the clip -> none
  any   a level other than none with no line / no question / feeling "none" -> none (the part names nothing)
  verdict  yes = stop, stay and land all clear; maybe = no part none and at most one weak; no = otherwise.
  The model's raw answer and every code note are stored.

Safety fallback: a batch Gemini blocks (promptFeedback.blockReason) is judged in halves, recursively, down to single
clips; a clip still blocked alone gets verdict ``unchecked``.

Cost: the response cache makes a replay free; the spend guard (``--max-ledger-inr``) checks the worst case of all
uncached batches once, then every billed attempt; the project ledger records with SHOTVI_SPEND_SOURCE.

CLI::

    python -m shotvi.select.emotion_select --run runs/v1_candidates --out runs/emotion_v1c --dry-run
    python -m shotvi.select.emotion_select --clips clips.json --out runs/emotion_x --dry-run
    SHOTVI_SPEND_SOURCE=emotion_v1c python -m shotvi.select.emotion_select --run runs/v1_candidates \
        --out runs/emotion_v1c --max-ledger-inr 1650

Writes ``<out>/results.json`` (one entry per clip: video_id, clip_id, verdict, stop, stay, land, reason, code_notes,
raw, extra fields), ``<out>/rendered/<video>_<clip>.txt`` (each clip's text exactly as sent), ``<out>/prompts/`` (the
full system + user text of every batch), ``<out>/summary.json`` (cost, counts). ``--run`` also writes
``<out>/<video>.json``: the run's document with the verdict and reason on every shown proposal, ordered yes, maybe, no,
unchecked (the run's order inside each group), for ``tools/preview_clips.py <out>``. A dry run makes no generate call
(only the free countTokens, unless ``--offline``), replays cached batches, and prints the projected rupees.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from shotvi.llm.base import SafetyBlocked
from shotvi.select import webseries as ws

ROOT = ws.ROOT
VERSION = "emotion/0.1"
ROLE = "ws_emotion"
FEELINGS = ["laugh", "aww", "shock", "tension", "tears", "curiosity", "none"]
LEVELS = ["clear", "weak", "none"]
VERDICT_ORDER = {"yes": 0, "maybe": 1, "no": 2, "unchecked": 3}
UNCHECKED_NOTE = "Gemini's safety check blocked this clip; check it manually"

DEFAULTS: dict = {
    "enabled": False,
    "prompt": "ws_emotion",
    "model": {"provider": "gemini", "model": "gemini-3.8-flash",
              "params": {"thinking_level": "medium", "max_output_tokens": 16384}},
    "batch_size": 8,
    "stop_window_s": 3.0,     # the stop line must start within this much playback
    "land_last_lines": 3,     # the land line must be one of the last N spoken lines of playback
    "budget_usd": 1.0,        # per-run USD cap of the standalone CLI's ledger (the rupee guard is the real limit)
}

# Projection. No ws_emotion call has run yet: expected output (visible + thinking) per call is taken from ws_review at
# thinking MEDIUM on the same kind of input (clips as played), measured ~1000 + 800 per clip (runs/v1_test: 4.2k out
# for Queen, 9.3k for 11 Krishna clips; shotvi.select.merged REVIEW_OUT_EXP). Worst = the call's whole output budget.
OUT_EXPECTED = (1000, 800)    # (per call, per clip)
EST_FACTOR = 1.15             # offline input estimate (segment.render.estimate_tokens) x this, when countTokens is off
IN_GUESS = (900, 700)         # input tokens per call (base, per clip) before the clips exist (pipeline projection);
                              # offline estimate on runs/v1_candidates: 2.4-4.8k for 4-7 clips


def emo_cfg(cfg: dict | None, pipeline: bool = False) -> dict:
    """The stage config: DEFAULTS deep-merged with ``cfg["emotion_select"]`` (``pipeline``: a web-series config) or
    with ``cfg`` itself (the standalone CLI's --config)."""
    sub = ((cfg or {}).get("emotion_select") or {}) if pipeline else (cfg or {})
    return ws.deep_merge(DEFAULTS, sub)


def enabled(cfg: dict | None) -> bool:
    """Read with .get so a config without the key (every replay config) is byte-identical to before the stage."""
    return bool(((cfg or {}).get("emotion_select") or {}).get("enabled"))


# ================================================================================================ render
_FIXED = re.compile(r"\{fixed: ([^}]*)\}")
_UNSURE = re.compile(r"(\S+)\(\?\)")


def clean_text(text: str) -> str:
    """Line text with the ASR layer's marks stripped: ``{fixed: X}`` -> X, ``word(?)`` -> word."""
    return " ".join(_UNSURE.sub(r"\1", _FIXED.sub(r"\1", text or "")).split())


def _span(t: float) -> str:
    return f"{t:.1f}" if t < 10 else f"{t:.0f}"


@dataclass
class Rendered:
    alias: str
    text: str
    duration: float
    speech: list = field(default_factory=list)   # spoken items in play order: {id, play_start, play_end, ...}

    def first_start(self, lid: str) -> float | None:
        ts = [r["play_start"] for r in self.speech if r["id"] == lid]
        return min(ts) if ts else None

    def last_start(self, lid: str) -> float | None:
        ts = [r["play_start"] for r in self.speech if r["id"] == lid]
        return max(ts) if ts else None

    def ids(self) -> set:
        return {r["id"] for r in self.speech}

    def last_ids(self, n: int) -> list[str]:
        """The last n distinct spoken line ids of playback (latest occurrence counts)."""
        out: list[str] = []
        for r in reversed(self.speech):
            if r["id"] not in out:
                out.append(r["id"])
            if len(out) == n:
                break
        return out[::-1]

    def play_pos(self, lid: str) -> int | None:
        """Index of the line's LAST occurrence in playback."""
        pos = [k for k, r in enumerate(self.speech) if r["id"] == lid]
        return pos[-1] if pos else None


def _words_in(ep, s: float, e: float) -> list[int]:
    """Word indices that play fully inside [s, e] (same tolerance as webseries.pieces_for)."""
    import bisect
    W = ep.words
    if not hasattr(ep, "_emo_starts"):
        ep._emo_starts = [w["start"] for w in W]
    k = bisect.bisect_left(ep._emo_starts, s - 0.01)
    out = []
    while k < len(W) and W[k]["start"] <= e + 0.01:
        if W[k]["end"] <= e + 0.01:
            out.append(k)
        k += 1
    return out


def render_clip(ep, clip: dict, alias: str, names: dict | None = None) -> Rendered:
    """The clip as it plays, from ``time_segments`` alone (no moment dict needed). See the module docstring."""
    from shotvi.select.lines import speaker_label
    segs = [(float(s["start"]), float(s["end"])) for s in clip["time_segments"]]
    if not segs:
        raise ValueError(f"{clip.get('clip_id')}: no time_segments")
    W = ep.words
    rows: list[str] = []
    speech: list[dict] = []
    off = 0.0
    for j, (s, e) in enumerate(segs):
        if j > 0:
            ps, pe = segs[j - 1]
            if s < ps - 0.01:
                rows.append("[cold open ends; scene starts]" if j == 1 and clip.get("cold_open")
                            else f"[jumps back {_span(ps - s)} s]")
            elif s - pe > 0.05:
                skipped_played = any(a < s and b > pe for a, b in segs[:j - 1])
                rows.append(f"[cut: {_span(s - pe)} s removed"
                            + ("; the opening line is skipped here, not replayed" if skipped_played else "") + "]")
        items = []
        # spoken lines: consecutive played words of one line form one item
        cur = None
        for i in _words_in(ep, s, e):
            r = ep.speech_line_at(i)
            if r is None:
                continue
            if cur is not None and cur["line"] is r and i == cur["w"][-1] + 1:
                cur["w"].append(i)
                continue
            cur = {"line": r, "w": [i]}
            items.append(("speech", W[i]["start"], cur))
        for ev in ep.events:
            ov = min(ev["end"], e) - max(ev["start"], s)
            mid = (ev["start"] + ev["end"]) / 2
            if (ov > 0 and ov >= min(0.3, 0.5 * (ev["end"] - ev["start"]))) or s <= mid <= e:
                items.append(("event", max(ev["start"], s), ev))
        items.sort(key=lambda x: (x[1], x[0] != "event"))
        for kind, t, it in items:
            pt = round(off + max(0.0, t - s), 2)
            if kind == "event":
                rows.append(f"[{ws.fmt(pt)}] (audio event) {clean_text(it['text'])}")
                continue
            r, w = it["line"], it["w"]
            full = w[0] == r["words"][0] and w[-1] == r["words"][1]
            text = clean_text(r.get("text", "")) if full else \
                ("… " if w[0] > r["words"][0] else "") + " ".join(W[i]["word"] for i in w) \
                + (" …" if w[-1] < r["words"][1] else "")
            who = speaker_label(r, names)
            rows.append(f"[{ws.fmt(pt)}] {r['id']} [{who}]: {text}")
            speech.append({"id": r["id"], "play_start": pt,
                           "play_end": round(off + min(e, W[w[-1]]["end"]) - s, 2), "speaker": who,
                           "partial": not full, "source_start": round(W[w[0]]["start"], 3)})
        off += e - s
    total = round(off, 2)
    if speech:
        rows.append(f"[picture holds {max(0.0, total - speech[-1]['play_end']):.1f} s]")
    else:
        rows.append("[no spoken lines]")
    head = f"### {alias} | plays {total:.1f} s"
    return Rendered(alias, "\n".join([head] + rows), total, speech)


# ================================================================================================ prompt + schema
def build_prompt(blocks: list[str], ecfg: dict) -> tuple[str, str]:
    from shotvi.select.prompts import load_prompt
    p = load_prompt(ecfg["prompt"])
    return p.system, p.render(n_clips=len(blocks), clips="\n\n".join(blocks))


_S = {"type": "string"}


def schema_for(aliases: list[str]) -> dict:
    line = {"type": "string", "description": "a line id from this clip (e.g. L0123), or none"}
    feeling = {"type": "string", "enum": FEELINGS}
    level = {"type": "string", "enum": LEVELS}
    return {
        "type": "object",
        "properties": {"clips": {"type": "array", "minItems": len(aliases), "maxItems": len(aliases), "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "enum": list(aliases)},
                "stop": {"type": "object", "properties": {
                    "line": line, "feeling": feeling,
                    "why": {"type": "string", "description": "one sentence; quote the words"}, "level": level},
                    "required": ["line", "feeling", "why", "level"]},
                "stay": {"type": "object", "properties": {
                    "question": {"type": "string", "description": "what the viewer wants to find out, or none"},
                    "level": level}, "required": ["question", "level"]},
                "land": {"type": "object", "properties": {
                    "line": line, "feeling": feeling,
                    "reaction": {"type": "string", "description": "the reaction line id after it, hold, or none"},
                    "why": {"type": "string", "description": "one sentence; quote the words"}, "level": level},
                    "required": ["line", "feeling", "reaction", "why", "level"]},
                "reason": _S,
            },
            "required": ["id", "stop", "stay", "land", "reason"]}}},
        "required": ["clips"],
    }


# ================================================================================================ code checks
def _is_none(x) -> bool:
    return str(x or "").strip().lower() in ("", "none", "null", "-", "n/a", "no")


def check_answer(ans: dict, rd: Rendered, ecfg: dict) -> dict:
    """Code checks on one clip's answer (see the module docstring). Returns {stop, stay, land, verdict, code_notes}."""
    notes: list[str] = []
    win, n_last = float(ecfg["stop_window_s"]), int(ecfg["land_last_lines"])
    ids = rd.ids()

    def part(name):
        p = dict(ans.get(name) or {})
        lv = str(p.get("level", "none")).strip().lower()
        p["level"] = lv if lv in LEVELS else "none"
        return p

    def down(p, name, why):
        if p["level"] != "none":
            notes.append(f"code: {name} {why} -> none")
            p["model_level"] = p["level"]
        p["level"] = "none"

    stop, stay, land = part("stop"), part("stay"), part("land")

    # stop: a line of this clip that starts in the first window
    sid = None if _is_none(stop.get("line")) else ws.norm_id(stop.get("line"))
    if sid is None:
        if stop["level"] != "none":
            down(stop, "stop", "names no line")
    elif sid not in ids:
        down(stop, "stop", f"line {sid} is not in the clip")
    else:
        t = rd.first_start(sid)
        stop["line_play_s"] = t
        if t > win + 1e-6:
            down(stop, "stop", f"line not in first {win:g} s ({sid} starts at {t:.1f} s)")
    if stop["level"] != "none" and _is_none(stop.get("feeling")):
        down(stop, "stop", "feeling is none")

    # stay: a concrete question
    if stay["level"] != "none" and _is_none(stay.get("question")):
        down(stay, "stay", "names no question")

    # land: one of the last n spoken lines; a reaction id must be in the clip
    lid = None if _is_none(land.get("line")) else ws.norm_id(land.get("line"))
    if lid is None:
        if land["level"] != "none":
            down(land, "land", "names no line")
    elif lid not in ids:
        down(land, "land", f"line {lid} is not in the clip")
    else:
        last = rd.last_ids(n_last)
        land["line_play_s"] = rd.last_start(lid)
        if lid not in last:
            down(land, "land", f"line not among the last {n_last} spoken lines ({lid}; last: {', '.join(last)})")
    rx = str(land.get("reaction") or "").strip()
    if rx and rx.lower() not in ("hold", "none", "null", "-"):
        rid = ws.norm_id(rx)
        if rid is None or rid not in ids:
            down(land, "land", f"reaction {rx} is not a line of the clip")
        elif lid in ids and rd.play_pos(rid) is not None and rd.play_pos(lid) is not None \
                and rd.play_pos(rid) <= rd.play_pos(lid):
            notes.append(f"code: reaction {rid} does not play after the land line {lid} (note only)")
    if land["level"] != "none" and _is_none(land.get("feeling")):
        down(land, "land", "feeling is none")

    return {"stop": stop, "stay": stay, "land": land, "verdict": verdict_of(stop, stay, land), "code_notes": notes}


def verdict_of(stop: dict, stay: dict, land: dict) -> str:
    lv = [stop["level"], stay["level"], land["level"]]
    if all(x == "clear" for x in lv):
        return "yes"
    if "none" not in lv and lv.count("weak") <= 1:
        return "maybe"
    return "no"


# ================================================================================================ projection
def batches_of(n: int, size: int) -> list[list[int]]:
    """Balanced batches of at most ``size`` (9 -> 5 + 4, not 8 + 1)."""
    if n <= 0:
        return []
    k = math.ceil(n / size)
    base, extra = divmod(n, k)
    out, i = [], 0
    for b in range(k):
        m = base + (1 if b < extra else 0)
        out.append(list(range(i, i + m)))
        i += m
    return out


def in_tokens(spec, system: str, user: str, use_api: bool) -> tuple[int, str]:
    """Input tokens: countTokens (a free network call, no generation), else an offline estimate."""
    if use_api:
        try:
            from shotvi.llm.client import get_provider
            return int(get_provider(spec.provider).count_tokens(spec, system + "\n" + user)), "countTokens"
        except Exception as e:   # noqa: BLE001 (projection only)
            print(f"countTokens unavailable ({str(e)[:120]}); estimating offline", file=sys.stderr)
    from shotvi.segment.render import estimate_tokens
    return int(estimate_tokens(system + "\n" + user) * EST_FACTOR), f"estimate x{EST_FACTOR}"


def call_inr(spec, n_in: int, n_out: int) -> float:
    return ws.inr(ws.project_usd(n_in, n_out, spec.model))


def call_projection(spec, n_in: int, n_clips: int) -> dict:
    exp_out = OUT_EXPECTED[0] + OUT_EXPECTED[1] * n_clips
    max_out = int(spec.params.get("max_output_tokens") or 16384)
    return {"in": n_in, "out_expected": min(exp_out, max_out), "out_worst": max_out,
            "inr_expected": round(call_inr(spec, n_in, min(exp_out, max_out)), 3),
            "inr_worst": round(call_inr(spec, n_in, max_out), 3)}


def project_guess(cfg: dict, n_clips: int) -> dict:
    """Pipeline projection before the clips exist (``n_clips`` guessed): input IN_GUESS per call, output as
    ``call_projection``. ``inr`` = the guard value: every call at its whole output budget plus one safety split."""
    from shotvi.llm import ModelSpec
    ecfg = emo_cfg(cfg, pipeline=True)
    spec = ModelSpec.from_dict(ecfg["model"])
    groups = batches_of(n_clips, int(ecfg["batch_size"]))
    exp = worst = fb = 0.0
    for g in groups:
        n_in = IN_GUESS[0] + IN_GUESS[1] * len(g)
        pj = call_projection(spec, n_in, len(g))
        exp += pj["inr_expected"]
        worst += pj["inr_worst"]
        fb = max(fb, fallback_worst_inr(spec, n_in, len(g)))
    return {"assumes_clips": n_clips, "n_calls": len(groups), "inr_expected": round(exp, 2),
            "inr_worst": round(worst, 2), "inr_fallback_worst": round(fb, 2), "inr": round(worst + fb, 2)}


def fallback_worst_inr(spec, n_in: int, n_clips: int) -> float:
    """Worst case of the split fallback for one blocked clip (webseries.review_split_sizes: ~2n clip judgements),
    input and output pro rata to the clips (as webseries.review_fallback_usd)."""
    if n_clips <= 1:
        return 0.0
    base = min(1200, n_in)
    per_in = max(0, n_in - base) / n_clips
    per_out = int(spec.params.get("max_output_tokens") or 16384) / n_clips
    return sum(call_inr(spec, int(base + per_in * k), int(per_out * k)) for k in ws.review_split_sizes(n_clips))


# ================================================================================================ run
class _Batch:
    def __init__(self, vid: str, idxs: list[int], rds: list[Rendered], system: str, user: str, schema: dict):
        self.vid, self.idxs, self.rds = vid, idxs, rds
        self.system, self.user, self.schema = system, user, schema


def make_batch(vid: str, idxs: list[int], rds: list[Rendered], ecfg: dict) -> _Batch:
    s, u = build_prompt([r.text for r in rds], ecfg)
    return _Batch(vid, idxs, rds, s, u, schema_for([r.alias for r in rds]))


class Runner:
    """Judges clips batch by batch: cache replay, spend guard, safety split. ``call_proj``: a pipeline's per-role
    guard dict (its llm.before_call reads it); None = install our own before_call on ``llm``."""

    def __init__(self, llm, ecfg: dict, guard, log=print, dry_run: bool = False, count_api: bool = True,
                 call_proj: dict | None = None, prompt_dir: Path | None = None):
        from shotvi.llm import ModelSpec
        self.llm, self.ecfg, self.guard, self.log, self.dry_run = llm, ecfg, guard, log, dry_run
        self.spec = ModelSpec.from_dict(ecfg["model"])
        self.count_api = count_api
        self.prompt_dir = prompt_dir
        self.calls: list[dict] = []
        self.proj: list[dict] = []        # uncached calls a dry run would make (and their rupees)
        if call_proj is None:
            call_proj = {}
            llm.before_call = lambda role, _spec: guard.check(f"emotion_select {role}", call_proj.get(ROLE, 0.0))
        self.call_proj = call_proj
        self._tok: dict = {}

    def tokens(self, b: _Batch) -> tuple[int, str]:
        key = (b.system, b.user)
        if key not in self._tok:
            self._tok[key] = in_tokens(self.spec, b.system, b.user, self.count_api)
        return self._tok[key]

    def cached(self, b: _Batch) -> bool:
        return ws.is_cached(self.llm, self.spec, b.system, b.user, b.schema)

    def save_prompt(self, b: _Batch, tag: str):
        if self.prompt_dir is None:
            return
        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        (self.prompt_dir / f"{b.vid}_{tag}.txt").write_text(
            f"### SYSTEM\n{b.system}\n\n### USER\n{b.user}\n", encoding="utf-8")

    def preflight(self, batches: list[_Batch]) -> dict:
        """Projection of the uncached batches; a real run checks their worst case (plus one split fallback) against
        the guard once, before the first billed call, so it never stops halfway through for money."""
        unc = [b for b in batches if not self.cached(b)]
        rows = []
        for b in unc:
            n_in, src = self.tokens(b)
            rows.append({"video_id": b.vid, "n_clips": len(b.rds), "in_source": src,
                         **call_projection(self.spec, n_in, len(b.rds)),
                         "inr_fallback_worst": round(fallback_worst_inr(self.spec, n_in, len(b.rds)), 3)})
        out = {"n_batches": len(batches), "n_cached": len(batches) - len(unc), "uncached": rows,
               "inr_expected": round(sum(r["inr_expected"] for r in rows), 2),
               "inr_worst": round(sum(r["inr_worst"] for r in rows), 2),
               "inr_fallback_worst": round(max([r["inr_fallback_worst"] for r in rows] or [0.0]), 2)}
        if rows and not self.dry_run:
            self.guard.check(f"emotion_select: {len(rows)} uncached call(s), worst case + one safety split",
                             out["inr_worst"] + out["inr_fallback_worst"])
        return out

    def judge(self, b: _Batch, tag: str) -> tuple[dict, dict, list]:
        """Returns ({alias: raw entry}, {alias: block reason}, [aliases projected but not judged (dry run)])."""
        self.save_prompt(b, tag)
        if not self.cached(b):
            pj = call_projection(self.spec, self.tokens(b)[0], len(b.rds))
            if self.dry_run:
                self.proj.append({"video_id": b.vid, "batch": tag, "n_clips": len(b.rds), **pj})
                return {}, {}, [r.alias for r in b.rds]
            self.call_proj[ROLE] = pj["inr_worst"]   # llm.before_call checks it before every billed attempt
        try:
            obj = self.llm.generate_json(ROLE, self.spec, b.system, b.user, b.schema,
                                         meta={"video": b.vid, "batch": tag, "stage": VERSION})
        except SafetyBlocked as e:
            self.calls.append({"video_id": b.vid, "batch": tag, "clips": [r.alias for r in b.rds],
                               "status": "blocked", "reason": e.reason})
            self.log(f"  {b.vid} {tag}: blocked by Gemini's safety check ({e.reason})")
            if len(b.rds) == 1:
                return {}, {b.rds[0].alias: e.reason}, []
            k = len(b.rds) // 2
            got, blk, prj = {}, {}, []
            for h, (lo, hi) in enumerate(((0, k), (k, len(b.rds)))):
                sub = make_batch(b.vid, b.idxs[lo:hi], b.rds[lo:hi], self.ecfg)
                g, bl, pr = self.judge(sub, f"{tag}{'ab'[h]}")
                got.update(g)
                blk.update(bl)
                prj += pr
            return got, blk, prj
        self.calls.append({"video_id": b.vid, "batch": tag, "clips": [r.alias for r in b.rds], "status": "ok"})
        got = {}
        for ent in obj.get("clips") or []:
            a = str(ent.get("id", "")).strip()
            if a and a not in got:
                got[a] = ent
        return got, {}, []


def run_clips(clips: list[dict], episodes: dict, llm, ecfg: dict, guard, log=print, dry_run: bool = False,
              count_api: bool = True, call_proj: dict | None = None, out_dir: Path | None = None,
              names: dict | None = None) -> dict:
    """Judge ``clips`` (each with video_id, clip_id, time_segments, cold_open; extra fields passed through).
    ``episodes`` = {video_id: webseries.Episode}. ``names`` = {video_id: line id -> speaker name} (opt-in).
    Returns {results: [...], projection, calls, dry_run}."""
    core = ("video_id", "clip_id", "time_segments", "cold_open")
    prompt_dir = (out_dir / "prompts") if out_dir else None
    rend_dir = (out_dir / "rendered") if out_dir else None
    runner = Runner(llm, ecfg, guard, log, dry_run, count_api, call_proj, prompt_dir)
    by_vid: dict[str, list[int]] = {}
    for k, c in enumerate(clips):
        by_vid.setdefault(c["video_id"], []).append(k)
    batches: list[tuple[_Batch, str]] = []
    rendered: dict[int, Rendered] = {}
    for vid, ks in by_vid.items():
        ep = episodes[vid]
        for bn, grp in enumerate(batches_of(len(ks), int(ecfg["batch_size"]))):
            idxs = [ks[g] for g in grp]
            rds = [render_clip(ep, clips[i], f"C{j + 1}", (names or {}).get(vid)) for j, i in enumerate(idxs)]
            for i, r in zip(idxs, rds):
                rendered[i] = r
                if rend_dir is not None:
                    rend_dir.mkdir(parents=True, exist_ok=True)
                    (rend_dir / f"{_safe(vid)}_{_safe(clips[i]['clip_id'])}.txt").write_text(r.text + "\n",
                                                                                          encoding="utf-8")
            batches.append((make_batch(vid, idxs, rds, ecfg), f"batch{bn + 1:02d}"))
    pre = runner.preflight([b for b, _ in batches])
    if dry_run:
        llm.cache_only = True
    answers: dict[int, dict] = {}
    blocked: dict[int, str] = {}
    projected: set = set()
    for b, tag in batches:
        got, blk, prj = runner.judge(b, tag)
        alias_to_i = {r.alias: i for r, i in zip(b.rds, b.idxs)}
        for a, ent in got.items():
            if a in alias_to_i:
                answers[alias_to_i[a]] = ent
        for a, why in blk.items():
            blocked[alias_to_i[a]] = why
        projected |= {alias_to_i[a] for a in prj}
    results = []
    for k, c in enumerate(clips):
        rd = rendered[k]
        res = {"video_id": c["video_id"], "clip_id": c["clip_id"]}
        if k in answers:
            chk = check_answer(answers[k], rd, ecfg)
            res.update(verdict=chk["verdict"], stop=chk["stop"], stay=chk["stay"], land=chk["land"],
                       reason=str(answers[k].get("reason") or "").strip(), code_notes=chk["code_notes"],
                       raw=answers[k])
        elif k in blocked:
            res.update(verdict="unchecked", stop=None, stay=None, land=None, reason=UNCHECKED_NOTE,
                       code_notes=[f"code: blocked by Gemini's safety check (blockReason {blocked[k]})"], raw=None)
        elif k in projected:
            res.update(verdict="not_run", stop=None, stay=None, land=None, reason="",
                       code_notes=["dry run: not cached, not called"], raw=None)
        else:
            res.update(verdict="unchecked", stop=None, stay=None, land=None, reason="",
                       code_notes=["code: the model returned no entry for this clip"], raw=None)
        res["rendered_as"] = rd.alias
        res["plays_s"] = rd.duration
        extra = {kk: v for kk, v in c.items() if kk not in core}
        clash = {kk: v for kk, v in extra.items() if kk in res}
        res.update({kk: v for kk, v in extra.items() if kk not in res})
        if clash:
            res["input_extra"] = clash
        results.append(res)
    proj = {"preflight": pre, "dry_run_calls": runner.proj,
            "inr_expected": round(sum(p["inr_expected"] for p in runner.proj), 2),
            "inr_worst": round(sum(p["inr_worst"] for p in runner.proj), 2)}
    return {"results": results, "projection": proj, "calls": runner.calls, "n_batches": len(batches)}


def _safe(s) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(s))


# ================================================================================================ pipeline hook
def doc_fields(res: dict, ecfg: dict | None = None) -> dict:
    """Fields a proposal carries: verdict, reason and the checked parts."""
    from shotvi.select.prompts import load_prompt
    e = ecfg or DEFAULTS
    return {"emotion_verdict": res["verdict"], "emotion_reason": res.get("reason") or "",
            "emotion": {k: res.get(k) for k in ("stop", "stay", "land", "code_notes")}
            | {"version": VERSION, "prompt": f"{e['prompt']}@{load_prompt(e['prompt']).tag}"}}


def order_by_verdict(items: list, verdict_of_item) -> list:
    """yes, maybe, no, unchecked; the incoming order inside each group (stable)."""
    return sorted(items, key=lambda x: VERDICT_ORDER.get(verdict_of_item(x), 9))


def pipeline_clips(shown: list) -> list[dict]:
    return [{"video_id": None, "clip_id": x["id"], "time_segments": x["clip"]["time_segments"],
             "cold_open": bool(x["clip"].get("cold_open"))} for x in shown]


def apply_to_shown(ep, shown: list, cfg: dict, llm, guard, log=print, call_proj: dict | None = None,
                   debug: dict | None = None) -> list:
    """Web-series stage (opt-in): judge the shown clips, put the result on each (``x["emotion"]``) and order the
    list yes, maybe, no, unchecked (v1's order inside each group). Nothing is hidden."""
    ecfg = emo_cfg(cfg, pipeline=True)
    clips = pipeline_clips(shown)
    for c in clips:
        c["video_id"] = ep.video_id
    names = {ep.video_id: ws.names_of(ep, cfg)} if ws.names_of(ep, cfg) else None
    # a cache-only replay makes no provider call of any kind (countTokens included); a miss fails as in v1's stages
    out = run_clips(clips, {ep.video_id: ep}, llm, ecfg, guard, log, call_proj=call_proj, names=names,
                    count_api=not llm.cache_only)
    for x, r in zip(shown, out["results"]):
        x["emotion"] = r
    if debug is not None:
        debug["emotion_select"] = {"calls": out["calls"], "projection": out["projection"],
                                   "results": out["results"]}
    log(f"{ep.video_id}: emotion select: " + ", ".join(
        f"{v} {sum(r['verdict'] == v for r in out['results'])}" for v in VERDICT_ORDER))
    return order_by_verdict(shown, lambda x: x["emotion"]["verdict"])


def project_shown(ep, shown: list, cfg: dict, llm, count_api: bool = False) -> dict:
    """Dry-run projection of the stage on ``shown`` (no call; cached batches count 0)."""
    ecfg = emo_cfg(cfg, pipeline=True)
    clips = pipeline_clips(shown)
    for c in clips:
        c["video_id"] = ep.video_id
    names = {ep.video_id: ws.names_of(ep, cfg)} if ws.names_of(ep, cfg) else None
    was = llm.cache_only
    try:
        out = run_clips(clips, {ep.video_id: ep}, llm, ecfg, ws.Guard(None), log=lambda *_: None, dry_run=True,
                        count_api=count_api, call_proj={}, names=names)
    finally:
        llm.cache_only = was
    return out["projection"]


# ================================================================================================ CLI
def clips_from_run(run_dir: Path) -> tuple[list[dict], dict, dict]:
    """Every shown proposal of a v1-format run dir (``<run>/<video>.json``, kind proposals), in rank order."""
    clips, docs, speaker_names = [], {}, {}
    for f in sorted(run_dir.glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict) or doc.get("kind") != "proposals":
            continue
        vid = doc["video_id"]
        docs[vid] = doc
        speaker_names[vid] = bool(((doc.get("pipeline") or {}).get("config") or {}).get("speaker_names"))
        for p in sorted(doc.get("proposals") or [], key=lambda p: p.get("rank", 0)):
            if not p.get("time_segments"):
                continue
            clips.append({"video_id": vid, "clip_id": p["id"], "time_segments": p["time_segments"],
                          "cold_open": bool(p.get("cold_open")),
                          "rank": p.get("rank"), "tier": p.get("tier"), "title": p.get("title")})
    return clips, docs, speaker_names


def reordered_doc(doc: dict, res_by_id: dict, ecfg: dict) -> dict:
    d = copy.deepcopy(doc)
    props = sorted(d["proposals"], key=lambda p: p.get("rank", 0))
    for p in props:
        r = res_by_id.get(p["id"])
        if r is not None:
            p.update(doc_fields(r, ecfg))
    props = order_by_verdict(props, lambda p: p.get("emotion_verdict", "unchecked"))
    for k, p in enumerate(props):
        p["rank_before_emotion"] = p.get("rank")
        p["rank"] = k + 1
    d["proposals"] = props
    d["emotion_select"] = {"version": VERSION, "order": "yes, maybe, no, unchecked; the run's order inside each"}
    return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Emotion select: retention check on clips as played (web series)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--clips", help="JSON list of {video_id, clip_id, time_segments, cold_open, ...extra}")
    src.add_argument("--run", help="a v1-format run dir: every shown proposal of every <video>.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true", help="no generate call: replay cached batches, project the rest")
    ap.add_argument("--offline", action="store_true", help="dry run without countTokens (estimate input offline)")
    ap.add_argument("--max-ledger-inr", type=float, default=None,
                    help="stop before any call whose worst case would push the project ledger past this")
    ap.add_argument("--config", default=None, help="JSON deep-merged over this stage's DEFAULTS")
    ap.add_argument("--no-retry", action="store_true", help="no automatic retries (each retry is billed)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    os.chdir(ROOT)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from shotvi.llm import Ledger, LLMClient
    from shotvi.llm.spend import total_inr
    from shotvi.select.prompts import load_prompt
    ecfg = emo_cfg(json.loads(Path(a.config).read_text(encoding="utf-8")) if a.config else {})
    out = Path(a.out)
    os.environ.setdefault("SHOTVI_SPEND_SOURCE", f"emotion_select:{out.name}")
    docs, want_names = {}, {}
    if a.run:
        clips, docs, want_names = clips_from_run(Path(a.run))
    else:
        clips = json.loads(Path(a.clips).read_text(encoding="utf-8"))
        for c in clips:
            for k in ("video_id", "clip_id", "time_segments"):
                if k not in c:
                    ap.error(f"--clips: every clip needs {k} (missing in {str(c)[:80]})")
            c.setdefault("cold_open", False)
    if not clips:
        print("no clips")
        return 1
    episodes, names = {}, {}
    for vid in dict.fromkeys(c["video_id"] for c in clips):
        sn = want_names.get(vid, False)
        ep, *_ = ws.load_episode(vid, with_loudness=False, speaker_names=sn, require_names=not a.dry_run)
        episodes[vid] = ep
        if sn and ep.names:
            names[vid] = ep.names
    led0 = total_inr()
    guard = ws.Guard(a.max_ledger_inr)
    llm = LLMClient(Ledger(max_usd=ecfg["budget_usd"]), cache_dir="runs/cache",
                    **({"max_retries": 0, "schema_retries": 0} if a.no_retry else {}))
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        res = run_clips(clips, episodes, llm, ecfg, guard, print, dry_run=a.dry_run,
                        count_api=not a.offline, out_dir=out, names=names)
    except ws.BudgetStop as e:
        print(f"STOP: {e}")
        return 2
    pre = res["projection"]["preflight"]
    tag = load_prompt(ecfg["prompt"]).tag
    print(f"{len(clips)} clips from {len(episodes)} episode(s), {res['n_batches']} batch call(s) "
          f"({pre['n_cached']} cached), prompt {ecfg['prompt']}@{tag}, model {ecfg['model']['model']} "
          f"thinking {ecfg['model']['params'].get('thinking_level')}")
    if a.dry_run:
        pj = res["projection"]
        dry = {"n_clips": len(clips), "n_calls": res["n_batches"], "n_cached": pre["n_cached"],
               "would_bill_inr_expected": pj["inr_expected"], "would_bill_inr_worst": pj["inr_worst"],
               "plus_safety_split_worst_inr": pre["inr_fallback_worst"], "calls": pj["dry_run_calls"],
               "ledger_inr": round(led0, 2), "prompt": f"{ecfg['prompt']}@{tag}", "out_expected_basis": OUT_EXPECTED}
        (out / "dry_run.json").write_text(json.dumps(dry, ensure_ascii=False, indent=1), encoding="utf-8")
        for p in pj["dry_run_calls"]:
            print(f"  {p['video_id']} {p['batch']}: {p['n_clips']} clips, {p['in']} in, out {p['out_expected']} "
                  f"expected / {p['out_worst']} worst -> Rs {p['inr_expected']:.2f} expected, "
                  f"Rs {p['inr_worst']:.2f} worst")
        print(f"DRY RUN: {len(pj['dry_run_calls'])} uncached call(s) would bill Rs {pj['inr_expected']:.2f} expected, "
              f"Rs {pj['inr_worst']:.2f} worst (every call at its whole output budget), plus up to "
              f"Rs {pre['inr_fallback_worst']:.2f} if Gemini's safety check blocks a batch; ledger Rs {led0:.2f}"
              + (f", limit Rs {a.max_ledger_inr:.2f}: "
                 f"{'fits' if led0 + pj['inr_worst'] + pre['inr_fallback_worst'] <= a.max_ledger_inr else 'does NOT fit'}"
                 if a.max_ledger_inr is not None else ""))
        print(f"rendered clips: {out / 'rendered'}; prompts: {out / 'prompts'}")
        return 0
    results = res["results"]
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    summ = llm.ledger.summary()
    counts = {v: sum(r["verdict"] == v for r in results) for v in VERDICT_ORDER}
    summary = {"version": VERSION, "prompt": f"{ecfg['prompt']}@{tag}", "model": ecfg["model"],
               "n_clips": len(clips), "verdicts": counts, "n_batches": res["n_batches"], "calls": res["calls"],
               "cost": {k: summ[k] for k in ("n_llm_calls", "n_cache_hits", "input_tokens", "output_tokens", "usd",
                                             "usd_billed")} | {"inr_billed": round(ws.inr(summ["usd_billed"]), 2),
                                                               "inr_full": round(ws.inr(summ["usd"]), 2)},
               "projection": res["projection"]["preflight"], "wall_time_s": round(time.time() - t0, 1)}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    if docs:
        for vid, doc in docs.items():
            rid = {r["clip_id"]: r for r in results if r["video_id"] == vid}
            (out / f"{vid}.json").write_text(json.dumps(reordered_doc(doc, rid, ecfg), ensure_ascii=False, indent=1),
                                             encoding="utf-8")
    print(f"verdicts: {counts}; billed Rs {summary['cost']['inr_billed']:.2f} "
          f"(full price Rs {summary['cost']['inr_full']:.2f}, {summ['n_cache_hits']} cache hits); "
          f"ledger Rs {total_inr():.2f} -> {out / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
