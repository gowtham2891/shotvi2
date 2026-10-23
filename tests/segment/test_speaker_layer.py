"""shotvi.segment.speaker_layer offline: Sarvam word-to-segment mapping (most overlap, nearest within 0.5 s, else the
Scribe label), majority line label with the split recorded, anonymous speaker_<n> labels in order of first
appearance, the llm_fix source (cast keys, never names), applying a layer to lines and words, the placeholder."""
import unittest

from shotvi.segment import speaker_layer as SL


def W(a, b, spk="speaker_0", word="w"):
    return {"word": word, "start": a, "end": b, "speaker": spk}


def seg(a, b, sid):
    return {"start_time_seconds": a, "end_time_seconds": b, "speaker_id": sid, "transcript": "x"}


def raw(*segs):
    return {"diarized_transcript": {"entries": list(segs)}}


def tr_of(words, sentences):
    """sentences: [(i0, i1)] -> a Scribe-like dict (sentence speaker = its first word's Scribe label)."""
    return {"word_timestamps": words,
            "sentences": [{"words": [a, b], "start": words[a]["start"], "end": words[b]["end"],
                           "speaker": words[a]["speaker"], "text": "t"} for a, b in sentences]}


class WordToSegment(unittest.TestCase):
    def test_most_overlap_wins(self):
        segs = SL.sarvam_segments(raw(seg(0.0, 1.2, "4"), seg(1.2, 3.0, "5")))
        ids, how = SL.words_to_segments([W(1.0, 1.6)], segs)        # 0.2 s in "4", 0.4 s in "5"
        self.assertEqual((ids, how), (["sarvam:5"], ["overlap"]))

    def test_overlapping_segments_take_the_larger_overlap(self):
        segs = SL.sarvam_segments(raw(seg(0.0, 5.0, "1"), seg(2.0, 2.6, "2")))
        ids, _ = SL.words_to_segments([W(2.0, 2.5), W(4.0, 4.5)], segs)
        # 2.0-2.5: a 0.5 s tie -> the segment whose centre is nearer the word's (2.25): "2" (2.3), not "1" (2.5)
        self.assertEqual(ids, ["sarvam:2", "sarvam:1"])
        ids, _ = SL.words_to_segments([W(2.1, 2.9)], segs)             # "1": 0.8 s, "2": 0.5 s
        self.assertEqual(ids, ["sarvam:1"])
        ids, _ = SL.words_to_segments([W(2.1, 2.5)], SL.sarvam_segments(raw(seg(0.0, 2.3, "1"), seg(2.2, 2.6, "2"))))
        self.assertEqual(ids, ["sarvam:2"])                             # "1": 0.2 s, "2": 0.3 s

    def test_gap_within_half_a_second_takes_the_nearest_segment(self):
        segs = SL.sarvam_segments(raw(seg(0.0, 1.0, "4"), seg(3.0, 4.0, "5")))
        ids, how = SL.words_to_segments([W(1.3, 1.5, "speaker_9"), W(2.6, 2.8, "speaker_9")], segs)
        self.assertEqual(ids, ["sarvam:4", "sarvam:5"])
        self.assertEqual(how, ["nearest", "nearest"])

    def test_gap_over_half_a_second_keeps_the_scribe_label(self):
        segs = SL.sarvam_segments(raw(seg(0.0, 1.0, "4"), seg(3.0, 4.0, "5")))
        ids, how = SL.words_to_segments([W(1.7, 2.2, "speaker_2")], segs)
        self.assertEqual((ids, how), (["scribe:speaker_2"], ["scribe_fallback"]))
        ids, _ = SL.words_to_segments([W(9.0, 9.5, "speaker_1")], segs)   # after every segment
        self.assertEqual(ids, ["scribe:speaker_1"])
        ids, _ = SL.words_to_segments([W(1.0, 1.4)], [])                     # no segments at all
        self.assertEqual(ids, ["scribe:speaker_0"])

    def test_zero_length_word_inside_a_segment(self):
        segs = SL.sarvam_segments(raw(seg(0.0, 1.0, "4")))
        ids, how = SL.words_to_segments([W(0.5, 0.5)], segs)
        self.assertEqual((ids, how), (["sarvam:4"], ["nearest"]))

    def test_inverted_or_empty_segments_are_dropped(self):
        self.assertEqual(SL.sarvam_segments(raw(seg(2.0, 1.0, "4"), seg(1.0, 1.0, "5"), seg(0.0, 1.0, "6"))),
                         [{"start": 0.0, "end": 1.0, "id": "6"}])


class Majority(unittest.TestCase):
    def test_majority_of_words_and_split_recorded(self):
        words = [W(0, 1), W(1, 2), W(2, 3), W(3, 4)]
        ids = ["sarvam:4", "sarvam:4", "sarvam:5", "sarvam:4"]
        lab, split = SL.majority(ids, words, 0, 3)
        self.assertEqual(lab, "sarvam:4")
        self.assertEqual(split["counts"], {"sarvam:4": 3, "sarvam:5": 1})
        self.assertEqual(split["runs"], [[0, 1, "sarvam:4"], [2, 2, "sarvam:5"], [3, 3, "sarvam:4"]])

    def test_tie_goes_to_more_speech_time_then_the_earlier_word(self):
        words = [W(0, 0.2), W(1, 2.0)]
        self.assertEqual(SL.majority(["a", "b"], words, 0, 1)[0], "b")
        words = [W(0, 1), W(1, 2)]
        self.assertEqual(SL.majority(["a", "b"], words, 0, 1)[0], "a")

    def test_one_label_no_split(self):
        self.assertEqual(SL.majority(["a", "a"], [W(0, 1), W(1, 2)], 0, 1), ("a", None))

    def test_split_line_keeps_line_structure_and_majority(self):
        words = [W(0.0, 0.4), W(0.5, 0.9), W(1.0, 1.4), W(5.0, 5.4)]
        tr = tr_of(words, [(0, 2), (3, 3)])
        doc = SL.build(tr, "sarvam", raw(seg(0.0, 0.95, "7"), seg(0.95, 1.5, "3"), seg(4.9, 6.0, "3")))
        self.assertEqual([r["words"] for r in doc["lines"]], [[0, 2], [3, 3]])   # never re-split
        self.assertEqual(doc["lines"][0]["speaker"], "speaker_0")                # "7": 2 of 3 words
        self.assertEqual(doc["lines"][0]["split"]["counts"], {"speaker_0": 2, "speaker_1": 1})
        self.assertEqual(doc["lines"][1]["speaker"], "speaker_1")
        self.assertNotIn("split", doc["lines"][1])
        self.assertEqual(doc["stats"]["n_split_lines"], 1)
        self.assertEqual(doc["words"], ["speaker_0", "speaker_0", "speaker_1", "speaker_1"])


class Anonymous(unittest.TestCase):
    def test_first_appearance_over_lines_then_word_only_ids(self):
        m = SL.anon_mapping(["sarvam:9", "sarvam:2", "sarvam:9", "sarvam:0"],
                            ["sarvam:9", "sarvam:5", "sarvam:2", "sarvam:0"])
        self.assertEqual(m, {"sarvam:9": "speaker_0", "sarvam:2": "speaker_1", "sarvam:0": "speaker_2",
                             "sarvam:5": "speaker_3"})

    def test_sarvam_ids_rendered_like_scribe_and_fallback_is_its_own_speaker(self):
        words = [W(0.0, 0.5, "speaker_0"), W(1.0, 1.5, "speaker_0"), W(3.0, 3.5, "speaker_1"),
                 W(10.0, 10.5, "speaker_0")]
        tr = tr_of(words, [(0, 0), (1, 1), (2, 2), (3, 3)])
        doc = SL.build(tr, "sarvam", raw(seg(0.0, 0.6, "11"), seg(0.9, 1.6, "4"), seg(2.9, 3.6, "11")))
        self.assertEqual([r["speaker"] for r in doc["lines"]], ["speaker_0", "speaker_1", "speaker_0", "speaker_2"])
        self.assertEqual([(m["label"], m["source_id"]) for m in doc["mapping"]],
                         [("speaker_0", "sarvam:11"), ("speaker_1", "sarvam:4"), ("speaker_2", "scribe:speaker_0")])
        self.assertEqual(doc["word_via"], {"overlap": 3, "scribe_fallback": 1})
        self.assertTrue(all(r["speaker"].startswith("speaker_") for r in doc["lines"]))
        self.assertEqual([r["scribe"] for r in doc["lines"]], ["speaker_0", "speaker_0", "speaker_1", "speaker_0"])

    def test_scribe_source_is_the_identity(self):
        words = [W(0, 1, "speaker_3"), W(1, 2, "speaker_1")]
        doc = SL.build(tr_of(words, [(0, 0), (1, 1)]), "scribe")
        self.assertEqual([r["speaker"] for r in doc["lines"]], ["speaker_3", "speaker_1"])
        self.assertEqual(doc["words"], ["speaker_3", "speaker_1"])


class LlmFix(unittest.TestCase):
    def fix(self, lines):
        return {"schema": "speaker_fix/2", "lines": lines}

    def test_cast_keys_not_names_unknown_keeps_scribe(self):
        words = [W(0, 1, "speaker_0"), W(1, 2, "speaker_0"), W(2, 3, "speaker_1"), W(3, 4, "speaker_1")]
        tr = tr_of(words, [(0, 0), (1, 1), (2, 2), (3, 3)])
        fx = self.fix([{"id": 0, "words": [0, 0], "start": 0, "key": "D", "name": "Surya"},
                       {"id": 1, "words": [1, 1], "start": 1, "key": "A", "name": "Meghana"},
                       {"id": 2, "words": [2, 2], "start": 2, "key": "D", "name": "Surya"},
                       {"id": 3, "words": [3, 3], "start": 3, "key": "X", "name": None}])
        doc = SL.build(tr, "llm_fix", fx)
        self.assertEqual([r["speaker"] for r in doc["lines"]], ["speaker_0", "speaker_1", "speaker_0", "speaker_2"])
        self.assertEqual([m["source_id"] for m in doc["mapping"]], ["llm_fix:D", "llm_fix:A", "scribe:speaker_1"])
        import json
        self.assertNotIn("Surya", json.dumps(doc))
        self.assertNotIn("Meghana", json.dumps(doc))

    def test_unmatched_fix_line_keeps_scribe(self):
        words = [W(0, 1, "speaker_0"), W(1, 2, "speaker_1")]
        tr = tr_of(words, [(0, 0), (1, 1)])
        fx = self.fix([{"id": 0, "words": [0, 0], "start": 0, "key": "B"},
                       {"id": 1, "words": [1, 1], "start": 7.5, "key": "B"}])     # start differs: stale
        doc = SL.build(tr, "llm_fix", fx)
        self.assertEqual(doc["stats"]["unmatched_lines"], 1)
        self.assertEqual([m["source_id"] for m in doc["mapping"]], ["llm_fix:B", "scribe:speaker_1"])

    def test_wrong_schema_raises(self):
        with self.assertRaises(ValueError):
            SL.build(tr_of([W(0, 1)], [(0, 0)]), "llm_fix", {"schema": "speaker_fix/1", "lines": []})


class Apply(unittest.TestCase):
    def setUp(self):
        self.words = [W(0.0, 0.5, "speaker_0"), W(1.0, 1.5, "speaker_0"), W(3.0, 3.5, "speaker_1")]
        self.tr = tr_of(self.words, [(0, 0), (1, 1), (2, 2)])
        self.layer = {**SL.build(self.tr, "sarvam", raw(seg(0.0, 0.6, "1"), seg(0.9, 1.6, "2"),
                                                        seg(2.9, 3.6, "1"))), "video_id": "v"}

    def lines(self):
        rows = [{"id": f"L{k + 1:04d}", "kind": "speech", "words": list(s["words"]), "start": s["start"],
                 "speaker": s["speaker"], "text": "t"} for k, s in enumerate(self.tr["sentences"])]
        rows.insert(1, {"id": "Lev", "kind": "event", "start": 0.7, "end": 0.8, "speaker": "event", "text": "[నవ్వు]"})
        return rows

    def test_relabels_lines_and_words_never_events(self):
        rows, words = self.lines(), [dict(w) for w in self.words]
        self.assertEqual(SL.apply(rows, words, self.layer), 3)
        self.assertEqual([r["speaker"] for r in rows], ["speaker_0", "event", "speaker_1", "speaker_0"])
        self.assertEqual([w["speaker"] for w in words], ["speaker_0", "speaker_1", "speaker_0"])
        self.assertEqual([w["speaker"] for w in self.words], ["speaker_0", "speaker_0", "speaker_1"])   # source kept

    def test_stale_layer_raises(self):
        rows = self.lines()
        rows[0]["words"] = [0, 1]
        with self.assertRaises(ValueError):
            SL.apply(rows, None, self.layer)
        with self.assertRaises(ValueError):
            SL.apply(self.lines(), self.words[:2], self.layer)

    def test_public_record_has_the_mapping_no_word_data(self):
        pub = SL.public(self.layer)
        self.assertEqual([m["label"] for m in pub["mapping"]], ["speaker_0", "speaker_1"])
        self.assertNotIn("words", pub)
        self.assertNotIn("lines", pub)

    def test_run_source(self):
        self.assertEqual(SL.run_source({}), "scribe")
        self.assertEqual(SL.run_source({"pipeline": {"config": {"speaker_source": "llm_fix"}}}), "llm_fix")
        self.assertEqual(SL.run_source({"speaker_layer": {"source": "sarvam"}}), "sarvam")
        self.assertIsNone(SL.for_run({"video_id": "v"}))


class RealFile(unittest.TestCase):
    """Surya Ep1 has a Sarvam raw file and a speaker fix in the repo: build both (free)."""

    def test_surya_layers_build_and_cover_every_line(self):
        vid = "p3XTZlZ6-xw"
        if not (SL.sarvam_raw_path(vid).exists() and SL.speaker_fix_path(vid).exists()
                and SL.transcript_path(vid).exists()):
            self.skipTest("Surya files not present")
        import json
        tr = json.loads(SL.transcript_path(vid).read_text(encoding="utf-8"))
        for src, p in (("sarvam", SL.sarvam_raw_path(vid)), ("llm_fix", SL.speaker_fix_path(vid))):
            doc = SL.build(tr, src, json.loads(p.read_text(encoding="utf-8")))
            self.assertEqual(len(doc["lines"]), len(tr["sentences"]))
            self.assertEqual(len(doc["words"]), len(tr["word_timestamps"]))
            self.assertEqual([m["label"] for m in doc["mapping"]], [f"speaker_{k}" for k in range(doc["n_speakers"])])
            self.assertEqual(doc["lines"][0]["speaker"], "speaker_0")


if __name__ == "__main__":
    unittest.main()
