"""Stage 4 ranking (founder, 2026-11-09): ONE text call per episode reads every clip step 7 made (what is said in play
order, hook, ending, why) together with the episode's story, and puts them in posting order: the order, the few to post
first, a reason per clip, and the episode's verdict (strong / thin / none). Text only, about ₹1-3 per episode.

Why a separate call: step 7 runs in batches of scenes, so no step 7 call sees all the clips to compare them; its own
per-clip "strength" is NOT sent here (the model would copy it). Code only checks the format: every clip once in the
order, post_first a subset of the clips. ``prompts/stage4_rank.md``, ``schemas/stage4_rank.schema.json``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from shotvi.llm.base import ModelSpec

from . import stage1_episode_pass as s1b
from . import stage3_find as S3
from .config import inr_per_usd
from .paths import write_json
from .runlog import append_log
from .stage1_facts import fmt_ts

VERSION = "stage4_rank/0.1"
P = Path(__file__).resolve().parent
PROMPT, SCHEMA = P / "prompts" / "stage4_rank.md", P / "schemas" / "stage4_rank.schema.json"


def clip_text(facts: dict, c: dict) -> list[str]:
    """One clip as the ranker reads it: what is said in play order (who says it), opening and ending."""
    nm = S3._names(facts)
    who = facts["episode_pass"]["derived"].get("line_speakers") or {}
    L = {r["id"]: r for r in facts["lines"]}
    said = []
    for p in c["pieces"]:
        for lid in p["lines"]:
            r = L[lid]
            if r["kind"] == "speech":
                said.append(f"{nm.get(who.get(lid), who.get(lid) or '?')}: {r['text']}")
            else:
                said.append(f"({r.get('text') or r.get('tag') or 'sound'})")
    return [f"== {c['id']} ({round(c['duration'])} s, from {fmt_ts(c['start_time'])}, {c.get('kind') or ''})",
            f"Hook: {c.get('hook') or ''}", "Said, in play order:"] + ["  " + x for x in said] + [
            f"Ends on: {(c.get('end') or {}).get('why') or ''}", f"Why the editor cut it: {c.get('why') or ''}",
            f"The viewer feels: {c.get('emotion') or ''}", ""]


def call_text(facts: dict, clips: list[dict]) -> str:
    out = ["# PART 1: THE EPISODE'S STORY", (facts.get("scene_notes") or {}).get("episode") or "", "", "# PART 2: THE CLIPS", ""]
    for c in clips:
        out += clip_text(facts, c)
    return "\n".join(out)


def validate(ans: dict, ids: list[str]) -> tuple[list[str], list[str], dict]:
    errs, warns = [], []
    order = ans.get("order") or []
    if sorted(order) != sorted(ids):
        missing, extra = sorted(set(ids) - set(order)), sorted(set(order) - set(ids))
        dup = sorted({x for x in order if order.count(x) > 1})
        errs.append(f"order must list every clip id exactly once (missing {missing}, unknown {extra}, repeated {dup})")
    pf = ans.get("post_first") or []
    if any(x not in ids for x in pf):
        errs.append(f"post_first {pf} must be clip ids from PART 2")
    if ans.get("verdict") not in ("strong", "thin", "none"):
        errs.append("verdict must be strong, thin or none")
    why = {x.get("clip"): x.get("why") for x in ans.get("reasons") or []}
    if not errs and set(why) != set(ids):
        warns.append(f"reasons missing for {sorted(set(ids) - set(why))}")
    return errs, warns, {"order": order, "post_first": pf, "why": why, "verdict": ans.get("verdict"), "verdict_why": ans.get("verdict_why") or ""}


def run(inputs, facts: dict, cfg: dict, clips: list[dict], client=None, log=None) -> dict:
    t0 = time.perf_counter()
    log = log or (lambda *a: None)
    c = cfg["stage4_rank"]
    spec = ModelSpec.from_dict(c["model"])
    run_dir = Path(inputs.paths.run_dir); run_dir.mkdir(parents=True, exist_ok=True)
    sections = s1b.load_prompt(PROMPT)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    client = S3._client(c, spec, client)
    ids = [x["id"] for x in clips]
    attempts, usage = [], {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0}
    log(f"stage 4: ranking {len(ids)} clips")
    ans, errs, warns, val = S3._ask(client, "stage4_rank", spec, sections, "", schema, [{"text": call_text(facts, clips)}],
                                    {"video_id": inputs.vid, "stage": "stage4_rank"}, lambda o: validate(o, ids),
                                    int(c.get("retries", 1)), attempts, usage)
    write_json(run_dir / "stage4_rank_debug.json", {"attempts": attempts, "model": spec.to_dict()})
    cost = S3._cost(cfg, usage)
    append_log(cfg, {"episode": inputs.vid, "stage": "stage4_rank", "status": "ok" if ans else "failed",
                     "duration_s": round(time.perf_counter() - t0, 1), "input_tokens": usage["input_tokens"],
                     "output_tokens": usage["output_tokens"] + usage["thought_tokens"], "cost_inr_est": cost,
                     "note": "; ".join(errs or warns)[:200]})
    if ans is None:
        raise RuntimeError("stage 4 ranking failed after the re-ask: " + " | ".join(errs[:4]))
    return {"version": VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": spec.to_dict(), **val,
            "warnings": warns, "usage": {**usage, "cost_inr": cost}}


def estimate(facts: dict, cfg: dict, clips: list[dict], counter=None) -> dict:
    if counter is None:
        from shotvi.segment.render import estimate_tokens
        counter = estimate_tokens
    c, r = cfg["stage4_rank"], cfg["rates"]
    n_in = int(counter(call_text(facts, clips) + "\n".join(s1b.load_prompt(PROMPT).values())))
    n_out = int(c.get("output_tokens", 4000))
    return {"input_tokens": n_in, "inr": round((n_in * r["usd_per_1m_input"] + n_out * r["usd_per_1m_output"]) / 1e6 * inr_per_usd(cfg), 2)}
