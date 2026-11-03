import copy
import unittest

from selection_v2 import present, stage1_facts as S
from selection_v2.config import load_cast_lists, load_config
from tests.v2.synthetic import make_inputs

CFG = copy.deepcopy(load_config()); CFG["stage1a"]["context_window_s"] = 8
CAST = load_cast_lists()


class TestPresent(unittest.TestCase):
    def setUp(self):
        self.f = S.run(make_inputs(), None, CFG, CAST)

    def test_summary_mentions_the_numbers(self):
        t = present.plain_summary(self.f, CFG)
        self.assertIn("Measured 1:32 of video", t)
        self.assertIn("1 laughs", t)
        self.assertIn("1 censored", t)
        self.assertIn("₹0", t)
        self.assertIn("possible scene changes", t)
        self.assertIn("look suspicious", t)

    def test_timeline_rows(self):
        marks, spans = present.timeline_rows(self.f, CFG, [{"id": "m01", "tier": "gold", "start": 40, "end": 50, "label": "x"}])
        kinds = {m["kind"] for m in marks}
        self.assertEqual(kinds, {"scene edge", "laugh", "hold"})
        self.assertEqual(len(spans), 1)
        self.assertTrue(all("detail" in m and "line" in m for m in marks))

    def test_sanity_all_green_on_synthetic(self):
        checks = present.sanity_checks(self.f, CFG, CAST)
        names = [c["name"] for c in checks]
        self.assertEqual(len(checks), 6)
        # the count check is length-scaled and meaningless on a 92 s synthetic episode; every other check is green
        self.assertTrue(all(c["ok"] in (True, None) for c in checks if "plausible" not in c["name"]),
                        [c for c in checks if c["ok"] is False])
        self.assertEqual(next(c for c in checks if "laugh" in c["name"])["ok"], True)   # synthetic title matches Surya (comedy)

    def test_sanity_catches_a_proposal_inside_a_line(self):
        f = copy.deepcopy(self.f)
        f["boundary_proposals"][0]["t"] = 1.5     # inside the first line
        bad = [c for c in present.sanity_checks(f, CFG, CAST) if c["ok"] is False]
        self.assertTrue(any("inside a dialogue line" in c["name"] for c in bad))
        f["episode"]["speakers"].append({"key": "Z", "name": "nobody"})
        bad = [c for c in present.sanity_checks(f, CFG, CAST) if c["ok"] is False]
        self.assertTrue(any("has lines in the line table" in c["name"] for c in bad))
        self.assertIn("filler-only", next(c for c in bad if "has lines" in c["name"])["detail"])  # H only says 'హా'

    def test_old_facts_without_selected_by_still_draw(self):
        f = copy.deepcopy(self.f)
        for p in f["boundary_proposals"]:
            p.pop("selected_by", None)
        marks, _ = present.timeline_rows(f, CFG, [])
        self.assertTrue(any(m["kind"] == "scene edge" for m in marks))


if __name__ == "__main__":
    unittest.main()
