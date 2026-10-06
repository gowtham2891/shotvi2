"""Web-series clip eval: automatic proposals vs the founder-approved web-series ground truth.

    python -m eval.webseries runs/ws_r1 [--video ID] [--gold-dir data/gold] [--out-dir DIR]

Ground truth: `data/gold/<id>.json` clips with `content_type: web_series` (CLAUDE.md "Web-series GT approved",
founder 2026-10-06: "all clips are good"). Proposals: `<run>/<id>.json` with `proposals` (or `clips`, so a gold file
can be scored against itself). Every clip is read through its `time_segments` (play order, seconds); `pieces`
are only a fallback when a clip has no time segments. No LLM, no network.

Three parts:
1. Moment matching (recall vs GT). Detection-style: proposals are processed in rank order, and each takes the
   still-unmatched GT clip with the highest IoU among those it is *eligible* for:
     core coverage >= MATCH_COV  (share of the GT's spoken core, i.e. its kept word pieces, inside the proposal's
                                   source span; the GT's silent holds are boundary details, scored separately)
     span IoU      >= MATCH_IOU  (IoU of the source-time unions; stops a 3-minute region from "finding" a 40 s
                                   moment)
   One proposal matches at most one GT clip, so a long clip spanning two GT moments counts once. Unmatched
   proposals are NOT wrong (CLAUDE.md "Ground truth philosophy"): they are listed as new / partial-overlap,
   for the founder to rate, or as duplicates when they cover a GT moment another proposal already matched.
2. Pair metrics on matched pairs: start/end deltas, payoff + reaction hold kept, setup dropped, word-level
   agreement, length ratio, cut counts, purpose, cold open.
3. Rule checks on every clip (GT clips too, as a sanity test of the checks): lead-in, replay, cold-open lift,
   internal cuts, shot flashes, mid-word cuts, reaction hold, length, opening/credits zones, sponsor overlap.
   The opening zone (title cards, song montage, recap/teaser) and sponsor segments come from the generic
   detector `shotvi.segment.episode_zones` (no fixed "first N seconds" rule).
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from shotvi.segment import episode_zones as EZ

from .common import HARD_MAX_S, HARD_MIN_S, ROOT, SOFT_MAX_S, load_json, resolve, utf8_stdout, write_json

# ---- thresholds (format-level, not per video) ---------------------------------------------------------
MATCH_COV = 0.5          # share of the GT's spoken core inside the proposal's source span
MATCH_IOU = 0.2          # IoU of source-time unions
PARTIAL_COV = 0.15       # below eligibility but overlapping: "partial" (not "new")
START_TOL = 1.0          # s: starting later than the GT by more than this can drop setup
END_TOL = 0.5            # s: proposal end >= GT end - END_TOL keeps the GT's reaction hold
PAYOFF_SHARE = 0.9       # share of the GT's last line that must be kept to count the payoff as kept

LEAD_IN_WARN = 0.3       # CLAUDE.md: <=0.3 s pad before the first word
LEAD_IN_FAIL = 0.4
LEAD_IN_SLACK = 0.01      # timestamp rounding
CONTIG_TOL = 0.05        # consecutive segments closer than this are one continuous stretch
OVERLAP_TOL = 0.05       # source overlap beyond this = a moment played twice
SHOT_TOL = 0.05          # "on a shot change" = within this of a detected cut
LIFT_MIN, LIFT_MAX = 1.0, 3.6
LIFT_SLACK = 0.02
LIFT_QUIET_TAIL = 0.7    # lift ending this long after its last word = "quiet tail" (warn)
MAX_CUTS = 3
FLASH_S = 0.3            # a shot fragment shorter than this at a segment edge is a flash
HOLD_MIN = 1.0           # reaction hold after the last word
MID_WORD_TOL = 0.08      # boundary this far inside a word's timing = mid-word cut (Scribe edges jitter ~0.05 s)
STRETCHED_WORD = 1.0     # a "word" longer than this is a Scribe timing artifact (stretched over silence): warn only
CREDITS_MIN_TAIL = 20.0  # a speechless tail at least this long is treated as end card / credits
END_HOLD_ALLOW = 10.0    # story beats may run this long past the last spoken word before credits are assumed
CADENCE_RUN = 3          # credits roll as evenly timed cards: this many equal shot intervals in the tail ...
CADENCE_TOL = 0.12       # ... within this spread ...
CADENCE_RANGE = (1.5, 6.0)  # ... each this long

MUSIC_KEYS = ("మ్యూజిక్", "పాట", "సంగీత", "గీతం", "music", "song")
LAUGH_KEYS = ("నవ్వు", "laugh")
KS_EXTRA = (5, 10)


# ---- episode context ----------------------------------------------------------------------------------

def _is_music(text):
    t = text.lower()
    return any(k in t for k in MUSIC_KEYS)


def _is_laugh(text):
    t = text.lower()
    return any(k in t for k in LAUGH_KEYS)


@dataclass
class Ctx:
    """Everything the checks need about one episode. Built from files (`load_ctx`) or directly in tests."""
    video_id: str
    words: list                   # [{word, start, end, ...}]
    duration: float
    events: list = field(default_factory=list)       # [{text, start, end}]
    sentences: list = field(default_factory=list)    # [{words: [first, last]}]
    cuts: list | None = None      # shot-change times, or None if unknown
    shots_source: str = ""
    meta: dict | None = None      # YouTube metadata (description, chapters), optional
    references: list = field(default_factory=list)   # sibling-episode transcript dicts (recap detection)
    reference_ids: list = field(default_factory=list)

    def __post_init__(self):
        self.starts = [w["start"] for w in self.words]
        self.mids = [(w["start"] + w["end"]) / 2 for w in self.words]
        self.music = [(e["start"], e["end"]) for e in self.events if _is_music(e["text"])]
        self.laughs = [(e["start"], e["end"]) for e in self.events if _is_laugh(e["text"])]
        self.sent_of = [None] * len(self.words)
        if self.sentences:
            for k, s in enumerate(self.sentences):
                a, b = s["words"]
                for j in range(a, min(b, len(self.words) - 1) + 1):
                    self.sent_of[j] = k
        else:  # fallback: split on 0.7 s gaps
            k = 0
            for j, w in enumerate(self.words):
                if j and w["start"] - self.words[j - 1]["end"] >= 0.7:
                    k += 1
                self.sent_of[j] = k
        if self.cuts is not None:
            self.cuts = sorted(self.cuts)
        self.first_word = self.words[0]["start"] if self.words else 0.0
        self.last_word = self.words[-1]["end"] if self.words else 0.0
        self.credits_zone, self.credits_status = self._credits()
        self._zones()

    def _zones(self):
        """Opening zone (hard = never story, soft = uncertain) and sponsor segments, from free signals."""
        if not self.words:
            self.opening, self.sponsors = {"status": "no words", "story_start": None, "hard": [], "soft": [],
                                           "parts": [], "head_end": 0.0}, []
        else:
            d = {"word_timestamps": self.words, "sentences": self.sentences, "audio_events": self.events,
                 "audio_duration_secs": self.duration}
            z = EZ.episode_zones(d, self.cuts, self.meta, self.references, self.video_id)
            self.opening, self.sponsors = z["opening"], z["sponsors"]
        self.intro_zone = (0.0, self.opening["head_end"])   # contiguous hard zone from 0 (report only)

    def _credits(self):
        tail = self.duration - self.last_word
        if tail < CREDITS_MIN_TAIL:
            return None, f"unchecked (speechless tail {tail:.1f}s < {CREDITS_MIN_TAIL:.0f}s; credits, if any, not detectable)"
        start = self.last_word + END_HOLD_ALLOW
        how = f"last word {self.last_word:.1f}s + {END_HOLD_ALLOW:.0f}s hold allowance"
        if self.cuts:
            tc = [c for c in self.cuts if c > self.last_word]
            d = [b - a for a, b in zip(tc, tc[1:])]
            for i in range(len(d) - CADENCE_RUN + 1):
                run = d[i:i + CADENCE_RUN]
                if max(run) - min(run) <= CADENCE_TOL and CADENCE_RANGE[0] <= min(run) and max(run) <= CADENCE_RANGE[1]:
                    if tc[i] < start:
                        start, how = tc[i], f"evenly timed shot cards from {tc[i]:.1f}s"
                    break
        return (start, self.duration), f"heuristic ({how}; speechless tail {tail:.0f}s)"

    # words whose midpoint lies inside [s, e]
    def words_in(self, s, e):
        i = bisect.bisect_left(self.mids, s)
        j = bisect.bisect_right(self.mids, e)
        return list(range(i, j))

    def near_cut(self, t, tol=SHOT_TOL):
        if not self.cuts:
            return None
        k = bisect.bisect_left(self.cuts, t)
        best = None
        for c in self.cuts[max(0, k - 1):k + 1]:
            if abs(c - t) <= tol and (best is None or abs(c - t) < abs(best - t)):
                best = c
        return best

    def cuts_between(self, a, b):
        if not self.cuts:
            return []
        return self.cuts[bisect.bisect_right(self.cuts, a):bisect.bisect_left(self.cuts, b)]

    def text(self, idxs, n=12):
        ws = [self.words[j]["word"] for j in idxs[:n]]
        return " ".join(ws) + (" ..." if len(idxs) > n else "")


def load_ctx(video_id, transcript_path=None, shots_dir=None):
    tp = resolve(transcript_path) if transcript_path else ROOT / "scribe_dataset" / video_id / f"{video_id}_scribe_transcript.json"
    d = load_json(tp)
    W = d["word_timestamps"]
    dur = float(d.get("audio_duration_secs") or (W[-1]["end"] if W else 0.0))
    sp = Path(shots_dir or ROOT / "runs" / "video") / video_id / "shots.json"
    cuts, src = None, "missing"
    if sp.exists():
        s = load_json(sp)
        cuts, src = list(s.get("cuts") or []), str(sp.relative_to(ROOT) if sp.is_relative_to(ROOT) else sp)
    meta = _meta(video_id)
    refs, ref_ids = siblings(video_id, meta)
    return Ctx(video_id, W, dur, d.get("audio_events") or [], d.get("sentences") or [], cuts, src, meta, refs, ref_ids)


def _meta(video_id):
    p = ROOT / "scribe_dataset" / video_id / "youtube_meta.json"
    return load_json(p) if p.exists() else None


def _series_key(meta):
    """(channel, series name) from YouTube metadata: the title's first '|'-separated part, e.g.
    'Couple Goals || Episode - 2 || ...' -> 'couple goals'."""
    if not meta or not meta.get("title"):
        return None
    name = " ".join(meta["title"].split("|")[0].lower().split())
    return (meta.get("channel") or "", name) if name else None


def siblings(video_id, meta=None):
    """Transcripts of other episodes of the same series (same channel and title prefix) in scribe_dataset/, used to
    spot a 'previously on' recap that replays an earlier episode's lines."""
    key = _series_key(meta)
    if key is None:
        return [], []
    refs, ids = [], []
    for mp in sorted((ROOT / "scribe_dataset").glob("*/youtube_meta.json")):
        vid = mp.parent.name
        tp = mp.parent / f"{vid}_scribe_transcript.json"
        if vid == video_id or not tp.exists() or _series_key(load_json(mp)) != key:
            continue
        refs.append(load_json(tp))
        ids.append(vid)
    return refs, ids


# ---- clip geometry ------------------------------------------------------------------------------------

def segments(clip, ctx):
    """Play-order (start, end) seconds. Falls back to word pieces when a clip has no time_segments."""
    ts = clip.get("time_segments")
    if ts:
        out = []
        for s in ts:
            a, b = (s["start"], s["end"]) if isinstance(s, dict) else (s[0], s[1])
            out.append((float(a), float(b)))
        return out
    return [(ctx.words[p["start_word"]]["start"], ctx.words[p["end_word"]]["end"]) for p in clip.get("pieces") or []]


def union(ivs):
    out = []
    for a, b in sorted(ivs):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def length(ivs):
    return sum(b - a for a, b in ivs)


def inter(A, B):
    """Total overlap of two interval unions."""
    A, B, tot, i, j = union(A), union(B), 0.0, 0, 0
    while i < len(A) and j < len(B):
        lo, hi = max(A[i][0], B[j][0]), min(A[i][1], B[j][1])
        tot += max(0.0, hi - lo)
        if A[i][1] < B[j][1]:
            i += 1
        else:
            j += 1
    return tot


def core(clip, ctx):
    """The GT's spoken core: its kept word pieces in seconds (silent holds excluded)."""
    ps = clip.get("pieces") or []
    if ps:
        return union([(ctx.words[p["start_word"]]["start"], ctx.words[p["end_word"]]["end"]) for p in ps])
    return union(segments(clip, ctx))


def played_words(segs, ctx):
    out = []
    for s, e in segs:
        out.extend(ctx.words_in(s, e))
    return out


@dataclass
class Shape:
    segs: list
    cold: bool               # first segment is a lift (declared or a backward jump after it)
    declared_cold: bool | None
    body: list
    cuts: list               # (after_body_index, kind, gap_s) kind in {skip, backward}
    lift_skips: list         # body gaps that only skip the lifted moment in place
    duration: float
    src_start: float
    play_end: float


def shape(clip, ctx):
    segs = segments(clip, ctx)
    declared = clip.get("cold_open")
    detected = len(segs) > 1 and segs[0][0] >= segs[1][1] - CONTIG_TOL
    cold = bool(declared) or detected
    body = segs[1:] if cold and len(segs) > 1 else segs
    lift = segs[0] if cold and len(segs) > 1 else None
    cuts, lift_skips = [], []
    for k, (a, b) in enumerate(zip(body, body[1:])):
        gap = b[0] - a[1]
        if abs(gap) <= CONTIG_TOL:
            continue
        if lift and gap > 0 and a[1] - CONTIG_TOL <= lift[0] and lift[1] <= b[0] + CONTIG_TOL:
            lift_skips.append((k, round(gap, 3)))
            continue
        cuts.append((k, "skip" if gap > 0 else "backward", round(gap, 3)))
    return Shape(segs, cold, declared, body, cuts, lift_skips, sum(b - a for a, b in segs),
                 min(a for a, _ in body) if body else 0.0, segs[-1][1] if segs else 0.0)


# ---- rule checks --------------------------------------------------------------------------------------

def _res(status, value=None, detail=""):
    return {"status": status, "value": value, "detail": detail}


def check_clip(clip, ctx):
    """All rule checks for one clip -> {name: {status: pass|warn|fail|unchecked|n/a, value, detail}}."""
    sh = shape(clip, ctx)
    segs = sh.segs
    R = {}
    if not segs:
        return {"segments": _res("fail", 0, "no time_segments or pieces")}
    bad = [(a, b) for a, b in segs if not (0 <= a < b <= ctx.duration + 0.5)]
    R["segments"] = _res("fail" if bad else "pass", len(segs), f"invalid {bad}" if bad else "")

    # lead-in: first segment start -> its first word
    s0, e0 = segs[0]
    w0 = ctx.words_in(s0, e0)
    if w0:
        lead = ctx.words[w0[0]]["start"] - s0
        mus = inter([(s0, ctx.words[w0[0]]["start"])], ctx.music) if lead > 0 else 0.0
        st = "pass" if lead <= LEAD_IN_WARN + LEAD_IN_SLACK else ("warn" if lead <= LEAD_IN_FAIL + LEAD_IN_SLACK else "fail")
        det = ""
        if st != "pass":
            det = f"{lead:.2f}s before the first word" + (f" ({mus:.1f}s of it music)" if mus > 0.2 else " (silence)")
        R["lead_in"] = _res(st, round(lead, 3), det)
    else:
        laugh = inter([(s0, e0)], ctx.laughs) > 0.2
        mus = inter([(s0, e0)], ctx.music) > 0.2
        if sh.cold and laugh and not mus:
            R["lead_in"] = _res("warn", None, "opener has no words: laughter reaction lift (check it reads without context)")
        else:
            R["lead_in"] = _res("fail", None, "opener has no words: " + ("music" if mus else "silence/visual") + " opener")

    # replay: any source moment played twice
    reps = []
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            ov = min(segs[i][1], segs[j][1]) - max(segs[i][0], segs[j][0])
            if ov > OVERLAP_TOL:
                both = sorted(set(ctx.words_in(*segs[i])) & set(ctx.words_in(*segs[j])))
                reps.append(f"seg{i} & seg{j} overlap {ov:.2f}s" + (f" ('{ctx.text(both, 8)}')" if both else ""))
    R["replay"] = _res("fail" if reps else "pass", len(reps), "; ".join(reps))

    # cold-open lift
    if sh.cold and len(segs) > 1:
        a, b = segs[0]
        d = b - a
        probs, warns = [], []
        if not (LIFT_MIN - LIFT_SLACK <= d <= LIFT_MAX + LIFT_SLACK):
            probs.append(f"lift {d:.2f}s outside {LIFT_MIN}-{LIFT_MAX}s")
        if ctx.cuts is None:
            warns.append("shot changes unknown")
        elif ctx.near_cut(b) is None:
            probs.append(f"lift ends at {b:.2f}s, not within {SHOT_TOL}s of a shot change")
        lw = ctx.words_in(a, b)
        if lw and b - ctx.words[lw[-1]]["end"] > LIFT_QUIET_TAIL:
            warns.append(f"quiet tail {b - ctx.words[lw[-1]]['end']:.2f}s after the lifted words")
        if any(la < b - SHOT_TOL and le > b + 0.1 for la, le in ctx.laughs):
            warns.append("lift ends mid-laugh")
        if sh.declared_cold is False:
            warns.append("first segment is a backward jump but cold_open is false")
        st = "fail" if probs else ("warn" if warns else "pass")
        R["cold_open_lift"] = _res(st, round(d, 3), "; ".join(probs + warns))
    elif sh.declared_cold:
        R["cold_open_lift"] = _res("warn", None, "cold_open is true but there is a single segment")
    else:
        R["cold_open_lift"] = _res("n/a")

    # internal cuts (excluding the cold open and its in-place skip)
    back = [c for c in sh.cuts if c[1] == "backward"]
    det = f"{len(sh.cuts)} cut(s)" + (f", {len(back)} backward (reorder inside the body)" if back else "") \
        + (f", +{len(sh.lift_skips)} lift skip" if sh.lift_skips else "")
    R["internal_cuts"] = _res("fail" if len(sh.cuts) > MAX_CUTS else ("warn" if back else "pass"), len(sh.cuts), det)

    # shot flashes at segment edges (only ones the edit creates; a source shot that is itself short is not ours)
    if ctx.cuts is None:
        R["flash"] = _res("unchecked", None, "no shots.json")
    else:
        fl = []
        for k, (a, b) in enumerate(segs):
            inside = ctx.cuts_between(a + SHOT_TOL, b - SHOT_TOL)
            if not inside:
                continue
            c = inside[-1]
            nxt = ctx.cuts_between(c + 1e-6, ctx.duration + 1)
            if b - c < FLASH_S and (not nxt or nxt[0] > b + SHOT_TOL):
                fl.append(f"seg{k} ends {b - c:.2f}s after the shot change at {c:.2f}s")
            c = inside[0]
            prv = ctx.cuts_between(-1, c - 1e-6)
            if c - a < FLASH_S and (not prv or prv[-1] < a - SHOT_TOL):
                fl.append(f"seg{k} starts {c - a:.2f}s before the shot change at {c:.2f}s")
        R["flash"] = _res("fail" if fl else "pass", len(fl), "; ".join(fl))

    # mid-word cuts (a segment edge inside a word's timing) and lines trimmed inside (warn only)
    mw, mw_soft, ml = [], [], []
    played = set(played_words(segs, ctx))
    for k, (a, b) in enumerate(segs):
        for t, edge in ((a, "start"), (b, "end")):
            i = bisect.bisect_right(ctx.starts, t) - 1
            if 0 <= i < len(ctx.words):
                w = ctx.words[i]
                if w["start"] + MID_WORD_TOL < t < w["end"] - MID_WORD_TOL:
                    soft = w["end"] - w["start"] > STRETCHED_WORD
                    (mw_soft if soft else mw).append(
                        f"seg{k} {edge} {t:.2f}s inside '{w['word']}' ({w['start']:.2f}-{w['end']:.2f}"
                        + (", stretched Scribe timing)" if soft else ")"))
        ws = ctx.words_in(a, b)
        if not ws or (k == 0 and sh.cold and len(segs) > 1):
            continue
        for j, nb in ((ws[0], ws[0] - 1), (ws[-1], ws[-1] + 1)):
            if 0 <= nb < len(ctx.words) and nb not in played and ctx.sent_of[nb] == ctx.sent_of[j]:
                ml.append(f"seg{k} trims line at '{ctx.words[j]['word']}' | '{ctx.words[nb]['word']}'")
    R["mid_word"] = _res("fail" if mw else ("warn" if mw_soft else "pass"), len(mw), "; ".join(mw + mw_soft))
    R["mid_line"] = _res("warn" if ml else "pass", len(ml), "; ".join(ml))

    # reaction hold
    a, b = segs[-1]
    lw = ctx.words_in(a, b)
    hold = b - ctx.words[lw[-1]]["end"] if lw else b - a
    on = ctx.near_cut(b)
    ok = hold >= HOLD_MIN - SHOT_TOL or (on is not None and hold > 0)
    if ok:
        R["reaction_hold"] = _res("pass", round(hold, 3), "ends on a shot change" if hold < HOLD_MIN - SHOT_TOL else "")
    else:
        R["reaction_hold"] = _res("unchecked" if ctx.cuts is None and hold > 0 else "fail", round(hold, 3),
                                  f"ends {hold:.2f}s after the last word, not on a shot change")

    # length
    d = sh.duration
    st = "fail" if d < HARD_MIN_S or d > HARD_MAX_S else ("warn" if d > SOFT_MAX_S else "pass")
    det = ""
    vd = clip.get("video_duration")
    if vd is not None and abs(vd - d) > 0.1:
        det = f"declared video_duration {vd} != segments {d:.2f}"
        st = "warn" if st == "pass" else st
    R["length"] = _res(st, round(d, 2), det)

    # opening zone (named recap_zone for continuity with earlier reports): hard parts fail, uncertain parts warn
    hz, sz = inter(segs, ctx.opening["hard"]), inter(segs, ctx.opening["soft"])
    if hz > OVERLAP_TOL or sz > OVERLAP_TOL:
        lvl = "hard" if hz > OVERLAP_TOL else "soft"
        kinds = [f"{p['kind']} {p['start']:.1f}-{p['end']:.1f}s" for p in ctx.opening["parts"]
                 if p["level"] == lvl and inter(segs, [(p["start"], p["end"])]) > OVERLAP_TOL]
        R["recap_zone"] = _res("fail" if lvl == "hard" else "warn", round(hz if lvl == "hard" else sz, 2),
                               f"{(hz if lvl == 'hard' else sz):.1f}s inside the opening zone ({'; '.join(kinds)})"
                               + ("" if lvl == "hard" else ", uncertain: check it is story"))
    else:
        R["recap_zone"] = _res("pass", 0.0)
    # sponsor segments (warn: detection is lexical)
    spo = [(s["start"], s["end"]) for s in ctx.sponsors]
    so = inter(segs, spo)
    R["sponsor"] = _res("warn" if so > OVERLAP_TOL else "pass", round(so, 2),
                        f"{so:.1f}s overlaps a likely sponsor segment "
                        + ", ".join(f"{a:.1f}-{b:.1f}s" for a, b in spo if inter(segs, [(a, b)]) > OVERLAP_TOL)
                        if so > OVERLAP_TOL else "")
    if ctx.credits_zone is None:
        R["credits_zone"] = _res("unchecked", None, ctx.credits_status)
    else:
        cz = inter(segs, [ctx.credits_zone])
        R["credits_zone"] = _res("fail" if cz > OVERLAP_TOL else "pass", round(cz, 2),
                                 f"{cz:.1f}s inside the assumed credits from {ctx.credits_zone[0]:.1f}s" if cz > OVERLAP_TOL else "")
    return R


def summarize_checks(R):
    fails = [k for k, v in R.items() if v["status"] == "fail"]
    warns = [k for k, v in R.items() if v["status"] == "warn"]
    return {"pass": not fails, "fails": fails, "warns": warns}


# ---- matching and pair metrics ------------------------------------------------------------------------

def overlap_stats(g, p, ctx):
    gc, gs, ps = core(g, ctx), union(segments(g, ctx)), union(segments(p, ctx))
    cov = inter(gc, ps) / length(gc) if length(gc) else 0.0
    i = inter(gs, ps)
    iou = i / (length(gs) + length(ps) - i) if gs or ps else 0.0
    return round(cov, 3), round(iou, 3)


def match(gts, props, ctx):
    """Rank-order greedy matching. Returns (assign {gt_idx: prop_idx}, stats {(gi, pi): (cov, iou)})."""
    stats = {(gi, pi): overlap_stats(g, p, ctx) for gi, g in enumerate(gts) for pi, p in enumerate(props)}
    assign, taken = {}, set()
    for pi in range(len(props)):
        el = [(stats[gi, pi][1], gi) for gi in range(len(gts))
              if gi not in assign and stats[gi, pi][0] >= MATCH_COV and stats[gi, pi][1] >= MATCH_IOU]
        if el:
            _, gi = max(el)
            assign[gi] = pi
            taken.add(pi)
    return assign, stats


def pair_metrics(g, p, ctx):
    G, P = shape(g, ctx), shape(p, ctx)
    gw, pw = set(played_words(G.segs, ctx)), set(played_words(P.segs, ctx))
    # payoff = the GT's last line (last sentence's words inside its last segment)
    a, b = G.segs[-1]
    last = ctx.words_in(a, b)
    pay = [j for j in last if ctx.sent_of[j] == ctx.sent_of[last[-1]]] if last else []
    pay_kept = (sum(1 for j in pay if j in pw) / len(pay)) if pay else 1.0
    hold_kept = P.play_end >= G.play_end - END_TOL
    lost_front = sorted(j for j in gw - pw if ctx.words[j]["end"] <= P.src_start + 0.05)
    drops_setup = P.src_start > G.src_start + START_TOL and bool(lost_front)
    tp = len(gw & pw)
    prec = tp / len(pw) if pw else 0.0
    rec = tp / len(gw) if gw else 0.0
    return {
        "start_delta": round(P.src_start - G.src_start, 2),
        "end_delta": round(P.play_end - G.play_end, 2),
        "payoff_kept": pay_kept >= PAYOFF_SHARE,
        "payoff_share": round(pay_kept, 2),
        "hold_kept": hold_kept,
        "payoff_and_hold": pay_kept >= PAYOFF_SHARE and hold_kept,
        "drops_setup": drops_setup,
        "setup_words_lost": len(lost_front),
        "setup_lost_text": ctx.text(lost_front, 10) if lost_front else "",
        "gt_words_missed": len(gw - pw),
        "extra_words": len(pw - gw),
        "word_precision": round(prec, 3),
        "word_recall": round(rec, 3),
        "word_f1": round(2 * prec * rec / (prec + rec), 3) if prec + rec else 0.0,
        "length_ratio": round(P.duration / G.duration, 3) if G.duration else None,
        "cuts_prop": len(P.cuts),
        "cuts_gt": len(G.cuts),
        "purpose_prop": p.get("clip_purpose"),
        "purpose_gt": g.get("clip_purpose"),
        "same_purpose": p.get("clip_purpose") == g.get("clip_purpose"),
        "cold_prop": P.cold,
        "cold_gt": G.cold,
    }


# ---- per-episode evaluation ---------------------------------------------------------------------------

def clips_of(doc):
    if doc.get("proposals") is not None:
        ps = list(doc["proposals"])
        if all("rank" in p for p in ps):
            ps.sort(key=lambda c: c["rank"])
        return ps
    return list(doc.get("clips") or [])


def gt_clips(gold):
    return [c for c in gold.get("clips") or [] if c.get("content_type") == "web_series"]


def _clip_row(c, rank, ctx):
    sh = shape(c, ctx)
    words = played_words(sh.segs, ctx)
    return {"id": c.get("id"), "rank": rank, "segments": [[round(a, 3), round(b, 3)] for a, b in sh.segs],
            "src_start": round(sh.src_start, 2), "play_end": round(sh.play_end, 2), "duration": round(sh.duration, 2),
            "purpose": c.get("clip_purpose"), "cold_open": sh.cold, "title": c.get("title", ""),
            "excerpt": ctx.text(words, 12)}


def evaluate_episode(gold, prop_doc, ctx):
    gts = gt_clips(gold)
    props = clips_of(prop_doc)
    assign, stats = match(gts, props, ctx)
    inv = {pi: gi for gi, pi in assign.items()}
    n = len(gts)

    gt_rows = []
    for gi, g in enumerate(gts):
        row = _clip_row(g, gi + 1, ctx)
        if gi in assign:
            pi = assign[gi]
            row.update({"matched": True, "prop_rank": pi + 1, "prop_id": props[pi].get("id"),
                        "cov": stats[gi, pi][0], "iou": stats[gi, pi][1]})
            row["pair"] = pair_metrics(g, props[pi], ctx)
        else:
            best = max(range(len(props)), key=lambda pi: stats[gi, pi], default=None)
            row.update({"matched": False})
            if best is not None and stats[gi, best][0] > 0:
                row["nearest"] = {"prop_rank": best + 1, "prop_id": props[best].get("id"),
                                  "cov": stats[gi, best][0], "iou": stats[gi, best][1],
                                  "matched_elsewhere": best in inv}
        gt_rows.append(row)

    prop_rows = []
    for pi, p in enumerate(props):
        row = _clip_row(p, pi + 1, ctx)
        R = check_clip(p, ctx)
        row["checks"] = R
        row.update(summarize_checks(R))
        if pi in inv:
            row["status"] = "matched"
            row["gt"] = gts[inv[pi]].get("id")
        else:
            el = [gi for gi in range(n) if stats[gi, pi][0] >= MATCH_COV and stats[gi, pi][1] >= MATCH_IOU]
            part = [gi for gi in range(n) if stats[gi, pi][0] >= PARTIAL_COV or stats[gi, pi][1] >= 0.1]
            if el:
                row["status"], row["gt"] = "duplicate", gts[el[0]].get("id")
            elif part:
                gi = max(part, key=lambda gi: stats[gi, pi])
                row["status"], row["gt"] = "partial", gts[gi].get("id")
                row["cov"], row["iou"] = stats[gi, pi]
            else:
                row["status"] = "new"
        prop_rows.append(row)

    gt_checks = []
    for gi, g in enumerate(gts):
        R = check_clip(g, ctx)
        gt_checks.append({"id": g.get("id"), "checks": R, **summarize_checks(R)})

    ranks = [r["prop_rank"] for r in gt_rows if r["matched"]]
    rec = {"any": len(ranks), "@k_gt": sum(r <= n for r in ranks)}
    for k in KS_EXTRA:
        rec[f"@{k}"] = sum(r <= k for r in ranks)
    pairs = [r["pair"] for r in gt_rows if r["matched"]]

    def mean(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 3) if xs else None

    summary = {
        "video_id": ctx.video_id, "n_gt": n, "k_gt": n, "n_props": len(props),
        "recall": rec, "recall_frac": {k: round(v / n, 3) if n else None for k, v in rec.items()},
        "missed": [r["id"] for r in gt_rows if not r["matched"]],
        "mean_abs_start_delta": mean([abs(x["start_delta"]) for x in pairs]),
        "mean_abs_end_delta": mean([abs(x["end_delta"]) for x in pairs]),
        "payoff_and_hold_kept": sum(x["payoff_and_hold"] for x in pairs),
        "drops_setup": sum(x["drops_setup"] for x in pairs),
        "mean_length_ratio": mean([x["length_ratio"] for x in pairs]),
        "mean_word_f1": mean([x["word_f1"] for x in pairs]),
        "same_purpose": sum(x["same_purpose"] for x in pairs),
        "n_matched": len(pairs),
        "props_pass_rules": sum(r["pass"] for r in prop_rows),
        "gt_pass_rules": sum(r["pass"] for r in gt_checks),
        "status_counts": {s: sum(r["status"] == s for r in prop_rows) for s in ("matched", "duplicate", "partial", "new")},
        "fail_counts": _count([f for r in prop_rows for f in r["fails"]]),
        "credits": ctx.credits_status, "credits_zone": ctx.credits_zone, "intro_zone": ctx.intro_zone,
        "opening": ctx.opening, "sponsors": ctx.sponsors, "sibling_episodes": ctx.reference_ids,
        "shots": ctx.shots_source,
    }
    return {"summary": summary, "gt": gt_rows, "proposals": prop_rows, "gt_checks": gt_checks}


def _count(xs):
    out = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


# ---- report -------------------------------------------------------------------------------------------

def _t(s):
    m, x = divmod(max(0.0, s), 60)
    return f"{int(m)}:{x:04.1f}"


def _pct(a, b):
    return f"{a}/{b}" + (f" ({100 * a / b:.0f}%)" if b else "")


def _yn(x):
    return "yes" if x else "no"


def _opening_md(s):
    o = s.get("opening") or {}
    parts = ", ".join(f"{p['kind']} {_t(p['start'])}-{_t(p['end'])}" + ("" if p["level"] == "hard" else " (uncertain)")
                      for p in o.get("parts", []))
    st = o.get("story_start")
    txt = f"Opening zone: {parts or 'none'}; " + (f"story starts {_t(st)}." if st is not None else f"{o.get('status')}.")
    sib = s.get("sibling_episodes")
    if sib:
        txt += f" Recap check vs {', '.join(sib)}."
    sp = s.get("sponsors") or []
    txt += " Sponsor: " + ("; ".join(f"{_t(x['start'])}-{_t(x['end'])} ({', '.join(x['evidence']['cues'][:4])}"
                                     + (", #ad brand" if x["evidence"]["brand_from_meta"] else "") + ")" for x in sp)
                           if sp else "none detected") + "."
    return txt


def render_markdown(results, run_dir):
    L = [f"# Web-series eval: `{run_dir}`", "",
         f"Matching: a proposal matches a GT clip if it covers >= {MATCH_COV:.0%} of the GT's spoken core and the "
         f"source-span IoU is >= {MATCH_IOU}; proposals are taken in rank order, each matching at most one GT clip. "
         "Unmatched proposals are not errors: `new` and `partial` ones need the founder's rating.", ""]
    L += ["## Episode summary", "",
          "| video | GT | props | recall (any) | recall@k=GT | recall@5 | missed | mean abs dstart / dend (s) | payoff+hold kept | drops setup | len ratio | word F1 | same purpose | props pass rules | GT pass rules | new / partial / dup |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        s = r["summary"]
        n, m = s["n_gt"], s["n_matched"]
        sc = s["status_counts"]
        L.append(f"| {s['video_id']} | {n} | {s['n_props']} | {_pct(s['recall']['any'], n)} | {_pct(s['recall']['@k_gt'], n)} | "
                 f"{_pct(s['recall']['@5'], n)} | {', '.join(s['missed']) or '-'} | {s['mean_abs_start_delta']} / {s['mean_abs_end_delta']} | "
                 f"{_pct(s['payoff_and_hold_kept'], m)} | {s['drops_setup']}/{m} | {s['mean_length_ratio']} | {s['mean_word_f1']} | "
                 f"{_pct(s['same_purpose'], m)} | {_pct(s['props_pass_rules'], s['n_props'])} | {_pct(s['gt_pass_rules'], n)} | "
                 f"{sc['new']} / {sc['partial']} / {sc['duplicate']} |")
    tot_g = sum(r["summary"]["n_gt"] for r in results)
    tot_any = sum(r["summary"]["recall"]["any"] for r in results)
    tot_k = sum(r["summary"]["recall"]["@k_gt"] for r in results)
    tot_5 = sum(r["summary"]["recall"]["@5"] for r in results)
    L += ["", f"Overall: recall {_pct(tot_any, tot_g)}, recall@k=GT {_pct(tot_k, tot_g)}, recall@5 per episode summed "
          f"{_pct(tot_5, tot_g)}. Small sample: every number above rests on {tot_g} GT clips from {len(results)} episode(s)."]
    for r in results:
        s = r["summary"]
        L += ["", f"## {s['video_id']}", "",
              f"Shots: `{s['shots']}`. {_opening_md(s)} Credits: {s['credits']}.", "",
              "### GT clips", "",
              "| GT | purpose | src | len | match rank | cov / IoU | dstart | dend | payoff / hold | drops setup | len ratio | word P/R | cuts P/GT | same purpose | cold P/GT |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for g in r["gt"]:
            head = f"| {g['id']} | {g['purpose']} | {_t(g['src_start'])}-{_t(g['play_end'])} | {g['duration']:.0f}s "
            if g["matched"]:
                x = g["pair"]
                L.append(head + f"| {g['prop_rank']} | {g['cov']} / {g['iou']} | {x['start_delta']:+.1f} | {x['end_delta']:+.1f} | "
                         f"{_yn(x['payoff_kept'])} / {_yn(x['hold_kept'])} | "
                         f"{('yes: ' + str(x['setup_words_lost']) + ' words') if x['drops_setup'] else 'no'} | {x['length_ratio']} | "
                         f"{x['word_precision']}/{x['word_recall']} | {x['cuts_prop']}/{x['cuts_gt']} | {_yn(x['same_purpose'])} | "
                         f"{_yn(x['cold_prop'])}/{_yn(x['cold_gt'])} |")
            else:
                nb = g.get("nearest")
                near = (f"nearest: rank {nb['prop_rank']} cov {nb['cov']} IoU {nb['iou']}" +
                        (" (matched to another GT)" if nb["matched_elsewhere"] else "")) if nb else "no overlap"
                L.append(head + f"| MISSED | {near} | | | | | | | | | |")
        missed = [g for g in r["gt"] if not g["matched"]]
        if missed:
            L += ["", "Missed GT clips:"]
            for g in missed:
                L.append(f"- {g['id']} ({g['purpose']}, {_t(g['src_start'])}-{_t(g['play_end'])}): {g['title']} | {g['excerpt']}")
        setup = [g for g in r["gt"] if g["matched"] and g["pair"]["drops_setup"]]
        if setup:
            L += ["", "Setup dropped (proposal starts later and loses GT-kept lines):"]
            for g in setup:
                L.append(f"- {g['id']} vs rank {g['prop_rank']}: starts {g['pair']['start_delta']:+.1f}s, loses "
                         f"{g['pair']['setup_words_lost']} words: {g['pair']['setup_lost_text']}")
        L += ["", "### Proposals", "",
              "| rank | status | src | len | purpose | cold | lead-in | cuts | hold | fails | warns | excerpt |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for p in r["proposals"]:
            c = p["checks"]
            st = p["status"] + (f" ({p['gt']})" if p.get("gt") else "")
            L.append(f"| {p['rank']} | {st} | {_t(p['src_start'])}-{_t(p['play_end'])} | {p['duration']:.0f}s | {p['purpose']} | "
                     f"{_yn(p['cold_open'])} | {c.get('lead_in', {}).get('value')} | {c.get('internal_cuts', {}).get('value')} | "
                     f"{c.get('reaction_hold', {}).get('value')} | {', '.join(p['fails']) or '-'} | {', '.join(p['warns']) or '-'} | "
                     f"{p['excerpt']} |")
        new = [p for p in r["proposals"] if p["status"] in ("new", "partial")]
        if new:
            L += ["", "New moments (outside the GT, NOT wrong; need the founder's rating):"]
            for p in new:
                L.append(f"- rank {p['rank']} [{p['status']}{(' ' + p['gt']) if p.get('gt') else ''}] "
                         f"{_t(p['src_start'])}-{_t(p['play_end'])} {p['duration']:.0f}s {p['purpose']}: {p['title']} | {p['excerpt']}")
        fails = [(p["rank"], k, v) for p in r["proposals"] for k, v in p["checks"].items() if v["status"] == "fail"]
        if fails:
            L += ["", "Rule failures (proposals):"]
            for rank, k, v in fails:
                L.append(f"- rank {rank} {k}: {v['detail'] or v['value']}")
        gfail = [(g["id"], k, v) for g in r["gt_checks"] for k, v in g["checks"].items() if v["status"] in ("fail", "warn")]
        L += ["", f"GT sanity (rule checks on the approved GT clips): {_pct(s['gt_pass_rules'], s['n_gt'])} pass."]
        for gid, k, v in gfail:
            L.append(f"- {gid} {k} [{v['status']}]: {v['detail'] or v['value']}")
    return "\n".join(L) + "\n"


# ---- CLI ----------------------------------------------------------------------------------------------

def run(run_dir, video=None, gold_dir=None, out_dir=None, shots_dir=None, write=True):
    run_dir = resolve(run_dir)
    gold_dir = resolve(gold_dir) if gold_dir else ROOT / "data" / "gold"
    results, skipped = [], []
    for gp in sorted(gold_dir.glob("*.json")):
        vid = gp.stem
        if video and vid != video:
            continue
        gold = load_json(gp)
        if not gt_clips(gold):
            continue
        pp = run_dir / f"{vid}.json"
        if not pp.exists():
            skipped.append(vid)
            continue
        tpath = (gold.get("transcript") or {}).get("path")
        ctx = load_ctx(vid, tpath, shots_dir)
        results.append(evaluate_episode(gold, load_json(pp), ctx))
    params = {k: v for k, v in globals().items() if k.isupper() and isinstance(v, (int, float, tuple))}
    params["zones"] = {k: v for k, v in vars(EZ).items() if k.isupper() and isinstance(v, (int, float))}
    out = {"run_dir": str(run_dir), "params": params,
           "skipped_no_proposals": skipped, "episodes": results}
    if write and results:
        od = resolve(out_dir) if out_dir else run_dir
        write_json(od / "eval_report.json", out)
        (od / "eval_report.md").write_text(render_markdown(results, run_dir), encoding="utf-8")
    return out


def main(argv=None):
    utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_dir")
    ap.add_argument("--video")
    ap.add_argument("--gold-dir")
    ap.add_argument("--out-dir", help="where eval_report.{md,json} go (default: the run dir)")
    ap.add_argument("--shots-dir", help="default runs/video (expects <id>/shots.json)")
    a = ap.parse_args(argv)
    out = run(a.run_dir, a.video, a.gold_dir, a.out_dir, a.shots_dir)
    if not out["episodes"]:
        print(f"no proposal files with web-series GT in {a.run_dir} (looked for <video_id>.json)", file=sys.stderr)
        return 1
    od = resolve(a.out_dir) if a.out_dir else resolve(a.run_dir)
    print(render_markdown(out["episodes"], a.run_dir).split("\n## ")[1])
    print(f"wrote {od / 'eval_report.md'} and {od / 'eval_report.json'}")
    if out["skipped_no_proposals"]:
        print(f"no proposals for: {', '.join(out['skipped_no_proposals'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
