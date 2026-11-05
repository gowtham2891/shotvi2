"""Stage 3, finding clips (founder, 2026-11-04), in two AI steps; code only looks up times and checks the format.

3a moments (``prompts/stage3_moments.md``): one TEXT call per scene with the whole transcript (who says each line, who
is on screen), the episode story and every scene's description (Stage 2, ``scene_notes``). It lists every moment whose
payoff lands in that scene (payoff-first, the 2026-10-07 v1 method): the payoff line, what it needs from earlier, the
scenes it uses, kind (standalone / promo), why it works. Generous; no ranking (Stage 4 chooses). The shared episode
text is the first part of every call so the provider can reuse it across the scene calls.

3b shape (``prompts/stage3_shape.md``): one call per scene (founder, option B) with the moments whose payoff lands there
and the VIDEO of the scenes they use, sent once; a clip covers one moment or several of the same stretch: start and end
on any word, up to three whole-line drops, an optional cold-open line, hook, title, why; ``works`` false when it does
not land on screen. Code turns line ids + quoted words into times and the played pieces (cold open first; the lifted line is
removed from its place); a clip over ``max_clip_s`` is re-asked.

One step (founder, 2026-11-04, ``find_clips``, ``prompts/stage3_clips.md``): 3a and 3b merged so nothing is handed
from one AI step to the next (3b copied 3a's lines). The whole episode video + people + story + scene notes +
transcript are kept once at Google (explicit context cache) and every scene call reuses them; each call gives the
finished clips whose payoff lands in that scene. Code only looks up times and checks the format, as in 3b.

Outputs: ``facts["moments"]`` (3a), ``facts["clips"]`` (3b or the one step).
"""
from __future__ import annotations

import json
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

VERSION = "stage3_find/0.2"
P = Path(__file__).resolve().parent
MOMENTS_PROMPT, SHAPE_PROMPT = P / "prompts" / "stage3_moments.md", P / "prompts" / "stage3_shape.md"
MOMENTS_SCHEMA, SHAPE_SCHEMA = P / "schemas" / "stage3_moments.schema.json", P / "schemas" / "stage3_clip.schema.json"
CLIPS_PROMPT, CLIPS_SCHEMA = P / "prompts" / "stage3_clips.md", P / "schemas" / "stage3_scene_clips.schema.json"


class Stage3Error(RuntimeError):
    pass


# ----------------------------------------------------------------------------------------------- shared text
def _names(facts: dict) -> dict:
    return {s["key"]: s.get("name") or s["key"] for s in facts["episode_pass"]["speakers"]}


def people_text(facts: dict) -> list[str]:
    nm = _names(facts)
    looks = {p["key"]: p.get("look") or "" for p in (facts.get("identity_pass") or {}).get("people") or []}
    d = facts["episode_pass"]["derived"]
    used = sorted(set((d.get("line_speakers") or {}).values()) | {k for v in (d.get("on_screen") or {}).values() for k in v})
    roles = {s["key"]: s.get("role") or "" for s in facts["episode_pass"]["speakers"]}
    return [f"{nm.get(k, k)}: {roles.get(k, '')}" + (f" ({looks[k]})" if looks.get(k) else "") for k in used]


def scene_note_text(n: dict) -> list[str]:
    out = [f"== Scene {n['scene_id']} ({fmt_ts(n['start'])}-{fmt_ts(n['end'])}) {n.get('where') or ''}", n["story"],
           f"Mood: {n.get('mood') or ''}"]
    out += [f"- {x['first']}-{x['last']}: {x['what_happens']}" for x in n.get("situations") or []]
    out += [f"Reaction after {x['after']}: {x['who']} {x['how']}" for x in n.get("reactions") or []]
    out += [f"Seen {x['range']}: {x['what_is_seen']}" for x in n.get("visual_moments") or []]
    out += [f"Silence {x['range']}: {x['what_happens']}" + (f" (music: {x['music']})" if x.get("music") else "") for x in n.get("silences") or []]
    if n.get("context_needed"):
        out.append(f"A new viewer needs: {n['context_needed']}")
    out += [f"Link to scene {x['scene']}: {x['how']}" for x in n.get("connections") or []]
    if n.get("relationships"):
        out.append(f"Relationships: {n['relationships']}")
    return out


def line_text(facts: dict, r: dict) -> str:
    nm = _names(facts)
    d = facts["episode_pass"]["derived"]
    if r["kind"] != "speech":
        return f"{r['id']} [{fmt_ts(r['start'])}] (sound) {r.get('text') or r.get('tag') or ''}"
    k = (d.get("line_speakers") or {}).get(r["id"])
    vis = ", ".join(nm.get(x, x) for x in (d.get("on_screen") or {}).get(r["id"]) or []) or "nobody"
    return f"{r['id']} [{fmt_ts(r['start'])}-{fmt_ts(r['end'])}] {nm.get(k, k or '?')} (on screen: {vis}) | {r['text']}"


def transcript_text(facts: dict, lo: int = 0, hi: int | None = None) -> list[str]:
    """Lines lo..hi (positions) grouped by the video step's scenes, gaps marked."""
    lines = facts["lines"]
    hi = len(lines) - 1 if hi is None else hi
    pos = {r["id"]: i for i, r in enumerate(lines)}
    owner = {}
    for s in facts["identity_pass"]["scenes"]:
        for i in range(pos[s["first_line"]], pos[s["last_line"]] + 1):
            owner[i] = s
    out, cur = [], "start"
    for i in range(lo, hi + 1):
        s = owner.get(i)
        sid = s["id"] if s else None
        if sid != cur:
            out.append("\n-- between scenes --" if s is None else f"\n== Scene {sid} ({fmt_ts(s['start'])}-{fmt_ts(s['end'])}) {s.get('where') or ''}")
            cur = sid
        out.append(line_text(facts, lines[i]))
    return out


def episode_text(facts: dict) -> str:
    sn = facts["scene_notes"]
    ep = facts["episode"]
    out = [f"# EPISODE: {ep.get('title') or ep['video_id']} ({ep['duration_str']})", "", "# PART 1: PEOPLE"] + people_text(facts)
    out += ["", "# PART 2: THE EPISODE'S STORY AND EVERY SCENE", sn["episode"], ""]
    for n in sn["scenes"]:
        out += scene_note_text(n) + [""]
    out += ["# PART 3: TRANSCRIPT BY SCENE (id [start-end] who says it (on screen) | words)"] + transcript_text(facts)
    return "\n".join(out)


def _client(c: dict, spec: ModelSpec, client):
    if client is not None:
        return client
    from shotvi.llm.client import Ledger, LLMClient, get_provider
    cl = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"), max_retries=int(c.get("http_retries", 1)))
    get_provider(spec.provider).timeout = float(c.get("http_timeout_s", 1200))
    return cl


def _cost(cfg: dict, usage: dict) -> float:
    r = cfg["rates"]
    cached = min(usage["cached_tokens"], usage["input_tokens"])
    return round(((usage["input_tokens"] - cached) * r["usd_per_1m_input"] + cached * r["usd_per_1m_cached_input"]
                  + (usage["output_tokens"] + usage["thought_tokens"]) * r["usd_per_1m_output"]) / 1e6 * inr_per_usd(cfg), 3)


def _ask(client, role, spec, sections, prompt_tail, schema, parts, meta, validate, retries, attempts, usage, cache=None):
    """One call plus re-asks with the validator's errors. Returns (answer or None, errors, warnings, value).
    ``cache`` (a ``VideoCache``): the system text, media and shared text live at Google; only the prompt is sent."""
    errors, warns, value = [], [], None
    for attempt in range(retries + 1):
        instr = (sections["instructions"] + "\n\n" + prompt_tail
                 + ("\n\n" + sections["retry"].replace("{errors}", "\n".join(f"- {e}" for e in errors)) + f"\n(attempt {attempt + 1})" if errors else "")
                 + "\n\n" + sections["output_format"])
        att = {"role": role, "meta": meta, "n": attempt + 1, "errors_shown": errors}
        attempts.append(att)
        if cache is not None:
            cache.ensure(client, instr, schema)
            obj, raw = client.generate_json(role, spec, "", instr, schema, meta=meta, want_raw=True, cached_content=cache.ref())
        else:
            obj, raw = client.generate_json(role, spec, sections["system"], instr, schema, meta=meta, want_raw=True, media=parts)
        att["raw_text"] = raw.text
        att["usage"] = {k: int(getattr(raw, k) or 0) for k in usage}
        for k in usage:
            usage[k] += att["usage"][k]
        errors, warns, value = validate(obj)
        att["validation"], att["warnings"] = errors, warns
        if not errors:
            return obj, errors, warns, value
    return None, errors, warns, value


# ----------------------------------------------------------------------------------------------- 3a moments
def validate_moments(ans: dict, facts: dict, scene: dict) -> tuple[list[str], list[str], list[dict]]:
    errs, warns = [], []
    if ans.get("scene_id") != scene["id"]:
        errs.append(f"scene_id must be {scene['id']}")
    pos = {r["id"]: i for i, r in enumerate(facts["lines"])}
    sids = {s["id"] for s in facts["identity_pass"]["scenes"]}
    lo, hi = pos[scene["first_line"]], pos[scene["last_line"]]
    out, seen, drops = [], set(), {}
    for m in ans.get("moments") or []:
        p, nf = m.get("payoff"), m.get("needs_from")
        why = None
        if p not in pos or not lo <= pos[p] <= hi or facts["lines"][pos[p]]["kind"] != "speech":
            why = "a payoff that is not a spoken line of this scene"
        elif nf not in pos or pos[nf] > pos[p]:
            why = "a needs_from that is not a line before the payoff"
        elif p in seen:
            why = "a repeated payoff"
        if why:
            drops[why] = drops.get(why, 0) + 1; continue
        seen.add(p)
        scenes = sorted({x for x in m.get("scenes") or [] if x in sids} | {scene["id"]})
        out.append({**m, "scene": scene["id"], "scenes": scenes})
    warns += [f"{n} moment(s) with {w} dropped" for w, n in drops.items()]
    return errs, warns, out


def find_moments(inputs, facts: dict, cfg: dict, client=None, log=None, scene_ids=None) -> dict:
    t0 = time.perf_counter()
    log = log or (lambda *a: None)
    if not facts.get("scene_notes"):
        raise Stage3Error("Stage 3 needs Stage 2's scene notes")
    c = cfg["stage3_find"]
    spec = ModelSpec.from_dict(c["moments_model"])
    run_dir = Path(inputs.paths.run_dir); run_dir.mkdir(parents=True, exist_ok=True)
    shared = episode_text(facts)
    (run_dir / "context_stage3.txt").write_text(shared, encoding="utf-8")
    sections = s1b.load_prompt(MOMENTS_PROMPT)
    schema = json.loads(MOMENTS_SCHEMA.read_text(encoding="utf-8"))
    client = _client(c, spec, client)
    attempts, moments, warns, failed = [], [], [], []
    usage = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0}
    for sc in facts["identity_pass"]["scenes"]:
        if scene_ids and sc["id"] not in scene_ids:
            continue
        log(f"stage 3a: scene {sc['id']}")
        tail = f"The scene to search: Scene {sc['id']} ({fmt_ts(sc['start'])}-{fmt_ts(sc['end'])}, lines {sc['first_line']}-{sc['last_line']}) {sc.get('where') or ''}."
        ans, errs, w, ms = _ask(client, "stage3_moments", spec, sections, tail, schema, [{"text": shared}],
                                {"video_id": inputs.vid, "stage": "stage3a", "scene": sc["id"]},
                                lambda o, sc=sc: validate_moments(o, facts, sc), int(c.get("retries", 1)), attempts, usage)
        if ans is None:
            failed.append(sc["id"]); warns.append(f"scene {sc['id']}: no valid answer ({' | '.join(errs[:3])})"); continue
        warns += [f"scene {sc['id']}: {x}" for x in w]
        moments += ms
    for i, m in enumerate(moments, 1):
        m["id"] = f"m{i:02d}"
    write_json(run_dir / "stage3a_debug.json", {"attempts": attempts, "model": spec.to_dict()})
    cost = _cost(cfg, usage)
    append_log(cfg, {"episode": inputs.vid, "stage": "stage3a_moments", "status": "ok" if not failed else "partial",
                     "duration_s": round(time.perf_counter() - t0, 1), "input_tokens": usage["input_tokens"],
                     "output_tokens": usage["output_tokens"] + usage["thought_tokens"], "cached_tokens": usage["cached_tokens"],
                     "cost_inr_est": cost, "note": f"{len(moments)} moments; " + "; ".join(warns)[:200]})
    out = dict(facts)
    out["moments"] = {"version": VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": spec.to_dict(),
                      "moments": moments, "warnings": warns, "failed_scenes": failed, "usage": {**usage, "cost_inr": cost}}
    return out


# ----------------------------------------------------------------------------------------------- 3b shape
def region(facts: dict, m: dict) -> tuple[int, int]:
    """Line positions of the scenes the moment uses, from the first to the last (gaps between them included)."""
    pos = {r["id"]: i for i, r in enumerate(facts["lines"])}
    sc = {s["id"]: s for s in facts["identity_pass"]["scenes"]}
    use = [sc[i] for i in m["scenes"]]
    lo = min([pos[s["first_line"]] for s in use] + [pos[m["needs_from"]]])
    hi = max([pos[s["last_line"]] for s in use] + [pos[m["payoff"]]])
    return lo, hi


def word_time(facts: dict, words: list[dict], lid: str, at: str, last: bool) -> tuple[float | None, int | None]:
    """Time of the quoted word in the line (its start, or its end when ``last``) and its position. None when the
    word is not in the line."""
    r = next(x for x in facts["lines"] if x["id"] == lid)
    toks = r["text"].split()
    want = NC._tok(at.split()[-1] if last and at.split() else at)
    hits = [i for i, t in enumerate(toks) if NC._tok(t) == want]
    if not hits:
        return None, None
    i = hits[-1] if last else hits[0]
    a, b = r.get("words") or (None, None)
    if a is not None and b - a + 1 == len(toks):
        w = words[a + i]
        return (w["end"] if last else w["start"]), i
    return (r["end"] if last else r["start"]), i       # the fixed text no longer matches the transcriber's words: whole line


def clock_s(x) -> float | None:
    """'m:ss.s' or 'h:mm:ss.s' (the transcript's own time format) -> seconds; a bare number is seconds. Founder run
    2026-11-04: asked for seconds next to m:ss line times, the model wrote 1:54.5 as 154.5 in 11 of 17 scenes."""
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return float(x)
    try:
        parts = [float(p) for p in str(x).strip().split(":")]
    except ValueError:
        return None
    if not 1 <= len(parts) <= 3 or any(p < 0 for p in parts):
        return None
    t = 0.0
    for p in parts:
        t = t * 60 + p
    return t


def validate_clip(ans: dict, facts: dict, words: list[dict], lo: int, hi: int, max_s: float, where: str = "PART 4") -> tuple[list[str], list[str], dict]:
    errs, warns = [], []
    lines = facts["lines"]
    pos = {r["id"]: i for i, r in enumerate(lines)}
    inside = lambda l: l in pos and lo <= pos[l] <= hi
    st, en = ans.get("start") or {}, ans.get("end") or {}
    for name, x in (("start", st), ("end", en)):
        if not inside(x.get("line")) or lines[pos[x["line"]]]["kind"] != "speech":
            errs.append(f"{name}.line {x.get('line')!r} is not a spoken line in {where}; give the line's id (like L0123), not its words")
    if errs:
        return errs, warns, {}
    t0, _ = word_time(facts, words, st["line"], st.get("at") or "", False)
    t1, _ = word_time(facts, words, en["line"], en.get("at") or "", True)
    if t0 is None:
        errs.append(f"start.at {st.get('at')!r} is not a word of {st['line']}; copy the first word exactly")
    if t1 is None:
        errs.append(f"end.at {en.get('at')!r} is not a word of {en['line']}; copy the last word exactly")
    if errs:
        return errs, warns, {}
    if t1 <= t0:
        return [f"the end ({en['line']} {en['at']}) is not after the start ({st['line']} {st['at']})"], warns, {}
    a, b = pos[st["line"]], pos[en["line"]]
    cut = set()
    drops = []
    for d in ans.get("drops") or []:
        if not (inside(d.get("first")) and inside(d.get("last")) and a < pos[d["first"]] <= pos[d["last"]] < b):
            errs.append(f"drop {d.get('first')}..{d.get('last')} is not a range of whole lines strictly inside the clip ({st['line']}..{en['line']})"); continue
        cut |= set(range(pos[d["first"]], pos[d["last"]] + 1)); drops.append(d)
    if len(drops) > 3:
        errs.append(f"{len(drops)} drops; at most three")
    co = ans.get("cold_open")
    lift = None
    if co:
        l = co.get("line")
        if not inside(l) or not a < pos[l] < b or pos[l] in cut or lines[pos[l]]["kind"] != "speech" or lines[pos[l]].get("zero_len"):
            errs.append(f"cold_open line {l!r} is not a spoken line inside the clip (not the start or end line, not dropped, not empty)")
        else:
            lift = pos[l]
    if errs:
        return errs, warns, {}
    # played pieces: the cold open, then the stretch with the drops and the lifted line taken out
    pieces = []
    if lift is not None:
        pieces.append({"start": lines[lift]["start"], "end": lines[lift]["end"], "lines": [lines[lift]["id"]], "cold_open": True})
    seg = None
    for i in range(a, b + 1):
        if i in cut or i == lift:
            if seg:
                pieces.append(seg); seg = None
            continue
        r = lines[i]
        s0 = t0 if i == a else r["start"]
        s1 = t1 if i == b else r.get("end", r["start"])
        if seg is None:
            seg = {"start": s0, "end": s1, "lines": [r["id"]]}
        else:
            seg["end"] = max(seg["end"], s1); seg["lines"].append(r["id"])
    if seg:
        pieces.append(seg)
    # a piece that is only sound tags carries no speech: keep it (music inside the stretch) unless it is empty
    pieces = [p for p in pieces if p["end"] > p["start"]]
    # founder, 2026-11-04: the clip may hold after its last word on a reaction / nod / sound (end.hold_until, chosen
    # by the model while watching); code only checks it is not before the last word and does not run into speech
    hold = en.get("hold_until")
    if en.get("hold") is not None and pieces:
        # PARKED (founder, 2026-11-04, not run): the model names WHAT the clip holds on; code only looks up its time.
        # Prompt + schema in prompts/parked/*pick_beat*; the live prompt asks hold_until (m:ss). Founder: sound tags miss
        # nods / looks, these beats come from watching; a close-watch "ending check" per clip is the other idea.
        h = str(en["hold"]).strip()
        nxt = next((r for r in lines[b + 1:] if r["kind"] == "speech" and not r.get("zero_len")), None)
        if h.lower() == "none":
            hold = None
        elif h.lower() == "next_line":
            if nxt is None:
                return ["end.hold \"next_line\": no spoken line follows; name a (sound) line or \"none\""], warns, {}
            hold = nxt["start"] - 0.1
        elif h in pos and lines[pos[h]]["kind"] != "speech" and pos[h] > b and (nxt is None or pos[h] < pos[nxt["id"]]):
            hold = float(lines[pos[h]].get("end", lines[pos[h]]["start"]))
            if nxt is not None:
                hold = min(hold, nxt["start"] - 0.1)
        else:
            return [f"end.hold {h!r} must be a (sound) line between {en['line']} and the next spoken line, \"next_line\" or \"none\""], warns, {}
        hold = max(hold, t1) if hold is not None else None
    if hold is not None and pieces:
        hold = clock_s(hold)
        if hold is None:
            return [f"end.hold_until {en.get('hold_until')!r} is not a time like the line times (m:ss.s)"], warns, {}
        if hold < t1 - 0.05:
            return [f"end.hold_until {hold:.1f} is before the last word ends ({t1:.1f}); give a time at or after it"], warns, {}
        nxt = next((r for r in lines[b + 1:] if r["kind"] == "speech" and not r.get("zero_len")), None)
        if nxt is not None and hold > nxt["start"] + 0.05:
            return [f"end.hold_until {hold:.1f} runs into the next spoken line {nxt['id']} (starts {nxt['start']:.1f}); "
                    f"hold until before it, or end on that line"], warns, {}
        pieces[-1]["end"] = max(pieces[-1]["end"], hold)
    dur = round(sum(p["end"] - p["start"] for p in pieces), 2)
    if dur > max_s:
        errs.append(f"the clip plays {dur:.0f} s; the maximum is {max_s:.0f} s: start later, end earlier or drop whole chunks")
    return errs, warns, {"pieces": pieces, "duration": dur, "start_time": t0, "end_time": t1,
                         "hold_s": round(pieces[-1]["end"] - t1, 2) if pieces else 0.0}


def validate_scene_clips(ans: dict, facts: dict, words: list[dict], group: list[dict], lo: int, hi: int, max_s: float) -> tuple[list[str], list[str], list[dict]]:
    """Each clip checked alone (``validate_clip``); errors are prefixed with the clip's number. A moment left out of
    every clip is a warning (its scene's other clips are kept)."""
    errs, warns, out = [], [], []
    ids = {m["id"] for m in group}
    covered = set()
    for i, c in enumerate(ans.get("clips") or [], 1):
        ms = [x for x in c.get("moments") or [] if x in ids]
        if not ms:
            errs.append(f"clip {i}: moments {c.get('moments')!r} are not moment ids of PART 2"); continue
        e, w, val = validate_clip(c, facts, words, lo, hi, max_s)
        errs += [f"clip {i}: {x}" for x in e]
        warns += [f"clip {i}: {x}" for x in w]
        if not e:
            covered |= set(ms)
            out.append({**c, "moments": ms, **val})
    left_out = {x.get("moment") for x in ans.get("left_out") or []}
    left = sorted(ids - covered - left_out)
    if left and not errs:
        warns.append(f"moment(s) {left} are in no clip and not in left_out")
    return errs, warns, out


def shape_clips(inputs, facts: dict, cfg: dict, client=None, log=None, scene_ids=None, cutter=None, uploader=None, deleter=None) -> dict:
    """founder, 2026-11-04 (option B): one call per scene with all the moments whose payoff lands there and the video of
    the scenes they use (sent once), so it can also make one clip of several moments of one stretch."""
    t0 = time.perf_counter()
    log = log or (lambda *a: None)
    c = cfg["stage3_find"]
    spec = ModelSpec.from_dict(c["shape_model"])
    run_dir = Path(inputs.paths.run_dir); run_dir.mkdir(parents=True, exist_ok=True)
    sections = s1b.load_prompt(SHAPE_PROMPT)
    schema = json.loads(SHAPE_SCHEMA.read_text(encoding="utf-8"))
    client = _client(c, spec, client)
    cutter = cutter or cut_scene; uploader = uploader or upload
    notes = {n["scene_id"]: n for n in facts["scene_notes"]["scenes"]}
    by_id = {m["id"]: m for m in facts["moments"]["moments"]}
    groups = {}
    for m in facts["moments"]["moments"]:
        groups.setdefault(m["scene"], []).append(m)
    words = inputs.words
    L = facts["lines"]
    attempts, clips, warns, left_out = [], [], [], []
    usage = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0}
    for sid, group in sorted(groups.items()):
        if scene_ids and sid not in scene_ids:
            continue
        spans = [region(facts, m) for m in group]
        lo, hi = min(x for x, _ in spans), max(y for _, y in spans)
        used = sorted({x for m in group for x in m["scenes"]})
        v0, v1 = float(L[lo]["start"]), float(L[hi].get("end", L[hi]["start"]))
        log(f"stage 3b: scene {sid}, {len(group)} moment(s) ({fmt_ts(v0)}-{fmt_ts(v1)})")
        mpath = cutter(Path(inputs.paths.mp4), run_dir / cfg["stage2"]["media_dir"], sid, v0, v1, "video", int(c.get("video_max_height", 480)), log)
        up = uploader(mpath, "video")
        try:
            mkey = content_key(mpath)
        except OSError:
            mkey = None
        text = ["# PART 1: PEOPLE"] + people_text(facts) + ["", "# PART 2: THE MOMENTS FOUND IN THIS SCENE"]
        for m in group:
            # founder, 2026-11-04: no line ids for where it starts or lands (3b copied them); what lands, in words
            text += [f"{m['id']} ({m['payoff_type']}, {m['kind']}, scenes {m['scenes']}): lands on \"{m['payoff_quote']}\". "
                     f"What lands: {m['what_lands']} Why it works: {m['why_it_works']}"]
        text += ["", "# PART 3: THE SCENES THEY USE"]
        for x in used:
            text += scene_note_text(notes[x]) + [""]
        text += [f"# PART 4: TRANSCRIPT (the video above starts at {fmt_ts(v0)} of the episode and ends at {fmt_ts(v1)})"] + transcript_text(facts, lo, hi)
        parts = [media_part(up, "video", 1, key=mkey), {"text": "\n".join(text)}]
        try:
            ans, errs, w, val = _ask(client, "stage3_shape", spec, sections, "", schema, parts, {"video_id": inputs.vid, "stage": "stage3b", "scene": sid},
                                     lambda o: validate_scene_clips(o, facts, words, group, lo, hi, float(c.get("max_clip_s", 180))),
                                     int(c.get("retries", 1)), attempts, usage)
        finally:
            if c.get("delete_upload_after_call", True):
                try:
                    from .media import delete_upload
                    (deleter or delete_upload)(up)
                except Exception:  # noqa: BLE001
                    pass
        if ans is None:
            warns.append(f"scene {sid}: clips with errors after the re-ask were left out ({' | '.join(errs[:3])})")
        warns += [f"scene {sid}: {x}" for x in w]
        for x in (ans or {}).get("left_out") or []:
            left_out.append({**x, "scene": sid})
        for cl in val or []:
            ms = [by_id[x] for x in cl["moments"]]
            clips.append({"id": f"c{len(clips) + 1:02d}", "scene": sid, "scenes": sorted({x for m in ms for x in m["scenes"]}),
                          "payoffs": [{"moment": m["id"], "line": m["payoff"], "quote": m["payoff_quote"], "type": m["payoff_type"]} for m in ms], **cl})
    write_json(run_dir / "stage3b_debug.json", {"attempts": attempts, "model": spec.to_dict()})
    cost = _cost(cfg, usage)
    append_log(cfg, {"episode": inputs.vid, "stage": "stage3b_shape", "status": "ok", "duration_s": round(time.perf_counter() - t0, 1),
                     "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"] + usage["thought_tokens"],
                     "cached_tokens": usage["cached_tokens"], "cost_inr_est": cost, "note": f"{len(clips)} clips; " + "; ".join(warns)[:200]})
    out = dict(facts)
    out["clips"] = {"version": VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": spec.to_dict(),
                    "clips": clips, "left_out": left_out, "warnings": warns, "usage": {**usage, "cost_inr": cost}}
    return out


# ----------------------------------------------------------------------------------------------- one step (3a + 3b)
class VideoCache:
    """founder, 2026-11-04: the whole episode video and the shared text uploaded once into a Gemini explicit context
    cache, reused by every scene call (cached input is billed at a tenth of the input price, plus storage per hour).
    Created lazily, only before a call that is not already in the local response cache (a rerun is free); deleted
    by ``close``. ``key`` names the cached CONTENT (model, system, text, video bytes) for the response cache."""

    def __init__(self, spec: ModelSpec, system: str, prefix: str, media: list[dict], media_key: str, ttl_s: int, log, provider=None):
        import hashlib
        self.spec, self.system, self.prefix, self.media, self.ttl_s, self.log = spec, system, prefix, media, int(ttl_s), log
        self.key = hashlib.sha256(f"{spec.model}\n{system}\n{prefix}\n{media_key}".encode("utf-8")).hexdigest()[:24]
        self.provider = provider
        self.name, self.info, self.created_at = None, {}, None

    def _prov(self):
        if self.provider is None:
            from shotvi.llm.client import get_provider
            self.provider = get_provider(self.spec.provider)
        return self.provider

    def ref(self) -> dict:
        return {"name": self.name, "key": self.key}

    def ensure(self, client, prompt: str, schema: dict) -> None:
        if self.name or getattr(client, "cache_only", False):
            return
        try:
            if client._cache_get(self.spec, client.cache_key(self.spec, "", prompt + f"\n<cached_content {self.key}>", schema)) is not None:
                return
        except Exception:   # noqa: BLE001  (a client without a response cache)
            pass
        from shotvi.llm.base import CacheUnavailable
        try:
            self.info = self._prov().create_cache(self.spec, self.system, self.prefix, self.ttl_s,
                                                  display_name=f"shotvi stage3 {self.key[:8]}", media=self.media)
        except CacheUnavailable as e:
            # never fall back to sending the whole video in every call (about ten times the price)
            raise Stage3Error(f"the episode video could not be cached at Google: {e}") from e
        self.name, self.created_at = self.info["name"], time.time()
        if hasattr(client, "ledger"):
            client.ledger.record_fixed("stage3_cache", self.spec, float(self.info.get("storage_usd") or 0.0),
                                       note=f"explicit cache storage, {self.info['tokens']} tokens x {self.ttl_s} s", tokens=int(self.info["tokens"]))
        self.log(f"stage 3: episode cached at Google ({self.info['tokens']} tokens, kept up to {self.ttl_s} s)")

    def close(self) -> dict:
        out = {"key": self.key}
        if self.name:
            ok = self._prov().delete_cache(self.name)
            out.update(name=self.name, tokens=self.info.get("tokens"), ttl_s=self.ttl_s, storage_usd=self.info.get("storage_usd"),
                       deleted=bool(ok), lived_s=round(time.time() - (self.created_at or time.time()), 1))
            self.name = None
        return out


def validate_found_clips(ans: dict, facts: dict, words: list[dict], scene: dict, max_s: float) -> tuple[list[str], list[str], list[dict]]:
    """Each clip: its payoff is a spoken line of this scene that plays in the clip; start / end / drops / cold open as in
    ``validate_clip`` (anywhere in the episode). Errors are prefixed with the clip's number; valid clips are kept."""
    errs, warns, out = [], [], []
    if ans.get("scene_id") != scene["id"]:
        errs.append(f"scene_id must be {scene['id']}")
    lines = facts["lines"]
    pos = {r["id"]: i for i, r in enumerate(lines)}
    lo, hi = pos[scene["first_line"]], pos[scene["last_line"]]
    for i, c in enumerate(ans.get("clips") or [], 1):
        p = c.get("payoff")
        if p not in pos or not lo <= pos[p] <= hi or lines[pos[p]]["kind"] != "speech":
            errs.append(f"clip {i}: payoff {p!r} is not a spoken line of scene {scene['id']} ({scene['first_line']}-{scene['last_line']})"); continue
        e, w, val = validate_clip(c, facts, words, 0, len(lines) - 1, max_s, where="the transcript")
        if not e and not any(p in pc["lines"] for pc in val["pieces"]):
            e = [f"the payoff {p} does not play in the clip (outside start..end, or dropped)"]
        errs += [f"clip {i}: {x}" for x in e]
        warns += [f"clip {i}: {x}" for x in w]
        if not e:
            out.append({**c, **val})
    return errs, warns, out


def shared_video_text(facts: dict) -> str:
    return "The video above is the whole episode; its clock matches the line times below.\n\n" + episode_text(facts)


def find_clips(inputs, facts: dict, cfg: dict, client=None, log=None, scene_ids=None, cutter=None, uploader=None, provider=None, deleter=None) -> dict:
    """founder, 2026-11-04: one call per scene with the whole episode (video + text, cached once) gives the finished
    clips whose payoff lands in that scene."""
    t0 = time.perf_counter()
    log = log or (lambda *a: None)
    if not facts.get("scene_notes"):
        raise Stage3Error("Stage 3 needs Stage 2's scene notes")
    c = cfg["stage3_find"]
    spec = ModelSpec.from_dict(c["clips_model"])
    run_dir = Path(inputs.paths.run_dir); run_dir.mkdir(parents=True, exist_ok=True)
    sections = s1b.load_prompt(CLIPS_PROMPT)
    schema = json.loads(CLIPS_SCHEMA.read_text(encoding="utf-8"))
    client = _client(c, spec, client)
    cutter = cutter or cut_scene; uploader = uploader or upload
    dur = float(facts["episode"]["duration"])
    mpath = cutter(Path(inputs.paths.mp4), run_dir / cfg["stage2"]["media_dir"], 0, 0.0, dur, "video", int(c.get("video_max_height", 480)), log)
    try:
        mkey = content_key(mpath)
    except OSError:
        mkey = str(mpath)
    up = uploader(mpath, "video")
    shared = shared_video_text(facts)
    (run_dir / "context_stage3.txt").write_text(shared, encoding="utf-8")
    cache = VideoCache(spec, sections["system"], shared, [media_part(up, "video", 1, key=mkey)], mkey, int(c.get("cache_ttl_s", 3600)), log, provider)
    words = inputs.words
    attempts, clips, warns, failed = [], [], [], []
    usage = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0}
    try:
        for sc in facts["identity_pass"]["scenes"]:
            if scene_ids and sc["id"] not in scene_ids:
                continue
            log(f"stage 3: scene {sc['id']}")
            tail = f"The scene to search: Scene {sc['id']} ({fmt_ts(sc['start'])}-{fmt_ts(sc['end'])}, lines {sc['first_line']}-{sc['last_line']}) {sc.get('where') or ''}."
            ans, errs, w, val = _ask(client, "stage3_clips", spec, sections, tail, schema, None,
                                     {"video_id": inputs.vid, "stage": "stage3", "scene": sc["id"]},
                                     lambda o, sc=sc: validate_found_clips(o, facts, words, sc, float(c.get("max_clip_s", 180))),
                                     int(c.get("retries", 1)), attempts, usage, cache=cache)
            if ans is None:
                failed.append(sc["id"])
                warns.append(f"scene {sc['id']}: clips with errors after the re-ask were left out ({' | '.join(errs[:3])})")
            warns += [f"scene {sc['id']}: {x}" for x in w]
            for cl in val or []:
                clips.append({"id": f"c{len(clips) + 1:02d}", "scene": sc["id"],
                              "payoffs": [{"line": cl["payoff"], "quote": cl.get("payoff_quote"), "type": cl.get("payoff_type")}], **cl})
    finally:
        cinfo = cache.close()
        if c.get("delete_upload_after_call", True):
            try:
                from .media import delete_upload
                (deleter or delete_upload)(up)
            except Exception:  # noqa: BLE001
                pass
    write_json(run_dir / "stage3_debug.json", {"attempts": attempts, "model": spec.to_dict(), "cache": cinfo})
    storage_inr = round(float(cinfo.get("storage_usd") or 0.0) * inr_per_usd(cfg), 3)
    cost = round(_cost(cfg, usage) + storage_inr, 3)
    append_log(cfg, {"episode": inputs.vid, "stage": "stage3_clips", "status": "ok" if not failed else "partial",
                     "duration_s": round(time.perf_counter() - t0, 1), "input_tokens": usage["input_tokens"],
                     "output_tokens": usage["output_tokens"] + usage["thought_tokens"], "cached_tokens": usage["cached_tokens"],
                     "cost_inr_est": cost, "note": f"{len(clips)} clips; cache storage ₹{storage_inr}; " + "; ".join(warns)[:200]})
    out = dict(facts)
    out["clips"] = {"version": VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": spec.to_dict(), "one_step": True,
                    "clips": clips, "warnings": warns, "failed_scenes": failed, "cache": cinfo,
                    "usage": {**usage, "storage_inr": storage_inr, "cost_inr": cost}}
    return out


def estimate_clips(facts: dict, cfg: dict, counter=None) -> dict:
    """The one step: the cache (video at ~tokens_per_s_video_low + the shared text) stored for the TTL and read once
    per scene call at the cached price; the prompt at the input price; answer + thinking at the output price."""
    if counter is None:
        from shotvi.segment.render import estimate_tokens
        counter = estimate_tokens
    c, r, rate = cfg["stage3_find"], cfg["rates"], inr_per_usd(cfg)
    n = len(facts["identity_pass"]["scenes"])
    cached = int(float(facts["episode"]["duration"]) * r["tokens_per_s_video_low"] + counter(shared_video_text(facts))
                 + counter(s1b.load_prompt(CLIPS_PROMPT)["system"]))
    prompt = int(counter(s1b.load_prompt(CLIPS_PROMPT)["instructions"])) + 300
    storage = cached * int(c.get("cache_ttl_s", 3600)) / 3600 * r.get("usd_per_1m_cache_storage_hour", 0.5) / 1e6
    calls = n * (cached * r["usd_per_1m_cached_input"] + prompt * r["usd_per_1m_input"]
                 + int(c.get("clips_output_tokens", 6000)) * r["usd_per_1m_output"]) / 1e6
    return {"calls": n, "cached_tokens": cached, "storage_inr": round(storage * rate, 2), "calls_inr": round(calls * rate, 2),
            "total_inr": round((storage + calls) * rate, 2)}


# ----------------------------------------------------------------------------------------------- estimates
def estimate(facts: dict, cfg: dict, counter=None) -> dict:
    """3a: one text call per scene. 3b (when moments exist): one call per scene with moments, its video once."""
    if counter is None:
        from shotvi.segment.render import estimate_tokens
        counter = estimate_tokens
    c, r, rate = cfg["stage3_find"], cfg["rates"], inr_per_usd(cfg)
    shared = int(counter(episode_text(facts)))
    prompt = int(counter("\n".join(s1b.load_prompt(MOMENTS_PROMPT).values())))
    n = len(facts["identity_pass"]["scenes"])
    a_in, a_out = n * (shared + prompt), n * int(c.get("moments_output_tokens", 1200))
    out = {"3a_calls": n, "3a_inr": round((a_in * r["usd_per_1m_input"] + a_out * r["usd_per_1m_output"]) / 1e6 * rate, 2)}
    if facts.get("moments"):
        L = facts["lines"]
        groups = {}
        for m in facts["moments"]["moments"]:
            groups.setdefault(m["scene"], []).append(region(facts, m))
        b_in = b_out = 0
        for sp in groups.values():
            lo, hi = min(x for x, _ in sp), max(y for _, y in sp)
            b_in += (L[hi].get("end", L[hi]["start"]) - L[lo]["start"]) * r["tokens_per_s_video_low"] + 6000
            b_out += len(sp) * int(c.get("shape_output_tokens", 1000)) + 3000
        out.update({"3b_calls": len(groups), "3b_inr": round((b_in * r["usd_per_1m_input"] + b_out * r["usd_per_1m_output"]) / 1e6 * rate, 2)})
    return out
