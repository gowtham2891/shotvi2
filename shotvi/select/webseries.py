"""Automatic web-series clip pipeline v1 (additive: the podcast pipeline, discover v1 and its config are untouched).

Stages (per episode):
  1. transcript in: Scribe lines + audio-event lines with the ASR correction layer (``LineIndex(asr_fix=True)``).
  2. discover (one text call, prompt ``ws_discover``): web-series moments as LINE IDS only: purpose (standalone /
     promo), start line, payoff/end line, hook and payoff rationale, an optional cold-open lift (line ids), up to 3
     whole-chunk drops (line ranges) and a spoiler flag.
  3. cut builder (deterministic, no model): line ids -> times.
       start   first spoken word of the start line, pad <= 0.3 s (a shot cut in that pad wins); never music/silence
       lift    1-3.6 s, ends 0.005 s before a shot cut right after the line, not mid-laugh, not a quiet tail, not a
               question/answer orphan, not ending on a comma; skipped IN PLACE later (never replayed)
       drops   whole lines, at most 3 internal cuts in total (lift skip included), each <= 45 s, edges snapped to
               shot cuts within 1.5 s (inside the gap only: never into a kept word)
       end     payoff line end + reaction hold to the next shot cut (2-8 s; through a laugh event), never into the
               next spoken line; capped at 4 s before a long silence (song, credits, scene change). ws/0.2: when the
               next line starts before the minimum hold, a short reaction line is played and held to the shot
               change after it; else the clip ends on a shot change before the next line; else just before it
       flashes ws/0.2: any segment edge showing < 0.3 s of another shot moves to the shot change unless that
               clips a played word (then it stays and is reported in ``shot_flags``)
     -> ``time_segments`` (play order, may include silent picture) + word ``pieces``.
  3b. review (one text call, prompt ``ws_review``): scores the clips AS BUILT (play order, cut markers), may veto
     a cold open or flag bad cuts (code then rebuilds without them; no further call), writes title / why.
     A clip repaired this way keeps its reviewer tier (worth_a_look if that tier is not shown) and is flagged
     ``needs_rescore``: its scores belong to the broken cut, so they never reject it.
  4. (optional, off by default) video end check: ``video_check`` clip2 with shot lines; only the END suggestion
     is applied, capped and flagged.
  5. rank by score, quality cutoff (no fixed N), write ``<out>/<video_id>.json`` (schema 1.1 + web-series fields).

CLI::

    python -m shotvi.select.webseries <video_id> [...] --out runs/ws_r1 --dry-run
    SHOTVI_SPEND_SOURCE=ws_r1 python -m shotvi.select.webseries QWIvgoTQGKk --out runs/ws_r1 --max-ledger-inr 1286
    # free rebuild of ws_r1 from its cached responses (the review saw the ws/0.1 ending):
    python -m shotvi.select.webseries j14Om1SaFYE --out runs/ws_r1 --cache-only \
        --config shotvi/select/configs/ws_r1_replay.json
    python -m shotvi.select.webseries QWIvgoTQGKk --out runs/ws_r1 --cache-only \
        --config shotvi/select/configs/ws_r1_replay_review_high.json
    # A/B arm against the shared cached baseline ws_r1: same config, ONE swapped prompt file; free dry run first
    # (no call of any kind, countTokens included; expected rupees calibrated on ws_r1's token counts)
    python -m shotvi.select.webseries QWIvgoTQGKk --out runs/ws_ab_setup --dry-run --calibrate-from runs/ws_r1 \
        --config shotvi/select/configs/ws_r1_replay_review_high.json --prompt ws_discover=ws_discover_b_setup \
        --max-ledger-inr 1309
    SHOTVI_SPEND_SOURCE=ws_ab_setup python -m shotvi.select.webseries QWIvgoTQGKk --out runs/ws_ab_setup \
        --config shotvi/select/configs/ws_r1_replay_review_high.json --prompt ws_discover=ws_discover_b_setup \
        --max-ledger-inr 1309

Spend guard (``--max-ledger-inr``): checked before every billed attempt (retries included) against the project
rupee ledger, plus once per episode before an uncached discover for discover + review together, so the run never
pays for a discover whose review the limit would then refuse.
"""
from __future__ import annotations

import argparse
import bisect
import copy
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
PIPELINE_VERSION = "ws/0.2"
PROMPTS = ("ws_discover", "ws_review")

_FLASH = {"provider": "gemini", "model": "gemini-3.8-flash"}
DEFAULTS: dict = {
    "models": {
        "ws_discover": {**_FLASH, "params": {"thinking_level": "high", "max_output_tokens": 32768}},
        "ws_review": {**_FLASH, "params": {"thinking_level": "medium", "max_output_tokens": 16384}},
        "video_end": {**_FLASH, "params": {"thinking_level": "low"}},
    },
    "cut": {
        "start_pad_s": 0.3,          # max lead-in before the first spoken word
        "lift_min_s": 1.0, "lift_max_s": 3.6,
        "lift_cut_eps_s": 0.005,     # a lift ends this far before the shot cut (no frame of the next shot)
        "lift_start_snap_s": 0.4,    # a shot cut this close before the lift's first word starts the lift
        "lift_start_pad_s": 0.12,
        "lift_max_tail_s": 2.0,      # picture after the lift's last word, before the cut
        "lift_tail_measure_s": 0.4,  # tails at least this long get a loudness check
        "lift_quiet_tail_db": 15.0,  # tail this much quieter than the lift's speech = quiet tail
        "lift_level_warn_db": 10.0,  # lift vs scene opening loudness gap that is flagged (preview levels it)
        "max_cuts": 3,               # internal cuts (chunk drops + the lift's in-place skip)
        "max_drop_s": 45.0,          # a longer removal is a scene stitch, not a chunk drop: ignored
        "snap_s": 1.5,               # drop edges snap to shot cuts within this (inside the gap)
        "join_pad_s": 0.25,
        "hold_min_s": 2.0, "hold_max_s": 8.0, "hold_short_min_s": 0.5, "hold_default_s": 2.0,
        "next_speech_margin_s": 0.1,
        "long_silence_s": 20.0, "long_silence_hold_max_s": 4.0,
        # ws/0.2 end when the next line comes before the minimum hold (see end_hold)
        "react_max_s": 1.5, "react_max_words": 3,   # a next line this short is a reaction / interjection
        "react_clear_s": 1.0,        # ... only if nobody speaks for this long after it (else it opens an exchange)
        "quick_cut_min_s": 0.02,     # a shot change at least this far after the last word can end the clip
        "quick_end_margin_s": 0.05,  # no shot change: end this far before the next line's first word
        # ws/0.2 flash repair (every segment edge, lift and in-place skips included)
        "flash_s": 0.3,              # a sliver of another shot shorter than this at a segment edge is a flash
        "shot_tol_s": 0.05,          # "on a shot change" = within this of a detected cut
        "word_clip_tol_s": 0.02,     # moving an edge more than this into a played word = clipping speech
    },
    # what the review call sees: "final" = the clip as built; "ws/0.1" = the ws/0.1 end hold without flash repair
    # (replays run ws_r1's cached reviews; the finished clip is still what is written)
    "review_view": "final",
    "guardrails": {"min_s": 15.0, "soft_max_s": 120.0, "hard_max_s": 150.0, "skip_s": 30.0, "dup_overlap": 0.5,
                   "blocks_flag_above": 3},
    "thresholds": {"t_show": 3.5, "t_rec": 4.0, "below_threshold_max": 3, "below_min_overall": 3.0},
    "video_end_check": {"enabled": False, "max_shift_s": 8.0},
    # prompt file per role (prompts/<name>.md); an A/B arm swaps exactly one (CLI --prompt ROLE=FILE)
    "prompts": {"ws_discover": "ws_discover", "ws_review": "ws_review"},
    "budget_usd": 0.6,
}
CATEGORIES = ["story", "advice", "humour", "controversy", "emotional", "insight", "reveal", "motivation", "other"]
LAUGH_TOKENS = ("నవ్వు", "laugh")
CENSOR_TOKENS = ("సెన్సార్డ్", "censor")
SONG_TOKENS = ("పాట", "song")


def deep_merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def norm_id(x) -> str | None:
    if x is None:
        return None
    s = str(x).strip().upper()
    if not s or s in ("NONE", "NULL", "-"):
        return None
    pre = "S" if s.startswith("S") else "L"
    try:
        return f"{pre}{int(s.lstrip('LS')):04d}"
    except ValueError:
        return None


def fmt(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


# ======================================================================================== episode + cut builder
@dataclass
class Episode:
    video_id: str
    lines: list[dict]          # LineIndex lines: speech (with "words": [i0, i1]) and audio events, time order
    words: list[dict]
    cuts: list[float]
    duration: float
    loudness: Callable[[float, float], float | None] | None = None
    by_id: dict = field(init=False)
    pos: dict = field(init=False)

    def __post_init__(self):
        self.cuts = sorted(self.cuts)
        self.by_id = {r["id"]: r for r in self.lines}
        self.pos = {r["id"]: k for k, r in enumerate(self.lines)}
        self.events = [r for r in self.lines if r["kind"] == "event"]
        self.speech = sorted((r for r in self.lines if self.is_speech(r)), key=lambda r: r["words"][0])
        self._sp_w0 = [r["words"][0] for r in self.speech]

    def speech_line_at(self, i: int):
        """The spoken line that contains word i (None if no line does)."""
        k = bisect.bisect_right(self._sp_w0, i) - 1
        if k >= 0 and self.speech[k]["words"][0] <= i <= self.speech[k]["words"][1]:
            return self.speech[k]
        return None

    def is_speech(self, r) -> bool:
        return r is not None and r["kind"] == "speech" and bool(r.get("words"))

    def cuts_in(self, a: float, b: float) -> list[float]:
        if b < a:
            return []
        return self.cuts[bisect.bisect_left(self.cuts, a):bisect.bisect_right(self.cuts, b)]

    def prev_end(self, i: int) -> float:
        return self.words[i - 1]["end"] if i > 0 else 0.0

    def next_start(self, i: int) -> float | None:
        return self.words[i + 1]["start"] if i + 1 < len(self.words) else None

    def w0(self, r) -> float:
        return self.words[r["words"][0]]["start"]

    def w1(self, r) -> float:
        return self.words[r["words"][1]]["end"]

    def text(self, r) -> str:
        return r.get("text", "")


class Reject(Exception):
    pass


def _ends_q(text: str) -> bool:
    return text.rstrip(" \"'”’.!…").endswith("?") or text.rstrip().endswith("?")


def clip_start(ep: Episode, r, cfg) -> float:
    """First spoken word of line r with <= start_pad_s lead-in; a shot cut inside that pad (and after the previous
    word) is used instead, so the clip opens on a clean picture, never on music or silence."""
    i0 = r["words"][0]
    w0, pe = ep.words[i0]["start"], ep.prev_end(i0)
    lo = max(pe + 0.02, w0 - cfg["start_pad_s"])
    cs = ep.cuts_in(lo, w0)
    if cs:
        return round(cs[-1], 3)
    return round(max(0.0, w0 - min(cfg["start_pad_s"], max(0.0, w0 - pe - 0.02))), 3)


def join_out(ep: Episode, last_word: int, cfg) -> tuple[float, bool]:
    """End of a kept run before a removed chunk: the first shot cut after the run's last word (inside the gap,
    within snap_s), else a small pad."""
    e = ep.words[last_word]["end"]
    g = ep.next_start(last_word)
    g = g if g is not None else ep.duration
    cs = ep.cuts_in(e + 0.05, min(e + cfg["snap_s"], g - 0.05))
    if cs:
        return round(cs[0] - cfg["lift_cut_eps_s"], 3), True
    return round(e + min(cfg["join_pad_s"], max(0.0, (g - e) / 2)), 3), False


def join_in(ep: Episode, first_word: int, cfg) -> tuple[float, bool]:
    """Start of a kept run after a removed chunk: the last shot cut before its first word (inside the gap, within
    snap_s), else a small pad."""
    s = ep.words[first_word]["start"]
    h = ep.prev_end(first_word)
    cs = ep.cuts_in(max(h + 0.05, s - cfg["snap_s"]), s)
    if cs:
        return round(cs[-1], 3), True
    return round(s - min(cfg["join_pad_s"], max(0.0, (s - h) / 2)), 3), False


def _laughs_after(ep: Episode, e: float) -> list:
    return [ev for ev in ep.events if any(t in ev["text"] for t in LAUGH_TOKENS) and e - 0.5 <= ev["start"] <= e + 1.5]


def end_hold(ep: Episode, last_word: int, cfg) -> dict:
    """Payoff end + reaction hold (ws/0.2).

    When the next spoken line leaves room for the minimum hold, this is the ws/0.1 hold (``end_hold_v1``: to a shot
    cut 2-8 s after the payoff, through a laugh, never into the next line, short before a long silence). When the
    next line starts sooner ("quick"), in this order:
      a. reaction: the next line is short (<= react_max_s and <= react_max_words), nobody speaks for react_clear_s
         after it, and a shot change follows it within the caps: play it and hold to that shot change;
      b. else end on the last shot change before the next line starts;
      c. else end just before the next line: max(last word end + small pad, next line start - quick_end_margin_s).
    ``rule`` in the result says which one applied."""
    e = ep.words[last_word]["end"]
    nx = ep.next_start(last_word)
    margin, eps = cfg["next_speech_margin_s"], cfg["lift_cut_eps_s"]
    if nx is None or nx - margin >= e + cfg["hold_min_s"]:
        return {**end_hold_v1(ep, last_word, cfg), "rule": "hold"}
    laugh = _laughs_after(ep, e)
    base = {"payoff_end": round(e, 3), "next_speech": round(nx, 3), "long_silence_after": False,
            "laugh_after": bool(laugh)}

    # a. a short reaction / interjection right after the payoff
    r = ep.speech_line_at(last_word + 1)
    if r is not None and r["words"][0] == last_word + 1:
        i0, i1 = r["words"]
        e2 = ep.words[i1]["end"]
        dur, nw = e2 - ep.words[i0]["start"], i1 - i0 + 1
        n2 = ep.next_start(i1)
        # short in time AND in words: "or" would take a fast 6-word sentence or a stretched 3-word line
        if dur <= cfg["react_max_s"] and nw <= cfg["react_max_words"] and \
                (n2 is None or n2 - e2 >= cfg["react_clear_s"]):
            lim = e + cfg["hold_max_s"]
            lim = min(lim, n2 - margin) if n2 is not None else min(lim, ep.duration)
            long2 = n2 is None or n2 - e2 > cfg["long_silence_s"]
            if long2:
                lim = min(lim, e2 + cfg["long_silence_hold_max_s"])
            cs = ep.cuts_in(e2 + 0.05, lim + eps)
            if cs:
                want = max([e + cfg["hold_min_s"]] + [ev["end"] + 0.3 for ev in laugh])
                pref = [c for c in cs if c >= want]
                end = (pref[0] if pref else cs[-1]) - eps
                return {**base, "end": round(end, 3), "hold_s": round(end - e, 2), "on_shot_cut": True,
                        "next_speech": None if n2 is None else round(n2, 3), "long_silence_after": long2,
                        "rule": "reaction",
                        "reaction": {"line": r["id"], "text": ep.text(r), "end": round(e2, 3)}}
    # b. a shot change before the next line starts
    cs = ep.cuts_in(e + cfg["quick_cut_min_s"], nx - cfg["quick_end_margin_s"] + eps)
    if cs:
        end = cs[-1] - eps
        return {**base, "end": round(end, 3), "hold_s": round(end - e, 2), "on_shot_cut": True,
                "rule": "shot_change_before_next_line", "note": "next line starts quickly: ends on the shot change"}
    # c. just before the next line
    end = max(e + min(0.05, (nx - e) / 2), nx - cfg["quick_end_margin_s"])
    return {**base, "end": round(end, 3), "hold_s": round(end - e, 2), "on_shot_cut": False,
            "rule": "before_next_line", "note": "next line starts quickly and no shot change before it"}


def end_hold_v1(ep: Episode, last_word: int, cfg) -> dict:
    """ws/0.1 end: payoff end + reaction hold, extended to a shot cut, capped; never into the next spoken word;
    short before a long silence (song, credits, scene change). Kept as the full rule when the minimum hold fits
    before the next line, and as the ``review_view: ws/0.1`` ending."""
    e = ep.words[last_word]["end"]
    nx = ep.next_start(last_word)
    limit = e + cfg["hold_max_s"]
    limit = min(limit, nx - cfg["next_speech_margin_s"]) if nx is not None else min(limit, ep.duration)
    long_silence = nx is None or nx - e > cfg["long_silence_s"]
    if long_silence:
        limit = min(limit, e + cfg["long_silence_hold_max_s"])
    limit = max(limit, e + 0.05)
    min_end = e + cfg["hold_min_s"]
    laugh = _laughs_after(ep, e)
    for ev in laugh:        # hold through a laugh that follows the payoff
        min_end = max(min_end, ev["end"] + 0.3)
    min_end = min(min_end, limit)
    eps = cfg["lift_cut_eps_s"]
    cs = ep.cuts_in(min_end, limit + eps)
    note = ""
    if cs:
        end, on_cut = cs[0] - eps, True
    else:
        short = ep.cuts_in(e + cfg["hold_short_min_s"], min_end)
        if short:
            end, on_cut, note = short[-1] - eps, True, "short hold (next shot cut before the minimum hold)"
        else:
            end, on_cut, note = min(e + cfg["hold_default_s"], limit), False, "hold not on a shot cut"
    end = max(end, e + 0.05)
    return {"payoff_end": round(e, 3), "end": round(end, 3), "hold_s": round(end - e, 2), "on_shot_cut": on_cut,
            "next_speech": None if nx is None else round(nx, 3), "long_silence_after": long_silence,
            "laugh_after": bool(laugh), **({"note": note} if note else {})}


def lift_position_check(S: list, ks: list[int], purpose: str):
    """Where the lift may come from (before any timing): inside the clip, not its first or last line."""
    if not ks:
        raise Reject("no lift")
    if ks[0] == 0:
        raise Reject("lift is the start line")
    if ks[-1] == len(S) - 1:
        raise Reject("lift is the payoff/end line (skipping it in place would cut the ending)")
    if purpose == "promo" and ks[-1] >= len(S) - 2:
        raise Reject("lift is the line right before a promo's cliffhanger")
    if ks != list(range(ks[0], ks[-1] + 1)):
        raise Reject("lift lines are not consecutive")


def build_lift(ep: Episode, S: list, ks: list[int], kept: set, cfg, purpose: str) -> dict:
    """Validate and time a cold-open lift (speech lines S[ks]). Raises Reject with the reason."""
    lift_position_check(S, ks, purpose)
    first, last = S[ks[0]], S[ks[-1]]
    i0, i1 = first["words"][0], last["words"][1]
    w0, wl = ep.words[i0]["start"], ep.words[i1]["end"]
    if wl - w0 > cfg["lift_max_s"]:
        raise Reject(f"lift speech is {wl - w0:.1f} s (> {cfg['lift_max_s']} s)")
    lift_text = " ".join(ep.text(S[k]) for k in ks).strip()
    if lift_text.rstrip().endswith((",", "-", "…", "...")):
        raise Reject("lift ends mid-sentence (comma/ellipsis)")
    pe = ep.prev_end(i0)
    cs = ep.cuts_in(max(pe + 0.02, w0 - cfg["lift_start_snap_s"]), w0)
    t0 = cs[-1] if cs else w0 - min(cfg["lift_start_pad_s"], max(0.0, (w0 - pe) / 2))
    nx = ep.next_start(i1)
    hi = min(nx if nx is not None else ep.duration, t0 + cfg["lift_max_s"] + cfg["lift_cut_eps_s"],
             wl + cfg["lift_max_tail_s"])
    eps = cfg["lift_cut_eps_s"]
    t1 = None
    for c in ep.cuts_in(wl, hi):
        if c - eps - t0 >= cfg["lift_min_s"]:
            t1 = c - eps
            break
    if t1 is None:
        raise Reject("no shot cut right after the line (lift would end mid-shot or run into the next line)")
    for ev in ep.events:
        if any(t in ev["text"] for t in LAUGH_TOKENS) and ev["start"] < t1 - 0.05 and ev["end"] > t1 + 0.15:
            raise Reject("lift ends mid-laugh")
    out = {"lines": [S[k]["id"] for k in ks], "text": lift_text, "start": round(t0, 3), "end": round(t1, 3),
           "duration": round(t1 - t0, 2), "tail_s": round(t1 - wl, 2), "start_on_cut": bool(cs),
           "word_range": [i0, i1]}
    if ep.loudness and t1 - wl >= cfg["lift_tail_measure_s"]:
        db_sp, db_tail = ep.loudness(w0, wl), ep.loudness(wl, t1)
        if db_sp is not None and db_tail is not None:
            out["tail_db_below_speech"] = round(db_sp - db_tail, 1)
            if db_sp - db_tail > cfg["lift_quiet_tail_db"]:
                raise Reject(f"lift ends on a quiet tail ({db_sp - db_tail:.0f} dB below its speech)")
    # reading across the in-place skip: the line before / after the lifted line in the scene as played
    before = [k for k in range(ks[0] - 1, -1, -1) if k in kept]
    after = [k for k in range(ks[-1] + 1, len(S)) if k in kept]
    if before and _ends_q(ep.text(S[before[0]])):
        raise Reject("the line before the lift is a question: skipping the lift in place orphans it")
    if after and _ends_q(ep.text(S[ks[-1]])):
        raise Reject("the lift is a question answered next: the answer would play without it")
    return out


def _loudness_gap(ep: Episode, lift: dict, scene_start: float, cfg) -> float | None:
    if not ep.loudness:
        return None
    a = ep.loudness(lift["start"], lift["end"])
    b = ep.loudness(scene_start, scene_start + 4.0)
    return None if a is None or b is None else round(a - b, 1)


def fix_flashes(ep: Episode, segs: list[dict], cfg) -> list[dict]:
    """Repair shot flashes at segment edges, in place (ws/0.2).

    A flash: a segment edge that shows a sliver (< flash_s) of a shot the edit opens or closes, i.e. a shot change
    within flash_s inside the segment's start or end, and that shot is not itself a short source shot played whole.
    The edge moves to the shot change (an end to 0.005 s before it) when no word played in the segment loses more
    than word_clip_tol_s; otherwise the edge stays (speech is never clipped) and the flash is reported. Covers starts
    and ends of every segment: the cold open, the scene start, chunk-drop joins, in-place lift skips and the end
    hold. Returns one record per flash found: {seg, kind, edge, cut, sliver_s, from, to, fixed[, word, word_s]}."""
    fs, tol, eps, wt = cfg["flash_s"], cfg["shot_tol_s"], cfg["lift_cut_eps_s"], cfg["word_clip_tol_s"]
    C, W = ep.cuts, ep.words
    starts = [w["start"] for w in W]
    out = []
    for k, s in enumerate(segs):
        a, b = s["start"], s["end"]
        inside = C[bisect.bisect_right(C, a + tol):bisect.bisect_left(C, b - tol)]
        if not inside:
            continue
        lo = bisect.bisect_left(starts, a - 0.01)
        played = []
        for i in range(lo, len(W)):
            if W[i]["start"] > b + 0.01:
                break
            if W[i]["end"] <= b + 0.01:
                played.append(i)
        # end: a sliver after the last shot change inside the segment
        c = inside[-1]
        j = bisect.bisect_right(C, c)
        if b - c < fs and (j >= len(C) or C[j] > b + tol):
            new = round(c - eps, 3)
            hit = [i for i in played if W[i]["end"] > new + wt]
            rec = {"seg": k, "kind": s.get("kind", "scene"), "edge": "end", "cut": round(c, 3),
                   "sliver_s": round(b - c, 3), "from": b}
            if hit and new > a:
                rec.update(fixed=False, to=b, word=W[hit[0]]["word"], word_s=round(W[hit[0]]["end"] - c, 3))
            elif new > a:
                s["end"] = new
                rec.update(fixed=True, to=new)
            else:
                rec.update(fixed=False, to=b)
            out.append(rec)
        # start: a sliver before the first shot change inside the segment
        b = s["end"]
        inside = [x for x in inside if x < b - tol]
        if not inside:
            continue
        c = inside[0]
        j = bisect.bisect_left(C, c) - 1
        if c - a < fs and (j < 0 or C[j] < a - tol):
            hit = [i for i in played if W[i]["start"] < c - wt and W[i]["end"] <= b + 0.01]
            rec = {"seg": k, "kind": s.get("kind", "scene"), "edge": "start", "cut": round(c, 3),
                   "sliver_s": round(c - a, 3), "from": a}
            if hit:
                rec.update(fixed=False, to=a, word=W[hit[0]]["word"], word_s=round(c - W[hit[0]]["start"], 3))
            else:
                s["start"] = round(c, 3)
                rec.update(fixed=True, to=round(c, 3))
            out.append(rec)
    return out


def flash_text(f: dict) -> str:
    where = f"seg{f['seg']} ({f['kind']}) {f['edge']}"
    side = "after" if f["edge"] == "end" else "before"
    if f["fixed"]:
        return f"flash repaired: {where} moved {f['from']:.3f} -> {f['to']:.3f} (was a {f['sliver_s']:.2f} s sliver " \
               f"{side} the shot change at {f['cut']:.2f} s)"
    if "word" in f:
        return f"flash kept: {where} shows {f['sliver_s']:.2f} s {side} the shot change at {f['cut']:.2f} s; " \
               f"moving it would clip '{f['word']}' ({f['word_s']:.2f} s of the word lies {side} the cut)"
    return f"flash kept: {where} shows {f['sliver_s']:.2f} s {side} the shot change at {f['cut']:.2f} s"


def build_clip(ep: Episode, m: dict, cfg: dict, no_lift: bool = False, skip_drops: set | None = None,
               view: str = "final") -> dict:
    """Moment (line ids) -> clip with time_segments and word pieces. Raises Reject for an unusable moment.
    ``view="ws/0.1"`` builds the ws/0.1 version (end hold v1, no flash repair): what run ws_r1's review saw."""
    warns: list[str] = []
    sid, eid = norm_id(m.get("start_line")), norm_id(m.get("end_line"))
    if sid not in ep.by_id or eid not in ep.by_id:
        raise Reject(f"unknown line id(s) {m.get('start_line')}..{m.get('end_line')}")
    ps, pe = ep.pos[sid], ep.pos[eid]
    if pe < ps:
        raise Reject(f"end line {eid} before start line {sid}")
    S = [r for r in ep.lines[ps:pe + 1] if ep.is_speech(r)]
    if not S:
        raise Reject("no spoken line in the range")
    if not ep.is_speech(ep.by_id[sid]):
        warns.append(f"start line {sid} is not speech: starts at {S[0]['id']}")
    if not ep.is_speech(ep.by_id[eid]):
        warns.append(f"end line {eid} is an audio event: payoff = {S[-1]['id']}, the hold covers the event")
    idx = {r["id"]: k for k, r in enumerate(S)}

    # ---- drops: whole lines strictly inside the clip
    drops = []
    for j, d in enumerate(m.get("drops") or []):
        if skip_drops and j in skip_drops:
            continue
        a, b = norm_id(d.get("first_line")), norm_id(d.get("last_line") or d.get("first_line"))
        if a not in ep.by_id or b not in ep.by_id:
            warns.append(f"drop {j + 1}: unknown line id(s); ignored")
            continue
        pa, pb = sorted((ep.pos[a], ep.pos[b]))
        sp = [idx[r["id"]] for r in ep.lines[pa:pb + 1] if r["id"] in idx]
        if pa <= ep.pos[S[0]["id"]] or pb >= ep.pos[S[-1]["id"]]:
            warns.append(f"drop {j + 1} ({a}..{b}) touches the start/end line or lies outside; ignored")
            continue
        gap_k = None
        if not sp:      # audio events only: remove the silent gap between the two spoken lines around it
            t_mid = (ep.lines[pa]["start"] + ep.lines[pb]["end"]) / 2
            gap_k = next((k for k in range(1, len(S)) if ep.w1(S[k - 1]) <= t_mid <= ep.w0(S[k]) + 0.01), None)
            if gap_k is None:
                warns.append(f"drop {j + 1} ({a}..{b}): no silent gap to remove; ignored")
                continue
        drops.append({"j": j, "first_line": ep.lines[pa]["id"], "last_line": ep.lines[pb]["id"],
                      "why": d.get("why", ""), "speech": set(sp), "gap_k": gap_k})

    # ---- lift candidate
    lift_ids = [norm_id(x) for x in (m.get("cold_open_lines") or [])]
    lift_ids = [x for x in lift_ids if x]
    lift_ks: list[int] = []
    lift_reject = None
    if lift_ids and not no_lift:
        if any(x not in idx for x in lift_ids):
            lift_reject = "lift line is not a spoken line inside the clip"
        else:
            lift_ks = sorted({idx[x] for x in lift_ids})
            try:
                lift_position_check(S, lift_ks, m.get("purpose", "standalone"))
            except Reject as e:
                lift_reject, lift_ks = str(e), []

    def runs_for(active: list, with_lift: bool):
        removed = set().union(*[d["speech"] for d in active]) if active else set()
        if with_lift:
            removed |= set(lift_ks)
        breaks = {d["gap_k"] for d in active if d["gap_k"] is not None}
        kept = [k for k in range(len(S)) if k not in removed]
        runs: list[list[int]] = []
        for k in kept:
            if runs and k == runs[-1][-1] + 1 and k not in breaks:
                runs[-1].append(k)
            else:
                runs.append([k])
        return runs, set(kept)

    def gap_s(r0, r1):
        return ep.w0(S[r1[0]]) - ep.w1(S[r0[-1]])

    active = list(drops)
    with_lift = bool(lift_ks)
    lift = None
    for _ in range(20):
        runs, kept = runs_for(active, with_lift)
        if 0 not in kept or len(S) - 1 not in kept:
            raise Reject("start or payoff line removed")
        long_gap = next((j for j in range(len(runs) - 1) if gap_s(runs[j], runs[j + 1]) > cfg["max_drop_s"]), None)
        if long_gap is not None:
            lo_t, hi_t = ep.w1(S[runs[long_gap][-1]]), ep.w0(S[runs[long_gap + 1][0]])
            culprit = next((d for d in active if any(lo_t <= ep.w0(S[k]) <= hi_t for k in d["speech"])
                            or (d["gap_k"] is not None and d["gap_k"] == runs[long_gap + 1][0])), None)
            if culprit is None:
                with_lift = False
                lift_reject = lift_reject or "lift skip would remove too long a stretch"
                continue
            warns.append(f"drop {culprit['first_line']}..{culprit['last_line']} removes "
                         f"{gap_s(runs[long_gap], runs[long_gap + 1]):.0f} s (> {cfg['max_drop_s']:.0f} s: "
                         f"a scene stitch); ignored")
            active.remove(culprit)
            continue
        if len(runs) - 1 > cfg["max_cuts"]:
            dur = {id(d): sum(ep.w1(S[k]) - ep.w0(S[k]) for k in d["speech"]) for d in active}
            if not active:
                with_lift = False
                lift_reject = lift_reject or "too many internal cuts"
                continue
            d = min(active, key=lambda d: dur[id(d)])
            warns.append(f"more than {cfg['max_cuts']} internal cuts: drop {d['first_line']}..{d['last_line']} "
                         f"ignored")
            active.remove(d)
            continue
        if with_lift and lift is None:
            try:
                lift = build_lift(ep, S, lift_ks, kept, cfg, m.get("purpose", "standalone"))
            except Reject as e:
                lift_reject, with_lift = str(e), False
                continue
        break
    else:
        raise Reject("could not settle drops")

    # ---- time segments
    segs = []
    if lift:
        segs.append({"start": lift["start"], "end": lift["end"], "kind": "cold_open"})
    joins = []
    hold = None
    for j, run in enumerate(runs):
        r_first, r_last = S[run[0]], S[run[-1]]
        if j == 0:
            a = clip_start(ep, r_first, cfg)
        else:
            a, on = join_in(ep, r_first["words"][0], cfg)
            joins[-1]["in_on_cut"] = on
        if j == len(runs) - 1:
            hold = (end_hold_v1 if view == "ws/0.1" else end_hold)(ep, r_last["words"][1], cfg)
            b = hold["end"]
        else:
            b, on = join_out(ep, r_last["words"][1], cfg)
            joins.append({"after_line": r_last["id"], "before_line": S[runs[j + 1][0]]["id"], "out_on_cut": on})
        segs.append({"start": a, "end": b, "kind": "scene"})
    for k in range(1 + bool(lift), len(segs)):
        if segs[k]["start"] < segs[k - 1]["end"] - 1e-6:
            raise Reject("overlapping segments (internal error)")
    flashes = []
    if view != "ws/0.1":
        flashes = fix_flashes(ep, segs, cfg)
        for f in flashes:
            warns.append(flash_text(f))
        if lift and (segs[0]["start"] != lift["start"] or segs[0]["end"] != lift["end"]):
            lift["start"], lift["end"] = segs[0]["start"], segs[0]["end"]
            lift["duration"] = round(lift["end"] - lift["start"], 2)
        if segs[-1]["end"] != hold["end"]:
            hold = {**hold, "end": segs[-1]["end"], "hold_s": round(segs[-1]["end"] - hold["payoff_end"], 2),
                    "on_shot_cut": True, "note": "end moved to the shot change (flash repair)"}

    # ---- cut descriptions (what each internal cut removes)
    cuts_info = []
    for j in range(len(runs) - 1):
        lo, hi = runs[j][-1], runs[j + 1][0]
        g0, g1 = segs[j + bool(lift)]["end"], segs[j + 1 + bool(lift)]["start"]
        removed_lines = [r["id"] for r in ep.lines[ep.pos[S[lo]["id"]] + 1:ep.pos[S[hi]["id"]]]
                         if r["kind"] == "speech" or g0 <= (r["start"] + r["end"]) / 2 <= g1]
        lifted = [x for x in removed_lines if lift and x in lift["lines"]]
        by = [d for d in active if (d["speech"] and min(d["speech"]) > lo and max(d["speech"]) < hi)
              or d["gap_k"] == hi]
        cuts_info.append({"after_line": S[lo]["id"], "before_line": S[hi]["id"],
                          "removed_lines": removed_lines, "lift_skip": bool(lifted),
                          "drops": [d["j"] for d in by], "why": "; ".join(d["why"] for d in by if d["why"]),
                          "start": segs[j + bool(lift)]["end"], "end": segs[j + 1 + bool(lift)]["start"],
                          "removed_s": round(segs[j + 1 + bool(lift)]["start"] - segs[j + bool(lift)]["end"], 2)})

    for s_ in segs[1 + bool(lift):-1]:
        if s_["end"] - s_["start"] < 1.5:
            warns.append(f"short kept fragment {fmt(s_['start'])}-{fmt(s_['end'])} between two cuts")
    pieces = pieces_for(ep, segs)
    if not pieces:
        raise Reject("no words in the clip")
    out = {"start_line": S[0]["id"], "end_line": S[-1]["id"], "time_segments": segs, "pieces": pieces,
           "cold_open": lift, "lift_rejected": lift_reject if lift_ids and not lift and not no_lift else None,
           "cuts": cuts_info, "end": hold, "warnings": warns,
           "video_duration": round(sum(s["end"] - s["start"] for s in segs), 2),
           "drops_used": [d["j"] for d in active], "flashes": flashes, "view": view}
    if lift:
        gap = _loudness_gap(ep, lift, segs[1]["start"], cfg)
        if gap is not None:
            lift["level_vs_scene_db"] = gap
            if abs(gap) > cfg["lift_level_warn_db"]:
                warns.append(f"cold open is {gap:+.0f} dB vs the scene opening (preview levels it)")
    return out


def pieces_for(ep: Episode, segs: list[dict]) -> list[dict]:
    """Word pieces in play order: the words that lie fully inside each time segment."""
    out = []
    W = ep.words
    starts = [w["start"] for w in W]
    for s in segs:
        k = bisect.bisect_left(starts, s["start"] - 0.01)
        run = []
        while k < len(W) and W[k]["start"] <= s["end"] + 0.01:
            if W[k]["end"] <= s["end"] + 0.01:
                if run and k != run[-1] + 1:
                    out.append(run)
                    run = []
                run.append(k)
            k += 1
        if run:
            out.append(run)
    return [{"start_word": r[0], "end_word": r[-1], "start": round(W[r[0]]["start"], 3),
             "end": round(W[r[-1]]["end"], 3)} for r in out]


def core_fields(ep: Episode, pieces: list[dict], skip_s: float) -> dict:
    W = ep.words
    lo = min(p["start_word"] for p in pieces)
    hi = max(p["end_word"] for p in pieces)
    jumps = sum(1 for a, b in zip(pieces, pieces[1:])
                if b["start_word"] <= a["end_word"] or W[b["start_word"]]["start"] - W[a["end_word"]]["end"] > skip_s)
    return {"region": {"start_word": lo, "end_word": hi, "start": round(W[lo]["start"], 3),
                       "end": round(W[hi]["end"], 3)},
            "edited_duration": round(sum(W[p["end_word"]]["end"] - W[p["start_word"]]["start"] for p in pieces), 3),
            "reordered": any(b["start_word"] <= a["end_word"] for a, b in zip(pieces, pieces[1:])),
            "n_blocks": 1 + jumps,
            "text": " ".join(" ".join(W[i]["word"] for i in range(p["start_word"], p["end_word"] + 1))
                             for p in pieces)}


def hook_line(ep: Episode, pieces: list[dict], line_of_word: dict) -> dict:
    p0 = pieces[0]
    ln = line_of_word.get(p0["start_word"])
    end = min(ln["words"][1], p0["end_word"]) if ln else p0["end_word"]
    return {"text": " ".join(ep.words[i]["word"] for i in range(p0["start_word"], end + 1)),
            "start_word": p0["start_word"], "end_word": end}


# ======================================================================================== play-order text (review)
def play_text(ep: Episode, cid: str, m: dict, c: dict) -> str:
    """The clip as it plays, for the review call: cold open, scene lines, cut markers, end hold."""
    out = [f"### {cid} | {m.get('purpose', 'standalone')} | plays {c['video_duration']:.1f} s"]

    def row(r):
        who = "(audio event)" if r["kind"] == "event" else f"[{r['speaker']}]"
        return f"{r['id']} {who}: {r['text']}"
    segs = c["time_segments"]
    if c["cold_open"]:
        lo = c["cold_open"]
        out.append(f"[COLD OPEN {lo['duration']:.1f} s] " + " / ".join(row(ep.by_id[x]) for x in lo["lines"]))
        out.append("[THE SCENE STARTS]")
    scene = [s for s in segs if s["kind"] == "scene"]
    xn = 0
    for j, s in enumerate(scene):
        for r in ep.lines:
            mid = (r["start"] + r["end"]) / 2
            if r["kind"] == "speech" and s["start"] - 0.05 <= r["start"] and r["end"] <= s["end"] + 0.3:
                out.append(row(r))
            elif r["kind"] == "event" and s["start"] <= mid <= s["end"]:
                out.append(row(r))
        if j < len(c["cuts"]):
            cu = c["cuts"][j]
            rest = [x for x in cu["removed_lines"] if not (c["cold_open"] and x in c["cold_open"]["lines"])]
            if cu["lift_skip"]:
                also = "" if cu["drops"] or not rest else                     "; also removed: " + " | ".join(row(ep.by_id[x]) for x in rest)
                out.append(f"[cold-open line {', '.join(c['cold_open']['lines'])} skipped here, not replayed{also}]")
            if rest and (cu["drops"] or not cu["lift_skip"]):
                xn += 1
                cu["marker"] = f"X{xn}"
                out.append(f"[X{xn}: cut {cu['removed_s']:.1f} s, removed: "
                           + " | ".join(row(ep.by_id[x]) for x in rest) + "]")
    e = c["end"]
    out.append(f"[END: picture holds {e['hold_s']:.1f} s after the last line]")
    return "\n".join(out)


# ======================================================================================== LLM schemas
_STR = {"type": "string"}
DISCOVER_SCHEMA = {
    "type": "object",
    "properties": {
        "episode_notes": _STR,
        "moments": {"type": "array", "items": {"type": "object", "properties": {
            "purpose": {"type": "string", "enum": ["standalone", "promo"]},
            "start_line": _STR, "end_line": _STR,
            "what_happens": _STR,
            "hook": {"type": "string", "description": "what grabs in the first 2-3 seconds"},
            "payoff": {"type": "string", "description": "what lands at the end (punchline, peak, cliffhanger)"},
            "opening_strength": {"type": "string", "enum": ["strong", "weak"]},
            "cold_open_lines": {"type": "array", "items": _STR},
            "cold_open_why": _STR,
            "drops": {"type": "array", "items": {"type": "object", "properties": {
                "first_line": _STR, "last_line": _STR, "why": _STR},
                "required": ["first_line", "last_line", "why"]}},
            "spoiler": {"type": "boolean"},
            "characters": {"type": "array", "items": _STR},
            "visual_dependency": {"type": "string", "enum": ["high", "medium", "low"]},
            "category": {"type": "string", "enum": CATEGORIES},
            "strength": {"type": "integer", "minimum": 1, "maximum": 5},
        }, "required": ["purpose", "start_line", "end_line", "what_happens", "hook", "payoff", "opening_strength",
                        "cold_open_lines", "cold_open_why", "drops", "spoiler", "characters", "visual_dependency",
                        "category", "strength"]}},
    },
    "required": ["episode_notes", "moments"],
}
_NUM = {"type": "number", "minimum": 1, "maximum": 5}
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"clips": {"type": "array", "items": {"type": "object", "properties": {
        "id": _STR, "hook": _NUM, "standalone": _NUM, "payoff": _NUM, "value_emotion": _NUM, "overall": _NUM,
        "keep_cold_open": {"type": "boolean"}, "cold_open_note": _STR,
        "hook_without_cold_open": _NUM, "overall_without_cold_open": _NUM,
        "bad_cuts": {"type": "array", "items": _STR}, "cut_note": _STR,
        "title": _STR, "why_it_works": _STR, "category": {"type": "string", "enum": CATEGORIES},
    }, "required": ["id", "hook", "standalone", "payoff", "value_emotion", "overall", "keep_cold_open",
                    "cold_open_note", "hook_without_cold_open", "overall_without_cold_open", "bad_cuts", "cut_note",
                    "title", "why_it_works", "category"]}}},
    "required": ["clips"],
}


# ======================================================================================== spend guard
class BudgetStop(RuntimeError):
    pass


USD_PER_M = {"in": 0.75, "out": 3.75}   # gemini-3.8-flash (promo price, pricing.PRICES)


def inr(usd: float) -> float:
    from shotvi.llm.spend import usd_inr
    return usd * usd_inr()


class Guard:
    """Stop before a call whose projection would push the project rupee ledger past ``limit``."""

    def __init__(self, limit: float | None):
        self.limit = limit

    def check(self, what: str, projected_inr: float):
        if self.limit is None:
            return
        from shotvi.llm.spend import total_inr
        now = total_inr()
        if now + projected_inr > self.limit:
            raise BudgetStop(f"{what}: ledger ₹{now:.2f} + projected ₹{projected_inr:.2f} > limit ₹{self.limit:.2f}")


def is_cached(llm, spec, system: str, prompt: str, schema: dict) -> bool:
    """True when the first attempt of this call is in the response cache (a rerun costs nothing: skip the guard)."""
    try:
        return llm._cache_get(spec, llm.cache_key(spec, system, prompt, schema)) is not None
    except Exception:   # noqa: BLE001
        return False


def project_usd(in_tok: int, out_tok: int) -> float:
    return (in_tok * USD_PER_M["in"] + out_tok * USD_PER_M["out"]) / 1e6


# projected output (visible + thinking) per call, conservative. Measured 2026-10-06 on a 22-min episode:
# discover (high) 19-25k out; review (medium) 1.6k out for 8 clips, review (high) 14.4k for 8 clips.
PROJ_REVIEW = {"high": (6000, 1800), "medium": (3000, 300), "low": (2000, 250)}   # (base, per clip)
REVIEW_IN_PER_CLIP = 900
# video end check per clip: guard value, and expected from the clip-check calls of 2026-10-05 (video_clip_shots:
# ₹1.1-1.7 per clip, mean ~₹1.35; the 8-clip text-free variant ~₹1.0)
VIDEO_END_INR_PER_CLIP = 2.0
VIDEO_END_INR_EXPECTED = 1.35


# ======================================================================================== stages
def load_episode(vid: str, with_loudness: bool = True):
    from shotvi.select.lines import LineIndex
    tp = ROOT / "scribe_dataset" / vid / f"{vid}_scribe_transcript.json"
    tr = json.loads(tp.read_text(encoding="utf-8"))
    ix = LineIndex(vid, asr_fix=True)
    shots = json.loads((ROOT / "runs" / "video" / vid / "shots.json").read_text(encoding="utf-8"))
    dur = float(shots.get("duration") or tr.get("audio_duration_secs") or tr["word_timestamps"][-1]["end"])
    wav = ROOT / "scribe_dataset" / vid / f"{vid}_audio.wav"
    loud = (lambda a, b: mean_db(wav, a, b)) if with_loudness and wav.exists() else None
    ep = Episode(vid, ix.lines, tr["word_timestamps"], shots["cuts"], dur, loud)
    return ep, ix, tr, tp


_db_cache: dict = {}


def mean_db(src: Path, a: float, b: float) -> float | None:
    """Mean loudness (dB) of [a, b] in the episode audio (ffmpeg volumedetect)."""
    key = (str(src), round(a, 3), round(b, 3))
    if key in _db_cache:
        return _db_cache[key]
    r = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{a:.3f}", "-t", f"{max(0.1, b - a):.3f}", "-i", str(src),
                        "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    val = None
    for line in r.stderr.splitlines():
        if "mean_volume:" in line:
            val = float(line.split("mean_volume:")[1].split("dB")[0])
    _db_cache[key] = val
    return val


def liftable_lines(ep: Episode, cfg: dict) -> list[str]:
    """Spoken lines that pass the lift checks with their episode neighbours (short, complete, ends right on a shot
    cut, not mid-laugh, no question orphaned): tagged <lift-ok> for the model. The clip-level check still runs."""
    sp = [r for r in ep.lines if ep.is_speech(r)]
    out = []
    for k in range(1, len(sp) - 1):
        if ep.w1(sp[k]) - ep.w0(sp[k]) > cfg["lift_max_s"]:
            continue
        S = sp[k - 1:k + 3] if k + 2 < len(sp) else sp[k - 1:k + 2] + [sp[k + 1]]
        try:
            build_lift(Episode(ep.video_id, ep.lines, ep.words, ep.cuts, ep.duration, None), S, [1],
                       set(range(len(S))) - {1}, cfg, "standalone")
            out.append(sp[k]["id"])
        except Reject:
            pass
    return out


def prompt_file(cfg: dict | None, role: str) -> str:
    """Prompt file (prompts/<name>.md) used for a role: ``cfg["prompts"][role]``, default the role name. An A/B arm
    swaps one file (e.g. ws_discover -> ws_discover_b_setup) and changes nothing else."""
    return ((cfg or {}).get("prompts") or {}).get(role) or role


def discover_prompt(ep: Episode, ix, cfg: dict | None = None) -> tuple[str, str]:
    from shotvi.select.lines import ASR_LEGEND, render
    from shotvi.select.prompts import load_prompt
    p = load_prompt(prompt_file(cfg, "ws_discover"))
    tags = {lid: "lift-ok" for lid in liftable_lines(ep, (cfg or DEFAULTS)["cut"])}
    return p.system, p.render(episode_len=fmt(ep.duration), n_lines=len(ep.lines), asr_legend=ASR_LEGEND,
                              transcript=render(ep.lines, tags))


def review_prompt(texts: list[str], cfg: dict | None = None) -> tuple[str, str]:
    from shotvi.select.prompts import load_prompt
    p = load_prompt(prompt_file(cfg, "ws_review"))
    return p.system, p.render(n_clips=len(texts), clips="\n\n".join(texts))


# ======================================================================================== projection (free)
DISCOVER_OUT_EXPECTED = 25000   # discover (high) output incl. thinking without a calibration run: 19-25k measured
N_CLIPS_GUESS = 15              # clips reviewed, before discovery has run


def calibration_of(run_dir: str | Path | None, vid: str) -> dict | None:
    """Token counts of a finished run of the same episode (``<run_dir>/<vid>.json``, cost.by_role): the offline basis
    of an expected-cost projection. Reads a local file only."""
    if not run_dir:
        return None
    p = Path(run_dir) / f"{vid}.json"
    if not p.exists():
        return None
    doc = json.loads(p.read_text(encoding="utf-8"))
    pipe = doc.get("pipeline") or {}
    return {"run": Path(run_dir).name, "by_role": (doc.get("cost") or {}).get("by_role") or {},
            "n_reviewed": (doc.get("cutoff") or {}).get("n_passed_hard_rules"),
            "prompt_versions": pipe.get("prompt_versions") or {},
            "prompt_files": pipe.get("prompt_files") or {},
            "models": (pipe.get("config") or {}).get("models") or {}}


def review_out_guard(params: dict, n_clips: int) -> int:
    """Conservative review output (visible + thinking), never above the call's output budget."""
    rb, rc = PROJ_REVIEW[params.get("thinking_level", "high")]
    return min(rb + rc * n_clips, int(params.get("max_output_tokens") or 10 ** 9))


def project_episode(ep: Episode, ix, cfg: dict, system: str, user: str, llm=None, calib: dict | None = None,
                    log=print) -> dict:
    """Projected cost of one episode, two numbers per call:
      ``inr``           guard value (conservative: discover at its whole output budget, review at PROJ_REVIEW);
                        the spend guard stops on these;
      ``inr_expected``  what the run will most likely bill: token counts of a calibration run of the same episode
                        (input scaled by prompt length; output as measured), else defaults.
    Input tokens come from countTokens when ``llm`` is given (a free network call), else offline (calibration
    run, else a character estimate)."""
    from shotvi.segment.render import estimate_tokens
    spec, rspec = spec_of(cfg, "ws_discover"), spec_of(cfg, "ws_review")
    cb = (calib or {}).get("by_role") or {}
    text = system + "\n" + user
    n_in, src = None, None
    if llm is not None:
        try:
            n_in, src = llm.count_tokens(spec, text), "countTokens"
        except Exception as e:   # noqa: BLE001 (projection only)
            log(f"countTokens unavailable ({e}); estimating offline")
    if n_in is None and (cb.get("ws_discover") or {}).get("input_tokens"):
        role_file = (calib.get("prompt_files") or {}).get("ws_discover", "ws_discover")
        bcfg = deep_merge(cfg, {"prompts": {"ws_discover": role_file}})
        bs, bu = discover_prompt(ep, ix, bcfg)
        n_in = int(round(cb["ws_discover"]["input_tokens"] * len(text) / max(1, len(bs + "\n" + bu))))
        src = f"calibrated on {calib['run']} ({cb['ws_discover']['input_tokens']} tokens for its prompt)"
    if n_in is None:
        n_in, src = int(estimate_tokens(system + user) * 1.25), "character estimate x1.25"
    d_max = int(spec.params.get("max_output_tokens", 32768))
    d_exp = int((cb.get("ws_discover") or {}).get("output_tokens") or DISCOVER_OUT_EXPECTED)
    disc = {"in": n_in, "in_source": src, "out_expected": d_exp, "out_guard": d_max,
            "inr_expected": round(inr(project_usd(n_in, d_exp)), 2), "inr": round(inr(project_usd(n_in, d_max)), 2)}
    # review: a calibration run only counts when it reviewed with the same model settings
    same = (calib or {}).get("models", {}).get("ws_review") == cfg["models"]["ws_review"]
    rcb = cb.get("ws_review") if same else None
    rv_in = int(rcb["input_tokens"]) if rcb else 1200 + REVIEW_IN_PER_CLIP * N_CLIPS_GUESS
    rv_exp_out = int(rcb["output_tokens"]) if rcb else review_out_guard(rspec.params, N_CLIPS_GUESS)
    rv_g_in = 1200 + REVIEW_IN_PER_CLIP * N_CLIPS_GUESS
    rv_g_out = review_out_guard(rspec.params, N_CLIPS_GUESS)
    rev = {"in_expected": rv_in, "out_expected": rv_exp_out, "inr_expected": round(inr(project_usd(rv_in, rv_exp_out)), 2),
           "in_guard": rv_g_in, "out_guard": rv_g_out, "inr": round(inr(project_usd(rv_g_in, rv_g_out)), 2),
           "assumes_clips": N_CLIPS_GUESS,
           "expected_from": f"{calib['run']} ({calib.get('n_reviewed')} clips)" if rcb else "defaults"}
    n_vc = (calib or {}).get("n_reviewed") or N_CLIPS_GUESS
    vend = {"enabled": bool(cfg["video_end_check"]["enabled"]), "assumes_clips": n_vc,
            "inr": round(VIDEO_END_INR_PER_CLIP * n_vc, 2) if cfg["video_end_check"]["enabled"] else 0.0,
            "inr_expected": round(VIDEO_END_INR_EXPECTED * n_vc, 2) if cfg["video_end_check"]["enabled"] else 0.0}
    return {"ws_discover": disc, "ws_review": rev, "video_end_check": vend,
            "episode": {"inr_expected": round(disc["inr_expected"] + rev["inr_expected"] + vend["inr_expected"], 2),
                        "inr": round(disc["inr"] + rev["inr"] + vend["inr"], 2)}}


def spec_of(cfg, role):
    from shotvi.llm import ModelSpec
    return ModelSpec.from_dict(cfg["models"][role])


def hard_rules(ep: Episode, c: dict, cfg) -> list[str]:
    g = cfg["guardrails"]
    out = []
    d = c["video_duration"]
    if d < g["min_s"]:
        out.append(f"L1: plays {d:.1f} s < min {g['min_s']:.0f} s")
    if d > g["hard_max_s"]:
        out.append(f"L2: plays {d:.1f} s > hard max {g['hard_max_s']:.0f} s")
    for ev in ep.events:
        if any(t in ev["text"] for t in CENSOR_TOKENS) and any(
                ev["start"] < s["end"] and ev["end"] > s["start"] for s in c["time_segments"]):
            out.append(f"A1: overlaps censored event at {ev['start']:.1f}s")
    return out


def overlap_frac(a: dict, b: dict) -> float:
    """Shared source time / the shorter clip's source time (time segments)."""
    def ivs(c):
        return [(s["start"], s["end"]) for s in c["time_segments"]]
    sh = sum(max(0.0, min(x1, y1) - max(x0, y0)) for x0, x1 in ivs(a) for y0, y1 in ivs(b))
    la, lb = sum(y - x for x, y in ivs(a)), sum(y - x for x, y in ivs(b))
    return sh / max(1e-6, min(la, lb))


def dry_result(vid: str, proj: dict, stage: str, inr_expected: float, inr_guard: float, log=print, **kw) -> dict:
    """What a dry run reports: the first stage that would bill, and the rupees from there to the end."""
    out = {"video_id": vid, "projection": proj, "first_paid_stage": stage,
           "would_bill_inr_expected": round(inr_expected, 2), "would_bill_inr_guard": round(inr_guard, 2), **kw}
    log(f"{vid}: DRY RUN: " + (f"first paid stage {stage}; would bill ₹{inr_expected:.2f} expected "
                               f"(guard projection ₹{inr_guard:.2f})" if stage else "everything is cached: ₹0"))
    return out


def run_episode(vid: str, cfg: dict, llm, guard: Guard, out_dir: Path, log=print, dry_run: bool = False,
                calib_dir: str | Path | None = None) -> dict:
    """One episode. ``dry_run``: no provider call of any kind (countTokens included); cached stages are walked for
    free (so a run that only adds a stage, e.g. the video end check, is projected exactly) and the result says
    which stage would bill first and how many rupees the rest would cost. Nothing is written."""
    t_start = time.time()
    ep, ix, tr, tp = load_episode(vid)
    system, user = discover_prompt(ep, ix, cfg)
    spec = spec_of(cfg, "ws_discover")
    calib = calibration_of(calib_dir, vid)
    proj = project_episode(ep, ix, cfg, system, user, llm=None if (dry_run or llm.cache_only) else llm,
                           calib=calib, log=log)
    log(f"{vid}: projection {json.dumps(proj, ensure_ascii=False)}")
    disc_cached = is_cached(llm, spec, system, user, DISCOVER_SCHEMA)
    if dry_run:
        if not disc_cached:
            return dry_result(vid, proj, "ws_discover", proj["episode"]["inr_expected"], proj["episode"]["inr"], log,
                              prompt_files={r: prompt_file(cfg, r) for r in PROMPTS})
        llm.cache_only = True     # walk the cached stages; the first miss is where the bill starts
    # spend guard on EVERY billed attempt (retries and schema retries included; cache hits never reach it)
    call_proj = {"ws_discover": proj["ws_discover"]["inr"], "ws_review": proj["ws_review"]["inr"],
                 "video_end": VIDEO_END_INR_PER_CLIP}
    llm.before_call = lambda role, _spec: guard.check(f"{vid} {role}", call_proj.get(role, max(call_proj.values())))
    if not disc_cached:
        # never pay for a discover whose review (and end check) would then be stopped by the limit
        guard.check(f"{vid} episode (discover + review"
                    f"{' + video end' if cfg['video_end_check']['enabled'] else ''})", proj["episode"]["inr"])

    debug = {"video_id": vid, "projection": proj, "warnings": []}
    # ---------------------------------------------------------------- 2. discover
    disc = llm.generate_json("ws_discover", spec, system, user, DISCOVER_SCHEMA, meta={"video": vid})
    moments = disc["moments"]
    debug["discover"] = disc
    log(f"{vid}: {len(moments)} moments nominated")

    # ---------------------------------------------------------------- 3. build
    cands, rejected = [], []
    for k, m in enumerate(moments):
        cid = f"w{k + 1:02d}"
        try:
            c = build_clip(ep, m, cfg["cut"])
        except Reject as e:
            rejected.append({"id": cid, "moment": m, "reject_reasons": [f"B1: {e}"]})
            log(f"  {cid}: rejected by the cut builder: {e}")
            continue
        rr = hard_rules(ep, c, cfg)
        if rr:
            rejected.append({"id": cid, "moment": m, "clip": c, "reject_reasons": rr})
            log(f"  {cid}: rejected: {rr}")
            continue
        cands.append({"id": cid, "moment": m, "clip": c})
    # de-duplicate on shared source time (keep the stronger nomination, then the earlier one)
    cands.sort(key=lambda x: -int(x["moment"].get("strength", 3)))
    kept = []
    for x in cands:
        dup = next((y for y in kept if overlap_frac(x["clip"], y["clip"]) > cfg["guardrails"]["dup_overlap"]), None)
        if dup:
            rejected.append({"id": x["id"], "moment": x["moment"], "clip": x["clip"],
                             "reject_reasons": [f"D1: overlaps {dup['id']} "
                                                f"({overlap_frac(x['clip'], dup['clip']):.0%} of the shorter)"]})
            log(f"  {x['id']}: duplicate of {dup['id']}")
            continue
        kept.append(x)
    kept.sort(key=lambda x: x["clip"]["time_segments"][-1]["start"])

    # ---------------------------------------------------------------- 3b. review the built clips
    view = cfg.get("review_view", "final")
    texts = []
    for x in kept:
        shown = x["clip"]
        if view != "final":
            shown = build_clip(ep, x["moment"], cfg["cut"], view=view)
            if [(a["after_line"], a["before_line"]) for a in shown["cuts"]] != \
                    [(a["after_line"], a["before_line"]) for a in x["clip"]["cuts"]]:
                raise RuntimeError(f"{x['id']}: review view {view} has different cuts than the built clip")
        texts.append(play_text(ep, x["id"], x["moment"], shown))
        for a, b in zip(shown["cuts"], x["clip"]["cuts"]):     # the review names cuts by these markers
            if a.get("marker"):
                b["marker"] = a["marker"]
        x["review_saw"] = shown
    reviews = {}
    if kept:
        rs, ru = review_prompt(texts, cfg)
        rspec = spec_of(cfg, "ws_review")
        try:
            rin = llm.count_tokens(rspec, rs + "\n" + ru)
        except Exception:   # noqa: BLE001
            rin = len(rs + ru) // 2
        rout = review_out_guard(rspec.params, len(kept))
        call_proj["ws_review"] = inr(project_usd(rin, rout))
        if not is_cached(llm, rspec, rs, ru, REVIEW_SCHEMA):
            if dry_run:
                vend = (VIDEO_END_INR_PER_CLIP, VIDEO_END_INR_EXPECTED) if cfg["video_end_check"]["enabled"] \
                    else (0.0, 0.0)
                return dry_result(vid, proj, "ws_review", proj["ws_review"]["inr_expected"] + vend[1] * len(kept),
                                  call_proj["ws_review"] + vend[0] * len(kept), log, n_clips=len(kept))
            guard.check(f"{vid} ws_review", call_proj["ws_review"])
        rv = llm.generate_json("ws_review", rspec, rs, ru, REVIEW_SCHEMA, meta={"video": vid})
        debug["review"] = rv
        debug["review_input"] = texts
        reviews = {str(r["id"]).strip(): r for r in rv["clips"]}

    apply_review(ep, kept, reviews, cfg)

    # ---------------------------------------------------------------- 4. optional video end check
    if dry_run:
        n = len(kept) if cfg["video_end_check"]["enabled"] else 0
        return dry_result(vid, proj, "video_end" if n else None, VIDEO_END_INR_EXPECTED * n,
                          VIDEO_END_INR_PER_CLIP * n, log, n_clips=len(kept))
    if cfg["video_end_check"]["enabled"] and kept:
        video_end_check(ep, kept, cfg, llm, guard, log)

    # ---------------------------------------------------------------- 5. rank, cut off, write
    line_of_word = {}
    for r in ep.lines:
        if ep.is_speech(r):
            line_of_word[r["words"][0]] = r
    shown = rank_clips(kept, rejected, cfg)
    out_props = [proposal_doc(ep, x, k + 1, cfg, line_of_word) for k, x in enumerate(shown)]
    out_rej = [rejected_doc(ep, x, cfg) for x in rejected]
    debug["rejected"] = rejected
    debug["clips"] = {x["id"]: x["clip"] for x in kept}
    doc = run_document(ep, tp, out_props, out_rej, cfg, llm, len(moments), len(kept), t_start, debug)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{vid}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "debug").mkdir(exist_ok=True)
    (out_dir / "debug" / f"{vid}.json").write_text(json.dumps(debug, ensure_ascii=False, indent=1, default=str),
                                                   encoding="utf-8")
    (out_dir / f"{vid}.md").write_text(clips_md(ep, doc), encoding="utf-8")
    log(f"{vid}: {len(out_props)} shown, {len(out_rej)} rejected -> {out_dir / (vid + '.json')}")
    return doc


def apply_review(ep: Episode, kept: list, reviews: dict, cfg: dict) -> None:
    """Apply the review to the built clips, in place: veto cold opens and restore drops the reviewer called bad
    joins (a code rebuild, no call); note end words the review did not see."""
    for x in kept:
        r = reviews.get(x["id"])
        x["review"] = r
        if not r:
            x["warnings_review"] = "no review returned for this clip"
            continue
        c = x["clip"]
        # the lift stays only when the reviewer keeps it AND it beats the plain opening (hook up, overall not down)
        no_lift = bool(c["cold_open"]) and not (
            r["keep_cold_open"] and float(r["hook"]) > float(r["hook_without_cold_open"])
            and float(r["overall"]) >= float(r["overall_without_cold_open"]))
        bad = {str(b).strip().upper() for b in r.get("bad_cuts") or []}
        restore = set()
        for cu in c["cuts"]:
            if cu.get("marker") in bad:
                restore |= set(cu["drops"])
        if no_lift or restore:
            try:
                c2 = build_clip(ep, x["moment"], cfg["cut"], no_lift=no_lift,
                                skip_drops=restore | set(x.get("restored", ())))
                rr = hard_rules(ep, c2, cfg)
                if rr:
                    raise Reject("; ".join(rr))
                why_drop = (f"review: hook {r['hook']} with the lift vs {r['hook_without_cold_open']} without, "
                            f"overall {r['overall']} vs {r['overall_without_cold_open']}; {r['cold_open_note']}")
                c2["review_changes"] = ([one_line("cold open dropped, " + why_drop)] if no_lift else []) \
                    + ([f"restored drop(s) {sorted(restore)}: " + r.get("cut_note", "")] if restore else [])
                if no_lift:
                    c2["lift_rejected"] = one_line(why_drop)
                    c2["cold_open_dropped"] = c["cold_open"]
                x["clip"] = c2
                x["rebuilt"] = True
                x["restored_drops"] = sorted(restore)
            except Reject as e:
                x["warnings_review"] = f"rebuild after review failed ({e}); kept the reviewed cut"

    # words at the end the review did not see (review_view ws/0.1 replays: a reaction line added by the ws/0.2 end)
    for x in kept:
        c, saw = x["clip"], x.get("review_saw")
        if saw is None or saw is c:
            continue
        seen = {i for p in saw["pieces"] for i in range(p["start_word"], p["end_word"] + 1)}
        last = ep.by_id[c["end_line"]]["words"][1]
        extra = [i for p in c["pieces"] for i in range(p["start_word"], p["end_word"] + 1) if i > last and i not in seen]
        if extra:
            c["warnings"].append("end plays words the review did not see (it reviewed the ws/0.1 ending): "
                                 + " ".join(ep.words[i]["word"] for i in extra))
            c["unreviewed_end_words"] = extra


def rank_clips(kept: list, rejected: list, cfg: dict) -> list:
    """Scores -> shown clips in rank order (quality cutoff, no fixed N); appends the cut ones to ``rejected``."""
    th = cfg["thresholds"]
    proposals, below = [], []
    for x in kept:
        r, c, m = x["review"], x["clip"], x["moment"]
        if not r:
            rejected.append({"id": x["id"], "moment": m, "clip": c, "reject_reasons": ["R0: no review"]})
            continue
        sc = {k: float(r[k]) for k in ("hook", "standalone", "payoff", "value_emotion", "overall")}
        if x.get("rebuilt") and c.get("cold_open_dropped"):
            sc["hook"], sc["overall"] = float(r["hook_without_cold_open"]), float(r["overall_without_cold_open"])
        if min(sc["hook"], sc["standalone"], sc["payoff"]) <= 2:
            sc["overall"] = min(sc["overall"], 2.0)
        x["scores"] = sc
        if x.get("restored_drops"):
            # the code repaired the clip after the review (put back drops the reviewer called bad joins): the
            # scores are for the broken cut, so they must not reject or bury the repaired clip. It keeps the
            # reviewer's tier when that tier is shown, else it is shown as worth_a_look; either way needs_rescore.
            tier = tier_of(sc["overall"], th)
            x["needs_rescore"] = one_line(
                f"repaired after review (restored drop(s) {x['restored_drops']}: {r.get('cut_note', '')}); scores "
                f"(overall {sc['overall']}, hook {sc['hook']}, standalone {sc['standalone']}, payoff {sc['payoff']}) "
                f"are for the cut before the repair", 300)
            if tier == "below_threshold":     # includes every score Q1 would reject (overall < t_show)
                x["tier_override"] = "worth_a_look"
            proposals.append(x)
            continue
        if min(sc["hook"], sc["standalone"], sc["payoff"]) <= 2 or sc["overall"] < th["below_min_overall"]:
            why = [f"Q1: below the quality bar (overall {sc['overall']}, hook {sc['hook']}, standalone "
                   f"{sc['standalone']}, payoff {sc['payoff']})"]
            rejected.append({"id": x["id"], "moment": m, "clip": c, "scores": sc, "reject_reasons": why})
            continue
        (proposals if sc["overall"] >= th["t_show"] else below).append(x)
    # a repaired clip shown as worth_a_look ranks as if its overall were t_show (bottom of that tier, above
    # below_threshold clips) until it is re-scored
    key = lambda x: (-(th["t_show"] if x.get("tier_override") else x["scores"]["overall"]),  # noqa: E731
                     -x["scores"]["hook"], -x["scores"]["payoff"], -int(x["moment"].get("strength", 3)))
    proposals.sort(key=key)
    below.sort(key=key)
    for x in below[th["below_threshold_max"]:]:
        rejected.append({"id": x["id"], "moment": x["moment"], "clip": x["clip"], "scores": x["scores"],
                         "reject_reasons": [f"Q2: below t_show, not in the top {th['below_threshold_max']} of those"]})
    return proposals + below[:th["below_threshold_max"]]


def video_end_check(ep: Episode, kept: list, cfg, llm, guard: Guard, log):
    """Optional: video_check clip2 (shot lines) per clip; apply only the END suggestion, capped and flagged."""
    from shotvi.llm.gemini import upload_file
    from shotvi.select.lines import LineIndex
    from shotvi.select import video_check as vc
    vid = ep.video_id
    ixs = LineIndex(vid, shot_lines=True)
    f = upload_file(str(ROOT / "scribe_dataset" / vid / f"{vid}.mp4"))
    spec = spec_of(cfg, "video_end")
    for x in kept:
        c = x["clip"]
        guard.check(f"{vid} video_end {x['id']}", VIDEO_END_INR_PER_CLIP)
        clip = {"id": x["id"], "title": (x.get("review") or {}).get("title", ""), "pieces": c["pieces"]}
        wins = vc.clip2_windows(c["pieces"])
        system, user = vc.clip2_prompt(ixs, clip, wins, None, "video_clip_shots")
        media = [{"file_uri": f["uri"], "mime_type": "video/mp4", "key": f"{vid}.mp4:{f['size']}",
                  "start_s": round(s0, 2), "end_s": round(s1, 2), "fps": 1.0} for s0, s1 in wins]
        obj = llm.generate_json("video_end", spec, system, user, vc.CLIP2_SCHEMA, media=media,
                                meta={"video": vid, "clip": x["id"], "ws": 1})
        conv = vc.convert(ixs, obj).get("end") or {}
        e = c["end"]
        res = {"verdict": obj["end"]["verdict"], "why": obj["end"]["why"], "suggested": conv.get("time")}
        t = conv.get("time")
        if obj["end"]["verdict"] != "ok" and t is not None:
            lo = e["payoff_end"] + 0.3
            hi = min(e["payoff_end"] + cfg["video_end_check"]["max_shift_s"],
                     (e["next_speech"] - 0.1) if e["next_speech"] else ep.duration)
            new = min(max(t, lo), hi)
            if abs(new - e["end"]) >= 0.3:
                c["time_segments"][-1]["end"] = round(new, 3)
                c["end"] = {**e, "end": round(new, 3), "hold_s": round(new - e["payoff_end"], 2),
                            "video_adjusted_from": e["end"], "video_capped": new != t}
                c["video_duration"] = round(sum(s["end"] - s["start"] for s in c["time_segments"]), 2)
                res["applied"] = round(new, 3)
        c["video_end_check"] = res
        log(f"  {x['id']}: video end {res}")


def tier_of(overall: float, th: dict) -> str:
    return "recommended" if overall >= th["t_rec"] else "worth_a_look" if overall >= th["t_show"] \
        else "below_threshold"


def proposal_doc(ep: Episode, x: dict, rank: int, cfg, line_of_word) -> dict:
    c, m, r, sc = x["clip"], x["moment"], x["review"], x["scores"]
    th, g = cfg["thresholds"], cfg["guardrails"]
    pieces = c["pieces"]
    core = core_fields(ep, pieces, g["skip_s"])
    tier = x.get("tier_override") or tier_of(sc["overall"], th)
    notes = []
    if c["cold_open"]:
        notes.append({"type": "cold_open", "after_piece": 0,
                      "reason": one_line(f"Lifted {', '.join(c['cold_open']['lines'])} "
                                         f"({c['cold_open']['duration']:.1f} s) to the front; skipped in place, "
                                         f"not replayed. {m.get('cold_open_why', '')}")})
    W = ep.words
    for k, (a, b) in enumerate(zip(pieces, pieces[1:])):
        if b["start_word"] > a["end_word"] and W[b["start_word"]]["start"] - W[a["end_word"]]["end"] > g["skip_s"]:
            notes.append({"type": "skip", "after_piece": k,
                          "reason": one_line(f"skips {W[b['start_word']]['start'] - W[a['end_word']]['end']:.0f} s")})
    if core["edited_duration"] > g["soft_max_s"]:
        notes.append({"type": "length_over_soft_max", "reason": f"{core['edited_duration']:.0f} s edited"})
    flags = []
    if core["n_blocks"] > g["blocks_flag_above"]:
        flags.append("many_jumps")
    if core["edited_duration"] > g["soft_max_s"]:
        flags.append("over_soft_max")
    if any(any(t in ev["text"] for t in SONG_TOKENS) and sum(
            max(0.0, min(ev["end"], s["end"]) - max(ev["start"], s["start"])) for s in c["time_segments"]) > 5.0
           for ev in ep.events):
        flags.append("song_overlap")
    title = r["title"].strip()
    if not any("ఀ" <= ch <= "౿" for ch in title):
        title = (title + " | తెలుగు")[:100]
    doc = {"id": x["id"], "pieces": pieces, **core, "rank": rank, "tier": tier, "scores": sc,
           "title": title[:100], "hook_line": hook_line(ep, pieces, line_of_word),
           "why_it_works": one_line(r["why_it_works"]), "category": r["category"],
           "standalone_risks": [], "edit_notes": notes, "flags": flags,
           # web-series fields
           "content_type": "web_series", "clip_purpose": m.get("purpose", "standalone"),
           "cold_open": bool(c["cold_open"]), "cold_open_detail": c["cold_open"],
           "cold_open_rejected": c.get("lift_rejected"),
           "time_segments": [{"start": s["start"], "end": s["end"], "kind": s["kind"]} for s in c["time_segments"]],
           "video_duration": c["video_duration"], "spoiler": bool(m.get("spoiler")),
           "characters": m.get("characters") or [], "visual_dependency": m.get("visual_dependency", "medium"),
           "start_line": c["start_line"], "end_line": c["end_line"], "end_hold": c["end"],
           "chunk_drops": [{"after_line": cu["after_line"], "before_line": cu["before_line"],
                            "removed_lines": cu["removed_lines"], "lift_skip": cu["lift_skip"],
                            "start": cu["start"], "end": cu["end"], "removed_s": cu["removed_s"],
                            "why": cu["why"]} for cu in c["cuts"]],
           "discovery": {"strength": m.get("strength"), "what_happens": m.get("what_happens"),
                         "hook": m.get("hook"), "payoff": m.get("payoff"),
                         "opening_strength": m.get("opening_strength")},
           "review": {"cold_open_note": r.get("cold_open_note"), "cut_note": r.get("cut_note"),
                      "bad_cuts": r.get("bad_cuts"), "changes": c.get("review_changes") or []},
           "needs_rescore": bool(x.get("needs_rescore")),
           **({"rescore_reason": x["needs_rescore"]} if x.get("needs_rescore") else {}),
           # shot flashes at segment edges the builder could not repair without clipping a word (the schema's
           # `flags` enum has no such value; repaired ones are listed in build_warnings)
           "shot_flags": [flash_text(f) for f in c.get("flashes") or [] if not f["fixed"]],
           "build_warnings": c["warnings"]}
    if c.get("video_end_check"):
        doc["video_end_check"] = c["video_end_check"]
    return doc


def rejected_doc(ep: Episode, x: dict, cfg) -> dict | None:
    c = x.get("clip")
    base = {"id": x["id"], "reject_reasons": x["reject_reasons"]}
    if not c:
        # nothing was built: describe the moment with its line span as one piece (schema needs pieces)
        m = x["moment"]
        sid, eid = norm_id(m.get("start_line")), norm_id(m.get("end_line"))
        sp = [r for r in ep.lines[ep.pos.get(sid, 0):ep.pos.get(eid, -1) + 1] if ep.is_speech(r)] \
            if sid in ep.pos and eid in ep.pos else []
        if not sp:
            return None
        W = ep.words
        pieces = [{"start_word": sp[0]["words"][0], "end_word": sp[-1]["words"][1],
                   "start": round(W[sp[0]["words"][0]]["start"], 3), "end": round(W[sp[-1]["words"][1]]["end"], 3)}]
        return {**base, "pieces": pieces, **core_fields(ep, pieces, cfg["guardrails"]["skip_s"]),
                "moment": {k: m.get(k) for k in ("purpose", "start_line", "end_line", "hook", "payoff", "strength")}}
    out = {**base, "pieces": c["pieces"], **core_fields(ep, c["pieces"], cfg["guardrails"]["skip_s"]),
           "time_segments": c["time_segments"], "video_duration": c["video_duration"],
           "clip_purpose": x["moment"].get("purpose"), "cold_open": bool(c["cold_open"])}
    if x.get("scores"):
        out["scores"] = x["scores"]
    return out


def one_line(s: str, n: int = 200) -> str:
    s = " ".join(str(s or "").split())
    return (s[:n - 1] + "…") if len(s) > n else (s or "-")


def run_document(ep, tp, props, rejected, cfg, llm, n_moments, n_passed, t_start, debug) -> dict:
    from shotvi.select.context import file_sha256
    from shotvi.select.prompts import load_prompt
    summ = llm.ledger.summary()
    return {
        "schema_version": "1.1", "kind": "proposals", "video_id": ep.video_id,
        "transcript": {"path": Path(tp).resolve().relative_to(ROOT).as_posix(), "sha256": file_sha256(tp),
                       "n_words": len(ep.words), "audio_duration_secs": float(json.loads(
                           Path(tp).read_text(encoding="utf-8")).get("audio_duration_secs") or ep.duration)},
        "derived_params": {"skip_s": cfg["guardrails"]["skip_s"]},
        "run_id": cfg.get("run_id", "ws"), "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "content_type": "web_series",
        "pipeline": {"version": PIPELINE_VERSION, "git_commit": None,
                     "prompt_versions": {n: load_prompt(prompt_file(cfg, n)).tag for n in PROMPTS},
                     "prompt_files": {n: prompt_file(cfg, n) for n in PROMPTS}, "config": cfg},
        "models": [{"role": k, **v} for k, v in cfg["models"].items()
                   if k != "video_end" or cfg["video_end_check"]["enabled"]],
        "cost": {"input_tokens": summ["input_tokens"], "output_tokens": summ["output_tokens"],
                 "usd": summ["usd"], "usd_billed": summ["usd_billed"], "wall_time_secs": round(time.time() - t_start, 1),
                 "n_llm_calls": summ["n_llm_calls"], "by_role": summ["by_role"], "projection": debug["projection"]},
        "cutoff": {"t_show": cfg["thresholds"]["t_show"], "t_rec": cfg["thresholds"]["t_rec"],
                   "n_candidates": n_moments, "n_passed_hard_rules": n_passed, "n_shown": len(props),
                   "warnings": debug.get("warnings", [])},
        "episode_notes": (debug.get("discover") or {}).get("episode_notes", ""),
        "proposals": props,
        "rejected": [r for r in rejected if r],
    }


def clips_md(ep: Episode, doc: dict) -> str:
    out = [f"# {doc['video_id']} web-series clips ({doc['run_id']})\n",
           f"{len(doc['proposals'])} shown, {len(doc['rejected'])} rejected. Cost ${doc['cost']['usd']:.4f}.\n"]
    for p in doc["proposals"]:
        segs = " + ".join(f"{fmt(s['start'])}-{fmt(s['end'])}" for s in p["time_segments"])
        out.append(f"\n## #{p['rank']} {p['id']} {p['title']} ({p['clip_purpose']}, {p['tier']}, "
                   f"overall {p['scores']['overall']})\n")
        out.append(f"- plays {p['video_duration']:.1f} s: {segs}")
        if p["cold_open"]:
            out.append(f"- cold open: {p['cold_open_detail']['text']} ({p['cold_open_detail']['duration']} s)")
        elif p.get("cold_open_rejected"):
            out.append(f"- cold open rejected: {p['cold_open_rejected']}")
        eh = p["end_hold"]
        out.append(f"- end: payoff ends {fmt(eh['payoff_end'])}, hold {eh['hold_s']} s "
                   f"({'on a shot cut' if eh['on_shot_cut'] else 'not on a shot cut'}; rule {eh.get('rule', '-')})"
                   + (f"; plays the reaction {eh['reaction']['line']} '{eh['reaction']['text']}'"
                      if eh.get("reaction") else ""))
        if p.get("needs_rescore"):
            out.append(f"- NEEDS RESCORE: {p['rescore_reason']}")
        for s in p.get("shot_flags") or []:
            out.append(f"- shot flag: {s}")
        out.append(f"- why: {p['why_it_works']}")
        out.append(f"- hook line: {p['hook_line']['text']}")
        for cu in p["chunk_drops"]:
            out.append(f"- cut {fmt(cu['start'])}-{fmt(cu['end'])} ({cu['removed_s']} s): "
                       f"{'cold-open skip ' if cu['lift_skip'] else ''}{cu['why']}")
        if p["build_warnings"]:
            out.append(f"- warnings: {'; '.join(p['build_warnings'])}")
    out.append("\n## Rejected\n")
    for r in doc["rejected"]:
        out.append(f"- {r['id']}: {'; '.join(r['reject_reasons'])}")
    return "\n".join(out) + "\n"


# ======================================================================================== CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description="Web-series clip pipeline v1")
    ap.add_argument("video_ids", nargs="+")
    ap.add_argument("--out", default="runs/ws_r1")
    ap.add_argument("--dry-run", action="store_true", help="token counts and projected rupees only (free)")
    ap.add_argument("--max-ledger-inr", type=float, default=None,
                    help="stop before any call whose projection would push the project ledger past this")
    ap.add_argument("--video-end-check", action="store_true", help="stage 4 (paid, ~₹1.35 per clip)")
    ap.add_argument("--prompt", action="append", default=[], metavar="ROLE=FILE",
                    help="use prompts/FILE.md for ROLE (ws_discover / ws_review): one A/B arm = one swapped file")
    ap.add_argument("--calibrate-from", default=None, metavar="RUN_DIR",
                    help="expected-cost projection from a finished run of the same episodes (its token counts); "
                         "offline, no call")
    ap.add_argument("--config", default=None, help="JSON deep-merged over DEFAULTS")
    ap.add_argument("--cache-only", action="store_true",
                    help="never call the provider: rebuild outputs from cached responses (free); a miss fails")
    ap.add_argument("--no-retry", action="store_true",
                    help="no automatic retries (a retry is billed again and bypasses the ledger guard)")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    os.chdir(ROOT)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from shotvi.llm import Ledger, LLMClient
    cfg = deep_merge(DEFAULTS, json.loads(Path(a.config).read_text(encoding="utf-8")) if a.config else {})
    if a.video_end_check:
        cfg["video_end_check"]["enabled"] = True
    for kv in a.prompt:
        role, _, name = kv.partition("=")
        if role not in PROMPTS or not name or not (ROOT / "prompts" / f"{name}.md").exists():
            ap.error(f"--prompt {kv}: expected ROLE=FILE with ROLE in {PROMPTS} and prompts/FILE.md present")
        cfg["prompts"][role] = name
    out = Path(a.out)
    cfg["run_id"] = out.name
    guard = Guard(a.max_ledger_inr)
    from shotvi.llm.spend import total_inr
    led0 = total_inr()
    if a.dry_run:
        print(f"DRY RUN (no provider call, countTokens included): ledger ₹{led0:.2f}"
              + (f", limit ₹{a.max_ledger_inr:.2f}" if a.max_ledger_inr is not None else ""))
    results = []
    for vid in a.video_ids:
        llm = LLMClient(Ledger(max_usd=cfg["budget_usd"]), cache_dir="runs/cache",
                        **({"max_retries": 0, "schema_retries": 0} if a.no_retry else {}))
        llm.cache_only = a.cache_only
        try:
            results.append(run_episode(vid, cfg, llm, guard, out, dry_run=a.dry_run, calib_dir=a.calibrate_from))
        except BudgetStop as e:
            print(f"STOP: {e}")
            return 2
    if a.dry_run:
        exp = sum(r.get("would_bill_inr_expected", 0.0) for r in results)
        grd = sum(r.get("would_bill_inr_guard", 0.0) for r in results)
        print(f"DRY RUN total: ₹{exp:.2f} expected (guard projection ₹{grd:.2f}); ledger would go "
              f"₹{led0:.2f} -> ~₹{led0 + exp:.2f}"
              + (f" (limit ₹{a.max_ledger_inr:.2f}: {'fits' if led0 + grd <= a.max_ledger_inr else 'the guard may stop a later episode'})"
                 if a.max_ledger_inr is not None else ""))
        return 0
    print(f"ledger now ₹{total_inr():.2f} (this run ₹{total_inr() - led0:.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
