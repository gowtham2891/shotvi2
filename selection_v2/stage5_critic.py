"""Stage 5, cold-viewer critic (doc 8): the first time an actual clip exists. Each selected candidate is rendered as
the real cut (Stage 3 segments, hard cuts, 480p) and shown to a fresh Gemini call with NO context: no transcript, no
beat sheet, no scores, no prefix. It answers eight open questions as a Telugu viewer who stumbled on it in Shorts.
Three independent runs per clip; code compares the answers to what Stages 2-4 expected.

Which clips: the ACTIVE Stage 4 run on the current Stage 3 output; passing, eligible candidates; one sibling family at
a time (the family's top by combined score plus its trimmed / untrimmed twin when that twin also passed); capped per
episode in combined order with a floor of emotional and hook clips. Thread candidates and critic_trimmed siblings are
always judged, outside the cap; loop families are skipped when Stage 6 loops are off for the content type (founder,
2026-10-29). Every verdict carries a reason class (``critic_class``: confusion or pace). Output: ``facts["critic"]``.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import jsonschema

from shotvi.llm.base import ModelSpec

from . import critic_class as CC, stage3_candidates as S3, stage4_scoring as S4
from .config import ROOT, inr_per_usd, loops_on
from .typical import typical_length
from . import textmatch as TM
from .media import content_key, media_part, upload
from .paths import write_json
from .preview import render as render_clip, time_map, to_clip_time
from .runlog import append_log
from .stage1_episode_pass import load_prompt
from .stage1_facts import fmt_ts

VERSION = "stage5/0.1"
STAGE_KEY = "5"
ROLE = "cold_critic"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "stage5_critic.md"
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "critic.schema.json"


class CriticError(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------- selection
def check_inputs(facts: dict | None) -> dict:
    """The active Stage 4 run, which must score the current Stage 3 output."""
    S4.check_stage3(facts)
    run = S4.active_run(facts)
    if run is None:
        raise RuntimeError("Stage 5 needs an active Stage 4 run; run Stage 4 (and mark a run active) first")
    if run.get("stage3_generated_at") != facts["stages"]["3"]["generated_at"]:
        raise RuntimeError(f"the active Stage 4 run {run['run_key']} scored an older Stage 3 output; rerun Stage 4 on the current candidates and mark it active")
    if len(run.get("scenes_scored") or []) < run.get("n_scenes", 0):
        raise RuntimeError(f"the active Stage 4 run scored {len(run.get('scenes_scored') or [])} of {run.get('n_scenes')} scenes; finish it first")
    return run


ALWAYS_VARIANTS = {"thread": "thread", "critic_trimmed": "critic_trim", "scene": "scene", "scene_trimmed": "scene",
                   "channel_short": "channel_short"}   # variant -> role; judged outside the cap (scene, channel_short: 2026-10-30)


def select_clips(facts: dict, cfg: dict) -> list[dict]:
    """[{id, family, role: top|twin|floor|typical|coverage|thread|critic_trim|scene|channel_short, combined, type, variant,
    final_duration}] in run order."""
    run = check_inputs(facts)
    c5 = cfg["stage5"]
    cands = {c["id"]: c for c in facts["candidates"]}
    skip_loops = not loops_on(facts, cfg)
    scored = {s["id"]: s for s in run["candidates"] if s["status"] == "pass" and s.get("eligible_for_final", True) and s["id"] in cands
              and cands[s["id"]]["variant"] not in ALWAYS_VARIANTS and not (skip_loops and s["type"] == "loop")}
    fams: dict[tuple, list[dict]] = {}
    for sid, s in scored.items():
        fams.setdefault(S3.slot_key(cands[sid]), []).append(s)
    picks: list[dict] = []
    for key, members in fams.items():
        top = max(members, key=lambda s: (s["combined"], s["id"]))
        c = cands[top["id"]]
        twin = c.get("trimmed_sibling") or c.get("untrimmed_sibling")
        fam = {"family": "|".join(str(k) for k in key), "top": top, "twin": scored.get(twin) if twin and twin in scored else None}
        picks.append(fam)
    picks.sort(key=lambda f: (-f["top"]["combined"], f["top"]["id"]))
    cap = int(c5["per_episode"])
    chosen: list[dict] = []
    def add_family(fam, role):
        # founder, 2026-10-28: per family the top-scored candidate, its twin (as before) and the SHORTEST passing variant when
        # that is a third clip
        chosen.append({"id": fam["top"]["id"], "family": fam["family"], "role": role, "combined": fam["top"]["combined"], "type": fam["top"]["type"],
                       "variant": fam["top"]["variant"], "final_duration": fam["top"]["final_duration"]})
        if fam["twin"]:
            chosen.append({"id": fam["twin"]["id"], "family": fam["family"], "role": "twin", "combined": fam["twin"]["combined"], "type": fam["twin"]["type"],
                           "variant": fam["twin"]["variant"], "final_duration": fam["twin"]["final_duration"]})
    taken = set()
    for fam in picks:
        if len(chosen) >= cap:
            break
        add_family(fam, "top"); taken.add(fam["family"])
    # the floor: at least N emotional / hook clips when any passed, replacing the lowest non-floor families
    for ctype, n in (c5.get("floor") or {}).items():
        have = sum(1 for x in chosen if x["type"] == ctype)
        pool = [f for f in picks if f["top"]["type"] == ctype and f["family"] not in taken]
        while have < int(n) and pool:
            fam = pool.pop(0)
            n_add = 1 + (1 if fam["twin"] else 0)
            while len(chosen) + n_add > cap and chosen:
                victims = [x for x in reversed(chosen) if x["type"] not in (c5.get("floor") or {})]
                if not victims:
                    break
                v = victims[0]
                chosen = [x for x in chosen if x["family"] != v["family"]]
                taken.discard(v["family"])
            add_family(fam, "floor"); taken.add(fam["family"])
            have = sum(1 for x in chosen if x["type"] == ctype)
    # founder, 2026-10-30: per judged MOMENT (scene + payoff line), the variant closest to the channel's typical Short length
    # plus its twin (replaces 2026-10-28's shortest passing variant)
    target = typical_length(facts, cfg)["target_s"]
    have = {x["id"] for x in chosen}
    moments = {(cands[x["id"]]["scene_id"], cands[x["id"]].get("payoff_line")) for x in chosen}
    for mk in sorted(moments, key=lambda k: (k[0], str(k[1]))):
        members = [s for s in scored.values() if (cands[s["id"]]["scene_id"], cands[s["id"]].get("payoff_line")) == mk]
        if not members:
            continue
        near = min(members, key=lambda s: (abs(s["final_duration"] - target), -s["combined"], s["id"]))
        c = cands[near["id"]]
        tw = c.get("trimmed_sibling") or c.get("untrimmed_sibling")
        for s in [near] + ([scored[tw]] if tw in scored else []):
            if s["id"] not in have:
                have.add(s["id"])
                chosen.append({"id": s["id"], "family": "|".join(str(k) for k in S3.slot_key(cands[s["id"]])), "role": "typical", "combined": s["combined"],
                               "type": s["type"], "variant": s["variant"], "final_duration": s["final_duration"]})
    # founder, 2026-10-30: coverage rule: every scene's top Stage 4 candidate (passing, eligible, not a loop where loops are
    # off) is judged regardless of rank, in addition to the list above
    if c5.get("coverage_per_scene", True):
        have = {x["id"] for x in chosen}
        for sid in sorted({s["scene_id"] for s in scored.values()}):
            top = max((s for s in scored.values() if s["scene_id"] == sid), key=lambda s: (s["combined"], s["id"]))
            if top["id"] not in have:
                chosen.append({"id": top["id"], "family": "|".join(str(k) for k in S3.slot_key(cands[top["id"]])), "role": "coverage", "combined": top["combined"],
                               "type": top["type"], "variant": top["variant"], "final_duration": top["final_duration"]})
    # founder, 2026-10-29: thread candidates and critic_trimmed siblings are always judged (any Stage 4 status), outside the cap
    by_id = {s["id"]: s for s in run["candidates"]}
    for c in sorted(facts["candidates"], key=lambda c: c["id"]):
        if c["variant"] in ALWAYS_VARIANTS and c["id"] in by_id and (c.get("eligible_for_final", True) or c["variant"] == "channel_short"):
            s = by_id[c["id"]]
            chosen.append({"id": c["id"], "family": "|".join(str(k) for k in S3.slot_key(c)), "role": ALWAYS_VARIANTS[c["variant"]],
                           "combined": s["combined"], "type": c["type"], "variant": c["variant"], "final_duration": c["final_duration"]})
    return chosen


def is_open_question(c: dict) -> bool:
    """An end-variant ending on a Stage 2 hook_moment (founder, 2026-10-29): judged like a hook (wants the next part)."""
    return str(c.get("variant") or "").startswith("open_question")


# ----------------------------------------------------------------------------------------------- expectations
def expected_moment(facts: dict, c: dict) -> dict:
    """What Stages 2-4 expect the viewer's moment to be, on the episode clock and the clip clock: the span from the
    payoff line's start to the end anchor (reaction) of the candidate."""
    lid = {r["id"]: r for r in facts["lines"]}
    p = lid.get(c.get("payoff_line")); a = lid.get(c.get("anchor_line")) or p
    if is_open_question(c):
        p = a = lid.get(c.get("anchor_line")) or p                # the clip ends on the question; the payoff is outside it
    tmap = time_map(c["segments"])
    if not p:
        return {"line": None, "ep_start": None, "ep_end": None, "clip_start": None, "clip_end": None, "time_map": tmap}
    ep0, ep1 = p["start"], max(p["end"], a["end"])
    if c.get("thread"):
        # founder, 2026-10-30: a thread's moment is anywhere in its PAYOFF SCENE part (from that part's first kept second to the end)
        sheet_p = next((s for s in facts.get("beat_sheets") or [] if s["scene_id"] == c["thread"].get("payoff_scene")), None)
        part = [sg for sg in c["segments"] if sheet_p and float(sg[0]) >= float(sheet_p["start"]) - 1.0]
        if part:
            ep0, ep1 = float(part[0][0]), float(c["end"])
    c0, c1 = to_clip_time(tmap, ep0), to_clip_time(tmap, min(ep1, c["end"]))
    # founder, 2026-10-29: what the critic's Q3 text is matched against: the payoff line (quoted), the setup line just
    # before it (its words are not distinctive), and the beat sheet's English note for that line (described)
    order = [r for r in facts["lines"] if r["kind"] == "speech"]
    k = next((i for i, r in enumerate(order) if r["id"] == p["id"]), None)
    prev = order[k - 1]["text"] if k else ""
    sheet = next((s for s in facts.get("beat_sheets") or [] if s["scene_id"] == c["scene_id"]), {})
    note = next((b.get("note") for b in sheet.get("beats") or [] if b.get("line") == p["id"] and b.get("note")), None)
    if not note and (sheet.get("payoff") or {}).get("line") == p["id"]:
        note = sheet["payoff"].get("note")
    return {"line": p["id"], "text": p["text"], "prev_text": prev, "note": note or "", "ep_start": ep0, "ep_end": ep1, "clip_start": c0, "clip_end": c1,
            "time_map": tmap, "names": sorted(TM.names_of(facts))}


def compare_run(c: dict, ans: dict, exp: dict, cfg5: dict) -> dict:
    """PASS / fail of one critic run against the expectation, with reasons. Comedy / emotional / loop: the critic's
    moment within moment_tol_s of the expected span, no (or minor) missing context, no swipe stretch over swipe_max_s.
    Hook: wants the next thing with a reason, and the best moment is not a resolving punchline at the end."""
    cmp = cfg5["compare"]
    clip_len = float(c["final_duration"])
    # founder, 2026-10-28: the tolerance grows with the clip (the critic's clock drifts ~6 s on a 130 s clip)
    tol = max(float(cmp["moment_tol_s"]), float(cmp.get("moment_tol_frac", 0.0)) * clip_len)
    swipe_max = float(cmp["swipe_max_s"])
    ok_missing = set(cmp.get("missing_ok") or ["none", "minor"])
    reasons, ok = [], True
    found_by_text = None
    sec = float(ans["best_moment"]["second"])
    long_swipes = [s for s in ans.get("swipe") or [] if float(s["to_s"]) - float(s["from_s"]) > swipe_max]
    if c["type"] == "hook" or is_open_question(c):
        # founder, 2026-10-28: a hook run passes on Q5 (wants the next thing, with a reason); the no-punchline-tail check
        # only on hooks longer than hook_tail_check_min_len_s (a 7 s hook IS one funny line)
        want = bool(ans["want_next"]["yes"]) and len(re.findall(r"\w+", ans["want_next"].get("why") or "")) >= 3
        if not want:
            ok = False; reasons.append("does not want to know what happens next (or gives no reason)")
        tail = float(cmp.get("hook_punchline_tail_s", 3.0))
        if clip_len > float(cmp.get("hook_tail_check_min_len_s", 0.0)) and ans["best_moment"]["kind"] == "funny" and sec >= clip_len - tail:
            ok = False; reasons.append(f"best moment is a funny line at {sec:.0f} s, inside the last {tail:.0f} s: reads as a resolving punchline")
        if ans["confusion"]["missing"] not in ok_missing:
            reasons.append("major missing context (noted, not failing a hook)")
    else:
        loop_tail = float(cmp.get("loop_tail_ok_s", 0.0)) if c["type"] == "loop" else 0.0
        if c.get("channel_short"):
            pass                                                # founder, 2026-10-30: the channel's own cut has no expected payoff (calibration rule)
        elif exp.get("clip_start") is None and not (loop_tail and sec >= clip_len - loop_tail):
            ok = False; reasons.append("expected moment is not inside the clip (cut out)")
        elif exp.get("clip_start") is not None:
            d = 0.0 if exp["clip_start"] - tol <= sec <= exp["clip_end"] + tol else min(abs(sec - exp["clip_start"]), abs(sec - exp["clip_end"]))
            if d > 0 and loop_tail and sec >= clip_len - loop_tail:
                d = 0.0                                        # founder, 2026-10-28: a loop's moment anywhere in its last seconds counts
            if d > 0 and cmp.get("text_match", True):
                # founder, 2026-10-29: Q3 naming the payoff (quoted words, or the beat described) counts regardless of clip time
                what = (ans.get("best_moment") or {}).get("what") or ""
                q = TM.quoted_match(what, exp.get("text") or "", float(cmp.get("quote_min_ratio", 0.5)), exp.get("prev_text") or "")
                dm = TM.description_match(what, exp.get("note") or "", set(exp.get("names") or []), int(cmp.get("description_min_shared", 3)))
                if q["match"] or dm["match"]:
                    d = 0.0
                    reasons_found = f"moment found by text ({'quoted: ' + ', '.join(q['hits']) if q['match'] else 'described: ' + ', '.join(dm['shared'])}) at {sec:.0f} s"
                    found_by_text = reasons_found
            if d > 0:
                ok = False; reasons.append(f"critic's moment at {sec:.0f} s is {d:.0f} s away from the expected {exp['clip_start']:.0f}-{exp['clip_end']:.0f} s (tolerance {tol:.1f} s)")
        if ans["confusion"]["missing"] not in ok_missing:
            ok = False; reasons.append(f"missing context: {ans['confusion']['missing']} ({(ans['confusion'].get('what') or '')[:80]})")
    if long_swipes:
        ok = False; reasons.append("swipe stretch over the limit: " + "; ".join(f"{float(s['from_s']):.0f}-{float(s['to_s']):.0f} s" for s in long_swipes))
    return {"pass": ok, "reasons": reasons, "located_s": sec, "long_swipes": long_swipes, "found_by_text": found_by_text,
            "fail_classes": [] if ok else sorted({k for k in (CC.classify_reason(x) for x in reasons) if k})}


def verdict_of(n_pass: int, n_runs: int, cfg5: dict) -> str:
    if n_pass >= int(cfg5["compare"].get("pass_runs", 2)):
        return "PASS"
    return "BORDERLINE" if n_pass >= 1 else "FAIL"


# ----------------------------------------------------------------------------------------------- caption context
def caption_context(facts: dict, c: dict, runs: list[dict], cfg5: dict) -> dict:
    """founder, 2026-10-29: needs_caption_context only when Q2 says "not understood" in >= caption_context_min_runs runs, or
    (Stage 6, text call) Q9's relationship contradicts the beat sheet's dynamic. The word-overlap heuristic of 2026-10-28 is
    gone (it flagged 21 of 35 clips on wording alone). Q9 answers and the sheet's dynamic are stored here for Stage 6."""
    sheet = next((s for s in facts.get("beat_sheets") or [] if s["scene_id"] == c["scene_id"]), {})
    per = [{"understood": bool(r["answers"]["first_3s"].get("understood")), "thought_it_was": r["answers"]["first_3s"].get("thought_it_was") or "",
            "relationship": r["answers"].get("relationship")} for r in runs]
    n = sum(1 for x in per if not x["understood"])
    return {"needs_caption_context": n >= int(cfg5["compare"].get("caption_context_min_runs", 2)), "runs_not_understood": n,
            "q9_vs_dynamic": None, "setup": sheet.get("setup") or "", "dynamic": sheet.get("dynamic") or {}, "framings": per}


# ----------------------------------------------------------------------------------------------- carry-over
def clip_key(c: dict) -> list:
    """What makes a clip the same clip across reruns: type, variant and the exact kept segments (ids may shift)."""
    return [c["type"], c["variant"], [[round(float(a), 3), round(float(b), 3)] for a, b in c["segments"]]]


def recompare(prev: dict, c: dict, x: dict, facts: dict, cfg: dict) -> dict:
    """A carried clip record: the stored critic answers of the same clip (same content) compared again with the current
    rules; no render, no upload, no call."""
    c5 = cfg["stage5"]
    exp = expected_moment(facts, c)
    runs = []
    for r in prev["runs"]:
        cmp = compare_run(c, r["answers"], exp, c5)
        runs.append({**r, **cmp})
    n_pass = sum(1 for z in runs if z["pass"])
    rec = {**prev, "id": c["id"], "family": x["family"], "role": x["role"], "type": c["type"], "variant": c["variant"], "scene_id": c["scene_id"],
           "final_duration": float(c["final_duration"]), "stage4_combined": x["combined"], "verdict": verdict_of(n_pass, len(runs), c5), "n_pass": n_pass,
           "n_runs": len(runs), "expected": {k: v for k, v in exp.items() if k not in ("time_map", "names")}, "time_map": exp["time_map"],
           "located_s": [z["located_s"] for z in runs], "runs": runs, "caption_context": caption_context(facts, c, runs, c5),
           "carried_from": prev.get("carried_from") or prev.get("generated_at"), "content_key": clip_key(c),
           "reason_class": CC.reason_class(runs), "pace_stretches": CC.pace_stretches(runs)}
    rec["usage"] = {**prev.get("usage", {}), "cost_inr": 0.0, "paid_cost_inr": prev.get("usage", {}).get("paid_cost_inr", prev.get("usage", {}).get("cost_inr", 0.0))}
    return rec


def recompare_all(facts: dict, cfg: dict) -> tuple[dict, list[dict]]:
    """Re-compare every stored record (selection + archive) with the CURRENT compare rules; no render, no upload, no
    call (₹0). Records whose cut no longer exists among today's candidates are left as they are. Returns the updated
    facts and the verdict flips [{id, before, after, n_pass_before, n_pass_after, archived, found_by_text}]."""
    cr = dict(facts.get("critic") or {})
    if not cr.get("clips") and not cr.get("archive"):
        raise RuntimeError("no stored critic answers to re-compare")
    by_key = {json.dumps(clip_key(c)): c for c in facts.get("candidates") or []}
    flips: list[dict] = []

    def redo(z: dict, archived: bool) -> dict:
        c = by_key.get(json.dumps(z.get("content_key") or clip_key_of_record(z)))
        if c is None:
            return z
        x = {"family": z.get("family"), "role": z.get("role"), "combined": z.get("stage4_combined")}
        nz = recompare(z, c, x, facts, cfg)
        nz["usage"] = dict(z.get("usage") or {})                      # a re-compare spends nothing and changes no cost record
        nz["carried_from"] = z.get("carried_from")
        if not nz["carried_from"]:
            nz.pop("carried_from", None)
        if nz["verdict"] != z.get("verdict") or nz["n_pass"] != z.get("n_pass"):
            flips.append({"id": c["id"], "stored_as": z["id"], "before": z.get("verdict"), "after": nz["verdict"], "n_pass_before": z.get("n_pass"),
                          "n_pass_after": nz["n_pass"], "archived": archived, "type": c["type"], "variant": c["variant"], "final_duration": c["final_duration"],
                          "found_by_text": [r.get("found_by_text") for r in nz["runs"]]})
        if archived:
            nz["id"] = z["id"]
        return nz

    cr["clips"] = [redo(z, False) for z in cr.get("clips") or []]
    cr["archive"] = [redo(z, True) for z in cr.get("archive") or []]
    verdicts: dict[str, int] = {}
    for z in cr["clips"]:
        verdicts[z["verdict"]] = verdicts.get(z["verdict"], 0) + 1
    cr["verdicts"] = verdicts
    cr["needs_caption_context"] = [z["id"] for z in cr["clips"] if (z.get("caption_context") or {}).get("needs_caption_context")]
    cr["recompared_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out = dict(facts); out["critic"] = cr
    return out, flips


def plan(facts: dict, cfg: dict, clip_ids=None) -> list[dict]:
    """The selection with an ``action`` per clip: ``carry`` when a previous critic record of the same clip (same content)
    exists (re-compared for free), ``run`` otherwise (founder, 2026-10-28: rerun only clips whose segments changed).
    ``clip_ids`` forces a fresh run of those ids."""
    run4 = check_inputs(facts)
    cands = {c["id"]: c for c in facts["candidates"]}
    selected = select_clips(facts, cfg)
    if clip_ids is not None:
        passing = {s["id"]: s for s in run4["candidates"] if s["status"] == "pass" and s.get("eligible_for_final", True)}
        have = {x["id"] for x in selected}
        for i in clip_ids:
            if i in have:
                continue
            if i not in passing or i not in cands:
                raise RuntimeError(f"clip {i} is not a passing, eligible candidate of the active Stage 4 run")
            s = passing[i]
            selected.append({"id": i, "family": "|".join(str(k) for k in S3.slot_key(cands[i])), "role": "manual", "combined": s["combined"], "type": s["type"],
                             "variant": s["variant"], "final_duration": s["final_duration"]})
    prev = facts.get("critic") or {}
    have = {x["id"] for x in selected}
    for x in prev.get("selected") or []:
        if x.get("role") == "manual" and x["id"] not in have and x["id"] in cands and any(json.dumps(clip_key(cands[x["id"]])) == json.dumps(z.get("content_key") or clip_key_of_record(z)) for z in prev.get("clips") or []):
            selected.append(x)
    by_key = stored_records(prev)
    forced = set(clip_ids or [])
    for x in selected:
        k = json.dumps(clip_key(cands[x["id"]]))
        x["action"] = "run" if (x["id"] in forced or k not in by_key) else "carry"
        x["carry_from"] = by_key[k]["id"] if x["action"] == "carry" else None
        x["carry_key"] = k if x["action"] == "carry" else None
    return selected


def stored_records(critic: dict | None) -> dict[str, dict]:
    """Every paid critic record kept so far, by content key: the current selection's clips plus the archive of records
    whose clip fell out of a later selection (2026-10-29: a rerun used to drop them, so their answers were lost)."""
    out: dict[str, dict] = {}
    for z in list((critic or {}).get("archive") or []) + list((critic or {}).get("clips") or []):
        if z.get("runs"):
            out[json.dumps(z.get("content_key") or clip_key_of_record(z))] = z
    return out


def judged_records(facts: dict) -> dict[str, dict]:
    """Every critic record whose cut exists among TODAY's candidates, by current candidate id: the Stage 5 selection's
    clips plus archived records matched by their cut (ids shift across Stage 3 reruns). Archived ones carry
    ``archived: True``."""
    cr = facts.get("critic") or {}
    by_key = {json.dumps(clip_key(c)): c["id"] for c in facts.get("candidates") or []}
    out: dict[str, dict] = {}
    for z in cr.get("clips") or []:
        cid = by_key.get(json.dumps(z.get("content_key") or clip_key_of_record(z)))
        if cid:
            out[cid] = {**z, "id": cid}
    for z in cr.get("archive") or []:
        cid = by_key.get(json.dumps(z.get("content_key") or clip_key_of_record(z)))
        if cid and cid not in out:
            out[cid] = {**z, "id": cid, "archived": True}
    return out


def clip_key_of_record(z: dict) -> list:
    """Content key of a critic record written before content keys were stored: its type, variant and time map."""
    segs = [[round(float(m["ep_start"]), 3), round(float(m["ep_end"]), 3)] for m in z.get("time_map") or []]
    return [z.get("type"), z.get("variant"), segs]


# ----------------------------------------------------------------------------------------------- estimate
def estimate(facts: dict, cfg: dict, clips: list[dict] | None = None) -> dict:
    r, rate = cfg["rates"], inr_per_usd(cfg)
    c5 = cfg["stage5"]
    clips = clips if clips is not None else plan(facts, cfg)
    n_runs = int(c5["runs_per_clip"]); tps = float(r["tokens_per_s_video_low"])
    rows = []
    for x in clips:
        vid_tok = int(float(x["final_duration"]) * tps)
        in_tok = (vid_tok + int(c5["prompt_tokens"])) * n_runs
        out_tok = int(c5["expected_output_tokens"]) * n_runs
        usd = (in_tok * r["usd_per_1m_input"] + out_tok * r["usd_per_1m_output"]) / 1e6
        carry = x.get("action") == "carry"
        rows.append({**x, "video_tokens_per_run": 0 if carry else vid_tok, "input_tokens": 0 if carry else in_tok, "output_tokens": 0 if carry else out_tok,
                     "inr": 0.0 if carry else round(usd * rate, 2)})
    return {"clips": rows, "n_clips": len(rows), "n_run": sum(1 for x in rows if x.get("action") != "carry"), "n_carry": sum(1 for x in rows if x.get("action") == "carry"),
            "runs_per_clip": n_runs, "total_inr": round(sum(x["inr"] for x in rows), 2), "inr_per_usd": rate,
            "note": f"{n_runs} independent runs per clip; clip seconds x {tps:.0f} tokens at the low-resolution rate, no prefix; carried clips (same cut as a stored record) cost 0"}


def schema_errors(obj: dict) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message[:120]}" for e in jsonschema.Draft202012Validator(schema).iter_errors(obj)]


def validate(ans: dict, clip_len: float) -> list[str]:
    errs = schema_errors(ans)
    if not errs:
        if float(ans["best_moment"]["second"]) > clip_len + 2.0:
            errs.append(f"best_moment.second {ans['best_moment']['second']} is beyond the clip length {clip_len:.1f} s (clip seconds, not episode time)")
        for s in ans.get("swipe") or []:
            if float(s["to_s"]) < float(s["from_s"]) or float(s["to_s"]) > clip_len + 2.0:
                errs.append(f"swipe {s['from_s']}-{s['to_s']} is outside the clip (0-{clip_len:.1f} s)")
    return errs


# ----------------------------------------------------------------------------------------------- run
def run(inputs, facts: dict | None, cfg: dict, client=None, clip_ids=None, log=None, renderer=None, uploader=None, deleter=None) -> dict:
    if str(cfg["stage5"].get("mode", "critic")) == "editor" and clip_ids is None:
        from . import stage5_editor                             # founder, 2026-10-30: the editor's pick replaces the critic as the gate
        return stage5_editor.run(inputs, facts, cfg, client=client, log=log, renderer=renderer, uploader=uploader, deleter=deleter)
    t0 = time.perf_counter()
    log = log or (lambda *a: None)
    run4 = check_inputs(facts)
    assert facts is not None
    c5 = cfg["stage5"]
    spec = ModelSpec.from_dict(cfg["models"].get("stage5") or cfg["models"]["default"])
    run_dir = Path(inputs.paths.run_dir); run_dir.mkdir(parents=True, exist_ok=True)
    sections = load_prompt(PROMPT_PATH)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if client is None:
        from shotvi.llm.client import Ledger, LLMClient, get_provider
        client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"), max_retries=int(c5.get("http_retries", 1)))
        try:
            get_provider(spec.provider).timeout = float(c5.get("http_timeout_s", 600))
        except Exception:
            pass
    renderer = renderer or render_clip
    uploader = uploader or upload
    r, rate = cfg["rates"], inr_per_usd(cfg)
    facts = dict(facts)
    cands = {c["id"]: c for c in facts["candidates"]}
    # founder, 2026-10-28: a clip whose cut is unchanged since a stored record is CARRIED (its answers re-compared with the
    # current rules, free); only clips whose segments changed, new ones and explicitly named ids are run
    selected = plan(facts, cfg, clip_ids)
    prev = facts.get("critic") or {}
    stored = stored_records(prev)
    clips: dict[str, dict] = {}
    for x in selected:
        if x["action"] == "carry":
            clips[x["id"]] = recompare(stored[x["carry_key"]], cands[x["id"]], x, facts, cfg)
    todo = [x for x in selected if x["action"] == "run" and (clip_ids is None or x["id"] in set(clip_ids))]
    if not todo:
        raise RuntimeError("no clips to run: " + ("every selected clip has a record for its current cut (carried and re-compared; pick clip ids to rerun)" if clip_ids is None else "nothing matched"))
    debug_path = run_dir / "stage5_debug.json"
    debug = json.loads(debug_path.read_text(encoding="utf-8")) if debug_path.exists() else {"clips": {}}
    debug.update({"model": spec.to_dict(), "system": sections["system"], "questions": sections["questions"]})
    n_runs = int(c5["runs_per_clip"])
    for x in todo:
        ts = time.perf_counter()
        c = cands[x["id"]]
        log(f"{c['id']}: rendering the cut ({len(c['segments'])} segment(s))")
        path = renderer(Path(inputs.paths.mp4), run_dir, c, int(c5["clip_max_height"]))
        clip_len = float(c["final_duration"])
        up = uploader(Path(path), "video")
        part = media_part(up, "video", c5.get("media_fps"), key=f"sha:{content_key(Path(path))}")  # uploads are deleted after use; cache by content
        exp = expected_moment(facts, c)
        runs, attempts_all = [], []
        usage = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0}
        for n in range(1, n_runs + 1):
            attempts, errors, ans = [], [], None
            for attempt in range(int(c5.get("retries", 1)) + 1):
                prompt = (sections["questions"] + ("\n\n" + sections["retry"].replace("{errors}", "\n".join(f"- {e}" for e in errors)) if errors else "")
                          + "\n\n" + sections["output_format"] + f"\n\n(clip length {clip_len:.1f} s; answer sheet {n} of {n_runs})"
                          + (f" (attempt {attempt + 1}: every second you name must lie between 0 and {clip_len:.1f})" if attempt else ""))
                # 2026-10-30: the attempt number makes a retry a NEW prompt; an identical retry replayed the cached invalid answer
                att = {"n": attempt + 1, "run": n, "prompt": prompt, "errors_shown": errors, "media": {"path": str(path), "uri": up.get("uri")}, "part_order": ["clip video", "questions"]}
                attempts.append(att)
                log(f"{c['id']}: critic run {n}, call {attempt + 1}")
                try:
                    obj, raw = client.generate_json(ROLE, spec, sections["system"], prompt, schema, meta={"video_id": inputs.vid, "stage": "5", "clip": c["id"], "run": n},
                                                    want_raw=True, media=[part])
                except BaseException as e:
                    att["error"] = f"{type(e).__name__}: {e}"
                    debug["clips"][c["id"]] = {"attempts": attempts_all + attempts}; write_json(debug_path, debug)
                    append_log(cfg, {"episode": inputs.vid, "stage": f"5:{c['id']}", "status": "failed", "duration_s": round(time.perf_counter() - ts, 1), "note": att["error"][:300]})
                    raise
                att["raw_text"] = raw.text
                att["usage"] = {"input_tokens": raw.input_tokens, "output_tokens": raw.output_tokens, "thought_tokens": raw.thought_tokens, "cached_tokens": raw.cached_tokens}
                for k in usage:
                    usage[k] += int(getattr(raw, k) or 0)
                errors = validate(obj, clip_len)
                att["validation"] = errors
                if not errors:
                    ans = obj; break
            attempts_all += attempts
            if ans is None:
                debug["clips"][c["id"]] = {"attempts": attempts_all}; write_json(debug_path, debug)
                raise CriticError(f"{c['id']} run {n}: the answer failed validation after {len(attempts)} attempts: " + " | ".join(errors[:5]))
            cm = compare_run(c, ans, exp, c5)
            runs.append({"run": n, "answers": ans, **cm})
        if c5.get("delete_upload_after_runs", True):
            try:
                from .media import delete_upload
                attempts_all[-1]["upload_deleted"] = (deleter or delete_upload)(up)
            except Exception as e:  # noqa: BLE001
                attempts_all[-1]["upload_deleted"] = f"failed: {type(e).__name__}: {e}"
        debug["clips"][c["id"]] = {"attempts": attempts_all}; write_json(debug_path, debug)
        cost = round(((usage["input_tokens"] - min(usage["cached_tokens"], usage["input_tokens"])) * r["usd_per_1m_input"]
                      + min(usage["cached_tokens"], usage["input_tokens"]) * r["usd_per_1m_cached_input"]
                      + (usage["output_tokens"] + usage["thought_tokens"]) * r["usd_per_1m_output"]) / 1e6 * rate, 3)
        n_pass = sum(1 for z in runs if z["pass"])
        clips[c["id"]] = {"id": c["id"], "family": x["family"], "role": x["role"], "type": c["type"], "variant": c["variant"], "scene_id": c["scene_id"],
                          "final_duration": clip_len, "clip_path": str(path), "stage4_combined": x["combined"],
                          "verdict": verdict_of(n_pass, n_runs, c5), "n_pass": n_pass, "n_runs": n_runs,
                          "expected": {k: v for k, v in exp.items() if k not in ("time_map", "names")}, "time_map": exp["time_map"],
                          "located_s": [z["located_s"] for z in runs], "swipe": [z["answers"]["swipe"] for z in runs],
                          "confusion": [z["answers"]["confusion"] for z in runs], "series_pull": [z["answers"]["want_more_people"] for z in runs],
                          "want_next": [z["answers"]["want_next"] for z in runs], "captions": [z["answers"]["caption"] for z in runs],
                          "relationships": [z["answers"].get("relationship") for z in runs],
                          "attention": [z["answers"].get("attention") for z in runs],      # Q10 (founder, 2026-10-30)
                          "runs": runs, "usage": {**usage, "cost_inr": cost, "paid_cost_inr": cost}, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          "caption_context": caption_context(facts, c, runs, c5), "content_key": clip_key(c),
                          "reason_class": CC.reason_class(runs), "pace_stretches": CC.pace_stretches(runs)}
        append_log(cfg, {"episode": inputs.vid, "stage": f"5:{c['id']}", "status": "ok", "duration_s": round(time.perf_counter() - ts, 1),
                         "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"] + usage["thought_tokens"],
                         "cached_tokens": usage["cached_tokens"], "cost_inr_est": cost, "note": f"{clips[c['id']]['verdict']} {n_pass}/{n_runs} · {clip_len:.0f} s"})
        log(f"{c['id']}: {clips[c['id']]['verdict']} ({n_pass}/{n_runs}), ₹{cost:.2f}")
    ordered = [clips[x["id"]] for x in selected if x["id"] in clips]
    # 2026-10-29: records whose clip is not in this selection are archived (by content key), never dropped: a later
    # selection that picks them again carries them for free, and the acceptance checks can still read them
    in_sel = {json.dumps(z.get("content_key") or clip_key(cands[z["id"]])) for z in ordered}
    archive = [z for k, z in stored.items() if k not in in_sel]
    total = round(sum(z["usage"]["cost_inr"] for z in ordered), 3)
    verdicts = {}
    for z in ordered:
        verdicts[z["verdict"]] = verdicts.get(z["verdict"], 0) + 1
    facts["critic"] = {"version": VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": spec.to_dict(), "prompt_file": PROMPT_PATH.name,
                       "run_key": run4["run_key"], "stage3_generated_at": facts["stages"]["3"]["generated_at"], "selected": selected,
                       "clips": ordered, "archive": archive, "verdicts": verdicts, "usage": {"cost_inr": total},
                       "n_carried": sum(1 for z in ordered if z.get("carried_from")), "n_run": sum(1 for z in ordered if not z.get("carried_from")),
                       "needs_caption_context": [z["id"] for z in ordered if (z.get("caption_context") or {}).get("needs_caption_context")]}
    facts.setdefault("stages", {})["5"] = {
        "version": VERSION, "model": spec.to_dict(), "prompt_file": PROMPT_PATH.name, "config_sha8": cfg.get("_sha8"),
        "generated_at": facts["critic"]["generated_at"], "duration_s": round(time.perf_counter() - t0, 3),
        "usage": {"input_tokens": sum(z["usage"]["input_tokens"] for z in ordered), "output_tokens": sum(z["usage"]["output_tokens"] + z["usage"]["thought_tokens"] for z in ordered),
                  "cached_tokens": sum(z["usage"]["cached_tokens"] for z in ordered), "cost_inr": total},
        "warnings": [] if len(ordered) == len(selected) else [f"{len(selected) - len(ordered)} selected clip(s) not run yet"],
        "summary": {"clips_done": len(ordered), "selected": len(selected), "verdicts": verdicts}, "debug_file": "stage5_debug.json"}
    return facts
