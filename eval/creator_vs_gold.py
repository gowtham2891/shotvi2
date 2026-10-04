"""Creator Shorts (data/anchors) vs strategist gold (data/gold): coverage, what gold adds, tightening styles.

usage: python -m eval.creator_vs_gold [--out runs/eval_creator_vs_gold]

Uses cached judge verdicts only (run python -m eval.calibrate first to fill them); never calls the API.
"""
import argparse
import statistics as st
from collections import Counter

from . import metrics as M
from .common import ROOT, clip_excerpt, clips_of, edited_duration, episode_for, fmt_t, reference_files, region_span_s
from .judge import judge_clip

VIDEOS = ("FS1DI5D_hUk", "TBlm6wcij94")


def med(x):
    return st.median(x) if x else float("nan")


def run(out):
    L = ["# Creator Shorts vs gold picks", ""]
    L.append("Gold was picked from the transcript without opening the creator Shorts (gold header `independence`). "
             "Coverage uses the same definition as eval.run: a Short is covered when ≥ 50% of its kept words lie "
             "inside a gold clip's block spans.")
    L.append("")
    tot = Counter()
    pairs_all, gold_only_all, gold_overlap_all, creator_all = [], [], [], []
    for vid in VIDEOS:
        A, G = reference_files(vid)
        ep = episode_for(G)
        anchors, gold = clips_of(A), clips_of(G)
        for u in A.get("unmapped") or []:   # published but unmappable: counts as not covered
            tot["n_anchor"] += 1
            L.append(f"- Short {u['short_id']} could not be mapped ({u.get('reason')}); counted as not covered.")
        L.append(f"## {vid}: {len(anchors)} creator Shorts, {len(gold)} gold clips")
        L.append("")
        L.append("| Short | at | edited s | best gold | cov (Short by gold) | cov (gold by Short) | verdict |")
        L.append("|---|---|---|---|---|---|---|")
        touched = set()
        for a in anchors:
            best = max(gold, key=lambda g: M.coverage(a, g, ep))
            c1 = M.coverage(a, best, ep)
            c2 = M.coverage(best, a, ep)
            thr = {t: any(M.coverage(a, g, ep) >= t for g in gold) for t in M.THR_SENSITIVITY}
            for t, v in thr.items():
                tot[(t, "covered")] += v
            tot["n_anchor"] += 1
            if c1 >= M.MATCH_THR:
                verdict = "covered"
            elif c1 > 0 or c2 > 0:
                verdict = "partial: different cut of a nearby moment" if c2 >= 0.5 else "partial overlap"
            else:
                verdict = "missed"
            for g in gold:
                if M.coverage(a, g, ep) >= 0.2 or M.coverage(g, a, ep) >= 0.5:
                    touched.add(g["id"])
            L.append(f"| {a['id']} | {fmt_t(ep.W[a['pieces'][0]['start_word']]['start'])} | "
                     f"{edited_duration(a, ep):.0f} | {best['id'] if c1 or c2 else '-'} | {c1:.2f} | {c2:.2f} | {verdict} |")
            if c1 >= M.MATCH_THR:
                t = M.tightening(best, a, ep)
                t.update({"vid": vid, "a": a["id"], "g": best["id"],
                          "ed_a": edited_duration(a, ep), "ed_g": edited_duration(best, ep),
                          "kr_a": edited_duration(a, ep) / region_span_s(a, ep),
                          "kr_g": edited_duration(best, ep) / region_span_s(best, ep)})
                pairs_all.append(t)
            creator_all.append((a, ep))
        L.append("")
        missed = [a for a in anchors if not any(M.coverage(a, g, ep) >= M.MATCH_THR for g in gold)]
        if missed:
            L.append("Shorts gold does not cover (≥ 0.5):")
            L.append("")
            for a in missed:
                L.append(f"- **{a['id']}** ({edited_duration(a, ep):.0f}s): {clip_excerpt(a, ep, 12)}")
            L.append("")
        only = [g for g in gold if g["id"] not in touched]
        gold_only_all += [(g, ep) for g in only]
        gold_overlap_all += [(g, ep) for g in gold if g["id"] in touched]
        L.append(f"Gold clips with no creator Short on the same moment: {len(only)}/{len(gold)} "
                 f"(overall: {dict(sorted(Counter(g['scores']['overall'] for g in only).items()))}; categories: "
                 f"{dict(Counter(g['category'] for g in only).most_common())}).")
        L.append("")
    n = tot["n_anchor"]
    L.insert(4, f"**Gold covers {tot[(0.5, 'covered')]}/{n} creator Shorts at threshold 0.5** "
                f"({tot[(0.3, 'covered')]}/{n} at 0.3, {tot[(0.7, 'covered')]}/{n} at 0.7).")
    L.insert(5, "")
    # judge view
    def jstats(items, version):
        rs = [judge_clip(c, ep, offline=True, version=version) for c, ep in items]
        rs = [r["result"] for r in rs if r]
        if not rs:
            return "no cached verdicts"
        return (f"n={len(rs)}, mean judge overall {sum(r['overall'] for r in rs) / len(rs):.2f}, publishable "
                f"{sum(r['publishable'] for r in rs)}/{len(rs)}, strong "
                f"{sum(r['publishable'] and r['overall'] >= 4 for r in rs)}/{len(rs)}")
    L.append("## What gold adds (judge view, cached verdicts)")
    L.append("")
    L.append("judge-v1 judged every clip; judge-v2 (current) judged all creator Shorts and a 12-clip gold subset.")
    L.append("")
    for version in ("judge-v1", "judge-v2"):
        L.append(f"- {version}: creator Shorts: {jstats(creator_all, version)}")
        L.append(f"- {version}: gold clips on a creator moment: {jstats(gold_overlap_all, version)}")
        L.append(f"- {version}: gold-only clips: {jstats(gold_only_all, version)}")
    L.append(f"- Strategist overall mean: gold on a creator moment "
             f"{sum(g['scores']['overall'] for g, _ in gold_overlap_all) / max(1, len(gold_overlap_all)):.2f} "
             f"(n={len(gold_overlap_all)}), gold-only "
             f"{sum(g['scores']['overall'] for g, _ in gold_only_all) / max(1, len(gold_only_all)):.2f} (n={len(gold_only_all)}).")
    L.append("")
    # tightening
    P = pairs_all
    L.append(f"## Tightening: gold vs creator on the {len(P)} covered pairs")
    L.append("")
    if P:
        L.append("| pair | creator s | gold s | creator keep ratio | gold keep ratio | creator pieces | gold pieces | "
                 "zone kept F1 | zone cut P / R / F1 |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for t in P:
            L.append(f"| {t['a']} ~ {t['g']} | {t['ed_a']:.0f} | {t['ed_g']:.0f} | {t['kr_a']:.2f} | {t['kr_g']:.2f} | "
                     f"{t['pieces_target']} | {t['pieces_prop']} | {t['zone_f1']:.2f} | "
                     f"{t['cut_p']:.2f} / {t['cut_r']:.2f} / {t['cut_f1']:.2f} |")
        L.append("")
        L.append(f"- Median edited length: creator {med([t['ed_a'] for t in P]):.0f}s, gold {med([t['ed_g'] for t in P]):.0f}s "
                 f"(median ratio gold/creator {med([t['len_ratio'] for t in P]):.2f}).")
        L.append(f"- Median keep ratio (edited / region span): creator {med([t['kr_a'] for t in P]):.2f}, "
                 f"gold {med([t['kr_g'] for t in P]):.2f}.")
        L.append(f"- Inside the overlap zone the creator cut {med([t['zone_cut_share_target'] for t in P]):.0%} of words "
                 f"(median); cut-word agreement P {med([t['cut_p'] for t in P]):.2f} / R {med([t['cut_r'] for t in P]):.2f} "
                 f"/ F1 {med([t['cut_f1'] for t in P]):.2f} (medians; P = share of gold's cuts the creator also cut).")
        L.append(f"- Raw kept-word P/R/F1 (gold as prediction, creator as reference): "
                 f"{med([t['raw_p'] for t in P]):.2f} / {med([t['raw_r'] for t in P]):.2f} / {med([t['raw_f1'] for t in P]):.2f} "
                 f"(medians; low raw recall = gold chose a narrower or different span).")
    all_a = [(a, ep) for a, ep in creator_all]
    all_g = gold_only_all + gold_overlap_all
    L.append("")
    L.append("## All clips")
    L.append("")
    L.append(f"- Edited length: creator median {med([edited_duration(a, ep) for a, ep in all_a]):.0f}s "
             f"(range {min(edited_duration(a, ep) for a, ep in all_a):.0f}-{max(edited_duration(a, ep) for a, ep in all_a):.0f}); "
             f"gold median {med([edited_duration(g, ep) for g, ep in all_g]):.0f}s "
             f"(range {min(edited_duration(g, ep) for g, ep in all_g):.0f}-{max(edited_duration(g, ep) for g, ep in all_g):.0f}).")
    L.append(f"- Pieces per clip: creator median {med([len(a['pieces']) for a, _ in all_a])}, gold median "
             f"{med([len(g['pieces']) for g, _ in all_g])}. Keep ratio: creator median "
             f"{med([edited_duration(a, ep) / region_span_s(a, ep) for a, ep in all_a]):.2f}, gold median "
             f"{med([edited_duration(g, ep) / region_span_s(g, ep) for g, ep in all_g if not M.long_range_jumps(g, ep)]):.2f}.")
    text = "\n".join(L)
    outd = ROOT / out
    outd.mkdir(parents=True, exist_ok=True)
    (outd / "report.md").write_text(text, encoding="utf-8")
    print(text)


def main(argv=None):
    from .common import utf8_stdout
    utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="runs/eval_creator_vs_gold")
    a = ap.parse_args(argv)
    run(a.out)


if __name__ == "__main__":
    main()
