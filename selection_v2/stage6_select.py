"""Stage 6, final selection (doc 9; founder rules of 2026-10-29).

Code turns the judged clips into a posting set; one text call writes the on-screen words.

Moments. A moment = scene + payoff line + kind (story / thread / loop; open-question end-variants and critic_trimmed
siblings belong to their moment): every judged clip (Stage 5 selection + archive, matched to today's candidates by
cut) is grouped by it. Shippable versions: PASS, BORDERLINE, and a FAIL whose reason class is pace (founder,
2026-10-29: shipped with the critic's note; a confusion FAIL is never shipped). Primary (founder, 2026-10-30): among the
shippable versions scoring within ``primary.score_window`` of the moment's best Stage 4 combined, the one closest to the
channel's typical Short length (``typical.typical_length``: median of its scripted-series channel Shorts, clamped to the
band; default 65 s), ties by combined then critic passes; fallback the best-scored version. Alternates are the other shippable versions,
listed when their length differs by more than ``alternate_min_diff_s`` from the primary and from each listed one.
A moment with no shippable version is dropped and reported.

Set. Moments are ranked by their BEST shippable version's Stage 4 combined score (founder, 2026-10-30; type weights and
the length rule are already in it). Set rules: no overlaps, at most ``rules.max_per_scene`` main moments per scene, never
a whole-scene version beside another moment of the same scene. Walking down the ranked list, a moment is taken when no rule
breaks (same scene, overlap, spread over the episode thirds, fan-clip cap, eligibility); then missing required types
(hook, emotional) and the people rules (series_pull, recurring cast) are backfilled with the best moment that
satisfies them, swapping out the lowest-ranked member that no satisfied rule depends on. Loops are bonus posts.

Words. One Gemini call (the cached Stage 2 prefix first, then the chosen moments only) writes hook_text, caption,
title, hashtags, context_line (fan clips) and judges Q9 vs the beat sheet's dynamic. Package: final/final.json,
final/rejection_report.md, final/review.html (self-contained), final/clips/ (Stage 5 renders reused).
"""
from __future__ import annotations

import base64
import csv
import html
import json
import math
import re
import shutil
import subprocess
import time
from pathlib import Path

import jsonschema

from shotvi.llm.base import ModelSpec

from . import critic_class as CC, people as PP, stage4_scoring as S4, stage5_critic as S5, textmatch as TM
from .acceptance import final_speaker
from .config import ROOT, inr_per_usd, loops_on
from .typical import typical_length  # noqa: F401  (re-exported: S6.typical_length)
from .faces import load_bank, norm as face_norm
from .paths import write_json
from .preview import clip_path, is_current, render as render_clip, time_map, to_clip_time
from .runlog import append_log
from .stage1_episode_pass import load_prompt
from .stage1_facts import fmt_ts

VERSION = "stage6/0.1"
STAGE_KEY = "6"
ROLE = "final_text"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "stage6_text.md"
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "final_text.schema.json"
TELUGU = re.compile(r"[ఀ-౿]")


class SelectionError(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------- inputs
def editor_mode(cfg: dict) -> bool:
    return str((cfg.get("stage5") or {}).get("mode", "critic")) == "editor"


def check_inputs(facts: dict | None, cfg: dict | None = None) -> dict:
    """The active Stage 4 run on the current Stage 3 output, and Stage 5 output made on that run (the editor's pick in
    stage5.mode editor, else the critic's verdicts)."""
    run4 = S5.check_inputs(facts)
    if cfg is not None and editor_mode(cfg):
        ed = (facts or {}).get("editor") or {}
        if not ed.get("posts"):
            raise SelectionError("Stage 6 needs the editor's pick; run Stage 5 (mode editor) first")
        if ed.get("run_key") != run4["run_key"]:
            raise SelectionError(f"the editor's pick was made on Stage 4 run {ed.get('run_key')}, the active run is {run4['run_key']}: run Stage 5 again")
        return run4
    cr = (facts or {}).get("critic") or {}
    if not cr.get("clips"):
        raise SelectionError("Stage 6 needs Stage 5 verdicts; run Stage 5 first")
    if cr.get("run_key") != run4["run_key"]:
        raise SelectionError(f"the Stage 5 verdicts were made on Stage 4 run {cr.get('run_key')}, the active run is {run4['run_key']}: "
                             "run Stage 5 (clips whose cut did not change are carried for free)")
    return run4


def moment_kind(ctype: str, variant: str = "") -> str:
    if variant == "thread":
        return "thread"
    return ctype if ctype in ("hook", "loop") else "story"


def strength(verdict: str, n_pass: int, n_runs: int) -> int:
    """3 = PASS with every run passing, 2 = PASS, 1 = BORDERLINE, 0 = FAIL (shippable only when its class is pace)."""
    if verdict == "PASS":
        return 3 if n_pass >= n_runs else 2
    return 1 if verdict == "BORDERLINE" else 0


def recurring_cast(facts: dict, cfg: dict) -> set[str]:
    """Normalised names of the series' recurring cast: the founder-verified face bank."""
    bank = load_bank((facts.get("episode") or {}).get("series_key"), cfg)
    return {face_norm(v.get("name") or k) for k, v in (bank.get("faces") or {}).items()}


def line_names(facts: dict) -> dict[str, str]:
    """Final speaker name per speech line (Stage 2 map > 1B > fix)."""
    return {r["id"]: (final_speaker(facts, r["id"])[1] or "") for r in facts["lines"] if r["kind"] == "speech"}


def kept_lines(facts: dict, c: dict) -> list[dict]:
    segs = [(float(a), float(b)) for a, b in c["segments"]]
    return [r for r in facts["lines"] if any(a - 1e-3 <= r["start"] and r["end"] <= b + 1e-3 for a, b in segs)]


# ----------------------------------------------------------------------------------------------- moments
def variant_entry(facts: dict, c: dict, z: dict, s4: dict | None, c6: dict) -> dict:
    s4 = s4 or {}
    runs = z.get("runs") or []
    status = s4.get("status")
    eligible = bool(c.get("eligible_for_final", True)) and bool(s4.get("eligible_for_final", True))
    why_not = None
    rclass = z.get("reason_class") or CC.reason_class(runs)
    pace_ok = z["verdict"] == "FAIL" and rclass == "pace" and bool(c6.get("pace_fail_ships", False))
    if z["verdict"] not in c6["ship_verdicts"] and not pace_ok:
        why_not = f"critic verdict {z['verdict']} ({z['n_pass']} of {z['n_runs']} runs passed; {rclass or 'no'} reason)"
    elif status not in c6["ship_stage4_status"]:
        why_not = f"Stage 4 status {status}"
    elif not eligible:
        why_not = "not eligible for posting (over the platform maximum)"
    return {"id": c["id"], "type": c["type"], "variant": c["variant"], "scene_id": c["scene_id"], "start": c["start"], "end": c["end"],
            "start_str": c["start_str"], "end_str": c["end_str"], "segments": c["segments"], "final_duration": float(c["final_duration"]),
            "payoff_line": c.get("payoff_line"), "verdict": z["verdict"], "n_pass": z["n_pass"], "n_runs": z["n_runs"],
            "strength": strength(z["verdict"], z["n_pass"], z["n_runs"]), "located_s": z.get("located_s") or [], "expected": z.get("expected") or {},
            "swipe": z.get("swipe") or [r["answers"].get("swipe") or [] for r in runs],
            "best": [r["answers"].get("best_moment") or {} for r in runs], "run_reasons": [r.get("reasons") or [] for r in runs],
            "q2": [r["answers"].get("first_3s") or {} for r in runs], "q8": [r["answers"].get("caption") for r in runs],
            "q9": [r["answers"].get("relationship") for r in runs], "missing": [r["answers"].get("confusion") or {} for r in runs],
            "needs_caption_context": bool((z.get("caption_context") or {}).get("needs_caption_context")),
            "stage4": {"status": status, "combined": s4.get("combined"), "scores": s4.get("scores") or {}, "fan_clip": bool(s4.get("fan_clip")) or status == "fan_clip_only",
                       "series_pull": (s4.get("scores") or {}).get("series_pull")},
            "eligible": eligible, "shippable": why_not is None, "not_shipped_why": why_not, "archived": bool(z.get("archived")),
            "reason_class": rclass, "critic_note": (("critic: pace FAIL, viewers swiped at " if z["verdict"] == "FAIL" else f"critic: {z['verdict']}, swipe at ")
                                                   + ", ".join(f"{a:.0f}-{b:.0f} s" for a, b in CC.pace_stretches(runs)))
                                                  if rclass == "pace" and z["verdict"] != "PASS" else None,
            "critic_trim_of": c.get("critic_trim_of"), "thread": c.get("thread"),
            "critic_cost_inr": (z.get("usage") or {}).get("paid_cost_inr", (z.get("usage") or {}).get("cost_inr"))}


def pick_primary(ship: list[dict], c6: dict, target_s: float) -> dict | None:
    """founder, 2026-10-30: among the shippable versions within ``primary.score_window`` of the best Stage 4 combined, the
    one closest to the typical length (ties: combined, then critic passes); fallback the best-scored version."""
    if not ship:
        return None
    best = max(ship, key=lambda v: ((v["stage4"]["combined"] or 0.0), v["n_pass"], -abs(v["final_duration"] - target_s)))
    window = float((c6.get("primary") or {}).get("score_window", 0.5))
    near = [v for v in ship if (v["stage4"]["combined"] or 0.0) >= (best["stage4"]["combined"] or 0.0) - window - 1e-9]
    if not near:
        return best
    return min(near, key=lambda v: (round(abs(v["final_duration"] - target_s), 3), -(v["stage4"]["combined"] or 0.0), -v["n_pass"], v["id"]))


def build_moments(facts: dict, cfg: dict) -> list[dict]:
    """Every judged clip grouped by moment, with primary / alternates / drop reason. Unranked."""
    c6 = cfg["stage6"]
    tl = typical_length(facts, cfg)
    run4 = S4.active_run(facts) or {}
    s4 = {s["id"]: s for s in run4.get("candidates") or []}
    cands = {c["id"]: c for c in facts["candidates"]}
    names = line_names(facts)
    recurring = recurring_cast(facts, cfg)
    sheets = {s["scene_id"]: s for s in facts.get("beat_sheets") or []}
    lid = {r["id"]: r for r in facts["lines"]}
    groups: dict[str, list[dict]] = {}
    for cid, z in S5.judged_records(facts).items():
        c = cands.get(cid)
        if c is None or c["variant"] == "channel_short":           # founder, 2026-10-30: reference only, never shipped
            continue
        v = variant_entry(facts, c, z, s4.get(cid), c6)
        groups.setdefault(f"{c['scene_id']}|{c.get('payoff_line')}|{moment_kind(c['type'], c['variant'])}", []).append(v)
    out = []
    for key, vs in groups.items():
        scene_id, payoff, kind = key.split("|")
        sheet = sheets.get(int(scene_id)) or {}
        ship = [v for v in vs if v["shippable"]]
        primary = pick_primary(ship, c6, tl["target_s"])
        alts: list[dict] = []
        if primary:
            for v in sorted((v for v in ship if v["id"] != primary["id"]), key=lambda v: (-v["strength"], v["final_duration"], v["id"])):
                if len(alts) >= int(c6.get("max_alternates", 3)):
                    break
                if all(abs(v["final_duration"] - x["final_duration"]) > float(c6.get("alternate_min_diff_s", 5)) for x in [primary] + alts):
                    alts.append(v)
        cast = sorted({names.get(r["id"]) for r in kept_lines(facts, cands[primary["id"]]) if names.get(r["id"])}) if primary else []
        out.append({"key": key, "scene_id": int(scene_id), "payoff_line": payoff, "payoff_text": (lid.get(payoff) or {}).get("text", ""),
                    "payoff_t": (lid.get(payoff) or {}).get("start"), "kind": kind, "label": sheet.get("label") or f"scene {scene_id}",
                    "setup": sheet.get("setup") or "", "dynamic": sheet.get("dynamic") or {},
                    "type": primary["type"] if primary else vs[0]["type"], "primary": primary, "alternates": alts,
                    "variants": sorted(vs, key=lambda v: v["final_duration"]),
                    "strength": primary["strength"] if primary else 0, "combined": (primary or {}).get("stage4", {}).get("combined") or 0.0,
                    "rank_score": round(max(((v["stage4"]["combined"] or 0.0) for v in ship), default=0.0), 3),   # best shippable version
                    "series_pull": (primary or {}).get("stage4", {}).get("series_pull"), "fan_clip": bool(primary and primary["stage4"]["fan_clip"]),
                    "start": primary["start"] if primary else min(v["start"] for v in vs), "end": primary["end"] if primary else max(v["end"] for v in vs),
                    "cast": cast, "recurring": any(face_norm(n) in recurring for n in cast), "typical_length": tl,
                    "in_band": bool(primary) and tl["band_s"][0] - 1e-6 <= primary["final_duration"] <= tl["band_s"][1] + 1e-6,
                    # founder, 2026-10-30: a version in the band counts only when it scores within the window of the moment's best
                    "band_available": any(tl["band_s"][0] - 1e-6 <= v["final_duration"] <= tl["band_s"][1] + 1e-6
                                          and (v["stage4"]["combined"] or 0.0) >= max(((x["stage4"]["combined"] or 0.0) for x in ship), default=0.0)
                                          - float((c6.get("primary") or {}).get("score_window", 0.5)) - 1e-9 for v in ship),
                    "whole_scene": bool(primary) and primary["variant"] in ("scene", "scene_trimmed")})
    return out


def rank_key(m: dict) -> tuple:
    """founder, 2026-10-30: the moment's best shippable version's Stage 4 combined (``rank_score``), then shorter."""
    score = m.get("rank_score", m.get("combined") or 0.0)
    return (-(score or 0.0), (m["primary"] or {}).get("final_duration", 1e9), m["key"])


# ----------------------------------------------------------------------------------------------- the set
def _bin(m: dict, episode_s: float, bins: int) -> int:
    width = max(1e-6, float(episode_s) / bins)
    return min(bins - 1, max(0, int(float(m["start"]) // width)))


def _overlaps(a: dict, b: dict) -> bool:
    return float(a["start"]) < float(b["end"]) - 1e-3 and float(b["start"]) < float(a["end"]) - 1e-3


def conflict(m: dict, members: list[dict], rules: dict, episode_s: float) -> dict | None:
    """The first set rule ``m`` breaks against ``members`` (main set), or None."""
    for o in members:
        if not rules.get("overlap", False) and _overlaps(o, m):
            return {"rule": "overlap", "other": o["key"]}
        if o["scene_id"] == m["scene_id"] and (m.get("whole_scene") or o.get("whole_scene")):
            return {"rule": "scene_with_situation", "other": o["key"]}     # founder, 2026-10-30: never a scene beside its own situations
    same = [o for o in members if o["scene_id"] == m["scene_id"]]
    cap = int(rules.get("max_per_scene", 0 if not rules.get("same_scene", True) else 99))
    if len(same) >= max(1, cap):
        return {"rule": "same_scene", "other": same[0]["key"], "cap": max(1, cap)}
    sp = rules.get("spread") or {}
    if sp:
        bins = int(sp.get("bins", 3))
        b = _bin(m, episode_s, bins)
        same = [o for o in members if _bin(o, episode_s, bins) == b]
        if len(same) >= int(sp.get("max_per_bin", 3)):
            return {"rule": "spread", "bin": b, "bins": bins, "others": [o["key"] for o in same]}
    if m["fan_clip"]:
        fans = [o for o in members if o["fan_clip"]]
        if len(fans) >= int(rules.get("fan_clip_max", 1)):
            return {"rule": "fan_cap", "other": fans[0]["key"]}
    return None


def requirements(pool: list[dict], rules: dict) -> list[dict]:
    """The rules a set must satisfy, in backfill order; a type requirement applies only when that type is shippable."""
    reqs = []
    for t in rules.get("require_types") or []:
        if any(m["type"] == t for m in pool):
            reqs.append({"name": f"at least one {t}", "pred": (lambda t: lambda m: m["type"] == t)(t), "min": (lambda n: 1)})
    sp = rules.get("series_pull") or {}
    if sp:
        ms, mn = float(sp.get("min_score", 7)), int(sp.get("min_moments", 2))
        reqs.append({"name": f"at least {mn} moments with series_pull >= {ms:g}", "pred": lambda m: (m["series_pull"] or 0) >= ms, "min": (lambda n: mn)})
    share = rules.get("recurring_cast_share")
    if share:
        reqs.append({"name": f"recurring cast in >= {float(share):.0%} of the set", "pred": lambda m: bool(m["recurring"]),
                     "min": (lambda n: math.ceil(float(share) * n))})
    return reqs


def _ok(req: dict, members: list[dict]) -> bool:
    return sum(1 for m in members if req["pred"](m)) >= req["min"](len(members))


def stage6_cfg(facts: dict, cfg: dict) -> dict:
    """``cfg["stage6"]`` for this episode: ``bonus_loops`` is 0 when ``loops_by_content_type`` turns loops off for the
    episode's content type (founder, 2026-10-29: off by default for scripted series); ``loops_off_for`` names it."""
    c6 = dict(cfg["stage6"])
    if not loops_on(facts, cfg):
        c6["bonus_loops"], c6["loops_off_for"] = 0, ((facts.get("episode_pass") or {}).get("content_type")) or "default"
    return c6


def select_set(moments: list[dict], c6: dict, episode_s: float) -> dict:
    """{main, bonus, ranked, why: {key: reason}, warnings, requirements: [{name, ok}]}."""
    rules = c6["rules"]
    slots, n_bonus = int(c6["main_slots"]), int(c6["bonus_loops"])
    ranked = sorted([m for m in moments if m["primary"]], key=rank_key)
    pool = [m for m in ranked if m["kind"] != "loop"]
    loops = [m for m in ranked if m["kind"] == "loop"]
    why: dict[str, dict] = {}
    chosen: list[dict] = []
    for i, m in enumerate(pool):
        if len(chosen) >= slots:
            why[m["key"]] = {"rule": "slots", "rank": i + 1, "of": len(pool)}
            continue
        cf = conflict(m, chosen, rules, episode_s)
        if cf:
            why[m["key"]] = cf
            continue
        chosen.append(m)
    warnings: list[str] = []
    reqs = requirements(pool, rules)
    for req in reqs:
        guard = 0
        while not _ok(req, chosen) and guard < 50:
            guard += 1
            placed = False
            for cand in (m for m in pool if m not in chosen and req["pred"](m)):
                if len(chosen) < slots and not conflict(cand, chosen, rules, episode_s):
                    chosen.append(cand); placed = True
                    break
                for v in sorted(chosen, key=rank_key, reverse=True):          # the lowest-ranked member first
                    if req["pred"](v):
                        continue
                    trial = [o for o in chosen if o is not v]
                    if conflict(cand, trial, rules, episode_s):
                        continue
                    after = trial + [cand]
                    if any(_ok(r, chosen) and not _ok(r, after) for r in reqs):
                        continue
                    chosen = after
                    why[v["key"]] = {"rule": "backfill", "swapped_for": cand["key"], "requirement": req["name"]}
                    placed = True
                    break
                if placed:
                    break
            if not placed:
                warnings.append(f"set rule not met: {req['name']} (no moment that satisfies it fits the other rules)")
                break
    for m in chosen:
        why.pop(m["key"], None)
    chosen = sorted(chosen, key=rank_key)
    bonus: list[dict] = []
    main_moments = {(m["scene_id"], m["payoff_line"]): m for m in chosen}
    for i, m in enumerate(loops):
        if c6.get("loops_off_for"):
            why[m["key"]] = {"rule": "loops_off", "content_type": c6["loops_off_for"]}
            continue
        if len(bonus) >= n_bonus:
            why[m["key"]] = {"rule": "bonus_slots", "rank": i + 1, "of": len(loops)}
            continue
        same = main_moments.get((m["scene_id"], m["payoff_line"]))
        if same and not (same["kind"] == "hook" and rules.get("bonus_loop_beside_main_hook", True)):
            # founder, 2026-10-29: a loop may share its payoff line with a main HOOK (the hook stops before the punchline,
            # the loop IS the punchline: different posts), never with a main comedy / emotional clip of that moment
            why[m["key"]] = {"rule": "same_moment_as_main", "other": same["key"]}
            continue
        cf = next(({"rule": "same_scene" if o["scene_id"] == m["scene_id"] else "overlap", "other": o["key"]} for o in bonus
                   if o["scene_id"] == m["scene_id"] or _overlaps(o, m)), None)
        if cf is None and not rules.get("bonus_may_overlap_main", True):
            cf = next(({"rule": "overlap", "other": o["key"]} for o in chosen if _overlaps(o, m)), None)
        if cf:
            why[m["key"]] = cf
            continue
        bonus.append(m)
    return {"main": chosen, "bonus": bonus, "ranked": ranked, "why": why, "warnings": warnings,
            "requirements": [{"name": r["name"], "ok": _ok(r, chosen)} for r in reqs]}


# ----------------------------------------------------------------------------------------------- crop hints
def crop_hint(facts: dict, c: dict, names: dict[str, str]) -> dict:
    """Per kept segment: who to follow (most talk time) and the speakers; plus the reaction holds (clip time)."""
    tmap = time_map(c["segments"])
    segs = []
    for row in tmap:
        a, b = row["ep_start"], row["ep_end"]
        talk: dict[str, float] = {}
        for r in facts["lines"]:
            if r["kind"] == "speech" and a - 1e-3 <= r["start"] and r["end"] <= b + 1e-3:
                n = names.get(r["id"]) or "?"
                talk[n] = talk.get(n, 0.0) + (r["end"] - r["start"])
        follow = max(talk, key=talk.get) if talk else None
        segs.append({"clip_start": round(row["clip_start"], 2), "clip_end": round(row["clip_end"], 2), "ep_start": round(a, 2), "ep_end": round(b, 2),
                     "follow": follow, "speakers": [{"name": k, "talk_s": round(v, 1)} for k, v in sorted(talk.items(), key=lambda kv: -kv[1])]})
    sheet = next((s for s in facts.get("beat_sheets") or [] if s["scene_id"] == c["scene_id"]), {})
    holds = [round(to_clip_time(tmap, float(nd["start"])), 2) for nd in sheet.get("non_dialogue") or []
             if nd.get("kind") == "reaction_hold" and nd.get("start") is not None and to_clip_time(tmap, float(nd["start"])) is not None]
    return {"segments": segs, "reaction_holds_clip_s": holds}


# ----------------------------------------------------------------------------------------------- the text call
def cta_target(m: dict, c6: dict) -> str:
    return "series" if (m["series_pull"] or 0) >= float(c6.get("cta_series_pull_min", 7)) else "episode"


def moment_people(facts: dict, m: dict) -> tuple[list[dict], str, str]:
    """(people in the primary clip, the scene's relationship line, the people line shown to the model and the creator)."""
    sheet = next((s for s in facts.get("beat_sheets") or [] if s["scene_id"] == m["scene_id"]), {})
    rel = ((sheet.get("dynamic") or {}).get("relationship")) or ""
    ppl = PP.clip_people(facts, m["primary"]["segments"], m["scene_id"])
    return ppl, rel, PP.people_line(ppl, rel)


def moment_block(facts: dict, m: dict, c6: dict) -> str:
    p = m["primary"]
    _, _, pline = moment_people(facts, m)
    c = next(x for x in facts["candidates"] if x["id"] == p["id"])
    if p.get("editor"):
        e = p["editor"]
        return (f"## MOMENT {m['id']} · {m['kind']} · primary {p['id']} {p['type']}/{p['variant']} · {p['final_duration']:.0f} s · "
                f"scene {m['scene_id']} ({m['label']}) · fan_clip {str(m['fan_clip']).lower()} · series_pull {m['series_pull']} · "
                f"cta_target {cta_target(m, c6)} · needs_caption_context {str(p['needs_caption_context']).lower()}\n"
                f"people: {pline}\nsetup: {m['setup']}\ndynamic: {json.dumps(m['dynamic'], ensure_ascii=False)}\n"
                f"editor's why: {e.get('why') or ''}\neditor's hook (first 3 s): seen: {(e.get('hook') or {}).get('seen', '')}; heard: {(e.get('hook') or {}).get('heard', '')}\n"
                f"editor: what a newcomer needs: {e.get('newcomer_needs') or 'nothing'}\n"
                "the clip as cut:\n" + S4.candidate_text(facts, c))
    q2 = "; ".join(f"{'understood' if x.get('understood') else 'NOT understood'}: {x.get('thought_it_was', '')}" for x in p["q2"]) or "none"
    q8 = "; ".join(x for x in p["q8"] if x) or "none"
    q9 = "; ".join(x for x in p["q9"] if x) or "none (asked from 2026-10-29 on)"
    return (f"## MOMENT {m['id']} · {m['kind']} · primary {p['id']} {p['type']}/{p['variant']} · {p['final_duration']:.0f} s · "
            f"scene {m['scene_id']} ({m['label']}) · fan_clip {str(m['fan_clip']).lower()} · series_pull {m['series_pull']} · "
            f"cta_target {cta_target(m, c6)} · needs_caption_context {str(p['needs_caption_context']).lower()}\n"
            f"people: {pline}\n"
            f"setup: {m['setup']}\ndynamic: {json.dumps(m['dynamic'], ensure_ascii=False)}\n"
            f"critics Q2 (first 3 s): {q2}\ncritics Q8 (caption ideas): {q8}\ncritics Q9 (who are they to each other): {q9}\n"
            "the clip as cut:\n" + S4.candidate_text(facts, c))


def text_prompt(facts: dict, moments: list[dict], c6: dict) -> str:
    return f"# CHOSEN MOMENTS ({len(moments)}): " + ", ".join(m["id"] for m in moments) + "\n\n" + "\n\n".join(moment_block(facts, m, c6) for m in moments)


def schema_errors(obj: dict) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message[:120]}" for e in jsonschema.Draft202012Validator(schema).iter_errors(obj)]


def hook_quotes_clip(hook: str, facts: dict, primary: dict) -> str | None:
    """founder, 2026-10-29: hook_text never quotes a line spoken inside the clip. Flags a kept line that shares >= 2
    content words (transliterated, fuzzy) with the hook AND makes up at least half of the hook's content words.
    Paraphrase cannot be caught in code: the prompt rule covers it."""
    h = TM.content_tokens(hook)
    if len(h) < 2:
        return None
    segs = [(float(a), float(b)) for a, b in primary["segments"]]
    for r in facts["lines"]:
        if r["kind"] != "speech" or not any(a - 1e-3 <= r["start"] and r["end"] <= b + 1e-3 for a, b in segs):
            continue
        lt = TM.content_tokens(r["text"])
        shared = [w for w in h if any(TM._tok_match(w, x) for x in lt)]
        if len(shared) >= 2 and len(shared) >= 0.5 * len(h):
            return f"{r['id']} \"{r['text']}\" (shared: {', '.join(shared)})"
    return None


def validate_text(ans: dict, moments: list[dict], c6: dict, facts: dict | None = None) -> list[str]:
    t = c6["text"]
    errs = []
    want = {m["id"]: m for m in moments}
    got = [e.get("moment_id") for e in ans.get("moments") or []]
    if sorted(got) != sorted(want):
        errs.append(f"moment ids must be exactly {sorted(want)}; got {got}")
    for e in ans.get("moments") or []:
        m = want.get(e.get("moment_id"))
        if not m:
            continue
        i = e["moment_id"]
        if len((e.get("hook_text") or "").split()) > int(t["hook_max_words"]):
            errs.append(f"{i}: hook_text has {len(e['hook_text'].split())} words, at most {t['hook_max_words']}")
        if not (e.get("hook_text") or "").strip():
            errs.append(f"{i}: hook_text is empty")
        if facts is not None and t.get("people_check", True):
            ppl, rel, _ = moment_people(facts, m)
            errs += [f"{i}: {x}" for x in PP.check_people_in_text(e.get("people_in_text") or [], ppl, PP.cast_name_set(facts), rel)]
            errs += [f"{i}: {x}" for x in PP.check_text_kin("hook_text", e.get("hook_text") or "", ppl, rel) + PP.check_text_kin("caption", e.get("caption") or "", ppl, rel)]
        if facts is not None and t.get("hook_no_quote", True):
            q = hook_quotes_clip(e.get("hook_text") or "", facts, m["primary"])
            if q:
                errs.append(f"{i}: hook_text quotes a line spoken inside the clip, {q}; frame the situation or ask the question instead")
        if len(e.get("caption") or "") > int(t["caption_max_chars"]):
            errs.append(f"{i}: caption has {len(e['caption'])} characters, at most {t['caption_max_chars']}")
        if len(e.get("title") or "") > int(t["title_max_chars"]):
            errs.append(f"{i}: title has {len(e['title'])} characters, at most {t['title_max_chars']}")
        tags = e.get("hashtags") or []
        if len(tags) != int(t["hashtags"]) or any(not str(h).startswith("#") or " " in str(h).strip() for h in tags):
            errs.append(f"{i}: hashtags must be exactly {t['hashtags']} strings starting with # and without spaces; got {tags}")
        if m["fan_clip"] and not (e.get("context_line") or "").strip():
            errs.append(f"{i}: a fan_clip moment needs a context_line")
        if not m["fan_clip"] and e.get("context_line"):
            errs.append(f"{i}: context_line must be null (not a fan_clip moment)")
        if e.get("cta_target") != cta_target(m, c6):
            errs.append(f"{i}: cta_target must be {cta_target(m, c6)!r} (series_pull {m['series_pull']})")
        if t.get("on_screen_latin_only", True):
            for f in ("hook_text", "caption", "title", "context_line"):
                if TELUGU.search(e.get(f) or ""):
                    errs.append(f"{i}: {f} must be Tenglish in Latin letters (no Telugu script)")
            if any(TELUGU.search(str(h)) for h in tags):
                errs.append(f"{i}: hashtags must be in Latin letters")
    return errs


def estimate(facts: dict, cfg: dict, chosen: list[dict] | None = None, run_dir: Path | None = None) -> dict:
    """Rupees for the one text call: the Stage 2 prefix + the chosen moments + the output. Headline without the
    prefix cache (Stage 4 calibration: local token counts x text_factor)."""
    from shotvi.segment.render import estimate_tokens
    r, rate, c6 = cfg["rates"], inr_per_usd(cfg), cfg["stage6"]
    tf = float((cfg["stage4"].get("calibration") or {}).get("text_factor", 1.0))
    if chosen is None:
        plan = plan_set(facts, cfg)
        chosen = plan["main"] + plan["bonus"]
    sections = load_prompt(PROMPT_PATH)
    n_pre = int(estimate_tokens(S4.prefix2_text(facts, cfg, run_dir)) * tf)
    n_txt = int(estimate_tokens(sections["system"] + sections["instructions"] + sections["output_format"] + text_prompt(facts, chosen, c6)) * tf)
    n_out = int(c6["text"]["expected_output_tokens_per_moment"]) * len(chosen)
    full = ((n_pre + n_txt) * r["usd_per_1m_input"] + n_out * r["usd_per_1m_output"]) / 1e6 * rate
    hit = ((n_pre * r["usd_per_1m_cached_input"] + n_txt * r["usd_per_1m_input"]) + n_out * r["usd_per_1m_output"]) / 1e6 * rate
    return {"n_moments": len(chosen), "prefix_tokens": n_pre, "prompt_tokens": n_txt, "output_tokens": n_out, "total_inr": round(full, 2),
            "total_inr_if_cache_hits": round(hit, 2), "note": f"one call; text tokens x {tf:.2f} (Stage 4 calibration); headline without the prefix cache"}


# ----------------------------------------------------------------------------------------------- plan (code only)
def plan_set(facts: dict, cfg: dict) -> dict:
    """The set and the rejections, code only (₹0): what the text call will be asked about."""
    if editor_mode(cfg):
        return editor_set(facts, cfg)
    check_inputs(facts)
    c6 = stage6_cfg(facts, cfg)
    moments = build_moments(facts, cfg)
    sel = select_set(moments, c6, float(facts["episode"]["duration"]))
    for i, m in enumerate(sel["main"]):
        m["id"] = f"M{i + 1}"
    for i, m in enumerate(sel["bonus"]):
        m["id"] = f"B{i + 1}"
    sel["moments"] = moments
    return sel


# ----------------------------------------------------------------------------------------------- editor mode
def editor_entry(facts: dict, c: dict, s4: dict | None, post: dict | None, verdict: str) -> dict:
    """A version entry shaped like ``variant_entry`` (so the package, report and text call work) for the editor's pick."""
    s4 = s4 or {}
    status = s4.get("status")
    e = {"id": c["id"], "type": c["type"], "variant": c["variant"], "scene_id": c["scene_id"], "start": c["start"], "end": c["end"],
         "start_str": c["start_str"], "end_str": c["end_str"], "segments": c["segments"], "final_duration": float(c["final_duration"]),
         "payoff_line": c.get("payoff_line"), "verdict": verdict, "n_pass": 0, "n_runs": 0, "strength": 0, "located_s": [], "expected": {},
         "swipe": [], "best": [], "run_reasons": [], "q2": [], "q8": [], "q9": [], "missing": [],
         "needs_caption_context": bool(((post or {}).get("newcomer_needs") or "").strip()),
         "stage4": {"status": status, "combined": s4.get("combined"), "scores": s4.get("scores") or {}, "fan_clip": bool(s4.get("fan_clip")) or status == "fan_clip_only",
                    "series_pull": (s4.get("scores") or {}).get("series_pull")},
         "eligible": True, "shippable": True, "not_shipped_why": None, "archived": False, "reason_class": None, "critic_note": None,
         "critic_trim_of": c.get("critic_trim_of"), "thread": c.get("thread"), "critic_cost_inr": 0.0}
    if post is not None:
        e["editor"] = {k: post.get(k) for k in ("why", "hook", "newcomer_needs", "fan_clip", "cut_fix", "source", "editor_rank")}
    return e


def editor_set(facts: dict, cfg: dict) -> dict:
    """founder, 2026-10-30: the posting set = the editor's posts (after the set rules), in the editor's order; each
    moment's other shortlisted version is its alternate. Rejected = the shortlisted moments the editor did not post."""
    check_inputs(facts, cfg)
    c6 = stage6_cfg(facts, cfg)
    ed = facts["editor"]
    run4 = S4.active_run(facts) or {}
    s4 = {s["id"]: s for s in run4.get("candidates") or []}
    cands = {c["id"]: c for c in facts["candidates"]}
    sheets = {s["scene_id"]: s for s in facts.get("beat_sheets") or []}
    lid = {r["id"]: r for r in facts["lines"]}
    names = line_names(facts)
    recurring = recurring_cast(facts, cfg)
    tl = ed.get("typical") or typical_length(facts, cfg)
    window = float((c6.get("primary") or {}).get("score_window", 0.5))
    from .stage5_editor import all_moments
    every = {m["key"]: m for m in all_moments(facts, cfg)}
    posted = {p["moment_key"]: p for p in ed["posts"]}

    def build(sm: dict) -> dict:
        post = posted.get(sm["key"])
        vids = [v["id"] for v in sm["versions"]]
        prim_id = post["candidate_id"] if post else vids[0]
        primary = editor_entry(facts, cands[prim_id], s4.get(prim_id), post, "editor pick" if post else "shortlisted")
        alts = [editor_entry(facts, cands[i], s4.get(i), None, "shortlisted") for i in vids if i != prim_id]
        sheet = sheets.get(sm["scene_id"]) or {}
        allv = (every.get(sm["key"]) or {}).get("all_versions") or []
        best = max((v["combined"] for v in allv), default=primary["stage4"]["combined"] or 0.0)
        cast = sorted({names.get(r["id"]) for r in kept_lines(facts, cands[prim_id]) if names.get(r["id"])})
        return {"key": sm["key"], "scene_id": sm["scene_id"], "payoff_line": sm["payoff_line"], "payoff_text": (lid.get(sm["payoff_line"]) or {}).get("text", ""),
                "payoff_t": (lid.get(sm["payoff_line"]) or {}).get("start"), "kind": sm["kind"], "label": sheet.get("label") or f"scene {sm['scene_id']}",
                "setup": sheet.get("setup") or "", "dynamic": sheet.get("dynamic") or {}, "type": primary["type"], "primary": primary, "alternates": alts,
                "variants": [primary] + alts, "strength": 0, "combined": best, "rank_score": best, "series_pull": primary["stage4"]["series_pull"],
                "fan_clip": bool(post.get("fan_clip")) if post else primary["stage4"]["fan_clip"], "start": primary["start"], "end": primary["end"],
                "cast": cast, "recurring": any(face_norm(n) in recurring for n in cast), "typical_length": tl,
                "in_band": tl["band_s"][0] - 1e-6 <= primary["final_duration"] <= tl["band_s"][1] + 1e-6,
                "band_available": any(tl["band_s"][0] - 1e-6 <= v["final_duration"] <= tl["band_s"][1] + 1e-6 and v["combined"] >= best - window - 1e-9 for v in allv),
                "whole_scene": primary["variant"] in ("scene", "scene_trimmed"), "editor_moment_id": sm["moment_id"]}

    shortlist_ms = [m for m in ed["shortlist"]["moments"] if not m["channel_short"]]
    moments = [build(m) for m in shortlist_ms]
    by_key = {m["key"]: m for m in moments}
    main = [by_key[p["moment_key"]] for p in ed["posts"] if p["moment_key"] in by_key]
    for i, m in enumerate(main):
        m["id"] = f"M{i + 1}"
    alt_keys = {a["moment_key"]: a for a in ed.get("unused_alternates") or []}
    repl = {x["moment_id"]: x for x in ed.get("replaced") or []}
    why = {}
    for m in moments:
        if m in main:
            continue
        if m["editor_moment_id"] in repl:
            why[m["key"]] = {"rule": "editor_replaced", "detail": repl[m["editor_moment_id"]]["rule"]}
        elif m["key"] in alt_keys:
            why[m["key"]] = {"rule": "editor_alternate", "detail": alt_keys[m["key"]].get("why", "")}
        else:
            why[m["key"]] = {"rule": "editor_not_picked"}
    return {"main": main, "bonus": [], "ranked": main, "why": why, "warnings": list((facts.get("stages", {}).get("5") or {}).get("warnings") or []),
            "requirements": [], "moments": moments}


# ----------------------------------------------------------------------------------------------- report
def _mmss(t) -> str:
    if t is None:
        return "?"
    t = max(0.0, float(t))
    return f"{int(t // 60)}:{int(t % 60):02d}"


def _quote(s: str, n: int = 90) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def variant_story(v: dict) -> list[str]:
    """Plain-language bullets on why viewers did not take a variant: where they found the best moment versus the
    payoff the cut was built on, where they would swipe, what confused them."""
    out = []
    exp = v.get("expected") or {}
    locs = ", ".join(_mmss(t) for t in v["located_s"])
    picks = "; ".join(f"\"{_quote(b.get('what', ''), 70)}\"" for b in v["best"][:3] if b)
    out.append(f"Best moment for the three viewers: {locs} ({picks}). The payoff this cut was built on is at "
               f"{_mmss(exp.get('clip_start'))} (\"{_quote(exp.get('text', ''), 60)}\").")
    sw = [f"{_mmss(s['from_s'])}-{_mmss(s['to_s'])} (\"{_quote(s.get('why', ''), 70)}\")" for runs in v["swipe"] for s in runs
          if float(s["to_s"]) - float(s["from_s"]) > 0]
    if sw:
        out.append("Where they would swipe away: " + "; ".join(sw[:4]) + ("" if len(sw) <= 4 else f"; and {len(sw) - 4} more"))
    miss = [f"{x.get('missing')}: \"{_quote(x.get('what', ''), 70)}\"" for x in v["missing"] if x.get("missing") not in (None, "none")]
    if miss:
        out.append("Missing context: " + "; ".join(miss[:3]))
    return out


def reason_text(why: dict, by_key: dict[str, dict], slots: int, n_bonus: int) -> str:
    rule = why.get("rule")
    o = by_key.get(why.get("other") or why.get("swapped_for") or "")
    ol = f"{o.get('id', '')} {o['label']} ({o['primary']['id']}, {o['primary']['final_duration']:.0f} s)" if o and o.get("primary") else (why.get("other") or "")
    if rule == "slots":
        return f"ranked #{why['rank']} of {why['of']}; the {slots} main slots went to higher-ranked moments"
    if rule == "editor_replaced":
        return f"the editor picked it, but it breaks a set rule ({why.get('detail')}), replaced by the next alternate"
    if rule == "editor_alternate":
        return f"an editor's alternate: {why.get('detail')}"
    if rule == "editor_not_picked":
        return "shortlisted; the editor did not pick it"
    if rule == "scene_with_situation":
        return f"a whole-scene version never goes beside another moment of its scene: {ol}"
    if rule == "same_scene" and why.get("cap", 1) > 1:
        return f"the set already has {why['cap']} moments from this scene (at most {why['cap']}): {ol}"
    if rule == "same_scene":
        return f"another moment from the same scene ranked higher: {ol}"
    if rule == "overlap":
        return f"its range overlaps a moment already in the set: {ol}"
    if rule == "spread":
        return f"the set already has {len(why.get('others') or [])} moments from the same third of the episode (part {why['bin'] + 1} of {why['bins']})"
    if rule == "fan_cap":
        return f"the one fan-clip place went to {ol}"
    if rule == "backfill":
        return f"swapped out to make room for {ol}, needed for the rule \"{why['requirement']}\""
    if rule == "bonus_slots":
        return f"loop ranked #{why['rank']} of {why['of']}; the {n_bonus} bonus places went to higher-ranked loops"
    if rule == "loops_off":
        return f"loops are off for {why.get('content_type')} episodes (stage6.loops_by_content_type)"
    if rule == "same_moment_as_main":
        return f"the same moment is already a main post: {ol}"
    return str(why)


def rejection_report(sel: dict, c6: dict, episode_title: str) -> str:
    by_key = {m["key"]: m for m in sel["moments"]}
    chosen = {m["key"] for m in sel["main"] + sel["bonus"]}
    L = [f"# Rejection report: {episode_title}", "",
         "Every judged moment that is not in the posting set, with the reason, and every version of a chosen moment that failed "
         "the cold-viewer test. Times are in clip time (m:ss from the start of that clip).", ""]
    shippable_out = [m for m in sel["ranked"] if m["key"] not in chosen]
    dropped = [m for m in sel["moments"] if not m["primary"]]
    L += ["## Good moments not in the set", ""]
    if not shippable_out:
        L += ["None.", ""]
    for m in shippable_out:
        p = m["primary"]
        L.append(f"### Scene {m['scene_id']} · {m['label']} · \"{_quote(m['payoff_text'], 70)}\" ({m['kind']})")
        L.append(f"- Best version {p['id']} ({p['type']}/{p['variant']}, {p['final_duration']:.0f} s): {p['verdict']} {p['n_pass']}/{p['n_runs']}, Stage 4 {p['stage4']['combined']}.")
        L.append(f"- Not in the set: {reason_text(sel['why'].get(m['key'], {}), by_key, int(c6['main_slots']), int(c6['bonus_loops']))}.")
        L.append("")
    L += ["## Moments with no version that works", ""]
    if not dropped:
        L += ["None.", ""]
    for m in sorted(dropped, key=lambda m: (m["scene_id"], m["payoff_line"])):
        L.append(f"### Scene {m['scene_id']} · {m['label']} · \"{_quote(m['payoff_text'], 70)}\" ({m['kind']})")
        for v in m["variants"]:
            L.append(f"- {v['id']} ({v['type']}/{v['variant']}, {v['final_duration']:.0f} s): not shipped, {v['not_shipped_why']}.")
            if v["verdict"] == "FAIL" or v["strength"] == 0:
                L += [f"  - {x}" for x in variant_story(v)]
        L.append("")
    L += ["## Versions of chosen moments that failed (not shipped)", ""]
    any_fail = False
    for m in sel["main"] + sel["bonus"]:
        for v in m["variants"]:
            if v["verdict"] == "FAIL" and not v["shippable"]:
                any_fail = True
                L.append(f"### {m['id']} · scene {m['scene_id']} · {m['label']}: {v['id']} ({v['type']}/{v['variant']}, {v['final_duration']:.0f} s), FAIL {v['n_pass']}/{v['n_runs']}")
                L += [f"- {x}" for x in variant_story(v)]
                L.append("")
    if not any_fail:
        L += ["None.", ""]
    return "\n".join(L).rstrip() + "\n"


def channel_short_reference(facts: dict) -> list[dict]:
    """founder, 2026-10-30: where the episode's own channel Shorts (reference candidates, never shipped) landed: their
    Stage 4 combined and rank among all scored candidates, and their Stage 5 verdict."""
    run4 = S4.active_run(facts) or {}
    scored = sorted((s for s in run4.get("candidates") or [] if s.get("combined") is not None), key=lambda s: -s["combined"])
    rank = {s["id"]: i + 1 for i, s in enumerate(scored)}
    s4 = {s["id"]: s for s in scored}
    judged = S5.judged_records(facts)
    out = []
    for c in facts.get("candidates") or []:
        if c["variant"] != "channel_short":
            continue
        z, s = judged.get(c["id"]), s4.get(c["id"])
        out.append({"id": c["id"], **(c.get("channel_short") or {}), "final_duration": c["final_duration"], "scene_id": c["scene_id"],
                    "stage4_combined": (s or {}).get("combined"), "stage4_status": (s or {}).get("status"), "stage4_rank": rank.get(c["id"]), "of": len(scored),
                    "verdict": (z or {}).get("verdict"), "n_pass": (z or {}).get("n_pass"), "reason_class": (z or {}).get("reason_class")})
    return out


# ----------------------------------------------------------------------------------------------- creator's picks
def _slim(v: dict) -> dict:
    keep = ("id", "type", "variant", "final_duration", "segments", "start_str", "end_str", "verdict", "n_pass", "n_runs", "located_s", "stage4",
            "expected", "swipe", "best", "run_reasons", "missing", "not_shipped_why", "reason_class", "critic_note", "critic_trim_of")
    return {k: v[k] for k in keep if k in v}


def creator_picks(sel: dict, c6: dict) -> list[dict]:
    """founder, 2026-10-29: the top N moments NOT in the set by Stage 4 combined (their best-scoring version), with the
    reason they are out, the critics' reasons and swipe stretches, marked promote = false so a creator can override.
    A loop left out only because its moment is already posted is not a pick (it is the same post), nor is any loop where
    loops are off for the content type."""
    chosen = {m["key"] for m in sel["main"] + sel["bonus"]}
    by_key = {m["key"]: m for m in sel["moments"]}
    picks = []
    for m in sel["moments"]:
        if m["key"] in chosen:
            continue
        why = sel["why"].get(m["key"])
        if why and why.get("rule") in ("same_moment_as_main", "loops_off"):
            continue                                           # the same post, or a loop where loops are off (founder, 2026-10-29)
        best = m["primary"] or max(m["variants"], key=lambda v: (v["stage4"]["combined"] or 0.0, -v["final_duration"]))
        picks.append({"key": m["key"], "scene_id": m["scene_id"], "label": m["label"], "kind": m["kind"], "payoff_line": m["payoff_line"],
                      "payoff_text": m["payoff_text"], "combined": best["stage4"]["combined"] or 0.0, "variant": _slim(best),
                      "reason": reason_text(why, by_key, int(c6["main_slots"]), int(c6["bonus_loops"])) if why else "no version passed the cold-viewer test",
                      "critic_story": ([f"Editor: {why.get('detail')}"] if why and why.get("rule") == "editor_alternate" else []) if best.get("verdict") in ("editor pick", "shortlisted")
                                      else variant_story(best), "promote": False})
    picks.sort(key=lambda p: (-p["combined"], p["key"]))
    return picks[: int(c6.get("creator_picks", 3))]


# ----------------------------------------------------------------------------------------------- package
def shrink(src: Path, dst: Path, height: int, crf: int) -> Path:
    """A small re-encode for embedding in review.html (cached while newer than the source)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size > 0 and dst.stat().st_mtime >= Path(src).stat().st_mtime:
        return dst
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vf", f"scale=-2:'min({int(height)},ih)'", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", str(int(crf)), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed shrinking {src}:\n{r.stderr[-1200:]}")
    return dst


def _card_html(m: dict, previews: dict[str, str], embed_alts: bool) -> str:
    p = m["primary"]
    esc = html.escape

    def vid(i: str) -> str:
        src = previews.get(i)
        return f'<video controls preload="metadata" src="{src}"></video>' if src else '<div class="novid">no preview</div>'

    alts = "".join(f'<div class="alt"><div class="meta">{esc(a["id"])} · {esc(a["type"])}/{esc(a["variant"])} · {a["final_duration"]:.0f} s · '
                   f'<b>{esc(a["verdict"])}</b> {a["n_pass"]}/{a["n_runs"]}</div>{vid(a["id"]) if embed_alts else ""}</div>' for a in m["alternates"])
    tags = " ".join(esc(h) for h in m.get("hashtags") or [])
    ctx = f'<p class="ctx">Context line: {esc(m["context_line"])}</p>' if m.get("context_line") else ""
    flag = '<span class="pill warn">needs caption context</span>' if m.get("needs_caption_context") else ""
    fan = '<span class="pill">fan clip</span>' if m["fan_clip"] else ""
    if p.get("critic_note"):
        flag += f'<span class="pill warn">{esc(p["critic_note"])}</span>'
    return (f'<article class="card"><header><span class="mid">{esc(m["id"])}</span> <span class="pill">{esc(m["kind"])}</span> '
            f'<span class="pill">{esc(p["type"])}</span>{fan}{flag}</header>'
            f'<h2>{esc(m.get("title") or m["label"])}</h2>{vid(p["id"])}'
            f'<p class="hook">{esc(m.get("hook_text") or "")}</p><p class="dyn">{esc(m.get("people_line") or "")}</p>'
            f'<p>{esc(m.get("caption") or "")}</p><p class="tags">{tags}</p>{ctx}'
            f'<p class="meta">Primary {esc(p["id"])} · {esc(p["variant"])} · {p["final_duration"]:.0f} s · scene {m["scene_id"]} ({esc(m["label"])}) · '
            f'critic <b>{esc(p["verdict"])}</b> {p["n_pass"]}/{p["n_runs"]} · Stage 4 {p["stage4"]["combined"]} · series_pull {m["series_pull"]} · CTA {esc(m.get("cta_target") or "")}</p>'
            + (f'<details><summary>{len(m["alternates"])} alternate(s)</summary>{alts}</details>' if m["alternates"] else "") + "</article>")


PICKS_JS = """(function(){var KEY='promoted:'+document.title;var st={};try{st=JSON.parse(localStorage.getItem(KEY)||'{}')||{}}catch(e){st={}}
function paint(b){var on=!!st[b.dataset.id];b.classList.toggle('on',on);b.textContent=on?'Promoted':'Promote';b.setAttribute('aria-pressed',on)}
document.querySelectorAll('button.promote').forEach(function(b){paint(b);b.addEventListener('click',function(){st[b.dataset.id]=!st[b.dataset.id];
try{localStorage.setItem(KEY,JSON.stringify(st))}catch(e){}paint(b)})});
var c=document.getElementById('copy-picks');if(c){c.addEventListener('click',function(){var ids=Object.keys(st).filter(function(k){return st[k]});
var txt=JSON.stringify({promote:ids});var s=document.getElementById('copy-status');
try{navigator.clipboard.writeText(txt).then(function(){s.textContent='copied: '+txt},function(){s.textContent=txt})}catch(e){s.textContent=txt}})}})();"""


def _pick_html(pk: dict, previews: dict[str, str]) -> str:
    esc = html.escape
    v = pk["variant"]
    src = previews.get(v["id"])
    vid = f'<video controls preload="metadata" src="{src}"></video>' if src else '<div class="novid">no preview</div>'
    bullets = "".join(f"<li>{esc(x)}</li>" for x in pk.get("critic_story") or [])
    return (f'<article class="card pick"><header><span class="pill">{esc(pk["kind"])}</span><span class="pill">{esc(v["type"])}</span>'
            f'<button class="promote" type="button" data-id="{esc(v["id"])}" aria-pressed="false">Promote</button></header>'
            f'<h2>Scene {pk["scene_id"]} · {esc(pk["label"])}</h2>{vid}'
            f'<p class="meta">{esc(v["id"])} · {esc(v["variant"])} · {v["final_duration"]:.0f} s · critic <b>{esc(v["verdict"])}</b> {v["n_pass"]}/{v["n_runs"]} · '
            f'Stage 4 {pk["combined"]}</p><p><b>Out because:</b> {esc(pk["reason"])}</p><ul class="meta">{bullets}</ul></article>')


def review_html(final: dict, previews: dict[str, str], report_md: str, embed_alts: bool = True) -> str:
    esc = html.escape
    css = """:root{--bg:#fbfaf8;--fg:#1d1d1f;--muted:#5f6368;--card:#ffffff;--line:#e3e1dc;--accent:#8a4b08;--warn:#a1260d}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#151514;--fg:#ecebe8;--muted:#a6a4a0;--card:#1f1f1d;--line:#34332f;--accent:#e0a35a;--warn:#ff8a70}}
:root[data-theme="dark"]{--bg:#151514;--fg:#ecebe8;--muted:#a6a4a0;--card:#1f1f1d;--line:#34332f;--accent:#e0a35a;--warn:#ff8a70}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:24px 16px}
main{max-width:1100px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:8px 0}.sub{color:var(--muted);margin:0 0 20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
video{width:100%;max-height:420px;background:#000;border-radius:6px}.hook{font-weight:700;font-size:17px;color:var(--accent);margin:10px 0 4px}
.meta,.tags{color:var(--muted);font-size:13px}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:0 8px;font-size:12px;margin-right:4px}
.pill.warn{color:var(--warn);border-color:var(--warn)}.mid{font-weight:700}.alt{margin-top:10px}.ctx{font-style:italic}.novid{color:var(--muted);font-size:13px}
pre.report{white-space:pre-wrap;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;font:13px/1.5 ui-monospace,Menlo,monospace}
section{margin-top:28px}button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;padding:4px 10px;cursor:pointer}
.dyn{font-size:13px;color:var(--muted);border-left:3px solid var(--accent);padding-left:8px;margin:2px 0 8px}button.promote{float:right}button.promote.on{background:var(--accent);color:var(--bg);border-color:var(--accent)}"""
    picks = "".join(_pick_html(pk, previews) for pk in final.get("creator_picks") or [])
    main = "".join(_card_html(m, previews, embed_alts) for m in final["main"])
    bonus = "".join(_card_html(m, previews, embed_alts) for m in final["bonus"])
    warn = "".join(f"<li>{esc(w)}</li>" for w in final.get("warnings") or [])
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(final["episode"]["title"] or final["episode"]["video_id"])} posting set</title><style>{css}</style></head><body><main>'
            f'<h1>{esc(final["episode"]["title"] or final["episode"]["video_id"])}: posting set</h1>'
            f'<p class="sub">{len(final["main"])} main posts and {len(final["bonus"])} bonus loops, chosen {esc(final["generated_at"])}. '
            f'Primary = the shortest version three cold viewers passed; alternates listed under each card.</p>'
            + (f"<ul>{warn}</ul>" if warn else "") +
            f'<section><h2>Main posts</h2><div class="grid">{main}</div></section>'
            f'<section><h2>Bonus loops</h2><div class="grid">{bonus or "<p>None.</p>"}</div></section>'
            f'<section><h2>Creator\'s picks</h2><p class="sub">The three best moments by Stage 4 score that are not in the set, with why. '
            f'Mark one "Promote" to override the selection; "Copy choices" puts your promoted ids on the clipboard.</p>'
            f'<div class="grid">{picks or "<p>None.</p>"}</div><p><button id="copy-picks" type="button">Copy choices</button> <span id="copy-status" class="meta"></span></p></section>'
            f'<section><h2>Rejection report</h2><pre class="report">{esc(report_md)}</pre></section></main><script>{PICKS_JS}</script></body></html>')


def transcripts_md(final: dict, facts: dict) -> str:
    """One file with the transcript of every selected clip (main posts, then bonus loops) AS CUT: clip time, episode
    time, speaker, line; a marker wherever a cut removes something. Lines in the original script."""
    names = line_names(facts)

    def mmss(t: float) -> str:
        return f"{int(t // 60)}:{t % 60:04.1f}"

    L = [f"# Transcripts of the selected clips: {final['episode'].get('title') or final['episode']['video_id']}", "",
         "Each clip as it plays: clip time (from the start of the clip), episode time, speaker, line. "
         "A [cut] marker shows where the edit removes part of the episode.", ""]
    for grp, title in (("main", "Main posts"), ("bonus", "Bonus loops")):
        L += [f"## {title}", ""]
        for m in final.get(grp) or []:
            p = m["primary"]
            L.append(f"### {m['id']} · {p['id']} · {p['type']}/{p['variant']} · {p['final_duration']:.0f} s · scene {m['scene_id']} ({m.get('label', '')})")
            L.append(f"Hook text: {m.get('hook_text', '')}  ")
            if m.get("people_line"):
                L.append(f"People: {m['people_line']}  ")
            L.append(f"Critic: {p['verdict']} {p['n_pass']}/{p['n_runs']} · Stage 4 {p['stage4'].get('combined')}"
                     + (f" · {p['critic_note']}" if p.get("critic_note") else ""))
            L.append("")
            L.append("| clip | episode | speaker | line |")
            L.append("|---|---|---|---|")
            tmap = time_map(p["segments"])
            for i, row in enumerate(tmap):
                if i > 0:
                    gone = row["ep_start"] - tmap[i - 1]["ep_end"]
                    L.append(f"| | | | *[cut: {gone:.1f} s removed, {mmss(tmap[i - 1]['ep_end'])} to {mmss(row['ep_start'])}]* |")
                for r in facts["lines"]:
                    if row["ep_start"] - 1e-3 <= r["start"] and r["end"] <= row["ep_end"] + 1e-3:
                        who = "(sound)" if r["kind"] == "event" else (names.get(r["id"]) or "?")
                        text = (r["text"] or "").replace("|", "\\|")
                        L.append(f"| {mmss(row['clip_start'] + r['start'] - row['ep_start'])} | {mmss(r['start'])} | {who} | {text} |")
            L.append("")
    return "\n".join(L).rstrip() + "\n"


def write_package(run_dir: Path, src_mp4: Path, final: dict, facts: dict, cfg: dict, report_md: str, renderer=None, shrinker=None, log=None) -> dict:
    """final/final.json, final/rejection_report.md, final/review.html, final/clips/<id>.mp4 (Stage 5 renders reused,
    any alternate not yet rendered is rendered now), final/previews/<id>.mp4 (small, embedded in review.html)."""
    log = log or (lambda *a: None)
    c6 = cfg["stage6"]
    renderer = renderer or render_clip
    shrinker = shrinker or shrink
    out = Path(run_dir) / "final"
    (out / "clips").mkdir(parents=True, exist_ok=True)
    cands = {c["id"]: c for c in facts["candidates"]}
    height = int(cfg["stage5"].get("clip_max_height", 480))
    previews: dict[str, str] = {}
    files = {}
    rendered_now = []
    for m in final["main"] + final["bonus"]:
        for v in [m["primary"]] + m["alternates"]:
            c = cands[v["id"]]
            if is_current(Path(run_dir), c):
                path = clip_path(Path(run_dir), c["id"])
            else:
                log(f"rendering {c['id']} (not rendered for its current cut yet)")
                path = renderer(Path(src_mp4), Path(run_dir), c, height)
                rendered_now.append(c["id"])
            dst = out / "clips" / f"{c['id']}.mp4"
            shutil.copyfile(path, dst)
            v["file"] = f"clips/{c['id']}.mp4"
            files[c["id"]] = str(dst)
            if v is m["primary"] or c6["review"].get("embed_alternates", True):
                small = shrinker(Path(path), out / "previews" / f"{c['id']}.mp4", int(c6["review"]["embed_height"]), int(c6["review"]["embed_crf"]))
                previews[c["id"]] = "data:video/mp4;base64," + base64.b64encode(Path(small).read_bytes()).decode("ascii")
    for pk in final.get("creator_picks") or []:
        v = pk["variant"]
        c = cands.get(v["id"])
        if c is None:
            continue
        if is_current(Path(run_dir), c):
            path = clip_path(Path(run_dir), c["id"])
        else:
            log(f"rendering creator's pick {c['id']}")
            path = renderer(Path(src_mp4), Path(run_dir), c, height)
            rendered_now.append(c["id"])
        dst = out / "clips" / f"{c['id']}.mp4"
        shutil.copyfile(path, dst)
        v["file"] = f"clips/{c['id']}.mp4"
        files[c["id"]] = str(dst)
        small = shrinker(Path(path), out / "previews" / f"{c['id']}.mp4", int(c6["review"]["embed_height"]), int(c6["review"]["embed_crf"]))
        previews[c["id"]] = "data:video/mp4;base64," + base64.b64encode(Path(small).read_bytes()).decode("ascii")
    (out / "rejection_report.md").write_text(report_md, encoding="utf-8")
    (out / "transcripts.md").write_text(transcripts_md(final, facts), encoding="utf-8")     # one file, every selected clip as cut
    write_json(out / "final.json", final)
    page = review_html(final, previews, report_md, bool(c6["review"].get("embed_alternates", True)))
    (out / "review.html").write_text(page, encoding="utf-8")
    return {"dir": str(out), "final_json": str(out / "final.json"), "report": str(out / "rejection_report.md"), "review_html": str(out / "review.html"),
            "transcripts": str(out / "transcripts.md"),
            "review_html_mb": round(len(page.encode("utf-8")) / 1e6, 1), "clips": files, "rendered_now": rendered_now}


# ----------------------------------------------------------------------------------------------- spend
def episode_spend(cfg: dict, vid: str) -> dict:
    """Rupees actually billed for this episode, per stage, from the runs log (stage-level rows, ledger delta)."""
    path = ROOT / cfg["paths"]["runs_log"]
    by: dict[str, float] = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                stg = row.get("stage") or ""
                # per-item child rows (4:scene3, 5:C10f, 6:text) are inside their stage row; a substage (6:words) is its own row
                if row.get("episode") != vid or re.match(r"^\w+:(scene\d+|C\d+[a-z]*|text)$", stg):
                    continue
                try:
                    d = float(row.get("ledger_delta_inr") or 0.0)
                except ValueError:
                    d = 0.0
                key = stg.split(":")[0]
                by[key] = round(by.get(key, 0.0) + d, 2)
    return {"by_stage": by, "total_inr": round(sum(by.values()), 2)}


# ----------------------------------------------------------------------------------------------- run
def _moment_out(m: dict) -> dict:
    keep = ("id", "key", "kind", "type", "scene_id", "label", "payoff_line", "payoff_text", "setup", "dynamic", "strength", "combined",
            "series_pull", "fan_clip", "cast", "recurring", "start", "end", "primary", "alternates", "hook_text", "caption", "title", "hashtags",
            "context_line", "cta_target", "q9_vs_dynamic", "q9_note", "needs_caption_context", "crop_hint", "people_line", "people_in_text",
            "typical_length", "in_band", "band_available", "whole_scene")
    return {k: m[k] for k in keep if k in m}


def run(inputs, facts: dict | None, cfg: dict, client=None, log=None, renderer=None, shrinker=None) -> dict:
    t0 = time.perf_counter()
    log = log or (lambda *a: None)
    check_inputs(facts, cfg)
    assert facts is not None
    c6 = stage6_cfg(facts, cfg)
    spec = ModelSpec.from_dict(cfg["models"].get("stage6") or cfg["models"]["default"])
    run_dir = Path(inputs.paths.run_dir)
    sel = plan_set(facts, cfg)
    chosen = sel["main"] + sel["bonus"]
    if not chosen:
        raise SelectionError("no moment has a PASS or BORDERLINE version: nothing to post (see the critic results)")
    sections = load_prompt(PROMPT_PATH)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    prefix2 = S4.prefix2_text(facts, cfg, run_dir)
    if client is None:
        from shotvi.llm.client import Ledger, LLMClient, get_provider
        client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"), max_retries=int(c6["text"].get("http_retries", 1)))
        try:
            get_provider(spec.provider).timeout = float(c6["text"].get("http_timeout_s", 300))
        except Exception:
            pass
    body = text_prompt(facts, chosen, c6)
    attempts, errors, ans = [], [], None
    usage = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0}
    for attempt in range(int(c6["text"].get("retries", 1)) + 1):
        prompt = (body + "\n\n" + sections["instructions"] + ("\n\n" + sections["editor"] if editor_mode(cfg) and "editor" in sections else "")
                  + ("\n\n" + sections["retry"].replace("{errors}", "\n".join(f"- {e}" for e in errors)) if errors else "")
                  + "\n\n" + sections["output_format"])
        att = {"n": attempt + 1, "prompt": prompt, "errors_shown": errors, "part_order": ["prefix_stage2.txt", "chosen moments + instructions"]}
        attempts.append(att)
        log(f"text call {attempt + 1} for {len(chosen)} moments")
        try:
            obj, raw = client.generate_json(ROLE, spec, sections["system"], prompt, schema, meta={"video_id": inputs.vid, "stage": "6"}, want_raw=True,
                                            media=[{"text": prefix2}])
        except BaseException as e:
            att["error"] = f"{type(e).__name__}: {e}"
            write_json(run_dir / "stage6_debug.json", {"model": spec.to_dict(), "system": sections["system"], "attempts": attempts})
            raise
        att["raw_text"] = raw.text
        att["usage"] = {"input_tokens": raw.input_tokens, "output_tokens": raw.output_tokens, "thought_tokens": raw.thought_tokens, "cached_tokens": raw.cached_tokens}
        for k in usage:
            usage[k] += int(getattr(raw, k) or 0)
        errors = schema_errors(obj) + validate_text(obj, chosen, c6, facts)
        att["validation"] = errors
        if not errors:
            ans = obj
            break
    write_json(run_dir / "stage6_debug.json", {"model": spec.to_dict(), "system": sections["system"], "attempts": attempts})
    r, rate = cfg["rates"], inr_per_usd(cfg)
    cached = min(usage["cached_tokens"], usage["input_tokens"])
    cost = round(((usage["input_tokens"] - cached) * r["usd_per_1m_input"] + cached * r["usd_per_1m_cached_input"]
                  + (usage["output_tokens"] + usage["thought_tokens"]) * r["usd_per_1m_output"]) / 1e6 * rate, 3)
    append_log(cfg, {"episode": inputs.vid, "stage": "6:text", "status": "ok" if ans else "failed", "duration_s": round(time.perf_counter() - t0, 1),
                     "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"] + usage["thought_tokens"], "cached_tokens": usage["cached_tokens"],
                     "cost_inr_est": cost, "note": f"{len(chosen)} moments" if ans else "validation failed after retry: " + " | ".join(errors[:4])})
    if ans is None:
        raise SelectionError("the text call failed validation after the retry: " + " | ".join(errors[:6]))
    words = {e["moment_id"]: e for e in ans["moments"]}
    names = line_names(facts)
    cands = {c["id"]: c for c in facts["candidates"]}
    for m in chosen:
        w = words[m["id"]]
        m.update({k: w[k] for k in ("hook_text", "caption", "title", "hashtags", "context_line", "cta_target", "q9_vs_dynamic", "q9_note", "people_in_text")})
        m["needs_caption_context"] = bool(m["primary"]["needs_caption_context"]) or w["q9_vs_dynamic"] == "contradicts"
        m["crop_hint"] = crop_hint(facts, cands[m["primary"]["id"]], names)
        m["people_line"] = moment_people(facts, m)[2]
    final = {"version": VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "episode": {"video_id": inputs.vid, "title": (facts.get("episode") or {}).get("title"), "duration": facts["episode"]["duration"]},
             "stage4_run": facts["scores_active"], "stage5_generated_at": (facts["editor"] if editor_mode(cfg) else facts["critic"])["generated_at"],
             "stage5_mode": "editor" if editor_mode(cfg) else "critic",
             "main": [_moment_out(m) for m in sel["main"]], "bonus": [_moment_out(m) for m in sel["bonus"]],
             "requirements": sel["requirements"], "warnings": sel["warnings"],
             "rejected": [{"key": m["key"], "scene_id": m["scene_id"], "label": m["label"], "kind": m["kind"], "payoff_line": m["payoff_line"],
                           "reason": (reason_text(sel["why"][m["key"]], {x["key"]: x for x in sel["moments"]}, int(c6["main_slots"]), int(c6["bonus_loops"]))
                                      if m["key"] in sel["why"] else "no shippable version (PASS, BORDERLINE or pace-only FAIL)"),
                           "rule": (sel["why"].get(m["key"]) or {}).get("rule") or "verdict",
                           "variants": [{"id": v["id"], "variant": v["variant"], "final_duration": v["final_duration"], "verdict": v["verdict"],
                                         "n_pass": v["n_pass"], "not_shipped_why": v["not_shipped_why"]} for v in m["variants"]]}
                          for m in sel["moments"] if m["key"] not in {x["key"] for x in chosen}],
             "text_call": {"usage": {**usage, "cost_inr": cost}, "attempts": len(attempts), "model": spec.to_dict()}}
    final["creator_picks"] = creator_picks(sel, c6)
    final["channel_shorts_reference"] = channel_short_reference(facts)
    report = rejection_report(sel, c6, final["episode"]["title"] or inputs.vid)
    pkg = write_package(run_dir, Path(inputs.paths.mp4), final, facts, cfg, report, renderer=renderer, shrinker=shrinker, log=log)
    final["package"] = {k: v for k, v in pkg.items() if k != "clips"}
    final["rejection_report_md"] = report
    final["spend"] = episode_spend(cfg, inputs.vid)
    # the runner logs this stage's own row after run() returns: add the text call so the total is complete
    final["spend"]["by_stage"]["6"] = round(final["spend"]["by_stage"].get("6", 0.0) + cost, 2)
    final["spend"]["total_inr"] = round(sum(final["spend"]["by_stage"].values()), 2)
    write_json(run_dir / "final" / "final.json", {k: v for k, v in final.items() if k != "rejection_report_md"})
    out = dict(facts)
    out["final"] = final
    out.setdefault("stages", {})[STAGE_KEY] = {
        "version": VERSION, "model": spec.to_dict(), "prompt_file": PROMPT_PATH.name, "config_sha8": cfg.get("_sha8"), "generated_at": final["generated_at"],
        "duration_s": round(time.perf_counter() - t0, 3), "usage": {"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"] + usage["thought_tokens"],
                                                                     "cached_tokens": usage["cached_tokens"], "cost_inr": cost},
        "warnings": sel["warnings"], "summary": {"main": len(sel["main"]), "bonus": len(sel["bonus"]), "rejected": len(final["rejected"]),
                                                 "review_html_mb": pkg["review_html_mb"], "rendered_now": pkg["rendered_now"]},
        "debug_file": "stage6_debug.json"}
    return out


# ----------------------------------------------------------------------------------------------- re-generate words
def regenerate(inputs, facts: dict | None, cfg: dict, moment_ids: list[str], client=None, log=None, renderer=None, shrinker=None) -> dict:
    """One text call for the named moments of the stored set only (e.g. after a prompt rule changes); the rest of the
    set, the clips and the picks are kept; the package is rewritten. Run through runner.run_substage (logged)."""
    log = log or (lambda *a: None)
    fin = (facts or {}).get("final")
    if not fin:
        raise SelectionError("no Stage 6 set to re-generate words for; run Stage 6 first")
    c6 = cfg["stage6"]
    spec = ModelSpec.from_dict(cfg["models"].get("stage6") or cfg["models"]["default"])
    run_dir = Path(inputs.paths.run_dir)
    pool = fin["main"] + fin["bonus"]
    moments = [m for m in pool if m["id"] in set(moment_ids)]
    if not moments:
        raise SelectionError(f"moment ids {moment_ids} are not in the set ({[m['id'] for m in pool]})")
    sections = load_prompt(PROMPT_PATH)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if client is None:
        from shotvi.llm.client import Ledger, LLMClient
        client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"), max_retries=int(c6["text"].get("http_retries", 1)))
    body = text_prompt(facts, moments, c6)
    attempts, errors, ans = [], [], None
    usage = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0}
    for attempt in range(int(c6["text"].get("retries", 1)) + 1):
        prompt = (body + "\n\n" + sections["instructions"] + ("\n\n" + sections["retry"].replace("{errors}", "\n".join(f"- {e}" for e in errors)) if errors else "")
                  + "\n\n" + sections["output_format"])
        att = {"n": attempt + 1, "prompt": prompt, "errors_shown": errors, "moments": moment_ids}
        attempts.append(att)
        obj, raw = client.generate_json(ROLE, spec, sections["system"], prompt, schema, meta={"video_id": inputs.vid, "stage": "6:words"}, want_raw=True,
                                        media=[{"text": S4.prefix2_text(facts, cfg, run_dir)}])
        att["raw_text"] = raw.text
        att["usage"] = {"input_tokens": raw.input_tokens, "output_tokens": raw.output_tokens, "thought_tokens": raw.thought_tokens, "cached_tokens": raw.cached_tokens}
        for k in usage:
            usage[k] += int(getattr(raw, k) or 0)
        errors = schema_errors(obj) + validate_text(obj, moments, c6, facts)
        att["validation"] = errors
        if not errors:
            ans = obj
            break
    dbg_path = run_dir / "stage6_debug.json"
    dbg = json.loads(dbg_path.read_text(encoding="utf-8")) if dbg_path.exists() else {}
    dbg.setdefault("regenerations", []).append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "attempts": attempts})
    write_json(dbg_path, dbg)
    r, rate = cfg["rates"], inr_per_usd(cfg)
    cached = min(usage["cached_tokens"], usage["input_tokens"])
    cost = round(((usage["input_tokens"] - cached) * r["usd_per_1m_input"] + cached * r["usd_per_1m_cached_input"]
                  + (usage["output_tokens"] + usage["thought_tokens"]) * r["usd_per_1m_output"]) / 1e6 * rate, 3)
    # runner.run_substage does not write the runs log, so the call logs itself (the episode spend reads that log)
    append_log(cfg, {"episode": inputs.vid, "stage": "6:words", "status": "ok" if ans else "failed", "input_tokens": usage["input_tokens"],
                     "output_tokens": usage["output_tokens"] + usage["thought_tokens"], "cached_tokens": usage["cached_tokens"], "cost_inr_est": cost,
                     "ledger_delta_inr": cost, "note": f"re-generated words for {', '.join(moment_ids)}"})
    if ans is None:
        raise SelectionError("the re-generated words failed validation after the retry: " + " | ".join(errors[:6]))
    words = {e["moment_id"]: e for e in ans["moments"]}
    fin = copy_final = json.loads(json.dumps(fin))
    for m in copy_final["main"] + copy_final["bonus"]:
        if m["id"] in words:
            w = words[m["id"]]
            m["previous_words"] = {k: m.get(k) for k in ("hook_text", "caption", "title", "hashtags", "context_line")}
            m.update({k: w[k] for k in ("hook_text", "caption", "title", "hashtags", "context_line", "cta_target", "q9_vs_dynamic", "q9_note", "people_in_text")})
            m["needs_caption_context"] = bool(m["primary"].get("needs_caption_context")) or w["q9_vs_dynamic"] == "contradicts"
    for m in copy_final["main"] + copy_final["bonus"]:
        m["people_line"] = moment_people(facts, m)[2]                # shown under every hook text (founder, 2026-10-29)
    fin["regenerated"] = list(fin.get("regenerated") or []) + [{"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "moments": moment_ids, "cost_inr": cost}]
    report = fin.get("rejection_report_md") or ""
    pkg = write_package(run_dir, Path(inputs.paths.mp4), fin, facts, cfg, report, renderer=renderer, shrinker=shrinker, log=log)
    fin["package"] = {k: v for k, v in pkg.items() if k != "clips"}
    fin["spend"] = episode_spend(cfg, inputs.vid)                    # this call is already in the log (above)
    write_json(run_dir / "final" / "final.json", {k: v for k, v in fin.items() if k != "rejection_report_md"})
    out = dict(facts)
    out["final"] = fin
    st = dict((out.get("stages") or {}).get(STAGE_KEY) or {})
    st["regenerated"] = fin["regenerated"]
    out.setdefault("stages", {})[STAGE_KEY] = st
    out["stages"]["6:words"] = {"version": VERSION, "duration_s": 0.0, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "usage": {"input_tokens": usage["input_tokens"],
                                "output_tokens": usage["output_tokens"] + usage["thought_tokens"], "cached_tokens": usage["cached_tokens"], "cost_inr": cost},
                                "warnings": [], "moments": moment_ids}
    return out
