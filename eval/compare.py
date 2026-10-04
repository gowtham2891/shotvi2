"""Compare two evaluated runs.

usage: python -m eval.compare runs/<a> runs/<b> [--judge]

Re-evaluates both runs (code metrics + cached judge verdicts; --judge lets it call the judge on the top 10)
and writes their runs/eval_reports/<id>/ outputs. Prints a short markdown report with metric deltas and notable wins/losses (targets found or lost, big
rank moves, new/dropped top-10 proposals with judge verdicts and excerpts) and writes it to
runs/eval_reports/compare_<a>_vs_<b>.md.

Verdict rule of thumb (stated in the report): with 19 creator Shorts and 55 gold clips, a recall change of
1-2 targets is noise; a judge top-k change needs >= 0.3 mean overall or >= 2 clips' publishable verdicts on the
same k to be worth talking about (judge test-retest noise: runs/eval_calibration/judge-v1/report.md).
"""
import argparse
from pathlib import Path

from .common import ROOT, load_json
from .run import JUDGE_KS, KS, evaluate, f2, pct


def load_metrics(run_dir, judge):
    run_dir = Path(run_dir)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    # re-evaluate every time: code-only metrics take seconds and cached judge verdicts cost nothing
    evaluate(run_dir, judge=judge)
    return load_json(ROOT / "runs" / "eval_reports" / run_dir.name / "metrics.json")


def d(a, b, fmt="{:+.2f}"):
    if a is None or b is None or a != a or b != b:
        return "n/a"
    return fmt.format(b - a)


def compare(A, B):
    na, nb = A["meta"]["run_id"], B["meta"]["run_id"]
    sa, sb = A["summary"], B["summary"]
    L = [f"# Compare `{na}` (A) → `{nb}` (B)", ""]
    L.append("| metric | A | B | Δ |")
    L.append("|---|---|---|---|")
    for name, label in (("anchors", "creator Shorts"), ("gold", "gold")):
        if name in sa and name in sb:
            for k in (3, 5, 10, "all"):
                a, b = sa[name]["curve"]["0.5"][str(k)], sb[name]["curve"]["0.5"][str(k)]
                L.append(f"| recall@{k} {label} (n={sa[name]['n']}) | {a} | {b} | {b - a:+d} |")
            ta, tb = sa[name]["tightening"], sb[name]["tightening"]
            L.append(f"| cut-word F1 vs {label} (pairs {sa[name]['n_pairs']} → {sb[name]['n_pairs']}) | "
                     f"{f2(ta['cut_f1'])} | {f2(tb['cut_f1'])} | {d(ta['cut_f1'], tb['cut_f1'])} |")
            L.append(f"| median length ratio vs {label} | {f2(sa[name]['len_ratio_median'])} | "
                     f"{f2(sb[name]['len_ratio_median'])} | {d(sa[name]['len_ratio_median'], sb[name]['len_ratio_median'])} |")
    for k in JUDGE_KS:
        ja, jb = sa["judge"][str(k)], sb["judge"][str(k)]
        if ja["n"] != jb["n"]:
            L.append(f"| judge top {k}: NOT COMPARABLE (judged {ja['n']} vs {jb['n']} clips; run with --judge) | "
                     f"{f2(ja['mean_overall'])} | {f2(jb['mean_overall'])} | n/a |")
            continue
        L.append(f"| judge mean overall, top {k} (n {ja['n']} → {jb['n']}) | {f2(ja['mean_overall'])} | "
                 f"{f2(jb['mean_overall'])} | {d(ja['mean_overall'], jb['mean_overall'])} |")
        L.append(f"| judge publishable, top {k} | {pct(ja['publishable'], ja['n'])} | {pct(jb['publishable'], jb['n'])} | "
                 f"{jb['publishable'] - ja['publishable']:+d} |")
    xa, xb = sa["structural"], sb["structural"]
    L.append(f"| proposals shown | {xa['n_proposals']} | {xb['n_proposals']} | {xb['n_proposals'] - xa['n_proposals']:+d} |")
    L.append(f"| failing a reject rule | {xa['clips_failing_reject_rules']} | {xb['clips_failing_reject_rules']} | "
             f"{xb['clips_failing_reject_rules'] - xa['clips_failing_reject_rules']:+d} |")
    L.append(f"| near-dups in top 10 | {xa['near_dups_top10']} | {xb['near_dups_top10']} | "
             f"{xb['near_dups_top10'] - xa['near_dups_top10']:+d} |")
    L.append(f"| median edited length (s) | {f2(sa['length']['median'])} | {f2(sb['length']['median'])} | "
             f"{d(sa['length']['median'], sb['length']['median'], '{:+.1f}')} |")
    ca, cb = A["cost"], B["cost"]
    L.append(f"| $ per audio hour | {f2(ca.get('usd_per_audio_hour'))} | {f2(cb.get('usd_per_audio_hour'))} | "
             f"{d(ca.get('usd_per_audio_hour'), cb.get('usd_per_audio_hour'))} |")
    L.append(f"| wall s per audio hour | {f2(ca.get('wall_s_per_audio_hour'))} | {f2(cb.get('wall_s_per_audio_hour'))} | "
             f"{d(ca.get('wall_s_per_audio_hour'), cb.get('wall_s_per_audio_hour'), '{:+.0f}')} |")
    L.append("")
    # wins / losses
    L.append("## Notable wins and losses")
    L.append("")
    va = {v["video_id"]: v for v in A["videos"]}
    vb = {v["video_id"]: v for v in B["videos"]}
    wins, losses, moves = [], [], []
    for vid in sorted(set(va) & set(vb)):
        for name in ("anchors", "gold"):
            if name not in va[vid] or name not in vb[vid]:
                continue
            ta = {t["id"]: t for t in va[vid][name]["targets"]}
            for t in vb[vid][name]["targets"]:
                a = ta.get(t["id"])
                if not a:
                    continue
                ra, rb = a["rank"], t["rank"]
                tag = f"{vid} {name[:-1] if name == 'anchors' else 'gold'} **{t['id']}** ({t['at']}): {t['excerpt'][:120]}"
                if ra is None and rb is not None:
                    wins.append(f"- found in B at rank {rb} (cov {t['best_cov']:.2f}): {tag}")
                elif ra is not None and rb is None:
                    losses.append(f"- lost in B (was rank {ra}; best cov now {t['best_cov']:.2f}): {tag}")
                elif ra and rb and abs(ra - rb) >= 3:
                    moves.append((-abs(ra - rb), f"- rank {ra} → {rb}: {tag}"))
    L += ["Targets found in B only:", ""] + (wins or ["- none"]) + [""]
    L += ["Targets lost in B:", ""] + (losses or ["- none"]) + [""]
    moves = [m for _, m in sorted(moves, key=lambda x: x[0])]
    L += [f"Rank moves ≥ 3 ({len(moves)}; largest 12 shown):", ""] + (moves[:12] or ["- none"]) + [""]
    # top-10 churn with judge verdicts
    L.append("Top-10 churn (by overlap of kept words, not id):")
    L.append("")
    for vid in sorted(set(va) & set(vb)):
        pa = va[vid]["props"][:10]
        pb = vb[vid]["props"][:10]
        ja = {j["id"]: j["judge"] for j in va[vid]["judge"]}
        jb = {j["id"]: j["judge"] for j in vb[vid]["judge"]}
        def same(p, others):
            k = {j for a, b in p["pieces"] for j in range(a, b + 1)}
            for o in others:
                ko = {j for a, b in o["pieces"] for j in range(a, b + 1)}
                if len(k & ko) > 0.5 * min(len(k), len(ko)):
                    return True
            return False
        for p in pb:
            if not same(p, pa):
                j = jb.get(p["id"])
                L.append(f"- {vid} NEW in B top 10, rank {p['rank']} ({p['edited_s']}s, judge "
                         f"{j['overall'] if j else '-'}{'' if not j or j['publishable'] else ', not publishable'}): "
                         f"{p['title']} / {p['excerpt'][:120]}")
        for p in pa:
            if not same(p, pb):
                j = ja.get(p["id"])
                L.append(f"- {vid} DROPPED from top 10 (was rank {p['rank']}, judge "
                         f"{j['overall'] if j else '-'}): {p['title']} / {p['excerpt'][:120]}")
    L.append("")
    L.append("Noise guide: with 19 creator Shorts and 55 gold clips, ±1–2 targets of recall is within noise. See "
             "runs/eval_calibration/judge-v1/report.md (section 4) for judge test-retest noise before reading small judge deltas.")
    return "\n".join(L)


def main(argv=None):
    from .common import utf8_stdout
    utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--judge", action="store_true")
    a = ap.parse_args(argv)
    A = load_metrics(a.a, a.judge)
    B = load_metrics(a.b, a.judge)
    text = compare(A, B)
    out = ROOT / "runs" / "eval_reports" / f"compare_{Path(a.a).name}_vs_{Path(a.b).name}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n[written to {out}]")


if __name__ == "__main__":
    main()
