"""Stage 3 edit step (founder, 2026-11-05): one call per found clip with the video of its stretch; times on the video's
clock; tighten / split / hold. Synthetic data, fake client (no network)."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from selection_v2 import stage3_edit as SE
from selection_v2 import stage3_find as S3
from selection_v2.config import load_config
from selection_v2.stage1_facts import fmt_ts
from tests.v2.synthetic import make_inputs
from tests.v2.test_stage1b import FakeClient, _cutter, _deleter, _uploader
from tests.v2.test_stage3_find import TestStage3OneStep, cfg, facts3, speech_ids


def found(f, w):
    sc = f["identity_pass"]["scenes"][0]; ids = speech_ids(f, sc)
    c = TestStage3OneStep().clip(f, ids[0], ids[-1])
    _, _, v = S3.validate_clip(c, f, w, 0, len(f["lines"]) - 1, 180)
    return {"id": "c01", "scene": sc["id"], "why": "w", **c, **v}, ids


class TestEdit(unittest.TestCase):
    def test_window_and_clock(self):
        f = facts3(); w = make_inputs().words
        clip, ids = found(f, w)
        w0, w1, lo, hi = SE.window(f, clip, 8, 15)
        self.assertLessEqual(w0, clip["start_time"]); self.assertGreaterEqual(w1, clip["pieces"][-1]["end"])
        txt = SE.call_text({**f}, clip, w0, w1, lo, hi)
        L = {r["id"]: r for r in f["lines"]}
        self.assertIn(f"{ids[0]} [{fmt_ts(L[ids[0]]['start'] - w0)}", txt)          # the video's clock
        self.assertIn("PART 3", txt)

    def test_validate_hold_and_split(self):
        f = facts3(); w = make_inputs().words
        clip, ids = found(f, w)
        w0, w1, lo, hi = SE.window(f, clip, 8, 15)
        L = {r["id"]: r for r in f["lines"]}
        a = TestStage3OneStep().clip(f, ids[0], ids[1]); a["end"]["hold_until"] = fmt_ts(L[ids[1]]["end"] - w0)
        b = TestStage3OneStep().clip(f, ids[2], ids[-1]); b["end"]["hold_until"] = ""
        errs, _, out = SE.validate({"split_why": "two", "clips": [a, b]}, f, w, w0, lo, hi, 180)
        self.assertEqual(errs, []); self.assertEqual(len(out), 2)
        self.assertAlmostEqual(out[0]["end"]["hold_until_episode"], L[ids[1]]["end"], delta=0.06)   # back on the episode clock
        self.assertTrue(SE.validate({"clips": []}, f, w, w0, lo, hi, 180)[0])
        bad = copy.deepcopy(a); bad["end"]["hold_until"] = "soon"
        self.assertTrue(SE.validate({"clips": [bad]}, f, w, w0, lo, hi, 180)[0])

    def test_run(self):
        f = facts3(); inputs = make_inputs()
        clip, ids = found(f, inputs.words)
        with tempfile.TemporaryDirectory() as td:
            inputs.paths = SimpleNamespace(run_dir=Path(td), mp4=Path(td) / "ep.mp4", title=lambda: "Synthetic")
            c = cfg(td); c["stage3_edit"] = load_config()["stage3_edit"]
            ans = TestStage3OneStep().clip(f, ids[0], ids[-1], drops=[{"first": ids[1], "last": ids[1], "why": "side talk"}])
            client = FakeClient([{"split_why": "", "clips": [ans]}])
            out = SE.edit_clips(inputs, f, c, [clip], client=client, cutter=_cutter, uploader=_uploader, deleter=_deleter)
            self.assertEqual(len(client.calls), 1); self.assertEqual(client.calls[0]["media"][0]["fps"], 2.0)
            e = out["clips"]["clips"][0]
            self.assertTrue(e["edited"]); self.assertEqual(e["id"], "c01"); self.assertLess(e["duration"], clip["duration"])
            self.assertIn("inr", SE.estimate(f, c, [clip], counter=len))
