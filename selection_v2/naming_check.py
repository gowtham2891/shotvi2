"""Naming check (founder, 2026-11-01; per-line video answer 2026-11-02): after the video step (identity pass), ONE text
call reads the whole transcript with the text step's person per line and the video step's per-line answer (who talks,
who is on screen, voice changes inside a line), and decides who says every spoken line (``prompts/naming_check.md``,
``schemas/naming_check.schema.json``). Every line where the two steps disagree must be answered (changed, split, kept
or not sure). Code only checks ids / keys / quoted words and writes the answer into the speaker map:

  derived.line_speakers   line -> the main person (the part with the most words; the first key of a group)
  derived.line_voices     line -> [{word, keys}] for lines with two voices or a group (word = token position)
  derived.on_screen       line -> the people the video step saw, under the final keys
  episode_pass.speakers / derived.speakers_table carry the final names; facts["naming_check"] the answer and summary.
"""
from __future__ import annotations

import copy
import json
import re
import time
import unicodedata
from pathlib import Path

from shotvi.llm.base import ModelSpec
from shotvi.llm.pricing import cost_usd

from . import stage1_episode_pass as s1b
from .config import ROOT, inr_per_usd
from .paths import write_json
from .runlog import append_log
from .stage1_facts import _PUNCT, fmt_ts

VERSION = "naming_check/0.2"
ROLE = "naming_check"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "naming_check.md"
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "naming_check.schema.json"
NEW_KEY = re.compile(r"^N\d+$")


class NamingCheckError(RuntimeError):
    pass


def base_of(facts: dict) -> dict:
    """The text step's names before any naming check: {speakers, speakers_table, line_speakers}. A rerun starts from the
    stored base, never from an earlier check's output."""
    for k in ("naming_check", "speakers_from_video"):
        if (facts.get(k) or {}).get("base"):
            return copy.deepcopy(facts[k]["base"])
    ep = facts["episode_pass"]
    d = ep["derived"]
    return {"speakers": copy.deepcopy(ep["speakers"]), "speakers_table": copy.deepcopy(d["speakers_table"]),
            "line_speakers": dict(d["line_speakers_text"] if "line_speakers_text" in d else d.get("line_speakers") or {})}   # by presence: {} is valid


def speaker_summary(facts: dict) -> dict:
    """The summary of whichever step wrote the final speaker map: the naming check when it ran, else the video step's
    own answer (founder, 2026-11-02: no naming check). Holds cast (names, looks), alone_on_screen, moved."""
    return facts.get("naming_check") or facts.get("speakers_from_video") or {}


def _names(facts: dict, base: dict) -> dict[str, str]:
    names = {s["key"]: s.get("name") for s in base["speakers"]}
    for c in facts["episode"].get("speakers") or []:          # fix keys the text step did not list keep the fix name
        names.setdefault(c["key"], c.get("name"))
    return names


def _key_of(r: dict, base: dict) -> str | None:
    return base["line_speakers"].get(r["id"]) or r.get("speaker_key")


def _speech(facts: dict) -> list[dict]:
    return [r for r in facts["lines"] if r["kind"] == "speech"]


def _tok(s: str) -> str:
    return unicodedata.normalize("NFC", s).strip(_PUNCT).lower()


def turn_index(text: str, at: str, after: int) -> int | None:
    """Word position (whitespace tokens of the line text) where the quoted word starts, searching after ``after``."""
    want = _tok((at or "").split()[0]) if (at or "").split() else ""
    if not want:
        return None
    toks = [_tok(t) for t in text.split()]
    for i in range(after + 1, len(toks)):
        if toks[i] == want:
            return i
    return None


# ----------------------------------------------------------------------------------------------- the video answer
def video_merge(identity: dict) -> dict[str, str]:
    """The video step's own same_as: key -> the key it says is the same person."""
    return {p["key"]: p["same_as"] for p in identity.get("people") or [] if p.get("same_as")}


def video_keys_used(identity: dict) -> set[str]:
    out = {p.get("key") for p in identity.get("people") or []}
    for e in (identity.get("lines") or {}).values():
        out |= set(e.get("talk") or []) | set(e.get("see") or [])
        for t in e.get("turns") or []:
            out |= set(t.get("talk") or [])
    return out - {None}


def disagreements(facts: dict, base: dict, identity: dict) -> set[str]:
    """Lines where the video step heard someone other than the text step's person, or a voice change inside the line
    (a format comparison of two answers, no judgement; "could not tell" is not a disagreement)."""
    vm = video_merge(identity)
    out = set()
    for r in _speech(facts):
        e = (identity.get("lines") or {}).get(r["id"])
        if not e:
            continue
        talk = {vm.get(k, k) for k in e.get("talk") or []}
        if e.get("turns") or (talk and talk != {_key_of(r, base)}):
            out.add(r["id"])
    return out


# ----------------------------------------------------------------------------------------------- prompt parts
def people_block(facts: dict, base: dict) -> str:
    names = _names(facts, base)
    roles = {s["key"]: s.get("role") or "" for s in base["speakers"]}
    used = {_key_of(r, base) for r in _speech(facts)} - {None}
    out = ["# PART A: PEOPLE THE TEXT STEP FOUND (key = name: role)"]
    for k in sorted(used):
        out.append(f"{k} = {names.get(k) or '?'}: {roles.get(k, '')}")
    return "\n".join(out)


def video_people_block(identity: dict) -> str:
    out = ["# PART B: PEOPLE THE VIDEO STEP FOUND (key = name: role; gender; what it saw; look)"]
    for p in identity.get("people") or []:
        out.append(f"{p.get('key')} = {p.get('name')}: {p.get('role') or ''}; {p.get('gender')}; saw: {p.get('seen') or ''}; look: {p.get('look') or ''}"
                   + (f"; same_as {p['same_as']}" if p.get("same_as") else ""))
    return "\n".join(out)


def _video_cell(e: dict | None) -> str:
    if not e:
        return "video: - | on screen: -"
    talk = ", ".join(e.get("talk") or []) or "?"
    for t in e.get("turns") or []:
        talk += f", then from '{t.get('at')}': " + (", ".join(t.get("talk") or []) or "?")
    return f"video: {talk} | on screen: {', '.join(e.get('see') or []) or 'nobody'}"


def transcript_block(facts: dict, base: dict, identity: dict) -> str:
    scenes = facts["episode_pass"]["derived"]["scenes"]
    diff = disagreements(facts, base, identity)
    vl = identity.get("lines") or {}
    out = ["# PART C: TRANSCRIPT BY SCENE (id [time] voice label | text: person | video: who talks | on screen: who is seen | words; ≠ = the steps disagree)"]
    si = 0
    for r in facts["lines"]:
        while si < len(scenes) and r["start"] >= scenes[si]["start"] - 1e-6:
            out.append(f"\n== Scene {scenes[si]['id']} ({scenes[si]['start_str']}-{scenes[si]['end_str']})")
            si += 1
        if r["kind"] == "speech":
            mark = " ≠" if r["id"] in diff else ""
            out.append(f"{r['id']}{mark} [{fmt_ts(r['start'])}] {r.get('speaker') or '?'} | text: {_key_of(r, base) or '?'} | {_video_cell(vl.get(r['id']))} | {r['text']}")
        else:
            out.append(f"{r['id']} [{fmt_ts(r['start'])}] (sound) {r.get('text') or r.get('tag') or ''}")
    return "\n".join(out)


def build_prompt_parts(facts: dict, base: dict, identity: dict) -> tuple[str, dict]:
    """(context, prompt sections): the three parts, then the prompt's instructions."""
    sections = s1b.load_prompt(PROMPT_PATH)
    context = "\n\n".join([people_block(facts, base), video_people_block(identity), transcript_block(facts, base, identity)])
    return context, sections


# ----------------------------------------------------------------------------------------------- checks
def _owners(ans: dict) -> dict[str, list[str]]:
    own: dict[str, list[str]] = {}
    for p in ans.get("people") or []:
        for vk in p.get("video_keys") or []:
            own.setdefault(vk, []).append(p.get("key"))
    return own


def validate(ans: dict, facts: dict, base: dict, identity: dict) -> list[str]:
    """Format checks only (ids, keys and quoted words exist; each line changed once; every disagreement answered); no
    judgement about who is right."""
    errs = []
    by_id = {r["id"]: r for r in _speech(facts)}
    used = {_key_of(r, base) for r in by_id.values()} - {None}
    people = ans.get("people") or []
    keys = [p.get("key") for p in people]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        errs.append(f"people: key(s) {dup} listed more than once")
    missing = sorted(used - set(keys))
    if missing:
        errs.append(f"people: the text step's key(s) {missing} are missing; list every one (use same_as when it is another person's key)")
    by_key = {p.get("key"): p for p in people}
    vused = video_keys_used(identity)
    for p in people:
        k = p.get("key")
        if k not in used and not NEW_KEY.match(str(k)):
            errs.append(f"people {k!r}: a person the text step did not have gets a new key N1, N2, ...")
        if not (p.get("name") or "").strip():
            errs.append(f"people {k}: empty name")
        sa = p.get("same_as")
        if sa:
            if sa not in by_key or sa == k:
                errs.append(f"people {k}: same_as {sa!r} is not another listed person's key")
            elif by_key[sa].get("same_as"):
                errs.append(f"people {k}: same_as {sa!r} points to a key that is itself same_as another; point to the final key")
            if k not in used:
                errs.append(f"people {k}: same_as is only for the text step's keys")
            if p.get("video_keys"):
                errs.append(f"people {k}: a same_as key has no video_keys; list them under {sa}")
        bad = [v for v in p.get("video_keys") or [] if v not in vused]
        if bad:
            errs.append(f"people {k}: video_keys {bad} are not keys the video step used (copy them from PART B)")
    own = _owners(ans)
    twice = sorted(v for v, ks in own.items() if len(ks) > 1)
    if twice:
        errs.append(f"video_keys {twice} are listed under more than one person")
    orphan = sorted(v for v in vused if v not in own and v not in by_key)
    if orphan:
        errs.append(f"the video step's key(s) {orphan} belong to no person; put each in one person's video_keys")
    final = {k for k, p in by_key.items() if not p.get("same_as")}
    addressed: set[str] = set()
    seen: dict[str, int] = {}
    for i, c in enumerate(ans.get("changes") or []):
        to = c.get("to") or []
        if not to or any(k not in final for k in to):
            errs.append(f"changes[{i}]: to {to} must be listed people's keys (not same_as keys)")
        bad = [x for x in c.get("lines") or [] if x not in by_id]
        if bad:
            errs.append(f"changes[{i}]: {bad[:6]} are not spoken line ids")
        for x in c.get("lines") or []:
            if x in seen and seen[x] != i:
                errs.append(f"changes: line {x} is in changes[{seen[x]}] and changes[{i}]; give each line one change")
            seen.setdefault(x, i)
            addressed.add(x)
    for i, s in enumerate(ans.get("splits") or []):
        lid = s.get("line")
        if lid not in by_id:
            errs.append(f"splits[{i}]: {lid!r} is not a spoken line id"); continue
        if turn_index(by_id[lid]["text"], s.get("at") or "", 0) is None:
            errs.append(f"splits[{i}]: the word {s.get('at')!r} is not in line {lid} after its first word (copy it exactly)")
        to = s.get("to") or []
        if not to or any(k not in final for k in to):
            errs.append(f"splits[{i}]: to {to} must be listed people's keys (not same_as keys)")
        addressed.add(lid)
    for part in ("keep_text", "not_sure"):
        for i, n in enumerate(ans.get(part) or []):
            bad = [x for x in n.get("lines") or [] if x not in by_id]
            if bad:
                errs.append(f"{part}[{i}]: {bad[:6]} are not spoken line ids")
            addressed |= set(n.get("lines") or [])
            if part == "not_sure":
                badk = [k for k in n.get("options") or [] if k not in by_key]
                if badk:
                    errs.append(f"not_sure[{i}]: options {badk} are not listed people's keys")
    # a disagreement settled by a same_as (the text step's key merged into the person the video step heard) needs no line
    vm = video_merge(identity)
    resolve = lambda k: (by_key.get(k) or {}).get("same_as") or k
    owner = {v: ks[0] for v, ks in own.items()}
    open_ = []
    for lid in sorted(disagreements(facts, base, identity) - addressed):
        e = identity["lines"][lid]
        heard = {owner.get(vm.get(k, k), resolve(vm.get(k, k))) for k in e.get("talk") or []}
        if e.get("turns") or heard != {resolve(_key_of(by_id[lid], base))}:
            open_.append(lid)
    if open_:
        errs.append(f"{len(open_)} line(s) marked ≠ are not answered (put each in changes, splits, keep_text or not_sure): {open_[:15]}")
    return errs


# ----------------------------------------------------------------------------------------------- apply
def apply(facts: dict, ans: dict, base: dict, identity: dict) -> tuple[dict, dict]:
    """Write the model's answer into the speaker map. Returns (new facts, summary). Pure bookkeeping."""
    f = copy.deepcopy(facts)
    ep = f["episode_pass"]; d = ep["derived"]
    by_key = {p["key"]: p for p in ans["people"]}
    resolve = lambda k: (by_key.get(k) or {}).get("same_as") or k
    old_speakers = {s["key"]: s for s in base["speakers"]}
    old_table = {s["key"]: s for s in base["speakers_table"]}
    old_names = _names(facts, base)
    by_id = {r["id"]: r for r in _speech(f)}
    parts: dict[str, list[dict]] = {lid: [{"word": 0, "keys": [resolve(_key_of(r, base))]}] for lid, r in by_id.items()}
    moved = []
    for c in ans.get("changes") or []:
        for lid in c["lines"]:
            if parts[lid][0]["keys"] != list(c["to"]):
                moved.append({"line": lid, "from": parts[lid][0]["keys"], "to": list(c["to"]), "why": c["why"]})
            parts[lid][0]["keys"] = list(c["to"])
    splits = sorted(((s["line"], turn_index(by_id[s["line"]]["text"], s["at"], 0), s) for s in ans.get("splits") or []),
                    key=lambda x: (x[0], x[1]))
    for lid, i, s in splits:
        if any(p["word"] == i for p in parts[lid]):
            continue
        parts[lid].append({"word": i, "keys": list(s["to"])})
        moved.append({"line": lid, "from": parts[lid][0]["keys"], "to": list(s["to"]), "from_word": s["at"], "why": s["why"]})
    line_speakers, line_voices = {}, {}
    for lid, ps in parts.items():
        n = len(by_id[lid]["text"].split())
        sizes = [((ps[j + 1]["word"] if j + 1 < len(ps) else n) - p["word"], -j) for j, p in enumerate(ps)]
        main = ps[-max(sizes)[1]]
        line_speakers[lid] = main["keys"][0]
        if len(ps) > 1 or len(ps[0]["keys"]) > 1:
            line_voices[lid] = ps
    # who is on screen, under the final keys: a video key belongs to the person whose video_keys list it, else it is
    # the text step's key of the same name (resolved through same_as); a key with neither is left out
    vm = video_merge(identity)
    owner = {v: ks[0] for v, ks in _owners(ans).items()}
    to_final = lambda k: owner.get(vm.get(k, k)) or (resolve(vm.get(k, k)) if vm.get(k, k) in by_key else None)
    on_screen = {}
    for lid, e in (identity.get("lines") or {}).items():
        if lid in by_id:
            on_screen[lid] = list(dict.fromkeys(x for x in (to_final(k) for k in e.get("see") or []) if x))
    speakers, table = [], []
    n_lines = {k: sum(1 for v in line_speakers.values() if v == k) for k in by_key}
    for p in ans["people"]:
        k = p["key"]
        prev = old_speakers.get(k) or {}
        prior = old_names.get(k)
        if p.get("same_as"):
            status = "merged"
        elif k not in old_names:
            status = "new"
        else:
            status = "confirmed" if (prior or "").strip() == p["name"].strip() else "renamed"
        sp = {**prev, "key": k, "name": p["name"], "role": p.get("role") or prev.get("role") or "", "status": status,
              "evidence": prev.get("evidence") or f"naming check ({VERSION})"}
        if p.get("same_as"):
            sp["merged_into"] = p["same_as"]
        speakers.append(sp)
        row = {**old_table.get(k, {}), "key": k, "prior_name": prior, "name": p["name"], "role": sp["role"], "status": status,
               "changed": status != "confirmed", "n_lines": n_lines.get(k, 0)}
        if p.get("same_as"):
            row["merged_into"] = p["same_as"]
        table.append(row)
    ep["speakers"] = speakers
    d["speakers_table"] = table
    d["line_speakers_text"] = dict(base["line_speakers"])     # the text step's map, kept for the accuracy view
    d["line_speakers"] = line_speakers
    d["line_voices"] = line_voices
    d["on_screen"] = on_screen
    # lines where the video step saw exactly one person: where that person's face can be taken from (the cast sheet)
    alone: dict[str, list[str]] = {}
    for lid, ks in on_screen.items():
        if len(ks) == 1:
            alone.setdefault(ks[0], []).append(lid)
    looks = {}
    for vp in identity.get("people") or []:
        k = to_final(vp.get("key"))
        if k and vp.get("look") and k not in looks:
            looks[k] = vp["look"]
    cast = [{"key": p["key"], "name": p["name"], "role": p.get("role") or "", "gender": p.get("gender"), "aka": p.get("aka") or [],
             "main": bool(p.get("main")), "n_lines": n_lines.get(p["key"], 0), "video_keys": p.get("video_keys") or [],
             "look": looks.get(p["key"], "")} for p in ans["people"] if not p.get("same_as")]
    summary = {"n_lines_moved": len(moved), "moved": moved, "alone_on_screen": alone, "cast": cast,
               "keep_text": ans.get("keep_text") or [], "not_sure": ans.get("not_sure") or [],
               "n_disagreements": len(disagreements(facts, base, identity)),
               "status": {p["key"]: s["status"] for p, s in zip(ans["people"], speakers)}}
    return f, summary


# ----------------------------------------------------------------------------------------------- call
def _inr(spec: ModelSpec, usage: dict, cfg: dict) -> float:
    """The call's cost at the model's own price (shotvi.llm.pricing), else at the config's Gemini rates."""
    out = usage["output_tokens"] + usage["thought_tokens"]
    usd = cost_usd(spec.model, usage["input_tokens"], out, cached_tokens=usage["cached_tokens"])
    if usd is None:
        r = cfg["rates"]
        cached = min(usage["cached_tokens"], usage["input_tokens"])
        usd = ((usage["input_tokens"] - cached) * r["usd_per_1m_input"] + cached * r["usd_per_1m_cached_input"] + out * r["usd_per_1m_output"]) / 1e6
    return round(usd * inr_per_usd(cfg), 3)


def estimate(facts: dict, cfg: dict, identity: dict | None = None, counter=None) -> dict:
    nc = cfg["naming_check"]
    spec = ModelSpec.from_dict(nc["model"])
    identity = identity or facts.get("identity_pass") or {}
    context, sections = build_prompt_parts(facts, base_of(facts), identity)
    text = sections["system"] + context + sections["instructions"] + sections["output_format"]
    n_in = int(counter(text)) if counter else len(text) // 3
    out = int(nc.get("expected_output_tokens", 8000))
    inr = _inr(spec, {"input_tokens": n_in, "output_tokens": out, "thought_tokens": 0, "cached_tokens": 0}, cfg)
    return {"input_tokens": n_in, "expected_output_tokens": out, "inr_expected": inr, "model": spec.model, "exact_counter": bool(counter)}


def check(vid: str, facts: dict, identity: dict, cfg: dict, run_dir: Path, client=None, log=None) -> dict:
    """One naming-check call (with one retry on validation errors) on top of the text step's names; returns new facts."""
    t0 = time.perf_counter()
    log = log or (lambda *a: None)
    nc = cfg["naming_check"]
    spec = ModelSpec.from_dict(nc["model"])
    base = base_of(facts)
    context, sections = build_prompt_parts(facts, base, identity)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if client is None:
        from shotvi.llm.client import Ledger, LLMClient
        client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"), max_retries=int(nc.get("http_retries", 1)))
    attempts, errors, ans, instr = [], [], None, ""
    usage = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0}
    for attempt in range(int(nc.get("retries", 1)) + 1):
        instr = (context + "\n\n" + sections["instructions"]
                 + ("\n\n" + sections["retry"].replace("{errors}", "\n".join(f"- {e}" for e in errors)) + f"\n(attempt {attempt + 1})" if errors else "")
                 + "\n\n" + sections["output_format"])
        att = {"n": attempt + 1, "errors_shown": errors}
        attempts.append(att)
        log(f"naming check: call {attempt + 1} ({spec.provider} {spec.model})")
        try:
            obj, raw = client.generate_json(ROLE, spec, sections["system"], instr, schema, meta={"video_id": vid, "stage": "naming_check"}, want_raw=True)
        except BaseException as e:
            att["error"] = f"{type(e).__name__}: {e}"
            write_json(Path(run_dir) / "naming_check_debug.json", {"attempts": attempts, "system": sections["system"], "prompt": instr})
            append_log(cfg, {"episode": vid, "stage": "naming_check", "status": "failed", "duration_s": round(time.perf_counter() - t0, 1), "note": att["error"][:300]})
            raise
        att["raw_text"] = raw.text
        att["usage"] = {k: int(getattr(raw, k) or 0) for k in usage}
        for k in usage:
            usage[k] += att["usage"][k]
        errors = validate(obj, facts, base, identity)
        att["validation"] = errors
        if not errors:
            ans = obj; break
    write_json(Path(run_dir) / "naming_check_debug.json", {"attempts": attempts, "system": sections["system"], "prompt_last": instr, "model": spec.to_dict()})
    cost = _inr(spec, usage, cfg)
    if ans is None:
        append_log(cfg, {"episode": vid, "stage": "naming_check", "status": "failed", "duration_s": round(time.perf_counter() - t0, 1),
                         "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"] + usage["thought_tokens"],
                         "cost_inr_est": cost, "note": "validation failed: " + " | ".join(errors[:5])})
        raise NamingCheckError("the naming check failed validation after the retry: " + " | ".join(errors[:6]))
    out, summary = apply(facts, ans, base, identity)
    out["naming_check"] = {"version": VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": spec.to_dict(),
                           "base": base, "answer": ans, **summary, "usage": {**usage, "cost_inr": cost}, "n_attempts": len(attempts)}
    append_log(cfg, {"episode": vid, "stage": "naming_check", "status": "ok", "duration_s": round(time.perf_counter() - t0, 1),
                     "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"] + usage["thought_tokens"],
                     "cost_inr_est": cost, "note": f"{summary['n_lines_moved']} line changes, {summary['n_disagreements']} disagreements, "
                                                   f"{len(summary['not_sure'])} not-sure groups"})
    return out


def run(inputs, facts: dict | None, cfg: dict, client=None, log=None) -> dict:
    """Rerun the naming check alone from the stored identity-pass answer (no video call)."""
    idp = (facts or {}).get("identity_pass") or {}
    if not idp.get("lines"):
        raise RuntimeError("the naming check needs the identity pass's per-line answer first (identity_pass/0.2)")
    return check(inputs.vid, facts, idp, cfg, Path(inputs.paths.run_dir), client=client, log=log)
