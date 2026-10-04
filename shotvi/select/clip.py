"""Clip candidate: pieces in playback order, derived metrics (spec section 0), and code-checkable rules."""
from __future__ import annotations

from dataclasses import dataclass, field

from shotvi.segment.text import norm

from .context import DEMONSTRATIVES


@dataclass
class Candidate:
    id: str
    moment: dict
    pieces: list[list[int]]                      # [[start_word, end_word], ...] playback order
    edit_notes: list[dict] = field(default_factory=list)
    flags: set = field(default_factory=set)
    reject: list[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)      # tightener output (point, dropped, reasons), repairs
    scores: dict | None = None
    judge: dict | None = None                    # full scorer output
    judge_first: dict | None = None              # flash judge output when a rescore replaced it
    write: dict | None = None                    # writer output (title, why, category, risks) for shown clips
    related: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    hidden: list[str] = field(default_factory=list)   # C5 / Q4 / threshold reasons (not shown)


def kept_words(pieces) -> list[int]:
    return [i for a, b in pieces for i in range(a, b + 1)]


def metrics(ctx, pieces) -> dict:
    ws = ctx.words
    g = ctx.cfg["guardrails"]
    durs = [ws[b].end - ws[a].start for a, b in pieces]
    edited = sum(durs)
    jumps = []
    for k, ((a0, a1), (b0, b1)) in enumerate(zip(pieces, pieces[1:])):
        if b0 <= a1:
            jumps.append({"after_piece": k, "kind": "backward",
                          "dist": abs(ws[b0].start - ws[a1].end)})
        else:
            gap = ws[b0].start - ws[a1].end
            if gap > g["skip_s"]:
                jumps.append({"after_piece": k, "kind": "skip", "dist": gap})
    # moved duration: edited - max-weight subsequence with strictly increasing start_word (spec section 0)
    n = len(pieces)
    best = [0.0] * n
    for i in range(n):
        best[i] = durs[i] + max([best[j] for j in range(i) if pieces[j][0] < pieces[i][0]], default=0.0)
    moved = edited - (max(best) if best else 0.0)
    long_range = [j for j in jumps if j["dist"] > g["long_range_s"]]
    # region span per side of long-range jumps (R2)
    groups, cur = [], [pieces[0]] if pieces else []
    lr_after = {j["after_piece"] for j in long_range}
    for k in range(1, n):
        if (k - 1) in lr_after:
            groups.append(cur)
            cur = []
        cur.append(pieces[k])
    if cur:
        groups.append(cur)
    spans = [ws[max(p[1] for p in gp)].end - ws[min(p[0] for p in gp)].start for gp in groups]
    r0, r1 = min(p[0] for p in pieces), max(p[1] for p in pieces)
    # overlaps: only piece 0 with one later piece (cold-open replay) is allowed (O4)
    bad_overlap, replay_overlaps = False, 0
    for i in range(n):
        for j in range(i + 1, n):
            if pieces[i][0] <= pieces[j][1] and pieces[j][0] <= pieces[i][1]:
                if i == 0:
                    replay_overlaps += 1
                else:
                    bad_overlap = True
    if replay_overlaps > 1:
        bad_overlap = True
    return {
        "edited": edited, "durs": durs, "jumps": jumps, "n_blocks": 1 + len(jumps),
        "n_backward": sum(j["kind"] == "backward" for j in jumps), "moved": moved,
        "long_range": long_range, "spans": spans, "region": (r0, r1),
        "region_span": ws[r1].end - ws[r0].start, "keep_ratio": edited / max(sum(spans), 1e-6),
        "reordered": any(b[0] <= a[1] for a, b in zip(pieces, pieces[1:])),
        "bad_overlap": bad_overlap, "n_pieces": n,
    }


def hook_span(ctx, pieces) -> tuple[int, int]:
    """Verbatim first played words, up to the first natural boundary after >= 2.5 s and 4 words, within
    hook_within_s."""
    ws = ctx.words
    s, e = pieces[0]
    lim = ctx.cfg["guardrails"]["hook_within_s"]
    end = s
    for k in range(s, e + 1):
        if ws[k].end - ws[s].start > lim:
            break
        end = k
        if ctx.natural_after[k] and ws[k].end - ws[s].start >= 2.5 and k - s >= 3:
            break
    return s, end


def code_checks(ctx, cand: Candidate) -> dict:
    """Apply code-checkable rules. Mutates cand.flags / cand.reject; returns metrics."""
    g = ctx.cfg["guardrails"]
    ws = ctx.words
    P = cand.pieces
    m = metrics(ctx, P)
    rej, fl = cand.reject, cand.flags
    if m["edited"] < g["min_s"]:
        rej.append(f"length: edited {m['edited']:.1f}s < min {g['min_s']}s")
    if m["edited"] > g["hard_max_s"]:
        rej.append(f"length: edited {m['edited']:.1f}s > hard max {g['hard_max_s']}s")
    if m["edited"] > g["soft_max_s"]:
        fl.add("over_soft_max")
    if m["n_blocks"] > g["blocks_max"]:
        rej.append(f"S1: {m['n_blocks']} blocks > {g['blocks_max']}")
    elif m["n_blocks"] > g["blocks_flag_above"]:
        fl.add("many_jumps")
    if m["n_backward"] > g["backward_jumps_max"]:
        rej.append(f"O1: {m['n_backward']} backward jumps")
    if m["moved"] > g["moved_max_s"]:
        rej.append(f"O2: moved duration {m['moved']:.1f}s > {g['moved_max_s']}s")
    if m["bad_overlap"]:
        rej.append("O4: overlapping pieces other than a replayed cold open")
    if len(m["long_range"]) > g["long_range_max"]:
        rej.append(f"S5: {len(m['long_range'])} long-range jumps")
    elif m["long_range"]:
        fl.add("long_range_stitch")
    for sp in m["spans"]:
        if sp > g["region_span_max_s"]:
            rej.append(f"R2: region span {sp:.0f}s > {g['region_span_max_s']:.0f}s")
    # A1 censored overlap
    for a, b in P:
        for cs, ce in ctx.censored:
            if cs < ws[b].end and ce > ws[a].start:
                rej.append(f"A1: piece {a}-{b} overlaps censored event at {cs:.1f}s")
    # R3 excluded (teaser) words
    if any(i in ctx.excluded for i in kept_words(P)):
        rej.append("R3: uses teaser-copy words")
    # T4 / T5 (v0.2: flags; the judge decides)
    s0, e_last = P[0][0], P[-1][1]
    if ctx.lead_in(s0) > g["lead_in_max_words"]:
        fl.add("rough_start")
    if ctx.tail(e_last) > g["tail_max_words"]:
        fl.add("rough_end")
    # T6 dead air
    for a, b in P:
        for i in range(a, b):
            gap = ws[i + 1].start - ws[i].end
            if gap > g["dead_air_s"] and not ctx.overlaps(ctx.laughter, ws[i].end, ws[i + 1].start):
                fl.add("dead_air")
    # T7 over-chopping, T9 keep ratio
    if m["edited"] > 0 and m["n_pieces"] / m["edited"] > g["max_pieces_per_s"]:
        fl.add("over_chopped")
    if m["keep_ratio"] < g["keep_ratio_flag_below"]:
        fl.add("low_keep_ratio")
    # A2 low-confidence runs
    kw = kept_words(P)
    hs, he = hook_span(ctx, P)
    run, run_start = 0, 0
    lp_thr = g["low_conf_logprob"]
    for k, i in enumerate(kw):
        lp = ws[i].logprob
        if lp is not None and lp < lp_thr:
            if run == 0:
                run_start = k
            run += 1
            if run == g["low_conf_run"]:
                if run_start <= he - hs:  # the hook is kept words 0..(he-hs)
                    rej.append(f"A2: low-confidence run in hook near word {i}")
                else:
                    fl.add("low_confidence")
        else:
            run = 0
    # A5 music
    for a, b in P:
        if ctx.overlaps(ctx.music, ws[a].start, ws[b].end):
            fl.add("song_overlap")
    # C2 demonstrative in first 5 kept words
    if any(norm(ws[i].text) in DEMONSTRATIVES for i in kw[:5]):
        fl.add("starts_with_demonstrative")
    # section 11 F7 host-led
    host = ctx.seg.host
    if host is not None and kw:
        share = sum(ws[i].speaker == host for i in kw) / len(kw)
        m["host_share"] = share
        if share > g["host_led_share"]:
            fl.add("host_led")
    return m
