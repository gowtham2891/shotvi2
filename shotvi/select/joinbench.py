"""Join-checker bake-off on founder-labelled joins (IN-SAMPLE).

usage: python -m shotvi.select.joinbench --model gemini-3.5-flash-lite [--thinking low] --out runs/calib_r7/lite.json

Positives: joins the founder called bad in sel_r4_flash (ratings_sel_r4_flash.csv notes), located by word index.
Negatives: every join of the sel_r5_flash FS1 top 5 (all rated 5/5 "yes"). Runs the current ``joins`` prompt once
per clip (round 1 only, nothing applied) and reports catches, false alarms and the proposed repairs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from shotvi.llm import Ledger, LLMClient, ModelSpec

from .config import load_config
from .context import load_ctx
from .joins import ACTIONS, SCHEMA, render
from .prompts import load_prompt

# (video, clip, accepted join ids, label) - located with the founder's quoted words (see the round-6 report)
POSITIVES = [
    ("FS1DI5D_hUk", "c006", {"J1", "J2"}, "answer 'నేను నమ్మను...' without the host's question"),
    ("FS1DI5D_hUk", "c006", {"J3"}, "answer 'ఆ అలా అస్సలు లేదు. I don't carry' without its question"),
    ("FS1DI5D_hUk", "c005", {"J1"}, "cold-open question followed by an unrelated line"),
    ("FS1DI5D_hUk", "c008", {"J4"}, "cut after 'Guinness world record fifty' (thought incomplete)"),
    ("TBlm6wcij94", "c004", {"J4"}, "cut after 'food control ఉండదు.'"),
    ("TBlm6wcij94", "c004", {"J5"}, "cut after '...instable saying that'"),
    ("TBlm6wcij94", "c022", {"J5"}, "cut after 'Growth hormone ఏం చేస్తది?'"),
    ("TBlm6wcij94", "c022", {"J11"}, "cut before 'For an IT employee' (topic shift)"),
    ("TBlm6wcij94", "c006", {"J2"}, "cut before 'forces'"),
]
NEG_RUN, NEG_VIDEO, NEG_TOP = "runs/sel_r5_flash", "FS1DI5D_hUk", 5


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--thinking", default="low")
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget-usd", type=float, default=0.06)
    a = ap.parse_args(argv)
    load_dotenv()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config()
    spec = ModelSpec("gemini", a.model, {"thinking_level": a.thinking})
    ledger = Ledger(max_usd=a.budget_usd)
    llm = LLMClient(ledger, cache_dir=cfg["cache_dir"])
    p = load_prompt("joins")
    ctxs, docs = {}, {}

    def clip(run, vid, cid):
        if vid not in ctxs:
            ctxs[vid] = load_ctx(f"scribe_dataset/{vid}/{vid}_scribe_transcript.json", cfg)
        key = (run, vid)
        if key not in docs:
            docs[key] = json.loads((Path(run) / f"{vid}.json").read_text(encoding="utf-8"))
        doc = docs[key]
        pr = next(x for x in doc["proposals"] if x["id"] == cid)
        return ctxs[vid], [[q["start_word"], q["end_word"]] for q in pr["pieces"]]

    def check(run, vid, cid):
        ctx, P = clip(run, vid, cid)
        c, j, ids = render(ctx, P)
        obj = llm.generate_json("joiner", spec, p.system, p.render(clip=c, joins=j), SCHEMA,
                                meta={"bench": True, "video": vid, "cand": cid, "run": run})
        fixes = [f for f in obj.get("fixes", []) if f.get("action") in ACTIONS]
        return ids, fixes

    out = {"model": a.model, "thinking": a.thinking, "prompt": load_prompt("joins").tag, "in_sample": True,
           "positives": [], "negatives": []}
    pos_cache = {}
    for vid, cid, ok_ids, label in POSITIVES:
        if (vid, cid) not in pos_cache:
            pos_cache[(vid, cid)] = check("runs/sel_r4_flash", vid, cid)
        ids, fixes = pos_cache[(vid, cid)]
        hit = [f for f in fixes if str(f["join"]).upper() in ok_ids]
        out["positives"].append({"video": vid, "clip": cid, "joins": sorted(ok_ids), "label": label,
                                 "caught": bool(hit), "fix": hit[0] if hit else None})
    for vid, cid in sorted(pos_cache):
        ids, fixes = pos_cache[(vid, cid)]
        labelled = set().union(*[ok for v, c, ok, _ in POSITIVES if (v, c) == (vid, cid)])
        out.setdefault("unlabelled_flags_on_positive_clips", []).extend(
            {"video": vid, "clip": cid, **f} for f in fixes if str(f["join"]).upper() not in labelled)
    neg = json.loads((Path(NEG_RUN) / f"{NEG_VIDEO}.json").read_text(encoding="utf-8"))["proposals"][:NEG_TOP]
    n_joins = 0
    for pr in neg:
        ids, fixes = check(NEG_RUN, NEG_VIDEO, pr["id"])
        n_joins += len(ids)
        out["negatives"].append({"clip": pr["id"], "n_joins": len(ids), "false_alarms": fixes})
    caught = sum(x["caught"] for x in out["positives"])
    fa = sum(len(x["false_alarms"]) for x in out["negatives"])
    cost = ledger.summary()
    n_calls_clip = len(pos_cache) + len(neg)
    out["summary"] = {"caught": f"{caught}/{len(POSITIVES)}", "false_alarms": fa, "negative_clips": len(neg),
                      "negative_joins": n_joins, "usd": cost["usd"], "calls": cost["n_llm_calls"],
                      "inr_per_clip": round(cost["usd"] * 90 / max(n_calls_clip, 1), 3)}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False))
    for x in out["positives"]:
        f = x["fix"]
        print(f"  {'CAUGHT' if x['caught'] else 'missed'} {x['video'][:4]} {x['clip']} {x['joins']}: "
              f"{(f['problem'], f['action'], f['to']) if f else ''}")
    for x in out["negatives"]:
        print(f"  neg {x['clip']} joins {x['n_joins']} false alarms {[(f['join'], f['problem']) for f in x['false_alarms']]}")


if __name__ == "__main__":
    main()
