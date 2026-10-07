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
        self.assertIn("| GT yes | maybe / no |", md)
        self.assertIn("| acceptable | founder_no |", md)
        self.assertIn("| synth | 1 | 1 / 1 | 3 | 1/1 (100%) |", md)
        self.assertIn("| 1/1 (100%) | 1/1 (100%) |", md)   # acceptable / founder_no
        self.assertIn("| y1 | standalone | 5/4 |", md)       # founder payoff/overall on the GT row
        self.assertIn("acceptable (m1) founder 3/3", md)


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
    yes = [c for c in clips if W.verdict(c) == "yes"]              # faults are injected into founder-yes clips only
    faults = {}
    # 1) shifted start: drop everything before the second spoken line of the first body segment
    def lines(x):
        ws = ctx.words_in(x["time_segments"][0]["start"], x["time_segments"][0]["end"])
        return ws, [j for j in ws if ctx.sent_of[j] != ctx.sent_of[ws[0]]]
    c = next(c for c in yes if not c.get("cold_open") and lines(c)[1])
    k = lines(c)[1][0]
    c["time_segments"][0]["start"] = round(ctx.words[k]["start"] - 0.2, 3)
    faults["shifted_start"] = c["id"]
    # 2) replayed cold open: lift a 2 s stretch from the middle of the longest segment, keep it in place too
    c2 = next(x for x in yes if not x.get("cold_open") and x["id"] != c["id"])
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
    c3 = max((x for x in yes if not x.get("cold_open") and x["id"] not in (c["id"], c2["id"])),
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
        return GT_IDS + ((HM,) if _have(HM) else ())

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
            if vid == HM:   # founder scores ride along on the matched pairs
                self.assertEqual(s["founder_scored"], n)
                self.assertTrue(all(g["pair"]["founder_payoff"] is not None for g in r["gt"]))
            # --gt all: the pre-verdict behaviour, every clip is GT and finds itself
            r_all = W.evaluate_episode(gold, gold, ctx, gt="all")
            self.assertEqual(r_all["summary"]["recall"]["any"], len(W.gt_clips(gold)), vid)
            # structural checks every approved GT clip passes (informative failures elsewhere are reported, not asserted)
            for g in r["gt_checks"]:
                for name in ("segments", "replay", "cold_open_lift", "internal_cuts", "flash", "length",
                             "recap_zone", "credits_zone"):
                    self.assertNotEqual(g["checks"][name]["status"], "fail", f"{vid} {g['id']} {name}")
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
