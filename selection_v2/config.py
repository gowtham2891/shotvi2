"""Configuration for selection_v2: ``config/pipeline.yaml`` (models, rates, thresholds, tag classes) and
``data/series_cast/cast_lists.yaml`` (cast per series). Nothing numeric lives in the stage modules."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = Path(__file__).resolve().parent / "config"
PIPELINE_YAML = CONFIG_DIR / "pipeline.yaml"
CAST_YAML = ROOT / "data" / "series_cast" / "cast_lists.yaml"   # per-series data, not config (2026-10-29)


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        d = yaml.safe_load(f)
    if not isinstance(d, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return d


def load_config(path: Path | None = None) -> dict:
    """``config/pipeline.yaml``, or the file named by env ``SELECTION_V2_CONFIG`` (tests point it at a copy whose
    ``paths`` go to a scratch directory)."""
    path = Path(path or os.environ.get("SELECTION_V2_CONFIG") or PIPELINE_YAML)
    cfg = _read_yaml(path)
    cfg["_path"] = str(path)
    cfg["_sha8"] = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
    return cfg


def load_cast_lists(path: Path = CAST_YAML) -> dict:
    return _read_yaml(path)


def inr_per_usd(cfg: dict) -> float:
    """The rupee rate: the env var named in the config (the one shotvi.llm.spend reads from .env), else the default."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    r = cfg["rates"]
    v = os.environ.get(r["inr_per_usd_env"])
    return float(v) if v else float(r["inr_per_usd_default"])


def usd_cost(cfg: dict, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
    r = cfg["rates"]
    cached = min(cached_tokens, input_tokens)
    return ((input_tokens - cached) * r["usd_per_1m_input"] + cached * r["usd_per_1m_cached_input"]
            + output_tokens * r["usd_per_1m_output"]) / 1e6


def inr_cost(cfg: dict, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
    return usd_cost(cfg, input_tokens, output_tokens, cached_tokens) * inr_per_usd(cfg)


def series_for(video_id: str, title: str, cast_cfg: dict) -> tuple[str | None, dict]:
    """(series key, series entry) matched by video id, then by title regex; (None, {}) when nothing matches."""
    series = cast_cfg.get("series") or {}
    for key, s in series.items():
        if video_id in ((s.get("match") or {}).get("video_ids") or []):
            return key, s
    for key, s in series.items():
        rx = (s.get("match") or {}).get("title_regex")
        if rx and title and re.search(rx, title, re.I):
            return key, s
    return None, {}


def loops_on(facts: dict, cfg: dict) -> bool:
    """Stage 6 loops for this episode's content type (config stage6.loops_by_content_type; founder, 2026-10-29: off by
    default for scripted series). Stage 3 skips critic trims of loops, Stage 5 skips loop families, Stage 6 ships none."""
    by = (cfg.get("stage6") or {}).get("loops_by_content_type") or {}
    ct = ((facts.get("episode_pass") or {}).get("content_type")) or "default"
    return bool(by.get(ct, by.get("default", True)))
