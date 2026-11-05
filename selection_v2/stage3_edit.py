"""Stage 3 edit step (founder, 2026-11-05): one call per found clip with the VIDEO of just its stretch (a little before
its start, a little after its end, 2 frames per second so looks and nods are visible). The editor tightens it (up to
three whole-line chunks cut), splits it when it holds two moments, picks the cold open and how long the end holds on
the reaction, and writes the hook, title, why and emotion. Why a separate step: the find call asked for everything at
once over the whole episode at 1 fps; across 19 clips it cut almost nothing (3 clips) and its end holds were seconds
off. Building on the found clip is this step's job (it edits that clip), so it gets the found start, end and payoff.

Times: the transcript in the call uses the VIDEO's clock (0:00 = the stretch's start), the clock the model sees; code
adds the stretch's start back. Code only checks the format and looks up times (``stage3_find.validate_clip``).
``prompts/stage3_edit.md``, ``schemas/stage3_edit.schema.json``. Output: ``facts["clips"]`` with the edited clips.
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path

from shotvi.llm.base import ModelSpec

from . import stage1_episode_pass as s1b
from . import stage3_find as S3
from .config import inr_per_usd
from .media import content_key, cut_scene, media_part, upload
from .paths import write_json
from .runlog import append_log
from .stage1_facts import fmt_ts

VERSION = "stage3_edit/0.1"
P = Path(__file__).resolve().parent
PROMPT, SCHEMA = P / "prompts" / "stage3_edit.md", P / "schemas" / "stage3_edit.schema.json"


def window(facts: dict, clip: dict, pre_s: float, post_s: float) -> tuple[float, float, int, int]:
    """The stretch sent: from pre_s before the clip's start to post_s after its end (inside the episode), and the
    positions of the lines fully inside it."""
    dur = float(facts["episode"]["duration"])
    w0 = max(0.0, float(clip["start_time"]) - pre_s)
    w1 = min(dur, float(clip["pieces"][-1]["end"] if clip.get("pieces") else clip["end_time"]) + post_s)
    lines = facts["lines"]
    inside = [i for i, r in enumerate(lines) if r["start"] >= w0 - 0.05 and r.get("end", r["start"]) <= w1 + 0.05]
    return w0, w1, inside[0], inside[-1]


def line_text(facts: dict, r: dict, w0: float) -> str:
    """``stage3_find.line_text`` on the video's clock."""
    nm = S3._names(facts)
    d = facts["episode_pass"]["derived"]
    a, b = fmt_ts(max(0.0, r["start"] - w0)), fmt_ts(max(0.0, r.get("end", r["start"]) - w0))
    if r["kind"] != "speech":
        return f"{r['id']} [{a}-{b}] (sound) {r.get('text') or r.get('tag') or ''}"
    k = (d.get("line_speakers") or {}).get(r["id"])
    vis = ", ".join(nm.get(x, x) for x in (d.get("on_screen") or {}).get(r["id"]) or []) or "nobody"
    return f"{r['id']} [{a}-{b}] {nm.get(k, k or '?')} (on screen: {vis}) | {r['text']}"


def call_text(facts: dict, clip: dict, w0: float, w1: float, lo: int, hi: int) -> str:
    notes = {n["scene_id"]: n for n in facts["scene_notes"]["scenes"]}
    L = facts["lines"]
    used = sorted({s["id"] for s in facts["identity_pass"]["scenes"] if s["start"] < w1 and s["end"] > w0} | {clip["scene"]})
    out = ["# PART 1: PEOPLE"] + S3.people_text(facts) + ["", "# PART 2: THE STORY OF THESE SCENES"]
    for sid in used:
        if sid in notes:
            n = notes[sid]
            out += [f"Scene {sid}: {n['story']}"] + ([f"A new viewer needs: {n['context_needed']}"] if n.get("context_needed") else []) + [""]
    pos = {r["id"]: i for i, r in enumerate(L)}
    rel = lambda lid: fmt_ts(max(0.0, L[pos[lid]]["start"] - w0))
    out += ["# PART 3: THE MOMENT FOUND",
            f"It lands on {clip['payoff']} [{rel(clip['payoff'])}] \"{clip.get('payoff_quote') or ''}\" ({clip.get('payoff_type') or ''}, {clip.get('kind') or ''}).",
            f"Rough start: {clip['start']['line']} [{rel(clip['start']['line'])}]; rough end: {clip['end']['line']} [{rel(clip['end']['line'])}].",
            f"Why it works: {clip.get('why') or ''}", "",
            f"# PART 4: TRANSCRIPT OF THE STRETCH (the video is {fmt_ts(w1 - w0)} long; id [start-end] who says it (on screen) | words)"]
    out += [line_text(facts, L[i], w0) for i in range(lo, hi + 1)]
    return "\n".join(out)


def validate(ans: dict, facts: dict, words: list[dict], w0: float, lo: int, hi: int, max_s: float) -> tuple[list[str], list[str], list[dict]]:
    errs, warns, out = [], [], []
    cl = ans.get("clips") or []
    if not 1 <= len(cl) <= 2:
        return [f"give one or two clips, not {len(cl)}"], warns, []
    pos = {r["id"]: i for i, r in enumerate(facts["lines"])}
    for i, c in enumerate(cl, 1):
        c2 = copy.deepcopy(c)
        h = S3.clock_s((c2.get("end") or {}).get("hold_until"))
        if (c2.get("end") or {}).get("hold_until") not in (None, "") and h is None:
            errs.append(f"clip {i}: end.hold_until {c2['end']['hold_until']!r} is not a time like the line times (m:ss.s)"); continue
        if h is not None:
            c2["end"]["hold_until"] = h + w0           # the video's clock -> the episode's
        elif isinstance(c2.get("end"), dict):
            c2["end"].pop("hold_until", None)          # empty: end just after the last word
        e, w, val = S3.validate_clip(c2, facts, words, lo, hi, max_s, where="PART 4")
        p = c.get("payoff")
        if not e and not (p in pos and any(p in pc["lines"] for pc in val["pieces"])):
            e = [f"payoff {p!r} is not a line that plays in the clip"]
        errs += [f"clip {i}: {x}" for x in e]
        warns += [f"clip {i}: {x}" for x in w]
        if not e:
            out.append({**c, "end": {**c["end"], "hold_until_episode": round(h + w0, 2) if h is not None else None}, **val})
    return errs, warns, out


def edit_clips(inputs, facts: dict, cfg: dict, clips: list[dict], client=None, log=None, clip_ids=None,
               cutter=None, uploader=None, deleter=None) -> dict:
    t0 = time.perf_counter()
    log = log or (lambda *a: None)
    c = cfg["stage3_edit"]
    spec = ModelSpec.from_dict(c["model"])
    run_dir = Path(inputs.paths.run_dir); run_dir.mkdir(parents=True, exist_ok=True)
    sections = s1b.load_prompt(PROMPT)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    client = S3._client(c, spec, client)
    cutter = cutter or cut_scene; uploader = uploader or upload
    attempts, out, warns, failed = [], [], [], []
    usage = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0}
    for clip in clips:
        if clip_ids and clip["id"] not in clip_ids:
            continue
        w0, w1, lo, hi = window(facts, clip, float(c.get("pre_s", 8)), float(c.get("post_s", 15)))
        log(f"stage 3 edit: {clip['id']} ({fmt_ts(w0)}-{fmt_ts(w1)})")
        mpath = cutter(Path(inputs.paths.mp4), run_dir / cfg["stage2"]["media_dir"], 99, w0, w1, "video", int(c.get("video_max_height", 480)), log)
        up = uploader(mpath, "video")
        try:
            mkey = content_key(mpath)
        except OSError:
            mkey = None
        parts = [media_part(up, "video", float(c.get("fps", 2)), key=mkey), {"text": call_text(facts, clip, w0, w1, lo, hi)}]
        try:
            ans, errs, w, val = S3._ask(client, "stage3_edit", spec, sections, "", schema, parts, {"video_id": inputs.vid, "stage": "stage3_edit", "clip": clip["id"]},
                                        lambda o: validate(o, facts, inputs.words, w0, lo, hi, float(c.get("max_clip_s", 180))),
                                        int(c.get("retries", 1)), attempts, usage)
        finally:
            if c.get("delete_upload_after_call", True):
                try:
                    from .media import delete_upload
                    (deleter or delete_upload)(up)
                except Exception:  # noqa: BLE001
                    pass
        if ans is None:
            failed.append(clip["id"]); warns.append(f"{clip['id']}: no valid edit after the re-ask ({' | '.join(errs[:3])}); the found clip is kept")
            out.append({**clip, "edited": False})
            continue
        warns += [f"{clip['id']}: {x}" for x in w]
        for k, e in enumerate(val):
            out.append({"id": clip["id"] + ("" if k == 0 else "b"), "scene": clip["scene"], "from_clip": clip["id"], "edited": True,
                        "split_why": ans.get("split_why") or "", "found": {x: clip.get(x) for x in ("start", "end", "duration", "payoff")}, **e})
    write_json(run_dir / "stage3_edit_debug.json", {"attempts": attempts, "model": spec.to_dict()})
    cost = S3._cost(cfg, usage)
    append_log(cfg, {"episode": inputs.vid, "stage": "stage3_edit", "status": "ok" if not failed else "partial",
                     "duration_s": round(time.perf_counter() - t0, 1), "input_tokens": usage["input_tokens"],
                     "output_tokens": usage["output_tokens"] + usage["thought_tokens"], "cached_tokens": usage["cached_tokens"],
                     "cost_inr_est": cost, "note": f"{len(out)} clips; " + "; ".join(warns)[:200]})
    res = dict(facts)
    res["clips"] = {"version": VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": spec.to_dict(),
                    "clips": out, "warnings": warns, "failed": failed, "usage": {**usage, "cost_inr": cost}}
    return res


def estimate(facts: dict, cfg: dict, clips: list[dict], counter=None) -> dict:
    """Per clip: the stretch's video at ``fps`` (~70 tokens a frame + 32 a second of sound) + the text at the input
    price; answer + thinking at the output price."""
    if counter is None:
        from shotvi.segment.render import estimate_tokens
        counter = estimate_tokens
    c, r, rate = cfg["stage3_edit"], cfg["rates"], inr_per_usd(cfg)
    prompt = int(counter("\n".join(s1b.load_prompt(PROMPT).values())))
    tot_in = tot_out = 0
    for clip in clips:
        w0, w1, lo, hi = window(facts, clip, float(c.get("pre_s", 8)), float(c.get("post_s", 15)))
        tot_in += (w1 - w0) * (70 * float(c.get("fps", 2)) + r["tokens_per_s_audio"]) + counter(call_text(facts, clip, w0, w1, lo, hi)) + prompt
        tot_out += int(c.get("output_tokens", 5000))
    usd = (tot_in * r["usd_per_1m_input"] + tot_out * r["usd_per_1m_output"]) / 1e6
    return {"calls": len(clips), "input_tokens": int(tot_in), "inr": round(usd * rate, 2)}
