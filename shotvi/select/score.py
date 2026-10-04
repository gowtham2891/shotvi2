"""Stages 4-5: judge the EDITED clip on the labeling-guide rubric (score v4); then write title, why, risks.

The scorer reads the kept text in playback order plus the cut context (spec v0.2 T2: 5 source words on each side of
every cut edge), so it can check meaning preservation, garbled joins, and rough starts/ends.
"""
from __future__ import annotations

from shotvi.llm import ModelSpec
from shotvi.segment.text import norm

from .clip import Candidate, metrics
from .prompts import load_prompt

CATEGORIES = ["story", "advice", "humour", "controversy", "emotional", "insight", "reveal", "motivation", "other"]
RISK_TYPES = ["unresolved_referent", "inside_reference", "missing_setup", "abrupt_end", "other"]
S15 = {"type": "number", "minimum": 1, "maximum": 5}

# Negation / verdict words (Telugu puts them sentence-finally). A cut that drops one gets an explicit warning in the
# judge's cut context (spec T2). A hint for the judge, not a rule: false positives only cost attention.
NEG_TOKENS = {"లేదు", "కాదు", "లేను", "కాను", "లేరు", "కారు", "వద్దు", "లేదండి", "కాదండి", "లేకుండా", "కాకుండా",
              "not", "no", "never", "don't", "dont", "can't", "cant", "won't", "didn't", "isn't", "doesn't",
              "shouldn't", "nothing", "nobody"}
NEG_SUFFIXES = ("లేదు", "కాదు", "కూడదు", "లేను", "లేరు", "లేదండి", "కాదండి", "వద్దు")


def _is_neg(tok: str) -> bool:
    t = norm(tok)
    return t in NEG_TOKENS or t.endswith(NEG_SUFFIXES)

OPENER_PROBLEMS = ["none", "pronoun_or_demonstrative", "mid_story", "greeting_or_filler", "slow_windup",
                   "myth_as_claim"]
ENDING_KINDS = ["verdict", "result", "concrete_example", "punchline", "emotional_peak", "tip_list", "moral",
                "framework", "trailing_metaphor", "hedge", "side_tip", "mid_thought", "other"]
STRONG_ENDINGS = {"verdict", "result", "concrete_example", "punchline", "emotional_peak"}
WEAK_ENDINGS = {"tip_list", "moral", "framework", "trailing_metaphor", "hedge", "side_tip", "mid_thought"}
ENDING_PROBLEMS = ["none", "mid_thought", "ends_before_point", "ends_on_setup", "trails_on", "wrap_up"]

SCHEMA = {  # judge (score v4): look first, then score; creator-facing text comes from the writer
    "type": "object",
    "properties": {
        "first_words": {"type": "string"},
        "opener_problem": {"type": "string", "enum": OPENER_PROBLEMS},
        "unresolved": {"type": "array", "items": {"type": "string"}},
        "last_words": {"type": "string"},
        "opening_question": {"type": "string"},
        "clip_kind": {"type": "string", "enum": ["advice_myth_insight", "personal_story", "other"]},
        "ending_kind": {"type": "string", "enum": ENDING_KINDS},
        "answers_opening": {"type": "boolean"},
        "ending_problem": {"type": "string", "enum": ENDING_PROBLEMS},
        "hook": S15, "standalone": S15, "payoff": S15, "value_emotion": S15, "overall": S15,
        "meaning_changed": {"type": "boolean"}, "meaning_note": {"type": "string"},
        "garbled_join": {"type": "boolean"},
        "starts_mid_thought": {"type": "boolean"}, "ends_mid_thought": {"type": "boolean"},
        "padded": {"type": "boolean"},
        "disqualifier": {"type": "string", "enum": ["none", "intro_outro", "promo_sponsor", "censored_payoff",
                                                     "montage"]},
        "sensitive": {"type": "boolean"}, "crude": {"type": "boolean"}, "relatable": {"type": "boolean"},
        "critique": {"type": "string"},
        "fix_hint": {"type": "string"},
    },
    "required": ["first_words", "opener_problem", "unresolved", "last_words", "opening_question", "clip_kind",
                 "ending_kind", "answers_opening", "ending_problem", "hook",
                 "standalone", "payoff", "value_emotion", "overall", "meaning_changed", "garbled_join",
                 "starts_mid_thought", "ends_mid_thought", "padded", "disqualifier", "sensitive", "crude",
                 "relatable", "critique", "fix_hint"],
}

WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "why_it_works": {"type": "string"},
        "category": {"type": "string", "enum": CATEGORIES},
        "standalone_risks": {"type": "array", "items": {
            "type": "object", "properties": {"type": {"type": "string", "enum": RISK_TYPES},
                                             "note": {"type": "string"}}, "required": ["type", "note"]}},
    },
    "required": ["title", "why_it_works", "category", "standalone_risks"],
}

# Caps applied in code from the problems the judge names (score v4). The judge must name the problem before it
# scores; these make the named problem cost the points the rubric says it costs, whatever number it wrote.
HOOK_CAP = {"pronoun_or_demonstrative": 2.0, "mid_story": 2.0, "greeting_or_filler": 2.0, "slow_windup": 2.5,
            "myth_as_claim": 2.5}
PAYOFF_CAP = {"mid_thought": 1.5, "ends_before_point": 2.0, "ends_on_setup": 2.0, "trails_on": 3.0, "wrap_up": 4.0}


def calibrated_scores(j: dict, caps: bool = True) -> tuple[dict, list[str]]:
    """Scores after the rubric's caps; returns (scores, notes)."""
    s = {k: float(j[k]) for k in ("hook", "standalone", "payoff", "value_emotion", "overall")}
    notes = []
    if not caps:
        return s, notes
    op, ep = j.get("opener_problem", "none"), j.get("ending_problem", "none")
    if op in HOOK_CAP and s["hook"] > HOOK_CAP[op]:
        s["hook"] = HOOK_CAP[op]
        notes.append(f"hook capped at {HOOK_CAP[op]} ({op})")
    if ep in PAYOFF_CAP and s["payoff"] > PAYOFF_CAP[ep]:
        s["payoff"] = PAYOFF_CAP[ep]
        notes.append(f"payoff capped at {PAYOFF_CAP[ep]} ({ep})")
    ek = j.get("ending_kind")
    if ek in WEAK_ENDINGS and s["payoff"] > 2.0:
        s["payoff"] = 2.0
        notes.append(f"payoff capped at 2 (ends on {ek})")
    elif (j.get("clip_kind") == "advice_myth_insight" and not j.get("answers_opening", True)
          and ek not in STRONG_ENDINGS and s["payoff"] > 3.0):
        s["payoff"] = 3.0
        notes.append("payoff capped at 3 (does not answer the opening)")
    unres = [u for u in j.get("unresolved") or [] if u and u.strip()]
    if unres and s["standalone"] > 3.5:
        s["standalone"] = 3.5
        notes.append("standalone capped at 3.5 (unresolved referents)")
    if len(unres) >= 2 and s["standalone"] > 3.0:
        s["standalone"] = 3.0
        notes.append("standalone capped at 3 (2+ unresolved)")
    weakest = min(s["hook"], s["standalone"], s["payoff"])
    top = weakest + (1.0 if s["value_emotion"] >= 5 else 0.5)
    if weakest <= 2:
        top = weakest
    if s["overall"] > top:
        notes.append(f"overall capped at {top} (weakest part {weakest})")
        s["overall"] = top
    return s, notes


def _words(ctx, idx: list[int], prev_spk=None) -> tuple[str, str | None]:
    out = []
    for i in idx:
        w = ctx.words[i]
        if w.speaker != prev_spk:
            out.append(f"{ctx.label(i)}:")
            prev_spk = w.speaker
        out.append(w.text)
    return " ".join(out), prev_spk


def viewer_text(ctx, cand: Candidate, m: dict) -> str:
    ws = ctx.words
    jumps = {j["after_piece"]: j for j in m["jumps"]}
    notes = {n.get("after_piece"): n for n in cand.edit_notes if "after_piece" in n}
    parts = []
    spk = None
    if any(n["type"] == "cold_open" for n in cand.edit_notes):
        parts.append("⟪cold open: a line from later in the source plays first⟫")
    for k, (a, b) in enumerate(cand.pieces):
        t, spk = _words(ctx, list(range(a, b + 1)), spk)
        parts.append(t)
        if k < len(cand.pieces) - 1:
            j = jumps.get(k)
            if j is None:
                pause_only = cand.pieces[k + 1][0] == b + 1
                parts.append("·" if pause_only else "‖")
            elif j["kind"] == "skip":
                parts.append(f"⟪skip: {j['dist']:.0f}s of source left out⟫")
            else:
                typ = notes.get(k, {}).get("type", "reorder")
                parts.append("⟪back to the story⟫" if typ == "cold_open" else "⟪moved line: from earlier in the source⟫")
            spk = None
    return " ".join(parts)


def cut_context(ctx, cand: Candidate, n: int) -> str:
    ws = ctx.words
    P = cand.pieces
    lines = []

    def src(i0, i1):
        i0, i1 = max(i0, 0), min(i1, ctx.n - 1)
        return _words(ctx, list(range(i0, i1 + 1)))[0] if i0 <= i1 else ""

    s = P[0][0]
    lines.append(f"START: [{src(s - n, s - 1)}] >> {src(s, min(s + n - 1, P[0][1]))} ...")
    for k in range(len(P) - 1):
        a1, b0 = P[k][1], P[k + 1][0]
        left = src(max(P[k][0], a1 - n + 1), a1)
        right = src(b0, min(P[k + 1][1], b0 + n - 1))
        if b0 > a1:
            gap_n = b0 - a1 - 1
            if gap_n == 0:
                dropped = "(pause only)"
            elif gap_n <= 2 * n:
                dropped = src(a1 + 1, b0 - 1)
            else:
                dropped = f"{src(a1 + 1, a1 + n)} ... ({gap_n - 2 * n} more words) ... {src(b0 - n, b0 - 1)}"
            # short cuts only: a long cut drops whole passages, a short one can flip its neighbour's meaning
            negs = [ws[i].text for i in range(a1 + 1, b0) if _is_neg(ws[i].text)] if gap_n <= 2 * n + 2 else []
            warn = (f"  (!) the cut words include a negation/verdict word ({', '.join(negs[:3])}): check T2"
                    if negs else "")
            lines.append(f"CUT {k + 1}: ... {left} [{dropped}] {right} ...{warn}")
        else:
            lines.append(f"CUT {k + 1} (jump back): ... {left} [{src(a1 + 1, a1 + n)}] || [{src(b0 - n, b0 - 1)}] "
                         f"{right} ...")
    e = P[-1][1]
    tail_negs = [ws[i].text for i in range(e + 1, min(e + 1 + n, ctx.n)) if _is_neg(ws[i].text)
                 and not any(ctx.natural_after[j] for j in range(e, i))]
    warn = f"  (!) the sentence continues into a negation ({tail_negs[0]}) after the clip ends" if tail_negs else ""
    lines.append(f"END: ... {src(max(P[-1][0], e - n + 1), e)} << [{src(e + 1, e + n)}]{warn}")
    return "\n".join(lines)


def structure_desc(m: dict, cand: Candidate) -> str:
    kinds = [n["type"] for n in cand.edit_notes if n["type"] in ("cold_open", "reorder", "skip")]
    if not kinds:
        return "source order, no jumps (tightened only)"
    return "jumps: " + ", ".join(kinds)


def score(ctx, cfg, llm, cand: Candidate, role: str = "scorer") -> dict:
    p = load_prompt("score")
    spec = ModelSpec.from_dict(cfg["models"][role])
    m = metrics(ctx, cand.pieces)
    prompt = p.render(duration=f"{m['edited']:.1f}", n_pieces=m["n_pieces"], structure=structure_desc(m, cand),
                      viewer_text=viewer_text(ctx, cand, m),
                      cut_context=cut_context(ctx, cand, cfg["guardrails"]["cut_context_words"]))
    return llm.generate_json(role, spec, p.system, prompt, SCHEMA,
                             meta={"video": ctx.video_id, "cand": cand.id})


def write(ctx, cfg, llm, cand: Candidate) -> dict:
    """Title / why / category / risks for a clip that will be shown (cheap model, after judging)."""
    p = load_prompt("write")
    spec = ModelSpec.from_dict(cfg["models"].get("writer") or cfg["models"]["scorer"])
    m = metrics(ctx, cand.pieces)
    j, sc = cand.judge or {}, cand.scores or {}
    prompt = p.render(guest=getattr(ctx, "guest_text", "") or "(none)",
                      brief=getattr(ctx, "brief_text", "") or "(no episode brief)", hook=sc.get("hook"), standalone=sc.get("standalone"), payoff=sc.get("payoff"),
                      value=sc.get("value_emotion"), overall=sc.get("overall"), critique=j.get("critique", ""),
                      unresolved="; ".join(j.get("unresolved") or []) or "none",
                      viewer_text=viewer_text(ctx, cand, m))
    return llm.generate_json("writer", spec, p.system, prompt, WRITE_SCHEMA,
                             meta={"video": ctx.video_id, "cand": cand.id})
