"""Stage 6: hard-rule gate, near-duplicate removal (Q4), similar-theme links (Q8), ranking (Q5), tiers (Q2, Q6)."""
from __future__ import annotations

from shotvi.llm import ModelSpec
from shotvi.segment.render import fmt_ts

from .clip import Candidate, kept_words
from .prompts import load_prompt

THEME_SCHEMA = {
    "type": "object",
    "properties": {"groups": {"type": "array", "items": {
        "type": "object",
        "properties": {"ids": {"type": "array", "items": {"type": "string"}},
                       "relation": {"type": "string", "enum": ["similar_theme", "sequel"]}},
        "required": ["ids", "relation"]}}},
    "required": ["groups"],
}


def sort_key(ctx, c: Candidate):
    s = c.scores
    return (-s["overall"], -s["hook"], c.pieces and min(p[0] for p in c.pieces))


def near_duplicates(ctx, cfg, cands: list[Candidate]) -> None:
    """Q4: if two candidates share > dup_overlap of the smaller one's kept words, hide the lower-scored one."""
    thr = cfg["thresholds"]["dup_overlap"]
    order = sorted(cands, key=lambda c: sort_key(ctx, c))
    kept: list[tuple[Candidate, set]] = []
    for c in order:
        kw = set(kept_words(c.pieces))
        dup = None
        for k, kwk in kept:
            if len(kw & kwk) / max(1, min(len(kw), len(kwk))) > thr:
                dup = k
                break
        if dup is not None:
            c.hidden.append(f"Q4: near-duplicate of {dup.id} (>{thr:.0%} shared kept words)")
        else:
            kept.append((c, kw))


def link_themes(ctx, cfg, llm, shown: list[Candidate]) -> dict:
    """Q8: flag the lower-ranked clip of a similar-theme pair and link them. Never hides."""
    if len(shown) < 2 or not cfg["models"].get("dedupe"):
        return {"groups": []}
    p = load_prompt("themes")
    spec = ModelSpec.from_dict(cfg["models"]["dedupe"])
    rows = []
    for c in shown:
        kw = kept_words(c.pieces)
        opening = " ".join(ctx.words[i].text for i in kw[:18])
        rows.append(f"{c.id} | {fmt_ts(ctx.words[c.pieces[0][0]].start)} | {(c.write or {}).get('title', '')} | "
                    f"{c.info.get('point') or c.moment.get('point')} | {opening}")
    obj = llm.generate_json("dedupe", spec, p.system, p.render(clips="\n".join(rows)), THEME_SCHEMA,
                            meta={"video": ctx.video_id})
    by_id = {c.id: c for c in shown}
    rank = {c.id: k for k, c in enumerate(shown)}
    for g in obj.get("groups", []):
        ids = [i for i in dict.fromkeys(g.get("ids", [])) if i in by_id]
        if len(ids) < 2:
            continue
        ids.sort(key=lambda i: rank[i])
        rel = g.get("relation", "similar_theme")
        top = ids[0]
        for other in ids[1:]:
            c = by_id[other]
            if rel == "similar_theme":
                c.flags.add("similar_theme")
            c.related.append({"id": top, "relation": rel})
            by_id[top].related.append({"id": other, "relation": rel})
    return obj


def tier_of(cfg, c: Candidate) -> str:
    t = cfg["thresholds"]
    o = c.scores["overall"]
    if o >= t["t_rec"] and "long_range_stitch" not in c.flags:
        return "recommended"
    if o >= t["t_show"]:
        return "worth_a_look"
    return "below_threshold"


# ---------------------------------------------------------------- list-wise episode ranking (rank v1)
RANK_SCHEMA = {
    "type": "object",
    "properties": {
        "order": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"},
                           "tier": {"type": "string", "enum": ["recommended", "worth_a_look", "hide"]},
                           "reason": {"type": "string"}},
            "required": ["id", "tier", "reason"]}},
        "groups": THEME_SCHEMA["properties"]["groups"],
    },
    "required": ["order", "groups"],
}
TIER_LEVEL = {"recommended": 2, "worth_a_look": 1, "below_threshold": 0, "hide": -1}


def _first_last(ctx, c: Candidate, n: int = 22) -> tuple[str, str]:
    kw = kept_words(c.pieces)
    first = " ".join(ctx.words[i].text for i in kw[:n])
    last = " ".join(ctx.words[i].text for i in kw[-n:])
    return first, last


def rank_episode(ctx, cfg, llm, cands: list[Candidate]) -> dict:
    """One call sees every candidate side by side; returns {"order": [...], "groups": [...]} (validated)."""
    if not cands or not cfg["models"].get("ranker"):
        return {"order": [], "groups": []}
    p = load_prompt("rank")
    spec = ModelSpec.from_dict(cfg["models"]["ranker"])
    rows = []
    for c in sorted(cands, key=lambda c: c.pieces[0][0]):
        j = {**(c.judge or {}), **(c.write or {})}
        s = c.scores
        first, last = _first_last(ctx, c)
        dur = sum(ctx.words[b].end - ctx.words[a].start for a, b in c.pieces)
        rows.append(f"{c.id} | {dur:.0f}s | {j.get('category', 'other')} | "
                    f"{s['hook']}/{s['standalone']}/{s['payoff']}/{s['value_emotion']}/{s['overall']} | "
                    f"{','.join(sorted(c.flags)) or '-'}\n"
                    f"  title: {j.get('title', '')}\n  first words: {first} ...\n  last words: ... {last}\n"
                    f"  reviewer: {j.get('critique', '')}")
    obj = llm.generate_json("ranker", spec, p.system,
                            p.render(brief=getattr(ctx, "brief_text", "") or "(no episode brief)",
                                     clips="\n".join(rows)), RANK_SCHEMA, meta={"video": ctx.video_id})
    ids = {c.id for c in cands}
    seen, order = set(), []
    for o in obj.get("order", []):
        if o.get("id") in ids and o["id"] not in seen:
            seen.add(o["id"])
            order.append(o)
    # any clip the ranker forgot goes after the ranked ones, in score order, keeping its code tier
    for c in sorted(cands, key=lambda c: sort_key(ctx, c)):
        if c.id not in seen:
            order.append({"id": c.id, "tier": None, "reason": "not ranked by the episode ranker"})
    return {"order": order, "groups": obj.get("groups", [])}


def apply_groups(shown: list[Candidate], groups: list[dict]) -> None:
    """Q8 links from the ranker's groups: flag the lower-ranked clip(s) similar_theme, link both ways."""
    by_id = {c.id: c for c in shown}
    pos = {c.id: k for k, c in enumerate(shown)}
    for g in groups:
        ids = sorted([i for i in dict.fromkeys(g.get("ids", [])) if i in by_id], key=lambda i: pos[i])
        if len(ids) < 2:
            continue
        rel = g.get("relation", "similar_theme")
        top = ids[0]
        for other in ids[1:]:
            if rel == "similar_theme":
                by_id[other].flags.add("similar_theme")
            by_id[other].related.append({"id": top, "relation": rel})
            by_id[top].related.append({"id": other, "relation": rel})


# ------------------------------------------------------------------ audience interest (interest v1)
INTEREST_SCHEMA = {
    "type": "object",
    "properties": {"clips": {"type": "array", "items": {
        "type": "object", "properties": {"id": {"type": "string"},
                                         "interest": {"type": "integer", "minimum": 1, "maximum": 5},
                                         "reason": {"type": "string"}},
        "required": ["id", "interest", "reason"]}}},
    "required": ["clips"],
}


def interest_scores(ctx, cfg, llm, cands: list[Candidate], notes_txt: str) -> dict[str, dict]:
    """One call per episode: {clip id: {"score": 1-5, "reason": str}} from the editor's notes (no view data)."""
    if not cands or not notes_txt or not cfg["models"].get("interest"):
        return {}
    p = load_prompt("interest")
    spec = ModelSpec.from_dict(cfg["models"]["interest"])
    rows = []
    for c in sorted(cands, key=lambda c: c.pieces[0][0]):
        j = {**(c.judge or {}), **(c.write or {})}
        first, _ = _first_last(ctx, c, 18)
        rows.append(f"{c.id} | {j.get('category', '?')} | {j.get('title', '')} | "
                    f"{c.info.get('point') or c.moment.get('point', '')} | {first}")
    obj = llm.generate_json("interest", spec, p.system, p.render(notes=notes_txt, clips="\n".join(rows)),
                            INTEREST_SCHEMA, meta={"video": ctx.video_id})
    ids = {c.id for c in cands}
    return {x["id"]: {"score": int(x["interest"]), "reason": " ".join(str(x.get("reason", "")).split())[:200]}
            for x in obj.get("clips", []) if x.get("id") in ids}


def order_with_interest(shown: list[Candidate], tier_of_c, max_gap: float = 0.5) -> list[Candidate]:
    """Quality first. Start from tier, then score order (overall, hook, position); interest may only swap two
    neighbours of the SAME tier when the higher-interest clip's overall is at most ``max_gap`` below the other's.
    Pairwise, so a clip can never pass one whose overall is more than max_gap higher (enforced in code)."""
    out = list(shown)
    changed = True
    while changed:
        changed = False
        for k in range(len(out) - 1):
            a, b = out[k], out[k + 1]
            ia = (a.info.get("interest") or {}).get("score", 0)
            ib = (b.info.get("interest") or {}).get("score", 0)
            if (tier_of_c(a) == tier_of_c(b) and ib > ia
                    and a.scores["overall"] - b.scores["overall"] <= max_gap + 1e-9):
                out[k], out[k + 1] = b, a
                changed = True
    return out
