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
  scenes <check|find> <vid> ...  the video step also gives the scenes (founder, 2026-11-03): check = the text step's
                               scene split is sent and checked, find = none sent -> episode_facts.<tag>_scenes_<mode>.json
  notes <vid> ...              Stage 2 as one call (stage2_episode) on episode_facts.<tag>_scenes_find.json
                               -> scene_notes.<tag>.json
  moments <vid> ...            Stage 3a (one text call per scene) on the v4 facts + scene notes -> stage3.<tag>.json
  shape <vid> ...              Stage 3b (one video call per scene with moments) on stage3.<tag>.json -> stage3.<tag>.json (with clips)
  clips <vid> ...              Stage 3 in one step (founder, 2026-11-04: 3a + 3b merged; the whole episode video cached at
                               Google, one call per scene) on the v4 facts + scene notes -> stage3.<S3_TAG>.json
  clips_estimate <vid> ...     ₹ estimate of the one step (no network)
  edit <vid> ...               Stage 3 edit step (founder, 2026-11-05): one call per clip of stage3.<EDIT_FROM>.json with
                               the video of its stretch -> stage3.<S3_TAG>.json (EDIT_FROM default v8)
  edit_estimate <vid> ...      ₹ estimate of the edit step (no network)
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
S3_TAG = __import__("os").environ.get("STAGE3_TAG", "v4")   # the Stage 3 output file stage3.<S3_TAG>.json
MODELS = {"flash": {"provider": "gemini", "model": "gemini-3.8-flash", "params": {"thinking_level": "medium", "max_output_tokens": 32768}},
          "sarvam": {"provider": "sarvam", "model": "sarvam-105b", "params": {"reasoning_effort": "low", "max_output_tokens": 4096}},   # Starter plan limit (founder, 2026-11-02)
          # with reasoning low the Starter limit ran out before the answer (2026-11-02, twice): reasoning off
          "sarvam_nothink": {"provider": "sarvam", "model": "sarvam-105b", "params": {"reasoning_effort": None, "max_output_tokens": 4096}}}


def cfg_for(vid):
    cfg = copy.deepcopy(load_config())
    cfg["paths"]["runs_dir"] = str(OUT)
    cfg["paths"]["runs_log"] = str(OUT / "runs.csv")
    cfg["stage1b"]["glossary_dir"] = str(OUT / vid / "glossary")      # no series memory in, nothing written to data/
    env = __import__("os").environ
    if env.get("VIDEO_BOX") == "1":                                       # founder, 2026-11-06 test: box A for steps 5 and 6
        cfg["video_box"]["enabled"] = True
    if env.get("STAGE3_MODE"):                                            # per_scene | all
        cfg["stage3_find"]["mode"] = env["STAGE3_MODE"]
    if env.get("STAGE3_MODEL"):                                           # founder, 2026-11-10: cheaper-model test
        cfg["stage3_find"]["clips_model"]["model"] = env["STAGE3_MODEL"]
    if env.get("RANK_MODEL"):
        cfg["stage4_rank"]["model"]["model"] = env["RANK_MODEL"]
    return cfg


# the text-step answer each episode's scored video run used (Hello Madam only has the 2026-11-02 one)
FACTS_1B = {"epZ0lbXkgWA": "facts_1b.v3.json"}


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


def scenes(mode, vid):
    cfg = cfg_for(vid)
    cfg["identity_pass"]["scenes"] = mode
    inp = load_inputs(vid, cfg)
    f = load(OUT / vid / FACTS_1B.get(vid, "facts_1b.json"))
    t0 = time.time()
    out = IDP.run(inp, f, cfg, log=print)
    write_json(OUT / vid / f"episode_facts.{TAG}_scenes_{mode}.json", out)
    rec = out["identity_pass"]
    print(vid, mode, "video step:", rec["usage"], f"| {len(rec['scenes'])} scenes | warnings:", rec["warnings"], f"| {time.time() - t0:.0f} s", flush=True)


def notes(vid):
    from selection_v2 import stage2_episode as S2E
    cfg = cfg_for(vid)
    inp = load_inputs(vid, cfg)
    f = load(OUT / vid / f"episode_facts.{TAG}_scenes_find.json")
    print(vid, "estimate:", S2E.estimate(f, cfg), flush=True)
    t0 = time.time()
    out = S2E.run(inp, f, cfg, log=print)
    write_json(OUT / vid / f"scene_notes.{TAG}.json", out["scene_notes"])
    sn = out["scene_notes"]
    print(vid, "scene notes:", sn["usage"], f"| {len(sn['scenes'])} scenes | warnings:", sn["warnings"], f"| {time.time() - t0:.0f} s", flush=True)


def s3_facts(vid):
    f = load(OUT / vid / f"episode_facts.{TAG}_scenes_find.json")
    f["scene_notes"] = load(OUT / vid / f"scene_notes.{TAG}.json")
    return f


def moments(vid):
    from selection_v2 import stage3_find as S3
    cfg = cfg_for(vid)
    inp = load_inputs(vid, cfg)
    f = s3_facts(vid)
    print(vid, "estimate:", S3.estimate(f, cfg), flush=True)
    out = S3.find_moments(inp, f, cfg, log=print)
    write_json(OUT / vid / f"stage3.{TAG}.json", {"moments": out["moments"]})
    m = out["moments"]
    print(vid, "3a:", m["usage"], f"| {len(m['moments'])} moments | warnings:", m["warnings"], flush=True)


def shape(vid):
    from selection_v2 import stage3_find as S3
    cfg = cfg_for(vid)
    inp = load_inputs(vid, cfg)
    f = s3_facts(vid)
    s3 = load(OUT / vid / f"stage3.{TAG}.json")
    f["moments"] = s3["moments"]
    print(vid, "estimate:", S3.estimate(f, cfg), flush=True)
    out = S3.shape_clips(inp, f, cfg, log=print)
    write_json(OUT / vid / f"stage3.{TAG}.json", {"moments": out["moments"], "clips": out["clips"]})
    c = out["clips"]
    print(vid, "3b:", c["usage"], f"| {len(c['clips'])} clips | warnings:", c["warnings"], flush=True)


def clips(vid):
    from selection_v2 import stage3_find as S3
    cfg = cfg_for(vid)
    inp = load_inputs(vid, cfg)
    f = s3_facts(vid)
    print(vid, "estimate:", S3.estimate_clips(f, cfg, words=inp.words), flush=True)
    only = [int(x) for x in __import__("os").environ.get("STAGE3_SCENES", "").split(",") if x.strip()]   # a test on some scenes
    out = S3.find_clips(inp, f, cfg, log=print, scene_ids=only or None)
    write_json(OUT / vid / f"stage3.{S3_TAG}.json", {"clips": out["clips"]})
    c = out["clips"]
    print(vid, "stage 3:", c["usage"], f"| {len(c['clips'])} clips | failed scenes {c['failed_scenes']} | warnings:", c["warnings"], flush=True)


def clips_estimate(vid):
    from selection_v2 import stage3_find as S3
    cfg = cfg_for(vid)
    print(vid, S3.estimate_clips(s3_facts(vid), cfg, words=load_inputs(vid, cfg).words), flush=True)


def _edit_in(vid):
    return load(OUT / vid / f"stage3.{__import__('os').environ.get('EDIT_FROM', 'v8')}.json")["clips"]["clips"]


def edit(vid):
    from selection_v2 import stage3_edit as SE
    cfg = cfg_for(vid)
    inp = load_inputs(vid, cfg)
    f = s3_facts(vid)
    cl = _edit_in(vid)
    print(vid, "estimate:", SE.estimate(f, cfg, cl, words=inp.words), flush=True)
    only = [x.strip() for x in __import__("os").environ.get("EDIT_CLIPS", "").split(",") if x.strip()]   # redo some clips
    out = SE.edit_clips(inp, f, cfg, cl, log=lambda *a: print(*a, flush=True), clip_ids=only or None)
    c = out["clips"]
    if only:   # merge into the existing output: the redone clips replace their earlier entries, in the found order
        prev = load(OUT / vid / f"stage3.{S3_TAG}.json")["clips"]
        new = {x.get("from_clip") or x["id"]: [] for x in c["clips"]}
        for x in c["clips"]:
            new[x.get("from_clip") or x["id"]].append(x)
        merged, done = [], set()
        for x in prev["clips"]:
            k = x.get("from_clip") or x["id"]
            if k in new:
                if k not in done:
                    merged += new[k]; done.add(k)
            else:
                merged.append(x)
        c = {**prev, "clips": merged, "redo": {"clips": only, "usage": c["usage"], "warnings": c["warnings"], "failed": c["failed"]}}
    write_json(OUT / vid / f"stage3.{S3_TAG}.json", {"clips": c})
    print(vid, "edit:", c["usage"], f"| {len(c['clips'])} clips | failed {c['failed']} | warnings:", c["warnings"], flush=True)


def edit_estimate(vid):
    from selection_v2 import stage3_edit as SE
    cfg = cfg_for(vid)
    print(vid, SE.estimate(s3_facts(vid), cfg, _edit_in(vid), words=load_inputs(vid, cfg).words), flush=True)


def rank(vid):
    """Stage 4 (founder, 2026-11-09): one text call orders stage3.<S3_TAG>.json's clips -> stage4.<S3_TAG>.json"""
    from selection_v2 import stage4_rank as R
    cfg = cfg_for(vid)
    inp = load_inputs(vid, cfg)
    f = s3_facts(vid)
    cl = load(OUT / vid / f"stage3.{S3_TAG}.json")["clips"]["clips"]
    print(vid, "estimate:", R.estimate(f, cfg, cl), flush=True)
    out = R.run(inp, f, cfg, cl, log=lambda *a: print(*a, flush=True))
    write_json(OUT / vid / f"stage4.{S3_TAG}.json", out)
    print(vid, "stage 4:", out["usage"], "| order", out["order"], "| post first", out["post_first"], "| verdict", out["verdict"], flush=True)


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
    if mode == "scenes":
        for v in rest[1:]:
            scenes(rest[0], v)
    elif mode == "check":
        for v in rest[1:]:
            check(rest[0], v)
    elif mode == "estimate":
        tot = [estimate(v) for v in rest]
        print(f"total: video step ≈ ₹{sum(a for a, _ in tot):.2f}, naming check input-only ≈ ₹{sum(b for _, b in tot):.2f}")
    else:
        for v in rest:
            {"video": video, "full": full, "score": score, "videoonly": videoonly, "notes": notes, "moments": moments, "shape": shape, "clips": clips, "clips_estimate": clips_estimate,
             "edit": edit, "edit_estimate": edit_estimate, "rank": rank}[mode](v)
