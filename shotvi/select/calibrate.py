"""Scorer calibration against human ratings of a run's proposals (IN-SAMPLE: the labels may be used for tuning).

usage: python -m shotvi.select.calibrate data/human/ratings_sel_r3_flash.csv --run runs/sel_r3_flash \
           --model gemini-3.8-flash [--thinking low] --out runs/calib_r4/flash.json [--budget-usd 0.1]

Re-scores exactly the rated clips (same pieces the human saw) with the current score prompt and reports Spearman
of the judge's overall (raw and after code caps) against the human overall, plus per-dimension agreement.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from shotvi.llm import Ledger, LLMClient

from .clip import Candidate
from .config import load_config
from .context import load_ctx
from .prompts import load_prompt
from .score import calibrated_scores, score

DIMS = ("hook", "standalone", "payoff", "overall")


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return cov / (va * vb) if va and vb else float("nan")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("ratings")
    ap.add_argument("--run", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--thinking", default="low")
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget-usd", type=float, default=0.2)
    ap.add_argument("--limit", type=int, default=None, help="only the first N rated clips (cost probe)")
    a = ap.parse_args(argv)
    load_dotenv()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config(None, {"models": {"scorer": {"provider": "gemini", "model": a.model,
                                                   "params": {"thinking_level": a.thinking}}}})
    ledger = Ledger(max_usd=a.budget_usd)
    llm = LLMClient(ledger, cache_dir=cfg["cache_dir"])
    rows = list(csv.DictReader(open(a.ratings, encoding="utf-8")))[: a.limit]
    ctxs, docs, out = {}, {}, []
    for r in rows:
        vid = r["video_id"]
        if vid not in docs:
            docs[vid] = json.loads((Path(a.run) / f"{vid}.json").read_text(encoding="utf-8"))
            ctxs[vid] = load_ctx(docs[vid]["transcript"]["path"], cfg)
        p = next(x for x in docs[vid]["proposals"] if x["id"] == r["clip_id"])
        cand = Candidate(p["id"], {}, [[q["start_word"], q["end_word"]] for q in p["pieces"]],
                         edit_notes=p["edit_notes"])
        j = score(ctxs[vid], cfg, llm, cand)
        capped, notes = calibrated_scores(j)
        human = {d: float(r[f"{d}_1to5"]) for d in DIMS}
        out.append({"video": vid, "clip": r["clip_id"], "human": human, "old_pipeline_overall":
                    float(r["pipeline_overall"]), "raw": {d: float(j[d]) for d in DIMS},
                    "capped": {d: capped[d] for d in DIMS}, "caps": notes,
                    "opener_problem": j["opener_problem"], "ending_problem": j["ending_problem"],
                    "unresolved": j["unresolved"], "critique": j["critique"]})
        print(f"{vid[:4]} {r['clip_id']} human {human['overall']:.0f} (h{human['hook']:.0f} s{human['standalone']:.0f}"
              f" p{human['payoff']:.0f}) | raw {j['overall']} (h{j['hook']} s{j['standalone']} p{j['payoff']}) | "
              f"capped {capped['overall']} | {j['opener_problem']}, {j['ending_problem']}, unres {len(j['unresolved'])}")
    summ = {"n": len(out), "in_sample": True, "model": a.model, "thinking": a.thinking,
            "prompt": load_prompt("score").tag}
    for kind in ("raw", "capped"):
        summ[kind] = {}
        for d in DIMS:
            h = [x["human"][d] for x in out]
            m = [x[kind][d] for x in out]
            summ[kind][d] = {"spearman": round(spearman(h, m), 3),
                             "mae": round(sum(abs(x - y) for x, y in zip(h, m)) / len(h), 2),
                             "mean_model": round(sum(m) / len(m), 2), "mean_human": round(sum(h) / len(h), 2)}
    summ["old_pipeline_overall_spearman"] = round(spearman([x["human"]["overall"] for x in out],
                                                           [x["old_pipeline_overall"] for x in out]), 3)
    summ["cost"] = ledger.summary()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"summary": summ, "clips": out}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    for kind in ("raw", "capped"):
        print(kind, {d: (v["spearman"], v["mae"]) for d, v in summ[kind].items()})
    print("old pipeline overall spearman", summ["old_pipeline_overall_spearman"],
          "| cost $", summ["cost"]["usd_billed"], "calls", summ["cost"]["n_llm_calls"])


if __name__ == "__main__":
    main()
