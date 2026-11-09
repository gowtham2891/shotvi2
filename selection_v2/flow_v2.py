"""The web-series flow agreed on 2026-11-03..05, step by step for one episode (the debug app's "New flow" tab calls this).

  text    Stage 1A (code, free) + Stage 1B text step: people, ads; COLD (no series cast list, no saved glossary)
  video   the video step with scenes: find (who talks / who is on screen per line, people's looks, the scenes)
  notes   Stage 2 scene notes, one whole-episode call (facts per scene, no payoffs or hooks)
  find    Stage 3 find: whole video + text cached at Google, one call per scene gives the clips
  edit    Stage 3 edit: one call per clip with the video of its stretch (tighten, split, hold, cold open)

Everything for an episode lives in ONE file, ``runs/selection_v2/<id>/flow_v2.json`` (the old stages' episode_facts.json
is not touched). ``import_test`` copies the results of the tests run from ``eval/naming/name_test.py`` (free). ``cut``
cuts every clip into its own mp4 and writes the rating page. Each paid step logs its own call in the runs log.
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path

from . import identity_pass as IDP
from . import stage1_episode_pass as S1B
from . import stage1_facts as S1A
from . import stage2_episode as S2E
from . import stage3_edit as SE
from . import stage3_find as S3
from .config import ROOT, load_config
from .inputs import load_inputs
from .paths import EpisodePaths, load_json, write_json

STEPS = [("text", "Text step (Stage 1A + 1B)"), ("video", "Video step: who talks, who is on screen, scenes"),
         ("notes", "Stage 2: scene notes"), ("find", "Stage 3 find: clips per scene"), ("edit", "Stage 3 edit: tighten each clip")]
TEST_DIR = ROOT / "runs" / "name_test"


def cfg_for(vid: str, cfg: dict | None = None) -> dict:
    """The flow's config: the video step finds the scenes; 1B gets no saved series glossary and writes none."""
    c = copy.deepcopy(cfg or load_config())
    c["identity_pass"]["scenes"] = "find"
    c["stage1b"]["glossary_dir"] = str(Path(EpisodePaths(vid, c).run_dir) / "flow_glossary")
    return c


def path(vid: str, cfg: dict) -> Path:
    return Path(EpisodePaths(vid, cfg).run_dir) / "flow_v2.json"


def load(vid: str, cfg: dict) -> dict | None:
    p = path(vid, cfg)
    return load_json(p) if p.exists() else None


def done(f: dict | None) -> dict:
    f = f or {}
    return {"text": bool(f.get("episode_pass")), "video": bool((f.get("identity_pass") or {}).get("scenes")),
            "notes": bool(f.get("scene_notes")), "find": bool(f.get("clips_found") or f.get("clips")),
            "edit": bool((f.get("clips") or {}).get("version", "").startswith("stage3_edit"))}


def _counter():
    from shotvi.segment.render import estimate_tokens
    return estimate_tokens


def estimate(vid: str, step: str, cfg: dict | None = None) -> float:
    """₹ estimate of one step from the file as it is now (no network)."""
    c = cfg_for(vid, cfg)
    f = load(vid, c)
    if step == "text":
        f0 = S1A.run(load_inputs(vid, c), None, c, {"series": {}})
        return S1B.estimate_cost(f0, c, counter=_counter())["inr_expected"]
    if f is None:
        raise RuntimeError("run the text step first")
    if step == "video":
        return IDP.estimate(f, c, counter=_counter())["inr_expected"]
    if step == "notes":
        return S2E.estimate(f, c, counter=_counter())["inr_expected"]
    if step == "find":
        return S3.estimate_clips(f, c, counter=_counter(), words=load_inputs(vid, c).words)["total_inr"]
    if step == "edit":
        found = (f.get("clips_found") or f.get("clips") or {}).get("clips") or []
        return SE.estimate(f, c, found, counter=_counter(), words=load_inputs(vid, c).words)["inr"]
    raise KeyError(step)


def run(vid: str, step: str, cfg: dict | None = None, log=None) -> dict:
    """Run one step on the episode's flow file and save it. Returns {"seconds", "warnings", "cost_inr"}. Raises on any
    failure (the caller shows the traceback); a step whose earlier step has not run refuses."""
    c = cfg_for(vid, cfg)
    log = log or (lambda *a: None)
    t0 = time.perf_counter()
    inp = load_inputs(vid, c)
    f = load(vid, c)
    have = done(f)
    order = [k for k, _ in STEPS]
    for prev in order[:order.index(step)]:
        if not have[prev]:
            raise RuntimeError(f"run '{prev}' first")
    if step == "text":
        new = S1B.run(inp, S1A.run(inp, None, c, {"series": {}}), c, log=log)
        info = (new.get("stages") or {}).get("1b") or {}
        warns, cost = info.get("warnings") or [], (info.get("usage") or {}).get("cost_inr")
    elif step == "video":
        new = IDP.run(inp, f, c, log=log)
        new.pop("scene_notes", None); new.pop("clips", None); new.pop("clips_found", None)     # later steps are stale
        warns, cost = new["identity_pass"].get("warnings") or [], new["identity_pass"]["usage"].get("cost_inr")
    elif step == "notes":
        new = S2E.run(inp, f, c, log=log)
        new.pop("clips", None); new.pop("clips_found", None)
        warns, cost = new["scene_notes"].get("warnings") or [], new["scene_notes"]["usage"].get("cost_inr")
    elif step == "find":
        new = S3.find_clips(inp, f, c, log=log)
        new.pop("clips_found", None)
        warns, cost = new["clips"]["warnings"], new["clips"]["usage"]["cost_inr"]
    elif step == "edit":
        found = f.get("clips_found") or f["clips"]
        new = SE.edit_clips(inp, f, c, found["clips"], log=log)
        new["clips_found"] = found
        warns, cost = new["clips"]["warnings"], new["clips"]["usage"]["cost_inr"]
    else:
        raise KeyError(step)
    write_json(path(vid, c), new)
    return {"seconds": round(time.perf_counter() - t0, 1), "warnings": warns, "cost_inr": cost}


def test_results(vid: str) -> dict:
    """Which tests from eval/naming/name_test.py exist for this episode (the files import_test would copy)."""
    d = TEST_DIR / vid
    edited = d / "stage3.v9.json"
    edited_ok = edited.exists() and any(c.get("edited") for c in load_json(edited)["clips"]["clips"])
    return {"facts": (d / "episode_facts.v4_scenes_find.json").exists(), "notes": (d / "scene_notes.v4.json").exists(),
            "find": (d / "stage3.v8.json").exists(), "edit": edited_ok}


def import_test(vid: str, cfg: dict | None = None) -> dict:
    """Free: build the flow file from the test runs (video step v4 with scenes, notes v4, find v8, edit v9 when it
    exists). Overwrites the episode's flow file."""
    c = cfg_for(vid, cfg)
    d = TEST_DIR / vid
    have = test_results(vid)
    if not have["facts"]:
        raise RuntimeError(f"no test run for {vid} under {d}")
    f = load_json(d / "episode_facts.v4_scenes_find.json")
    if have["notes"]:
        f["scene_notes"] = load_json(d / "scene_notes.v4.json")
        if have["find"]:
            f["clips"] = load_json(d / "stage3.v8.json")["clips"]
            if have["edit"]:
                f["clips_found"] = f["clips"]
                f["clips"] = load_json(d / "stage3.v9.json")["clips"]
    write_json(path(vid, c), f)
    return have


def cut(vid: str, cfg: dict | None = None, log=None) -> dict:
    """Cut every clip of the flow file into review/flow_clips/<id>/<clip>.mp4 and write review/flow_<id>.html (the
    rating page). Free (ffmpeg)."""
    from .eval.naming import make_clips_page as MP
    from .eval.naming import render_clips as RC
    c = cfg_for(vid, cfg)
    log = log or (lambda *a: None)
    f = load(vid, c)
    if not (f or {}).get("clips"):
        raise RuntimeError("no clips yet: run Stage 3 find first")
    src = Path(EpisodePaths(vid, c).mp4)
    dest = ROOT / "review" / "flow_clips" / vid
    files = []
    for cl in f["clips"]["clips"]:
        dst = dest / f"{cl['id']}.mp4"
        RC.render(src, cl["pieces"], dst)
        files.append(dst)
        log(f"{cl['id']} {cl['duration']:.0f} s -> {dst}")
    title = EpisodePaths(vid, c).title() or vid
    ep = MP.episode_from(f, f["clips"]["clips"], vid, title, f"flow_clips/{vid}")
    page = ROOT / "review" / f"flow_{vid}.html"
    page.write_text(MP.PAGE.replace("__DATA__", json.dumps([ep], ensure_ascii=False)).replace("__TAG__", f"flow_{vid}"), encoding="utf-8")
    return {"files": [str(x) for x in files], "page": str(page)}
