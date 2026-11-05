"""Stage 2 as one call over the whole episode (founder, 2026-11-03): facts per scene from the video step's scenes;
code checks the format only. Synthetic data, fake client (no network)."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from selection_v2 import identity_pass as IDP, naming_check as NC, stage2_episode as S2E
from tests.v2.synthetic import make_inputs
from tests.v2.test_naming_check import CFG, identity_record, video_answer
from tests.v2.test_hybrid import facts_1b
from tests.v2.test_stage1b import FakeClient, _cutter, _deleter, _uploader


def facts_with_scenes():
    f = facts_1b(); base = NC.base_of(f)
    ids = [r["id"] for r in f["lines"]]
    mid = len(ids) // 2
    rec = identity_record(f)
    _, _, rec["scenes"] = IDP.validate_scenes({"scenes": [{"first": ids[0], "last": ids[mid - 2], "who": [], "where": "home"},
                                                          {"first": ids[mid], "who": [], "where": "office"}]}, f, set())
    out = IDP.finalize(f, rec, base); out["identity_pass"] = rec
    return out


def notes_answer(f):
    sc = f["identity_pass"]["scenes"]
    return {"episode": "story", "scenes": [{"scene_id": s["id"], "story": "x", "mood": "light", "context_needed": "", "relationships": "",
                                            "situations": [{"first": s["first_line"], "last": s["last_line"], "what_happens": "y"}],
                                            "reactions": [], "visual_moments": [], "silences": [],
                                            "connections": [{"scene": 3 - s["id"], "how": "continues"}]} for s in sc]}


class TestSceneNotes(unittest.TestCase):
    def test_context_marks_scenes_and_gaps(self):
        f = facts_with_scenes()
        ctx = S2E.context_text(f)
        self.assertIn("== Scene 1", ctx); self.assertIn("== Scene 2", ctx); self.assertIn("-- between scenes --", ctx)
        self.assertIn("on screen:", ctx)

    def test_format_checks(self):
        f = facts_with_scenes()
        good = notes_answer(f)
        errs, warns, out = S2E.validate(good, f)
        self.assertEqual((errs, warns), ([], [])); self.assertEqual(len(out), 2)
        bad = copy.deepcopy(good); bad["scenes"] = bad["scenes"][:1]
        self.assertTrue(S2E.validate(bad, f)[0])
        slip = copy.deepcopy(good)
        slip["scenes"][0]["situations"][0]["last"] = f["identity_pass"]["scenes"][1]["first_line"]      # outside scene 1
        slip["scenes"][0]["visual_moments"] = [{"range": "nonsense", "what_is_seen": "a look"}]
        slip["scenes"][0]["connections"] = [{"scene": 9, "how": "?"}]
        errs, warns, out = S2E.validate(slip, f)
        self.assertEqual(errs, []); self.assertEqual(len(warns), 3)
        self.assertEqual((out[0]["situations"], out[0]["visual_moments"], out[0]["connections"]), ([], [], []))

    def test_run_one_call(self):
        f = facts_with_scenes()
        inputs = make_inputs()
        with tempfile.TemporaryDirectory() as td:
            inputs.paths = SimpleNamespace(run_dir=Path(td), mp4=Path(td) / "ep.mp4", title=lambda: "Synthetic")
            cfg = copy.deepcopy(CFG); cfg["paths"]["runs_log"] = str(Path(td) / "runs.csv")
            from selection_v2.config import load_config
            cfg["stage2_episode"] = load_config()["stage2_episode"]
            client = FakeClient([notes_answer(f)])
            out = S2E.run(inputs, f, cfg, client=client, cutter=_cutter, uploader=_uploader, deleter=_deleter)
        self.assertEqual(len(client.calls), 1)
        self.assertIn("file_uri", client.calls[0]["media"][0])
        self.assertIn("Do NOT pick clips", client.calls[0]["prompt"])
        self.assertEqual(len(out["scene_notes"]["scenes"]), 2)
