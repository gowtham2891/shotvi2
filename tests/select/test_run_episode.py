"""shotvi/run_episode.py offline: every subprocess and provider-facing helper is faked (no yt-dlp, ffmpeg,
ElevenLabs or Gemini). Covers: idempotence (a second run makes no call), --dry-run never starts a paid step,
the ledger guard refuses a paid step, shots.json keeps the existing format, id parsing."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from shotvi import run_episode as re_


VID = "abcdefghijk"
PAID_SCRIPTS = ("download_youtube.py", "transcribe_scribe.py")   # network / ElevenLabs
PAID_MODULES = ("shotvi.segment.asr_fix", "shotvi.select.webseries")   # Gemini unless --dry-run


def is_paid_call(args) -> bool:
    s = [str(a) for a in args]
    if any(x in s for x in PAID_SCRIPTS):
        return True
    return any(x in s for x in PAID_MODULES) and "--dry-run" not in s


class FakeWorld:
    """Fakes run_cmd & co. on the module; each fake creates the step's output like the real tool would."""

    def __init__(self, root: Path, ledger=1000.0, asr_inr=2.0, sel_guard=15.0, sel_expected=12.0):
        self.root, self.ledger = root, ledger
        self.asr_inr, self.sel_guard, self.sel_expected = asr_inr, sel_guard, sel_expected
        self.calls = []

    def run_cmd(self, args, capture=False):
        self.calls.append([str(a) for a in args])
        s = self.calls[-1]
        p = re_.Paths(VID, self.root / "runs" / f"ep_{VID}")
        if "download_youtube.py" in s:
            p.src.mkdir(parents=True, exist_ok=True)
            p.mp4.write_bytes(b"x")
            p.wav.write_bytes(b"x")
        elif "transcribe_scribe.py" in s:
            p.transcript.write_text(json.dumps({"word_timestamps": [], "audio_duration_secs": 600}), encoding="utf-8")
        elif "shotvi.segment" in s:
            p.segments.parent.mkdir(parents=True, exist_ok=True)
            p.segments.write_text("{}", encoding="utf-8")
        elif "shotvi.segment.asr_fix" in s:
            if "--dry-run" in s:
                return f"{VID}: 100 words\n  projected total: ₹{self.asr_inr:.2f}\n"
            p.corrections.parent.mkdir(parents=True, exist_ok=True)
            p.corrections.write_text("{}", encoding="utf-8")
            self.ledger += self.asr_inr
        elif "shotvi.select.webseries" in s:
            if "--dry-run" in s:
                return (f"DRY RUN total: ₹{self.sel_expected:.2f} expected (guard projection ₹{self.sel_guard:.2f}); "
                        f"ledger would go ₹{self.ledger:.2f} -> ~₹{self.ledger + self.sel_expected:.2f}\n")
            p.clips.parent.mkdir(parents=True, exist_ok=True)
            p.clips.write_text(json.dumps({
                "kind": "proposals", "video_id": VID,
                "proposals": [{"id": "w01", "tier": "recommended"}, {"id": "w02", "tier": "recommended"},
                              {"id": "w03", "tier": "worth_a_look"}],
                "rejected": [{"id": "w04"}],
                "episode_verdict": {"verdict": "thin", "strong_clips": 2, "worth_a_look": 1, "note": ""}}),
                encoding="utf-8")
            self.ledger += self.sel_expected
        elif "tools/preview_clips.py" in s:
            p.review.parent.mkdir(parents=True, exist_ok=True)
            p.review.write_text("<html></html>", encoding="utf-8")
        return ""

    def write_meta(self, url, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = {"id": VID, "title": "Test Ep 1", "channel": "c", "channel_url": "u", "duration": 600,
                "upload_date": "20260101", "description": "", "chapters": None, "view_count": 1}
        path.write_text(json.dumps(meta), encoding="utf-8")
        return meta

    def scene_cuts(self, mp4, log_path):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("fake", encoding="utf-8")
        return [7.341, 10.677, 16.225]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shotvi_run_ep_"))
        self.world = FakeWorld(self.tmp)
        self._saved = {k: getattr(re_, k) for k in
                       ("ROOT", "run_cmd", "ledger_inr", "spend_cap_inr", "media_duration_s", "write_youtube_meta",
                        "scene_cuts")}
        re_.ROOT = self.tmp
        re_.run_cmd = self.world.run_cmd
        re_.ledger_inr = lambda: self.world.ledger
        re_.spend_cap_inr = lambda: 1350.0
        re_.media_duration_s = lambda path: 600.0
        re_.write_youtube_meta = self.world.write_meta
        re_.scene_cuts = self.world.scene_cuts

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(re_, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = re_.main([VID, *args])
        return code, buf.getvalue()

    def paths(self):
        return re_.Paths(VID, self.tmp / "runs" / f"ep_{VID}")


class FullRun(Base):
    def test_runs_every_step_then_is_idempotent(self):
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        p = self.paths()
        for f in (p.mp4, p.wav, p.meta, p.transcript, p.shots, p.scene_log, p.segments, p.corrections, p.clips,
                  p.review):
            self.assertTrue(f.exists(), f"{f} missing\n{out}")
        shots = json.loads(p.shots.read_text(encoding="utf-8"))
        self.assertEqual(list(shots), ["video_id", "method", "duration", "cuts", "shots"])
        self.assertEqual(shots["cuts"], [7.341, 10.677, 16.225])
        self.assertEqual(shots["shots"][0], {"id": 0, "start": 0.0, "end": 7.341, "dur": 7.341})
        self.assertEqual(shots["shots"][-1]["end"], 600.0)
        self.assertEqual(len(shots["shots"]), len(shots["cuts"]) + 1)
        self.assertIn("recommended: 2, worth_a_look: 1; rejected: 1", out)
        self.assertIn("verdict:   THIN", out)
        self.assertIn("Test Ep 1", out)
        self.assertIn("₹14.00 this run", out)            # 2 (asr_fix) + 12 (select) on the fake ledger
        paid = [c for c in self.world.calls if is_paid_call(c)]
        self.assertEqual([c[1] if c[1] != "-m" else c[2] for c in paid],
                         ["download_youtube.py", "transcribe_scribe.py", "shotvi.segment.asr_fix",
                          "shotvi.select.webseries"])
        # the guard limit is passed down to both Gemini steps
        for c in paid[2:]:
            self.assertIn("--max-ledger-inr", c)
        self.assertIn("--no-retry", paid[3])
        self.assertIn("--intro", next(c for c in self.world.calls if "shotvi.segment" in c))

        n = len(self.world.calls)
        code, out2 = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.world.calls), n, "second run must not call anything")
        self.assertEqual(out2.count("done already"), 7, out2)
        self.assertIn("₹0.00 this run", out2)

    def test_skip_asr_fix(self):
        code, out = self.run_main("--skip-asr-fix")
        self.assertEqual(code, 0)
        self.assertFalse(self.paths().corrections.exists())
        self.assertIn("skipped (--skip-asr-fix)", out)
        self.assertFalse(any("shotvi.segment.asr_fix" in c for c in self.world.calls))

    def test_preview_rebuilt_when_clips_are_newer(self):
        self.run_main()
        p = self.paths()
        os.utime(p.review, (1, 1))                 # page older than the clip document
        n = len(self.world.calls)
        self.run_main()
        self.assertEqual(len(self.world.calls), n + 1)
        self.assertIn("tools/preview_clips.py", self.world.calls[-1])


class DryRun(Base):
    def test_dry_run_from_nothing_makes_no_paid_call(self):
        code, out = self.run_main("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.world.calls, [], "nothing exists yet, so even the sub-tools' dry runs can't run")
        self.assertFalse((self.tmp / "scribe_dataset").exists())
        self.assertEqual(out.count("would run"), 7, out)
        self.assertIn("~₹17.00", out)                 # 2 + 15 fallback projections
        self.assertIn("credits: unknown", out)

    def test_dry_run_with_transcript_uses_the_tools_dry_runs(self):
        p = self.paths()
        self.world.run_cmd(["x", "download_youtube.py"])
        self.world.write_meta("", p.meta)
        self.world.run_cmd(["x", "transcribe_scribe.py"])
        p.shots.parent.mkdir(parents=True)
        p.shots.write_text(json.dumps(re_.shots_doc(VID, [1.0], 600.0)), encoding="utf-8")
        self.world.calls.clear()
        code, out = self.run_main("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.world.calls)
        for c in self.world.calls:
            self.assertIn("--dry-run", c, c)
            self.assertFalse(is_paid_call(c), c)
        self.assertIn("projected ₹2.00 (asr_fix --dry-run)", out)
        self.assertIn("₹12.00 expected, ₹15.00 worst case", out)
        self.assertIn("~₹17.00", out)
        self.assertFalse(p.segments.exists(), "dry run must not run the free steps either")

    def test_dry_run_all_done_reports_zero(self):
        self.run_main()
        self.world.calls.clear()
        code, out = self.run_main("--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(self.world.calls, [])
        self.assertEqual(out.count("done already"), 7, out)
        self.assertIn("would bill ~₹0.00", out)


class Guard(Base):
    def test_refuses_paid_step_over_the_limit(self):
        self.world.ledger = 1340.0            # + ₹15 select projection > 1350
        code, out = self.run_main("--max-ledger-inr", "1350")
        self.assertEqual(code, re_.EXIT_GUARD_REFUSED, out)
        p = self.paths()
        self.assertTrue(p.corrections.exists(), "asr_fix (₹2) fits and runs")   # 1340 + 2 <= 1350
        self.assertFalse(p.clips.exists())
        self.assertFalse(any("shotvi.select.webseries" in c and "--dry-run" not in c for c in self.world.calls))
        self.assertIn("REFUSED: select", out)
        self.assertIn("1342.00 + projected ₹15.00 > limit ₹1350.00", out)

    def test_default_limit_is_the_spend_cap(self):
        re_.spend_cap_inr = lambda: 10.0
        self.world.ledger = 9.0
        code, out = self.run_main("--skip-asr-fix")
        self.assertEqual(code, re_.EXIT_GUARD_REFUSED, out)
        self.assertIn("limit ₹10.00", out)

    def test_dry_run_flags_a_refusal_without_calling(self):
        self.world.ledger = 1340.0
        code, out = self.run_main("--dry-run", "--max-ledger-inr", "1350")
        self.assertEqual(code, 0)
        self.assertIn("the guard would REFUSE it", out)
        self.assertEqual(self.world.calls, [])

    def test_substep_budget_stop_is_reported(self):
        import subprocess
        inner = self.world.run_cmd

        def stop(args, capture=False):
            if "shotvi.select.webseries" in args and "--dry-run" not in args:
                self.world.calls.append(list(args))
                raise subprocess.CalledProcessError(2, args)
            return inner(args, capture)
        re_.run_cmd = stop
        code, out = self.run_main()
        self.assertEqual(code, re_.EXIT_BUDGET_STOP, out)
        self.assertIn("STOP: select", out)


class Ids(unittest.TestCase):
    def test_video_id_parsing(self):
        for s in ("epZ0lbXkgWA", "https://www.youtube.com/watch?v=epZ0lbXkgWA&t=10s", "https://youtu.be/epZ0lbXkgWA",
                  "https://youtube.com/shorts/epZ0lbXkgWA", "https://m.youtube.com/watch?v=epZ0lbXkgWA"):
            self.assertEqual(re_.video_id_of(s), "epZ0lbXkgWA", s)
        with self.assertRaises(SystemExit):
            re_.video_id_of("https://example.com/x")
        with self.assertRaises(SystemExit):
            re_.video_id_of("not-an-id")

    def test_shots_doc_drops_cuts_past_the_end(self):
        d = re_.shots_doc("v", [1.0, 2.0, 9.5], 9.0)
        self.assertEqual([s["end"] for s in d["shots"]], [1.0, 2.0, 9.0])
        self.assertEqual(d["method"], "ffmpeg select=gt(scene,0.25) on 320px frames")

    def test_parse_inr(self):
        self.assertEqual(re_.parse_inr("  projected total: ₹2.34\n", r"projected total: ₹\s*([0-9.]+)"), 2.34)
        self.assertIsNone(re_.parse_inr("nothing", r"₹([0-9.]+)"))


if __name__ == "__main__":
    unittest.main()
