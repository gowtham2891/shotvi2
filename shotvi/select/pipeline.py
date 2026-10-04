"""End-to-end clip selection for one transcript: discover -> tighten -> score -> (rescore) -> rank."""
from __future__ import annotations

import re
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from shotvi.llm import BudgetExceeded, LLMError

from .clip import Candidate, code_checks, hook_span, kept_words, metrics
from .context import load_ctx
from .discover import discover, discover_extra, merge_moments
from .edit import review_feedback, tighten
from .ending import fix_ending, wants_ending_fix
from .joins import check_joins, over_soft_budget
from .opener import fix_openers
from .brief import brief_text, build_brief
from .rank import (TIER_LEVEL, apply_groups, interest_scores, link_themes, near_duplicates, order_with_interest,
                   rank_episode, sort_key, tier_of)
from .score import calibrated_scores, score, write

TELUGU = range(0x0C00, 0x0C80)
EMOJI = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D#]")


def apply_judge(ctx, cfg, c: Candidate, j: dict) -> None:
    """Turn scorer output into scores, flags and reject/hide reasons (spec sections 2, 6, 7, 11)."""
    c.judge = j
    # reset judge-derived state (a rescore replaces the first judge)
    c.reject = [r for r in c.reject if not r.startswith(("T2:", "T3:", "T4:", "T5:", "A1:", "A3:", "A4:", "R1:"))
                or not r.endswith("(judge)")]
    c.hidden = [h for h in c.hidden if not h.startswith("C5:")]
    for f in ("under_tightened", "sensitive_content", "crude_language"):
        c.flags.discard(f)
    s, cap_notes = calibrated_scores(j, caps=cfg.get("score_caps", True))
    c.info["score_caps"] = cap_notes
    floor = cfg["thresholds"]["min_part_score"]
    weakest = min(s["hook"], s["standalone"], s["payoff"])
    if weakest <= floor:
        s["overall"] = min(s["overall"], weakest)
        c.hidden.append(f"C5: hook/standalone/payoff {s['hook']}/{s['standalone']}/{s['payoff']} has a part <= {floor}")
    c.scores = s
    if j.get("meaning_changed"):
        c.reject.append(f"T2: edit changes meaning: {j.get('meaning_note', '')[:150]} (judge)")
    if j.get("garbled_join"):
        c.reject.append("T3: garbled join at an inner cut (judge)")
    if "rough_start" in c.flags and j.get("starts_mid_thought"):
        c.reject.append("T4: starts mid-thought (judge)")
    if "rough_end" in c.flags and j.get("ends_mid_thought"):
        c.reject.append("T5: ends mid-thought (judge)")
    dq = j.get("disqualifier", "none")
    if dq != "none":
        rule = {"intro_outro": "A4", "promo_sponsor": "A3", "censored_payoff": "A1", "montage": "R1"}.get(dq, "A4")
        c.reject.append(f"{rule}: {dq} (judge)")
    if j.get("padded"):
        c.flags.add("under_tightened")
    if j.get("sensitive"):
        c.flags.add("sensitive_content")
    if j.get("crude"):
        c.flags.add("crude_language")
    c.tags = ["relatable"] if j.get("relatable") else []


def needs_revision(cfg, c: Candidate) -> bool:
    """A judge-found, fixable problem on a moment with enough value to be worth a second cut."""
    r = cfg.get("revise") or {}
    if not r.get("enabled") or not c.judge or not c.pieces:
        return False
    if c.judge.get("disqualifier", "none") in ("intro_outro", "promo_sponsor", "censored_payoff"):
        return False
    if float(c.judge.get("value_emotion", 0)) < r["min_value"]:
        return False
    judge_rejects = [x for x in c.reject if x.endswith("(judge)")]
    code_rejects = [x for x in c.reject if not x.endswith("(judge)")]
    if code_rejects:
        return False
    fixable = bool(judge_rejects) or any(h.startswith("C5:") for h in c.hidden)
    if r.get("revise_below_show", True):
        fixable = fixable or c.scores["overall"] < cfg["thresholds"]["t_show"]
    return fixable


def wants_opener_fix(cfg, c: Candidate) -> bool:
    o = cfg.get("opener_fix") or {}
    if not o.get("enabled") or not c.judge or not c.pieces or c.reject and not all(
            r.startswith("T4:") for r in c.reject):
        return False
    return (c.judge.get("opener_problem", "none") != "none"
            and float(c.judge.get("value_emotion", 0)) >= o.get("min_value", 3.5))


def _better(a: Candidate, b: Candidate, ties_to_new: bool = False) -> Candidate:
    """Prefer the version that passes the rules, then the higher overall (ties go to ``b`` if ties_to_new)."""
    def key(c):
        ok = bool(c.pieces and c.scores and not c.reject and not c.hidden)
        return (ok, c.scores["overall"] if c.scores else 0.0)
    return b if key(b) > key(a) or (ties_to_new and key(b) == key(a)) else a


def evaluate(ctx, cfg, llm, c: Candidate) -> None:
    """Score with the scorer; if a rescore model is configured and the clip survives, the stronger model's review
    replaces it (the first review is kept in judge_first)."""
    if not c.pieces or c.reject:
        return
    apply_judge(ctx, cfg, c, score(ctx, cfg, llm, c))
    rs_min = cfg["thresholds"]["rescore_min_overall"]
    if cfg["models"].get("rescore") and not c.reject and not c.hidden and c.scores["overall"] >= rs_min:
        first = c.judge
        apply_judge(ctx, cfg, c, score(ctx, cfg, llm, c, role="rescore"))
        c.judge_first = first
        c.info["judged_by"] = "rescore"


def process_moment(ctx, cfg, llm, moment: dict, cid: str) -> tuple[Candidate, dict]:
    trace: dict = {"moment": moment}
    try:
        cand, t = tighten(ctx, cfg, llm, moment, cid)
        trace["tighten"] = t
        if (cfg.get("joins") or {}).get("enabled"):
            cand, trace["joins"] = check_joins(ctx, cfg, llm, cand)
        evaluate(ctx, cfg, llm, cand)
        if wants_opener_fix(cfg, cand) and over_soft_budget(cfg, llm):
            trace["opener_fix_skipped"] = "soft budget"
        elif wants_opener_fix(cfg, cand):
            news, tr = fix_openers(ctx, cfg, llm, cand)
            trace["opener_fix"] = tr
            tr["results"] = []
            for new in news:
                if new.reject:
                    continue
                jt = None
                if (cfg.get("joins") or {}).get("enabled"):
                    new, jt = check_joins(ctx, cfg, llm, new)
                if new.reject:
                    continue
                evaluate(ctx, cfg, llm, new)
                best = _better(cand, new)
                tr["results"].append({"mode": new.info.get("opener_fix"), "scores": new.scores,
                                      "kept": best is new, "joins": jt})
                cand = best
        if wants_ending_fix(cfg, cand) and over_soft_budget(cfg, llm, cfg["ending_fix"].get("soft_frac", 0.85)):
            trace["ending_fix_skipped"] = "soft budget"
        elif wants_ending_fix(cfg, cand):
            new, tr = fix_ending(ctx, cfg, llm, cand)
            trace["ending_fix"] = tr
            if new is not None:
                evaluate(ctx, cfg, llm, new)
                best = _better(cand, new, ties_to_new=True)
                tr["kept"] = best is new
                tr["scores"] = new.scores
                tr["last_words"] = (new.judge or {}).get("last_words")
                cand = best
        if needs_revision(cfg, cand):
            fb = review_feedback(t["attempts"][-1], cand.judge, cand)
            cand2, t2 = tighten(ctx, cfg, llm, moment, cid, feedback=fb)
            trace["revision"] = t2
            evaluate(ctx, cfg, llm, cand2)
            cand2.info["revised"] = True
            cand2.info["first_cut"] = {"pieces": cand.pieces, "scores": cand.scores, "reject": cand.reject,
                                       "hidden": cand.hidden}
            best = _better(cand, cand2)
            trace["revision_kept"] = best is cand2
            cand = best
        return cand, trace
    except BudgetExceeded:
        raise
    except LLMError as e:
        cand = Candidate(cid, moment, [])
        cand.reject.append(f"error: {str(e)[:200]}")
        trace["error"] = traceback.format_exc(limit=3)
        return cand, trace


def span(ctx, a: int, b: int) -> dict:
    ws = ctx.words
    return {"start_word": a, "end_word": b, "start": round(ws[a].start, 3), "end": round(ws[b].end, 3)}


def one_line(s: str, n: int = 200) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def clip_core(ctx, c: Candidate) -> dict:
    m = metrics(ctx, c.pieces)
    r0, r1 = m["region"]
    return {
        "id": c.id,
        "pieces": [span(ctx, a, b) for a, b in c.pieces],
        "region": span(ctx, r0, r1),
        "edited_duration": round(sum(ctx.words[b].end - ctx.words[a].start for a, b in c.pieces), 3),
        "reordered": m["reordered"],
        "n_blocks": m["n_blocks"],
        "text": " ".join(ctx.words[i].text for i in kept_words(c.pieces)),
    }


def proposal(ctx, cfg, c: Candidate, rank: int) -> dict:
    j = {**(c.judge or {}), **(c.write or {})}
    hs, he = hook_span(ctx, c.pieces)
    title = one_line(EMOJI.sub("", j.get("title") or ""), 100)
    if not any(ord(ch) in TELUGU for ch in title):  # schema: title must contain Telugu script
        title = one_line(ctx.text(hs, he), 100)
    risks = [{"type": r["type"], "note": one_line(r["note"])} for r in j.get("standalone_risks", []) if r.get("note")]
    d = {**clip_core(ctx, c), "rank": rank, "tier": c.info.get("final_tier") or tier_of(cfg, c),
         "scores": {k: round(v, 2) for k, v in c.scores.items()},
         "title": title,
         "hook_line": {"text": ctx.text(hs, he), "start_word": hs, "end_word": he},
         "why_it_works": one_line(j.get("why_it_works") or "n/a"),
         "category": j.get("category", "other"),
         "standalone_risks": risks,
         "edit_notes": c.edit_notes,
         "flags": sorted(c.flags)}
    if c.related:
        d["related"] = c.related
    for cl in c.info.get("claims") or []:
        d["standalone_risks"].append({"type": "other", "note": one_line(
            f"Flag for creator review ({cl.get('kind')} claim): {cl.get('claim', '')} | web: {cl.get('web_note', '')}")})
    if c.info.get("interest"):
        d["interest"] = {"score": c.info["interest"]["score"], "reason": one_line(c.info["interest"]["reason"])}
        # NOTE for product-lead: optional field, not yet in clip_proposal.schema.json (the schema allows extra keys)
    if c.moment.get("interest_reason"):
        d["discovery"] = {"pass": "extra", "interest_reason": one_line(c.moment["interest_reason"])}
    if c.tags:
        d["tags"] = c.tags
    return d


def _attach_claims(ctx, shown) -> None:
    """Editor's notes claims_to_flag (health / finance / legal claims made in the episode, with web notes) whose unit
    ids overlap a shown clip: flag sensitive_content and add a creator-review note. Never changes the cut or score."""
    claims = ((getattr(ctx, "notes_res", None) or {}).get("notes") or {}).get("claims_to_flag") or []
    if not claims:
        return
    for c in shown:
        units = {ctx.seg.unit_of_word(i).id for a, b in c.pieces for i in (a, b)}
        lo, hi = min(units), max(units)
        hits = [cl for cl in claims if any(lo <= u <= hi for u in cl.get("unit_ids") or [])]
        if hits:
            c.flags.add("sensitive_content")
            c.info["claims"] = hits


def _extra_moments(ctx, cfg, llm, moments, log) -> list[dict]:
    e = cfg.get("extra_discovery") or {}
    if not e.get("enabled") or not getattr(ctx, "notes_text", ""):
        return []
    try:
        new = discover_extra(ctx, cfg, llm, moments, ctx.notes_text, cap=e.get("cap", 8))
    except LLMError as err:
        log(f"[{ctx.video_id}] extra discovery failed: {err}")
        return []
    log(f"[{ctx.video_id}] extra discovery (editor's notes): {len(new)} new moments")
    return new


def load_previous(ctx, run_dir: str | Path) -> tuple[list[Candidate], dict]:
    """Rebuild a previous run's candidates exactly as it judged them (ranking-derived state is dropped)."""
    import json
    from .context import file_sha256
    vid = ctx.video_id
    doc = json.loads((Path(run_dir) / f"{vid}.json").read_text(encoding="utf-8"))
    dbg = json.loads((Path(run_dir) / "debug" / f"{vid}.json").read_text(encoding="utf-8"))
    if doc["transcript"]["sha256"] != file_sha256(ctx.path):
        raise ValueError(f"{run_dir}: transcript sha256 differs; cannot reuse its word indices")
    scores = {p["id"]: p.get("scores") for p in doc["proposals"] + (doc.get("rejected") or [])}
    moments = {m["id"]: m for m in dbg.get("moments", [])}
    out = []
    for cid, d in dbg["candidates"].items():
        info = {k: v for k, v in (d.get("info") or {}).items()
                if k not in ("final_tier", "rank_reason", "ranker_tier")}
        c = Candidate(cid, moments.get(d.get("moment_id"), {"id": d.get("moment_id")}),
                      [list(p) for p in d.get("pieces") or []], edit_notes=list(d.get("edit_notes") or []),
                      flags=set(d.get("flags") or []) - {"similar_theme"}, reject=list(d.get("reject") or []),
                      info=info)
        c.judge, c.judge_first = d.get("judge"), d.get("judge_first")
        c.scores = scores.get(cid)
        c.hidden = [h for h in d.get("hidden") or [] if h.startswith("C5:")]
        out.append(c)
    return out, dbg


def run_video(path: str | Path, cfg: dict, llm, log=print) -> dict:
    ctx = load_ctx(path, cfg)
    dur = ctx.seg.transcript.duration
    log(f"[{ctx.video_id}] {ctx.n} words, {dur / 60:.1f} min, excluded {ctx.excluded_ranges}")
    # verified web guest profile (runs/guest/<id>.json, built by `python -m shotvi.select.guest`): WRITER only. Not the
    # brief: the ranker reads the brief, and the profile must stay out of ranking (no "famous = better" bias)
    ctx.guest_text, ctx.notes_text = "", ""
    if cfg.get("editor_notes"):
        from .notes import load_notes, notes_text
        nres = load_notes(ctx.video_id)
        ctx.notes_res = nres
        ctx.notes_text = notes_text(nres)           # second discovery pass + interest (with interest topics)
        ctx.guest_text = notes_text(nres, interest=False)   # writer: names and angles, not interest claims
    if not ctx.guest_text and cfg.get("guest_profile"):
        from .guest import load_profile, profile_text
        ctx.guest_text = profile_text(load_profile(ctx.video_id))
    brief = {}
    if cfg["models"].get("brief"):
        try:
            brief = build_brief(ctx, cfg, llm)
        except LLMError as e:
            log(f"[{ctx.video_id}] episode brief failed: {e}")
    ctx.brief_text = brief_text(brief) if brief else ""
    src = cfg.get("reuse_from")
    if isinstance(src, dict):
        src = src.get(ctx.video_id)
    if src:
        # episode-level steps only: candidates (cuts, judge output, scores, flags, notes) come unchanged from a
        # previous run; brief, near-duplicates, ranker and writer run on top of them
        cands, prev = load_previous(ctx, src)
        traces = {c.id: {"reused_from": src} for c in cands}
        raw, chunk_info, moments = prev.get("raw_moments", []), prev.get("chunks", []), prev.get("moments", [])
        ms = list(moments)
        log(f"[{ctx.video_id}] reusing {len(cands)} candidates from {src}")
        new_ms = _extra_moments(ctx, cfg, llm, moments, log)
        if new_ms:
            with ThreadPoolExecutor(max_workers=cfg["concurrency"]) as ex:
                res2 = list(ex.map(lambda m: process_moment(ctx, cfg, llm, m, "c" + m["id"][1:]), new_ms))
            cands += [c for c, _ in res2]
            traces.update({c.id: t for c, t in res2})
            ms += new_ms
            moments = moments + new_ms
    else:
        raw, chunk_info = discover(ctx, cfg, llm)
        moments = merge_moments(raw, cfg)
        ms = [m for m in moments if m["strength"] >= cfg["discovery"]["min_strength"]]
        if cfg.get("only_moments"):
            ms = [m for m in ms if m["id"] in set(cfg["only_moments"])]
        if cfg.get("max_moments"):
            ms = sorted(ms, key=lambda m: -m["strength"])[: cfg["max_moments"]]
        log(f"[{ctx.video_id}] discovery: {len(raw)} nominations -> {len(moments)} moments -> {len(ms)} to edit")
        new_ms = _extra_moments(ctx, cfg, llm, moments, log)
        ms += new_ms
        moments = moments + new_ms

        def work(m):
            return process_moment(ctx, cfg, llm, m, "c" + m["id"][1:])

        # strongest nominations first: if the soft budget trips, the weakest moments lose the optional steps
        ms = sorted(ms, key=lambda m: (-m["strength"], m["start_unit"]))
        with ThreadPoolExecutor(max_workers=cfg["concurrency"]) as ex:
            results = list(ex.map(work, ms))
        cands = [c for c, _ in results]
        traces = {c.id: t for c, t in results}

    passed = [c for c in cands if c.pieces and not c.reject and c.scores]
    eligible = [c for c in passed if not c.hidden]
    near_duplicates(ctx, cfg, eligible)
    eligible = [c for c in eligible if not c.hidden]
    t = cfg["thresholds"]
    warnings = []
    merged = cfg.get("merged_write", False)
    if merged:  # score v9 writes title / why / category / risks in the judge call
        for c in eligible:
            c.write = {k: (c.judge or {}).get(k) for k in ("title", "why_it_works", "category", "standalone_risks")}
    ranking = {"order": [], "groups": []}
    if cfg["models"].get("ranker") and eligible:
        try:
            ranking = rank_episode(ctx, cfg, llm, eligible)
        except LLMError as e:
            warnings.append(f"episode ranker failed ({str(e)[:80]}); score order used")
    if ranking["order"]:
        pos = {o["id"]: k for k, o in enumerate(ranking["order"])}
        rk = {o["id"]: o for o in ranking["order"]}
        shown = []
        for c in eligible:
            o = rk.get(c.id, {})
            c.info["rank_reason"] = o.get("reason")
            c.info["ranker_tier"] = o.get("tier")
            code = tier_of(cfg, c)
            if o.get("tier") == "hide":
                c.hidden.append(f"RANK: hidden by the episode ranker: {str(o.get('reason', ''))[:120]}")
                continue
            if code == "below_threshold":
                continue
            lvl = min(TIER_LEVEL[code], TIER_LEVEL.get(o.get("tier") or code, TIER_LEVEL[code]))
            c.info["final_tier"] = "recommended" if lvl == 2 else "worth_a_look"
            shown.append(c)
        if cfg.get("order") == "score_interest":
            shown.sort(key=lambda c: (-TIER_LEVEL[c.info["final_tier"]],) + tuple(sort_key(ctx, c)))
        else:
            shown.sort(key=lambda c: (-TIER_LEVEL[c.info["final_tier"]], pos.get(c.id, 1 << 30)))
    else:
        shown = sorted([c for c in eligible if c.scores["overall"] >= t["t_show"]], key=lambda c: sort_key(ctx, c))
    if not shown and eligible:
        pool = [c for c in eligible if not any(h.startswith("RANK:") for h in c.hidden)] or eligible
        shown = sorted(pool, key=lambda c: sort_key(ctx, c))[: t["below_threshold_max"]]
        for c in shown:
            c.info["final_tier"] = "below_threshold"
        warnings.append(f"Q6: nothing cleared t_show={t['t_show']}; showing {len(shown)} best as below_threshold")
    for c in eligible:
        if c not in shown and not c.hidden:
            c.hidden.append(f"Q1: overall {c.scores['overall']} < t_show {t['t_show']}")

    def do_write(c):
        try:
            c.write = write(ctx, cfg, llm, c)
        except LLMError as e:
            c.write = {"title": "", "why_it_works": c.info.get("point") or "n/a", "category": "other",
                       "standalone_risks": []}
            traces[c.id]["write_error"] = str(e)[:300]
    if not merged:
        with ThreadPoolExecutor(max_workers=cfg["concurrency"]) as ex:
            list(ex.map(do_write, shown))
    if cfg.get("order") == "score_interest" and shown:
        if not ranking["order"]:
            for c in shown:
                c.info.setdefault("final_tier", tier_of(cfg, c))
        try:
            scores = interest_scores(ctx, cfg, llm, eligible, getattr(ctx, "notes_text", ""))
        except LLMError as e:
            scores = {}
            warnings.append(f"interest call failed ({str(e)[:80]}); score order only")
        for c in eligible:
            if c.id in scores:
                c.info["interest"] = scores[c.id]
        shown = order_with_interest(sorted(shown, key=lambda c: (-TIER_LEVEL[c.info["final_tier"]],)
                                           + tuple(sort_key(ctx, c))),
                                    lambda c: c.info["final_tier"], cfg["thresholds"].get("interest_max_gap", 0.5))
    _attach_claims(ctx, shown)
    if ranking["order"]:
        apply_groups(shown, ranking["groups"])
        theme_obj = {"groups": ranking["groups"], "order": ranking["order"], "source": "ranker"}
    else:
        try:
            theme_obj = link_themes(ctx, cfg, llm, shown)
        except LLMError as e:
            theme_obj = {"groups": [], "error": str(e)[:200]}
    per_min = len(shown) / max(dur / 60, 1e-6)
    if per_min > t["warn_clips_per_min"]:
        warnings.append(f"Q7: {len(shown)} shown clips in {dur / 60:.0f} min (> 1 per 2 min): scorer may be lenient")

    proposals = [proposal(ctx, cfg, c, k + 1) for k, c in enumerate(shown)]
    rejected = []
    for c in cands:
        if c in shown or not c.pieces:
            continue
        reasons = c.reject + c.hidden
        if not reasons:
            reasons = ["unknown"]
        r = {**clip_core(ctx, c), "reject_reasons": reasons}
        if c.scores:
            r["scores"] = {k: round(v, 2) for k, v in c.scores.items()}
        rejected.append(r)
    debug = {
        "chunks": chunk_info, "raw_moments": raw, "moments": moments, "excluded_ranges": ctx.excluded_ranges,
        "themes": theme_obj,
        "candidates": {c.id: {"moment_id": c.moment.get("id"), "pieces": c.pieces, "reject": c.reject,
                              "hidden": c.hidden, "flags": sorted(c.flags), "info": c.info, "judge": c.judge,
                              "judge_first": c.judge_first, "write": c.write, "edit_notes": c.edit_notes,
                              "metrics": {k: v for k, v in metrics(ctx, c.pieces).items() if k != "durs"}
                              if c.pieces else None} for c in cands},
        "traces": traces,
    }
    return {"ctx": ctx, "proposals": proposals, "rejected": rejected, "shown": shown, "cands": cands,
            "n_moments": len(ms), "n_passed": len(passed), "warnings": warnings, "debug": debug}
