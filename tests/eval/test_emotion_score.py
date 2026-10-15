"""Offline tests for eval.emotion_score (emotion-trigger stage vs founder would-post) and eval.emotion_testset.
No LLM, no network.

Run: python -m unittest tests.eval.test_emotion_score
"""
import unittest

from eval import emotion_score as S
from eval import emotion_testset as T
from eval.common import ROOT, load_json


def res(vid, cid, founder, verdict, stop="clear", stay="clear", land="clear", **kw):
    return {"video_id": vid, "clip_id": cid, "founder_would_post": founder, "verdict": verdict,
            "stop": {"line": "s", "feeling": "f", "level": stop, "why": ""}, "stay": {"question": "q", "level": stay},
            "land": {"line": "l", "feeling": "f", "reaction": "r", "level": land, "why": ""},
            "reason": f"reason {cid}", "code_notes": [], "raw": None, "source": kw.pop("source", "gt"),
            "series": kw.pop("series", "A"), **kw}


# 10 clips: founder 4 yes / 3 maybe / 3 no; stage 4 yes / 2 maybe / 3 no / 1 unchecked
FAKE = [
    res("v1", "a", "yes", "yes"),
    res("v1", "b", "yes", "yes"),
    res("v1", "c", "yes", "maybe", land="weak"),
    res("v1", "d", "yes", "no", stop="weak", land="weak"),           # founder yes, stage no
    res("v1", "e", "maybe", "yes", same_moment_as=["f"]),
    res("v1", "f", "no", "no", land="weak", same_moment_as=["e"]),   # re-cut of e, rated differently
    res("v2", "a", "maybe", "maybe", source="v1_test", series="B", pipeline_overall=4.5),
    res("v2", "b", "maybe", "unchecked", source="v1_test", series="B", pipeline_overall=3.5),
    res("v2", "c", "no", "yes", source="v1_test", series="B", pipeline_overall=4.0),   # founder no, stage yes
    res("v2", "d", "no", "no", stop="weak", land="weak", source="v1_test", series="B", pipeline_overall=4.2),
]


class TestFisher(unittest.TestCase):
    def test_known_values(self):
        # scipy.stats.fisher_exact reference values
        p2, pg = S.fisher_exact(3, 1, 1, 3)        # Fisher's tea tasting
        self.assertAlmostEqual(p2, 0.485714, places=5)
        self.assertAlmostEqual(pg, 0.242857, places=5)
        self.assertAlmostEqual(S.fisher_exact(8, 2, 1, 5)[0], 0.034965, places=5)
        self.assertAlmostEqual(S.fisher_exact(1, 9, 11, 3)[0], 0.002759, places=5)

    def test_degenerate(self):
        self.assertEqual(S.fisher_exact(0, 0, 3, 4), (1.0, 1.0))   # nothing picked
        self.assertEqual(S.fisher_exact(2, 3, 0, 0), (1.0, 1.0))   # everything picked
        self.assertEqual(S.fisher_exact(0, 0, 0, 0), (1.0, 1.0))


class TestScore(unittest.TestCase):
    def setUp(self):
        self.rows, self.notes = S.load_rows(FAKE)

    def test_confusion(self):
        cm = S.confusion(self.rows)
        self.assertEqual(cm["yes"], {"yes": 2, "maybe": 1, "no": 1, "unchecked": 0})
        self.assertEqual(cm["maybe"], {"yes": 1, "maybe": 1, "no": 0, "unchecked": 1})
        self.assertEqual(cm["no"], {"yes": 1, "maybe": 0, "no": 2, "unchecked": 0})
        self.assertEqual(sum(sum(r.values()) for r in cm.values()), 10)

    def test_selection_yes(self):
        s = S.selection(self.rows, {"yes"})
        self.assertEqual((s["n"], s["n_sel"], s["sel_yes"], s["sel_no"]), (10, 4, 2, 1))
        self.assertEqual((s["base_yes"], s["base_no"], s["rest_yes"], s["n_rest"]), (4, 3, 2, 6))
        self.assertAlmostEqual(s["p_two_sided"], S.fisher_exact(2, 2, 2, 4)[0])

    def test_selection_yes_maybe(self):
        s = S.selection(self.rows, {"yes", "maybe"})
        self.assertEqual((s["n_sel"], s["sel_yes"], s["sel_no"]), (6, 3, 1))

    def test_labels_filled_from_clips_and_missing_reported(self):
        bare = [{"video_id": "v1", "clip_id": "a", "verdict": "yes"},
                {"video_id": "v1", "clip_id": "b", "verdict": "YES "},          # normalised
                {"video_id": "v1", "clip_id": "c", "verdict": "probably"}]      # invalid -> unchecked
        clips = [{"video_id": "v1", "clip_id": k, "founder_would_post": f, "series": "A", "source": "gt"}
                 for k, f in (("a", "yes"), ("b", "no"), ("c", "maybe"), ("d", "yes"))]
        rows, notes = S.load_rows(bare, clips)
        self.assertEqual([(r["clip_id"], r["founder_would_post"], r["verdict"]) for r in rows],
                         [("a", "yes", "yes"), ("b", "no", "yes"), ("c", "maybe", "unchecked")])
        self.assertTrue(any("have no result" in n and "v1 d" in n for n in notes))
        self.assertTrue(any("probably" in n for n in notes))

    def test_unlabelled_row_not_scored(self):
        rows, notes = S.load_rows([{"video_id": "x", "clip_id": "y", "verdict": "yes"}])
        self.assertEqual(rows, [])
        self.assertTrue(any("no founder verdict" in n for n in notes))

    def test_duplicate_result_ignored(self):
        rows, notes = S.load_rows(FAKE + [res("v1", "a", "yes", "no")])
        self.assertEqual(len(rows), 10)
        self.assertTrue(any("duplicate" in n for n in notes))

    def test_report_sections_and_lists(self):
        md, summ = S.report(self.rows)
        for h in ("## (a)", "## (b)", "## (d) Per source", "## (d) Per series", "## (e)", "## (f)", "## (g)",
                  "## (h)"):
            self.assertIn(h, md)
        self.assertEqual(summ["founder_no_stage_yes"], ["c"])
        self.assertEqual(summ["founder_yes_stage_no"], ["d"])
        g = md.split("## (g)")[1].split("## (h)")[0]
        self.assertIn("reason c", g)
        self.assertIn("reason d", g)
        self.assertNotIn("reason a", g)
        # (h): e/f are a re-cut pair in v1; v2's a..d share clip ids with v1 but are other clips
        h = md.split("## (h)")[1]
        self.assertIn("v1: e founder maybe / stage yes  vs  f founder no / stage no", h)
        self.assertEqual(h.count("\n- "), 1)

    def test_v1_tier_comparison(self):
        md, _ = S.report(self.rows)
        e = md.split("## (e)")[1].split("## (f)")[0]
        self.assertIn("4 v1_test clips", e)
        # v1 strong = a (4.5, maybe), c (4.0, no), d (4.2, no): 0/3 founder yes, 2/3 founder no
        self.assertIn("| v1 strong | 4 | 0/4 (0%) | 2/4 (50%) | 3 | 0/3 (0%) | 2/3 (67%) |", e)

    def test_part_levels(self):
        md, _ = S.report(self.rows)
        f = md.split("## (f)")[1].split("## (g)")[0]
        land = f.split("**land**")[1]
        # land clear: v1 a, b, e; v2 a, b, c -> founder yes 2, maybe 3, no 1
        self.assertIn("| clear | 6 | 2 | 3 | 1 | 2/6 (33%) |", land)
        self.assertLess(land.index("| clear |"), land.index("| weak |"))
        self.assertIn("| weak | 4 | 2 | 0 | 2 | 2/4 (50%) |", land)

    def test_level_order(self):
        self.assertEqual(S._level_sort([1, "(missing)", 3, 2]), [3, 2, 1, "(missing)"])
        self.assertEqual(S._level_sort(["none", "(missing)", "weak", "clear"]), ["clear", "weak", "none", "(missing)"])

    def test_not_run_rows_skipped(self):
        rows, notes = S.load_rows(FAKE + [res("v3", "z", "yes", "not_run")])
        self.assertEqual(len(rows), 10)
        self.assertTrue(any("not_run" in n for n in notes))


class TestTestset(unittest.TestCase):
    WORDS = [{"word": "a", "start": 10.0, "end": 10.5}, {"word": "b", "start": 10.6, "end": 11.0},
             {"word": "c", "start": 20.0, "end": 20.4}, {"word": "d", "start": 20.45, "end": 21.0}]

    def test_padded_segments_match_preview_padding(self):
        pieces = [{"start_word": 0, "end_word": 0, "start": 10.0, "end": 10.5},
                  {"start_word": 2, "end_word": 2, "start": 20.0, "end": 20.4}]
        segs = T.padded_segments(pieces, self.WORDS)
        self.assertAlmostEqual(segs[0][0], 9.9)          # 0.10 s before
        self.assertAlmostEqual(segs[0][1], 10.55)        # halfway to the next word (0.20 s would reach into it)
        self.assertAlmostEqual(segs[1][0], 19.9)
        self.assertAlmostEqual(segs[1][1], 20.425)

    def test_touching_pieces_merge(self):
        pieces = [{"start_word": 0, "end_word": 0, "start": 10.0, "end": 10.5},
                  {"start_word": 1, "end_word": 1, "start": 10.6, "end": 11.0}]
        self.assertEqual(len(T.padded_segments(pieces, self.WORDS)), 1)

    def test_cold_open_from_backward_jump(self):
        segs = [{"start": 50, "end": 52}, {"start": 30, "end": 49}]
        self.assertTrue(T.is_cold_open({}, segs))
        self.assertFalse(T.is_cold_open({}, list(reversed(segs))))

    def test_committed_testset_is_consistent(self):
        p = ROOT / "runs" / "emotion_test" / "clips.json"
        if not p.exists():
            self.skipTest("runs/emotion_test/clips.json not built")
        clips = load_json(p)
        ids = [(c["video_id"], c["clip_id"]) for c in clips]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len({c["clip_id"] for c in clips}), len(clips))   # unique without the video id too
        for c in clips:
            self.assertIn(c["founder_would_post"], S.FOUNDER)
            self.assertTrue(c["time_segments"])
            self.assertTrue(all(s["end"] > s["start"] for s in c["time_segments"]))
            self.assertIsInstance(c["cold_open"], bool)


if __name__ == "__main__":
    unittest.main()
