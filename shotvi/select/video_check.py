"""Gemini video-understanding test for web series (additive; podcasts untouched).

usage:
  python -m shotvi.select.video_check map   <video_id> [--budget-usd 0.15]
  python -m shotvi.select.video_check clips <video_id> [--only w03,w05] [--fps 1] [--budget-usd 0.25]

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


if __name__ == "__main__":
    main()
