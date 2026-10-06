"""Scribe keyterms: request fields, no duration-recovery on keyterm runs, and the series glossary recipe.
Never calls the network: requests.post / recover / credits are patched."""
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests
import urllib3.filepost

import transcribe_scribe as ts
from shotvi.segment import glossary as G

from .helpers import build

ORIGINAL_FIELDS = {          # the request before keyterms support existed, verbatim
    "model_id": "scribe_v2",
    "diarize": "true",
    "timestamps_granularity": "word",
    "tag_audio_events": "true",
    "temperature": "0",
    "seed": "0",
}


def body(data):
    with mock.patch.object(urllib3.filepost, "choose_boundary", return_value="BOUNDARY"):
        return requests.Request("POST", ts.API, files={"file": ("a.mp3", b"xyz", "audio/mpeg")},
                                data=data).prepare().body


class TestRequestFields(unittest.TestCase):
    def test_no_keyterms_is_byte_identical(self):
        self.assertEqual(ts.request_fields(), ORIGINAL_FIELDS)
        self.assertEqual(ts.request_fields(keyterms=None), ORIGINAL_FIELDS)
        self.assertEqual(ts.request_fields(keyterms=[]), ORIGINAL_FIELDS)
        self.assertEqual(body(ts.request_fields()), body(dict(ORIGINAL_FIELDS)))

    def test_keyterms_are_repeated_form_parts(self):
        b = body(ts.request_fields(keyterms=["అమృత", "full meals"])).decode("utf-8")
        self.assertEqual(b.count('name="keyterms"'), 2)
        self.assertIn("అమృత", b)
        self.assertIn("full meals", b)
        for k in ORIGINAL_FIELDS:
            self.assertIn(f'name="{k}"', b)


class TestNoRecoveryWithKeyterms(unittest.TestCase):
    def _call(self, keyterms):
        with tempfile.TemporaryDirectory() as d:
            mp3 = Path(d) / "x.upload.mp3"
            mp3.write_bytes(b"\0" * 100)
            fake_old = {"text": "ORIGINAL", "audio_duration_secs": 1300.0}
            with mock.patch.object(ts.requests, "post", side_effect=requests.ConnectionError("down")), \
                    mock.patch.object(ts, "recover", return_value=fake_old) as rec, \
                    mock.patch.object(ts, "api_key", return_value="k"), \
                    mock.patch.object(ts.time, "sleep"):
                try:
                    got = ts.call(mp3, 1300.0, log=lambda *a: None, keyterms=keyterms)
                except SystemExit as e:
                    got = e
            return got, rec

    def test_keyterms_run_never_recovers_an_old_transcript(self):
        got, rec = self._call(["అమృత"])
        rec.assert_not_called()
        self.assertIsInstance(got, SystemExit)     # three failed uploads, nothing faked

    def test_plain_run_still_recovers(self):
        got, rec = self._call(None)
        rec.assert_called()
        self.assertEqual(got["text"], "ORIGINAL")


class TestTranscribeRecordsKeyterms(unittest.TestCase):
    def test_output_has_keyterms_and_surcharge(self):
        raw = {"language_code": "tel", "text": "a b", "transcription_id": "t1",
               "words": [{"text": "a", "start": 0, "end": .2, "type": "word", "speaker_id": "s0", "logprob": -.1},
                         {"text": "b", "start": .3, "end": .5, "type": "word", "speaker_id": "s0"}]}
        logs = []
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "x_audio.wav"
            wav.write_bytes(b"")
            wav.with_suffix(".upload.mp3").write_bytes(b"")
            for kt in (["అమృత"], None):
                with mock.patch.object(ts, "duration_s", return_value=600.0), \
                        mock.patch.object(ts, "credits_left", return_value=(10_000, 10_000)), \
                        mock.patch.object(ts, "call", return_value=raw) as c:
                    out = ts.transcribe(wav, log=logs.append, keyterms=kt)
                self.assertEqual(c.call_args.kwargs["keyterms"], kt)
                if kt:
                    self.assertEqual(out["keyterms"], kt)
                    self.assertTrue(any("needs ~800 credits" in m for m in logs))   # 10 min * 66.7 * 1.2
                else:
                    self.assertNotIn("keyterms", out)
                logs.clear()

    def test_load_keyterms(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "g.json"
            p.write_text(json.dumps({"keyterms": ["a  b", "a b", "c"]}), encoding="utf-8")
            self.assertEqual(ts.load_keyterms(p), ["a b", "c"])
            p.write_text(json.dumps({"terms": [{"term": "x"}]}), encoding="utf-8")
            self.assertEqual(ts.load_keyterms(p), ["x"])
            for bad in (["one two three four five six"], ["x" * 50], ["a[b]"]):
                p.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(SystemExit):
                    ts.load_keyterms(p)


def ep_transcript(text_rows, logprob=math.log(0.95)):
    d = build([("s0", t, 1.0) for t in text_rows], logprob=logprob)
    sents, i = [], 0
    for k, t in enumerate(text_rows):
        n = len(t.split())
        sents.append({"id": k, "text": t, "words": [i, i + n - 1], "speaker": "s0"})
        i += n
    d["sentences"] = sents
    return d


BACKGROUND = [ep_transcript(["నేను ఈ రోజు ఆఫీస్ కి వెళ్ళాను okay", "మీరు ఏం చేస్తున్నారు చెప్పండి"] * 200)]


class TestGlossary(unittest.TestCase):
    def test_clean_term_limits(self):
        self.assertEqual(G.clean_term(" Couple  Goals! "), "couple goals")
        self.assertIsNone(G.clean_term("a b c d e f"))
        self.assertIsNone(G.clean_term("x" * 50))
        self.assertEqual(G.clean_term("a{b}"), "ab")
        self.assertIsNone(G.clean_term("[ ]"))

    def test_recipe(self):
        tr = ep_transcript([
            "అమృత గారు మీరు చెప్పండి",
            "గోంగూర పచ్చడి చాలా బాగుంది",
            "గోంగూర పచ్చడి మళ్ళీ చేయండి",
            "నేను ఈ రోజు ఆఫీస్ కి వెళ్ళాను",
            "కూరంటా కూరంటా",
            "ఉయ్యమ్మో వద్దు",
            "khichdi తిన్నాను",
        ])
        tr["word_timestamps"][-2]["logprob"] = math.log(0.95)
        # word 16 = "ఉయ్యమ్మో": a verified correction
        idx = [w["word"] for w in tr["word_timestamps"]].index("ఉయ్యమ్మో")
        corr = {"corrections": [{"start_word": idx, "end_word": idx, "heard": "ఉయ్యమ్మో", "corrected": "ఒరియమ్మో",
                                 "applied": True},
                                {"start_word": 0, "end_word": 0, "heard": "అమృత", "corrected": "x", "applied": False}]}
        meta = {"title": "My Show || Episode - 1 || Some Actor || Telugu Web Series || Chan", "channel": "Chan"}
        g = G.build_glossary([G.Episode("e1", tr, corr, meta)], BACKGROUND,
                             manual=[("గోరింటాకు", "known")], drop=[("కూరంటా", "misheard")])
        terms = {t["term"]: t for t in g["terms"]}
        self.assertEqual(terms["అమృత"]["kind"], "name")                  # before గారు
        self.assertEqual(terms["ఒరియమ్మో"]["kind"], "correction")
        self.assertNotIn("ఉయ్యమ్మో", terms)                               # the misheard form never
        self.assertIn("ఉయ్యమ్మో", g["excluded_misheard"])
        self.assertIn("గోంగూర పచ్చడి", terms)                              # recurring phrase
        self.assertIn("గోంగూర", terms)
        self.assertIn("my show", terms)
        self.assertIn("some actor", terms)
        self.assertFalse(any("episode" in t or t == "1" or "chan" == t for t in terms))
        for common in ("నేను", "ఆఫీస్", "వెళ్ళాను", "okay", "చెప్పండి"):
            self.assertNotIn(common, terms)                                # general Telugu (background)
        self.assertIn("khichdi", terms)                                    # English absent from background
        self.assertEqual(terms["గోరింటాకు"]["kind"], "manual")
        self.assertNotIn("కూరంటా", terms)
        self.assertEqual(g["dropped_by_hand"][0]["term"], "కూరంటా")
        self.assertEqual(G.keyterms(g), [t["term"] for t in g["terms"]])

    def test_low_confidence_terms_excluded_and_cap(self):
        tr = ep_transcript(["పుల్లారెడ్డి స్వీట్లు", "పుల్లారెడ్డి స్వీట్లు"], logprob=math.log(0.3))
        g = G.build_glossary([G.Episode("e1", tr)], BACKGROUND)
        self.assertEqual(g["terms"], [])
        tr = ep_transcript(["పుల్లారెడ్డి స్వీట్లు", "పుల్లారెడ్డి స్వీట్లు"])
        g = G.build_glossary([G.Episode("e1", tr)], BACKGROUND, max_terms=1)
        self.assertEqual(g["n_terms"], 1)
        self.assertEqual(g["n_dropped_by_cap"], 2)

    def test_needs_an_episode(self):
        with self.assertRaises(ValueError):
            G.build_glossary([], BACKGROUND)


if __name__ == "__main__":
    unittest.main()
