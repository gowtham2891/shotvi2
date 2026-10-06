"""Opening zone and sponsor detection (shotvi.segment.episode_zones) on synthetic transcripts. No LLM, no network.

Run: python -m unittest tests.segment.test_episode_zones
"""
import unittest

from shotvi.segment import episode_zones as Z

from .helpers import build

A, B, C = "speaker_0", "speaker_1", "speaker_2"


def scene(n, tag="s", gap=0.8, first_gap=0.0):
    """n alternating 4-word lines (unique words), `gap` s apart."""
    return [((A, B)[k % 2], f"{tag}{k}a {tag}{k}b {tag}{k}c {tag}{k}d", first_gap if k == 0 else gap) for k in range(n)]


def first_start(d, word):
    return next(w["start"] for w in d["word_timestamps"] if w["word"] == word)


def kinds(o, level=None):
    return [p["kind"] for p in o["parts"] if level is None or p["level"] == level]


class TestOpening(unittest.TestCase):
    def test_lead_and_card_are_hard_story_starts_at_dialogue(self):
        segs = [(A, "Subscribe చేసుకొని ఆ bell icon కొట్టండి", 8.0)] + scene(40, first_gap=8.0)
        d = build(segs, events=[("[మ్యూజిక్]", 0.0, 7.5)], duration=900)
        o = Z.episode_zones(d)["opening"]
        st = first_start(d, "s0a")
        self.assertAlmostEqual(o["story_start"], st, places=2)
        self.assertEqual(kinds(o, "hard"), ["lead", "cards"])
        self.assertEqual(o["soft"], [])
        self.assertAlmostEqual(o["hard"][-1][1], st - Z.PAD, places=2)
        self.assertAlmostEqual(o["head_end"], st - Z.PAD, places=2)

    def test_isolated_story_line_is_only_soft(self):
        segs = [(A, "మమ్మీ hair clip ఏదే", 20.0)] + scene(40, first_gap=8.0)
        d = build(segs, duration=900)
        o = Z.episode_zones(d)["opening"]
        self.assertEqual(kinds(o, "hard"), ["lead"])
        self.assertEqual(kinds(o, "soft"), ["isolated"])
        self.assertAlmostEqual(o["soft"][0][0], 20.0 - Z.PAD, places=2)
        self.assertAlmostEqual(o["soft"][0][1], first_start(d, "s0a") - Z.PAD, places=2)

    def test_no_fixed_window(self):
        """Dialogue from 1 s: nothing before it is zoned (the old rule zoned the first 60 s)."""
        d = build(scene(60, first_gap=1.0), duration=900)
        o = Z.episode_zones(d)["opening"]
        self.assertAlmostEqual(o["story_start"], 1.0, places=2)
        self.assertEqual(o["hard"], [(0.0, 0.5)])
        self.assertLess(o["head_end"], 1.0)

    def test_long_song_montage(self):
        d = build(scene(40, first_gap=130.0), events=[("[పాట]", 2.0, 129.5)], duration=900)
        o = Z.episode_zones(d)["opening"]
        self.assertEqual(o["hard"], [(0.0, 129.5)])
        self.assertIn("music", o["parts"][0]["detail"])

    def test_no_sustained_dialogue(self):
        segs = [(A, f"card{k}a card{k}b", 9.0) for k in range(10)]
        o = Z.episode_zones(build(segs, duration=900))["opening"]
        self.assertIsNone(o["story_start"])
        self.assertIn("no sustained dialogue", o["status"])

    def test_teaser_repeated_later_is_hard(self):
        ex = [" ".join(f"t{k}w{i}" for i in range(9)) for k in range(3)]
        opening = [((A, B, A)[k], ex[k], 0.0 if k == 0 else 0.6) for k in range(3)]
        body = scene(60, first_gap=8.0)
        for k, j in enumerate((25, 38, 51)):
            body[j] = (body[j][0], ex[k], body[j][2])
        d = build(opening + body, duration=900)
        o = Z.episode_zones(d)["opening"]
        self.assertIn("teaser", kinds(o, "hard"))
        st = first_start(d, "s0a")
        self.assertAlmostEqual(o["story_start"], st, places=2)
        self.assertGreaterEqual(o["head_end"], first_start(d, "t2w0"))

    def test_recap_from_sibling_episode(self):
        rec = [f"r{k}a r{k}b r{k}c r{k}d r{k}e r{k}f" for k in range(4)]
        prev_ep = build([(A, x, 1.0) for x in rec] + scene(30, tag="p"))
        segs = [((A, B)[k % 2], x, 0.0 if k == 0 else 0.6) for k, x in enumerate(rec)] + scene(40, first_gap=9.0)
        d = build(segs, duration=900)
        o = Z.episode_zones(d, references=[prev_ep])["opening"]
        self.assertIn("recap", kinds(o, "hard"))
        self.assertAlmostEqual(o["story_start"], first_start(d, "s0a"), places=2)
        # without the sibling the recap lines are taken as the story start (no evidence)
        o2 = Z.episode_zones(d)["opening"]
        self.assertAlmostEqual(o2["story_start"], 0.0, places=2)
        # one repeated line is only a soft hint
        one = build([(A, rec[0], 0.0)] + scene(40, first_gap=9.0), duration=900)
        o3 = Z.episode_zones(one, references=[prev_ep])["opening"]
        self.assertIn("recap", kinds(o3, "soft"))
        self.assertNotIn("recap", kinds(o3, "hard"))

    def test_chapters(self):
        d = build(scene(80, first_gap=1.0), duration=900)
        meta = {"chapters": [{"title": "Recap", "start": 0, "end": 40}, {"title": "Episode", "start": 40, "end": 900}]}
        o = Z.episode_zones(d, meta=meta)["opening"]
        self.assertIn("chapter", kinds(o, "hard"))
        self.assertGreaterEqual(o["head_end"], 40.0)
        self.assertGreaterEqual(o["story_start"], 40.0)
        topic = {"chapters": [{"title": "Intro to hair loss and genetics", "start": 0, "end": 267}]}
        self.assertNotIn("chapter", kinds(Z.episode_zones(d, meta=topic)["opening"]))
        no_end = {"chapters": [{"title": "Previously", "start_time": 0}, {"title": "Story", "start_time": 30}]}
        self.assertEqual(Z.chapter_ranges(no_end, 900), [(0.0, 30.0, "Previously")])


AD = ["Morning ready అవుతూ ఈ neem face wash use చేస్తాను",
      "ఇది clinically proven to clear pimples",
      "ఇది Amazon Flipkart మరియు app and website లో available గా ఉంది",
      "Link description లో ఉంది వెళ్ళి check చేయండి"]


class TestSponsor(unittest.TestCase):
    def ad_episode(self, ad=AD, spk=C):
        segs = scene(30) + [(spk, x, 6.0 if k == 0 else 0.8) for k, x in enumerate(ad)] + scene(30, tag="q", first_gap=6.0)
        return build(segs, duration=900)

    def test_sponsor_block_detected_and_snapped(self):
        d = self.ad_episode()
        w0 = first_start(d, "Morning")
        last = next(w for w in reversed(d["word_timestamps"]) if w["word"] == "చేయండి")["end"]
        sp = Z.episode_zones(d)["sponsors"]
        self.assertEqual(len(sp), 1)
        self.assertGreaterEqual(sp[0]["points"], Z.SPONSOR_MIN_POINTS)
        self.assertTrue(sp[0]["evidence"]["monologue"])
        self.assertAlmostEqual(sp[0]["start"], w0 - Z.PAD, places=2)
        self.assertAlmostEqual(sp[0]["end"], last + Z.PAD, places=2)
        cuts = [w0 - 2.0, w0 - 0.3, last + 0.4, last + 3.0]
        sp = Z.episode_zones(d, cuts=cuts)["sponsors"]
        self.assertAlmostEqual(sp[0]["start"], w0 - 0.3, places=2)   # the cut closest to the first word
        self.assertAlmostEqual(sp[0]["end"], last + 0.4, places=2)

    def test_story_mention_is_not_a_sponsor(self):
        segs = scene(30) + [(A, "నేను Amazon లో order చేశా", 0.8)] + scene(30, tag="q")
        self.assertEqual(Z.episode_zones(build(segs, duration=900))["sponsors"], [])

    def test_brand_from_meta(self):
        ad = ["BrandX neem wash నాకు చాలా ఇష్టం", "ప్రతి రోజు BrandX వాడతాను", "మీరు కూడా try చేయండి",
              "BrandX తో skin బాగుంటుంది"]
        d = self.ad_episode(ad)
        self.assertEqual(Z.episode_zones(d)["sponsors"], [])
        meta = {"description": "Watch now\n#Ad #BrandX #BrandXNeemWash\n#WebSeries"}
        sp = Z.episode_zones(d, meta=meta)["sponsors"]
        self.assertEqual(len(sp), 1)
        self.assertTrue(sp[0]["evidence"]["brand_from_meta"])

    def test_ad_brands(self):
        meta = {"description": "One step\n#Ad #Himalaya #HimalayaPurifyingNeemFaceWash\n#HelloMadam #Episode6"}
        self.assertEqual(Z.ad_brands(meta), [["himalaya"], ["himalaya", "purifying", "neem", "face", "wash"]])
        self.assertEqual(Z.ad_brands({"description": "#HelloMadam #InfinitumMedia"}), [])
        self.assertEqual(Z.ad_brands(None), [])

    def test_long_gapfree_block_is_not_scored(self):
        talk = [(A, f"amazon flipkart nykaa link description లో available on use code x{k}", 0.3) for k in range(60)]
        d = build(talk, duration=900)
        self.assertGreater(d["word_timestamps"][-1]["end"], Z.SPONSOR_MAX_BLOCK_S)
        self.assertEqual(Z.episode_zones(d)["sponsors"], [])

    def test_sponsor_before_story_joins_opening(self):
        segs = [(C, x, 0.0 if k == 0 else 0.8) for k, x in enumerate(AD)] + scene(40, first_gap=6.0)
        d = build(segs, duration=900)
        z = Z.episode_zones(d)
        self.assertEqual(len(z["sponsors"]), 1)
        self.assertAlmostEqual(z["opening"]["story_start"], first_start(d, "s0a"), places=2)
        self.assertIn("sponsor", kinds(z["opening"], "hard"))


if __name__ == "__main__":
    unittest.main()
