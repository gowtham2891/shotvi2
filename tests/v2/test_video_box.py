"""Box A (founder, 2026-11-06): the episode video stored once at Google by the video step, read by it and the scene
notes. Fake provider and client; no network."""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from shotvi.llm.base import ModelSpec
from selection_v2 import stage2_episode as S2E
from selection_v2 import video_box as VB
from selection_v2.config import load_config
from tests.v2.synthetic import make_inputs
from tests.v2.test_naming_check import CFG
from tests.v2.test_stage1b import FakeClient, _cutter, _deleter, _uploader
from tests.v2.test_stage2_episode import facts_with_scenes, notes_answer

SPEC = ModelSpec("gemini", "gemini-3.8-flash", {"media_resolution": "low"})


class FakeProv:
    def __init__(self):
        self.made = []

    def create_cache(self, spec, system, prefix, ttl_s=3600, display_name="", media=None):
        self.made.append({"system": system, "media": media, "ttl_s": ttl_s, "params": spec.params})
        return {"name": "cachedContents/box", "tokens": 112000, "ttl_s": ttl_s, "storage_usd": 0.028}


class BoxClient(FakeClient):
    def generate_json(self, role, spec, system, prompt, schema, meta=None, want_raw=False, media=None, cached_content=None):
        self.calls.append({"system": system, "prompt": prompt, "media": media, "cached_content": cached_content, "spec": spec})
        obj = self.answers.pop(0)
        raw = SimpleNamespace(text=json.dumps(obj), input_tokens=25000, output_tokens=3000, thought_tokens=0, cached_tokens=11200, model_version="f")
        return (obj, raw) if want_raw else obj


class TestBox(unittest.TestCase):
    def cfg(self, on=True):
        c = copy.deepcopy(load_config()); c["video_box"]["enabled"] = on; return c

    def test_make_load_expire(self):
        with tempfile.TemporaryDirectory() as td:
            prov = FakeProv()
            box = VB.make(td, SPEC, {"file_uri": "u", "key": "k"}, "k1", self.cfg(), provider=prov)
            self.assertEqual(prov.made[0]["ttl_s"], 1800); self.assertEqual(prov.made[0]["system"], "")      # video only, 30 min
            self.assertEqual(prov.made[0]["params"], {})
            self.assertEqual(VB.load(td, "k1", SPEC.model, self.cfg())["name"], "cachedContents/box")
            self.assertIsNone(VB.load(td, "other-video", SPEC.model, self.cfg()))
            info = json.loads((Path(td) / VB.BOX_FILE).read_text()); info["expire_at"] = time.time() + 30
            (Path(td) / VB.BOX_FILE).write_text(json.dumps(info))
            self.assertIsNone(VB.load(td, "k1", SPEC.model, self.cfg()))          # under min_left_s: not used

    def test_request(self):
        rq = VB.request("SYS", {"file_uri": "u"}, "TEXT", None)
        self.assertEqual(rq["system"], "SYS"); self.assertIn("file_uri", rq["media"][0]); self.assertIsNone(rq["cached_content"])
        rq = VB.request("SYS", None, "TEXT", {"name": "n", "key": "k"})
        self.assertEqual(rq["system"], ""); self.assertEqual(rq["media"][0]["text"], "SYS")    # system text first, word for word
        self.assertEqual(rq["cached_content"]["name"], "n")
        self.assertNotIn("media_resolution", VB.spec_for(SPEC, {"name": "n"}).params)

    def test_scene_notes_read_the_box(self):
        f = facts_with_scenes(); inputs = make_inputs()
        with tempfile.TemporaryDirectory() as td:
            inputs.paths = SimpleNamespace(run_dir=Path(td), mp4=Path(td) / "ep.mp4", title=lambda: "Synthetic")
            cfg = copy.deepcopy(CFG); cfg["paths"]["runs_log"] = str(Path(td) / "runs.csv")
            full = self.cfg(); cfg["stage2_episode"] = full["stage2_episode"]; cfg["video_box"] = full["video_box"]
            VB.make(td, ModelSpec.from_dict(cfg["stage2_episode"]["model"]), {"file_uri": "u"}, "k1", cfg, provider=FakeProv())
            client = BoxClient([notes_answer(f)])
            ups = []
            with mock.patch.object(S2E, "content_key", lambda p: "k1"):
                out = S2E.run(inputs, f, cfg, client=client, cutter=_cutter, uploader=lambda p, m: ups.append(p) or _uploader(p, m), deleter=_deleter)
            call = client.calls[0]
            self.assertEqual(call["system"], ""); self.assertEqual(call["cached_content"]["name"], "cachedContents/box")
            self.assertEqual(ups, [])                                  # nothing uploaded: the video comes from the box
            self.assertTrue(out["scene_notes"]["video_box"])
