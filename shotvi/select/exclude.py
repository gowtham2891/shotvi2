"""Word ranges that must never be used as clip material (spec R3 teasers, and anything else upstream excludes).

Source: ``Segmentation.excluded_word_ranges()`` (teaser montage, opening music, greeting / host intro). For older
segment versions, list attributes ``teaser_ranges`` / ``teasers`` / ``excluded_ranges`` are honoured.

Fallback when upstream has no detector (provisional, ``provisional_teaser``): in the first ``teaser.head_s`` seconds, a unit whose
word n-grams mostly recur later in the episode is a teaser copy. If there are two or more, everything from the
first to the last such unit is treated as the montage (the connective bits between the copies carry no context
either). Nothing here depends on a particular episode.
"""
from __future__ import annotations

from shotvi.segment.text import norm


def _as_range(x):
    if isinstance(x, dict):
        if "words" in x:
            return int(x["words"][0]), int(x["words"][1])
        return int(x.get("i0", x.get("start_word"))), int(x.get("i1", x.get("end_word")))
    if hasattr(x, "i0") and hasattr(x, "i1"):
        return int(x.i0), int(x.i1)
    return int(x[0]), int(x[1])


def upstream_ranges(seg) -> list[tuple[int, int, str]] | None:
    if hasattr(seg, "excluded_word_ranges") and hasattr(seg, "intro_ranges"):
        # shotvi.segment >= teaser detector: teaser montage, opening music, greeting / host intro
        return [(r.i0, r.i1, f"R3/A4: {r.kind} range from shotvi.segment") for r in seg.intro_ranges
                if (r.i0, r.i1) in set(seg.excluded_word_ranges())]
    for attr in ("teaser_ranges", "teasers", "excluded_ranges"):
        val = getattr(seg, attr, None)
        if val is None and isinstance(getattr(seg, "video_signals", None), dict):
            val = seg.video_signals.get(attr)
        if val:
            return [(*_as_range(v), f"R3: {attr} from shotvi.segment") for v in val]
        if val is not None:  # present but empty: upstream says there is no teaser
            return []
    return None


def provisional_teaser(ctx) -> list[tuple[int, int, str]]:
    c = ctx.cfg["teaser"]
    ws = ctx.words
    n = c["ngram"]
    toks = [norm(w.text) for w in ws]
    head_end = ctx.time_idx(c["head_s"])
    units = [u for u in ctx.seg.units if u.start < c["head_s"]]
    hits = []
    for u in units:
        grams = {tuple(toks[i:i + n]) for i in range(u.i0, u.i1 - n + 2) if all(toks[i:i + n])}
        if len(grams) < c["min_ngrams"]:
            continue
        later_from = ctx.time_idx(u.end + c["gap_after_s"])
        later = _grams_from(toks, later_from, n)
        frac = sum(g in later for g in grams) / len(grams)
        if frac >= c["min_repeat_frac"]:
            hits.append((u.i0, u.i1, frac))
    if not hits:
        return []
    if len(hits) >= 2:
        return [(hits[0][0], hits[-1][1], f"R3: provisional teaser montage ({len(hits)} units in the first "
                                          f"{c['head_s']:.0f}s recur later)")]
    i0, i1, fr = hits[0]
    return [(i0, i1, f"R3: provisional teaser copy ({fr:.0%} of n-grams recur later)")]


_gram_cache: dict = {}


def _grams_from(toks, start, n):
    key = (id(toks), start, n)
    if key not in _gram_cache:
        _gram_cache.clear()
        _gram_cache[key] = {tuple(toks[i:i + n]) for i in range(start, len(toks) - n + 1)}
    return _gram_cache[key]


def excluded_ranges(ctx) -> list[tuple[int, int, str]]:
    up = upstream_ranges(ctx.seg)
    if up is not None:
        return up
    return provisional_teaser(ctx)
