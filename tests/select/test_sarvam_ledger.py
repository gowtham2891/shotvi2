"""Sarvam spend in the project ledger (2026-10-23), offline (no Sarvam call, no network): tools/transcribe_sarvam.py
records a job as a fixed ledger entry (source "sarvam", Rs = minutes x 0.59), refuses to start when ledger + its cost
would pass the limit (SHOTVI_SPEND_CAP_INR, or --max-ledger-inr), records a past job only once; run_episode's
opt-in --sarvam step sits right after transcribe and is skipped when the raw file exists."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shotvi import run_episode as re_
from shotvi.llm import spend

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import transcribe_sarvam as TS  # noqa: E402

VID = "abcdefghijk"


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ledger = self.root / "spend" / "llm_spend.json"
        (self.root / "scribe_dataset" / VID).mkdir(parents=True)
        (self.root / "scribe_dataset" / VID / f"{VID}_audio.wav").write_bytes(b"RIFF")
        self.patches = [mock.patch.object(spend, "LEDGER", self.ledger),
                        mock.patch.object(TS, "ROOT", self.root),
                        mock.patch.object(TS, "duration_s", return_value=20 * 60.0),
                        mock.patch.dict(os.environ, {"SHOTVI_SPEND_CAP_INR": "100", "SHOTVI_USD_INR": "90"})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def seed(self, inr: float):
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text(json.dumps({"total_usd": inr / 90.0, "calls": 1, "entries": [
            {"t": "x", "model": "gemini-3.8-flash", "in": 1, "out": 1, "usd": inr / 90.0, "source": "seed"}]}))

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = TS.main([VID, *args])
        return rc, out.getvalue()

    def test_record_job_is_a_fixed_sarvam_entry_in_rupees(self):
        self.seed(10.0)
        inr = TS.record_job(VID, "saaras:v4", "codemix", 20.0, "job j1")
        self.assertEqual(inr, 11.8)                                  # 20 min x Rs 0.59
        d = json.loads(self.ledger.read_text())
        e = d["entries"][-1]
        self.assertEqual((e["source"], e["model"], e["out"]), ("sarvam", "sarvam/saaras:v4", 0))
        self.assertIn(f"{VID} saaras:v4 codemix: 20.00 min x Rs 0.59 = Rs 11.80; job j1", e["note"])
        self.assertAlmostEqual(spend.total_inr(), 21.8, places=3)    # the cap now covers it
        self.assertTrue(TS.ledger_has(f"{VID} saaras:v4 codemix"))
        self.assertFalse(TS.ledger_has(f"{VID} saaras:v3 transcribe"))

    def test_refused_over_the_cap_before_any_sarvam_call(self):
        self.seed(95.0)                                              # + Rs 11.80 > cap Rs 100
        with mock.patch.dict(sys.modules, {"sarvamai": None}):       # importing the SDK would fail the test
            rc, out = self.run_main()
        self.assertEqual(rc, TS.EXIT_GUARD)
        self.assertIn("REFUSED: ledger Rs 95.00 + Sarvam Rs 11.80 = Rs 106.80 > limit Rs 100.00", out)
        self.assertEqual(json.loads(self.ledger.read_text())["calls"], 1)   # nothing recorded

    def test_max_ledger_inr_sets_the_limit(self):
        self.seed(95.0)
        rc, out = self.run_main("--dry-run", "--max-ledger-inr", "110")
        self.assertEqual(rc, 0)
        self.assertIn("<= limit Rs 110.00", out)
        self.assertIn("would run", out)
        rc, out = self.run_main("--dry-run")
        self.assertIn("would be REFUSED", out)
        self.assertEqual(json.loads(self.ledger.read_text())["calls"], 1)   # a dry run records nothing

    def test_past_job_recorded_once(self):
        self.seed(10.0)
        rc, out = self.run_main("--record-past-job", "--model", "saaras:v3", "--mode", "transcribe",
                                "--job-date", "2026-10-22")
        self.assertEqual(rc, 0)
        self.assertIn("recorded Rs 11.80", out)
        rc, out = self.run_main("--record-past-job", "--model", "saaras:v3", "--mode", "transcribe")
        self.assertIn("already in the ledger", out)
        d = json.loads(self.ledger.read_text())
        self.assertEqual(sum(1 for e in d["entries"] if e["source"] == "sarvam"), 1)
        self.assertIn("past job (2026-10-22)", d["entries"][-1]["note"])


class RunEpisodeStep(unittest.TestCase):
    def test_opt_in_after_transcribe(self):
        self.assertNotIn("sarvam", [s.name for s in re_.steps_for(False)])
        names = [s.name for s in re_.steps_for(False, True)]
        self.assertEqual(names[names.index("transcribe") + 1], "sarvam")
        names = [s.name for s in re_.steps_for(True, True)]
        self.assertEqual(names.index("speaker_fix"), names.index("asr_fix") + 1)
        self.assertEqual(names[names.index("transcribe") + 1], "sarvam")

    def test_skipped_when_the_raw_file_exists_and_priced_per_minute(self):
        with tempfile.TemporaryDirectory() as t:
            with mock.patch.object(re_, "ROOT", Path(t)):
                p = re_.Paths(VID, Path(t) / "runs" / "x")
                st = re_.Sarvam()
                self.assertFalse(st.done(p))
                p.src.mkdir(parents=True)
                p.wav.write_bytes(b"RIFF")
                ctx = re_.Ctx(p, "u", True, 2000.0, False, 0.0, sarvam=True)
                with mock.patch.object(re_, "media_duration_s", return_value=30 * 60.0):
                    inr, note = st.project(ctx)
                self.assertEqual(inr, 17.7)
                self.assertIn("30.0 min x Rs 0.59", note)
                p.sarvam_raw.write_text("{}")
                self.assertTrue(st.done(p))

    def test_run_passes_the_limit_to_the_script(self):
        calls = []
        with tempfile.TemporaryDirectory() as t, mock.patch.object(re_, "run_cmd", side_effect=lambda a, **k: calls.append(a)):
            p = re_.Paths(VID, Path(t))
            re_.Sarvam().run(re_.Ctx(p, "u", False, 1850.0, False, 0.0, sarvam=True))
        self.assertIn("tools/transcribe_sarvam.py", calls[0])
        self.assertEqual(calls[0][-2:], ["--max-ledger-inr", "1850"])


if __name__ == "__main__":
    unittest.main()
