"""Pipeline configuration. Every number that the spec marks HYP (or that product-lead is revising) lives here.

A run config is a JSON file (see ``configs/``) deep-merged over ``DEFAULTS``. The merged config is written into
the run file (``pipeline.config``) so each run records what it actually used.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

PIPELINE_VERSION = "select/0.1"

_GEMINI_FLASH = {"provider": "gemini", "model": "gemini-3.8-flash", "params": {"thinking_level": "low"}}

DEFAULTS: dict = {
    "name": "default",
    "models": {
        # role -> ModelSpec dict. "rescore" is optional: a stronger model re-scores the shortlist.
        "candidate_gen": _GEMINI_FLASH,
        "tightener": {**_GEMINI_FLASH, "params": {"thinking_level": "medium"}},
        "scorer": _GEMINI_FLASH,
        "rescore": None,
        "dedupe": _GEMINI_FLASH,
        "writer": _GEMINI_FLASH,     # title / why / category / risks (unused when merged_write)
        "brief": None,               # episode brief (round 6): off by default, it only fed the ranker
        "ranker": None,              # list-wise ranker (round 6) made ranking worse vs founder ratings: off
        "joiner": _GEMINI_FLASH,     # join checker / word-level join repair
        "ender": _GEMINI_FLASH,      # payoff editor: end on the verdict / result
        "interest": _GEMINI_FLASH,   # audience interest from the editor's notes (one call per episode)
        "opener": _GEMINI_FLASH,     # auto cold open / earlier start when the judge flags the opener
    },
    "segment_opening_mode": None,    # None = segment default; "rules" = the opening detector cached runs used
    "score_caps": True,
    "merged_write": False,
    "guest_profile": False,          # guest/editor context gave no gain in clip selection (rounds 7–10)
    "editor_notes": False,           # runs/editor_notes/<id>.json: writer, second discovery pass, interest
    "extra_discovery": {"enabled": False, "cap": 8},
    "order": "score",                # "ranker" (rank v1 order) or "score_interest" (score order, interest tie-breaks)           # use runs/guest/<id>.json in brief + writer when it exists            # score v9 writes title/why/category/risks; no separate writer call              # apply score.calibrated_scores caps from the judge's named problems
    "discovery": {
        "chunk_tokens": 6000,        # real (provider) tokens per discovery chunk
        "overlap_tokens": 900,
        "est_to_real": 0.71,         # segment.render estimate -> Gemini countTokens (calibrated 2026-09-27)
        "min_strength": 3,           # moments below this are not edited (cost bound)
        "merge_overlap": 0.5,        # merge nominations sharing > this share of the smaller unit span
    },
    "edit": {
        "pre_context_s": 60.0,       # source shown before the nominated start (to find the setup)
        "post_context_s": 30.0,      # and after the nominated end (to find the payoff)
        "max_window_s": 330.0,
        "max_setup_window_s": 60.0,  # extra block shown around a far-away setup unit
        "repair_rounds": 1,          # re-ask the tightener once with the rule violations
        "drop_ack_backchannels": True,
        "snap_start": False,         # v0.2 T4: a start may be mid-sentence; rough_start flag + join check + judge decide
        "split_dead_air": False,     # cut silences > dead_air_s inside a piece (off: zero-word cuts; T6 flags instead)
    },
    "guardrails": {                   # clip_spec.md v0.2 section 10 keys (HYP values; product-lead owns them)
        "min_s": 15.0, "soft_max_s": 120.0, "hard_max_s": 150.0,
        "natural_gap_s": 0.5,
        "lead_in_max_words": 4,       # T4 (fail = flag rough_start + judge check)
        "tail_max_words": 2,          # T5 (fail = flag rough_end + judge C3)
        "cut_context_words": 5,       # T2 judge input
        "dead_air_s": 1.5,            # T6
        "max_pieces_per_s": 1 / 3.0,  # T7
        "under_tighten_share": 0.20,  # T8
        "keep_ratio_flag_below": 0.25,  # T9
        "region_span_max_s": 300.0,   # R2
        "skip_s": 30.0,               # S4
        "blocks_flag_above": 3, "blocks_max": 4,   # S1
        "backward_jumps_max": 2,      # O1
        "moved_max_s": 30.0,          # O2
        "long_range_s": 300.0, "long_range_max": 1,  # S5 (pending founder)
        "hook_within_s": 6.0,         # C1
        "low_conf_logprob": -0.7, "low_conf_run": 4,   # A2
        "host_led_share": 0.5,        # section 11 F7
        "max_boundary_shift_s": 5.0,  # pipeline-only: how far code may move a clip edge to reach a natural boundary
    },
    "thresholds": {"t_show": 3.5, "t_rec": 4.0, "below_threshold_max": 3, "dup_overlap": 0.5,
                   "min_part_score": 2, "warn_clips_per_min": 0.5, "rescore_min_overall": 3.0},
    "teaser": {"head_s": 180.0, "ngram": 3, "min_repeat_frac": 0.4, "min_ngrams": 4, "gap_after_s": 60.0},
    "joins": {"enabled": True},
    "ending_fix": {"enabled": True, "min_value": 3.0, "max_payoff": 3.5, "soft_frac": 0.85,
                   "always_for_kinds": ["advice_myth_insight", "personal_story"]},  # re-end weak payoffs, re-score, keep better      # check every join once, repair, re-check once
    "opener_fix": {"enabled": True, "min_value": 3.5},  # try a cold open / earlier start, re-score, keep better
    "revise": {"enabled": False, "min_value": 3.5, "revise_below_show": True},  # one review -> re-cut round
    "budget_usd": 1.5,
    "soft_budget_frac": 0.7,         # past this share of budget_usd, skip optional steps (join re-check, opener fix)
    "concurrency": 6,
    "cache_dir": "runs/cache",
    "pricing": None,
}


def deep_merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "params":
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        cfg = deep_merge(cfg, json.loads(Path(path).read_text(encoding="utf-8")))
    if overrides:
        cfg = deep_merge(cfg, overrides)
    return cfg
