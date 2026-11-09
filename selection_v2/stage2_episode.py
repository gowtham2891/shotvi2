"""Stage 2 as ONE call over the whole episode (founder, 2026-11-03): Gemini watches the whole episode with the
transcript split into the video step's scenes (identity_pass.scenes, with their ends and the gaps between them) and
the video step's final who-talks / who-is-on-screen per line, and describes every scene as FACTS: the story, the
situations inside it, mood, reactions, visual moments, silences, the context a cold viewer needs, how it connects to
other scenes, relationships. No clip picks, hooks, payoffs, ratings or lines to cut: those are Stage 3's decisions
(founder: a judgement here would steer Stage 3). No photos / face checks: the video step's answer is final.

Code checks the format only (every scene answered; line ids inside their scene; ranges on the clock inside the scene;
connections name a real scene); a slip on one item drops that item with a warning, never a guess.
``prompts/stage2_episode.md``, ``schemas/scene_notes.schema.json``. Output: ``facts["scene_notes"]``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from shotvi.llm.base import ModelSpec

from . import stage1_episode_pass as s1b
from .config import ROOT, inr_per_usd
from .media import content_key, cut_scene, media_part, upload
from .paths import write_json
from .runlog import append_log
from .stage1_facts import fmt_ts, parse_ts

VERSION = "stage2_episode/0.1"
ROLE = "scene_notes"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "stage2_episode.md"
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "scene_notes.schema.json"


class SceneNotesError(RuntimeError):
    pass


def scenes_of(facts: dict) -> list[dict]:
    sc = (facts.get("identity_pass") or {}).get("scenes")
    if not sc:
        raise SceneNotesError("Stage 2 needs the video step's scenes (identity_pass with scenes: find)")
    return sc


def names(facts: dict) -> dict:
    return {s["key"]: s.get("name") or s["key"] for s in facts["episode_pass"]["speakers"]}


def context_text(facts: dict) -> str:
    """PART 1 (the people with their looks) and PART 2 (the transcript by the video step's scenes, gaps marked)."""
    nm = names(facts)
    d = facts["episode_pass"]["derived"]
    talk, seen = d.get("line_speakers") or {}, d.get("on_screen") or {}
    looks = {p["key"]: p.get("look") or "" for p in (facts.get("identity_pass") or {}).get("people") or []}
    roles = {s["key"]: s.get("role") or "" for s in facts["episode_pass"]["speakers"]}
    used = sorted({k for k in talk.values()} | {k for v in seen.values() for k in v})
    ep = facts["episode"]
    out = [f"# EPISODE: {ep.get('title') or ep['video_id']} ({ep['duration_str']})", "", "# PART 1: PEOPLE (key = name: role (look))"]
    for k in used:
        out.append(f"{k} = {nm.get(k, k)}: {roles.get(k, '')}" + (f" ({looks[k]})" if looks.get(k) else ""))
    out += ["", "# PART 2: TRANSCRIPT BY SCENE (id [start-end] who says it (on screen) | words)"]
    pos = {r["id"]: i for i, r in enumerate(facts["lines"])}
    sc = scenes_of(facts)
    owner = {}
    for s in sc:
        for i in range(pos[s["first_line"]], pos[s["last_line"]] + 1):
            owner[i] = s["id"]
    cur = None
    for i, r in enumerate(facts["lines"]):
        o = owner.get(i)
        if o != cur:
            if o is None:
                out.append("\n-- between scenes --")
            else:
                s = next(x for x in sc if x["id"] == o)
                out.append(f"\n== Scene {o} ({fmt_ts(s['start'])}-{fmt_ts(s['end'])}) {s.get('where') or ''}")
            cur = o
        if r["kind"] == "speech":
            who = nm.get(talk.get(r["id"]), talk.get(r["id"]) or "?")
            vis = ", ".join(nm.get(k, k) for k in seen.get(r["id"]) or []) or "nobody"
            out.append(f"{r['id']} [{fmt_ts(r['start'])}-{fmt_ts(r['end'])}] {who} (on screen: {vis}) | {r['text']}")
        else:
            out.append(f"{r['id']} [{fmt_ts(r['start'])}] (sound) {r.get('text') or r.get('tag') or ''}")
    return "\n".join(out)


def _range(s) -> tuple[float, float] | None:
    try:
        a, b = (parse_ts(x.strip())[0] for x in str(s or "").split("-"))
    except Exception:  # noqa: BLE001
        return None
    return (a, b) if a <= b else None


def validate(ans: dict, facts: dict) -> tuple[list[str], list[str], list[dict]]:
    """Format checks only. Errors (re-ask): a scene without an entry. Item slips are dropped with a warning."""
    errs, warns = [], []
    sc = {s["id"]: s for s in scenes_of(facts)}
    pos = {r["id"]: i for i, r in enumerate(facts["lines"])}
    got = {}
    for e in ans.get("scenes") or []:
        sid = e.get("scene_id")
        if sid not in sc:
            warns.append(f"an entry for scene {sid!r}, which is not a scene, was dropped"); continue
        if sid in got:
            warns.append(f"a second entry for scene {sid} was dropped"); continue
        got[sid] = e
    missing = [i for i in sc if i not in got]
    if missing:
        errs.append(f"scenes {missing} have no entry; describe every scene in PART 2")
    out, drops = [], {}

    def keep(items, ok, what):
        kept = [x for x in items or [] if ok(x)]
        if len(kept) < len(items or []):
            drops[what] = drops.get(what, 0) + len(items) - len(kept)
        return kept
    for sid, e in sorted(got.items()):
        s = sc[sid]
        lo, hi = pos[s["first_line"]], pos[s["last_line"]]
        inside = lambda l: l in pos and lo <= pos[l] <= hi
        # founder run 2026-11-05 (Surya): opening shots before a scene's first line and beats in the gap after its last
        # line are real; a range may reach into the gaps next to the scene (up to the neighbouring scenes' lines)
        order = sorted(sc.values(), key=lambda x: x["start"])
        k = next(i for i, x in enumerate(order) if x["id"] == sid)
        reach0 = order[k - 1]["end"] if k > 0 else 0.0
        reach1 = order[k + 1]["start"] if k + 1 < len(order) else float(facts["episode"].get("duration") or s["end"] + 60)
        on_clock = lambda x: ((r := _range(x.get("range"))) is not None and r[0] <= r[1] and min(reach0, s["start"] - 2.0) <= r[0]
                              and r[1] <= max(reach1, s["end"] + 2.0))
        sits = keep(e.get("situations"), lambda x: inside(x.get("first")) and inside(x.get("last")) and pos[x["first"]] <= pos[x["last"]],
                    "situations with line ids outside their scene")
        reacts = keep(e.get("reactions"), lambda x: inside(x.get("after")), "reactions after a line outside their scene")
        vis = keep(e.get("visual_moments"), on_clock, "visual moments with a range that is not m:ss.s-m:ss.s inside their scene")
        sil = keep(e.get("silences"), on_clock, "silences with a range that is not m:ss.s-m:ss.s inside their scene")
        conn = keep(e.get("connections"), lambda x: x.get("scene") in sc and x.get("scene") != sid, "connections to a scene that does not exist")
        out.append({**e, "situations": sits, "reactions": reacts, "visual_moments": vis, "silences": sil, "connections": conn,
                    "first_line": s["first_line"], "last_line": s["last_line"], "start": s["start"], "end": s["end"], "where": s.get("where") or ""})
    warns += [f"{n} {what} dropped" for what, n in drops.items()]
    return errs, warns, out


def estimate(facts: dict, cfg: dict, counter=None) -> dict:
    c = cfg["stage2_episode"]
    if counter is None:
        from shotvi.segment.render import estimate_tokens
        counter = estimate_tokens
    text = context_text(facts) + "\n\n" + "\n\n".join(s1b.load_prompt(PROMPT_PATH).values())
    n_text, n_video = int(counter(text)), int(float(facts["episode"]["duration"]) * cfg["rates"]["tokens_per_s_video_low"])
    out = int(c.get("output_tokens_base", 2000)) + int(c.get("output_tokens_per_scene", 900)) * len(scenes_of(facts))
    r = cfg["rates"]
    inr = ((n_text + n_video) * r["usd_per_1m_input"] + out * r["usd_per_1m_output"]) / 1e6 * inr_per_usd(cfg)
    return {"text_tokens": n_text, "video_tokens": n_video, "expected_output_tokens": out, "inr_expected": round(inr, 2)}


def run(inputs, facts: dict, cfg: dict, client=None, log=None, cutter=None, uploader=None, deleter=None) -> dict:
    t0 = time.perf_counter()
    log = log or (lambda *a: None)
    c = cfg["stage2_episode"]
    spec = ModelSpec.from_dict(c["model"])
    run_dir = Path(inputs.paths.run_dir); run_dir.mkdir(parents=True, exist_ok=True)
    context = context_text(facts)
    (run_dir / "context_scene_notes.txt").write_text(context, encoding="utf-8")
    cutter = cutter or cut_scene; uploader = uploader or upload
    dur = float(facts["episode"]["duration"])
    mpath = cutter(Path(inputs.paths.mp4), run_dir / cfg["stage2"]["media_dir"], 0, 0.0, dur, "video", int(c.get("video_max_height", 480)), log)
    try:
        mkey = content_key(mpath)
    except OSError:
        mkey = None
    sections = s1b.load_prompt(PROMPT_PATH)
    # founder, 2026-11-06: read box A (made by the video step) when it is still there; else send the video as before
    from . import video_box as VB
    box = VB.load(run_dir, mkey, spec.model, cfg) if VB.enabled(cfg) else None
    if box:
        log("scene notes: reading the stored video (box A)")
    up = None if box else uploader(mpath, "video")
    vpart = None if box else media_part(up, "video", 1, key=mkey)
    rq = VB.request(sections["system"], vpart, context, box)
    cspec = VB.spec_for(spec, box)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if client is None:
        from shotvi.llm.client import Ledger, LLMClient, get_provider
        client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"), max_retries=int(c.get("http_retries", 1)))
        get_provider(spec.provider).timeout = float(c.get("http_timeout_s", 1200))
    attempts, errors, ans, warns, notes = [], [], None, [], []
    usage = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0}
    for attempt in range(int(c.get("retries", 1)) + 1):
        instr = (sections["instructions"]
                 + ("\n\n" + sections["retry"].replace("{errors}", "\n".join(f"- {e}" for e in errors)) + f"\n(attempt {attempt + 1})" if errors else "")
                 + "\n\n" + sections["output_format"])
        att = {"n": attempt + 1, "instructions": instr, "errors_shown": errors}
        attempts.append(att)
        log(f"scene notes: call {attempt + 1}")
        try:
            obj, raw = client.generate_json(ROLE, cspec, rq["system"], instr, schema, meta={"video_id": inputs.vid, "stage": "scene_notes"}, want_raw=True,
                                            media=rq["media"], **({"cached_content": rq["cached_content"]} if rq["cached_content"] else {}))
        except BaseException as e:
            att["error"] = f"{type(e).__name__}: {e}"
            write_json(run_dir / "scene_notes_debug.json", {"attempts": attempts, "system": sections["system"]})
            append_log(cfg, {"episode": inputs.vid, "stage": "scene_notes", "status": "failed", "duration_s": round(time.perf_counter() - t0, 1), "note": att["error"][:300]})
            raise
        att["raw_text"] = raw.text
        att["usage"] = {k: int(getattr(raw, k) or 0) for k in usage}
        for k in usage:
            usage[k] += att["usage"][k]
        errors, warns, notes = validate(obj, facts)
        att["validation"] = errors; att["warnings"] = warns
        if not errors:
            ans = obj; break
    if c.get("delete_upload_after_call", True) and up is not None:
        try:
            from .media import delete_upload
            attempts[-1]["upload_deleted"] = (deleter or delete_upload)(up)
        except Exception as e:  # noqa: BLE001
            attempts[-1]["upload_deleted"] = f"failed: {type(e).__name__}: {e}"
    write_json(run_dir / "scene_notes_debug.json", {"attempts": attempts, "system": sections["system"], "model": spec.to_dict()})
    r, rate = cfg["rates"], inr_per_usd(cfg)
    cached = min(usage["cached_tokens"], usage["input_tokens"])
    cost = round(((usage["input_tokens"] - cached) * r["usd_per_1m_input"] + cached * r["usd_per_1m_cached_input"]
                  + (usage["output_tokens"] + usage["thought_tokens"]) * r["usd_per_1m_output"]) / 1e6 * rate, 3)
    status = "ok" if ans is not None else "failed"
    append_log(cfg, {"episode": inputs.vid, "stage": "scene_notes", "status": status, "duration_s": round(time.perf_counter() - t0, 1),
                     "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"] + usage["thought_tokens"],
                     "cached_tokens": usage["cached_tokens"], "cost_inr_est": cost, "note": "; ".join(errors or warns)[:300]})
    if ans is None:
        raise SceneNotesError("scene notes failed validation after the retry: " + " | ".join(errors[:6]))
    out = dict(facts)
    out["scene_notes"] = {"version": VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": spec.to_dict(),
                          "episode": ans.get("episode") or "", "scenes": notes, "warnings": warns,
                          "usage": {**usage, "cost_inr": cost}, "n_attempts": len(attempts), "video_box": bool(box)}
    return out
