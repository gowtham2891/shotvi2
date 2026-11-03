"""Build golden_set.yaml from what is already on disk (no network, no model):

  data/channel_shorts/<id>.json  accepted Shorts, one source per matched segment -> kind channel_short
                                 (url, views, likes, short coverage, flags)
  data/gold/<id>.json            founder-reviewed clips, one source per time block  -> kind human
                                 (confidence definite when founder_would_post == yes, else maybe)
  heatmap_peaks.yaml             hand-entered most-replayed peaks                   -> kind heatmap
  comments                       stub (selection_v2.eval.comments)                  -> kind comment
  existing golden_set.yaml       sources marked manual: true are kept

    python -m selection_v2.eval.import_sources j14Om1SaFYE [epZ0lbXkgWA ...]
    python -m selection_v2.eval.import_sources --all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config import load_config
from ..paths import EpisodePaths, episodes_on_disk, load_json
from . import comments, golden_set, heatmap

BLOCK_GAP_S = 30.0     # time segments of one gold clip further apart than this are separate moments (eval SKIP_S)
TYPE_MAP = {"humour": "comedy", "humor": "comedy", "comedy": "comedy", "emotional": "emotional", "emotion": "emotional",
            "romance": "emotional", "promo": "hook", "cliffhanger": "hook", "hook": "hook", "loop": "loop"}


def channel_short_sources(vid: str, p: EpisodePaths) -> list[dict]:
    if not p.channel_shorts_data.exists():
        return []
    d = load_json(p.channel_shorts_data)
    out = []
    for s in d.get("accepted") or []:
        segs = s.get("segments") or [{"episode_start": s["episode_span"][0], "episode_end": s["episode_span"][1]}]
        for k, seg in enumerate(segs):
            out.append({"kind": "channel_short", "short_id": s.get("short_id"), "url": s.get("url"),
                        "title": s.get("title"), "views": s.get("views"), "likes": s.get("likes"),
                        "views_vs_median": s.get("views_vs_median"), "segment": k, "n_segments": len(segs),
                        "short_coverage": s.get("short_coverage"), "flags": s.get("flags") or [],
                        "start": round(float(seg["episode_start"]), 2), "end": round(float(seg["episode_end"]), 2),
                        "label": s.get("title") or ""})
    return out


def _blocks(segments: list[dict]) -> list[tuple[float, float]]:
    segs = sorted(((float(s["start"]), float(s["end"])) for s in segments), key=lambda x: x[0])
    out: list[list[float]] = []
    for a, b in segs:
        if out and a - out[-1][1] <= BLOCK_GAP_S:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def human_sources(vid: str, p: EpisodePaths) -> list[dict]:
    if not p.gold.exists():
        return []
    d = load_json(p.gold)
    out = []
    for c in d.get("clips") or []:
        segs = c.get("time_segments") or [{"start": pc["start"], "end": pc["end"]} for pc in c.get("pieces") or []]
        if not segs:
            continue
        blocks = _blocks(segs)
        fwp = str(c.get("founder_would_post") or "").lower()
        conf = "definite" if fwp == "yes" else "maybe"
        ctype = TYPE_MAP.get(str(c.get("category") or "").lower(), "unknown")
        if str(c.get("clip_purpose") or "").lower() == "promo":
            ctype = "hook"
        for k, (a, b) in enumerate(blocks):
            out.append({"kind": "human", "clip_id": c.get("id"), "confidence": conf,
                        "founder_would_post": c.get("founder_would_post"), "block": k, "n_blocks": len(blocks),
                        "start": round(a, 2), "end": round(b, 2), "type": ctype,
                        "label": c.get("title") or "", "labeler": c.get("labeler"),
                        "founder_review": d.get("founder_review")})
    return out


def import_episode(vid: str, gs: dict, cfg: dict, rule: str = "kinds") -> dict:
    p = EpisodePaths(vid, cfg)
    old = (gs["episodes"].get(vid) or {})
    manual = [s for m in old.get("moments") or [] for s in m.get("sources") or [] if s.get("manual")]
    sources = (channel_short_sources(vid, p) + human_sources(vid, p) + heatmap.load_peaks(vid)
               + comments.extract(vid) + manual)
    meta = load_json(p.meta) if p.meta.exists() else {}
    moments = golden_set.build_moments(sources, rule)
    gs["episodes"][vid] = {"series": meta.get("channel"), "title": meta.get("title"), "duration": meta.get("duration"),
                           "n_sources": {k: sum(s["kind"] == k for s in sources) for k in golden_set.KINDS},
                           "moments": moments}
    return gs["episodes"][vid]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m selection_v2.eval.import_sources")
    ap.add_argument("video_ids", nargs="*")
    ap.add_argument("--all", action="store_true", help="every episode with a channel_shorts or gold file")
    ap.add_argument("--out", default=str(golden_set.DEFAULT_PATH))
    ap.add_argument("--rule", choices=("kinds", "entries"), default="kinds", help="what 'two sources agree' counts")
    a = ap.parse_args(argv)
    cfg = load_config()
    vids = list(a.video_ids)
    if a.all:
        vids = [v for v in episodes_on_disk()
                if EpisodePaths(v, cfg).channel_shorts_data.exists() or EpisodePaths(v, cfg).gold.exists()]
    if not vids:
        ap.error("give video ids or --all")
    gs = golden_set.load(Path(a.out))
    for vid in vids:
        ep = import_episode(vid, gs, cfg, a.rule)
        n = {t: sum(m["tier"] == t for m in ep["moments"]) for t in ("gold", "silver")}
        print(f"{vid}: {len(ep['moments'])} moments ({n['gold']} gold, {n['silver']} silver) from {ep['n_sources']}")
    errs = golden_set.validate(gs)
    if errs:
        print("\n".join("ERROR " + e for e in errs), file=sys.stderr)
        return 1
    golden_set.save(gs, Path(a.out))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
