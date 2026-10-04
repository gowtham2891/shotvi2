"""Cut-style statistics for a run's shown proposals (free, no LLM).

usage: python -m shotvi.select.cutstats runs/<run_id> [runs/<run_id2> ...]

Definitions (inner cuts = forward gaps between consecutive pieces, backward jumps excluded):
- edge inside a sentence: the word before the edge does not end with . ? ! । and the speaker does not change
  across it (each inner cut has two edges: after the left piece, before the right piece);
- micro content trim: the cut removes 1-3 words and at least one is not a filler / listener backchannel;
- filler/ack-only cut: every removed word is a filler or a listener backchannel (pause-only cuts counted apart);
- starts mid-sentence: the clip's first word follows a same-speaker word without end punctuation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from shotvi.segment import lexicon as L
from shotvi.segment.text import ends_sentence, norm

from .config import load_config
from .context import load_ctx


def stats(ctx, proposals) -> dict:
    ws = ctx.words

    def inside(i):  # edge between word i and i+1
        return not ends_sentence(ws[i].text) and ws[i].speaker == ws[i + 1].speaker

    edges = edges_in = cuts = micro = filler = pause_only = starts_mid = 0
    for p in proposals:
        P = [(q["start_word"], q["end_word"]) for q in p["pieces"]]
        s = P[0][0]
        if s > 0 and inside(s - 1):
            starts_mid += 1
        for (a0, a1), (b0, b1) in zip(P, P[1:]):
            if b0 <= a1:
                continue
            cuts += 1
            edges += 2
            edges_in += inside(a1) + inside(b0 - 1)
            gap = list(range(a1 + 1, b0))
            if not gap:
                pause_only += 1
                continue
            fill = [norm(ws[i].text) in L.FILLERS or i in ctx.bc_kind for i in gap]
            if all(fill):
                filler += 1
            elif len(gap) <= 3:
                micro += 1
    pct = lambda x, d: round(100 * x / d, 1) if d else None  # noqa: E731
    return {"clips": len(proposals), "inner_cuts": cuts, "edge_inside_sentence_pct": pct(edges_in, edges),
            "micro_content_trim_pct": pct(micro, cuts), "filler_ack_only_pct": pct(filler, cuts),
            "pause_only_pct": pct(pause_only, cuts), "starts_mid_sentence": f"{starts_mid}/{len(proposals)}"}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config()
    ctxs = {}
    for run in argv:
        agg = []
        for f in sorted(Path(run).glob("*.json")):
            if f.name == "cost.json":
                continue
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("kind") != "proposals":
                continue
            path = d["transcript"]["path"]
            if path not in ctxs:
                ctxs[path] = load_ctx(path, cfg)
            s = stats(ctxs[path], d["proposals"])
            agg.append((d["video_id"], s))
            print(run, d["video_id"], s)
        if len(agg) > 1:
            allp = []
            for f in sorted(Path(run).glob("*.json")):
                d = json.loads(f.read_text(encoding="utf-8")) if f.name != "cost.json" else {}
                if d.get("kind") == "proposals":
                    allp.append((ctxs[d["transcript"]["path"]], d["proposals"]))
            tot = {"edges": 0}
            print(run, "ALL", _pooled(allp))


def _pooled(items):
    keys = ("inner_cuts",)
    parts = [stats(c, p) for c, p in items]
    n = sum(x["inner_cuts"] for x in parts)
    w = lambda k: round(sum((x[k] or 0) * x["inner_cuts"] for x in parts) / n, 1) if n else None  # noqa: E731
    sm = [x["starts_mid_sentence"].split("/") for x in parts]
    return {"inner_cuts": n, "edge_inside_sentence_pct": w("edge_inside_sentence_pct"),
            "micro_content_trim_pct": w("micro_content_trim_pct"), "filler_ack_only_pct": w("filler_ack_only_pct"),
            "pause_only_pct": w("pause_only_pct"),
            "starts_mid_sentence": f"{sum(int(a) for a, _ in sm)}/{sum(int(b) for _, b in sm)}"}


if __name__ == "__main__":
    main()
