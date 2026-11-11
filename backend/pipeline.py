"""One episode, from a YouTube link to clips, for the worker.

Steps (``STEPS``; each is skipped when its output already exists, so a retried or resumed job starts where it stopped):

  download, transcribe, shots, word_fix, speaker_fix   shotvi.run_episode's step classes (yt-dlp, ElevenLabs Scribe,
                                                        ffmpeg shots, Gemini word fix, Gemini speaker fix)
  text, video, notes, find, rank                        selection_v2.flow_v2 (the web-series flow, v1 settings)
  save                                                  the clips into the database (queue.save_clips)

Money: customers are limited by their minutes (queue.py), never by our cost. Our cost is guarded by the worker's
runaway check (``add_cost`` raises once a run passes RUNAWAY_INR_PER_MIN x minutes) and, in development, by the
project-wide Gemini ledger cap (shotvi/llm/spend.py). Cost per run = Gemini rupees of the steps that ran + Scribe minutes x ``SCRIBE_INR_PER_MIN``.
NOTE: Gemini rupees of every paid step are measured as ledger deltas (per-model prices, cache storage included), correct while ONE worker runs at a time; with
several workers the ledger has to move into the database first.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from shotvi import run_episode as RE

ROOT = Path(__file__).resolve().parents[1]

STEPS: list[tuple[str, str]] = [
    ("download", "Downloading the episode"),
    ("transcribe", "Writing down every word"),
    ("shots", "Finding the shot changes"),
    ("word_fix", "Fixing misheard words"),
    ("speaker_fix", "Working out who says each line"),
    ("text", "Reading the story"),
    ("video", "Watching the episode"),
    ("notes", "Understanding each scene"),
    ("find", "Finding the clips"),
    ("rank", "Choosing the posting order"),
    ("save", "Saving the clips"),
]
PREP = {"download": RE.Download, "transcribe": RE.Transcribe, "shots": RE.Shots,
        "word_fix": RE.AsrFix, "speaker_fix": RE.SpeakerFix}
FLOW = ("text", "video", "notes", "find", "rank")
PAID = {"transcribe", "word_fix", "speaker_fix", *FLOW}     # download (first) and shots are free


def _swap_models(node, model: str) -> None:
    """Every Gemini model spec in the config -> ``model`` (test mode)."""
    if isinstance(node, dict):
        if node.get("provider") == "gemini" and "model" in node:
            node["model"] = model
        for v in node.values():
            _swap_models(v, model)
    elif isinstance(node, list):
        for v in node:
            _swap_models(v, model)


def flow_cfg(vid: str) -> dict:
    """The flow's config. Test mode (``KLIPVI_TEST_MODEL`` set, founder 2026-11-11): every flow step uses that cheap
    model, for plumbing tests of the worker and the database only; never for clips a customer sees."""
    from selection_v2 import flow_v2 as FV
    cfg = FV.cfg_for(vid)
    m = os.environ.get("KLIPVI_TEST_MODEL", "").strip()
    if m:
        _swap_models(cfg, m)
    return cfg


def scribe_inr_per_min() -> float:
    try:
        return float(os.environ.get("SCRIBE_INR_PER_MIN", "1.1"))
    except ValueError:
        return 1.1


def _ctx(vid: str, url: str) -> RE.Ctx:
    return RE.Ctx(p=RE.Paths(vid, ROOT / "runs" / "klipvi" / vid), url=url, dry_run=False,
                  limit_inr=RE.spend_cap_inr(), skip_asr_fix=False, ledger0=RE.ledger_inr(), speaker_fix=True)


def pending(vid: str, url: str = "") -> list[str]:
    """Steps whose output does not exist yet (``save`` is always pending). Free: reads files only."""
    from selection_v2 import flow_v2 as FV
    ctx = _ctx(vid, url or vid)
    out = [k for k, cls in PREP.items() if not cls().done(ctx.p)]
    have = FV.done(FV.load(vid, FV.cfg_for(vid)))
    out += [k for k in FLOW if not have[k]]
    return out + ["save"]


def source_length_s(vid: str, url: str) -> float:
    """The episode's length before anything is downloaded: the local file when we have it, else YouTube's metadata
    (yt-dlp, no download, ~1 s). Used at submit to check and hold the customer's minutes."""
    p = RE.Paths(vid, ROOT)
    if p.wav.exists() or p.mp4.exists():
        return length_s(vid)
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as y:
        info = y.extract_info(url, download=False)
    if not info.get("duration"):
        raise RuntimeError(f"YouTube gave no length for {url}")
    return float(info["duration"])


def length_s(vid: str) -> float:
    p = RE.Paths(vid, ROOT)
    return RE.media_duration_s(p.wav if p.wav.exists() else p.mp4)


def clip_rows(flow: dict) -> list[dict]:
    """flow_v2.json clips (+ Stage 4 ranking) -> rows for public.clips, in posting order."""
    clips = list((flow.get("clips") or {}).get("clips") or [])
    rk = flow.get("ranking") or {}
    at = {cid: i for i, cid in enumerate(rk.get("order") or [])}
    clips.sort(key=lambda c: at.get(c["id"], len(at)))
    post_first = set(rk.get("post_first") or [])
    rows = []
    for i, c in enumerate(clips, 1):
        rows.append({
            "number": c["id"], "rank": i, "post_first": c["id"] in post_first,
            "title": c.get("title"), "hook": c.get("hook"), "posting_text": None,
            "why": c.get("why"),
            "start_s": round(float(c["start_time"]), 2), "end_s": round(float(c["end_time"]), 2),
            "length_s": round(float(c["duration"]), 2),
            "pieces": [{"start": p["start"], "end": p["end"], "lines": p.get("lines", [])} for p in c["pieces"]],
            "strength": c.get("strength"),
        })
    return rows


def run(episode: dict, on_step: Callable[[int, str, str], None],
        add_cost: Callable[[float], None], save: Callable[[list[dict], dict], int],
        log: Callable[..., None] = print) -> dict:
    """Run every pending step for ``episode`` (a public.episodes row). Returns {"minutes", "clips", "ran"}."""
    from selection_v2 import flow_v2 as FV
    vid, url = episode["video_id"], episode["source_url"] or episode["video_id"]
    ctx = _ctx(vid, url)
    todo = set(pending(vid, url))
    ran, n = [], len(STEPS)
    for i, (key, label) in enumerate(STEPS, 1):
        if key not in todo:
            continue
        on_step(i, key, label)
        if key in PREP:
            step = PREP[key]()
            led0 = RE.ledger_inr()
            RE.run_step(i, step, ctx, n)
            if step.paid:
                add_cost(RE.ledger_inr() - led0)
            if key == "transcribe":
                add_cost(length_s(vid) / 60.0 * scribe_inr_per_min())
        elif key in FLOW:
            led0 = RE.ledger_inr()
            res = FV.run(vid, key, cfg=flow_cfg(vid), log=log)
            spent = RE.ledger_inr() - led0      # the ledger prices each call (and cache storage) at its own model's rate;
            add_cost(spent)                     # the step's own figure uses the config's 3.8 Flash rates
            log(f"    cost ₹{spent:.2f} (step's own estimate ₹{float(res.get('cost_inr') or 0.0):.2f})")
            for w in res.get("warnings") or []:
                log(f"    warning: {w}")
        else:   # save
            flow = FV.load(vid, FV.cfg_for(vid)) or {}
            rows = clip_rows(flow)
            rk = flow.get("ranking") or {}
            meta = RE.Paths(vid, ROOT).meta
            title = None
            if meta.exists():
                import json
                title = json.loads(meta.read_text(encoding="utf-8")).get("title")
            upd = {"length_s": round(length_s(vid), 2), "verdict": rk.get("verdict"), "verdict_why": rk.get("verdict_why")}
            if title and not episode.get("title"):
                upd["title"] = title
            save(rows, upd)
            log(f"    saved {len(rows)} clips")
        ran.append(key)
    return {"minutes": length_s(vid) / 60.0, "ran": ran}
