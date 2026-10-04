"""Build a fake proposals run from gold files, to exercise eval.run / eval.compare before real runs exist.

usage: python -m eval.fake_run --out runs/eval_fake_gold [--untighten] [--shuffle SEED]

--untighten  replace each clip's keep list by its block spans (no tightening, stitches kept): a
             "no tightening" baseline and a regression-test fixture for eval.compare.
--shuffle    randomise the ranking (scores kept) to test rank-sensitive metrics.
"""
import argparse
import random
from datetime import datetime, timezone

from .common import ROOT, SKIP_S, T_REC, clips_of, episode_for, load_json, write_json
from .metrics import block_spans

T_SHOW = 3.0   # fixture shows every gold clip (gold overall >= 3); the real pipeline uses spec Q2 (3.5)


def span(W, a, b):
    return {"start_word": a, "end_word": b, "start": round(W[a]["start"], 3), "end": round(W[b]["end"], 3)}


def core(W, pieces):
    lo = min(p["start_word"] for p in pieces)
    hi = max(p["end_word"] for p in pieces)
    ed = sum(W[p["end_word"]]["end"] - W[p["start_word"]]["start"] for p in pieces)
    re_ = any(b["start_word"] <= a["end_word"] for a, b in zip(pieces, pieces[1:]))
    jumps = sum(1 for a, b in zip(pieces, pieces[1:])
                if b["start_word"] <= a["end_word"] or W[b["start_word"]]["start"] - W[a["end_word"]]["end"] > SKIP_S)
    return span(W, lo, hi), round(ed, 3), re_, 1 + jumps


def gold_to_proposals(gdoc, run_id, untighten=False, shuffle=None):
    ep = episode_for(gdoc)
    W = ep.W
    clips = clips_of(gdoc)
    order = list(range(len(clips)))
    if shuffle is not None:
        random.Random(shuffle).shuffle(order)
    props = []
    for rank, i in enumerate(order, 1):
        g = clips[i]
        if untighten:
            pieces = [span(W, a, b) for a, b in block_spans(g, ep)]
        else:
            pieces = [span(W, p["start_word"], p["end_word"]) for p in g["pieces"]]
        region, ed, re_, nb = core(W, pieces)
        o = g["scores"]["overall"]
        hl = g.get("hook_line") or {"start_word": pieces[0]["start_word"],
                                    "end_word": min(pieces[0]["end_word"], pieces[0]["start_word"] + 6)}
        hl = {"text": " ".join(W[j]["word"] for j in range(hl["start_word"], hl["end_word"] + 1)),
              "start_word": hl["start_word"], "end_word": hl["end_word"]}
        # recompute jump notes at the current skip_s (gold files were written with 1.0's 20s)
        gold_notes = g.get("edit_notes") or []
        skip_reasons = [n["reason"] for n in gold_notes if n["type"] == "skip"]
        back_notes = [n for n in gold_notes if n["type"] in ("cold_open", "reorder")]
        notes = []
        for k, (a, b) in enumerate(zip(pieces, pieces[1:])):
            if b["start_word"] <= a["end_word"]:
                bn = back_notes.pop(0) if back_notes else {"type": "cold_open" if k == 0 else "reorder",
                                                             "reason": "Fixture: backward jump from gold."}
                notes.append({"type": bn["type"], "after_piece": k, "reason": bn["reason"]})
            elif W[b["start_word"]]["start"] - W[a["end_word"]]["end"] > SKIP_S:
                notes.append({"type": "skip", "after_piece": k,
                              "reason": skip_reasons.pop(0) if skip_reasons else "Fixture: skip from gold stitch."})
        if ed > 120 and not any(n["type"] == "length_over_soft_max" for n in notes):
            notes.append({"type": "length_over_soft_max", "reason": "Fixture: gold clip over the soft max."})
        props.append({
            "id": g["id"], "pieces": pieces, "region": region, "edited_duration": ed, "reordered": re_,
            "n_blocks": nb,
            "text": " ".join(W[j]["word"] for p in pieces for j in range(p["start_word"], p["end_word"] + 1)),
            "rank": rank,
            "tier": "recommended" if o >= T_REC else "worth_a_look" if o >= T_SHOW else "below_threshold",
            "scores": g["scores"], "title": g["title"], "hook_line": hl, "why_it_works": g["why_it_works"],
            "category": g["category"], "standalone_risks": g.get("standalone_risks") or [],
            "edit_notes": notes, "flags": g.get("flags") or [],
        })
    shown = [p for p in props if p["tier"] != "below_threshold"]
    return {
        "schema_version": "1.1", "kind": "proposals", "video_id": gdoc["video_id"],
        "transcript": gdoc["transcript"], "derived_params": {"skip_s": SKIP_S}, "run_id": run_id,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pipeline": {"version": "fixture", "git_commit": None, "prompt_versions": {},
                     "config": {"source": "data/gold", "untighten": untighten, "shuffle": shuffle}},
        "models": [{"role": "fixture", "provider": "none", "model": "gold"}],
        "cost": {"input_tokens": 0, "output_tokens": 0, "usd": 0.0, "wall_time_secs": 0.0, "n_llm_calls": 0},
        "cutoff": {"t_show": T_SHOW, "t_rec": T_REC, "n_candidates": len(props), "n_passed_hard_rules": len(props),
                   "n_shown": len(shown), "warnings": ["fixture built from gold labels; t_show lowered to 3.0"]},
        "proposals": shown,
    }


def main(argv=None):
    from .common import utf8_stdout
    utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--untighten", action="store_true")
    ap.add_argument("--shuffle", type=int, default=None)
    a = ap.parse_args(argv)
    out = ROOT / a.out if not a.out.startswith(("/", "\\")) and ":" not in a.out else a.out
    run_id = str(a.out).replace("\\", "/").rstrip("/").split("/")[-1]
    for g in sorted((ROOT / "data" / "gold").glob("*.json")):
        doc = gold_to_proposals(load_json(g), run_id, a.untighten, a.shuffle)
        write_json(f"{out}/{doc['video_id']}.json", doc)
        print(f"wrote {out}/{doc['video_id']}.json ({len(doc['proposals'])} proposals)")


if __name__ == "__main__":
    main()
