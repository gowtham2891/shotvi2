"""Web-series pipeline ws/0.2 fixes (shotvi/select/webseries.py), offline:
1. a clip repaired after review is never rejected on the broken cut's scores (needs_rescore);
2. the end when the next line starts quickly (reaction / shot change before the next line / just before it);
3. shot flashes at segment edges (repaired unless that clips a played word)."""
import copy
import unittest

from shotvi.select.webseries import (DEFAULTS, apply_review, build_clip, end_hold, end_hold_v1, fix_flashes,
                                     play_text, proposal_doc, rank_clips)
from tests.select.test_webseries_cut import SCENE, CUTS, make_ep, moment, words_played

CFG = DEFAULTS["cut"]
PAYOFF_END = 31.98          # last word of SCENE's L0008 ("payoff line end ok", 30.0-32.0)


def line_id(ep, text):
    return next(r["id"] for r in ep.lines if r.get("text") == text)


# ============================================================================ 2. end when the next line is quick
class QuickEnd(unittest.TestCase):
    def hold(self, extra, cuts, events=()):
        ep = make_ep(SCENE[:8] + list(extra), cuts, events=events)
        return ep, end_hold(ep, ep.by_id["L0008"]["words"][1], CFG)

    def test_reaction_played_and_held_to_the_shot_change(self):
        ep, h = self.hold([(32.3, 32.6, "ఆ!"), (45.0, 47.0, "next scene line")], [9.9, 33.5, 44.0])
        self.assertEqual(h["rule"], "reaction")
        self.assertEqual(h["reaction"]["text"], "ఆ!")
        self.assertAlmostEqual(h["end"], 33.495, places=3)
        self.assertTrue(h["on_shot_cut"])
        self.assertAlmostEqual(h["payoff_end"], PAYOFF_END, places=3)

    def test_reaction_prefers_a_shot_change_after_the_minimum_hold(self):
        _, h = self.hold([(32.3, 32.6, "ఆ!"), (45.0, 47.0, "next scene line")], [9.9, 33.5, 35.0, 44.0])
        self.assertAlmostEqual(h["end"], 34.995, places=3)

    def test_reaction_holds_through_a_laugh(self):
        _, h = self.hold([(32.3, 32.6, "ఆ!"), (45.0, 47.0, "next scene line")], [9.9, 33.0, 34.6, 44.0],
                         events=[(32.0, 34.0, "[నవ్వు]")])
        self.assertEqual(h["rule"], "reaction")
        self.assertAlmostEqual(h["end"], 34.595, places=3)

    def test_three_word_reaction_counts_four_words_do_not(self):
        _, h = self.hold([(32.3, 33.2, "ఆ అవునా నిజమా"), (45.0, 47.0, "next scene line")], [9.9, 34.0, 44.0])
        self.assertEqual(h["rule"], "reaction")
        _, h = self.hold([(32.3, 33.2, "ఆ అవునా నిజమా ఏంటి"), (45.0, 47.0, "next scene line")], [9.9, 34.0, 44.0])
        self.assertNotEqual(h["rule"], "reaction")

    def test_short_line_that_gets_a_reply_opens_an_exchange(self):
        # "ఏంటి" answered 0.4 s later: not a reaction; the clip ends on the shot change before it instead
        _, h = self.hold([(32.3, 32.6, "ఏంటి"), (33.0, 35.0, "why did you say that now")], [9.9, 32.15, 36.0])
        self.assertEqual(h["rule"], "shot_change_before_next_line")
        self.assertAlmostEqual(h["end"], 32.145, places=3)
        self.assertTrue(h["on_shot_cut"])

    def test_no_shot_change_ends_just_before_the_next_line(self):
        _, h = self.hold([(32.3, 33.5, "one two three four five six")], [9.9, 34.0])
        self.assertEqual(h["rule"], "before_next_line")
        self.assertAlmostEqual(h["end"], 32.3 - CFG["quick_end_margin_s"], places=3)
        self.assertFalse(h["on_shot_cut"])

    def test_reaction_without_a_shot_change_after_it_is_not_played(self):
        ep, h = self.hold([(32.3, 32.6, "ఆ!"), (45.0, 47.0, "next scene line")], [9.9, 44.5])
        self.assertEqual(h["rule"], "before_next_line")
        self.assertLess(h["end"], 32.3)

    def test_never_runs_into_the_line_after_a_reaction(self):
        # reaction, then the next line 1.2 s later; the only shot change after the reaction is past that line
        _, h = self.hold([(32.3, 32.6, "ఆ!"), (33.8, 35.0, "new exchange starts here now")], [9.9, 34.5])
        self.assertLess(h["end"], 33.8)

    def test_room_for_the_hold_keeps_the_ws01_rule(self):
        ep = make_ep(SCENE, CUTS)
        lw = ep.by_id["L0008"]["words"][1]
        self.assertEqual(end_hold(ep, lw, CFG), {**end_hold_v1(ep, lw, CFG), "rule": "hold"})

    def test_clip_plays_the_reaction_word(self):
        ep = make_ep(SCENE[:8] + [(32.3, 32.6, "ఆ!"), (45.0, 47.0, "next scene line")], [9.9, 33.5, 44.0])
        c = build_clip(ep, moment(), CFG)
        rid = line_id(ep, "ఆ!")
        self.assertIn(ep.by_id[rid]["words"][0], words_played(ep, c))
        self.assertEqual(c["end_line"], "L0008")
        self.assertAlmostEqual(c["time_segments"][-1]["end"], 33.495, places=3)
        # the ws/0.1 view (what run ws_r1's review saw) does not play it
        v = build_clip(ep, moment(), CFG, view="ws/0.1")
        self.assertNotIn(ep.by_id[rid]["words"][0], words_played(ep, v))


# ============================================================================ 3. flashes
class Flashes(unittest.TestCase):
    def test_end_sliver_moves_to_the_shot_change(self):
        ep = make_ep(SCENE, [9.9, 32.1])
        segs = [{"start": 9.9, "end": 32.3, "kind": "scene"}]
        f = fix_flashes(ep, segs, CFG)
        self.assertEqual(len(f), 1)
        self.assertTrue(f[0]["fixed"])
        self.assertAlmostEqual(segs[0]["end"], 32.095, places=3)

    def test_end_sliver_kept_when_moving_clips_a_word(self):
        ep = make_ep(SCENE, [9.9, 31.9])           # the cut falls inside the last word (31.5-31.98)
        segs = [{"start": 9.9, "end": 32.1, "kind": "scene"}]
        f = fix_flashes(ep, segs, CFG)
        self.assertFalse(f[0]["fixed"])
        self.assertEqual(f[0]["word"], "ok")
        self.assertEqual(segs[0]["end"], 32.1)

    def test_start_sliver_moves_to_the_shot_change(self):
        ep = make_ep(SCENE, [9.85, 35.0])
        segs = [{"start": 9.7, "end": 32.5, "kind": "scene"}]
        f = fix_flashes(ep, segs, CFG)
        self.assertTrue(f[0]["fixed"])
        self.assertEqual(segs[0]["start"], 9.85)

    def test_start_sliver_kept_when_the_first_word_starts_before_the_cut(self):
        ep = make_ep(SCENE, [10.1, 35.0])          # first word 10.0-10.48 starts 0.1 s before the cut
        segs = [{"start": 9.9, "end": 32.5, "kind": "scene"}]
        f = fix_flashes(ep, segs, CFG)
        self.assertFalse(f[0]["fixed"])
        self.assertEqual(f[0]["edge"], "start")
        self.assertEqual(f[0]["word"], "a")
        self.assertEqual(segs[0]["start"], 9.9)

    def test_short_source_shot_played_whole_is_not_a_flash(self):
        ep = make_ep(SCENE, [9.9, 32.0, 32.2])
        segs = [{"start": 9.9, "end": 32.195, "kind": "scene"}]
        self.assertEqual(fix_flashes(ep, segs, CFG), [])
        self.assertEqual(segs[0]["end"], 32.195)

    def test_drop_join_edge_repaired(self):
        # a shot change 0.02 s after L0004's last word: the padded join-out (+0.25 s) would show a 0.23 s sliver
        ep = make_ep(SCENE, [9.9, 20.0, 26.6, 35.0])
        c = build_clip(ep, moment(drops=[{"first_line": "L0005", "last_line": "L0006", "why": "side talk"}]), CFG)
        self.assertAlmostEqual(c["time_segments"][0]["end"], 19.995, places=3)
        self.assertTrue(any(f["fixed"] and f["edge"] == "end" for f in c["flashes"]))
        self.assertAlmostEqual(c["cuts"][0]["start"], 19.995, places=3)    # cut info follows the repaired edge
        self.assertIn(ep.by_id["L0004"]["words"][1], words_played(ep, c))

    def test_kept_start_flash_is_reported_on_the_clip(self):
        spec = [(5.0, 9.9, "x y z")] + SCENE       # previous word ends 9.88: the start pad is only 0.1 s
        ep = make_ep(spec, [10.1, 35.0])
        c = build_clip(ep, moment(start_line="L0002", end_line="L0009"), CFG)
        self.assertEqual(c["time_segments"][0]["start"], 9.9)
        self.assertFalse(c["flashes"][0]["fixed"])
        self.assertTrue(any("flash kept" in w for w in c["warnings"]))

    def test_quick_end_never_leaves_a_sliver(self):
        # a shot change 0.27 s before the next line: rule b ends on it (ws/0.1 ended 0.1 s before the line,
        # leaving a 0.17 s sliver of the new shot)
        ep = make_ep(SCENE[:8] + [(32.6, 34.0, "one two three four five six")], [9.9, 32.33, 36.0])
        c = build_clip(ep, moment(), CFG)
        self.assertAlmostEqual(c["time_segments"][-1]["end"], 32.325, places=3)
        self.assertEqual(c["end"]["end"], c["time_segments"][-1]["end"])
        self.assertTrue(c["end"]["on_shot_cut"])
        self.assertEqual(c["flashes"], [])
        v = build_clip(ep, moment(), CFG, view="ws/0.1")
        self.assertAlmostEqual(v["time_segments"][-1]["end"], 32.5, places=3)

    def test_ws01_view_has_no_flash_repair(self):
        ep = make_ep(SCENE, [9.9, 20.0, 26.6, 35.0])
        c = build_clip(ep, moment(drops=[{"first_line": "L0005", "last_line": "L0006", "why": ""}]), CFG,
                       view="ws/0.1")
        self.assertAlmostEqual(c["time_segments"][0]["end"], 20.23, places=3)
        self.assertEqual(c["flashes"], [])


# ============================================================================ 1. stale-score rejection
def review(**kw):
    r = {"id": "w01", "hook": 2.5, "standalone": 2.0, "payoff": 2.5, "value_emotion": 2.5, "overall": 2.0,
         "keep_cold_open": True, "cold_open_note": "", "hook_without_cold_open": 2.5,
         "overall_without_cold_open": 2.0, "bad_cuts": [], "cut_note": "", "title": "తెలుగు title",
         "why_it_works": "why", "category": "story"}
    r.update(kw)
    return r


class RepairedClip(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(DEFAULTS)
        self.ep = make_ep(SCENE, CUTS)

    def kept(self, cid="w01", **m):
        mm = moment(**m)
        c = build_clip(self.ep, mm, self.cfg["cut"])
        play_text(self.ep, cid, mm, c)             # sets the X markers the review refers to
        return {"id": cid, "moment": mm, "clip": c}

    def run_review(self, kept, reviews):
        apply_review(self.ep, kept, reviews, self.cfg)
        rejected = []
        return rank_clips(kept, rejected, self.cfg), rejected

    def test_repaired_clip_under_the_bar_is_rejected_with_a_rescore_note(self):
        # absolute tiers (2026-10-07): a repaired clip is tiered on its scores like any other; under the bar it is
        # rejected (never padded in as a maybe), with the note that the scores are for the cut before the repair
        x = self.kept(drops=[{"first_line": "L0005", "last_line": "L0006", "why": "side talk"}])
        self.assertEqual(len(x["clip"]["time_segments"]), 2)
        shown, rejected = self.run_review([x], {"w01": review(bad_cuts=["X1"], cut_note="X1 loses the context")})
        self.assertEqual(shown, [])
        self.assertEqual(len(x["clip"]["time_segments"]), 1)          # the drop was still put back
        self.assertIn("X1 loses the context", x["needs_rescore"])
        self.assertEqual([r["id"] for r in rejected], ["w01"])
        self.assertEqual(rejected[0]["scores"]["overall"], 2.0)        # the reviewer's numbers are kept as given
        self.assertIn("re-score", rejected[0]["reject_reasons"][0])
        self.assertIn("needs_rescore", rejected[0]["flaws"])

    def test_repaired_clip_is_capped_at_worth_a_look(self):
        x = self.kept(drops=[{"first_line": "L0005", "last_line": "L0006", "why": ""}])
        rv = review(hook=4.5, standalone=4.0, payoff=4.5, value_emotion=4.0, overall=4.2,
                    hook_without_cold_open=4.5, overall_without_cold_open=4.2, bad_cuts=["X1"])
        shown, rejected = self.run_review([x], {"w01": rv})
        self.assertEqual(rejected, [])
        doc = proposal_doc(self.ep, shown[0], 1, self.cfg, {})
        self.assertEqual(doc["tier"], "worth_a_look")                  # scores would recommend; the flaw blocks it
        self.assertTrue(doc["needs_rescore"])
        self.assertIn("before the repair", doc["rescore_reason"])
        self.assertEqual(doc["flaws"], ["needs_rescore"])

    def test_unrepaired_low_score_is_still_rejected(self):
        x = self.kept(drops=[{"first_line": "L0005", "last_line": "L0006", "why": ""}])
        shown, rejected = self.run_review([x], {"w01": review()})     # no bad cut flagged: the score is current
        self.assertEqual(shown, [])
        self.assertTrue(rejected[0]["reject_reasons"][0].startswith("Q1"))
        self.assertFalse(x.get("needs_rescore"))

    def test_no_padding_below_the_bar(self):
        # absolute tiers: a repaired clip scored 2.0 and a 3.2 clip are both rejected; only the 3.6 clip is shown
        a = self.kept("w01", drops=[{"first_line": "L0005", "last_line": "L0006", "why": ""}])
        b = self.kept("w02")
        c = self.kept("w03")
        reviews = {"w01": review(bad_cuts=["X1"]),
                   "w02": review(id="w02", hook=4.0, standalone=4.0, payoff=4.0, overall=3.6,
                                 hook_without_cold_open=4.0, overall_without_cold_open=3.6),
                   "w03": review(id="w03", hook=3.5, standalone=3.5, payoff=3.5, overall=3.2,
                                 hook_without_cold_open=3.5, overall_without_cold_open=3.2)}
        shown, rejected = self.run_review([a, b, c], reviews)
        self.assertEqual([y["id"] for y in shown], ["w02"])
        self.assertEqual(shown[0]["tier"], "worth_a_look")
        self.assertEqual(sorted(r["id"] for r in rejected), ["w01", "w03"])
        self.assertTrue(all(r["scores"] for r in rejected))           # scores kept on rejected entries


class CacheOnlyMakesNoCalls(unittest.TestCase):
    def test_count_tokens_refused_in_cache_only_mode(self):
        import tempfile
        from unittest import mock
        from shotvi.llm import Ledger, LLMClient, ModelSpec
        from shotvi.llm.base import LLMError
        with tempfile.TemporaryDirectory() as d:
            llm = LLMClient(Ledger(max_usd=0.01), cache_dir=d)
            llm.cache_only = True
            spec = ModelSpec.from_dict({"provider": "gemini", "model": "gemini-3.8-flash"})
            with mock.patch("shotvi.llm.client.get_provider") as gp:
                with self.assertRaises(LLMError):
                    llm.count_tokens(spec, "text")
                gp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
