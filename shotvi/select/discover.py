"""Stage 1: moment discovery over overlapping transcript chunks (many cheap calls)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from shotvi.llm import ModelSpec
from shotvi.segment.render import Chunk, fmt_ts

from .prompts import load_prompt

MOMENT_TYPES = ["bold_claim", "myth_vs_fact", "comeback", "incident", "insider_anecdote", "numbers_reveal",
                "practical_tip", "confession", "humour", "one_liner", "emotional", "sharp_question", "other"]

SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_unit": {"type": "integer"},
                    "end_unit": {"type": "integer"},
                    "setup_unit": {"type": "integer", "description": "-1 if none"},
                    "type": {"type": "string", "enum": MOMENT_TYPES},
                    "point": {"type": "string", "description": "One English sentence: X explains why Y / story of how Z."},
                    "hook_quote": {"type": "string"},
                    "payoff_quote": {"type": "string"},
                    "strength": {"type": "integer", "minimum": 1, "maximum": 5},
                    "risk": {"type": "string", "description": "What could stop it working standalone; empty if none."},
                },
                "required": ["start_unit", "end_unit", "setup_unit", "type", "point", "hook_quote", "payoff_quote",
                             "strength", "risk"],
            },
        }
    },
    "required": ["moments"],
}


NOTES_DISCOVER_SCHEMA = {  # discover_notes (= "discover v2"): same moments plus the note each one involves
    "type": "object",
    "properties": {"moments": {"type": "array", "items": {
        "type": "object",
        "properties": {**SCHEMA["properties"]["moments"]["items"]["properties"], "notes_link": {"type": "string"}},
        "required": SCHEMA["properties"]["moments"]["items"]["required"] + ["notes_link"]}}},
    "required": ["moments"],
}


def make_chunks(ctx, cfg) -> list[Chunk]:
    """Chunks over the rendered units. Hidden opening units (teaser montage, music) are replaced upstream by a
    note line; units fully inside other excluded ranges (e.g. greeting / host intro) stay, tagged by segment."""
    d = cfg["discovery"]
    f = d["est_to_real"]
    return ctx.seg.render_chunks(int(d["chunk_tokens"] / f), int(d["overlap_tokens"] / f))


def _qa_lines(ctx, first_unit, last_unit) -> str:
    out = []
    for l in ctx.seg.links:
        if not l.q_units or not l.a_units:
            continue
        q, a0, a1 = l.q_units[-1], l.a_units[0], l.a_units[-1]
        if first_unit <= q <= last_unit and (a1 - a0 >= 0):
            out.append(f"U{q}->U{a0}" + (f"-U{a1}" if a1 != a0 else ""))
    return ", ".join(out) if out else "(none)"


def discover(ctx, cfg, llm) -> tuple[list[dict], list[dict]]:
    """Returns (moments, chunk_info). Moments carry global unit ids and their chunk."""
    notes_txt = getattr(ctx, "notes_text", "") if cfg.get("discover_notes") else ""
    p = load_prompt("discover_notes" if notes_txt else "discover")
    schema = NOTES_DISCOVER_SCHEMA if notes_txt else SCHEMA
    spec = ModelSpec.from_dict(cfg["models"]["candidate_gen"])
    chunks = make_chunks(ctx, cfg)
    n_units = len(ctx.seg.units)
    legend = ctx.seg.render(legend=True).split("\n\n", 1)[0]  # LEGEND (+ intro tag note when present)

    def run(ch: Chunk):
        prompt = p.render(
            legend=legend,
            episode_len=fmt_ts(ctx.seg.transcript.duration),
            part=ch.id + 1, n_parts=len(chunks), span=f"{fmt_ts(ch.start)}-{fmt_ts(ch.end)}",
            qa_links=_qa_lines(ctx, ch.first_unit, ch.last_unit), transcript=ch.text, notes=notes_txt)
        obj = llm.generate_json("candidate_gen", spec, p.system, prompt, schema,
                                meta={"video": ctx.video_id, "chunk": ch.id})
        out = []
        for m in obj.get("moments", []):
            s, e = int(m["start_unit"]), int(m["end_unit"])
            if s > e:
                s, e = e, s
            s, e = max(s, ch.first_unit), min(e, ch.last_unit)
            if not (0 <= s < n_units) or e < s:
                continue
            su = int(m.get("setup_unit", -1))
            if not (0 <= su < s):
                su = -1
            out.append({**m, "start_unit": s, "end_unit": e, "setup_unit": su, "chunk": ch.id})
        return out

    with ThreadPoolExecutor(max_workers=cfg["concurrency"]) as ex:
        results = list(ex.map(run, chunks))
    moments = [m for r in results for m in r]
    info = [{"id": c.id, "units": [c.first_unit, c.last_unit], "start": round(c.start, 1), "end": round(c.end, 1),
             "est_tokens_real": int(c.est_tokens * cfg["discovery"]["est_to_real"]),
             "n_moments": len(r)} for c, r in zip(chunks, results)]
    return moments, info


def merge_moments(moments: list[dict], cfg) -> list[dict]:
    """Collapse nominations of the same moment (typically from chunk overlaps). Keeps the stronger one."""
    thr = cfg["discovery"]["merge_overlap"]
    ms = sorted(moments, key=lambda m: (-m["strength"], m["start_unit"]))
    kept: list[dict] = []
    for m in ms:
        dup = False
        for k in kept:
            inter = min(m["end_unit"], k["end_unit"]) - max(m["start_unit"], k["start_unit"]) + 1
            small = min(m["end_unit"] - m["start_unit"], k["end_unit"] - k["start_unit"]) + 1
            if inter > 0 and inter / small > thr:
                k.setdefault("merged", []).append({kk: m[kk] for kk in ("start_unit", "end_unit", "point", "chunk")})
                dup = True
                break
        if not dup:
            kept.append(dict(m))
    kept.sort(key=lambda m: m["start_unit"])
    for i, m in enumerate(kept):
        m["id"] = f"m{i:03d}"
    return kept


# ------------------------------------------------------------- second, context-aware pass (discover_extra v1)
EXTRA_SCHEMA = {
    "type": "object",
    "properties": {"moments": {"type": "array", "items": {
        "type": "object",
        "properties": {**SCHEMA["properties"]["moments"]["items"]["properties"],
                       "interest_reason": {"type": "string"}},
        "required": SCHEMA["properties"]["moments"]["items"]["required"] + ["interest_reason"]}}},
    "required": ["moments"],
}


def discover_extra(ctx, cfg, llm, existing: list[dict], notes_txt: str, cap: int = 8) -> list[dict]:
    """Nominate only moments the first pass missed, using the editor's notes. Old moments are untouched (their
    downstream calls stay cache hits). Returns at most ``cap`` new moments (ids m1NN), strongest first, none
    overlapping an existing moment by more than 30% of the shorter span."""
    if not notes_txt or cap <= 0:
        return []
    p = load_prompt("discover_extra")
    spec = ModelSpec.from_dict(cfg["models"]["candidate_gen"])
    chunks = make_chunks(ctx, cfg)
    n_units = len(ctx.seg.units)
    legend = ctx.seg.render(legend=True).split("\n\n", 1)[0]

    def run(ch):
        ex = [m for m in existing if m["end_unit"] >= ch.first_unit and m["start_unit"] <= ch.last_unit]
        ex_txt = "\n".join(f"U{m['start_unit']}-U{m['end_unit']}: {m.get('point', '')}" for m in ex) or "(none)"
        prompt = p.render(notes=notes_txt, existing=ex_txt, legend=legend, part=ch.id + 1, n_parts=len(chunks),
                          span=f"{fmt_ts(ch.start)}-{fmt_ts(ch.end)}", transcript=ch.text)
        obj = llm.generate_json("candidate_gen_extra", spec, p.system, prompt, EXTRA_SCHEMA,
                                meta={"video": ctx.video_id, "chunk": ch.id})
        out = []
        for m in obj.get("moments", []):
            s, e = sorted((int(m["start_unit"]), int(m["end_unit"])))
            s, e = max(s, ch.first_unit), min(e, ch.last_unit)
            if not (0 <= s < n_units) or e < s or int(m.get("strength", 0)) < 3:
                continue
            su = int(m.get("setup_unit", -1))
            out.append({**m, "start_unit": s, "end_unit": e, "setup_unit": su if 0 <= su < s else -1,
                        "chunk": ch.id, "pass": "extra"})
        return out

    with ThreadPoolExecutor(max_workers=cfg["concurrency"]) as ex:
        found = [m for r in ex.map(run, chunks) for m in r]

    def ov(a, b):
        inter = min(a["end_unit"], b["end_unit"]) - max(a["start_unit"], b["start_unit"]) + 1
        small = min(a["end_unit"] - a["start_unit"], b["end_unit"] - b["start_unit"]) + 1
        return inter / small if inter > 0 else 0.0

    kept: list[dict] = []
    for m in sorted(found, key=lambda m: (-int(m["strength"]), m["start_unit"])):
        if any(ov(m, o) > 0.3 for o in existing) or any(ov(m, k) > 0.3 for k in kept):
            continue
        kept.append(m)
        if len(kept) >= cap:
            break
    for i, m in enumerate(sorted(kept, key=lambda m: m["start_unit"])):
        m["id"] = f"m{100 + i}"
    return sorted(kept, key=lambda m: m["start_unit"])
