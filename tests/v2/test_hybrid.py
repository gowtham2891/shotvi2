"""Hybrid (founder, 2026-10-26): label-level findings applied on top of the text pass. No longer used by the pipeline
(the naming check replaced it, 2026-11-01; the identity pass answers per line since 2026-11-02); kept for old facts."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from selection_v2 import hybrid, identity_pass as IDP, stage1_episode_pass as E, stage1_facts as S
from selection_v2.config import load_cast_lists, load_config
from tests.v2.synthetic import make_inputs
from tests.v2.test_stage1b import FakeClient, good_answer, run_with, _cutter, _uploader, _deleter

CFG = copy.deepcopy(load_config()); CFG["stage1a"]["context_window_s"] = 8
CFG["stage1b"]["prior"] = "raw"; CFG["stage1b"]["video"] = True
CAST = load_cast_lists()


def facts_1b():
    f = S.run(make_inputs(), None, CFG, CAST)
    out, *_ = run_with(f, [good_answer(f)], CFG)
    return out


def label_of(f, lid):
    return f["episode_pass"]["derived"]["line_speakers"].get(lid)


class TestApplyFindings(unittest.TestCase):
    def test_new_person_relabel_merge_edge_and_sources(self):
        f = facts_1b()
        d = f["episode_pass"]["derived"]
        sc = d["scenes"][0]
        keys = sorted({d["line_speakers"][r["id"]] for r in f["lines"] if r["kind"] == "speech" and r["id"] in d["line_speakers"]
                       and sc["start"] <= r["start"] <= sc["end"]})
        k0 = keys[0]; k1 = keys[1] if len(keys) > 1 else keys[0]
        n_scenes = len(d["scenes"])
        mid = d["scenes"][1]["start"] if n_scenes > 1 else None
        findings = [
            {"kind": "new_person", "scene_id": sc["id"], "key": k0, "name": "Stranger", "source": "video_pass", "evidence": "e1"},
            {"kind": "merge_keys", "keys": [k1, keys[-1]], "name": "Merged", "source": "founder", "evidence": "e2"},
        ]
        out = hybrid.apply_findings(f, findings)
        d2 = out["episode_pass"]["derived"]
        self.assertIn("line_speakers_text", d2)                                  # the text pass is preserved
        self.assertEqual(d2["line_speakers_text"], d["line_speakers"])
        new_keys = {v for v in d2["line_speakers"].values()}
        self.assertTrue(any(k.startswith("hy_") for k in new_keys))
        for r in f["lines"]:
            if r["kind"] == "speech" and d["line_speakers"].get(r["id"]) == k0 and sc["start"] <= r["start"] <= sc["end"]:
                self.assertTrue(d2["line_speakers"][r["id"]].startswith("hy_"))
        if keys[-1] != k1:
            self.assertNotIn(keys[-1], new_keys)
        srcs = [a["source"] for a in out["identity_findings"]]
        self.assertEqual(srcs, ["video_pass", "founder"])
        self.assertTrue(all(a.get("effect") for a in out["identity_findings"]))
        # second application appends, never duplicates the text map
        out2 = hybrid.apply_findings(out, [{"kind": "relabel", "scene_id": sc["id"], "key": k1, "to_key": keys[-1] if keys[-1] != k1 else k0, "source": "video_pass"}])
        self.assertEqual(out2["episode_pass"]["derived"]["line_speakers_text"], d["line_speakers"])
        self.assertEqual(len(out2["identity_findings"]), 3)

    def test_findings_from_identity_pass_only_label_level(self):
        f = facts_1b()
        d = f["episode_pass"]["derived"]
        sc = d["scenes"][0]
        names = {s["key"]: s["name"] for s in f["episode_pass"]["speakers"]}
        raw = sorted({r["speaker"] for r in f["lines"] if r["kind"] == "speech" and sc["start"] <= r["start"] <= sc["end"]})
        ans = {"cast_by_scene": [{"scene_id": sc["id"], "label": raw[0], "name": "Totally New Person", "confidence": "high", "evidence": "seen"}],
               "same_person": [], "reference_times": [], "looks": []}
        fnd = hybrid.findings_from_identity_pass(ans, f)
        self.assertTrue(fnd)
        self.assertTrue(all(x["source"] == "video_pass" for x in fnd))
        self.assertTrue(all(x["kind"] in {"new_person", "relabel", "merge_keys", "scene_edge"} for x in fnd))
        # an answer that agrees with the text pass yields no finding
        key0 = d["line_speakers"][next(r["id"] for r in f["lines"] if r["kind"] == "speech" and r["speaker"] == raw[0] and sc["start"] <= r["start"] <= sc["end"])]
        ans2 = {"cast_by_scene": [{"scene_id": sc["id"], "label": raw[0], "name": names[key0], "confidence": "high", "evidence": "seen"}],
                "same_person": [], "reference_times": [], "looks": []}
        self.assertEqual(hybrid.findings_from_identity_pass(ans2, f), [])


if __name__ == "__main__":
    unittest.main()
