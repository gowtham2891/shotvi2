"""Cut rough preview MP4s of a run's top clips so a human can watch and rate them.

Plain jump-cuts from the source video in playback order: no captions, no
reframing (that's the later rendering phase). Each piece gets a small pad
that never reaches into the neighbouring word, and a short audio fade so the
joins don't click. Beside each video goes a transcript (preview time, source
time, speaker, and the words cut between pieces), plus index.md and a
ratings.csv to fill in.

When an ASR correction layer exists (runs/asr_fix/<video_id>/corrections.json), transcripts show the corrected
words, marked (page: hover / tap shows what Scribe heard and why; .txt: [✎ ...] plus a list under the transcript),
and low-confidence words with no correction get "(?)". Scribe's transcript is never changed. --no-asr-fix: off.

usage: python tools/preview_clips.py runs/<run_id> [--top 5] [--video <id>] [--skip-existing] [--no-asr-fix]
                                     [--reuse runs/<earlier_run> ...]
writes runs/previews/<run_id>/<video_id>/NN_<clip_id>.mp4 and NN_<clip_id>.txt

--reuse RUN_DIR (repeatable): copy an already rendered preview (runs/previews/<RUN_DIR name>/...) instead of
rendering again when that run has a clip of the same video with exactly the same played segments.
"""
import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

from preview_page import build_page

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shotvi.segment import asr_fix  # noqa: E402

TXT_FIXED = "[✎ %s]"   # .txt form of a corrected span

PRE_PAD = 0.10    # s before a piece's first word
POST_PAD = 0.20   # s after a piece's last word
FADE = 0.03       # s audio fade at every join
LEAD_S = 2.0      # input seek margin
JUMP_FADE = 0.12      # s audio fade on both sides of a cold-open jump (a softer join than a plain cut)
JUMP_MATCH_S = 4.0    # a cold-open segment is levelled to this much of the scene that follows it
JUMP_MAX_GAIN = 9.0   # dB, cap on that levelling


def padded_segments(pieces, words):
    """Pad each piece into the silence around it (at most halfway to the neighbour word);
    merge pieces that end up touching in source order."""
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


def mmss(t):
    t = max(0.0, t)
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def speaker_labels(vid):
    """H/G labels from the segmenter's role inference, if a segment run exists."""
    for run in ("segment_r2", "segment_r1"):
        p = Path("runs") / run / vid / "segments.json"
        if p.exists():
            sp = json.loads(p.read_text(encoding="utf-8")).get("speakers") or []
            return {s["id"]: s.get("label") or s["id"] for s in sp}
    return {}


def hook_text(c):
    h = c.get("hook_line", "")
    return h.get("text", "") if isinstance(h, dict) else h


def asr_marks(vid, words, on=True):
    """Display marks from the ASR correction layer, or None (layer off / not built for this video)."""
    if not on or not asr_fix.corrections_path(vid).exists():
        return None
    corr = asr_fix.load_corrections(vid)
    stale = asr_fix.stale_corrections(words, corr)
    if stale:
        print(f"warning: {vid}: {len(stale)} ASR corrections no longer match the transcript; not shown")
    return asr_fix.word_marks(words, corr)


def _say_text(words, idxs, marks):
    """(text, parts): plain text with corrections applied; parts (only when a word is marked) for the page:
    runs of plain words {"t"} and marked tokens {"t", "k": "fix"|"u", "tip"}."""
    if marks is None:
        return " ".join(words[i]["word"] for i in idxs), None
    toks = asr_fix.marked_tokens(words, marks, idxs)
    text = " ".join(t["text"] for t in toks)
    if all(t["kind"] == "word" for t in toks):
        return text, None
    parts = []
    for t in toks:
        if t["kind"] == "word":
            if parts and "k" not in parts[-1]:
                parts[-1]["t"] += " " + t["text"]
            else:
                parts.append({"t": t["text"]})
        else:
            parts.append({"t": t["text"], "k": "fix" if t["kind"] == "fixed" else "u", "tip": asr_fix.mark_tip(t)})
    return text, parts


def _say(t, src, spk, words, idxs, marks):
    text, parts = _say_text(words, idxs, marks)
    d = {"kind": "say", "t": t, "src": src, "spk": spk, "text": text}
    if parts:
        d["parts"] = parts
    return d


def transcript_lines(c, segs, words, labels, marks=None):
    """Structured transcript: speech lines {kind, t (preview s), src (episode s), spk, text},
    plus cut / jump markers between pieces. ``marks`` (from ``asr_marks``): text shows the corrections, and lines
    with a marked word carry ``parts``."""
    starts, acc = [], 0.0          # preview offset of each padded segment
    for a, b in segs:
        starts.append(acc)
        acc += b - a
    out, prev, k = [], None, 0
    for p in c["pieces"]:
        # search forward from the last segment: a cold open plays a moment twice (lifted to the front, then in place)
        k = next((i for i in range(k, len(segs)) if segs[i][0] - 0.01 <= p["start"] <= segs[i][1] + 0.01),
                 next(i for i, (a, b) in enumerate(segs) if a - 0.01 <= p["start"] <= b + 0.01))
        if prev is not None:
            if p["start_word"] > prev["end_word"] + 1:
                gap = words[prev["end_word"] + 1:p["start_word"]]
                text, parts = _say_text(words, range(prev["end_word"] + 1, p["start_word"]), marks)
                out.append({"kind": "cut", "dur": round(gap[-1]["end"] - gap[0]["start"], 1), "text": text,
                            **({"parts": parts} if parts else {})})
            elif p["start_word"] < prev["end_word"]:
                out.append({"kind": "jump", "src": p["start"]})
        line, spk, t_line, t_src = [], None, None, None
        for i in range(p["start_word"], p["end_word"] + 1):
            w = words[i]
            if w.get("speaker") != spk and line:
                out.append(_say(t_line, t_src, labels.get(spk, spk or "?"), words, line, marks))
                line = []
            if not line:
                spk, t_src = w.get("speaker"), w["start"]
                t_line = round(starts[k] + (w["start"] - segs[k][0]), 2)
            line.append(i)
        if line:
            out.append(_say(t_line, t_src, labels.get(spk, spk or "?"), words, line, marks))
        prev = p
    return out


def clip_meta(c):
    s = c.get("scores", {})
    return {"rank": c["rank"], "id": c["id"], "title": c["title"], "hook": hook_text(c),
            "why": c.get("why_it_works", ""), "flags": c.get("flags") or [], "tier": c.get("tier", ""),
            "tier_label": c.get("tier_label") or c.get("tier", ""),
            "payoff": c.get("payoff_line") or "", "payoff_type": c.get("payoff_type") or "",
            "review_note": c.get("review_note") or "",
            "category": " · ".join(x for x in (c.get("content_type", ""), c.get("clip_purpose", ""),
                                                c.get("category", "")) if x),
            "score": s.get("overall"), "scores": s,
            "duration": round(c["edited_duration"], 1), "n_pieces": len(c["pieces"]),
            "src_start": c["region"]["start"], "src_end": c["region"]["end"],
            "edit_notes": [n.get("note") or n.get("reason") or "" for n in c.get("edit_notes") or []]}


def _txt(ln):
    """.txt form of a line's words: corrected spans as [✎ ...], low-confidence words as word(?)."""
    if not ln.get("parts"):
        return ln["text"]
    return " ".join(TXT_FIXED % x["t"] if x.get("k") == "fix" else x["t"] + "(?)" if x.get("k") == "u" else x["t"]
                    for x in ln["parts"])


def transcript_text(meta, lines, video_name):
    """Readable .txt beside the video."""
    out = [f"#{meta['rank']} {meta['title']}",
           f"Video: {video_name} | {meta['duration']:.0f}s edited | source {mmss(meta['src_start'])}–"
           f"{mmss(meta['src_end'])} | {meta['n_pieces']} pieces | pipeline score {meta['score']} "
           f"({meta.get('tier_label') or meta['tier']})"]
    if meta.get("review_note"):
        out.append(f"Note: {meta['review_note']}")
    out.append(f"Hook: {meta['hook']}")
    if meta.get("payoff"):
        out.append(f"Payoff ({meta.get('payoff_type') or '?'}): {meta['payoff']}")
    out.append(f"Why: {meta['why']}")
    if meta["flags"]:
        out.append(f"Flags: {', '.join(meta['flags'])}")
    out += [f"Edit note: {n}" for n in meta["edit_notes"] if n]
    out += ["", "[preview time] (source time) speaker: words      ✂ = words cut between pieces", ""]
    marked = any(ln.get("parts") for ln in lines)
    if marked:
        out.insert(-1, "[✎ words] = ASR fix (Scribe misheard; see the list at the end)      word(?) = low ASR "
                       "confidence")
    fixes = []
    for ln in lines:
        if ln["kind"] == "cut":
            t = _txt(ln)
            out.append(f"      ✂ cut {ln['dur']:.1f}s: {t[:160]}{'…' if len(t) > 160 else ''}")
        elif ln["kind"] == "jump":
            out.append(f"      ↺ jumps back to {mmss(ln['src'])} (reordered)")
        else:
            out.append(f"[{mmss(ln['t'])}] ({mmss(ln['src'])}) {ln['spk']}: {_txt(ln)}")
        fixes += [x for x in ln.get("parts") or [] if x.get("k") == "fix"]
    fixes = list(dict.fromkeys(f"  [✎ {x['t']}] " + x["tip"].replace("\n", " | ") for x in fixes))  # cold opens
    if fixes:                                                                                 # replay a moment
        out += ["", "ASR fixes (Scribe's transcript itself is unchanged):"] + fixes
    return "\n".join(out) + "\n"


def mean_db(src, a, b):
    """Mean loudness (dB) of the source audio in [a, b] (ffmpeg volumedetect)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{a:.3f}", "-t", f"{max(0.1, b - a):.3f}", "-i", str(src),
                        "-vn", "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0])
    return None


def jump_gains(src, segs):
    """Gain (dB) per segment: a segment followed by a backward jump (a cold open lifted from later in the scene) is
    levelled to the first JUMP_MATCH_S of the segment that follows, so the cut back into the scene doesn't jump in
    volume. Other segments are untouched."""
    gains = [0.0] * len(segs)
    for k in range(len(segs) - 1):
        (s, e), (s2, e2) = segs[k], segs[k + 1]
        if s2 >= s:
            continue
        own, nxt = mean_db(src, s, e), mean_db(src, s2, min(e2, s2 + JUMP_MATCH_S))
        if own is not None and nxt is not None:
            gains[k] = max(-JUMP_MAX_GAIN, min(JUMP_MAX_GAIN, nxt - own))
    return gains


def render(src, segs, out):
    t0 = max(0.0, min(s for s, _ in segs) - LEAD_S)
    t1 = max(e for _, e in segs) + LEAD_S
    gains = jump_gains(src, segs)
    parts, labels = [], []
    for k, (s, e) in enumerate(segs):
        a, b, d = s - t0, e - t0, e - s
        jump_in = k > 0 and s < segs[k - 1][0]            # entering the scene after a cold open
        jump_out = k + 1 < len(segs) and segs[k + 1][0] < s
        fi, fo = (JUMP_FADE if jump_in else FADE), (JUMP_FADE if jump_out else FADE)
        vol = f"volume={gains[k]:.1f}dB," if abs(gains[k]) >= 0.5 else ""
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{k}]")
        parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,{vol}"
                     f"afade=t=in:d={fi},afade=t=out:st={max(0, d - fo):.3f}:d={fo}[a{k}]")
        labels.append(f"[v{k}][a{k}]")
    parts.append(f"{''.join(labels)}concat=n={len(segs)}:v=1:a=1[v][a]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", str(src),
           "-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
           "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {out.name}: {r.stderr[-400:]}")


def seg_key(segs):
    return tuple((round(a, 3), round(b, 3)) for a, b in segs)


def clip_segments(c, words):
    """Played segments: web-series clips carry time_segments (may include silent visual beats); else padded pieces."""
    return ([(t["start"], t["end"]) for t in c["time_segments"]] if c.get("time_segments")
            else padded_segments(c["pieces"], words))


def reuse_index(run_dirs, vid, words):
    """{segment key: rendered mp4} for this video's clips in earlier runs whose previews exist."""
    out = {}
    for rd in run_dirs:
        f = Path(rd) / f"{vid}.json"
        if not f.exists():
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        for c in doc.get("proposals") or []:
            mp4 = Path("runs/previews") / Path(rd).name / vid / f"{c['rank']:02d}_{c['id']}.mp4"
            if mp4.exists():
                out.setdefault(seg_key(clip_segments(c, words)), mp4)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--video")
    ap.add_argument("--skip-existing", action="store_true", help="don't re-render videos that exist")
    ap.add_argument("--no-asr-fix", action="store_true",
                    help="show Scribe's words as is (no ASR corrections, no low-confidence marks)")
    ap.add_argument("--reuse", action="append", default=[], metavar="RUN_DIR",
                    help="copy an earlier run's rendered preview when its clip plays exactly the same segments")
    args = ap.parse_args()
    run = Path(args.run)
    out_root = Path("runs/previews") / run.name
    rows, page = [], []
    index = [f"# Preview clips: `{run.name}`\n",
             "Rough jump-cuts from the source (no captions or reframing). Open `review.html` to watch and rate.\n"]
    for f in sorted(run.glob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        if doc.get("kind") not in ("proposals", "gold") or (args.video and doc["video_id"] != args.video):
            continue
        if doc["kind"] == "gold":   # GT picks have no rank: keep the labeller's order
            doc["proposals"] = [{**c, "rank": c.get("rank") or i + 1} for i, c in enumerate(doc.get("clips") or [])]
        vid = doc["video_id"]
        vdir = Path("scribe_dataset") / vid
        words = json.loads((vdir / f"{vid}_scribe_transcript.json").read_text(encoding="utf-8"))["word_timestamps"]
        labels = speaker_labels(vid)
        marks = asr_marks(vid, words, not args.no_asr_fix)
        if marks is not None:
            print(f"{vid}: ASR layer: {len({id(m) for m in marks.values() if m['kind'] == 'fixed'})} fixes, "
                  f"{sum(m['kind'] == 'unsure' for m in marks.values())} low-confidence words marked")
        out_dir = out_root / vid
        out_dir.mkdir(parents=True, exist_ok=True)
        reuse = reuse_index(args.reuse, vid, words) if args.reuse else {}
        index.append(f"\n## {vid}\n")
        page.append({"video_id": vid, "n_total": len(doc["proposals"]), "clips": [],
                     **({"verdict": doc["episode_verdict"]} if doc.get("episode_verdict") else {})})
        for c in sorted(doc["proposals"], key=lambda c: c["rank"])[:args.top]:
            out = out_dir / f"{c['rank']:02d}_{c['id']}.mp4"
            # web-series GT clips carry time_segments (may include silent visual beats): play those as is
            segs = clip_segments(c, words)
            if not (out.exists() and args.skip_existing):
                same = reuse.get(seg_key(segs))
                if same is not None and same.resolve() != out.resolve():
                    shutil.copy2(same, out)
                    print(f"{vid} #{c['rank']}: same cut as {same.as_posix()}: copied")
                else:
                    render(vdir / f"{vid}.mp4", segs, out)
            meta, lines = clip_meta(c), transcript_lines(c, segs, words, labels, marks)
            out.with_suffix(".txt").write_text(transcript_text(meta, lines, out.name), encoding="utf-8")
            page[-1]["clips"].append({**meta, "video": out.relative_to(out_root).as_posix(), "lines": lines})
            s = c.get("scores", {})
            print(f"{vid} #{c['rank']}: {len(c['pieces'])} pieces -> {len(segs)} cuts -> {out}")
            index.append(f"- **#{c['rank']} {c['title']}** ({c['edited_duration']:.0f}s, "
                         f"{c.get('tier_label') or c.get('tier', '')}, pipeline score "
                         f"{s.get('overall')}) — `{out.relative_to(out_root).as_posix()}` + transcript "
                         f"`{out.with_suffix('.txt').relative_to(out_root).as_posix()}`\n"
                         f"  - hook: {hook_text(c)}\n  - why: {c.get('why_it_works', '')}\n")
            rows.append({"video_id": vid, "rank": c["rank"], "clip_id": c["id"], "title": c["title"],
                         "file": out.relative_to(out_root).as_posix(), "pipeline_overall": s.get("overall"),
                         "hook_1to5": "", "standalone_1to5": "", "payoff_1to5": "", "overall_1to5": "",
                         "would_post": "", "notes": ""})
    (out_root / "index.md").write_text("".join(index), encoding="utf-8")
    (out_root / "review.html").write_text(build_page(run.name, page), encoding="utf-8")
    sheet = out_root / "ratings.csv"
    if sheet.exists():   # never wipe ratings someone already entered
        print(f"kept existing {sheet}")
    else:
        with open(sheet, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    print(f"review page: {out_root / 'review.html'}\nratings sheet: {sheet}")


if __name__ == "__main__":
    main()
