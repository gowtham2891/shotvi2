"""Stage 4, scoring and vetoes (doc 7): Gemini marks every Stage 3 candidate on six dimensions with evidence, one
TEXT call per scene (all of the scene's candidates together so the model can compare variants); code applies the hard
vetoes and the combined score. No media: the picture was judged in Stage 2 and is judged again in Stage 5 on the cut.

Call shape: [cached prefix (prefix_stage2.txt bytes, unchanged)] + the scene's beat sheet + every candidate (id, type,
variant, segments, transcript AS CUT with removed lines marked, sibling id, context_required, dynamic) + instructions.
Output: ``facts["scores"]`` (per candidate: scores, evidence, dead stretches, fan_clip, sibling verdict, combined,
weights used, vetoes fired, status, widened_to) and ``facts["stages"]["4"]``. Thresholds and weights: config ``stage4``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import jsonschema

from shotvi.llm.base import ModelSpec

from . import stage3_candidates as S3
from .config import ROOT, inr_per_usd
from .paths import write_json
from .runlog import append_log
from .stage1_episode_pass import load_prompt
from .stage1_facts import fmt_ts, parse_ts
from .media import cut_scene, media_part, upload

VERSION = "stage4/0.1"
STAGE_KEY = "4"
ROLE = "clip_scores"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "stage4_scoring.md"
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "scores.schema.json"
DIMS = ("hook", "payoff", "open_question", "standalone", "pace", "delivery", "series_pull")   # open_question replaces loop_strength (2026-10-29)
VETO_RULES = ("payoff_min", "open_question_min", "pace_min", "standalone_min")


class ScoringError(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------- prompt pieces
def check_stage3(facts: dict | None) -> None:
    if not facts or "3" not in (facts.get("stages") or {}) or not facts.get("candidates"):
        raise RuntimeError("Stage 4 needs Stage 3 candidates; run Stage 3 first")
    v = facts["stages"]["3"].get("version")
    if v != S3.VERSION:
        raise RuntimeError(f"Stage 3 output is {v}, but the current Stage 3 is {S3.VERSION}; re-run Stage 3 first")


def is_open_question(c: dict) -> bool:
    """founder, 2026-10-29: an end-variant ending on a Stage 2 hook_moment (or a legacy hook-type candidate)."""
    return c["type"] == "hook" or str(c.get("variant") or "").startswith("open_question")


def payoff_dim(c: dict) -> str:
    """The dimension the payoff veto reads: open_question for open-question end-variants, else payoff."""
    return "open_question" if is_open_question(c) else "payoff"


def sibling_of(c: dict) -> str | None:
    """The sibling the verdict compares against: the trim twin first, else a hook's other end, else the wider start."""
    return c.get("untrimmed_sibling") or c.get("trimmed_sibling") or c.get("end_sibling") or c.get("wider_sibling")


def media_mode(facts: dict, cfg: dict, override: str | None = None) -> str:
    """video | none: the override, else config stage4.media, else (auto) by the episode's content type."""
    m = override or str(cfg["stage4"].get("media", "auto"))
    if m in ("video", "none"):
        return m
    by = cfg["stage4"].get("media_by_content_type") or {}
    ct = ((facts.get("episode_pass") or {}).get("content_type")) or "default"
    return str(by.get(ct) or by.get("default") or "none")


def candidate_text(facts: dict, c: dict) -> str:
    """The candidate as WHAT REMAINS (founder, 2026-10-28): its kept ranges in order, each with the lines and sounds
    inside it; between two kept ranges one [cut ...] marker says how much is not in the clip and what it was (a
    shortened / removed stretch, skipped lines). Removed lines are not quoted, so pace and kept_dead are judged on
    the kept material. Deterministic, built only from the facts."""
    segs = [(float(a), float(b)) for a, b in c["segments"]]
    skipped = set(c["removed"]["lines_skipped"])
    stretches = [(g, "shortened") for g in c["removed"]["gaps_shortened"]] + [(g, "removed") for g in c["removed"]["gaps_removed"]]
    from .acceptance import final_speaker                   # the FINAL map: Stage 2 corrections > 1B / identity findings > fix
    lines = [r for r in facts["lines"] if r["end"] > c["start"] + 1e-3 and r["start"] < c["end"] - 1e-3]

    def who_of(r):
        if r["kind"] == "event":
            return "(sound)"
        key, name = final_speaker(facts, r["id"])
        return f"[{key or ''} {name or r.get('speaker_name') or ''}]".replace("[ ", "[").replace(" ]", "]")

    out = []
    for i, (a, b) in enumerate(segs):
        if i > 0:
            pa, pb = segs[i - 1]
            gone = a - pb
            what = []
            for g, kind in stretches:
                if abs(float(g.get("cut_at", -1)) - pb) <= 0.35:
                    what.append(f"{kind} stretch {g['range']} ({g.get('kind')}), its first {g.get('to_s')} s kept")
            sk = [r["id"] for r in lines if r["id"] in skipped and r["start"] >= pb - 1e-3 and r["end"] <= a + 1e-3]
            if sk:
                what.append("skipped line(s) " + ", ".join(sk))
            inside = [r["id"] for r in lines if r["kind"] == "speech" and r["id"] not in skipped and r["start"] >= pb - 1e-3 and r["end"] <= a + 1e-3]
            if inside:
                what.append(f"{len(inside)} line(s) inside the cut ({', '.join(inside)})")
            out.append(f"[cut: {gone:.1f} s not in the clip ({'; '.join(what) or 'pause'}); the clip continues at {fmt_ts(a)}]")
        out.append(f"kept range {i + 1}/{len(segs)} {fmt_ts(a)}-{fmt_ts(b)}, {b - a:.1f} s:")
        for r in lines:
            if a - 1e-3 <= r["start"] and r["end"] <= b + 1e-3:
                out.append(f"{r['id']} {fmt_ts(r['start'])}-{fmt_ts(r['end'])} {who_of(r)}: {r['text']}")
    return "\n".join(out)


def sheet_text(sheet: dict) -> str:
    L = [f"# SCENE {sheet['scene_id']} BEAT SHEET ({sheet['start_str']}-{sheet['end_str']}; register {sheet.get('register')}; "
         f"self-contained {sheet.get('self_contained')}; confidence {sheet.get('confidence')})",
         f"setup: {sheet.get('setup')}",
         "beats: " + "; ".join(f"{b['line']} {b.get('type')} ({b.get('t_str', '')}) {b.get('note') or ''}".strip() for b in sheet.get("beats") or []),
         f"payoff: {(sheet.get('payoff') or {}).get('line')} {(sheet.get('payoff') or {}).get('note') or ''}",
         f"reaction: {json.dumps({k: v for k, v in (sheet.get('reaction') or {}).items() if k in ('type', 'after_line', 'by', 'note', 't_str')}, ensure_ascii=False)}",
         f"delivery_notes: {sheet.get('delivery_notes')}",
         f"dynamic: {json.dumps(sheet.get('dynamic') or {}, ensure_ascii=False)}",
         f"context_required: {json.dumps(sheet.get('context_required') or [], ensure_ascii=False)}",
         f"callbacks: {json.dumps(sheet.get('callbacks') or [], ensure_ascii=False)}",
         "non_dialogue: " + "; ".join(f"{nd.get('range')} {nd.get('kind')} ({nd.get('recommend')}): {nd.get('reason') or ''}" for nd in sheet.get("non_dialogue") or []),
         "skippable_lines: " + ", ".join(f"{s['line']} ({s.get('reason') or ''})" for s in sheet.get("skippable_lines") or [])]
    return "\n".join(L)


def _ranges_text(facts: dict, c: dict, scene_start: float | None, scene_end: float | None = None) -> str:
    """The candidate as time ranges inside the scene: kept segments, shortened stretches with their targets, removed
    stretches and skipped lines; on the episode clock and, with a scene video, on the video clock too."""
    def rng(a: float, b: float) -> str:
        t = f"{fmt_ts(a)}-{fmt_ts(b)}"
        if scene_start is not None:
            if a < scene_start - 1.0 or (scene_end is not None and b > scene_end + 1.0):
                t += " (NOT in the attached video: another scene; judge it from the transcript)"
            else:
                t += f" (video {fmt_ts(max(0.0, a - scene_start))}-{fmt_ts(max(0.0, b - scene_start))})"
        return t
    lid = {r["id"]: r for r in facts["lines"]}
    L = ["kept segments: " + "; ".join(rng(a, b) for a, b in c["segments"])]
    sh = c["removed"]["gaps_shortened"]
    if sh:
        L.append("shortened stretches: " + "; ".join(f"{rng(*[parse_ts(t)[0] for t in g['range'].split('-')])} ({g.get('kind')}) keeps the first {g['to_s']} s, cut at {fmt_ts(g['cut_at'])}" for g in sh))
    rm = c["removed"]["gaps_removed"]
    if rm:
        L.append("removed stretches: " + "; ".join(f"{rng(*[parse_ts(t)[0] for t in g['range'].split('-')])} ({g.get('kind')})"
                                                   + (f" keeps the first {g['to_s']} s, cut at {fmt_ts(g['cut_at'])}" if g.get("cut_at") is not None else "") for g in rm))
    sk = c["removed"]["lines_skipped"]
    if sk:
        L.append("skipped lines: " + "; ".join(f"{x} {rng(lid[x]['start'], lid[x]['end'])}" for x in sk if x in lid))
    return "\n".join(L)


def candidate_block(facts: dict, c: dict, scene_start: float | None = None, scene_end: float | None = None) -> str:
    sib = sibling_of(c)
    rels = []
    if c.get("trimmed_sibling"):
        rels.append("untrimmed twin of " + c["trimmed_sibling"])
    if c.get("untrimmed_sibling"):
        rels.append("trimmed twin of " + c["untrimmed_sibling"])
    if c.get("end_sibling"):
        rels.append("same start, other end: " + c["end_sibling"])
    if c.get("wider_sibling"):
        rels.append("one beat earlier start: " + c["wider_sibling"])
    if c.get("critic_trim_of"):
        rels.append(f"critic-trimmed version of {c['critic_trim_of']} (cold viewers swiped in its stretch(es); each keeps its first seconds, then one cut)")
    if c.get("thread"):
        th = c["thread"]
        rels.append(f"THREAD: setup {th['setup_lines'][0]}-{th['setup_lines'][1]} from scene {th['setup_scene']}, one cut, then this scene to {th['end_line']} ({th.get('why') or ''})")
    if is_open_question(c):
        rels.append("ends on an OPEN QUESTION (the payoff is after the clip end)")
    if c["variant"] == "teaser_open":
        rels.append("TEASER OPEN: the first kept range is a short visual teaser lifted from later in the clip, then the clip plays in order")
    if c["variant"] == "channel_short":
        rels.append("the CHANNEL'S OWN posted Short of this moment (as cut by the channel); score it like any other candidate")
    if c.get("situation"):
        rels.append(f"situation {c['situation'].get('index')}: {c['situation'].get('what')}")
    rel = "; ".join(rels) or "no sibling"
    head = (f"## CANDIDATE {c['id']} · type {c['type']} · variant {c['variant']} · {c['start_str']}-{c['end_str']} · final {c['final_duration']:.1f} s "
            f"(raw {c['raw_duration']:.1f} s) · {rel}\n"
            + _ranges_text(facts, c, scene_start, scene_end) + "\n"
            f"removed: {len(c['removed']['gaps_shortened'])} stretch(es) shortened, {len(c['removed']['gaps_removed'])} removed, lines skipped {c['removed']['lines_skipped'] or 'none'}\n"
            f"beats included: {', '.join(c['beats_included'])} · payoff line {c.get('payoff_line')} · reaction {c.get('reaction')}\n"
            f"context_required: {json.dumps(c.get('context_required') or [], ensure_ascii=False)}\n"
            f"dynamic: {json.dumps(c.get('dynamic') or {}, ensure_ascii=False)}\n"
            f"judge: {'open_question is the main mark (the clip ends before the payoff)' if is_open_question(c) else 'payoff'}; sibling id: {sib or 'none'}\n"
            "what remains (kept ranges with their lines):\n")
    return head + candidate_text(facts, c)


TASTE_TITLE = "Clips this creator/channel has posted or approved from this series"


def _norm(x) -> str:
    return " ".join(str(x or "").lower().split())


def _episode_lines(vid: str, cfg: dict, facts: dict | None = None) -> list[tuple[float, float, str, str]]:
    """(start, end, speaker, text) of an episode's speech: its episode_facts.json (final speaker names) when it exists,
    else its Scribe transcript's sentences."""
    from .acceptance import final_speaker
    f = facts if facts and (facts.get("episode") or {}).get("video_id") == vid else None
    p = ROOT / cfg["paths"]["runs_dir"] / vid / "episode_facts.json"
    if f is None and p.exists():
        f = json.loads(p.read_text(encoding="utf-8"))
    if f is not None:
        return [(r["start"], r["end"], final_speaker(f, r["id"])[1] or "", r["text"]) for r in f["lines"] if r["kind"] == "speech"]
    q = ROOT / "scribe_dataset" / vid / f"{vid}_scribe_transcript.json"
    if not q.exists():
        return []
    d = json.loads(q.read_text(encoding="utf-8"))
    return [(float(x["start"]), float(x["end"]), str(x.get("speaker") or ""), x["text"]) for x in d.get("sentences") or []]


def taste_from_channel_shorts(facts: dict, cfg: dict, root: Path = ROOT) -> list[dict]:
    """founder, 2026-10-30: the channel Shorts matched to the SAME SERIES' OTHER scripted-series episodes (never this
    episode's own Shorts or golden clips), most viewed first, each as the episode lines inside its matched pieces."""
    t = cfg["stage4"].get("taste") or {}
    ep = facts.get("episode") or {}
    key, own = ep.get("series_key"), ep.get("video_id")
    rows = []
    for p in sorted((root / "data" / "channel_shorts").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        vid = d.get("episode_id") or p.stem
        if vid == own:
            continue
        fp = root / cfg["paths"]["runs_dir"] / vid / "episode_facts.json"
        if not fp.exists():
            continue
        f2 = json.loads(fp.read_text(encoding="utf-8"))
        if (f2.get("episode_pass") or {}).get("content_type") != "scripted_series" or not key or (f2.get("episode") or {}).get("series_key") != key:
            continue
        for s in d.get("accepted") or []:
            segs = sorted(s["segments"], key=lambda x: float(x["short_start"]))
            rows.append({"video_id": vid, "short_id": s["short_id"], "views": s.get("views") or 0, "type": "unknown", "label": (s.get("title") or "")[:80],
                         "source": "posted", "tier": "gold", "segs": [(float(x["episode_start"]), float(x["episode_end"])) for x in segs]})
    rows.sort(key=lambda r: -r["views"])
    rows = rows[:int(t.get("max_examples", 6))]
    cache: dict[str, list] = {}
    for r in rows:
        lines = cache.setdefault(r["video_id"], _episode_lines(r["video_id"], cfg))
        words, out = 0, []
        for a, b in r["segs"]:
            for x in lines:
                if a - 0.3 <= x[0] and x[1] <= b + 0.3:
                    n = len(x[3].split())
                    if words + n > int(t.get("max_words", 200)):
                        break
                    out.append(f"{x[2]}: {x[3]}" if x[2] else x[3]); words += n
        r["transcript"] = "\n".join(out)
    return [r for r in rows if r.get("transcript")]


def taste_examples(facts: dict, cfg: dict, gs: dict | None = None) -> list[dict]:
    """founder, 2026-10-29: up to ``stage4.taste.max_examples`` golden-set moments of the SAME series that the channel posted
    (channel_short) or the founder would post (human, definite / yes), each as its cut transcript (<= max_words words).
    The episode being scored is left out when ``exclude_same_episode`` (its golden moments are the answer key)."""
    t = cfg["stage4"].get("taste") or {}
    if not t:
        return []
    if t.get("source") == "channel_shorts" and gs is None:
        return taste_from_channel_shorts(facts, cfg)
    if gs is None:
        from .eval import golden_set as G
        gs = G.load()
    ep = facts.get("episode") or {}
    names = {_norm(ep.get("series")), _norm(ep.get("series_key"))} - {""}
    rows = []
    for vid, g in (gs.get("episodes") or {}).items():
        if _norm(g.get("series")) not in names or (t.get("exclude_same_episode", True) and vid == ep.get("video_id")):
            continue
        for m in g.get("moments") or []:
            srcs = m.get("sources") or []
            posted = any(x.get("kind") == "channel_short" for x in srcs)
            approved = any(x.get("kind") == "human" and (x.get("confidence") == "definite" or str(x.get("founder_would_post")).lower() == "yes") for x in srcs)
            if posted or approved:
                cuts = [tuple(x["cut"]) for x in srcs if x.get("cut")]
                rows.append({"video_id": vid, "start": float(m["start"]), "end": float(m["end"]), "type": m.get("type") or "unknown",
                             "label": m.get("label") or "", "tier": m.get("tier"), "source": "posted" if posted else "approved", "cuts": cuts})
    rows.sort(key=lambda r: (r["tier"] != "gold", r["source"] != "posted", r["video_id"], r["start"]))
    rows = rows[:int(t.get("max_examples", 6))]
    cache: dict[str, list] = {}
    for r in rows:
        lines = cache.setdefault(r["video_id"], _episode_lines(r["video_id"], cfg, facts))
        kept = [x for x in lines if x[0] >= r["start"] - 0.5 and x[1] <= r["end"] + 0.5 and not any(a - 0.05 <= x[0] and x[1] <= b + 0.05 for a, b in r["cuts"])]
        words, out = 0, []
        for _, _, who, text in kept:
            n = len(text.split())
            if words + n > int(t.get("max_words", 200)):
                out.append("..."); break
            out.append(f"{who}: {text}" if who else text); words += n
        r["transcript"] = "\n".join(out)
    return [r for r in rows if r.get("transcript")]


def taste_block(facts: dict, cfg: dict, gs: dict | None = None) -> str:
    ex = taste_examples(facts, cfg, gs)
    if not ex:
        return f"# {TASTE_TITLE}\n(none yet for this series)"
    parts = [f"# {TASTE_TITLE} ({len(ex)})"]
    for i, r in enumerate(ex, 1):
        parts.append(f"## example {i} · {r['source']} · type {r['type']} · {r['label']}\n{r['transcript']}")
    return "\n".join(parts)


def scene_prompt(facts: dict, sheet: dict, cands: list[dict], media: str = "none", taste: str = "") -> str:
    scene_start = float(sheet["start"]) if media == "video" else None
    scene_end = float(sheet["end"]) if media == "video" else None
    head = (taste + "\n\n" if taste else "") + sheet_text(sheet)
    if media == "video":
        head += f"\nvideo clock: the attached scene video starts at {sheet['start_str']} (video 0:00.0 = episode {sheet['start_str']})"
    parts = [head, f"\n# CANDIDATES OF SCENE {sheet['scene_id']} ({len(cands)}): " + ", ".join(c["id"] for c in cands)]
    for c in cands:
        parts.append("\n" + candidate_block(facts, c, scene_start, scene_end))
    return "\n".join(parts)


def prefix2_text(facts: dict, cfg: dict, run_dir: Path | None = None) -> str:
    """The Stage 2 prefix, byte-identical: the file written by Stage 2 when present, else rebuilt the same way."""
    p = (run_dir or (ROOT / cfg["paths"]["runs_dir"] / facts["episode"]["video_id"])) / "prefix_stage2.txt"
    if p.exists():
        return p.read_text(encoding="utf-8")
    from .prefix import build_prefix_stage2
    from .stage2_beats import _prefix1_text, all_looks
    return build_prefix_stage2(facts, _prefix1_text(facts, cfg), all_looks(facts, cfg))


def scenes_with_candidates(facts: dict) -> list[tuple[dict, list[dict]]]:
    by = {}
    for c in facts.get("candidates") or []:
        by.setdefault(c["scene_id"], []).append(c)
    out = []
    for sh in sorted(facts.get("beat_sheets") or [], key=lambda s: s["scene_id"]):
        if sh["scene_id"] in by:
            out.append((sh, sorted(by[sh["scene_id"]], key=lambda c: c["id"])))
    return out


def call_groups(cands: list[dict], mode: str) -> list[tuple[str, list[dict]]]:
    """founder, 2026-10-29: critic_trimmed siblings are scored TEXT ONLY (cheap), in their own call; everything else in
    the run's media mode. [(call media, candidates)] with empty groups left out."""
    if mode != "video":
        return [(mode, cands)]
    text = [c for c in cands if c["variant"] == "critic_trimmed"]
    rest = [c for c in cands if c["variant"] != "critic_trimmed"]
    return [(m, g) for m, g in ((mode, rest), ("none", text)) if g]


# ----------------------------------------------------------------------------------------------- estimate
def estimate(facts: dict, cfg: dict, counter=None, run_dir: Path | None = None, media: str | None = None, changed_only: bool = False,
             text_only: bool = False, exclude_ids=None) -> dict:
    """Per-scene and total rupees. HEADLINE (``total_inr``) = no prefix cache: the implicit cache missed on most scene
    calls on 2026-10-28/29. ``total_inr_if_cache_hits`` = prefix at the cached rate after the first call. Token counts
    are calibrated on actual Stage 4 calls (config stage4.calibration): the local counter undercounts Telugu text by
    ``text_factor``; video costs ``video_tokens_per_s``. With an exact counter (countTokens) the text factor is 1."""
    r, rate = cfg["rates"], inr_per_usd(cfg)
    cal = cfg["stage4"].get("calibration") or {}
    mode = media_mode(facts, cfg, media)
    exact = counter is not None
    if counter is None:
        from shotvi.segment.render import estimate_tokens
        counter = estimate_tokens
    tf = 1.0 if exact else float(cal.get("text_factor", 1.0))
    pre = prefix2_text(facts, cfg, run_dir)
    n_pre = int(counter(pre) * tf)
    per_out = int(cfg["stage4"]["expected_output_tokens_per_candidate_video" if mode == "video" else "expected_output_tokens_per_candidate"])
    tps = float(cal.get("video_tokens_per_s") or r["tokens_per_s_video_low"])
    rows = []
    pairs = [(sh, cs) for sh, cs, _ in changed_scenes(facts, active_run(facts))] if changed_only else scenes_with_candidates(facts)
    skip = set(exclude_ids or [])
    pairs = [(sh, [c for c in cs if c["id"] not in skip]) for sh, cs in pairs]
    pairs = [(sh, cs) for sh, cs in pairs if cs]
    taste = taste_block(facts, cfg)
    per_out_text = int(cfg["stage4"]["expected_output_tokens_per_candidate"])
    calls = [(sh, g, m) for sh, cs in pairs for m, g in call_groups(cs, "none" if text_only else mode)]
    for i, (sh, cands, m) in enumerate(calls):
        n_txt = int((counter(scene_prompt(facts, sh, cands, m, taste)) + 900) * tf)
        out_tok = (per_out if m == "video" else per_out_text) * len(cands)
        media_tok = int((float(sh["end"]) - float(sh["start"])) * tps) if m == "video" else 0
        usd_full = ((n_pre + n_txt + media_tok) * r["usd_per_1m_input"] + out_tok * r["usd_per_1m_output"]) / 1e6
        usd_hit = usd_full - (0 if i == 0 else n_pre * (r["usd_per_1m_input"] - r["usd_per_1m_cached_input"]) / 1e6)
        rows.append({"scene_id": sh["scene_id"], "media": m, "n_candidates": len(cands), "scene_tokens": n_txt, "prefix_tokens": n_pre, "media_tokens": media_tok,
                     "expected_output_tokens": out_tok, "media_inr": round(media_tok * r["usd_per_1m_input"] / 1e6 * rate, 2),
                     "inr": round(usd_full * rate, 2), "inr_if_cache_hits": round(usd_hit * rate, 2)})
    total = round(sum(x["inr"] for x in rows), 2)
    return {"media": mode, "prefix_tokens": n_pre, "scenes": rows, "n_candidates": sum(x["n_candidates"] for x in rows), "changed_only": changed_only,
            "total_inr": total, "total_inr_if_cache_hits": round(sum(x["inr_if_cache_hits"] for x in rows), 2),
            "total_inr_no_cache": total, "media_inr": round(sum(x["media_inr"] for x in rows), 2), "inr_per_usd": rate,
            "calibration": {"text_factor": tf, "video_tokens_per_s": tps, "exact_counter": exact, "fitted_on": cal.get("fitted_on")},
            "note": ("scene video at " + f"{tps:.0f} tokens/s + " if mode == "video" else "text only; ") +
                    f"text tokens x {tf:.2f} (calibrated); headline = no prefix cache; output at {per_out} tokens per candidate"}


# ----------------------------------------------------------------------------------------------- validation
def validate(ans: dict, cands: list[dict]) -> list[str]:
    errs = []
    want = {c["id"]: c for c in cands}
    got = [e.get("id") for e in ans.get("candidates") or []]
    missing = [i for i in want if i not in got]
    extra = [i for i in got if i not in want]
    dup = sorted({i for i in got if got.count(i) > 1})
    if missing:
        errs.append(f"missing candidate(s): {missing}")
    if extra:
        errs.append(f"unknown candidate id(s): {extra}")
    if dup:
        errs.append(f"duplicate candidate id(s): {dup}")
    for e in ans.get("candidates") or []:
        c = want.get(e.get("id"))
        if not c:
            continue
        for need in ("payoff", "open_question"):                     # founder, 2026-10-29: both on every candidate
            if not isinstance(e.get(need), dict):
                errs.append(f"{c['id']}: {need} is required")
        for d in DIMS:
            m = e.get(d)
            if isinstance(m, dict):
                if not isinstance(m.get("score"), int) or not 1 <= m["score"] <= 10:
                    errs.append(f"{c['id']}: {d}.score must be an integer 1-10")
                if not str(m.get("evidence") or "").strip():
                    errs.append(f"{c['id']}: {d}.evidence is empty")
        if sibling_of(c) and not str(e.get("verdict_vs_sibling") or "").strip():
            errs.append(f"{c['id']}: verdict_vs_sibling is required (sibling {sibling_of(c)})")
    return errs


# ----------------------------------------------------------------------------------------------- combination and vetoes
def weights_for(c: dict, cfg4: dict) -> dict[str, float]:
    w = dict((cfg4["weights"].get("open_question") if is_open_question(c) else None) or cfg4["weights"].get(c["type"]) or cfg4["weights"]["comedy"])
    lr = cfg4.get("length_rule") or {}
    above, per, shift = float(lr.get("above_s", 45)), float(lr.get("per_s", 30)), float(lr.get("shift", 0.05))
    extra = max(0.0, float(c["final_duration"]) - above)
    steps = int(extra // per)
    total_shift = min(steps * shift, float(lr.get("max_shift", 0.20)))
    pd = payoff_dim(c)
    if total_shift > 0 and pd in w:
        moved = min(total_shift, w[pd])
        w[pd] = round(w[pd] - moved, 4)
        w["hook"] = round(w.get("hook", 0.0) + moved, 4)
    return w


def combined_score(scores: dict[str, int], w: dict[str, float]) -> float:
    num = sum(float(w[d]) * scores[d] for d in w if d in scores)
    den = sum(float(w[d]) for d in w if d in scores)
    return round(num / den, 2) if den else 0.0


def apply_vetoes(c: dict, scores: dict[str, int], cfg4: dict, all_scores: dict[str, dict], cands_by_id: dict[str, dict]) -> dict:
    """Vetoes (code): payoff (open_question for open-question end-variants) below the floor -> rejected; pace below the floor -> rejected;
    standalone below the floor -> the widened sibling (untrimmed twin, else the widest candidate of the same moment)
    when it exists and scored >= the floor, else kept only as fan_clip. Returns status, vetoes, widened_to."""
    v = cfg4["vetoes"]
    pd = payoff_dim(c)
    vetoes = []
    floor = int(v["open_question_min"] if pd == "open_question" else v["payoff_min"])
    if scores.get(pd, 10) < floor:
        vetoes.append({"rule": f"{pd}<{floor}", "dimension": pd, "score": scores.get(pd)})
    if scores.get("pace", 10) < int(v["pace_min"]):
        vetoes.append({"rule": f"pace<{int(v['pace_min'])}", "dimension": "pace", "score": scores.get("pace")})
    status, widened_to = "pass", None
    if vetoes:
        status = "rejected"
    sa_min = int(v["standalone_min"])
    if scores.get("standalone", 10) < sa_min:
        wid = widened_sibling(c, cands_by_id)
        ws = all_scores.get(wid["id"]) if wid else None
        if wid and ws and ws.get("standalone", 0) >= sa_min:
            vetoes.append({"rule": f"standalone<{sa_min}", "dimension": "standalone", "score": scores.get("standalone"), "widened_to": wid["id"]})
            if status == "pass":
                status, widened_to = "widened", wid["id"]
        else:
            vetoes.append({"rule": f"standalone<{sa_min}", "dimension": "standalone", "score": scores.get("standalone"), "widened_to": None})
            if status == "pass":
                status = "fan_clip_only"
    return {"status": status, "vetoes": vetoes, "widened_to": widened_to}


def widened_sibling(c: dict, cands_by_id: dict[str, dict]) -> dict | None:
    """The untrimmed twin first; else the widest candidate of the same moment (same scene and payoff line, a long
    type, starting at or before this one) that is not this one."""
    if c.get("wider_sibling") and c["wider_sibling"] in cands_by_id:          # hooks / loops: the one-beat-earlier start
        return cands_by_id[c["wider_sibling"]]
    if c.get("untrimmed_sibling") and c["untrimmed_sibling"] in cands_by_id:
        return cands_by_id[c["untrimmed_sibling"]]
    same = [o for o in cands_by_id.values() if o["scene_id"] == c["scene_id"] and o.get("payoff_line") == c.get("payoff_line")
            and o["id"] != c["id"] and o["type"] in S3.LONG_TYPES and o["start"] <= c["start"] + 1e-3 and o["end"] >= c["end"] - 1e-3
            and o["final_duration"] > c["final_duration"] + 1e-3]
    if not same:
        return None
    return max(same, key=lambda o: o["final_duration"])


def score_candidates(cands: list[dict], answers: dict[str, dict], cfg: dict) -> list[dict]:
    """Code side for a set of candidates whose model answers are in ``answers`` (id -> entry)."""
    cfg4 = cfg["stage4"]
    by_id = {c["id"]: c for c in cands}
    marks: dict[str, dict] = {}
    for c in cands:
        e = answers.get(c["id"])
        if not e:
            continue
        marks[c["id"]] = {d: e[d]["score"] for d in DIMS if isinstance(e.get(d), dict)}
    out = []
    for c in cands:
        e = answers.get(c["id"])
        if not e:
            continue
        sc = marks[c["id"]]
        w = weights_for(c, cfg4)
        comb = combined_score(sc, w)
        vd = apply_vetoes(c, sc, cfg4, marks, by_id)
        out.append({"id": c["id"], "scene_id": c["scene_id"], "type": c["type"], "variant": c["variant"], "final_duration": c["final_duration"],
                    "sibling": sibling_of(c), "scores": sc, "evidence": {d: e[d]["evidence"] for d in DIMS if isinstance(e.get(d), dict)},
                    "dead_stretches": (e.get("pace") or {}).get("dead_stretches") or [], "fan_clip": bool(e.get("fan_clip")),
                    "removed_needed": e.get("removed_needed") or [], "kept_dead": e.get("kept_dead") or [],
                    "verdict_vs_sibling": e.get("verdict_vs_sibling"), "combined": comb, "weights_used": w,
                    "status": vd["status"], "vetoes": vd["vetoes"], "widened_to": vd["widened_to"],
                    "eligible_for_final": bool(c.get("eligible_for_final", True)),
                    "flags": {"ineligible_over_platform_max": not c.get("eligible_for_final", True), "fan_clip": bool(e.get("fan_clip")),
                              "widened_from": None}})
    for s in out:
        if s["widened_to"]:
            for t in out:
                if t["id"] == s["widened_to"]:
                    t["flags"]["widened_from"] = (t["flags"].get("widened_from") or []) + [s["id"]]
    return out


def schema_errors(obj: dict) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message[:120]}" for e in jsonschema.Draft202012Validator(schema).iter_errors(obj)]


# ----------------------------------------------------------------------------------------------- runs (A / B) and compare
def run_key(media: str, facts: dict) -> str:
    return f"{media}@{facts['stages']['3']['generated_at']}"


def runs_of(facts: dict) -> list[dict]:
    return list(facts.get("score_runs") or [])


def active_run(facts: dict) -> dict | None:
    runs = runs_of(facts)
    if not runs:
        return None
    key = facts.get("scores_active")
    return next((r for r in runs if r["run_key"] == key), runs[-1])


def set_active(facts: dict, key: str) -> dict:
    """Mark a stored run as the one downstream stages use (``facts["scores"]`` mirrors it)."""
    out = dict(facts)
    run = next((r for r in runs_of(out) if r["run_key"] == key), None)
    if run is None:
        raise KeyError(f"no Stage 4 run {key!r}; stored: {[r['run_key'] for r in runs_of(out)]}")
    out["scores_active"] = key
    out["scores"] = dict(run)
    return out


def compare_runs(a: dict, b: dict, top_n: int = 10) -> dict:
    """Run A vs run B on the same candidates: rejected in one only, each run's top order among passing candidates,
    B's removed_needed / kept_dead lists."""
    sa = {s["id"]: s for s in a.get("candidates") or []}
    sb = {s["id"]: s for s in b.get("candidates") or []}
    rej_a = {i for i, s in sa.items() if s["status"] == "rejected"}
    rej_b = {i for i, s in sb.items() if s["status"] == "rejected"}
    def top(sx):
        ok = [s for s in sx.values() if s["status"] == "pass" and s.get("eligible_for_final", True)]
        return [{"id": s["id"], "type": s["type"], "variant": s["variant"], "combined": s["combined"]} for s in sorted(ok, key=lambda s: (-s["combined"], s["id"]))[:top_n]]
    def why(s):
        return ", ".join(f"{v['rule']} ({v['dimension']} {v.get('score')})" for v in s["vetoes"])
    return {"a": a.get("run_key"), "b": b.get("run_key"), "common": sorted(set(sa) & set(sb)),
            "rejected_a_only": [{"id": i, "why_a": why(sa[i]), "status_b": sb[i]["status"] if i in sb else None} for i in sorted(rej_a - rej_b)],
            "rejected_b_only": [{"id": i, "why_b": why(sb[i]), "status_a": sa[i]["status"] if i in sa else None} for i in sorted(rej_b - rej_a)],
            "top_a": top(sa), "top_b": top(sb),
            "b_removed_needed": [{"id": i, "items": s.get("removed_needed") or []} for i, s in sorted(sb.items()) if s.get("removed_needed")],
            "b_kept_dead": [{"id": i, "items": s.get("kept_dead") or []} for i, s in sorted(sb.items()) if s.get("kept_dead")]}


# ----------------------------------------------------------------------------------------------- run
# ----------------------------------------------------------------------------------------------- carry-over (changed only)
def content_key(c: dict) -> tuple:
    """What makes a candidate the same clip across Stage 3 reruns: scene, type, variant and the exact kept segments
    (ids may shift when a rerun adds or drops a variant)."""
    return (int(c["scene_id"]), c["type"], c["variant"], tuple((round(float(a), 3), round(float(b), 3)) for a, b in c["segments"]))


def carry_over(facts: dict, prev: dict | None) -> tuple[dict[str, dict], list[dict], dict[str, str]]:
    """founder, 2026-10-28: after a Stage 3 rerun, rescore only the candidates whose content changed and carry the
    others over from ``prev`` (the active run). Returns (carried: new id -> prev score entry re-id'ed, changed: the
    current candidates with no match, old->new id map for the matched ones)."""
    if not prev:
        return {}, list(facts.get("candidates") or []), {}
    prev_by_key: dict[tuple, dict] = {}
    for s in prev.get("candidates") or []:
        c = s.get("_content_key")
        if c is not None:
            prev_by_key[(int(c[0]), c[1], c[2], tuple((round(float(a), 3), round(float(b), 3)) for a, b in c[3]))] = s
    if not prev_by_key:                                      # a run made before content keys were stored: match by the stored ranges
        pc = {c["id"]: c for c in facts.get("_prev_candidates") or []}
        for s in prev.get("candidates") or []:
            if s["id"] in pc:
                prev_by_key[content_key(pc[s["id"]])] = s
    carried, changed, idmap = {}, [], {}
    for c in facts.get("candidates") or []:
        k = content_key(c)
        s = prev_by_key.get(k)
        if s is None:
            changed.append(c)
            continue
        e = dict(s); e["id"] = c["id"]; e["carried_from"] = prev.get("run_key"); e["_content_key"] = list(k[:3]) + [[list(x) for x in k[3]]]
        carried[c["id"]] = e
        idmap[s["id"]] = c["id"]
    return carried, changed, idmap


def changed_scenes(facts: dict, prev: dict | None) -> list[tuple[dict, list[dict], list[dict]]]:
    """[(sheet, candidates to send, changed ones)] per scene with at least one changed candidate: the changed
    candidates plus the sibling each is judged against (so verdict_vs_sibling has both sides)."""
    carried, changed, _ = carry_over(facts, prev)
    by_id = {c["id"]: c for c in facts.get("candidates") or []}
    out = []
    for sh, cands in scenes_with_candidates(facts):
        ch = [c for c in cands if c["id"] in {x["id"] for x in changed}]
        if not ch:
            continue
        ids = {c["id"] for c in ch}
        for c in ch:
            for sid in (sibling_of(c), c.get("trimmed_sibling"), c.get("untrimmed_sibling")):
                if sid and sid in by_id and by_id[sid]["scene_id"] == sh["scene_id"]:
                    ids.add(sid)
        out.append((sh, [c for c in cands if c["id"] in ids], ch))
    return out


def run(inputs, facts: dict | None, cfg: dict, client=None, scene_ids=None, log=None, media=None, cutter=None, uploader=None, deleter=None,
        changed_only: bool = False, text_only: bool = False, exclude_ids=None) -> dict:
    """``text_only`` (founder, 2026-10-30): every call of this run is text only (no scene video), e.g. a cheap rescore of a few
    rebuilt candidates; the run key keeps the episode's media mode so the run carries from and replaces the active one.
    ``exclude_ids`` (founder, 2026-10-30): candidates left unscored in this run (they stay out of Stages 5-6 until scored)."""
    t0 = time.perf_counter()
    log = log or (lambda *a: None)
    check_stage3(facts)
    assert facts is not None
    cfg4 = cfg["stage4"]
    spec = ModelSpec.from_dict(cfg["models"].get("stage4") or cfg["models"]["default"])
    run_dir = Path(inputs.paths.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    prefix2 = prefix2_text(facts, cfg, run_dir)
    sections = load_prompt(PROMPT_PATH)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if client is None:
        from shotvi.llm.client import Ledger, LLMClient, get_provider
        client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"), max_retries=int(cfg4.get("http_retries", 1)))
        try:
            get_provider(spec.provider).timeout = float(cfg4.get("http_timeout_s", 600))
        except Exception:
            pass
    r, rate = cfg["rates"], inr_per_usd(cfg)
    facts = dict(facts)
    mode = media_mode(facts, cfg, media)
    key = run_key(mode, facts)
    runs = [dict(x) for x in runs_of(facts)]
    prev = next((x for x in runs if x["run_key"] == key and x.get("version") == VERSION), {})
    done = {s["scene_id"] for s in prev.get("per_scene") or []}
    scored: dict[str, dict] = {s["id"]: s for s in (prev.get("candidates") or [])} if done else {}
    per_scene: list[dict] = list(prev.get("per_scene") or []) if done else []
    pairs = scenes_with_candidates(facts)
    cutter = cutter or cut_scene
    uploader = uploader or upload
    media_dir = run_dir / cfg["stage2"]["media_dir"]
    todo = [(sh, cs) for sh, cs in pairs if (scene_ids is None and sh["scene_id"] not in done) or (scene_ids is not None and sh["scene_id"] in set(scene_ids))]
    carried_from, n_carried = None, 0
    if changed_only:
        # founder, 2026-10-28: after a Stage 3 rerun, rescore only the changed candidates (+ their siblings) and carry
        # the rest over from the ACTIVE run; the carried answers are re-vetoed with everything else below
        base = active_run(facts)
        if not base:
            raise RuntimeError("changed-only rescoring needs an active Stage 4 run to carry scores from")
        carried, changed, _ = carry_over(facts, base)
        if not changed:
            raise RuntimeError("no candidate changed since the active Stage 4 run: nothing to rescore")
        for cid, e in carried.items():
            if cid not in scored:
                scored[cid] = e
        carried_from, n_carried = base["run_key"], len(carried)
        by_scene_rows = {x["scene_id"]: x for x in base.get("per_scene") or []}
        have_rows = {x["scene_id"] for x in per_scene}
        for sid in sorted({cands[c]["scene_id"] for c in carried for cands in [{c["id"]: c for c in facts["candidates"]}]}):
            if sid not in have_rows and sid in by_scene_rows:
                per_scene.append({**by_scene_rows[sid], "n_candidates": sum(1 for c in carried.values() if c["scene_id"] == sid), "cost_inr": 0.0,
                                  "carried_from": base["run_key"]})
        skip = set(exclude_ids or [])
        todo = [(sh, [c for c in send if c["id"] not in skip]) for sh, send, ch in changed_scenes(facts, base)
                if scene_ids is None or sh["scene_id"] in set(scene_ids)]
        todo = [(sh, cs) for sh, cs in todo if cs]
    if not todo:
        raise RuntimeError("no scenes to score: " + ("every scene is already scored for the current Stage 3 output (pick scene ids to rerun)" if scene_ids is None else f"scene ids {scene_ids} have no candidates"))
    debug_path = run_dir / "stage4_debug.json"
    debug = json.loads(debug_path.read_text(encoding="utf-8")) if debug_path.exists() else {"scenes": {}}
    debug.update({"model": spec.to_dict(), "system": sections["system"], "prefix_file": "prefix_stage2.txt", "prefix_chars": len(prefix2)})
    usage_tot = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0}
    taste = taste_block(facts, cfg)
    row_acc: dict[int, dict] = {}
    for sh, cs, mode_c in [(sh, g, m) for sh, cs in todo for m, g in call_groups(cs, "none" if text_only else mode)]:
        ts = time.perf_counter()
        text = scene_prompt(facts, sh, cs, mode_c, taste)
        parts = [{"text": prefix2}]
        up, mpath = None, None
        if mode_c == "video":
            log(f"scene {sh['scene_id']}: scene video (cached cut, upload reused while it exists)")
            mpath = cutter(Path(inputs.paths.mp4), media_dir, sh["scene_id"], float(sh["start"]), float(sh["end"]), "video", int(cfg["stage4"]["media_max_height"]), log)
            up = uploader(mpath, "video")
            parts.append(media_part(up, "video", cfg["stage4"].get("media_fps")))
        attempts, errors, ans = [], [], None
        for attempt in range(int(cfg4.get("retries", 1)) + 1):
            instr = (sections["instructions"] + "\n\n" + sections["media" if mode_c == "video" else "no_media"]
                     + ("\n\n" + sections["retry"].replace("{errors}", "\n".join(f"- {e}" for e in errors)) if errors else "") + "\n\n" + sections["output_format"])
            prompt = text + "\n\n" + instr
            att = {"n": attempt + 1, "scene_text": text, "instructions": instr, "errors_shown": errors, "media": mode_c,
                   "media_file": {"path": str(mpath), "uri": (up or {}).get("uri")} if up else None,
                   "part_order": ["prefix_stage2.txt"] + (["scene video"] if mode_c == "video" else []) + ["scene beat sheet + candidates + instructions"]}
            attempts.append(att)
            log(f"scene {sh['scene_id']}: scoring {len(cs)} candidates, call {attempt + 1}")
            try:
                obj, raw = client.generate_json(ROLE, spec, sections["system"], prompt, schema, meta={"video_id": inputs.vid, "stage": "4", "scene": sh["scene_id"], "media": mode_c},
                                                want_raw=True, media=parts)
            except BaseException as e:
                att["error"] = f"{type(e).__name__}: {e}"
                debug["scenes"][f"{mode_c}:{sh['scene_id']}"] = {"attempts": attempts}
                write_json(debug_path, debug)
                append_log(cfg, {"episode": inputs.vid, "stage": f"4:scene{sh['scene_id']}", "status": "failed", "duration_s": round(time.perf_counter() - ts, 1), "note": att["error"][:300]})
                raise
            att["raw_text"] = raw.text
            att["usage"] = {"input_tokens": raw.input_tokens, "output_tokens": raw.output_tokens, "thought_tokens": raw.thought_tokens, "cached_tokens": raw.cached_tokens}
            for k in usage_tot:
                usage_tot[k] += int(getattr(raw, k) or 0)
            errors = schema_errors(obj) + validate(obj, cs)
            att["validation"] = errors
            if not errors:
                ans = obj
                break
        if up is not None and cfg["stage4"].get("delete_upload_after_call", False):
            try:
                from .media import delete_upload
                attempts[-1]["upload_deleted"] = (deleter or delete_upload)(up)
            except Exception as e:  # noqa: BLE001
                attempts[-1]["upload_deleted"] = f"failed: {type(e).__name__}: {e}"
        debug["scenes"][f"{mode_c}:{sh['scene_id']}"] = {"attempts": attempts}
        write_json(debug_path, debug)
        in_tok = sum(a["usage"]["input_tokens"] for a in attempts if a.get("usage"))
        cached = sum(a["usage"]["cached_tokens"] for a in attempts if a.get("usage"))
        out_tok = sum(a["usage"]["output_tokens"] + a["usage"]["thought_tokens"] for a in attempts if a.get("usage"))
        cost = round(((in_tok - min(cached, in_tok)) * r["usd_per_1m_input"] + min(cached, in_tok) * r["usd_per_1m_cached_input"] + out_tok * r["usd_per_1m_output"]) / 1e6 * rate, 3)
        row = {"scene_id": sh["scene_id"], "n_candidates": len(cs), "attempts": len(attempts), "input_tokens": in_tok, "cached_tokens": cached,
               "output_tokens": out_tok, "cost_inr": cost, "duration_s": round(time.perf_counter() - ts, 1), "media": mode_c}
        if ans is None:
            append_log(cfg, {"episode": inputs.vid, "stage": f"4:scene{sh['scene_id']}", "status": "failed", "duration_s": row["duration_s"], "input_tokens": in_tok,
                             "output_tokens": out_tok, "cached_tokens": cached, "cost_inr_est": cost, "note": "validation failed after retry: " + " | ".join(errors[:5])})
            raise ScoringError(f"scene {sh['scene_id']}: the answer failed validation after {len(attempts)} attempts: " + " | ".join(errors[:6]))
        answers = {e["id"]: e for e in ans["candidates"]}
        for s in score_candidates(cs, answers, cfg):
            s["scene_usage"] = {"cost_inr": cost, "attempts": len(attempts)}
            s.pop("carried_from", None)
            scored[s["id"]] = s
        if sh["scene_id"] in row_acc:                                # a second call for the scene (critic_trimmed, text only)
            acc = row_acc[sh["scene_id"]]
            for k in ("n_candidates", "attempts", "input_tokens", "cached_tokens", "output_tokens", "cost_inr", "duration_s"):
                acc[k] = round(acc[k] + row[k], 3)
            acc["media"] = f"{acc['media']}+{row['media']}"
            row = acc
        row_acc[sh["scene_id"]] = row
        per_scene = [x for x in per_scene if x["scene_id"] != sh["scene_id"]] + [row]
        append_log(cfg, {"episode": inputs.vid, "stage": f"4:scene{sh['scene_id']}", "status": "ok", "duration_s": row["duration_s"], "input_tokens": in_tok,
                         "output_tokens": out_tok, "cached_tokens": cached, "cost_inr_est": cost, "note": f"{len(cs)} candidates scored, media {mode_c}"})
        log(f"scene {sh['scene_id']}: done, ₹{cost:.2f}")
    # the standalone widening may point at a sibling scored in another call of the same scene: recompute over everything scored
    all_c = [c for c in facts["candidates"] if c["id"] in scored]
    answers_all = {}
    for s in scored.values():
        answers_all[s["id"]] = {**{d: {"score": s["scores"][d], "evidence": s["evidence"].get(d, "")} for d in s["scores"]},
                                "pace": {"score": s["scores"]["pace"], "evidence": s["evidence"].get("pace", ""), "dead_stretches": s["dead_stretches"]},
                                "fan_clip": s["fan_clip"], "verdict_vs_sibling": s["verdict_vs_sibling"],
                                "removed_needed": s.get("removed_needed") or [], "kept_dead": s.get("kept_dead") or []}
    usage_by_id = {s["id"]: s.get("scene_usage") for s in scored.values()}
    carried_by_id = {s["id"]: s.get("carried_from") for s in scored.values()}
    final = score_candidates(all_c, answers_all, cfg)
    cand_by_id = {c["id"]: c for c in facts["candidates"]}
    for s in final:
        s["scene_usage"] = usage_by_id.get(s["id"])
        k = content_key(cand_by_id[s["id"]]); s["_content_key"] = list(k[:3]) + [[list(x) for x in k[3]]]
        if carried_by_id.get(s["id"]):
            s["carried_from"] = carried_by_id[s["id"]]
    total_cost = round(sum(x["cost_inr"] for x in per_scene), 3)
    statuses = {}
    for s in final:
        statuses[s["status"]] = statuses.get(s["status"], 0) + 1
    this_run = {"run_key": key, "media": mode, "version": VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": spec.to_dict(),
                "prompt_file": PROMPT_PATH.name, "stage3_generated_at": facts["stages"]["3"]["generated_at"],
                "candidates": sorted(final, key=lambda s: (s["scene_id"], s["id"])), "per_scene": sorted(per_scene, key=lambda x: x["scene_id"]),
                "statuses": statuses, "scenes_scored": sorted(x["scene_id"] for x in per_scene), "n_scenes": len(pairs),
                "usage": {"cost_inr": total_cost}, "carried_from": carried_from, "n_carried": n_carried,
                "n_rescored": sum(len(cs) for _, cs in todo)}
    runs = [x for x in runs if x["run_key"] != key] + [this_run]
    facts["score_runs"] = runs
    if facts.get("scores_active") not in {x["run_key"] for x in runs} or changed_only:
        facts["scores_active"] = key                                  # the first / only run is active until the founder marks another; a
                                                                      # changed-only run supersedes the active run it carried from
    facts["scores"] = dict(next(x for x in runs if x["run_key"] == facts["scores_active"]))
    facts.setdefault("stages", {})["4"] = {
        "version": VERSION, "model": spec.to_dict(), "prompt_file": PROMPT_PATH.name, "config_sha8": cfg.get("_sha8"),
        "generated_at": facts["scores"]["generated_at"], "duration_s": round(time.perf_counter() - t0, 3),
        "usage": {"input_tokens": sum(x["input_tokens"] for x in per_scene), "output_tokens": sum(x["output_tokens"] for x in per_scene),
                  "cached_tokens": sum(x["cached_tokens"] for x in per_scene), "cost_inr": total_cost},
        "per_scene": facts["scores"]["per_scene"], "warnings": [] if len(per_scene) == len(pairs) else [f"{len(pairs) - len(per_scene)} scene(s) not scored yet"],
        "summary": {"scored": len(final), "statuses": statuses, "scenes_scored": len(per_scene), "n_scenes": len(pairs), "media": mode,
                    "runs": [x["run_key"] for x in runs], "active": facts["scores_active"]},
        "prefix_file": "prefix_stage2.txt", "debug_file": "stage4_debug.json"}
    return facts
