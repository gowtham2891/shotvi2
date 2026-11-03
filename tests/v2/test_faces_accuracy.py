import copy
import json
import tempfile
import unittest
from pathlib import Path

from selection_v2 import faces as FB, speaker_accuracy as SA, stage1_facts as S
from selection_v2.config import load_cast_lists, load_config
from tests.v2.synthetic import make_inputs
from tests.v2.test_stage1b import good_answer, run_with as run_1b

CFG = copy.deepcopy(load_config()); CFG["stage1a"]["context_window_s"] = 8
CAST = load_cast_lists()


class TestBank(unittest.TestCase):
    def test_save_lookup_replace(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(CFG); cfg["stage2"]["faces_bank_dir"] = td
            img = Path(td) / "src.jpg"; img.write_bytes(b"\xff\xd8x\xff\xd9")
            man = [{"key": "I", "name": "Siva", "t": 623.2, "t_str": "10:23.2", "line": "L0267", "why": "x", "path": str(img)},
                   {"key": "F", "name": "Swami", "t": 580.7, "t_str": "9:40.7", "line": "L0246", "why": "x", "path": str(img)}]
            bank = FB.save_verified("surya", "ep1", man, cfg)
            self.assertEqual(set(bank["faces"]), {"siva", "swami"})
            self.assertTrue((FB.bank_dir("surya", cfg) / "siva_ep1_623.2.jpg").exists())
            got = FB.lookup("surya", {"Q": "Siva", "R": "Nobody", "S": "SWAMI"}, cfg)
            self.assertEqual(set(got), {"Q", "S"})                      # matched by name, not by key; case-insensitive
            self.assertTrue(got["Q"][0]["bank"])
            # a second episode adds frames; re-saving the first episode replaces only its own
            FB.save_verified("surya", "ep2", [dict(man[0], t=50.0, t_str="0:50.0")], cfg)
            FB.save_verified("surya", "ep1", [dict(man[0], t=700.0, t_str="11:40.0")], cfg)
            frames = FB.load_bank("surya", cfg)["faces"]["siva"]["frames"]
            self.assertEqual(sorted((f["video_id"], f["t"]) for f in frames), [("ep1", 700.0), ("ep2", 50.0)])
            self.assertFalse(FB.lookup("other_series", {"Q": "Siva"}, cfg))


class TestAccuracy(unittest.TestCase):
    def test_accuracy_per_stage(self):
        f = S.run(make_inputs(), None, CFG, CAST)
        out, _, _, _ = run_1b(f, [good_answer(f)])
        with tempfile.TemporaryDirectory() as td:
            key = {"video_id": "synthetic", "characters": [{"name": "Surya", "desc": "lead"}, {"name": "Lakshmi", "desc": "Surya's mother"},
                                                           {"name": "Customer", "desc": "hungry man"}],
                   "labels": [{"start": 1.0, "end": 2.0, "speaker": "Surya"}, {"start": 2.4, "end": 3.6, "speaker": "Lakshmi"},
                              {"start": 60.0, "end": 62.0, "speaker": "Customer"}, {"start": 8.3, "end": 10.0, "speaker": "UNKNOWN"}]}
            kp = Path(td) / "synthetic.json"; kp.write_text(json.dumps(key), encoding="utf-8")
            SA_key_path = SA.key_path
            SA.key_path = lambda vid: kp
            try:
                res = SA.evaluate(out, "synthetic")
            finally:
                SA.key_path = SA_key_path
        self.assertEqual(res["n_checked"], 3)
        self.assertEqual(res["stages"]["fix"]["ok"], 1)          # Surya ok; 'Mother' has no match; Arjun != Customer
        self.assertEqual(res["stages"]["1b"]["ok"], 3)           # 1B renamed Mother -> Lakshmi; 'delivery customer' matches Customer
        self.assertIn("Mother", res["unmatched"]["fix"])
        line = SA.one_line(res)
        self.assertIn("Speaker map accuracy", line)
        self.assertIn("text pass", line)
        self.assertIsNone(res["stages"]["2"])                    # no Stage 2 map yet


if __name__ == "__main__":
    unittest.main()
