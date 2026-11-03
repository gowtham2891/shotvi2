"""Tests for selection_v2.eval: moment clustering / tiers, source importers on synthetic files, recall matching."""
import json
import tempfile
import unittest
from pathlib import Path

from selection_v2.eval import comments, golden_set, recall
from selection_v2.eval.import_sources import _blocks, human_sources, channel_short_sources


class TestGoldenSet(unittest.TestCase):
    def test_two_kinds_make_gold_one_makes_silver(self):
        srcs = [{"kind": "channel_short", "start": 100, "end": 150, "short_id": "x"},
                {"kind": "human", "start": 105, "end": 148, "confidence": "definite", "type": "comedy", "label": "joke"},
                {"kind": "heatmap", "start": 400, "end": 420}]
        ms = golden_set.build_moments(srcs)
        self.assertEqual([m["tier"] for m in ms], ["gold", "silver"])
        self.assertEqual(ms[0]["type"], "comedy")           # the human source's type wins over unknown
        self.assertEqual((ms[0]["start"], ms[0]["end"]), (105.0, 148.0))   # span from the human source
        self.assertEqual(ms[0]["label"], "joke")

    def test_two_shorts_same_kind_are_silver_under_kinds_rule(self):
        srcs = [{"kind": "channel_short", "start": 100, "end": 150, "short_id": "a"},
                {"kind": "channel_short", "start": 102, "end": 149, "short_id": "b"}]
        self.assertEqual(golden_set.build_moments(srcs)[0]["tier"], "silver")
        self.assertEqual(golden_set.build_moments(srcs, rule="entries")[0]["tier"], "gold")

    def test_containment_merges_short_piece(self):
        srcs = [{"kind": "human", "start": 100, "end": 200, "confidence": "maybe"},
                {"kind": "heatmap", "start": 150, "end": 165}]
        self.assertEqual(len(golden_set.build_moments(srcs)), 1)

    def test_validate(self):
        d = {"episodes": {"v": {"moments": [{"id": "m01", "start": 5, "end": 3, "type": "comedy",
                                              "sources": [{"kind": "human"}]}]}}}
        errs = golden_set.validate(d)
        self.assertTrue(any("end <= start" in e for e in errs))
        self.assertTrue(any("confidence" in e for e in errs))

    def test_blocks(self):
        self.assertEqual(_blocks([{"start": 10, "end": 20}, {"start": 25, "end": 30}, {"start": 100, "end": 120}]),
                         [(10.0, 30.0), (100.0, 120.0)])


class _P:
    def __init__(self, d):
        self.channel_shorts_data = d / "cs.json"
        self.gold = d / "gold.json"


class TestImporters(unittest.TestCase):
    def test_sources_from_disk_shapes(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "cs.json").write_text(json.dumps({"accepted": [
                {"short_id": "s1", "url": "u", "title": "t", "views": 10, "episode_span": [10, 60],
                 "segments": [{"episode_start": 10, "episode_end": 30}, {"episode_start": 300, "episode_end": 330}],
                 "short_coverage": 0.5}]}), encoding="utf-8")
            (d / "gold.json").write_text(json.dumps({"founder_review": "ok", "clips": [
                {"id": "w01", "category": "humour", "founder_would_post": "yes", "title": "x",
                 "time_segments": [{"start": 11, "end": 20}, {"start": 22, "end": 31}]},
                {"id": "w02", "category": "emotional", "founder_would_post": "no", "clip_purpose": "promo",
                 "pieces": [{"start": 500, "end": 540}]}]}), encoding="utf-8")
            cs = channel_short_sources("v", _P(d))
            hs = human_sources("v", _P(d))
        self.assertEqual([(s["start"], s["end"]) for s in cs], [(10.0, 30.0), (300.0, 330.0)])
        self.assertEqual(hs[0]["confidence"], "definite")
        self.assertEqual((hs[0]["start"], hs[0]["end"], hs[0]["type"]), (11.0, 31.0, "comedy"))
        self.assertEqual((hs[1]["confidence"], hs[1]["type"]), ("maybe", "hook"))
        ms = golden_set.build_moments(cs + hs)
        self.assertEqual([m["tier"] for m in ms], ["gold", "silver", "silver"])

    def test_comment_timestamps(self):
        self.assertEqual(comments.extract_timestamps("funniest at 12:40 and 1:02:03 lol 5:6"), [760.0, 3723.0])
        self.assertEqual(comments.extract("v"), [])


class TestRecall(unittest.TestCase):
    def test_match_and_unflagged(self):
        gs = {"episodes": {"v": {"moments": [
            {"id": "m01", "start": 100, "end": 140, "type": "comedy", "tier": "gold", "label": "a", "sources": []},
            {"id": "m02", "start": 500, "end": 520, "type": "hook", "tier": "silver", "label": "b", "sources": []}]}}}
        facts = {"stages": {"1a": {}, "3": {}}, "candidates": [
            {"id": "C1", "start": 95, "end": 145}, {"id": "C2", "start": 300, "end": 330},
            {"id": "C3", "start": 480, "end": 560, "segments": [[480, 500], [505, 560]]}],
            "final": {"clips": [{"id": "C2", "start": 300, "end": 330}]}}
        r = recall.report("v", facts, gs, 0.3)
        self.assertEqual(r["recall_candidates_gold"], 1.0)
        self.assertEqual(r["recall_candidates_silver"], 1.0)     # m02 is covered >= 50% by the long C3
        self.assertEqual(r["recall_final_all"], 0.0)
        self.assertEqual([u["id"] for u in r["unflagged_candidates"]], ["C2"])
        self.assertGreater(r["mean_iou_candidates"], 0.2)
        txt = recall.format_report(r)
        self.assertIn("FOUND  m01", txt)
        r0 = recall.report("v", None, gs)
        self.assertIsNone(r0["recall_candidates_gold"])
        self.assertTrue(r0["notes"])


if __name__ == "__main__":
    unittest.main()
