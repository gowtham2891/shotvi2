"""Stage 3, candidate generation (doc 6; pure code, no Gemini).

For every scene with a Stage 2 beat sheet, cut the moments the sheet found into concrete clip candidates with exact
boundaries, a type and a variant, generously (recall first: Stage 4 rejects, Stage 3 never pre-judges).

Inputs:  facts["beat_sheets"] (beats, payoff, reaction, non_dialogue, skippable_lines, clip_hints, register, ...),
         facts["gaps"] (the only legal boundary positions), shot cuts inside the gaps (cut quality of trims),
         non-story lines (speaker_map.non_story_confirmed after manual overrides, plus 1B's), config ``stage3``.
Output:  facts["candidates"] = [candidate, ...] (schemas/candidate.schema.json), facts["stages"]["3"].

Templates (doc 6.2): comedy = full arc / tight / cold open; emotional = full / peak; loop = one. A scene with several
payoffs gets the templates for each payoff plus an extended arc spanning all of them.
Founder, 2026-10-29 (Surya trace): no hook templates; each Stage 2 hook_moment adds an ``open_question`` END-VARIANT to its
scene's normal moment (natural start, ending on the open-question line). Moment ends go past the reaction to the BUTTON
(the sheet's button_line, else the button rule in ``end.button``). After the caps: one ``thread`` candidate per episode-pass
thread (setup lines + cut + payoff scene to its button) and a ``critic_trimmed`` sibling for every variant the critic
flagged on pace only (``critic_trim``); neither takes a cap slot.
Length policy: the untrimmed full arc is ALWAYS generated; trimmed versions are extra variants; the platform maximum
is the only hard limit (the untrimmed arc is kept but flagged when it exceeds it). Target lengths are recorded only.
Boundaries: start = inside the pause before the first word (``pad_s`` before it; a pause < ``start.min_gap_s`` cannot
carry a start, so the previous line is included and recorded); end = the first pause >= ``end.min_gap_s`` after the end
anchor (the sheet's reaction when it follows the payoff, else the payoff line), the whole laugh / hold included when
one fills the pause, else ``tail_s`` after the last word. Extending past ``extend_max_s`` is recorded. Never inside a line.
Trims: non_dialogue recommendations (shorten_to keeps the head of the stretch, remove drops it; reaction_hold never
touched), then skippable lines (cleanest cut first, never a beat, at most ``skippable_max_share`` of the lines).
A trimmed candidate is a list of kept segments; ``removed`` records exactly what went and how.
Beats are protected: every beat between the start and the end anchor is inside the kept segments.
Non-story: a candidate with a non-story line strictly inside is not generated; one whose tail would run into a
non-story line is clipped before it (recorded).
Caps: ``caps.per_scene`` slots per scene (an untrimmed full arc and its trimmed twin share one slot), ``per_episode``
slots per episode, dropping the slot with the fewest beats; the best hook / loop of a scene is protected. Exact
duplicates are merged (types recorded); overlapping variants are kept for Stage 4 to choose between.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import jsonschema

from . import critic_class as CC
from .acceptance import final_speaker
from .config import loops_on
from .stage1_episode_pass import expand_lines
from .stage1_facts import fmt_ts

VERSION = "stage3/0.1"
STAGE_KEY = "3"
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "candidate.schema.json"
EPS = 1e-3
LONG_TYPES = ("comedy", "emotional")
SHORT_TYPES = ("hook", "loop")
TYPE_ORDER = {"comedy": 0, "emotional": 1, "hook": 2, "loop": 3}
BEAT_TYPES_OPENING = ("entry", "setup", "escalation", "turn", "setup_for_payoff", "peak", "payoff")
PAYOFF_TYPES = ("payoff", "peak")
CUT_QUALITY_RANK = {"clean": 0, "jump": 1}


class Stage3Error(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------- context
class Ctx:
    """Indexes over the facts that every template needs: lines by id and order, gaps before / after each line,
    non-story lines, reaction holds per sheet."""

    def __init__(self, facts: dict, cfg: dict):
        self.facts, self.cfg = facts, cfg
        self.s3 = cfg["stage3"]
        self.lines: list[dict] = sorted(facts["lines"], key=lambda r: (r["start"], r["id"]))
        self.line = {r["id"]: r for r in self.lines}
        self.order = {r["id"]: i for i, r in enumerate(self.lines)}
        self.gaps: list[dict] = sorted(facts["gaps"], key=lambda g: g["t_prev_end"])
        self.gap_after = {g["prev"]: g for g in self.gaps}
        self.gap_before = {g["next"]: g for g in self.gaps}
        ns = set(((facts.get("speaker_map") or {}).get("non_story_confirmed")) or [])
        ns |= set((((facts.get("episode_pass") or {}).get("derived")) or {}).get("non_story_lines") or [])
        self.non_story = ns
        self.episode_end = max((r["end"] for r in self.lines), default=0.0)
        self.first_speech_start = min((r["start"] for r in self.lines if r["kind"] == "speech"), default=0.0)

    # lines
    def speech_in(self, a: float, b: float) -> list[dict]:
        return [r for r in self.lines if r["kind"] == "speech" and r["start"] >= a - EPS and r["end"] <= b + EPS]

    def prev_speech(self, lid: str) -> dict | None:
        i = self.order[lid] - 1
        while i >= 0:
            if self.lines[i]["kind"] == "speech":
                return self.lines[i]
            i -= 1
        return None

    def next_speech(self, lid: str) -> dict | None:
        i = self.order[lid] + 1
        while i < len(self.lines):
            if self.lines[i]["kind"] == "speech":
                return self.lines[i]
            i += 1
        return None

    def gap_after_line(self, lid: str) -> dict | None:
        """The gap-table row after this line; a line without its own row (zero-length, overlapped) uses the row of the
        nearest earlier line whose row reaches past it."""
        g = self.gap_after.get(lid)
        if g is not None:
            return g
        r = self.line[lid]
        later = [x for x in self.gaps if x["t_prev_end"] >= r["end"] - EPS]
        return later[0] if later else None

    def gap_before_line(self, lid: str) -> dict | None:
        g = self.gap_before.get(lid)
        if g is not None:
            return g
        r = self.line[lid]
        earlier = [x for x in self.gaps if x["t_next_start"] <= r["start"] + EPS]
        return earlier[-1] if earlier else None

    def on_pause(self, t: float, sheet: dict) -> bool:
        """True when t lies inside a gap-table pause (its endpoints included) or on the scene's own edge."""
        if abs(t - sheet["start"]) <= EPS or abs(t - sheet["end"]) <= EPS or abs(t - self.episode_end) <= EPS:
            return True
        if t <= self.first_speech_start + EPS or t >= self.episode_end - EPS:
            return True                                        # the episode's leading / trailing silence has no gap row
        for g in self.gaps:
            if g["t_prev_end"] - EPS <= t <= g["t_next_start"] + EPS:
                return True
        return False

    def song_start_in(self, a: float, b: float) -> float | None:
        """Start of the earliest [song] event (tag_class music) overlapping (a, b), or None. The start may lie before a
        when the music began under the dialogue."""
        hits = [x["start"] for x in self.lines if x["kind"] == "event" and x.get("tag_class") == "music" and x["start"] < b - EPS and x["end"] > a + EPS]
        return min(hits) if hits else None

    def inside_line(self, t: float) -> dict | None:
        for r in self.lines:
            if r["kind"] == "speech" and not r.get("zero_len") and r["start"] + EPS < t < r["end"] - EPS:
                return r
        return None


def holds_of(sheet: dict) -> list[dict]:
    return [nd for nd in sheet.get("non_dialogue") or [] if nd.get("kind") == "reaction_hold"
            and nd.get("start") is not None and nd.get("end") is not None]


def hold_at(sheet: dict, g: dict) -> dict | None:
    for h in holds_of(sheet):
        if abs(float(h["start"]) - g["t_prev_end"]) <= 0.35 or (float(h["start"]) <= g["t_prev_end"] + EPS and float(h["end"]) >= g["t_next_start"] - EPS):
            return h
    return None


def sheet_beats(ctx: Ctx, sheet: dict) -> list[dict]:
    """Beats of the sheet that are real lines, sorted by time, with t filled from the line."""
    out = []
    for b in sheet.get("beats") or []:
        r = ctx.line.get(b.get("line"))
        if not r:
            continue
        out.append({"line": r["id"], "type": b.get("type"), "t": r["start"], "end": r["end"], "note": b.get("note")})
    out.sort(key=lambda b: b["t"])
    return out


# ----------------------------------------------------------------------------------------------- boundaries
def snap_start(ctx: Ctx, lid: str, sheet: dict) -> tuple[float, list[str], dict]:
    """Start inside a pause before line ``lid``, ``pad_s`` before the first word that follows it, never before the pause
    begins. Founder (2026-10-27): prefer the nearest pause >= ``near_gap_s`` whose next line starts within
    ``near_window_s`` before the beat; only if none, walk back to the nearest pause >= ``min_gap_s``. Lines pulled in
    ahead of the beat are recorded in ``extra`` either way."""
    st = ctx.s3["start"]
    pad, min_gap = float(st["pad_s"]), float(st["min_gap_s"])
    near_gap, window = float(st.get("near_gap_s", min_gap)), float(st.get("near_window_s", 0.0))
    beat = ctx.line[lid]

    def place(cur: str, g: dict | None, rule: str) -> tuple[float, list[str], dict]:
        r = ctx.line[cur]
        extra = [x["id"] for x in ctx.lines if x["kind"] == "speech" and r["start"] <= x["start"] < beat["start"] - EPS and x["id"] != lid]
        lo = g["t_prev_end"] if g else 0.0
        start = max(lo, r["start"] - pad, 0.0)
        return round(start, 3), extra, {"rule": rule, "gap": g["gap"] if g else None, "scene_edge": g is None or r["start"] <= sheet["start"] + EPS}

    # pass 1: nearest pause >= near_gap within the window (the pause right before the beat first)
    cur = lid
    while True:
        r = ctx.line[cur]
        g = ctx.gap_before_line(cur)
        if g is None or r["start"] <= sheet["start"] + EPS:
            return place(cur, g, "scene edge")
        if beat["start"] - r["start"] > window + EPS:
            break
        if g["gap"] >= near_gap - EPS:
            return place(cur, g, "near pause")
        prev = g["prev"]
        if prev not in ctx.line or ctx.line[prev]["start"] < sheet["start"] - EPS:
            return place(cur, g, "scene edge")
        cur = prev
    # pass 2: walk back to the nearest pause >= min_gap
    cur = lid
    while True:
        r = ctx.line[cur]
        g = ctx.gap_before_line(cur)
        if g is None or r["start"] <= sheet["start"] + EPS:
            return place(cur, g, "scene edge")
        if g["gap"] >= min_gap - EPS:
            return place(cur, g, "walk back")
        prev = g["prev"]
        if prev not in ctx.line or ctx.line[prev]["start"] < sheet["start"] - EPS:
            return place(cur, g, "scene edge")
        cur = prev


def snap_end(ctx: Ctx, anchor: str, sheet: dict, kind: str = "default") -> tuple[float, list[str], dict]:
    """End after the anchor line: the first pause >= min_gap (or a laugh / hold filling it) before the next line.
    Includes the whole laugh / hold; else tail_s after the last word, capped by the next line's start."""
    e = ctx.s3["end"]; lp = ctx.s3["loop"]
    min_gap = float(e["min_gap_s"])
    fallback_gap = float(e["min_gap_s"])
    extend_max = float(e["extend_max_s"])
    tail = float((ctx.s3.get("open_question") or {}).get("tail_s", 0.3)) if kind == "open_question" else float(e["tail_s"])
    fills = set(e.get("reaction_fills") or [])
    extra: list[str] = []
    cur = anchor
    anchor_end = ctx.line[anchor]["end"]
    if kind == "open_question":
        # founder, 2026-10-29: an open-question end-variant ends on that line itself: a short tail, never the next pause
        g = ctx.gap_after_line(anchor)
        info = {"extended_past_max": False, "fill": None, "hold": False, "scene_end": False, "gap": g["gap"] if g else None}
        if g is None or g["t_next_start"] > sheet["end"] + EPS:
            info["scene_end"] = True
            return round(min(sheet["end"], anchor_end + tail), 3), extra, info
        return round(min(g["t_next_start"], g["t_prev_end"] + min(tail, max(g["gap"], 0.0)) if g["gap"] < tail else g["t_prev_end"] + tail), 3), extra, info
    if kind == "loop":
        # founder, 2026-10-27: the loop ends at the FIRST pause >= loop.end_gap_s after the punchline, or after its laugh tag
        # when the laugh starts within loop.laugh_within_s; it never extends to a later pause
        g = ctx.gap_after_line(anchor)
        info = {"extended_past_max": False, "fill": None, "hold": False, "scene_end": False, "gap": g["gap"] if g else None}
        if g is None or g["t_next_start"] > sheet["end"] + EPS:
            info["scene_end"] = True
            return round(sheet["end"], 3), extra, info
        laugh_starts = next((x["start"] for x in ctx.lines if x["kind"] == "event" and x.get("tag_class") == "laugh"
                             and g["t_prev_end"] - EPS <= x["start"] <= g["t_next_start"] + EPS), None)
        if g.get("filled_by") == "laugh" and laugh_starts is not None and laugh_starts - anchor_end <= float(lp.get("laugh_within_s", 1.5)) + EPS:
            info["fill"] = "laugh"
            return round(g["t_next_start"], 3), extra, info
        end = g["t_prev_end"] + min(tail, max(g["gap"], 0.0)) if g["gap"] < float(lp.get("end_gap_s", 0.3)) - EPS else min(g["t_next_start"], g["t_prev_end"] + tail)
        return round(end, 3), extra, info
    info = {"extended_past_max": False, "fill": None, "hold": False, "scene_end": False}
    while True:
        r = ctx.line[cur]
        g = ctx.gap_after_line(cur)
        if g is None or g["t_next_start"] > sheet["end"] + EPS or r["end"] >= sheet["end"] - EPS:
            info["scene_end"] = True
            end = sheet["end"] if g is None or g["t_next_start"] > sheet["end"] + EPS else min(sheet["end"], r["end"] + tail)
            end, info = _end_song_cap(ctx, round(min(max(end, r["end"]), sheet["end"]), 3), r["end"], tail, {**info, "gap": g["gap"] if g else None})
            return end, extra, info
        filled = (g.get("filled_by") in fills)
        hd = hold_at(sheet, g)
        qualifies = g["gap"] >= min_gap - EPS or filled or hd is not None
        past = (g["t_next_start"] - anchor_end) > extend_max
        if not qualifies and past and g["gap"] >= fallback_gap - EPS:
            qualifies = True
        if qualifies:
            if filled or hd is not None:
                info["fill"] = g.get("filled_by"); info["hold"] = hd is not None
                end = g["t_next_start"]
            else:
                end = min(g["t_next_start"], g["t_prev_end"] + tail)
            info["extended_past_max"] = (g["t_prev_end"] - anchor_end) > extend_max + EPS
            end, info = _end_song_cap(ctx, round(end, 3), r["end"], tail, {**info, "gap": g["gap"]})
            return end, extra, info
        nxt = g["next"]
        if nxt not in ctx.line:
            info["scene_end"] = True
            end, info = _end_song_cap(ctx, round(min(sheet["end"], r["end"] + tail), 3), r["end"], tail, {**info, "gap": g["gap"]})
            return end, extra, info
        extra.append(nxt)
        cur = nxt


def end_anchor(ctx: Ctx, sheet: dict, payoff_lid: str, next_payoff_t: float | None) -> tuple[str, dict | None]:
    """The sheet's reaction line is the end anchor when it follows the payoff (within reaction_max_after_s and before
    the next payoff); otherwise the payoff line itself."""
    r = sheet.get("reaction") or {}
    after = r.get("after_line")
    p = ctx.line[payoff_lid]
    if after in ctx.line and (r.get("type") or "none") != "none":
        a = ctx.line[after]
        dt = a["start"] - p["start"]
        if -EPS <= dt <= float(ctx.s3["end"]["reaction_max_after_s"]) and (next_payoff_t is None or a["start"] < next_payoff_t - EPS):
            return after, r
    return payoff_lid, None


def speaker_of(ctx: Ctx, lid: str) -> str | None:
    """The line's final speaker name (Stage 2 map > 1B > fix; manual fixes included)."""
    return final_speaker(ctx.facts, lid)[1] or ctx.line[lid].get("speaker_name")


def button_line(ctx: Ctx, sheet: dict, anchor_lid: str, payoff_lid: str, next_payoff_t: float | None) -> tuple[str | None, str | None]:
    """founder, 2026-10-29 (Surya trace: two of six golden clips ended one line early): the line a clip ends AFTER, past the
    payoff / reaction anchor: the sheet's ``button_line`` (the spoken reaction or exit line after the payoff) when the sheet
    has the key; for sheets without it, the first spoken line after the anchor (and its laugh) when it is by a different
    speaker, starts within ``end.button.within_s`` and lasts <= ``end.button.max_len_s``. (None, why) when there is none."""
    a = ctx.line[anchor_lid]
    if "button_line" in sheet:
        b = sheet.get("button_line")
        if not b:
            return None, None
        r = ctx.line.get(b)
        if (r is None or r["kind"] != "speech" or r["start"] < a["end"] - 0.25 or r["start"] > sheet["end"] + EPS
                or r["start"] - ctx.line[payoff_lid]["start"] > float(ctx.s3["end"]["reaction_max_after_s"])
                or (next_payoff_t is not None and r["start"] >= next_payoff_t - EPS) or b in ctx.non_story):
            return None, None
        return b, "sheet button_line"
    bt = ctx.s3["end"].get("button") or {}
    if not bt:
        return None, None
    nxt = ctx.next_speech(anchor_lid)
    if nxt is None or nxt["start"] > sheet["end"] + EPS or nxt["id"] in ctx.non_story:
        return None, None
    if next_payoff_t is not None and nxt["start"] >= next_payoff_t - EPS:
        return None, None
    ref = a["end"]
    laughs = [x["end"] for x in ctx.lines if x["kind"] == "event" and x.get("tag_class") == "laugh" and a["end"] - 0.25 <= x["start"] <= nxt["start"] + EPS]
    if laughs:
        ref = max(ref, min(max(laughs), nxt["start"]))
    if nxt["start"] - ref > float(bt["within_s"]) + EPS or nxt["end"] - nxt["start"] > float(bt["max_len_s"]) + EPS:
        return None, None
    who_a, who_b = speaker_of(ctx, anchor_lid), speaker_of(ctx, nxt["id"])
    if not who_a or not who_b or who_a == who_b:
        return None, None
    return nxt["id"], f"button rule ({who_b} answers within {nxt['start'] - ref:.1f} s, {nxt['end'] - nxt['start']:.1f} s long)"


# ----------------------------------------------------------------------------------------------- trims
def _merge_spans(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + EPS:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out if b - a > EPS]


def chunk_out_point(ctx: Ctx, a: float, want: float, land: float, tr: dict) -> tuple[float, str]:
    """Out-point of the kept head of a stretch starting at a (the last kept line's end): ``want`` (a + target),
    never past ``land`` (the next spoken line) and never into a [song]: a song
    starting before the out-point caps it at the song's start, keeping at least segment_tail_s after the line."""
    tail = float(tr.get("segment_tail_s", 0.5))
    cut_at, quality = float(want), "inside stretch"
    song = ctx.song_start_in(a, cut_at)
    if song is not None and song < cut_at - EPS:
        cut_at, quality = max(a + tail, song), "before song"
    return round(min(cut_at, land), 3), quality


def _end_song_cap(ctx: Ctx, end: float, last_line_end: float, tail: float, info: dict) -> tuple[float, dict]:
    """founder, 2026-10-28: the clip end never runs into a [song]; capped at the song start, the tail after the last
    line kept."""
    if not ctx.s3["end"].get("never_into_song", True):
        return end, info
    song = ctx.song_start_in(last_line_end, end)
    if song is not None and song < end - EPS:
        return round(max(last_line_end + tail, song), 3), {**info, "song_capped": True, "song_start": round(song, 3)}
    return end, info


def apply_trims(ctx: Ctx, sheet: dict, start: float, end: float, level: str, protected: set[str],
                payoff_t: float | None = None) -> tuple[list[list[float]], dict]:
    """Kept segments inside [start, end] after the trims of ``level`` (none | stretches | lines) and a record of
    what was removed. ``protected`` = line ids that are never skipped (beats, anchors, first / last line)."""
    removed = {"gaps_shortened": [], "gaps_removed": [], "lines_skipped": []}
    cuts: list[tuple[float, float]] = []
    tr = ctx.s3["trims"]
    if level in ("stretches", "lines"):
        # founder, 2026-10-28: Stage 2's non_dialogue recommendations are applied as CHUNK removal. A shorten_to / remove
        # stretch of a chunk kind keeps its head (target_s; remove_keep_s for "remove"), then ONE cut that lands just
        # before the next spoken line. The kept head never runs into a [song] (capped at the song start, tail kept).
        # reaction_hold is never touched; "keep" is kept.
        chunk_kinds = set(tr.get("chunk_kinds") or ["action_beat", "transition", "scene_setting"])
        for nd in sheet.get("non_dialogue") or []:
            if nd.get("kind") not in chunk_kinds or nd.get("start") is None or nd.get("end") is None:
                continue
            a, b = float(nd["start"]), float(nd["end"])
            if a < start - EPS or b > end + EPS or b - a <= EPS:
                continue
            rec = nd.get("recommend")
            if rec not in ("shorten_to", "remove"):
                continue
            nxt = ctx.line.get(nd.get("before_line") or "")
            land = float(nxt["start"]) if nxt and nxt["kind"] == "speech" and a + EPS < float(nxt["start"]) <= end + EPS else b
            if land >= end - EPS:
                continue                                       # a stretch that runs to the clip end is a boundary matter, not a trim
            target = float(nd["target_s"]) if rec == "shorten_to" and nd.get("target_s") is not None else float(tr.get("remove_keep_s", 3.0))
            if (land - a) - target < float(tr["min_shorten_gain_s"]):
                continue
            cut_at, quality = chunk_out_point(ctx, a, a + target, land, tr)
            if land - cut_at <= EPS:
                continue
            cuts.append((cut_at, land))
            entry = {"range": nd.get("range") or f"{fmt_ts(a)}-{fmt_ts(b)}", "to_s": round(cut_at - a, 2), "cut_at": cut_at, "cut_quality": quality,
                     "kind": nd.get("kind"), "recommend": rec, "lands_before": nxt["id"] if nxt else None, "lands_at": round(land, 3)}
            removed["gaps_shortened" if rec == "shorten_to" else "gaps_removed"].append(entry)
    if level in ("stretches", "lines") and payoff_t is not None:
        # founder, 2026-10-29: in TRIMMED twins, a "keep" stretch of a build-up kind that ends BEFORE the payoff line and is
        # longer than build_up_max_s keeps its head (build_up_keep_s), then one cut lands before the next spoken line.
        # Stretches after the payoff are never touched; untrimmed twins never reach here (level "none").
        bu_kinds = set(tr.get("build_up_kinds") or ["reaction_hold", "action_beat"])
        bu_max, bu_keep = float(tr.get("build_up_max_s", 6.0)), float(tr.get("build_up_keep_s", 4.0))
        for nd in sheet.get("non_dialogue") or []:
            if nd.get("kind") not in bu_kinds or nd.get("recommend") != "keep" or nd.get("start") is None or nd.get("end") is None:
                continue
            a, b = float(nd["start"]), float(nd["end"])
            if a < start - EPS or b > end + EPS or b > payoff_t + EPS or b - a <= bu_max + EPS:
                continue
            nxt = ctx.line.get(nd.get("before_line") or "")
            land = float(nxt["start"]) if nxt and nxt["kind"] == "speech" and a + EPS < float(nxt["start"]) <= end + EPS else b
            if land >= end - EPS or land > payoff_t + EPS:
                continue
            cut_at, quality = chunk_out_point(ctx, a, a + bu_keep, land, tr)
            if land - cut_at < float(tr["min_shorten_gain_s"]):
                continue
            cuts.append((cut_at, land))
            removed["gaps_shortened"].append({"range": nd.get("range") or f"{fmt_ts(a)}-{fmt_ts(b)}", "to_s": round(cut_at - a, 2), "cut_at": cut_at,
                                              "cut_quality": quality, "kind": nd.get("kind"), "recommend": "keep (build-up > "
                                              f"{bu_max:.0f} s, shortened in the trimmed twin)", "lands_before": nxt["id"] if nxt else None, "lands_at": round(land, 3)})
    if level == "lines":
        inside = ctx.speech_in(start, end)
        n_lines = len([r for r in inside if not r.get("zero_len")])
        cap = int(math.floor(float(tr["skippable_max_share"]) * n_lines))
        holds = [(float(h["start"]), float(h["end"])) for h in holds_of(sheet)]
        beat_spans = [(ctx.line[b]["start"], ctx.line[b]["end"]) for b in protected if b in ctx.line]
        cands = []
        for sk in sheet.get("skippable_lines") or []:
            lid = sk.get("line")
            r = ctx.line.get(lid)
            if not r or r["kind"] != "speech" or lid in protected or r["start"] < start - EPS or r["end"] > end + EPS:
                continue
            cands.append((r["end"] - r["start"], lid, sk))
        cands.sort()
        for _, lid, sk in cands:
            if len(removed["lines_skipped"]) >= cap:
                break
            r = ctx.line[lid]
            gb, ga = ctx.gap_before_line(lid), ctx.gap_after_line(lid)
            prev_end = gb["t_prev_end"] if gb else r["start"]
            a = max(start, min(r["start"], prev_end + float(tr.get("segment_tail_s", 0.5))))   # founder, 2026-10-28: last kept line + tail
            b = min(end, ga["t_prev_end"] if ga else r["end"])
            if b - a <= EPS:
                continue
            if any(a < hb - EPS and b > ha + EPS for ha, hb in holds):
                continue
            if any(a < be - EPS and b > bs + EPS for bs, be in beat_spans):
                continue
            cuts.append((a, b))
            removed["lines_skipped"].append(lid)
    merged = _merge_spans(cuts)
    segs: list[list[float]] = []
    cur = start
    for a, b in merged:
        if a > cur + EPS:
            segs.append([round(cur, 3), round(a, 3)])
        cur = max(cur, b)
    if end > cur + EPS:
        segs.append([round(cur, 3), round(end, 3)])
    return segs, removed


# ----------------------------------------------------------------------------------------------- candidates
def _targets(cfg3: dict, ctype: str) -> list[float]:
    t = cfg3["targets_s"].get(ctype) or [0, 0]
    return [float(t[0]), float(t[1])]


def _length_vs_target(final: float, target: list[float], sketch_max: float | None) -> str:
    lo, hi = target
    if final < lo:
        return "under"
    if final <= hi:
        return "in"
    if sketch_max and final <= sketch_max:
        return "sketch"
    return "over"


def build_candidate(ctx: Ctx, sheet: dict, ctype: str, variant: str, level: str, first_lid: str, anchor_lid: str,
                    payoff_lid: str, reaction: dict | None, beats: list[dict], kind: str = "default", note: str = "") -> dict | None:
    """One candidate from a first line and an end anchor; None when a non-story line lies strictly inside."""
    start, extra_start, sinfo = snap_start(ctx, first_lid, sheet)
    end, extra_end, einfo = snap_end(ctx, anchor_lid, sheet, kind)
    clipped = None
    inside = ctx.speech_in(start, end)
    ns_inside = [r["id"] for r in inside if r["id"] in ctx.non_story]
    if ns_inside:
        anchor_end = ctx.line[anchor_lid]["end"]
        before_anchor = [x for x in ns_inside if ctx.line[x]["start"] < anchor_end - EPS]
        if before_anchor:
            return None                                        # non-story dialogue inside the moment: not generated
        first_ns = min(ns_inside, key=lambda x: ctx.line[x]["start"])
        g = ctx.gap_before_line(first_ns)
        new_end = g["t_prev_end"] if g else ctx.line[first_ns]["start"]
        if new_end <= ctx.line[anchor_lid]["end"] + EPS:
            new_end = ctx.line[anchor_lid]["end"]
        end = round(min(end, new_end), 3)
        clipped = first_ns
        extra_end = [x for x in extra_end if ctx.line[x]["end"] <= end + EPS]
        inside = ctx.speech_in(start, end)
    beat_ids = [b["line"] for b in beats if start - EPS <= b["t"] and b["end"] <= end + EPS]
    protected = set(beat_ids) | {first_lid, anchor_lid, payoff_lid}
    if inside:
        protected |= {inside[0]["id"], inside[-1]["id"]}
    segs, removed = apply_trims(ctx, sheet, start, end, level, protected,
                                payoff_t=ctx.line[payoff_lid]["start"] if payoff_lid in ctx.line else None)
    if not segs:
        return None
    final = round(sum(b - a for a, b in segs), 3)
    raw = round(end - start, 3)
    s3 = ctx.s3
    target = _targets(s3, ctype)
    sketch_max = float(s3["targets_s"].get("comedy_sketch_max") or 0) if ctype == "comedy" else None
    r_t = None
    if reaction:
        after = reaction.get("after_line")
        g = ctx.gap_after_line(after) if after in ctx.line else None
        r_t = fmt_ts(g["t_prev_end"]) if g else None
    return {
        "id": "", "scene_id": sheet["scene_id"], "type": ctype, "variant": variant, "trim_level": level,
        "start": start, "end": end, "segments": segs, "raw_duration": raw, "final_duration": final,
        "beats_included": beat_ids, "payoff_line": payoff_lid, "anchor_line": anchor_lid,
        "reaction": r_t, "reaction_type": (reaction or {}).get("type"),
        "removed": removed,
        "extra_lines": {"start": extra_start, "end": extra_end},
        "start_note": sinfo.get("rule"),
        "end_note": ("scene end" if einfo.get("scene_end") else "laugh/hold included" if (einfo.get("fill") or einfo.get("hold")) else "pause")
                    + (", extended past the 6 s window" if einfo.get("extended_past_max") else "")
                    + (f", clipped before non-story {clipped}" if clipped else ""),
        "context_required": list(sheet.get("context_required") or []), "register": sheet.get("register"),
        "dynamic": sheet.get("dynamic") or {}, "callbacks": list(sheet.get("callbacks") or []),
        "flags": {"self_contained": bool(sheet.get("self_contained", True)), "thin_diarization": bool(sheet.get("thin_diarization")),
                  "confidence": sheet.get("confidence"), "over_platform_max": final > float(s3["platform_max_s"]) + EPS,
                  "end_clipped_by_non_story": clipped, "needs_series_context": bool(sheet.get("callbacks")) or bool(sheet.get("context_required")),
                  "primary_payoff": payoff_lid == (sheet.get("payoff") or {}).get("line")},
        "target_s": target, "length_vs_target": _length_vs_target(final, target, sketch_max),
        "eligible_for_final": final <= float(s3["platform_max_s"]) + EPS,     # Stage 6 must respect this (founder, 2026-10-27)
        "lines": [r["id"] for r in inside], "n_lines": len(inside), "note": note,
        "start_str": fmt_ts(start), "end_str": fmt_ts(end),
    }


def _moments(ctx: Ctx, sheet: dict, beats: list[dict]) -> list[dict]:
    """Payoff moments of a scene: one per payoff / peak beat (the sheet's payoff line always included), each with its
    opening beat (the first beat after the previous moment's end), its end anchor and the next payoff's time."""
    payoffs = [b for b in beats if b["type"] in PAYOFF_TYPES]
    plid = (sheet.get("payoff") or {}).get("line")
    if plid in ctx.line and plid not in {b["line"] for b in payoffs}:
        payoffs.append({"line": plid, "type": "payoff", "t": ctx.line[plid]["start"], "end": ctx.line[plid]["end"]})
    payoffs.sort(key=lambda b: b["t"])
    # a peak right after a payoff (same breath) is the same moment: keep the later line as the payoff of that moment
    merged: list[dict] = []
    for p in payoffs:
        if merged and p["t"] - merged[-1]["t"] <= 4.0:
            merged[-1] = p
        else:
            merged.append(p)
    out = []
    prev_end_t = None
    for i, p in enumerate(merged):
        nxt = merged[i + 1]["t"] if i + 1 < len(merged) else None
        base_anchor, reaction = end_anchor(ctx, sheet, p["line"], nxt)
        btn, btn_why = button_line(ctx, sheet, base_anchor, p["line"], nxt)
        anchor = btn or base_anchor
        opening = [b for b in beats if b["t"] < p["t"] - EPS and b["type"] in BEAT_TYPES_OPENING and (prev_end_t is None or b["t"] > prev_end_t + EPS)]
        first = opening[0]["line"] if opening else (ctx.prev_speech(p["line"]) or ctx.line[p["line"]])["id"]
        if ctx.line[first]["start"] < sheet["start"] - EPS:
            first = p["line"]
        out.append({"payoff": p["line"], "payoff_t": p["t"], "first": first, "anchor": anchor, "base_anchor": base_anchor, "button": btn,
                    "button_why": btn_why, "reaction": reaction, "next_payoff_t": nxt, "opening_beats": opening})
        prev_end_t = ctx.line[anchor]["end"]
    return out


def _allowed_types(ctx: Ctx, sheet: dict) -> list[str]:
    reg = (sheet.get("register") or "").strip().lower()
    tbr = ctx.s3["templates_by_register"]
    allowed = list(tbr.get(reg) or tbr.get("default") or [])
    rtype = ((sheet.get("reaction") or {}).get("type") or "").lower()
    if rtype in set(ctx.s3.get("emotional_reaction_types") or []) and "emotional" not in allowed:
        allowed.append("emotional")
    if not loops_on(ctx.facts, ctx.cfg):
        allowed = [t for t in allowed if t != "loop"]          # founder, 2026-10-30: loops stay off; never built, never scored
    return allowed


def generate_scene(ctx: Ctx, sheet: dict) -> tuple[list[dict], list[dict]]:
    """All candidates of one scene before the caps, plus the notes on what was not generated and why."""
    out: list[dict] = []
    notes: list[dict] = []
    beats = sheet_beats(ctx, sheet)
    if not beats or not (sheet.get("payoff") or {}).get("line"):
        notes.append({"scene_id": sheet["scene_id"], "why": "sheet has no beats / payoff line"})
        return out, notes
    allowed = _allowed_types(ctx, sheet)
    moments = _moments(ctx, sheet, beats)
    s3 = ctx.s3
    m_tag = lambda i: f" (moment {i + 1})" if len(moments) > 1 else ""

    def add(c: dict | None, why_not: str):
        if c is None:
            notes.append({"scene_id": sheet["scene_id"], "why": why_not})
        else:
            out.append(c)

    for i, m in enumerate(moments):
        p, first, anchor, reaction = m["payoff"], m["first"], m["anchor"], m["reaction"]
        p_t = m["payoff_t"]
        # ---- comedy: full arc (untrimmed + trimmed twin), tight, cold open
        if "comedy" in allowed:
            add(build_candidate(ctx, sheet, "comedy", "full_arc", "none", first, anchor, p, reaction, beats, note=f"whole moment{m_tag(i)}"),
                f"comedy full arc{m_tag(i)}: non-story line inside")
            tw = build_candidate(ctx, sheet, "comedy", "full_arc_trimmed", "lines", first, anchor, p, reaction, beats, note=f"whole moment, trimmed{m_tag(i)}")
            if tw and (tw["removed"]["gaps_shortened"] or tw["removed"]["gaps_removed"] or tw["removed"]["lines_skipped"]):
                out.append(tw)
            turns = [b for b in m["opening_beats"] if b["type"] == "turn"]
            if turns:
                pair(ctx, sheet, out, notes, "comedy", "tight", turns[-1]["line"], anchor, p, reaction, beats, f"from the turn{m_tag(i)}")
            esc = [b for b in m["opening_beats"] if b["type"] == "escalation" and p_t - b["t"] >= float(s3["cold_open_min_before_payoff_s"]) - EPS]
            if esc:
                pair(ctx, sheet, out, notes, "comedy", "cold_open", esc[-1]["line"], anchor, p, reaction, beats,
                     f"opens on the last escalation >= {s3['cold_open_min_before_payoff_s']} s before the payoff{m_tag(i)}")
        # ---- emotional: full (untrimmed + trimmed twin), peak
        if "emotional" in allowed:
            add(build_candidate(ctx, sheet, "emotional", "full", "none", first, anchor, p, reaction, beats, note=f"build to the peak, hold included{m_tag(i)}"),
                f"emotional full{m_tag(i)}: non-story line inside")
            tw = build_candidate(ctx, sheet, "emotional", "full_trimmed", "lines", first, anchor, p, reaction, beats, note=f"build to the peak, trimmed{m_tag(i)}")
            if tw and (tw["removed"]["gaps_shortened"] or tw["removed"]["gaps_removed"] or tw["removed"]["lines_skipped"]):
                out.append(tw)
            before = [b for b in m["opening_beats"] if b["type"] in ("setup_for_payoff", "turn", "escalation", "setup")]
            if before and before[-1]["line"] != first:
                pair(ctx, sheet, out, notes, "emotional", "peak", before[-1]["line"], anchor, p, reaction, beats, f"from the beat before the peak{m_tag(i)}")
        # ---- loop: a short exchange ending right after the punchline (its own pause or laugh), plus a one-beat-earlier start
        if "loop" in allowed:
            l_first = _loop_opening(ctx, sheet, beats, p, p_t)
            if l_first:
                base = m["base_anchor"]                         # a loop ends right after the punchline: never on the button
                l_anchor = base if ctx.line[base]["start"] - p_t <= 6.0 else p
                made = {}
                for sfx, first in (("", l_first), ("_early", _one_beat_earlier(ctx, sheet, beats, l_first))):
                    if not first:
                        continue
                    c = build_candidate(ctx, sheet, "loop", "one" + sfx, "none", first, l_anchor, p, reaction, beats, kind="loop",
                                        note=f"punchline loop{', one beat earlier' if sfx else ''}{m_tag(i)}")
                    add(c, f"loop{sfx}{m_tag(i)}: non-story line inside")
                    if c is not None:
                        made["one" + sfx] = c
                        if twin_worthwhile(ctx, sheet, c):
                            tw = build_candidate(ctx, sheet, "loop", "one" + sfx + "_trimmed", "lines", first, l_anchor, p, reaction, beats, kind="loop", note=c["note"] + ", trimmed")
                            if tw and (tw["removed"]["gaps_shortened"] or tw["removed"]["gaps_removed"] or tw["removed"]["lines_skipped"]):
                                out.append(tw)
                _link_variants(made)
    # ---- open-question END-VARIANTS (founder, 2026-10-29; replaces the hook templates and their 45 s start limit): each
    # Stage 2 hook_moment adds a variant to its scene's normal moment: the moment's natural start, ending on the
    # open-question line. Same type and payoff line as the moment (Stage 6 groups it with the moment's other versions).
    oq_type = next((t for t in (PRIMARY_TYPE_BY_REGISTER.get((sheet.get("register") or "").lower(), "comedy"),) + LONG_TYPES if t in allowed), None)
    for hm in sheet.get("hook_moments") or []:
        q = hm.get("line")
        if oq_type is None or q not in ctx.line or ctx.line[q]["kind"] != "speech":
            continue
        q_t = ctx.line[q]["start"]
        m = next((x for x in moments if ctx.line[x["anchor"]]["end"] >= q_t - EPS), moments[-1])
        if ctx.line[m["first"]]["start"] >= q_t - EPS:
            notes.append({"scene_id": sheet["scene_id"], "why": f"open_question at {q}: the line is at or before the moment's natural start"})
            continue
        note = "ends on the open question: " + (hm.get("why") or "")[:80]
        base = build_candidate(ctx, sheet, oq_type, "open_question", "none", m["first"], q, m["payoff"], None, beats, kind="open_question", note=note)
        add(base, f"open_question at {q}: non-story line inside")
        if base is not None:
            tw = build_candidate(ctx, sheet, oq_type, "open_question_trimmed", "lines", m["first"], q, m["payoff"], None, beats, kind="open_question", note=note + ", trimmed")
            if tw and (tw["removed"]["gaps_shortened"] or tw["removed"]["gaps_removed"] or tw["removed"]["lines_skipped"]):
                out.append(tw)
    # ---- a laugh elsewhere than the payoff: its own loop
    if "loop" in allowed:
        r = sheet.get("reaction") or {}
        after = r.get("after_line")
        if after in ctx.line and r.get("type") == "laugh" and all(abs(ctx.line[after]["start"] - m["payoff_t"]) > 6.0 and after != m["anchor"] for m in moments):
            l_first = _loop_opening(ctx, sheet, beats, after, ctx.line[after]["start"])
            if l_first:
                made = {}
                for sfx, first in (("", l_first), ("_early", _one_beat_earlier(ctx, sheet, beats, l_first))):
                    if not first:
                        continue
                    c = build_candidate(ctx, sheet, "loop", "one" + sfx, "none", first, after, after, r, beats, kind="loop",
                                        note="exchange ending on the laugh" + (", one beat earlier" if sfx else ""))
                    add(c, f"laugh loop{sfx}: non-story line inside")
                    if c is not None:
                        made["one" + sfx] = c
                _link_variants(made)
    # ---- several payoffs: one extended arc spanning all of them
    if len(moments) > 1:
        first, last = moments[0]["first"], moments[-1]
        for ctype, variant in (("comedy", "extended"), ("emotional", "extended")):
            if ctype in allowed:
                add(build_candidate(ctx, sheet, ctype, variant, "lines", first, last["anchor"], last["payoff"], last["reaction"], beats,
                                    note=f"extended arc over {len(moments)} payoffs"), f"{ctype} extended arc: non-story line inside")
                un = build_candidate(ctx, sheet, ctype, "extended_untrimmed", "none", first, last["anchor"], last["payoff"], last["reaction"], beats,
                                     note=f"extended arc over {len(moments)} payoffs, untrimmed")
                if un:
                    out.append(un)
    # founder, 2026-10-29: record the button line on every candidate that ends on one
    buttons = {m["button"]: m["button_why"] for m in moments if m.get("button")}
    for c in out:
        if c.get("anchor_line") in buttons and c["variant"] not in ("open_question", "open_question_trimmed") and c["type"] != "loop":
            c["button_line"], c["button_why"] = c["anchor_line"], buttons[c["anchor_line"]]
            c["end_note"] += ", ends after the button line"
    return out, notes


def pair(ctx: Ctx, sheet: dict, out: list[dict], notes: list[dict], ctype: str, variant: str, first: str, anchor: str, payoff: str,
         reaction: dict | None, beats: list[dict], note: str) -> None:
    """An untrimmed candidate plus its trimmed twin (``<variant>_trimmed``) when the trims remove anything (founder,
    2026-10-27: every trimmed candidate has an untrimmed sibling; Stage 6 outputs both)."""
    base = build_candidate(ctx, sheet, ctype, variant, "none", first, anchor, payoff, reaction, beats, note=note)
    if base is None:
        notes.append({"scene_id": sheet["scene_id"], "why": f"{ctype} {variant}: non-story line inside"})
        return
    out.append(base)
    tw = build_candidate(ctx, sheet, ctype, f"{variant}_trimmed", "lines", first, anchor, payoff, reaction, beats, note=note + ", trimmed")
    if tw and (tw["removed"]["gaps_shortened"] or tw["removed"]["gaps_removed"] or tw["removed"]["lines_skipped"]):
        out.append(tw)


def twin_worthwhile(ctx: Ctx, sheet: dict, base: dict) -> bool:
    """Founder, 2026-10-27: a hook / loop gets a trimmed twin only when it contains a removable (shorten_to / remove,
    never a hold) stretch of at least ``short_twin_min_stretch_s`` or at least ``short_twin_min_skippable`` skippable
    lines that are not beats."""
    tr = ctx.s3["trims"]
    a, b = base["start"], base["end"]
    big = any(nd.get("recommend") in ("shorten_to", "remove") and nd.get("kind") != "reaction_hold" and nd.get("start") is not None
              and float(nd["start"]) >= a - EPS and float(nd["end"]) <= b + EPS and float(nd["end"]) - float(nd["start"]) >= float(tr.get("short_twin_min_stretch_s", 4.0)) - EPS
              for nd in sheet.get("non_dialogue") or [])
    protected = set(base["beats_included"]) | {base.get("anchor_line"), base.get("payoff_line")}
    n_skip = sum(1 for sk in sheet.get("skippable_lines") or [] if sk.get("line") in ctx.line and sk["line"] not in protected
                 and ctx.line[sk["line"]]["start"] >= a - EPS and ctx.line[sk["line"]]["end"] <= b + EPS)
    return big or n_skip >= int(tr.get("short_twin_min_skippable", 2))


def _one_beat_earlier(ctx: Ctx, sheet: dict, beats: list[dict], first: str) -> str | None:
    """The beat (any opening type) immediately before line ``first`` inside the scene, else None."""
    t = ctx.line[first]["start"]
    earlier = [b for b in beats if b["type"] in BEAT_TYPES_OPENING and b["t"] < t - EPS and b["t"] >= sheet["start"] - EPS]
    return earlier[-1]["line"] if earlier else None


def _link_variants(made: dict[str, dict]) -> None:
    """Sibling links among a reveal's hook / loop variants: ``end_sibling`` (reveal <-> before_reveal, same start) and
    ``wider_sibling`` (a variant -> its one-beat-earlier start, same end). Stage 4 widens along wider_sibling."""
    for v, c in made.items():
        c.setdefault("end_sibling", None); c.setdefault("wider_sibling", None)
    for v, c in made.items():
        base = v[:-len("_early")] if v.endswith("_early") else v
        other = base + "_early" if not v.endswith("_early") else base
        if other in made and not v.endswith("_early"):
            c["wider_sibling"] = made[other]["id_tmp"] if "id_tmp" in made[other] else None
            c["_wider_ref"] = made[other]
        if base == "one" and ("one_before" + ("_early" if v.endswith("_early") else "")) in made:
            c["_end_ref"] = made["one_before" + ("_early" if v.endswith("_early") else "")]
        if base == "one_before" and ("one" + ("_early" if v.endswith("_early") else "")) in made:
            c["_end_ref"] = made["one" + ("_early" if v.endswith("_early") else "")]


def _pause_opening(ctx: Ctx, sheet: dict, t: float, max_before: float, min_before: float, min_gap: float) -> str | None:
    """The line after the latest pause >= min_gap whose next line starts between min_before and max_before seconds
    before t (fallback: the latest pause >= end.min_gap_s in that window; else the line before t)."""
    def pick(gmin: float) -> str | None:
        best = None
        for g in ctx.gaps:
            if g["gap"] < gmin - EPS or g["next"] not in ctx.line:
                continue
            nxt = ctx.line[g["next"]]
            if nxt["start"] < sheet["start"] - EPS:
                continue
            d = t - nxt["start"]
            if min_before - EPS <= d <= max_before + EPS and (best is None or nxt["start"] > ctx.line[best]["start"]):
                best = g["next"]
        return best
    lid = pick(min_gap) or pick(float(ctx.s3["end"]["min_gap_s"]))
    if lid:
        return lid
    rows = [r for r in ctx.lines if r["kind"] == "speech" and sheet["start"] - EPS <= r["start"] < t - EPS]
    return rows[-1]["id"] if rows else None


def _loop_opening(ctx: Ctx, sheet: dict, beats: list[dict], anchor_lid: str, t: float) -> str | None:
    lp = ctx.s3["loop"]
    lo, hi = float(lp["min_before_s"]), float(lp["max_before_s"])
    cands: list[str] = []
    bs = [b for b in beats if b["line"] != anchor_lid and b["type"] not in ("reaction", "other") and lo - EPS <= t - b["t"] <= hi + EPS]
    if bs:
        cands.append(bs[-1]["line"])
    pl = _pause_opening(ctx, sheet, t, hi, lo, float(lp["start_gap_s"]))
    if pl:
        cands.append(pl)
    if not cands:
        return None
    return max(cands, key=lambda lid: ctx.line[lid]["start"])


# ----------------------------------------------------------------------------------------------- validation
def validate_candidate(ctx: Ctx, c: dict, sheet: dict, beats: list[dict]) -> list[str]:
    errs = []
    segs = c["segments"]
    if not segs:
        return ["no segments"]
    for a, b in segs:
        if b - a <= EPS:
            errs.append(f"empty segment {a}-{b}")
    for (a0, b0), (a1, b1) in zip(segs, segs[1:]):
        if a1 < b0 - EPS:
            errs.append(f"segments overlap or are out of order: {b0} > {a1}")
    pad = float(ctx.s3["start"]["pad_s"])
    if segs[0][0] < sheet["start"] - pad - EPS or segs[-1][1] > sheet["end"] + EPS:
        errs.append(f"outside the scene {fmt_ts(sheet['start'])}-{fmt_ts(sheet['end'])}: {fmt_ts(segs[0][0])}-{fmt_ts(segs[-1][1])}")
    for a, b in segs:
        for t in (a, b):
            if not ctx.on_pause(t, sheet):
                errs.append(f"boundary {fmt_ts(t)} is not on a gap-table pause")
            r = ctx.inside_line(t)
            if r:
                errs.append(f"boundary {fmt_ts(t)} falls inside {r['id']}")
    anchor_end = ctx.line[c["anchor_line"]]["end"]
    cut_beats = {x for cc in c["removed"].get("critic_cut") or [] for x in cc.get("beats_cut") or []}
    for b in beats:
        if b["line"] in cut_beats:
            continue                                           # founder, 2026-10-29: the critic trim may cut a flagged stretch's beats
        if c["start"] - EPS <= b["t"] and b["end"] <= anchor_end + EPS:
            if not any(a - EPS <= b["t"] and b["end"] <= bb + EPS for a, bb in segs):
                errs.append(f"beat {b['line']} between start and payoff is not inside the kept segments")
            if b["line"] not in c["beats_included"]:
                errs.append(f"beat {b['line']} missing from beats_included")
    if abs(sum(b - a for a, b in segs) - c["final_duration"]) > 0.01:
        errs.append("final_duration does not match the segments")
    if abs((c["end"] - c["start"]) - c["raw_duration"]) > 0.01:
        errs.append("raw_duration does not match start/end")
    for lid in c["lines"]:
        if lid in ctx.non_story and ctx.line[lid]["start"] < anchor_end - EPS:
            errs.append(f"non-story line {lid} inside the candidate")
    return errs


def schema_errors(c: dict) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message[:120]}" for e in jsonschema.Draft202012Validator(schema).iter_errors(c)]


# ----------------------------------------------------------------------------------------------- caps and ids
TWIN_BASE = {"full_arc_trimmed": "full_arc", "full_trimmed": "full", "tight_trimmed": "tight", "cold_open_trimmed": "cold_open",
             "peak_trimmed": "peak", "one_trimmed": "one", "one_before_trimmed": "one_before", "one_early_trimmed": "one_early",
             "one_before_early_trimmed": "one_before_early", "extended_untrimmed": "extended", "open_question_trimmed": "open_question",
             "scene_trimmed": "scene", "situation_trimmed": "situation"}


def _is_twin(c: dict) -> bool:
    return c["variant"] in TWIN_BASE


def _twin_of(c: dict, cands: list[dict]) -> dict | None:
    base = TWIN_BASE.get(c["variant"])
    if not base:
        return None
    return next((o for o in cands if o is not c and o["scene_id"] == c["scene_id"] and o["type"] == c["type"] and o["variant"] == base
                 and o["payoff_line"] == c["payoff_line"] and abs(o["start"] - c["start"]) < EPS and abs(o["end"] - c["end"]) < EPS), None)


PRIMARY_TYPE_BY_REGISTER = {"comedy": "comedy", "comedy_to_emotional": "emotional", "emotional": "emotional", "drama": "emotional",
                            "romance": "emotional", "romantic": "emotional"}


def _primary_rank(c: dict) -> int:
    prim = PRIMARY_TYPE_BY_REGISTER.get((c.get("register") or "").lower(), "comedy")
    return 0 if c["type"] == prim else 1


def dedupe(cands: list[dict]) -> tuple[list[dict], list[dict]]:
    """Merge exact duplicates (same scene, segments); the survivor is the one typed like the scene's register (a
    comedy_to_emotional scene keeps the emotional reading) and records the merged types / variants in ``also``."""
    seen: dict[tuple, dict] = {}
    merged = []
    for c in sorted(cands, key=lambda c: (c["scene_id"], _primary_rank(c), TYPE_ORDER.get(c["type"], 9), c["start"])):
        c.pop("id_tmp", None)
        key = (c["scene_id"], tuple((round(a, 2), round(b, 2)) for a, b in c["segments"]))
        if key in seen:
            s = seen[key]
            s.setdefault("also", []).append({"type": c["type"], "variant": c["variant"]})
            merged.append({"scene_id": c["scene_id"], "type": c["type"], "variant": c["variant"], "merged_into": f"{s['type']}/{s['variant']}", "why": "exact duplicate"})
            continue
        seen[key] = c
    return list(seen.values()), merged


def _drop_priority(c: dict) -> tuple:
    """Lower = dropped first: fewest beats; among equals a hook / loop before a long type, a trimmed twin before its
    untrimmed base, then the shorter one."""
    return (len(c["beats_included"]), 0 if c["type"] in SHORT_TYPES else 1, 0 if _is_twin(c) else 1, c["final_duration"])


def _short_rank(c: dict) -> tuple:
    """Higher = better short candidate: not over its target length (a 33 s 'hook' that runs to the scene end is not a
    hook), built on the sheet's own payoff or ending on a laugh / hold, then more beats, then shorter."""
    lo, hi = c["target_s"]
    laugh = 1 if c["end_note"].startswith("laugh/hold") else 0
    on_payoff = 1 if (c.get("flags") or {}).get("primary_payoff") else 0
    return (1 if c["final_duration"] <= hi + 1e-6 else 0, laugh, on_payoff, len(c["beats_included"]), -c["final_duration"])


def slot_key(c: dict) -> tuple:
    """The cap slot a candidate belongs to. Siblings that exist to be compared or widened to share ONE slot (founder,
    2026-10-27): an untrimmed arc and its trimmed twin; a reveal's hook variants (both ends, both starts, their twins);
    a loop and its one-beat-earlier start. Long types: one slot per (variant family, boundaries)."""
    if c["type"] in SHORT_TYPES:
        return (c["scene_id"], c["type"], c.get("payoff_line"), c.get("anchor_line") if c["type"] == "loop" and c.get("note", "").startswith("exchange ending on the laugh") else "")
    base = TWIN_BASE.get(c["variant"], c["variant"])
    if c["variant"] == "extended_untrimmed":
        base = "extended"
    return (c["scene_id"], c["type"], base, c.get("payoff_line"), round(c["start"], 1), round(c["end"], 1))


def apply_caps(cands: list[dict], cfg3: dict) -> tuple[list[dict], list[dict]]:
    """Per-scene and per-episode caps count SLOTS (families of siblings, see ``slot_key``) and drop whole families,
    fewest beats first (a family's beats = its best member's). Protected: the untrimmed arc of the scene's primary
    moment and the best ``protect_short_per_scene`` hook / loop families."""
    caps = cfg3["caps"]
    per_scene, per_episode = int(caps["per_scene"]), int(caps["per_episode"])
    protect_short = int(caps.get("protect_short_per_scene", 1))
    protect_story = int(caps.get("protect_shortest_story_per_scene", 1))
    dropped: list[dict] = []
    keep = list(cands)

    def families(cs: list[dict]) -> dict[tuple, list[dict]]:
        fam: dict[tuple, list[dict]] = {}
        for c in cs:
            fam.setdefault(slot_key(c), []).append(c)
        return fam

    def slots(cs: list[dict]) -> int:
        return len(families(cs))

    def fam_best(members: list[dict]) -> dict:
        return max(members, key=lambda c: (len(c["beats_included"]), c["trim_level"] == "none", c["final_duration"]))

    def protected_keys(scene_cs: list[dict]) -> set[tuple]:
        fam = families(scene_cs)
        out = set()
        prim = min((o for o in scene_cs if o["variant"] in ("full_arc", "full") and o["trim_level"] == "none"), key=lambda o: o["start"], default=None)
        if prim is not None:
            out.add(slot_key(prim))
        shorts = sorted((k for k, m in fam.items() if k[1] in SHORT_TYPES), key=lambda k: _short_rank(max(fam[k], key=_short_rank)), reverse=True)
        out.update(shorts[:protect_short])
        # founder, 2026-10-29: the shortest comedy / emotional family of the scene is protected too (a peak has the fewest
        # beats by construction, so the cap used to eat it first: scene 13's peaks never reached Stage 4)
        story = sorted((k for k, m in fam.items() if k[1] in ("comedy", "emotional")), key=lambda k: (min(c["final_duration"] for c in fam[k]), k))
        out.update(story[:protect_story])
        return out

    def drop_one(scene_id: int, why: str) -> bool:
        scene_cs = [c for c in keep if c["scene_id"] == scene_id]
        fam = families(scene_cs)
        prot = protected_keys(scene_cs)
        cand = sorted((k for k in fam if k not in prot), key=lambda k: _drop_priority(fam_best(fam[k])))
        if not cand:
            return False
        for c in fam[cand[0]]:
            keep.remove(c)
            dropped.append({"scene_id": scene_id, "type": c["type"], "variant": c["variant"], "start_str": c["start_str"], "end_str": c["end_str"],
                            "beats": len(c["beats_included"]), "why": why})
        return True

    for sid in sorted({c["scene_id"] for c in keep}):
        while slots([c for c in keep if c["scene_id"] == sid]) > per_scene:
            if not drop_one(sid, f"per-scene cap {per_scene} slots: fewest beats (whole family)"):
                break
    guard = 0
    while slots(keep) > per_episode and guard < 1000:
        guard += 1
        by_scene = {}
        for c in keep:
            by_scene.setdefault(c["scene_id"], []).append(c)
        order = sorted(by_scene, key=lambda s: (slots(by_scene[s]), -s), reverse=True)
        done = False
        for sid in order:
            if drop_one(sid, f"episode cap {per_episode} slots: fewest beats in the fullest scene (whole family)"):
                done = True
                break
        if not done:
            break
    # a twin whose base was dropped keeps the slot: nothing to do; ids
    keep.sort(key=lambda c: (c["scene_id"], TYPE_ORDER.get(c["type"], 9), c["start"], c["end"]))
    per: dict[int, int] = {}
    for c in keep:
        n = per.get(c["scene_id"], 0)
        per[c["scene_id"]] = n + 1
        letters = ""
        k = n
        while True:
            letters = chr(ord("a") + k % 26) + letters
            k = k // 26 - 1
            if k < 0:
                break
        c["id"] = f"C{c['scene_id']}{letters}"
    kept_ids = {id(c) for c in keep}
    for c in keep:                                            # founder, 2026-10-27: Stage 6 outputs both versions of a moment
        c["untrimmed_sibling"] = None
        c["trimmed_sibling"] = None
        c["end_sibling"] = c["_end_ref"]["id"] if c.get("_end_ref") is not None and id(c["_end_ref"]) in kept_ids else None
        c["wider_sibling"] = c["_wider_ref"]["id"] if c.get("_wider_ref") is not None and id(c["_wider_ref"]) in kept_ids else None
        c.pop("_end_ref", None); c.pop("_wider_ref", None)
    for c in keep:
        if _is_twin(c):
            base = _twin_of(c, keep)
            if base is None:
                continue
            if c["trim_level"] == "none":                     # extended_untrimmed: c is the untrimmed one, base the trimmed
                base["untrimmed_sibling"], c["trimmed_sibling"] = c["id"], base["id"]
            else:
                c["untrimmed_sibling"], base["trimmed_sibling"] = base["id"], c["id"]
    return keep, dropped


# ----------------------------------------------------------------------------------------------- threads and critic trims
def _sheet_at(sheets: list[dict], t: float, pad: float = 0.0) -> dict | None:
    return next((sh for sh in sheets if sh["start"] - pad - EPS <= t <= sh["end"] + EPS), None)


def _letters(k: int) -> str:
    out = ""
    while True:
        out = chr(ord("a") + k % 26) + out
        k = k // 26 - 1
        if k < 0:
            return out


def _segments_minus(segs: list[list[float]], cuts: list[tuple[float, float]]) -> list[list[float]]:
    out = []
    for a, b in segs:
        pieces = [(float(a), float(b))]
        for ca, cb in sorted(cuts):
            nxt = []
            for x, y in pieces:
                if cb <= x + EPS or ca >= y - EPS:
                    nxt.append((x, y)); continue
                if ca > x + EPS:
                    nxt.append((x, ca))
                if cb < y - EPS:
                    nxt.append((cb, y))
            pieces = nxt
        out += [[round(x, 3), round(y, 3)] for x, y in pieces if y - x > EPS]
    return out


def _kept_speech(ctx: Ctx, segs: list[list[float]]) -> list[dict]:
    return [r for r in ctx.lines if r["kind"] == "speech" and any(a - EPS <= r["start"] and r["end"] <= b + EPS for a, b in segs)]


def thread_candidates(ctx: Ctx, sheets: list[dict]) -> tuple[list[dict], list[dict]]:
    """founder, 2026-10-29: one candidate per episode-pass thread (a setup that pays off in a later scene): the setup lines
    from their natural start to the setup's last line (usual end rule), one cut, then the payoff scene from its natural
    start to its button (``payoff_end_line`` when given, else the sheet's button_line, else the end of its first moment)."""
    out, notes = [], []
    threads = ((ctx.facts.get("episode_pass") or {}).get("threads")) or []
    all_ids = [r["id"] for r in ctx.lines]
    by_id = {sh["scene_id"]: sh for sh in sheets}
    for i, th in enumerate(threads):
        rng = th.get("setup_lines")
        ids, err = expand_lines(rng, all_ids) if isinstance(rng, str) else (list(rng or []), None)
        speech = [x for x in ids if x in ctx.line and ctx.line[x]["kind"] == "speech" and x not in ctx.non_story]
        p_sheet = by_id.get(int(th.get("payoff_scene") or -1))
        if err or not speech or p_sheet is None:
            notes.append({"scene_id": th.get("payoff_scene"), "why": f"thread {i + 1}: setup {rng!r} or payoff scene {th.get('payoff_scene')} not usable ({err or 'no sheet / no lines'})"})
            continue
        first, last = speech[0], speech[-1]
        s_sheet = _sheet_at(sheets, ctx.line[first]["start"])
        if s_sheet is None or s_sheet["scene_id"] == p_sheet["scene_id"] or p_sheet["start"] < ctx.line[last]["end"] - EPS:
            notes.append({"scene_id": p_sheet["scene_id"], "why": f"thread {i + 1}: the payoff scene must be a later scene than the setup"})
            continue
        a0, _, sinfo = snap_start(ctx, first, s_sheet)
        a1, _, _ = snap_end(ctx, last, s_sheet)
        p_lines = [r for r in ctx.lines if r["kind"] == "speech" and p_sheet["start"] - EPS <= r["start"] <= p_sheet["end"] + EPS and r["id"] not in ctx.non_story]
        end_lid = th.get("payoff_end_line") or p_sheet.get("button_line")
        if not end_lid:
            beats = sheet_beats(ctx, p_sheet)
            ms = _moments(ctx, p_sheet, beats) if beats and (p_sheet.get("payoff") or {}).get("line") else []
            end_lid = ms[0]["anchor"] if ms else None
        if not p_lines or end_lid not in ctx.line:
            notes.append({"scene_id": p_sheet["scene_id"], "why": f"thread {i + 1}: no payoff end line"})
            continue
        ptype = PRIMARY_TYPE_BY_REGISTER.get((p_sheet.get("register") or "").lower(), "comedy")
        base = build_candidate(ctx, p_sheet, ptype, "thread", "none", p_lines[0]["id"], end_lid, end_lid, None, sheet_beats(ctx, p_sheet),
                               note=f"thread: setup {first}-{last} (scene {s_sheet['scene_id']}) pays off in scene {p_sheet['scene_id']}: {(th.get('why') or '')[:80]}")
        if base is None:
            notes.append({"scene_id": p_sheet["scene_id"], "why": f"thread {i + 1}: non-story line inside the payoff part"})
            continue
        if a1 >= base["segments"][0][0] - EPS:
            notes.append({"scene_id": p_sheet["scene_id"], "why": f"thread {i + 1}: the setup runs into the payoff scene"})
            continue
        segs = [[round(a0, 3), round(a1, 3)]] + base["segments"]
        setup_ids = [r["id"] for r in _kept_speech(ctx, [[a0, a1]])]
        setup_beats = [b["line"] for b in sheet_beats(ctx, s_sheet) if a0 - EPS <= b["t"] and b["end"] <= a1 + EPS]
        c = dict(base)
        c.update({"start": round(a0, 3), "segments": segs, "raw_duration": round(base["end"] - a0, 3),
                  "final_duration": round(sum(b - a for a, b in segs), 3), "beats_included": setup_beats + base["beats_included"],
                  "lines": setup_ids + base["lines"], "n_lines": len(setup_ids) + base["n_lines"], "start_str": fmt_ts(a0),
                  "start_note": sinfo.get("rule"), "thread": {"setup_scene": s_sheet["scene_id"], "setup_lines": [first, last], "payoff_scene": p_sheet["scene_id"],
                                                             "end_line": end_lid, "why": th.get("why") or "", "source": th.get("source") or "episode pass"},
                  "context_required": list(dict.fromkeys(list(s_sheet.get("context_required") or []) + list(base["context_required"])))})
        c["removed"] = dict(base["removed"]); c["removed"]["thread_cut"] = {"from": round(a1, 3), "to": base["segments"][0][0],
                                                                          "range": f"{fmt_ts(a1)}-{fmt_ts(base['segments'][0][0])}"}
        fd = c["final_duration"]
        c["flags"] = dict(base["flags"], over_platform_max=fd > float(ctx.s3["platform_max_s"]) + EPS, thread=True)
        c["eligible_for_final"] = fd <= float(ctx.s3["platform_max_s"]) + EPS
        c["length_vs_target"] = _length_vs_target(fd, c["target_s"], float(ctx.s3["targets_s"].get("comedy_sketch_max") or 0) if ptype == "comedy" else None)
        out.append(c)
    return out, notes


def scene_parts(ctx: Ctx, sheet: dict) -> list[tuple[list[dict], str]]:
    """The scene's speech lines (non-story excluded) as one part, or two when the beat sheet shows two separate situations:
    a second ``entry`` beat AFTER a payoff / peak beat starts the second one (founder, 2026-10-30; config
    ``scene_variant.split_on_entry_after_payoff``). [(lines, why)]."""
    lines = [r for r in ctx.lines if r["kind"] == "speech" and sheet["start"] - EPS <= r["start"] <= sheet["end"] + EPS and r["id"] not in ctx.non_story]
    if not lines:
        return []
    if "situations" in sheet or not (ctx.s3.get("scene_variant") or {}).get("split_on_entry_after_payoff", True):
        return [(lines, "whole scene")]                       # founder, 2026-10-30: situations come from Stage 2 when it has them
    beats = sheet_beats(ctx, sheet)
    first_pay = next((b["t"] for b in beats if b["type"] in PAYOFF_TYPES), None)
    split = next((b for b in beats if b["type"] == "entry" and first_pay is not None and b["t"] > first_pay + EPS), None)
    if split is None:
        return [(lines, "whole scene")]
    a = [r for r in lines if r["start"] < split["t"] - EPS]
    b = [r for r in lines if r["start"] >= split["t"] - EPS]
    return [(p, f"situation {i + 1} of 2 (split at the entry beat {split['line']})") for i, p in enumerate((a, b)) if p]


def scene_variants(ctx: Ctx, sheets: list[dict]) -> tuple[list[dict], list[dict]]:
    """founder, 2026-10-30: every scene (or situation) as a clip: its first line to its last line (the usual end rule after
    it), trimmed with Stage 2's non_dialogue recommendations and skippable_lines (``scene_trimmed``), plus the untrimmed
    twin (``scene``). Payoff line = the sheet's payoff when inside the part, else the part's last payoff / peak beat, else
    its last line (Stage 6 groups the variant with that moment). Outside the caps; always judged."""
    out, notes = [], []
    for sh in sheets:
        beats = sheet_beats(ctx, sh)
        ptype = PRIMARY_TYPE_BY_REGISTER.get((sh.get("register") or "").lower(), "comedy")
        max_s = float((ctx.s3.get("scene_variant") or {}).get("max_scene_s", 0) or 0)
        for part, why in scene_parts(ctx, sh):
            first, last = part[0]["id"], part[-1]["id"]
            if max_s and part[-1]["end"] - part[0]["start"] > max_s + EPS:
                notes.append({"scene_id": sh["scene_id"], "why": f"scene variant ({why}): {part[-1]['end'] - part[0]['start']:.0f} s, over {max_s:.0f} s"})
                continue
            ids = {r["id"] for r in part}
            plid = (sh.get("payoff") or {}).get("line")
            if plid not in ids:
                pb = [b for b in beats if b["type"] in PAYOFF_TYPES and b["line"] in ids]
                plid = pb[-1]["line"] if pb else last
            base = build_candidate(ctx, sh, ptype, "scene", "none", first, last, plid, None, beats, note=f"whole scene: {why}")
            if base is None:
                notes.append({"scene_id": sh["scene_id"], "why": f"scene variant ({why}): non-story line inside"})
                continue
            base["scene_part"] = why
            out.append(base)
            tw = build_candidate(ctx, sh, ptype, "scene_trimmed", "lines", first, last, plid, None, beats, note=f"whole scene, trimmed: {why}")
            if tw and (tw["removed"]["gaps_shortened"] or tw["removed"]["gaps_removed"] or tw["removed"]["lines_skipped"]):
                tw["scene_part"] = why
                tw["_untrimmed"] = base
                out.append(tw)
    return out, notes


def _speech_overlaps(ctx: Ctx, a: float, b: float) -> bool:
    return any(r["kind"] == "speech" and not r.get("zero_len") and r["start"] < b - EPS and r["end"] > a + EPS for r in ctx.lines)


def visual_open(ctx: Ctx, sheet: dict, first_lid: str) -> tuple[float, dict] | None:
    """founder, 2026-10-30: a clip may START on a visual beat inside the silent stretch right before its first line,
    (start, beat) or None."""
    g = ctx.gap_before_line(first_lid)
    if g is None:
        return None
    lo, hi = float(g["t_prev_end"]), float(g["t_next_start"])
    vbs = [vb for vb in sheet.get("visual_beats") or [] if lo - EPS <= float(vb["start"]) < hi - 0.5]
    if not vbs:
        return None
    vb = max(vbs, key=lambda v: (int(v.get("strength") or 0), -float(v["start"])))
    t = round(float(vb["start"]), 3)
    return max(lo, t), vb


def _with_start(c: dict, t: float, why: str) -> dict:
    c = dict(c)
    segs = [list(x) for x in c["segments"]]
    segs[0][0] = round(t, 3)
    c.update({"segments": segs, "start": round(t, 3), "start_str": fmt_ts(t), "raw_duration": round(c["end"] - t, 3),
              "final_duration": round(sum(b - a for a, b in segs), 3), "start_note": why})
    return c


def teaser_segment(ctx: Ctx, sheet: dict, c: dict) -> tuple[list[float], dict] | None:
    """founder, 2026-10-30: 1.5-2.5 s of the strongest visual beat (strength >= teaser.min_strength) that lies inside the
    clip, BEFORE its payoff line (it must not reveal the payoff) and in a silent stretch (no speech cut)."""
    tz = ctx.s3.get("teaser") or {}
    lo_s, hi_s, need = float(tz.get("min_s", 1.5)), float(tz.get("max_s", 2.5)), int(tz.get("min_strength", 7))
    pay = ctx.line.get(c.get("payoff_line"))
    limit = float(pay["start"]) if pay and c["start"] <= pay["start"] <= c["end"] else float(c["end"])
    best = None
    for vb in sheet.get("visual_beats") or []:
        if int(vb.get("strength") or 0) < need or not (c["start"] + 3.0 - EPS <= float(vb["start"]) and float(vb["end"]) <= limit + EPS):   # not the opening itself
            continue
        g = next((g for g in ctx.gaps if float(g["t_prev_end"]) - EPS <= float(vb["start"]) < float(g["t_next_start"])), None)
        if g is None:
            continue
        t0 = round(float(vb["start"]), 3)
        t1 = min(t0 + min(max(float(vb["end"]) - t0, lo_s), hi_s), float(g["t_next_start"]), limit)
        if t1 - t0 < lo_s - EPS or _speech_overlaps(ctx, t0, t1):
            continue
        if best is None or int(vb.get("strength") or 0) > int(best[1].get("strength") or 0):
            best = ([round(t0, 3), round(t1, 3)], vb)
    return best


def situation_variants(ctx: Ctx, sheets: list[dict]) -> tuple[list[dict], list[dict]]:
    """founder, 2026-10-30: per Stage 2 situation, the situation as a clip (start line to its button), untrimmed
    (``situation``) and trimmed (``situation_trimmed``), aiming at about a minute; it may open on a visual beat in the
    silent stretch before it; plus a ``teaser_open`` version when a strong visual beat (before the payoff) exists."""
    out, notes = [], []
    for sh in sheets:
        sits = sh.get("situations") or []
        if not sits:
            continue
        beats = sheet_beats(ctx, sh)
        ptype = PRIMARY_TYPE_BY_REGISTER.get((sh.get("register") or "").lower(), "comedy")
        for i, st in enumerate(sits, 1):
            a, b = st.get("start_line"), st.get("end_line")
            if a not in ctx.line or b not in ctx.line:
                continue
            ids = {r["id"] for r in ctx.lines if r["kind"] == "speech" and ctx.line[a]["start"] - EPS <= r["start"] <= ctx.line[b]["start"] + EPS}
            plid = (sh.get("payoff") or {}).get("line")
            if plid not in ids:
                pb = [x for x in beats if x["type"] in PAYOFF_TYPES and x["line"] in ids]
                plid = pb[-1]["line"] if pb else b
            tag = {"index": i, "what": st.get("what_happens") or "", "strength": st.get("strength")}
            note = f"situation {i}: {(st.get('what_happens') or '')[:80]}"
            base = build_candidate(ctx, sh, ptype, "situation", "none", a, b, plid, None, beats, note=note)
            if base is None:
                notes.append({"scene_id": sh["scene_id"], "why": f"{note}: non-story line inside"}); continue
            tw = build_candidate(ctx, sh, ptype, "situation_trimmed", "lines", a, b, plid, None, beats, note=note + ", trimmed")
            vo = visual_open(ctx, sh, a)
            made = [base] + ([tw] if tw and (tw["removed"]["gaps_shortened"] or tw["removed"]["gaps_removed"] or tw["removed"]["lines_skipped"]) else [])
            for k, c in enumerate(made):
                if vo:
                    c = _with_start(c, vo[0], f"visual beat: {(vo[1].get('what_is_seen') or '')[:60]}")
                    c["visual_open"] = vo[1]
                c["situation"] = tag
                c["target_s"] = [float(x) for x in (ctx.s3.get("situation") or {}).get("target_s", [45, 75])]
                c["length_vs_target"] = _length_vs_target(c["final_duration"], c["target_s"], None)
                made[k] = c
            if len(made) == 2:
                made[1]["_untrimmed"] = made[0]
            out += made
            tz = teaser_segment(ctx, sh, made[-1])
            if tz:
                src = made[-1]
                t = dict(src)
                t.update({"variant": "teaser_open", "segments": [tz[0]] + [list(x) for x in src["segments"]],
                          "final_duration": round(sum(y - x for x, y in [tz[0]] + src["segments"]), 3), "teaser": tz[1],
                          "note": src["note"] + f"; opens on a {tz[0][1] - tz[0][0]:.1f} s visual teaser ({(tz[1].get('what_is_seen') or '')[:60]})"})
                t.pop("_untrimmed", None)
                t["length_vs_target"] = _length_vs_target(t["final_duration"], t["target_s"], None)
                out.append(t)
    return out, notes


CHANNEL_SHORTS_DIR = Path(__file__).resolve().parents[1] / "data" / "channel_shorts"


def channel_short_candidates(ctx: Ctx, sheets: list[dict]) -> list[dict]:
    """founder, 2026-10-30: the episode's own matched channel Shorts as REFERENCE candidates (variant channel_short):
    scored by Stage 4 and judged by Stage 5 like everything else, never shipped. Segments = the Short's matched pieces
    on the episode clock, in the Short's order (a stitched Short has several)."""
    vid = ((ctx.facts.get("episode") or {}).get("video_id")) or ""
    p = CHANNEL_SHORTS_DIR / f"{vid}.json"
    if not (ctx.s3.get("channel_short_candidates") or {}).get("enabled", True) or not p.exists():
        return []
    out = []
    for s in (json.loads(p.read_text(encoding="utf-8")).get("accepted") or []):
        segs = [[round(float(x["episode_start"]), 3), round(float(x["episode_end"]), 3)] for x in sorted(s["segments"], key=lambda x: float(x["short_start"]))]
        speech = [r for r in ctx.lines if r["kind"] == "speech" and any(a - 0.3 <= r["start"] and r["end"] <= b + 0.3 for a, b in segs)]
        over = {sh["scene_id"]: sum(max(0.0, min(b, sh["end"]) - max(a, sh["start"])) for a, b in segs) for sh in sheets}
        if not over or max(over.values()) <= 0:
            continue
        sid = max(over, key=over.get)
        sh = next(x for x in sheets if x["scene_id"] == sid)
        last = next((r for r in reversed(speech) if segs[-1][0] - 0.3 <= r["start"]), speech[-1] if speech else None)
        fd = round(sum(b - a for a, b in segs), 3)
        ptype = PRIMARY_TYPE_BY_REGISTER.get((sh.get("register") or "").lower(), "comedy")
        out.append({"id": "", "scene_id": sid, "type": ptype, "variant": "channel_short", "trim_level": "none",
                    "start": min(a for a, _ in segs), "end": max(b for _, b in segs), "segments": segs,
                    "raw_duration": round(max(b for _, b in segs) - min(a for a, _ in segs), 3), "final_duration": fd,
                    "beats_included": [x["line"] for x in sheet_beats(ctx, sh) if any(a - 0.3 <= x["t"] <= b for a, b in segs)],
                    "payoff_line": last["id"] if last else None, "anchor_line": last["id"] if last else None, "reaction": None, "reaction_type": None,
                    "removed": {"gaps_shortened": [], "gaps_removed": [], "lines_skipped": []}, "extra_lines": {"start": [], "end": []},
                    "start_note": "the channel's own cut", "end_note": "the channel's own cut", "context_required": list(sh.get("context_required") or []),
                    "register": sh.get("register"), "dynamic": sh.get("dynamic") or {}, "callbacks": list(sh.get("callbacks") or []),
                    "flags": {"channel_short": True, "over_platform_max": False}, "target_s": _targets(ctx.s3, ptype),
                    "length_vs_target": _length_vs_target(fd, _targets(ctx.s3, ptype), None), "eligible_for_final": False,
                    "lines": [r["id"] for r in speech], "n_lines": len(speech), "note": f"channel Short {s['short_id']} ({s.get('views', 0):,} views): reference only, never shipped",
                    "start_str": fmt_ts(min(a for a, _ in segs)), "end_str": fmt_ts(max(b for _, b in segs)),
                    "channel_short": {"short_id": s["short_id"], "url": s.get("url"), "views": s.get("views"), "views_vs_median": s.get("views_vs_median"),
                                      "duration": s.get("duration"), "n_segments": len(segs), "scenes": sorted(k for k, v in over.items() if v > 0)}})
    return out


def critic_trim(ctx: Ctx, c: dict, z: dict) -> dict | None:
    """founder, 2026-10-29: the critic_trimmed sibling of a variant the critic flagged on PACE only: every merged swipe
    stretch (clip seconds, from the failing runs) keeps its first ``critic_trim.keep_head_s`` (moved to the end of a line
    in progress, plus the segment tail), then ONE cut that lands right before the next spoken line. Nothing from the
    payoff / end line on is ever cut (so never the reaction hold after the payoff)."""
    ct = ctx.s3["critic_trim"]
    head, gain = float(ct["keep_head_s"]), float(ct["min_gain_s"])
    tail = float(ctx.s3["trims"].get("segment_tail_s", 0.5))
    tmap = z.get("time_map") or []
    stretches = [tuple(x) for x in (z.get("pace_stretches") or CC.pace_stretches(z.get("runs") or []))]
    if not tmap or not stretches:
        return None

    def ep(t: float) -> float:
        t = max(0.0, min(float(t), float(tmap[-1]["clip_end"])))
        m = next((m for m in tmap if float(m["clip_start"]) - EPS <= t <= float(m["clip_end"]) + EPS), tmap[-1])
        return float(m["ep_start"]) + (t - float(m["clip_start"]))

    segs = [[float(a), float(b)] for a, b in c["segments"]]
    kept = _kept_speech(ctx, segs)
    guard = [ctx.line[x]["start"] for x in (c.get("payoff_line"), c.get("anchor_line")) if x in ctx.line and c["start"] - EPS <= ctx.line[x]["start"] <= c["end"] + EPS]
    protect_t = min(guard) if guard else c["end"]
    cuts, recs = [], []
    for fa, fb in stretches:
        a_ep, b_ep = ep(fa + head), ep(fb)
        r = ctx.inside_line(a_ep)
        if r is not None:
            nxt = next((x for x in kept if x["start"] >= r["end"] - EPS and x["id"] != r["id"]), None)
            a_ep = min(r["end"] + tail, nxt["start"] if nxt else r["end"] + tail)
        seg = next((sg for sg in segs if sg[0] - EPS <= a_ep <= sg[1] + EPS), None)
        if seg is None:
            continue
        land_line = next((x for x in kept if x["end"] > b_ep + EPS and x["start"] > a_ep + EPS), None)
        land = min(land_line["start"] if land_line else protect_t, protect_t)
        if land - a_ep < gain - EPS:
            continue
        gone = [x["id"] for x in kept if x["start"] >= a_ep - EPS and x["end"] <= land + EPS]
        cut_beats = [b for b in c["beats_included"] if b in gone]
        cuts.append((round(a_ep, 3), round(land, 3)))
        recs.append({"range": f"{fmt_ts(a_ep)}-{fmt_ts(land)}", "flagged_clip_s": [round(fa, 1), round(fb, 1)], "cut_at": round(a_ep, 3),
                     "lands_at": round(land, 3), "lands_before": land_line["id"] if land_line and abs(land_line["start"] - land) <= EPS else None,
                     "lines_cut": gone, "beats_cut": cut_beats, "to_s": round(head, 2)})
    if not cuts:
        return None
    new = _segments_minus([[round(a, 3), round(b, 3)] for a, b in segs], cuts)
    if not new:
        return None
    t = dict(c)
    lines = [r["id"] for r in _kept_speech(ctx, new)]
    t.update({"variant": "critic_trimmed", "segments": new, "final_duration": round(sum(b - a for a, b in new), 3), "lines": lines, "n_lines": len(lines),
              "beats_included": [b for b in c["beats_included"] if b in set(lines)], "critic_trim_of": c["id"], "critic_trim_of_variant": c["variant"],
              "critic_verdict": f"{z.get('verdict')} {z.get('n_pass')}/{z.get('n_runs')} (pace)",
              "note": (c.get("note") or "") + f"; critic-trimmed ({len(cuts)} swipe stretch(es) shortened)",
              "untrimmed_sibling": None, "trimmed_sibling": None, "end_sibling": None, "wider_sibling": None})
    t["removed"] = dict(c["removed"]); t["removed"]["critic_cut"] = recs
    t["flags"] = dict(c.get("flags") or {}, critic_trimmed=True)
    t["length_vs_target"] = _length_vs_target(t["final_duration"], t["target_s"], float(ctx.s3["targets_s"].get("comedy_sketch_max") or 0) if t["type"] == "comedy" else None)
    return t


def pace_flagged(facts: dict) -> dict[str, dict]:
    """Critic records (selection + archive) whose verdict is FAIL or BORDERLINE with reason class pace, by content key."""
    cr = facts.get("critic") or {}
    out = {}
    for z in list(cr.get("archive") or []) + list(cr.get("clips") or []):
        if not z.get("runs") or z.get("verdict") not in ("FAIL", "BORDERLINE"):
            continue
        if (z.get("reason_class") or CC.reason_class(z["runs"])) == "pace":
            out[CC.record_key(z)] = z
    return out


def validate_extra(ctx: Ctx, c: dict, sheets: list[dict]) -> list[str]:
    """Threads and critic trims: every kept segment inside one scene, every boundary on a pause and never inside a line,
    durations consistent (beats inside a cut are allowed: the cut is the point)."""
    errs = []
    segs = c["segments"]
    if not segs:
        return ["no segments"]
    if c["variant"] == "channel_short":                         # the channel's own cut: reference only
        return [] if abs(sum(b - a for a, b in segs) - c["final_duration"]) <= 0.01 else ["final_duration does not match the segments"]
    body = segs[1:] if c["variant"] == "teaser_open" else segs    # the teaser is lifted from later in the clip (founder, 2026-10-30)
    for (a0, b0), (a1, b1) in zip(body, body[1:]):
        if a1 < b0 - EPS:
            errs.append(f"segments overlap or are out of order: {b0} > {a1}")
    pad = float(ctx.s3["start"]["pad_s"])
    for a, b in segs:
        sh = _sheet_at(sheets, a, pad)
        if sh is None or b > sh["end"] + EPS:
            errs.append(f"segment {fmt_ts(a)}-{fmt_ts(b)} is not inside one scene"); continue
        for t in (a, b):
            if not ctx.on_pause(t, sh):
                errs.append(f"boundary {fmt_ts(t)} is not on a gap-table pause")
            r = ctx.inside_line(t)
            if r:
                errs.append(f"boundary {fmt_ts(t)} falls inside {r['id']}")
    if abs(sum(b - a for a, b in segs) - c["final_duration"]) > 0.01:
        errs.append("final_duration does not match the segments")
    for lid in c["lines"]:
        if lid in ctx.non_story:
            errs.append(f"non-story line {lid} inside the candidate")
    return errs


def add_extras(ctx: Ctx, kept: list[dict], sheets: list[dict], cfg: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Thread candidates and critic_trimmed siblings, after the caps (they never take a slot). Ids continue each scene's
    letters. Returns (extras, notes, invalid)."""
    extras, notes, invalid = [], [], []
    th, ns = thread_candidates(ctx, sheets)
    extras += th; notes += ns
    if (ctx.s3.get("scene_variant") or {}).get("enabled", True):
        seen = {json.dumps([[round(float(a), 3), round(float(b), 3)] for a, b in c["segments"]]): c for c in kept}
        sv, ns = scene_variants(ctx, sheets)
        for c in sv:
            same = seen.get(json.dumps([[round(float(a), 3), round(float(b), 3)] for a, b in c["segments"]]))
            if same is not None:                                    # the same cut as a kept candidate: judged once, under that id
                notes.append({"scene_id": c["scene_id"], "why": f"scene variant {c['variant']} is the same cut as {same['id']} ({same['variant']})"})
                if c["variant"] == "scene":
                    for t in sv:
                        if t.get("_untrimmed") is c:
                            t["_untrimmed"] = same
                continue
            extras.append(c)
        notes += ns
    sits, ns = situation_variants(ctx, sheets)                  # founder, 2026-10-30
    seen = {json.dumps([[round(float(a), 3), round(float(b), 3)] for a, b in c["segments"]]): c for c in kept + extras}
    for c in sits:
        same = seen.get(json.dumps([[round(float(a), 3), round(float(b), 3)] for a, b in c["segments"]]))
        if same is not None:
            notes.append({"scene_id": c["scene_id"], "why": f"{c['variant']} ({(c.get('situation') or {}).get('index')}) is the same cut as {same.get('id') or same['variant']}"})
            continue
        extras.append(c)
    notes += ns
    for c in kept:                                              # tighter variants inside a situation: its alternates
        sh = next((x for x in sheets if x["scene_id"] == c["scene_id"]), {})
        for i, st in enumerate(sh.get("situations") or [], 1):
            if float(st.get("t", -1)) - 1.0 <= c["start"] and c["end"] <= float(st.get("end", -1)) + 3.0:
                c["situation"] = {"index": i, "what": st.get("what_happens") or "", "strength": st.get("strength")}
                break
    extras += channel_short_candidates(ctx, sheets)
    flagged = pace_flagged(ctx.facts)
    skip_loops = not loops_on(ctx.facts, cfg)
    for c in kept + list(extras):                               # threads and scene variants get critic trims too
        if c["variant"] in ("critic_trimmed", "channel_short", "teaser_open") or (skip_loops and c["type"] == "loop"):
            continue
        z = flagged.get(json.dumps([c["type"], c["variant"], [[round(float(a), 3), round(float(b), 3)] for a, b in c["segments"]]]))
        if z is None:
            continue
        t = critic_trim(ctx, c, z)
        if t is None:
            notes.append({"scene_id": c["scene_id"], "why": f"{c['id'] or 'thread'} {c['type']}/{c['variant']}: pace-flagged but no stretch could be cut"})
            continue
        t["_base"] = c
        extras.append(t)
    used: dict[int, int] = {}
    for c in kept:
        used[c["scene_id"]] = used.get(c["scene_id"], 0) + 1
    ok = []
    for c in sorted(extras, key=lambda c: (c["scene_id"], c["variant"] != "thread", c["variant"] == "critic_trimmed", c["start"], c["variant"])):
        errs = validate_extra(ctx, c, sheets)
        base = c.pop("_base", None)
        unt = c.pop("_untrimmed", None)
        if errs:
            invalid.append({"scene_id": c["scene_id"], "type": c["type"], "variant": c["variant"], "start_str": c["start_str"], "end_str": c["end_str"], "errors": errs})
            continue
        n = used.get(c["scene_id"], 0); used[c["scene_id"]] = n + 1
        c["id"] = f"C{c['scene_id']}{_letters(n)}"
        if base is not None:
            c["critic_trim_of"] = base["id"]
        if unt is not None and unt.get("id"):                      # scene_trimmed <-> scene (or the kept candidate with the same cut)
            c["untrimmed_sibling"] = unt["id"]
            unt["trimmed_sibling"] = c["id"]
        ok.append(c)
    for c in ok:
        c.setdefault("untrimmed_sibling", None); c.setdefault("trimmed_sibling", None); c.setdefault("end_sibling", None); c.setdefault("wider_sibling", None)
    return ok, notes, invalid


SHEET_OVERRIDE_DIR = Path(__file__).resolve().parents[1] / "data" / "sheet_overrides"


def sheet_overrides(video_id: str, path: Path | None = None) -> dict[int, dict]:
    """Manual beat-sheet corrections (data/sheet_overrides/<video_id>.yaml: scenes: {<scene_id>: {button_line: Lxxxx, note}}),
    applied by Stage 3 on top of the Stage 2 sheets (founder, 2026-10-30). Only ``button_line`` is honoured for now."""
    p = path or (SHEET_OVERRIDE_DIR / f"{video_id}.yaml")
    if not p.exists():
        return {}
    import yaml
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {int(k): dict(v or {}) for k, v in (d.get("scenes") or {}).items()}


def apply_sheet_overrides(sheets: list[dict], over: dict[int, dict]) -> tuple[list[dict], list[dict]]:
    out, applied = [], []
    for sh in sheets:
        o = over.get(int(sh["scene_id"]))
        if o and "button_line" in o:
            sh = dict(sh, button_line=o["button_line"])
            applied.append({"scene_id": sh["scene_id"], "button_line": o["button_line"], "note": o.get("note") or ""})
        out.append(sh)
    return out, applied


# ----------------------------------------------------------------------------------------------- run
def run(inputs, facts: dict | None, cfg: dict) -> dict:
    t0 = time.perf_counter()
    if not facts or not facts.get("beat_sheets"):
        raise Stage3Error("Stage 3 needs Stage 2 beat sheets in episode_facts.json; run Stage 2 first")
    ctx = Ctx(facts, cfg)
    scenes = (((facts.get("episode_pass") or {}).get("derived")) or {}).get("scenes") or []
    sheets, overrides_applied = apply_sheet_overrides(sorted(facts["beat_sheets"], key=lambda s: s["scene_id"]),
                                                      sheet_overrides(((facts.get("episode") or {}).get("video_id")) or ""))
    have = {s["scene_id"] for s in sheets}
    warnings = []
    missing = [sc["id"] for sc in scenes if sc["id"] not in have]
    if missing:
        warnings.append(f"no beat sheet for scene(s) {missing}: no candidates there until Stage 2 runs on them")
    all_c: list[dict] = []
    notes: list[dict] = []
    invalid: list[dict] = []
    for sh in sheets:
        cs, ns = generate_scene(ctx, sh)
        notes += ns
        beats = sheet_beats(ctx, sh)
        for c in cs:
            errs = validate_candidate(ctx, c, sh, beats)
            if errs:
                invalid.append({"scene_id": sh["scene_id"], "type": c["type"], "variant": c["variant"], "start_str": c["start_str"], "end_str": c["end_str"], "errors": errs})
                continue
            all_c.append(c)
    if invalid:
        warnings.append(f"{len(invalid)} candidate(s) failed validation and were not kept (see stages.3.invalid)")
    over = [c for c in all_c if c["flags"]["over_platform_max"]]
    kept_over = []
    for c in over:
        if c["trim_level"] == "none" and c["variant"] in ("full_arc", "full", "extended_untrimmed"):
            kept_over.append(c)                                 # always generated, flagged for Stage 4
        else:
            all_c.remove(c)
            notes.append({"scene_id": c["scene_id"], "why": f"{c['type']}/{c['variant']} {c['start_str']}-{c['end_str']}: final {c['final_duration']:.0f} s over the platform maximum {cfg['stage3']['platform_max_s']} s"})
    all_c, merged = dedupe(all_c)
    kept, dropped = apply_caps(all_c, cfg["stage3"])
    extras, ns, inv = add_extras(ctx, kept, sheets, cfg)        # founder, 2026-10-29: threads + critic_trimmed siblings, outside the caps
    notes += ns; invalid += inv
    if inv:
        warnings.append(f"{len(inv)} thread / critic-trimmed candidate(s) failed validation and were not kept (see stages.3.invalid)")
    kept = kept + extras
    for c in kept:
        se = schema_errors(c)
        if se:
            raise Stage3Error(f"candidate {c['id']} violates candidate.schema.json: {'; '.join(se[:4])}")
    counts = {"generated": len(all_c) + len(merged), "after_dedupe": len(all_c), "kept": len(kept), "slots": len({slot_key(c) for c in kept}), "dropped_by_caps": len(dropped),
              "by_type": {t: sum(1 for c in kept if c["type"] == t) for t in TYPE_ORDER},
              "by_scene": {str(s): sum(1 for c in kept if c["scene_id"] == s) for s in sorted({c["scene_id"] for c in kept})},
              "untrimmed_over_platform_max": len(kept_over), "scenes_with_sheets": len(sheets), "scenes_without_sheets": missing,
              "threads": sum(1 for c in kept if c["variant"] == "thread"), "critic_trimmed": sum(1 for c in kept if c["variant"] == "critic_trimmed"),
              "ending_on_button": sum(1 for c in kept if c.get("button_line")), "open_question": sum(1 for c in kept if c["variant"].startswith("open_question")),
              "scene_variants": sum(1 for c in kept if c["variant"] in ("scene", "scene_trimmed")),
              "situation_variants": sum(1 for c in kept if c["variant"] in ("situation", "situation_trimmed")),
              "teaser_open": sum(1 for c in kept if c["variant"] == "teaser_open"), "channel_shorts": sum(1 for c in kept if c["variant"] == "channel_short")}
    out = dict(facts)
    out["candidates"] = kept
    stages = dict(out.get("stages") or {})
    stages[STAGE_KEY] = {"version": VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "duration_s": round(time.perf_counter() - t0, 3),
                         "usage": {"input_tokens": 0, "output_tokens": 0, "cost_inr": 0.0}, "warnings": warnings, "counts": counts,
                         "dropped": dropped, "merged": merged, "not_generated": notes, "invalid": invalid, "config_sha8": cfg.get("_sha8"),
                         "sheet_overrides": overrides_applied}
    out["stages"] = stages
    return out
