"""tools/segment_review.py page build and chapters.compare on synthetic data."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from shotvi.segment import segment_dict
from shotvi.segment.chapters import compare, is_opening_title, load_chapters

from .test_intro import with_teaser

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import segment_review  # noqa: E402


class TestReviewPage(unittest.TestCase):
    def test_page_builds_offline(self):
        d, n = with_teaser()
        with tempfile.TemporaryDirectory() as td:
            vd = Path(td) / "vid1"
            vd.mkdir()
            tp = vd / "vid1_scribe_transcript.json"
            tp.write_text(json.dumps(d), encoding="utf-8")
            (vd / "youtube_meta.json").write_text(json.dumps(
                {"chapters": [{"title": "Trailer", "start": 0, "end": 20}, {"title": "Topic", "start": 25, "end": 99}]}),
                encoding="utf-8")
            out = Path(td) / "out"
            self.assertEqual(segment_review.main([str(tp), "--out", str(out)]), 0)
            page = (out / "review.html").read_text(encoding="utf-8")
            self.assertIn("Segmentation review", page)
            data = json.loads(page.split("const D = ", 1)[1].split(";\nconst KEY", 1)[0])
            self.assertEqual(data["vid"], "vid1")
            self.assertEqual(data["ranges"][0]["kind"], "teaser")
            self.assertEqual(data["opening"]["mode"], "rules")
            self.assertEqual(len(data["chapters"]), 2)
            seg_units = data["units"]
            self.assertGreater(len(seg_units), 5)
            self.assertTrue(all(u["runs"] for u in seg_units))
            self.assertIn("vid1/vid1.mp4", page)


class TestChapters(unittest.TestCase):
    def test_compare(self):
        d, n = with_teaser()
        seg = segment_dict(d)
        t_end = seg.intro_ranges[0].end
        ch = [{"title": "Introduction", "start": 0, "end": round(t_end)},
              {"title": "Meet the guest", "start": round(t_end), "end": round(t_end) + 5},
              {"title": "Career", "start": round(t_end) + 5, "end": 200}]
        r = compare(seg, ch)
        self.assertLessEqual(abs(r["teaser_end_minus_first_chapter_end"]), 0.6)
        self.assertEqual(r["chapter_content_start_s"], round(t_end) + 5)
        self.assertEqual(r["chapter_vs_units"]["n_chapter_starts"], 2)
        self.assertTrue(is_opening_title("Podcast Highlights") and not is_opening_title("Career"))
        self.assertIsNone(load_chapters(ROOT / "no_such_dir" / "x.json"))


if __name__ == "__main__":
    unittest.main()
