"""Write <video_id>.json (kind: proposals, schema 1.1), clips.md, cost.json and debug files."""
from __future__ import annotations

import json
import time
from pathlib import Path

from shotvi.llm import PRICES
from shotvi.segment.render import fmt_ts

from .config import PIPELINE_VERSION
from .context import file_sha256
from .prompts import load_prompt

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ("discover", "discover_notes", "discover_extra", "tighten", "joins", "score", "hook", "ending", "brief", "rank",
           "interest", "write")


def rel_path(p: Path) -> str:
    p = Path(p).resolve()
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def models_list(cfg) -> list[dict]:
    out = []
    for role, spec in cfg["models"].items():
        if spec:
            out.append({"role": role, "provider": spec["provider"], "model": spec["model"],
                        "params": spec.get("params", {})})
    return out


def run_doc(res: dict, cfg: dict, run_id: str, cost: dict) -> dict:
    ctx = res["ctx"]
    t = cfg["thresholds"]
    return {
        "schema_version": "1.1",
        "kind": "proposals",
        "video_id": ctx.video_id,
        "transcript": {"path": rel_path(ctx.path), "sha256": file_sha256(ctx.path), "n_words": ctx.n,
                       "audio_duration_secs": round(ctx.seg.transcript.duration, 3)},
        "derived_params": {"skip_s": cfg["guardrails"]["skip_s"]},
        "run_id": run_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pipeline": {"version": PIPELINE_VERSION, "git_commit": None,
                     "prompt_versions": {n: load_prompt(n).tag for n in PROMPTS}, "config": cfg},
        "models": models_list(cfg),
        "cost": cost,
        "cutoff": {"t_show": t["t_show"], "t_rec": t["t_rec"], "n_candidates": res["n_moments"],
                   "n_passed_hard_rules": res["n_passed"], "n_shown": len(res["proposals"]),
                   "warnings": res["warnings"]},
        "proposals": res["proposals"],
        "rejected": res["rejected"],
    }


def _kept_md(ctx, pieces, notes) -> str:
    ws = ctx.words
    by_after = {n["after_piece"]: n for n in notes if "after_piece" in n}
    out = []
    spk = None
    for k, (a, b) in enumerate(pieces):
        seg = []
        for i in range(a, b + 1):
            if ws[i].speaker != spk:
                seg.append(f"**{ctx.label(i)}:**")
                spk = ws[i].speaker
            seg.append(ws[i].text)
        out.append(" ".join(seg))
        if k < len(pieces) - 1:
            n = by_after.get(k)
            out.append(f" ⟪{n['type']}⟫ " if n else " ‖ ")
    return "".join(out)


def clips_md(res: dict, doc: dict) -> str:
    ctx = res["ctx"]
    by_id = {c.id: c for c in res["cands"]}
    L = [f"# {ctx.video_id} ({fmt_ts(ctx.seg.transcript.duration)}), run `{doc['run_id']}`", "",
         f"{len(doc['proposals'])} shown from {doc['cutoff']['n_candidates']} candidates "
         f"({doc['cutoff']['n_passed_hard_rules']} passed hard rules). "
         f"Cost ${doc['cost']['usd']:.3f}, {doc['cost']['n_llm_calls']} LLM calls.", ""]
    for w in doc["cutoff"]["warnings"]:
        L.append(f"> warning: {w}")
    if ctx.excluded_ranges:
        L.append("Excluded (teaser): " + "; ".join(
            f"{fmt_ts(ctx.words[a].start)}-{fmt_ts(ctx.words[b].end)}" for a, b, _ in ctx.excluded_ranges))
    L.append("")
    for p in doc["proposals"]:
        c = by_id[p["id"]]
        s = p["scores"]
        r = p["region"]
        L += [f"## {p['rank']}. {p['title']}  ({p['tier']}, overall {s['overall']})", "",
              f"- id `{p['id']}` | source {fmt_ts(r['start'])}-{fmt_ts(r['end'])} | edited {p['edited_duration']:.0f}s "
              f"in {len(p['pieces'])} pieces, {p['n_blocks']} block(s) | {p['category']}",
              f"- scores: hook {s['hook']}, standalone {s['standalone']}, payoff {s['payoff']}, "
              f"value {s['value_emotion']}",
              f"- hook line: \"{p['hook_line']['text']}\"",
              f"- why: {p['why_it_works']}"]
        if p["flags"]:
            L.append(f"- flags: {', '.join(p['flags'])}")
        for rk in p["standalone_risks"]:
            L.append(f"- risk ({rk['type']}): {rk['note']}")
        for n in p["edit_notes"]:
            L.append(f"- edit ({n['type']}): {n['reason']}")
        if p.get("related"):
            L.append("- related: " + ", ".join(f"{x['id']} ({x['relation']})" for x in p["related"]))
        if c.info.get("join_repairs"):
            L.append(f"- joins repaired by the finishing check: {c.info['join_repairs']}")
        if c.info.get("interest"):
            L.append(f"- audience interest {c.info['interest']['score']}/5: {c.info['interest']['reason']}")
        if c.moment.get("interest_reason"):
            L.append(f"- found by the editor's-notes pass: {c.moment['interest_reason']}")
        if c.info.get("rank_reason"):
            L.append(f"- episode ranker ({c.info.get('ranker_tier')}): {c.info['rank_reason']}")
        if c.info.get("ending_fix"):
            L.append(f"- ending moved onto the payoff ({c.info['ending_fix']}): {c.info.get('ending_reason', '')}")
        if c.info.get("opener_fix"):
            L.append(f"- opener fixed automatically ({c.info['opener_fix']}): {c.info.get('opener_reason', '')}")
        if c.info.get("revised"):
            L.append("- re-cut once after the internal review")
        if c.judge and c.judge.get("fix_hint") and c.judge["fix_hint"].lower() != "none":
            L.append(f"- reviewer hint: {c.judge['fix_hint']}")
        L += ["", "> " + _kept_md(ctx, c.pieces, c.edit_notes), ""]
    if doc["rejected"]:
        L += ["## Not shown", ""]
        for r in doc["rejected"]:
            c = by_id[r["id"]]
            title = (c.write or {}).get("title") or (c.info or {}).get("point") or c.moment.get("point", "")
            sc = f" (overall {r['scores']['overall']})" if r.get("scores") else ""
            L.append(f"- `{r['id']}` {fmt_ts(r['region']['start'])} {title}{sc}: {'; '.join(r['reject_reasons'])}")
    no_pieces = [c for c in res["cands"] if not c.pieces]
    for c in no_pieces:
        L.append(f"- `{c.id}` (no clip) {c.moment.get('point', '')}: {'; '.join(c.reject)}")
    return "\n".join(L) + "\n"


def cost_block(summary: dict, audio_s: float, cfg: dict) -> dict:
    hours = audio_s / 3600
    list_usd = 0.0
    out = {"input_tokens": summary["input_tokens"], "output_tokens": summary["output_tokens"],
           "usd": summary["usd"], "wall_time_secs": summary["wall_time_secs"],
           "n_llm_calls": summary["n_llm_calls"], "by_role": summary["by_role"],
           "usd_billed_this_run": summary["usd_billed"], "n_cache_hits": summary["n_cache_hits"],
           "thought_tokens": summary["thought_tokens"],
           "audio_hours": round(hours, 3),
           "usd_per_audio_hour": round(summary["usd"] / hours, 4) if hours else None,
           "input_tokens_per_audio_hour": int(summary["input_tokens"] / hours) if hours else None,
           "output_tokens_per_audio_hour": int(summary["output_tokens"] / hours) if hours else None,
           "note": "usd = full uncached cost (what a production run pays); Gemini 3.x Flash at promo price "
                   "(list price after 2026-12-31 is 2x)."}
    return out


def write_debug(out_dir: Path, vid: str, debug: dict):
    d = out_dir / "debug"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{vid}.json").write_text(json.dumps(debug, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
