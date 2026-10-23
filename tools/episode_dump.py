"""One readable file per episode for an emotion_pick run: what the pipeline found, built, rejected and showed, plus
the whole transcript. No timestamps (founder, 2026-10-16): lines are referred to by their line id only.
Speaker labels: a run made with a speaker source other than Scribe (emotion_pick ``speaker_source``) shows that layer's
anonymous labels (shotvi.segment.speaker_layer); other runs show corrected names where runs/speaker_fix exists, as before.

    python tools/episode_dump.py runs/emotion1 --out emotion_first_episodes
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shotvi.select import webseries as ws  # noqa: E402
from shotvi.select.emotion_select import clean_text, render_clip  # noqa: E402
from shotvi.select.lines import speaker_label  # noqa: E402
from shotvi.segment import speaker_layer  # noqa: E402

SERIES = {"5HqkHAZfzos": "Queen", "Sy8yZ4DgzrU": "Project Pelli S2", "fjGrX8aBVAc": "Love Brew",
          "qIdAPG8UulE": "Office lo Krishna Leelalu", "ZvISbJNSolg": "The Software DevLOVEper",
          "0TK5hWAEXBw": "30 Weds 21", "p3XTZlZ6-xw": "Surya", "LJQOGxrXalI": "Temporary Roommates"}
REJECT_CODES = {"N0": "Gemini answered no clip", "K1": "stop line does not play first", "K2": "land line not at the end",
                "L1": "too short", "L2": "too long", "D1": "repeat of another clip", "V1": "invalid line ids",
                "B1": "cut builder refused", "C1": "call failed", "X1": "blocked by safety check"}


def load_ep(doc: dict, vid: str):
    """The episode as the run saw its speakers: the run's speaker layer (anonymous labels, no names) when it used one,
    else corrected names where runs/speaker_fix exists (as before)."""
    layer = speaker_layer.for_run(doc, vid)
    if layer is None:
        return ws.load_episode(vid, with_loudness=False, speaker_names=True, require_names=False)[0]
    ep = ws.load_episode(vid, with_loudness=False, speaker_names=False)[0]
    speaker_layer.apply(ep.lines, ep.words, layer)
    return ep


def line_text(ep, lid: str) -> str:
    r = ep.by_id.get(lid)
    return clean_text(r.get("text", "")) if r else ""


def as_played(ep, clip: dict) -> list[str]:
    rd = render_clip(ep, {"time_segments": clip["time_segments"], "cold_open": bool(clip.get("cold_open"))}, "C", ep.names)
    out = []
    for row in rd.text.splitlines()[1:]:          # drop the "### C | plays N s" header
        row = re.sub(r"^\[\d+:\d+(\.\d+)?\] ", "", row)
        if row.startswith("[cut:"):
            row = "[cut: a part is removed here" + ("; the cold-open line is skipped here, not replayed]"
                                                  if "skipped" in row else "]")
        elif row.startswith("[picture holds"):
            row = "[picture holds on the reaction]"
        elif row.startswith("[jumps back"):
            row = "[jumps back]"
        out.append(row)
    return out


def answer_block(ep, a: dict) -> list[str]:
    if not a:
        return ["  (no build answer)"]
    if a.get("no_clip"):
        return [f"  Gemini: no clip. {a.get('no_clip_why', '')}"]
    st, ld = a.get("stop") or {}, a.get("land") or {}
    out = [f"  Title: {a.get('title', '')}",
           f"  Stop: {st.get('line', '')} \"{line_text(ep, st.get('line', ''))}\". {st.get('why', '')}",
           f"  Stay: {a.get('stay', '')}",
           f"  Land: {ld.get('line', '')} \"{line_text(ep, ld.get('line', ''))}\"; reaction: {ld.get('reaction', '')}",
           f"  Clip lines: {a.get('start_line', '')} to {a.get('end_line', '')}"]
    for d in a.get("drops") or []:
        out.append(f"  Remove: {d.get('first_line')} to {d.get('last_line') or d.get('first_line')}. {d.get('why', '')}")
    out.append(f"  Reason: {a.get('reason', '')}")
    return out


def transcript_rows(ep) -> list[str]:
    out = []
    for r in ep.lines:
        if r["kind"] == "speech":
            out.append(f"{r['id']} [{speaker_label(r, ep.names)}]: {clean_text(r.get('text', ''))}")
        else:
            out.append(f"{r['id']} (sound) {clean_text(r.get('text', ''))}")
    return out


def dump_short(run: Path, vid: str) -> str:
    """Only the whole transcript and the clips shown (founder, 2026-10-16)."""
    doc = json.loads((run / f"{vid}.json").read_text(encoding="utf-8"))
    ep = load_ep(doc, vid)
    L = [f"# {SERIES.get(vid, vid)} ({vid})", "", "## Transcript", "", "```"] + transcript_rows(ep) + ["```", "",
         f"## Clips ({len(doc['proposals'])})"]
    for n, c in enumerate(doc["proposals"], 1):
        L += ["", f"### Clip {n}: {c.get('title', '')}", "", "```"] + as_played(ep, c) + ["```"]
    return "\n".join(L) + "\n"


def dump(run: Path, vid: str) -> str:
    doc = json.loads((run / f"{vid}.json").read_text(encoding="utf-8"))
    dbg = json.loads((run / "debug" / f"{vid}.json").read_text(encoding="utf-8"))
    ep = load_ep(doc, vid)
    answers = {c["moment"]: c.get("answer") for c in dbg.get("calls", [])}
    s = doc["episode_summary"]
    usd = (doc.get("cost") or {}).get("usd_billed") or (doc.get("cost") or {}).get("usd") or 0
    L = [f"# {SERIES.get(vid, vid)} ({vid}): emotion-first clip selection",
         "",
         f"Run: {run.as_posix()} (pipeline {doc.get('pipeline', {}).get('version', '')}), cost about Rs {usd * 90:.2f}.",
         "Lines are referred to by their id (L0123). No timestamps.",
         "",
         "## 1. Summary",
         f"- Moments found by the find call: {s['n_moments_valid']}",
         f"- Build calls made: {s['n_build_calls']} (limit {s.get('max_moments')} per episode;"
         f" {s.get('n_not_built_cap', 0)} not built because of the limit)",
         f"- Gemini said no clip: {s.get('n_no_clip', 0)}",
         f"- Dropped by our code checks: {s.get('n_stop_check_failed', 0) + s.get('n_land_check_failed', 0)}"
         f" (stop or land check), {s.get('n_hard_rules', 0)} (too short / too long / basic rules),"
         f" {s.get('n_deduped', 0)} (repeats)",
         f"- Clips shown: {s['n_shown']}",
         "",
         "Episode notes written by Gemini in the find call:",
         f"> {(dbg.get('find_raw') or {}).get('episode_notes', '')}",
         "",
         f"## 2. Clips shown ({len(doc['proposals'])})"]
    for c in doc["proposals"]:
        st, ld = c.get("stop") or {}, c.get("land") or {}
        L += ["", f"### {c['id']}: {c.get('title', '')}",
              f"- Feeling: {c.get('feeling')} (strength {c.get('intensity')} of 5)",
              f"- Length: {c.get('edited_duration', 0):.0f} s; cold open: {'yes' if c.get('cold_open') else 'no'}",
              f"- Reason: {c.get('reason', '')}",
              f"- Stop: {st.get('line')} \"{st.get('text', '')}\". {st.get('why', '')}",
              f"- Stay: {c.get('stay', '')}",
              f"- Land: {ld.get('line')} \"{ld.get('text', '')}\"; reaction: {ld.get('reaction', '')}"
              + (f" \"{ld['reaction_text']}\"" if ld.get("reaction_text") else ""),
              f"- Clip lines: {c.get('start_line')} to {c.get('end_line')}"]
        for d in c.get("chunk_drops") or []:
            L.append(f"- Removed: {', '.join(d.get('removed_lines') or [])}. {d.get('why', '')}")
        for f in c.get("build_flags") or []:
            L.append(f"- Note: {f}")
        L += ["", "As played:", "```"] + as_played(ep, c) + ["```"]
    L += ["", "## 3. Every moment the find call listed, and what happened to it"]
    for m in doc.get("moment_table") or []:
        L += ["", f"### {m['id']}: {m['line']} ({m['feeling']}, strength {m['intensity']}): {m.get('status')}",
              f"- The line: \"{m.get('text', '')}\"",
              f"- Why (find call): {m.get('why', '')}"]
        if m.get("reason") and not str(m["reason"]).startswith("N0"):
            code = str(m["reason"])[:2]
            L.append(f"- Result: {REJECT_CODES.get(code, '')}: {m['reason']}" if code in REJECT_CODES
                     else f"- Result: {m['reason']}")
        if m["id"] in answers:
            L += ["- Build call answer:"] + answer_block(ep, answers[m["id"]])
        elif m.get("status") not in ("shown",):
            L.append("- No build call (over the limit of moments per episode)")
    L += ["", "## 4. Whole transcript", "", "```"]
    for r in ep.lines:
        if r["kind"] == "speech":
            L.append(f"{r['id']} [{speaker_label(r, ep.names)}]: {clean_text(r.get('text', ''))}")
        else:
            L.append(f"{r['id']} (sound) {clean_text(r.get('text', ''))}")
    L.append("```")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--out", default="emotion_first_episodes")
    ap.add_argument("--full", action="store_true",
                    help="also the summary, every moment found, build answers and rejections")
    a = ap.parse_args(argv)
    run, out = Path(a.run), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for f in sorted(run.glob("*.json")):
        if f.name.startswith("dry_run") or not (run / "debug" / f.name).exists():
            continue
        vid = f.stem
        name = re.sub(r"[^A-Za-z0-9]+", "_", SERIES.get(vid, vid)).strip("_")
        p = out / f"{name}_{vid}.md"
        p.write_text((dump if a.full else dump_short)(run, vid), encoding="utf-8")
        print(p)


if __name__ == "__main__":
    main()
