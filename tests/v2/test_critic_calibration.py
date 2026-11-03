"""Critic calibration on posted Shorts (founder, 2026-10-30): scripted-series scope and the calibration run rules.
Synthetic data only (no podcast episodes)."""
import json
import tempfile
import unittest
from pathlib import Path

from selection_v2.config import load_config
from selection_v2.eval import critic_calibration as CAL

CFG = load_config()


def ans(missing="none", swipe=None, second=10.0):
    return {"best_moment": {"second": second, "what": "x", "kind": "funny"}, "swipe": swipe or [], "confusion": {"missing": missing, "what": "who"}}


class TestCalibration(unittest.TestCase):
    def test_run_rules(self):
        c5 = CFG["stage5"]
        self.assertTrue(CAL.judge(ans(), 60, c5)["pass"])
        r = CAL.judge(ans(missing="major"), 60, c5); self.assertEqual(r["fail_classes"], ["confusion"])
        r = CAL.judge(ans(swipe=[{"from_s": 10, "to_s": 35, "why": "w"}]), 60, c5); self.assertEqual(r["fail_classes"], ["pace"])   # 25 s > the 20 s limit
        self.assertTrue(CAL.judge(ans(swipe=[{"from_s": 10, "to_s": 30, "why": "w"}]), 60, c5)["pass"])                      # 20 s: not over
        self.assertTrue(CAL.judge(ans(second=55), 60, c5)["pass"])            # no expected moment: Q3 is recorded, never judged

    def test_scripted_series_only(self):
        cfg = {"paths": {"runs_dir": "runs"}, "stage5": CFG["stage5"], "rates": CFG["rates"]}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "data" / "channel_shorts").mkdir(parents=True)
            for vid, ct in (("S1", "scripted_series"), ("P1", "podcast")):
                (root / "runs" / vid).mkdir(parents=True)
                (root / "runs" / vid / "episode_facts.json").write_text(json.dumps({"episode_pass": {"content_type": ct}}))
                (root / f"{vid}.mp4").write_bytes(b"x")
                (root / "data" / "channel_shorts" / f"{vid}.json").write_text(json.dumps({"episode_id": vid, "accepted": [
                    {"short_id": vid + "a", "channel": "C", "duration": 60, "views": 10, "views_vs_median": 1.0, "local_file": f"{vid}.mp4"},
                    {"short_id": vid + "b", "channel": "C", "duration": 60, "local_file": "missing.mp4"}]}))
            rows, notes = CAL.scripted_shorts(cfg, root)
            self.assertEqual([r["short_id"] for r in rows], ["S1a"])
            self.assertTrue(any("P1" in n and "podcast" in n for n in notes)); self.assertTrue(any("S1b" in n for n in notes))
            self.assertEqual(CAL.estimate(cfg, rows)["n_shorts"], 1)

    def test_spearman(self):
        self.assertEqual(CAL.spearman([1, 2, 3], [10, 20, 30]), 1.0)
        self.assertIsNone(CAL.spearman([1, 2], [1, 2]))


if __name__ == "__main__":
    unittest.main()
