"""Episode memory for web series: the editor's "watched the whole thing" context, built window by window.

usage: python -m shotvi.select.episode_memory <video_id> [--window-s 150] [--budget-usd 0.15]

Each ~150 s window: that window's video (low media resolution, 1 fps, via offsets) + its transcript lines + the memory
so far; the model returns UPDATES referencing LINE IDS only (never timestamps). Code merges the updates, converts
visual beats placed between two line ids into time spans (snapped to ffmpeg shot cuts), and writes
runs/video/<id>/memory.json and memory.md (compact, ~2.5k tokens, for later prompts).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from shotvi.llm import Ledger, LLMClient, ModelSpec
from shotvi.llm.gemini import upload_file

from .lines import LineIndex, fmt, render
from .prompts import load_prompt

LREF = {"type": "string", "description": "a line id like L0123, or '' if none"}
SCHEMA = {
    "type": "object",
    "properties": {
        "characters": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string", "description": "existing id (C1, C2...) to update, or 'new'"},
            "name": {"type": "string"}, "description": {"type": "string"},
            "relations": {"type": "array", "items": {"type": "string"}},
            "speaker_labels": {"type": "array", "items": {"type": "object", "properties": {
                "label": {"type": "string"}, "from_line": LREF, "to_line": LREF},
                "required": ["label", "from_line", "to_line"]}},
            "label_errors": {"type": "array", "items": {"type": "string"}}},
            "required": ["id", "name", "description", "relations", "speaker_labels", "label_errors"]}},
        "scenes": {"type": "array", "items": {"type": "object", "properties": {
            "from_line": LREF, "to_line": LREF, "place": {"type": "string"}, "what": {"type": "string"}},
            "required": ["from_line", "to_line", "place", "what"]}},
        "plot": {"type": "object", "properties": {
            "setup": {"type": "string"}, "conflict": {"type": "string"}, "turn": {"type": "string"},
            "cliffhanger": {"type": "string"}}, "required": ["setup", "conflict", "turn", "cliffhanger"]},
        "links": {"type": "array", "items": {"type": "object", "properties": {
            "setup_line": LREF, "payoff_line": LREF, "payoff_after_line": LREF, "payoff_before_line": LREF,
            "what": {"type": "string"}},
            "required": ["setup_line", "payoff_line", "payoff_after_line", "payoff_before_line", "what"]}},
        "gags": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "lines": {"type": "array", "items": {"type": "string"}},
            "what": {"type": "string"}}, "required": ["name", "lines", "what"]}},
        "visual_beats": {"type": "array", "items": {"type": "object", "properties": {
            "after_line": LREF, "before_line": LREF, "what": {"type": "string"}, "why": {"type": "string"}},
            "required": ["after_line", "before_line", "what", "why"]}},
    },
    "required": ["characters", "scenes", "plot", "links", "gags", "visual_beats"],
}


def windows(ix: LineIndex, duration: float, window_s: float) -> list[tuple[float, float, list[dict]]]:
    """Consecutive windows of ~window_s, cut in a silence between lines (never inside a line)."""
    out, t0 = [], 0.0
    while t0 < duration - 1:
        target = min(duration, t0 + window_s)
        if target < duration:
            # move the cut to the nearest gap between two lines after the target
            nxt = [r for r in ix.lines if r["start"] >= target]
            target = nxt[0]["start"] - 0.05 if nxt and nxt[0]["start"] - target < 30 else target
            inside = [r for r in ix.lines if r["start"] < target < r["end"]]
            if inside:
                target = max(r["end"] for r in inside) + 0.05
        lines = [r for r in ix.lines if t0 <= r["start"] < target]
        out.append((t0, min(target, duration), lines))
        t0 = target
    return out


def merge(mem: dict, upd: dict) -> None:
    chars = mem.setdefault("characters", [])
    for c in upd.get("characters", []):
        ex = next((x for x in chars if x["id"] == c.get("id")), None)
        if ex is None:
            c = dict(c)
            c["id"] = f"C{len(chars) + 1}"
            chars.append(c)
        else:
            for k in ("name", "description"):
                if c.get(k) and c[k].lower() not in ("unclear", "unknown"):
                    ex[k] = c[k]
            for k in ("relations", "label_errors"):
                ex[k] = list(dict.fromkeys(ex.get(k, []) + c.get(k, [])))
            ex["speaker_labels"] = ex.get("speaker_labels", []) + c.get("speaker_labels", [])
    mem.setdefault("scenes", []).extend(upd.get("scenes", []))
    plot = mem.setdefault("plot", {})
    w = mem.setdefault("_window", 0)
    src = mem.setdefault("_plot_window", {})
    for k, v in (upd.get("plot") or {}).items():
        if not (v and v.strip()):
            continue
        if k in ("setup", "conflict") and plot.get(k):
            continue                      # the story's setup / conflict are established early: first wins
        if k == "turn" and plot.get(k) and src.get("turn", 0) > src.get("conflict", 0):
            continue                      # first turn after the conflict
        plot[k] = v                       # cliffhanger: last window wins
        src[k] = w
    mem["_window"] = w + 1
    mem.setdefault("links", []).extend(upd.get("links", []))
    gags = mem.setdefault("gags", [])
    for gg in upd.get("gags", []):
        ex = next((x for x in gags if x["name"].lower() == gg["name"].lower()), None)
        if ex:
            ex["lines"] = list(dict.fromkeys(ex["lines"] + gg.get("lines", [])))
            ex["what"] = gg.get("what") or ex["what"]
        else:
            gags.append(dict(gg))
    mem.setdefault("visual_beats", []).extend(upd.get("visual_beats", []))


def add_times(mem: dict, ix: LineIndex) -> None:
    for b in mem.get("visual_beats", []):
        b["time"] = ix.gap_span(b.get("after_line"), b.get("before_line"))
    for l in mem.get("links", []):
        if l.get("payoff_after_line") or l.get("payoff_before_line"):
            l["payoff_time"] = ix.gap_span(l.get("payoff_after_line"), l.get("payoff_before_line"))
    for s in mem.get("scenes", []):
        a, b = ix.line_span(s.get("from_line")), ix.line_span(s.get("to_line"))
        s["time"] = f"{fmt(a[0])}-{fmt(b[1])}" if a and b else ""


def compact(mem: dict) -> str:
    """Memory as compact text for later prompts (~2.5k tokens): line ids kept, times only as orientation."""
    cut = lambda x, n: (x or "")[:n]  # noqa: E731
    L = ["CHARACTERS:"]
    for c in mem.get("characters", []):
        labels = ", ".join(dict.fromkeys(x["label"] for x in c.get("speaker_labels", [])))
        rel = "; ".join(c.get("relations", [])[:2])
        L.append(f"- {c['id']} {cut(c['name'], 30)}: {cut(c['description'], 70)}" + (f" | {cut(rel, 80)}" if rel else "")
                 + (f" | labels: {labels}" if labels else ""))
    L.append("SCENES:")
    for s in mem.get("scenes", []):
        L.append(f"- {s.get('from_line')}-{s.get('to_line')} {cut(s['place'], 25)}: {cut(s['what'], 80)}")
    p = mem.get("plot", {})
    L.append("PLOT: setup: " + cut(p.get("setup"), 160) + " | conflict: " + cut(p.get("conflict"), 160)
             + " | turn: " + cut(p.get("turn"), 160) + " | cliffhanger: " + cut(p.get("cliffhanger"), 160))
    L.append("SETUP -> PAYOFF:")
    for l in mem.get("links", []):
        pay = l.get("payoff_line") or (f"between {l.get('payoff_after_line')}/{l.get('payoff_before_line')}")
        L.append(f"- {l.get('setup_line')} -> {pay}: {cut(l['what'], 90)}")
    L.append("RUNNING GAGS:")
    for g in mem.get("gags", []):
        L.append(f"- {cut(g['name'], 30)} ({', '.join(g.get('lines', [])[:4])}): {cut(g['what'], 70)}")
    L.append("VISUAL BEATS (no dialogue; between two lines):")
    for b in mem.get("visual_beats", []):
        L.append(f"- {b.get('after_line')}/{b.get('before_line')}: {cut(b['what'], 95)}")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("--window-s", type=float, default=150.0)
    ap.add_argument("--fps", type=float, default=1.0)
    ap.add_argument("--budget-usd", type=float, default=0.15)
    a = ap.parse_args(argv)
    load_dotenv()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    vid = a.video_id
    out = Path("runs/video") / vid
    out.mkdir(parents=True, exist_ok=True)
    f = upload_file(f"scribe_dataset/{vid}/{vid}.mp4")
    ix = LineIndex(vid)
    dur = json.loads((out / "shots.json").read_text(encoding="utf-8"))["duration"]
    ledger = Ledger(max_usd=a.budget_usd)
    llm = LLMClient(ledger, cache_dir="runs/cache")
    p = load_prompt("memory_window")
    spec = ModelSpec("gemini", "gemini-3.8-flash", {"thinking_level": "low", "media_resolution": "low"})
    mem: dict = {}
    usage = []
    for k, (t0, t1, lines) in enumerate(windows(ix, dur, a.window_s)):
        if not lines:
            continue
        prompt = p.render(memory=compact(mem) if mem else "(empty: this is the first window)",
                          first_line=lines[0]["id"], last_line=lines[-1]["id"], lines=render(lines))
        media = [{"file_uri": f["uri"], "mime_type": "video/mp4", "key": f"{vid}.mp4:{f['size']}",
                  "start_s": round(t0, 2), "end_s": round(t1, 2), "fps": a.fps}]
        upd, raw = llm.generate_json("memory_window", spec, p.system, prompt, SCHEMA, media=media, want_raw=True,
                                     meta={"video": vid, "window": k})
        merge(mem, upd)
        usage.append({"window": k, "t": [round(t0, 1), round(t1, 1)], "lines": [lines[0]["id"], lines[-1]["id"]],
                      "in": raw.input_tokens, "out": raw.output_tokens + raw.thought_tokens,
                      "cached": raw.cached_tokens, "modalities": raw.prompt_modalities})
        print(f"window {k}: {fmt(t0)}-{fmt(t1)} {len(lines)} lines | in {raw.input_tokens} "
              f"out {raw.output_tokens + raw.thought_tokens} | run ${ledger.summary()['usd']:.4f}")
    add_times(mem, ix)
    txt = compact(mem)
    (out / "memory.json").write_text(json.dumps({"prompt": p.tag, "usage": usage, "cost": ledger.summary(),
                                                 "memory": mem}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "memory.md").write_text(f"# Episode memory: {vid}\n\n{txt}\n", encoding="utf-8")
    print(f"memory.md: {len(txt)} chars | total ${ledger.summary()['usd']:.4f}")


if __name__ == "__main__":
    main()
