"""Stage inputs for one episode, loaded from the existing layers (never modified):

  * Scribe transcript            scribe_dataset/<id>/<id>_scribe_transcript.json
  * ASR corrections (optional)   runs/asr_fix/<id>/corrections.json      -> corrected line text (flagged if missing)
  * speaker correction (REQUIRED) runs/speaker_fix/<id>.json             -> key / name / confidence per line
  * YouTube metadata (optional)  scribe_dataset/<id>/youtube_meta.json

Lines are ``shotvi.select.lines.build_lines`` rows (Scribe sentences + audio events in time order, ids L0001...),
the numbering the reference files use, with ``sentence_id`` and the speaker layer attached.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from shotvi.segment import asr_fix as _asr
from shotvi.select.lines import build_lines

from .paths import EpisodePaths, load_json


class MissingInputError(RuntimeError):
    """A required input layer is missing; the message says which file and how to make it."""


@dataclass
class EpisodeInputs:
    vid: str
    paths: EpisodePaths
    transcript: dict
    lines: list[dict]                 # build_lines rows + sentence_id, text_scribe, speaker_key/name/confidence
    speaker_fix: dict
    corrections: dict | None
    meta: dict
    layers: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def words(self) -> list[dict]:
        return self.transcript["word_timestamps"]

    @property
    def duration(self) -> float:
        d = self.transcript.get("audio_duration_secs")
        if not d and self.words:
            d = self.words[-1]["end"]
        return float(d or 0.0)


def load_inputs(vid: str, cfg: dict) -> EpisodeInputs:
    p = EpisodePaths(vid, cfg)
    if not p.transcript.exists():
        raise MissingInputError(f"{vid}: no Scribe transcript at {p.transcript}")
    if not p.speakers.exists():
        raise MissingInputError(
            f"{vid}: the speaker correction layer {p.speakers} does not exist. Stage 1A always reads it. "
            f"Run it first: 'Run speaker fix' in the sidebar, or  python -m shotvi.segment.speaker_fix {vid}")
    tr = load_json(p.transcript)
    fix = load_json(p.speakers)
    n_sent = len(tr.get("sentences") or [])
    n_fix = int((fix.get("coverage") or {}).get("n_lines") or len(fix.get("lines") or []))
    if n_fix != n_sent:
        raise MissingInputError(f"{vid}: speaker_fix covers {n_fix} lines but the transcript has {n_sent} "
                                f"sentences; the layer was built from another transcript. Re-run speaker_fix.")
    warnings: list[str] = []
    corr = None
    if p.corrections.exists():
        corr = _asr.load_corrections(p.corrections)
        stale = _asr.stale_corrections(tr["word_timestamps"], corr)
        if stale:
            warnings.append(f"{len(stale)} ASR corrections no longer match the transcript and were skipped")
    else:
        warnings.append(f"no ASR correction layer at {p.corrections}: line text is raw Scribe (flagged in layers)")
    meta = load_json(p.meta) if p.meta.exists() else {}

    rows = build_lines(vid, asr_fix=False, transcript=tr)
    n_corrected = attach_layers(vid, rows, tr, fix, corr)
    layers = {
        "transcript": str(p.transcript.relative_to(p.src.parent.parent)),
        "asr_fix": corr is not None, "asr_fix_lines_corrected": n_corrected,
        "speaker_fix": True, "speaker_fix_schema": fix.get("schema"), "speaker_fix_prompt": fix.get("prompt"),
        "youtube_meta": bool(meta),
    }
    return EpisodeInputs(vid, p, tr, rows, fix, corr, meta, layers, warnings)


def attach_layers(vid: str, rows: list[dict], tr: dict, fix: dict, corr: dict | None) -> int:
    """Attach ``sentence_id``, corrected ``text`` (``text_scribe`` keeps Scribe's) and the speaker_fix key / name /
    confidence to ``build_lines`` rows, in place. Returns the number of lines whose text was corrected."""
    by_key = {}
    for k, s in enumerate(tr["sentences"]):
        by_key[(s["start"], s["end"], tuple(s.get("words") or ()))] = k
    fix_lines = {int(x["id"]): x for x in fix.get("lines") or []}
    n_corrected = 0
    for r in rows:
        r["text_scribe"] = r["text"]
        if r["kind"] != "speech":
            r["sentence_id"] = None
            continue
        sid = by_key.get((r["start"], r["end"], tuple(r.get("words") or ())))
        if sid is None:
            raise MissingInputError(f"{vid}: line {r['id']} could not be matched to a Scribe sentence")
        r["sentence_id"] = sid
        if corr is not None and r.get("words"):
            t = _asr.display_text(tr["word_timestamps"], corr, r["words"][0], r["words"][1])
            if t and t != r["text"]:
                r["text"] = t
                n_corrected += 1
        f = fix_lines.get(sid)
        key = (f or {}).get("key")
        if f is None or not key or key == "X":
            r["speaker_key"], r["speaker_name"], r["speaker_confidence"] = None, None, 0.0
        else:
            r["speaker_key"], r["speaker_name"] = key, f.get("name")
            r["speaker_confidence"] = float(f.get("confidence") or 0.0)
    return n_corrected
