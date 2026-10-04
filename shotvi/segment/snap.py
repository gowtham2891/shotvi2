"""Boundary snapping: rough (start, end) -> nearest clean cut on word boundaries.

Every word boundary gets a *start quality* (how good it is to begin a clip on
word i) and an *end quality* (how good to end after word j): the pause length
relative to this video's pause distribution, end punctuation, turn/unit
boundaries and discourse openers raise it; "sentence clearly continues" cues
and fillers lower it. A clip never starts or ends on a listener backchannel
word. The snapper picks the candidate maximizing quality minus a distance
cost, preferring candidates above ``CLEAN`` quality.

Pre/post-roll never reaches into the neighbouring word, so the next
speaker's first syllable is not swallowed. A laughter event right after the
end can optionally be kept.

Search window: each boundary is first searched within ``max_shift`` of the
request. If no clean cut is there, the window is widened to 2x and 4x
``max_shift`` (``widen=True``, the default) and a boundary that lands outside
``max_shift`` is flagged ``start_widened`` / ``end_widened`` with a note.
``clean`` is True only when both cuts are clean, inside the base window, and
no fallback was used. ``strict=True`` raises ``SnapError`` instead of
returning a non-clean snap.

Excluded ranges (opening teaser / music / intro, see ``intro.py``): the
result always reports ``crosses_excluded``; with ``avoid_excluded=True`` (or
a tuple of kinds) no cut is placed inside them and the span never covers one.
"""
from __future__ import annotations

import bisect
from dataclasses import asdict, dataclass, field

from . import lexicon as L
from .loader import Transcript
from .pauses import PauseStats
from .text import ends_sentence, is_cutoff, is_filler, norm
from .turns import Turn
from .units import Unit

CLEAN = 1.0
NEG = float("-inf")
WIDEN_STEPS = (1, 2, 4)
EXCLUDED_KINDS = ("teaser", "music", "intro")


class SnapError(ValueError):
    """Raised by ``snap(..., strict=True)`` when the result would not be clean."""


@dataclass
class Snap:
    start_word: int
    end_word: int
    start: float          # first word start
    end: float            # last word end
    cut_in: float         # with pre-roll
    cut_out: float        # with post-roll (and optional trailing laughter)
    start_shift: float    # start - requested start
    end_shift: float
    start_quality: float
    end_quality: float
    clean: bool
    notes: list[str]
    start_widened: bool = False     # start lies beyond max_shift (found by widening / fallback)
    end_widened: bool = False
    fallback: bool = False          # no candidate in any window: the nearest usable word was taken
    crosses_excluded: bool = False  # span overlaps an excluded (teaser / music / intro) range
    excluded_ids: list[int] = field(default_factory=list)  # ids of those IntroRanges

    def to_dict(self):
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, float):
                d[k] = round(v, 3)
        return d


class Snapper:
    def __init__(self, tr: Transcript, ps: PauseStats, turns: list[Turn], units: list[Unit],
                 alpha: float = 0.3, excluded=()):
        """``excluded``: [(i0, i1, kind, id), ...] inclusive word ranges to report / avoid."""
        self.tr, self.ps, self.alpha = tr, ps, alpha
        self.excluded = [tuple(r) for r in excluded]
        ws = tr.words
        n = len(ws)
        self.starts = [w.start for w in ws]
        self.ends = [w.end for w in ws]
        bc = [False] * n
        for t in turns:
            for b in t.backchannels:
                for i in range(b.i0, b.i1 + 1):
                    bc[i] = True
        self.bc = bc
        turn_first = {t.main[0] for t in turns if t.main}
        turn_last = {t.main[-1] for t in turns if t.main}
        unit_first = {u.main[0] for u in units if u.main}
        unit_last = {u.main[-1] for u in units if u.main}
        # next / previous non-backchannel word
        nxt_main = [n] * n
        k = n
        for i in range(n - 1, -1, -1):
            nxt_main[i] = k
            if not bc[i]:
                k = i
        prv_main = [-1] * n
        k = -1
        for i in range(n):
            prv_main[i] = k
            if not bc[i]:
                k = i
        sq = [NEG] * n
        eq = [NEG] * n
        for i, w in enumerate(ws):
            if bc[i]:
                continue
            p = prv_main[i]
            gap = (w.start - ws[p].end) if p >= 0 else 10.0
            q = min(max(gap, 0.0) / ps.major, 2.0)
            if p < 0 or ends_sentence(ws[p].text):
                q += 0.6
            if i in turn_first:
                q += 1.0
            if i in unit_first:
                q += 0.3
            if norm(w.text) in L.OPENERS:
                q += 0.2
            if p >= 0 and (norm(ws[p].text) in L.CONTINUATION_END or is_cutoff(ws[p].text)):
                q -= 0.5
            if is_filler(w.text):
                q -= 0.4
            sq[i] = q
            nx = nxt_main[i]
            gap = (ws[nx].start - w.end) if nx < n else 10.0
            q = min(max(gap, 0.0) / ps.major, 2.0)
            if nx >= n or ends_sentence(w.text):
                q += 0.6
            if i in turn_last:
                q += 1.0
            if i in unit_last:
                q += 0.3
            if nx < n and norm(ws[nx].text) in L.OPENERS:
                q += 0.2
            if norm(w.text) in L.CONTINUATION_END or is_cutoff(w.text):
                q -= 0.6
            if is_filler(w.text):
                q -= 0.3
            eq[i] = q
        self.sq, self.eq = sq, eq

    # ------------------------------------------------------------------ #
    def _pick(self, qual, times, target, lo_t, hi_t, blocked=None):
        """Best index with time in [lo_t, hi_t] by quality - alpha*|shift|."""
        i0 = bisect.bisect_left(times, lo_t)
        i1 = bisect.bisect_right(times, hi_t)
        best = best_clean = None
        for i in range(max(0, i0), min(len(times), i1)):
            q = qual[i]
            if q == NEG or (blocked is not None and blocked(i)):
                continue
            sc = q - self.alpha * abs(times[i] - target)
            if best is None or sc > best[0]:
                best = (sc, i)
            if q >= CLEAN and (best_clean is None or sc > best_clean[0]):
                best_clean = (sc, i)
        return (best_clean or best or (None, None))[1]

    def _window(self, target, mode, side, max_shift, tol=0.25):
        lo, hi = target - max_shift, target + max_shift
        if mode == "expand":
            if side == "start":
                hi = target + tol
            else:
                lo = target - tol
        elif mode == "contract":
            if side == "start":
                lo = target - tol
            else:
                hi = target + tol
        return lo, hi

    def _excluded_for(self, avoid) -> list[tuple]:
        if not avoid:
            return []
        kinds = EXCLUDED_KINDS if avoid is True else tuple(avoid)
        return [r for r in self.excluded if r[2] in kinds]

    def snap(self, start_s: float, end_s: float, mode: str = "nearest", max_shift: float = 4.0,
             pre_roll: float = 0.15, post_roll: float = 0.25, keep_laughter: bool = True,
             widen: bool = True, strict: bool = False, avoid_excluded=False) -> Snap:
        """Snap a rough [start_s, end_s] to clean word-boundary cuts.

        mode: "nearest" (either direction), "expand" (never cut inside the
        requested span), "contract" (never include speech outside it).
        widen: if no clean cut is within max_shift, also search 2x / 4x
        max_shift (a boundary beyond max_shift is flagged ``*_widened`` and
        the snap is not ``clean``). With widen=False the best (possibly
        unclean) cut inside max_shift is returned.
        strict: raise ``SnapError`` unless the result is ``clean``.
        avoid_excluded: True (all kinds) or a tuple of kinds ("teaser",
        "music", "intro"): cuts are never placed inside those ranges and the
        span never covers one.
        """
        if mode not in ("nearest", "expand", "contract"):
            raise ValueError(f"unknown mode {mode!r}")
        if end_s < start_s:
            start_s, end_s = end_s, start_s
        ws = self.tr.words
        notes: list[str] = []
        avoid = self._excluded_for(avoid_excluded)
        blocked = (lambda i: any(r[0] <= i <= r[1] for r in avoid)) if avoid else None
        steps = WIDEN_STEPS if widen else (1,)
        si = ei = None
        for mult in steps:
            lo, hi = self._window(start_s, mode, "start", max_shift * mult)
            si = self._pick(self.sq, self.starts, start_s, lo, hi, blocked)
            if si is not None and self.sq[si] >= CLEAN:
                break
        for mult in steps:
            lo, hi = self._window(end_s, mode, "end", max_shift * mult)
            ei = self._pick(self.eq, self.ends, end_s, lo, hi, blocked)
            if ei is not None and self.eq[ei] >= CLEAN:
                break
        fallback = False
        if si is None:  # nothing at all nearby (e.g. silence): nearest usable word
            cands = [i for i in range(len(ws)) if not self.bc[i] and not (blocked and blocked(i))]
            cands = cands or [i for i in range(len(ws)) if not self.bc[i]] or list(range(len(ws)))
            si = min(cands, key=lambda i: abs(ws[i].start - start_s))
            notes.append("start: no candidate in window; took the nearest word")
            fallback = True
        for r in sorted(avoid, key=lambda r: r[0]):  # never start before / inside an avoided range it would cover
            covers = (r[0] <= si <= r[1]) if ei is None else (si <= r[1] and ei >= r[0])
            if covers:
                nxt = next((i for i in range(r[1] + 1, len(ws)) if self.sq[i] != NEG), None)
                if nxt is not None:
                    si = nxt
                    notes.append(f"start moved past excluded {r[2]} range")
        if ei is None or ei < si:
            cands = [i for i in range(si, len(ws)) if not self.bc[i] and not (blocked and blocked(i))]
            ei = min(cands, key=lambda i: abs(ws[i].end - end_s)) if cands else si
            notes.append("end: no candidate after start; took the nearest word")
            fallback = True
        for r in avoid:
            if si < r[0] <= ei:
                ei = max((i for i in range(si, r[0]) if not self.bc[i]), default=si)
                notes.append(f"end moved before excluded {r[2]} range")
        start_widened = abs(ws[si].start - start_s) > max_shift + 1e-6
        end_widened = abs(ws[ei].end - end_s) > max_shift + 1e-6
        if start_widened:
            notes.append(f"start moved {ws[si].start - start_s:+.1f}s, beyond max_shift {max_shift:g}s")
        if end_widened:
            notes.append(f"end moved {ws[ei].end - end_s:+.1f}s, beyond max_shift {max_shift:g}s")
        if self.sq[si] < CLEAN:
            notes.append("start is not at a clean pause")
        if self.eq[ei] < CLEAN:
            notes.append("end is not at a clean pause")
        crossed = [r for r in self.excluded if r[0] <= ei and r[1] >= si]
        for r in crossed:
            notes.append(f"span overlaps excluded {r[2]} range (words {r[0]}-{r[1]})")
        prev_end = ws[si - 1].end if si > 0 else 0.0
        cut_in = min(ws[si].start, max(ws[si].start - pre_roll, prev_end + 0.02, 0.0))
        next_start = ws[ei + 1].start if ei + 1 < len(ws) else max(self.tr.duration, ws[ei].end)
        cut_out = max(ws[ei].end, min(ws[ei].end + post_roll, next_start - 0.02))
        if keep_laughter:
            for e in self.tr.events:
                if e.kind == "laughter" and ws[ei].end - 0.2 <= e.start <= ws[ei].end + 0.8:
                    ext = min(e.end + 0.2, next_start - 0.02, ws[ei].end + 3.0)
                    if ext > cut_out:
                        cut_out = ext
                        notes.append("kept trailing laughter")
                    break
        clean = (self.sq[si] >= CLEAN and self.eq[ei] >= CLEAN and not fallback
                 and not start_widened and not end_widened)
        snap = Snap(si, ei, ws[si].start, ws[ei].end, cut_in, cut_out, ws[si].start - start_s,
                    ws[ei].end - end_s, self.sq[si], self.eq[ei], clean, notes,
                    start_widened, end_widened, fallback, bool(crossed), [r[3] for r in crossed])
        if strict and not clean:
            raise SnapError(f"no clean cut for [{start_s:.2f}, {end_s:.2f}] within {max_shift:g}s: "
                            + "; ".join(notes))
        return snap

    def snap_words(self, i0: int, i1: int, **kw) -> Snap:
        ws = self.tr.words
        return self.snap(ws[i0].start, ws[i1].end, **kw)
