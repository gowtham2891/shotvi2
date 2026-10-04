"""Code-only metrics: recall vs anchors/gold, tightening agreement, structural checks, near-duplicates.

Definitions (see the module docstrings of each function and the report legend in eval/run.py):

* Block spans of a clip: split its pieces at jumps (backward move, or source skip > 20s, clip_spec §0) and take
  [min start_word, max end_word] of each block. This is "the region" at the granularity a reviewer sees it; the
  single [min, max] region is misleading for stitched clips (one gold clip joins a setup at 4 min to a
  payoff at 49 min, so its region would "cover" every Short in between).
* coverage(T by P) = share of target T's kept words that lie inside any block span of proposal P.
  A target is **found** by P if coverage >= MATCH_THR (0.5: the proposal's region holds the majority of what
  the editor kept, so the reviewer lands on the moment and only nudges boundaries). Sensitivity at 0.3 / 0.7 is
  reported alongside.
* Tightening agreement on a matched pair: word-level precision / recall / F1 of P's kept words vs T's kept
  words, (a) raw and (b) restricted to the overlap zone Z (intersection of both clips' block spans), which
  isolates tightening from boundary choice. The keep-everything baseline inside Z is reported next to it.
"""
import re
from collections import defaultdict

from .common import (CENSOR_TAG, HARD_MAX_S, HARD_MIN_S, LONG_RANGE_S, SKIP_S, SOFT_MAX_S, clip_excerpt, clips_of,
                     edited_duration, jumps, kept_set, kept_words, region, region_span_s)

MATCH_THR = 0.5
THR_SENSITIVITY = (0.3, 0.5, 0.7)
KS = (1, 3, 5, 10, 20)
NEAR_DUP = 0.5            # Q4
REGION_MAX_S = 300.0      # R2
LONG_RANGE_S, LONG_RANGE_MAX = 300.0, 1   # S5 (PENDING)
BLOCKS_FLAG_ABOVE, BLOCKS_MAX = 3, 4      # S1
BACKWARD_MAX = 2          # O1
MOVED_MAX_S = 30.0        # O2
LEAD_IN_MAX, TAIL_MAX = 4, 2              # T4 / T5
HOOK_MAX_S = 6.0          # C1
DEAD_AIR_S = 1.5          # T6
CHOP_PER_S = 1 / 3.0      # T7
LOW_KEEP = 0.25           # T9
LOWCONF_LP, LOWCONF_RUN = -0.7, 4   # A2
TEASER_SHARE = 0.4        # R3 heuristic: share of kept 3-grams also spoken far away in the episode
TEASER_FAR_S = 60.0

# pronoun-like referents that need an antecedent (C2 flag). Determiners ఆ/ఈ/this are left out on purpose:
# "ఈ సమాజం ..." is a fine opener.
DEMONSTRATIVES = {"అది", "ఇది", "ఆయన", "ఈయన", "అతను", "ఆమె", "ఈమె", "ఆవిడ", "వాడు", "వీడు", "వాళ్ళు",
                  "వాళ్లు", "దాని", "దాన్ని", "దానికి", "అదే", "అప్పుడు", "అక్కడ", "ఆయనకి", "ఆయన్ని",
                  "he", "she", "they", "him", "her", "that", "those"}

_PUNCT = re.compile(r"[\s.,?!।:;\"'“”‘’()\[\]…-]+")


def norm(w):
    return _PUNCT.sub("", w.lower())


# ---- regions and coverage -----------------------------------------------------------------------------

def blocks(clip, ep):
    """Pieces grouped into blocks (list of lists of pieces), split at jumps."""
    P = clip["pieces"]
    cut = {k for k, _ in jumps(clip, ep)}
    out, cur = [], []
    for k, p in enumerate(P):
        cur.append(p)
        if k in cut:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def block_spans(clip, ep):
    return [(min(p["start_word"] for p in b), max(p["end_word"] for p in b)) for b in blocks(clip, ep)]


def in_spans(j, spans):
    return any(a <= j <= b for a, b in spans)


def coverage(target, prop, ep, _cache={}):
    key = (id(target), id(prop))
    if key not in _cache:
        spans = block_spans(prop, ep)
        T = kept_set(target)
        _cache[key] = sum(in_spans(j, spans) for j in T) / max(1, len(T))
    return _cache[key]


def match_targets(props, targets, ep, thr=MATCH_THR):
    """For each target: best proposal (max coverage, ties -> better rank), first rank at which coverage >= thr."""
    rows = []
    for t in targets:
        best, best_cov, first_rank = None, 0.0, None
        for r, p in enumerate(props, 1):
            c = coverage(t, p, ep)
            if c > best_cov + 1e-9:
                best, best_cov = (r, p), c
            if first_rank is None and c >= thr:
                first_rank = r
        rows.append({"target": t, "best_rank": best[0] if best else None, "best": best[1] if best else None,
                     "best_cov": best_cov, "rank": first_rank})
    return rows


def recall_at(rows, ks=KS):
    n = len(rows)
    out = {}
    for k in ks:
        out[k] = sum(1 for r in rows if r["rank"] is not None and r["rank"] <= k)
    out["all"] = sum(1 for r in rows if r["rank"] is not None)
    out["n"] = n
    return out


def rank_curve(props, targets, ep, thrs=THR_SENSITIVITY, ks=KS):
    return {thr: recall_at(match_targets(props, targets, ep, thr), ks) for thr in thrs}


# ---- tightening ---------------------------------------------------------------------------------------

def prf(pred, gold):
    tp = len(pred & gold)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(gold) if gold else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def tightening(prop, target, ep):
    P, T = kept_set(prop), kept_set(target)
    raw = prf(P, T)
    zone = set()
    for a, b in block_spans(prop, ep):
        for c, d in block_spans(target, ep):
            lo, hi = max(a, c), min(b, d)
            if lo <= hi:
                zone.update(range(lo, hi + 1))
    Pz, Tz = P & zone, T & zone
    inz = prf(Pz, Tz)
    base = prf(zone, Tz)   # keep everything inside the overlap zone
    cut = prf(zone - Pz, zone - Tz)   # agreement on what to CUT inside the zone (keep-all scores 0)
    return {
        "cut_p": cut[0], "cut_r": cut[1], "cut_f1": cut[2], "zone_cut_share_target": len(zone - Tz) / max(1, len(zone)),
        "raw_p": raw[0], "raw_r": raw[1], "raw_f1": raw[2],
        "zone_words": len(zone), "zone_p": inz[0], "zone_r": inz[1], "zone_f1": inz[2],
        "zone_keepall_f1": base[2],
        "len_ratio": edited_duration(prop, ep) / max(1e-6, edited_duration(target, ep)),
        "keep_ratio_prop": edited_duration(prop, ep) / max(1e-6, region_span_s(prop, ep)),
        "keep_ratio_target": edited_duration(target, ep) / max(1e-6, region_span_s(target, ep)),
        "pieces_prop": len(prop["pieces"]), "pieces_target": len(target["pieces"]),
    }


# ---- near duplicates (Q4) -----------------------------------------------------------------------------

def near_dups(clips, k=None, thr=NEAR_DUP):
    top = clips[:k] if k else clips
    sets = [kept_set(c) for c in top]
    out = []
    for i in range(len(top)):
        for j in range(i + 1, len(top)):
            a, b = sets[i], sets[j]
            if not a or not b:
                continue
            sh = len(a & b) / min(len(a), len(b))
            if sh > thr:
                out.append((i + 1, j + 1, sh))
    return out


# ---- structural checks --------------------------------------------------------------------------------

class TeaserIndex:
    """3-gram positions over the whole episode, for the R3 duplicate-text heuristic."""

    def __init__(self, ep):
        self.ep = ep
        toks = [norm(w["word"]) for w in ep.W]
        self.toks = toks
        self.pos = defaultdict(list)
        for i in range(len(toks) - 2):
            g = (toks[i], toks[i + 1], toks[i + 2])
            if all(g):
                self.pos[g].append(i)

    def far_share(self, clip):
        W, toks = self.ep.W, self.toks
        lo, hi = region(clip)
        t0, t1 = W[lo]["start"] - TEASER_FAR_S, W[hi]["end"] + TEASER_FAR_S
        grams = []
        for p in clip["pieces"]:
            for i in range(p["start_word"], p["end_word"] - 1):
                g = (toks[i], toks[i + 1], toks[i + 2])
                if all(g):
                    grams.append(g)
        if len(grams) < 10:
            return 0.0, None
        hits, where = 0, []
        for g in grams:
            far = [i for i in self.pos[g] if not (t0 <= W[i]["start"] <= t1)]
            if far:
                hits += 1
                where.append(W[far[0]]["start"])
        where.sort()
        return hits / len(grams), (where[len(where) // 2] if where else None)


def _overlaps(t0, t1, ivs):
    return [(a, b) for a, b in ivs if a < t1 and b > t0]


def lead_in(ep, s):
    """spec 0.2 §0: words from the previous natural boundary up to s-1."""
    k = s
    while k > 0 and not ep.natural_after(k - 1):
        k -= 1
    return s - k


def tail(ep, e):
    """spec 0.2 §0: words from e+1 up to the next natural boundary."""
    k = e
    while k < ep.n - 1 and not ep.natural_after(k):
        k += 1
    return k - e


def moved_duration(clip, ep):
    """spec 0.2 §0: edited duration minus the heaviest subsequence of pieces with increasing start_word."""
    P = clip["pieces"]
    d = [ep.W[p["end_word"]]["end"] - ep.W[p["start_word"]]["start"] for p in P]
    best = list(d)
    for i in range(len(P)):
        for j in range(i):
            if P[j]["start_word"] < P[i]["start_word"]:
                best[i] = max(best[i], best[j] + d[i])
    return sum(d) - (max(best) if best else 0.0)


def long_range_jumps(clip, ep):
    P, W = clip["pieces"], ep.W
    return [k for k, (a, b) in enumerate(zip(P, P[1:]))
            if abs(W[b["start_word"]]["start"] - W[a["end_word"]]["end"]) > LONG_RANGE_S]


def clip_checks(clip, ep, kind, teaser=None):
    """Code-checkable clip_spec v0.2 rules for one clip. Returns list of (rule, action, detail).
    action: reject / hide / flag / info. For gold and anchors the same rules are *measured*, not enforced."""
    W = ep.W
    P = clip["pieces"]
    out = []
    ed = edited_duration(clip, ep)
    # §5 length
    if ed < HARD_MIN_S:
        out.append(("L-min", "reject", f"edited {ed:.1f}s < {HARD_MIN_S:.0f}s"))
    if ed > HARD_MAX_S:
        out.append(("L-max", "reject", f"edited {ed:.1f}s > {HARD_MAX_S:.0f}s"))
    elif ed > SOFT_MAX_S:
        has_note = any(n.get("type") == "length_over_soft_max" for n in clip.get("edit_notes") or [])
        out.append(("L-soft", "flag" if has_note or kind != "proposals" else "reject",
                    f"over_soft_max: edited {ed:.1f}s > {SOFT_MAX_S:.0f}s" + ("" if has_note else " without length note")))
    # R2 per side of a long-range jump, S5
    lr = long_range_jumps(clip, ep)
    sides, cur = [], []
    for k, p in enumerate(P):
        cur.append(p)
        if k in lr:
            sides.append(cur)
            cur = []
    sides.append(cur)
    for side in sides:
        lo = min(p["start_word"] for p in side)
        hi = max(p["end_word"] for p in side)
        span = W[hi]["end"] - W[lo]["start"]
        if span > REGION_MAX_S:
            out.append(("R2", "reject", f"region span {span:.0f}s > {REGION_MAX_S:.0f}s"))
    if len(lr) > LONG_RANGE_MAX:
        out.append(("S5", "reject", f"{len(lr)} long-range jumps (> {LONG_RANGE_S:.0f}s) > {LONG_RANGE_MAX}"))
    elif lr:
        k = lr[0]
        gap = abs(W[P[k + 1]["start_word"]]["start"] - W[P[k]["end_word"]]["end"])
        out.append(("S5", "flag", f"long_range_stitch: jump of {gap:.0f}s"))
    # S1 blocks
    J = jumps(clip, ep)
    nb = len(J) + 1
    if nb > BLOCKS_MAX:
        out.append(("S1", "reject", f"{nb} blocks > {BLOCKS_MAX}"))
    elif nb > BLOCKS_FLAG_ABOVE:
        out.append(("S1", "flag", f"many_jumps: {nb} blocks"))
    # O1 / O2 / O4 reorders
    back = [k for k, kind_ in J if kind_ == "backward"]
    if len(back) > BACKWARD_MAX:
        out.append(("O1", "reject", f"{len(back)} backward jumps > {BACKWARD_MAX}"))
    if back:
        md = moved_duration(clip, ep)
        if md > MOVED_MAX_S:
            out.append(("O2", "reject", f"moved duration {md:.1f}s > {MOVED_MAX_S:.0f}s"))
    overl = []
    for i in range(len(P)):
        for j in range(i + 1, len(P)):
            if P[i]["start_word"] <= P[j]["end_word"] and P[j]["start_word"] <= P[i]["end_word"]:
                overl.append((i, j))
    bad = [(i, j) for i, j in overl if i != 0]
    if len({j for i, j in overl if i == 0}) > 1:
        bad += [(i, j) for i, j in overl if i == 0]
    if bad:
        out.append(("O4", "reject", f"overlapping pieces {bad} (only piece 0 may be replayed, in one later piece)"))
    # T4 / T5 (code part; when they flag, the judge decides)
    s, e = P[0]["start_word"], P[-1]["end_word"]
    li, tl = lead_in(ep, s), tail(ep, e)
    if li > LEAD_IN_MAX:
        out.append(("T4", "flag", f"rough_start: lead-in {li} words: ..." +
                    " ".join(W[x]["word"] for x in range(max(0, s - min(li, 6)), s)) + " | " + W[s]["word"]))
    if tl > TAIL_MAX:
        out.append(("T5", "flag", f"rough_end: tail {tl} words: " + W[e]["word"] + " | " +
                    " ".join(W[x]["word"] for x in range(e + 1, min(ep.n, e + 1 + min(tl, 6)))) + " ..."))
    # T6 dead air
    for p in P:
        for j in range(p["start_word"], p["end_word"]):
            g0, g1 = W[j]["end"], W[j + 1]["start"]
            if g1 - g0 > DEAD_AIR_S and not _overlaps(g0, g1, ep.laughs):
                out.append(("T6", "flag", f"dead_air: {g1 - g0:.1f}s gap at {g0:.1f}s"))
                break
    if ed > 0 and len(P) / ed > CHOP_PER_S:
        out.append(("T7", "flag", f"over_chopped: {len(P)} pieces in {ed:.0f}s"))
    rspan = sum(W[max(p["end_word"] for p in sd)]["end"] - W[min(p["start_word"] for p in sd)]["start"]
                for sd in sides)
    if rspan > 0 and ed / rspan < LOW_KEEP:
        out.append(("T9", "flag", f"low_keep_ratio: {ed / rspan:.0%}"))
    # A1 censored, A5 song
    for p in P:
        t0, t1 = W[p["start_word"]]["start"], W[p["end_word"]]["end"]
        for a_, _ in _overlaps(t0, t1, ep.censored):
            out.append(("A1", "reject", f"piece {p['start_word']}-{p['end_word']} overlaps {CENSOR_TAG} at {a_:.1f}s"))
        for a_, _ in _overlaps(t0, t1, ep.songs):
            out.append(("A5", "flag", f"song_overlap at {a_:.1f}s"))
    # A2 low confidence
    hl = clip.get("hook_line")
    hook_end = hl["end_word"] if hl else P[0]["start_word"] + 5
    run, found = [], None
    for j in kept_words(clip):
        if W[j].get("logprob", 0.0) < LOWCONF_LP:
            run.append(j)
            if len(run) >= LOWCONF_RUN and found is None:
                found = run
        else:
            run = []
    if found:
        in_hook = P[0]["start_word"] <= found[0] <= hook_end
        out.append(("A2", "reject" if in_hook else "flag",
                    f"low_confidence: {len(found)}+ words at {W[found[0]]['start']:.1f}s: "
                    + " ".join(W[j]["word"] for j in found[:6])))
    # C1 hook line
    if hl:
        p0 = P[0]
        if hl["start_word"] != p0["start_word"] or not (p0["start_word"] <= hl["end_word"] <= p0["end_word"]):
            out.append(("C1", "reject", "hook_line is not the first played words of piece 0"))
        else:
            hd = W[hl["end_word"]]["end"] - W[hl["start_word"]]["start"]
            if hd > HOOK_MAX_S:
                out.append(("C1", "reject", f"hook line takes {hd:.1f}s > {HOOK_MAX_S:.0f}s"))
    elif kind == "proposals":
        out.append(("C1", "reject", "no hook_line"))
    # C2 demonstrative in first 5 kept words
    first = [norm(W[j]["word"]) for j in kept_words(clip)[:5]]
    if any(w in DEMONSTRATIVES for w in first):
        out.append(("C2", "flag", "starts_with_demonstrative: " + " ".join(first)))
    # C5
    sc = clip.get("scores")
    if kind == "proposals" and sc and min(sc["hook"], sc["standalone"], sc["payoff"]) <= 2:
        out.append(("C5", "hide", f"shown with hook/standalone/payoff <= 2 ({sc})"))
    # S2 / O5 notes, S5 tier cap (proposals only)
    if kind == "proposals":
        notes = clip.get("edit_notes") or []
        n_back = sum(1 for n in notes if n.get("type") in ("cold_open", "reorder"))
        n_skip = sum(1 for n in notes if n.get("type") == "skip")
        want_skip = sum(1 for _, k in J if k == "skip")
        if n_back != len(back):
            out.append(("O5", "reject", f"{n_back} cold_open/reorder notes, expected {len(back)}"))
        if n_skip != want_skip:
            out.append(("S2", "reject", f"{n_skip} skip notes, expected {want_skip}"))
        if lr and clip.get("tier") == "recommended":
            out.append(("S5", "reject", "long-range stitch must be capped at worth_a_look"))
    # R3 teaser duplicate (heuristic)
    if teaser is not None:
        sh, where = teaser.far_share(clip)
        if sh >= TEASER_SHARE:
            later = where > W[P[0]["start_word"]]["start"]
            out.append(("R3", "flag" if later else "info",
                        f"{sh:.0%} of kept 3-grams are also spoken at ~{where:.0f}s"
                        + (" (later): likely a teaser copy of that moment" if later
                           else " (earlier): this is the body copy")))
    return out


CODE_FLAGS = {"dead_air", "over_chopped", "low_keep_ratio", "low_confidence", "song_overlap",
              "starts_with_demonstrative", "over_soft_max", "rough_start", "rough_end", "many_jumps",
              "long_range_stitch"}


def expected_flags(checks):
    """Schema flag names that the code checks say this clip should carry."""
    out = set()
    for _, action, detail in checks:
        name = detail.split(":", 1)[0]
        if name in CODE_FLAGS and action in ("flag", "reject"):
            out.add(name)
    return out


def file_checks(doc, ep):
    """File-level rules for a proposals file (ranks, tiers, cut-off, Q4, Q7)."""
    out = []
    props = doc.get("proposals") or []
    ranks = [p["rank"] for p in props]
    if sorted(ranks) != list(range(1, len(props) + 1)):
        out.append(("Q-rank", "reject", f"ranks not 1..n: {sorted(ranks)[:10]}"))
    cut = doc.get("cutoff") or {}
    t_show, t_rec = cut.get("t_show", 3.5), cut.get("t_rec", 4.0)
    ranked = clips_of(doc)
    below = [p for p in ranked if p["tier"] == "below_threshold"]
    for p in ranked:
        o = p["scores"]["overall"]
        want = "recommended" if o >= t_rec else "worth_a_look" if o >= t_show else "below_threshold"
        if p["tier"] != want:
            out.append(("Q2", "reject", f"rank {p['rank']}: tier {p['tier']} but overall {o} -> {want}"))
    if below and (len(below) != len(ranked) or len(below) > 3):
        out.append(("Q6", "reject", f"{len(below)} below_threshold clips shown alongside others or > 3"))
    for a, b in zip(ranked, ranked[1:]):
        ka = (a["scores"]["overall"], a["scores"]["hook"])
        kb = (b["scores"]["overall"], b["scores"]["hook"])
        if kb > ka:
            out.append(("Q5", "reject", f"rank {b['rank']} scores {kb} above rank {a['rank']} {ka}"))
            break
    if cut.get("n_shown") is not None and cut["n_shown"] != len(props):
        out.append(("Q-count", "reject", f"cutoff.n_shown {cut['n_shown']} != {len(props)} proposals"))
    for i, j, sh in near_dups(ranked):
        out.append(("Q4", "reject", f"ranks {i} and {j} share {sh:.0%} of the smaller clip's kept words"))
    per2 = len(props) / max(1e-6, ep.duration / 120.0)
    if per2 > 1:
        out.append(("Q7", "info", f"{len(props)} shown clips = {per2:.2f} per 2 min of audio (> 1): lenient scorer?"))
    return out
