"""LLM speaker correction: who speaks each line, inferred from content (an additive layer; the transcript is never
modified).

Scribe's diarization merges characters under one label and swaps labels (web series especially). The episode is
read in CHUNKS of Scribe sentences ("lines"): each call shows a core of ``CORE_MIN``-``CORE_MAX`` lines plus
``MARGIN`` lines of overlap on each side, ASR-corrected text from ``runs/asr_fix/<id>/corrections.json`` where
present, Scribe's labels as weak hints, and the RUNNING CAST (keys, names, one-line descriptions inferred by the
earlier chunks) so names stay consistent. The model returns a cast plus a cast key per line, run-length coded::

    {"cast": [{"key": "A", "name": "...", "desc": "..."}], "turns": ["0-2:A", "3:B?", "4:C??", "5:X"]}

``?`` = fairly unsure, ``??`` = a guess, ``X`` = no basis. Code validates each answer: only the line ids shown, each
id once (first answer wins), keys must be in the running or returned cast. Merge by line id: the chunk whose CORE
holds the line wins; a margin answer is used only when the core answer is missing. A chunk may rename a key
(a role named later, e.g. "the roommate" -> a name): the final name is the latest one, earlier ones go to ``aka``.

Chunking (no LLM): cut between two lines at the strongest boundary in the allowed range, strength = silence (s) +
``MUSIC_BONUS_S`` if a music / song audio event lies in the silence (same signal as ``shotvi.select.scenes``; that
splitter drops openings and sponsor reads, which still need speakers, so it is not reused).

Model: gemini-3.8-flash, thinking LOW, default temperature, ``max_output_tokens`` sized to the chunk. Why low: the
evidence is local (who is addressed, who answers), the answer is small (~1k tokens), and the one whole-episode call
at thinking medium spent its whole 16k-token budget without an answer (2026-10-13, ₹12). Exactly ONE paid attempt per
chunk; a failed chunk is saved to ``<id>.failed.json`` and its lines fall back to the neighbours' margin answers.

Output ``runs/speaker_fix/<id>.json``::

    {"video_id", "schema": "speaker_fix/2", "model", "params", "prompt": "<version>+<sha8>", "transcript", "asr_fix",
     "chunks": [{part, core: [a, b], shown: [a, b], ok, error?, cost}],
     "cast": [{key, name, desc, aka, n_lines, n_words}],
     "lines": [{id, start, end, words: [i0, i1], scribe, key, name, confidence, part, from_margin}],
     "coverage": {n_lines, assigned, unknown, missing, from_margin, duplicate_ids, invalid_items, bad_keys},
     "cost": {projected, actual}}

``confidence``: 0.9 plain, 0.6 ``?``, 0.3 ``??``, 0.0 ``X`` / missing.

CLI::

    python -m shotvi.segment.speaker_fix <id> [<id> ...] --dry-run
    python -m shotvi.segment.speaker_fix <id> ... --max-ledger-inr 1513 --budget-inr 15
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
SCHEMA_VERSION = "speaker_fix/2"
PROMPT_PATH = ROOT / "prompts" / "speaker_fix.md"
OUT_DIR = ROOT / "runs" / "speaker_fix"
MODEL = {"provider": "gemini", "model": "gemini-3.8-flash", "params": {"thinking_level": "low"}}
CONF = {"": 0.9, "?": 0.6, "??": 0.3}
CORE_MIN, CORE_MAX = 80, 120    # lines per chunk core
LAST_MAX = 140                  # a final remainder up to this many lines stays one chunk
MARGIN = 10                     # overlap lines shown on each side
MUSIC_BONUS_S = 2.0             # as shotvi.select.scenes
MUSIC_RE = re.compile(r"సంగీత|పాట|music|song", re.I)
TEXT_CAL = 1.25                 # estimate_tokens under-counts (asr_fix calibration against countTokens)
OUT_TOKENS_PER_LINE = 5         # run-length turns + JSON
CAST_OUT_TOKENS = 40            # per cast entry in the answer
THINK_EST = (1500, 10)          # projection: low thinking, base + per line (a guess; checked against actuals)
MAX_OUT = (4096, 40)            # max_output_tokens = base + per shown line (several times the expected answer)
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


def render(tr: dict, corrections: dict | None = None, first: int = 0, last: int | None = None,
           texts: list[str] | None = None) -> tuple[str, int]:
    """Numbered lines first..last (id = Scribe sentence position) with the audio events among them."""
    sents = tr["sentences"]
    last = len(sents) - 1 if last is None else last
    texts = texts if texts is not None else line_texts(tr, corrections)
    rows = [(sents[i]["start"], 1, f"{i} [{fmt_ts(sents[i]['start'])}] {short_label(sents[i].get('speaker'))}| "
                                   f"{texts[i]}") for i in range(first, last + 1)]
    if rows:
        t0 = sents[first]["start"] if first > 0 else float("-inf")
        t1 = sents[last + 1]["start"] if last + 1 < len(sents) else float("inf")
        rows += [(e["start"], 0, f"({fmt_ts(e['start'])} {e['text']})") for e in tr.get("audio_events") or []
                 if t0 <= e["start"] < t1]
    rows.sort(key=lambda r: (r[0], r[1]))
    return "\n".join(r[2] for r in rows), last - first + 1


def cast_block(cast: list[dict]) -> str:
    if not cast:
        return "No speakers identified yet (this is the first part)."
    rows = [f"{c['key']}: {c['name']}" + (f" ({c['desc']})" if c.get("desc") else "") for c in cast]
    return ("Speakers identified in earlier parts (reuse these keys for the same people; new people get new keys):\n"
            + "\n".join(rows))


# ------------------------------------------------------------------ chunks
def boundary_strength(tr: dict, k: int) -> float:
    """Strength of the cut before line k: silence + music bonus."""
    s = tr["sentences"]
    a, b = s[k - 1]["end"], s[k]["start"]
    music = any(MUSIC_RE.search(e.get("text", "")) and e["start"] < b and e["end"] > a
                for e in tr.get("audio_events") or [])
    return max(0.0, b - a) + (MUSIC_BONUS_S if music else 0.0)


def plan_chunks(tr: dict, core_min: int = CORE_MIN, core_max: int = CORE_MAX, last_max: int = LAST_MAX,
                margin: int = MARGIN) -> list[dict]:
    """[{part, core: [a, b], shown: [a', b']}] covering every line exactly once in the cores."""
    n = len(tr["sentences"])
    cores, a = [], 0
    while n - a > last_max:
        lo, hi = a + core_min, min(a + core_max, n - core_min)
        if hi < lo:
            hi = lo = min(a + core_max, n - 1)
        k = max(range(lo, hi + 1), key=lambda k: (boundary_strength(tr, k), -abs(k - (a + (core_min + core_max) // 2))))
        cores.append((a, k - 1))
        a = k
    if a < n:
        cores.append((a, n - 1))
    return [{"part": i + 1, "core": [x, y], "shown": [max(0, x - margin), min(n - 1, y + margin)]}
            for i, (x, y) in enumerate(cores)]


# ------------------------------------------------------------------ cost
def inr(model: str, tin: int, tout: int) -> float:
    from shotvi.llm.pricing import cost_usd
    from shotvi.llm.spend import usd_inr
    return (cost_usd(model, tin, tout) or (tin * 5 + tout * 20) / 1e6) * usd_inr()


def max_out_tokens(n_shown: int) -> int:
    return MAX_OUT[0] + MAX_OUT[1] * n_shown


def project_chunk(system: str, user: str, n_shown: int, n_cast_guess: int = 8, model: str = MODEL["model"]) -> dict:
    tin = int(TEXT_CAL * (estimate_tokens(system) + estimate_tokens(user))) + 30 * n_cast_guess
    tout = (CAST_OUT_TOKENS * n_cast_guess + OUT_TOKENS_PER_LINE * n_shown
            + THINK_EST[0] + THINK_EST[1] * n_shown)
    mx = max_out_tokens(n_shown)
    return {"input_tokens": tin, "output_tokens": tout, "max_output_tokens": mx,
            "inr": round(inr(model, tin, tout), 2), "worst_case_inr": round(inr(model, tin, mx), 2)}


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
    project ledger past ``max_ledger_inr`` or this run's spend past ``budget_inr``. ``in_flight()`` = rupees this
    run has billed so far (all chunks and attempts; bug fixed 2026-10-13: a retry once passed the budget check)."""
    def guard(role, spec):
        if max_ledger_inr is not None:
            from shotvi.llm.spend import total_inr
            now = total_inr()
            if now + worst_case_inr > max_ledger_inr:
                raise SpeakerFixStop(f"ledger ₹{now:.2f} + worst case ₹{worst_case_inr:.2f} > ₹{max_ledger_inr:.2f}")
        if budget_inr is not None:
            spent = in_flight() if in_flight else 0.0
            if spent + worst_case_inr > budget_inr:
                raise SpeakerFixStop(f"this run spent ₹{spent:.2f} + worst case ₹{worst_case_inr:.2f} "
                                     f"> budget ₹{budget_inr:.2f}")
    return guard


# ------------------------------------------------------------------ parse / merge
def parse_chunk(ans: dict, ids: range, cast: dict[str, dict]) -> tuple[dict[int, tuple], dict]:
    """Validate one answer. ``cast`` = running cast {key: {key, name, desc, aka}}, updated IN PLACE (new keys,
    renames). Returns ({line id: (key or None, '?'-marks)}, coverage)."""
    cov = {"duplicate_ids": 0, "invalid_items": [], "bad_keys": []}
    seen = set()
    for c in ans.get("cast") or []:
        k = str(c.get("key", "")).strip()
        name = " ".join(str(c.get("name", "")).split())
        desc = " ".join(str(c.get("desc", "")).split())
        if not k or not name or k == "X" or k in seen:   # a key twice in one answer: the first entry wins
            continue
        seen.add(k)
        if k in cast:
            old = cast[k]
            if name.lower() != old["name"].lower():
                if old["name"] not in old["aka"]:
                    old["aka"].append(old["name"])
                old["name"] = name
            if desc:
                old["desc"] = desc
        else:
            cast[k] = {"key": k, "name": name, "desc": desc, "aka": []}
    got: dict[int, tuple] = {}
    for item in ans.get("turns") or []:
        m = TURN_RE.match(str(item))
        if not m:
            cov["invalid_items"].append(str(item)[:40])
            continue
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) is not None else a
        key, q = m.group(3), m.group(4)
        if b < a or a not in ids or b not in ids:
            cov["invalid_items"].append(str(item)[:40])   # an id not shown: never invent lines
            continue
        if key != "X" and key not in cast:
            cov["bad_keys"].append(key)
            key = None
        for i in range(a, b + 1):
            if i in got:
                cov["duplicate_ids"] += 1
                continue
            got[i] = (None if key == "X" else key, q)
    cov["bad_keys"] = sorted(set(cov["bad_keys"]))
    return got, cov


def merge(tr: dict, chunks: list[dict], answers: dict[int, dict], cast: dict[str, dict]) -> dict:
    """answers: part -> {line id: (key, q)}. Core answer wins, then the nearest chunk's margin answer."""
    sents = tr["sentences"]
    lines, n_margin = [], 0
    for i, s in enumerate(sents):
        own = next(c for c in chunks if c["core"][0] <= i <= c["core"][1])
        pick, part, margin = answers.get(own["part"], {}).get(i), own["part"], False
        if pick is None:
            for c in sorted(chunks, key=lambda c: abs(c["part"] - own["part"])):
                if c is not own and i in answers.get(c["part"], {}):
                    pick, part, margin = answers[c["part"]][i], c["part"], True
                    break
        k, q = pick if pick else (None, "")
        n_margin += margin and k is not None
        lines.append({"id": i, "start": s["start"], "end": s["end"], "words": list(s["words"]),
                      "scribe": s.get("speaker"), "key": k, "name": cast[k]["name"] if k else None,
                      "confidence": CONF.get(q, 0.3) if k else 0.0, "part": part if pick else None,
                      "from_margin": margin})
    out_cast = []
    for c in cast.values():
        mine = [ln for ln in lines if ln["key"] == c["key"]]
        out_cast.append({**c, "n_lines": len(mine),
                         "n_words": sum(ln["words"][1] - ln["words"][0] + 1 for ln in mine)})
    answered = {i for a in answers.values() for i in a}
    cov = {"n_lines": len(sents), "assigned": sum(1 for ln in lines if ln["key"]),
           "unknown": sum(1 for ln in lines if not ln["key"] and ln["id"] in answered),
           "missing": sum(1 for ln in lines if ln["id"] not in answered), "from_margin": n_margin}
    return {"cast": out_cast, "lines": lines, "coverage": cov}


def parse_answer(ans: dict, tr: dict) -> dict:
    """Single answer over the whole transcript (one chunk); kept for tests and small transcripts."""
    n = len(tr["sentences"])
    cast: dict[str, dict] = {}
    got, cov = parse_chunk(ans, range(n), cast)
    res = merge(tr, [{"part": 1, "core": [0, n - 1], "shown": [0, n - 1]}], {1: got}, cast)
    res["coverage"].update(cov)
    return res


def word_names(fix: dict, n_words: int) -> list[str | None]:
    """Per-word speaker name from a speaker_fix result (for scoring / display)."""
    out: list[str | None] = [None] * n_words
    for ln in fix["lines"]:
        for i in range(ln["words"][0], min(ln["words"][1], n_words - 1) + 1):
            out[i] = ln["name"]
    return out


# ------------------------------------------------------------------ run
def chunk_request(tr: dict, ch: dict, n_parts: int, cast: dict, texts: list[str], prompt=None) -> tuple[str, str, str]:
    tag, system, user = prompt or load_prompt()
    a, b = ch["shown"]
    text, _ = render(tr, first=a, last=b, texts=texts)
    return tag, system, Template(user).substitute(part=ch["part"], n_parts=n_parts, cast=cast_block(list(cast.values())),
                                                  first_id=a, last_id=b, lines=text)


def project(tr: dict, corrections: dict | None = None, model: str = MODEL["model"]) -> dict:
    texts = line_texts(tr, corrections)
    chunks = plan_chunks(tr)
    prompt = load_prompt()
    per = []
    for ch in chunks:
        _, system, user = chunk_request(tr, ch, len(chunks), {}, texts, prompt)
        per.append({"part": ch["part"], "core": ch["core"], "shown": ch["shown"],
                    **project_chunk(system, user, ch["shown"][1] - ch["shown"][0] + 1, model=model)})
    return {"chunks": per, "inr": round(sum(p["inr"] for p in per), 2),
            "worst_case_inr": round(sum(p["worst_case_inr"] for p in per), 2)}


def run(tr: dict, video_id: str, client, spec, corrections: dict | None = None, max_ledger_inr: float | None = None,
        budget_inr: float | None = None, spent_before=lambda: 0.0, log=print) -> dict:
    """All chunks of one episode. ``client`` = LLMClient-like (generate_json(role, spec, system, prompt, schema, meta=,
    want_raw=), optional ``ledger`` and ``before_call``). ``spent_before()`` = rupees this run billed before this
    episode (budget). Stops (``stopped`` set) when a guard refuses; failed chunks are recorded, not retried."""
    from shotvi.llm.base import LLMError, ModelSpec
    texts = line_texts(tr, corrections)
    chunks = plan_chunks(tr)
    prompt = load_prompt()
    cast: dict[str, dict] = {}
    answers: dict[int, dict] = {}
    led = getattr(client, "ledger", None)
    n0 = len(led.calls) if led is not None else 0
    proj = project(tr, corrections, spec.model)
    out_chunks, cov_all, failures, stopped = [], {"duplicate_ids": 0, "invalid_items": [], "bad_keys": []}, [], None
    for ch, pj in zip(chunks, proj["chunks"]):
        tag, system, user = chunk_request(tr, ch, len(chunks), cast, texts, prompt)
        n_shown = ch["shown"][1] - ch["shown"][0] + 1
        cspec = ModelSpec(spec.provider, spec.model, {**spec.params, "max_output_tokens": max_out_tokens(n_shown)})
        worst = project_chunk(system, user, n_shown, n_cast_guess=0, model=spec.model)["worst_case_inr"]
        if hasattr(client, "before_call"):
            client.before_call = make_guard(worst, max_ledger_inr, budget_inr,
                                            in_flight=lambda: spent_before() + billed_inr(led, n0))
        k0 = len(led.calls) if led is not None else 0
        rec = {"part": ch["part"], "core": ch["core"], "shown": ch["shown"], "projected_inr": pj["inr"]}
        try:
            ans, raw = client.generate_json("speaker_fix", cspec, system, user, SCHEMA,
                                            meta={"video_id": video_id, "prompt": tag, "part": ch["part"]},
                                            want_raw=True)
        except SpeakerFixStop as e:
            stopped = str(e)
            log(f"{video_id} part {ch['part']}: STOP {e}")
            break
        except LLMError as e:
            raw = getattr(e.__cause__, "response", None)
            fail = {"part": ch["part"], "error": str(e)[:300]}
            if raw is not None:
                fail.update({"finish_reason": raw.finish_reason, "input_tokens": raw.input_tokens,
                             "output_tokens": raw.output_tokens, "thought_tokens": raw.thought_tokens,
                             "partial_text": raw.text})
            failures.append(fail)
            rec.update({"ok": False, "error": str(e)[:200], "inr": round(billed_inr(led, k0), 3)})
            out_chunks.append(rec)
            log(f"{video_id} part {ch['part']}: FAILED {str(e)[:160]}")
            continue
        ids = range(ch["shown"][0], ch["shown"][1] + 1)
        got, cov = parse_chunk(ans, ids, cast)
        answers[ch["part"]] = got
        cov_all["duplicate_ids"] += cov["duplicate_ids"]
        cov_all["invalid_items"] += cov["invalid_items"]
        cov_all["bad_keys"] = sorted(set(cov_all["bad_keys"]) | set(cov["bad_keys"]))
        rec.update({"ok": True, "inr": round(billed_inr(led, k0), 3), "input_tokens": raw.input_tokens,
                    "output_tokens": raw.output_tokens, "thought_tokens": raw.thought_tokens,
                    "missing_in_chunk": sum(1 for i in ids if i not in got)})
        out_chunks.append(rec)
        log(f"{video_id} part {ch['part']}/{len(chunks)} lines {ch['shown'][0]}-{ch['shown'][1]}: ₹{rec['inr']:.2f} "
            f"(projected ₹{pj['inr']:.2f}; in {raw.input_tokens:,}, out {raw.output_tokens:,}, "
            f"thinking {raw.thought_tokens:,}); cast now {len(cast)}")
    res = {"video_id": video_id, "schema": SCHEMA_VERSION, "model": spec.model, "params": spec.params,
           "prompt": prompt[0], "chunks": out_chunks, **merge(tr, chunks, answers, cast)}
    res["coverage"].update(cov_all)
    res["cost"] = {"projected": proj, "actual": {"inr": round(billed_inr(led, n0), 3),
                                                 "chunks_ok": sum(c.get("ok", False) for c in out_chunks),
                                                 "chunks": len(chunks)}}
    res["stopped"] = stopped
    res["failures"] = failures
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
                    help="refuse a call whose worst case would push THIS run's spend past this")
    ap.add_argument("--out", default=str(OUT_DIR))
    a = ap.parse_args(argv)
    if a.transcript and len(a.video_ids) != 1:
        ap.error("--transcript needs exactly one video id")
    for st in (sys.stdout, sys.stderr):   # ₹ and Telugu on Windows consoles
        if hasattr(st, "reconfigure"):
            st.reconfigure(encoding="utf-8")
    from .asr_fix import corrections_path, load_corrections, stale_corrections
    jobs, tot, worst = [], 0.0, 0.0
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
        proj = project(tr, corr)
        tot += proj["inr"]
        worst += proj["worst_case_inr"]
        print(f"{vid}: {len(tr['sentences'])} lines, asr_fix={'yes' if corr else 'no'}, prompt {load_prompt()[0]}, "
              f"{len(proj['chunks'])} chunks: projected ₹{proj['inr']:.2f} (worst case ₹{proj['worst_case_inr']:.2f})")
        for c in proj["chunks"]:
            print(f"    part {c['part']}: core {c['core'][0]}-{c['core'][1]} shown {c['shown'][0]}-{c['shown'][1]}  "
                  f"in {c['input_tokens']:,} out ~{c['output_tokens']:,} (max {c['max_output_tokens']:,})  "
                  f"₹{c['inr']:.2f} / worst ₹{c['worst_case_inr']:.2f}")
        jobs.append((vid, tr, tp, corr))
    print(f"projected total ₹{tot:.2f} (worst case ₹{worst:.2f}); speaker_fix spent before ₹{session_spent():.2f}")
    if a.dry_run:
        return 0
    os.environ.setdefault("SHOTVI_SPEND_SOURCE", "speaker_fix")
    from shotvi.llm import Ledger, LLMClient, ModelSpec
    client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"), max_retries=0, schema_retries=0)
    spec = ModelSpec.from_dict(MODEL)
    rc = 0
    for vid, tr, tp, corr in jobs:
        n0 = len(client.ledger.calls)
        try:
            res = run(tr, vid, client, spec, corr, a.max_ledger_inr, a.budget_inr,
                      spent_before=lambda before=billed_inr(client.ledger, 0): before)
        finally:
            spent = billed_inr(client.ledger, n0)
            if spent:
                add_session_spend(spent, f"{vid} (chunked)")
        res["transcript"] = str(tp.relative_to(ROOT)) if tp.is_relative_to(ROOT) else str(tp)
        res["asr_fix"] = bool(corr)
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        if res["failures"]:
            (out / f"{vid}.failed.json").write_text(json.dumps({"video_id": vid, "failures": res["failures"]},
                                                               ensure_ascii=False, indent=1), encoding="utf-8")
        if any(c.get("ok") for c in res["chunks"]):
            (out / f"{vid}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        cov = res["coverage"]
        print(f"{vid}: ₹{res['cost']['actual']['inr']:.2f} actual vs ₹{res['cost']['projected']['inr']:.2f} projected; "
              f"{res['cost']['actual']['chunks_ok']}/{res['cost']['actual']['chunks']} chunks ok; cast "
              f"{len(res['cast'])}: " + ", ".join(f"{c['name']} ({c['n_lines']})" for c in res["cast"]))
        print(f"  coverage: {cov['assigned']}/{cov['n_lines']} assigned, {cov['unknown']} X, {cov['missing']} missing, "
              f"{cov['from_margin']} from margins, {cov['duplicate_ids']} dup, {len(cov['invalid_items'])} invalid, "
              f"bad keys {cov['bad_keys']}")
        if res["stopped"]:
            rc = 2
            break
        if res["failures"]:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
