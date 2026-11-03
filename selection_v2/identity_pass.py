"""Identity pass, the VIDEO step (founder, 2026-11-02): ONE Gemini call that watches the whole episode and reads the
transcript with the text step's (Stage 1B's) answer, and says for EVERY spoken line who talks (one person, several
together, or nobody it can tell; a voice change inside the line as ``turns``) and who is on screen (anyone visible at
any moment of the line, possibly several or nobody). ``prompts/identity_pass.md``, ``schemas/identity_pass.schema.json``.

It replaces the per-(scene, raw label) answer of 2026-10-26 (the founder lifted that rule on 2026-11-02): reaction
shots, groups and two voices in one line need a per-line answer. Scene titles are no longer sent (the model copied
them); the text step's people and per-line persons are, marked as a guess to check.

Code checks the format only (ids, keys, the quoted turn word exists) and stores the answer; the naming check
(``naming_check.py``, one text call) then decides the final person per line. Output: ``facts["identity_pass"]``.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from shotvi.llm.base import ModelSpec

from . import naming_check as NC
from . import stage1_episode_pass as s1b
from .config import ROOT, inr_per_usd
from .media import content_key, cut_scene, media_part, upload
from .paths import write_json
from .runlog import append_log
from .stage1_facts import fmt_ts

VERSION = "identity_pass/0.2"
ROLE = "identity_pass"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "identity_pass.md"
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "identity_pass.schema.json"
NEW_KEY = re.compile(r"^V\d+$")


class IdentityPassError(RuntimeError):
    pass


def naming_check_on(cfg: dict) -> bool:
    """founder, 2026-11-01: a model (the naming check) decides the final names from the transcript + this answer."""
    return bool((cfg.get("naming_check") or {}).get("enabled"))


def speech(facts: dict) -> list[dict]:
    return [r for r in facts["lines"] if r["kind"] == "speech"]


def context_text(facts: dict, base: dict) -> str:
    """PART 1 (the text step's people) and PART 2 (the transcript by scene with the text step's person per line)."""
    names = NC._names(facts, base)
    sp = {s["key"]: s for s in base["speakers"]}
    used = sorted({NC._key_of(r, base) for r in speech(facts)} - {None})
    ep = facts["episode"]
    out = [f"# EPISODE: {ep.get('title') or ep['video_id']} ({ep['duration_str']})", "",
           "# PART 1: PEOPLE THE TEXT STEP FOUND (key = name: role; the clues it used)"]
    for k in used:
        s = sp.get(k) or {}
        out.append(f"{k} = {names.get(k) or '?'}: {s.get('role') or ''}" + (f" ({s['evidence']})" if s.get("evidence") else ""))
    out += ["", "# PART 2: TRANSCRIPT BY SCENE (id [start-end] voice label | text step's person | words)"]
    scenes = facts["episode_pass"]["derived"]["scenes"]
    si = 0
    for r in facts["lines"]:
        while si < len(scenes) and r["start"] >= scenes[si]["start"] - 1e-6:
            out.append(f"\n== Scene {scenes[si]['id']} ({scenes[si]['start_str']}-{scenes[si]['end_str']})")
            si += 1
        if r["kind"] == "speech":
            out.append(f"{r['id']} [{fmt_ts(r['start'])}-{fmt_ts(r['end'])}] {r.get('speaker') or '?'} | {NC._key_of(r, base) or '?'} | {r['text']}")
        else:
            out.append(f"{r['id']} [{fmt_ts(r['start'])}] (sound) {r.get('text') or r.get('tag') or ''}")
    return "\n".join(out)


def validate(ans: dict, facts: dict, base: dict, max_missing_share: float = 0.05) -> tuple[list[str], list[str], dict]:
    """Format checks only. Returns (errors, warnings, lines): errors make the call retry; small line-level slips are
    warnings (the item is dropped, never guessed); ``lines`` = id -> {talk, see, turns: [{at, word, talk}], why}."""
    errs, warns = [], []
    by_id = {r["id"]: r for r in speech(facts)}
    text_keys = {NC._key_of(r, base) for r in by_id.values()} - {None}
    people = ans.get("people") or []
    keys = [p.get("key") for p in people]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        errs.append(f"people: key(s) {dup} listed more than once")
    by_key = {p.get("key"): p for p in people}
    # a text-step key left out of people is a slip worth a re-ask (a whole second video call) only when the answer still
    # uses it; otherwise its lines simply keep the text step's person where the video step did not give them to anyone
    used = {k for e in ans.get("lines") or [] for k in list(e.get("talk") or []) + list(e.get("see") or [])
            + [x for t in e.get("turns") or [] for x in t.get("talk") or []]}
    missing = sorted(text_keys - set(keys))
    if missing and set(missing) & used:
        errs.append(f"people: the text step's key(s) {sorted(set(missing) & used)} are used in lines but missing from people; list every person you use")
    elif missing:
        warns.append(f"the text step's key(s) {missing} are not in people (and not used in lines); their lines keep the text step's person where not reassigned")
    for p in people:
        k = p.get("key")
        if k not in text_keys and not NEW_KEY.match(str(k)):
            errs.append(f"people {k!r}: a person the text step did not have gets a new key V1, V2, ...")
        if not (p.get("name") or "").strip():
            errs.append(f"people {k}: empty name")
        sa = p.get("same_as")
        if sa:
            if sa not in by_key or sa == k:
                errs.append(f"people {k}: same_as {sa!r} is not another listed person's key")
            elif by_key[sa].get("same_as"):
                errs.append(f"people {k}: same_as {sa!r} is itself same_as another key; point to the final key")
    known = set(by_key)
    lines, bad_keys = {}, set()
    n_unknown = n_dup = n_turn_dropped = n_no_why = 0
    for e in ans.get("lines") or []:
        lid = e.get("id")
        if lid not in by_id:
            n_unknown += 1; continue
        if lid in lines:
            n_dup += 1; continue
        talk, see = list(e.get("talk") or []), list(e.get("see") or [])
        bad_keys |= {k for k in talk + see if k not in known}
        turns, after = [], 0
        for t in e.get("turns") or []:
            bad_keys |= {k for k in t.get("talk") or [] if k not in known}
            i = NC.turn_index(by_id[lid]["text"], t.get("at") or "", after)
            if i is None:
                n_turn_dropped += 1; continue
            turns.append({"at": t.get("at"), "word": i, "talk": list(t.get("talk") or [])})
            after = i
        why = (e.get("why") or "").strip()
        if (talk != [NC._key_of(by_id[lid], base)] or turns) and not why:
            n_no_why += 1
        lines[lid] = {"talk": talk, "see": see, "turns": turns, "why": why}
    if bad_keys:
        errs.append(f"lines: key(s) {sorted(bad_keys)} in talk / see / turns are not in people; list every person you use")
    absent = [lid for lid in by_id if lid not in lines]
    if len(absent) > max_missing_share * len(by_id):
        errs.append(f"lines: {len(absent)} of {len(by_id)} spoken lines have no entry (e.g. {absent[:8]}); answer every spoken line")
    elif absent:
        warns.append(f"{len(absent)} spoken line(s) without an entry: {absent[:12]}")
    for n, what in ((n_unknown, "entries with an id that is not a spoken line were dropped"), (n_dup, "repeated line entries were dropped (first kept)"),
                    (n_turn_dropped, "turns whose word is not in the line were dropped"), (n_no_why, "changed lines have no why")):
        if n:
            warns.append(f"{n} {what}")
    return errs, warns, lines


def finalize(facts: dict, record: dict, base: dict) -> dict:
    """The final speaker map from the video step alone (see ``answer_from_video``); the summary (cast with names and
    looks, alone_on_screen for the cast sheet, line changes) goes to ``facts["speakers_from_video"]``."""
    out, summary = NC.apply(facts, answer_from_video(facts, record, base), base, record)
    out["speakers_from_video"] = {"version": VERSION, "base": base, **summary}
    return out


def answer_from_video(facts: dict, record: dict, base: dict) -> dict:
    """founder, 2026-11-02 (no naming check): the video step's answer IS the final answer. This only rewrites it in the
    naming check's answer shape so the same bookkeeping (``naming_check.apply``) writes the speaker map: its people with
    their names, every line where it heard someone other than the text step's person, its voice changes. A line it could
    not tell (talk []) or did not answer keeps the text step's person."""
    by_id = {r["id"]: r for r in speech(facts)}
    vm = NC.video_merge(record)
    people = [{"key": p["key"], "name": p["name"], "role": p.get("role") or "", "gender": p.get("gender") or "unknown",
               "aka": list(p.get("aka") or []), "main": bool(p.get("main")), "video_keys": [] if p.get("same_as") else [p["key"]],
               **({"same_as": p["same_as"]} if p.get("same_as") else {})} for p in record.get("people") or []]
    listed = {p["key"] for p in people}
    names = NC._names(facts, base)
    roles = {x["key"]: x.get("role") or "" for x in base["speakers"]}
    for k in sorted({NC._key_of(r, base) for r in by_id.values()} - listed - {None}):   # a text-step person it left out
        people.append({"key": k, "name": names.get(k) or k, "role": roles.get(k, ""), "gender": "unknown", "aka": [], "main": False, "video_keys": []})
    changes, splits = [], []
    for lid, e in (record.get("lines") or {}).items():
        if lid not in by_id:
            continue
        talk = list(dict.fromkeys(vm.get(k, k) for k in e.get("talk") or []))
        if talk and talk != [vm.get(NC._key_of(by_id[lid], base), NC._key_of(by_id[lid], base))]:
            changes.append({"why": e.get("why") or "the video step", "lines": [lid], "to": talk})
        for t in e.get("turns") or []:
            to = list(dict.fromkeys(vm.get(k, k) for k in t.get("talk") or []))
            if to:
                splits.append({"line": lid, "at": t["at"], "to": to, "why": e.get("why") or "the video step"})
    return {"people": people, "changes": changes, "splits": splits, "keep_text": [], "not_sure": []}


def expected_output_tokens(facts: dict, ic: dict) -> int:
    n = len(speech(facts))
    return int(ic.get("output_tokens_base", 3000)) + int(ic.get("output_tokens_per_line", 30)) * n


def estimate(facts: dict, cfg: dict, counter=None) -> dict:
    ic = cfg["identity_pass"]
    spec = ModelSpec.from_dict(ic["model"])
    text = context_text(facts, NC.base_of(facts)) + "\n\n" + "\n\n".join(s1b.load_prompt(PROMPT_PATH).values())
    if counter is None:
        from shotvi.segment.render import estimate_tokens
        counter = estimate_tokens
    dur = float(facts["episode"]["duration"])
    n_text = int(counter(text))
    n_video = int(dur * cfg["rates"]["tokens_per_s_video_low"])      # ~100 tokens/s at low resolution, sound included (countTokens, 2026-11-01: 6,182/min)
    r, rate = cfg["rates"], inr_per_usd(cfg)
    out = expected_output_tokens(facts, ic)
    inr = ((n_text + n_video) * r["usd_per_1m_input"] + out * r["usd_per_1m_output"]) / 1e6 * rate
    return {"text_tokens": n_text, "video_tokens": n_video, "input_tokens": n_text + n_video, "expected_output_tokens": out,
            "inr_expected": round(inr, 2), "model": spec.model}


def run(inputs, facts: dict | None, cfg: dict, client=None, log=None, cutter=None, uploader=None, deleter=None) -> dict:
    t0 = time.perf_counter()
    log = log or (lambda *a: None)
    if not facts or "1b" not in (facts.get("stages") or {}):
        raise RuntimeError("the identity pass needs the Stage 1B text pass first (its people and scenes are the starting point)")
    ic = cfg["identity_pass"]
    spec = ModelSpec.from_dict(ic["model"])
    run_dir = Path(inputs.paths.run_dir); run_dir.mkdir(parents=True, exist_ok=True)
    base = NC.base_of(facts)
    context = context_text(facts, base)
    (run_dir / "context_identity.txt").write_text(context, encoding="utf-8")
    cutter = cutter or cut_scene; uploader = uploader or upload
    dur = float(facts["episode"]["duration"])
    mpath = cutter(Path(inputs.paths.mp4), run_dir / cfg["stage2"]["media_dir"], 0, 0.0, dur, "video", int(ic.get("video_max_height", 480)), log)
    up = uploader(mpath, "video")
    try:
        mkey = content_key(mpath)
    except OSError:
        mkey = None
    # the video first, then the text that refers to it (Gemini's advice for long media)
    parts = [media_part(up, "video", 1, key=mkey), {"text": "The video above is the whole episode; its clock matches the line times below.\n\n" + context}]
    sections = s1b.load_prompt(PROMPT_PATH)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if client is None:
        from shotvi.llm.client import Ledger, LLMClient, get_provider
        client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"), max_retries=int(ic.get("http_retries", 1)))
        get_provider(spec.provider).timeout = float(ic.get("http_timeout_s", 1200))
    attempts, errors, ans, warns, lines = [], [], None, [], {}
    usage = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0}
    for attempt in range(int(ic.get("retries", 1)) + 1):
        instr = (sections["instructions"]
                 + ("\n\n" + sections["retry"].replace("{errors}", "\n".join(f"- {e}" for e in errors)) + f"\n(attempt {attempt + 1})" if errors else "")
                 + "\n\n" + sections["output_format"])
        att = {"n": attempt + 1, "instructions": instr, "errors_shown": errors}
        attempts.append(att)
        log(f"identity pass: call {attempt + 1}")
        try:
            obj, raw = client.generate_json(ROLE, spec, sections["system"], instr, schema, meta={"video_id": inputs.vid, "stage": "identity"}, want_raw=True, media=parts)
        except BaseException as e:
            att["error"] = f"{type(e).__name__}: {e}"
            write_json(run_dir / "identity_pass_debug.json", {"attempts": attempts, "system": sections["system"]})
            append_log(cfg, {"episode": inputs.vid, "stage": "identity_pass", "status": "failed", "duration_s": round(time.perf_counter() - t0, 1), "note": att["error"][:300]})
            raise
        att["raw_text"] = raw.text
        att["usage"] = {k: int(getattr(raw, k) or 0) for k in usage}
        for k in usage:
            usage[k] += att["usage"][k]
        errors, warns, lines = validate(obj, facts, base, float(ic.get("max_missing_share", 0.05)))
        att["validation"] = errors; att["warnings"] = warns
        if not errors:
            ans = obj; break
    if ic.get("delete_upload_after_call", True):
        try:
            from .media import delete_upload
            attempts[-1]["upload_deleted"] = (deleter or delete_upload)(up)
        except Exception as e:  # noqa: BLE001
            attempts[-1]["upload_deleted"] = f"failed: {type(e).__name__}: {e}"
    write_json(run_dir / "identity_pass_debug.json", {"attempts": attempts, "system": sections["system"], "model": spec.to_dict()})
    r, rate = cfg["rates"], inr_per_usd(cfg)
    cached = min(usage["cached_tokens"], usage["input_tokens"])
    cost = round(((usage["input_tokens"] - cached) * r["usd_per_1m_input"] + cached * r["usd_per_1m_cached_input"]
                  + (usage["output_tokens"] + usage["thought_tokens"]) * r["usd_per_1m_output"]) / 1e6 * rate, 3)
    if ans is None:
        append_log(cfg, {"episode": inputs.vid, "stage": "identity_pass", "status": "failed", "duration_s": round(time.perf_counter() - t0, 1),
                         "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"] + usage["thought_tokens"], "cost_inr_est": cost,
                         "note": "validation failed: " + " | ".join(errors[:5])})
        raise IdentityPassError("the identity pass failed validation after the retry: " + " | ".join(errors[:6]))
    record = {"version": VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": spec.to_dict(),
              "people": ans["people"], "lines": lines, "warnings": list(warns), "usage": {**usage, "cost_inr": cost},
              "n_attempts": len(attempts)}
    append_log(cfg, {"episode": inputs.vid, "stage": "identity_pass", "status": "ok", "duration_s": round(time.perf_counter() - t0, 1),
                     "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"] + usage["thought_tokens"],
                     "cached_tokens": usage["cached_tokens"], "cost_inr_est": cost, "note": f"{len(lines)} lines answered; " + "; ".join(warns)[:200]})
    out = finalize(facts, record, base)
    if naming_check_on(cfg):
        # no code rule decides who is who: the naming check (one text call) reads the transcript with this answer and
        # decides. If it fails, the paid video answer is kept and the check can be rerun alone (naming_check.run).
        try:
            out = NC.check(inputs.vid, facts, record, cfg, run_dir, log=log, client=client)
            out.pop("speakers_from_video", None)
        except Exception as e:  # noqa: BLE001
            record["warnings"].append(f"naming check failed ({type(e).__name__}: {e}); the video step's answer is used; rerun the naming check alone")
    out["identity_pass"] = record
    return out
