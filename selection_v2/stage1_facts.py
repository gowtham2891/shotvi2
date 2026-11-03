"""Stage 1A, episode facts, Half A: code only, no model call (spec section 4.1).

Input: ``inputs.EpisodeInputs`` (Scribe lines with ASR corrections, the speaker_fix layer).
Output: the ``episode_facts.json`` document (spec appendix) with

  normalize        NFC text, repaired / capped times, zero-duration filler lines marked, tag classes
  gap table        every pair of consecutive dialogue lines: gap, what filled it, speaker sets 30 s before / after
                   (filler lines ignored), percentile within THIS episode
  tag events       whitelisted tags (class != other) with the preceding dialogue line (literal) and the preceding
                   CONTENT line (filler lines skipped)
  boundary         a score per gap from RELATIVE evidence (gap percentile, speaker turnover, tag class);
  proposals        a proposal needs two evidence types, or total speaker turnover alone (the only trace a hard cut
                   leaves); top-k after light non-max suppression; every gap keeps its score and evidence
  suspects         structural flags in BOTH label spaces (speaker_fix keys and Scribe labels): lone speaker in a
                   provisional segment, filler-only speaker, overlapping line; plus speaker_fix confidence flags
  speaker summary  per corrected speaker: the proposal-delimited segments they appear in, line counts, co-speakers

Times are floats (seconds); ``fmt_ts`` renders ``m:ss.s`` for display. Stage 1A decides nothing about clips.
"""
from __future__ import annotations

import bisect
import time
import unicodedata

from .config import load_cast_lists, series_for

VERSION = "stage1a/0.5"
SCHEMA = "episode_facts/1"


# ----------------------------------------------------------------------------------------------- helpers
def fmt_ts(t: float) -> str:
    m = int(t // 60)
    return f"{m}:{t - 60 * m:04.1f}"


def parse_ts(s) -> tuple[float, str | None]:
    """``'m:ss.s'`` / ``'h:mm:ss'`` / number -> seconds. Malformed minute/second carries (``1:60.0`` -> ``2:00.0``)
    are repaired and reported in the second value."""
    if isinstance(s, (int, float)):
        return float(s), None
    parts = str(s).strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"bad timestamp {s!r}")
    nums = [float(x) for x in parts]
    note = None
    while len(nums) < 3:
        nums.insert(0, 0.0)
    h, m, sec = nums
    if sec >= 60:
        m += int(sec // 60)
        sec = sec % 60
        note = f"seconds >= 60 in {s!r}"
    if m >= 60:
        h += int(m // 60)
        m = m % 60
        note = f"minutes >= 60 in {s!r}"
    return h * 3600 + m * 60 + sec, note


_PUNCT = "".join(chr(c) for c in range(0x21, 0x7F) if not chr(c).isalnum()) + "।॥“”‘’…–—"


def tokens_of(text: str) -> list[str]:
    out = []
    for tok in unicodedata.normalize("NFC", text).split():
        t = tok.strip(_PUNCT).lower()
        if t:
            out.append(t)
    return out


def is_telugu(s: str) -> bool:
    return any("ఀ" <= ch <= "౿" for ch in s)


def classify_tag(text: str, tags_cfg: dict) -> str:
    """Free-form tag text -> class: first class in ``order`` with a case-insensitive substring match, else other."""
    t = unicodedata.normalize("NFC", text).strip().strip("[]()").lower()
    for cls in tags_cfg["order"]:
        for pat in tags_cfg["classes"].get(cls) or []:
            if unicodedata.normalize("NFC", str(pat)).lower() in t:
                return cls
    return "other"


def spk(r: dict) -> str:
    """The speaker label a line carries downstream: the speaker_fix key, else Scribe's label marked as such."""
    return r.get("speaker_key") or f"scribe:{r.get('speaker')}"


def spk_scribe(r: dict) -> str:
    return str(r.get("speaker"))


def is_filler_line(r: dict, cfg: dict) -> bool:
    """Zero-length, or <= filler.max_s and made only of filler tokens (config stage1a.filler)."""
    if r["kind"] != "speech":
        return False
    if r.get("zero_len"):
        return True
    f = cfg["stage1a"]["filler"]
    if r["end"] - r["start"] > float(f["max_s"]) + 1e-6:
        return False
    toks = tokens_of(r["text"])
    fillers = {unicodedata.normalize("NFC", str(x)).lower() for x in f["tokens"]}
    return bool(toks) and all(t in fillers for t in toks)


def percentile_value(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = min(len(sorted_vals) - 1, max(0, int(round(p * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


# ----------------------------------------------------------------------------------------------- normalize
def normalize(inputs, cfg: dict) -> tuple[list[dict], list[dict]]:
    c = cfg["stage1a"]
    eps, wmax, dur = float(c["zero_len_eps_s"]), c.get("word_max_s"), inputs.duration
    words = inputs.words
    out, fixes = [], []
    for r in inputs.lines:
        start, end = float(r["start"]), float(r["end"])
        text = unicodedata.normalize("NFC", r.get("text") or "")
        if text != (r.get("text") or ""):
            fixes.append({"line": r["id"], "field": "text", "from": r.get("text"), "to": text, "reason": "NFC"})
        rec = {"id": r["id"], "kind": r["kind"], "start": start, "end": end, "end_raw": end,
               "speaker": r.get("speaker") if r["kind"] == "speech" else None,
               "speaker_key": r.get("speaker_key"), "speaker_name": r.get("speaker_name"),
               "speaker_confidence": r.get("speaker_confidence"),
               "text": text, "text_scribe": unicodedata.normalize("NFC", r.get("text_scribe") or ""),
               "tag": None, "tag_class": None, "zero_len": False, "filler": False,
               "sentence_id": r.get("sentence_id"), "words": r.get("words"), "n_words": 0}
        if end < start:
            fixes.append({"line": r["id"], "field": "end", "from": end, "to": start, "reason": "end before start"})
            rec["end"] = end = start
        if dur and end > dur + 0.5:
            fixes.append({"line": r["id"], "field": "end", "from": end, "to": dur, "reason": "beyond episode end"})
            rec["end"] = end = dur
        if r["kind"] == "speech":
            w = r.get("words")
            if w:
                rec["n_words"] = int(w[1]) - int(w[0]) + 1
                if wmax and 0 <= w[1] < len(words):
                    cap = float(words[w[1]]["start"]) + float(wmax)
                    if end > cap + 1e-6:
                        fixes.append({"line": r["id"], "field": "end", "from": end, "to": round(cap, 3),
                                      "reason": f"last word stretched {end - words[w[1]]['start']:.1f} s; capped at "
                                                f"word_max_s {wmax}"})
                        rec["end"] = end = round(cap, 3)
            if end - start <= eps:
                rec["zero_len"] = True
                fixes.append({"line": r["id"], "field": "zero_len", "from": False, "to": True,
                              "reason": f"zero-duration filler {text!r}"})
            rec["filler"] = is_filler_line(rec, cfg)
        else:
            rec["tag"] = text
            rec["tag_class"] = classify_tag(text, cfg["tags"])
            rec["speaker_key"] = rec["speaker_name"] = rec["speaker_confidence"] = None
        out.append(rec)
    out.sort(key=lambda x: (x["start"], x["kind"] != "event", x["id"]))
    return out, fixes


# ----------------------------------------------------------------------------------------------- gap table
def dialogue_lines(lines: list[dict]) -> list[dict]:
    return [r for r in lines if r["kind"] == "speech" and not r["zero_len"]]


def build_gaps(lines: list[dict], cfg: dict) -> list[dict]:
    c = cfg["stage1a"]
    win = float(c["context_window_s"])
    dlg = dialogue_lines(lines)
    content = [r for r in dlg if not r.get("filler")]     # speaker sets ignore filler lines
    events = [r for r in lines if r["kind"] == "event"]
    # cast entry / exit, each attributed to ONE gap: the gap just before a speaker's first content line and the gap
    # just after their last content line, skipping over other people's filler lines in between
    pos = {r["id"]: i for i, r in enumerate(dlg)}
    first_idx: dict[str, int] = {}
    last_idx: dict[str, int] = {}
    for r in content:
        first_idx.setdefault(spk(r), pos[r["id"]])
        last_idx[spk(r)] = pos[r["id"]]
    entry_at: dict[int, set] = {}
    exit_at: dict[int, set] = {}
    for x, j in first_idx.items():
        k = j - 1
        while k >= 0 and dlg[k].get("filler") and spk(dlg[k]) != x:
            k -= 1
        if k >= 0:
            entry_at.setdefault(k, set()).add(x)        # gap index k = gap between dlg[k] and dlg[k+1]
    for x, j in last_idx.items():
        k = j + 1
        while k < len(dlg) and dlg[k].get("filler") and spk(dlg[k]) != x:
            k += 1
        if k < len(dlg):
            exit_at.setdefault(k - 1, set()).add(x)
    gaps = []
    for gi, (a, b) in enumerate(zip(dlg, dlg[1:])):
        raw = round(b["start"] - a["end"], 3)
        g = max(0.0, raw)
        filled = []
        for e in events:
            if e["end"] > a["end"] - 1e-6 and e["start"] < b["start"] + 1e-6:
                ov = min(e["end"], b["start"]) - max(e["start"], a["end"])
                inside = e["start"] >= a["end"] - 0.05 and e["end"] <= b["start"] + 0.05
                if inside or ov >= 0.2:
                    filled.append({"line": e["id"], "class": e["tag_class"], "tag": e["tag"]})
        classes = [f["class"] for f in filled]
        primary = next((x for x in classes if x != "other"), ("other" if classes else None))
        # lines that STARTED before the gap (within the window) vs lines that END after it: a line that starts
        # exactly where the previous one ends (a hard cut) belongs to "after" only
        before = {spk(r) for r in content if r["end"] > a["end"] - win and r["start"] < a["end"] - 1e-6}
        after = {spk(r) for r in content if r["start"] < b["start"] + win and r["end"] > b["start"] + 1e-6}
        # a filler by the speaker who was just talking ("ఆ" while the other replies) is continuation, so the gap's own
        # prev-line speaker stays in `after` (and the next-line speaker in `before`) when they have a filler there;
        # a filler by anyone else never adds a speaker (the stray 'హా' at a hard cut)
        fc = float(c["filler"].get("continuation_s", 5.0))
        if any(r.get("filler") and spk(r) == spk(a) and b["start"] - 1e-6 < r["start"] < b["start"] + fc for r in dlg):
            after.add(spk(a))
        if any(r.get("filler") and spk(r) == spk(b) and a["end"] - fc < r["end"] < a["end"] + 1e-6 for r in dlg):
            before.add(spk(b))
        before, after = sorted(before), sorted(after)
        entries = sorted(entry_at.get(gi, ()))
        exits = sorted(exit_at.get(gi, ()))
        # an entry / exit attributed to a neighbouring gap within cast_change_s (a one-word reply between the last
        # line of the leaver and the first line of the newcomer) still counts towards a swap at this gap
        cs = float(c["boundary"].get("cast_change_s", 0) or 0)
        near_e = sorted({x for k, xs in entry_at.items() for x in xs
                         if k != gi and abs(dlg[k + 1]["start"] - b["start"]) <= cs} - set(entries))
        near_x = sorted({x for k, xs in exit_at.items() for x in xs
                         if k != gi and abs(dlg[k]["end"] - a["end"]) <= cs} - set(exits))
        gaps.append({"prev": a["id"], "next": b["id"], "t_prev_end": a["end"], "t_next_start": b["start"],
                     "gap": round(g, 3), "gap_raw": raw, "overlap": raw < -float(c["overlap_tol_s"]),
                     "filled_by": primary, "filled_by_classes": classes, "filled_by_lines": [f["line"] for f in filled],
                     "spk_before": before, "spk_after": after,
                     "entries": entries, "exits": exits, "entries_near": near_e, "exits_near": near_x,
                     "prev_filler": bool(a.get("filler")), "next_filler": bool(b.get("filler"))})
    vals = sorted(g["gap"] for g in gaps)
    n = len(vals)
    for g in gaps:
        # share of this episode's gaps that are STRICTLY shorter (ties rank low: a common 0.5 s gap is not "top 5%")
        g["gap_pct"] = round(bisect.bisect_left(vals, g["gap"]) / n, 3) if n else 0.0
    return gaps


def build_tag_events(lines: list[dict]) -> list[dict]:
    dlg = dialogue_lines(lines)
    content = [r for r in dlg if not r.get("filler")]
    ends = [r["end"] for r in dlg]
    cends = [r["end"] for r in content]
    out = []
    for e in lines:
        if e["kind"] != "event" or e["tag_class"] in (None, "other"):
            continue
        k = bisect.bisect_right(ends, e["start"] + 1e-6) - 1
        kc = bisect.bisect_right(cends, e["start"] + 1e-6) - 1
        inside = next((r["id"] for r in dlg if r["start"] <= e["start"] < r["end"]), None)   # a bleep inside a line
        out.append({"line": e["id"], "t": e["start"], "end": e["end"], "dur": round(e["end"] - e["start"], 3),
                    "class": e["tag_class"], "tag": e["tag"], "after": dlg[k]["id"] if k >= 0 else None,
                    "after_content_line": content[kc]["id"] if kc >= 0 else None, "inside": inside})
    return out


# ----------------------------------------------------------------------------------------------- boundaries
def score_gaps(gaps: list[dict], cfg: dict) -> None:
    """Write ``boundary_score`` / ``boundary_evidence`` / ``boundary_components`` / ``qualifies`` into every gap."""
    b = cfg["stage1a"]["boundary"]
    w = b["weights"]
    core = {k: v for k, v in w.items() if k != "cast_change"}   # cast_change is a bonus on top, not in the denominator
    wsum = float(sum(core.values())) or 1.0
    soft, full = float(b["gap_soft_percentile"]), float(b["gap_full_percentile"])
    need = int(b.get("min_evidence_types", 1))
    alone = bool(b.get("turnover_alone_ok", True))
    for g in gaps:
        ev, comp = [], {}
        pct = g["gap_pct"]
        if pct >= full:
            comp["gap"] = 1.0
        elif pct > soft and full > soft:
            comp["gap"] = round((pct - soft) / (full - soft), 3)
        else:
            comp["gap"] = 0.0
        if comp["gap"] > 0:
            ev.append(f"gap p{int(pct * 100)} ({g['gap']:.1f} s)")
        bs, as_ = set(g["spk_before"]), set(g["spk_after"])
        comp["turnover"] = round(1.0 - len(bs & as_) / len(bs | as_), 3) if (bs and as_) else 0.0
        total = comp["turnover"] >= 0.999
        if total:
            ev.append("turnover: total speaker change")
        elif comp["turnover"] > 0:
            ev.append(f"turnover {comp['turnover']:.2f}")
        hit = [x for x in g["filled_by_classes"] if x in b["tag_classes"]]
        comp["tag"] = 1.0 if hit else 0.0
        if hit:
            ev.append("tag:" + ",".join(sorted(set(hit))))
        if g["gap"] <= float(b["hard_cut_max_gap_s"]) and total:
            ev.append("hard_cut")
        ent, ex = g.get("entries") or [], g.get("exits") or []
        ent_all = ent + (g.get("entries_near") or [])
        ex_all = ex + (g.get("exits_near") or [])
        swap = bool(ent_all and ex_all) and bool(ent or ex)      # the gap itself must carry one of the two
        if "cast_change" in w:
            comp["cast_change"] = 1.0 if (ent or ex) else 0.0
            if ent or ex:
                ev.append("cast " + ("swap: " if swap else "change: ")
                          + " ".join([f"+{x}" for x in ent] + [f"-{x}" for x in ex]
                                     + [f"+{x}~" for x in g.get("entries_near") or []] + [f"-{x}~" for x in g.get("exits_near") or []]))
        g["boundary_components"] = comp
        g["boundary_score"] = round(min(1.0, sum(core[k] * comp[k] for k in core) / wsum
                                        + float(w.get("cast_change", 0)) * comp.get("cast_change", 0.0)), 3)
        g["boundary_evidence"] = ev
        g["n_evidence_types"] = sum(1 for k in w if comp[k] > 0)
        g["total_turnover"] = total
        g["cast_swap"] = swap
        g["qualifies"] = (g["boundary_score"] >= float(b["min_score"]) and g["n_evidence_types"] >= need) \
            or (alone and total) or (bool(b.get("cast_swap_alone_ok", True)) and swap)


def propose_boundaries(gaps: list[dict], cfg: dict) -> list[dict]:
    b = cfg["stage1a"]["boundary"]
    top_k, sep = int(b["top_k"]), float(b["min_separation_s"])
    # total speaker turnover is the hard-cut signature and scores low on its own (no gap, no tag): such gaps are
    # never dropped by the cap; the cap applies to the rest, by score
    def exempt(g):
        return bool(g.get("total_turnover")) or bool(g.get("cast_swap"))

    def t_of(g):
        # the proposal time is the next line's start (shot cuts are not used: never checked, and they missed real cuts)
        return g["t_next_start"]

    def mk(g, why):
        return {"t": round(t_of(g), 3), "t_str": fmt_ts(t_of(g)), "score": g["boundary_score"],
                "evidence": g["boundary_evidence"], "prev": g["prev"], "next": g["next"],
                "t_prev_end": g["t_prev_end"], "t_next_start": g["t_next_start"], "gap": g["gap"],
                "spk_before": g["spk_before"], "spk_after": g["spk_after"],
                "total_turnover": bool(g.get("total_turnover")), "cast_swap": bool(g.get("cast_swap")),
                "selected_by": why}

    ranked = sorted((g for g in gaps if g.get("qualifies")),
                    key=lambda g: (not exempt(g), -g["boundary_score"], g["t_next_start"]))
    chosen: list[dict] = []
    n_capped = 0
    for g in ranked:
        if not exempt(g) and n_capped >= top_k:
            break
        if any(abs(t_of(g) - p["t"]) < sep for p in chosen):
            continue
        chosen.append(mk(g, "total_turnover" if g.get("total_turnover") else "cast_swap" if g.get("cast_swap") else "score"))
        if not exempt(g):
            n_capped += 1
    # coverage: no stretch longer than coverage_max_unbroken_s stays without a proposal while a qualifying gap is in it
    max_un = float(b.get("coverage_max_unbroken_s") or 0)
    if max_un > 0:
        dur = max((g["t_next_start"] for g in gaps), default=0.0)
        while True:
            edges = sorted(p["t"] for p in chosen)
            bounds = [0.0] + edges + [dur]
            added = False
            for lo, hi in zip(bounds, bounds[1:]):
                if hi - lo <= max_un:
                    continue
                inside = [g for g in gaps if g.get("qualifies") and lo + sep <= t_of(g) <= hi - sep
                          and not any(abs(t_of(g) - p["t"]) < sep for p in chosen)]
                if inside:
                    best = max(inside, key=lambda g: (g["boundary_score"], -abs(t_of(g) - (lo + hi) / 2)))
                    chosen.append(mk(best, "coverage"))
                    added = True
                    break
            if not added:
                break
    chosen.sort(key=lambda p: p["t"])
    for i, p in enumerate(chosen, 1):
        p["rank"] = i
    return chosen


# ----------------------------------------------------------------------------------------------- cast names
def cast_names(inputs, cast_cfg: dict, cfg: dict | None = None) -> tuple[dict, dict]:
    """``(names, info)``: canonical name -> spellings, from the per-series config plus the speaker_fix cast
    (name and aka). ``info`` records where they came from."""
    title = inputs.meta.get("title") or inputs.paths.title()
    key, series = series_for(inputs.vid, title, cast_cfg)
    names: dict[str, list[str]] = {}
    for canon, entry in (series.get("cast") or {}).items():
        variants = entry.get("aka") if isinstance(entry, dict) else entry     # [spellings] or {aka, gender}
        names.setdefault(canon, [])
        for v in ([canon] + list(variants or [])):
            if v and v not in names[canon]:
                names[canon].append(str(v))
    n_fix = 0
    skipped = []
    role_words = {str(x).lower() for x in ((cfg or {}).get("stage1a", {}).get("cast_names", {}).get("role_words") or [])}
    for c in inputs.speaker_fix.get("cast") or []:
        canon = (c.get("name") or "").strip()
        if not canon:
            continue
        toks = {t.strip(_PUNCT).lower() for t in canon.split()}
        if "'s" in canon.lower() or (toks & role_words):
            skipped.append(canon)       # a role descriptor, not a name
            continue
        n_fix += 1
        names.setdefault(canon, [])
        for v in [canon] + list(c.get("aka") or []):
            if v and v not in names[canon]:
                names[canon].append(str(v))
    return names, {"series_key": key, "series": series.get("label"), "n_config_names": len(series.get("cast") or {}),
                   "n_speaker_fix_names": n_fix, "speaker_fix_names_skipped": skipped}


# ----------------------------------------------------------------------------------------------- suspects
def segment_index(proposals: list[dict]) -> list[float]:
    return [p["t"] for p in proposals]


def seg_label(edges: list[float], seg: int) -> str:
    a = fmt_ts(edges[seg - 1]) if seg else "0:00.0"
    b = fmt_ts(edges[seg]) if seg < len(edges) else "end"
    return f"{a}-{b}"


def structural_suspects(lines: list[dict], proposals: list[dict], cfg: dict, label=spk, label_space: str = "fix") -> list[dict]:
    """Structural flags in one label space (``label`` maps a line to its speaker label)."""
    c = cfg["stage1a"]["suspects"]
    tol = float(cfg["stage1a"]["overlap_tol_s"])
    speech = [r for r in lines if r["kind"] == "speech"]
    edges = segment_index(proposals)
    seg_of = {r["id"]: bisect.bisect_right(edges, r["start"]) for r in speech}
    members: dict[int, dict[str, list[str]]] = {}
    for r in speech:
        members.setdefault(seg_of[r["id"]], {}).setdefault(label(r), []).append(r["id"])
    by_id = {r["id"]: r for r in speech}
    out = []

    def rec(r, reason, detail, x, lines_=None):
        d = {"line": r["id"] if r else None, "label_space": label_space, "speaker": x,
             "speaker_name": (r or by_id[lines_[0]]).get("speaker_name"), "scribe": (r or by_id[lines_[0]]).get("speaker"),
             "fix_key": (r or by_id[lines_[0]]).get("speaker_key"), "reason": reason, "detail": detail}
        if lines_:
            d["lines"] = lines_
        return d

    for seg, byspk in members.items():
        for x, ids in byspk.items():
            if len(ids) > int(c["single_line_max"]):
                continue
            others = set(byspk) - {x}
            if not others:   # alone in a tiny segment: judge against the neighbouring segments' people
                others = (set(members.get(seg - 1, {})) | set(members.get(seg + 1, {}))) - {x}
            if not others:
                continue
            elsewhere = any(x in m and (others & set(m)) for s2, m in members.items() if s2 != seg)
            if not elsewhere:
                for lid in ids:
                    out.append(rec(by_id[lid], "lone_speaker",
                                   f"only line of {x} in provisional segment {seg} ({seg_label(edges, seg)}); never seen "
                                   f"with {', '.join(sorted(others))} elsewhere", x))
    by_spk: dict[str, list[dict]] = {}
    for r in speech:
        by_spk.setdefault(label(r), []).append(r)
    for x, rs in by_spk.items():
        if all(r["n_words"] <= int(c["filler_max_words"]) and (r["end"] - r["start"]) <= float(c["filler_max_s"])
               for r in rs):
            out.append(rec(None, "filler_only_speaker",
                           f"{len(rs)} line(s), all <= {c['filler_max_words']} words: likely a merge artifact", x,
                           [r["id"] for r in rs]))
    # a speaker whose ONLY lines in a segment are filler ('హా', 'ఆ'...) and who has no line in the next segment: the
    # usual signature of a grunt attached to the wrong person across a scene change
    seg_speakers = {seg: set(m) for seg, m in members.items()}
    for seg, byspk in members.items():
        for x, ids in byspk.items():
            rs = [by_id[i] for i in ids]
            if all(r.get("filler") for r in rs) and x not in seg_speakers.get(seg + 1, set()):
                for r in rs:
                    out.append(rec(r, "filler_island",
                                   f"{x}'s only line(s) in segment {seg} ({seg_label(edges, seg)}) are filler and {x} is "
                                   f"absent from the next segment", x))
    prev = None
    for r in speech:
        if prev is not None and r["start"] < prev["end"] - tol and label(r) != label(prev):
            out.append(rec(r, "overlaps_previous_line",
                           f"starts {prev['end'] - r['start']:.2f} s before {prev['id']} ({label(prev)}) ends", label(r)))
        if prev is None or r["end"] >= prev["end"]:
            prev = r
    return out


def speaker_segments(lines: list[dict], proposals: list[dict], min_presence: int = 3) -> list[dict]:
    """Per corrected speaker key: the proposal-delimited segments they appear in, line counts, co-speakers.
    A speaker is PRESENT in a segment only with >= ``min_presence`` lines there or with a co-speaker (a 2-line solo tail
    after a scene does not count), so isolation, exclusive partners and anchoring are not fooled by fine segments.
    ``anchored`` on a segment entry: the speaker is isolated, or every co-speaker there is an exclusive partner
    (someone who never appears without them): Stage 1B then asks who this speaker is with the prior name withheld."""
    speech = [r for r in lines if r["kind"] == "speech"]
    edges = segment_index(proposals)
    seg_of = {r["id"]: bisect.bisect_right(edges, r["start"]) for r in speech}
    raw: dict[int, dict[str, int]] = {}
    for r in speech:
        raw.setdefault(seg_of[r["id"]], {})
        raw[seg_of[r["id"]]][spk(r)] = raw[seg_of[r["id"]]].get(spk(r), 0) + 1
    members: dict[int, dict[str, int]] = {}
    for seg, byspk in raw.items():
        keep = {k: n for k, n in byspk.items() if n >= int(min_presence) or len(byspk) > 1}
        if keep:
            members[seg] = keep
    out = []
    keys = {}
    for r in speech:
        k = spk(r)
        d = keys.setdefault(k, {"key": k, "name": r.get("speaker_name"), "scribe_labels": set(), "n_lines": 0,
                                "first": r["start"], "last": r["end"], "segments": {}})
        d["n_lines"] += 1
        d["scribe_labels"].add(str(r.get("speaker")))
        d["first"], d["last"] = min(d["first"], r["start"]), max(d["last"], r["end"])
        d["segments"][seg_of[r["id"]]] = d["segments"].get(seg_of[r["id"]], 0) + 1
    for k, d in keys.items():
        segs = []
        present = [seg for seg in sorted(d["segments"]) if k in members.get(seg, {})]
        only_here = len(present) <= 1
        for seg, n in sorted(d["segments"].items()):
            if k not in members.get(seg, {}):
                continue                                   # a tail too small to count as presence
            co = sorted(x for x in members[seg] if x != k)
            # exclusive partner: a co-speaker who never appears in any segment WITHOUT this speaker (the delivery
            # customer who only ever exists next to one other character)
            excl = [x for x in co if all(k in members[s2] for s2 in members if x in members[s2])]
            lo = edges[seg - 1] if seg else 0.0
            hi = edges[seg] if seg < len(edges) else float("inf")
            ids = [r["id"] for r in speech if spk(r) == k and seg_of[r["id"]] == seg]
            segs.append({"segment": seg, "range": seg_label(edges, seg), "t0": lo, "t1": hi if hi != float("inf") else None,
                         "n_lines": n, "co_speakers": co, "co_speakers_exclusive": excl, "line_ids": ids,
                         "anchored": bool(only_here or (co and set(co) <= set(excl)))})
        out.append({"key": k, "name": d["name"], "scribe_labels": sorted(d["scribe_labels"]), "n_lines": d["n_lines"],
                    "n_segments": len(segs), "first": fmt_ts(d["first"]), "last": fmt_ts(d["last"]),
                    "segments": segs, "isolated": only_here,
                    "n_anchored_lines": sum(len(s["line_ids"]) for s in segs if s["anchored"]),
                    "exclusive_partners": sorted({x for s in segs for x in s["co_speakers_exclusive"]}),
                    "segments_str": "; ".join(
                        f"{s['range']} ({s['n_lines']} with "
                        f"{' '.join(c + ('*' if c in s['co_speakers_exclusive'] else '') for c in s['co_speakers']) or '-'})"
                        for s in segs)})
    out.sort(key=lambda d: -d["n_lines"])
    return out   # '*' in segments_str marks an exclusive partner: a co-speaker who never appears without this speaker


def speaker_flags(lines: list[dict], cfg: dict) -> list[dict]:
    c = cfg["stage1a"]["suspects"]
    out = []
    for r in lines:
        if r["kind"] != "speech":
            continue
        conf = r.get("speaker_confidence")
        if r.get("speaker_key") is None:
            out.append({"line": r["id"], "confidence": conf, "flag": "unresolved", "speaker": spk(r),
                        "speaker_name": None})
        elif conf is not None and conf <= float(c["low_confidence_max"]):
            out.append({"line": r["id"], "confidence": conf, "flag": "suspect", "speaker": spk(r),
                        "speaker_name": r.get("speaker_name")})
        elif conf is not None and abs(conf - float(c["low_mark"])) < 1e-6:
            out.append({"line": r["id"], "confidence": conf, "flag": "low", "speaker": spk(r),
                        "speaker_name": r.get("speaker_name")})
    return out


# ----------------------------------------------------------------------------------------------- run
def run(inputs, facts: dict | None, cfg: dict, cast_cfg: dict | None = None) -> dict:
    t0 = time.perf_counter()
    cast_cfg = cast_cfg if cast_cfg is not None else load_cast_lists()
    lines, fixes = normalize(inputs, cfg)
    gaps = build_gaps(lines, cfg)
    score_gaps(gaps, cfg)
    proposals = propose_boundaries(gaps, cfg)
    tags = build_tag_events(lines)
    names, cast_info = cast_names(inputs, cast_cfg, cfg)
    suspects = (structural_suspects(lines, proposals, cfg, spk, "fix")
                + structural_suspects(lines, proposals, cfg, spk_scribe, "scribe"))
    flags = speaker_flags(lines, cfg)
    segs = speaker_segments(lines, proposals, int(cfg["stage1a"]["suspects"].get("min_lines_for_presence", 3)))
    dlg = dialogue_lines(lines)
    dur = inputs.duration
    first = dlg[0]["start"] if dlg else None
    last = max((r["end"] for r in dlg), default=None)
    vals = sorted(g["gap"] for g in gaps)
    pcts = {f"p{p}": round(percentile_value(vals, p / 100.0), 2) for p in cfg["stage1a"]["gap_percentiles"]}
    scribe_spk = sorted({r["speaker"] for r in lines if r["kind"] == "speech" and r["speaker"]})
    cast = [{"key": c.get("key"), "name": c.get("name"), "desc": c.get("desc"), "aka": c.get("aka") or [],
             "n_lines": c.get("n_lines")} for c in inputs.speaker_fix.get("cast") or []]
    suspect_ids = sorted({s["line"] for s in suspects if s.get("line")} | {f["line"] for f in flags if f["flag"] == "suspect"})
    warnings = list(inputs.warnings)
    old_stages = sorted(k for k in ((facts or {}).get("stages") or {}) if k != "1a")
    if old_stages:
        warnings.append(f"re-running 1a dropped the earlier outputs of stage(s) {', '.join(old_stages)}")
    summary = {
        "n_lines": len(lines), "n_speech": sum(r["kind"] == "speech" for r in lines),
        "n_events": sum(r["kind"] == "event" for r in lines), "n_zero_len": sum(r["zero_len"] for r in lines),
        "n_filler_lines": sum(r.get("filler", False) for r in lines),
        "n_dialogue": len(dlg), "n_gaps": len(gaps), "gap_percentiles_s": pcts,
        "n_gaps_qualifying": sum(g["qualifies"] for g in gaps),
        "n_tag_events": len(tags), "tag_classes": _count(e["class"] for e in tags),
        "n_censored": sum(e["class"] == "censored" for e in tags),
        "n_proposals": len(proposals),
        "n_structural_suspects": _count(s["label_space"] for s in suspects), "n_speaker_flags": _count(f["flag"] for f in flags),
        "n_isolated_speakers": sum(s["isolated"] for s in segs),
        "n_normalization_fixes": len(fixes), "n_asr_corrected_lines": inputs.layers.get("asr_fix_lines_corrected", 0),
    }
    return {
        "schema": SCHEMA,
        "episode": {"video_id": inputs.vid, "series": cast_info["series"], "series_key": cast_info["series_key"],
                    "title": inputs.meta.get("title"), "channel": inputs.meta.get("channel"),
                    "duration": round(dur, 3), "duration_str": fmt_ts(dur),
                    "first_dialogue": first, "first_dialogue_str": fmt_ts(first) if first is not None else None,
                    "last_dialogue": last, "last_dialogue_str": fmt_ts(last) if last is not None else None,
                    "outro_s": round(dur - last, 3) if last is not None else None,
                    "speakers": cast, "scribe_speakers": scribe_spk},
        "layers": dict(inputs.layers),
        "lines": lines, "gaps": gaps, "tag_events": tags, "boundary_proposals": proposals,
        "cast_list": {**cast_info, "names": names},
        "structural_suspects": suspects, "structural_suspect_ids": suspect_ids, "speaker_flags": flags,
        "speaker_segments": segs,
        "normalization_fixes": fixes,
        "stages": {"1a": {"version": VERSION, "config_sha8": cfg.get("_sha8"),
                          "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          "duration_s": round(time.perf_counter() - t0, 3), "warnings": warnings,
                          "usage": {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "cost_inr": 0.0},
                          "summary": summary}},
    }


def _count(it) -> dict:
    out: dict = {}
    for x in it:
        out[x] = out.get(x, 0) + 1
    return out


# ----------------------------------------------------------------------------------------------- diarization stats
def diarization_stats(lines: list[dict], line_ids: list[str], reassigned_ids: set | None = None, keys: dict | None = None) -> dict:
    """How much the speaker map inside a stretch of lines rests on thin diarization (Stage 1A addition, 2026-10-26).
    raw_labels = distinct Scribe labels; map_speakers = distinct speaker-fix keys; reassigned = lines whose key is
    not the majority key of their Scribe label within the stretch (the fix split one label into several people), plus
    any ids in ``reassigned_ids`` (1B reassignments)."""
    ids = set(line_ids)
    rows = [r for r in lines if r["id"] in ids and r["kind"] == "speech" and not r.get("zero_len")]
    kof = (lambda r: (keys or {}).get(r["id"]) or r.get("speaker_key"))
    raw = {r.get("speaker") for r in rows if r.get("speaker")}
    mkeys = {kof(r) for r in rows if kof(r)}
    by_label: dict[str, dict[str, int]] = {}
    for r in rows:
        by_label.setdefault(str(r.get("speaker")), {})
        by_label[str(r.get("speaker"))][str(kof(r))] = by_label[str(r.get("speaker"))].get(str(kof(r)), 0) + 1
    majority = {lab: max(d.items(), key=lambda kv: kv[1])[0] for lab, d in by_label.items()}
    re = {r["id"] for r in rows if str(kof(r)) != majority.get(str(r.get("speaker")))}
    re |= (set(reassigned_ids or ()) & {r["id"] for r in rows})
    n = len(rows)
    return {"n_lines": n, "raw_labels": sorted(raw), "n_raw_labels": len(raw), "map_speakers": sorted(mkeys),
            "n_map_speakers": len(mkeys), "n_reassigned": len(re),
            "reassigned_fraction": round(len(re) / n, 3) if n else 0.0, "reassigned_ids": sorted(re)}


def is_thin_diarization(stats: dict, max_fraction: float = 0.33) -> bool:
    return stats["n_raw_labels"] < stats["n_map_speakers"] or stats["reassigned_fraction"] > float(max_fraction)
