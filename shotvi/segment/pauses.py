"""Per-video pause distribution and the relative thresholds derived from it.

Thresholds are percentiles of *this* video's within-speaker inter-word gaps,
clamped to generic sanity bounds (so a transcript with almost no pauses, or a
very slow speaker, still gets sensible values). Nothing is tuned per video.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .loader import Transcript


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


@dataclass
class PauseStats:
    n_gaps: int
    p50: float
    p75: float
    p90: float
    p95: float
    p97: float
    p99: float
    minor: float   # clause-level pause: candidate cut point
    major: float   # thought-level pause: strong boundary
    long: float    # very long pause: never merge across when joining short units

    def to_dict(self):
        return {k: round(v, 3) if isinstance(v, float) else v for k, v in asdict(self).items()}


def pause_stats(tr: Transcript) -> PauseStats:
    ws = tr.words
    gaps = [max(0.0, ws[i + 1].start - ws[i].end)
            for i in range(len(ws) - 1) if ws[i].speaker == ws[i + 1].speaker]
    p = {q: percentile(gaps, q) for q in (50, 75, 90, 95, 97, 99)}
    minor = _clamp(p[90], 0.25, 0.8)
    major = _clamp(p[97], max(0.6, minor + 0.2), 2.0)
    long_ = _clamp(p[99] * 2.0, max(1.5, major + 0.5), 5.0)
    return PauseStats(len(gaps), p[50], p[75], p[90], p[95], p[97], p[99], minor, major, long_)
