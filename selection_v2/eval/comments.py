"""Timestamped viewer comments as golden-set sources. STUB: no comment data exists in the repo yet (checked
2026-10-25: nothing under data/, runs/ or scribe_dataset/ holds YouTube comments). When a comments dump arrives
(e.g. yt-dlp ``--write-comments`` -> ``<id>.info.json`` with ``comments[]``), implement ``extract`` to parse
``m:ss`` / ``h:mm:ss`` mentions out of each comment's text and return source entries of kind ``comment``.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..stage1_facts import parse_ts

TS_RE = re.compile(r"(?<![\d:])(\d{1,2}:\d{2}(?::\d{2})?)(?![\d:])")


def extract_timestamps(text: str) -> list[float]:
    """The one piece that is already implementable: every m:ss / h:mm:ss mention in a comment's text."""
    return [parse_ts(m)[0] for m in TS_RE.findall(text or "")]


def extract(vid: str, comments_path: Path | None = None, window_s: float = 20.0) -> list[dict]:
    """Return golden-set sources of kind ``comment`` for ``vid``. Stub: returns [] until comment data exists."""
    if comments_path is None or not Path(comments_path).exists():
        return []
    raise NotImplementedError("comment extraction: a comments file exists but the parser is not written yet "
                              f"({comments_path}); see the module docstring")
