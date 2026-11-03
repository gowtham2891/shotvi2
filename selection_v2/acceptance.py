"""Acceptance tests for a stage's output on a specific episode (``reference/acceptance/<video_id>.yaml``), shown
green / red in the app. Each check is one item with a ``kind``; the result row says what was found.

Kinds (Stage 1B):
  content_type         {expect}
  scene_count          {min, max}
  scene_edge_at        {t, tol}      a scene starts or ends within tol seconds of t
  no_scene_edge_at     {t, tol}      no scene edge within tol of t (a proposal that should have been merged)
  lines_not_speaker    {lines, not_name, min_share, new_speaker}  the final speaker of >= min_share of these lines
                                     does not carry not_name; with new_speaker, that speaker has status new
  line_not_speaker     {line, not_name, expect_any}  the line's final speaker is not not_name, and its resolution
                                     (likely / name) mentions one of expect_any
  line_reviewed        {line}        the line appears in suspect_resolutions or changed speaker
  non_story_lines      {lines, type_any}  every listed line is covered by a non_story entry of a matching type
  story_lines          {lines}        none of the listed lines is covered by a non-story entry (confirmed story dialogue)
  speaker_names_include {names: ["<Name>|<alias>|<role>", ...]}  every alternative-set is matched by some final name/role

Kinds (Stage 2, read from beat_sheets and the Stage 2 speaker map):
  beat_payoff          {scene_containing, line, reaction_t?, tol}  the scene's payoff line is `line`; reaction near reaction_t
  non_dialogue_class   {range: "16:44.9-16:50.2", stretch_kind, recommend_any, tol}  that stretch is classified so
  scene_register       {scene_containing, any: [...]}
  dynamic_mentions     {scene_containing, any: [...]}   the scene's dynamic text mentions one of the words
  speakers_seen_check  {scene_containing, key, on_screen?, matches_map?}   that key's speakers_seen entry says so
  speakers_seen_line   {line}  the scene holding the line has a speakers_seen entry for the line's key (either outcome passes)
  map_corrected        {lines, from_name, not_name?}  the Stage 2 map gives those lines a name other than from_name
Speaker checks consult the Stage 2 map first (speaker_map.line_names), then 1B's reassignments, then the fix.

Kinds (Stage 3, read from candidates):
  candidates_min       {scene_containing, type?, min, variants_include?: [...], untrimmed?: true}
  candidate_exists     {scene_containing?, type?, variant?, trim_level?, start_line?, start_near?, end_near?, end_near_any?,
                        end_after?, contains_line?, contains_range?, tol}   some candidate satisfies every given constraint
  candidates_end_near  {scene_containing, type?, t, tol}   every candidate of that type in the scene ends within tol of t
  candidate_end_after_line {scene_containing, type?, line | line_any, within_s}  a candidate contains the line and ends within within_s after it
  range_in_candidates  {range: "16:44.9-16:50.2", expect: kept|shortened|not_removed}  among candidates spanning the range
  no_overlap_lines     {lines, unless_variant_in?: [...]}  no candidate overlaps the lines, except those variants
  candidate_count      {min, max}   counts base candidates (cap slots), not trimmed twins
  no_boundary_inside_line {}

Kinds (Stage 4, read from scores.candidates; selectors: scene_containing, candidate_containing, contains_line, lines,
type / type_any, variant, ends_before, ends_after_line_any + within_s):
  vetoed               {selectors, dimension, max_score, all?}   some (all) matching candidates carry that veto
  scores_check         {selectors, min: {dim: n}, max: {dim: n}, not_vetoed?, status_any?, all?}
  sibling_verdicts     {selectors}   every twin among the matches has a verdict_vs_sibling
  fan_clip_or_standalone {selectors, standalone_min, not_vetoed?}
  rejections_have_reasons {}   every rejected candidate carries a veto from the known list (never length)

Kinds (Stage 5, read from critic.clips; same selectors plus variant_any):
  critic_verdict       {selectors, verdict_any?, min_pass_runs?, moment_near_line? + tol + min_runs, q6_positive_min_runs?, no_missing_min_runs?, all?}
  critic_record        {selectors, if_fail_q7_mentions?: [words]}   always recorded; red only when a FAIL's Q7 misses the words
  critic_hook_q5       {selectors, min_runs}   the matching hook with the best Stage 4 combined is PASS with Q5 = yes in >= min_runs runs
  critic_all_recorded  {}
  final_set_shape      {main, bonus_max, primary_max_s}   main count, bonus <= max, no confusion FAIL shipped (a pace FAIL may ship,
                       founder 2026-10-29), every main primary <= max (the platform maximum); the length spread is reported,
                       never judged (founder, 2026-10-30)
  final_golden_recall  {min, full_lines_min?, partial_share_min?}   founder, 2026-10-30: golden-set moments of the episode
                       (data/eval/golden_set.yaml) found in the MAIN set, full or partial, >= min
  final_moment         {selectors, in?, primary_variant_any?, alternate_longer?, cta_target?, series_pull_min?}
  final_any_of         {options: [selectors], in?}   at least one moment of the set matches one of the options
  final_bonus_loops    {contains_line?, min}         >= min bonus loops, one of them containing the line
  final_hook_text_mentions {selectors, words}        the matching moment's hook_text names one of the words
  final_rules          {series_pull_min, count, max_per_scene?}   at most max_per_scene (default 1) main moments per scene and no
                       overlaps; >= count with series_pull >= min
  final_report_mentions {scenes: [m:ss], words_any}  the rejection report has a section on each scene, with critic reasons
  critic_moment_short_variant {max_len_s}   every MOMENT (scene + payoff line) in the critic run has at least one judged variant <= max_len_s (founder, 2026-10-29)
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .stage1_episode_pass import expand_lines
from .stage1_facts import fmt_ts, parse_ts

ACC_DIR = Path(__file__).resolve().parent / "reference" / "acceptance"


def acceptance_path(vid: str, stage: str = "1b") -> Path:
    return ACC_DIR / f"{vid}.{stage}.yaml"


def load_acceptance(vid: str, stage: str = "1b") -> dict | None:
    p = acceptance_path(vid, stage)
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None


def _loose(s) -> str:
    s = str(s or "").lower().replace("sh", "s")
    return re.sub(r"[^a-z0-9ఀ-౿ ]", "", s)


def _mentions(text, words) -> bool:
    t = _loose(text)
    return any(_loose(w) and _loose(w) in t for w in words)


def final_speaker(facts: dict, lid: str) -> tuple[str | None, str | None]:
    """(key, name) of a line: the Stage 2 map (speakers_seen corrections) first, then 1B's reassignments, then the fix."""
    ep = facts.get("episode_pass") or {}
    sm = facts.get("speaker_map") or {}
    r = next((x for x in facts["lines"] if x["id"] == lid), None)
    key = (sm.get("line_keys") or {}).get(lid) or (ep.get("derived") or {}).get("line_speakers", {}).get(lid)
    if key is None and r is not None:
        key = r.get("speaker_key")
    names = {s["key"]: s for s in ep.get("speakers") or []}
    name = (sm.get("line_names") or {}).get(lid) or (names.get(key) or {}).get("name") or (r.get("speaker_name") if r else None)
    return key, name


def evaluate_1b(facts: dict, ref: dict) -> list[dict]:
    ep = facts.get("episode_pass")
    rows = []
    all_ids = [r["id"] for r in facts["lines"]]
    for i, c in enumerate(ref.get("checks") or [], 1):
        kind = c.get("kind")
        row = {"#": i, "kind": kind, "expect": c.get("note") or "", "got": "", "status": "disagree"}
        if ep is None:
            row["status"], row["got"] = "missing", "no Stage 1B output"
            rows.append(row)
            continue
        d = ep["derived"]
        try:
            if kind == "content_type":
                row["expect"] = f"content_type = {c['expect']}"
                row["got"] = str(ep.get("content_type"))
                row["status"] = "agree" if ep.get("content_type") == c["expect"] else "disagree"
            elif kind == "scene_count":
                n = len(d["scenes"])
                row["expect"], row["got"] = f"{c['min']}-{c['max']} scenes", f"{n} scenes"
                row["status"] = "agree" if int(c["min"]) <= n <= int(c["max"]) else "disagree"
            elif kind in ("scene_edge_at", "no_scene_edge_at"):
                t, tol = parse_ts(c["t"])[0], float(c.get("tol", 1.5))
                edges = [s["start"] for s in d["scenes"]] + [s["end"] for s in d["scenes"]]
                near = [e for e in edges if abs(e - t) <= tol]
                row["expect"] = f"{'a' if kind == 'scene_edge_at' else 'no'} scene edge near {c['t']}" + (f" ({c['note']})" if c.get("note") else "")
                row["got"] = ("edges at " + ", ".join(fmt_ts(e) for e in near)) if near else "no edge within tolerance"
                row["status"] = "agree" if (bool(near) == (kind == "scene_edge_at")) else "disagree"
            elif kind == "lines_not_speaker":
                ids, e = expand_lines(c["lines"], all_ids)
                if e:
                    raise ValueError(e)
                ids = [x for x in ids if next(r for r in facts["lines"] if r["id"] == x)["kind"] == "speech"]
                bad = [x for x in ids if _mentions(final_speaker(facts, x)[1], [c["not_name"]])]
                share = 1 - len(bad) / max(1, len(ids))
                keys = {final_speaker(facts, x)[0] for x in ids}
                statuses = {k: next((s.get("status") for s in ep["speakers"] if s["key"] == k), None) for k in keys}
                ok = share >= float(c.get("min_share", 0.8))
                if c.get("new_speaker"):
                    fnames = {str(final_speaker(facts, x)[1] or "").lower() for x in ids}
                    ok = ok and (any(v == "new" for v in statuses.values()) or any(str(k).startswith("s2_new") for k in keys if k)
                                 or any("unknown" in n or "customer" in n or "stranger" in n for n in fnames))
                row["expect"] = f"{c['lines']}: not {c['not_name']}" + (", a new speaker" if c.get("new_speaker") else "")
                row["got"] = f"{share:.0%} of {len(ids)} lines not {c['not_name']}; final keys {sorted(k for k in keys if k)} statuses {statuses}"
                row["status"] = "agree" if ok else "disagree"
            elif kind == "line_not_speaker":
                k, name = final_speaker(facts, c["line"])
                res = next((r for r in ep.get("suspect_resolutions") or [] if r.get("line") == c["line"]), None)
                blob = " ".join([str(name or ""), str((res or {}).get("likely") or ""), str((res or {}).get("reason") or "")])
                ok = not _mentions(name, [c["not_name"]]) and (not c.get("expect_any") or _mentions(blob, c["expect_any"]))
                if res is not None and _mentions(str(res.get("likely")), c.get("expect_any") or []):
                    ok = ok or not _mentions(str(res.get("likely")), [c["not_name"]])
                row["expect"] = f"{c['line']}: not {c['not_name']}" + (f", one of {c['expect_any']}" if c.get("expect_any") else "")
                row["got"] = f"final {k} ({name}); resolution: {(res or {}).get('likely')} — {(res or {}).get('reason', '')[:80]}"
                row["status"] = "agree" if ok else "disagree"
            elif kind == "line_reviewed":
                res = next((r for r in ep.get("suspect_resolutions") or [] if r.get("line") == c["line"]), None)
                rr = next((x for x in facts["lines"] if x["id"] == c["line"]), {})
                prior_key = rr.get("speaker") if ep.get("prior") == "raw" else rr.get("speaker_key")
                k, name = final_speaker(facts, c["line"])
                changed = k is not None and k != prior_key
                row["expect"] = f"{c['line']} flagged or reassigned" + (f" ({c['note']})" if c.get("note") else "")
                row["got"] = (f"resolution: {res.get('likely')} — {res.get('reason', '')[:80]}" if res else "not in suspect_resolutions") + f"; final {k} ({name})"
                row["status"] = "agree" if (res is not None or changed) else "disagree"
            elif kind == "non_story_lines":
                want, e = expand_lines(c["lines"], all_ids) if isinstance(c["lines"], str) else (list(c["lines"]), None)
                if e:
                    raise ValueError(e)
                cover = {}
                for n in ep.get("possible_non_story") or ep.get("non_story") or []:
                    ids, e2 = expand_lines(n.get("lines", ""), all_ids)
                    if not e2:
                        for x in ids:
                            cover[x] = n.get("kind") or n.get("type")
                miss = [x for x in want if x not in cover]
                wrong = [f"{x}:{cover[x]}" for x in want if x in cover and c.get("type_any") and not _mentions(cover[x], c["type_any"])]
                row["expect"] = f"{c['lines']} non-story" + (f" ({'/'.join(c['type_any'])})" if c.get("type_any") else "")
                row["got"] = ("all covered: " + ", ".join(f"{x}:{cover[x]}" for x in want)) if not miss and not wrong else f"not covered {miss}; wrong type {wrong}"
                row["status"] = "agree" if not miss and not wrong else "disagree"
            elif kind == "story_lines":
                want, e = expand_lines(c["lines"], all_ids) if isinstance(c["lines"], str) else (list(c["lines"]), None)
                if e:
                    raise ValueError(e)
                cover = {}
                for n in ep.get("possible_non_story") or ep.get("non_story") or []:
                    ids, e2 = expand_lines(n.get("lines", ""), all_ids)
                    if not e2:
                        for x in ids:
                            cover[x] = n.get("kind") or n.get("type")
                hit = [f"{x}:{cover[x]}" for x in want if x in cover]
                row["expect"] = f"{c['lines']} is story dialogue" + (f" ({c['note']})" if c.get("note") else "")
                row["got"] = "not flagged as non-story" if not hit else f"flagged: {hit}"
                row["status"] = "agree" if not hit else "disagree"
            elif kind == "same_person":
                ka, na = final_speaker(facts, c["line_a"]); kb, nb = final_speaker(facts, c["line_b"])
                row["expect"] = f"{c['line_a']} and {c['line_b']} are the same person" + (f" ({c['note']})" if c.get("note") else "")
                row["got"] = f"{c['line_a']} -> {ka} ({na}); {c['line_b']} -> {kb} ({nb})"
                row["status"] = "agree" if ka is not None and ka == kb else "disagree"
            elif kind == "speaker_names_include":
                blobs = [f"{s.get('name', '')} {s.get('role', '')}" for s in ep.get("speakers") or []]
                missing = [alt for alt in c["names"] if not any(_mentions(b, alt.split("|")) for b in blobs)]
                row["expect"] = f"{len(c['names'])} names present"
                row["got"] = "all present" if not missing else f"missing {missing}"
                row["status"] = "agree" if not missing else "disagree"
            elif kind in ("beat_payoff", "non_dialogue_class", "scene_register", "dynamic_mentions", "speakers_seen_check",
                          "speakers_seen_line", "map_corrected"):
                _eval_stage2(facts, c, row)
            elif kind in STAGE3_KINDS:
                _eval_stage3(facts, c, row)
            elif kind in STAGE4_KINDS:
                _eval_stage4(facts, c, row)
            elif kind in STAGE5_KINDS:
                _eval_stage5(facts, c, row)
            elif kind in STAGE6_KINDS:
                _eval_stage6(facts, c, row)
            else:
                row["status"], row["got"] = "error", f"unknown check kind {kind!r}"
        except Exception as e:  # noqa: BLE001 - one broken check must not hide the rest
            row["status"], row["got"] = "error", f"{type(e).__name__}: {e}"
        rows.append(row)
    return rows


def _sheet_containing(facts: dict, t: float) -> dict | None:
    return next((sh for sh in facts.get("beat_sheets") or [] if sh["start"] - 1e-6 <= t <= sh["end"] + 1e-6), None)


def _sheet_of_line(facts: dict, lid: str) -> dict | None:
    r = next((x for x in facts["lines"] if x["id"] == lid), None)
    return _sheet_containing(facts, r["start"]) if r else None


def _eval_stage2(facts: dict, c: dict, row: dict) -> None:
    kind = c["kind"]
    if not facts.get("beat_sheets"):
        row["status"], row["got"] = "missing", "no Stage 2 beat sheets yet"
        return
    if kind == "map_corrected":
        all_ids = [r["id"] for r in facts["lines"]]
        ids, e = expand_lines(c["lines"], all_ids)
        if e:
            raise ValueError(e)
        ids = [x for x in ids if next(r for r in facts["lines"] if r["id"] == x)["kind"] == "speech"]
        names = {x: final_speaker(facts, x)[1] for x in ids}
        still = [x for x in ids if _mentions(names[x], [c["from_name"]])]
        share = 1 - len(still) / max(1, len(ids))
        row["expect"] = f"{c['lines']}: no longer {c['from_name']}" + (f" ({c['note']})" if c.get("note") else "")
        row["got"] = f"{share:.0%} of {len(ids)} lines corrected; names now {sorted(set(str(v) for v in names.values()))[:5]}"
        row["status"] = "agree" if share >= float(c.get("min_share", 0.8)) else "disagree"
        return
    if kind == "speakers_seen_line":
        sh = _sheet_of_line(facts, c["line"])
        k, name = final_speaker(facts, c["line"])
        row["expect"] = f"{c['line']}: speakers_seen reports its speaker ({k})" + (f" ({c['note']})" if c.get("note") else "")
        if sh is None:
            row["status"], row["got"] = "missing", "the scene holding this line has no beat sheet yet"
            return
        r = next((x for x in facts["lines"] if x["id"] == c["line"]), {})
        ent = next((x for x in sh.get("speakers_seen") or [] if x.get("key") in (k, r.get("speaker_key"))), None)
        row["got"] = (f"on_screen={ent.get('on_screen')} matches_map={ent.get('matches_map')} who={ent.get('who')}: {str(ent.get('evidence'))[:90]}"
                      if ent else "no speakers_seen entry for this key")
        row["status"] = "agree" if ent else "disagree"
        return
    t = parse_ts(c["scene_containing"])[0] if c.get("scene_containing") else None
    sh = _sheet_containing(facts, t) if t is not None else None
    if kind == "non_dialogue_class":
        a, b = c["range"].split("-")
        a, b = parse_ts(a.strip())[0], parse_ts(b.strip())[0]
        tol = float(c.get("tol", 1.0))
        sh = _sheet_containing(facts, (a + b) / 2)
        row["expect"] = f"{c['range']} = {c['stretch_kind']}, recommend {c.get('recommend_any')}"
        if sh is None:
            row["status"], row["got"] = "missing", "no beat sheet for that stretch yet"
            return
        nd = next((x for x in sh.get("non_dialogue") or [] if abs(x["start"] - a) <= tol and abs(x["end"] - b) <= tol), None)
        if nd is None:
            near = [f"{x['range']} {x['kind']}/{x['recommend']}" for x in sh.get("non_dialogue") or []]
            row["status"], row["got"] = "disagree", f"no non_dialogue entry at that range; entries: {near[:6]}"
            return
        ok = nd["kind"] == c["stretch_kind"] and (not c.get("recommend_any") or nd["recommend"] in c["recommend_any"])
        row["got"] = f"{nd['range']} {nd['kind']} / {nd['recommend']}" + (f" {nd.get('target_s')} s" if nd.get("target_s") else "") + f": {str(nd.get('reason'))[:80]}"
        row["status"] = "agree" if ok else "disagree"
        return
    if sh is None:
        row["status"], row["got"] = "missing", f"no beat sheet for the scene containing {c.get('scene_containing')}"
        row["expect"] = f"{kind} in scene at {c.get('scene_containing')}"
        return
    if kind == "beat_payoff":
        po = sh.get("payoff") or {}
        rx = sh.get("reaction") or {}
        ok = po.get("line") == c["line"]
        got = f"payoff {po.get('line')} ({po.get('t_str')}) {str(po.get('text'))[:40]}; reaction {rx.get('type')} at {rx.get('t_str')}"
        if c.get("reaction_t") and ok:
            rt = parse_ts(c["reaction_t"])[0]
            ok = rx.get("t") is not None and abs(rx["t"] - rt) <= float(c.get("tol", 1.5))
        row["expect"] = f"scene {sh['scene_id']}: payoff {c['line']}" + (f", reaction near {c['reaction_t']}" if c.get("reaction_t") else "")
        row["got"], row["status"] = got, ("agree" if ok else "disagree")
    elif kind == "scene_register":
        reg = str(sh.get("register") or "")
        row["expect"] = f"scene {sh['scene_id']} register in {c['any']}"
        row["got"] = reg
        row["status"] = "agree" if any(_loose(x) == _loose(reg) or _loose(x) in _loose(reg) for x in c["any"]) else "disagree"
    elif kind == "dynamic_mentions":
        d = sh.get("dynamic") or {}
        blob = " ".join([str(d.get("relationship") or ""), str(d.get("reveals") or ""), " ".join(d.get("characters") or [])])
        row["expect"] = f"scene {sh['scene_id']} dynamic mentions one of {c['any']}"
        row["got"] = blob[:160]
        row["status"] = "agree" if _mentions(blob, c["any"]) else "disagree"
    elif kind == "speakers_seen_check":
        ent = next((x for x in sh.get("speakers_seen") or [] if x.get("key") == c["key"]), None)
        row["expect"] = f"scene {sh['scene_id']} key {c['key']}: " + ", ".join(f"{k}={c[k]}" for k in ("on_screen", "matches_map") if k in c)
        if ent is None:
            row["status"], row["got"] = "disagree", "no speakers_seen entry for that key"
            return
        ok = all(bool(ent.get(k)) == bool(c[k]) for k in ("on_screen", "matches_map") if k in c)
        row["got"] = f"on_screen={ent.get('on_screen')} matches_map={ent.get('matches_map')} who={ent.get('who')}: {str(ent.get('evidence'))[:90]}"
        row["status"] = "agree" if ok else "disagree"


# ----------------------------------------------------------------------------------------------- Stage 3
STAGE4_KINDS = {"vetoed", "scores_check", "sibling_verdicts", "fan_clip_or_standalone", "rejections_have_reasons"}
STAGE6_KINDS = {"final_set_shape", "final_moment", "final_any_of", "final_bonus_loops", "final_hook_text_mentions", "final_rules", "final_report_mentions",
                "final_golden_recall"}
STAGE5_KINDS = {"critic_verdict", "critic_record", "critic_hook_q5", "critic_all_recorded", "critic_moment_short_variant"}
STAGE3_KINDS = {"candidates_min", "candidate_exists", "candidates_end_near", "candidate_end_after_line", "range_in_candidates",
                "no_overlap_lines", "candidate_count", "no_boundary_inside_line"}


def _cands_in(facts: dict, c: dict) -> list[dict]:
    cands = facts.get("candidates") or []
    if c.get("scene_containing") is not None:
        sh = _sheet_containing(facts, parse_ts(c["scene_containing"])[0])
        if sh is None:
            return []
        cands = [x for x in cands if x["scene_id"] == sh["scene_id"]]
    if c.get("type"):
        cands = [x for x in cands if x["type"] == c["type"] or any(a.get("type") == c["type"] for a in x.get("also") or [])]
    if c.get("variant"):
        cands = [x for x in cands if x["variant"] == c["variant"] or any(a.get("variant") == c["variant"] for a in x.get("also") or [])]
    if c.get("trim_level"):
        cands = [x for x in cands if x.get("trim_level") == c["trim_level"]]
    return cands


def _span_of(facts: dict, lines_spec) -> tuple[float, float, list[str]]:
    all_ids = [r["id"] for r in facts["lines"]]
    ids, _ = expand_lines(lines_spec, all_ids) if isinstance(lines_spec, str) else (list(lines_spec), [])
    rows = [r for r in facts["lines"] if r["id"] in set(ids)]
    return min(r["start"] for r in rows), max(r["end"] for r in rows), ids


def _cand_desc(x: dict) -> str:
    return f"{x['id']} {x['type']}/{x['variant']} {x['start_str']}-{x['end_str']} ({x['final_duration']:.0f} s)"


def _eval_stage3(facts: dict, c: dict, row: dict) -> None:
    kind = c["kind"]
    if not facts.get("candidates"):
        row["status"], row["got"] = "missing", "no Stage 3 candidates yet"
        return
    tol = float(c.get("tol", 0.6))
    lid = {r["id"]: r for r in facts["lines"]}
    if kind == "candidates_min":
        cands = _cands_in(facts, c)
        if c.get("untrimmed"):
            cands_u = [x for x in cands if x.get("trim_level") == "none"]
        variants = {x["variant"] for x in cands}
        need = set(c.get("variants_include") or [])
        ok = len(cands) >= int(c["min"]) and need <= variants and (not c.get("untrimmed") or cands_u)
        row["expect"] = row["expect"] or f">= {c['min']} {c.get('type') or ''} candidates incl. {sorted(need)}"
        row["got"] = f"{len(cands)}: " + "; ".join(_cand_desc(x) for x in cands)
        row["status"] = "agree" if ok else "disagree"
    elif kind == "candidate_exists":
        cands = _cands_in(facts, c)
        hits = []
        for x in cands:
            ok = True
            if c.get("start_line"):
                r = lid.get(c["start_line"])
                ok &= r is not None and abs(x["start"] - r["start"]) <= tol + 0.35
            if c.get("start_near") is not None:
                ok &= abs(x["start"] - parse_ts(c["start_near"])[0]) <= tol
            if c.get("end_near") is not None:
                ok &= abs(x["end"] - parse_ts(c["end_near"])[0]) <= tol
            if c.get("end_near_any"):
                ok &= any(abs(x["end"] - parse_ts(t)[0]) <= tol for t in c["end_near_any"])
            if c.get("end_after") is not None:
                ok &= x["end"] >= parse_ts(c["end_after"])[0] - 1e-3
            if c.get("contains_line"):
                ok &= c["contains_line"] in x["lines"]
            if c.get("contains_range"):
                a, b = (parse_ts(t)[0] for t in c["contains_range"].split("-"))
                ok &= any(sa <= a + 0.05 and sb >= b - 0.05 for sa, sb in x["segments"])
            if ok:
                hits.append(x)
        row["got"] = ("found: " + "; ".join(_cand_desc(x) for x in hits[:4])) if hits else \
            ("none of " + "; ".join(_cand_desc(x) for x in cands[:6]) if cands else "no candidate in that scene / type")
        row["status"] = "agree" if hits else "disagree"
    elif kind == "candidates_end_near":
        cands = _cands_in(facts, c)
        t = parse_ts(c["t"])[0]
        bad = [x for x in cands if abs(x["end"] - t) > tol]
        row["got"] = f"{len(cands) - len(bad)} of {len(cands)} end within {tol} s of {c['t']}" + ("; off: " + "; ".join(_cand_desc(x) for x in bad) if bad else "")
        row["status"] = "agree" if cands and not bad else "disagree"
    elif kind == "candidate_end_after_line":
        cands = _cands_in(facts, c)
        hits = []
        for ln in (c.get("line_any") or [c["line"]]):
            r = lid[ln]
            hits += [x for x in cands if ln in x["lines"] and r["end"] - 1e-3 <= x["end"] <= r["end"] + float(c.get("within_s", 6)) + 1e-3 and x not in hits]
        row["got"] = ("found: " + "; ".join(_cand_desc(x) for x in hits[:4])) if hits else "no candidate contains the line and ends within the window; " + "; ".join(_cand_desc(x) for x in cands[:5])
        row["status"] = "agree" if hits else "disagree"
    elif kind == "range_in_candidates":
        a, b = (parse_ts(t)[0] for t in c["range"].split("-"))
        spanning = [x for x in facts["candidates"] if x["start"] <= a + 0.05 and x["end"] >= b - 0.05]
        kept = [x for x in spanning if any(sa <= a + 0.05 and sb >= b - 0.05 for sa, sb in x["segments"])]
        removed = [x for x in spanning if not any(sa < b and sb > a for sa, sb in x["segments"])]
        shortened = [x for x in spanning if any(abs(parse_ts(g["range"].split("-")[0])[0] - a) <= 0.2 for g in x["removed"]["gaps_shortened"])]
        exp = c["expect"]
        ok = {"kept": bool(spanning) and len(kept) == len(spanning), "shortened": bool(shortened) and not removed,
              "not_removed": bool(spanning) and not removed}.get(exp, False)
        row["got"] = f"{len(spanning)} spanning: {len(kept)} keep it whole, {len(shortened)} shorten it, {len(removed)} remove it"
        row["status"] = "agree" if ok else "disagree"
    elif kind == "no_overlap_lines":
        a, b, ids = _span_of(facts, c["lines"])
        unless = set(c.get("unless_variant_in") or [])
        bad = [x for x in facts["candidates"] if x["start"] < b and x["end"] > a and x["variant"] not in unless]
        row["got"] = "no overlapping candidate outside the allowed variants" if not bad else "overlap: " + "; ".join(_cand_desc(x) for x in bad)
        row["status"] = "agree" if not bad else "disagree"
    elif kind == "candidate_count":
        # counts base candidates (cap slots); a trimmed twin of a kept untrimmed candidate is not counted (founder, 2026-10-27)
        from .stage3_candidates import slot_key
        cands = facts["candidates"]
        n = len({slot_key(x) for x in cands})
        row["got"] = f"{n} slots (sibling families) over {len(cands)} candidates"
        row["status"] = "agree" if int(c.get("min", 0)) <= n <= int(c.get("max", 10**9)) else "disagree"
    elif kind == "no_boundary_inside_line":
        speech = [r for r in facts["lines"] if r["kind"] == "speech" and not r.get("zero_len")]
        bad = []
        for x in facts["candidates"]:
            for sa, sb in x["segments"]:
                for t in (sa, sb):
                    if any(r["start"] + 1e-3 < t < r["end"] - 1e-3 for r in speech):
                        bad.append(f"{x['id']} {fmt_ts(t)}")
        row["got"] = f"{sum(len(x['segments']) for x in facts['candidates'])} segment boundaries checked" + ("; inside a line: " + ", ".join(bad[:6]) if bad else "")
        row["status"] = "agree" if not bad else "disagree"


# ----------------------------------------------------------------------------------------------- Stage 4
def _scored(facts: dict) -> dict[str, dict]:
    return {s["id"]: s for s in ((facts.get("scores") or {}).get("candidates") or [])}


def _select4(facts: dict, c: dict) -> list[dict]:
    """Candidates matching the check's selectors: scene_containing / candidate_containing (a time inside the span),
    contains_line / lines (overlap), type / type_any (also-types count), variant, ends_before / ends_after_line_any."""
    cands = facts.get("candidates") or []
    lid = {r["id"]: r for r in facts["lines"]}
    out = []
    for x in cands:
        ok = True
        if c.get("scene_containing") is not None:
            sh = _sheet_containing(facts, parse_ts(c["scene_containing"])[0])
            ok &= sh is not None and x["scene_id"] == sh["scene_id"]
        if c.get("candidate_containing") is not None:
            t = parse_ts(c["candidate_containing"])[0]
            ok &= x["start"] - 1e-3 <= t <= x["end"] + 1e-3
        if c.get("contains_line"):
            ok &= c["contains_line"] in x["lines"]
        if c.get("lines"):
            a, b, _ = _span_of(facts, c["lines"])
            ok &= x["start"] < b and x["end"] > a
        types = [c["type"]] if c.get("type") else list(c.get("type_any") or [])
        if types:
            ok &= x["type"] in types or any(a.get("type") in types for a in x.get("also") or [])
        if c.get("variant"):
            ok &= x["variant"] == c["variant"]
        if c.get("ends_before") is not None:
            ok &= x["end"] <= parse_ts(c["ends_before"])[0] + 1e-3
        if c.get("ends_after_line_any"):
            ok &= any(ln in x["lines"] and lid[ln]["end"] - 1e-3 <= x["end"] <= lid[ln]["end"] + float(c.get("within_s", 8)) + 1e-3 for ln in c["ends_after_line_any"])
        if ok:
            out.append(x)
    return out


def _s_desc(x: dict, s: dict | None) -> str:
    if not s:
        return f"{x['id']} (not scored)"
    sc = s["scores"]
    dims = " ".join(f"{d[:4]}={sc[d]}" for d in ("hook", "payoff", "open_question", "loop_strength", "standalone", "pace", "delivery", "series_pull") if d in sc)
    vet = ",".join(v["rule"] for v in s["vetoes"]) or "-"
    return f"{x['id']} {x['type']}/{x['variant']} {x['start_str']}-{x['end_str']} [{dims}] {s['status']} vetoes={vet}"


def _eval_stage4(facts: dict, c: dict, row: dict) -> None:
    kind = c["kind"]
    scored = _scored(facts)
    if not scored:
        row["status"], row["got"] = "missing", "no Stage 4 scores yet"
        return
    sel = _select4(facts, c)
    pairs = [(x, scored.get(x["id"])) for x in sel]
    pairs_scored = [(x, s) for x, s in pairs if s]
    if kind == "vetoed":
        dim, mx = c["dimension"], int(c.get("max_score", 4))
        hits = [(x, s) for x, s in pairs_scored if any(v["dimension"] == dim and (v.get("score") or 0) <= mx for v in s["vetoes"])]
        want_all = bool(c.get("all"))
        ok = bool(hits) and (not want_all or len(hits) == len(pairs_scored))
        row["got"] = (f"{len(hits)} of {len(pairs_scored)} vetoed on {dim}: " + "; ".join(_s_desc(x, s) for x, s in (hits or pairs_scored)[:4])) if pairs_scored else "no matching scored candidate"
        row["status"] = "agree" if ok else "disagree"
    elif kind == "scores_check":
        mins, maxs = c.get("min") or {}, c.get("max") or {}
        def good(s):
            sc = s["scores"]
            if any(sc.get(d, 0) < v for d, v in mins.items()):
                return False
            if any(sc.get(d, 0) > v for d, v in maxs.items()):
                return False
            if c.get("not_vetoed") and s["vetoes"]:
                return False
            if c.get("status_any") and s["status"] not in c["status_any"]:
                return False
            return True
        hits = [(x, s) for x, s in pairs_scored if good(s)]
        ok = bool(hits) and (len(hits) == len(pairs_scored) if c.get("all") else True)
        row["got"] = (f"{len(hits)} of {len(pairs_scored)} satisfy min {mins} max {maxs}" + (" not vetoed" if c.get("not_vetoed") else "") + ": "
                      + "; ".join(_s_desc(x, s) for x, s in (hits if hits else pairs_scored)[:4])) if pairs_scored else "no matching scored candidate"
        row["status"] = "agree" if ok else "disagree"
    elif kind == "sibling_verdicts":
        twins = [(x, s) for x, s in pairs_scored if x.get("untrimmed_sibling") or x.get("trimmed_sibling")]
        bad = [x["id"] for x, s in twins if not str(s.get("verdict_vs_sibling") or "").strip()]
        row["got"] = f"{len(twins)} twin candidates, {len(twins) - len(bad)} with a verdict" + (f"; missing: {bad}" if bad else "")
        row["status"] = "agree" if twins and not bad else "disagree"
    elif kind == "fan_clip_or_standalone":
        mn = int(c.get("standalone_min", 6))
        hits = [(x, s) for x, s in pairs_scored if (s["fan_clip"] or s["scores"].get("standalone", 0) >= mn) and (not c.get("not_vetoed") or not s["vetoes"])]
        row["got"] = (f"{len(hits)} of {len(pairs_scored)}: " + "; ".join(f"{_s_desc(x, s)} fan_clip={s['fan_clip']}" for x, s in (hits or pairs_scored)[:4])) if pairs_scored else "no matching scored candidate"
        row["status"] = "agree" if hits else "disagree"
    elif kind == "rejections_have_reasons":
        rej = [s for s in scored.values() if s["status"] == "rejected"]
        known = {"payoff", "open_question", "loop_strength", "pace", "standalone"}
        bad = [s["id"] for s in rej if not s["vetoes"] or any(v["dimension"] not in known for v in s["vetoes"])]
        row["got"] = f"{len(rej)} rejected, every one with a veto from {sorted(known)}" if not bad else f"rejected without a listed veto: {bad}"
        row["status"] = "agree" if not bad else "disagree"


# ----------------------------------------------------------------------------------------------- Stage 5
def _critic_clips(facts: dict) -> dict[str, dict]:
    """Critic records by CURRENT candidate id (selection + archive matched by cut), see stage5_critic.judged_records."""
    from .stage5_critic import judged_records
    return judged_records(facts)


def _c_desc(z: dict) -> str:
    return f"{z['id']} {z['type']}/{z['variant']} {z['verdict']} {z['n_pass']}/{z['n_runs']} located {[round(t) for t in z['located_s']]} s expected {z['expected'].get('clip_start')}-{z['expected'].get('clip_end')} s"


def _eval_stage5(facts: dict, c: dict, row: dict) -> None:
    kind = c["kind"]
    clips = _critic_clips(facts)
    if not clips:
        row["status"], row["got"] = "missing", "no Stage 5 critic results yet"
        return
    sel = _select4(facts, c)
    if c.get("variant_any"):
        sel = [x for x in sel if x["variant"] in c["variant_any"]]
    have = [(x, clips[x["id"]]) for x in sel if x["id"] in clips]
    if kind == "critic_all_recorded":
        bad = [z["id"] for z in clips.values() if len(z["runs"]) != z["n_runs"] or z["usage"].get("cost_inr") is None]
        row["got"] = f"{len(clips)} clips, each with {next(iter(clips.values()))['n_runs']} recorded answers and a cost" if not bad else f"incomplete: {bad}"
        row["status"] = "agree" if clips and not bad else "disagree"
        return
    if kind == "critic_moment_short_variant":
        # founder, 2026-10-29: keyed by MOMENT (scene + payoff line), not by slot family (a full-arc family never holds a cold open)
        max_len = float(c.get("max_len_s", 45))
        cands = {x["id"]: x for x in facts.get("candidates") or []}
        moments: dict[tuple, list[dict]] = {}
        for z in clips.values():
            cz = cands.get(z["id"]) or {}
            moments.setdefault((z["scene_id"], cz.get("payoff_line")), []).append(z)
        missing = {m: min(zs, key=lambda z: z["final_duration"]) for m, zs in moments.items() if not any(z["final_duration"] <= max_len + 1e-6 for z in zs)}
        row["got"] = (f"{len(moments)} moments, each with a judged variant <= {max_len:.0f} s" if not missing
                      else f"{len(missing)} of {len(moments)} moments have no judged variant <= {max_len:.0f} s: "
                           + "; ".join(f"scene {m[0]} payoff {m[1]} (shortest judged {z['id']} {z['final_duration']:.0f} s)" for m, z in missing.items()))
        row["status"] = "agree" if moments and not missing else "disagree"
        return
    if not sel:
        row["status"], row["got"] = "disagree", "no candidate matches the selectors"; return
    if not have:
        row["status"], row["got"] = "missing", "matching candidate(s) not in the critic run: " + ", ".join(x["id"] for x in sel[:5]); return
    if kind == "critic_hook_q5":
        # the better of the matching hooks by Stage 4 combined must have Q5 = yes in >= min_runs runs (and PASS)
        s4 = {s["id"]: s for s in ((facts.get("scores") or {}).get("candidates") or [])}
        best = max(have, key=lambda xz: s4.get(xz[0]["id"], {}).get("combined", 0))
        x, z = best
        n_yes = sum(1 for w in z["want_next"] if w.get("yes"))
        ok = n_yes >= int(c.get("min_runs", 2)) and z["verdict"] == "PASS"
        row["got"] = f"best by Stage 4: {_c_desc(z)}; Q5 yes in {n_yes}/{z['n_runs']} runs"
        row["status"] = "agree" if ok else "disagree"
        return
    lid = {r["id"]: r for r in facts["lines"]}
    def check(x, z):
        ok, notes = True, []
        if c.get("verdict_any") and z["verdict"] not in c["verdict_any"]:
            ok = False; notes.append(f"verdict {z['verdict']}")
        if c.get("min_pass_runs") is not None and z["n_pass"] < int(c["min_pass_runs"]):
            ok = False; notes.append(f"{z['n_pass']}/{z['n_runs']} runs passed")
        if c.get("moment_near_line"):
            r = lid.get(c["moment_near_line"]); tmap = z["time_map"]
            from .preview import to_clip_time
            c0 = to_clip_time(tmap, r["start"]) if r else None; c1 = to_clip_time(tmap, r["end"]) if r else None
            tol = float(c.get("tol", 3.0))
            n_ok = sum(1 for t in z["located_s"] if c0 is not None and c0 - tol <= t <= (c1 if c1 is not None else c0) + tol)
            if n_ok < int(c.get("min_runs", 2)):
                ok = False; notes.append(f"moment near {c['moment_near_line']} in {n_ok}/{z['n_runs']} runs")
        if c.get("q6_positive_min_runs") is not None:
            n = sum(1 for w in z["series_pull"] if w.get("yes"))
            if n < int(c["q6_positive_min_runs"]):
                ok = False; notes.append(f"Q6 positive in {n}/{z['n_runs']}")
        if c.get("no_missing_min_runs") is not None:
            n = sum(1 for w in z["confusion"] if w.get("missing") in ("none", "minor"))
            if n < int(c["no_missing_min_runs"]):
                ok = False; notes.append(f"no missing context in {n}/{z['n_runs']}")
        return ok, notes
    if kind == "critic_verdict":
        results = [(x, z, *check(x, z)) for x, z in have]
        hits = [r for r in results if r[2]]
        row["got"] = "; ".join(f"{_c_desc(z)}" + (f" [{', '.join(n)}]" if n else "") for x, z, ok, n in results[:4])
        row["status"] = "agree" if hits and (not c.get("all") or len(hits) == len(results)) else "disagree"
    elif kind == "critic_record":
        x, z = have[0]
        words = [w.lower() for w in (c.get("if_fail_q7_mentions") or [])]
        q7 = " ".join((w.get("what") or "") for w in z["confusion"]).lower()
        ok = True
        if z["verdict"] == "FAIL" and words and not any(w in q7 for w in words):
            ok = False
        row["got"] = f"recorded: {_c_desc(z)}; Q7: " + " | ".join((w.get('what') or '')[:60] for w in z["confusion"])
        row["status"] = "agree" if ok else "disagree"


def summary(rows: list[dict]) -> dict:
    out = {"agree": 0, "disagree": 0, "missing": 0, "error": 0}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
    out["total"] = len(rows)
    return out


# ----------------------------------------------------------------------------------------------- Stage 6
def _final_moments(facts: dict, where: str = "any") -> list[dict]:
    f = facts.get("final") or {}
    return (f.get("main") or []) * (where in ("any", "main")) + (f.get("bonus") or []) * (where in ("any", "bonus"))


def _final_matches(facts: dict, m: dict, c: dict) -> bool:
    lid = {r["id"]: r for r in facts["lines"]}
    vs = [m["primary"]] + list(m.get("alternates") or [])
    if c.get("scene_containing") is not None:
        sh = _sheet_containing(facts, parse_ts(c["scene_containing"])[0])
        if sh is None or sh["scene_id"] != m["scene_id"]:
            return False
    if c.get("type") and m["primary"]["type"] != c["type"]:
        return False
    if c.get("type_any") and m["primary"]["type"] not in c["type_any"]:
        return False
    if c.get("moment_kind") and m["kind"] != c["moment_kind"]:      # "kind" is the check's own kind
        return False
    if c.get("payoff_line") and m["payoff_line"] != c["payoff_line"]:
        return False
    if c.get("contains_line"):
        r = lid.get(c["contains_line"])
        if r is None or not any(a - 1e-3 <= r["start"] and r["end"] <= b + 1e-3 for v in vs for a, b in v["segments"]):
            return False
    return True


def _f_desc(m: dict) -> str:
    p = m["primary"]
    return (f"{m.get('id')} scene {m['scene_id']} {m['kind']} primary {p['id']} {p['variant']} {p['final_duration']:.0f} s {p['verdict']} {p['n_pass']}/{p['n_runs']}"
            + (f", alternates {[a['id'] + ' ' + str(round(a['final_duration'])) + ' s' for a in m.get('alternates') or []]}" if m.get("alternates") else ""))


def golden_recall(facts: dict, moments: list[dict], gs: dict | None = None, full_lines_min: float = 0.8, partial_share_min: float = 0.5) -> list[dict]:
    """Per golden moment of this episode: ``full`` when a main primary keeps >= full_lines_min of its speech lines and ends
    after its last line; ``partial`` when >= partial_share_min of a primary's kept time lies inside the golden span (a piece
    of that moment); else ``missed``. Golden moments come from data/eval/golden_set.yaml (lines / cut_lines when given)."""
    if gs is None:
        from .eval import golden_set as G
        gs = G.load()
    ep = (gs.get("episodes") or {}).get((facts.get("episode") or {}).get("video_id")) or {}
    ids = [r["id"] for r in facts["lines"]]; lid = {r["id"]: r for r in facts["lines"]}
    out = []
    for gm in ep.get("moments") or []:
        src = next((x for x in gm.get("sources") or [] if x.get("lines")), {})
        if src.get("lines") and all(x in lid for x in src["lines"]):
            a, b = src["lines"]
            span = ids[ids.index(a):ids.index(b) + 1]
            if src.get("cut_lines"):
                ca, cb = src["cut_lines"]; cut = set(ids[ids.index(ca):ids.index(cb) + 1])
                span = [x for x in span if x not in cut]
            last = lid[b]
        else:
            span = [r["id"] for r in facts["lines"] if gm["start"] - 0.05 <= r["start"] and r["end"] <= gm["end"] + 0.05]
            last = lid[span[-1]] if span else None
        speech = [x for x in span if lid[x]["kind"] == "speech"]
        lo, hi = (min(lid[x]["start"] for x in span), max(lid[x]["end"] for x in span)) if span else (gm["start"], gm["end"])
        best = {"id": gm.get("id"), "label": gm.get("label"), "status": "missed", "post": None, "clip": None}
        for m in moments:
            p = m["primary"]; segs = [(float(x), float(y)) for x, y in p["segments"]]
            inside = lambda r: any(x - 1e-3 <= r["start"] and r["end"] <= y + 1e-3 for x, y in segs)
            share = sum(1 for x in speech if inside(lid[x])) / len(speech) if speech else 0.0
            ends = last is not None and any(x - 0.05 <= last["start"] and last["end"] <= y + 0.3 for x, y in segs)
            kept = sum(y - x for x, y in segs)
            in_span = sum(max(0.0, min(y, hi) - max(x, lo)) for x, y in segs) / kept if kept else 0.0
            status = "full" if share >= full_lines_min and ends else ("partial" if in_span >= partial_share_min else None)
            if status and (best["status"] != "full"):
                if status == "full" or best["status"] == "missed":
                    best.update({"status": status, "post": m.get("id"), "clip": p["id"], "lines_share": round(share, 2), "in_span": round(in_span, 2)})
        out.append(best)
    return out


def _eval_stage6(facts: dict, c: dict, row: dict) -> None:
    kind = c["kind"]
    f = facts.get("final")
    if not f:
        row["status"], row["got"] = "missing", "no Stage 6 output yet"
        return
    main, bonus = f.get("main") or [], f.get("bonus") or []
    if kind == "final_golden_recall":
        rec = golden_recall(facts, main, c.get("_golden_set"), float(c.get("full_lines_min", 0.8)), float(c.get("partial_share_min", 0.5)))
        n = sum(1 for x in rec if x["status"] != "missed")
        row["got"] = f"{n} of {len(rec)} in the main set: " + "; ".join(f"{x['id']} {x['status']}" + (f" ({x['post']} {x['clip']})" if x["post"] else "") for x in rec)
        row["status"] = "agree" if rec and n >= int(c.get("min", 4)) else "disagree"
        return
    if kind == "final_set_shape":
        shipped = [v for m in main + bonus for v in [m["primary"]] + list(m.get("alternates") or [])]
        fails = [v["id"] for v in shipped if v["verdict"] == "FAIL" and v.get("reason_class") != "pace"]
        long_ = [m["primary"]["id"] for m in main if m["primary"]["final_duration"] > float(c.get("primary_max_s", 60)) + 1e-6]
        # founder, 2026-10-30: length is never judged but the platform maximum; the length spread is reported
        lens = sorted(round(m["primary"]["final_duration"]) for m in main)
        ok = len(main) == int(c.get("main", 6)) and len(bonus) <= int(c.get("bonus_max", 2)) and not fails and not long_
        row["got"] = (f"{len(main)} main, {len(bonus)} bonus; confusion FAIL shipped: {fails or 'none'}; main primaries over {c.get('primary_max_s', 60)} s: {long_ or 'none'}"
                      + (f"; primary lengths {lens} s (min {lens[0]}, median {lens[len(lens) // 2]}, max {lens[-1]})" if lens else ""))
        row["status"] = "agree" if ok else "disagree"
        return
    if kind == "final_rules":
        scenes = [m["scene_id"] for m in main]
        dup = sorted({s for s in scenes if scenes.count(s) > int(c.get("max_per_scene", 1))})
        dup += [f"{a['id']}~{b['id']}" for i, a in enumerate(main) for b in main[i + 1:]
                if float(a["start"]) < float(b["end"]) - 1e-3 and float(b["start"]) < float(a["end"]) - 1e-3]
        n = sum(1 for m in main if (m.get("series_pull") or 0) >= float(c.get("series_pull_min", 7)))
        row["got"] = f"scenes {scenes} (over {c.get('max_per_scene', 1)} per scene or overlapping: {dup or 'none'}); {n} main moments with series_pull >= {c.get('series_pull_min', 7)}"
        row["status"] = "agree" if not dup and n >= int(c.get("count", 2)) else "disagree"
        return
    if kind == "final_report_mentions":
        md = f.get("rejection_report_md") or ""
        words = [w.lower() for w in c.get("words_any") or ["swipe", "best moment"]]
        got, ok = [], True
        for t in c.get("scenes") or []:
            sh = _sheet_containing(facts, parse_ts(t)[0])
            sid = sh["scene_id"] if sh else None
            secs = [blk for blk in re.split(r"\n(?=### )", md) if re.search(rf"(scene {sid}\b|Scene {sid}\b)", blk)]
            hit = any(any(w in blk.lower() for w in words) for blk in secs)
            got.append(f"scene {sid}: {len(secs)} section(s){', with critic reasons' if hit else ', NO critic reasons'}")
            ok = ok and bool(secs) and hit
        row["got"] = "; ".join(got)
        row["status"] = "agree" if ok else "disagree"
        return
    if kind == "final_bonus_loops":
        hit = [m for m in bonus if not c.get("contains_line") or _final_matches(facts, m, {"contains_line": c["contains_line"]})]
        row["got"] = f"bonus: {[_f_desc(m) for m in bonus] or 'none'}"
        row["status"] = "agree" if len(bonus) >= int(c.get("min", 2)) and hit else "disagree"
        return
    if kind == "final_any_of":
        pool = _final_moments(facts, c.get("in", "main"))
        hits = [m for m in pool for o in c.get("options") or [] if _final_matches(facts, m, o)]
        row["got"] = "matching: " + ("; ".join(_f_desc(m) for m in hits) if hits else "none") + f" (set: {[m['primary']['id'] for m in pool]})"
        row["status"] = "agree" if hits else "disagree"
        return
    pool = [m for m in _final_moments(facts, c.get("in", "any")) if _final_matches(facts, m, c)]
    if not pool:
        row["status"], row["got"] = "disagree", "no moment of the set matches the selectors"
        return
    m = pool[0]
    if kind == "final_hook_text_mentions":
        words = [w.lower() for w in c.get("words") or []]
        ht = (m.get("hook_text") or "")
        ok = any(w in ht.lower() for w in words)
        row["got"] = f"{_f_desc(m)}; hook_text \"{ht}\"; needs_caption_context {m.get('needs_caption_context')}"
        row["status"] = "agree" if ok else "disagree"
        return
    if kind == "final_moment":
        bad = []
        p = m["primary"]
        if c.get("primary_variant_any") and p["variant"] not in c["primary_variant_any"]:
            bad.append(f"primary variant {p['variant']}")
        if c.get("alternate_longer") and not any(a["final_duration"] > p["final_duration"] for a in m.get("alternates") or []):
            bad.append("no longer alternate")
        if c.get("cta_target") and m.get("cta_target") != c["cta_target"]:
            bad.append(f"cta_target {m.get('cta_target')}")
        if c.get("series_pull_min") is not None and (m.get("series_pull") or 0) < float(c["series_pull_min"]):
            bad.append(f"series_pull {m.get('series_pull')}")
        row["got"] = _f_desc(m) + (f" · cta {m.get('cta_target')} · series_pull {m.get('series_pull')}") + (f" · problems: {bad}" if bad else "")
        row["status"] = "agree" if not bad else "disagree"
        return
    row["status"], row["got"] = "error", f"unknown Stage 6 kind {kind}"
