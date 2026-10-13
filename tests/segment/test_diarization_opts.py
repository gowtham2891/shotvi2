"""Scribe diarization options (num_speakers / diarization_threshold): request fields, validation, no
duration-recovery on such runs. Never calls the network."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

import transcribe_scribe as ts

from .test_keyterms import ORIGINAL_FIELDS, body


class TestDiarizationFields(unittest.TestCase):
    def test_absent_is_byte_identical(self):
        self.assertEqual(ts.request_fields(num_speakers=None, diarization_threshold=None), ORIGINAL_FIELDS)
        self.assertEqual(body(ts.request_fields(num_speakers=None, diarization_threshold=None)),
                         body(dict(ORIGINAL_FIELDS)))
        self.assertEqual(ts.diarization_opts(), {})

    def test_num_speakers(self):
        f = ts.request_fields(num_speakers=5)
        self.assertEqual(f["num_speakers"], "5")
        self.assertNotIn("diarization_threshold", f)
        self.assertEqual({k: v for k, v in f.items() if k != "num_speakers"}, ORIGINAL_FIELDS)
        self.assertIn('name="num_speakers"', body(f).decode("utf-8"))

    def test_threshold(self):
        f = ts.request_fields(diarization_threshold=0.15)
        self.assertEqual(f["diarization_threshold"], "0.15")
        self.assertNotIn("num_speakers", f)

    def test_validation(self):
        for kw in ({"num_speakers": 0}, {"num_speakers": 33}, {"diarization_threshold": 0.05},
                   {"diarization_threshold": 0.5}, {"num_speakers": 3, "diarization_threshold": 0.2}):
            with self.assertRaises(SystemExit):
                ts.diarization_opts(**kw)


class TestNoRecoveryWithDiarizationOptions(unittest.TestCase):
    def _call(self, **kw):
        with tempfile.TemporaryDirectory() as d:
            mp3 = Path(d) / "x.upload.mp3"
            mp3.write_bytes(b"\0" * 100)
            fake_old = {"text": "ORIGINAL", "audio_duration_secs": 1300.0}
            with mock.patch.object(ts.requests, "post", side_effect=requests.ConnectionError("down")), \
                    mock.patch.object(ts, "recover", return_value=fake_old) as rec, \
                    mock.patch.object(ts, "api_key", return_value="k"), \
                    mock.patch.object(ts.time, "sleep"):
                try:
                    got = ts.call(mp3, 1300.0, log=lambda *a: None, **kw)
                except SystemExit as e:
                    got = e
            return got, rec

    def test_never_recovers(self):
        for kw in ({"num_speakers": 5}, {"diarization_threshold": 0.15}):
            got, rec = self._call(**kw)
            rec.assert_not_called()
            self.assertIsInstance(got, SystemExit)

    def test_plain_run_still_recovers(self):
        got, rec = self._call()
        rec.assert_called()
        self.assertEqual(got["text"], "ORIGINAL")

    def test_transcribe_records_options(self):
        raw = {"language_code": "tel", "text": "a", "transcription_id": "t9",
               "words": [{"text": "a", "start": 0, "end": .2, "type": "word", "speaker_id": "s0"}]}
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "x_audio.wav"
            wav.write_bytes(b"")
            wav.with_suffix(".upload.mp3").write_bytes(b"")
            with mock.patch.object(ts, "duration_s", return_value=600.0), \
                    mock.patch.object(ts, "credits_left", return_value=(10_000, 10_000)), \
                    mock.patch.object(ts, "call", return_value=raw) as c:
                out = ts.transcribe(wav, log=lambda *a: None, diarization_threshold=0.15)
                self.assertEqual(c.call_args.kwargs["diarization_threshold"], 0.15)
                self.assertEqual(out["diarization_options"], {"diarization_threshold": 0.15})
                self.assertEqual(out["transcription_id"], "t9")
                out = ts.transcribe(wav, log=lambda *a: None)
                self.assertNotIn("diarization_options", out)
                self.assertNotIn("diarization_threshold", c.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
