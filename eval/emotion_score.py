"""Score the emotion-trigger stage (shotvi/select/emotion_select.py) against the founder's would-post verdicts.

    python -m eval.emotion_score runs/emotion_test/<run>/results.json [--clips runs/emotion_test/clips.json]
                                 [--out report.md] [--json summary.json]

Reads the stage's results.json (one row per clip: verdict yes|maybe|no|unchecked, stop/stay/land with a level, plus
the extra fields of eval/emotion_testset.py passed through). Labels missing from a row are filled from --clips by
(video_id, clip_id), so a stage that drops extra fields can still be scored. Reports, as markdown on stdout:
  (a) founder yes/maybe/no x stage yes/maybe/no/unchecked, with the founder's mean overall score per stage verdict;
  (b) precision of stage "yes" (share founder yes, share founder no) vs the base rate, Fisher exact p for
      stage-yes vs founder-yes (2x2 yes / not yes);
  (c) the same for stage yes+maybe;
  (d) (b) and (c) per source and per series;
  (e) v1_test clips: the v1 review's own tier (pipeline_overall >= 4 = "strong") vs the stage on the same clips;
  (f) stop / stay / land level vs founder verdict (which part carries signal);
  (g) every founder-"no" clip the stage said yes to, and every founder-"yes" clip the stage said no to;
  (h) re-cuts of the same moment rated differently by the founder, and what the stage said to each cut.
Counts are given with every share: the set is ~90 clips, so one clip moves a share by 1-10 points. No LLM, no network.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from math import comb
from pathlib import Path

from .common import load_json, resolve, utf8_stdout

FOUNDER = ("yes", "maybe", "no")
STAGE = ("yes", "maybe", "no", "unchecked")
PARTS = ("stop", "stay", "land")
LEVEL_ORDER = ("clear", "weak", "none")   # shotvi/select/emotion_select.py LEVELS, strongest first
NOT_RUN = "not_run"                        # the stage's placeholder for a clip it did not call (dry run)
V1_STRONG = 4.0          # v1 review overall >= this = "strong candidate" (CLAUDE.md, v1 output 2026-10-13)
LABEL_FIELDS = ("founder_would_post", "founder_scores", "founder_notes", "source", "series", "pipeline_overall",
                "v1_tier", "title", "same_moment_as", "cold_open")
DEFAULT_CLIPS = "runs/emotion_test/clips.json"


# ---- statistics -------------------------------------------------------------------------------------------------

def _hyper(x, r1, c1, n):
    return comb(c1, x) * comb(n - c1, r1 - x) / comb(n, r1)


def fisher_exact(a, b, c, d):
    """2x2 [[a, b], [c, d]] (rows: predicted positive / not; cols: actual positive / not).
    Returns (p_two_sided, p_greater): two-sided = sum of tables no more likely than the observed one (the usual
    definition, as scipy.stats.fisher_exact); greater = P(X >= a), i.e. the predicted-positive row is enriched."""
    n, r1, c1 = a + b + c + d, a + b, a + c
    if n == 0 or r1 in (0, n) or c1 in (0, n):
        return 1.0, 1.0
    lo, hi = max(0, r1 + c1 - n), min(r1, c1)
    probs = {x: _hyper(x, r1, c1, n) for x in range(lo, hi + 1)}
    p_obs = probs[a]
    two = sum(p for p in probs.values() if p <= p_obs * (1 + 1e-7))
    greater = sum(p for x, p in probs.items() if x >= a)
    return min(1.0, two), min(1.0, greater)


def share(k, n):
    return f"{k}/{n} ({100 * k / n:.0f}%)" if n else f"{k}/0 (-)"


def fmt_p(p):
    return f"{p:.3f}" if p >= 0.001 else f"{p:.1e}"


# ---- loading ----------------------------------------------------------------------------------------------------

def norm(v, allowed):
    v = str(v or "").strip().lower()
    return v if v in allowed else (v or "(missing)")


def load_rows(results, clips=None):
    """(rows, notes). rows = results with labels filled from clips; notes = join problems."""
    notes = []
    by_key = {(c["video_id"], c["clip_id"]): c for c in clips or []}
    rows, seen = [], set()
    for r in results:
        k = (r.get("video_id"), r.get("clip_id"))
        if k in seen:
            notes.append(f"duplicate result for {k[0]} {k[1]}: later row ignored")
            continue
        seen.add(k)
        row = dict(r)
        src = by_key.get(k)
        if src is not None:
            for f in LABEL_FIELDS:
                if row.get(f) in (None, "", [], {}) and src.get(f) not in (None, ""):
                    row[f] = src[f]
        row["founder_would_post"] = norm(row.get("founder_would_post"), FOUNDER)
        row["verdict"] = norm(row.get("verdict"), STAGE)
        if row["verdict"] == NOT_RUN:
            notes.append(f"{k[0]} {k[1]}: stage did not run on this clip (not_run): not scored")
            continue
        if row["founder_would_post"] not in FOUNDER:
            notes.append(f"{k[0]} {k[1]}: no founder verdict ({row['founder_would_post']}): not scored")
            continue
        if row["verdict"] not in STAGE:
            notes.append(f"{k[0]} {k[1]}: stage verdict {row['verdict']!r} not in {STAGE}: scored as 'unchecked'")
            row["stage_verdict_raw"] = row["verdict"]
            row["verdict"] = "unchecked"
        rows.append(row)
    missing = [k for k in by_key if k not in seen]
    if missing:
        notes.append(f"{len(missing)} clips of the test set have no result: "
                     + ", ".join(f"{v} {c}" for v, c in missing[:12]) + (" ..." if len(missing) > 12 else ""))
    return rows, notes


def founder_overall(r):
    s = r.get("founder_scores") or {}
    v = s.get("overall") if isinstance(s, dict) else None
    return float(v) if isinstance(v, (int, float)) else None


def part_level(r, part):
    p = r.get(part)
    lv = p.get("level") if isinstance(p, dict) else None
    if lv is None or lv == "":
        return "(missing)"
    return str(lv).strip().lower() if isinstance(lv, str) else lv


def _level_sort(levels):
    """Known levels strongest first (clear, weak, none), then numbers high first, then anything else."""
    known = [x for x in LEVEL_ORDER if x in levels]
    nums = [x for x in levels if isinstance(x, (int, float)) and not isinstance(x, bool)]
    rest = sorted((x for x in levels if x not in nums and x not in known), key=str)
    return known + sorted(nums, reverse=True) + rest


# ---- metrics ----------------------------------------------------------------------------------------------------

def confusion(rows):
    m = Counter((r["founder_would_post"], r["verdict"]) for r in rows)
    return {f: {s: m[(f, s)] for s in STAGE} for f in FOUNDER}


def selection(rows, picked):
    """Stats for the clips the stage picked (picked: set of stage verdicts) vs the rest."""
    sel = [r for r in rows if r["verdict"] in picked]
    rest = [r for r in rows if r["verdict"] not in picked]
    fy = lambda xs: sum(r["founder_would_post"] == "yes" for r in xs)
    fn = lambda xs: sum(r["founder_would_post"] == "no" for r in xs)
    a, b = fy(sel), len(sel) - fy(sel)
    c, d = fy(rest), len(rest) - fy(rest)
    p2, pg = fisher_exact(a, b, c, d)
    return {"n": len(rows), "n_sel": len(sel), "sel_yes": a, "sel_no": fn(sel), "base_yes": fy(rows),
            "base_no": fn(rows), "rest_yes": c, "n_rest": len(rest), "rest_no": fn(rest),
            "p_two_sided": p2, "p_greater": pg}


def sel_line(s):
    return (f"| {s['n']} | {share(s['base_yes'], s['n'])} | {share(s['base_no'], s['n'])} | {s['n_sel']} | "
            f"{share(s['sel_yes'], s['n_sel'])} | {share(s['sel_no'], s['n_sel'])} | "
            f"{share(s['rest_yes'], s['n_rest'])} | {fmt_p(s['p_two_sided'])} | {fmt_p(s['p_greater'])} |")


SEL_HEAD = ("| n | base: founder yes | base: founder no | picked | picked: founder yes | picked: founder no | "
            "not picked: founder yes | Fisher p (2-sided) | p (picked enriched) |\n"
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|")


def _clip_ref(r):
    t = (r.get("title") or "").strip()
    sc = r.get("founder_scores") or {}
    scs = "/".join(str(sc.get(k, "-")) for k in ("hook", "standalone", "payoff", "overall")) if sc else "-"
    return f"**{r['clip_id']}** ({r['video_id']}, {r.get('series', '?')}) {t} — founder h/s/p/o {scs}"


def _part_text(r, part, field):
    p = r.get(part)
    return (p.get(field) or "").strip() if isinstance(p, dict) else ""


def report(rows, notes=(), title="Emotion stage vs founder would-post"):
    L = [f"# {title}", ""]
    n = len(rows)
    vc = Counter(r["verdict"] for r in rows)
    L.append(f"{n} clips scored; stage verdicts: " + ", ".join(f"{vc[s]} {s}" for s in STAGE)
             + f". Founder: " + ", ".join(f"{sum(r['founder_would_post'] == f for r in rows)} {f}" for f in FOUNDER)
             + ".")
    if notes:
        L += ["", "Join notes:"] + [f"- {x}" for x in notes]

    # (a)
    cm = confusion(rows)
    L += ["", "## (a) Founder verdict x stage verdict", "",
          "| founder \\ stage | " + " | ".join(STAGE) + " | total |", "|---|" + "---:|" * (len(STAGE) + 1)]
    for f in FOUNDER:
        L.append(f"| {f} | " + " | ".join(str(cm[f][s]) for s in STAGE) + f" | {sum(cm[f].values())} |")
    L.append("| total | " + " | ".join(str(sum(cm[f][s] for f in FOUNDER)) for s in STAGE) + f" | {n} |")
    L += ["", "Founder overall (1-5) by stage verdict: " + "; ".join(
        f"{s} {_mean([founder_overall(r) for r in rows if r['verdict'] == s])}" for s in STAGE)]

    # (b), (c)
    sy, sym = selection(rows, {"yes"}), selection(rows, {"yes", "maybe"})
    L += ["", "## (b) Stage \"yes\" / (c) stage \"yes\"+\"maybe\"", "",
          "Fisher test on stage-picked vs founder-yes (2x2: picked / not picked x founder yes / not yes); "
          "unchecked counts as not picked.", "", "| picked = | " + SEL_HEAD.split("\n")[0][2:],
          "|---|" + SEL_HEAD.split("\n")[1][1:]]
    L.append("| yes " + sel_line(sy))
    L.append("| yes+maybe " + sel_line(sym))
    checked = [r for r in rows if r["verdict"] != "unchecked"]
    if len(checked) != n:
        L.append("| yes (unchecked dropped) " + sel_line(selection(checked, {"yes"})))

    # (d)
    for key, name in (("source", "source"), ("series", "series")):
        L += ["", f"## (d) Per {name}", "", f"| {name} | picked = | " + SEL_HEAD.split("\n")[0][2:],
              "|---|---|" + SEL_HEAD.split("\n")[1][1:]]
        groups = defaultdict(list)
        for r in rows:
            groups[r.get(key) or "?"].append(r)
        for g, rs in groups.items():
            L.append(f"| {g} | yes " + sel_line(selection(rs, {"yes"})))
            L.append(f"| {g} | yes+maybe " + sel_line(selection(rs, {"yes", "maybe"})))

    # (e)
    v1 = [r for r in rows if str(r.get("source", "")).startswith("v1") and isinstance(r.get("pipeline_overall"),
                                                                                      (int, float))]
    L += ["", "## (e) v1_test: v1 review tier vs the stage, same clips", ""]
    if v1:
        strong = {id(r) for r in v1 if r["pipeline_overall"] >= V1_STRONG}
        L += [f"{len(v1)} v1_test clips; v1 \"strong\" = pipeline_overall >= {V1_STRONG:g}.", "",
              "| picked by | " + SEL_HEAD.split("\n")[0][2:], "|---|" + SEL_HEAD.split("\n")[1][1:]]
        mark = lambda pred: [{**r, "verdict": "yes" if pred(r) else "no"} for r in v1]
        for label, pred in (
                ("v1 strong", lambda r: id(r) in strong),
                ("stage yes", lambda r: r["verdict"] == "yes"),
                ("stage yes+maybe", lambda r: r["verdict"] in ("yes", "maybe")),
                ("v1 strong AND stage yes", lambda r: id(r) in strong and r["verdict"] == "yes"),
                ("v1 strong AND stage yes+maybe", lambda r: id(r) in strong and r["verdict"] in ("yes", "maybe")),
                ("v1 strong AND stage not no", lambda r: id(r) in strong and r["verdict"] != "no")):
            L.append(f"| {label} " + sel_line(selection(mark(pred), {"yes"})))
        cross = Counter((id(r) in strong, r["verdict"]) for r in v1)
        L += ["", "| v1 tier \\ stage | " + " | ".join(STAGE) + " |", "|---|" + "---:|" * len(STAGE)]
        for flag, lab in ((True, "strong"), (False, "other")):
            L.append(f"| {lab} | " + " | ".join(str(cross[(flag, s)]) for s in STAGE) + " |")
    else:
        L.append("No v1_test clips with pipeline_overall in the results.")

    # (f)
    L += ["", "## (f) Part level vs founder verdict", "",
          "Per level: founder yes / maybe / no counts, share yes, mean founder overall, and Fisher p (2-sided) for "
          "\"this level\" vs the other levels on founder yes. Many levels x 3 parts = many tests: treat any single "
          "p < 0.05 as a lead, not a finding."]
    for part in PARTS:
        levels = _level_sort(list(dict.fromkeys(part_level(r, part) for r in rows)))
        L += ["", f"**{part}**", "", "| level | n | yes | maybe | no | share yes | founder overall | p |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for lv in levels:
            rs = [r for r in rows if part_level(r, part) == lv]
            c = Counter(r["founder_would_post"] for r in rs)
            s = selection([{**r, "verdict": "yes" if part_level(r, part) == lv else "no"} for r in rows], {"yes"})
            L.append(f"| {lv} | {len(rs)} | {c['yes']} | {c['maybe']} | {c['no']} | {share(c['yes'], len(rs))} | "
                     f"{_mean([founder_overall(r) for r in rs])} | {fmt_p(s['p_two_sided'])} |")

    # (g)
    L += ["", "## (g) Disagreements", "", "### Founder \"no\", stage \"yes\"", ""]
    fp = [r for r in rows if r["founder_would_post"] == "no" and r["verdict"] == "yes"]
    L += [_disagree(r) for r in fp] or ["(none)"]
    L += ["", "### Founder \"yes\", stage \"no\"", ""]
    fn = [r for r in rows if r["founder_would_post"] == "yes" and r["verdict"] == "no"]
    L += [_disagree(r) for r in fn] or ["(none)"]

    # (h)
    L += ["", "## (h) Re-cuts of one moment that the founder rated differently", ""]
    by_id = {(r["video_id"], r["clip_id"]): r for r in rows}   # clip ids are unique within a video only
    pairs = set()
    for r in rows:
        for o in r.get("same_moment_as") or []:
            k = (r["video_id"], o)
            if k in by_id and by_id[k]["founder_would_post"] != r["founder_would_post"]:
                pairs.add((r["video_id"],) + tuple(sorted((r["clip_id"], o))))
    for vid, a, b in sorted(pairs):
        ra, rb = by_id[(vid, a)], by_id[(vid, b)]
        L.append(f"- {vid}: {a} founder {ra['founder_would_post']} / stage {ra['verdict']}  vs  {b} founder "
                 f"{rb['founder_would_post']} / stage {rb['verdict']}")
    if not pairs:
        L.append("(none)")
    return "\n".join(L) + "\n", {"n": n, "confusion": cm, "stage_yes": sy, "stage_yes_maybe": sym,
                                 "founder_no_stage_yes": [r["clip_id"] for r in fp],
                                 "founder_yes_stage_no": [r["clip_id"] for r in fn]}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return f"{sum(xs) / len(xs):.2f} (n={len(xs)})" if xs else "-"


def _disagree(r):
    out = [f"- {_clip_ref(r)}"]
    if r.get("founder_notes"):
        out.append(f"  - founder note: {r['founder_notes']}")
    out.append(f"  - stage reason: {(r.get('reason') or '').strip() or '-'}")
    lv = ", ".join(f"{p} {part_level(r, p)}" for p in PARTS)
    out.append(f"  - levels: {lv}; stop: {_part_text(r, 'stop', 'line') or '-'}; land: "
               f"{_part_text(r, 'land', 'line') or '-'}")
    if r.get("code_notes"):
        cn = r["code_notes"]
        out.append(f"  - code notes: {cn if isinstance(cn, str) else json.dumps(cn, ensure_ascii=False)}")
    return "\n".join(out)


def main(argv=None):
    utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("results", help="<dir>/results.json written by shotvi.select.emotion_select (or the dir)")
    ap.add_argument("--clips", default=None, help=f"test set to fill labels from (default {DEFAULT_CLIPS} if present)")
    ap.add_argument("--out", help="also write the markdown report here")
    ap.add_argument("--json", help="also write a machine-readable summary here")
    args = ap.parse_args(argv)
    rp = resolve(args.results)
    if rp.is_dir():
        rp = rp / "results.json"
    results = load_json(rp)
    cp = resolve(args.clips or DEFAULT_CLIPS)
    clips = load_json(cp) if (args.clips or cp.exists()) else None
    rows, notes = load_rows(results, clips)
    md, summ = report(rows, notes, title=f"Emotion stage vs founder would-post: {Path(args.results).as_posix()}")
    print(md)
    if args.out:
        Path(resolve(args.out)).write_text(md, encoding="utf-8")
    if args.json:
        Path(resolve(args.json)).write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
