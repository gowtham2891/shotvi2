"""Plain-language presentation of a Stage 1A document for the debug app (no Streamlit here, so it is testable):

  plain_summary   one paragraph: what was measured, how many pauses / tags / scene edges / speakers, time, rupees
  timeline_rows   the rows the timeline draws: scene-edge proposals, laugh tags, holds, golden-set moments
  sanity_checks   automatic green / red checks that need no reference (red = a code bug, not a judgment call)
"""
from __future__ import annotations

from .config import series_for
from .stage1_facts import fmt_ts


def holds_of(facts: dict, cfg: dict) -> list[dict]:
    """Dialogue pauses >= hold_min_s with nothing or only silence / a sigh under them (no music)."""
    hm = float(cfg["stage1a"]["summary"]["hold_min_s"])
    return [g for g in facts["gaps"] if g["gap"] >= hm and (g["filled_by"] in (None, "silence", "sigh"))]


def genre_of(facts: dict, cast_cfg: dict) -> str | None:
    ep = facts["episode"]
    key = ep.get("series_key")
    series = (cast_cfg.get("series") or {}).get(key) if key else None
    if series is None:
        key, series = series_for(ep["video_id"], ep.get("title") or "", cast_cfg)
    return (series or {}).get("genre")


def suspicious_speakers(facts: dict) -> list[str]:
    """Short labels of what looks off: 'D@<line id>' for a flagged line, 'H (1 line)' for a tiny isolated speaker."""
    out = []
    for x in facts["structural_suspects"]:
        if x.get("label_space", "fix") == "fix":
            out.append(f"{x['speaker']}@{x['line']}" if x.get("line") else f"{x['speaker']} ({x['reason']})")
    out += [f"{f['speaker']}@{f['line']}" for f in facts["speaker_flags"] if f["flag"] in ("suspect", "unresolved")]
    out += [f"{d['key']} ({d['n_lines']} line{'s' if d['n_lines'] != 1 else ''}, one segment)"
            for d in facts.get("speaker_segments") or [] if d["isolated"] and d["n_lines"] <= 3]
    return sorted(set(out))


def plain_summary(facts: dict, cfg: dict) -> str:
    s = facts["stages"]["1a"]
    sm, ep = s["summary"], facts["episode"]
    lp = float(cfg["stage1a"]["summary"]["long_pause_s"])
    n_long = sum(g["gap"] >= lp for g in facts["gaps"])
    laughs = sum(e["class"] == "laugh" for e in facts["tag_events"])
    cens = sum(e["class"] == "censored" for e in facts["tag_events"])
    holds = len(holds_of(facts, cfg))
    n_spk = len(ep.get("speakers") or [])
    sus = suspicious_speakers(facts)
    cost = s["usage"].get("cost_inr", 0.0)
    text = (f"Measured {ep['duration_str'].split('.')[0]} of video (dialogue {ep['first_dialogue_str'].split('.')[0]} to "
            f"{ep['last_dialogue_str'].split('.')[0]}). Found {sm['n_gaps']} pauses between lines ({n_long} longer than "
            f"{lp:g} s), {sm['n_tag_events']} sound tags ({laughs} laughs, {holds} holds"
            + (f", {cens} censored" if cens else "") + f"), {sm['n_proposals']} possible scene changes, "
            f"{n_spk} speakers" + (f" ({len(sus)} look suspicious: {', '.join(sus[:4])}{'…' if len(sus) > 4 else ''})" if sus else "") + ". "
            f"Took {s['duration_s']:.1f} s, ₹{cost:.0f}.")
    if not facts["layers"].get("asr_fix"):
        text += " Text is raw Scribe (no ASR correction layer)."
    return text


def timeline_rows(facts: dict, cfg: dict, gold_moments: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """(marks, spans): point / rule marks and the golden-set spans for the timeline."""
    marks = []
    for p in facts["boundary_proposals"]:
        marks.append({"t": p["t"], "kind": "scene edge", "label": p["t_str"], "line": p["next"],
                      "detail": f"{p.get('selected_by', 'score')} {p['score']:.2f}: " + "; ".join(p["evidence"])})
    for sc in ((facts.get("episode_pass") or {}).get("derived") or {}).get("scenes") or []:
        marks.append({"t": sc["start"], "kind": "scene (1B)", "label": sc["start_str"], "line": sc["first_line"],
                      "detail": f"scene {sc['id']} {sc.get('label') or ''} [{sc.get('register') or ''}] {sc['start_str']}-{sc['end_str']} "
                                f"({sc.get('origin')})"})
    for e in facts["tag_events"]:
        if e["class"] == "laugh":
            marks.append({"t": e["t"], "kind": "laugh", "label": fmt_ts(e["t"]), "line": e.get("after_content_line") or e["after"],
                          "detail": f"{e['tag']} after {e.get('after_content_line') or e['after']}"})
    for g in holds_of(facts, cfg):
        mid = (g["t_prev_end"] + g["t_next_start"]) / 2
        marks.append({"t": round(mid, 3), "kind": "hold", "label": f"{fmt_ts(g['t_prev_end'])}-{fmt_ts(g['t_next_start'])}",
                      "line": g["prev"], "detail": f"{g['gap']:.1f} s pause after {g['prev']}" + (f" ({g['filled_by']})" if g["filled_by"] else "")})
    spans = [{"start": m["start"], "end": m["end"], "tier": m["tier"], "label": f"{m['id']} {m['tier']}: {m.get('label', '')[:60]}"}
             for m in gold_moments or []]
    return marks, spans


def sanity_checks(facts: dict, cfg: dict, cast_cfg: dict) -> list[dict]:
    """[{name, ok: True|False|None, detail}]; ok None = not applicable (grey)."""
    out = []
    gaps = {(g["prev"], g["next"]) for g in facts["gaps"]}
    props = facts["boundary_proposals"]
    bad = [p["t_str"] for p in props if (p["prev"], p["next"]) not in gaps]
    out.append({"name": "every scene-edge proposal sits on a pause from the gap table", "ok": not bad,
                "detail": f"{len(props)} proposals checked" if not bad else f"not in the gap table: {bad}"})
    speech = [r for r in facts["lines"] if r["kind"] == "speech" and not r["zero_len"]]
    inside = []
    for p in props:
        for r in speech:
            if r["start"] + 1e-6 < p["t"] < r["end"] - 1e-6:
                inside.append(f"{p['t_str']} inside {r['id']} ({fmt_ts(r['start'])}-{fmt_ts(r['end'])})")
                break
    out.append({"name": "no proposal time falls inside a dialogue line", "ok": not inside,
                "detail": "all proposals sit between lines" if not inside else "; ".join(inside[:5])})
    genre = genre_of(facts, cast_cfg)
    laughs = sum(e["class"] == "laugh" for e in facts["tag_events"])
    if genre == "comedy":
        out.append({"name": "a comedy episode has laugh tags", "ok": laughs > 0, "detail": f"{laughs} laugh tags"})
    else:
        out.append({"name": "a comedy episode has laugh tags", "ok": None,
                    "detail": f"genre unknown for this series (set `genre` in cast_lists.yaml); {laughs} laugh tags"})
    any_line, content = {}, {}
    for r in facts["lines"]:                      # zero-length lines count as lines here (they are filler)
        if r["kind"] != "speech":
            continue
        k = r.get("speaker_key")
        any_line[k] = any_line.get(k, 0) + 1
        if not r.get("filler") and not r.get("zero_len"):
            content[k] = content.get(k, 0) + 1
    cast = facts["episode"].get("speakers") or []
    declared = {c["key"]: c.get("n_lines") for c in cast}
    # red only when the speaker fix says a key HAS lines and the join found none (a code bug); a key the fix declared
    # but never used (n_lines 0) and a key with only filler lines are speaker-fix artefacts, reported as information
    empty = [k for k, n in declared.items() if (n is None or n > 0) and not any_line.get(k)]
    unused = [k for k, n in declared.items() if n == 0]
    filler_only = [k for k in declared if any_line.get(k) and not content.get(k)]
    notes = []
    if unused:
        notes.append(f"declared by the speaker fix but given no lines (not a bug): {unused}")
    if filler_only:
        notes.append(f"filler-only keys (a speaker-fix artefact, not a bug): {filler_only}")
    out.append({"name": "every speaker key the fix uses has lines in the line table", "ok": not empty,
                "detail": (f"{len(declared)} keys" if not empty else f"the fix counts lines for {empty} but none were joined")
                + ("; " + "; ".join(notes) if notes else "")})
    mins = facts["episode"]["duration"] / 60.0
    lo, hi = cfg["sanity"]["proposals_per_min"]
    nmin, nmax = max(1, round(lo * mins)), round(hi * mins)
    out.append({"name": f"scene-edge count is plausible for {mins:.0f} min ({nmin}-{nmax})", "ok": nmin <= len(props) <= nmax,
                "detail": f"{len(props)} proposals"})
    ids = [r["id"] for r in facts["lines"]]
    starts = [r["start"] for r in facts["lines"]]
    out.append({"name": "line ids are unique and in time order", "ok": len(set(ids)) == len(ids) and starts == sorted(starts),
                "detail": f"{len(ids)} lines"})
    return out
