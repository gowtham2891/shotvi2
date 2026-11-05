"""The cached prefix for every Gemini call from Stage 1B onwards (spec 11.2: transcript + episode facts first,
byte-identical across calls, so implicit caching hits). Deterministic text, fixed number formats, stable ordering.

    build_prefix(facts, glossary=None, cfg) -> str      written to runs/selection_v2/<id>/prefix.txt by the stage
"""
from __future__ import annotations

import json

from .stage1_facts import fmt_ts

PREFIX_VERSION = "prefix/3"   # 2026-11-03: no scene-edge proposals, suspects or anchor checks (code decides nothing)


def _line(r: dict) -> str:
    if r["kind"] == "event":
        return f"{r['id']} [{fmt_ts(r['start'])}-{fmt_ts(r['end'])}] (sound) {r['text']}"
    key = r.get("speaker_key") or "?"
    name = r.get("speaker_name") or "unresolved"
    conf = r.get("speaker_confidence")
    cmark = "" if conf in (None, 0.9) else f" c={conf:g}"
    z = " (zero-length)" if r.get("zero_len") else ""
    return f"{r['id']} [{fmt_ts(r['start'])}-{fmt_ts(r['end'])}] [{key} {name}{cmark}]: {r['text']}{z}"


def _line_raw(r: dict) -> str:
    if r["kind"] == "event":
        return f"{r['id']} [{fmt_ts(r['start'])}-{fmt_ts(r['end'])}] (sound) {r['text']}"
    z = " (zero-length)" if r.get("zero_len") else ""
    return f"{r['id']} [{fmt_ts(r['start'])}-{fmt_ts(r['end'])}] [{r.get('speaker') or '?'}]: {r['text']}{z}"


def fix_suggestions(facts: dict) -> list[dict]:
    """Per raw label: the speaker fix's names with line counts (low trust)."""
    by: dict[str, dict[str, int]] = {}
    for r in facts["lines"]:
        if r["kind"] != "speech" or not r.get("speaker"):
            continue
        by.setdefault(r["speaker"], {})
        nm = f"{r.get('speaker_key') or '?'} {r.get('speaker_name') or 'unresolved'}"
        by[r["speaker"]][nm] = by[r["speaker"]].get(nm, 0) + 1
    out = []
    for lab in sorted(by, key=lambda x: (len(x), x)):
        total = sum(by[lab].values())
        out.append({"label": lab, "n_lines": total, "suggestions": sorted(by[lab].items(), key=lambda kv: -kv[1])})
    return out


def build_prefix(facts: dict, glossary: dict | None = None, cfg: dict | None = None) -> str:
    if ((cfg or {}).get("stage1b") or {}).get("prior", "fix") == "raw":
        return build_prefix_raw(facts, glossary, cfg)
    gmin = float(((cfg or {}).get("stage1b") or {}).get("prefix_gap_min_s", 2.0))
    ep = facts["episode"]
    out = [f"# PART 1: EPISODE {ep['video_id']} ({PREFIX_VERSION})",
           f"title: {ep.get('title') or ''}",
           f"series: {ep.get('series') or ''}",
           f"duration: {ep['duration_str']}; dialogue {ep['first_dialogue_str']} to {ep['last_dialogue_str']}",
           "",
           "## LINE TABLE (id [start-end] [speaker key name]: words; (sound) = audio tag)"]
    out += [_line(r) for r in facts["lines"]]
    out += ["", "## SPEAKER KEYS (from the speaker-correction step; a prior, not the truth)"]
    for c in ep.get("speakers") or []:
        aka = f" aka {', '.join(c['aka'])}" if c.get("aka") else ""
        out.append(f"{c['key']}: {c.get('name')} ({c.get('desc') or ''}){aka}; {c.get('n_lines')} lines")
    out += ["", f"## PAUSES >= {gmin:g} s BETWEEN DIALOGUE LINES (prev -> next: length; what fills it; speakers 30 s before -> after)"]
    for g in facts["gaps"]:
        if g["gap"] >= gmin:
            out.append(f"{g['prev']} -> {g['next']}: {g['gap']:.1f} s; {g['filled_by'] or '-'}; "
                       f"{' '.join(g['spk_before']) or '-'} -> {' '.join(g['spk_after']) or '-'}")
    out += ["", "## SOUND TAGS (time, class, tag, after line)"]
    for e in facts["tag_events"]:
        out.append(f"{fmt_ts(e['t'])} {e['class']} {e['tag']} after {e.get('after_content_line') or e['after']}")
    out += ["", "## LINES THE SPEAKER-CORRECTION STEP WAS UNSURE ABOUT (its own confidence)"]
    for x in facts.get("speaker_flags") or []:
        out.append(f"{x['line']} {x['speaker']}: speaker-fix confidence {x['confidence']} ({x['flag']})")
    if glossary:
        out += ["", "## SERIES GLOSSARY FROM EARLIER EPISODES (a prior)",
                json.dumps(glossary, ensure_ascii=False, sort_keys=True, indent=0)]
    out.append("")
    return "\n".join(out)


PREFIX2_VERSION = "prefix2/2"


def build_prefix_stage2(facts: dict, prefix1: str, looks: dict | None = None) -> str:
    """The Stage 2+ cached prefix: the Stage 1B prefix bytes (``prefix.txt``) followed by the 1B results, serialized
    deterministically (speaker map with each person's LOOK where known, scenes, resolutions, possible non-story,
    glossary). Identical across every scene call of an episode. ``looks``: key -> look sentence (series bank /
    cast sheet); when None, the facts' cast_sheet looks are used."""
    ep = facts.get("episode_pass") or {}
    d = ep.get("derived") or {}
    if looks is None:
        looks = {k: v.get("look") for k, v in ((facts.get("cast_sheet") or {}).get("looks") or {}).items() if v.get("look")}
    out = [prefix1.rstrip("\n"), "", f"# PART 1B: EPISODE PASS RESULTS ({PREFIX2_VERSION})",
           f"content_type: {ep.get('content_type')}", "", "## SPEAKER MAP (key: final name; status; role; look as seen on screen)"]
    for sp in sorted(d.get("speakers_table") or [], key=lambda x: x["key"]):
        lk = looks.get(sp["key"])
        out.append(f"{sp['key']}: {sp.get('name')} ({sp.get('status')}); {sp.get('role') or ''}" + (f"; look: {lk}" if lk else ""))
    if d.get("prior") == "raw":
        for m in d.get("merges") or []:
            out.append(f"same person: {' + '.join(m['labels'])} = {m.get('name')} (key {m.get('key')})")
        for sp in d.get("splits") or []:
            out.append(f"split: {sp.get('label')} lines {sp.get('lines')} are {sp.get('name')} (key {sp.get('key')})")
    elif d.get("line_speakers"):
        out.append("reassigned lines: " + ", ".join(f"{k}->{v}" for k, v in sorted(d["line_speakers"].items())))
    out += ["", "## SCENES (id first_line-last_line start-end register: label)"]
    for sc in d.get("scenes") or []:
        out.append(f"{sc['id']}: {sc['first_line']}-{sc['last_line']} {sc['start_str']}-{sc['end_str']} {sc.get('register')}: {sc.get('label')}")
    out += ["", "## SUSPECT RESOLUTIONS"]
    for r in ep.get("suspect_resolutions") or []:
        out.append(f"{r.get('line')}: labelled {r.get('labelled')}, likely {r.get('likely')}: {r.get('reason')}")
    out += ["", "## POSSIBLY NON-STORY LINES (low stakes; confirm from the picture)"]
    for n in ep.get("possible_non_story") or []:
        out.append(f"{n.get('lines')}: {n.get('kind')} [{', '.join(n.get('signals') or [])}]: {n.get('reason')}")
    out += ["", "## SERIES GLOSSARY", json.dumps(ep.get("glossary") or {}, ensure_ascii=False, sort_keys=True, indent=0), ""]
    return "\n".join(out)


PREFIX_RAW_VERSION = "prefix/3-raw"


def build_prefix_raw(facts: dict, glossary: dict | None = None, cfg: dict | None = None) -> str:
    """The 1B prefix with RAW diarization labels as the prior (founder, 2026-10-26). The speaker fix's names and every
    1A fact that was grouped by the fix's keys sit in a LOW-TRUST block at the end."""
    gmin = float(((cfg or {}).get("stage1b") or {}).get("prefix_gap_min_s", 2.0))
    ep = facts["episode"]
    out = [f"# PART 1: EPISODE {ep['video_id']} ({PREFIX_RAW_VERSION})",
           f"title: {ep.get('title') or ''}", f"series: {ep.get('series') or ''}",
           f"duration: {ep['duration_str']}; dialogue {ep['first_dialogue_str']} to {ep['last_dialogue_str']}",
           "", "## LINE TABLE (id [start-end] [raw speaker label]: words; (sound) = audio tag)"]
    out += [_line_raw(r) for r in facts["lines"]]
    sug = fix_suggestions(facts)
    out += ["", "## RAW LABELS (speech-to-text diarization; one label may cover two people, one person may have several labels)"]
    out += [f"{x['label']}: {x['n_lines']} lines" for x in sug]
    out += ["", f"## PAUSES >= {gmin:g} s BETWEEN DIALOGUE LINES (prev -> next: length; what fills it)"]
    for g in facts["gaps"]:
        if g["gap"] >= gmin:
            out.append(f"{g['prev']} -> {g['next']}: {g['gap']:.1f} s; {g['filled_by'] or '-'}")
    out += ["", "## SOUND TAGS (time, class, tag, after line)"]
    for e in facts["tag_events"]:
        out.append(f"{fmt_ts(e['t'])} {e['class']} {e['tag']} after {e.get('after_content_line') or e['after']}")
    out += ["", "## LOW-TRUST SUGGESTIONS: an earlier TEXT-ONLY step guessed these names per raw label (key name x lines). It merged a",
            "stranger into a main character in another episode; use only as a hint, never as evidence."]
    out += [f"{x['label']}: " + "; ".join(f"{nm} x{c}" for nm, c in x["suggestions"]) for x in sug]
    if glossary:
        out += ["", "## SERIES GLOSSARY FROM EARLIER EPISODES (a prior)", json.dumps(glossary, ensure_ascii=False, sort_keys=True, indent=0)]
    out.append("")
    return "\n".join(out)
