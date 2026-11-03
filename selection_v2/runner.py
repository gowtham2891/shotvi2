"""Runs one stage for one episode and keeps the books. Stages never call each other; this module does.

    from selection_v2.runner import run_stage, STAGES
    res = run_stage("<video_id>", "1a")        # -> RunResult; raises on failure AFTER logging the failed row

Every run appends one line to ``runs/selection_v2/runs.csv``: episode, stage, status, duration, token usage, cost
estimate in rupees (config rates x the shared rupee rate) and the project ledger's actual delta
(``shotvi.llm.spend``). ``episode_facts.json`` is validated against ``schemas/episode_facts.schema.json`` before
it is written; a stage that returns an invalid document fails loudly and writes nothing.
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

from shotvi.llm import spend

from . import (stage1_episode_pass, stage1_facts, stage2_beats, stage3_candidates, stage4_scoring, stage5_critic,
               stage6_select)
from .config import ROOT, inr_per_usd, load_cast_lists, load_config
from .inputs import load_inputs
from .paths import EpisodePaths, load_json, write_json
from .runlog import LOG_COLUMNS, append_log as _append_log

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "episode_facts.schema.json"
PY = sys.executable

STAGES: list[tuple[str, str, object]] = [
    ("1a", "Stage 1A: episode facts (code)", stage1_facts),
    ("1b", "Stage 1B: episode pass (Gemini)", stage1_episode_pass),
    ("2", "Stage 2: scene beat sheets", stage2_beats),
    ("3", "Stage 3: candidate generation", stage3_candidates),
    ("4", "Stage 4: scoring and vetoes", stage4_scoring),
    ("5", "Stage 5: cold-viewer critic", stage5_critic),
    ("6", "Stage 6: final selection", stage6_select),
]
STAGE_BY_KEY = {k: (label, mod) for k, label, mod in STAGES}

@dataclass
class RunResult:
    episode: str
    stage: str
    status: str                     # ok | failed
    duration_s: float
    facts: dict | None = None
    error: str | None = None
    traceback: str | None = None
    usage: dict = field(default_factory=dict)
    ledger_delta_inr: float = 0.0
    warnings: list[str] = field(default_factory=list)


def validate_facts(facts: dict) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errs = sorted(jsonschema.Draft202012Validator(schema).iter_errors(facts), key=lambda e: list(e.path))
    if errs:
        msg = "; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message[:160]}" for e in errs[:5])
        raise ValueError(f"episode_facts.json violates {SCHEMA_PATH.name} ({len(errs)} error(s)): {msg}")


def append_log(cfg: dict, row: dict) -> Path:
    return _append_log(cfg, row)


def _ledger() -> float:
    try:
        return spend.total_inr()
    except Exception:  # the ledger file is optional for free stages; a broken ledger must not hide a stage result
        return 0.0


def run_stage(vid: str, key: str, cfg: dict | None = None, **kwargs) -> RunResult:
    """Run stage ``key`` for ``vid`` (``kwargs`` go to the stage, e.g. ``scene_ids`` for Stage 2). Always logs a row;
    re-raises the stage's exception after logging it with the full traceback stored on the RunResult."""
    cfg = cfg or load_config()
    if key not in STAGE_BY_KEY:
        raise KeyError(f"unknown stage {key!r}; known: {[k for k, _, _ in STAGES]}")
    label, mod = STAGE_BY_KEY[key]
    p = EpisodePaths(vid, cfg)
    t0, led0 = time.perf_counter(), _ledger()
    res = RunResult(vid, key, "failed", 0.0)
    try:
        inputs = load_inputs(vid, cfg)
        facts = load_json(p.facts) if p.facts.exists() else None
        if key != "1a" and (facts is None or "1a" not in (facts.get("stages") or {})):
            raise RuntimeError(f"{vid}: Stage 1A has not run yet ({p.facts} missing or without stage 1a); run it first")
        if key == "1a":
            new = mod.run(inputs, facts, cfg, load_cast_lists())
        else:
            new = mod.run(inputs, facts, cfg, **kwargs)
        validate_facts(new)
        write_json(p.facts, new)
        res.facts = new
        res.status = "ok"
        res.usage = (new.get("stages", {}).get(key) or {}).get("usage") or {}
        res.warnings = (new.get("stages", {}).get(key) or {}).get("warnings") or []
    except BaseException as e:  # noqa: BLE001 - every failure is logged, then re-raised for the UI
        res.error = f"{type(e).__name__}: {e}"
        res.traceback = traceback.format_exc()
        raise
    finally:
        res.duration_s = round(time.perf_counter() - t0, 3)
        res.ledger_delta_inr = round(_ledger() - led0, 4)
        u = res.usage or {}
        append_log(cfg, {"episode": vid, "stage": key, "status": res.status, "duration_s": res.duration_s,
                         "input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0),
                         "cached_tokens": u.get("cached_tokens", 0), "cost_inr_est": u.get("cost_inr", 0.0),
                         "ledger_delta_inr": res.ledger_delta_inr,
                         "note": (res.error or "; ".join(res.warnings))[:300]})
    return res


def run_substage(vid: str, name: str, fn, cfg: dict | None = None, **kwargs) -> RunResult:
    """Run a function ``fn(inputs, facts, cfg, **kwargs) -> facts`` (e.g. the Stage 2 cast sheet) with the same
    validation and writing as a stage. It does NOT write the runs log: a paid substage logs its own call (as
    stage6_select.regenerate does)."""
    cfg = cfg or load_config()
    p = EpisodePaths(vid, cfg)
    t0, led0 = time.perf_counter(), _ledger()
    res = RunResult(vid, name, "failed", 0.0)
    try:
        inputs = load_inputs(vid, cfg)
        facts = load_json(p.facts) if p.facts.exists() else None
        new = fn(inputs, facts, cfg, **kwargs)
        validate_facts(new)
        write_json(p.facts, new)
        res.facts, res.status = new, "ok"
    except BaseException as e:  # noqa: BLE001
        res.error, res.traceback = f"{type(e).__name__}: {e}", traceback.format_exc()
        raise
    finally:
        res.duration_s = round(time.perf_counter() - t0, 3)
        res.ledger_delta_inr = round(_ledger() - led0, 4)
    return res


def refresh_1a(vid: str, cfg: dict | None = None) -> RunResult:
    """founder, 2026-10-30: rerun Stage 1A (free) after the series cast list changed and KEEP the later outputs (1B, the
    identity pass and its findings, ...) when the lines and gaps come out identical: the cast list only changes ``cast_list``. Refuses (raises) with the first difference otherwise; then 1B and later must be rerun."""
    cfg = cfg or load_config()
    p = EpisodePaths(vid, cfg)
    t0 = time.perf_counter()
    res = RunResult(vid, "1a:refresh", "failed", 0.0)
    try:
        old = load_json(p.facts)
        new = stage1_facts.run(load_inputs(vid, cfg), old, cfg, load_cast_lists())
        sig = lambda f: [(r["id"], r["kind"], r["start"], r["end"], r["text"], r.get("speaker")) for r in f["lines"]]
        gsig = lambda f: [(g["prev"], g["next"], g["gap"]) for g in f["gaps"]]
        if sig(old) != sig(new) or gsig(old) != gsig(new):
            diff = next(((a, b) for a, b in zip(sig(old), sig(new)) if a != b), None)
            raise RuntimeError(f"Stage 1A output changed beyond the cast list (first differing line: {diff}); rerun 1B and later instead")
        out = dict(old)
        out["cast_list"] = new["cast_list"]
        for k in ("name_votes", "name_votes_scribe", "role_votes", "name_vote_events", "name_vote_check"):
            out.pop(k, None)              # founder, 2026-11-02: name votes removed (a code rule)
        out["episode"] = {**old["episode"], **{k: v for k, v in new["episode"].items() if k != "speakers"}}
        st = dict(out["stages"]); st["1a"] = {**new["stages"]["1a"], "warnings": [w for w in new["stages"]["1a"]["warnings"] if "dropped" not in w]
                                              + ["refreshed after a cast-list change: lines and gaps identical; later stages kept (1B saw the earlier cast list)"]}
        out["stages"] = st
        validate_facts(out)
        write_json(p.facts, out)
        res.facts, res.status = out, "ok"
    except BaseException as e:  # noqa: BLE001
        res.error, res.traceback = f"{type(e).__name__}: {e}", traceback.format_exc()
        raise
    finally:
        res.duration_s = round(time.perf_counter() - t0, 3)
        append_log(cfg, {"episode": vid, "stage": "1a:refresh", "status": res.status, "duration_s": res.duration_s, "cost_inr_est": 0.0,
                         "note": (res.error or "lines and gaps identical; later stages kept")[:300]})
    return res


# ----------------------------------------------------------------------------------------------- speaker fix
SPEAKER_FIX_PROJ_RE = re.compile(r"projected total ₹\s*([0-9.]+) \(worst case ₹\s*([0-9.]+)\)")


def _run(args: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    return subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")


def project_speaker_fix(vid: str) -> dict:
    """``python -m shotvi.segment.speaker_fix <id> --dry-run`` -> {expected_inr, worst_case_inr, output}."""
    r = _run([PY, "-m", "shotvi.segment.speaker_fix", vid, "--dry-run"])
    out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    if r.returncode != 0:
        raise RuntimeError(f"speaker_fix --dry-run exited {r.returncode}:\n{out[-2000:]}")
    m = SPEAKER_FIX_PROJ_RE.search(out)
    if not m:
        raise RuntimeError(f"could not read the projection from speaker_fix --dry-run output:\n{out[-2000:]}")
    return {"expected_inr": float(m.group(1)), "worst_case_inr": float(m.group(2)), "output": out}


def run_speaker_fix(vid: str, cfg: dict | None = None, max_ledger_inr: float | None = None) -> dict:
    """Run the existing Gemini speaker correction (unchanged) for an episode that has none; logs a runs.csv row with
    the ledger delta as the actual cost. Raises with the full output when the step fails."""
    cfg = cfg or load_config()
    t0, led0 = time.perf_counter(), _ledger()
    args = [PY, "-m", "shotvi.segment.speaker_fix", vid]
    if max_ledger_inr is not None:
        args += ["--max-ledger-inr", f"{max_ledger_inr:g}"]
    r = _run(args)
    out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    delta = round(_ledger() - led0, 4)
    p = EpisodePaths(vid, cfg)
    ok = r.returncode == 0 and p.speakers.exists()
    append_log(cfg, {"episode": vid, "stage": "speaker_fix", "status": "ok" if ok else "failed",
                     "duration_s": round(time.perf_counter() - t0, 1), "ledger_delta_inr": delta,
                     "note": f"exit {r.returncode}" + ("" if ok else f": {out[-200:]}")})
    if not ok:
        raise RuntimeError(f"speaker_fix exited {r.returncode} (ledger delta ₹{delta:.2f}):\n{out[-4000:]}")
    return {"ledger_delta_inr": delta, "output": out, "path": str(p.speakers)}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m selection_v2.runner", description="run one stage for one episode")
    ap.add_argument("video_id")
    ap.add_argument("stage", choices=[k for k, _, _ in STAGES])
    a = ap.parse_args(argv)
    try:
        res = run_stage(a.video_id, a.stage)
    except Exception:
        traceback.print_exc()
        return 1
    s = (res.facts or {}).get("stages", {}).get(a.stage, {}).get("summary")
    print(f"{a.video_id} stage {a.stage}: {res.status} in {res.duration_s}s; summary: {json.dumps(s, ensure_ascii=False)[:600]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
