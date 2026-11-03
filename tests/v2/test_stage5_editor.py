"""Stage 5 editor's pick (founder, 2026-10-30): shortlist, version choice, answer validation, set rules with replacement by
alternates, a mocked run (no reel encode, no network) and Stage 6 building its set from the picks. Synthetic data only."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from selection_v2 import stage5_editor as ED, stage6_select as S6
from selection_v2.runner import validate_facts
from tests.v2.test_stage3 import KEYS, make_inputs, transcript
from tests.v2.test_stage5 import CFG as CFG5, facts_after_4

CFG = copy.deepcopy(CFG5); CFG["stage5"]["mode"] = "editor"


def answer(sl, picks=None, alts=None):
    ms = sl["moments"]
    picks = picks if picks is not None else [m["moment_id"] for m in ms[:6]]
    alts = alts if alts is not None else [m["moment_id"] for m in ms[6:9]]
    vid = {m["moment_id"]: m["versions"][0]["version_id"] for m in ms}
    return {"picks": [{"rank": i + 1, "moment_id": mid, "version_id": vid[mid], "why": "lands", "hook": {"seen": "a look", "heard": "a line"},
                       "newcomer_needs": "", "fan_clip": False, "cut_fix": None} for i, mid in enumerate(picks)],
            "alternates": [{"moment_id": mid, "version_id": vid[mid], "why": "next"} for mid in alts]}


class FakeEditorClient:
    def __init__(self, ans):
        self.ans, self.calls = ans, []
        self.ledger = SimpleNamespace(summary=lambda: {"usd": 0.0})

    def generate_json(self, role, spec, system, prompt, schema, meta=None, want_raw=False, media=None):
        self.calls.append({"role": role, "prompt": prompt, "media": media, "spec": spec})
        obj = copy.deepcopy(self.ans)
        raw = SimpleNamespace(text=json.dumps(obj), input_tokens=200000, output_tokens=3000, thought_tokens=6000, cached_tokens=0)
        return (obj, raw) if want_raw else obj


def fake_moments(n=10):
    """Synthetic shortlist moments, one version each, spread over the episode in different scenes."""
    ms = []
    for i in range(n):
        v = {"id": f"C{i}a", "version_id": f"P{i + 1:02d}-A", "variant": "full", "type": "comedy", "final_duration": 50.0, "combined": 8.0,
             "start": i * 100.0, "end": i * 100.0 + 50, "segments": [[i * 100.0, i * 100.0 + 50]], "scene_id": i + 1}
        ms.append({"key": f"{i + 1}|L{i}|story", "moment_id": f"P{i + 1:02d}", "scene_id": i + 1, "channel_short": False, "versions": [v]})
    return {"moments": ms}


class TestShortlist(unittest.TestCase):
    def test_versions_best_typical_longest(self):
        """founder, 2026-10-30: up to 3 versions: the best-scored, the closest to typical length, the longest (<= 180 s)."""
        m = {"all_versions": [{"id": "a", "combined": 9.0, "final_duration": 20}, {"id": "b", "combined": 8.7, "final_duration": 60},
                              {"id": "c", "combined": 7.0, "final_duration": 65}, {"id": "d", "combined": 6.0, "final_duration": 120}]}
        self.assertEqual([v["id"] for v in ED.pick_versions(m, 65.0)], ["a", "c", "d"])
        m2 = {"all_versions": [{"id": "a", "combined": 9.0, "final_duration": 64}, {"id": "b", "combined": 8.9, "final_duration": 30}]}
        self.assertEqual([v["id"] for v in ED.pick_versions(m2, 65.0)], ["a"])                 # best = closest = longest: one version

    def test_shortlist_top_moments_and_channel_shorts_unlabelled(self):
        f, x = facts_after_4()
        cfg = copy.deepcopy(CFG); cfg["stage5"]["editor"]["top_moments"] = 2; cfg["stage5"]["editor"]["scene_extra"] = 0
        cs = copy.deepcopy(next(c for c in f["candidates"] if c["variant"] == "full_arc"))
        cs.update({"id": "C9z", "variant": "channel_short", "eligible_for_final": False})
        f["candidates"].append(cs)
        sl = ED.shortlist(f, cfg)
        ranked = ED.all_moments(f, cfg)
        best_per_scene = {next(m["key"] for m in ranked if m["scene_id"] == s) for s in {m["scene_id"] for m in ranked}}
        self.assertTrue(best_per_scene <= {m["key"] for m in sl["moments"]})                  # every scene's best moment is listed
        self.assertEqual(sl["n_top"], max(2, len(f["beat_sheets"])))
        self.assertEqual(sum(1 for m in sl["moments"] if m["channel_short"]), 1)
        ids = [m["moment_id"] for m in sl["moments"]]
        self.assertEqual(ids, [f"P{i + 1:02d}" for i in range(len(ids))])
        lead = ED.lead_text(f, next(m for m in sl["moments"] if m["channel_short"]))
        self.assertNotIn("channel", lead.lower())                                          # nothing marks the channel's own Short
        self.assertTrue(all(len(m["versions"]) <= 2 for m in sl["moments"]))


class TestValidationAndRules(unittest.TestCase):
    def test_validation(self):
        sl = fake_moments()
        cfg = copy.deepcopy(CFG)
        self.assertEqual(ED.validate(answer(sl), sl, cfg), [])
        bad = answer(sl); bad["picks"] = bad["picks"][:5]
        self.assertTrue(any("exactly 6" in e for e in ED.validate(bad, sl, cfg)))
        bad = answer(sl); bad["picks"][0]["version_id"] = "P02-A"
        self.assertTrue(any("is not a version of" in e for e in ED.validate(bad, sl, cfg)))
        bad = answer(sl, alts=["P01", "P08", "P09"])
        self.assertTrue(any("used twice" in e for e in ED.validate(bad, sl, cfg)))

    def test_rules_replace_by_next_alternate(self):
        sl = fake_moments()
        ms = sl["moments"]
        ms[1]["versions"][0].update({"start": 20.0, "end": 70.0, "segments": [[20.0, 70.0]]})       # P02 overlaps P01
        ms[2]["channel_short"] = True                                                              # P03 is a channel Short
        for i in (3, 4, 5):
            ms[i]["versions"][0]["scene_id"] = 4                                                  # P04-P06 in one scene
        cfg = copy.deepcopy(CFG)
        ans = answer(sl)
        ans["picks"][0]["fan_clip"] = True
        res = ED.apply_rules(ans, sl, cfg)
        self.assertEqual([p["moment_id"] for p in res["posts"]], ["P01", "P07", "P08", "P04", "P05", "P09"])
        rules = {x["moment_id"]: x["rule"] for x in res["replaced"]}
        self.assertIn("overlaps P01", rules["P02"]); self.assertIn("channel Short", rules["P03"]); self.assertIn("posts from scene 4", rules["P06"])
        self.assertEqual(res["channel_short_picks"][0]["moment_id"], "P03")
        sl2 = fake_moments(); ans2 = answer(sl2)
        for p in ans2["picks"][:3]:
            p["fan_clip"] = True
        res2 = ED.apply_rules(ans2, sl2, cfg)
        self.assertIn("fan clips", res2["replaced"][0]["rule"])                                # the third fan clip is replaced


class TestReel(unittest.TestCase):
    def test_reel_passes_full_clips_to_the_renderer(self):
        sl = fake_moments(2)
        seen = []
        def renderer(src, run_dir, c, h):
            for k in ("id", "segments", "start", "end", "final_duration", "removed"):
                self.assertIn(k, c)
            seen.append(c["id"]); p = Path(run_dir) / f"{c['id']}.mp4"; p.write_bytes(b"x"); return p
        orig = ED._probe
        ED._probe = lambda p: 50.0
        try:
            with tempfile.TemporaryDirectory() as td:
                (Path(td) / "editor").mkdir()
                import hashlib
                rows_sig = None
                try:
                    ED.build_reel(Path(td) / "ep.mp4", Path(td), sl, CFG, renderer=renderer)
                except ED.EditorError:
                    pass                                                        # ffmpeg cannot join fake files; the renderer calls are what is tested
        finally:
            ED._probe = orig
        self.assertEqual(seen, ["C0a", "C1a"])


class TestRunAndStage6(unittest.TestCase):
    def test_run_then_stage6_uses_the_picks(self):
        f, x = facts_after_4()
        cfg = copy.deepcopy(CFG); cfg["stage5"]["editor"]["picks"] = 2; cfg["stage5"]["editor"]["alternates"] = 1
        cfg["stage6"]["main_slots"] = 2
        sl = ED.shortlist(f, cfg)
        self.assertGreaterEqual(len(sl["moments"]), 3)
        ans = answer(sl, picks=[sl["moments"][0]["moment_id"], sl["moments"][1]["moment_id"]], alts=[sl["moments"][2]["moment_id"]])
        ans["picks"][0]["newcomer_needs"] = "she is his mother"
        client = FakeEditorClient(ans)
        orig = ED.build_reel
        ED.build_reel = lambda src, run_dir, sl_, cfg_, renderer=None, log=None: (Path(run_dir) / "reel.mp4",
                                                                                 [{"version_id": v["version_id"], "candidate_id": v["id"], "reel_start": 0.0, "reel_end": 1.0}
                                                                                  for m in sl_["moments"] for v in m["versions"]])
        try:
            with tempfile.TemporaryDirectory() as td:
                (Path(td) / "reel.mp4").write_bytes(b"x")
                (Path(td) / "prefix_stage2.txt").write_text("PREFIX\n", encoding="utf-8")
                cfg["paths"]["runs_log"] = str(Path(td) / "runs.csv")
                inputs = make_inputs(transcript=transcript(), keys=KEYS)
                inputs.paths = SimpleNamespace(run_dir=Path(td), mp4=Path(td) / "ep.mp4", wav=Path(td) / "a.wav", title=lambda: "x")
                up = lambda path, mode: {"uri": "files/reel", "name": "files/reel", "mime_type": "video/mp4"}
                out = ED.run(inputs, f, cfg, client=client, uploader=up, deleter=lambda u: True)
                validate_facts(out)
                self.assertEqual(client.calls[0]["media"][0], {"text": "PREFIX\n"})              # the cached prefix first
                self.assertEqual(client.calls[0]["spec"].params.get("thinking_level"), "medium")
                self.assertIn("not a limit", client.calls[0]["prompt"])
                self.assertEqual(len(out["editor"]["posts"]), 2); self.assertEqual(out["stages"]["5"]["mode"], "editor")
                sel = S6.plan_set(out, cfg)
                self.assertEqual([m["primary"]["id"] for m in sel["main"]], [p["candidate_id"] for p in out["editor"]["posts"]])
                self.assertTrue(sel["main"][0]["primary"]["needs_caption_context"])
                block = S6.moment_block(out, dict(sel["main"][0], id="M1"), S6.stage6_cfg(out, cfg))
                self.assertIn("what a newcomer needs: she is his mother", block); self.assertNotIn("critics Q2", block)
                self.assertTrue(all(why["rule"].startswith("editor_") for why in sel["why"].values()))
        finally:
            ED.build_reel = orig


if __name__ == "__main__":
    unittest.main()
