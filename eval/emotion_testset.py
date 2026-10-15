"""Test set for the emotion-trigger stage (shotvi/select/emotion_select.py): every web-series clip the founder has
rated with a would-post verdict, in the exact cut he watched.

    python -m eval.emotion_testset [--out runs/emotion_test/clips.json] [--no-verify]

Writes a list of {video_id, clip_id (<source>.<video_id>.<gold clip id>, unique in the set), time_segments (play
order), cold_open, ...labels}. The stage passes every extra
field through untouched and never shows it to the model, so the founder's labels live in extra fields:
founder_would_post (yes|maybe|no), founder_scores {hook, standalone, payoff, overall}, founder_notes, source, series,
pipeline_overall (v1 review overall, v1_test clips only), v1_tier, gt_editor_overall (GT clips: the clip-gt-editor's
own score, NOT a pipeline review), rating_file, preview, cut_from, cut_verified, same_moment_as.

Which cut was rated (each rating sheet was filled in on previews rendered from one clip file):
  gt_cg_r1   ratings_gt_webseries_r1.csv  Couple Goals Ep1/Ep2 round 1 = data/gold at commit c710a02 (the commit that
             added the ratings; round 2 re-cut every clip afterwards). Ep1 had no time_segments then: the preview
             played the word pieces padded by tools/preview_clips.py (0.10 s before, 0.20 s after, at most halfway to
             the neighbouring word, touching pieces merged); that padding is reproduced here.
  gt_hm_r1   ratings_gt_hm_r1.csv         runs/gold_hm (= data/gold at f95ffc7), Hello Madam Ep6 round 1
  gt_hm_r2   ratings_gt_hm_r2.csv         runs/gold_hm_r2 (= data/gold at aaa0018), round 2 re-cuts
  gt_gh2_r1  ratings_gt_gh2_r1.csv        runs/gold_gh2_show (judge-gated set shown to the founder; same cuts as data/gold)
  v1_test    ratings_v1_test.csv          runs/v1_test (ws/0.2 v1 run on 4 new episodes)
With --verify (default when ffprobe and the previews exist) each cut's played length is checked against the duration
of the preview MP4 the founder watched (runs/previews/<run>/<file>); a mismatch over VERIFY_TOL excludes the clip.

The same cut rated in two rounds (identical time_segments) is kept once, with the LATER rating; the earlier one is
kept in `earlier_ratings`. Different cuts of the same moment (re-cuts across rounds) are kept as separate clips and
linked by `same_moment_as` (source overlap >= SAME_MOMENT_OVERLAP of the shorter clip), since they are not independent.
No LLM, no network.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

from .common import ROOT, load_json, resolve, utf8_stdout, write_json

RATED = ("yes", "maybe", "no")
SCORE_COLS = {"hook": "hook_1to5", "standalone": "standalone_1to5", "payoff": "payoff_1to5", "overall": "overall_1to5"}
VERIFY_TOL = 0.10          # s: preview MP4 duration vs the cut's summed segments (encoder rounding is <= 0.05 s)
SAME_MOMENT_OVERLAP = 0.5  # share of the shorter clip's source time
PRE_PAD, POST_PAD = 0.10, 0.20   # tools/preview_clips.py padding at round 1 (commit ccbba05)
DEFAULT_OUT = "runs/emotion_test/clips.json"

# (source, rating csv, preview run under runs/previews, clip file: "git:<rev>:<path>" or a repo path; {vid} = video)
SOURCES = (
    ("gt_cg_r1", "data/human/ratings_gt_webseries_r1.csv", "gold", "git:c710a02:data/gold/{vid}.json"),
    ("gt_hm_r1", "data/human/ratings_gt_hm_r1.csv", "gold_hm", "runs/gold_hm/{vid}.json"),
    ("gt_hm_r2", "data/human/ratings_gt_hm_r2.csv", "gold_hm_r2", "runs/gold_hm_r2/{vid}.json"),
    ("gt_gh2_r1", "data/human/ratings_gt_gh2_r1.csv", "gold_gh2_show", "runs/gold_gh2_show/{vid}.json"),
    ("v1_test", "data/human/ratings_v1_test.csv", "v1_test", "runs/v1_test/{vid}.json"),
)


def _num(x):
    x = (x or "").strip()
    if not x:
        return None
    f = float(x)
    return int(f) if f.is_integer() else f


def read_rows(path):
    with open(resolve(path), encoding="utf-8-sig", newline="") as f:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]


def load_clip_file(spec):
    if spec.startswith("git:"):
        _, rev, path = spec.split(":", 2)
        r = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8")
        if r.returncode != 0:
            raise FileNotFoundError(f"git show {rev}:{path} failed: {r.stderr.strip()}")
        return json.loads(r.stdout)
    return load_json(resolve(spec))


def padded_segments(pieces, words):
    """tools/preview_clips.py (round 1): pad each word piece into the silence around it, merge touching pieces."""
    segs = []
    for p in pieces:
        i0, i1 = p["start_word"], p["end_word"]
        prev_end = words[i0 - 1]["end"] if i0 > 0 else 0.0
        next_start = words[i1 + 1]["start"] if i1 + 1 < len(words) else p["end"] + POST_PAD
        s = max(p["start"] - PRE_PAD, (prev_end + p["start"]) / 2)
        e = min(p["end"] + POST_PAD, (p["end"] + next_start) / 2)
        if segs and abs(segs[-1][1] - s) < 0.05 and s >= segs[-1][0]:
            segs[-1] = (segs[-1][0], e)
        else:
            segs.append((s, e))
    return segs


def played_segments(clip, words_of):
    """Segments in play order, as the preview played them: time_segments as is, else padded word pieces."""
    if clip.get("time_segments"):
        return [dict(t) for t in clip["time_segments"]], "time_segments"
    segs = padded_segments(clip["pieces"], words_of())
    return [{"start": round(a, 3), "end": round(b, 3), "kind": "padded_pieces"} for a, b in segs], "padded_pieces"


def is_cold_open(clip, segs):
    return bool(clip.get("cold_open")) or any(segs[i + 1]["start"] < segs[i]["start"] for i in range(len(segs) - 1))


def seg_key(segs):
    return tuple((round(s["start"], 2), round(s["end"], 2)) for s in segs)


def seg_len(segs):
    return sum(s["end"] - s["start"] for s in segs)


def overlap(a, b):
    tot = 0.0
    for x in a:
        for y in b:
            tot += max(0.0, min(x["end"], y["end"]) - max(x["start"], y["start"]))
    return tot


def series_name(video_id):
    """'Couple Goals || Episode - 2 || ...' -> 'Couple Goals'; YouTube metadata in scribe_dataset/<id>/."""
    p = ROOT / "scribe_dataset" / video_id / "youtube_meta.json"
    if not p.exists():
        return "?", ""
    title = load_json(p).get("title") or ""
    name = " ".join(title.split("|")[0].split())
    for suffix in (" Part - 1", " Part - 2"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name or "?", title


def mp4_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def build(verify=True, sources=SOURCES):
    """(clips, excluded, notes). excluded = [{source, video_id, clip_id, reason}]."""
    can_verify = verify and shutil.which("ffprobe") is not None
    words_cache, clips, excluded, notes = {}, [], [], []
    if verify and not can_verify:
        notes.append("ffprobe not found: cuts not verified against the preview MP4s")

    def words_of(vid):
        def get():
            if vid not in words_cache:
                p = ROOT / "scribe_dataset" / vid / f"{vid}_scribe_transcript.json"
                words_cache[vid] = load_json(p)["word_timestamps"]
            return words_cache[vid]
        return get

    for source, csv_path, preview_run, clip_spec in sources:
        docs = {}
        for row in read_rows(csv_path):
            vid, cid = row["video_id"], row["clip_id"]
            base = {"source": source, "video_id": vid, "clip_id": cid}
            wp = row.get("would_post", "").lower()
            if wp not in RATED:
                excluded.append({**base, "reason": f"would_post={row.get('would_post')!r} not in {RATED}"})
                continue
            if vid not in docs:
                try:
                    docs[vid] = load_clip_file(clip_spec.format(vid=vid))
                except (FileNotFoundError, OSError, json.JSONDecodeError) as e:
                    docs[vid] = None
                    notes.append(f"{source} {vid}: clip file not loadable ({e})")
            doc = docs[vid]
            by_id = {c.get("id"): c for c in (doc.get("clips") or doc.get("proposals") or [])} if doc else {}
            clip = by_id.get(cid)
            if clip is None:
                excluded.append({**base, "reason": f"clip not in {clip_spec.format(vid=vid)}"})
                continue
            segs, cut_from = played_segments(clip, words_of(vid))
            verified, preview = None, f"runs/previews/{preview_run}/{row.get('file', '')}"
            if can_verify:
                mp4 = ROOT / preview
                if mp4.exists():
                    d = mp4_duration(mp4)
                    verified = d is not None and abs(d - seg_len(segs)) <= VERIFY_TOL
                    if not verified:
                        excluded.append({**base, "reason": f"cut does not match the rated preview ({preview}: "
                                                           f"{d} s vs {seg_len(segs):.2f} s played)"})
                        continue
            series, title = series_name(vid)
            scores = {k: _num(row.get(c)) for k, c in SCORE_COLS.items()}
            is_v1 = source.startswith("v1")
            item = {
                "video_id": vid,
                "clip_id": f"{source}.{vid}.{cid}",   # unique across the whole set (gold ids repeat per video)
                "time_segments": segs,
                "cold_open": is_cold_open(clip, segs),
                # ---- labels and bookkeeping: passed through, never shown to the model ----
                "founder_would_post": wp,
                "founder_scores": scores,
                "founder_notes": row.get("notes", ""),
                "source": source,
                "series": series,
                "episode_title": title,
                "content_type": clip.get("content_type") or (doc or {}).get("content_type") or "web_series",
                "gold_clip_id": cid,
                "rating_file": csv_path,
                "rating_rank": _num(row.get("rank")),
                "title": clip.get("title") or row.get("title", ""),
                "preview": preview,
                "cut_from": f"{clip_spec.format(vid=vid)} ({cut_from})",
                "cut_verified": verified,
                "play_s": round(seg_len(segs), 2),
                "pipeline_overall": _num(row.get("pipeline_overall")) if is_v1 else None,
                "v1_tier": clip.get("tier") if is_v1 else None,
                "gt_editor_overall": None if is_v1 else _num(row.get("pipeline_overall")),
                "earlier_ratings": [],
                "same_moment_as": [],
            }
            clips.append(item)

    # the same cut rated twice: keep the later rating (sources are listed in rating order)
    seen, kept = {}, []
    for c in clips:
        k = (c["video_id"], seg_key(c["time_segments"]))
        if k in seen:
            first = seen[k]
            old = {f: first[f] for f in ("clip_id", "source", "founder_would_post", "founder_scores", "founder_notes")}
            c["earlier_ratings"] = first["earlier_ratings"] + [old]
            kept[next(i for i, x in enumerate(kept) if x is first)] = c
            seen[k] = c
            excluded.append({"source": first["source"], "video_id": first["video_id"], "clip_id": first["gold_clip_id"],
                             "reason": f"same cut re-rated as {c['clip_id']} (kept the later rating "
                                       f"{c['founder_would_post']}; earlier {first['founder_would_post']} kept in "
                                       f"earlier_ratings)"})
        else:
            seen[k] = c
            kept.append(c)

    for i, a in enumerate(kept):
        for b in kept[i + 1:]:
            if a["video_id"] != b["video_id"]:
                continue
            ov = overlap(a["time_segments"], b["time_segments"])
            if ov >= SAME_MOMENT_OVERLAP * min(a["play_s"], b["play_s"]):
                a["same_moment_as"].append(b["clip_id"])
                b["same_moment_as"].append(a["clip_id"])

    # consistency with the founder verdicts stored in data/gold (latest round only)
    for c in kept:
        if c["source"].startswith("gt_"):
            gp = ROOT / "data" / "gold" / f"{c['video_id']}.json"
            g = {x.get("id"): x for x in load_json(gp).get("clips") or []} if gp.exists() else {}
            gc = g.get(c["gold_clip_id"])
            if gc and gc.get("founder_would_post") in RATED and gc["founder_would_post"] != c["founder_would_post"]:
                same = seg_key(gc.get("time_segments") or []) == seg_key(c["time_segments"])
                notes.append(f"{c['clip_id']} ({c['video_id']}): rated {c['founder_would_post']} here, data/gold says "
                             f"{gc['founder_would_post']} ({'same' if same else 'a different'} cut)")
    return kept, excluded, notes


def summary(clips, excluded, notes):
    out = [f"{len(clips)} clips"]
    by_src = Counter((c["source"], c["founder_would_post"]) for c in clips)
    srcs = list(dict.fromkeys(c["source"] for c in clips))
    out.append(f"{'source':12s} {'n':>3s} {'yes':>4s} {'maybe':>6s} {'no':>4s}")
    for s in srcs + ["TOTAL"]:
        f = (lambda v: by_src[(s, v)]) if s != "TOTAL" else (lambda v: sum(by_src[(x, v)] for x in srcs))
        out.append(f"{s:12s} {sum(f(v) for v in RATED):3d} {f('yes'):4d} {f('maybe'):6d} {f('no'):4d}")
    ser = Counter((c["series"], c["founder_would_post"]) for c in clips)
    out.append("")
    for s in dict.fromkeys(c["series"] for c in clips):
        out.append(f"  {s}: {sum(ser[(s, v)] for v in RATED)} ({', '.join(f'{ser[(s, v)]} {v}' for v in RATED)})")
    n_co = sum(c["cold_open"] for c in clips)
    n_ver = Counter(c["cut_verified"] for c in clips)
    linked = sum(1 for c in clips if c["same_moment_as"])
    out.append(f"\ncold opens: {n_co}; cut verified against the rated preview: {n_ver[True]} yes / "
               f"{n_ver[False]} no / {n_ver[None]} not checked; clips sharing a moment with another clip in the set: "
               f"{linked}")
    if excluded:
        out.append(f"\nexcluded ({len(excluded)}):")
        out += [f"  {e['source']} {e['video_id']} {e['clip_id']}: {e['reason']}" for e in excluded]
    if notes:
        out.append("\nnotes:")
        out += [f"  {n}" for n in notes]
    return "\n".join(out)


def main(argv=None):
    utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-verify", action="store_true", help="skip the ffprobe check against the rated previews")
    args = ap.parse_args(argv)
    clips, excluded, notes = build(verify=not args.no_verify)
    write_json(resolve(args.out), clips)
    print(summary(clips, excluded, notes))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
