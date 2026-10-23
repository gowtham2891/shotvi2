"""Blind audio word-accuracy judge: which of two transcripts of the same episode (Scribe vs Sarvam) hears the words
right, over the WHOLE episode.

Pairing (the scratch ``word_compare.py`` logic, as a module): every raw Scribe word goes to the Sarvam segment it
overlaps most in time (first one on ties; a word overlapping none is an "orphan"). Both sides are tokenised the same
way (lower case, punctuation stripped, split on spaces and hyphens) and aligned with a plain edit distance; a
segment "differs" when any token differs. Then one repair the scratch script did not have: a word near a segment
edge is sometimes placed in the neighbouring segment by one transcriber and not the other, which shows up as one
"missing" word in each of two segments. Adjacent differing atoms (segments, orphan groups) are merged when the
merged edit distance is SMALLER than the two separate ones (and the gap is <= ``MERGE_GAP_S``, the merged span <=
``MAX_UNIT_S``); a pure boundary migration then stops differing at all. ``pairing()`` reports both counts.

Judge: for every differing unit, the audio of [min(segment start, first Scribe word) - 0.3 s, max(segment end,
last Scribe word) + 0.3 s] (cut from the episode WAV, sent inline) plus version A and version B. Which of Scribe /
Sarvam is A is a seeded coin per unit (sha256 of seed + video + unit key: stable whatever the order or batch), and
the model never sees the words Scribe or Sarvam. Units are shuffled (seeded) before batching, so a run stopped by
the spend guard is a random sample of the episode, not its first minutes. ``prompts/word_judge.md``; one attempt per
batch; a safety block or a failed call marks the batch's units ``unchecked``.

Spend: ``--max-ledger-inr`` is checked before EVERY billed call against that call's worst case (input + audio at
the audio rate + ``max_output_tokens``), on the project ledger (``shotvi/llm/spend.py``; set SHOTVI_SPEND_SOURCE).
Audio input is billed at ``asr_fix.AUDIO_PRICE_FACTOR`` x the text rate; the surcharge is recorded in the rupee
ledger after each fresh call, as asr_fix does. ``--dry-run`` makes no generate call: it counts the exact input
tokens of every batch request (audio included) with Gemini countTokens (free), or estimates them (text by
characters, audio at 32 tokens/s) with ``--no-count-tokens`` / when countTokens fails, and says which.

Report: totals over judged units (Scribe / Sarvam real errors, which is better, ties, unclear), error rate per 100
words where words = each system's OWN token count over the whole episode and units where both agree count as 0
errors for both (an error both make is invisible here: absolute rates are lower bounds; the comparison is what
counts), error types, where the ASR-fix layer (runs/asr_fix/<id>/corrections.json) changed a Scribe word inside a
judged unit, a TSV of every unit, and a blind local review page (30 random units: audio, A, B, buttons, CSV export)
for a native speaker to check the judge (``--human-csv`` compares an exported CSV with the judge).

CLI::

    python -m eval.word_judge p3XTZlZ6-xw --dry-run              # calls, Rs expected / worst (countTokens, free)
    SHOTVI_SPEND_SOURCE=word_judge python -m eval.word_judge p3XTZlZ6-xw --max-ledger-inr 1800
    python -m eval.word_judge p3XTZlZ6-xw --human-csv ~/Downloads/word_judge_p3XTZlZ6-xw.csv   # replays the cache
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import json
import os
import random
import re
import sys
import time
from bisect import bisect_left, bisect_right
from collections import Counter
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent
PROMPT = ROOT / "prompts" / "word_judge.md"
OUT_DIR = ROOT / "runs" / "word_judge"
SCHEMA_VERSION = "word_judge/1"
ROLE = "word_judge"

PAD_S = 0.3
MERGE_GAP_S = 1.0          # adjacent atoms further apart than this are never merged
MAX_UNIT_S = 30.0          # a merged unit never spans more than this
MAX_WORD_S = 2.0           # a Scribe word's end counts at most this far after its start (audio span only)
ORPHAN_GAP_S = 1.0         # consecutive orphan Scribe words closer than this form one orphan atom
BATCH_ITEMS = 13           # clips per call (task: 12-15)
BATCH_AUDIO_S = 120.0      # audio seconds per call
AUDIO_TOKENS_PER_S = 32    # Gemini audio input rate (docs; matches asr_fix relisten usage: 2105 tokens / 66 s)
TEXT_CAL = 1.25            # estimate_tokens under-counts (asr_fix, checked with countTokens)
EXP_OUT_PER_CLIP = 110     # expected visible output per clip (heard_text + error items); an assumption
EXP_OUT_PER_CALL = 1200    # expected thinking (LOW) + JSON overhead per call; an assumption
MODEL = {"provider": "gemini", "model": "gemini-3.8-flash", "params": {"thinking_level": "low",
                                                                        "max_output_tokens": 8192}}
REVIEW_N = 30
EMPTY = "(nothing)"

_PUNCT = re.compile(r"[.,?!।\"'“”‘’…:;()\[\]{}]")

ERROR_ITEM = {"type": "object",
              "properties": {"type": {"type": "string", "enum": ["wrong", "missing", "added"]},
                             "written": {"type": "string"}, "said": {"type": "string"}},
              "required": ["type", "written", "said"]}
SCHEMA = {
    "type": "object",
    "properties": {"clips": {"type": "array", "items": {
        "type": "object",
        "properties": {"clip": {"type": "integer"}, "heard_text": {"type": "string"},
                       "a_errors": {"type": "integer", "minimum": 0}, "b_errors": {"type": "integer", "minimum": 0},
                       "a_error_words": {"type": "array", "items": ERROR_ITEM},
                       "b_error_words": {"type": "array", "items": ERROR_ITEM},
                       "a_filler_diff": {"type": "integer", "minimum": 0},
                       "b_filler_diff": {"type": "integer", "minimum": 0},
                       "filler_only": {"type": "boolean"}, "unclear": {"type": "boolean"}},
        "required": ["clip", "heard_text", "a_errors", "b_errors", "a_error_words", "b_error_words",
                     "a_filler_diff", "b_filler_diff", "filler_only", "unclear"]}}},
    "required": ["clips"],
}


class WordJudgeStop(RuntimeError):
    """The next billed call's worst case would push the project ledger past --max-ledger-inr."""


# ================================================================== pairing
def toks(s: str) -> list[str]:
    return [t for t in re.split(r"[\s\-]+", _PUNCT.sub("", (s or "").lower())) if t]


def scribe_words(tr: dict) -> list[dict]:
    """Raw Scribe words (no ASR-fix layer) with their ORIGINAL word_timestamps index ``i``."""
    return [{"i": i, "word": w["word"], "start": float(w["start"]), "end": float(w["end"])}
            for i, w in enumerate(tr["word_timestamps"])
            if w.get("type", "word") == "word" and str(w.get("word", "")).strip()]


def sarvam_segments(raw: dict) -> list[dict]:
    es = (raw.get("diarized_transcript") or {}).get("entries") or []
    segs = sorted(({"start": float(e["start_time_seconds"]), "end": float(e["end_time_seconds"]),
                    "text": e.get("transcript", ""), "speaker": e.get("speaker_id")} for e in es),
                  key=lambda e: e["start"])
    for k, s in enumerate(segs):
        s["k"] = k
    return segs


def assign_words(words: list[dict], segs: list[dict]) -> tuple[dict[int, list[int]], list[int]]:
    """{segment k: [positions in ``words``]}, orphan positions. Same rule as word_compare.py (largest positive time
    overlap, first segment on ties), with a bisect window instead of a scan from 0."""
    starts = [s["start"] for s in segs]
    max_len = max((s["end"] - s["start"] for s in segs), default=0.0)
    own: dict[int, list[int]] = {}
    orphan: list[int] = []
    for p, w in enumerate(words):
        best, ov = None, 0.0
        lo = bisect_left(starts, w["start"] - max_len - 1e-9)
        hi = bisect_right(starts, w["end"] + 1)
        for k in range(lo, hi):
            e = segs[k]
            o = min(w["end"], e["end"]) - max(w["start"], e["start"])
            if o > ov:
                best, ov = k, o
        if best is None:
            orphan.append(p)
        else:
            own.setdefault(best, []).append(p)
    return own, orphan


def align(a: list[str], b: list[str]) -> list[tuple[str, str, str]]:
    """Edit-distance alignment: ops ``ok`` / ``sub`` / ``scribe_only`` (a only) / ``sarvam_only`` (b only)."""
    n, m = len(a), len(b)
    D = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        D[i][0] = i
    for j in range(m + 1):
        D[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i][j] = min(D[i - 1][j] + 1, D[i][j - 1] + 1, D[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
    i, j, ops = n, m, []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and D[i][j] == D[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            ops.append(("ok" if a[i - 1] == b[j - 1] else "sub", a[i - 1], b[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and D[i][j] == D[i - 1][j] + 1:
            ops.append(("scribe_only", a[i - 1], ""))
            i -= 1
        else:
            ops.append(("sarvam_only", "", b[j - 1]))
            j -= 1
    return ops[::-1]


def cost(a: list[str], b: list[str]) -> int:
    return sum(op != "ok" for op, _, _ in align(a, b))


def _atom(words, seg=None, pos=()) -> dict:
    ws = [words[p] for p in pos]
    st = [x["start"] for x in ws] + ([seg["start"]] if seg else [])
    # Scribe sometimes stretches a word's end over the silence or music after it (38 s seen): cap it for the span
    en = [min(x["end"], x["start"] + MAX_WORD_S) for x in ws] + ([seg["end"]] if seg else [])
    return {"seg_ids": [seg["k"]] if seg else [], "orphan": seg is None,
            "word_idx": [x["i"] for x in ws], "scribe_toks": [t for x in ws for t in toks(x["word"])],
            "sarvam_toks": toks(seg["text"]) if seg else [], "sarvam_raw": [seg["text"]] if seg else [],
            "t0": min(st), "t1": max(en)}


def _merge(a: dict, b: dict) -> dict:
    return {"seg_ids": a["seg_ids"] + b["seg_ids"], "orphan": a["orphan"] and b["orphan"],
            "word_idx": a["word_idx"] + b["word_idx"], "scribe_toks": a["scribe_toks"] + b["scribe_toks"],
            "sarvam_toks": a["sarvam_toks"] + b["sarvam_toks"], "sarvam_raw": a["sarvam_raw"] + b["sarvam_raw"],
            "t0": min(a["t0"], b["t0"]), "t1": max(a["t1"], b["t1"])}


def unit_key(u: dict) -> str:
    if u["seg_ids"]:
        return "s" + "+".join(str(k) for k in u["seg_ids"]) + ("+o" if any_orphan(u) else "")
    return f"o{u['word_idx'][0]}-{u['word_idx'][-1]}"


def any_orphan(u: dict) -> bool:
    return bool(u.get("orphan_words"))


def pairing(tr: dict, sarvam_raw: dict, merge: bool = True) -> dict:
    """Units covering the whole episode (every Scribe word and every Sarvam segment in exactly one unit)."""
    words = scribe_words(tr)
    segs = sarvam_segments(sarvam_raw)
    own, orphan = assign_words(words, segs)
    atoms = []
    raw_differ = 0
    for s in segs:
        a = _atom(words, s, own.get(s["k"], []))
        a["cost"] = cost(a["scribe_toks"], a["sarvam_toks"])
        raw_differ += a["cost"] > 0
        atoms.append(a)
    groups: list[list[int]] = []
    for p in orphan:
        if groups and words[p]["start"] - words[groups[-1][-1]]["end"] <= ORPHAN_GAP_S and p == groups[-1][-1] + 1:
            groups[-1].append(p)
        else:
            groups.append([p])
    for g in groups:
        a = _atom(words, None, g)
        a["cost"] = len(a["scribe_toks"])
        a["orphan_words"] = list(a["word_idx"])
        atoms.append(a)
    atoms.sort(key=lambda a: (a["t0"], a["t1"]))
    by_i = {w["i"]: w for w in words}
    units: list[dict] = []
    n_merges = 0
    for a in atoms:
        if merge and units:
            u = units[-1]
            if (u["cost"] > 0 and a["cost"] > 0 and a["t0"] - u["t1"] <= MERGE_GAP_S
                    and max(u["t1"], a["t1"]) - min(u["t0"], a["t0"]) <= MAX_UNIT_S):
                m = _merge(u, a)
                m["cost"] = cost([t for i in sorted(m["word_idx"]) for t in toks(by_i[i]["word"])], m["sarvam_toks"])
                if m["cost"] < u["cost"] + a["cost"]:
                    m["orphan_words"] = u.get("orphan_words", []) + a.get("orphan_words", [])
                    m["merged_from"] = u.get("merged_from", 1) + a.get("merged_from", 1)
                    units[-1] = m
                    n_merges += 1
                    continue
        units.append(a)
    for n, u in enumerate(units):
        u["word_idx"] = sorted(u["word_idx"])
        u["scribe_toks"] = [t for i in u["word_idx"] for t in toks(by_i[i]["word"])]
        ops = align(u["scribe_toks"], u["sarvam_toks"])
        u["ops"] = [list(o) for o in ops if o[0] != "ok"]
        u["cost"] = len(u["ops"])
        u["differs"] = u["cost"] > 0
        u["id"] = f"u{n:03d}"
        u["key"] = unit_key(u)
        u["scribe_text"] = " ".join(u["scribe_toks"])
        u["sarvam_text"] = " ".join(u["sarvam_toks"])
        u["clip_t0"] = round(max(0.0, u["t0"] - PAD_S), 2)
        u["clip_t1"] = round(u["t1"] + PAD_S, 2)
    return {"units": units, "n_scribe_words": len(words), "n_sarvam_segments": len(segs),
            "scribe_tokens": sum(len(toks(w["word"])) for w in words),
            "sarvam_tokens": sum(len(toks(s["text"])) for s in segs),
            "raw_segments_differ": raw_differ, "orphan_words": len(orphan), "orphan_groups": len(groups),
            "merges": n_merges, "units_differ": sum(u["differs"] for u in units)}


# ================================================================== blind assignment
def scribe_is_a(seed: int, video_id: str, key: str) -> bool:
    h = hashlib.sha256(f"{seed}|{video_id}|{key}".encode("utf-8")).digest()
    return bool(h[0] & 1)


def versions(u: dict, seed: int, video_id: str) -> tuple[str, str, bool]:
    """(A text, B text, scribe_is_A)."""
    sa = scribe_is_a(seed, video_id, u["key"])
    s, v = u["scribe_text"] or EMPTY, u["sarvam_text"] or EMPTY
    return (s, v, True) if sa else (v, s, False)


# ================================================================== requests
def load_prompt(path: Path = PROMPT) -> tuple[str, str, str]:
    from shotvi.segment.asr_fix import _load_prompt
    return _load_prompt(path)


def judge_units(units: list[dict], seed: int) -> list[dict]:
    """Differing units in a seeded random order (a stopped run is a random sample)."""
    xs = [u for u in units if u["differs"]]
    xs.sort(key=lambda u: hashlib.sha256(f"order|{seed}|{u['key']}".encode()).hexdigest())
    return xs


def make_batches(units: list[dict], max_items: int = BATCH_ITEMS, max_audio_s: float = BATCH_AUDIO_S):
    out, cur, secs = [], [], 0.0
    for u in units:
        d = u["clip_t1"] - u["clip_t0"]
        if cur and (len(cur) >= max_items or secs + d > max_audio_s):
            out.append(cur)
            cur, secs = [], 0.0
        cur.append(u)
        secs += d
    if cur:
        out.append(cur)
    return out


def request(batch: list[dict], audio, video_id: str, seed: int, prompt_path: Path = PROMPT):
    """(system, user, media). Media alternate a text label and the clip's inline WAV (as asr_fix.relisten_request)."""
    from shotvi.segment.asr_fix import cut_audio
    _, system, user = load_prompt(prompt_path)
    blocks, media = [], []
    aname = Path(audio).name if audio is not None else "noaudio"
    for k, u in enumerate(batch, 1):
        a, b, _ = versions(u, seed, video_id)
        blocks.append(f"## Clip {k} ({u['clip_t1'] - u['clip_t0']:.1f} s of audio)\nA: {a}\nB: {b}")
        media.append({"text": f"Clip {k}:"})
        m = {"mime_type": "audio/wav", "key": f"{video_id}:{aname}:{u['clip_t0']:.2f}-{u['clip_t1']:.2f}"}
        if audio is not None:
            m["data"] = base64.b64encode(cut_audio(audio, u["clip_t0"], u["clip_t1"])).decode("ascii")
        media.append(m)
    return system, Template(user).substitute(clips="\n\n".join(blocks)), media


# ================================================================== cost
def inr(model: str, input_tokens: int, output_tokens: int, audio_tokens: int = 0) -> float:
    """Rupees for one call; ``input_tokens`` includes the audio tokens, billed at AUDIO_PRICE_FACTOR x."""
    from shotvi.segment.asr_fix import _inr
    return _inr(model, input_tokens, output_tokens, audio_tokens)


def estimate_input(system: str, user: str, batch: list[dict]) -> tuple[int, int]:
    """(input tokens incl. audio, audio tokens) without a network call: text by characters x TEXT_CAL, audio at
    AUDIO_TOKENS_PER_S, plus ~8 tokens per media label."""
    from shotvi.segment.render import estimate_tokens
    audio = int(sum(u["clip_t1"] - u["clip_t0"] for u in batch) * AUDIO_TOKENS_PER_S) + 1
    text = int(TEXT_CAL * (estimate_tokens(system) + estimate_tokens(user))) + 8 * len(batch)
    return text + audio, audio


def expected_out(n_clips: int) -> int:
    return EXP_OUT_PER_CLIP * n_clips + EXP_OUT_PER_CALL


def worst_call_inr(model: str, input_tokens: int, audio_tokens: int, max_out: int) -> float:
    return inr(model, input_tokens, max_out, audio_tokens)


def make_guard(worst_inr: float, max_ledger_inr: float | None):
    """before_call hook: refuse a billed attempt whose worst case would push the project ledger past the limit."""
    def guard(role, spec):
        if max_ledger_inr is None:
            return
        from shotvi.llm.spend import total_inr
        now = total_inr()
        if now + worst_inr > max_ledger_inr:
            raise WordJudgeStop(f"ledger ₹{now:.2f} + this call's worst case ₹{worst_inr:.2f} "
                                f"> limit ₹{max_ledger_inr:.2f}")
    return guard


# ================================================================== parse
def parse_answer(ans: dict, batch: list[dict]) -> tuple[dict[str, dict], list[str]]:
    """{unit id: judged item}, problems. A clip number outside the batch or answered twice is dropped (problem);
    units with no valid answer are left out (the caller marks them unchecked)."""
    got: dict[int, dict] = {}
    probs = []
    for c in (ans or {}).get("clips") or []:
        k = c.get("clip")
        if not isinstance(k, int) or not 1 <= k <= len(batch):
            probs.append(f"clip number {k!r} outside 1..{len(batch)}")
            continue
        if k in got:
            probs.append(f"clip {k} answered twice (first kept)")
            continue
        got[k] = c
    out = {}
    for k, u in enumerate(batch, 1):
        c = got.get(k)
        if c is None:
            probs.append(f"clip {k} ({u['id']}) not answered")
            continue
        out[u["id"]] = {
            "heard_text": c.get("heard_text", ""), "unclear": bool(c.get("unclear")),
            "filler_only": bool(c.get("filler_only")),
            "a_errors_said": int(c.get("a_errors") or 0), "b_errors_said": int(c.get("b_errors") or 0),
            "a_error_words": list(c.get("a_error_words") or []), "b_error_words": list(c.get("b_error_words") or []),
            "a_filler_diff": int(c.get("a_filler_diff") or 0), "b_filler_diff": int(c.get("b_filler_diff") or 0)}
    return out, probs


def resolve(u: dict, j: dict | None, seed: int, video_id: str, status: str = "judged") -> dict:
    """One unit's row with A/B mapped back to Scribe / Sarvam. Error count = the itemised list (auditable); the
    model's own integer is kept, and a mismatch is flagged."""
    sa = scribe_is_a(seed, video_id, u["key"])
    row = {"id": u["id"], "key": u["key"], "t0": round(u["t0"], 2), "t1": round(u["t1"], 2),
           "clip": [u["clip_t0"], u["clip_t1"]], "scribe_is_a": sa, "scribe_text": u["scribe_text"],
           "sarvam_text": u["sarvam_text"], "word_idx": [u["word_idx"][0], u["word_idx"][-1]] if u["word_idx"] else [],
           "seg_ids": u["seg_ids"], "status": status}
    if j is None:
        row["verdict"] = status
        return row
    s, v = ("a", "b") if sa else ("b", "a")
    se, ve = j[f"{s}_error_words"], j[f"{v}_error_words"]
    row.update({"heard_text": j["heard_text"], "unclear": j["unclear"], "filler_only": j["filler_only"],
                "scribe_errors": len(se), "sarvam_errors": len(ve), "scribe_error_words": se,
                "sarvam_error_words": ve, "scribe_filler_diff": j[f"{s}_filler_diff"],
                "sarvam_filler_diff": j[f"{v}_filler_diff"],
                "count_mismatch": j[f"{s}_errors_said"] != len(se) or j[f"{v}_errors_said"] != len(ve)})
    if j["unclear"]:
        row["verdict"] = "unclear"
    elif len(se) < len(ve):
        row["verdict"] = "scribe_better"
    elif len(ve) < len(se):
        row["verdict"] = "sarvam_better"
    else:
        row["verdict"] = "tie"
    return row


# ================================================================== run
def run(pair: dict, video_id: str, client, spec, audio, seed: int = 7, max_ledger_inr: float | None = None,
        batch_items: int = BATCH_ITEMS, max_calls: int | None = None, log=print) -> dict:
    """Judge every differing unit. ``client`` = LLMClient-like (generate_json(role, spec, system, prompt, schema,
    meta=, want_raw=, media=), optional ``ledger`` and ``before_call``). Returns {rows, batches, stopped, cost}."""
    from shotvi.llm.base import LLMError, SafetyBlocked
    xs = judge_units(pair["units"], seed)
    bs = make_batches(xs, batch_items)
    model = spec.model
    max_out = int(spec.params.get("max_output_tokens", MODEL["params"]["max_output_tokens"]))
    rows: dict[str, dict] = {}
    blog, stopped = [], None
    actual = {"calls": 0, "fresh": 0, "cached": 0, "input_tokens": 0, "output_tokens": 0, "audio_tokens": 0,
              "inr": 0.0}
    led = getattr(client, "ledger", None)
    t_start = time.time()
    for bi, b in enumerate(bs):
        if stopped or (max_calls is not None and bi >= max_calls):
            for u in b:
                rows[u["id"]] = resolve(u, None, seed, video_id, "not_run")
            blog.append({"batch": bi, "n": len(b), "status": "not_run"})
            continue
        system, user, media = request(b, audio, video_id, seed)
        tin, atok = estimate_input(system, user, b)
        worst = worst_call_inr(model, int(tin * 1.2), int(atok * 1.2), max_out)  # +20% on the estimate's error
        if hasattr(client, "before_call"):
            client.before_call = make_guard(worst, max_ledger_inr)
        b0 = led.billed_usd() if led is not None else 0.0
        rec = {"batch": bi, "n": len(b), "units": [u["id"] for u in b], "worst_inr": round(worst, 3)}
        try:
            r = client.generate_json(ROLE, spec, system, user, SCHEMA, meta={"video_id": video_id, "batch": bi},
                                     want_raw=True, media=media)
            ans, raw = r if isinstance(r, tuple) else (r, None)
        except WordJudgeStop as e:
            stopped = str(e)
            log(f"[word_judge] STOP before batch {bi + 1}/{len(bs)}: {e}")
            for u in b:
                rows[u["id"]] = resolve(u, None, seed, video_id, "not_run")
            blog.append({**rec, "status": "not_run", "error": stopped})
            continue
        except SafetyBlocked as e:
            for u in b:
                rows[u["id"]] = resolve(u, None, seed, video_id, "unchecked")
            blog.append({**rec, "status": "safety_blocked", "error": str(e)[:300]})
            log(f"[word_judge] batch {bi + 1}/{len(bs)}: safety block -> {len(b)} units unchecked")
            continue
        except LLMError as e:
            for u in b:
                rows[u["id"]] = resolve(u, None, seed, video_id, "unchecked")
            blog.append({**rec, "status": "failed", "error": str(e)[:300]})
            log(f"[word_judge] batch {bi + 1}/{len(bs)}: FAILED {str(e)[:160]} -> {len(b)} units unchecked")
            continue
        fresh = bool(led is not None and led.billed_usd() > b0)
        atok_real = int((getattr(raw, "prompt_modalities", None) or {}).get("AUDIO", 0)) if raw is not None else 0
        if fresh and atok_real:
            from shotvi.llm import spend
            from shotvi.segment.asr_fix import AUDIO_PRICE_FACTOR
            if AUDIO_PRICE_FACTOR > 1:
                spend.record(model, int(atok_real * (AUDIO_PRICE_FACTOR - 1)), 0,
                             source=os.environ.get("SHOTVI_SPEND_SOURCE", "word_judge") + "-audio-surcharge")
        actual["calls"] += 1
        actual["fresh" if fresh else "cached"] += 1
        if raw is not None:
            out_t = raw.output_tokens + getattr(raw, "thought_tokens", 0)
            actual["input_tokens"] += raw.input_tokens
            actual["output_tokens"] += out_t
            actual["audio_tokens"] += atok_real
            if fresh:
                actual["inr"] = round(actual["inr"] + inr(model, raw.input_tokens, out_t, atok_real), 3)
        got, probs = parse_answer(ans, b)
        for u in b:
            rows[u["id"]] = resolve(u, got.get(u["id"]), seed, video_id,
                                    "judged" if u["id"] in got else "unchecked")
        blog.append({**rec, "status": "ok", "fresh": fresh, "problems": probs})
        log(f"[word_judge] batch {bi + 1}/{len(bs)}: {len(got)}/{len(b)} judged"
            + (" (cached)" if not fresh else "") + (f"; {len(probs)} problems" if probs else ""))
    actual["wall_s"] = round(time.time() - t_start, 1)
    ordered = [rows[u["id"]] for u in pair["units"] if u["id"] in rows]
    return {"rows": ordered, "batches": blog, "stopped": stopped, "cost": actual, "n_batches": len(bs)}


# ================================================================== aggregate
def aggregate(pair: dict, rows: list[dict]) -> dict:
    """Totals over judged (clear) units. Words = each system's own token count over the whole episode; agreeing
    units count as 0 errors for both."""
    judged = [r for r in rows if r["status"] == "judged"]
    clear = [r for r in judged if not r.get("unclear")]
    v = Counter(r["verdict"] for r in rows)
    se = sum(r["scribe_errors"] for r in clear)
    ve = sum(r["sarvam_errors"] for r in clear)
    types = {"scribe": Counter(), "sarvam": Counter()}
    for r in clear:
        for sysname in ("scribe", "sarvam"):
            types[sysname].update(x.get("type", "?") for x in r[f"{sysname}_error_words"])
    sw, vw = pair["scribe_tokens"], pair["sarvam_tokens"]
    by_id = {u["id"]: u for u in pair["units"]}
    not_clear = [r for r in rows if r["status"] != "judged" or r.get("unclear")]
    return {
        "units_total": len(pair["units"]), "units_differ": pair["units_differ"],
        "raw_segments_differ": pair["raw_segments_differ"], "n_sarvam_segments": pair["n_sarvam_segments"],
        "judged": len(judged), "clear": len(clear), "unclear": v.get("unclear", 0),
        "unchecked": v.get("unchecked", 0), "not_run": v.get("not_run", 0),
        "scribe_errors": se, "sarvam_errors": ve,
        "scribe_better": v.get("scribe_better", 0), "sarvam_better": v.get("sarvam_better", 0),
        "tie": v.get("tie", 0), "tie_filler_only": sum(r["verdict"] == "tie" and r.get("filler_only") for r in clear),
        "tie_zero_errors": sum(r["verdict"] == "tie" and r["scribe_errors"] == 0 for r in clear),
        "scribe_words": sw, "sarvam_words": vw,
        "scribe_err_per_100": round(100 * se / sw, 2) if sw else None,
        "sarvam_err_per_100": round(100 * ve / vw, 2) if vw else None,
        "error_types": {k: dict(c) for k, c in types.items()},
        "filler_diff": {"scribe": sum(r.get("scribe_filler_diff", 0) for r in clear),
                        "sarvam": sum(r.get("sarvam_filler_diff", 0) for r in clear)},
        "count_mismatch": sum(bool(r.get("count_mismatch")) for r in judged),
        "words_not_judged": {"scribe": sum(len(by_id[r["id"]]["scribe_toks"]) for r in not_clear),
                             "sarvam": sum(len(by_id[r["id"]]["sarvam_toks"]) for r in not_clear)},
        "assumption": "units where Scribe and Sarvam agree count as 0 errors for both (an error both make is "
                      "invisible); unclear / unchecked / not-run units add no errors; words = each system's own "
                      "token count over the whole episode",
    }


def asr_fix_overlaps(pair: dict, rows: list[dict], corrections: dict) -> list[dict]:
    """ASR-fix corrections (runs/asr_fix) whose Scribe words fall in a judged-or-differing unit, with the verdict."""
    cs = corrections.get("corrections") or []
    by_row = {r["id"]: r for r in rows}
    out = []
    for u in pair["units"]:
        if not u["differs"] or not u["word_idx"]:
            continue
        idx = set(u["word_idx"])
        for c in cs:
            span = set(range(int(c["start_word"]), int(c["end_word"]) + 1))
            if span & idx:
                r = by_row.get(u["id"], {})
                out.append({"unit": u["id"], "heard": c.get("heard"), "corrected": c.get("corrected"),
                            "applied": bool(c.get("applied")), "source": c.get("source"),
                            "confidence": c.get("confidence"), "verdict": r.get("verdict", "not_run"),
                            "scribe_error_words": r.get("scribe_error_words"), "heard_text": r.get("heard_text"),
                            "scribe_text": u["scribe_text"], "sarvam_text": u["sarvam_text"]})
    return out


def summary_md(video_id: str, pair: dict, agg: dict, asr: list[dict], res: dict | None) -> str:
    a = agg
    L = [f"# Word judge: {video_id}", "",
         f"Pairing: {a['n_sarvam_segments']} Sarvam segments, {a['raw_segments_differ']} differ at segment level; "
         f"after boundary merges ({pair['merges']}) and {pair['orphan_groups']} orphan groups "
         f"({pair['orphan_words']} Scribe words outside any Sarvam segment): {a['units_differ']} differing units "
         f"of {a['units_total']}.",
         f"Judged {a['judged']} (clear {a['clear']}, unclear {a['unclear']}), unchecked {a['unchecked']}, "
         f"not run {a['not_run']}.", "",
         "| | Scribe | Sarvam |", "|---|---|---|",
         f"| real errors (clear units) | {a['scribe_errors']} | {a['sarvam_errors']} |",
         f"| own words, whole episode | {a['scribe_words']} | {a['sarvam_words']} |",
         f"| errors per 100 words | {a['scribe_err_per_100']} | {a['sarvam_err_per_100']} |",
         f"| units where better | {a['scribe_better']} | {a['sarvam_better']} |",
         *(f"| {t} | {a['error_types']['scribe'].get(t, 0)} | {a['error_types']['sarvam'].get(t, 0)} |"
           for t in ("wrong", "missing", "added")),
         f"| filler differences (not errors) | {a['filler_diff']['scribe']} | {a['filler_diff']['sarvam']} |", "",
         f"Ties: {a['tie']} (both 0 errors: {a['tie_zero_errors']}; filler only: {a['tie_filler_only']}). "
         f"Error-count mismatches (model's integer vs its own list; the list is used): {a['count_mismatch']}.",
         f"Words in unclear / unchecked / not-run units (no errors counted): Scribe {a['words_not_judged']['scribe']}, "
         f"Sarvam {a['words_not_judged']['sarvam']}.", "", f"Assumption: {a['assumption']}.", ""]
    if asr:
        vc = Counter(x["verdict"] for x in asr)
        L += [f"ASR-fix layer touched {len(asr)} correction(s) inside differing units "
              f"({sum(x['applied'] for x in asr)} applied); verdicts there: "
              + ", ".join(f"{k} {n}" for k, n in sorted(vc.items())), ""]
        for x in asr:
            errs = "; ".join(f"{e.get('type')} {e.get('written')!r}->{e.get('said')!r}"
                             for e in x.get("scribe_error_words") or [])
            L.append(f"- {x['unit']}: \"{x['heard']}\" -> \"{x['corrected']}\" ({'applied' if x['applied'] else 'not applied'}, "
                     f"{x['source']}); verdict {x['verdict']}" + (f"; judge's Scribe errors: {errs}" if errs else ""))
        L.append("")
    if res:
        c = res["cost"]
        L.append(f"Cost: {c['calls']} calls ({c['fresh']} fresh, {c['cached']} cached), ₹{c['inr']:.2f} billed "
                 f"(audio surcharge included), {c['input_tokens']} in / {c['output_tokens']} out tokens, "
                 f"{c.get('wall_s', 0)} s wall"
                 + (f". STOPPED: {res['stopped']}" if res.get("stopped") else ""))
    return "\n".join(L) + "\n"


def write_tsv(path: Path, rows: list[dict]):
    cols = ["id", "t0", "t1", "verdict", "scribe_errors", "sarvam_errors", "scribe_text", "sarvam_text",
            "heard_text", "scribe_error_words", "sarvam_error_words", "scribe_is_a", "status"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(cols)
        for r in rows:
            w.writerow([json.dumps(r.get(c), ensure_ascii=False) if isinstance(r.get(c), list) else r.get(c, "")
                        for c in cols])


# ================================================================== review page
def review_sample(pair: dict, seed: int, n: int = REVIEW_N) -> list[dict]:
    xs = sorted((u for u in pair["units"] if u["differs"]), key=lambda u: u["t0"])
    rng = random.Random(f"review|{seed}")
    pick = rng.sample(xs, min(n, len(xs)))
    return sorted(pick, key=lambda u: u["t0"])


def build_review(pair: dict, video_id: str, audio, out_dir: Path, seed: int = 7, n: int = REVIEW_N) -> Path:
    """review.html + clips/NN.wav (relative paths: double-click works). Blind: same A/B as the judge, no system
    names, no judge verdicts. The answer key is in units.json (do not open it before rating)."""
    from shotvi.segment.asr_fix import cut_audio
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "clips").mkdir(exist_ok=True)
    items = []
    for k, u in enumerate(review_sample(pair, seed, n), 1):
        a, b, _ = versions(u, seed, video_id)
        fn = f"clips/{k:02d}.wav"
        if audio is not None:
            (out_dir / fn).write_bytes(cut_audio(audio, u["clip_t0"], u["clip_t1"]))
        items.append({"n": k, "unit": u["id"], "src": fn, "a": a, "b": b, "dur": round(u["clip_t1"] - u["clip_t0"], 1)})
    page = (_PAGE.replace("__TITLE__", html.escape(f"Which transcript is right? {video_id}"))
            .replace("__VID__", json.dumps(video_id)).replace("__N__", str(len(items)))
            .replace("__DATA__", json.dumps(items, ensure_ascii=False).replace("</", "<\\/")))
    p = out_dir / "review.html"
    p.write_text(page, encoding="utf-8")
    return p


_PAGE = """<!doctype html>
<html lang="te"><head><meta charset="utf-8"><title>__TITLE__</title>
<style>
body{font-family:system-ui,"Noto Sans Telugu",sans-serif;max-width:900px;margin:24px auto;padding:0 12px;color:#222}
.card{border:1px solid #ccc;border-radius:8px;padding:12px 16px;margin:14px 0}
.v{font-size:1.15em;margin:6px 0;padding:6px 8px;background:#f6f6f6;border-radius:4px}
.v b{display:inline-block;width:1.6em}
button{font-size:1em;margin:4px 6px 0 0;padding:6px 14px;border-radius:6px;border:1px solid #888;background:#fff;cursor:pointer}
button.on{background:#2b6;color:#fff;border-color:#2b6}
.done{color:#2b6;font-weight:600}
input.note{width:60%;margin-left:6px}
#bar{position:sticky;top:0;background:#fff;padding:8px 0;border-bottom:1px solid #ddd}
</style></head><body>
<div id="bar"><b>__TITLE__</b> &middot; <span id="count"></span>
<button onclick="exportCsv()">Download CSV</button></div>
<p>Each clip has two written versions, A and B, of the same speech. Listen, then pick the version with FEWER real
word mistakes (a wrong word, a spoken word left out, a word added that is not said). Ignore spelling, Telugu vs
English script, digits vs words, joined vs split words, punctuation, and fillers like ఉమ్ / ఆ. "Same" = equally
right or equally wrong. Choices are saved in this browser; click "Download CSV" when done and send the file.</p>
<div id="list"></div>
<script>
const VID=__VID__, DATA=__DATA__, KEY="word_judge_review_"+VID;
let S=JSON.parse(localStorage.getItem(KEY)||"{}");
function save(){localStorage.setItem(KEY,JSON.stringify(S));count();}
function count(){document.getElementById("count").textContent=Object.values(S).filter(x=>x.choice).length+" / __N__ rated";}
function esc(s){return s.replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
function render(){
 const L=document.getElementById("list");L.innerHTML="";
 for(const it of DATA){
  const st=S[it.unit]||{};
  const d=document.createElement("div");d.className="card";
  d.innerHTML=`<div><b>Clip ${it.n}</b> (${it.dur} s)</div>
  <audio controls preload="none" src="${it.src}"></audio>
  <div class="v"><b>A</b>${esc(it.a)}</div><div class="v"><b>B</b>${esc(it.b)}</div>
  <div>${["A better","B better","same"].map(c=>`<button data-c="${c}" class="${st.choice===c?"on":""}">${c}</button>`).join("")}
  <input class="note" placeholder="note (optional)" value="${esc(st.note||"")}"></div>`;
  d.querySelectorAll("button").forEach(b=>b.onclick=()=>{S[it.unit]={...(S[it.unit]||{}),choice:b.dataset.c};save();render();});
  d.querySelector("input").onchange=e=>{S[it.unit]={...(S[it.unit]||{}),note:e.target.value};save();};
  L.appendChild(d);
 }
 count();
}
function exportCsv(){
 const q=s=>'"'+String(s??"").replace(/"/g,'""')+'"';
 const rows=[["n","unit","choice","note"].join(",")];
 for(const it of DATA){const st=S[it.unit]||{};rows.push([it.n,it.unit,q(st.choice||""),q(st.note||"")].join(","));}
 const blob=new Blob(["\\ufeff"+rows.join("\\n")],{type:"text/csv;charset=utf-8"});
 const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="word_judge_"+VID+".csv";a.click();
}
render();
</script></body></html>
"""


def compare_human(csv_path, rows: list[dict]) -> dict:
    """Founder choices (A better / B better / same) vs the judge on the same units, in the same blind A/B terms."""
    by_id = {r["id"]: r for r in rows}
    pairs = []
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for x in csv.DictReader(fh):
            ch = (x.get("choice") or "").strip()
            r = by_id.get((x.get("unit") or "").strip())
            if not ch or r is None or r.get("status") != "judged" or r.get("unclear"):
                continue
            a_err = r["scribe_errors"] if r["scribe_is_a"] else r["sarvam_errors"]
            b_err = r["sarvam_errors"] if r["scribe_is_a"] else r["scribe_errors"]
            judge = "A better" if a_err < b_err else "B better" if b_err < a_err else "same"
            pairs.append((ch, judge, r["id"]))
    agree = sum(h == j for h, j, _ in pairs)
    conf = Counter(f"{h} / judge {j}" for h, j, _ in pairs)
    labs = ["A better", "B better", "same"]
    n = len(pairs)
    po = agree / n if n else None
    pe = sum((sum(h == l for h, _, _ in pairs) / n) * (sum(j == l for _, j, _ in pairs) / n) for l in labs) if n else 0
    kappa = round((po - pe) / (1 - pe), 3) if n and pe < 1 else None
    return {"n": n, "agree": agree, "agreement": round(po, 3) if n else None, "kappa": kappa,
            "confusion": dict(conf), "disagreements": [i for h, j, i in pairs if h != j]}


# ================================================================== dry run
def project(pair: dict, video_id: str, spec, audio, seed: int, batch_items: int = BATCH_ITEMS,
            count_tokens: bool = True, client=None, log=print) -> dict:
    """Calls, Rs expected and worst, no generate call. Input tokens per batch: Gemini countTokens on the exact
    request (system + every label + every inline WAV + text; free) or the estimate. Cached batches cost 0."""
    from shotvi.llm.spend import cap_inr, total_inr
    xs = judge_units(pair["units"], seed)
    bs = make_batches(xs, batch_items)
    max_out = int(spec.params.get("max_output_tokens", MODEL["params"]["max_output_tokens"]))
    prov, how = None, "estimate (text by characters x 1.25, audio at 32 tokens/s)"
    if count_tokens:
        try:
            from shotvi.llm.gemini import GeminiProvider
            prov = GeminiProvider()
            how = "Gemini countTokens on the full request (system + text + inline WAV parts)"
        except Exception as e:  # noqa: BLE001 - no key: fall back to the estimate and say so
            log(f"countTokens unavailable ({e}); using the estimate")
    rows = []
    for bi, b in enumerate(bs):
        system, user, media = request(b, audio, video_id, seed)
        est_in, est_a = estimate_input(system, user, b)
        n_in, n_a, src = est_in, est_a, "estimate"
        if prov is not None:
            try:
                n_in, mod = prov.count_tokens_request(spec, system, user, media, details=True)
                n_a = int(mod.get("AUDIO", 0)) or est_a
                src = "countTokens" + ("" if mod.get("AUDIO") else " (audio tokens not itemised: audio at 32/s)")
            except Exception as e:  # noqa: BLE001
                log(f"batch {bi}: countTokens failed ({str(e)[:120]}); estimate used")
        cached = False
        if client is not None:
            from shotvi.llm.base import LLMError
            try:
                client.generate_json(ROLE, spec, system, user, SCHEMA, meta={"video_id": video_id, "batch": bi},
                                     media=media)
                cached = True
            except LLMError:
                cached = False
        exp_o = expected_out(len(b))
        rows.append({"batch": bi, "clips": len(b), "audio_s": round(sum(u["clip_t1"] - u["clip_t0"] for u in b), 1),
                     "input_tokens": n_in, "audio_tokens": n_a, "estimate_input": est_in, "source": src,
                     "cached": cached, "exp_out": exp_o,
                     "inr_expected": 0.0 if cached else round(inr(spec.model, n_in, exp_o, n_a), 3),
                     "inr_worst": 0.0 if cached else round(inr(spec.model, n_in, max_out, n_a), 3)})
    led, cap = total_inr(), cap_inr()
    return {"calls": len(bs), "cached_calls": sum(r["cached"] for r in rows), "clips": len(xs),
            "audio_s": round(sum(r["audio_s"] for r in rows), 1),
            "input_tokens": sum(r["input_tokens"] for r in rows), "audio_tokens": sum(r["audio_tokens"] for r in rows),
            "exp_output_tokens": sum(r["exp_out"] for r in rows if not r["cached"]),
            "worst_output_tokens": max_out * sum(not r["cached"] for r in rows),
            "inr_expected": round(sum(r["inr_expected"] for r in rows), 2),
            "inr_worst": round(sum(r["inr_worst"] for r in rows), 2), "token_source": how,
            "ledger_inr": round(led, 2), "cap_inr": cap, "rows": rows, "max_output_tokens": max_out,
            "assumptions": f"expected output = {EXP_OUT_PER_CLIP}/clip + {EXP_OUT_PER_CALL}/call (thinking LOW); "
                           f"worst = max_output_tokens {max_out} per call; audio billed at "
                           f"asr_fix.AUDIO_PRICE_FACTOR x the text input rate"}


# ================================================================== CLI
def load_inputs(video_id: str, scribe: str | None = None, sarvam: str | None = None):
    d = ROOT / "scribe_dataset" / video_id
    tr = json.loads(Path(scribe or d / f"{video_id}_scribe_transcript.json").read_text(encoding="utf-8"))
    sv = json.loads(Path(sarvam or d / f"{video_id}_sarvam_raw.json").read_text(encoding="utf-8"))
    return tr, sv


def main(argv=None):
    ap = argparse.ArgumentParser(description="Blind audio word-accuracy judge: Scribe vs Sarvam over a whole episode")
    ap.add_argument("video_id")
    ap.add_argument("--scribe", help="default scribe_dataset/<id>/<id>_scribe_transcript.json (raw words)")
    ap.add_argument("--sarvam", help="default scribe_dataset/<id>/<id>_sarvam_raw.json")
    ap.add_argument("--audio", help="default scribe_dataset/<id>/<id>_audio.wav")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--batch-items", type=int, default=BATCH_ITEMS)
    ap.add_argument("--no-merge", action="store_true", help="judge raw Sarvam segments (no boundary merges)")
    ap.add_argument("--dry-run", action="store_true", help="calls and Rs expected / worst; no generate call")
    ap.add_argument("--no-count-tokens", action="store_true", help="dry run: estimate instead of countTokens")
    ap.add_argument("--max-ledger-inr", type=float, default=None,
                    help="refuse any call whose worst case would push the project ledger past this")
    ap.add_argument("--max-calls", type=int, default=None, help="judge only the first N (seeded random) batches")
    ap.add_argument("--review-n", type=int, default=REVIEW_N)
    ap.add_argument("--human-csv", default=None, help="exported review CSV: agreement with the judge")
    ap.add_argument("--out", default=str(OUT_DIR))
    a = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    vid = a.video_id
    tr, sv = load_inputs(vid, a.scribe, a.sarvam)
    pair = pairing(tr, sv, merge=not a.no_merge)
    from shotvi.segment.asr_fix import find_audio
    audio = Path(a.audio) if a.audio else find_audio(vid)
    if audio is None or not Path(audio).exists():
        print(f"{vid}: no audio file", file=sys.stderr)
        return 1
    from shotvi.llm import Ledger, LLMClient, ModelSpec
    spec = ModelSpec.from_dict(MODEL)
    out = Path(a.out) / vid
    out.mkdir(parents=True, exist_ok=True)
    print(f"{vid}: {pair['n_sarvam_segments']} Sarvam segments, {pair['raw_segments_differ']} differ (segment level); "
          f"{pair['orphan_words']} orphan Scribe words in {pair['orphan_groups']} groups; {pair['merges']} boundary "
          f"merges -> {pair['units_differ']} differing units of {len(pair['units'])}; words Scribe "
          f"{pair['scribe_tokens']}, Sarvam {pair['sarvam_tokens']}")
    units_doc = {"video_id": vid, "schema": SCHEMA_VERSION, "seed": a.seed, "merge": not a.no_merge,
                 "audio": str(audio), "pad_s": PAD_S,
                 "note": "ANSWER KEY (scribe_is_a): do not open before rating the review page",
                 **{k: v for k, v in pair.items() if k != "units"},
                 "units": [{k: v for k, v in u.items() if k not in ("scribe_toks", "sarvam_toks")}
                           | {"scribe_is_a": scribe_is_a(a.seed, vid, u["key"])} for u in pair["units"]]}
    (out / "units.json").write_text(json.dumps(units_doc, ensure_ascii=False, indent=1), encoding="utf-8")
    page = build_review(pair, vid, audio, out / "review", a.seed, a.review_n)
    print(f"  review page: {page}")
    if a.dry_run:
        client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"))
        client.cache_only = True
        pj = project(pair, vid, spec, audio, a.seed, a.batch_items, not a.no_count_tokens, client)
        (out / "dry_run.json").write_text(json.dumps(pj, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  dry run ({pj['token_source']}): {pj['calls']} calls ({pj['cached_calls']} cached), {pj['clips']} "
              f"clips, {pj['audio_s']} s audio; input {pj['input_tokens']} tokens (audio {pj['audio_tokens']}); "
              f"output expected {pj['exp_output_tokens']}, worst {pj['worst_output_tokens']}")
        print(f"  Rs expected {pj['inr_expected']:.2f}, worst {pj['inr_worst']:.2f} "
              f"(max_output_tokens {pj['max_output_tokens']} on every call)")
        print(f"  ledger Rs {pj['ledger_inr']:.2f} of cap Rs {pj['cap_inr']:.2f}: headroom Rs "
              f"{pj['cap_inr'] - pj['ledger_inr']:.2f} -> expected "
              f"{'fits' if pj['ledger_inr'] + pj['inr_expected'] <= pj['cap_inr'] else 'does NOT fit'}, worst "
              f"{'fits' if pj['ledger_inr'] + pj['inr_worst'] <= pj['cap_inr'] else 'does NOT fit'}")
        print(f"  assumptions: {pj['assumptions']}")
        return 0
    os.environ.setdefault("SHOTVI_SPEND_SOURCE", "word_judge")
    client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"), max_retries=0, schema_retries=0)
    res = run(pair, vid, client, spec, audio, a.seed, a.max_ledger_inr, a.batch_items, a.max_calls)
    agg = aggregate(pair, res["rows"])
    corr = {}
    cp = ROOT / "runs" / "asr_fix" / vid / "corrections.json"
    if cp.exists():
        corr = json.loads(cp.read_text(encoding="utf-8"))
    asr = asr_fix_overlaps(pair, res["rows"], corr)
    doc = {"video_id": vid, "schema": SCHEMA_VERSION, "seed": a.seed, "model": spec.to_dict(),
           "prompt_version": load_prompt()[0], "aggregate": agg, "asr_fix": asr, "batches": res["batches"],
           "cost": res["cost"], "stopped": res["stopped"], "rows": res["rows"]}
    if a.human_csv:
        doc["human"] = compare_human(a.human_csv, res["rows"])
    (out / "judged.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    write_tsv(out / "segments.tsv", res["rows"])
    md = summary_md(vid, pair, agg, asr, res)
    if a.human_csv:
        h = doc["human"]
        md += (f"\nFounder check: {h['n']} units, agreement {h['agreement']}, kappa {h['kappa']}; "
               f"{json.dumps(h['confusion'], ensure_ascii=False)}\n")
    (out / "summary.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"  files: {out / 'judged.json'}, {out / 'segments.tsv'}, {out / 'summary.md'}")
    return 2 if res["stopped"] else 0


if __name__ == "__main__":
    sys.exit(main())
