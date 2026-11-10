"""Stage 4 ranking (founder, 2026-11-09): one text call per episode; code checks the format only."""
import unittest

from selection_v2 import stage4_rank as R


class TestRank(unittest.TestCase):
    def test_validate(self):
        ids = ["c01", "c02", "c03"]
        ok = {"order": ["c02", "c01", "c03"], "post_first": ["c02"], "reasons": [{"clip": i, "why": "w"} for i in ids], "verdict": "strong", "verdict_why": "v"}
        e, w, v = R.validate(ok, ids)
        self.assertEqual(e, []); self.assertEqual(v["order"][0], "c02")
        self.assertTrue(R.validate({**ok, "order": ["c01", "c01", "c03"]}, ids)[0])
        self.assertTrue(R.validate({**ok, "post_first": ["c09"]}, ids)[0])
        self.assertTrue(R.validate({**ok, "verdict": "great"}, ids)[0])
        self.assertTrue(R.validate({**ok, "reasons": []}, ids)[1])          # missing reasons: a warning only

    def test_no_strength_sent(self):
        f = {"lines": [{"id": "L1", "kind": "speech", "text": "hi", "start": 0, "end": 1}], "scene_notes": {"episode": "story"},
             "episode_pass": {"speakers": [{"key": "A", "name": "Ravi"}], "derived": {"line_speakers": {"L1": "A"}}}}
        c = {"id": "c01", "duration": 30, "start_time": 0, "pieces": [{"lines": ["L1"]}], "hook": "h", "why": "w", "emotion": "e",
             "end": {"why": "ends"}, "kind": "standalone", "strength": "post_first", "strength_why": "SECRET"}
        t = R.call_text(f, [c])
        self.assertIn("Ravi: hi", t); self.assertNotIn("SECRET", t); self.assertNotIn("post_first", t)
