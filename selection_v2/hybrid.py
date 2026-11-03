"""NOT USED by the pipeline since 2026-11-02 (the identity pass answers per line; the naming check decides). Kept for
older facts files.

Hybrid speaker map (founder, 2026-10-26): the TEXT pass (Stage 1B) owns every line's label and the scenes; the VIDEO
identity pass owns per-label identity. Findings are applied in code on top of the text pass, each with its source, so
the accuracy line can show text / combined.

A finding (``facts["identity_findings"]``) is one of:
  {"kind": "new_person",  "scene_id": 13, "key": "I", "name": "Delivery Customer", "source": "video_pass", "evidence": ...}
      every line of fix key K inside that text-pass scene becomes a new person
  {"kind": "relabel",     "scene_id": 7,  "key": "D", "to_key": "F", "source": ..., ...}
      every line of fix key K inside that scene belongs to existing key to_key
  {"kind": "merge_keys",  "keys": ["K", "M"], "name": "<Name>", "source": "founder", ...}
      two fix keys are one person; the first key is kept
  {"kind": "scene_edge",  "t": 303.3, "source": ..., ...}
      split the text-pass scene containing t at the gap-table pause nearest t
Nothing here touches individual lines by id: a finding names a label (fix key) and a scene, or two labels, or a time.
"""
from __future__ import annotations

import copy
import time

from .stage1_facts import fmt_ts


def _lines_of(facts: dict, key: str, scene: dict) -> list[str]:
    ls = ((facts.get("episode_pass") or {}).get("derived") or {}).get("line_speakers_text") or {}
    out = []
    for r in facts["lines"]:
        if r["kind"] != "speech" or not (scene["start"] - 1e-6 <= r["start"] <= scene["end"] + 1e-6):
            continue
        k = ls.get(r["id"]) or r.get("speaker_key")
        if k == key:
            out.append(r["id"])
    return out


def _scene_containing(scenes: list[dict], t: float) -> dict | None:
    return next((s for s in scenes if s["start"] - 1e-6 <= t <= s["end"] + 1e-6), None)


def fold_cast_sheet(f: dict, keep: str, others: list[str]) -> None:
    """After a merge, the cast sheet's candidate frames, chosen frames and look of the merged keys belong to the kept
    key (so Stage 2 references and the series-bank Save see one person). Idempotent."""
    cs = f.get("cast_sheet")
    if not cs:
        return
    name = next((s["name"] for s in (f.get("episode_pass") or {}).get("speakers") or [] if s["key"] == keep), None)
    for fr in cs.get("frames") or []:
        if fr.get("key") in others:
            fr["key"] = keep
            if name:
                fr["name"] = name
    chosen = cs.get("chosen") or {}
    for o in others:
        for fr in chosen.pop(o, []) or []:
            fr["key"] = keep
            if name:
                fr["name"] = name
            chosen.setdefault(keep, []).append(fr)
    for fr in chosen.get(keep) or []:                 # idempotent: frames moved on an earlier pass
        if fr.get("key") in others:
            fr["key"] = keep
            if name:
                fr["name"] = name
    if keep in chosen:
        seen, uniq = set(), []
        for fr in sorted(chosen[keep], key=lambda x: float(x.get("t", 0))):
            if fr.get("t") not in seen:
                seen.add(fr.get("t")); uniq.append(fr)
        chosen[keep] = uniq
    looks = cs.get("looks") or {}
    for o in others:
        lk = looks.pop(o, None)
        if lk and not (looks.get(keep) or {}).get("look"):
            looks[keep] = lk


def rekey_beat_sheets(f: dict) -> list[str]:
    """Scene ids are positional; after a scene edge is added, re-key existing Stage 2 beat sheets (and speaker-map
    corrections) by their start time. A sheet whose scene bounds no longer exist is dropped. Returns notes."""
    scenes = ((f.get("episode_pass") or {}).get("derived") or {}).get("scenes") or []
    by_start = {round(float(s["start"]), 1): s["id"] for s in scenes}
    notes = []
    kept = []
    for b in f.get("beat_sheets") or []:
        st = b.get("start")
        if st is None:
            st = next((s["start"] for s in scenes if s["start_str"] == b.get("start_str")), None)
        sid = by_start.get(round(float(st), 1)) if st is not None else None
        if sid is None:
            notes.append(f"beat sheet for old scene {b.get('scene_id')} dropped: its bounds no longer match a scene"); continue
        if sid != b.get("scene_id"):
            notes.append(f"beat sheet scene {b.get('scene_id')} -> {sid} ({b.get('start_str')})")
            old_id = b.get("scene_id"); b["scene_id"] = sid
            for c in (f.get("speaker_map") or {}).get("corrections") or []:
                if c.get("scene_id") == old_id and c.get("_rekeyed") is None:
                    c["scene_id"], c["_rekeyed"] = sid, old_id
        kept.append(b)
    if "beat_sheets" in f:
        f["beat_sheets"] = kept
    for c in (f.get("speaker_map") or {}).get("corrections") or []:
        c.pop("_rekeyed", None)
    return notes


def apply_findings(facts: dict, findings: list[dict]) -> dict:
    """Return a copy of facts with the findings applied on top of the TEXT pass. The text pass's own line map is kept
    as ``derived.line_speakers_text``; ``derived.line_speakers`` becomes the combined map; ``episode.speakers`` /
    ``episode_pass.speakers`` gain new people; scenes may be split. Every applied finding is recorded with its effect."""
    f = copy.deepcopy(facts)
    ep = f.get("episode_pass")
    if not ep:
        raise RuntimeError("the hybrid needs the Stage 1B text pass first")
    d = ep["derived"]
    if "line_speakers_text" not in d:
        d["line_speakers_text"] = dict(d.get("line_speakers") or {})
    combined = dict(d["line_speakers_text"])
    speakers = {s["key"]: s for s in ep["speakers"]}
    table = {s["key"]: s for s in d["speakers_table"]}
    scenes = d["scenes"]
    applied = []
    n_new = sum(1 for k in speakers if k.startswith("hy_"))
    gaps = f["gaps"]
    for fd in findings:
        kind = fd.get("kind")
        rec = {**fd, "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if kind in ("new_person", "relabel"):
            sc = next((s for s in scenes if s["id"] == fd.get("scene_id")), None)
            if sc is None:
                rec["effect"] = f"no text-pass scene {fd.get('scene_id')}; skipped"
                applied.append(rec); continue
            ids = _lines_of(f, fd["key"], sc)
            if kind == "new_person":
                n_new += 1
                nk = fd.get("new_key") or f"hy_{n_new}"
                speakers[nk] = {"key": nk, "name": fd["name"], "role": fd.get("role") or "", "status": "new",
                                "evidence_against_prior": fd.get("evidence") or "", "evidence": f"identity finding ({fd.get('source')})"}
                table[nk] = {"key": nk, "prior_name": table.get(fd["key"], {}).get("name"), "name": fd["name"], "role": fd.get("role") or "",
                             "status": "new", "changed": True, "n_lines": len(ids), "evidence": f"identity finding ({fd.get('source')})",
                             "evidence_against_prior": fd.get("evidence") or ""}
                target = nk
            else:
                target = fd["to_key"]
                if target not in speakers:
                    rec["effect"] = f"unknown to_key {target}; skipped"
                    applied.append(rec); continue
            for lid in ids:
                combined[lid] = target
            rec["effect"] = f"{len(ids)} lines of {fd['key']} in scene {sc['id']} ({sc['start_str']}-{sc['end_str']}) -> {target} ({speakers[target]['name']})"
            rec["lines"] = ids
        elif kind == "skipped_merge":
            rec["effect"] = f"not applied: {fd.get('why')}"
            applied.append(rec); continue
        elif kind == "merge_keys":
            keep, *others = fd["keys"]
            if keep not in speakers:
                rec["effect"] = f"unknown key {keep}; skipped"; applied.append(rec); continue
            n = 0
            for r in f["lines"]:
                if r["kind"] == "speech" and (combined.get(r["id"]) or r.get("speaker_key")) in others:
                    combined[r["id"]] = keep; n += 1
            if fd.get("name"):
                speakers[keep]["name"] = fd["name"]; table[keep]["name"] = fd["name"]
            for o in others:
                if o in speakers:
                    speakers[o]["status"] = "merged"; table[o]["status"] = "merged"; table[o]["changed"] = True
                    table[o]["merged_into"] = keep
            fold_cast_sheet(f, keep, others)
            rec["effect"] = f"{n} lines of {others} -> {keep} ({speakers[keep]['name']})"
        elif kind == "scene_edge":
            t = float(fd["t"])
            sc = _scene_containing(scenes, t)
            if sc is None or not (sc["start"] + 1 < t < sc["end"] - 1):
                rec["effect"] = f"no text-pass scene strictly contains {fmt_ts(t)}; skipped"; applied.append(rec); continue
            g = min((g for g in gaps if sc["start"] <= g["t_prev_end"] and g["t_next_start"] <= sc["end"]),
                    key=lambda g: abs(g["t_next_start"] - t), default=None)
            if g is None or abs(g["t_next_start"] - t) > 3.0:
                rec["effect"] = f"no gap-table pause within 3 s of {fmt_ts(t)} inside scene {sc['id']}; skipped"; applied.append(rec); continue
            first, last = sc["first_line"], sc["last_line"]
            new_sc = {**sc, "id": sc["id"] + 0.5, "first_line": g["next"], "start": g["t_next_start"], "start_str": fmt_ts(g["t_next_start"]),
                      "label": f"{sc.get('label')} (part 2)", "origin": f"added by {fd.get('source')}", "proposals_confirmed": [], "proposals_merged": []}
            sc["last_line"], sc["end"], sc["end_str"] = g["prev"], g["t_prev_end"], fmt_ts(g["t_prev_end"])
            scenes.append(new_sc)
            scenes.sort(key=lambda s: s["start"])
            for i, s2 in enumerate(scenes, 1):
                s2["id"] = i
            for note in rekey_beat_sheets(f):
                rec.setdefault("notes", []).append(note)
            rec["effect"] = f"scene split at {fmt_ts(g['t_next_start'])} ({g['prev']} | {g['next']})"
        else:
            rec["effect"] = f"unknown finding kind {kind!r}; skipped"
        applied.append(rec)
    d["line_speakers"] = {k: v for k, v in combined.items()}
    ep["speakers"] = list(speakers.values())
    d["speakers_table"] = list(table.values())
    for s in d["speakers_table"]:
        s["n_lines"] = sum(1 for r in f["lines"] if r["kind"] == "speech" and (combined.get(r["id"]) or r.get("speaker_key")) == s["key"])
    d["scenes"] = scenes
    d["counts"]["n_scenes"] = len(scenes)
    d["counts"]["n_identity_findings"] = len([a for a in applied if "skipped" not in a.get("effect", "")])
    f["identity_findings"] = (f.get("identity_findings") or []) + applied
    f.setdefault("stages", {}).setdefault("1b", {}).setdefault("warnings", [])
    return f


def findings_from_identity_pass(answer: dict, facts: dict, alias: dict[str, str] | None = None, cast: list[dict] | None = None) -> list[dict]:
    """Translate an identity-pass answer (per label x scene) into findings. ``cast_by_scene`` entries name a raw label
    inside a text-pass scene; code maps that to the fix key that owns most of those lines there."""
    d = ((facts.get("episode_pass") or {}).get("derived") or {})
    scenes = d.get("scenes") or []
    ls = d.get("line_speakers_text") or d.get("line_speakers") or {}
    names = {s["key"]: s.get("name") for s in (facts.get("episode_pass") or {}).get("speakers") or []}
    out = []
    for e in answer.get("cast_by_scene") or []:
        sc = next((s for s in scenes if s["id"] == e.get("scene_id")), None)
        if sc is None:
            continue
        rows = [r for r in facts["lines"] if r["kind"] == "speech" and r.get("speaker") == e.get("label")
                and sc["start"] - 1e-6 <= r["start"] <= sc["end"] + 1e-6]
        if not rows:
            continue
        cnt: dict[str, int] = {}
        for r in rows:
            k = ls.get(r["id"]) or r.get("speaker_key")
            cnt[k] = cnt.get(k, 0) + 1
        key = max(cnt.items(), key=lambda kv: kv[1])[0]
        who = (e.get("name") or "").strip()
        if not who or who.lower() == "unresolved":
            continue
        who = (alias or {}).get(who.lower(), who)      # 2026-10-30: a generated (spoken) name maps back to the text pass's name
        same = names.get(key, "") or ""
        if same.strip().lower() == who.lower():
            continue                                   # agrees with the text pass: nothing to apply
        to_key = next((k for k, n in names.items() if n and n.strip().lower() == who.lower()), None)
        base = {"scene_id": sc["id"], "key": key, "raw_label": e.get("label"), "source": "video_pass", "evidence": e.get("evidence")}
        if to_key:
            out.append({"kind": "relabel", "to_key": to_key, **base})
        else:
            out.append({"kind": "new_person", "name": who, **base})
    for g in answer.get("same_person") or []:
        labs = [x for x in g.get("labels") or []]
        keys = []
        for lab in labs:
            cnt = {}
            for r in facts["lines"]:
                if r["kind"] == "speech" and r.get("speaker") == lab:
                    k = ls.get(r["id"]) or r.get("speaker_key"); cnt[k] = cnt.get(k, 0) + 1
            if cnt:
                keys.append(max(cnt.items(), key=lambda kv: kv[1])[0])
        keys = list(dict.fromkeys(keys))
        # 2026-10-30: never merge labels the generated cast lists as different people (a label that is mostly someone
        # else, sharing one line with this person, is a line-level slip, not the same face)
        people = {(c.get("name_latin") or c.get("name") or "").strip().lower() for c in cast or [] if set(c.get("labels") or []) & set(labs)}
        if cast and len(people) >= 2 and len(keys) >= 2:
            out.append({"kind": "skipped_merge", "keys": keys, "labels": labs, "name": g.get("name"), "source": "video_pass",
                        "why": f"the generated cast lists different people on these labels: {sorted(people)}", "evidence": g.get("evidence")})
            continue
        if len(keys) >= 2:
            out.append({"kind": "merge_keys", "keys": keys, "name": g.get("name"), "source": "video_pass", "evidence": g.get("evidence")})
    return out
