"""Regressions for code-review round 1 (snap widening, host floor, force-split clauses, snap vs intro ranges)."""
import unittest

from shotvi.segment import segment_dict
from shotvi.segment.snap import SnapError

from .helpers import build, monologue
from .test_intro import with_teaser


def _two_sentences():
    a = " ".join(f"a{i}" for i in range(60)) + "."
    b = " ".join(f"b{i}" for i in range(120)) + "."
    return segment_dict(build([("A", a, 0), ("A", b, 1.5)]))


class SnapWidening(unittest.TestCase):
    def test_widened_snap_is_flagged_and_not_clean(self):
        seg = _two_sentences()
        for mode in ("nearest", "expand"):
            s = seg.snap(35.0, 50.0, mode=mode)
            self.assertTrue(s.start_widened and s.end_widened, mode)
            self.assertFalse(s.clean, mode)
            self.assertTrue(any("beyond max_shift" in n for n in s.notes), mode)

    def test_no_widen_stays_in_window(self):
        seg = _two_sentences()
        s = seg.snap(35.0, 50.0, widen=False)
        self.assertLessEqual(abs(s.start_shift), 4.0)
        self.assertLessEqual(abs(s.end_shift), 4.0)
        self.assertFalse(s.start_widened or s.end_widened)
        self.assertFalse(s.clean)

    def test_strict_raises(self):
        seg = _two_sentences()
        with self.assertRaises(SnapError):
            seg.snap(35.0, 50.0, strict=True)
        with self.assertRaises(ValueError):  # SnapError is a ValueError
            seg.snap(35.0, 50.0, widen=False, strict=True)

    def test_fallback_is_never_clean(self):
        seg = _two_sentences()
        s = seg.snap(-50.0, -10.0)
        self.assertTrue(s.fallback)
        self.assertFalse(s.clean)
        self.assertTrue(any("no candidate" in n for n in s.notes))

    def test_clean_snap_unchanged(self):
        seg = segment_dict(build(monologue("A", 6)))
        ws = seg.transcript.words
        s = seg.snap(ws[8].start - 0.3, ws[23].end + 0.2, max_shift=1.0)
        self.assertTrue(s.clean)
        self.assertEqual((s.start_word, s.end_word), (8, 23))
        self.assertFalse(s.start_widened or s.end_widened or s.fallback or s.crosses_excluded)
        self.assertIn("start_widened", s.to_dict())
        seg.snap(ws[8].start, ws[23].end, max_shift=1.0, strict=True)  # does not raise


class HostFloor(unittest.TestCase):
    def test_panel_without_questions_has_no_host(self):
        segs = []
        for k in range(9):
            spk = "ABC"[k % 3]
            segs += [(spk, s, 0.8 if j else 1.2) for j, (_, s, _) in enumerate(monologue(spk, 3, prefix=f"p{k}"))]
        seg = segment_dict(build(segs))
        self.assertIsNone(seg.host)
        self.assertEqual({seg.roles[s].role for s in "ABC"}, {"speaker"})
        self.assertEqual(sorted(seg.roles[s].label for s in "ABC"), ["S1", "S2", "S3"])
        self.assertFalse([l for l in seg.links if l.kind == "prompt"])
        txt = seg.render()
        self.assertNotIn(" H:", txt)
        self.assertNotIn("G1:", txt)
        self.assertIn("S1:", txt)

    def test_clear_host_still_found(self):
        segs = []
        for k in range(6):
            segs.append(("H", f"ఇది ఎలా జరిగింది{k}?", 0.5))
            segs += monologue("G1" if k % 2 else "G2", 3, prefix=f"z{k}")
        seg = segment_dict(build(segs))
        self.assertEqual(seg.host, "H")
        self.assertGreaterEqual(seg.host_confidence, 0.1)


class ForceSplitClauses(unittest.TestCase):
    def test_straddling_clause_is_kept(self):
        txt = " ".join(f"w{i}" for i in range(200)) + " ఎందుకు ఇలా చేశారు?"
        seg = segment_dict(build([("A", txt, 0),
                                  ("B", "reply words here now and more words to make it long enough ok.", 1.0)]))
        a_units = [u for u in seg.units if u.speaker == "A"]
        self.assertGreaterEqual(len(a_units), 2)
        for u in a_units:
            covered = {i for c in u.clauses for i in c.words}
            self.assertEqual(covered, set(u.main), f"unit {u.id} lost clause words")
        self.assertTrue(a_units[-1].signals["question"])
        self.assertFalse(a_units[0].signals["question"])


class SnapExcluded(unittest.TestCase):
    def setUp(self):
        d, self.n_teaser = with_teaser()
        self.seg = segment_dict(d)
        self.ws = self.seg.transcript.words
        self.t_end = self.seg.intro_ranges[0].i1

    def test_crossing_is_reported(self):
        s = self.seg.snap(self.ws[10].start, self.ws[self.n_teaser + 30].end)
        self.assertTrue(s.crosses_excluded)
        self.assertIn(self.seg.intro_ranges[0].id, s.excluded_ids)
        self.assertTrue(any("excluded teaser" in n for n in s.notes))

    def test_avoid_excluded_never_crosses(self):
        for a, b in ((10, self.n_teaser + 30), (self.n_teaser - 3, self.n_teaser + 40)):
            s = self.seg.snap(self.ws[a].start, self.ws[b].end, avoid_excluded=True)
            self.assertFalse(s.crosses_excluded, (a, b))
            self.assertFalse(self.seg.overlaps_excluded(s.start_word, s.end_word))
            self.assertGreater(s.start_word, self.t_end)

    def test_avoid_only_some_kinds(self):
        intro = next(r for r in self.seg.intro_ranges if r.kind == "intro")
        b = intro.i1 + 30
        s = self.seg.snap(self.ws[intro.i0].start, self.ws[b].end, avoid_excluded=("teaser",))
        self.assertTrue(s.crosses_excluded)          # intro still reported ...
        self.assertLessEqual(s.start_word, intro.i1)  # ... but not avoided
        s2 = self.seg.snap(self.ws[intro.i0].start, self.ws[b].end, avoid_excluded=True)
        self.assertGreater(s2.start_word, intro.i1)
        self.assertFalse(s2.crosses_excluded)


if __name__ == "__main__":
    unittest.main()
