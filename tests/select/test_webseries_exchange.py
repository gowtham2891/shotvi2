"""Web-series exchange start (shotvi/select/webseries.py ``exchange_walk``): offline, synthetic lines and cuts."""
import unittest

from shotvi.select.webseries import (DEFAULTS, Episode, apply_review, barrier_for, build_clip, deep_merge,
                                     exchange_walk)

from .test_webseries_cut import make_ep

OFF = DEFAULTS["cut"]
ON = {**DEFAULTS["cut"], "exchange_start": True, "exchange_max_play_s": 120.0}

# a scene after the recap zone: one exchange L0002..L0006 (gaps <= 1 s), a 5 s break before it
SCENE = [(100.0, 102.0, "old scene line"),        # L0001
         (107.0, 109.0, "where were you yesterday?"),  # L0002 exchange opener (5 s silence before it)
         (109.5, 111.0, "at the market"),          # L0003
         (111.8, 113.0, "with whom tell me"),      # L0004
         (113.5, 116.0, "with my old friend raju"),  # L0005 model's start
         (116.5, 119.0, "he still owes me money"),  # L0006
         (119.5, 122.0, "payoff line lands here"),  # L0007 payoff
         (135.0, 137.0, "next scene starts")]      # L0008
CUTS = [99.9, 106.8, 113.3, 122.9, 124.0, 134.8]


def moment(**kw):
    m = {"purpose": "standalone", "start_line": "L0005", "end_line": "L0007", "cold_open_lines": [], "drops": []}
    m.update(kw)
    return m


def first_word_time(ep, c):
    return ep.words[c["pieces"][0]["start_word"]]["start"]


class Walk(unittest.TestCase):
    def test_off_by_default(self):
        self.assertFalse(DEFAULTS["cut"]["exchange_start"])
        ep = make_ep(SCENE, CUTS)
        c = build_clip(ep, moment(), OFF)
        self.assertEqual(c["start_line"], "L0005")
        self.assertNotIn("start_extended_by", c)

    def test_walks_back_to_the_exchange_opener_and_stops_at_the_gap(self):
        ep = make_ep(SCENE, CUTS)
        c = build_clip(ep, moment(), ON)
        self.assertEqual(c["start_line"], "L0002")
        se = c["start_extended_by"]
        self.assertEqual((se["from_line"], se["to_line"], se["lines"]), ("L0005", "L0002", 3))
        self.assertTrue(se["stop"].startswith("gap"))
        self.assertAlmostEqual(se["s"], c["start_extended_by"]["s"])
        self.assertGreater(se["s"], 6.0)
        # opening rules still hold: first spoken word of the new line, <= 0.3 s lead-in (shot cut at 106.8)
        self.assertEqual(c["time_segments"][0]["start"], 106.8)
        self.assertLessEqual(first_word_time(ep, c) - c["time_segments"][0]["start"], 0.3 + 1e-6)

    def test_gap_threshold_is_the_config_value(self):
        ep = make_ep(SCENE, CUTS)
        # the largest gap inside the exchange is 0.8 s (L0003 -> L0004): G below it stops there
        c = build_clip(ep, moment(), {**ON, "exchange_gap_s": 0.7})
        self.assertEqual(c["start_line"], "L0004")

    def test_music_event_stops_and_never_leads_in(self):
        ep = make_ep(SCENE, CUTS, events=[(111.05, 111.75, "[బ్యాక్ గ్రౌండ్ మ్యూజిక్]")])
        c = build_clip(ep, moment(start_line=ep_id(ep, "with my old")), ON)
        self.assertEqual(ep.text(ep.by_id[c["start_line"]]), "with whom tell me")
        self.assertTrue(c["start_extended_by"]["stop"].startswith("music"))
        self.assertGreaterEqual(c["time_segments"][0]["start"], 111.75 - 1e-6)    # no music in the lead-in
        self.assertLessEqual(first_word_time(ep, c) - c["time_segments"][0]["start"], 0.3 + 1e-6)

    def test_cap_keeps_the_original_start(self):
        # 20 lines, 1.5 s apart, no break: the walk passes 30 s without finding an opener
        spec = [(100.0 + 2.0 * k, 101.5 + 2.0 * k, f"line number {k} here") for k in range(25)]
        ep = make_ep(spec, [])
        m = moment(start_line="L0022", end_line="L0025")
        c = build_clip(ep, m, ON)
        self.assertEqual(c["start_line"], "L0022")
        self.assertTrue(c["start_extended_by"]["stop"].startswith("cap"))
        self.assertEqual(c["start_extended_by"]["lines"], 0)

    def test_cap_stop_mode_extends_to_the_cap(self):
        spec = [(100.0 + 2.0 * k, 101.5 + 2.0 * k, f"line number {k} here") for k in range(25)]
        ep = make_ep(spec, [])
        c = build_clip(ep, moment(start_line="L0022", end_line="L0025"),
                       {**ON, "exchange_on_cap": "stop", "exchange_back_max_lines": 99})
        t0 = ep.w0(ep.by_id["L0022"])
        self.assertLessEqual(t0 - ep.w0(ep.by_id[c["start_line"]]), 30.0)
        self.assertEqual(c["start_line"], "L0007")      # 15 lines x 2 s = 30 s

    def test_line_cap(self):
        spec = [(100.0 + 2.0 * k, 101.5 + 2.0 * k, f"line number {k} here") for k in range(25)]
        ep = make_ep(spec, [])
        c = build_clip(ep, moment(start_line="L0022", end_line="L0025"),
                       {**ON, "exchange_on_cap": "stop", "exchange_back_max_lines": 4})
        self.assertEqual(c["start_line"], "L0018")

    def test_soft_max_guardrail_keeps_the_original_start(self):
        ep = make_ep(SCENE, CUTS)
        base = build_clip(ep, moment(), OFF)["video_duration"]
        c = build_clip(ep, moment(), {**ON, "exchange_max_play_s": base + 1.0})
        self.assertEqual(c["start_line"], "L0005")
        self.assertIn("soft max", c["start_extended_by"]["kept_original"])
        self.assertEqual(c["video_duration"], base)

    def test_recap_zone_stops(self):
        shifted = [(a - 50.0, b - 50.0, t) for a, b, t in SCENE]     # L0002, L0003 start before 60 s
        ep = make_ep(shifted, [c - 50.0 for c in CUTS])
        c = build_clip(ep, moment(), ON)
        self.assertEqual(c["start_line"], "L0004")                  # 61.8 s: outside the zone
        self.assertTrue(c["start_extended_by"]["stop"].startswith("recap"))

    def test_sponsor_line_stops(self):
        spec = list(SCENE)
        spec[2] = (109.5, 111.0, "please subscribe now")
        ep = make_ep(spec, CUTS)
        c = build_clip(ep, moment(), ON)
        self.assertEqual(c["start_line"], "L0004")
        self.assertTrue(c["start_extended_by"]["stop"].startswith("sponsor"))

    def test_never_crosses_another_clips_payoff(self):
        ep = make_ep(SCENE, CUTS)
        other = moment(start_line="L0002", end_line="L0003")
        mine = moment()
        b = barrier_for(ep, [other, mine], mine)
        self.assertEqual(b, ep.pos["L0003"])
        c = build_clip(ep, mine, ON, barrier_pos=b)
        self.assertEqual(c["start_line"], "L0004")
        self.assertTrue(c["start_extended_by"]["stop"].startswith("payoff"))

    def test_filler_opener_is_skipped(self):
        spec = list(SCENE)
        spec[1] = (108.6, 109.0, "ఉమ్")          # filler opener right before L0003 (0.5 s gap)
        ep = make_ep(spec, CUTS)
        w = exchange_walk(ep, ep.by_id["L0005"], ON)
        self.assertEqual(w["line"], "L0003")
        self.assertEqual(w["skipped_fillers"], ["L0002"])

    def test_drops_and_lift_are_judged_against_the_moments_own_start(self):
        ep = make_ep(SCENE, CUTS)
        # a drop that starts AT the moment's start line is ignored with or without the extension
        m = moment(start_line="L0004", drops=[{"first_line": "L0004", "last_line": "L0005", "why": "x"}],
                   cold_open_lines=["L0004"])
        a, b = build_clip(ep, m, OFF), build_clip(ep, m, ON)
        self.assertEqual(a["cuts"], [])
        self.assertEqual(b["cuts"], [])
        self.assertEqual(a["lift_rejected"], b["lift_rejected"])
        self.assertEqual(b["start_line"], "L0002")

    def test_review_view_ws01_is_not_extended(self):
        # cache replays review the clip as ws_r1 built it: the review view never moves the start
        ep = make_ep(SCENE, CUTS)
        self.assertEqual(build_clip(ep, moment(), ON, view="ws/0.1")["start_line"], "L0005")


class ReviewWarning(unittest.TestCase):
    def test_unreviewed_start_words_are_flagged(self):
        ep = make_ep(SCENE, CUTS)
        m = moment()
        x = {"id": "w01", "moment": m, "clip": build_clip(ep, m, ON), "review_saw": build_clip(ep, m, ON, view="ws/0.1")}
        r = {"id": "w01", "hook": 4, "standalone": 4, "payoff": 4, "value_emotion": 4, "overall": 4,
             "keep_cold_open": False, "cold_open_note": "", "hook_without_cold_open": 4,
             "overall_without_cold_open": 4, "bad_cuts": [], "cut_note": "", "title": "t", "why_it_works": "w",
             "category": "humour"}
        apply_review(ep, [x], {"w01": r}, deep_merge(DEFAULTS, {"cut": ON}))
        self.assertTrue(x["clip"].get("unreviewed_start_words"))
        self.assertTrue(any("review did not see" in w and "exchange start" in w for w in x["clip"]["warnings"]))


def ep_id(ep: Episode, prefix: str) -> str:
    return next(r["id"] for r in ep.lines if r["kind"] == "speech" and r["text"].startswith(prefix))


if __name__ == "__main__":
    unittest.main()
