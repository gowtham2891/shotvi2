"""The New flow tab's module (founder, 2026-11-05): step status and order. No network."""
import unittest

from selection_v2 import flow_v2 as FV


class TestFlow(unittest.TestCase):
    def test_done(self):
        self.assertEqual(FV.done(None), {"text": False, "video": False, "notes": False, "find": False, "edit": False, "rank": False})
        f = {"episode_pass": {"speakers": []}, "identity_pass": {"scenes": [{"id": 1}]}, "scene_notes": {"scenes": []},
             "clips": {"version": "stage3_find/0.2", "clips": []}}
        d = FV.done(f)
        self.assertTrue(d["find"]); self.assertFalse(d["edit"])
        f["clips"]["version"] = "stage3_edit/0.1"
        self.assertTrue(FV.done(f)["edit"])

    def test_cfg(self):
        c = FV.cfg_for("x")
        self.assertEqual(c["identity_pass"]["scenes"], "find")
        self.assertIn("flow_glossary", c["stage1b"]["glossary_dir"])     # no series memory in or out
        self.assertEqual([k for k, _ in FV.STEPS], ["text", "video", "notes", "find", "rank"])
