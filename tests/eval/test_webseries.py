"""Offline tests for eval.webseries (web-series proposals vs founder-approved GT). No LLM, no network.

Run: python -m unittest tests.eval.test_webseries
"""
import copy
import unittest
import warnings
from pathlib import Path

from eval import webseries as W
from eval.common import ROOT, load_json

GT_IDS = ("j14Om1SaFYE", "QWIvgoTQGKk")
HM = "epZ0lbXkgWA"   # Hello Madam Ep6: held-out series; founder round-2 verdicts (6 yes / 4 maybe)
GH2 = ("gQZBXu5FAbs", "H41fJJJYTqo", "xmx9n4oLs-M")   # Girls Hostel 2.0 Ep1-3: 3 yes / 7 maybe / 6 no / 14 held
# Known rule-check failures on founder-yes gold clips that are detector issues, not clip faults (pinned so a change in
# either direction is noticed). gQZBXu5FAbs w03 (founder 5/5): episode_zones marks 114.6-150.1 s as a hard recap zone,
# but the GT coverage note puts the Ep1 replay at 1:55-2:06 (+ song to 2:14); the zone over-runs ~16 s into the story
# and the clip's opening lines fall inside it. Owner: transcript-signal-engineer (shotvi/segment/episode_zones.py).
KNOWN_GT_FAILS = {("gQZBXu5FAbs", "w03", "recap_zone")}


# ---- synthetic episode ----------------------------------------------------------------------------------

def synth_ctx(cuts="default", duration=400.0):
    """Words 62-330 s: 8-word sentences (0.4 s words, 0.1 s gaps, 1.5 s pause between sentences), a music bridge
    150-156 s with no speech, laughter 200-202 s. Shot cuts every 3 s (2, 5, 8, ...) up to the last word, then an
    evenly timed credits roll (every 2.5 s) from 4 s after the last word."""
    words, sents, t = [], [], 62.0
    while t < 330:
        a = len(words)
        for _ in range(8):
            if 150 <= t < 156:
                t = 156.0
            words.append({"word": f"w{len(words)}", "start": round(t, 3), "end": round(t + 0.4, 3)})
            t += 0.5
        sents.append({"words": [a, len(words) - 1]})
        t += 1.5
    events = [{"text": "[మ్యూజిక్]", "start": 150.0, "end": 156.0}, {"text": "[నవ్వు]", "start": 200.0, "end": 202.0}]
    if cuts == "default":
        last = words[-1]["end"]
        cuts = [float(c) for c in range(2, int(last), 3)] + [round(last + 4 + 2.5 * k, 3) for k in range(25)]
    return W.Ctx("synth", words, duration, events, sents, cuts)


def clip(segs, **kw):
    return {"id": kw.pop("id", "c"), "time_segments": [{"start": a, "end": b} for a, b in segs], **kw}


def sentence_span(ctx, k):
    """(first word start, last word end) of sentence k."""
    a, b = ctx.sentences[k]["words"]
    return ctx.words[a]["start"], ctx.words[b]["end"]


def body(ctx, k0, k1, pad=0.2, hold=1.1):
    """A clean body segment from sentence k0 to sentence k1: starts `pad` before the first word, holds `hold` s."""
    return (round(sentence_span(ctx, k0)[0] - pad, 3), round(sentence_span(ctx, k1)[1] + hold, 3))


def no_cuts_ctx():
    return synth_ctx(cuts=[])


class TestRuleChecks(unittest.TestCase):
    def setUp(self):
        self.ctx = no_cuts_ctx()  # no shot changes: flash/lift-on-cut only where a test adds cuts

    def st(self, c, name, ctx=None):
        return W.check_clip(c, ctx or self.ctx)[name]

    def test_clean_clip_passes(self):
        c = clip([body(self.ctx, 30, 37)])
        R = W.check_clip(c, self.ctx)
        self.assertEqual(W.summarize_checks(R)["fails"], [], R)
        self.assertEqual(R["mid_line"]["status"], "pass")
        self.assertEqual(R["internal_cuts"]["value"], 0)

    def test_lead_in(self):
        s, _ = sentence_span(self.ctx, 30)
        e = body(self.ctx, 30, 37)[1]
        self.assertEqual(self.st(clip([(s - 0.25, e)]), "lead_in")["status"], "pass")
        self.assertEqual(self.st(clip([(s - 0.35, e)]), "lead_in")["status"], "warn")
        r = self.st(clip([(s - 0.8, e)]), "lead_in")
        self.assertEqual(r["status"], "fail")
        self.assertIn("silence", r["detail"])

    def test_music_lead_in(self):
        i = next(j for j, w in enumerate(self.ctx.words) if w["start"] >= 156.0)
        r = self.st(clip([(151.0, self.ctx.words[i + 20]["end"] + 1.1)]), "lead_in")
        self.assertEqual(r["status"], "fail")
        self.assertIn("music", r["detail"])

    def test_wordless_opener(self):
        r = self.st(clip([(151.0, 153.0), body(self.ctx, 30, 37)], cold_open=True), "lead_in")
        self.assertEqual(r["status"], "fail")
        self.assertIn("music", r["detail"])

    def test_replay(self):
        b = body(self.ctx, 30, 37)
        a0, a1 = sentence_span(self.ctx, 34)
        replayed = clip([(a0 - 0.1, a1 + 0.1), b], cold_open=True)
        self.assertEqual(self.st(replayed, "replay")["status"], "fail")
        skipped = clip([(a0 - 0.1, a1 + 0.1), (b[0], a0 - 0.1), (a1 + 0.1, b[1])], cold_open=True)
        self.assertEqual(self.st(skipped, "replay")["status"], "pass")
        R = W.check_clip(skipped, self.ctx)
        self.assertEqual(R["internal_cuts"]["value"], 0, "skipping the lifted line in place is not a chunk cut")

    def test_cold_open_lift(self):
        b = body(self.ctx, 30, 37)
        a0, _ = sentence_span(self.ctx, 34)
        lift = (round(a0 - 0.1, 3), round(a0 + 1.9, 3))  # 2.0 s
        ctx = synth_ctx(cuts=[lift[1] + 0.02])
        c = clip([lift, (b[0], lift[0]), (lift[1], b[1])], cold_open=True)
        self.assertEqual(self.st(c, "cold_open_lift", ctx)["status"], "pass")
        off = synth_ctx(cuts=[lift[1] + 0.3])
        r = self.st(c, "cold_open_lift", off)
        self.assertEqual(r["status"], "fail")
        self.assertIn("shot change", r["detail"])
        long_lift = (lift[0], lift[0] + 4.5)
        c2 = clip([long_lift, (b[0], long_lift[0]), (long_lift[1], b[1])], cold_open=True)
        r = self.st(c2, "cold_open_lift", synth_ctx(cuts=[long_lift[1]]))
        self.assertEqual(r["status"], "fail")
        self.assertIn("outside", r["detail"])

    def test_undeclared_reorder_is_detected(self):
        b = body(self.ctx, 30, 37)
        a0, _ = sentence_span(self.ctx, 34)
        lift = (a0 - 0.1, a0 + 1.9)
        c = clip([lift, (b[0], lift[0]), (lift[1], b[1])], cold_open=False)
        r = self.st(c, "cold_open_lift", synth_ctx(cuts=[lift[1]]))
        self.assertEqual(r["status"], "warn")
        self.assertIn("cold_open is false", r["detail"])

    def test_internal_cuts(self):
        segs3 = [body(self.ctx, k, k, hold=0.3) for k in (30, 32, 34)] + [body(self.ctx, 36, 37)]
        self.assertEqual(self.st(clip(segs3), "internal_cuts")["value"], 3)
        self.assertEqual(self.st(clip(segs3), "internal_cuts")["status"], "pass")
        segs4 = [body(self.ctx, k, k, hold=0.3) for k in (28, 30, 32, 34)] + [body(self.ctx, 36, 37)]
        self.assertEqual(self.st(clip(segs4), "internal_cuts")["status"], "fail")
        contiguous = [(200.0, 220.0), (220.0, 240.0)]
        self.assertEqual(self.st(clip(contiguous), "internal_cuts")["value"], 0)

    def test_flash(self):
        b = body(self.ctx, 30, 37)
        end_flash = synth_ctx(cuts=[b[1] - 0.15, b[1] + 5.0])
        r = self.st(clip([b]), "flash", end_flash)
        self.assertEqual(r["status"], "fail")
        self.assertIn("ends 0.15s after", r["detail"])
        start_flash = synth_ctx(cuts=[b[0] - 5.0, b[0] + 0.12])
        self.assertEqual(self.st(clip([b]), "flash", start_flash)["status"], "fail")
        on_cut = synth_ctx(cuts=[b[0] - 5.0, b[1] + 0.01])
        self.assertEqual(self.st(clip([b]), "flash", on_cut)["status"], "pass")
        self.assertEqual(self.st(clip([b]), "flash", synth_ctx(cuts=None))["status"], "unchecked")

    def test_reaction_hold(self):
        s, e = sentence_span(self.ctx, 37)
        start = sentence_span(self.ctx, 30)[0] - 0.2
        self.assertEqual(self.st(clip([(start, e + 1.1)]), "reaction_hold")["status"], "pass")
        self.assertEqual(self.st(clip([(start, e + 0.4)]), "reaction_hold")["status"], "fail")
        on = synth_ctx(cuts=[e + 0.4])
        r = self.st(clip([(start, e + 0.4)]), "reaction_hold", on)
        self.assertEqual(r["status"], "pass")
        self.assertIn("shot change", r["detail"])

    def test_length(self):
        self.assertEqual(self.st(clip([body(self.ctx, 30, 31)]), "length")["status"], "fail")      # ~11 s
        long_ = clip([body(self.ctx, 20, 42)])                                                     # ~125 s
        self.assertEqual(self.st(long_, "length")["status"], "warn")
        self.assertEqual(self.st(clip([body(self.ctx, 0, 30)]), "length")["status"], "fail")       # ~170 s
        bad = clip([body(self.ctx, 30, 37)], video_duration=99.0)
        self.assertIn("declared", self.st(bad, "length")["detail"])

    def test_zones(self):
        # words start at 62 s: the speechless lead is the opening zone, story from the first word (no fixed 60 s)
        r = self.st(clip([(55.0, 80.0)]), "recap_zone")
        self.assertEqual(r["status"], "fail")
        self.assertIn("lead", r["detail"])
        self.assertEqual(self.st(clip([(61.8, 80.0)]), "recap_zone")["status"], "pass")
        self.assertEqual(self.ctx.opening["story_start"], 62.0)
        soft = W.Ctx("s", self.ctx.words, self.ctx.duration, self.ctx.events, self.ctx.sentences, [])
        soft.opening = dict(soft.opening, soft=[(61.5, 70.0)],
                            parts=soft.opening["parts"] + [{"kind": "isolated", "level": "soft", "start": 61.5, "end": 70.0}])
        self.assertEqual(self.st(clip([(61.8, 80.0)]), "recap_zone", soft)["status"], "warn")
        ctx = synth_ctx()
        self.assertIsNotNone(ctx.credits_zone)
        self.assertIn("evenly timed", ctx.credits_status)
        last = ctx.words[-1]["end"]
        self.assertAlmostEqual(ctx.credits_zone[0], last + 4, places=2)
        tail = clip([(ctx.words[-60]["start"] - 0.2, last + 20)])
        self.assertEqual(self.st(tail, "credits_zone", ctx)["status"], "fail")
        ok = clip([(ctx.words[-60]["start"] - 0.2, last + 3.5)])
        self.assertEqual(self.st(ok, "credits_zone", ctx)["status"], "pass")
        short_tail = synth_ctx(cuts=None, duration=ctx.words[-1]["end"] + 5)
        self.assertEqual(self.st(ok, "credits_zone", short_tail)["status"], "unchecked")

    def test_sponsor_overlap_warns(self):
        ctx = no_cuts_ctx()
        self.assertEqual(ctx.sponsors, [])
        ctx.sponsors = [{"start": 100.0, "end": 120.0, "evidence": {"cues": []}}]
        far = clip([body(ctx, 30, 37)])   # ~227-270 s
        self.assertEqual(self.st(far, "sponsor", ctx)["status"], "pass")
        near = clip([(95.0, 101.0)])
        self.assertEqual(self.st(near, "sponsor", ctx)["status"], "warn")
        self.assertIn("100.0-120.0", self.st(near, "sponsor", ctx)["detail"])

    def test_mid_word_and_mid_line(self):
        a, b = sentence_span(self.ctx, 30)
        w = self.ctx.words[self.ctx.sentences[30]["words"][0] + 3]
        r = self.st(clip([(a - 0.2, (w["start"] + w["end"]) / 2), body(self.ctx, 32, 37)]), "mid_word")
        self.assertEqual(r["status"], "fail")
        r = self.st(clip([(a - 0.2, w["end"] + 0.05), body(self.ctx, 32, 37)]), "mid_line")
        self.assertEqual(r["status"], "warn")


class TestMatching(unittest.TestCase):
    def setUp(self):
        self.ctx = no_cuts_ctx()
        self.g1 = clip([body(self.ctx, 30, 37)], id="g1", clip_purpose="standalone", content_type="web_series",
                       founder_would_post="yes")
        self.g2 = clip([body(self.ctx, 40, 46)], id="g2", clip_purpose="promo", content_type="web_series",
                       founder_would_post="yes")
        self.gold = {"video_id": "synth", "clips": [self.g1, self.g2]}

    def ev(self, props):
        return W.evaluate_episode(self.gold, {"proposals": [dict(p, rank=i + 1) for i, p in enumerate(props)]}, self.ctx)

    def test_identity(self):
        r = self.ev([copy.deepcopy(self.g1), copy.deepcopy(self.g2)])
        s = r["summary"]
        self.assertEqual(s["recall"]["any"], 2)
        self.assertEqual(s["mean_abs_start_delta"], 0.0)
        self.assertEqual(s["mean_abs_end_delta"], 0.0)
        self.assertEqual(s["payoff_and_hold_kept"], 2)
        self.assertEqual(s["mean_word_f1"], 1.0)

    def test_shifted_start_drops_setup(self):
        p = clip([body(self.ctx, 32, 37)], clip_purpose="standalone")
        r = self.ev([p])
        g = r["gt"][0]
        self.assertTrue(g["matched"])
        self.assertGreater(g["pair"]["start_delta"], 5)
        self.assertTrue(g["pair"]["drops_setup"])
        self.assertEqual(g["pair"]["setup_words_lost"], 16)
        self.assertTrue(g["pair"]["payoff_and_hold"])

    def test_cut_payoff(self):
        p = clip([body(self.ctx, 30, 36)], clip_purpose="promo")
        x = self.ev([p])["gt"][0]["pair"]
        self.assertFalse(x["payoff_kept"])
        self.assertFalse(x["hold_kept"])
        self.assertFalse(x["same_purpose"])

    def test_new_partial_duplicate(self):
        far = clip([body(self.ctx, 5, 12)])
        part = clip([body(self.ctx, 36, 39)])
        r = self.ev([copy.deepcopy(self.g1), copy.deepcopy(self.g1), far, part])
        st = [p["status"] for p in r["proposals"]]
        self.assertEqual(st, ["matched", "duplicate", "new", "partial"])
        self.assertEqual(r["summary"]["missed"], ["g2"])

    def test_one_proposal_matches_one_gt(self):
        big = clip([body(self.ctx, 30, 46)])  # spans both GT moments
        r = self.ev([big])
        self.assertEqual(r["summary"]["recall"]["any"], 1)

    def test_recall_at_k(self):
        fillers = [clip([body(self.ctx, k, k + 3)]) for k in (0, 5, 10, 15, 20)]
        r = self.ev(fillers + [copy.deepcopy(self.g1)])
        rec = r["summary"]["recall"]
        self.assertEqual(rec["any"], 1)
        self.assertEqual(rec["@k_gt"], 0)
        self.assertEqual(rec["@5"], 0)
        self.assertEqual(rec["@10"], 1)


class TestFounderVerdict(unittest.TestCase):
    """GT = founder_would_post 'yes'; 'maybe' hits are acceptable, 'no' hits are founder_no; --gt all = old behaviour."""

    def setUp(self):
        self.ctx = no_cuts_ctx()
        self.yes = clip([body(self.ctx, 30, 37)], id="y1", clip_purpose="standalone", content_type="web_series",
                        founder_would_post="yes", founder_scores={"hook": 4, "standalone": 4, "payoff": 5, "overall": 4})
        self.maybe = clip([body(self.ctx, 40, 46)], id="m1", clip_purpose="promo", content_type="web_series",
                          founder_would_post="maybe", founder_scores={"hook": 3, "standalone": 3, "payoff": 3, "overall": 3})
        self.no = clip([body(self.ctx, 10, 16)], id="n1", clip_purpose="standalone", content_type="web_series",
                       founder_would_post="no")
        self.gold = {"video_id": "synth", "clips": [self.yes, self.maybe, self.no]}

    def ev(self, props, gold=None, **kw):
        with warnings.catch_warnings():
            warnings.simplefilter("error")   # a complete gold file must not warn
            return W.evaluate_episode(gold or self.gold, {"proposals": [dict(p, rank=i + 1) for i, p in enumerate(props)]},
                                      self.ctx, **kw)

    def test_split(self):
        gts, others, notes = W.split_gt(self.gold)
        self.assertEqual([c["id"] for c in gts], ["y1"])
        self.assertEqual([c["id"] for c in others], ["m1", "n1"])
        self.assertEqual(notes, [])
        with self.assertRaises(ValueError):
            W.verdict(dict(self.yes, founder_would_post="sure"))
        self.assertEqual(W.verdict(dict(self.yes, founder_would_post=" Yes ")), "yes")

    def test_maybe_is_acceptable_no_is_founder_no(self):
        r = self.ev([copy.deepcopy(self.maybe), copy.deepcopy(self.no), copy.deepcopy(self.yes)])
        s = r["summary"]
        self.assertEqual((s["n_gt"], s["n_maybe"], s["n_no"], s["n_gt_all"]), (1, 1, 1, 3))
        self.assertEqual([p["status"] for p in r["proposals"]], ["acceptable", "founder_no", "matched"])
        self.assertEqual([p.get("gt") for p in r["proposals"]], ["m1", "n1", "y1"])
        self.assertEqual(s["acceptable_hits"], 1)
        self.assertEqual(s["founder_no_hits"], 1)
        self.assertEqual(s["status_counts"]["new"], 0)
        self.assertEqual(s["missed"], [])          # the maybe/no clips are not misses
        self.assertEqual(s["recall"]["any"], 1)    # recall is over the yes clip only
        self.assertEqual(s["recall"]["@k_gt"], 0)  # ... and the yes clip sits at rank 3 > k_gt = 1
        self.assertEqual(s["recall_frac"]["any"], 1.0)
        # founder scores travel with the match (informational)
        g = r["gt"][0]
        self.assertEqual(g["pair"]["founder_payoff"], 5)
        self.assertEqual(g["pair"]["founder_overall"], 4)
        self.assertEqual(s["mean_founder_payoff_matched"], 5.0)
        acc = r["proposals"][0]
        self.assertEqual(acc["founder_verdict"], "maybe")
        self.assertEqual(acc["founder_scores"], {"payoff": 3, "overall": 3})
        self.assertIsNone(r["proposals"][1]["founder_scores"])
        other = {o["id"]: o for o in r["other_gt"]}
        self.assertTrue(other["m1"]["hit"] and other["n1"]["hit"])
        self.assertEqual(other["m1"]["prop_rank"], 1)

    def test_maybe_miss_is_not_a_miss_and_second_hit_is_duplicate(self):
        r = self.ev([copy.deepcopy(self.yes), copy.deepcopy(self.maybe), copy.deepcopy(self.maybe)])
        self.assertEqual([p["status"] for p in r["proposals"]], ["matched", "acceptable", "duplicate"])
        self.assertEqual(r["proposals"][2]["gt"], "m1")
        r = self.ev([copy.deepcopy(self.yes)])
        s = r["summary"]
        self.assertEqual(s["missed"], [])
        self.assertEqual(s["acceptable_hits"], 0)
        self.assertFalse(any(o["hit"] for o in r["other_gt"]))

    def test_partial_on_a_maybe_clip(self):
        part = clip([body(self.ctx, 45, 46)])   # the last two lines of m1 only: cov ~0.3 < MATCH_COV, IoU >= 0.1
        r = self.ev([part])
        p = r["proposals"][0]
        self.assertEqual(p["status"], "partial")
        self.assertEqual(p["gt"], "m1")
        self.assertEqual(p["founder_verdict"], "maybe")

    def test_missing_verdict_is_yes_with_warning(self):
        old = {"video_id": "old", "clips": [{k: v for k, v in self.yes.items() if k != "founder_would_post"},
                                              copy.deepcopy(self.maybe)]}
        with self.assertWarns(UserWarning) as cm:
            gts, others, notes = W.split_gt(old)
        self.assertEqual([c["id"] for c in gts], ["y1"])
        self.assertEqual([c["id"] for c in others], ["m1"])
        self.assertEqual(len(notes), 1)
        self.assertIn("y1", notes[0])
        self.assertIn("old", str(cm.warning))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            r = W.evaluate_episode(old, {"proposals": [dict(copy.deepcopy(self.yes), rank=1)]}, self.ctx)
        self.assertEqual(r["summary"]["n_gt"], 1)
        self.assertEqual(r["summary"]["recall"]["any"], 1)
        self.assertEqual(r["summary"]["gt_notes"], notes)
        self.assertIn("treating them as 'yes'", W.render_markdown([r], "x"))

    def test_gt_all_is_the_old_behaviour(self):
        r = self.ev([copy.deepcopy(self.maybe), copy.deepcopy(self.no), copy.deepcopy(self.yes)], gt="all")
        s = r["summary"]
        self.assertEqual(s["gt_mode"], "all")
        self.assertEqual((s["n_gt"], s["n_maybe"], s["n_no"]), (3, 0, 0))
        self.assertEqual([p["status"] for p in r["proposals"]], ["matched"] * 3)
        self.assertEqual(s["recall"]["any"], 3)
        self.assertEqual(r["other_gt"], [])
        # a missing verdict field does not warn in --gt all mode (the verdict is not used)
        old = {"video_id": "old", "clips": [{k: v for k, v in self.yes.items() if k != "founder_would_post"}]}
        self.assertEqual(W.split_gt(old, "all")[2], [])
        with self.assertRaises(ValueError):
            W.split_gt(self.gold, "some")

    def test_markdown_columns(self):
        r = self.ev([copy.deepcopy(self.maybe), copy.deepcopy(self.no), copy.deepcopy(self.yes)])
        md = W.render_markdown([r], "runs/x")
        self.assertIn("## Headline: precision at the top", md)
        self.assertLess(md.index("## Headline"), md.index("## Secondary"))
        # untiered proposals: the whole list is the top tier; 1 hit, 1 acceptable, 1 founder_no -> 1/3, over-proposing
        self.assertIn("| synth | 1 / 1 / 1 / 0 | 0 / 0 / 0 (+3 untiered) | untiered (3) | 1 / 1 / 1 / 0 / 0 | 1/3 (33%) | 1 / 1 |", md)
        self.assertIn("| - | 1/1 (100%) | over-proposing |", md)
        self.assertIn("| GT yes | maybe / no / unrated |", md)
        self.assertIn("| acceptable | founder_no | unrated hits |", md)
        self.assertIn("| synth | 1 | 1 / 1 / 0 | 3 | 1/1 (100%) |", md)
        self.assertIn("| 1/1 (100%) | 1/1 (100%) | 0/0 |", md)   # acceptable / founder_no / unrated hits
        self.assertIn("| y1 | standalone | 5/4 |", md)             # founder payoff/overall on the GT row
        self.assertIn("acceptable (m1) founder 3/3", md)


class TestUnratedAndPrecision(unittest.TestCase):
    """held / pending gold clips are unrated: a hit on one is `unrated` (not a hit, not a miss); the headline is
    precision at the top by tier and rank, founder_no shown, missed yes and count honesty."""

    def setUp(self):
        self.ctx = no_cuts_ctx()

        def mk(k0, k1, cid, v, **kw):
            return clip([body(self.ctx, k0, k1)], id=cid, clip_purpose="standalone", content_type="web_series",
                        founder_would_post=v, **kw)
        self.y1 = mk(30, 37, "y1", "yes", founder_scores={"hook": 4, "standalone": 4, "payoff": 5, "overall": 4})
        self.y2 = mk(38, 42, "y2", "yes")
        self.m1 = mk(10, 16, "m1", "maybe")
        self.n1 = mk(20, 26, "n1", "no")
        self.h1 = mk(0, 6, "h1", "held")
        self.p1 = mk(43, 46, "p1", "pending")   # the synthetic episode has ~48 sentences
        self.gold = {"video_id": "synth", "clips": [self.y1, self.y2, self.m1, self.n1, self.h1, self.p1]}

    def ev(self, props, **kw):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            return W.evaluate_episode(self.gold, {"proposals": [dict(p, rank=i + 1) for i, p in enumerate(props)]},
                                      self.ctx, **kw)

    def prop(self, g, tier):
        return dict(copy.deepcopy(g), tier=tier)

    def test_verdicts_and_split(self):
        gts, others, notes = W.split_gt(self.gold)
        self.assertEqual([c["id"] for c in gts], ["y1", "y2"])
        self.assertEqual([c["id"] for c in others], ["m1", "n1", "h1", "p1"])
        self.assertEqual(notes, [])
        self.assertEqual(W.verdict(self.h1), "held")
        self.assertFalse(W.is_rated(self.h1))
        self.assertFalse(W.is_rated(self.p1))
        self.assertTrue(W.is_rated(self.n1))
        with self.assertRaises(ValueError):
            W.verdict(dict(self.h1, founder_would_post="hidden"))

    def test_unrated_hit_is_neither_hit_nor_miss(self):
        r = self.ev([self.prop(self.h1, "recommended"), self.prop(self.p1, "recommended"), self.prop(self.y1, "recommended")])
        s = r["summary"]
        self.assertEqual([p["status"] for p in r["proposals"]], ["unrated", "unrated", "matched"])
        self.assertEqual([p.get("founder_verdict") for p in r["proposals"]], ["held", "pending", None])
        self.assertIn("judge gate", r["proposals"][0]["unrated_detail"])
        self.assertEqual((s["n_gt"], s["n_maybe"], s["n_no"], s["n_unrated"]), (2, 1, 1, 2))
        self.assertEqual(s["unrated_verdicts"], {"held": 1, "pending": 1})
        self.assertEqual(s["unrated_hits"], 2)
        self.assertEqual(s["missed"], ["y2"])                   # only the yes clip; h1/p1 hits are not misses either
        self.assertEqual(s["headline"]["missed_yes"], ["y2"])
        t = s["precision"]["top"]
        self.assertEqual((t["shown"], t["hit"], t["unrated"], t["rated"], t["precision"]), (3, 1, 2, 1, 1.0))
        other = {o["id"]: o for o in r["other_gt"]}
        self.assertTrue(other["h1"]["hit"] and other["p1"]["hit"])
        md = W.render_markdown([r], "runs/x")
        self.assertIn("unrated (h1 held)", md)
        self.assertIn("Hits on unrated gold clips", md)
        self.assertIn("2 gold clips are unrated (held/pending)", md)

    def test_precision_by_tier_and_rank(self):
        props = [self.prop(self.y1, "recommended"), self.prop(self.n1, "recommended"), self.prop(self.m1, "worth_a_look"),
                 self.prop(self.y2, "worth_a_look"), self.prop(self.y1, "worth_a_look"),
                 clip([body(self.ctx, 17, 19)], tier="below_threshold")]   # outside every gold clip
        r = self.ev(props)
        p = r["summary"]["precision"]
        self.assertEqual([x["status"] for x in r["proposals"]],
                         ["matched", "founder_no", "acceptable", "matched", "duplicate", "new"])
        self.assertEqual(p["top_tier"], "recommended")
        rec = p["by_tier"]["recommended"]
        self.assertEqual((rec["shown"], rec["hit"], rec["founder_no"], rec["rated"], rec["precision"]), (2, 1, 1, 2, 0.5))
        wal = p["by_tier"]["worth_a_look"]
        self.assertEqual((wal["shown"], wal["hit"], wal["acceptable"], wal["duplicate"], wal["rated"]), (3, 1, 1, 1, 2))
        bel = p["by_tier"]["below_threshold"]
        self.assertEqual((bel["shown"], bel["unrated"], bel["rated"], bel["precision"]), (1, 1, 0, None))
        self.assertEqual(p["by_rank"]["@3"]["hit"], 1)
        self.assertEqual(p["by_rank"]["@3"]["rated"], 3)
        self.assertEqual(p["by_rank"]["@5"]["hit"], 2)
        self.assertEqual(p["by_rank"]["all"]["shown"], 6)
        self.assertEqual(p["by_rank"]["all"]["precision"], 0.5)   # 2 hits of 4 rated
        self.assertEqual(p["founder_no_shown"], 1)
        h = r["summary"]["headline"]
        self.assertEqual((h["n_yes"], h["shown_top"], h["hits_top"], h["founder_no_top"], h["founder_no_shown"]), (2, 2, 1, 1, 1))
        self.assertEqual(h["count_flag"], "ok")
        self.assertEqual(h["missed_yes"], [])
        self.assertEqual(h["recall_yes"], 1.0)
        md = W.render_markdown([r], "runs/x")
        self.assertIn("| synth | 2 / 1 / 1 / 2 | 2 / 3 / 1 | recommended (2) | 1 / 0 / 1 / 0 / 0 | 1/2 (50%) | 1 / 1 |", md)
        self.assertIn("| recommended | matched (y1) |", md)

    def test_count_honesty(self):
        self.assertEqual(W.count_flag(2, 2), "ok")
        self.assertEqual(W.count_flag(2, 4), "ok")
        self.assertEqual(W.count_flag(2, 5), "over-proposing")
        self.assertEqual(W.count_flag(2, 1), "under-proposing")
        self.assertEqual(W.count_flag(0, 0), "ok")               # an episode with nothing to post, and nothing shown
        self.assertEqual(W.count_flag(0, 1), "over-proposing")
        # no recommended clips at all: the top tier is empty -> under-proposing, with the shown ones in other tiers
        r = self.ev([self.prop(self.y1, "worth_a_look"), self.prop(self.y2, "worth_a_look")])
        h = r["summary"]["headline"]
        self.assertEqual((h["shown_top"], h["count_flag"], h["missed_yes"]), (0, "under-proposing", []))
        self.assertEqual(r["summary"]["precision"]["top"]["precision"], None)
        md = W.render_markdown([r], "runs/x")
        self.assertIn("recommended (0)", md)
        self.assertIn("| none shown |", md)
        # an unknown tier string is reported as 'other', never dropped
        r = self.ev([self.prop(self.y1, "shortlist")])
        self.assertEqual(r["summary"]["precision"]["by_tier"]["other"]["shown"], 1)

    def test_gt_all_treats_unrated_as_gt(self):
        r = self.ev([self.prop(self.h1, "recommended"), self.prop(self.p1, "recommended")], gt="all")
        s = r["summary"]
        self.assertEqual((s["n_gt"], s["n_unrated"]), (6, 0))
        self.assertEqual([p["status"] for p in r["proposals"]], ["matched", "matched"])
        self.assertEqual(s["precision"]["top"]["hit"], 2)
        self.assertEqual(len(s["headline"]["missed_yes"]), 4)


class TestFounderRatingsTool(unittest.TestCase):
    """eval.founder_ratings writes a ratings CSV into gold files the way the Hello Madam file carries them."""

    def test_apply(self):
        import json
        import tempfile
        from eval import founder_ratings as FR
        gold = {"video_id": "v1", "clips": [{"id": "w01", "founder_would_post": "pending"},
                                            {"id": "w02", "founder_would_post": "held"},
                                            {"id": "w03", "founder_would_post": "pending"}]}
        head = ("\ufeffvideo_id,rank,clip_id,title,file,pipeline_overall,hook_1to5,standalone_1to5,payoff_1to5,"
                "overall_1to5,would_post,notes\n")
        with tempfile.TemporaryDirectory() as td:
            gd = Path(td) / "gold"
            gd.mkdir()
            (gd / "v1.json").write_text(json.dumps(gold), encoding="utf-8")
            csvp = Path(td) / "r.csv"
            csvp.write_text(head + "v1,1,w01,t,f,4.4,5,4,5,5,yes,\nv1,2,w03,t,f,3.3,3,3,2,2,no,weak ending\n", encoding="utf-8")
            FR.run(csvp, gd, dry_run=True)
            self.assertEqual(load_json(gd / "v1.json")["clips"][0]["founder_would_post"], "pending")   # dry run
            rep = FR.run(csvp, gd)
            self.assertEqual(rep["v1"]["rated"], {"yes": 1, "no": 1})
            self.assertEqual(rep["v1"]["file"], {"yes": 1, "held": 1, "no": 1})
            out = load_json(gd / "v1.json")
            c = {x["id"]: x for x in out["clips"]}
            self.assertEqual(c["w01"]["founder_scores"], {"hook": 5, "standalone": 4, "payoff": 5, "overall": 5})
            self.assertEqual(c["w02"]["founder_would_post"], "held")        # untouched
            self.assertNotIn("founder_scores", c["w02"])
            self.assertEqual(c["w03"]["founder_notes"], "weak ending")
            self.assertEqual(out["founder_review"], str(csvp))
            # a rating for a clip that is not in the gold file is an error, and nothing is written
            csvp.write_text(head + "v1,1,w99,t,f,4,4,4,4,4,yes,\n", encoding="utf-8")
            with self.assertRaises(KeyError):
                FR.run(csvp, gd)
            csvp.write_text(head + "v1,1,w01,t,f,4,4,4,4,4,held,\n", encoding="utf-8")
            with self.assertRaises(ValueError):     # the founder's CSV can only say yes / maybe / no
                FR.run(csvp, gd)


# ---- real GT (skipped when the local data is absent) -----------------------------------------------------

def _have(vid):
    return (ROOT / "data" / "gold" / f"{vid}.json").exists() and \
        (ROOT / "scribe_dataset" / vid / f"{vid}_scribe_transcript.json").exists() and \
        (ROOT / "runs" / "video" / vid / "shots.json").exists()


def perturb(gold, ctx):
    """Proposals from the GT with three injected faults: clip 0 starts at its second line (shifted start),
    the first non-cold clip gets a replayed cold open (lifted line also played in place), and another gets a
    3 s lead-in. Returns (proposal doc, {fault: gt clip id})."""
    clips = [copy.deepcopy(c) for c in W.gt_clips(gold)]          # proposals: every gold clip, maybe/no included
    yes = [c for c in clips if W.verdict(c) == "yes"]              # fault 1 needs a founder-yes clip (pair metrics)
    pool = yes + [c for c in clips if W.verdict(c) != "yes"]       # faults 2-3 are rule checks: any gold clip (GH2 has one yes each)
    faults = {}
    # 1) shifted start: drop everything before the second spoken line of the first body segment
    def lines(x):
        # first word of a later line that starts >= 2 s in (a one-word first line, e.g. a name call, is too short a shift)
        ws = ctx.words_in(x["time_segments"][0]["start"], x["time_segments"][0]["end"])
        t0 = ctx.words[ws[0]]["start"]
        return ws, [j for j in ws if ctx.sent_of[j] != ctx.sent_of[ws[0]] and ctx.words[j]["start"] >= t0 + 2.0]
    c = next(c for c in yes if not c.get("cold_open") and lines(c)[1])
    k = lines(c)[1][0]
    c["time_segments"][0]["start"] = round(ctx.words[k]["start"] - 0.2, 3)
    faults["shifted_start"] = c["id"]
    # 2) replayed cold open: lift a 2 s stretch from the middle of the longest segment, keep it in place too
    c2 = next(x for x in pool if not x.get("cold_open") and x["id"] != c["id"])
    seg = max(c2["time_segments"], key=lambda s: s["end"] - s["start"])
    mid = (seg["start"] + seg["end"]) / 2
    c2["time_segments"].insert(0, {"start": round(mid, 3), "end": round(mid + 2.0, 3)})
    c2["cold_open"] = True
    c2["video_duration"] = round(c2["video_duration"] + 2.0, 3)
    faults["replayed_cold_open"] = c2["id"]
    # 3) lead-in of up to 3 s of non-speech (music/silence) before the first word: the clip whose first word has
    #    the longest speechless gap before it
    def gap_before(x):
        j = ctx.words_in(x["time_segments"][0]["start"], x["time_segments"][0]["end"])[0]
        return ctx.words[j]["start"] - (ctx.words[j - 1]["end"] if j else 0.0), ctx.words[j]["start"]
    c3 = max((x for x in pool if not x.get("cold_open") and x["id"] not in (c["id"], c2["id"])),
             key=lambda x: gap_before(x)[0])
    gap, first = gap_before(c3)
    lead = min(3.0, gap - 0.1)
    assert lead > W.LEAD_IN_FAIL, "no clip with a long enough speechless gap to inject a lead-in"
    old = c3["time_segments"][0]["start"]
    c3["time_segments"][0]["start"] = round(first - lead, 3)
    c3["video_duration"] = round(c3["video_duration"] + old - (first - lead), 3)
    faults["lead_in"] = c3["id"]
    for i, x in enumerate(clips):
        x["rank"] = i + 1
    return {"kind": "proposals", "proposals": clips}, faults


@unittest.skipUnless(all(_have(v) for v in GT_IDS), "web-series GT data not present")
class TestRealGT(unittest.TestCase):
    def ids(self):
        return GT_IDS + ((HM,) if _have(HM) else ()) + tuple(v for v in GH2 if _have(v))

    def test_gold_vs_gold(self):
        """Every founder-yes clip finds itself (100% recall over yes clips), every maybe/no clip is an acceptable /
        founder_no hit, nothing is new, and the shipped gold files carry a verdict on every clip (no warning)."""
        for vid in self.ids():
            gold = load_json(ROOT / "data" / "gold" / f"{vid}.json")
            ctx = W.load_ctx(vid)
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                r = W.evaluate_episode(gold, gold, ctx)
            s = r["summary"]
            n = s["n_gt"]
            self.assertGreater(n, 0)
            self.assertEqual(s["gt_notes"], [], vid)
            self.assertEqual(n, sum(W.verdict(c) == "yes" for c in W.gt_clips(gold)), vid)
            self.assertEqual(s["n_gt_all"], len(W.gt_clips(gold)), vid)
            self.assertEqual(s["recall"]["any"], n, vid)
            # recall@k=GT counts the yes clips found within the first n proposals; here proposals are the gold clips in
            # file order, so the expectation is the number of yes clips among the first n gold clips
            self.assertEqual(s["recall"]["@k_gt"], sum(W.verdict(c) == "yes" for c in W.gt_clips(gold)[:n]), vid)
            self.assertEqual(s["acceptable_hits"], s["n_maybe"], vid)
            self.assertEqual(s["founder_no_hits"], s["n_no"], vid)
            self.assertEqual(s["unrated_hits"], s["n_unrated"], vid)
            self.assertEqual(s["headline"]["missed_yes"], [], vid)
            self.assertEqual(s["precision"]["top_tier"], "untiered", vid)   # gold clips carry no tier
            self.assertEqual(s["precision"]["top"]["hit"], n, vid)
            self.assertEqual(s["precision"]["top"]["founder_no"], s["n_no"], vid)
            if vid in GH2:   # held clips are never rated; the founder's verdicts came from ratings_gt_gh2_r1.csv
                self.assertGreater(s["n_unrated"], 0, vid)
                self.assertEqual(set(s["unrated_verdicts"]), {"held"}, vid)
                self.assertEqual(s["founder_scored"], n, vid)
            self.assertEqual(s["status_counts"]["new"] + s["status_counts"]["partial"] + s["status_counts"]["duplicate"], 0, vid)
            self.assertEqual(s["mean_abs_start_delta"], 0.0)
            self.assertEqual(s["mean_abs_end_delta"], 0.0)
            self.assertEqual(s["payoff_and_hold_kept"], n)
            self.assertEqual(s["drops_setup"], 0)
            self.assertEqual(s["mean_length_ratio"], 1.0)
            self.assertEqual(s["same_purpose"], n)
            for g in r["gt"]:
                self.assertEqual(g["prop_id"], g["id"])
                self.assertEqual(g["verdict"], "yes")
            for o in r["other_gt"]:
                self.assertEqual(o["prop_id"], o["id"])
            if vid == HM or vid in GH2:   # founder scores ride along on the matched pairs
                self.assertEqual(s["founder_scored"], n)
                self.assertTrue(all(g["pair"]["founder_payoff"] is not None for g in r["gt"]))
            # --gt all: the pre-verdict behaviour, every clip is GT and finds itself
            r_all = W.evaluate_episode(gold, gold, ctx, gt="all")
            self.assertEqual(r_all["summary"]["recall"]["any"], len(W.gt_clips(gold)), vid)
            # structural checks every approved GT clip passes (informative failures elsewhere are reported, not asserted)
            for g in r["gt_checks"]:
                for name in ("segments", "replay", "cold_open_lift", "internal_cuts", "flash", "length",
                             "recap_zone", "credits_zone"):
                    if (vid, g["id"], name) in KNOWN_GT_FAILS:
                        self.assertEqual(g["checks"][name]["status"], "fail", f"{vid} {g['id']} {name}: known case cleared, drop it from KNOWN_GT_FAILS")
                        continue
                    self.assertNotEqual(g["checks"][name]["status"], "fail", f"{vid} {g['id']} {name}")
                if (vid, g["id"], "recap_zone") not in KNOWN_GT_FAILS:
                    self.assertEqual(g["checks"]["recap_zone"]["status"], "pass", f"{vid} {g['id']}")
                self.assertEqual(g["checks"]["sponsor"]["status"], "pass", f"{vid} {g['id']}")

    def test_opening_and_sponsor_zones(self):
        """Detected openings: Ep1 music then dialogue at 24-34 s, Ep2 a 134 s song montage, Hello Madam cards then
        the frame scene at 17.8 s; the Hello Madam ad (map.md: 57.27-113.32 s) is found, nothing elsewhere."""
        ctx = W.load_ctx("QWIvgoTQGKk")
        self.assertGreater(ctx.opening["head_end"], 130.0)
        self.assertEqual(ctx.sponsors, [])
        self.assertEqual(W.check_clip(clip([(120.0, 150.0)]), ctx)["recap_zone"]["status"], "fail")
        ctx = W.load_ctx("j14Om1SaFYE")
        self.assertLess(ctx.opening["head_end"], 34.0)
        self.assertEqual(ctx.sponsors, [])
        if _have(HM):
            ctx = W.load_ctx(HM)
            self.assertLess(ctx.opening["story_start"], 17.9)
            self.assertGreater(ctx.opening["head_end"], 10.0)    # the subscribe card is zoned
            self.assertEqual(len(ctx.sponsors), 1)
            self.assertAlmostEqual(ctx.sponsors[0]["start"], 57.27, delta=0.1)
            self.assertAlmostEqual(ctx.sponsors[0]["end"], 113.32, delta=0.1)
            ad = W.check_clip(clip([(98.0, 125.0)]), ctx)
            self.assertEqual(ad["sponsor"]["status"], "warn")
            self.assertEqual(ad["recap_zone"]["status"], "pass")

    def test_perturbations_are_caught(self):
        for vid in self.ids():
            gold = load_json(ROOT / "data" / "gold" / f"{vid}.json")
            ctx = W.load_ctx(vid)
            doc, faults = perturb(gold, ctx)
            r = W.evaluate_episode(gold, doc, ctx)
            self.assertEqual(r["summary"]["recall"]["any"], r["summary"]["n_gt"], vid)
            gt = {g["id"]: g for g in r["gt"]}
            props = {p["id"]: p for p in r["proposals"]}
            x = gt[faults["shifted_start"]]["pair"]
            self.assertGreater(x["start_delta"], 0)
            self.assertTrue(x["drops_setup"], vid)
            self.assertEqual(props[faults["replayed_cold_open"]]["checks"]["replay"]["status"], "fail")
            self.assertEqual(props[faults["lead_in"]]["checks"]["lead_in"]["status"], "fail")
            for f in ("replayed_cold_open", "lead_in"):  # a later start is a pair-metric fault, not a rule failure
                self.assertFalse(props[faults[f]]["pass"])


if __name__ == "__main__":
    unittest.main()
