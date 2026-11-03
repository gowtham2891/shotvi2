"""Cast sheet (founder, 2026-10-26): ONE Gemini call per episode that picks the REFERENCE FACES for Stage 2.

Why: a frame cut while a line is spoken shows whoever the camera is on (a listener, the one holding the phone, the one
being sung to), so a midpoint rule labelled three of five frames wrong. The model can read the transcript around each
frame and say who is really visible. Steps:

  1. code cuts candidate frames: up to ``candidates_per_speaker`` per person, from lines where the video step saw
     them alone on screen (founder, 2026-11-02; see ``candidate_lines``)
  2. one call: the cached prefix + every frame with the lines around it; the model returns per frame
     n_people_visible, visible_keys, named_visible, named_identifiable, and per character a LOOK description
  3. code keeps, per key, frames where the named character is visible and identifiable: single-person frames first,
     two-person frames only when nothing better exists, NEVER a frame with three or more people
  4. the chosen frames + looks are shown in the app (Reference faces row); nothing enters the series bank until the
     founder presses Save. Stage 2 uses, in order: the series bank, the cast sheet, hand pins, the automatic fallback.

Output: ``facts["cast_sheet"]`` = {frames, chosen, looks, usage} and ``runs/selection_v2/<id>/cast_sheet_debug.json``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from shotvi.llm.base import ModelSpec

from . import stage1_episode_pass as s1b
from .config import ROOT, inr_per_usd
from .media import extract_frame, image_part
from .paths import write_json
from .prefix import build_prefix_stage2
from .runlog import append_log
from .stage1_facts import fmt_ts
from .stage2_beats import _prefix1_text, line_key, map_names, scene_lines

VERSION = "cast_sheet/0.1"
ROLE = "cast_sheet"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "cast_sheet.md"
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "cast_sheet.schema.json"


class CastSheetError(RuntimeError):
    pass


def nominated_times(facts: dict, cfg: dict) -> dict[str, list[dict]]:
    """Stage 1B's reference_times (Tier 0, from watching) mapped to final person keys by name: key -> [{t, t_str}]."""
    from .stage1_facts import parse_ts
    from .stage1_episode_pass import _loose
    ep = facts.get("episode_pass") or {}
    names, _ = map_names(facts)
    by_name = {_loose(n): k for k, n in names.items() if n}
    cap = int((cfg.get("stage1b") or {}).get("reference_times_per_character", 3))
    out: dict[str, list[dict]] = {}
    for rt in ep.get("reference_times") or []:
        k = by_name.get(_loose(rt.get("name")))
        if k is None:
            continue
        for t in (rt.get("times") or [])[:cap]:
            try:
                tv = parse_ts(t)[0]
            except Exception:
                continue
            out.setdefault(k, []).append({"line": None, "t": round(tv, 2), "t_str": fmt_ts(tv), "text": rt.get("note") or "", "why": "nominated by 1B"})
    return out


def _spread(rows: list[dict], n: int) -> list[dict]:
    rows = sorted(rows, key=lambda r: r["start"])
    if len(rows) <= n:
        return rows
    idx = sorted({round(i * (len(rows) - 1) / (n - 1)) for i in range(n)})
    return [rows[i] for i in idx]


def candidate_lines(facts: dict, cfg: dict) -> dict[str, list[dict]]:
    """Per person key: the frames to cut for the cast-sheet check.

    founder, 2026-11-02: after the naming check, ONLY lines where the video step saw that person ALONE on screen
    (``naming_check.alone_on_screen``; a frame cut while someone speaks often shows the listener). A person never seen
    alone gets no candidate. Without the naming check: Stage 1B's nominated times, else up to N spoken lines."""
    cc = cfg["stage2"].get("cast_sheet") or {}
    n_per, min_s = int(cc.get("candidates_per_speaker", 8)), float(cc.get("min_line_s", 1.0))
    ok = lambda r: r["kind"] == "speech" and not r.get("zero_len") and (r["end"] - r["start"]) >= min_s
    mk = lambda r, why: {"line": r["id"], "t": round((r["start"] + r["end"]) / 2, 2), "t_str": fmt_ts((r["start"] + r["end"]) / 2),
                         "text": r["text"][:60], "why": why}
    from .naming_check import speaker_summary
    alone = speaker_summary(facts).get("alone_on_screen")
    if alone is not None:
        by_id = {r["id"]: r for r in facts["lines"]}
        return {k: [mk(r, "the video step saw them alone on screen") for r in _spread([by_id[x] for x in ids if x in by_id and ok(by_id[x])], n_per)]
                for k, ids in alone.items() if any(x in by_id and ok(by_id[x]) for x in ids)}
    nominated = nominated_times(facts, cfg)
    _, over = map_names(facts)
    by_key: dict[str, list[dict]] = {}
    for r in facts["lines"]:
        if ok(r) and not r.get("filler"):
            k = line_key(r, over)
            if k:
                by_key.setdefault(k, []).append(r)
    out = dict(nominated)
    for k, rows in by_key.items():
        if k not in out:
            out[k] = [mk(r, "spoken line") for r in _spread(rows, n_per)]
    return out


def context_lines(facts: dict, lid: str, n: int = 3) -> str:
    ids = [r["id"] for r in facts["lines"]]
    i = ids.index(lid)
    names, over = map_names(facts)
    out = []
    for r in facts["lines"][max(0, i - n):i + n + 1]:
        mark = ">>" if r["id"] == lid else "  "
        if r["kind"] == "event":
            out.append(f"{mark} {r['id']} (sound) {r['text']}")
        else:
            k = line_key(r, over)
            out.append(f"{mark} {r['id']} [{k} {names.get(k)}]: {r['text'][:70]}")
    return "\n".join(out)


def choose(frames: list[dict], verdicts: dict[str, dict], per_speaker: int) -> dict[str, list[dict]]:
    """Per key, the frames to keep: named visible + identifiable; prefer n_people == 1; allow 2 only when no single;
    never >= 3."""
    out: dict[str, list[dict]] = {}
    by_key: dict[str, list[tuple]] = {}
    for f in frames:
        v = verdicts.get(f["frame_id"])
        if not v or not v.get("named_visible") or not v.get("named_identifiable"):
            continue
        n = int(v.get("n_people_visible") or 0)
        if n >= 3 or n == 0:
            continue
        by_key.setdefault(f["key"], []).append((n, f, v))
    for k, lst in by_key.items():
        singles = [x for x in lst if x[0] == 1]
        use = singles if singles else lst
        use.sort(key=lambda x: (x[0], x[1]["t"]))
        out[k] = [{**f, "n_people_visible": n, "note": v.get("note"), "why": "cast sheet: " + ("alone on screen" if n == 1 else "two people, named identifiable")}
                  for n, f, v in use[:per_speaker]]
    return out


def validate(ans: dict, frames: list[dict], keys: set) -> list[str]:
    errs = []
    ids = {f["frame_id"] for f in frames}
    seen = set()
    for v in ans.get("frames") or []:
        fid = str(v.get("frame_id"))
        if fid not in ids:
            errs.append(f"unknown frame_id {fid!r}")
        seen.add(fid)
        for k in v.get("visible_keys") or []:
            if k != "?" and k not in keys:
                errs.append(f"frame {fid}: visible_keys has unknown key {k!r}")
    missing = sorted(ids - seen)
    if missing:
        errs.append(f"no verdict for frame(s) {missing[:10]}{'...' if len(missing) > 10 else ''}: every frame needs one")
    for c in ans.get("characters") or []:
        if c.get("key") not in keys:
            errs.append(f"characters: unknown key {c.get('key')!r}")
        for fid in c.get("best_frames") or []:
            if fid not in ids:
                errs.append(f"characters {c.get('key')}: best_frames has unknown frame {fid!r}")
    return errs


def estimate(facts: dict, cfg: dict, prefix_tokens: int | None = None) -> dict:
    r, rate = cfg["rates"], inr_per_usd(cfg)
    cc = cfg["stage2"].get("cast_sheet") or {}
    cands = candidate_lines(facts, cfg)
    n = sum(len(v) for v in cands.values())
    per_img = int((cfg["stage2"].get("reference_frames") or {}).get("tokens_per_image", 264))
    if prefix_tokens is None:
        from shotvi.segment.render import estimate_tokens
        prefix_tokens = estimate_tokens(build_prefix_stage2(facts, _prefix1_text(facts, cfg)))
    inp = prefix_tokens + n * (per_img + 120)
    out = int(cc.get("expected_output_tokens", 60 * n + 800))
    usd = (inp * r["usd_per_1m_input"] + out * r["usd_per_1m_output"]) / 1e6
    return {"n_frames": n, "n_keys": len(cands), "input_tokens": inp, "output_tokens": out, "inr": round(usd * rate, 2)}


def run(inputs, facts: dict | None, cfg: dict, client=None, framer=None, log=None) -> dict:
    t0 = time.perf_counter()
    log = log or (lambda *a: None)
    if not facts or "1b" not in (facts.get("stages") or {}):
        raise RuntimeError("the cast sheet needs Stage 1B output (the speaker map); run Stage 1B first")
    framer = framer or extract_frame
    run_dir = Path(inputs.paths.run_dir)
    media_dir = run_dir / cfg["stage2"]["media_dir"]
    cc = cfg["stage2"].get("cast_sheet") or {}
    width = int((cfg["stage2"].get("reference_frames") or {}).get("width", 320))
    names, _ = map_names(facts)
    prefix2 = build_prefix_stage2(facts, _prefix1_text(facts, cfg))
    cands = candidate_lines(facts, cfg)
    frames, parts = [], [{"text": prefix2}, {"text": "# CANDIDATE FRAMES (one per line; the lines around each are shown)"}]
    n = 0
    for k in sorted(cands):
        for c in cands[k]:
            n += 1
            fid = f"F{n}"
            path = framer(Path(inputs.paths.mp4), media_dir, f"cand_{k}", c["t"], width, log)
            frames.append({"frame_id": fid, "key": k, "name": names.get(k), **c, "path": str(path)})
            if c.get("line"):
                ctx = f"cut during line {c['line']} ({c['t_str']}), which the map credits to speaker key {k} ({names.get(k)}). Lines around it:\n{context_lines(facts, c['line'])}"
            else:
                near = min((r for r in facts["lines"] if r["kind"] == "speech"), key=lambda r: abs(r["start"] - c["t"]))
                ctx = f"at {c['t_str']}, a moment Stage 1B nominated as showing speaker key {k} ({names.get(k)}) alone. Lines around it:\n{context_lines(facts, near['id'])}"
            parts.append({"text": f"Frame {fid}: {ctx}"})
            parts.append(image_part(path, key=f"cand|{k}|{c['line']}|{c['t']}"))
    if not frames:
        raise CastSheetError("no candidate frames: no speaker key has a line of at least the minimum length")
    sections = s1b.load_prompt(PROMPT_PATH)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    spec = ModelSpec.from_dict(cfg["models"].get("cast_sheet") or cfg["models"].get("stage2") or cfg["models"]["default"])
    if client is None:
        from shotvi.llm.client import Ledger, LLMClient
        client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"))
    keys = set(names)
    attempts, errors, ans = [], [], None
    usage = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0}
    for attempt in range(2):
        instr = sections["instructions"] + ("\n\n" + sections["retry"].replace("{errors}", "\n".join(f"- {e}" for e in errors)) if errors else "") + "\n\n" + sections["output_format"]
        att = {"n": attempt + 1, "instructions": instr, "errors_shown": errors, "n_frames": len(frames)}
        attempts.append(att)
        log(f"cast sheet: call {attempt + 1} with {len(frames)} frames")
        try:
            obj, raw = client.generate_json(ROLE, spec, sections["system"], instr, schema,
                                            meta={"video_id": inputs.vid, "stage": "2cast"}, want_raw=True, media=parts)
        except BaseException as e:
            att["error"] = f"{type(e).__name__}: {e}"
            write_json(run_dir / "cast_sheet_debug.json", {"frames": frames, "attempts": attempts})
            append_log(cfg, {"episode": inputs.vid, "stage": "2:cast_sheet", "status": "failed", "duration_s": round(time.perf_counter() - t0, 1), "note": att["error"][:300]})
            raise
        att["raw_text"] = raw.text
        att["usage"] = {"input_tokens": raw.input_tokens, "output_tokens": raw.output_tokens, "thought_tokens": raw.thought_tokens, "cached_tokens": raw.cached_tokens}
        for k2 in usage:
            usage[k2] += int(getattr(raw, k2) or 0)
        errors = validate(obj, frames, keys)
        att["validation"] = errors
        if not errors:
            ans = obj
            break
    write_json(run_dir / "cast_sheet_debug.json", {"frames": frames, "attempts": attempts, "model": spec.to_dict(), "system": sections["system"]})
    r = cfg["rates"]; rate = inr_per_usd(cfg)
    cost = round(((usage["input_tokens"] - min(usage["cached_tokens"], usage["input_tokens"])) * r["usd_per_1m_input"]
                  + min(usage["cached_tokens"], usage["input_tokens"]) * r["usd_per_1m_cached_input"]
                  + (usage["output_tokens"] + usage["thought_tokens"]) * r["usd_per_1m_output"]) / 1e6 * rate, 3)
    if ans is None:
        append_log(cfg, {"episode": inputs.vid, "stage": "2:cast_sheet", "status": "failed", "duration_s": round(time.perf_counter() - t0, 1),
                         "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"] + usage["thought_tokens"], "cost_inr_est": cost,
                         "note": "validation failed after retry: " + " | ".join(errors[:5])})
        raise CastSheetError("the cast sheet answer failed validation after 2 attempts: " + " | ".join(errors[:6]))
    verdicts = {str(v["frame_id"]): v for v in ans["frames"]}
    per = int((cfg["stage2"].get("reference_frames") or {}).get("per_speaker", 3))
    chosen = choose(frames, verdicts, per)
    looks = {c["key"]: {"look": c.get("look"), "look_changes": c.get("look_changes"), "confidence": c.get("confidence"),
                        "best_frames": c.get("best_frames") or []} for c in ans["characters"]}
    facts = dict(facts)
    facts["cast_sheet"] = {"version": VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "frames": [{**f, **{k2: verdicts[f["frame_id"]].get(k2) for k2 in ("n_people_visible", "visible_keys", "named_visible", "named_identifiable", "note")}} for f in frames],
                           "chosen": chosen, "looks": looks, "n_attempts": len(attempts),
                           "usage": {**usage, "cost_inr": cost}, "verified": False}
    append_log(cfg, {"episode": inputs.vid, "stage": "2:cast_sheet", "status": "ok", "duration_s": round(time.perf_counter() - t0, 1),
                     "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"] + usage["thought_tokens"],
                     "cached_tokens": usage["cached_tokens"], "cost_inr_est": cost,
                     "note": f"{len(frames)} candidate frames, {sum(len(v) for v in chosen.values())} kept for {len(chosen)} keys"})
    return facts
