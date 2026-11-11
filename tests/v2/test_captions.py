"""Captions (founder, 2026-11-10): settings bundles + templates, cue grouping, wrapping, ASS text. No ffmpeg."""
import unittest

from selection_v2 import captions as C


def W(tok, s, e):
    return {"tok": tok, "start": s, "end": e, "line": "L1"}


class TestCues(unittest.TestCase):
    def test_pause_and_sentence_split(self):
        ws = [W("అవును", 0.0, 0.3), W("ఎందుకు?", 0.35, 0.7), W("నాకు", 0.8, 1.0), W("తెలియదు", 1.05, 1.4), W("గానీ", 2.5, 2.8)]
        cs = C.cues(ws)
        self.assertEqual([[w["tok"] for w in c["words"]] for c in cs], [["అవును", "ఎందుకు?"], ["నాకు", "తెలియదు"], ["గానీ"]])
        self.assertLessEqual(cs[0]["end"], cs[1]["start"])
        self.assertGreaterEqual(cs[2]["end"] - cs[2]["start"], C.MIN_CUE_S - 0.01)

    def test_width_and_fixed_words(self):
        ws = [W(t, i * 0.3, i * 0.3 + 0.25) for i, t in enumerate(["నేనెందుకింత", "తొందరగా", "లేచానో", "నాకు", "తెలియదు", "గానీ,", "నువ్వెందుకింత", "తొందరగా", "లేచావో"])]
        by_width = C.cues(ws, C.settings_for("clean"))
        self.assertGreater(len(by_width), 1)
        for c in by_width:
            self.assertLessEqual(len(C.wrap([w["tok"] for w in c["words"]], C.settings_for("clean"))), 2)
        three = C.cues(ws, C.settings_for("one_line"))            # 3 words per cue, one line
        self.assertEqual([len(c["words"]) for c in three], [3, 3, 3])

    def test_settings_and_colours(self):
        s = C.settings_for("boxed", size=90, pos_y=0.7)
        self.assertEqual(s["font"], "Mandali-Regular.ttf"); self.assertEqual(s["size"], 90); self.assertEqual(s["pos_y"], 0.7)
        self.assertEqual(C.ass_colour("#FFD400"), "&H0000D4FF"); self.assertEqual(C.ass_colour("#000000B3"), "&H4C000000")
        for name in C.TEMPLATES:
            self.assertTrue((C.FONTS / C.settings_for(name)["font"]).exists(), name)

    def test_ass(self):
        cs = C.cues([W("Marriage", 0, .4), W("అంటే", .45, .7), W("full", .75, .9), W("meals", .95, 1.3)])
        t = C.ass_text(cs, C.settings_for("clean"), "crop")
        self.assertIn("PlayResY: 1920", t); self.assertEqual(t.count("Dialogue:"), 1)
        self.assertIn("\\pos(540,1613)", t)                                 # pos_x 0.5, crop default pos_y 0.84
        h = C.ass_text(cs, C.settings_for("highlight"), "crop")
        self.assertEqual(h.count("Dialogue:"), 4); self.assertIn("\\c&H0000D4FF&", h)
        p = C.ass_text(cs, C.settings_for("pop"), "fit")
        self.assertIn("\\fscx112", p); self.assertIn("\\pos(540,1536)", p)   # fit default pos_y 0.80
        u = C.ass_text(cs, C.settings_for("clean", uppercase_latin=True), "crop")
        self.assertIn("MARRIAGE", u); self.assertIn("అంటే", u)
        f = C.ass_text(cs, C.settings_for("mandali"), "crop")
        self.assertIn("\\fad(120,120)", f)
