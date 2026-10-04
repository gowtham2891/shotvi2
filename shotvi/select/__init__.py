"""Clip selection: transcript -> ranked, tightened clip proposals (docs/spec/output_schema.md, kind: proposals).

Stages: discover (chunked moment nomination) -> tighten (word-level keep list + boundary snapping) -> score
(rubric on the edited clip, title/why) -> optional rescore (stronger model on the shortlist) -> rank (Q4 dedupe,
Q8 theme links, tiers, cut-off). CLI: ``python -m shotvi.select <transcript.json> --out runs/<run_id>/``.
"""
from .config import PIPELINE_VERSION, load_config
from .pipeline import run_video

__all__ = ["PIPELINE_VERSION", "load_config", "run_video"]
