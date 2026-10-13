"""Video gate for web-series clips (additive; OFF by default: ``cfg["video_gate"]["enabled"]``).

What it does, per clip that the text pipeline would show:
  1. one Gemini video call (prompt ``ws_gate``, gemini-3.8-flash, 1 fps, default media resolution) on a wide window:
     ~40 s before the clip's first segment to ~25 s after its last (blocks merged across gaps <= 45 s). The prompt lists
     the window's transcript lines with ids (shot lines for long silent stretches), tagged BEFORE / IN CLIP / CUT /
     COLD OPEN / AFTER, with marker rows where the clip starts, jumps and ends. The model returns hook, standalone,
     payoff, overall (1-5, one-sentence reason each), a verdict keep | fix | drop and, for fix, a new start and/or end
     as LINE / SHOT IDS only (never times; code converts).
  2. fix -> code re-cuts the clip with the unchanged cut builder (``webseries.build_clip``: first-word start <= 0.3 s,
     shot snapping, reaction hold, whole lines only, chunk drops <= 45 s so no far stitch, a cold open is skipped in
     place and never replayed) and checks the guardrails (15-120 s, censored audio). Allowed: start EARLIER, end
     LATER, end EARLIER; never a later start. Then ONE re-check call on the new cut (same prompt): keep -> kept,
     anything else -> drop. No second fix. An invalid fix (later start, unknown id, guardrail) -> drop.
  3. tiers: keep -> recommended; fix-then-keep -> recommended with ``gate_fixed: true``; drop -> rejected.

CLI (gate a finished v1 run; the clips are rebuilt from the run's debug moments and must match exactly)::

    python -m shotvi.select.video_gate run runs/v1_test --out runs/v1_gate --fixes-out runs/v1_gate_fixes --dry-run
    SHOTVI_SPEND_SOURCE=v1_gate python -m shotvi.select.video_gate run runs/v1_test --out runs/v1_gate \
        --fixes-out runs/v1_gate_fixes --max-ledger-inr 1528
    python -m shotvi.select.video_gate report runs/v1_gate data/human/ratings_v1_test.csv   (free)
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import webseries as ws
from .webseries import Episode, Reject, fmt, norm_id

ROOT = ws.ROOT
GATE_VERSION = "gate/0.1"

DEFAULTS: dict = {
    "enabled": False,
    "prompt": "ws_gate",
    "model": {"provider": "gemini", "model": "gemini-3.8-flash",
              "params": {"thinking_level": "low", "max_output_tokens": 8192}},   # default media resolution
    "fps": 1.0,
    "before_s": 40.0, "after_s": 25.0,     # window around the clip
    "inner_pad_s": 5.0, "merge_gap_s": 45.0,
    "max_hold_s": 8.0,                     # a shot / event end may hold the picture at most this long after the payoff
    "event_hold_pad_s": 0.3,               # an audio-event end holds this long past the event (then to a shot cut)
    "min_s": 15.0, "max_s": 120.0,         # guardrails on a re-cut (play length)
    # cost projection per call (rupees come from tokens x pricing); output = visible + thinking
    "out_expected": 1500, "out_guard": 4000,
    "workers": 4,
}

_S = {"type": "string"}
_SCORE = {"type": "object", "properties": {"score": {"type": "integer", "minimum": 1, "maximum": 5}, "why": _S},
          "required": ["score", "why"]}
PAYOFF_KINDS = ["laugh_beat", "reveal", "quotable_line", "emotional_peak", "scene_just_ends", "question_or_demand",
                "not_performed", "mid_thought"]
GATE_SCHEMA = {
    "type": "object",
    "properties": {
        "last_beat": {"type": "object", "properties": {"line": _S, "on_screen": _S}, "required": ["line", "on_screen"]},
        "hook": _SCORE, "standalone": _SCORE, "payoff": _SCORE,
        "payoff_kind": {"type": "string", "enum": PAYOFF_KINDS},
        "overall": _SCORE,
        "verdict": {"type": "string", "enum": ["keep", "fix", "drop"]},
        "fix": {"type": "object", "properties": {"new_start": _S, "new_end": _S, "why": _S},
                "required": ["new_start", "new_end", "why"]},
    },
    "required": ["last_beat", "hook", "standalone", "payoff", "payoff_kind", "overall", "verdict", "fix"],
}
DIMS = ("hook", "standalone", "payoff", "overall")
# pipeline projection per shown clip. Measured on runs/v1_gate (2026-10-13, 30 clips, 4 episodes): ₹1.03 per call
# (13.9k input incl. ~91 video tokens/s of window, ~290 output at thinking low), 7 re-checks per 30 clips -> ₹1.28 per
# clip. Guard: a first pass and a re-check, each at 1.4x the largest input and 4k output.
GATE_INR_EXPECTED_PER_CLIP = 1.3
GATE_INR_GUARD_PER_CALL = 3.3
GATE_INR_GUARD_PER_CLIP = 2 * GATE_INR_GUARD_PER_CALL


def gate_cfg(cfg: dict | None) -> dict:
    return ws.deep_merge(DEFAULTS, (cfg or {}).get("video_gate") or {})


# ======================================================================================== windows and prompt
def gate_windows(segs: list[dict], duration: float, g: dict) -> list[tuple[float, float]]:
    """Blocks = the clip's time segments merged across gaps <= merge_gap_s; the first window opens before_s before
    the clip, the last closes after_s after it; inner block edges get inner_pad_s."""
    ps = sorted((s["start"], s["end"]) for s in segs)
    blocks = [list(ps[0])]
    for a, b in ps[1:]:
        if a - blocks[-1][1] <= g["merge_gap_s"]:
            blocks[-1][1] = max(blocks[-1][1], b)
        else:
            blocks.append([a, b])
    out = []
    for k, (a, b) in enumerate(blocks):
        out.append((round(max(0.0, a - (g["before_s"] if k == 0 else g["inner_pad_s"])), 2),
                    round(min(duration, b + (g["after_s"] if k == len(blocks) - 1 else g["inner_pad_s"])), 2)))
    return out


def line_tags(ep: Episode, rows: list[dict], clip: dict) -> dict[str, str]:
    """Tag per window line: BEFORE CLIP / IN CLIP / CUT / COLD OPEN / AFTER CLIP. Speech lines by their words (any
    played word in a scene segment = IN CLIP), other lines by their midpoint."""
    scene = [s for s in clip["time_segments"] if s["kind"] == "scene"]
    c0, c1 = scene[0]["start"], scene[-1]["end"]
    lift = set((clip.get("cold_open") or {}).get("lines") or [])
    played = set()
    for s in scene:
        for p in ws.pieces_for(ep, [s]):
            played.update(range(p["start_word"], p["end_word"] + 1))
    tags = {}
    for r in rows:
        if r["id"] in lift:
            tags[r["id"]] = "COLD OPEN"
            continue
        if r["kind"] == "speech" and r.get("words"):
            i0, i1 = r["words"]
            if any(i in played for i in range(i0, i1 + 1)):
                tags[r["id"]] = "IN CLIP"
                continue
            t = ep.words[i0]["start"]
        else:
            t = (r["start"] + r["end"]) / 2
            if any(s["start"] <= t <= s["end"] for s in scene):
                tags[r["id"]] = "IN CLIP"
                continue
        tags[r["id"]] = "BEFORE CLIP" if t < c0 else "AFTER CLIP" if t > c1 else "CUT"
    return tags


def render_window_lines(ep: Episode, ixs, clip: dict, wins: list[tuple[float, float]]) -> tuple[str, list[str]]:
    """Window lines with tags and marker rows; returns (text, ids shown in order)."""
    from .lines import fmt as lfmt
    hold = clip["end"]
    parts, shown, seen = [], [], set()
    for w0, w1 in wins:
        rows = [r for r in ixs.in_range(w0, w1) if r["id"] not in seen]
        seen.update(r["id"] for r in rows)
        tags = line_tags(ep, rows, clip)
        out, prev = [], None
        for r in rows:
            t = tags[r["id"]]
            state = "IN" if t == "IN CLIP" else t
            if state != prev:
                if state == "IN" and prev in (None, "BEFORE CLIP"):
                    out.append("==== CLIP STARTS here (first spoken word of the next line) ====")
                elif state == "IN" and prev in ("CUT", "COLD OPEN"):
                    out.append("==== CLIP RESUMES here ====")
                elif state in ("CUT", "COLD OPEN") and prev == "IN":
                    out.append("==== CUT: the clip jumps over the line(s) below ====")
                elif state == "AFTER CLIP" and prev in ("IN", "CUT", "COLD OPEN"):
                    out.append(f"==== CLIP ENDS here: the picture holds {hold['hold_s']:.1f} s after the last "
                               f"IN CLIP spoken line ====")
            prev = state
            who = {"event": "(audio event)", "shot": "(shot, no dialogue)"}.get(r["kind"], f"[{r['speaker']}]")
            out.append(f"{r['id']} [{lfmt(r['start'])}] <{t}> {who}" + (f": {r['text']}" if r.get("text") else ""))
            shown.append(r["id"])
        if prev in ("IN",):
            out.append(f"==== CLIP ENDS here: the picture holds {hold['hold_s']:.1f} s after the last line ====")
        parts.append("\n".join(out))
    return "\n... [next video window] ...\n".join(parts), shown


def play_summary(ep: Episode, clip: dict) -> str:
    """The clip in play order, by line ids."""
    out = []
    n = 1
    if clip.get("cold_open"):
        lo = clip["cold_open"]
        out.append(f"{n}. COLD OPEN ({lo['duration']:.1f} s): {', '.join(lo['lines'])}, played first; skipped in its "
                   f"place later (never replayed)")
        n += 1
    sp = [r for r in ep.lines[ep.pos[clip["start_line"]]:ep.pos[clip["end_line"]] + 1] if ep.is_speech(r)]
    lift = set((clip.get("cold_open") or {}).get("lines") or [])
    cuts = clip.get("cuts") or []
    run_first = sp[0]["id"]
    for cu in cuts:
        out.append(f"{n}. {run_first} to {cu['after_line']}")
        n += 1
        what = "cold-open line skipped here" if cu["lift_skip"] and all(x in lift for x in cu["removed_lines"]
                                                                       if ep.is_speech(ep.by_id[x])) \
            else f"cut {cu['removed_s']:.1f} s"
        rem = [x for x in cu["removed_lines"] if ep.is_speech(ep.by_id[x])]
        out.append(f"   [{what}: {', '.join(rem) if rem else 'a silent gap'} removed]")
        run_first = cu["before_line"]
    e = clip["end"]
    tail = f"; the picture then holds {e['hold_s']:.1f} s after the last line"
    if e.get("reaction"):
        tail += f" (plays the short reaction {e['reaction']['line']})"
    out.append(f"{n}. {run_first} to {clip['end_line']}{tail}")
    return "\n".join(out)


def gate_prompt(ep: Episode, ixs, clip: dict, cid: str, purpose: str, g: dict):
    from .lines import ASR_LEGEND
    from .prompts import load_prompt
    p = load_prompt(g["prompt"])
    wins = gate_windows(clip["time_segments"], ep.duration, g)
    lines_txt, shown = render_window_lines(ep, ixs, clip, wins)
    user = p.render(clip_id=cid, purpose=purpose or "standalone", duration=f"{clip['video_duration']:.1f}",
                    play=play_summary(ep, clip), n_windows=len(wins), asr_legend=ASR_LEGEND, lines=lines_txt)
    return p.system, user, wins, shown


# ======================================================================================== fix -> re-cut
def build_args(m: dict, clip: dict) -> dict:
    """How the shown clip was built from its moment: no_lift when it plays no cold open (a lift the builder or the
    review rejected must not come back unreviewed), skip_drops = the moment's drops the clip does not use."""
    return {"no_lift": not bool(clip.get("cold_open")),
            "skip_drops": set(range(len(m.get("drops") or []))) - set(clip.get("drops_used") or [])}


def resolve_fix(ep: Episode, ixs, clip: dict, fix: dict, shown: list[str]) -> dict:
    """Model fix (ids) -> {start_line, end_line, hold_to, changes}. Raises Reject for a fix the rules forbid."""
    shown_set = set(shown)
    new_s, new_e = norm_id(fix.get("new_start")), norm_id(fix.get("new_end"))
    if not new_s and not new_e:
        raise Reject("fix without a new start or end")
    cur_s, cur_e = clip["start_line"], clip["end_line"]
    out = {"start_line": cur_s, "end_line": cur_e, "hold_to": None, "changes": []}

    def row(i):
        if i not in shown_set:
            raise Reject(f"{i} is not a line of the window")
        r = ixs.by_id.get(i)
        if r is None:
            raise Reject(f"unknown id {i}")
        return r

    if new_s and new_s != cur_s:
        r = row(new_s)
        if r["kind"] == "speech" and ep.is_speech(ep.by_id.get(r["id"])):
            sl = ep.by_id[r["id"]]
        else:   # a shot / event: the clip still opens on the first spoken word after it
            sl = next((x for x in ep.speech if ep.w0(x) >= r["start"] - 0.05), None)
            if sl is None:
                raise Reject(f"no spoken line after {new_s}")
        if ep.pos[sl["id"]] > ep.pos[cur_s]:
            raise Reject(f"start later ({cur_s} -> {sl['id']}) is not allowed")
        if sl["id"] != cur_s:
            out["start_line"] = sl["id"]
            out["changes"].append("start_earlier")
    if new_e and new_e != cur_e:
        r = row(new_e)
        if r["kind"] == "speech" and ep.is_speech(ep.by_id.get(r["id"])):
            el = ep.by_id[r["id"]]
        else:   # shot / audio event: end on the last spoken line before its end, hold the picture to its end
            el = None
            for x in ep.speech:
                if ep.w1(x) <= r["end"] + 0.05:
                    el = x
                else:
                    break
            if el is None:
                raise Reject(f"no spoken line before {new_e}")
            hold = r["end"] if r["kind"] == "shot" else r["end"] + DEFAULTS["event_hold_pad_s"]
            out["hold_to"] = {"t": round(hold, 3), "from": r["id"], "kind": r["kind"]}
        if el["id"] != cur_e or out["hold_to"]:
            pe, pc = ep.pos[el["id"]], ep.pos[cur_e]
            later = pe > pc or (pe == pc and out["hold_to"]["t"] > clip["time_segments"][-1]["end"])
            out["end_line"] = el["id"]
            out["changes"].append("end_later" if later else "end_earlier")
    if ep.pos[out["end_line"]] <= ep.pos[out["start_line"]]:
        raise Reject(f"end {out['end_line']} is not after start {out['start_line']}")
    if not out["changes"]:
        raise Reject("fix changes nothing")
    return out


def recut(ep: Episode, m: dict, clip: dict, rf: dict, cut_cfg: dict, g: dict, guard_cfg: dict) -> dict:
    """Rebuild the clip from its moment with the new start / end line (the cut builder applies every cutting rule),
    then the optional hold to a shot / event end, then the guardrails. Raises Reject."""
    m2 = {**copy.deepcopy(m), "start_line": rf["start_line"], "end_line": rf["end_line"]}
    ba = build_args(m, clip)
    c = ws.build_clip(ep, m2, cut_cfg, no_lift=ba["no_lift"], skip_drops=ba["skip_drops"])
    if c["start_line"] != rf["start_line"] or c["end_line"] != rf["end_line"]:
        raise Reject(f"builder moved the edges ({c['start_line']}..{c['end_line']})")
    if rf.get("hold_to"):
        e = c["end"]
        nx = e.get("next_speech")
        last = ep.by_id[c["end_line"]]["words"][1]
        nx2 = ep.next_start(last)
        lim = e["payoff_end"] + g["max_hold_s"]
        if nx2 is not None:
            lim = min(lim, nx2 - cut_cfg["next_speech_margin_s"])
        lim = min(lim, ep.duration)
        t = rf["hold_to"]["t"]
        on_cut = False
        if rf["hold_to"]["kind"] == "shot":
            cs = ep.cuts_in(t - 0.1, t + 0.1)
            on_cut = bool(cs)
            t = (cs[0] if cs else t) - cut_cfg["lift_cut_eps_s"]
        else:
            cs = ep.cuts_in(t, min(t + cut_cfg["snap_s"], lim + cut_cfg["lift_cut_eps_s"]))
            if cs:
                t, on_cut = cs[0] - cut_cfg["lift_cut_eps_s"], True
        if t > lim:
            t, on_cut = lim, False
        if t < e["payoff_end"] + cut_cfg["hold_short_min_s"]:
            raise Reject("the shot / event end leaves no hold after the last line")
        segs = c["time_segments"]
        segs[-1]["end"] = round(t, 3)
        c["end"] = {**e, "end": round(t, 3), "hold_s": round(t - e["payoff_end"], 2), "on_shot_cut": on_cut,
                    "rule": "gate_hold", "gate_hold_from": rf["hold_to"]["from"], "next_speech": nx}
        c["pieces"] = ws.pieces_for(ep, segs)
        c["video_duration"] = round(sum(s["end"] - s["start"] for s in segs), 2)
    d = c["video_duration"]
    if d < g["min_s"]:
        raise Reject(f"re-cut plays {d:.1f} s < {g['min_s']:.0f} s")
    if d > g["max_s"]:
        raise Reject(f"re-cut plays {d:.1f} s > {g['max_s']:.0f} s")
    rr = ws.hard_rules(ep, c, guard_cfg)
    if rr:
        raise Reject("; ".join(rr))
    scene = [s for s in c["time_segments"] if s["kind"] == "scene"]
    for a, b in zip(scene, scene[1:]):
        if b["start"] - a["end"] > cut_cfg["max_drop_s"] + 1.0:
            raise Reject("re-cut would stitch scenes far apart")
    return c


# ======================================================================================== the gate
def scores_of(obj: dict) -> dict:
    return {k: int(obj[k]["score"]) for k in DIMS}


def verdict_ok(obj: dict) -> str:
    """The model's verdict, made consistent with the mechanical keep rule (a keep that fails it is a drop; the model
    may still ask for a fix)."""
    v = obj["verdict"]
    sc = scores_of(obj)
    if v == "keep" and not (sc["payoff"] >= 4 and sc["overall"] >= 4 and sc["hook"] >= 3 and sc["standalone"] >= 3):
        return "drop"
    return v


class GateRunner:
    """Calls + accounting. ``llm``: shotvi.llm.LLMClient; ``media_base``: {file_uri, mime_type, key} of the episode
    video (None in offline tests: the fake client ignores media)."""

    def __init__(self, ep: Episode, ixs, cfg: dict, llm, media_base: dict | None, guard=None, log=print):
        self.ep, self.ixs, self.cfg, self.llm, self.media_base = ep, ixs, cfg, llm, media_base
        self.g = gate_cfg(cfg)
        self.guard, self.log = guard, log
        from shotvi.llm import ModelSpec
        self.spec = ModelSpec.from_dict(self.g["model"])
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def call(self, clip: dict, cid: str, purpose: str, stage: str) -> dict:
        system, user, wins, shown = gate_prompt(self.ep, self.ixs, clip, cid, purpose, self.g)
        media = [{**self.media_base, "start_s": a, "end_s": b, "fps": self.g["fps"]} for a, b in wins] \
            if self.media_base else None
        # fresh vs cached is decided before the call (the ledger is shared by worker threads, so a slice of it is not
        # this call's); the cost is this call's own usage (first attempt; a schema retry would add to the ledger only)
        fresh = not self._cached(system, user, media)
        obj, raw = self.llm.generate_json("ws_gate", self.spec, system, user, GATE_SCHEMA, media=media, want_raw=True,
                                          meta={"video": self.ep.video_id, "clip": cid, "stage": stage})
        from shotvi.llm.pricing import cost_usd
        usd = cost_usd(self.spec.model, raw.input_tokens, raw.output_tokens + raw.thought_tokens, None,
                       raw.cached_tokens) or 0.0
        audio = int((raw.prompt_modalities or {}).get("AUDIO", 0))
        if audio:
            # audio input is billed at 2x the text rate on Gemini Flash (pricing.PRICES is text-only); so far video
            # windows report one VIDEO modality (audio included), so this has not triggered
            usd += audio * 0.75 / 1e6
            if fresh:
                from shotvi.llm import spend
                spend.record(self.spec.model, audio, 0,
                             source=os.environ.get("SHOTVI_SPEND_SOURCE", "gate") + "-audio-surcharge")
        rec = {"clip": cid, "stage": stage, "cached": not fresh, "windows": wins,
               "input_tokens": getattr(raw, "input_tokens", 0),
               "output_tokens": getattr(raw, "output_tokens", 0) + getattr(raw, "thought_tokens", 0),
               "modalities": getattr(raw, "prompt_modalities", None),
               # full cost of the call (what an uncached run pays); billed only when not cached
               "usd": round(usd, 5), "inr": round(ws.inr(usd), 3)}
        with self._lock:
            self.calls.append(rec)
        return {"obj": obj, "shown": shown, "windows": wins, "usage": rec}

    def _cached(self, system: str, user: str, media) -> bool:
        from shotvi.llm.client import _media_key
        mkey = json.dumps([_media_key(m) for m in media or []], sort_keys=True) if media else ""
        return ws.is_cached(self.llm, self.spec, system, user + mkey, GATE_SCHEMA)

    def gate(self, x: dict) -> dict:
        """Gate one shown clip x = {id, moment, clip, ...}. Returns the gate record; x["clip"] is replaced by the
        re-cut when a fix is kept."""
        ep, cid, m = self.ep, x["id"], x["moment"]
        purpose = m.get("purpose", "standalone")
        c = x["clip"]
        r1 = self.call(c, cid, purpose, "first")
        o1 = r1["obj"]
        v1 = verdict_ok(o1)
        rec = {"version": GATE_VERSION, "first": {"scores": scores_of(o1), "verdict": o1["verdict"],
                                                  "verdict_applied": v1, "payoff_kind": o1["payoff_kind"],
                                                  "why": {k: o1[k]["why"] for k in DIMS},
                                                  "last_beat": o1["last_beat"], "fix": o1["fix"],
                                                  "usage": r1["usage"]},
               "fix_applied": None, "recheck": None}
        final = v1
        if v1 == "fix":
            try:
                rf = resolve_fix(ep, self.ixs, c, o1["fix"], r1["shown"])
                c2 = recut(ep, m, c, rf, self.cfg["cut"], self.g, self.cfg)
            except Reject as e:
                rec["fix_rejected"] = str(e)
                final = "drop"
                self.log(f"  {cid}: fix rejected: {e}")
            else:
                old = [(s["start"], s["end"], s["kind"]) for s in c["time_segments"]]
                new = [(s["start"], s["end"], s["kind"]) for s in c2["time_segments"]]
                rec["fix_applied"] = {
                    "changes": rf["changes"], "why": o1["fix"]["why"],
                    "old": {"start_line": c["start_line"], "end_line": c["end_line"], "segments": old,
                            "plays_s": c["video_duration"]},
                    "new": {"start_line": c2["start_line"], "end_line": c2["end_line"], "segments": new,
                            "plays_s": c2["video_duration"], **({"hold_to": rf["hold_to"]} if rf["hold_to"] else {})},
                    "cold_open_lost": bool(c.get("cold_open")) and not c2.get("cold_open")}
                r2 = self.call(c2, cid, purpose, "recheck")
                o2 = r2["obj"]
                v2 = verdict_ok(o2)
                rec["recheck"] = {"scores": scores_of(o2), "verdict": o2["verdict"], "verdict_applied": v2,
                                  "payoff_kind": o2["payoff_kind"], "why": {k: o2[k]["why"] for k in DIMS},
                                  "last_beat": o2["last_beat"], "fix_ignored": o2["fix"] if v2 == "fix" else None,
                                  "usage": r2["usage"]}
                final = "keep" if v2 == "keep" else "drop"
                x["clip_before_gate"] = c
                x["gate_clip"] = c2
                if final == "keep":
                    x["clip"] = c2
        rec["final"] = final
        rec["gate_fixed"] = final == "keep" and rec["fix_applied"] is not None
        rec["tier"] = "recommended" if final == "keep" else "rejected"
        x["gate"] = rec
        return rec


def episode_media(vid: str, upload: bool = True) -> dict:
    """Media part base of the episode video. The cache identity is "<id>.mp4:<size>" (never the expiring upload
    uri), so a cache-only replay needs no upload."""
    mp4 = ROOT / "scribe_dataset" / vid / f"{vid}.mp4"
    if not upload:
        return {"file_uri": "", "mime_type": "video/mp4", "key": f"{vid}.mp4:{mp4.stat().st_size}"}
    from shotvi.llm.gemini import upload_file
    f = upload_file(str(mp4))
    return {"file_uri": f["uri"], "mime_type": "video/mp4", "key": f"{vid}.mp4:{f['size']}"}


def gate_shown(ep: Episode, shown: list, rejected: list, cfg: dict, llm, guard=None, log=print,
               media_base: dict | None = None, ixs=None) -> list:
    """Pipeline hook (cfg video_gate.enabled): gate every shown clip; keep -> recommended (fixed ones flagged),
    drop -> appended to ``rejected``. Returns the new shown list (gate order: recommended by gate overall)."""
    from .lines import LineIndex
    if ixs is None:
        ixs = LineIndex(ep.video_id, shot_lines=True, asr_fix=True)
    if media_base is None:
        media_base = episode_media(ep.video_id, upload=not getattr(llm, "cache_only", False))
    runner = GateRunner(ep, ixs, cfg, llm, media_base, guard, log)
    out = []
    for x in shown:
        rec = runner.gate(x)
        log(f"  {x['id']}: gate {rec['first']['verdict_applied']} -> {rec['final']}")
        if rec["final"] == "keep":
            x["tier"] = "recommended"
            out.append(x)
        else:
            rejected.append({"id": x["id"], "moment": x["moment"], "clip": x["clip"], "scores": x.get("scores"),
                             "tier": "rejected", "gate": rec,
                             "reject_reasons": [gate_reason(rec)]})
    out.sort(key=lambda x: (-gate_final_scores(x["gate"])["overall"], -gate_final_scores(x["gate"])["payoff"],
                            x.get("_disc", 0)))
    return out


def gate_final_scores(rec: dict) -> dict:
    return (rec.get("recheck") or rec["first"])["scores"]


def gate_reason(rec: dict) -> str:
    f = rec["first"]
    s = gate_final_scores(rec)
    base = (f"G1: video gate drop (first pass {f['verdict_applied']}; payoff {s['payoff']}, overall {s['overall']}: "
            f"{ws.one_line(f['why']['payoff'], 160)})")
    if rec.get("fix_rejected"):
        base += f"; fix rejected: {rec['fix_rejected']}"
    elif rec.get("recheck"):
        base += f"; re-check after fix: {rec['recheck']['verdict_applied']}"
    return base


# ======================================================================================== run documents
def gate_doc_fields(rec: dict) -> dict:
    return {"gate": rec, "gate_fixed": bool(rec.get("gate_fixed")), "gate_verdict": rec["final"],
            "gate_scores": gate_final_scores(rec)}


def proposal_with_clip(ep: Episode, p: dict, c: dict, cfg: dict) -> dict:
    """A copy of a proposal doc with its cut replaced by clip c (pieces, segments, edges, end hold, cuts)."""
    q = copy.deepcopy(p)
    core = ws.core_fields(ep, c["pieces"], cfg["guardrails"]["skip_s"])
    q.update(core)
    q.update({"pieces": c["pieces"], "time_segments": [{"start": s["start"], "end": s["end"], "kind": s["kind"]}
                                                        for s in c["time_segments"]],
              "video_duration": c["video_duration"], "start_line": c["start_line"], "end_line": c["end_line"],
              "end_hold": c["end"], "cold_open": bool(c.get("cold_open")), "cold_open_detail": c.get("cold_open"),
              "chunk_drops": [{"after_line": cu["after_line"], "before_line": cu["before_line"],
                               "removed_lines": cu["removed_lines"], "lift_skip": cu["lift_skip"],
                               "start": cu["start"], "end": cu["end"], "removed_s": cu["removed_s"],
                               "why": cu["why"]} for cu in c["cuts"]],
              "build_warnings": c["warnings"]})
    line_of_word = {r["words"][0]: r for r in ep.lines if ep.is_speech(r)}
    q["hook_line"] = ws.hook_line(ep, c["pieces"], line_of_word)
    return q


def rebuild_shown(ep: Episode, doc: dict, dbg: dict, cfg: dict) -> list[dict]:
    """The shown clips of a finished run as gate inputs {id, moment, clip, proposal}; each rebuild must reproduce the
    run's time segments exactly (else raise: the gate must judge the cut the founder rated)."""
    ms = dbg["discover"]["moments"]
    out = []
    for p in doc["proposals"]:
        m = ms[int(p["id"].lstrip("w")) - 1]
        c0 = dbg["clips"][p["id"]]
        ba = build_args(m, c0)
        c = ws.build_clip(ep, m, cfg["cut"], no_lift=ba["no_lift"], skip_drops=ba["skip_drops"])
        a = [(round(s["start"], 3), round(s["end"], 3)) for s in c["time_segments"]]
        b = [(round(s["start"], 3), round(s["end"], 3)) for s in p["time_segments"]]
        if a != b:
            raise RuntimeError(f"{doc['video_id']} {p['id']}: rebuild differs from the run ({a} vs {b})")
        out.append({"id": p["id"], "moment": m, "clip": c, "proposal": p, "scores": p["scores"],
                    "_disc": int(p["id"].lstrip("w"))})
    return out


def gated_run_doc(ep: Episode, doc: dict, xs: list[dict], cfg: dict, runner: GateRunner, run_id: str) -> dict:
    """runs/<gate>/<id>.json: the source run with gate results; keep -> recommended (fixed ones carry the new cut and
    gate_fixed), drop -> rejected (the original cut, reason G1)."""
    out = copy.deepcopy(doc)
    props, rej = [], []
    for x in xs:
        p, rec = x["proposal"], x["gate"]
        q = proposal_with_clip(ep, p, x["clip"], cfg) if rec["gate_fixed"] else copy.deepcopy(p)
        q.update(gate_doc_fields(rec))
        q["tier_before_gate"] = p["tier"]
        if rec["final"] == "keep":
            q["tier"] = "recommended"
            props.append(q)
        else:
            q["tier"] = "rejected"
            q["reject_reasons"] = [gate_reason(rec)]
            rej.append(q)
    props.sort(key=lambda q: (-q["gate_scores"]["overall"], -q["gate_scores"]["payoff"], q["rank"]))
    for k, q in enumerate(props):
        q["rank_before_gate"] = q["rank"]
        q["rank"] = k + 1
    out["proposals"] = props
    out["rejected"] = rej + out.get("rejected", [])
    out["run_id"] = run_id
    out["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    th = ws.deep_merge(ws.DEFAULTS, doc["pipeline"]["config"])["thresholds"]
    out["episode_verdict"] = ws.episode_verdict(props, th)
    from .prompts import load_prompt
    calls = [c for c in runner.calls]
    out["video_gate"] = {"version": GATE_VERSION, "source_run": doc.get("run_id"),
                         "prompt": {"file": runner.g["prompt"], "version": load_prompt(runner.g["prompt"]).tag},
                         "model": runner.g["model"], "fps": runner.g["fps"],
                         "window": {"before_s": runner.g["before_s"], "after_s": runner.g["after_s"]},
                         "n_gated": len(xs), "n_keep": sum(x["gate"]["final"] == "keep" for x in xs),
                         "n_fixed_kept": sum(bool(x["gate"]["gate_fixed"]) for x in xs),
                         "cost": {"calls": len(calls), "fresh_calls": sum(not c["cached"] for c in calls),
                                  "input_tokens": sum(c["input_tokens"] for c in calls),
                                  "output_tokens": sum(c["output_tokens"] for c in calls),
                                  "usd": round(sum(c["usd"] for c in calls), 5),
                                  "inr": round(sum(c["inr"] for c in calls), 2),
                                  "inr_billed_this_run": round(sum(c["inr"] for c in calls if not c["cached"]), 2),
                                  "note": "usd / inr = full cost of the calls (an uncached production run)"},
                         "calls": calls}
    out["cutoff"] = {**out.get("cutoff", {}), "n_shown": len(props), "n_recommended": len(props),
                     "n_worth_a_look": 0, "n_rejected": len(out["rejected"])}
    return out


def fixes_doc(ep: Episode, doc: dict, xs: list[dict], cfg: dict, run_id: str) -> dict | None:
    """Only the re-cut clips (whatever the re-check said), for founder review; rank: re-check keep first."""
    fixed = [x for x in xs if x["gate"].get("fix_applied")]
    if not fixed:
        return None
    props = []
    for x in fixed:
        rec = x["gate"]
        q = proposal_with_clip(ep, x["proposal"], x["gate_clip"], cfg)
        q.update(gate_doc_fields(rec))
        fa = rec["fix_applied"]
        # schema: why_it_works <= 200 characters; the full record is in q["gate"]
        q["why_it_works"] = ws.one_line(
            f"RE-CUT {', '.join(fa['changes'])} {fa['old']['plays_s']:.0f}->{fa['new']['plays_s']:.0f} s, re-check "
            f"{rec['recheck']['verdict_applied']}: {fa['why']}", 200)
        q["tier"] = "recommended" if rec["final"] == "keep" else "worth_a_look"
        props.append(q)
    props.sort(key=lambda q: (q["tier"] != "recommended", q["rank"]))
    for k, q in enumerate(props):
        q["rank_in_source"] = q["rank"]
        q["rank"] = k + 1
    out = copy.deepcopy(doc)
    out.update({"run_id": run_id, "proposals": props, "rejected": [],
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    out["episode_verdict"] = {"verdict": "review", "strong_clips": sum(q["tier"] == "recommended" for q in props),
                              "worth_a_look": sum(q["tier"] != "recommended" for q in props),
                              "note": "Video-gate re-cuts only (not rated yet). recommended = the re-check kept it."}
    return out


def gate_md(doc: dict, xs: list[dict]) -> str:
    vg = doc["video_gate"]
    out = [f"# {doc['video_id']} video gate ({doc['run_id']}, {vg['prompt']['file']} {vg['prompt']['version']})\n",
           f"Gated {vg['n_gated']}: keep {vg['n_keep']} (of which re-cut {vg['n_fixed_kept']}), drop "
           f"{vg['n_gated'] - vg['n_keep']}. Cost ₹{vg['cost']['inr']:.2f} ({vg['cost']['calls']} calls, "
           f"{vg['cost']['input_tokens']} in / {vg['cost']['output_tokens']} out tokens).\n"]
    for x in xs:
        rec, p = x["gate"], x["proposal"]
        f = rec["first"]
        s = f["scores"]
        out.append(f"\n## {x['id']} {p['title']} ({p['tier']} before) -> {rec['final'].upper()}"
                   + (" (re-cut)" if rec["gate_fixed"] else ""))
        out.append(f"- first pass: {f['verdict']} (applied {f['verdict_applied']}), payoff kind {f['payoff_kind']}; "
                   f"hook {s['hook']}, standalone {s['standalone']}, payoff {s['payoff']}, overall {s['overall']}")
        for k in DIMS:
            out.append(f"  - {k}: {f['why'][k]}")
        out.append(f"  - last beat {f['last_beat']['line']}: {f['last_beat']['on_screen']}")
        if rec.get("fix_rejected"):
            out.append(f"- fix asked {f['fix']} -> rejected: {rec['fix_rejected']}")
        if rec.get("fix_applied"):
            fa = rec["fix_applied"]
            seg = lambda ss: " + ".join(f"{fmt(a)}-{fmt(b)}" for a, b, _ in ss)  # noqa: E731
            out.append(f"- fix ({', '.join(fa['changes'])}): {fa['old']['start_line']}..{fa['old']['end_line']} "
                       f"[{seg(fa['old']['segments'])}, {fa['old']['plays_s']} s] -> {fa['new']['start_line']}.."
                       f"{fa['new']['end_line']} [{seg(fa['new']['segments'])}, {fa['new']['plays_s']} s]"
                       + (" (cold open lost)" if fa["cold_open_lost"] else ""))
            out.append(f"  - why: {fa['why']}")
            r = rec["recheck"]
            out.append(f"- re-check: {r['verdict']} (applied {r['verdict_applied']}); "
                       + ", ".join(f"{k} {r['scores'][k]}" for k in DIMS))
            out.append(f"  - payoff: {r['why']['payoff']}")
    return "\n".join(out) + "\n"


# ======================================================================================== CLI: gate a finished run
# Video input measured on generateContent (gemini-3.8-flash, 1 fps): ~91 tokens per second of window, the same at the
# default and the low media resolution (map / memory / clip calls, 2026-10-05..13; reported as VIDEO, audio included).
# countTokens does not count Files-API video frames, so the projection adds them per second.
VIDEO_TOK_PER_S = 92


def project_calls(xs_by_vid: dict, g: dict) -> dict:
    """Free projection: countTokens (text part) on every first-pass request + VIDEO_TOK_PER_S x window seconds, plus an
    expected number of re-checks (fix rate assumed 40%; worst case: every clip re-checked)."""
    from shotvi.llm import ModelSpec
    from shotvi.llm.gemini import GeminiProvider
    spec = ModelSpec.from_dict(g["model"])
    prov = GeminiProvider()
    rows = []
    for vid, (ep, ixs, xs, _media_base) in xs_by_vid.items():
        for x in xs:
            system, user, wins, _ = gate_prompt(ep, ixs, x["clip"], x["id"], x["moment"].get("purpose", ""), g)
            n_text = prov.count_tokens_request(spec, system, user, None)
            win_s = sum(b - a for a, b in wins)
            rows.append({"vid": vid, "id": x["id"], "text": n_text, "window_s": round(win_s, 1),
                         "in": int(n_text + VIDEO_TOK_PER_S * win_s * g["fps"])})
    n_first = len(rows)
    in_mean = sum(r["in"] for r in rows) / max(1, n_first)
    exp1 = sum(ws.inr(ws.project_usd(r["in"], g["out_expected"])) for r in rows)
    grd1 = sum(ws.inr(ws.project_usd(r["in"], g["out_guard"])) for r in rows)
    n_re = round(0.4 * n_first)
    exp2 = n_re * ws.inr(ws.project_usd(int(in_mean * 1.15), g["out_expected"]))
    grd2 = n_first * ws.inr(ws.project_usd(int(in_mean * 1.4), g["out_guard"]))
    return {"rows": rows, "n_first": n_first, "in_mean": round(in_mean), "inr_expected": round(exp1 + exp2, 2),
            "inr_guard_worst": round(grd1 + grd2, 2), "recheck_assumed": n_re,
            "inr_per_call_guard": round(ws.inr(ws.project_usd(int(max(r["in"] for r in rows) * 1.4),
                                                              g["out_guard"])), 2) if rows else 0.0}


def main_run(argv=None):
    ap = argparse.ArgumentParser(description="video gate over a finished web-series run")
    ap.add_argument("run_dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixes-out", default=None)
    ap.add_argument("--only", default=None, help="video ids, comma separated")
    ap.add_argument("--clips", default=None, help="vid:wNN,... (subset, e.g. a token test)")
    ap.add_argument("--dry-run", action="store_true", help="countTokens projection only (free)")
    ap.add_argument("--max-ledger-inr", type=float, default=None)
    ap.add_argument("--config", default=None, help="JSON deep-merged over the gate DEFAULTS")
    a = ap.parse_args(argv)
    from dotenv import load_dotenv
    from shotvi.llm import Ledger, LLMClient
    from shotvi.llm.spend import total_inr
    from .lines import LineIndex
    os.chdir(ROOT)
    load_dotenv(ROOT / ".env")
    gcfg = json.loads(Path(a.config).read_text(encoding="utf-8")) if a.config else {}
    run = Path(a.run_dir)
    only = set(a.only.split(",")) if a.only else None
    pick = None
    if a.clips:
        pick = {}
        for t in a.clips.split(","):
            v, c = t.split(":")
            pick.setdefault(v, set()).add(c)
    xs_by_vid = {}
    for f in sorted(run.glob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        if doc.get("kind") != "proposals":
            continue
        vid = doc["video_id"]
        if (only and vid not in only) or (pick is not None and vid not in pick):
            continue
        dbg = json.loads((run / "debug" / f"{vid}.json").read_text(encoding="utf-8"))
        cfg = ws.deep_merge(ws.DEFAULTS, doc["pipeline"]["config"])
        cfg["video_gate"] = ws.deep_merge(DEFAULTS, gcfg)
        ep, _, _, _ = ws.load_episode(vid)
        ixs = LineIndex(vid, shot_lines=True, asr_fix=True)
        xs = rebuild_shown(ep, doc, dbg, cfg)
        if pick is not None:
            xs = [x for x in xs if x["id"] in pick[vid]]
        media_base = episode_media(vid, upload=not a.dry_run)
        xs_by_vid[vid] = (ep, ixs, xs, media_base, doc, cfg)
    g = ws.deep_merge(DEFAULTS, gcfg)
    proj = project_calls({v: t[:4] for v, t in xs_by_vid.items()}, g)
    led0 = total_inr()
    for r in proj["rows"]:
        print(f"  {r['vid']} {r['id']}: window {r['window_s']} s, text {r['text']} + video -> input ~{r['in']} tokens")
    print(f"PROJECTION: {proj['n_first']} first-pass calls (mean input {proj['in_mean']} tokens) + ~"
          f"{proj['recheck_assumed']} re-checks: expected ₹{proj['inr_expected']:.2f}, worst ₹"
          f"{proj['inr_guard_worst']:.2f} (guard per call ₹{proj['inr_per_call_guard']:.2f}); ledger ₹{led0:.2f}"
          + (f", limit ₹{a.max_ledger_inr:.2f}" if a.max_ledger_inr is not None else ""))
    if a.dry_run:
        return 0
    guard = ws.Guard(a.max_ledger_inr)
    per_call = proj["inr_per_call_guard"]
    workers = int(g["workers"])
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tot = 0.0
    for vid, (ep, ixs, xs, media_base, doc, cfg) in xs_by_vid.items():
        llm = LLMClient(Ledger(max_usd=None), cache_dir="runs/cache")
        # every billed attempt checks the project ledger with room for the calls in flight
        llm.before_call = lambda role, _spec, v=vid: guard.check(f"{v} {role}", per_call * workers)
        runner = GateRunner(ep, ixs, cfg, llm, media_base, guard, print)

        def one(x):
            try:
                rec = runner.gate(x)
                print(f"{vid} {x['id']}: {rec['first']['verdict_applied']} -> {rec['final']} "
                      f"(payoff {rec['first']['scores']['payoff']}, overall {rec['first']['scores']['overall']})",
                      flush=True)
            except ws.BudgetStop as e:
                print(f"STOP {vid} {x['id']}: {e}", flush=True)
                x["gate_error"] = str(e)
            except Exception as e:   # noqa: BLE001 (one failed clip must not lose the others' paid answers)
                print(f"ERROR {vid} {x['id']}: {e}", flush=True)
                x["gate_error"] = str(e)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, xs))
        done = [x for x in xs if "gate" in x]
        if len(done) < len(xs):
            print(f"{vid}: {len(xs) - len(done)} clip(s) not gated: {[x['id'] for x in xs if 'gate' not in x]}")
        if not done:
            continue
        gdoc = gated_run_doc(ep, doc, done, cfg, runner, out_dir.name)
        (out_dir / f"{vid}.json").write_text(json.dumps(gdoc, ensure_ascii=False, indent=1, default=list),
                                             encoding="utf-8")
        (out_dir / f"{vid}.gate.md").write_text(gate_md(gdoc, done), encoding="utf-8")
        tot += gdoc["video_gate"]["cost"]["inr"]
        if a.fixes_out:
            fd = fixes_doc(ep, doc, done, cfg, Path(a.fixes_out).name)
            if fd:
                Path(a.fixes_out).mkdir(parents=True, exist_ok=True)
                (Path(a.fixes_out) / f"{vid}.json").write_text(json.dumps(fd, ensure_ascii=False, indent=1,
                                                                          default=list), encoding="utf-8")
        print(f"{vid}: keep {gdoc['video_gate']['n_keep']}/{len(done)}, ₹{gdoc['video_gate']['cost']['inr']:.2f}")
    print(f"gate total ₹{tot:.2f}; ledger now ₹{total_inr():.2f} (was ₹{led0:.2f})")
    return 0


# ======================================================================================== CLI: separation report (free)
def spearman(a: list[float], b: list[float]) -> float | None:
    def ranks(v):
        idx = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[idx[j + 1]] == v[idx[i]]:
                j += 1
            for k in range(i, j + 1):
                r[idx[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    if len(a) < 3:
        return None
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else None


def main_report(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("gate_dir")
    ap.add_argument("ratings")
    a = ap.parse_args(argv)
    os.chdir(ROOT)
    rat = {(r["video_id"], r["clip_id"]): r for r in csv.DictReader(open(a.ratings, encoding="utf-8-sig"))}
    rows = []
    for f in sorted(Path(a.gate_dir).glob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        for q in doc["proposals"] + doc["rejected"]:
            if "gate" not in q:
                continue
            r = rat.get((doc["video_id"], q["id"]))
            if not r:
                continue
            g1 = q["gate"]["first"]
            rows.append({"vid": doc["video_id"], "id": q["id"], "tier": q.get("tier_before_gate"),
                         "founder": r["would_post"], "f_overall": float(r["overall_1to5"]),
                         "f_payoff": float(r["payoff_1to5"]), "v1": g1["verdict_applied"],
                         "g_overall": g1["scores"]["overall"], "g_payoff": g1["scores"]["payoff"],
                         "final": q["gate"]["final"], "fixed": bool(q["gate"].get("fix_applied")),
                         "pipe_overall": (q.get("scores") or {}).get("overall")})
    print(f"{len(rows)} rated clips gated\n")
    print("first pass (original cut) x founder would_post")
    print(f"{'':6}" + "".join(f"{c:>7}" for c in ("yes", "maybe", "no", "total")))
    for v in ("keep", "fix", "drop"):
        cnt = [sum(1 for r in rows if r["v1"] == v and r["founder"] == c) for c in ("yes", "maybe", "no")]
        print(f"{v:6}" + "".join(f"{n:>7}" for n in cnt) + f"{sum(cnt):>7}")
    cnt = [sum(1 for r in rows if r["founder"] == c) for c in ("yes", "maybe", "no")]
    print(f"{'total':6}" + "".join(f"{n:>7}" for n in cnt) + f"{sum(cnt):>7}")
    sp = spearman([r["g_overall"] for r in rows], [r["f_overall"] for r in rows])
    spp = spearman([r["g_payoff"] for r in rows], [r["f_payoff"] for r in rows])
    spt = spearman([r["pipe_overall"] for r in rows if r["pipe_overall"] is not None],
                   [r["f_overall"] for r in rows if r["pipe_overall"] is not None])
    print(f"\nSpearman gate overall vs founder overall: {sp:.2f}" if sp is not None else "")
    print(f"Spearman gate payoff vs founder payoff:   {spp:.2f}" if spp is not None else "")
    print(f"(text review overall vs founder overall:  {spt:.2f})" if spt is not None else "")
    for lab, sel in (("gate keep only (first pass)", lambda r: r["v1"] == "keep"),
                     ("gate final keep (incl. re-cut keeps, as originally rated)", lambda r: r["final"] == "keep"),
                     ("text pipeline recommended", lambda r: r["tier"] == "recommended"),
                     ("text pipeline shown (all)", lambda r: True)):
        s = [r for r in rows if sel(r)]
        print(f"{lab}: {len(s)} clips -> yes {sum(r['founder'] == 'yes' for r in s)} / maybe "
              f"{sum(r['founder'] == 'maybe' for r in s)} / no {sum(r['founder'] == 'no' for r in s)}")
    print("\nper clip: vid id founder(f_overall/f_payoff) | gate first verdict (overall/payoff) -> final")
    for r in sorted(rows, key=lambda r: (r["vid"], r["id"])):
        print(f"  {r['vid']} {r['id']} {r['founder']:5} ({r['f_overall']:.0f}/{r['f_payoff']:.0f}) | {r['v1']:4} "
              f"({r['g_overall']}/{r['g_payoff']}) -> {r['final']}{' (re-cut)' if r['fixed'] else ''}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "run":
        sys.exit(main_run(sys.argv[2:]))
    elif cmd == "report":
        sys.exit(main_report(sys.argv[2:]))
    else:
        print(__doc__)
        sys.exit(2)
