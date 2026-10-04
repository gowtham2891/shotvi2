import unittest

from shotvi.segment import UnitParams, from_dict, segment_dict
from shotvi.segment.pauses import pause_stats
from shotvi.segment.turns import build_turns

from .helpers import build, monologue

LONG_A = "a1 a2 a3 a4 a5 a6 a7 a8 a9 a10."
LONG_A2 = "b1 b2 b3 b4 b5 b6 b7 b8 b9 b10."


def turns_of(d):
    tr = from_dict(d)
    return tr, build_turns(tr, pause_stats(tr))


def assert_tiles(tc, items, n):
    """Inclusive ranges cover 0..n-1 exactly once, in order."""
    nxt = 0
    for it in items:
        tc.assertEqual(it.i0, nxt)
        tc.assertGreaterEqual(it.i1, it.i0)
        nxt = it.i1 + 1
    tc.assertEqual(nxt, n)


class TurnsTest(unittest.TestCase):
    def test_lexicon_backchannel_absorbed_and_recorded(self):
        tr, turns = turns_of(build([("A", LONG_A, 0), ("B", "ఉమ్", 0.1), ("A", LONG_A2, 0.1)]))
        self.assertEqual(len(turns), 1)
        t = turns[0]
        self.assertEqual(t.speaker, "A")
        self.assertEqual(len(t.backchannels), 1)
        b = t.backchannels[0]
        self.assertEqual((b.kind, b.speaker, b.text, b.i0, b.i1), ("lexicon", "B", "ఉమ్", 10, 10))
        self.assertNotIn(10, t.main)
        assert_tiles(self, turns, len(tr.words))

    def test_short_echo_is_interjection(self):
        _, turns = turns_of(build([("A", LONG_A, 0), ("B", "ఇది కావాలి", 0.1), ("A", LONG_A2, 0.1)]))
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].backchannels[0].kind, "interjection")

    def test_short_question_is_not_absorbed(self):
        _, turns = turns_of(build([("A", LONG_A, 0), ("B", "ఎందుకు?", 0.1), ("A", LONG_A2, 0.1)]))
        self.assertEqual([t.speaker for t in turns], ["A", "B", "A"])

    def test_backchannel_after_long_silence_not_absorbed(self):
        _, turns = turns_of(build([("A", LONG_A, 0), ("B", "hi sir", 15.0), ("A", LONG_A2, 0.2)]))
        self.assertEqual([t.speaker for t in turns], ["A", "B", "A"])

    def test_chain_of_listeners(self):
        tr, turns = turns_of(build([("A", LONG_A, 0), ("B", "ఉమ్", 0.1), ("C", "yeah", 0.05),
                                    ("A", LONG_A2, 0.1), ("B", LONG_A2, 0.3), ("C", LONG_A, 0.3)]))
        self.assertEqual([t.speaker for t in turns], ["A", "B", "C"])
        self.assertEqual(sorted(b.speaker for b in turns[0].backchannels), ["B", "C"])
        assert_tiles(self, turns, len(tr.words))

    def test_trailing_backchannel_before_third_speaker_becomes_turn(self):
        d = build([("A", LONG_A, 0), ("B", "ఉమ్", 0.1), ("C", LONG_A2, 0.2)])
        d["word_timestamps"][10]["start"] -= 0.4  # overlaps A's last word
        tr, turns = turns_of(d)
        assert_tiles(self, turns, len(tr.words))
        self.assertEqual(sum(len(t.backchannels) for t in turns), 0)

    def test_ghost_run_absorbed(self):
        segs = [("A", "a " * 150, 0), ("G", "x y z w v", 0.2), ("A", "a " * 50, 0.2), ("B", "b " * 150, 0.3)]
        tr, turns = turns_of(build(segs))
        self.assertIn("G", tr.ghost_speakers)
        self.assertEqual(turns[0].backchannels[0].kind, "ghost")

    def test_overlap_run_absorbed(self):
        d = build([("A", LONG_A, 0), ("B", "that one", 0.1), ("A", LONG_A2, 0.1), ("B", LONG_A, 0.3)])
        d["word_timestamps"][10]["start"] -= 0.3
        _, turns = turns_of(d)
        kinds = [b.kind for b in turns[0].backchannels]
        self.assertTrue(kinds and kinds[0] in ("overlap", "interjection"))


class UnitsTest(unittest.TestCase):
    def test_monologue_splits_into_bounded_units(self):
        seg = segment_dict(build(monologue("A", 40, pause=1.5)))
        self.assertIsNone(seg.host)
        self.assertEqual(seg.roles["A"].role, "solo")
        assert_tiles(self, seg.units, len(seg.transcript.words))
        p = seg.params
        for u in seg.units:
            self.assertGreaterEqual(u.dur, p.min_s - 1e-6)
            self.assertLessEqual(u.dur, p.max_s + 1e-6)
        self.assertEqual([u.id for u in seg.units], list(range(len(seg.units))))

    def test_split_prefers_the_long_pause(self):
        segs = monologue("A", 4, pause=0.4, prefix="p") + [("A", s, 4.0 if k == 0 else 0.4)
                                                          for k, s in enumerate(
                                                              ["q%d." % i + " x" * 7 for i in range(4)])]
        seg = segment_dict(build(segs))
        cut_word = 32  # first word after the 4s pause
        self.assertIn(cut_word, [u.i0 for u in seg.units])

    def test_no_punctuation_no_pauses_is_force_split(self):
        seg = segment_dict(build([("A", " ".join(f"w{i}" for i in range(400)), 0)]))
        self.assertGreater(len(seg.units), 1)
        self.assertTrue(all(u.dur <= seg.params.max_s + 1e-6 for u in seg.units))
        assert_tiles(self, seg.units, len(seg.transcript.words))

    def test_short_question_joins_its_answer(self):
        segs = (monologue("G", 3, prefix="g") + [("H", "ఇది ఎందుకు?", 0.5)]
                + monologue("G", 3, prefix="k")[:1] + [("G", s, 0.3) for s in
                                                        ["k9 y y y y y y y.", "k8 y y y y y y y."]])
        seg = segment_dict(build(segs))
        qi = next(i for i, w in enumerate(seg.transcript.words) if w.text == "ఇది")
        u = seg.unit_of_word(qi)
        self.assertEqual(u.i0, qi, "question should open its unit, not close the previous one")
        self.assertIn("G", u.speaker_share)

    def test_music_event_is_a_hard_boundary(self):
        segs = monologue("A", 2, prefix="m") + [("A", "n0 x x x x x x x.", 0.3)]
        d = build(segs)
        t_mid = d["word_timestamps"][8]["start"] - 0.2  # between sentence 1 and 2
        d["audio_events"] = [{"text": "[పాట]", "start": t_mid - 0.05, "end": t_mid + 0.05}]
        seg = segment_dict(d, UnitParams(min_s=1.0))
        self.assertIn(8, [u.i0 for u in seg.units])

    def test_three_speakers_labels(self):
        segs = []
        for k in range(6):
            segs.append(("H", f"ఇది ఎలా జరిగింది{k}?", 0.5))
            segs += monologue("G1" if k % 2 else "G2", 3, prefix=f"z{k}")
        seg = segment_dict(build(segs))
        self.assertEqual(seg.host, "H")
        self.assertEqual({seg.roles[s].label for s in ("G1", "G2")}, {"G1", "G2"})
        self.assertEqual(len([l for l in seg.links if l.asker == "H"]), 6)


if __name__ == "__main__":
    unittest.main()
