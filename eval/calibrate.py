"""Calibrate the LLM judge (eval/judge.py) before trusting it.

usage: python -m eval.calibrate [--offline] [--out runs/eval_calibration]

Sets (all built from the two sample episodes; nothing here is tuned per podcast):
  gold          55 strategist picks with rubric scores (all publishable; overall 3-5)
  creator       19 creator Shorts from data/anchors (published by the creator = publishable)
  cut_payoff    gold clip truncated at ~55% of its kept words, mid-sentence      -> expect payoff <= 2
  no_setup      gold clip started at ~45% of its kept words, mid-sentence        -> expect standalone/hook <= 2
  intro_outro   episode intro right after the teaser, and the last minute         -> expect overall 1 (A4)
  neg_flip      gold clip with one sentence-final negation (కాదు/లేదు/...) cut out -> expect meaning_changed
  neg_control   the same clip with a nearby filler/backchannel cut out instead     -> expect no meaning change
  random        random 40-80s stretches of the body away from gold/creator clips (unlabelled; expected lower)
  repeat        20 gold clips re-judged at temperature 1.0 (judge noise)

Caveat printed in the report: gold scores come from the telugu-content-strategist agent (an LLM), not a human.
Creator Shorts are the only human signal, and they are positives only.
"""
import argparse
import json
import random
import sys
from collections import Counter, defaultdict

from .common import (ROOT, clips_of, edited_duration, episode_for, excerpt, fmt_t, kept_words,
                     reference_files, render_for_judge, write_json)
from .judge import DIMS, JUDGE_MODEL, PROMPT_VERSION, judge_many, spent
from .metrics import block_spans, norm

VIDEOS = ("FS1DI5D_hUk", "TBlm6wcij94")
NEGATIONS = {"కాదు", "లేదు", "కాదండి", "లేదండి", "కాదు.", "లేదు.", "కూడదు", "వద్దు", "not", "no", "never"}
FILLERS = {"ఉమ్", "మ్", "ఆ", "ఏ", "అంటే", "so", "సో", "ఇంక", "okay", "actually", "basically", "అసలు", "ఇక"}
# teaser / intro windows: taken from the gold files' coverage notes (strategist read the whole episode);
# used only to place intro negatives, never by the judge.
MIN_NEG_S = 20.0   # constructed negatives stay above the length floor so only content can sink them
INTRO = {"FS1DI5D_hUk": (157.0, 215.0), "TBlm6wcij94": (117.0, 180.0)}


def seq_to_pieces(seq):
    out = []
    for j in seq:
        if out and j == out[-1]["end_word"] + 1:
            out[-1]["end_word"] = j
        else:
            out.append({"start_word": j, "end_word": j})
    return out


def mk(cid, seq, meta):
    return {"id": cid, "pieces": seq_to_pieces(seq), **meta}


def cut_payoff(g, ep):
    L = kept_words(g)
    m = int(len(L) * 0.55)
    for d in range(0, 15):
        for k in (m + d, m - d):
            if 10 < k < len(L) - 5 and not ep.natural_after(L[k]):
                c = mk(g["id"] + "~cut_payoff", L[:k + 1], {})
                return c if edited_duration(c, ep) >= MIN_NEG_S else None
    return None


def no_setup(g, ep):
    L = kept_words(g)
    m = int(len(L) * 0.45)
    for d in range(0, 15):
        for k in (m + d, m - d):
            if 5 < k < len(L) - 10 and not ep.natural_after(L[k] - 1):
                c = mk(g["id"] + "~no_setup", L[k:], {})
                return c if edited_duration(c, ep) >= MIN_NEG_S else None
    return None


def span_clip(cid, ep, t0, t1):
    W = ep.W
    a = next(i for i, w in enumerate(W) if w["start"] >= t0)
    b = max(i for i, w in enumerate(W) if w["end"] <= t1)
    return {"id": cid, "pieces": [{"start_word": a, "end_word": b}]}


def negation_pairs(g, ep):
    """(flip, control) variants: drop one negation token inside a piece; control drops a filler nearby."""
    W = ep.W
    L = kept_words(g)
    inner = [(pos, j) for pos, j in enumerate(L[5:-3], 5)]
    for pos, j in inner:
        w = norm(W[j]["word"])
        # sentence-final negation, the only one in its neighbourhood (so dropping it really flips the claim)
        near = [norm(W[x]["word"]) for x in range(max(0, j - 6), min(ep.n, j + 4)) if x != j]
        if (w in NEGATIONS and L[pos - 1] == j - 1 and L[pos + 1] == j + 1 and ep.natural_after(j)
                and not any(x in NEGATIONS for x in near)):
            flip = mk(g["id"] + "~neg_flip", L[:pos] + L[pos + 1:], {"dropped": W[j]["word"]})
            ctrl = None
            for dpos, dj in sorted(inner, key=lambda x: abs(x[0] - pos)):
                if dpos != pos and norm(W[dj]["word"]) in FILLERS and abs(dpos - pos) < 60:
                    ctrl = mk(g["id"] + "~neg_control", L[:dpos] + L[dpos + 1:], {"dropped": W[dj]["word"]})
                    break
            if ctrl is None:   # fall back: drop a non-negation word in a later sentence
                for dpos in range(pos + 8, len(L) - 3):
                    dj = L[dpos]
                    if norm(W[dj]["word"]) not in NEGATIONS and len(W[dj]["word"]) <= 4:
                        ctrl = mk(g["id"] + "~neg_control", L[:dpos] + L[dpos + 1:], {"dropped": W[dj]["word"]})
                        break
            return flip, ctrl
    return None, None


def random_stretches(vid, ep, avoid, n, rng):
    W = ep.W
    out, tries = [], 0
    lo_t = INTRO[vid][1] + 200
    hi_t = ep.duration - 120
    while len(out) < n and tries < 5000:
        tries += 1
        t = rng.uniform(lo_t, hi_t)
        a = next(i for i, w in enumerate(W) if w["start"] >= t)
        while a < ep.n - 1 and not ep.natural_after(a - 1):
            a += 1
        dur = rng.uniform(40, 80)
        b = a
        while b < ep.n - 1 and (W[b]["end"] - W[a]["start"] < dur or not ep.natural_after(b)):
            b += 1
        ta, tb = W[a]["start"] - 30, W[b]["end"] + 30
        if any(not (tb < W[x]["start"] or ta > W[y]["end"]) for x, y in avoid):
            continue
        if any(not (W[b]["end"] < W[c["pieces"][0]["start_word"]]["start"] or
                    W[a]["start"] > W[c["pieces"][0]["end_word"]]["end"]) for c in out):
            continue
        out.append({"id": f"rand{len(out) + 1:02d}", "pieces": [{"start_word": a, "end_word": b}]})
    return out


def build_sets(seed=11):
    rng = random.Random(seed)
    items = defaultdict(list)   # set name -> [(clip, ep, meta)]
    for vid in VIDEOS:
        A, G = reference_files(vid)
        ep = episode_for(G)
        gold = clips_of(G)
        for g in gold:
            items["gold"].append((g, ep, {"vid": vid, "gold": g["scores"]}))
        for a in clips_of(A):
            items["creator"].append((a, ep, {"vid": vid}))
        for name, fn in (("cut_payoff", cut_payoff), ("no_setup", no_setup)):
            k = 0
            for g in rng.sample(gold, len(gold)):
                if k >= (7 if vid == VIDEOS[0] else 8):
                    break
                c = fn(g, ep)
                if c:
                    items[name].append((c, ep, {"vid": vid, "src": g["id"]}))
                    k += 1
        t0, t1 = INTRO[vid]
        items["intro_outro"].append((span_clip(f"{vid}~intro", ep, t0, t1), ep, {"vid": vid}))
        items["intro_outro"].append((span_clip(f"{vid}~outro", ep, ep.duration - 60, ep.duration), ep, {"vid": vid}))
        nflip = 0
        for g in gold:
            if nflip >= 6:
                break
            f, c = negation_pairs(g, ep)
            if f and c:
                items["neg_flip"].append((f, ep, {"vid": vid, "src": g["id"], "dropped": f["dropped"]}))
                items["neg_control"].append((c, ep, {"vid": vid, "src": g["id"], "dropped": c["dropped"]}))
                nflip += 1
        avoid = [s for c in gold + clips_of(A) for s in block_spans(c, ep)]
        for c in random_stretches(vid, ep, avoid, 6, rng):
            c["id"] = f"{vid}~{c['id']}"
            items["random"].append((c, ep, {"vid": vid}))
    rep = rng.sample(items["gold"], 20)
    return items, rep


# ---- statistics (no scipy dependency) ------------------------------------------------------------------

def ranks(x):
    order = sorted(range(len(x)), key=lambda i: x[i])
    r = [0.0] * len(x)
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and x[order[j + 1]] == x[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def pearson(x, y):
    n = len(x)
    if n < 3:
        return float("nan")
    mx, my = sum(x) / n, sum(y) / n
    sx = sum((a - mx) ** 2 for a in x) ** 0.5
    sy = sum((b - my) ** 2 for b in y) ** 0.5
    if sx == 0 or sy == 0:
        return float("nan")
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def spearman(x, y):
    return pearson(ranks(x), ranks(y))


def kendall_b(x, y):
    n = len(x)
    c = d = tx = ty = 0
    for i in range(n):
        for j in range(i + 1, n):
            a, b = x[i] - x[j], y[i] - y[j]
            if a == 0 and b == 0:
                continue
            if a == 0:
                tx += 1
            elif b == 0:
                ty += 1
            elif (a > 0) == (b > 0):
                c += 1
            else:
                d += 1
    den = ((c + d + tx) * (c + d + ty)) ** 0.5
    return (c - d) / den if den else float("nan")


def auc(pos, neg):
    if not pos or not neg:
        return float("nan")
    s = 0.0
    for p in pos:
        for q in neg:
            s += 1.0 if p > q else 0.5 if p == q else 0.0
    return s / (len(pos) * len(neg))


def boot_ci(x, y, f, n=1000, seed=0):
    rng = random.Random(seed)
    idx = list(range(len(x)))
    vals = []
    for _ in range(n):
        s = [rng.choice(idx) for _ in idx]
        v = f([x[i] for i in s], [y[i] for i in s])
        if v == v:
            vals.append(v)
    vals.sort()
    if not vals:
        return float("nan"), float("nan")
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]


# ---- report ---------------------------------------------------------------------------------------------

def version_table(items, versions=("judge-v1", "judge-v2")):
    """Same clips, cached verdicts of each prompt version (offline). Only clips judged by every version count."""
    sub = subset_items(items)
    L = ["## Prompt versions on the same clips (cached verdicts)", "",
         "| set | n | " + " | ".join(f"{v} publishable | {v} mean overall" for v in versions) + " |",
         "|---|---|" + "---|---|" * len(versions)]
    gold_corr = {}
    for name, lst in sub.items():
        recs = {v: judge_many([(c, ep) for c, ep, _ in lst], offline=True, version=v) for v in versions}
        keep = [i for i in range(len(lst)) if all(recs[v][i] for v in versions)]
        if not keep:
            continue
        cells = []
        for v in versions:
            rs = [recs[v][i]["result"] for i in keep]
            if name == "neg_flip":
                caught = sum(r["meaning_changed"] or r["overall"] <= 1 for r in rs)
                cells.append(f"meaning caught {caught}/{len(rs)} | {sum(r['overall'] for r in rs) / len(rs):.2f}")
            else:
                cells.append(f"{sum(r['publishable'] for r in rs)}/{len(rs)} | {sum(r['overall'] for r in rs) / len(rs):.2f}")
            if name == "gold":
                g = [lst[i][2]["gold"]["overall"] for i in keep]
                gold_corr[v] = (spearman(g, [r["overall"] for r in rs]),
                                sum(abs(a - r["overall"]) for a, r in zip(g, rs)) / len(rs))
        L.append(f"| {name} | {len(keep)} | " + " | ".join(cells) + " |")
    L.append("")
    for v, (sp, mae) in gold_corr.items():
        L.append(f"- {v} vs gold overall on the subset: Spearman {f2(sp)}, MAE {mae:.2f}.")
    return chr(10).join(L)


def f2(x):
    return "n/a" if x != x else f"{x:.2f}"


def pct(a, b):
    return f"{a}/{b} ({a / b:.0%})" if b else "0/0"


SUBSET = {"creator": None, "gold": 12, "cut_payoff": 4, "no_setup": 4, "neg_flip": None, "neg_control": None}


def subset_items(items):
    """Validation subset for a prompt revision: all creator Shorts and seeded flips, every ~3rd gold clip,
    the first few constructed negatives."""
    out = {}
    for name, k in SUBSET.items():
        lst = items.get(name, [])
        if k is None:
            out[name] = lst
        elif name == "gold":
            step = len(lst) / k
            out[name] = [lst[int(i * step)] for i in range(k)]
        else:
            out[name] = lst[:k]
    return out


def run(offline=False, out="runs/eval_calibration", version=None, subset=False):
    version = version or PROMPT_VERSION
    items, rep = build_sets()
    if subset:
        items, rep = subset_items(items), []
    res = defaultdict(list)
    for name, lst in items.items():
        print(f"judging {name}: {len(lst)} clips", file=sys.stderr)
        recs = judge_many([(c, ep) for c, ep, _ in lst], offline=offline, version=version)
        res[name] = [(c, ep, m, r) for (c, ep, m), r in zip(lst, recs)]
    print(f"judging repeat: {len(rep)} clips", file=sys.stderr)
    rrecs = judge_many([(c, ep) for c, ep, _ in rep], rep=1, offline=offline, version=version)
    L = [f"# Judge calibration ({JUDGE_MODEL}, prompt {version}{', validation subset' if subset else ''})", ""]
    L.append("Gold scores come from the `telugu-content-strategist` agent (an LLM with its own prompt), not a human. "
             "Agreement with gold therefore measures agreement between two independent raters, not with human "
             "taste. Creator Shorts are the only human signal, and they are positives only.")
    L.append("")
    tot_usd = sum(r["usage"]["usd"] for lst in res.values() for *_, r in lst if r) + \
        sum(r["usage"]["usd"] for r in rrecs if r)
    ncalls = sum(1 for lst in res.values() for *_, r in lst if r) + sum(1 for r in rrecs if r)
    L.append(f"Judged {ncalls} clips for ${tot_usd:.2f} (cached; total judge spend so far ${spent():.2f}).")
    L.append("")

    # 1. agreement with gold
    g = [(m["gold"], r["result"], c) for c, ep, m, r in res["gold"] if r]
    L.append(f"## 1. Agreement with gold scores (n = {len(g)} clips)")
    L.append("")
    L.append("| dim | gold mean | judge mean | bias | MAE | exact (±0.5) | within 1 | Spearman [95% CI] | Kendall tau-b |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    summary = {}
    for d in DIMS:
        x = [a[d] for a, b, _ in g]
        y = [b[d] for a, b, _ in g]
        n = len(x)
        mae = sum(abs(a - b) for a, b in zip(x, y)) / n
        ex = sum(abs(a - b) <= 0.5 for a, b in zip(x, y))
        w1 = sum(abs(a - b) <= 1.0 for a, b in zip(x, y))
        sp = spearman(x, y)
        lo, hi = boot_ci(x, y, spearman)
        kt = kendall_b(x, y)
        summary[d] = {"spearman": sp, "ci": [lo, hi], "kendall": kt, "mae": mae, "bias": sum(y) / n - sum(x) / n}
        L.append(f"| {d} | {sum(x) / n:.2f} | {sum(y) / n:.2f} | {sum(y) / n - sum(x) / n:+.2f} | {mae:.2f} | "
                 f"{pct(ex, n)} | {pct(w1, n)} | {f2(sp)} [{f2(lo)}, {f2(hi)}] | {f2(kt)} |")
    L.append("")
    cm = Counter((a["overall"], b["overall"]) for a, b, _ in g)
    gv = sorted({k[0] for k in cm})
    jv = sorted({k[1] for k in cm})
    L.append("Overall, gold (rows) vs judge (columns):")
    L.append("")
    L.append("| gold \\ judge | " + " | ".join(f"{v:g}" for v in jv) + " |")
    L.append("|---" * (len(jv) + 1) + "|")
    for a in gv:
        L.append(f"| {a:g} | " + " | ".join(str(cm.get((a, b), 0)) for b in jv) + " |")
    L.append("")

    # 2. publishable rates per set
    L.append("## 2. Judge verdicts per set")
    L.append("")
    L.append("| set | n | expected | judge publishable | strong (pub & overall ≥ 4) | mean overall | "
             "meaning_changed | starts_mid | ends_mid |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    expect = {"gold": "publishable", "creator": "publishable", "cut_payoff": "not (payoff ≤ 2)",
              "no_setup": "not (standalone/hook ≤ 2)", "intro_outro": "not (overall 1)",
              "neg_flip": "meaning_changed", "neg_control": "as source clip", "random": "unlabelled, lower"}
    for name in ("gold", "creator", "cut_payoff", "no_setup", "intro_outro", "neg_flip", "neg_control", "random"):
        rs = [r["result"] for *_, r in res.get(name, []) if r]
        n = len(rs)
        if not n:
            continue
        pub = sum(r["publishable"] for r in rs)
        strong = sum(r["publishable"] and r["overall"] >= 4 for r in rs)
        L.append(f"| {name} | {n} | {expect[name]} | {pct(pub, n)} | {pct(strong, n)} | "
                 f"{sum(r['overall'] for r in rs) / n:.2f} | {sum(r['meaning_changed'] for r in rs)} | "
                 f"{sum(r['starts_mid_thought'] for r in rs)} | {sum(r['ends_mid_thought'] for r in rs)} |")
    L.append("")
    L.append(version_table(items) if subset or version != "judge-v1" else "")
    L.append("")
    pos = [r["result"]["overall"] for s in ("gold", "creator") for *_, r in res[s] if r]
    neg = [r["result"]["overall"] for s in ("cut_payoff", "no_setup", "intro_outro") for *_, r in res[s] if r]
    rnd = [r["result"]["overall"] for *_, r in res["random"] if r]
    L.append(f"- Separation, overall score: AUC(gold+creator vs cut_payoff+no_setup+intro_outro) = "
             f"{f2(auc(pos, neg))} ({len(pos)} vs {len(neg)} clips); AUC(gold+creator vs random) = "
             f"{f2(auc(pos, rnd))} ({len(pos)} vs {len(rnd)}).")
    cp = [r["result"] for *_, r in res["cut_payoff"] if r]
    ns = [r["result"] for *_, r in res["no_setup"] if r]
    L.append(f"- cut_payoff caught (payoff ≤ 2 or ends_mid_thought): "
             f"{pct(sum(r['payoff'] <= 2 or r['ends_mid_thought'] for r in cp), len(cp))}; "
             f"no_setup caught (standalone ≤ 2, hook ≤ 2 or starts_mid_thought): "
             f"{pct(sum(r['standalone'] <= 2 or r['hook'] <= 2 or r['starts_mid_thought'] for r in ns), len(ns))}.")
    cr = [r["result"] for *_, r in res["creator"] if r]
    gr = [r["result"] for *_, r in res["gold"] if r]
    L.append(f"- T4/T5 judge half on known-good clips: starts_mid_thought fires on "
             f"{pct(sum(r['starts_mid_thought'] for r in cr), len(cr))} creator Shorts and "
             f"{pct(sum(r['starts_mid_thought'] for r in gr), len(gr))} gold; ends_mid_thought on "
             f"{pct(sum(r['ends_mid_thought'] for r in cr), len(cr))} creator and "
             f"{pct(sum(r['ends_mid_thought'] for r in gr), len(gr))} gold.")
    L.append("")

    # 3. seeded negation
    L.append("## 3. Seeded negation drops (spec T2)")
    L.append("")
    fl = res.get("neg_flip", [])
    co = res.get("neg_control", [])
    caught = sum(1 for *_, r in fl if r and (r["result"]["meaning_changed"] or r["result"]["overall"] <= 1))
    fa = sum(1 for *_, r in co if r and r["result"]["meaning_changed"])
    L.append(f"- Negation dropped: judge says meaning_changed (or overall 1) on {pct(caught, len(fl))}.")
    L.append(f"- Control (a filler / small word dropped from the same clip instead): meaning_changed on "
             f"{pct(fa, len(co))} (false alarms).")
    L.append("")
    L.append("| clip | dropped | meaning_changed | garbled | overall | judge critique |")
    L.append("|---|---|---|---|---|---|")
    for (c, ep, m, r) in fl + co:
        if not r:
            continue
        s = r["result"]
        L.append(f"| {c['id']} | {m['dropped']} | {s['meaning_changed']} | {s['garbled_join']} | {s['overall']:g} | "
                 f"{s['critique'][:160].replace('|', '/')} |")
    L.append("")

    # 4. noise
    pairs = [(a["result"], b["result"]) for (c, ep, m), a, b in
             zip(rep, judge_many([(c, ep) for c, ep, _ in rep], offline=True, version=version), rrecs) if a and b]
    L.append(f"## 4. Judge noise: {len(pairs)} gold clips re-judged at temperature 1.0")
    L.append("")
    if pairs:
        for d in ("overall", "hook", "payoff"):
            x = [a[d] for a, b in pairs]
            y = [b[d] for a, b in pairs]
            L.append(f"- {d}: mean |diff| {sum(abs(p - q) for p, q in zip(x, y)) / len(x):.2f}, identical "
                     f"{pct(sum(p == q for p, q in zip(x, y)), len(x))}, Spearman {f2(spearman(x, y))}")
        L.append(f"- publishable verdict flips: {pct(sum(a['publishable'] != b['publishable'] for a, b in pairs), len(pairs))}")
    L.append("")

    # 5. disagreements
    L.append("## 5. Largest disagreements with gold (|overall diff| ≥ 1)")
    L.append("")
    dis = sorted(((b["overall"] - a["overall"], a, b, c) for a, b, c in g), key=lambda x: -abs(x[0]))
    for diff, a, b, c in dis:
        if abs(diff) < 1:
            break
        L.append(f"- **{c['id']}** gold {a['overall']:g} (h{a['hook']:g} s{a['standalone']:g} p{a['payoff']:g}) vs "
                 f"judge {b['overall']:g} (h{b['hook']:g} s{b['standalone']:g} p{b['payoff']:g}). "
                 f"Gold: {c['why_it_works'][:150]} Judge: {b['critique'][:220]}")
    L.append("")
    L.append("Creator Shorts the judge rates not publishable:")
    L.append("")
    for c, ep, m, r in res["creator"]:
        if r and not r["result"]["publishable"]:
            s = r["result"]
            L.append(f"- **{c['id']}** ({m['vid']}, {edited_duration(c, ep):.0f}s) h{s['hook']:g} s{s['standalone']:g} "
                     f"p{s['payoff']:g} o{s['overall']:g}: {s['critique'][:260]}")
    L.append("")
    L.append("Random stretches the judge rates publishable (possible good clips nobody picked, or leniency):")
    L.append("")
    for c, ep, m, r in res["random"]:
        if r and r["result"]["publishable"]:
            s = r["result"]
            L.append(f"- **{c['id']}** at {fmt_t(ep.W[c['pieces'][0]['start_word']]['start'])} o{s['overall']:g}: "
                     f"{s['moment'][:120]} | {excerpt((ep.W[j]['word'] for j in kept_words(c)), 10)}")
    L.append("")

    # 6. length buckets (spec §9 item 5)
    L.append("## 6. Judged quality by edited length (gold + creator + random)")
    L.append("")
    L.append("| bucket | n | mean judge overall | judge publishable |")
    L.append("|---|---|---|---|")
    bk = defaultdict(list)
    for s in ("gold", "creator", "random"):
        for c, ep, m, r in res[s]:
            if r:
                ed = edited_duration(c, ep)
                b = "<30" if ed < 30 else "30-60" if ed < 60 else "60-90" if ed < 90 else "90-120" if ed < 120 else ">120"
                bk[b].append(r["result"])
    for b in ("<30", "30-60", "60-90", "90-120", ">120"):
        rs = bk.get(b, [])
        if rs:
            L.append(f"| {b}s | {len(rs)} | {sum(r['overall'] for r in rs) / len(rs):.2f} | "
                     f"{pct(sum(r['publishable'] for r in rs), len(rs))} |")
    L.append("")
    text = "\n".join(L)
    outd = ROOT / out
    outd.mkdir(parents=True, exist_ok=True)
    (outd / "report.md").write_text(text, encoding="utf-8")
    dump = {name: [{"id": c["id"], "meta": m, "judge": (r or {}).get("result"), "usage": (r or {}).get("usage")}
                   for c, ep, m, r in lst] for name, lst in res.items()}
    dump["repeat"] = [{"id": c["id"], "judge": (r or {}).get("result")} for (c, ep, m), r in zip(rep, rrecs)]
    dump["summary"] = summary
    write_json(outd / "results.json", dump)
    print(text)


def main(argv=None):
    from .common import utf8_stdout
    utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="use cached judgements only")
    ap.add_argument("--out", default="runs/eval_calibration")
    ap.add_argument("--dry", action="store_true", help="only list the sets and show one rendered clip per set")
    ap.add_argument("--version", default=None, help="judge prompt version (default: eval.judge.PROMPT_VERSION)")
    ap.add_argument("--subset", action="store_true", help="validation subset only (for a prompt revision)")
    a = ap.parse_args(argv)
    if a.dry:
        items, rep = build_sets()
        for name, lst in items.items():
            print(f"== {name}: {len(lst)}")
            c, ep, m = lst[0]
            print(c["id"], m, f"{edited_duration(c, ep):.0f}s")
            print(render_for_judge(c, ep)[:600])
        return
    out = a.out if a.out != "runs/eval_calibration" or not (a.subset or a.version) else         f"runs/eval_calibration/{a.version or PROMPT_VERSION}{'_subset' if a.subset else ''}"
    run(a.offline, out, a.version, a.subset)


if __name__ == "__main__":
    main()
