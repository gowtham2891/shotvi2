import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from selection_v2 import cast_sheet as CS, faces as FB, prefix, stage2_beats as S2
from selection_v2.config import load_config
from tests.v2.synthetic import make_inputs
from tests.v2.test_stage1b import FakeClient
from tests.v2.test_stage2 import FakeMedia, facts_after_1b

CFG = copy.deepcopy(load_config()); CFG["stage1a"]["context_window_s"] = 8


def answer_for(frames, keys):
    """Every frame: the named person visible and identifiable, alone; except the first frame of each key = crowd of 3."""
    seen = set(); fr = []
    for f in frames:
        first = f["key"] not in seen; seen.add(f["key"])
        fr.append({"frame_id": f["frame_id"], "n_people_visible": 3 if first else 1, "visible_keys": [f["key"]],
                   "named_visible": True, "named_identifiable": True, "note": "ok"})
    chars = [{"key": k, "look": f"{k} wears a blue shirt", "best_frames": [], "confidence": 0.8} for k in sorted(keys)]
    return {"frames": fr, "characters": chars}


class TestCastSheet(unittest.TestCase):
    def test_candidates_and_choose_rule(self):
        f = facts_after_1b()
        cands = CS.candidate_lines(f, CFG)
        self.assertTrue(cands)
        self.assertTrue(all(len(v) <= 8 for v in cands.values()))
        frames = [{"frame_id": "F1", "key": "A", "t": 1.0}, {"frame_id": "F2", "key": "A", "t": 2.0}, {"frame_id": "F3", "key": "A", "t": 3.0},
                  {"frame_id": "F4", "key": "B", "t": 4.0}, {"frame_id": "F5", "key": "B", "t": 5.0}]
        v = {"F1": {"named_visible": True, "named_identifiable": True, "n_people_visible": 3},   # crowd: never
             "F2": {"named_visible": True, "named_identifiable": True, "n_people_visible": 2},
             "F3": {"named_visible": True, "named_identifiable": True, "n_people_visible": 1},
             "F4": {"named_visible": False, "named_identifiable": False, "n_people_visible": 1},  # not the person
             "F5": {"named_visible": True, "named_identifiable": True, "n_people_visible": 2}}
        ch = CS.choose(frames, v, 3)
        self.assertEqual([x["frame_id"] for x in ch["A"]], ["F3"])          # a single-person frame beats the two-person one
        self.assertEqual([x["frame_id"] for x in ch["B"]], ["F5"])          # two people allowed when nothing better
        self.assertEqual(ch["B"][0]["n_people_visible"], 2)

    def test_validate(self):
        frames = [{"frame_id": "F1", "key": "A"}]
        errs = CS.validate({"frames": [{"frame_id": "F9", "visible_keys": ["ZZ"]}], "characters": [{"key": "QQ", "best_frames": ["F7"]}]}, frames, {"A"})
        self.assertTrue(any("unknown frame_id 'F9'" in e for e in errs))
        self.assertTrue(any("no verdict for frame(s) ['F1']" in e for e in errs))
        self.assertTrue(any("unknown key 'ZZ'" in e for e in errs))
        self.assertTrue(any("unknown key 'QQ'" in e for e in errs))

    def test_run_then_stage2_uses_sheet_and_looks(self):
        f = facts_after_1b()
        inputs = make_inputs()
        with tempfile.TemporaryDirectory() as td:
            inputs.paths = SimpleNamespace(run_dir=Path(td), mp4=Path(td) / "ep.mp4", title=lambda: "x")
            (Path(td) / "prefix.txt").write_text(prefix.build_prefix(f, None, CFG), encoding="utf-8")
            c2 = copy.deepcopy(CFG); c2["paths"]["runs_log"] = str(Path(td) / "runs.csv"); c2["stage2"]["faces_bank_dir"] = str(Path(td) / "bank")
            c2["stage1b"]["glossary_dir"] = str(Path(td) / "gl")   # never the real series glossary in a test
            fm = FakeMedia()
            holder = {}

            class Client(FakeClient):
                def generate_json(self, role, spec, system, prompt, schema, meta=None, want_raw=False, media=None):
                    if role == "cast_sheet":
                        frames = json.loads((Path(td) / "frames.json").read_text()) if (Path(td) / "frames.json").exists() else holder["frames"]
                        self.answers.insert(0, answer_for(frames, holder["keys"]))
                    return super().generate_json(role, spec, system, prompt, schema, meta, want_raw, media)
            # the client needs the frames the stage will cut: compute them the same way
            names, _ = S2.map_names(f)
            cands = CS.candidate_lines(f, c2)
            n = 0; frames = []
            for k in sorted(cands):
                for c in cands[k]:
                    n += 1; frames.append({"frame_id": f"F{n}", "key": k})
            holder["frames"], holder["keys"] = frames, set(names)
            client = Client([])
            out = CS.run(inputs, f, c2, client=client, framer=fm.framer)
            cs = out["cast_sheet"]
            self.assertEqual(len(cs["frames"]), len(frames))
            self.assertTrue(all(len(v) <= 3 for v in cs["chosen"].values()))
            self.assertTrue(all(x["n_people_visible"] == 1 for v in cs["chosen"].values() for x in v))   # crowd frames dropped
            self.assertIn("speaker_0", cs["looks"]); self.assertFalse(cs["verified"])
            self.assertIn("2:cast_sheet", (Path(td) / "runs.csv").read_text())
            parts = client.calls[0]["media"]
            self.assertTrue(parts[0]["text"].startswith("# PART 1"))                 # prefix first
            self.assertTrue(any("Lines around it" in (p.get("text") or "") for p in parts))
            # Stage 2 now carries the cast-sheet frames and the looks in its prefix
            from tests.v2.test_stage2 import good_sheet
            sc = out["episode_pass"]["derived"]["scenes"][1]
            c3 = Client([]); c3.answers = [good_sheet(out, sc)]
            out2 = S2.run(inputs, out, c2, client=c3, scene_ids=[sc["id"]], cutter=fm.cutter, uploader=fm.uploader, framer=fm.framer, deleter=fm.deleter)
            p2 = (Path(td) / "prefix_stage2.txt").read_text(encoding="utf-8")
            self.assertIn("look: speaker_0 wears a blue shirt", p2)
            labels = [p.get("text") for p in c3.calls[0]["media"] if p.get("text") and "confirmed by the cast sheet" in p["text"]]
            self.assertTrue(labels)
            # save to the bank with looks; a later lookup returns the look and the bank wins over the sheet
            have = [{"key": k, "name": names.get(k), **x} for k, v in cs["chosen"].items() for x in v]
            bank = FB.save_verified("surya", "synthetic", have, c2, looks=cs["looks"])
            self.assertEqual(bank["faces"][FB.norm(names["speaker_0"])]["look"], "speaker_0 wears a blue shirt")
            self.assertEqual(FB.looks_for("surya", names, c2)["speaker_0"], "speaker_0 wears a blue shirt")
            self.assertTrue(FB.lookup("surya", names, c2)["speaker_0"][0]["bank"])
            est = CS.estimate(out, c2, prefix_tokens=1000)
            self.assertEqual(est["n_frames"], len(frames)); self.assertGreater(est["inr"], 0)


if __name__ == "__main__":
    unittest.main()
