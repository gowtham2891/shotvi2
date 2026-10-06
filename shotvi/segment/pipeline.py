"""End-to-end segmentation of one transcript and its JSON output."""
from __future__ import annotations

import bisect
import os
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from .asr_fix import ASR_LEGEND, corrections_path, load_corrections, stale_corrections, word_marks
from .intro import HIDDEN_KINDS, KINDS, IntroRange, TeaserFragment, cuts_of, detect_intros, detect_teasers, finalize
from .loader import Transcript, from_dict, load
from .opening_llm import OpeningLLM, OpeningLLMError, find_all_fragments, ranges_from_answer
from .pauses import PauseStats, pause_stats, percentile
from .qa import QALink, build_links
from .render import INTRO_TAG, LEGEND, Chunk, RenderedUnit, chunk_rendered, estimate_tokens, fmt_ts, render_units
from .roles import SpeakerInfo, infer_roles
from .signals import add_signals
from .snap import Snap, Snapper
from .turns import Turn, build_turns
from .units import Unit, UnitParams, build_units

SCHEMA_VERSION = "shotvi.segment/1"


@dataclass
class Segmentation:
    transcript: Transcript
    pauses: PauseStats
    turns: list[Turn]
    units: list[Unit]
    roles: dict[str, SpeakerInfo]
    host: str | None
    host_confidence: float
    links: list[QALink]
    video_signals: dict
    rendered: list[RenderedUnit]
    params: UnitParams
    intro_ranges: list[IntroRange] = field(default_factory=list)
    opening: dict = field(default_factory=dict)   # how the opening was decided (mode, LLM answer, fallback)
    asr_fix: dict | None = None   # opt-in ASR layer used for rendering: counts of shown corrections / (?) marks
    _snapper: Snapper | None = field(default=None, repr=False)
    _unit_starts: list[int] = field(default_factory=list, repr=False)

    # ---------------------------------------------------------------- lookup
    def unit_of_word(self, i: int) -> Unit:
        k = bisect.bisect_right(self._unit_starts, i) - 1
        return self.units[max(k, 0)]

    def units_in(self, t0: float, t1: float) -> list[Unit]:
        return [u for u in self.units if u.end > t0 and u.start < t1]

    def text(self, i0: int, i1: int, drop_backchannels: bool = True) -> str:
        """Plain text of an inclusive word range, optionally without listener backchannels."""
        ws = self.transcript.words
        if not drop_backchannels:
            return self.transcript.text(i0, i1)
        bc = set()
        for u in range(self.unit_of_word(i0).id, self.unit_of_word(i1).id + 1):
            for b in self.units[u].backchannels:
                bc.update(range(b.i0, b.i1 + 1))
        return " ".join(ws[i].text for i in range(i0, i1 + 1) if i not in bc)

    def role(self, speaker: str) -> str:
        return self.roles[speaker].role if speaker in self.roles else "other"

    # ------------------------------------------------------------- snapping
    def snap(self, start_s: float, end_s: float, **kw) -> Snap:
        return self._snapper.snap(start_s, end_s, **kw)

    def snap_words(self, i0: int, i1: int, **kw) -> Snap:
        return self._snapper.snap_words(i0, i1, **kw)

    # -------------------------------------------------------- opening ranges
    def excluded_word_ranges(self, kinds=KINDS) -> list[tuple[int, int]]:
        """Inclusive word ranges clip selection should not take moments from:
        opening teaser montage, opening music, greeting / host intro."""
        return [(r.i0, r.i1) for r in self.intro_ranges if r.kind in kinds]

    def intro_of_word(self, i: int) -> IntroRange | None:
        return next((r for r in self.intro_ranges if r.i0 <= i <= r.i1), None)

    def overlaps_excluded(self, i0: int, i1: int, kinds=KINDS) -> bool:
        return any(r.i0 <= i1 and r.i1 >= i0 for r in self.intro_ranges if r.kind in kinds)

    def teaser_body_copy(self, i0: int, i1: int) -> list[TeaserFragment]:
        """Teaser fragments overlapping words [i0, i1], each with the body
        location (``body_i0..body_i1``) where the same words are said in context."""
        return [f for r in self.intro_ranges if r.kind == "teaser" for f in r.fragments
                if f.i0 <= i1 and f.i1 >= i0]

    def _hidden_blocks(self, include_intro: bool) -> list[tuple[list[int], IntroRange]]:
        if include_intro:
            return []
        out = []
        for r in self.intro_ranges:
            if r.kind in HIDDEN_KINDS:
                ids = [u.id for u in self.units if u.intro_id == r.id]
                if ids:
                    out.append((ids, r))
        return out

    @staticmethod
    def _omit_note(ids: list[int], r: IntroRange) -> str:
        what = ("opening teaser montage: excerpts of moments that are said again later in the episode; "
                "take clips from the later, full version") if r.kind == "teaser" else "opening music"
        return f"(U{ids[0]}-U{ids[-1]} [{fmt_ts(r.start)}-{fmt_ts(r.end)}] omitted: {what})"

    # ------------------------------------------------------------ rendering
    def render(self, legend: bool = False, include_intro: bool = False) -> str:
        """Numbered unit lines for an LLM. Units inside an opening teaser or
        opening music are replaced by one note line unless ``include_intro``;
        greeting / host-intro units stay, tagged ``(intro)``."""
        blocks = self._hidden_blocks(include_intro)
        first_of = {ids[0]: (ids, r) for ids, r in blocks}
        hidden = {i for ids, _ in blocks for i in ids}
        lines = []
        for r in self.rendered:
            if r.unit_id in first_of:
                lines.append(self._omit_note(*first_of[r.unit_id]))
            if r.unit_id not in hidden:
                lines.append(r.line)
        body = "\n".join(lines)
        if legend:
            leg = LEGEND
            if any(r.kind == "intro" for r in self.intro_ranges):
                leg += f" {INTRO_TAG} marks greeting / episode-introduction speech (not clip material)."
            if self.asr_fix is not None:
                leg += " " + ASR_LEGEND
            return leg + "\n\n" + body
        return body

    def render_chunks(self, max_tokens: int = 12000, overlap_tokens: int = 1500,
                      include_intro: bool = False) -> list[Chunk]:
        """Overlapping chunks of ``render()`` lines. Hidden opening units are
        dropped; the omission note goes at the top of the chunk that continues
        after them."""
        blocks = self._hidden_blocks(include_intro)
        hidden = {i for ids, _ in blocks for i in ids}
        keep = [k for k, r in enumerate(self.rendered) if r.unit_id not in hidden]
        if not keep:
            return []
        chunks = chunk_rendered([self.rendered[k] for k in keep], [self.units[k] for k in keep],
                                max_tokens, overlap_tokens)
        for ids, r in blocks:
            c = next((c for c in chunks if c.last_unit > ids[-1]), chunks[-1])
            note = self._omit_note(ids, r)
            c.text = note + "\n" + c.text
            c.est_tokens += estimate_tokens(note) + 1
        return chunks

    # --------------------------------------------------------------- output
    def stats(self, chunk_tokens: int = 12000, overlap_tokens: int = 1500) -> dict:
        durs = [u.dur for u in self.units]
        n_bc = sum(len(t.backchannels) for t in self.turns)
        by_kind: dict[str, int] = {}
        for t in self.turns:
            for b in t.backchannels:
                by_kind[b.kind] = by_kind.get(b.kind, 0) + 1
        chunks = self.render_chunks(chunk_tokens, overlap_tokens)
        dist = {f"p{p}": round(percentile(durs, p), 1) for p in (5, 10, 25, 50, 75, 90, 95)}
        return {
            "n_words": len(self.transcript.words),
            "duration_s": round(self.transcript.duration, 1),
            "n_turns": len(self.turns),
            "n_backchannels_absorbed": n_bc,
            "backchannels_by_kind": by_kind,
            "n_units": len(self.units),
            "unit_duration": {"min": round(min(durs), 1), **dist, "max": round(max(durs), 1),
                              "mean": round(statistics.fmean(durs), 1)},
            "units_lt_5s": sum(d < 5 for d in durs),
            "units_5_40s": sum(5 <= d <= 40 for d in durs),
            "units_gt_40s": sum(d > 40 for d in durs),
            "units_exchange": sum(u.kind == "exchange" for u in self.units),
            "n_qa_links": len(self.links),
            "qa_links_by_kind": {k: sum(l.kind == k for l in self.links) for k in ("question", "prompt")},
            "qa_links_host_asked": sum(l.asker == self.host for l in self.links) if self.host else 0,
            "qa_answer_duration": ({f"p{p}": round(percentile([l.a_end - l.a_start for l in self.links], p), 1)
                                    for p in (25, 50, 75, 90)} if self.links else {}),
            "rendered_tokens_est": sum(r.tokens for r in self.rendered
                                       if self.units[r.unit_id].intro_kind not in HIDDEN_KINDS),
            "rendered_tokens_hidden_est": sum(r.tokens for r in self.rendered
                                              if self.units[r.unit_id].intro_kind in HIDDEN_KINDS),
            "intro_ranges": [{"kind": r.kind, "start": round(r.start, 1), "end": round(r.end, 1),
                              "words": [r.i0, r.i1], "confidence": r.confidence} for r in self.intro_ranges],
            "units_in_intro": {k: sum(u.intro_kind == k for u in self.units) for k in KINDS},
            "opening_decided_by": self.opening.get("decided_by"),
            "n_chunks": len(chunks),
            "chunk_tokens_est": [c.est_tokens for c in chunks],
            "host": self.host,
            "host_confidence": self.host_confidence,
        }

    def _turn_intro_kind(self, t: Turn) -> str | None:
        r = self.intro_of_word(t.i0)
        return r.kind if r is not None and r.i1 >= t.i1 else None

    def to_dict(self, chunk_tokens: int = 12000, overlap_tokens: int = 1500) -> dict:
        tr = self.transcript
        roles = self.roles
        line_by_id = {r.unit_id: r for r in self.rendered}
        link_by_unit_q: dict[int, list[int]] = {}
        link_by_unit_a: dict[int, list[int]] = {}
        for l in self.links:
            for u in l.q_units:
                link_by_unit_q.setdefault(u, []).append(l.id)
            for u in l.a_units:
                link_by_unit_a.setdefault(u, []).append(l.id)
        return {
            "schema_version": SCHEMA_VERSION,
            "source": {"video_id": tr.video_id, "path": tr.source_path, "asr_model": tr.asr_model,
                       "language": tr.language, "duration_s": round(tr.duration, 3),
                       "n_words": len(tr.words), "has_logprob": tr.has_logprob,
                       "warnings": tr.warnings},
            "params": {"units": self.params.__dict__, "pauses": self.pauses.to_dict()},
            "speakers": [roles[s].to_dict() for s in sorted(roles, key=lambda s: -roles[s].talk_s)],
            "host": {"speaker": self.host, "confidence": self.host_confidence},
            "video_signals": self.video_signals,
            "intro_ranges": [r.to_dict() for r in self.intro_ranges],
            "opening": self.opening,
            **({"asr_fix": self.asr_fix} if self.asr_fix is not None else {}),
            "events": [{"kind": e.kind, "text": e.text, "start": round(e.start, 3), "end": round(e.end, 3)}
                       for e in tr.events],
            "turns": [{
                "id": t.id, "speaker": t.speaker, "role": self.role(t.speaker),
                "words": [t.i0, t.i1], "start": round(t.start, 3), "end": round(t.end, 3),
                "n_words": len(t.main), "is_question": t.is_question,
                "ends_with_question": t.ends_with_question,
                "backchannels": [b.to_dict() for b in t.backchannels],
                "intro_kind": self._turn_intro_kind(t),
            } for t in self.turns],
            "units": [{
                "id": u.id, "words": [u.i0, u.i1], "start": round(u.start, 3), "end": round(u.end, 3),
                "duration": round(u.dur, 2), "kind": u.kind,
                "speaker": u.speaker, "role": self.role(u.speaker), "speakers": u.speaker_share,
                "turn_ids": u.turn_ids, "boundary_before": u.boundary_before,
                "backchannel_words": [[b.i0, b.i1] for b in u.backchannels],
                "qa_question_of": link_by_unit_q.get(u.id, []),
                "qa_answer_of": link_by_unit_a.get(u.id, []),
                "signals": u.signals,
                "in_intro": u.in_intro, "intro_kind": u.intro_kind, "intro_id": u.intro_id,
                "text": _strip_tag(line_by_id[u.id].line.split("] ", 1)[1]) if u.id in line_by_id else "",
                "est_tokens": line_by_id[u.id].tokens if u.id in line_by_id else 0,
            } for u in self.units],
            "qa_links": [l.to_dict(roles) for l in self.links],
            "chunks": [c.to_dict(with_text=False) for c in self.render_chunks(chunk_tokens, overlap_tokens)],
            "stats": self.stats(chunk_tokens, overlap_tokens),
        }


def _strip_tag(text: str) -> str:
    return text[len(INTRO_TAG) + 1:] if text.startswith(INTRO_TAG + " ") else text


OPENING_MODES = ("llm", "rules", "off")


def opening_mode(detect_intro=None) -> str:
    """None / True -> $SHOTVI_OPENING_MODE or "llm"; False -> "off"; else one of OPENING_MODES."""
    if detect_intro is None or detect_intro is True:
        mode = os.environ.get("SHOTVI_OPENING_MODE", "llm").strip().lower()
    elif detect_intro is False:
        mode = "off"
    else:
        mode = str(detect_intro).lower()
    if mode not in OPENING_MODES:
        raise ValueError(f"detect_intro must be one of {OPENING_MODES}, got {detect_intro!r}")
    return mode


def _asr_marks(tr: Transcript, asr_fix) -> tuple[dict | None, dict | None]:
    """(word marks, summary) for the opt-in ASR layer; (None, None) when off or the layer is missing."""
    if not asr_fix:
        return None, None
    if isinstance(asr_fix, dict):
        corr = asr_fix
    elif tr.video_id and corrections_path(tr.video_id).exists():
        corr = load_corrections(tr.video_id)
    else:
        tr.warnings.append(f"asr_fix requested but no corrections for {tr.video_id!r}; rendering Scribe's text")
        return None, None
    stale = stale_corrections(tr.words, corr)
    if stale:
        tr.warnings.append(f"asr_fix: {len(stale)} corrections no longer match the transcript; skipped")
    marks = word_marks(tr.words, corr)
    return marks, {"corrections_shown": len({id(m) for m in marks.values() if m["kind"] == "fixed"}),
                   "low_conf_marked": sum(m["kind"] == "unsure" for m in marks.values()),
                   "stale_skipped": len(stale)}


def segment(tr: Transcript, params: UnitParams | None = None, detect_intro=None,
            opening_llm=None, asr_fix: bool | dict = False) -> Segmentation:
    """detect_intro: "llm" (default; one cached LLM call per long episode, falls
    back to "rules" on any failure), "rules" (offline repeat detector), "off".
    ``opening_llm``: a callable like ``OpeningLLM`` (inject a fake in tests).
    ``asr_fix`` (opt-in, default off; True = runs/asr_fix/<video_id>/corrections.json, or the corrections dict):
    rendered unit text (``render``, chunks, unit "text" in ``to_dict``) shows audio-confirmed corrections as
    ``{fixed: ...}`` and leftover low-confidence words as ``word(?)``. Word indices, times, units, links and
    signals do not change; off renders exactly as before."""
    params = params or UnitParams()
    mode = opening_mode(detect_intro)
    ps = pause_stats(tr)
    info: dict = {"mode": mode}
    llm_ranges = None
    opening: list[IntroRange] = []
    if mode != "off":
        opening = detect_teasers(tr, ps)
        for r in opening:
            r.evidence.setdefault("decided_by", "rules")
        info["decided_by"] = "rules"
    if mode == "llm":
        det = opening_llm if opening_llm is not None else OpeningLLM()
        try:
            frs = find_all_fragments(tr)
            ans = det(tr, ps, frs)
            llm_ranges = ranges_from_answer(tr, ans, opening, frs)
            info.update(decided_by="llm", content_start_word=ans["content_start_word"],
                        confidence=ans["confidence"], reason=ans["reason"],
                        llm={k: (list(v) if isinstance(v, tuple) else v) for k, v in ans.items()},
                        rules_ranges=[{"kind": r.kind, "words": [r.i0, r.i1]} for r in opening])
        except OpeningLLMError as e:
            info["fallback_reason"] = str(e)
    if llm_ranges is not None:
        pre = llm_ranges
        opening = [r for r in llm_ranges if r.kind in HIDDEN_KINDS]
    else:
        pre = opening
    # opening ranges first: turns never span their edges, and teaser / music
    # (out-of-context, often mis-diarized) speech is kept out of roles and Q->A
    turns = build_turns(tr, ps, cuts_of(pre))
    hidden = {t.id for t in turns if any(r.i0 <= t.i0 and t.i1 <= r.i1 for r in opening)}
    roles, host, conf = infer_roles(tr, turns, hidden)
    links = build_links(tr, ps, turns, host, hidden)
    intros: list[IntroRange] = [r for r in pre if r.kind == "intro"]
    if mode != "off" and llm_ranges is None:
        after = max((r.i1 for r in opening), default=-1)
        first_q = next((l.q_i0 for l in links if l.q_i0 > after and l.asker == host), None)
        intros = detect_intros(tr, opening, first_q)
        for r in intros:
            r.evidence.setdefault("decided_by", "rules")
    ranges = finalize(opening + intros)
    if mode != "off" and "content_start_word" not in info:
        info["content_start_word"] = max((r.i1 + 1 for r in ranges), default=0)
    if intros:  # greeting chatter is not a Q->A moment
        links = [l for l in links if not any(r.i0 <= l.q_core_i0 <= r.i1 for r in intros)]
        for k, l in enumerate(links):
            l.id = k
    units = build_units(tr, ps, turns, params, cuts_of(ranges))
    for u in units:
        for r in ranges:
            n_in = sum(1 for i in u.main if r.i0 <= i <= r.i1)
            if n_in * 2 > len(u.main):
                u.intro_kind, u.intro_id = r.kind, r.id
                break
    video = add_signals(tr, ps, units)
    marks, asr_info = _asr_marks(tr, asr_fix)
    rendered = render_units(tr, units, roles, marks)
    seg = Segmentation(tr, ps, turns, units, roles, host, conf, links, video, rendered, params, ranges, info,
                       asr_info)
    seg._unit_starts = [u.i0 for u in units]
    seg._snapper = Snapper(tr, ps, turns, units, excluded=[(r.i0, r.i1, r.kind, r.id) for r in ranges])
    for l in links:
        l.q_units = sorted({seg.unit_of_word(l.q_i0).id, seg.unit_of_word(l.q_i1).id} |
                           {u.id for u in units if u.i0 <= l.q_i1 and u.i1 >= l.q_i0})
        l.a_units = [u.id for u in units if u.i0 <= l.a_i1 and u.i1 >= l.a_i0]
    return seg


def segment_file(path: str | Path, params: UnitParams | None = None, detect_intro=None,
                 opening_llm=None, asr_fix: bool | dict = False) -> Segmentation:
    return segment(load(path), params, detect_intro, opening_llm, asr_fix)


def segment_dict(d: dict, params: UnitParams | None = None, video_id: str | None = None,
                 detect_intro=None, opening_llm=None, asr_fix: bool | dict = False) -> Segmentation:
    return segment(from_dict(d, video_id=video_id), params, detect_intro, opening_llm, asr_fix)
