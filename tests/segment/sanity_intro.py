"""Sanity check for opening teaser / intro detection on the sample data.

    python -m tests.segment.sanity_intro [scribe_dataset]

Prints, per episode: detected ranges, host / Q->A changes vs. detection off,
where each creator Short's text is found (teaser vs body); then runs the
detector on every creator-Short transcript and on each episode with its
teaser cut off (both must return nothing).
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

from shotvi.segment import from_dict, load, segment
from shotvi.segment.intro import _content_positions, detect_teasers, find_fragments
from shotvi.segment.pauses import pause_stats


def _episode(path: Path) -> None:
    tr = load(path)
    on, off = segment(tr), segment(tr, detect_intro=False)
    print(f"\n== {tr.video_id}: {tr.duration / 60:.1f} min, {len(tr.words)} words")
    for r in on.intro_ranges:
        ev = {k: v for k, v in r.evidence.items()}
        print(f"  {r.kind:6s} words {r.i0}-{r.i1}  {r.start:.1f}-{r.end:.1f}s  conf {r.confidence}  {ev}")
        for f in r.fragments:
            print(f"      {f.start:7.1f}-{f.end:6.1f}s  -> body {f.body_start:7.1f}-{f.body_end:7.1f}s  "
                  f"matched {f.n_matched:3d} info {f.info:5.1f} sim {f.similarity:.2f}")
    for name, s in (("off", off), ("on", on)):
        print(f"  [{name:3s}] host={s.host} conf={s.host_confidence} "
              + " ".join(f"{k}:{v.role}/score={v.host_score:.3f}/q={v.n_question_turns}" for k, v in s.roles.items())
              + f"  links={len(s.links)} units={len(s.units)} rendered_tokens={s.stats()['rendered_tokens_est']}")
    key = lambda l: (round(l.q_core_start, 2), round(l.a_start, 2))  # noqa: E731
    kon = {key(l) for l in on.links}
    gone = [l for l in off.links if key(l) not in kon]
    for l in gone:
        print(f"  link removed: {l.kind} asked by {on.role(l.asker)} at {l.q_core_start:.1f}s, "
              f"answer {l.a_start:.1f}-{l.a_end:.1f}s")
    cp, toks = _content_positions(tr)
    idx: dict[tuple, list[int]] = {}
    for j in range(len(toks) - 2):
        idx.setdefault(tuple(toks[j:j + 3]), []).append(cp[j])
    for sp in sorted(glob.glob(str(path.parent / "creator_shorts" / "*_scribe_transcript.json"))):
        s = load(sp)
        _, st = _content_positions(s)
        hits = [w for j in range(len(st) - 2) for w in idx.get(tuple(st[j:j + 3]), [])]
        ex = sum(on.overlaps_excluded(w, w) for w in hits)
        print(f"  short {s.video_id}: 3-gram hits in excluded ranges {ex:3d}, in body {len(hits) - ex:3d}")


def _negatives(root: Path) -> int:
    bad = 0
    for sp in sorted(glob.glob(str(root / "*" / "creator_shorts" / "*_scribe_transcript.json"))):
        tr = load(sp)
        rs = detect_teasers(tr, pause_stats(tr))
        if rs:
            bad += 1
            print(f"  FALSE POSITIVE short {tr.video_id}: {[(r.kind, r.start, r.end) for r in rs]}")
    print(f"  creator shorts checked: {len(glob.glob(str(root / '*' / 'creator_shorts' / '*_scribe_transcript.json')))}"
          f", with detections: {bad}")
    for p in sorted(root.glob("*/*_scribe_transcript.json")):
        tr = load(p)
        rs = [r for r in detect_teasers(tr, pause_stats(tr)) if r.kind == "teaser"]
        if not rs:
            continue
        cut = rs[-1].end + 0.01
        d = json.loads(p.read_text(encoding="utf-8"))
        sh = lambda x: dict(x, start=x["start"] - cut, end=x["end"] - cut)  # noqa: E731
        d2 = {"word_timestamps": [sh(w) for w in d["word_timestamps"] if w["start"] >= cut],
              "audio_events": [sh(e) for e in d.get("audio_events", []) if e["start"] >= cut],
              "audio_duration_secs": d["audio_duration_secs"] - cut}
        tb = from_dict(d2)
        r2 = detect_teasers(tb, pause_stats(tb))
        fr = find_fragments(tb)
        bad += bool(r2)
        print(f"  {tr.video_id} with teaser cut off: detections {[(r.kind, round(r.start), round(r.end)) for r in r2]}"
              f", stray fragments {len(fr)}")
    return bad


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    root = Path(argv[0] if argv else "scribe_dataset")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for p in sorted(root.glob("*/*_scribe_transcript.json")):
        _episode(p)
    print("\n== negatives (must be empty)")
    return 1 if _negatives(root) else 0


if __name__ == "__main__":
    raise SystemExit(main())
