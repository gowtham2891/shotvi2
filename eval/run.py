"""Evaluate a pipeline run.

usage: python -m eval.run --run runs/<id> [--judge] [--judge-top 10] [--out runs/eval_reports/<id>]

Reads every proposals file under the run dir (kind == "proposals", one per video) and, when present,
runs/<id>/cost.json. Writes report.md and metrics.json to runs/eval_reports/<id>/ (eval never writes inside the
pipeline's run dir) and prints the report.

Without --judge only cached judge verdicts are used (no API calls). With --judge the top K proposals per video
(default 10) are judged, within the judge budget (eval/judge.py).

Metric definitions: see eval/metrics.py (coverage, match threshold, tightening) and the legend in the report.
"""
import argparse
import json
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

from . import metrics as M
from .common import (ROOT, clip_excerpt, clips_of, edited_duration, episode_for, fmt_t, find_run_files,
                     load_json, reference_files, write_json)
from .judge import JUDGE_MODEL, PROMPT_VERSION, judge_many, spent
from .schema_check import check_doc

KS = (1, 3, 5, 10, 20)
JUDGE_KS = (3, 5, 10)


def _mean(x):
    return sum(x) / len(x) if x else float("nan")


def f2(x):
    return "n/a" if x is None or x != x else f"{x:.2f}"


def pct(a, b):
    return f"{a}/{b} ({a / b:.0%})" if b else "0/0"


def eval_video(path, doc, judge=False, judge_top=10):
    ep = episode_for(doc)
    vid = doc["video_id"]
    props = clips_of(doc)
    anchors_doc, gold_doc = reference_files(vid)
    out = {"video_id": vid, "file": str(path), "n_proposals": len(props),
           "audio_s": ep.duration, "tiers": dict(Counter(p.get("tier") for p in props))}
    # schema + structural
    out["schema_errors"] = check_doc(doc)
    teaser = M.TeaserIndex(ep)
    per = []
    for p in props:
        ch = M.clip_checks(p, ep, "proposals", teaser)
        exp = M.expected_flags(ch)
        declared = set(p.get("flags") or [])
        per.append({"id": p["id"], "rank": p["rank"], "checks": ch,
                    "missing_flags": sorted(exp - declared)})
    out["checks"] = per
    out["file_checks"] = M.file_checks(doc, ep)
    out["near_dups"] = {k: M.near_dups(props, k) for k in (5, 10)}
    out["lengths"] = [edited_duration(p, ep) for p in props]
    # recall + tightening
    for name, rdoc in (("anchors", anchors_doc), ("gold", gold_doc)):
        if not rdoc:
            continue
        targets = clips_of(rdoc)
        rows = M.match_targets(props, targets, ep)
        curve = M.rank_curve(props, targets, ep)
        tl = []
        for r in rows:
            if r["rank"] is not None and (name != "gold" or r["target"].get("tightening_labeled", True)):
                prop = props[r["rank"] - 1]
                t = M.tightening(prop, r["target"], ep)
                t.update({"target": r["target"]["id"], "prop": prop["id"], "rank": r["rank"]})
                tl.append(t)
        # Shorts the mapper could not place are still Shorts the creator published: they count as not found
        unmapped = (rdoc.get("unmapped") or []) if name == "anchors" else []
        out[name] = {
            "n": len(targets) + len(unmapped),
            "n_unmapped": len(unmapped),
            "curve": {str(k): v for k, v in curve.items()},
            "targets": [{"id": r["target"]["id"], "rank": r["rank"], "best_rank": r["best_rank"],
                         "best_cov": round(r["best_cov"], 3),
                         "gold_overall": (r["target"].get("scores") or {}).get("overall"),
                         "excerpt": clip_excerpt(r["target"], ep, 10),
                         "at": fmt_t(ep.W[r["target"]["pieces"][0]["start_word"]]["start"])} for r in rows]
                       + [{"id": u["short_id"], "rank": None, "best_rank": None, "best_cov": 0.0, "gold_overall": None,
                           "excerpt": f"(unmapped: {u.get('reason', '')})", "at": "-"} for u in unmapped],
            "tightening": tl,
        }
    # judge
    top = props[:judge_top] if judge_top else props
    recs = judge_many([(p, ep) for p in top], offline=not judge)
    jl = []
    for p, r in zip(top, recs):
        if r:
            s = r["result"]
            jl.append({"id": p["id"], "rank": p["rank"], "tier": p.get("tier"), "scorer": p.get("scores"),
                       "judge": {k: s[k] for k in ("hook", "standalone", "payoff", "value_emotion", "overall",
                                                   "publishable", "padded", "meaning_changed", "garbled_join",
                                                   "starts_mid_thought", "ends_mid_thought", "critique")},
                       "usd": r["usage"]["usd"]})
    out["judge"] = jl
    out["props"] = [{"id": p["id"], "rank": p["rank"], "tier": p.get("tier"),
                     "overall": (p.get("scores") or {}).get("overall"), "title": p.get("title"),
                     "edited_s": round(edited_duration(p, ep), 1),
                     "at": fmt_t(ep.W[p["pieces"][0]["start_word"]]["start"]),
                     "excerpt": clip_excerpt(p, ep, 10),
                     "pieces": [[x["start_word"], x["end_word"]] for x in p["pieces"]]} for p in props]
    out["cost"] = doc.get("cost")
    return out


def read_cost(run_dir, videos):
    cp = Path(run_dir) / "cost.json"
    src = "cost.json"
    agg = defaultdict(float)
    by_role = defaultdict(lambda: defaultdict(float))
    if cp.exists():
        c = load_json(cp)
        if isinstance(c.get("videos"), dict):          # shotvi.select layout: {videos: {vid: cost_block}, total}
            entries = list(c["videos"].values())
        elif "usd" in c:
            entries = [c]
        else:
            entries = [v for v in c.values() if isinstance(v, dict) and "usd" in v]
    else:
        src = "proposal files"
        entries = [v["cost"] for v in videos if v.get("cost")]
    for e in entries:
        for k in ("input_tokens", "output_tokens", "usd", "wall_time_secs", "n_llm_calls",
                  "usd_billed_this_run", "n_cache_hits", "thought_tokens"):
            agg[k] += float(e.get(k) or 0)
        for role, rc in (e.get("by_role") or {}).items():
            for k in ("usd", "n_llm_calls", "input_tokens", "output_tokens"):
                if isinstance(rc, dict):
                    by_role[role][k] += float(rc.get(k) or 0)
    hours = sum(v["audio_s"] for v in videos) / 3600
    return {"source": src, **agg, "audio_hours": hours, "by_role": {r: dict(v) for r, v in by_role.items()},
            "usd_per_audio_hour": agg["usd"] / hours if hours else None,
            "wall_s_per_audio_hour": agg["wall_time_secs"] / hours if hours else None}


def summarize(videos):
    s = {}
    for name in ("anchors", "gold"):
        vs = [v[name] for v in videos if name in v]
        if not vs:
            continue
        n = sum(x["n"] for x in vs)
        curve = {}
        for thr in vs[0]["curve"]:
            curve[thr] = {str(k): sum(x["curve"][thr][k if k in x["curve"][thr] else int(k)] for x in vs)
                          for k in list(KS) + ["all"]}
        tl = [t for x in vs for t in x["tightening"]]
        s[name] = {"n": n, "curve": curve,
                   "tightening": {k: _mean([t[k] for t in tl]) for k in
                                  ("raw_p", "raw_r", "raw_f1", "zone_p", "zone_r", "zone_f1", "zone_keepall_f1",
                                   "cut_p", "cut_r", "cut_f1", "zone_cut_share_target")},
                   "len_ratio_median": st.median([t["len_ratio"] for t in tl]) if tl else None,
                   "n_pairs": len(tl)}
        if name == "gold":
            strong = [t for x in vs for t in x["targets"] if (t["gold_overall"] or 0) >= 4]
            s[name]["strong_n"] = len(strong)
            s[name]["strong_found_at_10"] = sum(1 for t in strong if t["rank"] and t["rank"] <= 10)
            s[name]["strong_found_all"] = sum(1 for t in strong if t["rank"])
    # structural
    rej = Counter()
    flg = Counter()
    n_prop = 0
    n_rej_clips = 0
    for v in videos:
        for c in v["checks"]:
            n_prop += 1
            rules = [(r, a) for r, a, _ in c["checks"]]
            if any(a in ("reject", "hide") for _, a in rules):
                n_rej_clips += 1
            for r, a in rules:
                (rej if a in ("reject", "hide") else flg)[r] += 1
    s["structural"] = {"n_proposals": n_prop, "clips_failing_reject_rules": n_rej_clips,
                       "reject_by_rule": dict(rej), "flag_by_rule": dict(flg),
                       "schema_errors": sum(len(v["schema_errors"]) for v in videos),
                       "near_dups_top5": sum(len(v["near_dups"][5]) for v in videos),
                       "near_dups_top10": sum(len(v["near_dups"][10]) for v in videos),
                       "missing_flags": sum(len(c["missing_flags"]) for v in videos for c in v["checks"])}
    L = [x for v in videos for x in v["lengths"]]
    s["length"] = {"n": len(L), "min": min(L) if L else None, "median": st.median(L) if L else None,
                   "max": max(L) if L else None,
                   "buckets": dict(Counter("<30" if x < 30 else "30-60" if x < 60 else "60-90" if x < 90 else
                                           "90-120" if x < 120 else ">120" for x in L))}
    # judge
    J = {}
    for k in JUDGE_KS:
        rows = [j for v in videos for j in v["judge"] if j["rank"] <= k]
        J[str(k)] = {"n": len(rows), "mean_overall": _mean([j["judge"]["overall"] for j in rows]),
                     "publishable": sum(j["judge"]["publishable"] for j in rows),
                     "strong": sum(j["judge"]["publishable"] and j["judge"]["overall"] >= 4 for j in rows)}
    for tier in ("recommended", "worth_a_look", "below_threshold"):
        rows = [j for v in videos for j in v["judge"] if j["tier"] == tier]
        J["tier_" + tier] = {"n": len(rows), "publishable": sum(j["judge"]["publishable"] for j in rows)}
    rows = [j for v in videos for j in v["judge"] if j.get("scorer")]
    if len(rows) >= 3:
        from .calibrate import spearman
        J["scorer_vs_judge_spearman"] = spearman([j["scorer"]["overall"] for j in rows],
                                                 [j["judge"]["overall"] for j in rows])
        J["scorer_minus_judge"] = _mean([j["scorer"]["overall"] - j["judge"]["overall"] for j in rows])
    s["judge"] = J
    return s


def render(run_dir, meta, videos, s, cost):
    L = [f"# Eval report: `{meta['run_id']}`", ""]
    L.append(f"Pipeline {meta.get('version')} | models: {meta.get('models')} | created {meta.get('created_at')} | "
             f"judge {JUDGE_MODEL} ({PROMPT_VERSION}) | videos: {', '.join(v['video_id'] for v in videos)}")
    L.append("")
    tiers = Counter()
    for v in videos:
        tiers.update(v["tiers"])
    L.append(f"**{s['structural']['n_proposals']} proposals shown** ({dict(tiers)}); edited length "
             f"{f2(s['length']['min'])}–{f2(s['length']['max'])}s, median {f2(s['length']['median'])}s; "
             f"buckets {s['length']['buckets']}.")
    L.append("")
    # recall
    L.append("## Recall vs anchors (match = ≥ 50% of the target's kept words inside a proposal's block spans)")
    L.append("")
    L.append("| target set | n | " + " | ".join(f"@{k}" for k in KS) + " | any rank | @all, thr 0.3 | @all, thr 0.7 |")
    L.append("|---|---|" + "---|" * (len(KS) + 3))
    for name, label in (("anchors", "creator Shorts"), ("gold", "gold picks")):
        if name not in s:
            continue
        c = s[name]["curve"]
        L.append(f"| {label} | {s[name]['n']} | " + " | ".join(str(c['0.5'][str(k)]) for k in KS) +
                 f" | {c['0.5']['all']} | {c['0.3']['all']} | {c['0.7']['all']} |")
    if "gold" in s:
        g = s["gold"]
        L.append("")
        L.append(f"Gold rated strong (overall ≥ 4): {g['strong_found_at_10']}/{g['strong_n']} found in the top 10 "
                 f"per video, {g['strong_found_all']}/{g['strong_n']} at any rank.")
    L.append("")
    # tightening
    L.append("## Tightening agreement on matched pairs")
    L.append("")
    L.append("| vs | pairs | raw kept P / R / F1 | zone kept P / R / F1 | zone keep-all F1 | "
             "zone CUT P / R / F1 | their cut share in zone | median length ratio (ours / theirs) |")
    L.append("|---|---|---|---|---|---|---|---|")
    for name in ("anchors", "gold"):
        if name in s:
            t = s[name]["tightening"]
            L.append(f"| {name} | {s[name]['n_pairs']} | {f2(t['raw_p'])} / {f2(t['raw_r'])} / {f2(t['raw_f1'])} | "
                     f"{f2(t['zone_p'])} / {f2(t['zone_r'])} / {f2(t['zone_f1'])} | {f2(t['zone_keepall_f1'])} | "
                     f"{f2(t['cut_p'])} / {f2(t['cut_r'])} / {f2(t['cut_f1'])} | {f2(t['zone_cut_share_target'])} | "
                     f"{f2(s[name]['len_ratio_median'])} |")
    L.append("")
    L.append("Creators' edits are one valid edit, not the only one. Kept-word F1 is dominated by the words everyone "
             "keeps (keep-all already scores high), so the cut-word F1 is the sharper tightening signal "
             "(keep-all scores 0 on it).")
    L.append("")
    # judge
    J = s["judge"]
    L.append(f"## Precision by merit (LLM judge {JUDGE_MODEL}, {PROMPT_VERSION}; see runs/eval_calibration/judge-v1/report.md and judge-v2_subset/report.md)")
    L.append("")
    if any(J[str(k)]["n"] for k in JUDGE_KS):
        L.append("| top-k per video | clips judged | mean judge overall | publishable | strong |")
        L.append("|---|---|---|---|---|")
        for k in JUDGE_KS:
            j = J[str(k)]
            L.append(f"| {k} | {j['n']} | {f2(j['mean_overall'])} | {pct(j['publishable'], j['n'])} | "
                     f"{pct(j['strong'], j['n'])} |")
        L.append("")
        L.append("By tier (spec Q3 targets: ≥ 80% of recommended and ≥ 60% of worth_a_look publishable): " +
                 "; ".join(f"{t}: {pct(J['tier_' + t]['publishable'], J['tier_' + t]['n'])}"
                           for t in ("recommended", "worth_a_look", "below_threshold") if J["tier_" + t]["n"]))
        if "scorer_vs_judge_spearman" in J:
            L.append(f"Scorer vs judge overall: Spearman {f2(J['scorer_vs_judge_spearman'])}, mean scorer − judge "
                     f"{J['scorer_minus_judge']:+.2f}.")
    else:
        L.append("No judge verdicts (run with --judge).")
    L.append("")
    # structural
    S = s["structural"]
    L.append("## Structural checks (code only, spec v0.2)")
    L.append("")
    L.append(f"- Schema/derived-field errors: {S['schema_errors']}.")
    L.append(f"- Proposals failing a reject/hide rule: {pct(S['clips_failing_reject_rules'], S['n_proposals'])}; "
             f"by rule {S['reject_by_rule'] or '{}'}.")
    L.append(f"- Flags raised by code: {S['flag_by_rule'] or '{}'}; code flags missing from the proposal's "
             f"`flags`: {S['missing_flags']}.")
    L.append(f"- Near-duplicates (Q4, > 50% shared kept words): {S['near_dups_top5']} pairs in top 5, "
             f"{S['near_dups_top10']} in top 10.")
    for v in videos:
        for e in v["schema_errors"][:5]:
            L.append(f"  - {v['video_id']} schema: {e}")
        for r, a, d in v["file_checks"]:
            L.append(f"  - {v['video_id']} {r} ({a}): {d}")
        for c in v["checks"]:
            for r, a, d in c["checks"]:
                if a in ("reject", "hide"):
                    L.append(f"  - {v['video_id']} rank {c['rank']} ({c['id']}) {r}: {d}")
    L.append("")
    # cost
    L.append("## Cost and latency")
    L.append("")
    if cost.get("usd") or cost.get("n_llm_calls"):
        L.append(f"From {cost['source']}: ${cost['usd']:.3f} for {cost['audio_hours']:.2f} h of audio = "
                 f"**${f2(cost['usd_per_audio_hour'])} per audio hour**; wall {cost['wall_time_secs']:.0f}s "
                 f"({f2(cost['wall_s_per_audio_hour'])} s per audio hour); {int(cost['n_llm_calls'])} LLM calls, "
                 f"{int(cost['input_tokens'])} in / {int(cost['output_tokens'])} out tokens.")
        if cost.get("n_cache_hits"):
            L.append(f"(This run hit the LLM cache {int(cost['n_cache_hits'])} times; `usd` is the full uncached "
                     f"cost, billed this run ${cost.get('usd_billed_this_run', 0):.3f}. Wall time with cache hits "
                     "understates production latency.)")
        if cost.get("by_role"):
            L.append("By role: " + "; ".join(f"{r} ${v.get('usd', 0):.3f} ({int(v.get('n_llm_calls', 0))} calls)"
                                            for r, v in sorted(cost["by_role"].items(), key=lambda x: -x[1].get("usd", 0))))
    else:
        L.append("No cost recorded (fixture run or missing cost.json).")
    jusd = sum(j["usd"] for v in videos for j in v["judge"])
    L.append(f"Judge cost for this report's verdicts: ${jusd:.3f} (cached calls count once).")
    L.append("")
    # per video detail
    for v in videos:
        L.append(f"## {v['video_id']}")
        L.append("")
        for name, label in (("anchors", "Creator Shorts"), ("gold", "Gold picks")):
            if name not in v:
                continue
            L.append(f"{label}: found {sum(1 for t in v[name]['targets'] if t['rank'])}/{v[name]['n']}.")
            L.append("")
            L.append("| target | at | rank | best coverage | excerpt |")
            L.append("|---|---|---|---|---|")
            for t in v[name]["targets"]:
                g = f" (gold {t['gold_overall']:g})" if t.get("gold_overall") else ""
                L.append(f"| {t['id']}{g} | {t['at']} | {t['rank'] or '-'} | {t['best_cov']:.2f} (rank {t['best_rank']}) | "
                         f"{t['excerpt'][:140]} |")
            L.append("")
        jmap = {j["id"]: j["judge"] for j in v["judge"]}
        L.append("Top proposals:")
        L.append("")
        L.append("| rank | tier | scorer | judge | len | at | title / excerpt |")
        L.append("|---|---|---|---|---|---|---|")
        for p in v["props"][:10]:
            j = jmap.get(p["id"])
            js = f"{j['overall']:g}{'' if j['publishable'] else ' (not pub.)'}" if j else "-"
            L.append(f"| {p['rank']} | {p['tier']} | {p['overall']} | {js} | {p['edited_s']}s | {p['at']} | "
                     f"{p['title']} / {p['excerpt'][:110]} |")
        L.append("")
    L.append("## Legend")
    L.append("")
    L.append("- Block spans: pieces split at jumps (backward, or skip > 30s); each block's [min, max] word range.")
    L.append("- Coverage(target by proposal) = share of the target's kept words inside the proposal's block spans. "
             "Found = coverage ≥ 0.5; rank = best rank among proposals that reach it. @k counts targets found at "
             "rank ≤ k (per video).")
    L.append("- Tightening: word-level P/R/F1 of our kept words vs theirs, raw and inside the overlap zone "
             "(intersection of both clips' block spans). Keep-all F1 = F1 of keeping every word in the zone.")
    L.append("- Publishable (judge): overall ≥ 3, hook/standalone/payoff ≥ 3, no meaning change, not intro/promo, "
             "15–150s. Strong = publishable and overall ≥ 4 (labeling guide).")
    return "\n".join(L)


def evaluate(run_dir, judge=False, judge_top=10, out=None):
    run_dir = Path(run_dir)
    files = find_run_files(run_dir)
    if not files:
        raise SystemExit(f"no proposals files under {run_dir}")
    videos = [eval_video(p, d, judge, judge_top) for p, d in files]
    d0 = files[0][1]
    meta = {"run_id": d0.get("run_id", run_dir.name), "version": (d0.get("pipeline") or {}).get("version"),
            "models": [f"{m['role']}={m['model']}" for m in d0.get("models") or []],
            "created_at": d0.get("created_at")}
    s = summarize(videos)
    cost = read_cost(run_dir, videos)
    text = render(run_dir, meta, videos, s, cost)
    outd = Path(out) if out else ROOT / "runs" / "eval_reports" / run_dir.name
    outd.mkdir(parents=True, exist_ok=True)
    (outd / "report.md").write_text(text, encoding="utf-8")
    slim = [{k: v for k, v in x.items() if k != "checks"} | {"checks": [
        {"id": c["id"], "rank": c["rank"], "checks": c["checks"], "missing_flags": c["missing_flags"]}
        for c in x["checks"]]} for x in videos]
    write_json(outd / "metrics.json", {"meta": meta, "summary": s, "cost": cost, "videos": slim})
    return text, outd


def main(argv=None):
    from .common import utf8_stdout
    utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--judge", action="store_true", help="call the judge on the top K per video (costs money)")
    ap.add_argument("--judge-top", type=int, default=10)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    run_dir = Path(a.run)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    text, outd = evaluate(run_dir, a.judge, a.judge_top, a.out)
    print(text)
    print(f"\n[written to {outd}; judge spend so far ${spent():.2f}]")


if __name__ == "__main__":
    main()
