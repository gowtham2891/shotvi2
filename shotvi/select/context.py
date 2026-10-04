"""Per-episode context for selection: words, natural boundaries, backchannels, events, excluded ranges."""
from __future__ import annotations

import bisect
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from shotvi.segment import Segmentation, segment_file
from shotvi.segment import lexicon as L
from shotvi.segment.text import ends_sentence, norm

# Words a clip start may skip over (spec T4: "only fillers or conjunctions were dropped").
START_SKIPPABLE = set(L.FILLERS) | {
    "అంటే", "సో", "మరి", "అయితే", "కానీ", "ఇంక", "ఇక", "ఇంకా", "కాబట్టి", "and", "so", "but", "then",
    "actually", "basically", "okay", "ok", "like", "now", "అండి", "sir", "సార్",
}

# Referent-dependent words that should not open a clip unresolved (spec C2 code flag).
DEMONSTRATIVES = {
    "అది", "ఇది", "అవి", "ఇవి", "ఆయన", "ఈయన", "ఆవిడ", "ఈవిడ", "ఆమె", "ఈమె", "వాడు", "వీడు", "వాళ్ళు",
    "వీళ్ళు", "అతను", "ఇతను", "అప్పుడు", "అక్కడ", "అదే", "ఇదే", "దాని", "దానికి", "దాన్ని", "ఆయనకి",
    "ఆయన్ని", "వాళ్ళకి", "that", "this", "he", "she", "they", "them", "him", "her", "it", "those", "these",
}


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Ctx:
    path: Path
    seg: Segmentation
    cfg: dict
    excluded: set = field(default_factory=set)            # word indices never used (teaser copies, ...)
    excluded_ranges: list = field(default_factory=list)   # [(i0, i1, reason)]

    def __post_init__(self):
        tr = self.seg.transcript
        self.words = tr.words
        self.n = len(self.words)
        self.video_id = tr.video_id or self.path.stem.replace("_scribe_transcript", "")
        g = self.cfg["guardrails"]
        ws = self.words
        nat = [False] * self.n
        for i in range(self.n):
            if i == self.n - 1:
                nat[i] = True
                continue
            a, b = ws[i], ws[i + 1]
            nat[i] = ends_sentence(a.text) or a.speaker != b.speaker or (b.start - a.end) >= g["natural_gap_s"]
        self.natural_after = nat
        self.bc_kind: dict[int, str] = {}
        for t in self.seg.turns:
            for b in t.backchannels:
                for i in range(b.i0, b.i1 + 1):
                    self.bc_kind[i] = b.kind
        ev = tr.events
        self.censored = sorted([(e.start, e.end) for e in ev if e.kind == "censored"])
        self.music = sorted([(e.start, e.end) for e in ev if e.kind == "music"])
        self.laughter = sorted([(e.start, e.end) for e in ev if e.kind == "laughter"])
        self.starts = [w.start for w in ws]
        self.labels = {s: info.label for s, info in self.seg.roles.items()}

    # ------------------------------------------------------------ boundaries
    def start_ok(self, i: int) -> bool:
        return i == 0 or self.natural_after[i - 1]

    def end_ok(self, i: int) -> bool:
        return self.natural_after[i]

    def lead_in(self, s: int, cap: int = 50) -> int:
        """Spec v0.2 section 0: words from the previous natural boundary up to s-1."""
        k = 0
        while s - k > 0 and not self.natural_after[s - k - 1] and k < cap:
            k += 1
        return k

    def tail(self, e: int, cap: int = 50) -> int:
        """Words from e+1 up to the next natural boundary."""
        k = 0
        while e + k < self.n - 1 and not self.natural_after[e + k] and k < cap:
            k += 1
        return k

    def label(self, i: int) -> str:
        return self.labels.get(self.words[i].speaker, "O")

    def is_bc(self, i: int) -> bool:
        return i in self.bc_kind

    def time_idx(self, t: float) -> int:
        """First word starting at or after t."""
        return min(bisect.bisect_left(self.starts, t), self.n - 1)

    def text(self, i0: int, i1: int) -> str:
        return " ".join(self.words[i].text for i in range(i0, i1 + 1))

    @staticmethod
    def overlaps(ranges, t0, t1, pad=0.0):
        return any(s < t1 + pad and e > t0 - pad for s, e in ranges)


def load_ctx(path: str | Path, cfg: dict) -> Ctx:
    from .exclude import excluded_ranges
    path = Path(path)
    # segment_opening_mode: None = shotvi.segment default ($SHOTVI_OPENING_MODE or "llm"); "rules" reproduces the
    # exclusions every cached run up to sel_r6 was built on (keeps discover/tighten/joins cache hits)
    mode = cfg.get("segment_opening_mode")
    seg = segment_file(path, detect_intro=mode) if mode else segment_file(path)
    ctx = Ctx(path, seg, cfg)
    ctx.excluded_ranges = excluded_ranges(ctx)
    for i0, i1, _ in ctx.excluded_ranges:
        ctx.excluded.update(range(i0, i1 + 1))
    return ctx
