"""Stage 3 in two AI steps (founder, 2026-11-04): 3a moments per scene (text), 3b one clip per moment (video). Code
only looks up times and checks the format. Synthetic data, fake client (no network)."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from selection_v2 import stage3_find as S3
from selection_v2.config import load_config
from tests.v2.synthetic import make_inputs
from tests.v2.test_naming_check import CFG
from tests.v2.test_stage1b import FakeClient, _cutter, _deleter, _uploader
from tests.v2.test_stage2_episode import facts_with_scenes, notes_answer
from selection_v2 import stage2_episode as S2E


def facts3():
    f = facts_with_scenes()
    _, _, notes = S2E.validate(notes_answer(f), f)
    f["scene_notes"] = {"episode": "story", "scenes": notes}
    return f


def speech_ids(f, scene):
    pos = {r["id"]: i for i, r in enumerate(f["lines"])}
    return [r["id"] for r in f["lines"][pos[scene["first_line"]]:pos[scene["last_line"]] + 1] if r["kind"] == "speech" and not r.get("zero_len")]


def cfg(td):
    c = copy.deepcopy(CFG); c["paths"]["runs_log"] = str(Path(td) / "runs.csv")
    c["stage3_find"] = load_config()["stage3_find"]
    return c


class TestStage3(unittest.TestCase):
    def test_moments_format(self):
        f = facts3(); sc = f["identity_pass"]["scenes"][0]; ids = speech_ids(f, sc)
        ok = {"payoff": ids[-1], "payoff_quote": "x", "payoff_type": "punchline", "kind": "standalone", "needs_from": ids[0],
              "scenes": [sc["id"], 99], "what_lands": "a", "why_it_works": "b", "spoiler": False}
        out_scene = f["identity_pass"]["scenes"][1]["first_line"]
        ans = {"scene_id": sc["id"], "moments": [ok, {**ok, "payoff": out_scene}, {**ok}]}
        errs, warns, ms = S3.validate_moments(ans, f, sc)
        self.assertEqual(errs, []); self.assertEqual(len(ms), 1); self.assertEqual(ms[0]["scenes"], [sc["id"]])
        self.assertEqual(len(warns), 2)                                   # outside the scene, repeated payoff
        self.assertTrue(S3.validate_moments({"scene_id": 9, "moments": []}, f, sc)[0])

    def test_clip_pieces_and_cold_open(self):
        f = facts3(); sc = f["identity_pass"]["scenes"][0]; ids = speech_ids(f, sc)
        inputs = make_inputs()
        pos = {r["id"]: i for i, r in enumerate(f["lines"])}
        lo, hi = pos[sc["first_line"]], pos[sc["last_line"]]
        L = {r["id"]: r for r in f["lines"]}
        first_word = L[ids[0]]["text"].split()[0]; last_word = L[ids[-1]]["text"].split()[-1]
        ans = {"works": True, "why_not": "", "start": {"line": ids[0], "at": first_word}, "end": {"line": ids[-1], "at": last_word},
               "drops": [{"first": ids[1], "last": ids[1], "why": "aside"}], "cold_open": {"line": ids[2], "why": "funny"},
               "hook": "h", "title": "t", "why": "w", "kind": "standalone", "spoiler": False}
        errs, warns, val = S3.validate_clip(ans, f, inputs.words, lo, hi, 180)
        self.assertEqual(errs, [])
        p = val["pieces"]
        self.assertTrue(p[0]["cold_open"]); self.assertEqual(p[0]["lines"], [ids[2]])
        played = [l for x in p[1:] for l in x["lines"]]
        self.assertNotIn(ids[1], played); self.assertNotIn(ids[2], played)   # dropped, and the lift is not heard twice
        self.assertEqual(p[1]["start"], L[ids[0]]["start"])
        bad = copy.deepcopy(ans); bad["start"]["at"] = "nosuchword"
        self.assertTrue(any("start.at" in e for e in S3.validate_clip(bad, f, inputs.words, lo, hi, 180)[0]))
        self.assertTrue(any("maximum" in e for e in S3.validate_clip(ans, f, inputs.words, lo, hi, 1)[0]))
        bad = copy.deepcopy(ans); bad["cold_open"] = {"line": ids[0], "why": ""}
        self.assertTrue(any("cold_open" in e for e in S3.validate_clip(bad, f, inputs.words, lo, hi, 180)[0]))

    def test_runs(self):
        f = facts3(); scs = f["identity_pass"]["scenes"]
        inputs = make_inputs()
        with tempfile.TemporaryDirectory() as td:
            inputs.paths = SimpleNamespace(run_dir=Path(td), mp4=Path(td) / "ep.mp4", title=lambda: "Synthetic")
            c = cfg(td)
            ans_a = []
            for sc in scs:
                ids = speech_ids(f, sc)
                ans_a.append({"scene_id": sc["id"], "moments": [{"payoff": ids[-1], "payoff_quote": "x", "payoff_type": "punchline", "kind": "standalone",
                                                                  "needs_from": ids[0], "scenes": [sc["id"]], "what_lands": "a", "why_it_works": "b", "spoiler": False}]})
            client = FakeClient(ans_a)
            out = S3.find_moments(inputs, f, c, client=client)
            self.assertEqual(len(client.calls), len(scs))
            self.assertEqual(client.calls[0]["media"][0]["text"], client.calls[1]["media"][0]["text"])   # shared episode text first
            self.assertIn("Scene 2", client.calls[1]["prompt"])
            ms = out["moments"]["moments"]; self.assertEqual([m["id"] for m in ms], ["m01", "m02"])
            L = {r["id"]: r for r in f["lines"]}
            ans_b = [{"clips": [{"moments": [m["id"]], "works": True, "why_not": "", "start": {"line": m["needs_from"], "at": L[m["needs_from"]]["text"].split()[0]},
                                 "end": {"line": m["payoff"], "at": L[m["payoff"]]["text"].split()[-1]}, "drops": [], "cold_open": None,
                                 "hook": "h", "title": "t", "why": "w", "kind": "standalone", "spoiler": False}]} for m in ms]
            client = FakeClient(ans_b)
            out = S3.shape_clips(inputs, out, c, client=client, cutter=_cutter, uploader=_uploader, deleter=_deleter)
            self.assertEqual(len(client.calls), 2); self.assertIn("file_uri", client.calls[0]["media"][0])   # one call per scene
            cl = out["clips"]["clips"]
            self.assertEqual(len(cl), 2); self.assertGreater(cl[0]["duration"], 0); self.assertEqual(cl[0]["payoffs"][0]["moment"], "m01")

    def test_scene_clips_check_moment_ids(self):
        f = facts3(); sc = f["identity_pass"]["scenes"][0]; ids = speech_ids(f, sc)
        inputs = make_inputs()
        pos = {r["id"]: i for i, r in enumerate(f["lines"])}
        L = {r["id"]: r for r in f["lines"]}
        group = [{"id": "m01"}, {"id": "m02"}]
        clip = {"moments": ["m01"], "works": True, "why_not": "", "start": {"line": ids[0], "at": L[ids[0]]["text"].split()[0]},
                "end": {"line": ids[-1], "at": L[ids[-1]]["text"].split()[-1]}, "drops": [], "cold_open": None}
        errs, warns, out = S3.validate_scene_clips({"clips": [clip]}, f, inputs.words, group, pos[sc["first_line"]], pos[sc["last_line"]], 180)
        self.assertEqual(errs, []); self.assertEqual(len(out), 1); self.assertTrue(any("m02" in w for w in warns))
        errs, _, _ = S3.validate_scene_clips({"clips": [{**clip, "moments": ["m09"]}]}, f, inputs.words, group, 0, 5, 180)
        self.assertTrue(errs)


class CachedFakeClient(FakeClient):
    """FakeClient that also takes ``cached_content`` (the one step) and has an empty response cache."""

    def __init__(self, answers):
        super().__init__(answers)
        self.ledger = SimpleNamespace(summary=lambda: {}, record_fixed=lambda *a, **k: None)
        self.cache_only = False

    def cache_key(self, spec, system, prompt, schema, salt=""):
        return "k"

    def _cache_get(self, spec, key):
        return None

    def generate_json(self, role, spec, system, prompt, schema, meta=None, want_raw=False, media=None, cached_content=None):
        self.calls.append({"role": role, "system": system, "prompt": prompt, "media": media, "cached_content": cached_content})
        obj = self.answers.pop(0)
        raw = SimpleNamespace(text="{}", input_tokens=150000, output_tokens=500, thought_tokens=3000, cached_tokens=145000, model_version="fake")
        return (obj, raw) if want_raw else obj


class FakeProvider:
    def __init__(self):
        self.created, self.deleted = [], []

    def create_cache(self, spec, system, prefix, ttl_s=3600, display_name="", media=None):
        self.created.append({"system": system, "prefix": prefix, "media": media, "ttl_s": ttl_s})
        return {"name": "cachedContents/x", "tokens": 145000, "ttl_s": ttl_s, "storage_usd": 0.05}

    def delete_cache(self, name):
        self.deleted.append(name); return True


class TestStage3OneStep(unittest.TestCase):
    """founder, 2026-11-04: 3a + 3b merged; the whole episode (video + text) cached once, one call per scene."""

    def clip(self, f, first, last, payoff=None, **kw):
        L = {r["id"]: r for r in f["lines"]}
        return {"payoff": payoff or last, "payoff_quote": "x", "payoff_type": "punchline",
                "start": {"line": first, "at": L[first]["text"].split()[0]}, "end": {"line": last, "at": L[last]["text"].split()[-1]},
                "drops": [], "cold_open": None, "hook": "h", "title": "t", "why": "w", "kind": "standalone", "spoiler": False, **kw}

    def test_validate(self):
        f = facts3(); s1, s2 = f["identity_pass"]["scenes"][:2]; a, b = speech_ids(f, s1), speech_ids(f, s2)
        w = make_inputs().words
        # a clip may start in an earlier scene when its payoff lands in this one
        errs, _, out = S3.validate_found_clips({"scene_id": s2["id"], "clips": [self.clip(f, a[0], b[-1])]}, f, w, s2, 180)
        self.assertEqual(errs, []); self.assertEqual(len(out), 1); self.assertGreater(out[0]["duration"], 0)
        # the payoff must be a line of this scene
        errs, _, out = S3.validate_found_clips({"scene_id": s2["id"], "clips": [self.clip(f, a[0], a[-1])]}, f, w, s2, 180)
        self.assertTrue(any("scene" in e for e in errs)); self.assertEqual(out, [])
        # ... and must play in the clip
        errs, _, _ = S3.validate_found_clips({"scene_id": s1["id"], "clips": [self.clip(f, a[0], a[1], payoff=a[-1])]}, f, w, s1, 180)
        self.assertTrue(any("does not play" in e for e in errs))
        errs, _, _ = S3.validate_found_clips({"scene_id": s1["id"], "clips": [self.clip(f, a[0], a[-1], payoff=a[1], drops=[{"first": a[1], "last": a[1], "why": "x"}])]}, f, w, s1, 180)
        self.assertTrue(any("does not play" in e for e in errs))
        self.assertTrue(S3.validate_found_clips({"scene_id": 99, "clips": []}, f, w, s1, 180)[0])

    def test_run_caches_once(self):
        f = facts3(); scs = f["identity_pass"]["scenes"]
        inputs = make_inputs()
        with tempfile.TemporaryDirectory() as td:
            inputs.paths = SimpleNamespace(run_dir=Path(td), mp4=Path(td) / "ep.mp4", title=lambda: "Synthetic")
            c = cfg(td)
            answers = [{"scene_id": sc["id"], "clips": [self.clip(f, speech_ids(f, sc)[0], speech_ids(f, sc)[-1])]} for sc in scs]
            client, prov = CachedFakeClient(answers), FakeProvider()
            out = S3.find_clips(inputs, f, c, client=client, cutter=_cutter, uploader=_uploader, provider=prov, deleter=_deleter)
            self.assertEqual(len(prov.created), 1); self.assertEqual(prov.deleted, ["cachedContents/x"])     # cached once, deleted at the end
            self.assertIn("file_uri", prov.created[0]["media"][0]); self.assertIn("PART 3", prov.created[0]["prefix"])
            self.assertEqual(len(client.calls), len(scs))
            self.assertTrue(all(x["system"] == "" and x["media"] is None and x["cached_content"]["name"] == "cachedContents/x" for x in client.calls))
            self.assertIn(f"Scene {scs[1]['id']}", client.calls[1]["prompt"])
            cl = out["clips"]["clips"]
            self.assertEqual([x["id"] for x in cl], ["c01", "c02"]); self.assertEqual(cl[1]["scene"], scs[1]["id"])
            self.assertGreater(out["clips"]["usage"]["storage_inr"], 0)
            self.assertIn("total_inr", S3.estimate_clips(f, c, counter=len))

    def test_cache_failure_stops(self):
        from shotvi.llm.base import CacheUnavailable
        f = facts3(); inputs = make_inputs()

        class NoCache(FakeProvider):
            def create_cache(self, *a, **k):
                raise CacheUnavailable("400")
        with tempfile.TemporaryDirectory() as td:
            inputs.paths = SimpleNamespace(run_dir=Path(td), mp4=Path(td) / "ep.mp4", title=lambda: "Synthetic")
            client = CachedFakeClient([])
            with self.assertRaises(S3.Stage3Error):
                S3.find_clips(inputs, f, cfg(td), client=client, cutter=_cutter, uploader=_uploader, provider=NoCache(), deleter=_deleter)
            self.assertEqual(client.calls, [])          # never sends the whole video per call instead


class TestEndHold(unittest.TestCase):
    """founder, 2026-11-04: a clip may hold after its last word on a reaction (end.hold_until)."""

    def test_hold(self):
        f = facts3(); sc = f["identity_pass"]["scenes"][0]; ids = speech_ids(f, sc)
        w = make_inputs().words
        L = {r["id"]: r for r in f["lines"]}
        pos = {r["id"]: i for i, r in enumerate(f["lines"])}
        nxt = next(r for r in f["lines"][pos[ids[1]] + 1:] if r["kind"] == "speech" and not r.get("zero_len"))
        base = TestStage3OneStep().clip(f, ids[0], ids[1])
        _, _, v0 = S3.validate_clip(base, f, w, 0, len(f["lines"]) - 1, 180)
        t1 = v0["end_time"]
        hold = round(min(t1 + 1.0, nxt["start"] - 0.05), 1)
        ok = copy.deepcopy(base); ok["end"]["hold_until"] = f"{int(hold // 60)}:{hold % 60:04.1f}"
        errs, _, v = S3.validate_clip(ok, f, w, 0, len(f["lines"]) - 1, 180)
        self.assertEqual(errs, []); self.assertAlmostEqual(v["pieces"][-1]["end"], hold); self.assertGreaterEqual(v["hold_s"], 0)
        bad = copy.deepcopy(base); bad["end"]["hold_until"] = nxt["start"] + 1.0
        self.assertTrue(any("runs into" in e for e in S3.validate_clip(bad, f, w, 0, len(f["lines"]) - 1, 180)[0]))
        self.assertEqual(S3.clock_s("1:02:03.5"), 3723.5); self.assertIsNone(S3.clock_s("abc"))
        bad["end"]["hold_until"] = t1 - 2
        self.assertTrue(any("before the last word" in e for e in S3.validate_clip(bad, f, w, 0, len(f["lines"]) - 1, 180)[0]))


class TestEndHoldPick(unittest.TestCase):
    """founder, 2026-11-04 (v8): the model names what the clip holds on (a sound line, next_line, none); code looks up the time."""

    def test_pick(self):
        f = facts3(); sc = f["identity_pass"]["scenes"][0]; ids = speech_ids(f, sc)
        w = make_inputs().words
        pos = {r["id"]: i for i, r in enumerate(f["lines"])}
        base = TestStage3OneStep().clip(f, ids[0], ids[1])
        n = len(f["lines"]) - 1
        nxt = next(r for r in f["lines"][pos[ids[1]] + 1:] if r["kind"] == "speech" and not r.get("zero_len"))
        c = copy.deepcopy(base); c["end"]["hold"] = "none"
        e, _, v = S3.validate_clip(c, f, w, 0, n, 180); self.assertEqual(e, []); self.assertLess(v["hold_s"], 0.5)
        c["end"]["hold"] = "next_line"
        e, _, v = S3.validate_clip(c, f, w, 0, n, 180); self.assertEqual(e, [])
        self.assertAlmostEqual(v["pieces"][-1]["end"], max(nxt["start"] - 0.1, v["end_time"]))
        c["end"]["hold"] = ids[0]                                   # a spoken line, not a sound line
        self.assertTrue(S3.validate_clip(c, f, w, 0, n, 180)[0])
        # a sound line right after the last word: hold until it ends (never past the next spoken line)
        f2 = copy.deepcopy(f); i = pos[ids[1]] + 1
        f2["lines"].insert(i, {"id": "S1", "kind": "event", "start": f2["lines"][i - 1]["end"], "end": nxt["start"] + 5, "text": "[music]", "tag": "[music]"})
        c["end"]["hold"] = "S1"
        e, _, v = S3.validate_clip(c, f2, w, 0, len(f2["lines"]) - 1, 180); self.assertEqual(e, [])
        self.assertAlmostEqual(v["pieces"][-1]["end"], nxt["start"] - 0.1)
