"""Stage 5, EDITOR'S PICK (founder, 2026-10-30; replaces the cold critic as the gate, which stays as stage5.mode critic).

Shortlist (code): the top ``editor.top_moments`` moments by their best version's Stage 4 combined, across all scenes,
plus every moment holding a situation or thread version scoring >= ``situation_thread_min``. Per moment up to 2
versions: the one closest to the channel's typical Short length among versions within ``stage6.primary.score_window``
of the moment's best, and the best-scored one if different. The episode's own matched channel Shorts join as extra
moments, unlabelled (same neutral ids, a lead like any other).

One call (thinking medium): the cached Stage 2 prefix (transcript, facts, speaker map, glossary) first; then the
taste block, the leads (per moment: scene, setup, payoff note), the typical length, and every version as ONE low-res
reel (the rendered clips back to back; each version's reel range is listed). As this channel's editor the model picks 6
posts in posting order (version, why, hook seen + heard, what a newcomer needs, fan clip, cut fix) and 3 alternates.

Code then applies the set rules in rank order: no overlaps, <= ``stage6.rules.max_per_scene`` per scene, never a
whole-scene version beside another moment of its scene, <= ``max_fan_clips`` fan clips; a violating pick is replaced by
the next alternate that fits. A channel_short pick is never shipped: it is replaced and reported.
Output: ``facts["editor"]``; Stage 6 builds its set from it and its text call uses the editor's notes.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path

import jsonschema

from shotvi.llm.base import ModelSpec

from . import stage4_scoring as S4
from . import stage5_critic as S5
from .config import ROOT, inr_per_usd, loops_on
from .media import content_key, media_part, upload
from .paths import write_json
from .preview import render as render_clip
from .runlog import append_log
from .stage1_episode_pass import load_prompt
from .stage1_facts import fmt_ts
from .typical import typical_length

VERSION = "stage5-editor/0.1"
ROLE = "editor_pick"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "stage5_editor.md"
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "editor.schema.json"
SITUATION_VARIANTS = ("situation", "situation_trimmed", "thread")
WHOLE_SCENE = ("scene", "scene_trimmed")


class EditorError(RuntimeError):
    pass


def moment_kind(c: dict) -> str:
    if c["variant"] == "thread":
        return "thread"
    return c["type"] if c["type"] in ("hook", "loop") else "story"


# ----------------------------------------------------------------------------------------------- shortlist
def all_moments(facts: dict, cfg: dict) -> list[dict]:
    """Every moment with at least one shippable version (Stage 4 status in stage6.ship_stage4_status, eligible), its
    best combined, and its versions sorted best first."""
    run4 = S5.check_inputs(facts)
    s4 = {s["id"]: s for s in run4["candidates"]}
    ship = set(cfg["stage6"]["ship_stage4_status"])
    skip_loops = not loops_on(facts, cfg)
    groups: dict[str, list[dict]] = {}
    for c in facts["candidates"]:
        s = s4.get(c["id"])
        if c["variant"] == "channel_short" or (skip_loops and c["type"] == "loop") or s is None:
            continue
        if s["status"] not in ship or not c.get("eligible_for_final", True) or not s.get("eligible_for_final", True):
            continue
        groups.setdefault(f"{c['scene_id']}|{c.get('payoff_line')}|{moment_kind(c)}", []).append(
            {"id": c["id"], "variant": c["variant"], "type": c["type"], "final_duration": float(c["final_duration"]), "combined": float(s["combined"]),
             "start": float(c["start"]), "end": float(c["end"]), "segments": c["segments"], "scene_id": c["scene_id"]})
    out = []
    for key, vs in groups.items():
        vs.sort(key=lambda v: (-v["combined"], v["final_duration"], v["id"]))
        scene_id, payoff, kind = key.split("|")
        out.append({"key": key, "scene_id": int(scene_id), "payoff_line": payoff, "kind": kind, "best": vs[0]["combined"], "all_versions": vs})
    out.sort(key=lambda m: (-m["best"], m["key"]))
    return out


def pick_versions(m: dict, target: float, window: float = 0.0, n: int = 3) -> list[dict]:
    """founder, 2026-10-30 (length is never a rule but the platform maximum): the best-scored version, the one closest
    to the typical length (if different) and the longest one (if different; every version here is <= platform max)."""
    vs = m["all_versions"]
    best = vs[0]
    typ = min(vs, key=lambda v: (abs(v["final_duration"] - target), -v["combined"], v["id"]))
    longest = max(vs, key=lambda v: (v["final_duration"], v["combined"], v["id"]))
    out = []
    for v in (best, typ, longest):
        if v["id"] not in {x["id"] for x in out}:
            out.append(v)
    return out[:n]


def shortlist(facts: dict, cfg: dict) -> dict:
    ed = cfg["stage5"]["editor"]
    tl = typical_length(facts, cfg)
    window = float((cfg["stage6"].get("primary") or {}).get("score_window", 0.5))
    ms = all_moments(facts, cfg)
    # founder, 2026-10-30: Stage 4 scores scenes in separate calls, so cross-scene rank alone must not decide what the editor
    # sees: every scene's best moment first, then the top moments overall up to max(top_moments, scenes + scene_extra)
    n_scenes = len({s["scene_id"] for s in facts.get("beat_sheets") or []}) or len({m["scene_id"] for m in ms})
    target_n = max(int(ed["top_moments"]), n_scenes + int(ed.get("scene_extra", 5)))
    listed: dict[str, str] = {}
    for m in ms:                                                  # ms is ranked best first: the first per scene is its best
        if m["scene_id"] not in {x["scene_id"] for x in ms if x["key"] in listed}:
            listed[m["key"]] = "the best moment of its scene"
    for m in ms:
        if len(listed) >= target_n:
            break
        listed.setdefault(m["key"], "top by Stage 4")
    for m in ms:
        if m["key"] not in listed and any(v["variant"] in SITUATION_VARIANTS and v["combined"] >= float(ed["situation_thread_min"]) - 1e-9 for v in m["all_versions"]):
            listed[m["key"]] = "situation / thread >= threshold"
    chosen = []
    for m in ms:
        if m["key"] in listed:
            chosen.append({**{k: m[k] for k in ("key", "scene_id", "payoff_line", "kind", "best")}, "channel_short": False, "why_listed": listed[m["key"]],
                           "versions": pick_versions(m, tl["target_s"], window, int(ed["versions_per_moment"]))})
    top = [m for m in chosen if m["why_listed"] != "situation / thread >= threshold"]
    extra = [m for m in chosen if m["why_listed"] == "situation / thread >= threshold"]
    for c in facts["candidates"]:                                 # the channel's own Shorts, unlabelled
        if c["variant"] == "channel_short":
            chosen.append({"key": f"channel_short|{c['id']}", "scene_id": c["scene_id"], "payoff_line": c.get("payoff_line"), "kind": "story", "best": None,
                           "channel_short": True, "why_listed": "the episode's own channel Short (reference)",
                           "versions": [{"id": c["id"], "variant": c["variant"], "type": c["type"], "final_duration": float(c["final_duration"]), "combined": None,
                                         "start": float(c["start"]), "end": float(c["end"]), "segments": c["segments"], "scene_id": c["scene_id"]}]})
    chosen.sort(key=lambda m: (min(v["start"] for v in m["versions"]), m["key"]))
    for i, m in enumerate(chosen, 1):
        m["moment_id"] = f"P{i:02d}"
        for j, v in enumerate(m["versions"]):
            v["version_id"] = f"{m['moment_id']}-{'ABC'[j]}"
    return {"typical": tl, "moments": chosen, "n_ranked": len(ms), "n_top": len(top), "n_extra": len(extra),
            "n_channel_shorts": sum(1 for m in chosen if m["channel_short"])}


def lead_text(facts: dict, m: dict) -> str:
    """Where the scene is and what it is about, from the beat sheet (no scores, nothing marking a channel Short)."""
    sh = next((s for s in facts.get("beat_sheets") or [] if s["scene_id"] == m["scene_id"]), {})
    lid = {r["id"]: r for r in facts["lines"]}
    pay = lid.get(m.get("payoff_line"))
    note = ""
    if pay is not None:
        if (sh.get("payoff") or {}).get("line") == pay["id"]:
            note = (sh.get("payoff") or {}).get("note") or ""
        else:
            note = next((b.get("note") for b in sh.get("beats") or [] if b.get("line") == pay["id"] and b.get("note")), "") or ""
    return f"scene {m['scene_id']}: {sh.get('label') or ''}. Setup: {sh.get('setup') or ''}" + (f" Ends around: {note}" if note else "")


# ----------------------------------------------------------------------------------------------- reel
def _probe(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float((r.stdout or "0").strip() or 0)


def build_reel(src: Path, run_dir: Path, sl: dict, cfg: dict, renderer=None, log=None) -> tuple[Path, list[dict]]:
    """Every version rendered (Stage 5 renders reused) and joined into one low-res reel; [{version_id, reel_start, reel_end}]."""
    ed = cfg["stage5"]["editor"]
    renderer = renderer or render_clip
    paths, rows, t = [], [], 0.0
    for m in sl["moments"]:
        for v in m["versions"]:
            c = {"id": v["id"], "segments": v["segments"], "start": v["start"], "end": v["end"], "final_duration": v["final_duration"],
                 "removed": {"gaps_shortened": [], "gaps_removed": [], "lines_skipped": []}}
            p = Path(renderer(src, run_dir, c, int(cfg["stage5"]["clip_max_height"])))
            d = _probe(p) or v["final_duration"]
            paths.append(p)
            rows.append({"version_id": v["version_id"], "candidate_id": v["id"], "reel_start": round(t, 2), "reel_end": round(t + d, 2)})
            t += d
    sig = hashlib.sha1(json.dumps([[r["candidate_id"], str(p.stat().st_size)] for r, p in zip(rows, paths)]).encode()).hexdigest()[:12]
    out = Path(run_dir) / "editor" / f"reel_{sig}.mp4"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as td:
            lst = Path(td) / "list.txt"
            lst.write_text("".join(f"file '{p}'\n" for p in paths), encoding="utf-8")
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                   "-vf", f"scale=-2:{int(ed['reel_height'])}", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(int(ed["reel_crf"])),
                   "-c:a", "aac", "-b:a", "64k", str(out)]
            if log:
                log(f"reel: {len(paths)} versions, {t:.0f} s")
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0 or not out.exists():
                raise EditorError(f"ffmpeg failed building the reel (exit {r.returncode}):\n{r.stderr[-1500:]}")
    return out, rows


# ----------------------------------------------------------------------------------------------- prompt
def listing(facts: dict, sl: dict, rows: list[dict]) -> str:
    at = {r["version_id"]: r for r in rows}
    L = [f"# CANDIDATE MOMENTS ({len(sl['moments'])}), in episode order"]
    for m in sl["moments"]:
        L.append(f"\n## {m['moment_id']} · {lead_text(facts, m)}")
        for v in m["versions"]:
            r = at.get(v["version_id"]) or {}
            L.append(f"- {v['version_id']}: {v['final_duration']:.0f} s, episode {fmt_ts(v['start'])}-{fmt_ts(v['end'])}"
                     + (f" in {len(v['segments'])} pieces" if len(v["segments"]) > 1 else "")
                     + f"; reel {fmt_ts(r.get('reel_start', 0))}-{fmt_ts(r.get('reel_end', 0))}")
    return "\n".join(L)


def validate(ans: dict, sl: dict, cfg: dict) -> list[str]:
    ed = cfg["stage5"]["editor"]
    errs = [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message[:120]}"
            for e in jsonschema.Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))).iter_errors(ans)]
    if errs:
        return errs
    versions = {v["version_id"]: m["moment_id"] for m in sl["moments"] for v in m["versions"]}
    moments = {m["moment_id"] for m in sl["moments"]}
    picks, alts = ans["picks"], ans["alternates"]
    if len(picks) != int(ed["picks"]):
        errs.append(f"picks: exactly {ed['picks']}, got {len(picks)}")
    if sorted(p["rank"] for p in picks) != list(range(1, len(picks) + 1)):
        errs.append("picks: ranks must be 1..N, each once")
    if len(alts) != int(ed["alternates"]):
        errs.append(f"alternates: exactly {ed['alternates']}, got {len(alts)}")
    seen = set()
    for x in picks + alts:
        if x["moment_id"] not in moments:
            errs.append(f"unknown moment_id {x['moment_id']}")
        elif versions.get(x["version_id"]) != x["moment_id"]:
            errs.append(f"{x['version_id']} is not a version of {x['moment_id']}")
        if x["moment_id"] in seen:
            errs.append(f"{x['moment_id']} is used twice (picks and alternates must be different moments)")
        seen.add(x["moment_id"])
    return errs


# ----------------------------------------------------------------------------------------------- set rules
def _span(v: dict) -> tuple[float, float]:
    return float(v["start"]), float(v["end"])


def rule_break(v: dict, fan: bool, accepted: list[dict], cfg: dict) -> str | None:
    rules = cfg["stage6"]["rules"]
    a0, a1 = _span(v)
    for o in accepted:
        b0, b1 = _span(o["version"])
        if a0 < b1 - 1e-3 and b0 < a1 - 1e-3:
            return f"overlaps {o['moment_id']}"
        if o["version"]["scene_id"] == v["scene_id"] and (v["variant"] in WHOLE_SCENE or o["version"]["variant"] in WHOLE_SCENE):
            return f"a whole-scene version beside {o['moment_id']} of the same scene"
    if sum(1 for o in accepted if o["version"]["scene_id"] == v["scene_id"]) >= int(rules.get("max_per_scene", 2)):
        return f"already {rules.get('max_per_scene', 2)} posts from scene {v['scene_id']}"
    if fan and sum(1 for o in accepted if o["fan_clip"]) >= int(cfg["stage5"]["editor"].get("max_fan_clips", 2)):
        return f"already {cfg['stage5']['editor'].get('max_fan_clips', 2)} fan clips"
    return None


def apply_rules(ans: dict, sl: dict, cfg: dict) -> dict:
    """The editor's picks in rank order under the set rules; a violating pick (or a channel Short) is replaced by the next
    alternate that fits. {posts, replaced, unused_alternates, channel_short_picks}."""
    by_v = {v["version_id"]: (m, v) for m in sl["moments"] for v in m["versions"]}
    queue = [dict(a, source="alternate") for a in ans["alternates"]]
    posts, replaced, cs_picks = [], [], []

    def entry(x: dict) -> dict:
        m, v = by_v[x["version_id"]]
        return {"moment_id": m["moment_id"], "moment_key": m["key"], "version_id": x["version_id"], "candidate_id": v["id"], "version": v,
                "why": x.get("why", ""), "hook": x.get("hook") or {}, "newcomer_needs": x.get("newcomer_needs", ""),
                "fan_clip": bool(x.get("fan_clip", False)), "cut_fix": x.get("cut_fix"), "source": x.get("source", "pick"), "editor_rank": x.get("rank")}

    for x in sorted(ans["picks"], key=lambda p: p["rank"]):
        e = entry(dict(x, source="pick"))
        m, _ = by_v[x["version_id"]]
        why = "a channel Short (reference only, never shipped)" if m["channel_short"] else rule_break(e["version"], e["fan_clip"], posts, cfg)
        if m["channel_short"]:
            cs_picks.append({"rank": x["rank"], "moment_id": m["moment_id"], "candidate_id": e["candidate_id"], "why": x.get("why", "")})
        if why is None:
            posts.append(e); continue
        sub = None
        while queue:
            a = queue.pop(0)
            ma, _ = by_v[a["version_id"]]
            ea = entry(a)
            if ma["channel_short"]:
                cs_picks.append({"rank": None, "moment_id": ma["moment_id"], "candidate_id": ea["candidate_id"], "why": a.get("why", ""), "as": "alternate"})
                continue
            if rule_break(ea["version"], ea["fan_clip"], posts, cfg) is None:
                sub = ea; break
        replaced.append({"rank": x["rank"], "moment_id": e["moment_id"], "candidate_id": e["candidate_id"], "rule": why,
                         "replaced_by": (sub or {}).get("moment_id")})
        if sub is not None:
            sub["editor_rank"] = x["rank"]
            posts.append(sub)
    return {"posts": posts, "replaced": replaced, "unused_alternates": [entry(a) for a in queue], "channel_short_picks": cs_picks}


# ----------------------------------------------------------------------------------------------- estimate / run
def estimate(facts: dict, cfg: dict, sl: dict | None = None, run_dir: Path | None = None) -> dict:
    sl = sl or shortlist(facts, cfg)
    ed, r, rate = cfg["stage5"]["editor"], cfg["rates"], inr_per_usd(cfg)
    cal = cfg["stage4"].get("calibration") or {}
    from shotvi.segment.render import estimate_tokens
    tf = float(cal.get("text_factor", 1.0))
    pre = int(estimate_tokens(S4.prefix2_text(facts, cfg, run_dir)) * tf)
    reel_s = sum(v["final_duration"] for m in sl["moments"] for v in m["versions"])
    vid = int(reel_s * float(cal.get("video_tokens_per_s") or r["tokens_per_s_video_low"]))
    text = int((estimate_tokens(S4.taste_block(facts, cfg)) + 120 * len(sl["moments"]) + int(ed["prompt_tokens"])) * tf)
    out = int(ed["expected_output_tokens"])
    inr = ((pre + vid + text) * r["usd_per_1m_input"] + out * r["usd_per_1m_output"]) / 1e6 * rate
    return {"n_moments": len(sl["moments"]), "n_versions": sum(len(m["versions"]) for m in sl["moments"]), "reel_s": round(reel_s),
            "prefix_tokens": pre, "video_tokens": vid, "text_tokens": text, "expected_output_tokens": out, "total_inr": round(inr, 2),
            "note": "one call; the reel at the low-resolution video rate; medium thinking counted as output"}


def run(inputs, facts: dict | None, cfg: dict, client=None, log=None, renderer=None, uploader=None, deleter=None, **_ignored) -> dict:
    t0 = time.perf_counter()
    log = log or (lambda *a: None)
    run4 = S5.check_inputs(facts)
    assert facts is not None
    ed = cfg["stage5"]["editor"]
    spec = ModelSpec.from_dict(cfg["models"].get("stage5_editor") or cfg["models"]["default"])
    run_dir = Path(inputs.paths.run_dir)
    sl = shortlist(facts, cfg)
    if not sl["moments"]:
        raise EditorError("no moment has a version Stage 4 passed: nothing for the editor to pick from")
    reel, rows = build_reel(Path(inputs.paths.mp4), run_dir, sl, cfg, renderer=renderer, log=log)
    sections = load_prompt(PROMPT_PATH)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    prefix2 = S4.prefix2_text(facts, cfg, run_dir)
    if client is None:
        from shotvi.llm.client import Ledger, LLMClient, get_provider
        client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"), max_retries=1)
        try:
            get_provider(spec.provider).timeout = float(ed.get("http_timeout_s", 1200))
        except Exception:
            pass
    up = (uploader or upload)(reel, "video")
    part = media_part(up, "video", ed.get("media_fps"), key=f"sha:{content_key(reel)}")
    body = (S4.taste_block(facts, cfg) + "\n\n" + f"How long this channel's posts usually are: about {sl['typical']['target_s']:.0f} s (not a limit).\n\n"
            + listing(facts, sl, rows))
    instr = sections["instructions"].replace("{typical_s}", f"{sl['typical']['target_s']:.0f}")
    attempts, errors, ans = [], [], None
    usage = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0}
    for attempt in range(int(ed.get("retries", 1)) + 1):
        prompt = (body + "\n\n" + instr + ("\n\n" + sections["retry"].replace("{errors}", "\n".join(f"- {e}" for e in errors)) if errors else "")
                  + "\n\n" + sections["output_format"] + (f"\n\n(attempt {attempt + 1})" if attempt else ""))
        att = {"n": attempt + 1, "prompt": prompt, "errors_shown": errors, "part_order": ["prefix_stage2.txt", "reel video", "taste + typical + leads + instructions"]}
        attempts.append(att)
        log(f"editor's pick: call {attempt + 1} ({len(sl['moments'])} moments, {len(rows)} versions, reel {rows[-1]['reel_end']:.0f} s)")
        try:
            obj, raw = client.generate_json(ROLE, spec, sections["system"], prompt, schema, meta={"video_id": inputs.vid, "stage": "5-editor"},
                                            want_raw=True, media=[{"text": prefix2}, part])
        except BaseException as e:
            att["error"] = f"{type(e).__name__}: {e}"
            write_json(run_dir / "stage5_editor_debug.json", {"model": spec.to_dict(), "system": sections["system"], "attempts": attempts})
            append_log(cfg, {"episode": inputs.vid, "stage": "5:editor", "status": "failed", "duration_s": round(time.perf_counter() - t0, 1), "note": att["error"][:300]})
            raise
        att["raw_text"] = raw.text
        att["usage"] = {k: int(getattr(raw, k) or 0) for k in usage}
        for k in usage:
            usage[k] += int(getattr(raw, k) or 0)
        errors = validate(obj, sl, cfg)
        att["validation"] = errors
        if not errors:
            ans = obj; break
    try:
        from .media import delete_upload
        (deleter or delete_upload)(up)
    except Exception:  # noqa: BLE001
        pass
    write_json(run_dir / "stage5_editor_debug.json", {"model": spec.to_dict(), "system": sections["system"], "attempts": attempts, "reel": str(reel), "reel_rows": rows})
    r, rate = cfg["rates"], inr_per_usd(cfg)
    cached = min(usage["cached_tokens"], usage["input_tokens"])
    cost = round(((usage["input_tokens"] - cached) * r["usd_per_1m_input"] + cached * r["usd_per_1m_cached_input"]
                  + (usage["output_tokens"] + usage["thought_tokens"]) * r["usd_per_1m_output"]) / 1e6 * rate, 3)
    append_log(cfg, {"episode": inputs.vid, "stage": "5:editor", "status": "ok" if ans else "failed", "duration_s": round(time.perf_counter() - t0, 1),
                     "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"] + usage["thought_tokens"], "cached_tokens": usage["cached_tokens"],
                     "cost_inr_est": cost, "note": f"{len(sl['moments'])} moments" if ans else "validation failed: " + " | ".join(errors[:4])})
    if ans is None:
        raise EditorError("the editor's answer failed validation after the retry: " + " | ".join(errors[:6]))
    res = apply_rules(ans, sl, cfg)
    facts = dict(facts)
    facts["editor"] = {"version": VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": spec.to_dict(), "prompt_file": PROMPT_PATH.name,
                       "run_key": run4["run_key"], "stage3_generated_at": facts["stages"]["3"]["generated_at"], "typical": sl["typical"],
                       "shortlist": sl, "reel": {"path": str(reel), "rows": rows}, "answer": ans, **res, "usage": {**usage, "cost_inr": cost}}
    facts.setdefault("stages", {})["5"] = {
        "version": VERSION, "mode": "editor", "model": spec.to_dict(), "prompt_file": PROMPT_PATH.name, "config_sha8": cfg.get("_sha8"),
        "generated_at": facts["editor"]["generated_at"], "duration_s": round(time.perf_counter() - t0, 3),
        "usage": {"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"] + usage["thought_tokens"], "cached_tokens": usage["cached_tokens"], "cost_inr": cost},
        "warnings": ([f"only {len(res['posts'])} posts after the set rules (alternates ran out)"] if len(res["posts"]) < int(ed["picks"]) else [])
                    + [f"rank {x['rank']} {x['moment_id']} replaced: {x['rule']}" for x in res["replaced"]],
        "summary": {"posts": len(res["posts"]), "replaced": len(res["replaced"]), "channel_short_picks": len(res["channel_short_picks"]),
                    "shortlist_moments": len(sl["moments"])}, "debug_file": "stage5_editor_debug.json"}
    return facts
