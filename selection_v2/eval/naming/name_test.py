"""Cold-start naming test (founder, 2026-11-01): per episode, 1A -> 1B -> video step (identity pass) -> naming check with
NO hand inputs (no series cast list, no saved glossary / faces, no manual files), written under runs/name_test/<vid>/,
then scored against data/speakers/<vid>.json (selection_v2/eval/naming/score.py).

  estimate <vid> ...           ₹ estimate of the video step + naming check (no network)
  video <vid> ...              reuse the paid facts_1b.json; run the per-line video step + Flash naming check
                               -> episode_facts.<tag>.json (tag "v2")
  check <model> <vid> ...      rerun ONLY the naming check from the stored video answer with another model
                               (flash | sarvam) -> episode_facts.<tag>_<model>.json
  full <vid> ...               everything cold (1A with the current code, a new 1B) -> facts_1b.<tag>.json, episode_facts.<tag>_full.json
  videoonly <vid> ...          free: the stored video answer made final, no naming check -> episode_facts.<tag>_videoonly.json
  score <vid> ...              score every episode_facts*.json in the episode's folder

Paid modes need the founder's ₹ approval first (shotvi/llm/spend.py enforces the cap)."""
import copy
import json
import sys
import time
from pathlib import Path

from selection_v2 import identity_pass as IDP, naming_check as NC, stage1_episode_pass as S1B, stage1_facts as S1A
from selection_v2.config import load_config
from selection_v2.eval.naming import score as SC
from selection_v2.inputs import load_inputs
from selection_v2.paths import write_json

OUT = Path("runs/name_test")
TAG = __import__("os").environ.get("NAME_TEST_TAG", "v2")   # one tag per prompt version
MODELS = {"flash": {"provider": "gemini", "model": "gemini-3.8-flash", "params": {"thinking_level": "medium", "max_output_tokens": 32768}},
          "sarvam": {"provider": "sarvam", "model": "sarvam-105b", "params": {"reasoning_effort": "low", "max_output_tokens": 4096}},   # Starter plan limit (founder, 2026-11-02)
          # with reasoning low the Starter limit ran out before the answer (2026-11-02, twice): reasoning off
          "sarvam_nothink": {"provider": "sarvam", "model": "sarvam-105b", "params": {"reasoning_effort": None, "max_output_tokens": 4096}}}


def cfg_for(vid):
    cfg = copy.deepcopy(load_config())
    cfg["paths"]["runs_dir"] = str(OUT)
    cfg["paths"]["runs_log"] = str(OUT / "runs.csv")
    cfg["stage1b"]["glossary_dir"] = str(OUT / vid / "glossary")      # no series memory in, nothing written to data/
    return cfg


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def estimate(vid):
    cfg = cfg_for(vid)
    f = load(OUT / vid / "facts_1b.json")
    e = IDP.estimate(f, cfg)
    n = NC.estimate(f, cfg, identity={"people": [], "lines": {}})
    print(vid, f"{f['episode']['duration'] / 60:.1f} min, {len(IDP.speech(f))} spoken lines | video step: {e['input_tokens']:,} in "
          f"({e['video_tokens']:,} video) + {e['expected_output_tokens']:,} out ≈ ₹{e['inr_expected']:.2f} | naming check (no video answer yet): "
          f"{n['input_tokens']:,} in ≈ ₹{n['inr_expected']:.2f}", flush=True)
    return e["inr_expected"], n["inr_expected"]


def video(vid):
    cfg = cfg_for(vid)
    inp = load_inputs(vid, cfg)
    f = load(OUT / vid / "facts_1b.json")
    t0 = time.time()
    out = IDP.run(inp, f, cfg, log=print)
    write_json(OUT / vid / f"episode_facts.{TAG}.json", out)
    print(vid, "video step:", out["identity_pass"]["usage"], "| warnings:", out["identity_pass"]["warnings"],
          "| naming check:", (out.get("naming_check") or {}).get("usage"), f"| {time.time() - t0:.0f} s", flush=True)


def check(model, vid):
    cfg = cfg_for(vid)
    cfg["naming_check"]["model"] = MODELS[model]
    inp = load_inputs(vid, cfg)
    f = load(OUT / vid / f"episode_facts.{TAG}.json")
    out = NC.run(inp, f, cfg, log=print)
    write_json(OUT / vid / f"episode_facts.{TAG}_{model}.json", out)
    print(vid, model, "naming check:", out["naming_check"]["usage"], flush=True)


def videoonly(vid):
    """Free: the stored video answer made final without the naming check (founder, 2026-11-02)."""
    f = load(OUT / vid / "facts_1b.json")
    rec = load(OUT / vid / f"episode_facts.{TAG}.json")["identity_pass"]
    out = IDP.finalize(f, rec, NC.base_of(f)); out["identity_pass"] = rec
    write_json(OUT / vid / f"episode_facts.{TAG}_videoonly.json", out)


def full(vid):
    cfg = cfg_for(vid)
    inp = load_inputs(vid, cfg)
    f = S1A.run(inp, None, cfg, {"series": {}})                        # no series cast list
    f = S1B.run(inp, f, cfg, log=print); write_json(OUT / vid / f"facts_1b.{TAG}.json", f)
    out = IDP.run(inp, f, cfg, log=print)
    write_json(OUT / vid / f"episode_facts.{TAG}_full.json", out)


def score(vid):
    for p in sorted((OUT / vid).glob("episode_facts*.json")):
        print(f"-- {p.name}")
        print(SC.fmt(vid, SC.score(load(p), vid)), flush=True)


if __name__ == "__main__":
    mode, rest = sys.argv[1], sys.argv[2:]
    if mode == "check":
        for v in rest[1:]:
            check(rest[0], v)
    elif mode == "estimate":
        tot = [estimate(v) for v in rest]
        print(f"total: video step ≈ ₹{sum(a for a, _ in tot):.2f}, naming check input-only ≈ ₹{sum(b for _, b in tot):.2f}")
    else:
        for v in rest:
            {"video": video, "full": full, "score": score, "videoonly": videoonly}[mode](v)
