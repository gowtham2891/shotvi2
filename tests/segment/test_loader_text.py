import unittest

from shotvi.segment import TranscriptError, from_dict
from shotvi.segment.text import is_backchannel_text, latin_fraction, question_score

from .helpers import build


class LoaderTest(unittest.TestCase):
    def test_missing_logprobs_speakers_events(self):
        d = build([("A", "one two three", 0)], drop=("logprob",))
        del d["audio_events"]
        d["word_timestamps"][1].pop("speaker")
        tr = from_dict(d)
        self.assertFalse(tr.has_logprob)
        self.assertEqual(tr.events, [])
        self.assertEqual(tr.words[1].speaker, "speaker_unknown")
        self.assertTrue(any("logprob" in w for w in tr.warnings))

    def test_empty_raises(self):
        with self.assertRaises(TranscriptError):
            from_dict({"word_timestamps": []})
        with self.assertRaises(TranscriptError):
            from_dict({"text": "x"})

    def test_ghost_speaker_by_share(self):
        d = build([("A", "a " * 200, 0), ("C", "x", 0.2), ("B", "b " * 100, 0.2)])
        tr = from_dict(d)
        self.assertEqual(tr.speakers, ["A", "B"])
        self.assertEqual(tr.ghost_speakers, ["C"])

    def test_swapped_and_missing_times_keep_indices(self):
        d = build([("A", "one two three four", 0)])
        w = d["word_timestamps"]
        w[1]["start"], w[1]["end"] = w[1]["end"], w[1]["start"]
        w[2]["start"] = None
        tr = from_dict(d)
        self.assertEqual([x.i for x in tr.words], [0, 1, 2, 3])
        self.assertLessEqual(tr.words[1].start, tr.words[1].end)
        self.assertGreaterEqual(tr.words[2].start, 0)
        self.assertTrue(tr.warnings)

    def test_events_classified(self):
        d = build([("A", "a b", 0)], events=[("[నవ్వు]", 0.1, 0.2), ("[సెన్సార్డ్]", 0.3, 0.5),
                                             ("[పాట]", 1, 2), ("[చప్]", 2, 3)])
        kinds = [e.kind for e in from_dict(d).events]
        self.assertEqual(kinds, ["laughter", "censored", "music", "applause"])


class TextTest(unittest.TestCase):
    def test_backchannel_lexicon(self):
        for t in ["ఉమ్", "అవును అవును", "Right.", "makes sense", "ok sir", "యా యా"]:
            self.assertTrue(is_backchannel_text(t.split()), t)
        for t in ["sir", "it", "ఇది కావాలి", "right now we go"]:
            self.assertFalse(is_backchannel_text(t.split()), t)

    def test_question_score(self):
        yes = ["ఇంకేమైనా ఉన్నాయా?", "how was that stage sir", "మీరు ఏం చేశారు",
               "ఇంకెక్కడా conflict ఉంటదా sir", "do you think so"]
        no = ["So that's what I believe in", "Which is a blockbuster.", "నేను ప్రతిరోజూ తింటా",
              "ఎందుకంటే చాలా మంది", "యా నిజంగా అనిపిస్తాది నిజంగా", "So మర్చిపోతాను ఎవరు ఉన్నా"]
        for t in yes:
            self.assertGreaterEqual(question_score(t.split(), turn_final=True), 0.55, t)
        for t in no:
            self.assertLess(question_score(t.split(), turn_final=True), 0.55, t)

    def test_latin_fraction(self):
        self.assertEqual(latin_fraction("నేను happy గా ఉన్నా".split()), 0.25)
        self.assertIsNone(latin_fraction(["123", "..."]))


if __name__ == "__main__":
    unittest.main()
