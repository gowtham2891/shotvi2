"""Gemini video-understanding test for web series (additive; podcasts untouched).

usage:
  python -m shotvi.select.video_check map   <video_id> [--budget-usd 0.15]
  python -m shotvi.select.video_check clips <video_id> [--only w03,w05] [--fps 1] [--budget-usd 0.25]
  python -m shotvi.select.video_check clips2 <video_id> --arm A [--shot-lines] [--only w03] [--budget-usd 0.03]
  python -m shotvi.select.video_check reconvert <video_id> <results.json> [--shot-lines]   (free: line ids -> times)

map:   one call over the full episode (low media resolution, 1 fps): scenes, visual beats, spans.
clips: one call per gold clip; windows = the clip's pieces merged across gaps <= MERGE_GAP_S, padded -1 s / +8 s
       (default media resolution). Suggested boundaries are snapped to ffmpeg shot cuts (shots.json).
Outputs: runs/video/<video_id>/{map.json, clips.json}.
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from shotvi.llm import Ledger, LLMClient, ModelSpec
from shotvi.llm.gemini import upload_file

from .prompts import load_prompt

MERGE_GAP_S = 45.0
PAD_BEFORE, PAD_AFTER = 1.0, 8.0
SNAP_S = 1.5

T = {"type": "number"}
MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {"type": "array", "items": {"type": "object", "properties": {
            "start": T, "end": T, "location": {"type": "string"},
            "characters": {"type": "array", "items": {"type": "object", "properties": {
                "description": {"type": "string"}, "who": {"type": "string"}}, "required": ["description", "who"]}},
            "what_happens": {"type": "string"}}, "required": ["start", "end", "location", "characters",
                                                                "what_happens"]}},
        "visual_beats": {"type": "array", "items": {"type": "object", "properties": {
            "t": T, "end": T, "what": {"type": "string"}, "why": {"type": "string"}},
            "required": ["t", "end", "what", "why"]}},
        "spans": {"type": "array", "items": {"type": "object", "properties": {
            "start": T, "end": T, "kind": {"type": "string", "enum": ["song", "montage", "credits", "title_card",
                                                                       "recap", "other"]},
            "note": {"type": "string"}}, "required": ["start", "end", "kind", "note"]}},
    },
    "required": ["scenes", "visual_beats", "spans"],
}
CLIP_SCHEMA = {
    "type": "object",
    "properties": {
        "reaction_visible": {"type": "object", "properties": {
            "visible": {"type": "boolean"}, "t": T, "what": {"type": "string"}},
            "required": ["visible", "t", "what"]},
        "visual_beats": {"type": "array", "items": {"type": "object", "properties": {
            "t": T, "end": T, "what": {"type": "string"}, "in_current_pieces": {"type": "boolean"},
            "should_include": {"type": "boolean"}, "why": {"type": "string"}},
            "required": ["t", "end", "what", "in_current_pieces", "should_include", "why"]}},
        "framing": {"type": "object", "properties": {
            "faces": {"type": "integer"}, "positions": {"type": "string"},
            "crop_916_score": {"type": "integer", "minimum": 1, "maximum": 5}, "note": {"type": "string"}},
            "required": ["faces", "positions", "crop_916_score", "note"]},
        "start_clean": {"type": "boolean"}, "end_clean": {"type": "boolean"}, "clean_note": {"type": "string"},
        "suggested": {"type": "array", "items": {"type": "object", "properties": {
            "start": T, "end": T, "why": {"type": "string"}}, "required": ["start", "end", "why"]}},
    },
    "required": ["reaction_visible", "visual_beats", "framing", "start_clean", "end_clean", "clean_note",
                 "suggested"],
}


def load_shots(vid: str) -> list[float]:
    return json.loads((Path("runs/video") / vid / "shots.json").read_text(encoding="utf-8"))["cuts"]


def snap(t: float, cuts: list[float], tol: float = SNAP_S) -> tuple[float, bool]:
    k = bisect.bisect_left(cuts, t)
    near = [c for c in cuts[max(0, k - 1):k + 1] if abs(c - t) <= tol]
    if not near:
        return t, False
    return min(near, key=lambda c: abs(c - t)), True


def windows_for(pieces: list[dict]) -> list[tuple[float, float]]:
    ps = sorted((p["start"], p["end"]) for p in pieces)
    out = [list(ps[0])]
    for a, b in ps[1:]:
        if a - out[-1][1] <= MERGE_GAP_S:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(max(0.0, a - PAD_BEFORE), b + PAD_AFTER) for a, b in out]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["map", "clips"])
    ap.add_argument("video_id")
    ap.add_argument("--only", default=None)
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
    ledger = Ledger(max_usd=a.budget_usd)
    llm = LLMClient(ledger, cache_dir="runs/cache")
    media_base = {"file_uri": f["uri"], "mime_type": "video/mp4", "key": f"{vid}.mp4:{f['size']}"}
    if a.mode == "map":
        p = load_prompt("video_map")
        spec = ModelSpec("gemini", "gemini-3.8-flash", {"thinking_level": "low", "media_resolution": "low"})
        obj, raw = llm.generate_json("video_map", spec, p.system, p.render(), MAP_SCHEMA, want_raw=True,
                                     media=[{**media_base, "fps": 1}], meta={"video": vid})
        res = {"prompt": p.tag, "usage": {"input": raw.input_tokens, "output": raw.output_tokens,
                                          "thought": raw.thought_tokens, "modalities": raw.prompt_modalities},
               "cost": ledger.summary(), **obj}
        (out / "map.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"map: {len(obj['scenes'])} scenes, {len(obj['visual_beats'])} beats, {len(obj['spans'])} spans | "
              f"in {raw.input_tokens} {raw.prompt_modalities} out {raw.output_tokens + raw.thought_tokens} | "
              f"${ledger.summary()['usd']:.4f}")
        return
    gold = json.loads(Path(f"data/gold/{vid}.json").read_text(encoding="utf-8"))
    tr = json.loads(Path(f"scribe_dataset/{vid}/{vid}_scribe_transcript.json").read_text(encoding="utf-8"))
    W = tr["word_timestamps"]
    cuts = load_shots(vid)
    only = set(a.only.split(",")) if a.only else None
    p = load_prompt("video_clip")
    spec = ModelSpec("gemini", "gemini-3.8-flash", {"thinking_level": "low"})   # default media resolution
    resf = out / "clips.json"
    results = json.loads(resf.read_text(encoding="utf-8")) if resf.exists() else {}
    for c in gold["clips"]:
        if only and c["id"] not in only:
            continue
        wins = windows_for(c["pieces"])
        pieces_txt = "\n".join(
            f"{q['start']:.1f}-{q['end']:.1f}: " + " ".join(W[i]["word"] for i in range(q["start_word"], q["end_word"] + 1))
            for q in c["pieces"])
        prompt = p.render(clip_id=c["id"], title=c.get("title", ""), pieces=pieces_txt,
                          windows=", ".join(f"{s:.1f}-{e:.1f}" for s, e in wins))
        media = [{**media_base, "start_s": s, "end_s": e, "fps": a.fps} for s, e in wins]
        obj, raw = llm.generate_json("video_clip", spec, p.system, prompt, CLIP_SCHEMA, want_raw=True, media=media,
                                     meta={"video": vid, "clip": c["id"]})
        for sg in obj.get("suggested", []):
            sg["start_snapped"], sg["start_on_cut"] = snap(sg["start"], cuts)
            sg["end_snapped"], sg["end_on_cut"] = snap(sg["end"], cuts)
        results[c["id"]] = {"windows": wins, "fps": a.fps, "usage": {
            "input": raw.input_tokens, "output": raw.output_tokens, "thought": raw.thought_tokens,
            "modalities": raw.prompt_modalities}, **obj}
        resf.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{c['id']}: windows {[(round(s), round(e)) for s, e in wins]} | in {raw.input_tokens} "
              f"out {raw.output_tokens + raw.thought_tokens} | run ${ledger.summary()['usd']:.4f}")


# ------------------------------------------------------------------ clip check v2: line ids only, wide windows
POS = {"type": "object", "properties": {
    "line": {"type": "string", "description": "a line id, or '' when the position is between two lines"},
    "after_line": {"type": "string"}, "before_line": {"type": "string"}},
    "required": ["line", "after_line", "before_line"]}
CLIP2_SCHEMA = {
    "type": "object",
    "properties": {
        "start": {"type": "object", "properties": {
            "verdict": {"type": "string", "enum": ["ok", "too_early", "too_late"]}, "at": POS,
            "why": {"type": "string"}}, "required": ["verdict", "at", "why"]},
        "end": {"type": "object", "properties": {
            "verdict": {"type": "string", "enum": ["ok", "too_early", "too_late"]}, "at": POS,
            "why": {"type": "string"}}, "required": ["verdict", "at", "why"]},
        "visual_beats_to_include": {"type": "array", "items": {"type": "object", "properties": {
            "after_line": {"type": "string"}, "before_line": {"type": "string"}, "what": {"type": "string"},
            "why": {"type": "string"}}, "required": ["after_line", "before_line", "what", "why"]}},
        "reaction": {"type": "object", "properties": {
            "after_line": {"type": "string"}, "what": {"type": "string"}}, "required": ["after_line", "what"]},
        "clean_edges": {"type": "object", "properties": {
            "start": {"type": "boolean"}, "end": {"type": "boolean"}, "note": {"type": "string"}},
            "required": ["start", "end", "note"]},
    },
    "required": ["start", "end", "visual_beats_to_include", "reaction", "clean_edges"],
}
BEFORE_S, AFTER_S, INNER_PAD_S = 40.0, 20.0, 5.0


def clip2_windows(pieces):
    """Blocks = pieces merged across gaps <= MERGE_GAP_S. The first window starts 40 s before the clip and the last
    ends 20 s after it (so a late start or an early end can be judged); inner block edges get 5 s."""
    ps = sorted((p["start"], p["end"]) for p in pieces)
    blocks = [list(ps[0])]
    for a, b in ps[1:]:
        if a - blocks[-1][1] <= MERGE_GAP_S:
            blocks[-1][1] = max(blocks[-1][1], b)
        else:
            blocks.append([a, b])
    out = []
    for k, (a, b) in enumerate(blocks):
        out.append((max(0.0, a - (BEFORE_S if k == 0 else INNER_PAD_S)),
                    b + (AFTER_S if k == len(blocks) - 1 else INNER_PAD_S)))
    return out


def clip2_prompt(ix, clip, wins, memory_md, prompt_name="video_clip"):
    """(system, user) for arm A (memory_md None) or arm B (identical plus one marked memory block)."""
    from .lines import render
    p = load_prompt(prompt_name)
    c0, c1 = clip["pieces"][0]["start"], clip["pieces"][-1]["end"]
    tags, parts = {}, []
    for w0, w1 in wins:
        cur = []
        for r in ix.in_range(w0, w1):
            if r["id"] in tags:
                continue
            mid = (r["start"] + r["end"]) / 2
            if mid < c0:
                tags[r["id"]] = "BEFORE CLIP"
            elif mid > c1:
                tags[r["id"]] = "AFTER CLIP"
            elif any(q["start"] - 0.05 <= mid <= q["end"] + 0.05 for q in clip["pieces"]):
                tags[r["id"]] = "IN CLIP"
            else:
                tags[r["id"]] = "CUT"
            cur.append(r)
        if cur:
            parts.append(render(cur, tags))
    lines_txt = "\n... [next video window] ...\n".join(parts)
    block = ("EPISODE MEMORY (what the editor knows from watching the whole episode; line ids refer to the same "
             "transcript):\n<memory>\n" + memory_md.strip() + "\n</memory>\n") if memory_md else ""
    return p.system, p.render(memory_block=block, clip_id=clip["id"], title=clip.get("title", ""), lines=lines_txt)


def convert(ix, obj: dict) -> dict:
    """Model positions (line ids only) -> times. An end on a long event line goes through gap_span so it is capped."""
    conv = {}
    for k in ("start", "end"):
        at = obj[k]["at"]
        r = ix.by_id.get(ix.norm(at.get("line"))) if at.get("line") else None
        if r and (k == "start" or r["kind"] != "event"):
            conv[k] = {"time": round(r["start"] if k == "start" else r["end"], 2)}
            continue
        gap = ix.gap_span(r["id"], r["id"]) if r else ix.gap_span(at.get("after_line"), at.get("before_line"))
        if gap:
            conv[k] = {"time": gap["start"] if k == "start" else gap["end"], "gap": gap}
    for b in obj.get("visual_beats_to_include", []):
        b["time"] = ix.gap_span(b.get("after_line"), b.get("before_line"))
    return conv


def main_reconvert(argv=None):
    from .lines import LineIndex
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("results")
    ap.add_argument("--shot-lines", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ix = LineIndex(a.video_id, shot_lines=a.shot_lines)
    path = Path(a.results)
    res = json.loads(path.read_text(encoding="utf-8"))
    for cid, r in res.items():
        old = {k: v.get("time") for k, v in r.get("converted", {}).items()}
        r["converted"] = convert(ix, r)
        new = {k: v.get("time") for k, v in r["converted"].items()}
        caps = [b["time"]["label"] for b in r.get("visual_beats_to_include", []) if (b.get("time") or {}).get("capped")]
        print(f"{cid}: {old} -> {new}" + (f" | capped beats {caps}" if caps else ""))
    path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


def main_clips2(argv=None):
    import difflib
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("--arm", choices=["A", "B"], required=True)
    ap.add_argument("--only", default=None)
    ap.add_argument("--fps", type=float, default=1.0)
    ap.add_argument("--budget-usd", type=float, default=0.03)
    ap.add_argument("--diff-only", action="store_true", help="print the A-vs-B prompt diff and exit (free)")
    ap.add_argument("--shot-lines", action="store_true",
                    help="list long silent stretches shot by shot (prompt video_clip_shots, output clips_v3_<arm>)")
    a = ap.parse_args(argv)
    load_dotenv()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from .lines import LineIndex
    vid = a.video_id
    out = Path("runs/video") / vid
    ix = LineIndex(vid, shot_lines=a.shot_lines)
    pname, ver = ("video_clip_shots", "v3") if a.shot_lines else ("video_clip", "v2")
    gold = json.loads(Path(f"data/gold/{vid}.json").read_text(encoding="utf-8"))
    mem_md = (out / "memory.md").read_text(encoding="utf-8")
    only = set(a.only.split(",")) if a.only else None
    clips = [c for c in gold["clips"] if not only or c["id"] in only]
    if a.diff_only:
        c = clips[0]
        wins = clip2_windows(c["pieces"])
        sa, ua = clip2_prompt(ix, c, wins, None, pname)
        sb, ub = clip2_prompt(ix, c, wins, mem_md, pname)
        diff = list(difflib.unified_diff(ua.splitlines(), ub.splitlines(), lineterm="", n=1))
        added = [l for l in diff if l.startswith("+") and not l.startswith("+++")]
        removed = [l for l in diff if l.startswith("-") and not l.startswith("---")]
        print("system identical:", sa == sb, "| windows:", [(round(x), round(y)) for x, y in wins])
        print("\n".join(diff[:6]))
        print(f"... {len(added)} added lines (the memory block), {len(removed)} removed lines")
        return
    f = upload_file(f"scribe_dataset/{vid}/{vid}.mp4")
    ledger = Ledger(max_usd=a.budget_usd)
    llm = LLMClient(ledger, cache_dir="runs/cache")
    spec = ModelSpec("gemini", "gemini-3.8-flash", {"thinking_level": "low"})
    resf = out / f"clips_{ver}_{a.arm}.json"
    results = json.loads(resf.read_text(encoding="utf-8")) if resf.exists() else {}
    for c in clips:
        wins = clip2_windows(c["pieces"])
        system, user = clip2_prompt(ix, c, wins, mem_md if a.arm == "B" else None, pname)
        media = [{"file_uri": f["uri"], "mime_type": "video/mp4", "key": f"{vid}.mp4:{f['size']}",
                  "start_s": round(s0, 2), "end_s": round(s1, 2), "fps": a.fps} for s0, s1 in wins]
        obj, raw = llm.generate_json(f"video_clip_{ver}", spec, system, user, CLIP2_SCHEMA, media=media, want_raw=True,
                                     meta={"video": vid, "clip": c["id"], "arm": a.arm})
        conv = convert(ix, obj)
        results[c["id"]] = {"windows": wins, "fps": a.fps, "usage": {
            "input": raw.input_tokens, "cached": raw.cached_tokens, "output": raw.output_tokens + raw.thought_tokens,
            "modalities": raw.prompt_modalities}, "converted": conv, **obj}
        resf.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{c['id']} [{a.arm}]: in {raw.input_tokens} cached {raw.cached_tokens} out "
              f"{raw.output_tokens + raw.thought_tokens} | run ${ledger.summary()['usd']:.4f}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "clips2":
        main_clips2(sys.argv[2:])
    elif len(sys.argv) > 1 and sys.argv[1] == "reconvert":
        main_reconvert(sys.argv[2:])
    else:
        main()
