"""Episode zones for scripted shows (web series, comedy skits): the non-story opening and spoken sponsor segments.

Free signals only (no LLM, no network): the Scribe transcript (words, sentences, audio events), optional shot
changes, optional YouTube metadata (chapters, description), optional transcripts of sibling episodes.

    from shotvi.segment.episode_zones import episode_zones
    z = episode_zones(transcript_dict, cuts=shot_change_times, meta=youtube_meta, references=[sibling_dict, ...])
    z["opening"]   {"story_start", "story_line", "parts": [{kind, level, start, end, detail}], "status", ...}
    z["sponsors"]  [{"start", "end", "points", "evidence"}]

Opening rule of thumb: the opening zone ends where SUSTAINED DIALOGUE begins, unless that dialogue is a recap.
  * lead       (hard)  the speechless stretch before the first word (song montage, cards, logo).
  * cards      (hard)  isolated lines before the story with channel-plug cues ("subscribe", "bell icon").
  * isolated   (soft)  other isolated lines before the story: a card the cue list misses, or a stray story line.
  * teaser     (hard)  opening stretches repeated later in this episode (shotvi.segment.intro.detect_teasers,
                       the podcast teaser detector: verbatim content-token repeats, validated on 5 podcasts).
  * lyrics     (hard)  words under an opening song, before the story starts (intro.detect_teasers "music").
  * recap      (hard if >= RECAP_MIN_LINES lines, else soft) opening lines repeated from a sibling episode
                       ("previously on"), when sibling transcripts are given.
  * chapter    (hard)  leading YouTube chapters with a short title (<= 3 words) naming a recap / trailer /
                       teaser / intro / highlights ("Trailer", "Podcast Highlights"; not "Intro to hair loss and genetics").
  * sponsor    (hard)  a sponsor segment (below) that sits before the story start.
"hard" = never story; "soft" = uncertain, a reviewer should look.

Sustained dialogue = RUN_LINES consecutive lines, each starting within RUN_GAP s of the previous line's end,
with RUN_WORDS words in total, none of them inside a teaser / lyrics / sponsor / recap range. Why these numbers:
  - RUN_GAP 5.0 s: inside a scene, lines follow each other closely (3 web-series episodes: line-gap median
    0.8-1.0 s, p90 3.5-4.0 s); cards, title music and montage bridges leave >= 7 s with no speech.
  - RUN_LINES 4, RUN_WORDS 12: the smallest exchange that is clearly a scene and not a card or a stray lyric
    (lines median 3 words, so 4 lines ~ 12 words ~ 5-8 s of speech). A card is 1-2 lines.
Sensitivity (3 web-series episodes, 2026-10-06): the detected story start is unchanged for RUN_LINES 3-4,
RUN_GAP 5-7 s, RUN_WORDS 8-16. RUN_LINES >= 5 moves Hello Madam Ep6's start from 17.8 s to 33.1 s (its frame scene
has a 7 s music gap after 4 lines; the extra stretch is only "soft"); RUN_GAP 3-4 s makes Couple Goals Ep2's
first 7 s soft; RUN_GAP >= 10 s lets the subscribe card count as dialogue (Hello Madam's hard zone shrinks
from 17.3 s to 7.3 s). Otherwise the hard zones did not move.

A montage-style recap without verbatim repeats (dense short lines under music, quick cuts) is NOT flagged: on the
three web-series episodes ordinary scenes already have median 3-word lines and ~2.5 s shots, and Scribe places
music events in the gaps between lines, so those features do not separate a recap from a scene.

Sponsor segments: lines are grouped into self-contained blocks (split at gaps >= SPONSOR_BLOCK_GAP). A block
scores points: +1 per distinct call-to-action / retail cue ("link in description", "available on", "amazon",
"use code", ...), +2 if it names the product the video's description tags with #ad / #sponsored, +1 if it is a
monologue (one speaker >= 90% of words) of >= 8 s. >= SPONSOR_MIN_POINTS points = sponsor. Blocks longer than
SPONSOR_MAX_BLOCK_S are not scored: a podcast runs as one gap-free block, so its sponsor reads are out of scope. Adjacent blocks of
the same speaker with a cue or a shared rare word are absorbed; sponsor blocks within SPONSOR_MERGE_GAP merge.
Edges snap to the shot change in the surrounding silence when shots are known.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from . import intro
from .loader import Transcript, from_dict
from .pauses import pause_stats
from .text import norm

PAD = 0.5                 # zone edge sits this far before a story line's first word (lead-in fail is 0.4 s)
RUN_LINES = 4
RUN_GAP = 5.0
RUN_WORDS = 12
SCAN_MAX_S, SCAN_FRAC = 300.0, 0.25   # the opening is looked for in the first min(300 s, 25% of the episode)
LINE_GAP_SPLIT = 0.7      # fallback line split when a transcript has no sentences (same as Scribe sentences)

CARD_CUES = ("subscribe", "bell icon", "notification", "like and share", "like share", "channel", "episode",
             "సబ్స్క్రైబ్", "సబ్‌స్క్రైబ్", "ఛానల్", "చానల్", "ఎపిసోడ్", "బెల్ ఐకాన్")
CHAPTER_CUES = {"recap", "previously", "intro", "introduction", "title", "teaser", "trailer", "promo", "highlights",
                "opening", "రీక్యాప్", "ట్రైలర్", "టీజర్"}
CHAPTER_MAX_WORDS = 3

RECAP_TOKENS = 4          # a line needs this many tokens to be tested against sibling episodes
RECAP_CONTAIN = 0.5       # share of the line's word 3-grams found in a sibling episode
RECAP_MIN_LINES = 3       # this many repeated lines (within RECAP_CLUSTER_S) = a recap (hard); fewer = soft
RECAP_CLUSTER_S = 20.0

SPONSOR_BLOCK_GAP = 3.0
SPONSOR_MAX_BLOCK_S = 180.0   # a longer gap-free block is a conversation, not a self-contained read (podcasts)
SPONSOR_MERGE_GAP = 15.0
SPONSOR_MIN_POINTS = 3
SPONSOR_MONO_SHARE, SPONSOR_MONO_S = 0.9, 8.0
SPONSOR_CUES = (
    "sponsor", "presented by", "powered by", "brought to you", "in association with", "paid partnership",
    "link in", "link description", "description లో", "description box", "link below",
    "available on", "available at", "available in", "available గా", "use code", "promo code", "coupon",
    "% off", "discount code", "order now", "buy now", "shop now", "download the app", "download చేసుకోండి",
    "app and website", "app లో", "website లో", "check out", "check చేయండి",
    "amazon", "flipkart", "nykaa", "myntra", "meesho", "blinkit", "zepto", "bigbasket",
    "clinically proven", "dermatologically",
)
AD_MARKERS = ("#ad", "#sponsored", "#paidpartnership", "#collab", "paid partnership", "paid promotion",
              "sponsored by", "in association with")


@dataclass
class Line:
    i0: int
    i1: int
    start: float
    end: float
    speaker: str
    text: str

    @property
    def n(self):
        return self.i1 - self.i0 + 1


def lines_of(d: dict, tr: Transcript) -> list[Line]:
    """Scribe sentences (or a gap / speaker-change split when absent) as Lines."""
    ws = tr.words
    spans = []
    sents = d.get("sentences") or []
    if sents:
        for s in sents:
            a, b = s["words"]
            b = min(b, len(ws) - 1)
            if 0 <= a <= b:
                spans.append((a, b))
    else:
        a = 0
        for j in range(1, len(ws) + 1):
            if j == len(ws) or ws[j].start - ws[j - 1].end >= LINE_GAP_SPLIT or ws[j].speaker != ws[j - 1].speaker:
                spans.append((a, j - 1))
                a = j
    out = []
    for a, b in spans:
        spk = Counter(w.speaker for w in ws[a:b + 1]).most_common(1)[0][0]
        out.append(Line(a, b, ws[a].start, ws[b].end, spk, " ".join(w.text for w in ws[a:b + 1])))
    return out


def _low(s: str) -> str:
    return " " + re.sub(r"\s+", " ", s.lower()) + " "


def _has(text_low: str, cue: str) -> bool:
    if cue.isascii() and cue[0].isalnum():
        return re.search(r"(?<![a-z0-9])" + re.escape(cue) + r"(?![a-z0-9])", text_low) is not None
    return cue in text_low


def _overlaps(line: Line, ivs) -> bool:
    return any(line.start < b and line.end > a for a, b in ivs)


# ---- sponsor segments -----------------------------------------------------------------------------------

def _split_camel(tag: str) -> list[str]:
    return [p.lower() for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", tag)]


def ad_brands(meta: dict | None) -> list[list[str]]:
    """Product names tagged as an ad in the YouTube description: the hashtags on the line(s) carrying an ad
    marker (#ad, #sponsored, "paid partnership" ...). Each brand = its camel-case words, e.g.
    #HimalayaPurifyingNeemFaceWash -> [himalaya, purifying, neem, face, wash]."""
    desc = (meta or {}).get("description") or ""
    out = []
    for ln in desc.splitlines():
        low = ln.lower()
        if not any(m in low for m in AD_MARKERS):
            continue
        for tag in re.findall(r"#(\w+)", ln):
            if tag.lower() in {m.lstrip("#").replace(" ", "") for m in AD_MARKERS}:
                continue
            parts = _split_camel(tag) or [tag.lower()]
            if len("".join(parts)) >= 4:
                out.append(parts)
        m = re.search(r"sponsored by ([A-Z][\w&'-]*(?: [A-Z][\w&'-]*)*)", ln)
        if m:
            out.append([p.lower() for p in m.group(1).split()])
    return out


def _brand_hit(text_low: str, brands) -> bool:
    """A brand counts when its full phrase is said, or a one-word brand name / the first word of a
    multi-word product name (the maker: 'Himalaya') appears as a word."""
    for parts in brands:
        if _has(text_low, " ".join(parts)) or _has(text_low, "".join(parts)):
            return True
        if len(parts[0]) >= 4 and _has(text_low, parts[0]):
            return True
    return False


def _snap_out(t: float, lo: float, hi: float, cuts, prefer_late: bool) -> float:
    """Shot change inside (lo, hi] closest to the speech edge; None-safe."""
    cand = [c for c in (cuts or []) if lo < c <= hi]
    if not cand:
        return None
    return max(cand) if prefer_late else min(cand)


def sponsor_segments(tr: Transcript, lines: list[Line], cuts=None, meta=None) -> list[dict]:
    if not lines:
        return []
    brands = ad_brands(meta)
    blocks = [[lines[0]]]
    for ln in lines[1:]:
        if ln.start - blocks[-1][-1].end >= SPONSOR_BLOCK_GAP:
            blocks.append([ln])
        else:
            blocks[-1].append(ln)
    tok_count = Counter(norm(w.text) for w in tr.words)

    def block_counts(bl):
        return Counter(norm(w.text) for l in bl for w in tr.words[l.i0:l.i1 + 1])

    def info(bl):
        low = _low(" ".join(l.text for l in bl))
        cues = sorted({c for c in SPONSOR_CUES if _has(low, c)})
        brand = bool(brands) and _brand_hit(low, brands)
        spk = Counter()
        for l in bl:
            for w in tr.words[l.i0:l.i1 + 1]:
                spk[w.speaker] += 1
        top, n = spk.most_common(1)[0]
        mono = n / sum(spk.values()) >= SPONSOR_MONO_SHARE and bl[-1].end - bl[0].start >= SPONSOR_MONO_S
        toks = {norm(w.text) for l in bl for w in tr.words[l.i0:l.i1 + 1]}
        return {"cues": cues, "brand": brand, "mono": mono, "speaker": top, "toks": toks,
                "points": len(cues) + 2 * brand + mono}

    B = [info(b) for b in blocks]
    long_ = {k for k, b in enumerate(blocks) if b[-1].end - b[0].start > SPONSOR_MAX_BLOCK_S}
    spon = [k for k, x in enumerate(B) if x["points"] >= SPONSOR_MIN_POINTS and k not in long_]
    # absorb neighbours: same speaker, close, and a cue or a rare word shared with the sponsor block
    grown = set(spon)
    for k in spon:
        for nb in (k - 1, k + 1):
            if nb < 0 or nb >= len(blocks) or nb in grown or nb in long_:
                continue
            gap = blocks[k][0].start - blocks[nb][-1].end if nb < k else blocks[nb][0].start - blocks[k][-1].end
            if gap > SPONSOR_MERGE_GAP or B[nb]["speaker"] != B[k]["speaker"]:
                continue
            # a rare word (said only in these two blocks, e.g. the product name) ties them together
            both = block_counts(blocks[k] + blocks[nb])
            shared = {t for t in B[nb]["toks"] & B[k]["toks"] if len(t) >= 4 and tok_count[t] == both[t]}
            if B[nb]["cues"] or B[nb]["brand"] or shared:
                grown.add(nb)
                B[nb]["absorbed"] = sorted(shared)[:5]
    ks = sorted(grown)
    groups = []
    for k in ks:
        if groups and blocks[k][0].start - blocks[groups[-1][-1]][-1].end <= SPONSOR_MERGE_GAP:
            groups[-1].append(k)
        else:
            groups.append([k])
    out = []
    for g in groups:
        first, last = blocks[g[0]][0], blocks[g[-1]][-1]
        prev_end = lines[lines.index(first) - 1].end if lines.index(first) > 0 else 0.0
        nxt = lines.index(last) + 1
        next_start = lines[nxt].start if nxt < len(lines) else tr.duration
        s = _snap_out(first.start, prev_end, first.start + 0.05, cuts, prefer_late=True)
        e = _snap_out(last.end, last.end - 0.05, next_start, cuts, prefer_late=False)
        s = s if s is not None else max(prev_end, first.start - PAD)
        e = e if e is not None else min(next_start, last.end + PAD)
        cues = sorted({c for k in g for c in B[k]["cues"]})
        out.append({"start": round(s, 3), "end": round(e, 3), "words": [first.i0, last.i1],
                    "points": max(B[k]["points"] for k in g),
                    "evidence": {"cues": cues, "brand_from_meta": any(B[k]["brand"] for k in g),
                                 "monologue": any(B[k]["mono"] for k in g), "n_blocks": len(g),
                                 "excerpt": " ".join(first.text.split()[:10])}})
    return out


# ---- opening zone ---------------------------------------------------------------------------------------

def _grams(toks, k=3):
    return {tuple(toks[i:i + k]) for i in range(len(toks) - k + 1)}


def _line_toks(tr, ln):
    return [t for t in (norm(w.text) for w in tr.words[ln.i0:ln.i1 + 1]) if t]


def recap_lines(tr: Transcript, lines: list[Line], references, scan_end: float) -> list[tuple[Line, float]]:
    """Opening lines whose word 3-grams mostly appear in a sibling episode's transcript."""
    ref = set()
    for r in references or []:
        toks = [t for t in (norm(str(w.get("word", ""))) for w in r.get("word_timestamps") or []) if t]
        ref |= _grams(toks)
    if not ref:
        return []
    out = []
    for ln in lines:
        if ln.start > scan_end:
            break
        toks = _line_toks(tr, ln)
        if len(toks) < RECAP_TOKENS:
            continue
        g = _grams(toks)
        c = sum(x in ref for x in g) / len(g)
        if c >= RECAP_CONTAIN:
            out.append((ln, round(c, 2)))
    return out


def chapter_ranges(meta, duration) -> list[tuple[float, float, str]]:
    """The episode's leading YouTube chapters whose short title (<= CHAPTER_MAX_WORDS words) names a recap /
    teaser / intro ("Trailer", "Recap", "Podcast Highlights", "Introduction"); stops at the first other chapter,
    so "Intro to hair loss and genetics" (a topic chapter) does not count."""
    chs = sorted((meta or {}).get("chapters") or [], key=lambda c: float(c.get("start", c.get("start_time", 0)) or 0))
    out = []
    for k, c in enumerate(chs):
        title = str(c.get("title", ""))
        words = re.findall(r"\w+", title.lower())
        if not (0 < len(words) <= CHAPTER_MAX_WORDS and any(w in CHAPTER_CUES for w in words)):
            break
        s = float(c.get("start", c.get("start_time", 0)) or 0)
        e = c.get("end", c.get("end_time"))
        if e is None:
            e = chs[k + 1].get("start", chs[k + 1].get("start_time")) if k + 1 < len(chs) else duration
        out.append((s, float(e), title))
    return out


def opening_zone(tr: Transcript, lines: list[Line], meta=None, sponsors=(), references=(),
                 run_lines=RUN_LINES, run_gap=RUN_GAP, run_words=RUN_WORDS) -> dict:
    ws = tr.words
    scan_end = min(SCAN_MAX_S, SCAN_FRAC * tr.duration)
    parts = []

    def part(kind, level, s, e, detail):
        if e - s > 0.05:
            parts.append({"kind": kind, "level": level, "start": round(max(0.0, s), 3), "end": round(e, 3),
                          "detail": detail})

    excl = []   # (start, end) ranges whose lines never count as story dialogue
    ranges = intro.detect_teasers(tr, pause_stats(tr)) if ws else []
    teasers = [r for r in ranges if r.kind == "teaser" and r.start <= scan_end]
    lyrics = [r for r in ranges if r.kind == "music" and r.start <= scan_end]
    for r in teasers:
        excl.append((r.start, r.end))
    for r in lyrics:
        excl.append((r.start, r.end))
    for s in sponsors:
        if s["start"] <= scan_end:
            excl.append((s["start"], s["end"]))
    rec = recap_lines(tr, lines, references, scan_end)
    rec_clusters = []
    for ln, c in rec:
        if rec_clusters and ln.start - rec_clusters[-1][-1][0].end <= RECAP_CLUSTER_S:
            rec_clusters[-1].append((ln, c))
        else:
            rec_clusters.append([(ln, c)])
    for cl in rec_clusters:
        if len(cl) >= RECAP_MIN_LINES:
            excl.append((cl[0][0].start, cl[-1][0].end))
    chapters = [c for c in chapter_ranges(meta, tr.duration) if c[0] <= scan_end]
    for s, e, _ in chapters:
        excl.append((s, e))

    # first sustained dialogue
    story = None
    usable = [not _overlaps(ln, excl) for ln in lines]
    for k, ln in enumerate(lines):
        if ln.start > scan_end:
            break
        if not usable[k]:
            continue
        j, nw = k, ln.n
        while j + 1 < len(lines) and usable[j + 1] and lines[j + 1].start - lines[j].end <= run_gap:
            j += 1
            nw += lines[j].n
        if j - k + 1 >= run_lines and nw >= run_words:
            story = k
            break

    first = ws[0].start if ws else 0.0
    mus = [(e.start, e.end) for e in tr.events if e.kind == "music" and e.start < first]
    mus_s = sum(min(b, first) - a for a, b in mus)
    part("lead", "hard", 0.0, first - PAD,
         f"no speech before {first:.1f}s" + (f" ({mus_s:.0f}s of it music/song)" if mus_s > 1 else ""))
    for r in teasers:
        part("teaser", "hard", r.start - PAD, r.end,
             f"repeated later in this episode ({r.evidence.get('n_fragments', len(r.fragments))} excerpt(s), "
             f"confidence {r.confidence})")
    for cl in rec_clusters:
        lvl = "hard" if len(cl) >= RECAP_MIN_LINES else "soft"
        if lvl == "soft" and story is not None and cl[0][0].start >= lines[story].start:
            continue  # one or two chance repeats inside the story (~0.5% of lines match a sibling by chance)
        part("recap", lvl, cl[0][0].start - PAD, cl[-1][0].end,
             f"{len(cl)} line(s) repeated from a sibling episode, e.g. '{cl[0][0].text[:40]}'")
    for s, e, title in chapters:
        part("chapter", "hard", s, e, f"YouTube chapter '{title}'")

    if story is None:
        status = f"no sustained dialogue in the first {scan_end:.0f}s"
        story_start = None
    else:
        status = "detected"
        story_start = lines[story].start
        for r in lyrics:
            if r.end <= story_start + 0.05:
                part("lyrics", "hard", r.start - PAD, r.end, "words under an opening song")
        for s in sponsors:
            if s["end"] <= story_start + 0.05:
                part("sponsor", "hard", s["start"], s["end"], "sponsor segment before the story")
        pre = [ln for k, ln in enumerate(lines[:story]) if usable[k]]
        if pre:
            # leading channel-plug cards are hard; from the first other isolated line on it is uncertain
            k0 = 0
            while k0 < len(pre) and any(_has(_low(pre[k0].text), c) for c in CARD_CUES):
                k0 += 1
            if k0:
                part("cards", "hard", first - PAD, (pre[k0].start if k0 < len(pre) else story_start) - PAD,
                     f"channel-plug line(s) before the story: '{pre[0].text[:50]}'")
            if k0 < len(pre):
                part("isolated", "soft", pre[k0].start - PAD, story_start - PAD,
                     f"{len(pre) - k0} isolated line(s) before sustained dialogue: '{pre[k0].text[:50]}'")
    parts.sort(key=lambda p: (p["start"], p["end"]))
    hard = _merge([(p["start"], p["end"]) for p in parts if p["level"] == "hard"])
    soft = _merge([(p["start"], p["end"]) for p in parts if p["level"] == "soft"])
    head = hard[0][1] if hard and hard[0][0] <= 0.05 else 0.0
    return {"status": status, "story_start": story_start,
            "story_line": lines[story].text if story is not None else None,
            "head_end": round(head, 3), "hard": hard, "soft": soft, "parts": parts,
            "params": {"run_lines": run_lines, "run_gap": run_gap, "run_words": run_words,
                       "scan_end": round(scan_end, 1)}}


def _merge(ivs):
    out = []
    for a, b in sorted(ivs):
        if out and a <= out[-1][1] + 1e-6:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return [(round(a, 3), round(b, 3)) for a, b in out]


def episode_zones(d: dict, cuts=None, meta=None, references=(), video_id=None, **run_kw) -> dict:
    """Both detectors on a Scribe transcript dict. `cuts` = shot-change times (s) or None; `meta` = YouTube
    metadata dict (description, chapters) or None; `references` = sibling-episode transcript dicts."""
    tr = from_dict(d, video_id)
    lines = lines_of(d, tr)
    sp = sponsor_segments(tr, lines, cuts, meta)
    op = opening_zone(tr, lines, meta, sp, references, **run_kw)
    return {"opening": op, "sponsors": sp}
