"""Stage 1B, the episode pass (spec 4.2): ONE Gemini call per episode through ``shotvi.llm``. Input = the cached
prefix (full line table + Stage 1A facts + the series glossary prior) and the six questions. Output = strict JSON
validated against ``schemas/episode_pass.schema.json`` and then by code:

  Tier 1  speakers (every key has an entry; new keys for merged strangers), scenes (as first / last LINE IDS, so every
          edge is a gap-table pause by construction; ordered, non-overlapping, covering every dialogue line except
          non-story ones), suspect_resolutions (real line ids)
  Tier 2  non_story (real line ranges), content_type, glossary (bounded)

A tier that fails validation is re-asked ALONE with the validator's errors, at most ``max_retries_per_tier`` times;
every attempt is logged (tokens, rupees, errors) in ``runs/selection_v2/<id>/stage1b_debug.json`` with the exact
instructions and the raw answers; the prefix bytes are in ``prefix.txt``. Code then DERIVES (never decides):
scene times, which proposals were confirmed / merged / added, which lines changed speaker, and counts.

Refuses to run when Stage 1A is missing or older than the current ``stage1_facts.VERSION``.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from shotvi.llm.base import ModelSpec

from . import stage1_facts
from .config import ROOT, inr_per_usd
from .glossary import load_glossary, merge_glossary, save_glossary
from .paths import write_json
from .prefix import anchor_checks, build_prefix
from .stage1_facts import fmt_ts, parse_ts

VERSION = "stage1b/0.3"
COMPATIBLE_VERSIONS = {"stage1b/0.2", VERSION}      # text-pass outputs Stage 2 accepts (0.3 only added the raw/video experiment)
STAGE_KEY = "1b"
ROLE = "episode_pass"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "stage1b_episode_pass.md"
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "episode_pass.schema.json"
TIER0 = ("cast", "same_person", "split_labels", "reference_times")
TIER1 = ("speakers", "anchor_checks", "scenes", "suspect_resolutions", "threads")   # threads: founder, 2026-10-29
TIER2 = ("possible_non_story", "content_type", "glossary")
LINE_RE = re.compile(r"L\d{4,}")
LEFTOVER_SHARE = 0.2     # raw mode: a label needs its own speakers entry only when > this share of its lines was not split away


class EpisodePassError(RuntimeError):
    """The answer never passed validation (the attempts are in the debug file)."""


# ----------------------------------------------------------------------------------------------- prompt / schema
def load_prompt(path: Path = PROMPT_PATH) -> dict[str, str]:
    sections, cur = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            sections[cur] = []
        elif cur is not None:
            sections[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def tier_schema(schema: dict, keys: tuple[str, ...]) -> dict:
    return {**schema, "required": [k for k in schema["required"] if k in keys],
            "properties": {k: v for k, v in schema["properties"].items() if k in keys}}


def make_instructions(sections: dict, tier: str, cfg: dict, errors: list[str] | None = None) -> str:
    gmax = int(cfg["stage1b"]["glossary_max_entries"])
    parts = ["# PART 2: INSTRUCTIONS AND QUESTIONS", sections["instructions"]]
    keys: tuple[str, ...]
    raw = (cfg.get("stage1b") or {}).get("prior", "fix") == "raw"
    t1 = (TIER0 + TIER1) if raw else TIER1
    if tier == "all":
        parts += [sections["tier1"], sections["tier2"].replace("{glossary_max}", str(gmax))]
        keys = t1 + TIER2
    elif tier == "1":
        parts += [sections["tier1"]]
        keys = t1
    else:
        parts += [sections["tier2"].replace("{glossary_max}", str(gmax))]
        keys = TIER2
    if errors:
        parts.append(sections["retry"].replace("{errors}", "\n".join(f"- {e}" for e in errors)))
    parts.append(sections["output_format"].replace("{keys}", ", ".join(keys)))
    return "\n\n".join(parts)


# ----------------------------------------------------------------------------------------------- line helpers
def dialogue_ids(facts: dict) -> list[str]:
    return [r["id"] for r in facts["lines"] if r["kind"] == "speech" and not r["zero_len"]]


def expand_lines(spec: str, all_ids: list[str]) -> tuple[list[str], str | None]:
    """'L<a>-L<b>, L<c>' -> ids (in table order); (ids, error) with error naming an unknown id or bad range."""
    pos = {x: i for i, x in enumerate(all_ids)}
    out: list[str] = []
    for part in re.split(r"[,;]\s*", str(spec).strip()):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(L\d{4,})\s*[-–]\s*(L\d{4,})", part)
        if m:
            a, b = m.group(1), m.group(2)
            if a not in pos or b not in pos:
                return out, f"unknown line id in range {part!r}"
            if pos[a] > pos[b]:
                return out, f"range {part!r} runs backwards"
            out += all_ids[pos[a]:pos[b] + 1]
        elif re.fullmatch(r"L\d{4,}", part):
            if part not in pos:
                return out, f"unknown line id {part!r}"
            out.append(part)
        else:
            return out, f"cannot read line spec {part!r} (use one line id or a range of two ids: L<nnnn> or L<nnnn>-L<nnnn>)"
    return out, None


# ----------------------------------------------------------------------------------------------- raw-label helpers
def raw_labels(facts: dict) -> list[str]:
    return sorted({r["speaker"] for r in facts["lines"] if r["kind"] == "speech" and r.get("speaker")}, key=lambda x: (len(x), x))


def _loose(s) -> str:
    return re.sub(r"[^a-z0-9\u0c00-\u0c7f]", "", str(s or "").lower().replace("sh", "s").replace("y", "i"))


def final_key_map(ans: dict, facts: dict) -> tuple[dict[str, str], list[str]]:
    """raw label -> final person key after same_person merges (the group's label that has a speakers entry, else its
    first label). Returns (map, errors)."""
    errs = []
    labels = raw_labels(facts)
    have = {str(s.get("key")) for s in ans.get("speakers") or []}
    fk = {lab: lab for lab in labels}
    for g in ans.get("same_person") or []:
        labs = [str(x) for x in g.get("labels") or []]
        bad = [x for x in labs if x not in labels]
        if bad:
            errs.append(f"same_person: unknown raw label(s) {bad}")
            continue
        if len(labs) < 2:
            continue                                   # a one-label "group" says nothing; ignored, not an error
        canon = next((x for x in labs if x in have), labs[0])
        for x in labs:
            fk[x] = canon
    return fk, errs


def validate_tier0(ans: dict, facts: dict, duration: float) -> list[str]:
    errs: list[str] = []
    labels = raw_labels(facts)
    all_ids = [r["id"] for r in facts["lines"]]
    cast = {str(c.get("label")): c for c in ans.get("cast") or []}
    missing = [x for x in labels if x not in cast]
    if missing:
        errs.append(f"cast: no entry for raw label(s) {missing} (every raw label in the line table needs one; 'unresolved' is allowed)")
    for lab, c in cast.items():
        if lab not in labels:
            errs.append(f"cast: {lab!r} is not a raw label of this episode")
        if not str(c.get("name") or "").strip():
            errs.append(f"cast {lab}: empty name")
    _, e2 = final_key_map(ans, facts)
    errs += e2
    speech = {r["id"] for r in facts["lines"] if r["kind"] == "speech"}
    for sp in ans.get("split_labels") or []:
        lab = str(sp.get("label"))
        if lab not in labels:
            errs.append(f"split_labels: unknown raw label {lab!r}")
            continue
        for person in sp.get("people") or []:
            ids, e = expand_lines(person.get("lines", ""), all_ids)
            if e:
                errs.append(f"split_labels {lab} / {person.get('name')}: {e}")
            elif any(i not in speech for i in ids):
                errs.append(f"split_labels {lab} / {person.get('name')}: {person.get('lines')} includes sound-tag lines")
            # lines of OTHER raw labels inside a person's list are accepted: the model re-labelled by face, which is the point
    for rt in ans.get("reference_times") or []:
        for t in rt.get("times") or []:
            try:
                tv = parse_ts(t)[0]
            except Exception:
                errs.append(f"reference_times {rt.get('name')}: cannot read time {t!r} (use m:ss.s)")
                continue
            if tv < 0 or tv > duration + 1:
                errs.append(f"reference_times {rt.get('name')}: {t} is outside the episode")
    return errs


# ----------------------------------------------------------------------------------------------- validation
def repair_scene_edges(ans: dict, facts: dict) -> list[str]:
    """Snap a scene first_line / last_line that is a sound tag or zero-length line to the nearest dialogue line inside
    the scene (first: forward; last: backward). Returns the repairs made (shown to the founder), in place."""
    all_ids = [r["id"] for r in facts["lines"]]
    dlg = dialogue_ids(facts)
    dset = set(dlg)
    pos = {x: i for i, x in enumerate(all_ids)}
    notes = []
    for sc in ans.get("scenes") or []:
        for what, step in (("first_line", 1), ("last_line", -1)):
            lid = str(sc.get(what) or "")
            if lid in dset or lid not in pos:
                continue
            i = pos[lid]
            while 0 <= i < len(all_ids) and all_ids[i] not in dset:
                i += step
            if 0 <= i < len(all_ids):
                notes.append(f"scene {sc.get('id')}: {what} {lid} is not a dialogue line; snapped to {all_ids[i]}")
                sc[what] = all_ids[i]
    return notes


GAP_JOIN_MAX_LINES = 2


def repair_scene_gaps(ans: dict, facts: dict, max_lines: int = GAP_JOIN_MAX_LINES) -> list[str]:
    """2026-10-30: at most ``max_lines`` dialogue lines left BETWEEN two consecutive scenes (after one scene's last line,
    before the next one's first line) join the earlier scene (its last_line moves forward). A larger gap is left for
    the validator. Returns the repairs made, in place."""
    dlg = dialogue_ids(facts)
    pos = {x: i for i, x in enumerate(dlg)}
    notes = []
    scs = [sc for sc in ans.get("scenes") or [] if sc.get("first_line") in pos and sc.get("last_line") in pos]
    scs.sort(key=lambda sc: pos[sc["first_line"]])
    for a, b in zip(scs, scs[1:]):
        gap = dlg[pos[a["last_line"]] + 1:pos[b["first_line"]]]
        if 0 < len(gap) <= max_lines:
            notes.append(f"scene {a.get('id')}: dialogue line(s) {', '.join(gap)} between it and scene {b.get('id')} joined to it")
            a["last_line"] = gap[-1]
    return notes


def validate_tier1(ans: dict, facts: dict, non_story: list[dict] | None = None, max_share: float = 0.5, raw: bool = False) -> list[str]:
    errs: list[str] = []
    all_ids = [r["id"] for r in facts["lines"]]
    dlg = dialogue_ids(facts)
    dpos = {x: i for i, x in enumerate(dlg)}
    raw_label_keys: set = set()
    if raw:
        fk, _ = final_key_map(ans, facts)
        split_away = set()
        for sp in ans.get("split_labels") or []:
            for person in sp.get("people") or []:
                ids, e = expand_lines(person.get("lines", ""), all_ids)
                if not e:
                    split_away |= set(ids)
        total, left = {}, {}
        for r in facts["lines"]:
            if r["kind"] == "speech" and r.get("speaker") and not r.get("zero_len"):
                k = fk.get(r["speaker"], r["speaker"])
                total[k] = total.get(k, 0) + 1
                if r["id"] not in split_away:
                    left[k] = left.get(k, 0) + 1
        # a label mostly split into named people needs no entry of its own (its few stray lines are filled by code)
        keys_in_table = sorted(k for k, n in total.items() if left.get(k, 0) / n > float(LEFTOVER_SHARE))
        raw_label_keys = set(fk) | set(fk.values())
    else:
        keys_in_table = sorted({r["speaker_key"] for r in facts["lines"] if r["kind"] == "speech" and r.get("speaker_key")})
    speakers = ans.get("speakers") or []
    seen = {}
    for s in speakers:
        k = str(s.get("key") or "")
        if not k:
            errs.append("a speakers entry has an empty key")
            continue
        if k in seen:
            errs.append(f"speaker key {k} listed twice")
        seen[k] = s
        if not str(s.get("name") or "").strip():
            errs.append(f"speaker {k} has an empty name (use 'unresolved' if unknown)")
        if s.get("status") == "new":
            assigned_by_anchor = any(a.get("final_key") == k for a in ans.get("anchor_checks") or [])
            split_out = raw and any(_loose(person.get("name")) == _loose(s.get("name"))
                                    for sp in ans.get("split_labels") or [] for person in sp.get("people") or [])
            if not s.get("lines") and not assigned_by_anchor and not split_out:
                errs.append(f"new speaker {k} must list the line ranges they speak (lines)" + (
                    ", or be a person named in split_labels" if raw else ", or be the final_key of an anchor check"))
            elif s.get("lines"):
                _, e = expand_lines(s["lines"], all_ids)
                if e:
                    errs.append(f"new speaker {k}: {e}")
        elif (k not in keys_in_table) and not (raw and k in raw_label_keys):
            errs.append(f"speaker key {k} is not a raw label of this episode and not 'new'; "
                        f"a person split out of a label needs status 'new'" if raw else
                        f"speaker key {k} is not in the line table; a person the fix merged into someone else needs status 'new'")
    missing = [k for k in keys_in_table if k not in seen]
    if missing:
        errs.append(f"no speakers entry for key(s) {missing} (every final person needs one; status may be 'unresolved')")
    known_keys = set(seen) | set(keys_in_table)
    if raw:
        for sp in ans.get("split_labels") or []:
            for person in sp.get("people") or []:
                nm = _loose(person.get("name"))
                if not any(s.get("status") == "new" and _loose(s.get("name")) == nm for s in ans.get("speakers") or []) \
                        and not any(_loose(s.get("name")) == nm for s in ans.get("speakers") or []):
                    errs.append(f"split_labels {sp.get('label')}: person {person.get('name')!r} needs a speakers entry (status new, same name)")
    expected = {} if raw else {q["check_id"]: q for q in anchor_checks(facts, max_share)}
    got = {}
    for a in ans.get("anchor_checks") or []:
        cid = str(a.get("check_id") or "")
        if cid not in expected:
            errs.append(f"anchor_checks: unknown check_id {cid!r} (expected {sorted(expected)})")
            continue
        got[cid] = a
        fk = a.get("final_key")
        if fk and fk not in known_keys:
            errs.append(f"anchor check {cid}: final_key {fk!r} is not a known key and has no speakers entry (add one with status new)")
    missing_q = [c for c in expected if c not in got]
    if missing_q:
        errs.append(f"anchor_checks missing for {missing_q}: every ANCHOR CHECK in PART 1 needs an answer")
    scenes = ans.get("scenes") or []
    if not scenes:
        errs.append("scenes is empty")
    ns_lines: set[str] = set()
    for n in non_story or []:
        ids, e = expand_lines(n.get("lines", ""), all_ids)
        if not e:
            ns_lines |= set(ids)
    covered: list[str] = []
    prev_last = None
    for i, sc in enumerate(scenes, 1):
        f, l = str(sc.get("first_line") or ""), str(sc.get("last_line") or "")
        for lid, what in ((f, "first_line"), (l, "last_line")):
            if lid not in dpos:
                errs.append(f"scene {sc.get('id', i)} {what} {lid!r} is not a dialogue line id"
                            + (" (it is an audio tag or zero-length line)" if lid in all_ids else ""))
        if f in dpos and l in dpos:
            if dpos[f] > dpos[l]:
                errs.append(f"scene {sc.get('id', i)}: first_line {f} comes after last_line {l}")
                continue
            if prev_last is not None and dpos[f] <= dpos[prev_last]:
                errs.append(f"scene {sc.get('id', i)} starts at {f}, which is not after the previous scene's last line {prev_last} "
                            f"(scenes must be in order and must not overlap)")
            covered += dlg[dpos[f]:dpos[l] + 1]
            prev_last = l
        for k in sc.get("speakers") or []:
            if k not in known_keys:
                errs.append(f"scene {sc.get('id', i)} lists unknown speaker key {k}")
    cov = set(covered)
    uncovered = [x for x in dlg if x not in cov and x not in ns_lines]
    if uncovered and not any("not a dialogue line" in e or "comes after" in e for e in errs):
        head = uncovered[:8]
        errs.append(f"{len(uncovered)} dialogue line(s) belong to no scene: {head}{'...' if len(uncovered) > 8 else ''} "
                    f"(scenes must cover every dialogue line from {dlg[0]} to {dlg[-1]}, except lines listed in non_story)")
    scene_first = {int(sc.get("id", -1)): str(sc.get("first_line") or "") for sc in ans.get("scenes") or []}
    threads = ans.get("threads") or []
    if len(threads) > 3:
        errs.append(f"threads: at most 3, got {len(threads)}")
    for i, th in enumerate(threads, 1):
        ids, e = expand_lines(str(th.get("setup_lines") or ""), all_ids)
        if e or not ids:
            errs.append(f"threads[{i}]: setup_lines {th.get('setup_lines')!r} is not a line range ({e or 'empty'})")
            continue
        ps = th.get("payoff_scene")
        if ps not in scene_first:
            errs.append(f"threads[{i}]: payoff_scene {ps!r} is not one of your scenes")
        elif scene_first[ps] in all_ids and all_ids.index(scene_first[ps]) <= all_ids.index(ids[-1]):
            errs.append(f"threads[{i}]: payoff scene {ps} must come after the setup {th.get('setup_lines')}")
    for r in ans.get("suspect_resolutions") or []:
        lid = str(r.get("line") or "")
        if lid not in all_ids:
            errs.append(f"suspect_resolutions: unknown line id {lid!r}")
            continue
        lk = r.get("likely_key")
        if lk and lk not in known_keys:
            errs.append(f"suspect_resolutions {lid}: likely_key {lk!r} is not a known or new speaker key")
    return errs


def validate_tier2(ans: dict, facts: dict, cfg: dict) -> list[str]:
    errs: list[str] = []
    all_ids = [r["id"] for r in facts["lines"]]
    for n in ans.get("possible_non_story") or []:
        _, e = expand_lines(n.get("lines", ""), all_ids)
        if e:
            errs.append(f"possible_non_story: {e}")
    g = ans.get("glossary") or {}
    n = sum(len(g.get(k) or []) for k in ("nicknames", "running_bits", "catchphrases", "relationships"))
    gmax = int(cfg["stage1b"]["glossary_max_entries"])
    if n > gmax:
        errs.append(f"glossary has {n} entries; keep at most {gmax} (nicknames + running bits + catchphrases + relationships)")
    if not str(g.get("premise") or "").strip():
        errs.append("glossary.premise is empty")
    return errs


# ----------------------------------------------------------------------------------------------- derivation (code)
def derive(ans: dict, facts: dict, min_scene_s: float = 0.0, max_share: float = 0.5, raw: bool = False) -> dict:
    lines = {r["id"]: r for r in facts["lines"]}
    all_ids = [r["id"] for r in facts["lines"]]
    dlg = dialogue_ids(facts)
    dpos = {x: i for i, x in enumerate(dlg)}
    prior = {c["key"]: c for c in facts["episode"].get("speakers") or []}
    final = {s["key"]: s for s in ans.get("speakers") or []}
    # lines that change speaker: new speakers' ranges, then explicit resolutions
    line_speakers: dict[str, str] = {}
    merges, splits, inferred = [], [], []
    if raw:
        # raw mode: EVERY speech line gets a final key: raw label -> merged key, then split-out people
        fk, _ = final_key_map(ans, facts)
        for g in ans.get("same_person") or []:
            labs = [str(x) for x in g.get("labels") or []]
            merges.append({"labels": labs, "key": fk.get(labs[0], labs[0]) if labs else None, "name": g.get("name"), "evidence": g.get("evidence")})
        for r in facts["lines"]:
            if r["kind"] == "speech" and r.get("speaker"):
                line_speakers[r["id"]] = fk.get(r["speaker"], r["speaker"])
        assigned: set = set()
        for sp in ans.get("split_labels") or []:
            for person in sp.get("people") or []:
                nm = _loose(person.get("name"))
                key = next((s["key"] for s in ans.get("speakers") or [] if _loose(s.get("name")) == nm), None)
                ids, e = expand_lines(person.get("lines", ""), all_ids)
                if key and not e:
                    for lid in ids:
                        if lines[lid]["kind"] == "speech":
                            line_speakers[lid] = key
                            assigned.add(lid)
                    splits.append({"label": sp.get("label"), "name": person.get("name"), "key": key, "n_lines": len(ids), "lines": person.get("lines"), "evidence": sp.get("evidence")})
        # stray lines of a label whose other lines were mostly re-labelled by face: take the person of the nearest
        # assigned line with the same raw label (recorded as inferred, never silent)
        inferred = []
        by_label: dict[str, list[str]] = {}
        for r in facts["lines"]:
            if r["kind"] == "speech" and r.get("speaker"):
                by_label.setdefault(r["speaker"], []).append(r["id"])
        have_entry = {s["key"] for s in ans.get("speakers") or []}
        for lab, ids in by_label.items():
            done = [i for i in ids if i in assigned]
            if not done or len(done) / len(ids) <= 1 - LEFTOVER_SHARE or fk.get(lab, lab) in have_entry:
                continue
            pos = {i: n for n, i in enumerate(ids)}
            for lid in ids:
                if lid in assigned:
                    continue
                near = min(done, key=lambda d: abs(pos[d] - pos[lid]))
                line_speakers[lid] = line_speakers[near]
                inferred.append({"line": lid, "label": lab, "key": line_speakers[near], "from": near})
    for s in ans.get("speakers") or []:
        if s.get("status") == "new" and s.get("lines"):
            ids, _ = expand_lines(s["lines"], all_ids)
            for lid in ids:
                if lines[lid]["kind"] == "speech":
                    line_speakers[lid] = s["key"]
    anchors = {q["check_id"]: q for q in anchor_checks(facts, max_share)}
    anchor_table = []
    for a in ans.get("anchor_checks") or []:
        q = anchors.get(str(a.get("check_id")))
        if q is None:
            continue
        fk = a.get("final_key") or q["key"]
        ids = list(q["line_ids"])
        if fk != q["key"]:
            for lid in ids:
                line_speakers[lid] = fk
        anchor_table.append({"check_id": q["check_id"], "key": q["key"], "range": q["range"], "n_lines": len(ids),
                             "who": a.get("who"), "consistent": a.get("consistent_with_episode"), "final_key": fk,
                             "changed": fk != q["key"], "evidence": a.get("evidence")})
    for r in ans.get("suspect_resolutions") or []:
        lk = r.get("likely_key")
        if lk and r.get("line") in lines and lk != lines[r["line"]].get("speaker_key"):
            line_speakers[r["line"]] = lk
    if not raw:
        line_speakers = {k: v for k, v in line_speakers.items() if v != lines[k].get("speaker_key")}
    # prior name per final key: the fix's majority name on that key's lines (raw mode) or the fix entry (fix mode)
    def prior_name_of(key: str):
        if not raw:
            p = prior.get(key)
            return p.get("name") if p else None
        cnt: dict[str, int] = {}
        for lid, k in line_speakers.items():
            if k == key:
                nm = lines[lid].get("speaker_name") or "unresolved"
                cnt[nm] = cnt.get(nm, 0) + 1
        return max(cnt.items(), key=lambda kv: kv[1])[0] if cnt else None
    speakers_table = []
    for s in ans.get("speakers") or []:
        pname = prior_name_of(s["key"])
        changed = s.get("status") in ("renamed", "new", "unresolved") or (pname is not None and (s.get("name") or "").strip().lower() != str(pname).strip().lower())
        n_final = sum(1 for lid in dlg if line_speakers.get(lid, lines[lid].get("speaker_key")) == s["key"])
        labs = sorted({lines[lid].get("speaker") for lid in dlg if line_speakers.get(lid, lines[lid].get("speaker_key")) == s["key"] and lines[lid].get("speaker")}) if raw else []
        speakers_table.append({"key": s["key"], "prior_name": pname, "evidence_against_prior": s.get("evidence_against_prior"),
                               "name": s.get("name"), "role": s.get("role"), "status": s.get("status"), "changed": bool(changed),
                               "n_lines": n_final, "raw_labels": labs, "evidence": s.get("evidence")})
    # scenes with times; proposal status
    scenes = []
    for sc in ans.get("scenes") or []:
        f, l = lines[sc["first_line"]], lines[sc["last_line"]]
        scenes.append({"id": sc.get("id"), "first_line": sc["first_line"], "last_line": sc["last_line"],
                       "start": f["start"], "end": l["end"], "start_str": fmt_ts(f["start"]), "end_str": fmt_ts(l["end"]),
                       "label": sc.get("label"), "register": sc.get("register"), "speakers": sc.get("speakers") or [],
                       "note": sc.get("note"), "proposals_confirmed": [], "proposals_merged": [], "origin": None})
    scenes.sort(key=lambda s: s["start"])
    # code post-processing: a scene shorter than min_scene_s merges into the previous scene
    merged_short = []
    min_s = float(min_scene_s or 0)
    if min_s > 0:
        kept = []
        for sc in scenes:
            if kept and (sc["end"] - sc["start"]) < min_s:
                prev = kept[-1]
                merged_short.append({"id": sc["id"], "start_str": sc["start_str"], "end_str": sc["end_str"], "label": sc["label"],
                                     "into": prev["id"]})
                prev["last_line"], prev["end"], prev["end_str"] = sc["last_line"], sc["end"], sc["end_str"]
                prev["speakers"] = sorted(set(prev["speakers"]) | set(sc["speakers"]))
                prev["note"] = ((prev.get("note") or "") + f" (+ merged short scene {sc['id']}: {sc['label']})").strip()
            else:
                kept.append(sc)
        scenes = kept
    edges = []   # (last_line_of_prev, first_line_of_next)
    for a, b in zip(scenes, scenes[1:]):
        edges.append((a["last_line"], b["first_line"], a, b))
    props = facts.get("boundary_proposals") or []
    status: dict[int, str] = {}
    for a_last, b_first, a, b in edges:
        hit = [p for p in props if p["prev"] == a_last and p["next"] == b_first]
        if hit:
            for p in hit:
                status[p["rank"]] = "confirmed"
                b["proposals_confirmed"].append(p["t_str"])
            b["origin"] = "confirmed"
        else:
            # the edge gap: between a_last and b_first there may be non-story lines; a proposal whose t falls in there
            lo, hi = lines[a_last]["end"], lines[b_first]["start"]
            near = [p for p in props if lo - 1e-6 <= p["t_next_start"] <= hi + 1e-6 or lo - 1e-6 <= p["t_prev_end"] <= hi + 1e-6]
            if near:
                for p in near:
                    status[p["rank"]] = "confirmed"
                    b["proposals_confirmed"].append(p["t_str"])
                b["origin"] = "confirmed"
            else:
                b["origin"] = "added"
    if scenes:
        scenes[0]["origin"] = scenes[0]["origin"] or "start"
    for p in props:
        if p["rank"] in status:
            continue
        inside = next((s for s in scenes if s["start"] - 1e-6 < p["t"] < s["end"] + 1e-6), None)
        if inside is not None:
            status[p["rank"]] = "merged"
            inside["proposals_merged"].append(p["t_str"])
        else:
            status[p["rank"]] = "outside"
    proposals = [{"rank": p["rank"], "t_str": p["t_str"], "status": status.get(p["rank"], "outside")} for p in props]
    ns_lines = []
    for n in ans.get("possible_non_story") or []:
        ids, _ = expand_lines(n.get("lines", ""), all_ids)
        ns_lines += ids
    counts = {"n_speakers": len(ans.get("speakers") or []),
              "n_speakers_changed": sum(1 for s in speakers_table if s["changed"]),
              "n_new_speakers": sum(1 for s in speakers_table if s["status"] == "new"),
              "n_scenes": len(scenes),
              "n_proposals_confirmed": sum(1 for v in status.values() if v == "confirmed"),
              "n_proposals_merged": sum(1 for v in status.values() if v == "merged"),
              "n_edges_added": sum(1 for s in scenes if s["origin"] == "added"),
              "n_short_scenes_merged": len(merged_short),
              "n_merges": len(merges), "n_splits": len(splits), "n_inferred_lines": len(inferred),
              "n_anchor_checks": len(anchor_table), "n_anchor_changed": sum(1 for a in anchor_table if a["changed"]),
              "n_resolutions": len(ans.get("suspect_resolutions") or []),
              "n_lines_reassigned": len(line_speakers),
              "n_non_story_lines": len(set(ns_lines)), "content_type": ans.get("content_type")}
    return {"speakers_table": speakers_table, "anchor_table": anchor_table, "scenes": scenes, "merged_short": merged_short,
            "proposals": proposals, "line_speakers": line_speakers, "merges": merges, "splits": splits, "inferred_lines": inferred,
            "prior": "raw" if raw else "fix",
            "non_story_lines": sorted(set(ns_lines), key=all_ids.index), "counts": counts}


def plain_summary_1b(ep: dict) -> str:
    c = ep["derived"]["counts"]
    return (f"Named {c['n_speakers']} speakers ({c['n_speakers_changed']} changed from the speaker fix, {c['n_new_speakers']} new; "
            f"{c.get('n_merges', 0)} label merges, {c.get('n_splits', 0)} label splits; "
            f"{c.get('n_anchor_checks', 0)} anchor checks, {c.get('n_anchor_changed', 0)} overturned), "
            f"confirmed {c['n_scenes']} scenes (confirmed {c['n_proposals_confirmed']} proposals, merged {c['n_proposals_merged']}, "
            f"added {c['n_edges_added']} edges, {c.get('n_short_scenes_merged', 0)} short scenes merged), resolved {c['n_resolutions']} "
            f"suspects ({c['n_lines_reassigned']} lines reassigned), flagged {c['n_non_story_lines']} possibly non-story lines, "
            f"content type {c['content_type']}.")


# ----------------------------------------------------------------------------------------------- the call
def _check_stage1a(facts: dict | None) -> None:
    if not facts or "1a" not in (facts.get("stages") or {}):
        raise RuntimeError("Stage 1B needs Stage 1A output; run Stage 1A first")
    v = facts["stages"]["1a"].get("version")
    if v != stage1_facts.VERSION:
        raise RuntimeError(f"Stage 1A output is {v}, but the current Stage 1A is {stage1_facts.VERSION}; re-run Stage 1A first")


def prepare(facts: dict, cfg: dict) -> dict:
    """Prefix, system text, instructions for the full call, schema, model spec (no network)."""
    series_key = facts["episode"].get("series_key")
    glossary_prior = load_glossary(series_key, cfg)
    prefix = build_prefix(facts, glossary_prior, cfg)
    sections = load_prompt()
    schema = load_schema()
    spec = ModelSpec.from_dict(cfg["models"].get("stage1b") or cfg["models"]["default"])
    return {"prefix": prefix, "system": sections["system"], "sections": sections, "schema": schema, "spec": spec,
            "glossary_prior": glossary_prior, "series_key": series_key,
            "instructions": make_instructions(sections, "all", cfg)}


def uses_video(cfg: dict) -> bool:
    return bool((cfg.get("stage1b") or {}).get("video", False))


def estimate_cost(facts: dict, cfg: dict, counter=None) -> dict:
    """Token count of the prefix + instructions (``counter(text) -> int``; default Gemini countTokens, free), the
    whole-episode video at the low-resolution rate when ``stage1b.video`` is on, and the rupee estimate at config
    rates. ``expected_output_tokens`` comes from config; worst case = max_output_tokens."""
    _check_stage1a(facts)
    prep = prepare(facts, cfg)
    text = prep["system"] + "\n" + prep["prefix"] + "\n\n" + prep["instructions"]
    if counter is None:
        from shotvi.llm.gemini import GeminiProvider
        prov = GeminiProvider()
        counter = lambda t: prov.count_tokens(prep["spec"], t)  # noqa: E731
    n_text = int(counter(text))
    n_prefix = int(counter(prep["prefix"]))
    n_video = int(float(facts["episode"]["duration"]) * cfg["rates"]["tokens_per_s_video_low"]) if uses_video(cfg) else 0
    n_in = n_text + n_video
    rate = inr_per_usd(cfg)
    r = cfg["rates"]
    out_exp = int(cfg["stage1b"]["expected_output_tokens"])
    out_max = int(prep["spec"].params.get("max_output_tokens", 16384))
    inr = lambda o: (n_in * r["usd_per_1m_input"] + o * r["usd_per_1m_output"]) / 1e6 * rate  # noqa: E731
    return {"input_tokens": n_in, "text_tokens": n_text, "video_tokens": n_video, "prefix_tokens": n_prefix,
            "expected_output_tokens": out_exp, "max_output_tokens": out_max,
            "inr_expected": round(inr(out_exp), 2), "inr_worst": round(inr(out_max), 2), "inr_per_usd": rate,
            "model": prep["spec"].model, "video": uses_video(cfg),
            "note": ("whole episode as low-resolution video (1 fps) + text; " if uses_video(cfg) else "text only; ")
                    + "a validation retry re-sends everything (implicit cache may discount the text)"}


def run(inputs, facts: dict | None, cfg: dict, client=None, log=None, cutter=None, uploader=None, deleter=None) -> dict:
    """The episode pass. ``client``: an ``LLMClient``-like object with ``generate_json(role, spec, system, prompt,
    schema, meta, want_raw, media)`` (tests pass a fake); default = the real client with the response cache.
    With ``stage1b.video`` the whole episode is cut to low resolution (``cutter``), uploaded (``uploader``) and sent
    after the prefix text."""
    t0 = time.perf_counter()
    log = log or (lambda *a: None)
    _check_stage1a(facts)
    assert facts is not None
    raw = (cfg.get("stage1b") or {}).get("prior", "fix") == "raw"
    prep = prepare(facts, cfg)
    run_dir = Path(inputs.paths.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prefix.txt").write_text(prep["prefix"], encoding="utf-8")
    media_parts: list[dict] = [{"text": prep["prefix"]}]
    video_info = None
    if uses_video(cfg):
        from .media import cut_scene, media_part, upload
        cutter = cutter or cut_scene
        uploader = uploader or upload
        dur = float(facts["episode"]["duration"])
        log(f"cutting the whole episode to {cfg['stage1b'].get('video_max_height', 480)}p for the episode pass")
        mpath = cutter(Path(inputs.paths.mp4), run_dir / cfg["stage2"]["media_dir"], 0, 0.0, dur, "video",
                       int(cfg["stage1b"].get("video_max_height", 480)), log)
        up = uploader(mpath, "video")
        media_parts.append(media_part(up, "video", 1))
        media_parts.append({"text": "The video above is the whole episode; its clock matches the line times in PART 1."})
        video_info = {"path": str(mpath), "uri": up.get("uri"), "duration_s": dur}
    if client is None:
        from shotvi.llm.client import Ledger, LLMClient, get_provider
        client = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"),
                           max_retries=int(cfg["stage1b"].get("http_retries", 1)))
        if uses_video(cfg):
            get_provider(prep["spec"].provider).timeout = float(cfg["stage1b"].get("http_timeout_s", 1200))
    rate = inr_per_usd(cfg)
    max_retry = int(cfg["stage1b"]["max_retries_per_tier"])
    attempts: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "thought_tokens": 0}

    t1keys = (TIER0 + TIER1) if raw else TIER1

    def call(tier: str, errors: list[str] | None) -> dict:
        instr = make_instructions(prep["sections"], tier, cfg, errors)
        # part order: the cached prefix text first (byte-identical), then the episode video, then the instructions
        prompt = instr
        full = prep["schema"] if raw else tier_schema(prep["schema"], TIER1 + TIER2)
        schema = full if tier == "all" else tier_schema(prep["schema"], t1keys if tier == "1" else TIER2)
        att = {"n": len(attempts) + 1, "tier": tier, "errors_shown": errors or [], "instructions": instr,
               "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "video": video_info,
               "part_order": ["prefix.txt", "episode video" if video_info else "(no video)", "instructions"]}
        attempts.append(att)
        log(f"stage 1B call {att['n']}: tier {tier}" + (f" (retry with {len(errors)} errors)" if errors else ""))
        try:
            obj, raw_resp = client.generate_json(ROLE, prep["spec"], prep["system"], prompt, schema,
                                                 meta={"video_id": inputs.vid, "stage": "1b", "tier": tier}, want_raw=True,
                                                 media=media_parts)
            raw_ = raw_resp
        except BaseException as e:
            att["error"] = f"{type(e).__name__}: {e}"
            _write_debug(run_dir, prep, attempts, usage, rate)
            raise
        att["raw_text"] = raw_.text
        att["usage"] = {"input_tokens": raw_.input_tokens, "output_tokens": raw_.output_tokens,
                        "thought_tokens": raw_.thought_tokens, "cached_tokens": raw_.cached_tokens,
                        "model_version": raw_.model_version, "modalities": getattr(raw_, "prompt_modalities", None)}
        for k in ("input_tokens", "output_tokens", "cached_tokens", "thought_tokens"):
            usage[k] += int(getattr(raw_, k) or 0)
        return obj

    repairs: list[str] = []

    def v1(a: dict) -> list[str]:
        repairs.extend(repair_scene_edges(a, facts))
        repairs.extend(repair_scene_gaps(a, facts))
        e0 = validate_tier0(a, facts, float(facts["episode"]["duration"])) if raw else []
        return e0 + validate_tier1(a, facts, a.get("possible_non_story"), share, raw)

    share = float(cfg["stage1b"].get("anchor_max_share", 0.5))
    ans = call("all", None)
    t1_errs = v1(ans)
    attempts[-1]["validation_tier1"] = t1_errs
    n = 0
    while t1_errs and n < max_retry:
        n += 1
        part = call("1", t1_errs)
        for k in t1keys:
            if k in part:
                ans[k] = part[k]
        t1_errs = v1(ans)
        attempts[-1]["validation_tier1"] = t1_errs
    if t1_errs:
        _write_debug(run_dir, prep, attempts, usage, rate)
        raise EpisodePassError(f"Tier 1 failed validation after {n} retr{'y' if n == 1 else 'ies'}: " + " | ".join(t1_errs[:6]))
    t2_errs = validate_tier2(ans, facts, cfg)
    attempts[-1]["validation_tier2"] = t2_errs
    n = 0
    while t2_errs and n < max_retry:
        n += 1
        part = call("2", t2_errs)
        for k in TIER2:
            if k in part:
                ans[k] = part[k]
        t2_errs = validate_tier2(ans, facts, cfg)
        attempts[-1]["validation_tier2"] = t2_errs
    if t2_errs:
        _write_debug(run_dir, prep, attempts, usage, rate)
        raise EpisodePassError(f"Tier 2 failed validation after {n} retr{'y' if n == 1 else 'ies'}: " + " | ".join(t2_errs[:6]))
    # a last cross-check: Tier 2 non_story may have changed which lines a scene may skip
    t1_errs = v1(ans)
    if t1_errs:
        _write_debug(run_dir, prep, attempts, usage, rate)
        raise EpisodePassError("Tier 1 no longer validates after the Tier 2 answer: " + " | ".join(t1_errs[:6]))

    if video_info and cfg["stage1b"].get("delete_upload_after_call", True):
        try:
            from .media import delete_upload
            video_info["deleted_from_google"] = (deleter or delete_upload)({"name": up.get("name")})
        except Exception as e:  # noqa: BLE001 - a failed delete must be visible, never fatal (the file expires in 48 h)
            video_info["deleted_from_google"] = f"failed: {type(e).__name__}: {e}"
    derived = derive(ans, facts, float(cfg["stage1b"].get("min_scene_s", 0) or 0), share, raw)
    merged = merge_glossary(prep["glossary_prior"], ans.get("glossary") or {}, inputs.vid)
    gpath = save_glossary(prep["series_key"], merged, cfg)
    usd = (usage["input_tokens"] - min(usage["cached_tokens"], usage["input_tokens"])) * cfg["rates"]["usd_per_1m_input"] / 1e6 \
        + min(usage["cached_tokens"], usage["input_tokens"]) * cfg["rates"]["usd_per_1m_cached_input"] / 1e6 \
        + (usage["output_tokens"] + usage["thought_tokens"]) * cfg["rates"]["usd_per_1m_output"] / 1e6
    cost_inr = round(usd * rate, 3)
    ledger = getattr(client, "ledger", None)
    billed = None
    if ledger is not None and hasattr(ledger, "summary"):
        sm = ledger.summary()
        billed = round(float(sm.get("usd_billed", 0.0)) * rate, 3)
    _write_debug(run_dir, prep, attempts, usage, rate)
    facts = dict(facts)
    facts["episode_pass"] = {**{k: ans.get(k) for k in (TIER0 if raw else ()) + TIER1 + TIER2}, "derived": derived,
                             "prior": "raw" if raw else "fix", "video": video_info is not None,
                             "glossary_file": _rel(gpath), "glossary_prior_used": prep["glossary_prior"] is not None}
    facts.setdefault("stages", {})["1b"] = {
        "version": VERSION, "model": prep["spec"].to_dict(), "prompt_file": PROMPT_PATH.name, "prior": "raw" if raw else "fix",
        "video": video_info, "config_sha8": cfg.get("_sha8"), "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_s": round(time.perf_counter() - t0, 3), "n_attempts": len(attempts),
        "usage": {"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"] + usage["thought_tokens"],
                  "thought_tokens": usage["thought_tokens"], "cached_tokens": usage["cached_tokens"], "cost_inr": cost_inr,
                  "cost_inr_billed": billed, "inr_per_usd": rate},
        "warnings": ([] if billed is None or billed > 0 else ["answer replayed from the response cache (₹0 billed)"]) + [f"repaired: {r}" for r in repairs],
        "summary": derived["counts"], "prefix_file": "prefix.txt", "debug_file": "stage1b_debug.json"}
    return facts


def _rel(p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(p)


def _write_debug(run_dir: Path, prep: dict, attempts: list[dict], usage: dict, rate: float) -> None:
    write_json(run_dir / "stage1b_debug.json", {
        "model": prep["spec"].to_dict(), "system": prep["system"], "prefix_file": "prefix.txt",
        "prefix_chars": len(prep["prefix"]), "glossary_prior": prep["glossary_prior"], "attempts": attempts,
        "usage": usage, "inr_per_usd": rate})
