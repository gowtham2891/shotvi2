"""eval.speakers on a synthetic answer key: time-overlap majority vote, Hungarian one-to-one, purity, name matching,
UNKNOWN skipped, OFFSCREEN scored and reported separately."""
import json
import tempfile
import unittest
from pathlib import Path

from eval import speakers as S


def words(spans):
    """spans: (start, end, speaker) per word."""
    return [{"word": f"w{i}", "start": a, "end": b, "speaker": sp} for i, (a, b, sp) in enumerate(spans)]


KEY = {"characters": [{"name": "Asha"}, {"name": "Ravi", "aliases": ["Ravi garu"]}, {"name": "Asha's mother"},
                      {"name": "Warden"}],
       "labels": [{"start": 0.0, "end": 2.0, "speaker": "Asha", "confidence": 3},
                  {"start": 2.0, "end": 4.0, "speaker": "Ravi", "confidence": 3},
                  {"start": 4.0, "end": 6.0, "speaker": "Asha", "confidence": 2},
                  {"start": 6.0, "end": 8.0, "speaker": "OFFSCREEN:Asha's mother", "confidence": 2},
                  {"start": 8.0, "end": 10.0, "speaker": "Warden", "confidence": 1},
                  {"start": 10.0, "end": 12.0, "speaker": "UNKNOWN"},
                  {"start": 20.0, "end": 21.0, "speaker": "Ravi", "confidence": 3}]}   # no words there


def key_file(d):
    p = Path(d) / "k.json"
    p.write_text(json.dumps(KEY), encoding="utf-8")
    return S.load_key(p)


class TestKey(unittest.TestCase):
    def test_load(self):
        with tempfile.TemporaryDirectory() as d:
            k = key_file(d)
        self.assertEqual([l["speaker"] for l in k["lines"]][3], "Asha's mother")
        self.assertTrue(k["lines"][3]["offscreen"])
        self.assertTrue(k["lines"][5]["unknown"])


class TestLineLabel(unittest.TestCase):
    def test_majority_and_tie_by_seconds(self):
        tr = S.Track(S.word_track(words([(0.0, 0.5, "a"), (0.6, 0.9, "b"), (1.0, 1.9, "b"), (1.95, 2.5, "a")])))
        self.assertEqual(S.line_label(tr, 0.0, 2.0), "b")          # a: 2 words (0.55 s), b: 2 words (1.2 s)
        self.assertIsNone(S.line_label(tr, 5.0, 6.0))
        tr2 = S.Track(S.word_track(words([(0.0, 3.0, "x")])))
        self.assertEqual(S.line_label(tr2, 1.0, 2.0), "x")          # a word spanning the whole line


class TestHungarian(unittest.TestCase):
    def test_rectangular(self):
        self.assertEqual(S.hungarian_max([[5, 1], [4, 3], [0, 0]]), [0, 1, -1])
        self.assertEqual(S.hungarian_max([[1, 9, 0]]), [1])
        self.assertEqual(S.hungarian_max([]), [])

    def test_one_to_one_vs_purity(self):
        pairs = [("s0", "A")] * 5 + [("s0", "B")] * 4 + [("s1", "A")] * 3
        one = S.best_one_to_one(pairs)
        self.assertEqual(one, {"s0": "B", "s1": "A"})               # 4 + 3 = 7 beats s0->A (5)
        self.assertEqual(S.majority_map(pairs), {"s0": "A", "s1": "A"})


class TestScore(unittest.TestCase):
    def test_scribe_labels(self):
        # merged: s0 covers Asha and Ravi (Scribe's typical error); s1 = mother; s2 = warden
        w = words([(0.2, 1.8, "s0"), (2.2, 3.8, "s0"), (4.2, 5.8, "s0"), (6.2, 7.8, "s1"), (8.2, 9.8, "s2"),
                   (10.2, 11.8, "s2")])
        with tempfile.TemporaryDirectory() as d:
            k = key_file(d)
        r = S.score_track(k, S.word_track(w))
        self.assertEqual(r["n_unknown_skipped"], 1)
        self.assertEqual(r["no_words"], 1)
        o = r["one_to_one"]
        self.assertEqual(o["all"]["n"], 6)
        self.assertEqual(o["all"]["correct"], 4)                     # Asha x2, mother, warden; Ravi lost
        self.assertEqual(o["offscreen"], {"n": 1, "correct": 1, "acc": 1.0})
        self.assertEqual(o["onscreen"]["n"], 5)
        self.assertEqual(r["purity"]["all"]["correct"], 4)

    def test_llm_names(self):
        w = words([(0.2, 1.8, None), (2.2, 3.8, None), (4.2, 5.8, None), (6.2, 7.8, None), (8.2, 9.8, None)])
        names = ["asha", "Ravi garu", "Asha (student)", "Asha's Mom", "the hostel warden"]
        with tempfile.TemporaryDirectory() as d:
            k = key_file(d)
        r = S.score_track(k, S.word_track(w, names), names=True)
        self.assertEqual(r["name_acc"]["all"]["correct"], 5)
        self.assertEqual(r["unmatched_names"], [])
        r = S.score_track(k, S.word_track(w, ["Asha", "Ravi", "Asha", "Asha", "Principal"]), names=True)
        self.assertEqual(r["name_acc"]["all"]["correct"], 3)
        self.assertEqual(r["unmatched_names"], ["Principal"])

    def test_match_name(self):
        t = S.alias_table([{"name": "Asha", "aliases": []}, {"name": "Asha's mother", "aliases": []}])
        self.assertEqual(S.match_name("Asha's mother", t), "Asha's mother")
        self.assertEqual(S.match_name("Asha's father", t), "Asha's mother")   # relation words -> "parent"
        self.assertEqual(S.match_name("Asha Reddy", t), "Asha")
        self.assertIsNone(S.match_name("Asha's boss", t))
        self.assertIsNone(S.match_name(None, t))
        t = S.alias_table([{"name": "Ekta"}, {"name": "Harshita"}, {"name": "Coworker (Sindhu)"},
                           {"name": "Pratyusha"}])
        self.assertEqual(S.match_name("Ektha", t), "Ekta")
        self.assertEqual(S.match_name("Harshitha", t), "Harshita")
        self.assertEqual(S.match_name("Sindhu", t), "Coworker (Sindhu)")
        self.assertEqual(S.match_name("Pratyushaa", t), "Pratyusha")
        self.assertIsNone(S.match_name("Pratheeksha", t))                     # a different name, not a spelling

    def test_overall_pools_lines(self):
        a = {"video_id": "v1", "systems": {"x": {"one_to_one": {k: {"n": 2, "correct": 1} for k in
                                                                ("all", "onscreen", "offscreen")},
                                                 "purity": {k: {"n": 2, "correct": 2} for k in
                                                            ("all", "onscreen", "offscreen")}}}}
        b = {"video_id": "v2", "systems": {"x": a["systems"]["x"]}}
        ov = S.overall([a, b])
        self.assertEqual(ov["x"]["one_to_one.all"], {"correct": 2, "n": 4, "acc": 0.5})
        self.assertEqual(ov["x"]["episodes"], ["v1", "v2"])


if __name__ == "__main__":
    unittest.main()
