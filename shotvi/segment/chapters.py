"""YouTube chapters (``scribe_dataset/<id>/youtube_meta.json``) as a reference.

Chapters are written by the creator, so they are a free check for opening
detection (the first chapter is often "Introduction" / "Trailer" /
"Highlights") and a coarse view of topic boundaries. They are evaluation
input only; nothing in segmentation reads them.
"""
from __future__ import annotations

import bisect
import json
import random
from pathlib import Path

# Titles that mark a non-content opening chapter (reporting heuristic, not used for detection).
OPENING_TITLE_CUES = ("intro", "trailer", "highlight", "teaser", "promo", "preview", "glimpse", "meet ",
                      "welcome", "guest introduction")


def load_chapters(transcript_path: str | Path) -> list[dict] | None:
    """Chapters [{title, start, end}] from youtube_meta.json next to the transcript, or None."""
    p = Path(transcript_path).parent / "youtube_meta.json"
    if not p.exists():
        return None
    ch = json.loads(p.read_text(encoding="utf-8")).get("chapters") or []
    return ch or None


def is_opening_title(title: str) -> bool:
    t = (title or "").lower() + " "
    return any(c in t for c in OPENING_TITLE_CUES)


def compare(seg, chapters: list[dict], seed: int = 0) -> dict:
    """Opening detection vs chapters, and chapter starts vs unit boundaries."""
    ws = seg.transcript.words
    teaser = next((r for r in seg.intro_ranges if r.kind == "teaser"), None)
    cs_word = seg.opening.get("content_start_word")
    if cs_word is None:
        cs_word = max((r.i1 + 1 for r in seg.intro_ranges), default=0)
    content_start = ws[cs_word].start if cs_word < len(ws) else seg.transcript.duration
    opening_ch = []
    for c in chapters:
        if is_opening_title(c["title"]):
            opening_ch.append(c)
        else:
            break
    ch_content = next((c["start"] for c in chapters if not is_opening_title(c["title"])), None)
    first_end = chapters[0]["end"] if chapters else None
    out = {
        "chapters_opening": [(c["title"], c["start"], c["end"]) for c in opening_ch],
        "first_chapter": (chapters[0]["title"], chapters[0]["start"], chapters[0]["end"]) if chapters else None,
        "teaser_end_s": round(teaser.end, 1) if teaser else None,
        "teaser_end_minus_first_chapter_end": round(teaser.end - first_end, 1) if teaser and first_end else None,
        "content_start_s": round(content_start, 1),
        "chapter_content_start_s": ch_content,
        "content_start_minus_chapter": round(content_start - ch_content, 1) if ch_content is not None else None,
    }
    # chapter starts vs unit boundaries (skip 0:00), with a random-time baseline
    starts = [u.start for u in seg.units]
    def dist(t):
        k = bisect.bisect_left(starts, t)
        return min(abs(starts[j] - t) for j in (k - 1, k) if 0 <= j < len(starts))
    cs = [c["start"] for c in chapters[1:]]
    d = sorted(dist(t) for t in cs)
    rnd = random.Random(seed)
    base = sorted(dist(rnd.uniform(0, seg.transcript.duration)) for _ in range(2000))
    med = lambda xs: xs[len(xs) // 2] if xs else None  # noqa: E731
    out["chapter_vs_units"] = {
        "n_chapter_starts": len(cs),
        "median_dist_to_unit_start_s": round(med(d), 2) if d else None,
        "within_2s": sum(x <= 2 for x in d), "within_5s": sum(x <= 5 for x in d),
        "random_baseline_median_s": round(med(base), 2),
        "random_baseline_within_2s_frac": round(sum(x <= 2 for x in base) / len(base), 2),
    }
    return out
