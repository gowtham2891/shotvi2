"""LLM speaker correction: who speaks each line, inferred from content (an additive layer; the transcript is never
modified).

Scribe's diarization merges characters under one label and swaps labels (web series especially). One text call per
episode (gemini-3.8-flash, thinking medium, default temperature) reads the whole transcript as numbered lines
(Scribe sentences; ASR-corrected text from ``runs/asr_fix/<id>/corrections.json`` where present; Scribe's labels shown
as weak hints) and returns a cast plus, per line, a cast key, run-length coded to keep the output small::

    {"cast": [{"key": "A", "name": "...", "desc": "..."}], "turns": ["0-2:A", "3:B?", "4:C??", "5:X"]}

``?`` = fairly unsure, ``??`` = a guess, ``X`` = no basis. Code validates: only given line ids, each id once (first
answer wins), keys must be in the cast. Missing / invalid ids get ``name: null``.

Output ``runs/speaker_fix/<id>.json``::

    {"video_id", "schema": "speaker_fix/1", "model", "prompt": "<version>+<sha8>", "transcript", "asr_fix",
     "cast": [{key, name, desc, n_lines, n_words}],
     "lines": [{id, start, end, words: [i0, i1], scribe, key, name, confidence}],   # id = Scribe sentence id
     "coverage": {n_lines, assigned, unknown, missing, duplicate_ids, invalid_items, bad_keys},
     "cost": {projected, actual}}

``confidence``: 0.9 plain, 0.6 ``?``, 0.3 ``??``, 0.0 ``X`` / missing.

CLI::

    python -m shotvi.segment.speaker_fix <id> [<id> ...] --dry-run
    python -m shotvi.segment.speaker_fix <id> --max-ledger-inr 1500 --budget-inr 10
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from string import Template

from .render import estimate_tokens, fmt_ts

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "speaker_fix/1"
PROMPT_PATH = ROOT / "prompts" / "speaker_fix.md"
OUT_DIR = ROOT / "runs" / "speaker_fix"
MODEL = {"provider": "gemini", "model": "gemini-3.8-flash",
         "params": {"thinking_level": "medium", "max_output_tokens": 16384}}
CONF = {"": 0.9, "?": 0.6, "??": 0.3}
TEXT_CAL = 1.25                 # estimate_tokens under-counts (asr_fix calibration against countTokens)
OUT_TOKENS_PER_LINE = 4         # run-length turns: ~6 tokens per run, ~1.5 lines per run
THINK_TOKENS_EST = (4000, 8)    # projection: base + per line (medium thinking; a guess, checked after each call)
SPEND_FILE = OUT_DIR / "_spend.json"   # this experiment's own rupee tally (the project ledger stays the authority)

SCHEMA = {
    "type": "object",
    "properties": {
        "cast": {"type": "array", "items": {
            "type": "object",
            "properties": {"key": {"type": "string"}, "name": {"type": "string"}, "desc": {"type": "string"}},
            "required": ["key", "name", "desc"]}},
        "turns": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["cast", "turns"],
}

TURN_RE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+))?\s*:\s*([A-Za-z0-9_]+)\s*(\?{0,2})\s*$")


class SpeakerFixStop(RuntimeError):
    """A spend guard refused the call."""


# ------------------------------------------------------------------ prompt
def load_prompt(path: Path = PROMPT_PATH) -> tuple[str, str, str]:
    """(tag, system, user template). tag = version+sha8 of the file."""
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.S)
    if not m:
        raise ValueError(f"{path}: missing front matter")
    meta = {k.strip(): v.strip() for k, v in (ln.split(":", 1) for ln in m.group(1).strip().splitlines())}
    sm = re.search(r"### SYSTEM\n(.*?)\n### USER\n(.*)", m.group(2), re.S)
    if not sm:
        raise ValueError(f"{path}: needs '### SYSTEM' and '### USER' sections")
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{meta.get('version', '?')}+{sha}", sm.group(1).strip(), sm.group(2).strip()


def short_label(sp) -> str:
    if sp is None:
        return "s?"
    m = re.search(r"(\d+)$", str(sp))
    return f"s{m.group(1)}" if m else str(sp)


def line_texts(tr: dict, corrections: dict | None) -> list[str]:
    """Display text per Scribe sentence, with accepted ASR corrections applied (word indices unchanged)."""
    sents = tr["sentences"]
    if not corrections or not corrections.get("corrections"):
        return [s["text"] for s in sents]
    from .asr_fix import apply_corrections
    dw = apply_corrections(tr["word_timestamps"], corrections)
    return [" ".join(d["text"] for d in dw[s["words"][0]:s["words"][1] + 1] if d["text"]) or s["text"]
            for s in sents]


def render(tr: dict, corrections: dict | None = None) -> tuple[str, int]:
    """Numbered transcript lines (id = Scribe sentence id) with audio events as unnumbered lines."""
    texts = line_texts(tr, corrections)
    rows = [(s["start"], 1, f"{s['id']} [{fmt_ts(s['start'])}] {short_label(s.get('speaker'))}| {t}")
            for s, t in zip(tr["sentences"], texts)]
    rows += [(e["start"], 0, f"({fmt_ts(e['start'])} {e['text']})") for e in tr.get("audio_events") or []]
    rows.sort(key=lambda r: (r[0], r[1]))
    return "\n".join(r[2] for r in rows), len(tr["sentences"])


def build_request(tr: dict, corrections: dict | None = None, prompt_path: Path = PROMPT_PATH) -> tuple[str, str, str]:
    tag, system, user = load_prompt(prompt_path)
    text, n = render(tr, corrections)
    return tag, system, Template(user).substitute(n_lines=n, last_id=n - 1, lines=text)


# ------------------------------------------------------------------ cost
def inr(model: str, tin: int, tout: int) -> float:
    from shotvi.llm.pricing import cost_usd
    from shotvi.llm.spend import usd_inr
    return (cost_usd(model, tin, tout) or (tin * 5 + tout * 20) / 1e6) * usd_inr()


def project(system: str, user: str, n_lines: int, model: str = MODEL["model"],
            max_output_tokens: int = MODEL["params"]["max_output_tokens"]) -> dict:
    tin = int(TEXT_CAL * (estimate_tokens(system) + estimate_tokens(user)))
    tout = 300 + OUT_TOKENS_PER_LINE * n_lines + THINK_TOKENS_EST[0] + THINK_TOKENS_EST[1] * n_lines
    return {"input_tokens": tin, "output_tokens": tout, "inr": round(inr(model, tin, tout), 2),
            "worst_case_inr": round(inr(model, tin, max_output_tokens), 2)}


def billed_inr(ledger, n0: int = 0) -> float:
    """Rupees billed by ledger calls[n0:], priced like the project ledger (spend.record: no cached-input discount)."""
    calls = ledger.calls[n0:] if ledger is not None else []
    return sum(inr(c["model"], c["input_tokens"], c["output_tokens"] + c["thought_tokens"])
               for c in calls if not c["cached"])


def session_spent() -> float:
    if SPEND_FILE.exists():
        return float(json.loads(SPEND_FILE.read_text(encoding="utf-8")).get("inr", 0.0))
    return 0.0


def add_session_spend(amount: float, what: str):
    SPEND_FILE.parent.mkdir(parents=True, exist_ok=True)
    d = json.loads(SPEND_FILE.read_text(encoding="utf-8")) if SPEND_FILE.exists() else {"inr": 0.0, "calls": []}
    d["inr"] = round(d["inr"] + amount, 4)
    d["calls"].append({"what": what, "inr": round(amount, 4)})
    SPEND_FILE.write_text(json.dumps(d, indent=1), encoding="utf-8")


def make_guard(worst_case_inr: float, max_ledger_inr: float | None, budget_inr: float | None, in_flight=None):
    """before_call hook: refuse any billed attempt whose WORST case (all max_output_tokens used) would push the
    project ledger past ``max_ledger_inr`` or this experiment's tally past ``budget_inr``. ``in_flight()`` = rupees
    already billed by earlier attempts of the running call (retries), which the saved tally does not hold yet.
    (Bug fixed 2026-10-13: without it a retry after a MAX_TOKENS failure passed the budget check.)"""
    def guard(role, spec):
        if max_ledger_inr is not None:
            from shotvi.llm.spend import total_inr
            now = total_inr()
            if now + worst_case_inr > max_ledger_inr:
                raise SpeakerFixStop(f"ledger ₹{now:.2f} + worst case ₹{worst_case_inr:.2f} > ₹{max_ledger_inr:.2f}")
        if budget_inr is not None:
            spent = session_spent() + (in_flight() if in_flight else 0.0)
            if spent + worst_case_inr > budget_inr:
                raise SpeakerFixStop(f"speaker_fix spent ₹{spent:.2f} + worst case ₹{worst_case_inr:.2f} "
                                     f"> budget ₹{budget_inr:.2f}")
    return guard


# ------------------------------------------------------------------ parse
def parse_answer(ans: dict, tr: dict) -> dict:
    """Validate the model answer against the transcript; returns cast / lines / coverage (see module doc)."""
    sents = tr["sentences"]
    n = len(sents)
    cast, keys = [], {}
    for c in ans.get("cast") or []:
        k = str(c.get("key", "")).strip()
        name = " ".join(str(c.get("name", "")).split())
        if not k or not name or k in keys or k == "X":
            continue
        keys[k] = {"key": k, "name": name, "desc": " ".join(str(c.get("desc", "")).split())}
        cast.append(keys[k])
    got: dict[int, tuple[str | None, str]] = {}
    cov = {"n_lines": n, "duplicate_ids": 0, "invalid_items": [], "bad_keys": []}
    for item in ans.get("turns") or []:
        m = TURN_RE.match(str(item))
        if not m:
            cov["invalid_items"].append(str(item)[:40])
            continue
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) is not None else a
        key, q = m.group(3), m.group(4)
        if b < a or a < 0 or b >= n:
            cov["invalid_items"].append(str(item)[:40])   # an id outside the transcript: never invent lines
            continue
        if key != "X" and key not in keys:
            cov["bad_keys"].append(key)
            key = None
        for i in range(a, b + 1):
            if i in got:
                cov["duplicate_ids"] += 1
                continue
            got[i] = (None if key == "X" else key, q)
    lines = []
    for s in sents:
        k, q = got.get(s["id"], (None, ""))
        lines.append({"id": s["id"], "start": s["start"], "end": s["end"], "words": list(s["words"]),
                      "scribe": s.get("speaker"), "key": k, "name": keys[k]["name"] if k else None,
                      "confidence": CONF.get(q, 0.3) if k else 0.0})
    for c in cast:
        mine = [ln for ln in lines if ln["key"] == c["key"]]
        c["n_lines"] = len(mine)
        c["n_words"] = sum(ln["words"][1] - ln["words"][0] + 1 for ln in mine)
    cov["assigned"] = sum(1 for ln in lines if ln["key"])
    cov["unknown"] = sum(1 for i, ln in enumerate(lines) if not ln["key"] and i in got)
    cov["missing"] = n - len(got)
    cov["bad_keys"] = sorted(set(cov["bad_keys"]))
    return {"cast": cast, "lines": lines, "coverage": cov}


def word_names(fix: dict, n_words: int) -> list[str | None]:
    """Per-word speaker name from a speaker_fix result (for scoring / display)."""
    out: list[str | None] = [None] * n_words
    for ln in fix["lines"]:
        for i in range(ln["words"][0], min(ln["words"][1], n_words - 1) + 1):
            out[i] = ln["name"]
    return out


# ------------------------------------------------------------------ run
def run(tr: dict, video_id: str, client, spec, corrections: dict | None = None, guard=None) -> dict:
    """One call. ``client`` = LLMClient-like (generate_json(role, spec, system, prompt, schema, meta=, want_raw=))."""
    tag, system, user = build_request(tr, corrections)
    proj = project(system, user, len(tr["sentences"]), spec.model,
                   int(spec.params.get("max_output_tokens", MODEL["params"]["max_output_tokens"])))
    if guard is not None and hasattr(client, "before_call"):
        client.before_call = guard
    led = getattr(client, "ledger", None)
    n0 = len(led.calls) if led is not None else 0
    ans, raw = client.generate_json("speaker_fix", spec, system, user, SCHEMA,
                                    meta={"video_id": video_id, "prompt": tag}, want_raw=True)
    spent = billed_inr(led, n0)
    res = {"video_id": video_id, "schema": SCHEMA_VERSION, "model": spec.model, "params": spec.params,
           "prompt": tag, **parse_answer(ans, tr),
           "cost": {"projected": proj, "actual": {"input_tokens": raw.input_tokens,
                                                   "output_tokens": raw.output_tokens,
                                                   "thought_tokens": raw.thought_tokens,
                                                   "inr": round(spent, 3), "cached": spent == 0.0}}}
    return res


def load_transcript(video_id: str, path: str | Path | None = None) -> tuple[dict, Path]:
    p = Path(path or ROOT / "scribe_dataset" / video_id / f"{video_id}_scribe_transcript.json")
    return json.loads(p.read_text(encoding="utf-8")), p


def main(argv=None):
    ap = argparse.ArgumentParser(description="LLM speaker correction layer (never edits the transcript).")
    ap.add_argument("video_ids", nargs="+")
    ap.add_argument("--transcript", help="only with one id; default scribe_dataset/<id>/<id>_scribe_transcript.json")
    ap.add_argument("--no-asr-fix", action="store_true", help="show raw Scribe text even where runs/asr_fix exists")
    ap.add_argument("--dry-run", action="store_true", help="print the projection; no API call")
    ap.add_argument("--max-ledger-inr", type=float, default=None,
                    help="refuse a call whose worst case would push the project ledger past this")
    ap.add_argument("--budget-inr", type=float, default=None,
                    help="refuse a call whose worst case would push this experiment's tally past this")
    ap.add_argument("--out", default=str(OUT_DIR))
    a = ap.parse_args(argv)
    if a.transcript and len(a.video_ids) != 1:
        ap.error("--transcript needs exactly one video id")
    from .asr_fix import corrections_path, load_corrections, stale_corrections
    jobs = []
    for vid in a.video_ids:
        tr, tp = load_transcript(vid, a.transcript)
        corr = None
        if not a.no_asr_fix and corrections_path(vid).exists():
            corr = load_corrections(vid)
            stale = stale_corrections(tr["word_timestamps"], corr)
            if stale:
                print(f"warning: {vid}: {len(stale)} stale ASR corrections ignored")
                bad = {(c["start_word"], c["end_word"]) for c in stale}
                corr = {**corr, "corrections": [c for c in corr["corrections"]
                                                if (c["start_word"], c["end_word"]) not in bad]}
        tag, system, user = build_request(tr, corr)
        proj = project(system, user, len(tr["sentences"]))
        print(f"{vid}: {len(tr['sentences'])} lines, asr_fix={'yes' if corr else 'no'}, prompt {tag}: "
              f"projected in {proj['input_tokens']:,} / out {proj['output_tokens']:,} tokens, ₹{proj['inr']:.2f} "
              f"(worst case ₹{proj['worst_case_inr']:.2f})")
        jobs.append((vid, tr, tp, corr, proj))
    print(f"projected total ₹{sum(j[4]['inr'] for j in jobs):.2f} "
          f"(worst case ₹{sum(j[4]['worst_case_inr'] for j in jobs):.2f}); "
          f"speaker_fix spent so far ₹{session_spent():.2f}")
    if a.dry_run:
        return 0
    os.environ.setdefault("SHOTVI_SPEND_SOURCE", "speaker_fix")
    from shotvi.llm import Ledger, LLMClient, ModelSpec
    from shotvi.llm.base import LLMError
    client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"), max_retries=0, schema_retries=0)
    # one paid attempt per episode: a MAX_TOKENS / invalid answer is not retried (2026-10-13: two 16k-token
    # MAX_TOKENS attempts on one episode cost ₹12)
    spec = ModelSpec.from_dict(MODEL)
    rc = 0
    for vid, tr, tp, corr, proj in jobs:
        n0 = len(client.ledger.calls)
        guard = make_guard(proj["worst_case_inr"], a.max_ledger_inr, a.budget_inr,
                           in_flight=lambda n0=n0: billed_inr(client.ledger, n0))
        try:
            res = run(tr, vid, client, spec, corr, guard)
        except SpeakerFixStop as e:
            print(f"{vid}: STOP {e}")
            rc = 2
            break
        except LLMError as e:
            # keep what was billed for diagnosis (finish reason, token split, the truncated text)
            raw = getattr(e.__cause__, "response", None)
            fail = {"video_id": vid, "error": str(e)[:300], "prompt": load_prompt()[0], "model": spec.model,
                    "params": spec.params}
            if raw is not None:
                fail.update({"finish_reason": raw.finish_reason, "input_tokens": raw.input_tokens,
                             "output_tokens": raw.output_tokens, "thought_tokens": raw.thought_tokens,
                             "partial_text": raw.text})
            fp = Path(a.out) / f"{vid}.failed.json"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(json.dumps(fail, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{vid}: FAILED {str(e)[:200]} -> {fp}")
            rc = 1
            break
        finally:
            spent = billed_inr(client.ledger, n0)   # failed attempts included
            if spent:
                add_session_spend(spent, vid)
        res["transcript"] = str(tp)
        res["asr_fix"] = bool(corr)
        out = Path(a.out) / f"{vid}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        act, cov = res["cost"]["actual"], res["coverage"]
        print(f"{vid}: ₹{act['inr']:.2f} (in {act['input_tokens']:,}, out {act['output_tokens']:,}, "
              f"thinking {act['thought_tokens']:,}{', cached' if act['cached'] else ''}); cast "
              f"{len(res['cast'])}: " + ", ".join(f"{c['name']} ({c['n_lines']})" for c in res["cast"]))
        print(f"  coverage: {cov['assigned']}/{cov['n_lines']} assigned, {cov['unknown']} X, {cov['missing']} missing, "
              f"{cov['duplicate_ids']} dup, {len(cov['invalid_items'])} invalid, bad keys {cov['bad_keys']} -> {out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
