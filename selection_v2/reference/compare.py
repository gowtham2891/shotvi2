"""Compare a Stage 1A episode_facts document with a hand-written reference YAML (``reference/<video_id>.yaml``).

Each check is one item; the result rows say agree / disagree / missing with what was found, so the founder can
judge the stage against the spec's worked example without reading JSON.

    python -m selection_v2.reference.compare p3XTZlZ6-xw
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

from ..stage1_facts import fmt_ts, parse_ts

REF_DIR = Path(__file__).resolve().parent
# reference field names -> episode_facts.json keys
EPISODE_FIELDS = {"media_duration": "duration", "last_dialogue_end": "last_dialogue"}


def loose_name(s: str) -> str:
    """Spelling-insensitive name key: lowercase, sh->s, doubled letters collapsed, non-letters dropped."""
    s = (s or "").lower().replace("sh", "s").replace("ch", "c")
    s = re.sub(r"(.)\1+", r"\1", s)
    return re.sub(r"[^a-z\u0c00-\u0c7f]", "", s)


def names_match(want: str, cast_entry: dict | None, fallback_name: str | None) -> bool:
    """``want`` against a speaker_fix cast entry's name, description and aka (loose spelling)."""
    w = loose_name(want)
    if not w:
        return False
    blob = [fallback_name or ""]
    if cast_entry:
        blob += [cast_entry.get("name") or "", cast_entry.get("desc") or ""] + list(cast_entry.get("aka") or [])
    for b in blob:
        for tok in re.split(r"[\s,;:()'\"/]+", b):
            k = loose_name(tok)
            if k and (k == w or k.startswith(w) or w.startswith(k) and len(k) >= 4):
                return True
    return False


def reference_path(vid: str) -> Path:
    return REF_DIR / f"{vid}.yaml"


def load_reference(vid: str) -> dict | None:
    p = reference_path(vid)
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None


def parse_reference(text: str) -> dict:
    d = yaml.safe_load(text)
    if not isinstance(d, dict) or not isinstance(d.get("checks"), list):
        raise ValueError("reference YAML needs a top-level mapping with a 'checks' list")
    return d


def _t(x) -> float:
    return parse_ts(x)[0]


def _lines(facts):
    return {r["id"]: r for r in facts["lines"]}


def evaluate(facts: dict, ref: dict) -> list[dict]:
    tol = {"gap_s": 0.15, "time_s": 0.5, "boundary_s": 1.5, **(ref.get("tolerances") or {})}
    rows = []
    gaps = {(g["prev"], g["next"]): g for g in facts["gaps"]}
    lines = _lines(facts)
    for i, c in enumerate(ref["checks"], 1):
        kind = c.get("kind")
        row = {"#": i, "kind": kind, "expect": "", "got": "", "status": "disagree", "note": c.get("note") or ""}
        try:
            if kind == "episode":
                f = EPISODE_FIELDS.get(c["field"], c["field"])
                exp = _t(c["expect"]) if isinstance(c["expect"], str) and ":" in c["expect"] else c["expect"]
                got = facts["episode"].get(f)
                row["expect"] = f"{f} = {c['expect']}"
                row["got"] = fmt_ts(got) if isinstance(got, (int, float)) else str(got)
                if got is None:
                    row["status"] = "missing"
                elif isinstance(exp, (int, float)) and isinstance(got, (int, float)):
                    row["status"] = "agree" if abs(exp - got) <= tol["time_s"] else "disagree"
                else:
                    row["status"] = "agree" if str(exp) == str(got) else "disagree"
            elif kind == "gap":
                g = gaps.get((c["prev"], c["next"]))
                row["expect"] = f"{c['prev']}->{c['next']} {c['gap']} s" + (f" filled_by {c['filled_by']}" if c.get("filled_by") else "")
                if g is None:
                    nxt = next((x for x in facts["gaps"] if x["prev"] == c["prev"]), None)
                    row["status"] = "missing"
                    row["got"] = (f"no such gap row; after {c['prev']} comes {nxt['next']} ({nxt['gap']} s)" if nxt
                                  else f"{c['prev']} is not a dialogue line (zero-length or event?)")
                else:
                    ok = abs(g["gap"] - float(c["gap"])) <= tol["gap_s"]
                    if c.get("filled_by"):
                        ok = ok and (g["filled_by"] == c["filled_by"])
                    row["got"] = (f"{g['gap']} s, filled_by {g['filled_by']}, {g['spk_before']} -> {g['spk_after']}, "
                                  f"score {g['boundary_score']}")
                    row["status"] = "agree" if ok else "disagree"
            elif kind == "tag_event":
                t = _t(c["t"])
                row["expect"] = f"{c['class']} @ {c['t']}" + (f" after {c['after']}" if c.get("after") else "")
                cands = [e for e in facts["tag_events"] if e["class"] == c["class"] and abs(e["t"] - t) <= tol["time_s"]]
                if not cands:
                    near = min((e for e in facts["tag_events"] if abs(e["t"] - t) <= 5), key=lambda e: abs(e["t"] - t), default=None)
                    row["status"] = "missing"
                    row["got"] = (f"nearest tag within 5 s: {near['class']} {near['tag']} @ {fmt_ts(near['t'])}" if near
                                  else "no tag within 5 s")
                else:
                    e = min(cands, key=lambda e: abs(e["t"] - t))
                    row["got"] = (f"{e['class']} {e['tag']} @ {fmt_ts(e['t'])} after {e['after']}"
                                  f" (content line {e.get('after_content_line')})")
                    ok = not c.get("after") or c["after"] in (e["after"], e.get("after_content_line"))
                    row["status"] = "agree" if ok else "disagree"
            elif kind == "tag_count":
                n = sum(e["class"] == c["class"] for e in facts["tag_events"])
                row["expect"], row["got"] = f"{c['expect']} x {c['class']}", str(n)
                row["status"] = "agree" if n == int(c["expect"]) else "disagree"
            elif kind == "hold":
                a, b = _t(c["start"]), _t(c["end"])
                row["expect"] = f"dialogue gap {c['start']}-{c['end']} ({b - a:.1f} s)"
                g = min(facts["gaps"], key=lambda g: abs(g["t_prev_end"] - a) + abs(g["t_next_start"] - b))
                d = abs(g["t_prev_end"] - a) + abs(g["t_next_start"] - b)
                row["got"] = f"{g['prev']}->{g['next']} {fmt_ts(g['t_prev_end'])}-{fmt_ts(g['t_next_start'])} ({g['gap']} s)"
                row["status"] = "agree" if d <= 2 * tol["time_s"] else "disagree"
            elif kind == "boundary":
                t = _t(c["t"])
                row["expect"] = f"proposal near {c['t']}"
                # a proposal is one GAP: it matches on its snapped time or on either edge (prev end / next start)
                dist = lambda p: min(abs(p["t"] - t), abs(p["t_prev_end"] - t), abs(p["t_next_start"] - t))  # noqa: E731
                p = min(facts["boundary_proposals"], key=dist, default=None)
                if p is None or dist(p) > tol["boundary_s"]:
                    g = min(facts["gaps"], key=lambda g: abs(g["t_next_start"] - t), default=None)
                    row["status"] = "missing"
                    row["got"] = (f"nearest proposal {fmt_ts(p['t'])} (score {p['score']})" if p else "no proposals") + (
                        f"; nearest gap {g['prev']}->{g['next']} @ {fmt_ts(g['t_next_start'])} score {g['boundary_score']} "
                        f"{g['boundary_evidence']}" if g else "")
                else:
                    row["got"] = (f"{p['prev']}->{p['next']} edges {fmt_ts(p['t_prev_end'])} / {fmt_ts(p['t_next_start'])}, "
                                  f"cut {fmt_ts(p['t'])}, score {p['score']} {p['evidence']}")
                    row["status"] = "agree"
            elif kind == "boundary_count":
                n = len(facts["boundary_proposals"])
                row["expect"], row["got"] = f"{c.get('min', 0)}-{c.get('max', 999)} proposals", str(n)
                row["status"] = "agree" if int(c.get("min", 0)) <= n <= int(c.get("max", 999)) else "disagree"
            elif kind == "name_vote":
                evs = [e for e in facts["name_vote_events"] if e["line"] == c["line"]]
                row["expect"] = f"{c['line']} says {c['name']}; next speaker = {c.get('next_name', c['name'])}"
                hit = [e for e in evs if e["name"].lower() == str(c["name"]).lower()]
                if not hit:
                    row["status"] = "missing"
                    txt = lines.get(c["line"], {}).get("text", "?")
                    row["got"] = (f"no vote for {c['name']} on this line; votes: {[e['name'] for e in evs]}; text: {txt[:60]}")
                else:
                    e = hit[0]
                    want = str(c.get("next_name") or c["name"])
                    cast = next((x for x in facts["episode"].get("speakers") or [] if x.get("key") == e.get("next_speaker")), None)
                    row["got"] = (f"said by {e['said_by']} ({e['said_by_name']}); next {e['next_speaker']} "
                                  f"({e['next_speaker_name']}: {(cast or {}).get('desc', '')}) at {e['next_line']}")
                    row["status"] = "agree" if names_match(want, cast, e.get("next_speaker_name")) else "disagree"
            elif kind == "suspect":
                ids = set(facts.get("structural_suspect_ids") or [])
                row["expect"] = f"{c['line']} flagged"
                if c["line"] in ids:
                    why = [f"{s['reason']} [{s.get('label_space', 'fix')}]" for s in facts["structural_suspects"] if s.get("line") == c["line"]]
                    why += [f"speaker_fix confidence {f['confidence']}" for f in facts["speaker_flags"]
                            if f["line"] == c["line"] and f["flag"] == "suspect"]
                    row["got"], row["status"] = ", ".join(why), "agree"
                else:
                    r = lines.get(c["line"], {})
                    row["status"] = "missing"
                    row["got"] = f"not flagged; {r.get('speaker_key')} ({r.get('speaker_name')}, conf {r.get('speaker_confidence')}): {r.get('text', '')[:50]}"
            elif kind == "speaker_isolated":
                r = lines.get(c["line"], {})
                d = next((x for x in facts.get("speaker_segments") or [] if x["key"] == (r.get("speaker_key") or f"scribe:{r.get('speaker')}")), None)
                row["expect"] = f"{c['line']}'s speaker appears in one segment only"
                if d is None:
                    row["status"], row["got"] = "missing", f"no speaker_segments row for {c['line']}"
                else:
                    row["got"] = f"{d['key']} ({d['name']}): {d['n_lines']} lines, {d['n_segments']} segment(s): {d['segments_str']}"
                    row["status"] = "agree" if d["isolated"] else "disagree"
            elif kind == "suspect_speaker":
                fo = [s for s in facts["structural_suspects"] if s["reason"] == "filler_only_speaker"]
                row["expect"] = "at least one filler-only speaker (either label space)"
                row["got"] = "; ".join(f"[{s.get('label_space', 'fix')}] {s['speaker']} ({s['speaker_name']}): {s['lines']}" for s in fo) or "none"
                row["status"] = "agree" if fo else "missing"
            else:
                row["status"], row["got"] = "error", f"unknown check kind {kind!r}"
        except Exception as e:  # noqa: BLE001 - one bad check must not hide the others; the row shows the error
            row["status"], row["got"] = "error", f"{type(e).__name__}: {e}"
        rows.append(row)
    return rows


def summary(rows: list[dict]) -> dict:
    out = {"agree": 0, "disagree": 0, "missing": 0, "error": 0}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
    out["total"] = len(rows)
    return out


if __name__ == "__main__":
    from ..config import load_config
    from ..paths import EpisodePaths, load_json
    vid = sys.argv[1]
    cfg = load_config()
    facts = load_json(EpisodePaths(vid, cfg).facts)
    ref = load_reference(vid)
    rows = evaluate(facts, ref)
    for r in rows:
        print(f"{r['status']:8s} {r['kind']:14s} {r['expect']}  ->  {r['got']}")
    print(json.dumps(summary(rows)))
