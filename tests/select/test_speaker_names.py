"""Speaker names in the web-series prompts (opt-in ``speaker_names``): off is byte-identical to before the option
existed, names replace only the rendered label, lines without a name keep Scribe's label, line ids / times / the
``speaker`` field never change; run_episode --speaker-fix wiring. Offline: no provider call of any kind."""
import contextlib
import copy
import io
import json
import unittest
from pathlib import Path

from shotvi.select import lines as L
from shotvi.select import webseries as W
from shotvi.select.lines import build_lines, clean_name, render, speaker_name_map

ROOT = Path(__file__).resolve().parents[2]
REAL = "fjGrX8aBVAc"     # an episode with runs/speaker_fix/<id>.json (Love Brew Ep6)


def render_before_names(lines, tags=None):
    """Verbatim copy of lines.render before the names option (2026-10-13): the reference for 'off'."""
    out = []
    for r in lines:
        tag = f" <{tags[r['id']]}>" if tags and r["id"] in tags else ""
        who = {"event": "(audio event)", "shot": "(shot, no dialogue)"}.get(r["kind"], f"[{r['speaker']}]")
        out.append(f"{r['id']} [{L.fmt(r['start'])}]{tag} {who}" + (f": {r['text']}" if r["text"] else ""))
    return "\n".join(out)


def synthetic():
    words = ["ఏం", "చేశావు?", "పప్పు", "చేశాను.", "సరే", "అయితే."]
    ws = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4, "speaker": "speaker_0", "logprob": 0.0}
          for i, w in enumerate(words)]
    sents = [{"id": 0, "text": "ఏం చేశావు?", "start": 0.0, "end": 0.9, "speaker": "speaker_0", "words": [0, 1]},
             {"id": 1, "text": "పప్పు చేశాను.", "start": 1.0, "end": 1.9, "speaker": "speaker_0", "words": [2, 3]},
             {"id": 2, "text": "సరే అయితే.", "start": 2.0, "end": 2.9, "speaker": "speaker_1", "words": [4, 5]}]
    return {"word_timestamps": ws, "sentences": sents, "audio_events": [{"text": "[నవ్వు]", "start": 0.95, "end": 1.0}]}


def fix_doc(names=("Prasad", "Vaishu", None), words=([0, 1], [2, 3], [4, 5]), starts=(0.0, 1.0, 2.0)):
    return {"schema": "speaker_fix/2", "cast": [],
            "lines": [{"id": i, "start": s, "words": list(w), "name": n, "key": "A" if n else None}
                      for i, (n, w, s) in enumerate(zip(names, words, starts))]}


class Render(unittest.TestCase):
    def test_off_is_byte_identical(self):
        lines = build_lines("v", transcript=synthetic())
        tags = {"L0002": "lift-ok"}
        self.assertEqual(render(lines, tags), render_before_names(lines, tags))
        self.assertEqual(render(lines, tags, None), render_before_names(lines, tags))
        self.assertEqual(render(lines, tags, {}), render_before_names(lines, tags))

    def test_names_replace_only_the_label_and_missing_names_fall_back(self):
        lines = build_lines("v", transcript=synthetic())
        before = copy.deepcopy(lines)
        names = speaker_name_map("v", lines, fix_doc())
        self.assertEqual(lines, before, "line dicts (ids, times, speaker) must not change")
        # L0001 speech, L0002 laughter event (0.95), L0003 speech, L0004 speech with no corrected name
        self.assertEqual([r["id"] for r in lines], ["L0001", "L0002", "L0003", "L0004"])
        self.assertEqual(names, {"L0001": "Prasad", "L0003": "Vaishu"})
        on, off = render(lines, None, names).split("\n"), render(lines).split("\n")
        self.assertEqual(on[0], "L0001 [0:00.0] [Prasad]: ఏం చేశావు?")
        self.assertEqual(on[1], off[1])                                   # event line untouched
        self.assertEqual(on[2], "L0003 [0:01.0] [Vaishu]: పప్పు చేశాను.")
        self.assertEqual(on[3], off[3])                                   # no name: Scribe's label
        self.assertIn("[speaker_1]", on[3])
        for a, b in zip(on, off):                                         # same ids and times, line for line
            self.assertEqual(a.split("]")[0], b.split("]")[0])

    def test_stale_layer_matches_nothing_and_warns(self):
        lines = build_lines("v", transcript=synthetic())
        msgs = []
        names = speaker_name_map("v", lines, fix_doc(words=([0, 0], [2, 3], [4, 5]), starts=(0.0, 1.5, 2.0)),
                                 log=msgs.append)
        self.assertEqual(names, {})          # line 0: other word range; line 1: other start; line 2: no name
        self.assertTrue(msgs and "2 speech lines" in msgs[0], msgs)

    def test_missing_layer_and_bad_schema(self):
        lines = build_lines("v", transcript=synthetic())
        saved = L.speaker_fix_path
        L.speaker_fix_path = lambda vid: ROOT / "runs" / "speaker_fix" / "__no_such_video__.json"
        try:
            with self.assertRaises(FileNotFoundError):
                speaker_name_map("v", lines)
        finally:
            L.speaker_fix_path = saved
        with self.assertRaises(ValueError):
            speaker_name_map("v", lines, {**fix_doc(), "schema": "speaker_fix/1"})

    def test_clean_name(self):
        self.assertEqual(clean_name("  Abhi's\n father "), "Abhi's father")
        self.assertEqual(clean_name("[boss]"), "(boss)")
        self.assertIsNone(clean_name(None))
        self.assertIsNone(clean_name("  "))


class PlayText(unittest.TestCase):
    def ep(self, names):
        tr = synthetic()
        lines = build_lines("v", transcript=tr)
        return W.Episode("v", lines, tr["word_timestamps"], [], 3.0, None, names)

    def clip(self):
        return {"video_duration": 3.0, "time_segments": [{"kind": "scene", "start": 0.0, "end": 3.0}],
                "cold_open": None, "cuts": [], "end": {"hold_s": 0.5}}

    def test_review_text_uses_names_only_when_set(self):
        off = W.play_text(self.ep(None), "w01", {"purpose": "standalone"}, self.clip())
        on = W.play_text(self.ep({"L0001": "Prasad"}), "w01", {"purpose": "standalone"}, self.clip())
        self.assertIn("L0001 [speaker_0]: ఏం చేశావు?", off)
        self.assertIn("L0001 [Prasad]: ఏం చేశావు?", on)
        self.assertEqual(on.replace("[Prasad]", "[speaker_0]"), off)

    def test_names_switch(self):
        ep = self.ep({"L0001": "Prasad"})
        self.assertIsNone(W.names_of(ep, W.DEFAULTS))
        self.assertIsNone(W.names_of(ep, {"speaker_names": False}))
        self.assertEqual(W.names_of(ep, {"speaker_names": True}), {"L0001": "Prasad"})
        self.assertNotIn("speaker_names", W.DEFAULTS, "replayed configs must stay byte-identical")
        self.assertTrue(W.names_missing(self.ep(None), {"speaker_names": True}))
        self.assertFalse(W.names_missing(self.ep(None), W.DEFAULTS))

    def test_config_file(self):
        cfg = json.loads((ROOT / "shotvi/select/configs/ws_v1_speakers.json").read_text(encoding="utf-8"))
        merged = W.deep_merge(W.DEFAULTS, cfg)
        self.assertTrue(merged["speaker_names"])
        self.assertEqual({k: v for k, v in merged.items() if k != "speaker_names"}, W.DEFAULTS,
                         "ws_v1_speakers = v1 defaults + speaker_names only")


@unittest.skipUnless((ROOT / "runs" / "speaker_fix" / f"{REAL}.json").exists()
                     and (ROOT / "runs" / "video" / REAL / "shots.json").exists(), "real episode data not present")
class RealEpisode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.off, cls.ix, _, _ = W.load_episode(REAL, with_loudness=False)
        cls.on, _, _, _ = W.load_episode(REAL, with_loudness=False, speaker_names=True)
        cls.on_cfg = W.deep_merge(W.DEFAULTS, {"speaker_names": True})

    def test_lines_unchanged_and_names_loaded(self):
        self.assertIsNone(self.off.names)
        self.assertEqual(self.on.lines, self.off.lines)
        self.assertGreater(len(self.on.names), 0.9 * sum(r["kind"] == "speech" for r in self.on.lines))

    def test_flag_off_prompt_identical_even_with_names_loaded(self):
        self.assertEqual(W.discover_prompt(self.on, self.ix, W.DEFAULTS), W.discover_prompt(self.off, self.ix, W.DEFAULTS))

    def test_flag_on_changes_labels_only(self):
        s_off, u_off = W.discover_prompt(self.off, self.ix, W.DEFAULTS)
        s_on, u_on = W.discover_prompt(self.on, self.ix, self.on_cfg)
        self.assertEqual(s_on, s_off)
        a, b = u_off.split("\n"), u_on.split("\n")
        self.assertEqual(len(a), len(b))
        changed = 0
        for x, y in zip(a, b):
            if x != y:
                changed += 1
                self.assertTrue(x.startswith("L"), x)
                self.assertEqual(x.split(" [")[0:2], y.split(" [")[0:2])          # same id and time
                self.assertEqual(x.split("]: ", 1)[-1], y.split("]: ", 1)[-1])    # same text
        self.assertGreater(changed, 100)
        self.assertIn("[Abhi]", u_on)

    def test_flag_off_prompt_is_the_cached_v1_test_prompt(self):
        """The default prompt is the one runs/v1_test sent (its response is in the local cache)."""
        from shotvi.llm import Ledger, LLMClient
        if not (ROOT / "runs" / "v1_test" / f"{REAL}.json").exists():
            self.skipTest("runs/v1_test not present")
        llm = LLMClient(Ledger(), cache_dir=str(ROOT / "runs" / "cache"))
        llm.cache_only = True
        s, u = W.discover_prompt(self.off, self.ix, W.DEFAULTS)
        if not W.is_cached(llm, W.spec_of(W.DEFAULTS, "ws_discover"), s, u, W.discover_schema(W.DEFAULTS)):
            self.skipTest("v1_test discover response not in the local cache")
        # the names prompt is a different call (its own cache key; runs/v1_speakers has since cached it too)
        s2, u2 = W.discover_prompt(self.on, self.ix, self.on_cfg)
        spec, schema = W.spec_of(W.DEFAULTS, "ws_discover"), W.discover_schema(W.DEFAULTS)
        self.assertNotEqual(llm.cache_key(spec, s2, u2, schema), llm.cache_key(spec, s, u, schema))

    def test_calibration_renders_the_calibration_runs_labels(self):
        cal = W.calibration_of(ROOT / "runs" / "v1_test", REAL)
        if cal is None:
            self.skipTest("runs/v1_test not present")
        self.assertFalse(cal["speaker_names"])


class RunEpisodeSpeakerFix(unittest.TestCase):
    """--speaker-fix wiring in shotvi/run_episode.py on the fake world of test_run_episode."""

    def setUp(self):
        from tests.select import test_run_episode as T
        self.T = T
        self.case = T.Base()
        self.case.setUp()
        world, inner = self.case.world, self.case.world.run_cmd

        def run_cmd(args, capture=False):
            s = [str(a) for a in args]
            if "shotvi.segment.speaker_fix" in s:
                world.calls.append(s)
                if "--dry-run" in s:
                    return f"{T.VID}: 300 lines\nprojected total ₹4.00 (worst case ₹9.00); speaker_fix spent before ₹0\n"
                out = T.re_.Paths(T.VID, world.root).speakers
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text("{}", encoding="utf-8")
                world.ledger += 1.5
                return ""
            return inner(args, capture)
        T.re_.run_cmd = run_cmd

    def tearDown(self):
        self.case.tearDown()

    def run_main(self, *args):
        return self.case.run_main(*args)

    def test_off_by_default(self):
        code, out = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertNotIn("speaker_fix", out)
        self.assertIn("[7/7] preview", out)
        calls = self.case.world.calls
        self.assertFalse(any("shotvi.segment.speaker_fix" in c for c in calls))
        sel = [c for c in calls if "shotvi.select.webseries" in c]
        self.assertTrue(sel and all("--config" not in c for c in sel))

    def test_on_runs_the_step_before_select_with_the_speakers_config(self):
        code, out = self.run_main("--speaker-fix", "--max-ledger-inr", "1100")
        self.assertEqual(code, 0, out)
        calls = self.case.world.calls
        names = [c[2] for c in calls if len(c) > 2 and c[1] == "-m" and "--dry-run" not in c]
        self.assertEqual(names.index("shotvi.segment.speaker_fix"), names.index("shotvi.segment.asr_fix") + 1)
        self.assertLess(names.index("shotvi.segment.speaker_fix"), names.index("shotvi.select.webseries"))
        sf = next(c for c in calls if "shotvi.segment.speaker_fix" in c and "--dry-run" not in c)
        self.assertIn("--max-ledger-inr", sf)
        for c in calls:
            if "shotvi.select.webseries" in c:
                self.assertEqual(c[c.index("--config") + 1], "shotvi/select/configs/ws_v1_speakers.json")
        self.assertIn("[6/8] speaker_fix", out)
        self.assertIn("₹4.00 expected", out)
        self.assertIn("₹9.00 worst case", out)
        self.assertTrue(self.T.re_.Paths(self.T.VID, self.case.tmp).speakers.exists())

    def test_guard_refuses_on_the_worst_case(self):
        self.case.world.ledger = 1090.0          # asr_fix ₹2 fits; speaker_fix worst ₹9 does not (1092 + 9 > 1100)
        code, out = self.run_main("--speaker-fix", "--max-ledger-inr", "1100")
        self.assertEqual(code, self.T.re_.EXIT_GUARD_REFUSED, out)
        self.assertIn("REFUSED: speaker_fix", out)
        self.assertFalse(any("shotvi.segment.speaker_fix" in c and "--dry-run" not in c
                             for c in self.case.world.calls))

    def test_dry_run_calls_only_dry_runs(self):
        self.run_main("--speaker-fix")                       # everything done
        self.T.re_.Paths(self.T.VID, self.case.tmp).speakers.unlink()
        self.case.paths().clips.unlink()
        self.case.world.calls.clear()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = self.T.re_.main([self.T.VID, "--speaker-fix", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertTrue(self.case.world.calls)
        for c in self.case.world.calls:
            self.assertIn("--dry-run", c)
        self.assertIn("speaker_fix would run", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
