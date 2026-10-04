"""Transcript -> turns, thought units, Q->A links, signals, clean cut points.

Public API::

    from shotvi.segment import segment_file
    seg = segment_file("scribe_dataset/<id>/<id>_scribe_transcript.json")
    seg.units, seg.turns, seg.links, seg.roles, seg.host
    seg.snap(start_s, end_s, mode="nearest"|"expand"|"contract")  -> Snap
    seg.snap_words(i0, i1)                                         -> Snap
    seg.render(legend=True, include_intro=False)                   -> str
    seg.render_chunks(max_tokens=12000, overlap_tokens=1500)       -> list[Chunk]
    seg.to_dict()                                                  -> JSON-able dict

Opening teaser / music / intro (``intro.py`` rules, ``opening_llm.py`` one LLM call)::

    segment_file(path, detect_intro="llm"|"rules"|"off")   default: $SHOTVI_OPENING_MODE or "llm";
                                          "llm" falls back to "rules" on any failure (no key, cap, bad answer)
    seg.opening                           {mode, decided_by, content_start_word, confidence, reason,
                                           fallback_reason, llm, rules_ranges}

    seg.intro_ranges                      -> list[IntroRange] (kind teaser|music|intro)
    seg.excluded_word_ranges(kinds=...)   -> [(i0, i1), ...] inclusive word ranges
    seg.overlaps_excluded(i0, i1)         -> bool
    seg.intro_of_word(i)                  -> IntroRange | None
    seg.teaser_body_copy(i0, i1)          -> [TeaserFragment] (where teaser words recur in the body)
    unit.in_intro / unit.intro_kind / unit.intro_id

All word indices are inclusive and index the input's ``word_timestamps``.
"""
from .intro import HIDDEN_KINDS, KINDS as INTRO_KINDS, IntroRange, TeaserFragment
from .loader import Transcript, TranscriptError, Word, from_dict, load
from .pipeline import SCHEMA_VERSION, Segmentation, segment, segment_dict, segment_file
from .render import LEGEND, estimate_tokens
from .snap import Snap, SnapError
from .units import UnitParams

__all__ = [
    "HIDDEN_KINDS", "INTRO_KINDS", "IntroRange", "SCHEMA_VERSION", "LEGEND", "TeaserFragment", "Segmentation", "Snap", "SnapError", "Transcript", "TranscriptError", "UnitParams",
    "Word", "estimate_tokens", "from_dict", "load", "segment", "segment_dict", "segment_file",
]
