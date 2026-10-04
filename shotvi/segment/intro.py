"""Opening teaser / intro montage detection.

Many podcast episodes open with a *teaser*: a 30s-3min montage of short
excerpts from later in the episode (often with music or censor beeps), then
the real start ("hi sir, welcome ..."). Clips must be taken from the body, and
teaser speech must not feed host inference or Q->A links (the excerpts are
out of context and often mis-diarized).

Main signal: teaser passages recur later in the same transcript. The opening
window is aligned against the rest of the episode with content-token 3-gram
seeds (fillers dropped, very common n-grams ignored), chained forward with
small gaps on the teaser side (single-word ASR differences) and larger jumps
on the body side (editors jump-cut inside an excerpt), then extended at the
edges (one skipped token, or a variant spelling anchored by an exact match). Matched stretches are
``TeaserFragment``s; fragments close together in time form a teaser range.

Supporting signals (music / censored events, speaker-switch rate, number of
distinct body locations, shuffled order) only shape confidence and the teaser
tail; without verbatim repeats nothing is reported as a teaser.

Other ranges:
- ``music``: opening music/song events with transcribed words (lyrics) that are
  not inside a teaser.
- ``intro``: greeting / host introduction between the teaser (or the start)
  and the host's first question, if it contains greeting/intro cue words;
  plus a short stretch before a teaser that does not start at time 0.

Thresholds are in seconds / token counts that describe the *format* (a teaser
is at the start and is made of multi-second excerpts), relative to the
episode's pause statistics where timing matters. Nothing is per-video.
"""
from __future__ import annotations

import difflib
from collections import Counter
from dataclasses import dataclass, field

from . import lexicon as L
from .loader import Transcript
from .pauses import PauseStats
from .text import norm

# where to look
SEARCH_MAX_S = 900.0        # teaser fragments are searched in the first min(900s, 50% of duration)
SEARCH_FRAC = 0.5
START_MAX_S = 300.0         # a teaser must start within min(300s, 25% of duration)
START_FRAC = 0.25
# matching
SEED_K = 3                  # content-token n-gram length of a seed
MIN_LAG_S = 20.0            # the repeat must be at least this much later
MAX_GRAM_OCC = 8            # n-grams occurring more often than this are phrases, not evidence
CHAIN_GAP = 4               # tokens that may be skipped between seeds (ASR variation)
BODY_JUMP = 40              # teaser excerpts are jump-cut: the body side may skip this many tokens
FUZZY_RATIO = 0.75          # edge extension: token similarity to accept a variant spelling
COMMON_DISCOUNT = 0.34      # info weight of very frequent tokens ("you", "sir", "అని")
# acceptance
MIN_FRAG_INFO = 5.0         # a fragment needs ~5 informative matched tokens
WEAK_FRAG_INFO = 2.0        # weaker matches may only extend an already-detected teaser ...
WEAK_REACH_S = 15.0         # ... when they are this close to its edge
COLD_OPEN_INFO = 12.0       # a teaser made of one excerpt needs more evidence
MIN_TEASER_INFO = 15.0      # total for a multi-fragment montage
CLUSTER_GAP_S = 20.0        # fragments this close belong to the same montage
# boundaries
EDGE_EXT_S = 8.0            # extend a teaser edge over contiguous speech up to this far
TAIL_SEP_S = 30.0           # look this far past the last excerpt for the teaser's end (music / silence)
# intro
INTRO_MAX_S = 300.0
PRE_TEASER_MAX_S = 90.0
MIN_MUSIC_S = 3.0

KINDS = ("teaser", "music", "intro")
HIDDEN_KINDS = ("teaser", "music")   # omitted from LLM rendering; excluded from roles / Q->A

INTRO_CUES = {
    "hi", "hello", "hey", "welcome", "podcast", "episode", "channel", "subscribe", "guest", "guests",
    "namaste", "namaskaram", "నమస్కారం", "నమస్తే", "నమస్కారాలు", "స్వాగతం", "హాయ్", "హలో", "పాడ్కాస్ట్",
    "పాడ్‌కాస్ట్", "ఎపిసోడ్", "ఛానల్", "చానల్", "సబ్స్క్రైబ్",
}
INTRO_CUE_BIGRAMS = {("nice", "meeting"), ("nice", "to"), ("thank", "you"), ("thanks", "for"),
                     ("today", "we"), ("ఈ", "రోజు"), ("ఈరోజు", "మనం")}


@dataclass
class TeaserFragment:
    i0: int                 # teaser words (inclusive)
    i1: int
    start: float
    end: float
    body_i0: int            # where the same words are said in the body
    body_i1: int
    body_start: float
    body_end: float
    n_matched: int          # matched content tokens
    info: float             # rarity-weighted matched tokens
    similarity: float       # matched / longer side, content tokens

    def to_dict(self):
        return {"words": [self.i0, self.i1], "start": round(self.start, 3), "end": round(self.end, 3),
                "body_words": [self.body_i0, self.body_i1], "body_start": round(self.body_start, 3),
                "body_end": round(self.body_end, 3), "n_matched": self.n_matched,
                "info": round(self.info, 1), "similarity": round(self.similarity, 3)}


@dataclass
class IntroRange:
    id: int
    kind: str               # teaser | music | intro
    i0: int                 # inclusive word range
    i1: int
    start: float            # seconds; may extend past the words over music / silence
    end: float
    confidence: float
    evidence: dict = field(default_factory=dict)
    fragments: list[TeaserFragment] = field(default_factory=list)

    @property
    def dur(self) -> float:
        return self.end - self.start

    @property
    def n_words(self) -> int:
        return self.i1 - self.i0 + 1

    def contains_word(self, i: int) -> bool:
        return self.i0 <= i <= self.i1

    def to_dict(self):
        return {"id": self.id, "kind": self.kind, "words": [self.i0, self.i1],
                "start": round(self.start, 3), "end": round(self.end, 3), "duration": round(self.dur, 2),
                "confidence": self.confidence, "evidence": self.evidence,
                "fragments": [f.to_dict() for f in self.fragments]}


# --------------------------------------------------------------- matching
def _key(tok: str) -> str:
    return norm(tok).replace("-", "").replace("‌", "").replace("‍", "")


def _content_positions(tr: Transcript) -> tuple[list[int], list[str]]:
    cp, toks = [], []
    for w in tr.words:
        k = _key(w.text)
        if k and k not in L.FILLERS:
            cp.append(w.i)
            toks.append(k)
    return cp, toks


def _fuzzy_eq(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < 3:
        return False
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= FUZZY_RATIO


def _extend_edge(toks: list[str], a: int, b: int, sign: int, ok) -> tuple[int, int, list[int]]:
    """Walk outward from a matched pair (a, b) while the tokens keep matching.
    Exact matches may skip one token on either side; a fuzzy (variant
    spelling) match is only kept when an exact match follows it further out,
    so the edge is always anchored on an exact match."""
    added: list[int] = []
    pending: tuple[int, int] | None = None
    best = (a, b)
    for _ in range(8):
        step = None
        for da, db in ((1, 1), (2, 1), (1, 2)):
            na, nb = a + sign * da, b + sign * db
            if ok(na, nb) and toks[na] == toks[nb]:
                step = (na, nb, True)
                break
        if step is None:
            na, nb = a + sign, b + sign
            if ok(na, nb) and _fuzzy_eq(toks[na], toks[nb]):
                step = (na, nb, False)
        if step is None:
            break
        na, nb, exact = step
        if exact:
            if pending is not None:
                added.append(pending[0])
                pending = None
            added.append(na)
            best = (na, nb)
        elif pending is not None:
            break
        else:
            pending = (na, nb)
        a, b = na, nb
    return best[0], best[1], added


@dataclass
class _Chain:
    a0: int
    a1: int
    b0: int
    b1: int
    seeds: list[tuple[int, int]]


def find_fragments(tr: Transcript, search_end_s: float | None = None,
                   min_lag_s: float = MIN_LAG_S, min_info: float = MIN_FRAG_INFO) -> list[TeaserFragment]:
    """Stretches starting before ``search_end_s`` that are repeated (nearly)
    verbatim at least ``min_lag_s`` later. Non-overlapping on the early side,
    strongest first resolved, returned in time order."""
    ws = tr.words
    cp, toks = _content_positions(tr)
    n = len(toks)
    if n < SEED_K * 2:
        return []
    if search_end_s is None:
        search_end_s = min(SEARCH_MAX_S, SEARCH_FRAC * tr.duration)
    counts = Counter(toks)
    common = max(10.0, n / 500.0)
    info_w = [1.0 if counts[t] <= common else COMMON_DISCOUNT for t in toks]
    index: dict[tuple, list[int]] = {}
    for j in range(n - SEED_K + 1):
        index.setdefault(tuple(toks[j:j + SEED_K]), []).append(j)
    seeds: list[tuple[int, int]] = []
    for a in range(n - SEED_K + 1):
        ta = ws[cp[a]].start
        if ta > search_end_s:
            break
        occ = index[tuple(toks[a:a + SEED_K])]
        if len(occ) > MAX_GRAM_OCC:
            continue
        for b in occ:
            if b > a and ws[cp[b]].start >= ta + min_lag_s:
                seeds.append((a, b))
    # chain seeds along a diagonal, tolerating skipped tokens on either side
    chains: list[_Chain] = []
    open_: list[_Chain] = []
    for a, b in seeds:  # sorted by a, then b
        open_ = [c for c in open_ if a - c.a1 <= CHAIN_GAP + 1]
        best, best_d = None, None
        for c in open_:
            # forward on both sides; the body may skip words the editor cut out of the excerpt
            if 0 < a - c.a1 and 0 < b - c.b1 <= BODY_JUMP:
                d = abs((b - a) - (c.b1 - c.a1))
                if best_d is None or d < best_d:
                    best, best_d = c, d
        if best is None:
            best = _Chain(a, a, b, b, [])
            chains.append(best)
            open_.append(best)
        best.a1, best.b1 = a, b
        best.seeds.append((a, b))

    frags: list[TeaserFragment] = []
    for c in chains:
        cov_a = {p for a, _ in c.seeds for p in range(a, a + SEED_K)}
        a0, a1 = c.a0, c.a1 + SEED_K - 1
        b0, b1 = c.b0, c.b1 + SEED_K - 1
        # edge extension, tolerant of one skipped token or one variant spelling
        a0, b0, add = _extend_edge(toks, a0, b0, -1, lambda x, y, a1=a1: x >= 0 and y > a1)
        cov_a.update(add)
        a1, b1, add = _extend_edge(toks, a1, b1, 1, lambda x, y, b0=b0: y < n and x < b0)
        cov_a.update(add)
        if b0 <= a1:
            continue  # overlaps its own repeat: a chant / self-similar stretch, not a teaser copy
        info = sum(info_w[p] for p in cov_a)
        if info < min_info or len(cov_a) < SEED_K:
            continue
        i0, i1, j0, j1 = cp[a0], cp[a1], cp[b0], cp[b1]
        sim = len(cov_a) / max(a1 - a0 + 1, b1 - b0 + 1)
        frags.append(TeaserFragment(i0, i1, ws[i0].start, ws[i1].end, j0, j1, ws[j0].start, ws[j1].end,
                                    len(cov_a), round(info, 2), round(sim, 3)))
    # the same early stretch can repeat in several places: keep the best per early region
    frags.sort(key=lambda f: (-f.info, f.i0))
    kept: list[TeaserFragment] = []
    for f in frags:
        ov = max((min(f.i1, k.i1) - max(f.i0, k.i0) + 1 for k in kept), default=0)
        if ov <= 0.5 * (f.i1 - f.i0 + 1):
            kept.append(f)
    kept.sort(key=lambda f: f.i0)
    return kept


# ------------------------------------------------------------- boundaries
def _extend_start(tr: Transcript, ps: PauseStats, i: int, floor: int) -> int:
    ws = tr.words
    s = i
    while s - 1 >= floor and ws[s].start - ws[s - 1].end < ps.major and ws[i].start - ws[s - 1].start <= EDGE_EXT_S:
        s -= 1
    return s


def _extend_end(tr: Transcript, ps: PauseStats, i: int) -> int:
    ws = tr.words
    e = i
    while e + 1 < len(ws) and ws[e + 1].start - ws[e].end < ps.major and ws[e + 1].end - ws[i].end <= EDGE_EXT_S:
        e += 1
    return e


def _tail(tr: Transcript, ps: PauseStats, e: int) -> tuple[int, str | None]:
    """The teaser usually ends with a music sting or a silence before the real
    start. Within TAIL_SEP_S past the last excerpt, take the strongest break
    (a music event beats any silence, then the longest silence >= the video's
    long pause); the unmatched speech before it still belongs to the teaser."""
    ws = tr.words
    sep_gap = max(ps.long, 2.0)
    limit = ws[e].end + TAIL_SEP_S
    music = [ev for ev in tr.events if ev.kind == "music" and ws[e].end - 1.0 <= ev.start <= limit]
    best, best_sc, best_kind = e, 0.0, None
    k = e + 1
    while k < len(ws) and ws[k].start <= limit:
        gap = ws[k].start - ws[k - 1].end
        m = next((ev for ev in music if ws[k - 1].start <= ev.start < ws[k].start + 1e-6), None)
        if m is not None:
            last = k - 1
            while last + 1 < len(ws) and ws[last + 1].start < m.end - 0.2:
                last += 1   # lyrics under the music belong to the teaser
            sc = 1000.0 + (m.end - m.start)
            if sc > best_sc:
                best, best_sc, best_kind = last, sc, "music"
        elif gap >= sep_gap and gap > best_sc:
            best, best_sc, best_kind = k - 1, gap, "silence"
        k += 1
    if best_kind is None and e + 1 < len(ws) and ws[e + 1].start - ws[e].end >= sep_gap:
        best_kind = "silence"
    return best, best_kind


def _end_seconds(tr: Transcript, i1: int) -> float:
    """Extend a range end over non-speech events (music, beeps) that follow its
    last word, up to the next word."""
    ws = tr.words
    end = ws[i1].end
    nxt = ws[i1 + 1].start if i1 + 1 < len(ws) else tr.duration
    for ev in tr.events:
        if ev.kind != "laughter" and end - 0.5 <= ev.start < nxt:
            end = max(end, min(ev.end, nxt))
    return end


def _switch_rate(tr: Transcript, i0: int, i1: int) -> float:
    ws = tr.words
    if i1 <= i0:
        return 0.0
    sw = sum(ws[i].speaker != ws[i - 1].speaker for i in range(i0 + 1, i1 + 1))
    dur = max(ws[i1].end - ws[i0].start, 1.0)
    return sw / dur * 60.0


def _speech_s(tr: Transcript, i0: int, i1: int) -> float:
    return sum(max(w.dur, 0.0) for w in tr.words[i0:i1 + 1])


# ----------------------------------------------------------------- detect
def detect_teasers(tr: Transcript, ps: PauseStats) -> list[IntroRange]:
    """Teaser and opening-music ranges (word-level; no turns needed)."""
    ws = tr.words
    if not ws:
        return []
    start_limit = min(START_MAX_S, START_FRAC * tr.duration)
    all_frags = find_fragments(tr, min_info=WEAK_FRAG_INFO)
    frags = [f for f in all_frags if f.info >= MIN_FRAG_INFO]
    weak = [f for f in all_frags if f.info < MIN_FRAG_INFO]
    clusters: list[list[TeaserFragment]] = []
    for f in frags:
        if clusters and f.start - clusters[-1][-1].end <= CLUSTER_GAP_S:
            clusters[-1].append(f)
        else:
            clusters.append([f])
    ranges: list[IntroRange] = []
    floor = 0
    ep_switch = _switch_rate(tr, 0, len(ws) - 1)
    for cl in clusters:
        if cl[0].start > start_limit:
            break
        # a teaser's excerpts are said later, outside the teaser itself
        cl = [f for f in cl if f.body_start >= cl[-1].end]
        if not cl:
            continue
        info = sum(f.info for f in cl)
        if not ((len(cl) >= 2 and info >= MIN_TEASER_INFO) or info >= COLD_OPEN_INFO):
            continue
        cl = _add_satellites(cl, weak, ws[floor].start)
        i0 = _extend_start(tr, ps, cl[0].i0, floor)
        if ws[i0].start - ws[floor].start <= 3.0:
            i0 = floor  # only a breath before it: the teaser starts the episode
        i1 = _extend_end(tr, ps, cl[-1].i1)
        i1, sep = _tail(tr, ps, i1)
        if ranges and ws[i0].start - ranges[-1].end <= CLUSTER_GAP_S:
            prev = ranges.pop()   # two parts of one montage split by an unmatched stretch
            i0, cl = prev.i0, prev.fragments + cl
        ranges.append(IntroRange(0, "teaser", i0, i1, ws[i0].start, _end_seconds(tr, i1), 0.0,
                                 {"separator": sep}, cl))
        floor = i1 + 1
    for r in ranges:
        _teaser_evidence(tr, r, ep_switch)

    # opening music with lyrics, outside any teaser
    for ev in tr.events:
        if ev.kind != "music" or ev.start > start_limit or ev.end - ev.start < MIN_MUSIC_S:
            continue
        if any(r.start - 0.5 <= ev.start <= r.end + 0.5 for r in ranges):
            continue
        inside = [w.i for w in ws if ev.start - 0.2 <= w.start < ev.end]
        if not inside:
            continue  # pure BGM: no words to exclude
        i0, i1 = inside[0], inside[-1]
        ranges.append(IntroRange(0, "music", i0, i1, min(ev.start, ws[i0].start), max(ev.end, ws[i1].end),
                                 0.7, {"event": ev.text}))
    ranges.sort(key=lambda r: r.i0)
    return ranges


def _add_satellites(cl: list[TeaserFragment], weak: list[TeaserFragment], floor_s: float) -> list[TeaserFragment]:
    """Weak matches (ASR variants, jump cuts) right next to a detected teaser extend it."""
    cl = list(cl)
    grown = True
    while grown:
        grown = False
        for f in weak:
            if f in cl or f.start < floor_s or f.body_start < max(cl[-1].end, f.end):
                continue
            before = 0 <= cl[0].start - f.end + 1e-6 <= WEAK_REACH_S
            after = 0 <= f.start - cl[-1].end + 1e-6 <= WEAK_REACH_S
            if before or after:
                cl.append(f)
                cl.sort(key=lambda x: x.i0)
                grown = True
    return cl


def _teaser_evidence(tr: Transcript, r: IntroRange, ep_switch: float) -> None:
    fr = r.fragments
    matched_s = sum(_speech_s(tr, f.i0, f.i1) for f in fr)
    span_s = _speech_s(tr, r.i0, r.i1) or 1.0
    coverage = min(1.0, matched_s / span_s)
    body_locs: list[float] = []
    for f in sorted(fr, key=lambda f: f.body_start):
        if not body_locs or f.body_start - body_locs[-1] > 30.0:
            body_locs.append(f.body_start)
    order = [f.body_start for f in fr]
    shuffled = any(b < a for a, b in zip(order, order[1:]))
    evs = [e for e in tr.events if r.start - 0.5 <= e.start <= r.end]
    n_music = sum(e.kind == "music" for e in evs)
    n_cens = sum(e.kind == "censored" for e in evs)
    sw = _switch_rate(tr, r.i0, r.i1)
    conf = 0.45 + 0.1 * min(len(fr) - 1, 3) + 0.25 * coverage
    conf += 0.05 if len(body_locs) >= 2 else 0.0
    conf += 0.05 if shuffled else 0.0
    conf += 0.05 if (n_music or n_cens or r.evidence.get("separator")) else 0.0
    conf += 0.05 if ep_switch and sw > 1.5 * ep_switch else 0.0
    r.confidence = round(min(conf, 0.99), 2)
    r.evidence.update({
        "n_fragments": len(fr), "matched_info": round(sum(f.info for f in fr), 1),
        "coverage": round(coverage, 3), "n_body_locations": len(body_locs), "body_order_shuffled": shuffled,
        "music_events": n_music, "censored_events": n_cens,
        "speaker_switches_per_min": round(sw, 1), "episode_switches_per_min": round(ep_switch, 1),
        "mean_fragment_s": round(sum(f.end - f.start for f in fr) / len(fr), 1),
    })


def _cues(tr: Transcript, i0: int, i1: int) -> list[str]:
    toks = [norm(tr.words[i].text) for i in range(i0, i1 + 1)]
    found = {t for t in toks if t in INTRO_CUES}
    found |= {f"{a} {b}" for a, b in zip(toks, toks[1:]) if (a, b) in INTRO_CUE_BIGRAMS}
    return sorted(found)


def detect_intros(tr: Transcript, ranges: list[IntroRange], first_question_word: int | None) -> list[IntroRange]:
    """Greeting / host introduction ranges, given teaser ranges and the word
    index where the host's first Q->A link (setup included) starts."""
    ws = tr.words
    out: list[IntroRange] = []
    opening = [r for r in ranges if r.kind in HIDDEN_KINDS]
    # speech before a teaser that doesn't start the episode: greeting / channel intro
    if opening and opening[0].i0 > 0:
        i1 = opening[0].i0 - 1
        if ws[i1].end - ws[0].start <= PRE_TEASER_MAX_S:
            cues = _cues(tr, 0, i1)
            out.append(IntroRange(0, "intro", 0, i1, ws[0].start, ws[i1].end,
                                  round(min(0.5 + 0.1 * len(cues), 0.8), 2),
                                  {"position": "before_teaser", "cues": cues}))
    # after the teaser (or from the start) up to the first question
    i0 = (opening[-1].i1 + 1) if opening else 0
    if first_question_word is None or first_question_word <= i0 or i0 >= len(ws):
        return out
    i1 = first_question_word - 1
    if ws[i1].end - ws[i0].start > INTRO_MAX_S:
        return out
    if any(r.i0 <= i1 and r.i1 >= i0 for r in ranges):
        return out
    cues = _cues(tr, i0, i1)
    if not cues:
        return out
    conf = min(0.5 + 0.1 * len(cues) + (0.1 if opening else 0.0), 0.85)
    out.append(IntroRange(0, "intro", i0, i1, ws[i0].start, ws[i1].end, round(conf, 2),
                          {"position": "after_teaser" if opening else "episode_start", "cues": cues}))
    return out


def finalize(ranges: list[IntroRange]) -> list[IntroRange]:
    ranges = sorted(ranges, key=lambda r: r.i0)
    for k, r in enumerate(ranges):
        r.id = k
    return ranges


def cuts_of(ranges: list[IntroRange]) -> set[int]:
    """Word indices where a new turn / unit must start."""
    out = set()
    for r in ranges:
        out.add(r.i0)
        out.add(r.i1 + 1)
    out.discard(0)
    return out
