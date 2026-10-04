"""CLI: python -m shotvi.segment <transcript.json> --out runs/<id>/segments.json

Also writes ``transcript.txt`` (compact LLM rendering) next to the output and
prints stats.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .pipeline import segment_file
from .units import UnitParams


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m shotvi.segment", description=__doc__.splitlines()[0])
    ap.add_argument("transcript", help="Scribe transcript JSON")
    ap.add_argument("--out", required=True, help="output segments.json path")
    ap.add_argument("--chunk-tokens", type=int, default=12000)
    ap.add_argument("--overlap-tokens", type=int, default=1500)
    ap.add_argument("--min-unit", type=float, default=UnitParams.min_s)
    ap.add_argument("--max-unit", type=float, default=UnitParams.max_s)
    ap.add_argument("--no-render", action="store_true", help="skip writing transcript.txt")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--intro", choices=("llm", "rules", "off"), default=None,
                    help="opening teaser / intro detection (default: $SHOTVI_OPENING_MODE or llm; "
                         "llm falls back to rules on any failure)")
    ap.add_argument("--no-intro", action="store_true", help="same as --intro off")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    t0 = time.perf_counter()
    params = UnitParams(min_s=a.min_unit, max_s=a.max_unit,
                        soft_max_s=min(UnitParams.soft_max_s, a.max_unit))
    seg = segment_file(a.transcript, params, detect_intro="off" if a.no_intro else a.intro)
    d = seg.to_dict(a.chunk_tokens, a.overlap_tokens)
    elapsed = time.perf_counter() - t0
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    if not a.no_render:
        (out.parent / "transcript.txt").write_text(seg.render(legend=True) + "\n", encoding="utf-8")
    if not a.quiet:
        s = d["stats"]
        print(f"{seg.transcript.video_id}: {s['n_words']} words, {s['duration_s'] / 60:.1f} min, "
              f"segmented in {elapsed:.2f}s")
        for w in seg.transcript.warnings:
            print(f"  warning: {w}")
        sp = ", ".join(f"{x['id']}={x['role']}({x['talk_share']:.0%}, q-turns {x['n_question_turns']})"
                       for x in d["speakers"])
        for r in seg.intro_ranges:
            ev = r.evidence
            if r.kind == "teaser" and "coverage" in ev:   # rules-decided teaser
                extra = (f"{ev['n_fragments']} fragments, coverage {ev['coverage']:.0%}, "
                         f"{ev['n_body_locations']} body locations")
            else:                                          # LLM-decided ranges carry different evidence
                extra = ", ".join(f"{k}={v}" for k, v in ev.items()
                                  if not isinstance(v, (list, dict)) or k == "decided_by")[:300]
            print(f"  {r.kind}: words {r.i0}-{r.i1}, {r.start:.1f}-{r.end:.1f}s, "
                  f"confidence {r.confidence} ({extra})")
        if not seg.intro_ranges:
            print("  no opening teaser / intro detected")
        op = seg.opening
        print(f"  opening: mode={op.get('mode')} decided_by={op.get('decided_by')}"
              + (f" content_start_word={op['content_start_word']}" if "content_start_word" in op else "")
              + (f" fallback: {op['fallback_reason']}" if op.get("fallback_reason") else "")
              + (f" reason: {op['reason']}" if op.get("reason") else ""))
        print(f"  speakers: {sp}; host={s['host']} confidence={s['host_confidence']}")
        p = d["params"]["pauses"]
        print(f"  pauses: p50={p['p50']}s p90={p['p90']}s p97={p['p97']}s -> "
              f"minor={p['minor']}s major={p['major']}s long={p['long']}s")
        print(f"  turns: {s['n_turns']}, backchannels absorbed: {s['n_backchannels_absorbed']} "
              f"{s['backchannels_by_kind']}")
        ud = s["unit_duration"]
        print(f"  units: {s['n_units']} (exchange {s['units_exchange']}); duration s: "
              + " ".join(f"{k}={v}" for k, v in ud.items()))
        print(f"    <5s: {s['units_lt_5s']}, 5-40s: {s['units_5_40s']}, >40s: {s['units_gt_40s']}")
        print(f"  Q->A links: {s['n_qa_links']} {s['qa_links_by_kind']}, host-asked "
              f"{s['qa_links_host_asked']}; answer duration {s['qa_answer_duration']}")
        print(f"  rendered tokens (est.): {s['rendered_tokens_est']}; chunks: {s['n_chunks']} "
              f"@ {a.chunk_tokens}/{a.overlap_tokens} -> {s['chunk_tokens_est']}")
        from .chapters import compare, load_chapters
        ch = load_chapters(a.transcript)
        if ch:
            c = compare(seg, ch)
            cu = c["chapter_vs_units"]
            print(f"  youtube chapters: first {c['first_chapter']}, content chapter at {c['chapter_content_start_s']}s; "
                  f"teaser end - first chapter end = {c['teaser_end_minus_first_chapter_end']}s, "
                  f"content start {c['content_start_s']}s ({c['content_start_minus_chapter']:+}s vs chapter); "
                  f"chapter starts within 2s of a unit start: {cu['within_2s']}/{cu['n_chapter_starts']} "
                  f"(median {cu['median_dist_to_unit_start_s']}s, random {cu['random_baseline_median_s']}s)")
        print(f"  wrote {out}" + ("" if a.no_render else f" and {out.parent / 'transcript.txt'}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
