"""Web-series absolute tiers + episode verdict (founder, 2026-10-07), offline:
recommended = overall >= t_rec AND payoff >= t_rec_payoff AND no blocking flaw; worth_a_look = overall >= t_show;
everything else rejected with its scores kept (no "top 3 below threshold" padding). Within a tier: overall, payoff,
discovery order. episode_verdict: none / thin / strong."""
import copy
import unittest

from shotvi.select.webseries import (DEFAULTS, apply_review, build_clip, clip_flaws, clips_md, episode_verdict,
                                     play_text, proposal_doc, rank_clips, rejected_doc, tier_of)
from tests.select.test_webseries_cut import SCENE, CUTS, make_ep, moment
from tests.select.test_webseries_v02 import review

TH = DEFAULTS["thresholds"]


def sc(overall, payoff=4.0, hook=4.0, standalone=4.0):
    return {"hook": hook, "standalone": standalone, "payoff": payoff, "value_emotion": 4.0, "overall": overall}


class TierRule(unittest.TestCase):
    def test_recommended_needs_overall_and_payoff_and_no_flaw(self):
        self.assertEqual(tier_of(sc(4.0, 4.0), [], TH), "recommended")
        self.assertEqual(tier_of(sc(4.5, 3.5), [], TH), "worth_a_look")        # payoff under t_rec_payoff
        self.assertEqual(tier_of(sc(3.9, 4.5), [], TH), "worth_a_look")        # overall under t_rec
        for flaw in ("needs_rescore", "shot_flash", "unreviewed_start", "review_repair_failed"):
            self.assertEqual(tier_of(sc(4.5, 4.5), [flaw], TH), "worth_a_look", flaw)
        self.assertEqual(tier_of(sc(4.5, 4.5), ["unreviewed_end"], TH), "recommended")   # not a blocker by default
        th = {**TH, "rec_blockers": TH["rec_blockers"] + ["unreviewed_end"]}
        self.assertEqual(tier_of(sc(4.5, 4.5), ["unreviewed_end"], th), "worth_a_look")

    def test_below_the_bar_is_rejected_whatever_the_others_say(self):
        self.assertEqual(tier_of(sc(3.5, 3.0), [], TH), "worth_a_look")
        self.assertEqual(tier_of(sc(3.4, 4.5), [], TH), "rejected")
        self.assertEqual(tier_of(sc(4.5, 4.5, standalone=2.0), [], TH), "rejected")     # a part score <= 2

    def test_thresholds_are_config(self):
        th = {**TH, "t_rec": 4.5, "t_rec_payoff": 4.5, "t_show": 4.0}
        self.assertEqual(tier_of(sc(4.0, 4.0), [], th), "worth_a_look")
        self.assertEqual(tier_of(sc(4.5, 4.5), [], th), "recommended")
        self.assertEqual(tier_of(sc(3.9, 4.5), [], th), "rejected")


class RankAndReject(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(DEFAULTS)
        self.ep = make_ep(SCENE, CUTS)

    def kept(self, cid, **m):
        mm = moment(**m)
        c = build_clip(self.ep, mm, self.cfg["cut"])
        play_text(self.ep, cid, mm, c)
        return {"id": cid, "moment": mm, "clip": c}

    def rv(self, cid, overall, payoff=4.0, hook=4.0, standalone=4.0):
        return review(id=cid, hook=hook, standalone=standalone, payoff=payoff, value_emotion=4.0, overall=overall,
                      hook_without_cold_open=hook, overall_without_cold_open=overall)

    def go(self, ids_scores):
        kept = [self.kept(cid) for cid, _ in ids_scores]
        reviews = {cid: self.rv(cid, *args) for cid, args in ids_scores}
        apply_review(self.ep, kept, reviews, self.cfg)
        rejected = []
        shown = rank_clips(kept, rejected, self.cfg)
        return shown, rejected, kept

    def test_tiers_then_overall_then_payoff_then_discovery_order(self):
        # kept arrives in time order (here reversed); ties keep the discovery order (w03 before w05)
        shown, rejected, _ = self.go([("w05", (4.0, 4.0)), ("w04", (4.0, 3.5)), ("w03", (4.0, 4.0)),
                                       ("w02", (4.5, 4.0)), ("w01", (3.7, 4.5)), ("w06", (3.6, 4.5)),
                                       ("w07", (3.4, 5.0))])
        self.assertEqual([x["id"] for x in shown], ["w02", "w03", "w05", "w04", "w01", "w06"])
        self.assertEqual([x["tier"] for x in shown],
                         ["recommended", "recommended", "recommended", "worth_a_look", "worth_a_look", "worth_a_look"])
        self.assertEqual([r["id"] for r in rejected], ["w07"])
        self.assertEqual(rejected[0]["scores"]["overall"], 3.4)
        self.assertEqual(rejected[0]["tier"], "rejected")
        self.assertTrue(rejected[0]["reject_reasons"][0].startswith("Q1"))

    def test_no_padding_when_everything_is_under_the_bar(self):
        shown, rejected, _ = self.go([("w01", (3.4,)), ("w02", (3.2,)), ("w03", (3.0,)), ("w04", (2.9,))])
        self.assertEqual(shown, [])
        self.assertEqual(len(rejected), 4)
        self.assertNotIn("Q2", " ".join(r["reject_reasons"][0] for r in rejected))

    def test_flaw_blocks_recommended_but_keeps_worth_a_look(self):
        shown, rejected, kept = self.go([("w01", (4.5, 4.5))])
        x = kept[0]
        self.assertEqual(x["tier"], "recommended")
        x["clip"]["flashes"] = [{"fixed": False, "segment": 0, "edge": "start"}]
        self.assertEqual(clip_flaws(x), ["shot_flash"])
        rejected = []
        shown = rank_clips(kept, rejected, self.cfg)
        self.assertEqual(shown[0]["tier"], "worth_a_look")
        x["clip"]["flashes"] = []
        x["clip"]["unreviewed_start_words"] = [0, 1]
        self.assertEqual(rank_clips(kept, [], self.cfg)[0]["tier"], "worth_a_look")
        x["clip"]["unreviewed_start_words"] = []
        x["warnings_review"] = "rebuild after review failed (x); kept the reviewed cut"
        self.assertEqual(rank_clips(kept, [], self.cfg)[0]["tier"], "worth_a_look")

    def test_docs_keep_scores_flaws_and_payoff_fields(self):
        shown, rejected, kept = self.go([("w01", (4.0, 4.0)), ("w02", (3.0, 4.0))])
        kept[1]["moment"]["payoff_type"] = "punchline"
        kept[1]["moment"]["payoff_line"] = "payoff line end ok"
        d = proposal_doc(self.ep, shown[0], 1, self.cfg, {})
        self.assertEqual(d["tier"], "recommended")
        self.assertEqual(d["flaws"], [])
        rj = rejected_doc(self.ep, rejected[0], self.cfg)
        self.assertEqual(rj["tier"], "rejected")
        self.assertEqual(rj["scores"]["overall"], 3.0)
        self.assertEqual(rj["discovery"]["payoff_type"], "punchline")
        md = clips_md(self.ep, {"video_id": "v", "run_id": "r", "cost": {"usd": 0.0}, "proposals": [d],
                                "rejected": [rj], "episode_verdict": episode_verdict([d], TH)})
        self.assertIn("Episode verdict: THIN (1 recommended, 0 worth a look, 1 rejected)", md)
        self.assertIn("w02: Q1", md)


class Verdict(unittest.TestCase):
    def props(self, n_rec, n_wal):
        return [{"tier": "recommended"}] * n_rec + [{"tier": "worth_a_look"}] * n_wal

    def test_none_thin_strong(self):
        v = episode_verdict(self.props(0, 0), TH)
        self.assertEqual((v["verdict"], v["strong_clips"], v["worth_a_look"]), ("none", 0, 0))
        self.assertIn("No clip", v["note"])
        v = episode_verdict(self.props(0, 2), TH)
        self.assertEqual(v["verdict"], "none")
        self.assertIn("2 worth a look", v["note"])
        self.assertEqual(episode_verdict(self.props(1, 0), TH)["verdict"], "thin")
        self.assertEqual(episode_verdict(self.props(2, 5), TH)["verdict"], "thin")
        v = episode_verdict(self.props(3, 1), TH)
        self.assertEqual(v["verdict"], "strong")
        self.assertIn("3 strong clips", v["note"])
        self.assertIn("1 worth a look", v["note"])

    def test_strong_min_is_config(self):
        self.assertEqual(episode_verdict(self.props(2, 0), {**TH, "verdict_strong_min": 2})["verdict"], "strong")
        self.assertEqual(episode_verdict(self.props(3, 0), {**TH, "verdict_strong_min": 5})["verdict"], "thin")

    def test_note_is_one_sentence(self):
        for n in ((0, 0), (0, 3), (1, 0), (2, 2), (4, 1)):
            note = episode_verdict(self.props(*n), TH)["note"]
            self.assertTrue(note.endswith("."), note)
            self.assertEqual(note.count(". "), 0, note)


if __name__ == "__main__":
    unittest.main()
