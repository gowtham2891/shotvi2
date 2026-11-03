"""Stage 2, scene beat sheets (spec section 5): ONE Gemini call per scene of the Stage 1B scene list, with the scene
MEDIA (low-resolution video for scripted series, audio for podcasts / talking heads) and the cached prefix
(``prefix_stage2.txt`` = the 1B prefix bytes + the 1B results, identical across every scene call of the episode).

Per scene the model returns a beat sheet (``schemas/beat_sheet.schema.json``): setup / turn / payoff / reaction,
register, context_required, dynamic, delivery notes, clip hints, every non-dialogue stretch classified, skippable
lines, ``speakers_seen`` (who is physically there: the FINAL word on identity, because a face settles what text
cannot), non-story confirmations, confidence. Positions are LINE IDS; code converts to times.

Code then: validates (beats inside the scene, payoff exists, non-dialogue ranges are gap-table pauses, skippable
lines are not beats; one retry with the errors shown), derives times, and turns speakers_seen mismatches into
SPEAKER MAP CORRECTIONS (scene, lines, old name, new name, evidence) applied to a new map version; 1B's map is never
overwritten and every correction can be reverted in the app. Scenes are marked ``thin_diarization`` (raw Scribe labels
< map speakers, or > 33% of lines reassigned) and the prompt then tells the model the labels are unreliable.

Refuses to run without current Stage 1B output. Runs one scene or any subset (``scene_ids``); reruns replace that
scene's sheet. Per-scene cost goes to runs.csv.
"""
from __future__ import annotations

import re

import json
import time
from pathlib import Path

from shotvi.llm.base import ModelSpec

from . import stage1_episode_pass as s1b
from . import stage1_facts
from .config import ROOT, inr_per_usd
from .glossary import load_glossary
from .media import cut_scene, extract_frame, image_part, media_part, upload
from .paths import write_json
from .prefix import build_prefix, build_prefix_stage2
from .runlog import append_log
from .stage1_facts import diarization_stats, fmt_ts, is_thin_diarization
from .stage1_episode_pass import expand_lines

VERSION = "stage2/0.3"
STAGE_KEY = "2"
ROLE = "beat_sheet"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "stage2_beats.md"
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "beat_sheet.schema.json"


class BeatSheetError(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------- scene facts
def scene_lines(facts: dict, scene: dict) -> list[dict]:
    """Every line (speech and sound) from the scene's first line to its last, in table order."""
    ids = [r["id"] for r in facts["lines"]]
    a, b = ids.index(scene["first_line"]), ids.index(scene["last_line"])
    return facts["lines"][a:b + 1]


def scene_dialogue_ids(facts: dict, scene: dict) -> list[str]:
    return [r["id"] for r in scene_lines(facts, scene) if r["kind"] == "speech" and not r["zero_len"]]


def map_names(facts: dict) -> tuple[dict[str, str], dict[str, str]]:
    """(key -> 1B name, line id -> key overrides from 1B)."""
    ep = facts.get("episode_pass") or {}
    names = {s["key"]: s.get("name") for s in ep.get("speakers") or []}
    if ep.get("prior") != "raw":                       # fix mode: keys the fix used but 1B did not list keep the fix name
        for c in facts["episode"].get("speakers") or []:
            names.setdefault(c["key"], c.get("name"))
    return names, dict((ep.get("derived") or {}).get("line_speakers") or {})


def line_key(r: dict, overrides: dict) -> str | None:
    """A line's person key: 1B's final key (every line in raw mode; reassigned lines in fix mode), else the fix key."""
    return overrides.get(r["id"]) or r.get("speaker_key")


def anchored_in_scene(facts: dict, scene: dict) -> list[dict]:
    """Stage 1A anchored speaker segments (isolated speaker, or every co-speaker an exclusive partner) whose lines fall
    in this scene: the structural signal that caught the delivery customer."""
    ids = set(scene_dialogue_ids(facts, scene))
    out = []
    for d in facts.get("speaker_segments") or []:
        for seg in d["segments"]:
            if seg.get("anchored"):
                hit = [x for x in seg.get("line_ids") or [] if x in ids]
                if hit:
                    out.append({"key": d["key"], "range": seg["range"], "n_lines": len(hit),
                                "why": "isolated speaker" if d.get("isolated") else f"only company {seg['co_speakers']} never appears without them"})
    return out


def scene_stats(facts: dict, scene: dict, cfg: dict) -> dict:
    ids = scene_dialogue_ids(facts, scene)
    _, over = map_names(facts)
    raw_mode = (facts.get("episode_pass") or {}).get("prior") == "raw"
    # in raw mode the final keys come from 1B (every line is in `over`), so "reassigned" = lines whose final key is
    # not the majority key of their raw label (a split), plus 1B merges are not reassignments
    st = diarization_stats(facts["lines"], ids, set() if raw_mode else set(over), keys=over if raw_mode else None)
    tcfg = cfg["stage2"]["thin_diarization"]
    st["anchored"] = anchored_in_scene(facts, scene) if tcfg.get("anchored_segments", True) else []
    st["thin"] = is_thin_diarization(st, float(tcfg["reassigned_fraction"])) or bool(st["anchored"])
    st["thin_reasons"] = ([f"{st['n_raw_labels']} raw label(s) for {st['n_map_speakers']} speakers"] if st["n_raw_labels"] < st["n_map_speakers"] else []) \
        + ([f"{st['reassigned_fraction']:.0%} of lines reassigned by the fix"] if st["reassigned_fraction"] > float(tcfg["reassigned_fraction"]) else []) \
        + [f"1A anchor: {a['key']} in {a['range']} ({a['why']})" for a in st["anchored"]]
    return st


def scene_facts_text(facts: dict, scene: dict, cfg: dict, stats: dict) -> str:
    names, over = map_names(facts)
    rows = scene_lines(facts, scene)
    ids = {r["id"] for r in rows}
    out = [f"# PART 2: SCENE {scene['id']} ({scene['start_str']}-{scene['end_str']}; {scene.get('register')}: {scene.get('label')})",
           f"media: the scene from {scene['start_str']} to {scene['end_str']} (t=0 in the media is {scene['start_str']} in the episode)",
           "", "## LINES IN THIS SCENE (id [start-end] [key name]: words)"]
    for r in rows:
        if r["kind"] == "event":
            out.append(f"{r['id']} [{fmt_ts(r['start'])}-{fmt_ts(r['end'])}] (sound) {r['text']}")
        else:
            k = line_key(r, over)
            out.append(f"{r['id']} [{fmt_ts(r['start'])}-{fmt_ts(r['end'])}] [{k} {names.get(k, '?')}]: {r['text']}")
    out += ["", "## PAUSES INSIDE THE SCENE (after -> before: length; what fills it) = the only non-dialogue stretches"]
    for g in facts["gaps"]:
        if g["prev"] in ids and g["next"] in ids and g["gap"] >= 0.3:
            out.append(f"{g['prev']} -> {g['next']}: {g['gap']:.1f} s; {g['filled_by'] or '-'}")
    out += ["", "## SOUND TAGS INSIDE THE SCENE"]
    for e in facts["tag_events"]:
        if scene["start"] - 0.5 <= e["t"] <= scene["end"] + 2.0:
            out.append(f"{fmt_ts(e['t'])} ({e['t'] - scene['start']:.1f} s into the media) {e['class']} {e['tag']} after {e.get('after_content_line') or e['after']} (tag line {e['line']})")
    ep = facts.get("episode_pass") or {}
    ns = [n for n in ep.get("possible_non_story") or [] if any(x in ids for x in expand_lines(n.get("lines", ""), [r["id"] for r in facts["lines"]])[0])]
    out += ["", "## POSSIBLY NON-STORY LINES IN THIS SCENE (confirm or reject from the media)"]
    out += [f"{n['lines']}: {n.get('kind')} [{', '.join(n.get('signals') or [])}]: {n.get('reason')}" for n in ns] or ["(none)"]
    out += ["", f"## DIARIZATION: {stats['n_raw_labels']} raw label(s) for {stats['n_map_speakers']} map speaker(s); "
                f"{stats['n_reassigned']} of {stats['n_lines']} lines reassigned by the speaker fix"
                + (f"; structural flags: {'; '.join(a for a in stats.get('thin_reasons', []) if a.startswith('1A anchor'))}" if stats.get("anchored") else "")
                + f" -> {'THIN: labels unreliable' if stats['thin'] else 'ok'}"]
    return "\n".join(out)


# ----------------------------------------------------------------------------------------------- reference frames
def reference_lines(facts: dict, cfg: dict, per_speaker: int | None = None) -> dict[str, list[dict]]:
    """For every speaker key, the best-supported lines to take a reference face from: a line spoken right after
    lines in scenes whose diarization is not thin, then the longest lines; two different scenes when possible. Returns key -> [{line, t, t_str, why, scene_id}]."""
    rc = cfg["stage2"].get("reference_frames") or {}
    n_per = int(per_speaker or rc.get("per_speaker", 2))
    min_s = float(rc.get("min_line_s", 1.0))
    names, over = map_names(facts)
    scenes = (facts.get("episode_pass") or {}).get("derived", {}).get("scenes") or []
    thin = {sc["id"]: scene_stats(facts, sc, cfg)["thin"] for sc in scenes}
    scene_of = {}
    for sc in scenes:
        for r in scene_lines(facts, sc):
            scene_of[r["id"]] = sc["id"]
    # never take a reference from a line whose label is itself in doubt: anchored segments (the prior was withheld
    # there), structural / confidence suspects, 1B resolutions and reassignments, possibly non-story lines
    all_ids = [r["id"] for r in facts["lines"]]
    raw_mode = (facts.get("episode_pass") or {}).get("prior") == "raw"
    prior_of = {r["id"]: (r.get("speaker") if raw_mode else r.get("speaker_key")) for r in facts["lines"]}
    reassigned = {lid for lid, k in over.items() if k != prior_of.get(lid)}      # lines 1B moved to another person
    excluded = set(facts.get("structural_suspect_ids") or []) | reassigned
    from .prefix import anchor_checks
    for q in anchor_checks(facts, float(cfg.get("stage1b", {}).get("anchor_max_share", 0.5))):   # the anchors 1B actually asked
        excluded |= set(q.get("line_ids") or [])
    ep = facts.get("episode_pass") or {}
    for x in ep.get("suspect_resolutions") or []:
        excluded.add(x.get("line"))
    for n in ep.get("possible_non_story") or []:
        ids, e = expand_lines(n.get("lines", ""), all_ids)
        if not e:
            excluded |= set(ids)
    cands: dict[str, list[tuple]] = {}
    for r in facts["lines"]:
        if r["kind"] != "speech" or r.get("zero_len") or r.get("filler") or r["id"] in excluded:
            continue
        k = line_key(r, over)
        if not k or (r["end"] - r["start"]) < min_s:
            continue
        sid = scene_of.get(r["id"])
        clean = sid is not None and not thin.get(sid, True)
        if not clean:
            continue
        score = 2 + min(1.0, (r["end"] - r["start"]) / 5.0)
        cands.setdefault(k, []).append((score, r, sid, "scene with clean diarization"))
    # a key with no acceptable line at all: fall back to its longest line outside the excluded set, marked as weak
    for r in facts["lines"]:
        k = line_key(r, over) if r["kind"] == "speech" else None
        if k and k not in cands and not r.get("zero_len") and not r.get("filler") and r["id"] not in excluded and (r["end"] - r["start"]) >= min_s:
            cands.setdefault(k, []).append((min(1.0, (r["end"] - r["start"]) / 5.0), r, scene_of.get(r["id"]), "WEAK: longest line in a thin scene"))
    out = {}
    for k, lst in cands.items():
        lst.sort(key=lambda x: -x[0])
        chosen, used = [], set()
        for score, r, sid, why in lst:
            if sid in used and len(lst) > n_per:
                continue
            t = (r["start"] + r["end"]) / 2
            chosen.append({"line": r["id"], "t": round(t, 2), "t_str": fmt_ts(t), "why": why, "scene_id": sid, "text": r["text"][:50]})
            used.add(sid)
            if len(chosen) >= n_per:
                break
        out[k] = chosen
    return out


def manual_references(facts: dict, cfg: dict) -> dict[str, list[dict]]:
    """Hand-pinned reference times: selection_v2/reference/faces/<video_id>.yaml = {key: ["m:ss.s", ...]}. A key listed
    there replaces the automatic choice for that key (the founder checked the frames by eye)."""
    import yaml
    from .stage1_facts import parse_ts
    rc = cfg["stage2"].get("reference_frames") or {}
    p = ROOT / (rc.get("manual_dir") or "selection_v2/reference/faces") / f"{facts['episode']['video_id']}.yaml"
    if not p.exists():
        return {}
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = {}
    for k, spec in (d.get("faces") or d).items():
        if k in ("episode", "note"):
            continue
        times = spec.get("times") if isinstance(spec, dict) else spec        # "I: [t, t]" or "I: {times: [...], look: ...}"
        look = spec.get("look") if isinstance(spec, dict) else None
        out[str(k)] = [{"line": None, "t": round(parse_ts(t)[0], 2), "t_str": fmt_ts(parse_ts(t)[0]), "why": "pinned by hand",
                        "scene_id": None, "text": "", "look": look} for t in (times or [])]
    return out


def manual_looks(facts: dict, cfg: dict) -> dict[str, str]:
    return {k: v[0]["look"] for k, v in manual_references(facts, cfg).items() if v and v[0].get("look")}


def reference_parts(facts: dict, cfg: dict, mp4: Path, media_dir: Path, framer=None, log=None) -> tuple[list[dict], list[dict]]:
    """Media parts (label text + inline JPEG) for the reference faces, and a manifest for the debug file."""
    framer = framer or extract_frame
    names, _ = map_names(facts)
    rc = cfg["stage2"].get("reference_frames") or {}
    parts, manifest = [], []
    from .faces import lookup as bank_lookup
    bank = bank_lookup(facts["episode"].get("series_key"), names, cfg)        # 1. verified faces of this series
    sheet = {k: [dict(f, why=f.get("why") or "cast sheet") for f in v] for k, v in ((facts.get("cast_sheet") or {}).get("chosen") or {}).items()}
    sheet = {k: v for k, v in sheet.items() if k not in bank}                   # 2. this episode's cast sheet
    auto = {k: v for k, v in reference_lines(facts, cfg).items() if k not in bank and k not in sheet}   # 4. fallback
    refs = {**auto, **sheet, **bank, **manual_references(facts, cfg)}         # 3. hand pins override a key
    without = [f"{k} ({names.get(k)})" for k in sorted(names) if k not in refs]
    if without:
        parts.append({"text": "No reference face exists for speaker key(s) " + ", ".join(without) + ": every line of theirs "
                              "is in a segment where the label is in doubt, so judge them from this scene alone."})
    for k in sorted(refs):
        for ref in refs[k]:
            if ref.get("bank"):
                path = Path(ref["path"])
                label = (f"Reference frame {len([m for m in manifest if m['key'] == k]) + 1} for speaker key {k} ({names.get(k)}): "
                         f"VERIFIED face from episode {ref.get('video_id')} at {ref.get('t_str')}.")
            elif ref.get("path") and Path(ref["path"]).exists() and "cast sheet" in str(ref.get("why", "")):
                path = Path(ref["path"])
                label = (f"Reference frame {len([m for m in manifest if m['key'] == k]) + 1} for speaker key {k} ({names.get(k)}): "
                         f"at {ref.get('t_str')}, confirmed by the cast sheet ({ref.get('why')}; {ref.get('note') or ''}).")
            else:
                path = framer(Path(mp4), media_dir, k, ref["t"], int(rc.get("width", 320)), log)
                label = (f"Reference frame {len([m for m in manifest if m['key'] == k]) + 1} for speaker key {k} ({names.get(k)}): at {ref['t_str']}"
                         + (f" during {ref['line']}" if ref.get("line") else "") + f" ({ref['why']}). The frame may also show "
                         f"listeners; the person who appears in all of {k}'s frames is {k}.")
            parts.append({"text": label})
            parts.append(image_part(path, key=f"ref|{k}|{ref.get('video_id') or ''}|{ref.get('line')}|{ref['t']}"))
            manifest.append({"key": k, "name": names.get(k), **ref, "path": str(path)})
    return parts, manifest


# ----------------------------------------------------------------------------------------------- prompt
def load_prompt(path: Path = PROMPT_PATH) -> dict[str, str]:
    return s1b.load_prompt(path)


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def make_instructions(sections: dict, thin: bool, errors: list[str] | None = None) -> str:
    parts = [sections["instructions"]]
    if thin:
        parts.append(sections["thin_diarization"])
    if errors:
        parts.append(sections["retry"].replace("{errors}", "\n".join(f"- {e}" for e in errors)))
    parts.append(sections["output_format"])
    return "\n\n".join(parts)


# ----------------------------------------------------------------------------------------------- validation
def validate(ans: dict, facts: dict, scene: dict) -> list[str]:
    errs: list[str] = []
    all_ids = [r["id"] for r in facts["lines"]]
    rows = scene_lines(facts, scene)
    in_scene = {r["id"] for r in rows}
    dlg = scene_dialogue_ids(facts, scene)
    if int(ans.get("scene_id", -1)) != int(scene["id"]):
        errs.append(f"scene_id must be {scene['id']}")
    beat_lines = set()
    for b in ans.get("beats") or []:
        lid = str(b.get("line") or "")
        if lid not in dlg:
            errs.append(f"beat line {lid!r} is not a dialogue line of this scene ({dlg[0]}-{dlg[-1]})")
        beat_lines.add(lid)
    po = (ans.get("payoff") or {}).get("line")
    if po is not None and po not in dlg:
        errs.append(f"payoff line {po!r} is not a dialogue line of this scene")
    if po:
        beat_lines.add(po)
    # founder, 2026-10-30: situations (start_line <= end_line, scene dialogue lines, in order) and visual beats (inside the scene)
    prev_end = None
    for i, sit in enumerate(ans.get("situations") or [], 1):
        a, b = sit.get("start_line"), sit.get("end_line")
        if a not in dlg or b not in dlg:
            errs.append(f"situations[{i}]: start_line / end_line must be dialogue lines of this scene ({a}, {b})")
            continue
        if dlg.index(a) > dlg.index(b):
            errs.append(f"situations[{i}]: start_line {a} comes after end_line {b}")
        if prev_end is not None and dlg.index(a) <= dlg.index(prev_end):
            errs.append(f"situations[{i}]: starts at {a}, inside or before the previous situation (ends {prev_end})")
        prev_end = b
    for i, vb in enumerate(ans.get("visual_beats") or [], 1):
        try:
            t0, t1 = (stage1_facts.parse_ts(x.strip())[0] for x in str(vb.get("range") or "").split("-"))
        except Exception:
            errs.append(f"visual_beats[{i}]: range {vb.get('range')!r} is not m:ss.s-m:ss.s"); continue
        if not (float(scene["start"]) - 1.0 <= t0 < t1 <= float(scene["end"]) + 1.0):
            errs.append(f"visual_beats[{i}]: range {vb.get('range')} is outside the scene ({scene['start_str']}-{scene['end_str']}) on the episode clock")
    bl = ans.get("button_line")
    if bl:
        if bl not in dlg:
            errs.append(f"button_line {bl!r} is not a dialogue line of this scene")
        elif po and po in all_ids and all_ids.index(bl) <= all_ids.index(po):
            errs.append(f"button_line {bl} must come after the payoff line {po}")
    rx = ans.get("reaction") or {}
    for f_ in ("after_line", "tag_line"):
        v = rx.get(f_)
        if v and v not in in_scene and v not in all_ids:
            errs.append(f"reaction.{f_} {v!r} is not a line id")
    gaps = {(g["prev"], g["next"]) for g in facts["gaps"]}
    for nd in ans.get("non_dialogue") or []:
        a, b = str(nd.get("after_line") or ""), str(nd.get("before_line") or "")
        if (a, b) not in gaps:
            errs.append(f"non_dialogue {a}->{b} is not a pause between two consecutive dialogue lines (see PAUSES INSIDE THE SCENE)")
        elif a not in in_scene or b not in in_scene:
            errs.append(f"non_dialogue {a}->{b} lies outside this scene")
        if nd.get("kind") == "reaction_hold" and nd.get("recommend") == "remove":
            errs.append(f"non_dialogue {a}->{b}: a reaction_hold must never be removed")
        if nd.get("recommend") == "shorten_to" and not nd.get("target_s"):
            errs.append(f"non_dialogue {a}->{b}: shorten_to needs target_s")
    for sk in ans.get("skippable_lines") or []:
        lid = str(sk.get("line") or "")
        if lid not in dlg:
            errs.append(f"skippable line {lid!r} is not a dialogue line of this scene")
        elif lid in beat_lines:
            errs.append(f"skippable line {lid} is a beat line (never skip a beat)")
    _, over = map_names(facts)
    keys = {line_key(r, over) for r in rows if r["kind"] == "speech" and line_key(r, over)}
    seen = set()
    for sp in ans.get("speakers_seen") or []:
        k = str(sp.get("key") or "")
        if k not in keys:
            errs.append(f"speakers_seen key {k!r} does not speak in this scene (keys here: {sorted(keys)})")
        seen.add(k)
        if sp.get("lines"):
            ids, e = expand_lines(sp["lines"], all_ids)
            if e:
                errs.append(f"speakers_seen {k}: {e}")
            elif any(x not in in_scene for x in ids):
                errs.append(f"speakers_seen {k}: lines {sp['lines']} are not all in this scene")
    missing = sorted(keys - seen)
    if missing:
        errs.append(f"speakers_seen has no entry for key(s) {missing}; every speaker key in the scene needs one")
    for n in ans.get("non_story_confirm") or []:
        _, e = expand_lines(n.get("lines", ""), all_ids)
        if e:
            errs.append(f"non_story_confirm: {e}")
    for d in ans.get("other_device_audio") or []:
        if str(d.get("line")) not in all_ids:
            errs.append(f"other_device_audio: unknown line {d.get('line')!r}")
    for f_ in ("natural_open", "natural_close"):
        for lid in (ans.get("clip_hints") or {}).get(f_) or []:
            if lid not in dlg:
                errs.append(f"clip_hints.{f_} {lid!r} is not a dialogue line of this scene")
    return errs


# ----------------------------------------------------------------------------------------------- derivation
def _derive_sheet_inner(ans: dict, facts: dict, scene: dict, stats: dict) -> dict:
    lines = {r["id"]: r for r in facts["lines"]}
    gaps = {(g["prev"], g["next"]): g for g in facts["gaps"]}
    names, over = map_names(facts)

    beats = []
    for b in ans.get("beats") or []:
        r = lines.get(b.get("line"))
        if r:
            beats.append({**b, "t": r["start"], "t_str": fmt_ts(r["start"]), "text": r["text"][:80]})
    po = ans.get("payoff") or {}
    payoff = None
    if po.get("line") in lines:
        r = lines[po["line"]]
        payoff = {"line": po["line"], "t": r["start"], "t_str": fmt_ts(r["start"]), "end": r["end"], "note": po.get("note"),
                  "text": r["text"][:80], "more_than_one": po.get("more_than_one")}
    rx = dict(ans.get("reaction") or {})
    tl = rx.get("tag_line")
    if tl in lines:
        rx["t"] = lines[tl]["start"]; rx["t_str"] = fmt_ts(lines[tl]["start"]); rx["end"] = lines[tl]["end"]
    elif rx.get("after_line") in lines:
        rx["t"] = lines[rx["after_line"]]["end"]; rx["t_str"] = fmt_ts(rx["t"])
    nd = []
    for x in ans.get("non_dialogue") or []:
        g = gaps.get((x.get("after_line"), x.get("before_line")))
        if g:
            nd.append({**x, "start": g["t_prev_end"], "end": g["t_next_start"], "dur_s": g["gap"],
                       "range": f"{fmt_ts(g['t_prev_end'])}-{fmt_ts(g['t_next_start'])}"})
    sk = []
    for x in ans.get("skippable_lines") or []:
        r = lines.get(x.get("line"))
        if r:
            sk.append({**x, "t_str": fmt_ts(r["start"]), "dur_s": round(r["end"] - r["start"], 2), "text": r["text"][:60]})
    speakers = []
    for sp in ans.get("speakers_seen") or []:
        ids = [r["id"] for r in scene_lines(facts, scene) if r["kind"] == "speech" and line_key(r, over) == sp.get("key")]
        if sp.get("lines"):
            sub, e = expand_lines(sp["lines"], [r["id"] for r in facts["lines"]])
            if not e:
                ids = [i for i in ids if i in set(sub)] or sub
        speakers.append({**sp, "map_name": names.get(sp.get("key")), "n_lines": len(ids), "line_ids": ids,
                         "mismatch": (not sp.get("matches_map", True)) or sp.get("voice_source") in ("off_screen", "phone", "tv_or_device")})
    return {"scene_id": scene["id"], "start": scene["start"], "end": scene["end"], "start_str": scene["start_str"],
            "end_str": scene["end_str"], "label": scene.get("label"), "register": ans.get("register"),
            "self_contained": ans.get("self_contained"), "setup": ans.get("setup"), "beats": beats, "payoff": payoff,
            "reaction": rx, "context_required": ans.get("context_required") or [], "callbacks": ans.get("callbacks") or [],
            "dynamic": ans.get("dynamic") or {}, "delivery_notes": ans.get("delivery_notes"),
            "clip_hints": ans.get("clip_hints") or {}, "non_dialogue": nd, "skippable_lines": sk, "speakers_seen": speakers,
            "non_story_confirm": ans.get("non_story_confirm") or [], "other_device_audio": ans.get("other_device_audio") or [],
            "confidence": ans.get("confidence"), "needs_video_default_res": ans.get("needs_video_default_res"),
            "diarization": stats, "thin_diarization": stats["thin"]}


# ----------------------------------------------------------------------------------------------- speaker map
CALL_RE = re.compile(r"\b(phone call|video call|on (a |the |his |her )?call|through (a |the )?(phone|video) ?call|speaking (into|on) (his|her|the|a) (mobile|phone)|calling|on the phone|over the phone|incoming voice)\b", re.I)
PLAYBACK_RE = re.compile(r"\b(playing|recording|recorded|video (clip|playing)|tv|television|radio|song|film|movie|clip|voice ?over|announcement|speaker ?phone recording)\b", re.I)


def is_call_line(reason: str | None, key_is_on_call: bool) -> bool:
    """True when an ``other_device_audio`` entry describes a character on a live phone / video call (a person speaking),
    not audio playing from a device. Playback words win over call words ("a video playing on the call" is playback)."""
    text = reason or ""
    if PLAYBACK_RE.search(text):
        return False
    if CALL_RE.search(text):
        return True
    return key_is_on_call


MANUAL_FIX_DIR = ROOT / "data" / "speakers" / "manual"


def manual_fixes(video_id: str, path: Path | None = None) -> list[dict]:
    """Hand corrections of who speaks a line (data/speakers/manual/<video_id>.yaml: fixes: [{lines, name, key?, note}]).
    founder, 2026-10-29: applied last, so they win over the beat sheets' corrections and survive every rebuild."""
    p = path or (MANUAL_FIX_DIR / f"{video_id}.yaml")
    if not p.exists():
        return []
    import yaml
    return list((yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("fixes") or [])


def build_speaker_map(facts: dict, manual: list[dict] | None = None) -> dict:
    """Speaker map version 2 from the 1B map plus every (non-reverted) correction from the beat sheets, then the manual
    fixes (``manual_fixes``). Reruns and reverts recompute it; 1B's map is never modified."""
    names, over = map_names(facts)
    prev = facts.get("speaker_map") or {}
    reverted = {c["id"] for c in prev.get("corrections") or [] if c.get("reverted")}
    corrections = []
    new_keys: dict[str, str] = dict(prev.get("new_keys") or {})
    all_ids = [r["id"] for r in facts["lines"]]
    for sheet in facts.get("beat_sheets") or []:
        for sp in sheet.get("speakers_seen") or []:
            if not sp.get("mismatch"):
                continue
            who = (sp.get("who") or "").strip() or "unknown person"
            src = sp.get("voice_source") or "unknown"
            known = next((k for k, n in names.items() if n and n.strip().lower() == who.lower()), None)
            if src in ("phone", "tv_or_device", "off_screen") and not sp.get("matches_map", True) is False:
                new_name = who if who.lower() not in ("", "unknown") else "off-screen voice"
            else:
                new_name = who
            new_key = known
            if new_key is None and src == "on_screen":
                new_key = new_keys.get(new_name.lower())
                if new_key is None:
                    new_key = f"s2_new_{len(new_keys) + 1}"
                    new_keys[new_name.lower()] = new_key
            cid = f"S{sheet['scene_id']}-{sp.get('key')}"
            corrections.append({"id": cid, "scene_id": sheet["scene_id"], "key": sp.get("key"), "lines": sp.get("line_ids") or [],
                                "n_lines": len(sp.get("line_ids") or []), "old_name": names.get(sp.get("key")), "new_name": new_name,
                                "new_key": new_key, "voice_source": src, "on_screen": sp.get("on_screen"),
                                "evidence": sp.get("evidence"), "reverted": cid in reverted})
        call_keys = {sp.get("key") for sp in sheet.get("speakers_seen") or [] if sp.get("voice_source") == "phone"}
        for d in sheet.get("other_device_audio") or []:
            lid = d.get("line")
            if lid in all_ids:
                r = next(x for x in facts["lines"] if x["id"] == lid)
                k = line_key(r, over)
                if is_call_line(d.get("reason"), k in call_keys):
                    # a character talking on a phone / video call is a PERSON speaking (either end); the name stays,
                    # the call is a voice-source note only (founder, 2026-10-26: a character on a call, another on their phone)
                    continue
                cid = f"S{sheet['scene_id']}-dev-{lid}"
                corrections.append({"id": cid, "scene_id": sheet["scene_id"], "key": k, "lines": [lid], "n_lines": 1,
                                    "old_name": names.get(k), "new_name": "device audio", "new_key": None,
                                    "voice_source": "tv_or_device", "on_screen": False, "evidence": d.get("reason"), "reverted": cid in reverted})
    if manual is None:
        manual = manual_fixes(((facts.get("episode") or {}).get("video_id")) or "")
    by_line = {r["id"]: r for r in facts["lines"]}
    sheet_of = lambda t: next((sh["scene_id"] for sh in facts.get("beat_sheets") or [] if sh["start"] - 1e-3 <= t <= sh["end"] + 1e-3), None)
    for fx in manual:
        new_name = str(fx.get("name") or "").strip()
        new_key = fx.get("key") or next((k for k, n in names.items() if n and n.strip().lower() == new_name.lower()), None)
        for lid in fx.get("lines") or []:
            if lid not in by_line:
                continue
            cid = f"manual-{lid}"
            corrections.append({"id": cid, "scene_id": sheet_of(by_line[lid]["start"]), "key": line_key(by_line[lid], over), "lines": [lid], "n_lines": 1,
                                "old_name": names.get(line_key(by_line[lid], over)), "new_name": new_name, "new_key": new_key,
                                "voice_source": "manual", "on_screen": None, "evidence": fx.get("note") or "manual correction", "reverted": cid in reverted})
    line_names: dict[str, str] = {}
    line_keys: dict[str, str] = {}
    for c in corrections:
        if c["reverted"]:
            continue
        for lid in c["lines"]:
            line_names[lid] = c["new_name"]
            if c["new_key"]:
                line_keys[lid] = c["new_key"]
    non_story = sorted({lid for c in corrections if not c["reverted"] and c["voice_source"] == "tv_or_device" for lid in c["lines"]})
    for sheet in facts.get("beat_sheets") or []:
        for n in sheet.get("non_story_confirm") or []:
            if n.get("verdict") == "confirm":
                ids, e = expand_lines(n.get("lines", ""), all_ids)
                if not e:
                    non_story = sorted(set(non_story) | set(ids))
    return {"version": 2, "base": names, "corrections": corrections, "line_names": line_names, "line_keys": line_keys,
            "new_keys": new_keys, "non_story_confirmed": non_story,
            "n_corrections": sum(1 for c in corrections if not c["reverted"]),
            "n_lines_corrected": len(line_names)}


# ----------------------------------------------------------------------------------------------- cost
def media_mode(facts: dict, cfg: dict) -> str:
    ct = (facts.get("episode_pass") or {}).get("content_type")
    return "audio" if ct in (cfg["stage2"].get("audio_content_types") or []) else "video"


def estimate_scenes(facts: dict, cfg: dict, prefix_tokens: int | None = None, counter=None) -> dict:
    """Per-scene and total rupees: media seconds x token rate + scene facts + prefix (full price on the first call,
    cached price after) + expected output. ``counter(text)`` counts prefix tokens (Gemini countTokens, free); else an
    offline estimate."""
    r, rate = cfg["rates"], inr_per_usd(cfg)
    mode = media_mode(facts, cfg)
    tps = r["tokens_per_s_audio"] if mode == "audio" else r["tokens_per_s_video_low"]
    if prefix_tokens is None:
        p1 = _prefix1_text(facts, cfg)
        p2 = build_prefix_stage2(facts, p1, all_looks(facts, cfg))
        if counter is None:
            from shotvi.segment.render import estimate_tokens
            counter = estimate_tokens
        prefix_tokens = int(counter(p2))
    out_exp = int(cfg["stage2"]["expected_output_tokens"])
    rc = cfg["stage2"].get("reference_frames") or {}
    n_ref = sum(len(v) for v in reference_lines(facts, cfg).values())
    ref_tok = n_ref * (int(rc.get("tokens_per_image", 264)) + 40)      # measured: ~264 tokens per 320 px frame + its label
    rows = []
    scenes = (facts.get("episode_pass") or {}).get("derived", {}).get("scenes") or []
    for i, sc in enumerate(scenes):
        dur = float(sc["end"]) - float(sc["start"])
        media_tok = int(dur * tps)
        facts_tok = 400 + 25 * len(scene_dialogue_ids(facts, sc)) + ref_tok
        prefix_cost = prefix_tokens * (r["usd_per_1m_input"] if i == 0 else r["usd_per_1m_cached_input"]) / 1e6
        usd = prefix_cost + (media_tok + facts_tok) * r["usd_per_1m_input"] / 1e6 + out_exp * r["usd_per_1m_output"] / 1e6
        rows.append({"scene_id": sc["id"], "range": f"{sc['start_str']}-{sc['end_str']}", "dur_s": round(dur, 1),
                     "media_tokens": media_tok, "facts_tokens": facts_tok, "prefix_tokens": prefix_tokens,
                     "prefix_cached": i > 0, "inr": round(usd * rate, 2), "too_long": dur > float(cfg["stage2"]["max_scene_s"])})
    return {"mode": mode, "prefix_tokens": prefix_tokens, "tokens_per_s": tps, "reference_frames": n_ref, "reference_tokens": ref_tok, "scenes": rows,
            "total_inr": round(sum(x["inr"] for x in rows), 2), "inr_per_usd": rate,
            "note": "prefix at full price on the first call and at the cached rate after (implicit cache); output at the "
                    "configured expected size; media at the low-resolution rate"}


def all_looks(facts: dict, cfg: dict) -> dict[str, str]:
    """key -> look: the series bank first (verified), then this episode's cast sheet."""
    from .faces import looks_for
    names, _ = map_names(facts)
    out = {k: v.get("look") for k, v in ((facts.get("cast_sheet") or {}).get("looks") or {}).items()
           if v.get("look") and "not seen" not in str(v.get("look")).lower()}
    from .stage1_episode_pass import _loose
    by_name = {_loose(n): k for k, n in names.items() if n}
    for lk in (facts.get("identity_pass") or {}).get("looks") or []:          # an older video identity pass, by name
        k = by_name.get(_loose(lk.get("name")))
        if k and lk.get("look"):
            out[k] = lk["look"]
    from .naming_check import speaker_summary
    for c in speaker_summary(facts).get("cast") or []:                       # the video step's looks, by final key
        if c.get("look"):
            out[c["key"]] = c["look"]
    out.update(looks_for(facts["episode"].get("series_key"), names, cfg))
    out.update(manual_looks(facts, cfg))                                     # a hand-pinned look wins
    return out


def _prefix1_text(facts: dict, cfg: dict) -> str:
    p = ROOT / cfg["paths"]["runs_dir"] / facts["episode"]["video_id"] / "prefix.txt"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return build_prefix(facts, load_glossary(facts["episode"].get("series_key"), cfg), cfg)


# ----------------------------------------------------------------------------------------------- run
def _check_1b(facts: dict | None) -> None:
    if not facts or "1b" not in (facts.get("stages") or {}):
        raise RuntimeError("Stage 2 needs Stage 1B output (the scene list and speaker map); run Stage 1B first")
    v = facts["stages"]["1b"].get("version")
    if v not in s1b.COMPATIBLE_VERSIONS:
        raise RuntimeError(f"Stage 1B output is {v}, but the current Stage 1B is {s1b.VERSION}; re-run Stage 1B first")
    if facts["stages"]["1a"].get("version") != stage1_facts.VERSION:
        raise RuntimeError("Stage 1A output is older than the current Stage 1A; re-run Stage 1A, then 1B")


def run(inputs, facts: dict | None, cfg: dict, client=None, scene_ids=None, log=None, cutter=None, uploader=None,
        framer=None, deleter=None) -> dict:
    """Run the beat-sheet call for ``scene_ids`` (default: every scene without a sheet). Media: ``cutter`` /
    ``uploader`` / ``framer`` default to ffmpeg + the Files API (tests pass fakes)."""
    t0 = time.perf_counter()
    log = log or (lambda *a: None)
    _check_1b(facts)
    assert facts is not None
    cutter = cutter or cut_scene
    uploader = uploader or upload
    run_dir = Path(inputs.paths.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    prefix1 = _prefix1_text(facts, cfg)
    prefix2 = build_prefix_stage2(facts, prefix1, all_looks(facts, cfg))
    (run_dir / "prefix_stage2.txt").write_text(prefix2, encoding="utf-8")
    sections = load_prompt()
    schema = load_schema()
    spec = ModelSpec.from_dict(cfg["models"].get("stage2") or cfg["models"]["default"])
    mode = media_mode(facts, cfg)
    if client is None:
        from shotvi.llm.client import Ledger, LLMClient
        client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"))
    rate = inr_per_usd(cfg)
    r = cfg["rates"]
    scenes = (facts.get("episode_pass") or {}).get("derived", {}).get("scenes") or []
    done_ids = {s["scene_id"] for s in facts.get("beat_sheets") or []}
    todo = [sc for sc in scenes if (scene_ids is None and sc["id"] not in done_ids) or (scene_ids is not None and sc["id"] in set(scene_ids))]
    if not todo:
        raise RuntimeError("no scenes to run: " + ("every scene already has a beat sheet (pick scene ids to rerun)" if scene_ids is None else f"scene ids {scene_ids} not found"))
    max_s = float(cfg["stage2"]["max_scene_s"])
    facts = dict(facts)
    sheets = {s["scene_id"]: s for s in facts.get("beat_sheets") or []}
    debug_path = run_dir / "stage2_debug.json"
    debug = json.loads(debug_path.read_text(encoding="utf-8")) if debug_path.exists() else {"scenes": {}}
    debug.update({"model": spec.to_dict(), "system": sections["system"], "prefix_file": "prefix_stage2.txt", "prefix_chars": len(prefix2)})
    per_scene_log = []
    usage_tot = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0}
    media_dir0 = run_dir / cfg["stage2"]["media_dir"]
    ref_parts, ref_manifest = reference_parts(facts, cfg, Path(inputs.paths.mp4), media_dir0, framer, log)
    debug["reference_frames"] = ref_manifest
    for sc in todo:
        ts = time.perf_counter()
        dur = float(sc["end"]) - float(sc["start"])
        if dur > max_s:
            raise RuntimeError(f"scene {sc['id']} is {dur:.0f} s, over stage2.max_scene_s {max_s:.0f}; split it in Stage 1B first")
        stats = scene_stats(facts, sc, cfg)
        media_dir = run_dir / cfg["stage2"]["media_dir"]
        log(f"scene {sc['id']}: cutting {mode} {sc['start_str']}-{sc['end_str']}")
        mpath = cutter(Path(inputs.paths.mp4), media_dir, sc["id"], float(sc["start"]), float(sc["end"]), mode,
                       int(cfg["stage2"]["media_max_height"]), log)
        up = uploader(mpath, mode)
        part = media_part(up, mode, cfg["stage2"].get("media_fps"))
        facts_text = scene_facts_text(facts, sc, cfg, stats)
        attempts = []
        ans = None
        errors: list[str] = []
        for attempt in range(int(cfg["stage2"]["retries"]) + 1):
            instr = make_instructions(sections, stats["thin"], errors if attempt else None)
            # part order: the cached prefix TEXT first (byte-identical across scenes, so the implicit cache can hit),
            # then the scene media, then the reference faces, then this scene's facts and the questions
            media_parts = [{"text": prefix2}, part, {"text": "# REFERENCE FACES: one or two frames per speaker key, cut from lines "
                                                     "where that key's label is best supported. Compare the people in the scene "
                                                     "with these faces; the labels are the map's (a prior), so say so if a face "
                                                     "does not match its label."}] + ref_parts
            prompt = facts_text + "\n\n" + instr
            att = {"n": attempt + 1, "scene_facts": facts_text, "instructions": instr, "errors_shown": errors,
                   "media": {"path": str(mpath), "uri": up.get("uri"), "mode": mode, "n_reference_frames": len(ref_manifest)},
                   "part_order": ["prefix_stage2.txt", "scene media", f"{len(ref_manifest)} reference faces", "scene facts + instructions"]}
            attempts.append(att)
            log(f"scene {sc['id']}: call {attempt + 1}" + (f" (retry with {len(errors)} errors)" if errors else ""))
            try:
                obj, raw = client.generate_json(ROLE, spec, sections["system"], prompt, schema,
                                                meta={"video_id": inputs.vid, "stage": "2", "scene": sc["id"]}, want_raw=True, media=media_parts)
            except BaseException as e:
                att["error"] = f"{type(e).__name__}: {e}"
                debug["scenes"][str(sc["id"])] = {"attempts": attempts}
                write_json(debug_path, debug)
                append_log(cfg, {"episode": inputs.vid, "stage": f"2:scene{sc['id']}", "status": "failed",
                                 "duration_s": round(time.perf_counter() - ts, 1), "note": att["error"][:300]})
                raise
            att["raw_text"] = raw.text
            att["usage"] = {"input_tokens": raw.input_tokens, "output_tokens": raw.output_tokens, "thought_tokens": raw.thought_tokens,
                            "cached_tokens": raw.cached_tokens, "modalities": raw.prompt_modalities if hasattr(raw, "prompt_modalities") else None}
            for k in usage_tot:
                usage_tot[k] += int(getattr(raw, k) or 0)
            errors = validate(obj, facts, sc)
            att["validation"] = errors
            if not errors:
                ans = obj
                break
        if cfg["stage2"].get("delete_upload_after_call", True):
            try:
                from .media import delete_upload
                attempts[-1]["upload_deleted"] = (deleter or delete_upload)(up)
            except Exception as e:  # noqa: BLE001
                attempts[-1]["upload_deleted"] = f"failed: {type(e).__name__}: {e}"
        debug["scenes"][str(sc["id"])] = {"attempts": attempts}
        write_json(debug_path, debug)
        in_tok = sum(a["usage"]["input_tokens"] for a in attempts if a.get("usage"))
        cached = sum(a["usage"]["cached_tokens"] for a in attempts if a.get("usage"))
        out_tok = sum(a["usage"]["output_tokens"] + a["usage"]["thought_tokens"] for a in attempts if a.get("usage"))
        usd = ((in_tok - min(cached, in_tok)) * r["usd_per_1m_input"] + min(cached, in_tok) * r["usd_per_1m_cached_input"] + out_tok * r["usd_per_1m_output"]) / 1e6
        cost = round(usd * rate, 3)
        row = {"scene_id": sc["id"], "attempts": len(attempts), "input_tokens": in_tok, "cached_tokens": cached, "output_tokens": out_tok,
               "cost_inr": cost, "duration_s": round(time.perf_counter() - ts, 1), "media": str(mpath.name), "mode": mode, "thin": stats["thin"]}
        if ans is None:
            append_log(cfg, {"episode": inputs.vid, "stage": f"2:scene{sc['id']}", "status": "failed", "duration_s": row["duration_s"],
                             "input_tokens": in_tok, "output_tokens": out_tok, "cached_tokens": cached, "cost_inr_est": cost,
                             "note": "validation failed after retry: " + " | ".join(errors[:5])})
            raise BeatSheetError(f"scene {sc['id']}: the answer failed validation after {len(attempts)} attempts: " + " | ".join(errors[:6]))
        sheet = derive_sheet(ans, facts, sc, stats)
        sheet["usage"] = row
        sheet["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        sheets[sc["id"]] = sheet
        facts["beat_sheets"] = [sheets[k] for k in sorted(sheets)]
        facts["speaker_map"] = build_speaker_map(facts)
        per_scene_log.append(row)
        append_log(cfg, {"episode": inputs.vid, "stage": f"2:scene{sc['id']}", "status": "ok", "duration_s": row["duration_s"],
                         "input_tokens": in_tok, "output_tokens": out_tok, "cached_tokens": cached, "cost_inr_est": cost,
                         "note": f"{mode} {dur:.0f}s" + (" thin_diarization" if stats["thin"] else "")})
        log(f"scene {sc['id']}: done, ₹{cost:.2f}")
    prev = (facts.get("stages") or {}).get("2") or {}
    prev_usage = prev.get("usage") or {}
    total_cost = round(float(prev_usage.get("cost_inr") or 0) + sum(x["cost_inr"] for x in per_scene_log), 3)
    facts.setdefault("stages", {})["2"] = {
        "version": VERSION, "model": spec.to_dict(), "prompt_file": PROMPT_PATH.name, "config_sha8": cfg.get("_sha8"),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "duration_s": round(time.perf_counter() - t0, 3),
        "media_mode": mode, "scenes_done": sorted(sheets), "n_scenes": len(scenes),
        "usage": {"input_tokens": int(prev_usage.get("input_tokens") or 0) + usage_tot["input_tokens"],
                  "output_tokens": int(prev_usage.get("output_tokens") or 0) + usage_tot["output_tokens"] + usage_tot["thought_tokens"],
                  "cached_tokens": int(prev_usage.get("cached_tokens") or 0) + usage_tot["cached_tokens"], "cost_inr": total_cost},
        "per_scene": (prev.get("per_scene") or []) + per_scene_log,
        "warnings": [], "summary": {"scenes_done": len(sheets), "n_scenes": len(scenes),
                                    "n_corrections": facts["speaker_map"]["n_corrections"],
                                    "n_thin": sum(1 for s in sheets.values() if s.get("thin_diarization"))},
        "prefix_file": "prefix_stage2.txt", "debug_file": "stage2_debug.json"}
    return facts


def derive_sheet(ans: dict, facts: dict, scene: dict, stats: dict) -> dict:
    """``_derive_sheet_inner`` plus ``hook_moments`` (founder, 2026-10-28: lines where a question is left open / a reveal
    lands without an answer; Stage 3 builds hook candidates only from these). Old sheets carry none."""
    sheet = _derive_sheet_inner(ans, facts, scene, stats)
    lid = {r["id"] for r in facts["lines"]}
    sheet["hook_moments"] = [{"line": h["line"], "why": h.get("why", "")} for h in (ans.get("hook_moments") or []) if h.get("line") in lid]
    sheet["button_line"] = ans.get("button_line") if ans.get("button_line") in lid else None    # founder, 2026-10-29; old sheets carry no key
    # founder, 2026-10-30: situations, visual beats (with episode times), hook channels
    by = {r["id"]: r for r in facts["lines"]}
    sheet["situations"] = [{**x, "t": by[x["start_line"]]["start"], "end": by[x["end_line"]]["end"]} for x in ans.get("situations") or []
                           if x.get("start_line") in by and x.get("end_line") in by]
    vbs = []
    for x in ans.get("visual_beats") or []:
        try:
            t0, t1 = (stage1_facts.parse_ts(v.strip())[0] for v in str(x.get("range") or "").split("-"))
        except Exception:
            continue
        vbs.append({**x, "start": round(t0, 3), "end": round(t1, 3)})
    sheet["visual_beats"] = vbs
    if sheet.get("payoff") is not None:
        sheet["payoff"]["hook_channel"] = (ans.get("payoff") or {}).get("hook_channel")
    sheet["hook_moments"] = [dict(h, hook_channel=next((x.get("hook_channel") for x in ans.get("hook_moments") or [] if x.get("line") == h["line"]), None))
                             for h in sheet["hook_moments"]]
    return sheet
