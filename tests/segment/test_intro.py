"""Opening teaser / intro detection on synthetic transcripts."""
import json
import tempfile
import unittest
from pathlib import Path

from shotvi.segment import from_dict, segment_dict
from shotvi.segment.__main__ import main as cli_main
from shotvi.segment.intro import detect_teasers, find_fragments
from shotvi.segment.pauses import pause_stats

from .helpers import build

H, G = "speaker_1", "speaker_0"


def _answer(k, j, n=10):
    return " ".join(f"g{k}s{j}w{i}" for i in range(n - 1)) + f" g{k}s{j}end."


def _question(k):
    return f"hq{k}a hq{k}b hq{k}c hq{k}d ఏంటి?"


def body(n_rounds=14, sents=4, greeting=None):
    """Host asks, guest answers with `sents` unique sentences per round."""
    segs = []
    if greeting:
        segs += greeting
    for k in range(n_rounds):
        segs.append((H, _question(k), 1.5))
        for j in range(sents):
            segs.append((G, _answer(k, j), 0.8 if j == 0 else 1.0))
    return segs


def teaser(excerpts, gap=0.6):
    """excerpts: list of (speaker, text). Short gaps between them (montage)."""
    return [(spk, txt, gap if n else 0.0) for n, (spk, txt) in enumerate(excerpts)]


GREETING = [(H, "hi sir welcome to the podcast", 3.0), (G, "hello thank you for having me", 0.5)]
STD_EXCERPTS = [(G, _answer(9, 1)), (G, _answer(3, 2)), (H, _question(11)), (G, _answer(12, 0)),
                (G, _answer(6, 3))]


def with_teaser(excerpts=STD_EXCERPTS, n_rounds=14, music=True, pre=None, greeting=GREETING):
    segs = (pre or []) + teaser(excerpts)
    d0 = build(segs)
    t_end = d0["word_timestamps"][-1]["end"]
    gap = 6.0
    segs = segs + [(spk, txt, (gap if k == 0 else g)) for k, (spk, txt, g) in enumerate(body(n_rounds, greeting=greeting))]
    events = [("[పాట]", t_end + 0.5, t_end + gap - 0.5)] if music else []
    return build(segs, events=events), len(d0["word_timestamps"])


class TestTeaserDetection(unittest.TestCase):
    def test_teaser_at_start_detected_with_body_locations(self):
        d, n_teaser = with_teaser()
        seg = segment_dict(d)
        teasers = [r for r in seg.intro_ranges if r.kind == "teaser"]
        self.assertEqual(len(teasers), 1)
        t = teasers[0]
        self.assertEqual((t.i0, t.i1), (0, n_teaser - 1))
        self.assertGreaterEqual(t.confidence, 0.8)
        self.assertEqual(t.evidence["separator"], "music")
        # seconds extend over the music sting up to the real start
        self.assertGreater(t.end, seg.transcript.words[t.i1].end + 1.0)
        words = seg.transcript.words
        for f in t.fragments:
            self.assertGreater(f.body_i0, t.i1)
            self.assertEqual(words[f.i0].text, words[f.body_i0].text)
        # excerpts are shuffled relative to the body
        self.assertTrue(t.evidence["body_order_shuffled"])
        self.assertGreaterEqual(t.evidence["n_body_locations"], 4)

    def test_teaser_body_copy_maps_to_body(self):
        d, n_teaser = with_teaser()
        seg = segment_dict(d)
        frs = seg.teaser_body_copy(0, 5)
        self.assertTrue(frs)
        words = seg.transcript.words
        self.assertEqual(words[frs[0].body_i0].text, "g9s1w0")

    def test_units_and_turns_do_not_straddle_and_are_marked(self):
        d, n_teaser = with_teaser()
        seg = segment_dict(d)
        for u in seg.units:
            inside = u.i1 < n_teaser
            outside = u.i0 >= n_teaser
            self.assertTrue(inside or outside, f"unit {u.id} straddles the teaser edge")
            self.assertEqual(u.intro_kind == "teaser", inside)
        for t in seg.turns:
            self.assertTrue(t.i1 < n_teaser or t.i0 >= n_teaser)
        self.assertEqual(seg.excluded_word_ranges(("teaser",)), [(0, n_teaser - 1)])
        self.assertTrue(seg.overlaps_excluded(n_teaser - 1, n_teaser + 3))

    def test_no_links_from_teaser_and_host_evidence_excludes_it(self):
        # the host's teaser question is mis-diarized as the guest
        # (and the guest's answer as the host), as in a mis-diarized montage
        ex = [(G, _answer(9, 1)), (G, _question(11)), (H, _answer(11, 0)), (G, _answer(12, 0)),
              (G, _question(5)), (H, _answer(5, 1)), (G, _answer(6, 3))]
        d, n_teaser = with_teaser(ex)
        on = segment_dict(d)
        off = segment_dict(d, detect_intro=False)
        self.assertEqual(on.host, H)
        for l in on.links:
            self.assertGreaterEqual(l.q_i0, n_teaser)
            self.assertGreaterEqual(l.a_i0, n_teaser)
        self.assertTrue(any(l.q_i0 < n_teaser for l in off.links))
        self.assertLess(on.roles[G].host_score, off.roles[G].host_score)

    def test_render_omits_teaser_with_note(self):
        d, n_teaser = with_teaser()
        seg = segment_dict(d)
        hidden = [u.id for u in seg.units if u.intro_kind == "teaser"]
        txt = seg.render()
        self.assertIn("omitted: opening teaser", txt.splitlines()[0])
        for uid in hidden:
            self.assertNotIn(f"\nU{uid} [", "\n" + txt)
        full = seg.render(include_intro=True)
        self.assertIn(f"U{hidden[0]} [", full)
        chunks = seg.render_chunks(max_tokens=400, overlap_tokens=60)
        self.assertIn("omitted: opening teaser", chunks[0].text)
        self.assertGreater(chunks[0].first_unit, hidden[-1])
        self.assertTrue(all(c.first_unit > hidden[-1] for c in chunks))

    def test_intro_after_teaser_is_tagged(self):
        d, n_teaser = with_teaser()
        seg = segment_dict(d)
        intros = [r for r in seg.intro_ranges if r.kind == "intro"]
        self.assertEqual(len(intros), 1)
        r = intros[0]
        self.assertEqual(r.i0, n_teaser)
        self.assertIn("welcome", r.evidence["cues"])
        self.assertEqual(seg.transcript.words[r.i1 + 1].text, "hq0a")  # up to the first question
        self.assertIn("(intro) H: hi sir welcome", seg.render())
        # intro speech still counts for the host
        self.assertEqual(seg.host, H)

    def test_no_intro_without_cues(self):
        d, _ = with_teaser(greeting=[(H, "so today topic lo chala vishayalu unnayi", 3.0)])
        seg = segment_dict(d)
        self.assertEqual([r.kind for r in seg.intro_ranges], ["teaser"])

    def test_teaser_not_at_time_zero(self):
        pre = [(H, "namaskaram andariki welcome back to the channel", 0.0)]
        d0 = build(pre)
        n_pre = len(d0["word_timestamps"])
        segs = pre + [(spk, txt, 3.0 if k == 0 else g) for k, (spk, txt, g) in enumerate(teaser(STD_EXCERPTS))]
        n_t = len(build(segs)["word_timestamps"])
        segs += [(spk, txt, 6.0 if k == 0 else g) for k, (spk, txt, g) in enumerate(body())]
        seg = segment_dict(build(segs))
        kinds = [(r.kind, r.i0, r.i1) for r in seg.intro_ranges]
        self.assertIn(("teaser", n_pre, n_t - 1), kinds)
        self.assertIn(("intro", 0, n_pre - 1), kinds)

    def test_asr_variation_tolerated(self):
        def noisy(text, every=4):
            ws = text.split()
            return " ".join(("X" + w if k % every == every - 1 else w) for k, w in enumerate(ws))
        ex = [(G, noisy(_answer(9, 1))), (G, "ఉమ్ " + noisy(_answer(3, 2))), (G, noisy(_answer(12, 0))),
              (G, noisy(_answer(6, 3)))]
        d, n_teaser = with_teaser(ex)
        seg = segment_dict(d)
        t = [r for r in seg.intro_ranges if r.kind == "teaser"]
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0].i0, t[0].i1), (0, n_teaser - 1))

    def test_jump_cut_excerpt_still_matches(self):
        # the editor dropped words from inside the excerpt
        a = _answer(9, 1).split() + _answer(9, 2).split()
        cut = " ".join(a[:4] + a[9:13] + a[16:])
        ex = [(G, cut), (G, _answer(3, 2)), (G, _answer(12, 0))]
        d, n_teaser = with_teaser(ex)
        tr = from_dict(d)
        frs = find_fragments(tr)
        first = [f for f in frs if f.i0 == 0]
        self.assertTrue(first)
        self.assertGreaterEqual(first[0].i1, 10)

    def test_single_speaker_monologue_with_cold_open(self):
        sents = [" ".join(f"m{k}w{i}" for i in range(9)) + f" m{k}end." for k in range(70)]
        cold = [("speaker_0", sents[40] + " " + sents[41], 0.0)]
        segs = cold + [("speaker_0", s, 4.0 if k == 0 else 1.0) for k, s in enumerate(sents)]
        seg = segment_dict(build(segs))
        self.assertIsNone(seg.host)
        t = [r for r in seg.intro_ranges if r.kind == "teaser"]
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0].i0, t[0].i1), (0, 19))
        self.assertEqual(len(t[0].fragments), 1)


class TestNoTeaser(unittest.TestCase):
    def test_plain_dialogue_returns_nothing(self):
        seg = segment_dict(build(body()))
        self.assertEqual(seg.intro_ranges, [])
        self.assertEqual(seg.excluded_word_ranges(), [])
        self.assertTrue(all(not u.in_intro for u in seg.units))
        self.assertNotIn("omitted", seg.render())

    def test_repeated_catchphrase_is_not_a_teaser(self):
        segs = body()
        segs.insert(2, (G, "success and money only two languages", 1.0))
        segs.insert(40, (G, "success and money only two languages", 1.0))
        tr = from_dict(build(segs))
        self.assertEqual(detect_teasers(tr, pause_stats(tr)), [])

    def test_callback_late_in_episode_is_not_a_teaser(self):
        segs = body(n_rounds=30)
        # guest repeats an earlier answer much later (a callback), well past the opening
        segs.insert(80, (G, _answer(20, 1) + " " + _answer(25, 2), 1.0))
        tr = from_dict(build(segs))
        self.assertEqual(detect_teasers(tr, pause_stats(tr)), [])

    def test_common_phrases_are_not_evidence(self):
        phrase = "thank you sir thank you so much"
        segs = [(H, phrase, 0.0)] + body() + [(G, phrase, 1.0)]
        tr = from_dict(build(segs))
        self.assertEqual(detect_teasers(tr, pause_stats(tr)), [])

    def test_opening_music_with_lyrics(self):
        segs = [(G, "la la naa paata lyric one lyric two", 0.0)] + body()
        d = build(segs, events=[("[పాట]", 0.0, 4.0)])
        seg = segment_dict(d)
        self.assertEqual([r.kind for r in seg.intro_ranges], ["music"])
        self.assertEqual(seg.intro_ranges[0].i0, 0)
        self.assertTrue(seg.units[0].intro_kind == "music")
        self.assertIn("omitted: opening music", seg.render())


class TestOutput(unittest.TestCase):
    def test_to_dict_and_cli(self):
        d, n_teaser = with_teaser()
        seg = segment_dict(d)
        out = seg.to_dict(chunk_tokens=400, overlap_tokens=60)
        json.dumps(out, ensure_ascii=False)
        self.assertEqual(out["intro_ranges"][0]["kind"], "teaser")
        self.assertEqual(out["intro_ranges"][0]["words"], [0, n_teaser - 1])
        self.assertIn("body_words", out["intro_ranges"][0]["fragments"][0])
        u0 = out["units"][0]
        self.assertTrue(u0["in_intro"])
        self.assertEqual(u0["intro_kind"], "teaser")
        intro_units = [u for u in out["units"] if u["intro_kind"] == "intro"]
        self.assertTrue(intro_units and not intro_units[0]["text"].startswith("(intro)"))
        self.assertEqual(out["turns"][0]["intro_kind"], "teaser")
        self.assertEqual(out["stats"]["units_in_intro"]["intro"], len(intro_units))
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x_scribe_transcript.json"
            p.write_text(json.dumps(d), encoding="utf-8")
            o = Path(td) / "out" / "segments.json"
            self.assertEqual(cli_main([str(p), "--out", str(o), "--quiet"]), 0)
            self.assertTrue(json.loads(o.read_text(encoding="utf-8"))["intro_ranges"])
            self.assertEqual(cli_main([str(p), "--out", str(o), "--quiet", "--no-intro"]), 0)
            self.assertEqual(json.loads(o.read_text(encoding="utf-8"))["intro_ranges"], [])


if __name__ == "__main__":
    unittest.main()
