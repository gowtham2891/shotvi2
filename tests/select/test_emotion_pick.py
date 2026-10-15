"""Emotion-first selection (shotvi/select/emotion_pick.py), offline with a fake provider and a temp response cache:
1. two calls only (find once, build per moment, both through the context cache); no review / judge call;
2. stop first: stop == start_line starts there; else v1's lift path (a failed lift is rejected); the stop line must
   start within 3 s of playback;
3. land last: the land line must be among the last 3 spoken lines; a land far from the found moment is invalid;
4. rejection reasons (no_clip, safety block, under 15 s never padded, stop / land checks), dedupe (higher intensity,
   then earlier), episode order, --max-moments cap (highest intensity first);
5. dry run makes no generate call; a rerun replays at Rs 0; ws3_payoffs reuse is ws3's exact call;
6. merged's refactored helpers keep their behaviour (the rest is covered by test_merged)."""
import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shotvi.llm import Ledger, LLMClient
from shotvi.llm.base import RawResponse, SafetyBlocked
from shotvi.select import emotion_pick as E
from shotvi.select import merged as M
from shotvi.select import webseries as ws
from shotvi.select import ws3
from tests.select.test_webseries_cut import make_ep
from tests.select.test_webseries_scene import block, shot_cuts

# one long scene: 60 spoken lines L0001..L0060, line k (1-based) at 10 + 3 (k - 1) s, 2 s long; a shot change after
# every odd line (L0001, L0003, ...), so odd lines can be lifted and even lines cannot
SPEC = block(10, 60, "a")
CUTS = shot_cuts(SPEC)


def episode():
    return make_ep(SPEC, CUTS, duration=400.0)


def L(k):
    return f"L{k:04d}"


def moment(k, intensity=4, emotion="laugh"):
    return {"line": L(k), "type": "punchline", "emotion": emotion, "intensity": intensity, "reason": f"{L(k)} lands",
            "lands_on_screen": True}


def build(start, end, stop=None, land=None, reaction="hold", no_clip=False):
    if no_clip:
        return {"no_clip": True, "no_clip_why": "needs the earlier scene", "stop": {"line": "", "why": ""}, "stay": "",
                "land": {"line": "", "reaction": ""}, "start_line": "", "end_line": "", "drops": [],
                "purpose": "standalone", "spoiler": False, "characters": [], "title": "", "reason": ""}
    return {"no_clip": False, "no_clip_why": "", "stop": {"line": L(stop or start), "why": "a sharp line"},
            "stay": "will he admit it?", "land": {"line": L(land or end), "reaction": reaction},
            "start_line": L(start), "end_line": L(end), "drops": [], "purpose": "standalone", "spoiler": False,
            "characters": ["A"], "title": "అతను ఒప్పుకున్నాడా", "reason": "Viewer stops because a, stays to find out b, "
                                                                     "and it ends on c (laugh)."}


MOMENTS = [moment(10, 5),        # e01 shown: stop = start L0004
           moment(22, 4),        # e02 shown: cold open L0017 (odd line: liftable)
           moment(30, 4),        # e03 K1: stop L0026 (even line) cannot be lifted
           moment(33, 4),        # e04 X1: safety block, skipped
           moment(36, 3),        # e05 L1: 5 s clip, rejected, never padded
           moment(40, 4),        # e06 N0: no_clip
           moment(46, 3),        # e07 D1: overlaps e08 (intensity 5 wins)
           moment(47, 5),        # e08 shown
           moment(52, 4),        # e09 K2: ends 3 lines after the land line
           moment(58, 3)]        # e10 V1: land answered 8 lines away
BUILDS = {L(10): build(4, 10), L(22): build(14, 22, stop=17), L(30): build(24, 30, stop=26),
          L(36): build(35, 36), L(40): build(0, 0, no_clip=True), L(46): build(40, 46), L(47): build(41, 47),
          L(52): build(46, 55, land=52), L(58): build(50, 58, land=50)}
BLOCKED = {L(33)}


class FakeProvider:
    name = "fake"

    def __init__(self, moments=None):
        self.calls, self.caches, self.deleted, self.counted = [], [], [], 0
        self.moments = MOMENTS if moments is None else moments

    def generate(self, spec, system, prompt, schema, media=None, cached_content=None):
        p = schema["properties"]
        if "payoffs" in p:
            role, obj = "find", {"episode_notes": "a sitcom", "payoffs": self.moments}
        elif "no_clip" in p:
            role = "build"
            line = re.search(r"The moment: line (L\d+)", prompt).group(1)
            self.calls.append({"role": role, "system": system, "prompt": prompt, "cached": cached_content})
            if line in BLOCKED:
                raise SafetyBlocked("blocked", reason="PROHIBITED_CONTENT")
            obj = BUILDS[line]
        else:
            role, obj = "other", {}
        if role != "build":
            self.calls.append({"role": role, "system": system, "prompt": prompt, "cached": cached_content})
        return RawResponse(json.dumps(obj, ensure_ascii=False), 5000, 300, 400, "m",
                           cached_tokens=4000 if cached_content else 0)

    def count_tokens(self, spec, text):
        self.counted += 1
        return len(text) // 3

    def create_cache(self, spec, system, prefix, ttl_s=3600, display_name=""):
        name = f"cachedContents/fake{len(self.caches) + 1}"
        self.caches.append({"name": name, "system": system, "prefix": prefix})
        return {"name": name, "tokens": 5000, "ttl_s": ttl_s, "expire_time": "x", "storage_usd": 0.0025,
                "model": spec.model}

    def delete_cache(self, name):
        self.deleted.append(name)
        return True


def roles(prov):
    return [c["role"] for c in prov.calls]


class Run:
    def __init__(self, prov, cfg=None, dry_run=False, cache_dir=None, ledger_inr=0.0, limit=None):
        self.prov, self.ep = prov, episode()
        self.cfg = ws.deep_merge(E.DEFAULTS, cfg or {})
        for r in ("find", "build"):
            self.cfg["models"][r]["provider"] = "fake"
        self.cfg["run_id"] = "t"
        self.dry_run, self.ledger_inr, self.limit = dry_run, ledger_inr, limit
        self.cache_dir = cache_dir or tempfile.mkdtemp()
        self.root = Path(tempfile.mkdtemp())
        self.out = self.root / "out"
        self.tp = self.root / "t.json"
        self.tp.write_text(json.dumps({"audio_duration_secs": 400.0}), encoding="utf-8")
        self.logs = []

    def load(self, vid, with_loudness=True, speaker_names=False, require_names=True):
        return self.ep, None, {}, self.tp

    def __call__(self):
        self.llm = LLMClient(Ledger(max_usd=5.0), cache_dir=self.cache_dir, max_retries=0, schema_retries=0)
        with mock.patch.object(ws, "load_episode", self.load), mock.patch.object(ws, "ROOT", self.root), \
                mock.patch.object(E, "ROOT", self.root), \
                mock.patch("shotvi.llm.client.get_provider", return_value=self.prov), \
                mock.patch("shotvi.llm.spend.total_inr", return_value=self.ledger_inr):
            return E.run_episode("vid", self.cfg, self.llm, ws.Guard(self.limit), self.out, log=self.logs.append,
                                 dry_run=self.dry_run)


# ============================================================================ the full run
class FullRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prov = FakeProvider()
        cls.r = Run(cls.prov)
        cls.doc = cls.r()
        cls.props = cls.doc["proposals"]
        cls.rej = {x["id"]: x for x in cls.doc["rejected"]}

    def test_two_calls_only_no_review(self):
        self.assertEqual(roles(self.prov), ["find"] + ["build"] * 10)
        self.assertEqual(len(self.prov.caches), 1)
        for c in self.prov.calls:
            self.assertEqual(c["system"], "")                       # persona + transcript come from the cache
            self.assertEqual(c["cached"], self.prov.caches[0]["name"])
            self.assertNotIn("<transcript>", c["prompt"])
        self.assertEqual(self.prov.deleted, [self.prov.caches[0]["name"]])
        self.assertNotIn("ws_review", json.dumps(self.doc["cost"]["by_role"]))

    def test_build_calls_highest_intensity_first(self):
        lines = [re.search(r"The moment: line (L\d+)", c["prompt"]).group(1) for c in self.prov.calls
                 if c["role"] == "build"]
        self.assertEqual(lines, [L(10), L(47), L(22), L(30), L(33), L(40), L(52), L(36), L(46), L(58)])

    def test_shown_in_episode_order_with_emotion_fields(self):
        self.assertEqual([p["id"] for p in self.props], ["e01", "e02", "e08"])
        self.assertEqual([p["rank"] for p in self.props], [1, 2, 3])
        for p in self.props:
            for f in ("feeling", "intensity", "stop", "stay", "land", "reason", "title", "moment", "pieces",
                      "time_segments", "hook_line", "payoff_line", "why_it_works"):
                self.assertIn(f, p, f)
            self.assertEqual(p["tier"], E.TIER)
            self.assertEqual(p["scores"], {})                       # no 1-5 scores
            self.assertLessEqual(p["stop"]["plays_at_s"], 3.0)
        self.assertEqual(self.doc["kind"], "proposals")
        self.assertNotIn("episode_verdict", self.doc)

    def test_stop_is_the_start_line(self):
        p = self.props[0]
        self.assertFalse(p["cold_open"])
        self.assertEqual((p["start_line"], p["stop"]["line"]), (L(4), L(4)))
        self.assertLess(p["stop"]["plays_at_s"], 0.5)
        self.assertEqual(p["stop"]["via"], "start_line")

    def test_stop_lifted_as_cold_open(self):
        p = self.props[1]
        self.assertTrue(p["cold_open"])
        self.assertEqual(p["cold_open_detail"]["lines"], [L(17)])
        self.assertEqual(p["time_segments"][0]["kind"], "cold_open")
        self.assertTrue(p["stop"]["cold_open"])
        self.assertEqual(p["stop"]["via"], "cold_open")
        self.assertLess(p["stop"]["plays_at_s"], 0.5)
        played = [i for x in p["pieces"] for i in range(x["start_word"], x["end_word"] + 1)]
        self.assertEqual(len(played), len(set(played)))             # the lifted line is not replayed

    def test_rejection_reasons(self):
        code = {k: v["reject_reasons"][0].split(":")[0] for k, v in self.rej.items()}
        self.assertEqual(code, {"e03": "K1", "e04": "X1", "e05": "L1", "e06": "N0", "e07": "D1", "e09": "K2",
                                "e10": "V1"})
        self.assertIn("could not be lifted", self.rej["e03"]["reject_reasons"][0])
        self.assertIn("no shot cut", self.rej["e03"]["reject_reasons"][0])
        self.assertIn("needs the earlier scene", self.rej["e06"]["reject_reasons"][0])
        self.assertIn("overlaps clip e08", self.rej["e07"]["reject_reasons"][0])
        self.assertIn("last 3 spoken lines", self.rej["e09"]["reject_reasons"][0])
        self.assertIn("a different moment", self.rej["e10"]["reject_reasons"][0])
        self.assertNotIn("start_extended", json.dumps(self.doc))    # never padded

    def test_episode_summary_and_table(self):
        s = self.doc["episode_summary"]
        self.assertEqual((s["n_moments_valid"], s["n_build_calls"], s["n_no_clip"], s["n_blocked"], s["n_invalid"],
                          s["n_hard_rules"], s["n_stop_check_failed"], s["n_land_check_failed"], s["n_built"],
                          s["n_deduped"], s["n_shown"]), (10, 10, 1, 1, 1, 1, 1, 1, 4, 1, 3))
        st = {r["id"]: r["status"] for r in self.doc["moment_table"]}
        self.assertEqual(st["p01"], "shown")
        self.assertEqual(st["p04"], "rejected")
        self.assertIn("3 shown, in episode order", self.doc["episode_note"]["text"])
        md = (self.r.out / "vid.md").read_text(encoding="utf-8")
        self.assertIn("stay: will he admit it?", md)

    def test_free_rerun_from_the_cache(self):
        prov2 = FakeProvider()
        doc2 = Run(prov2, cache_dir=self.r.cache_dir)()
        self.assertEqual(prov2.calls, [])
        self.assertEqual(prov2.caches, [])
        self.assertEqual(doc2["cost"]["usd_billed"], 0.0)
        self.assertEqual([p["id"] for p in doc2["proposals"]], [p["id"] for p in self.props])
        # the safety block is remembered: the replay skips it again for free
        self.assertTrue(next(x for x in doc2["rejected"] if x["id"] == "e04")["reject_reasons"][0].startswith("X1"))

    def test_preview_card_fields(self):
        sys.path.insert(0, str(ws.ROOT / "tools"))
        import preview_clips as pc
        import preview_page
        meta = pc.clip_meta(self.props[1])
        self.assertEqual(meta["feeling"], "laugh")
        self.assertIn(L(17), meta["stop"])
        self.assertIn("[cold open]", meta["stop"])
        self.assertIn("[plays at", meta["stop"])
        self.assertEqual(meta["stay"], "will he admit it?")
        self.assertIn(L(22), meta["land"])
        self.assertTrue(meta["reason"].startswith("Viewer stops because"))
        page = preview_page.build_page("t", [{"video_id": "vid", "n_total": 1, "clips": []}])
        self.assertIn("chip feel", page)
        self.assertEqual(pc.clip_meta({**self.props[0], "feeling": None, "stop": None})["feeling"], "")


class Cap(unittest.TestCase):
    def test_max_moments_builds_the_most_intense_first(self):
        prov = FakeProvider()
        doc = Run(prov, cfg={"build": {"max_moments": 3}})()
        self.assertEqual(roles(prov), ["find", "build", "build", "build"])
        s = doc["episode_summary"]
        self.assertEqual((s["n_build_calls"], s["n_not_built_cap"]), (3, 7))
        self.assertEqual([p["id"] for p in doc["proposals"]], ["e01", "e02", "e08"])
        nb = [r for r in doc["moment_table"] if r["status"] == "not_built"]
        self.assertEqual(len(nb), 7)

    def test_spend_limit_cuts_the_build_list(self):
        prov = FakeProvider()
        r = Run(prov, ledger_inr=0.0, limit=25.0)
        doc = r()
        n = doc["episode_summary"]["n_build_calls"]
        self.assertLess(n, 10)
        self.assertEqual(doc["episode_summary"]["n_not_built_spend"], 10 - n)

    def test_spend_guard_stops_before_find(self):
        prov = FakeProvider()
        with self.assertRaises(ws.BudgetStop):
            Run(prov, ledger_inr=100.0, limit=100.5)()
        self.assertEqual(prov.calls, [])


# ============================================================================ checks in isolation
class Checks(unittest.TestCase):
    def setUp(self):
        self.ep = episode()
        self.cfg = copy.deepcopy(E.DEFAULTS)
        self.p = {"id": "p01", "line": L(10), "text": "x", "type": "punchline", "emotion": "laugh", "intensity": 4,
                  "reason": "r", "lands_on_screen": True, "order": 0}

    def built(self, ans):
        m, emo, _ = E.validate_build(self.ep, self.p, ans, self.cfg)
        return ws.build_clip(self.ep, m, ws.DEFAULTS["cut"]), emo

    def test_stop_must_play_in_the_first_3_s(self):
        c, emo = self.built(build(4, 10))
        emo = {**emo, "stop": {**emo["stop"], "line": L(5)}}       # second line: starts 3.3 s in
        with self.assertRaises(E.Rejected) as cm:
            E.check_built(self.ep, c, emo, self.cfg)
        self.assertEqual(cm.exception.code, "K1")
        self.assertIn("> 3 s", str(cm.exception))

    def test_stop_outside_the_clip(self):
        with self.assertRaises(E.Rejected) as cm:
            E.validate_build(self.ep, self.p, build(4, 10, stop=2), self.cfg)
        self.assertEqual(cm.exception.code, "K1")

    def test_land_must_be_among_the_last_three(self):
        c, emo = self.built(build(4, 13, land=10))
        with self.assertRaises(E.Rejected) as cm:
            E.check_built(self.ep, c, emo, self.cfg)
        self.assertEqual(cm.exception.code, "K2")
        c, emo = self.built(build(4, 12, land=10))                  # land two lines before the end: fine
        self.assertEqual(E.check_built(self.ep, c, emo, self.cfg)["last_lines"], [L(10), L(11), L(12)])

    def test_end_too_far_after_the_land_is_moved(self):
        m, _, flags = E.validate_build(self.ep, self.p, build(4, 15, land=10), self.cfg)
        self.assertEqual(m["end_line"], L(10))
        self.assertTrue(any("moved to the payoff" in f for f in flags))

    def test_start_after_the_land_is_invalid(self):
        with self.assertRaises(E.Rejected) as cm:
            E.validate_build(self.ep, self.p, build(12, 13, land=11), self.cfg)
        self.assertEqual(cm.exception.code, "V1")

    def test_no_clip(self):
        with self.assertRaises(E.Rejected) as cm:
            E.validate_build(self.ep, self.p, build(0, 0, no_clip=True), self.cfg)
        self.assertEqual(cm.exception.code, "N0")

    def test_moment_fields_for_the_v1_builder(self):
        m, emo, _ = E.validate_build(self.ep, self.p, build(4, 11, stop=7, land=10, reaction=L(11)), self.cfg)
        self.assertEqual((m["start_line"], m["end_line"], m["cold_open_lines"]), (L(4), L(11), [L(7)]))
        self.assertEqual(emo["land"]["reaction"], L(11))
        self.assertEqual(m["payoff_type"], "punchline")


# ============================================================================ dry run + reuse
class DryRun(unittest.TestCase):
    def test_no_generate_call_and_an_estimate_when_find_is_not_cached(self):
        prov = FakeProvider()
        res = Run(prov, dry_run=True)()
        self.assertEqual(prov.calls, [])
        self.assertEqual(prov.caches, [])
        self.assertFalse(res["find_cached"])
        self.assertGreater(prov.counted, 0)                         # countTokens (free) for the input
        rows = res["projection"]["rows"]
        self.assertEqual(rows["cap"]["cap"], E.PROJ_CAP)
        self.assertGreater(rows["no_cap"]["inr_worst"], rows["no_cap"]["inr_expected"])
        self.assertGreater(rows["no_cap"]["find_inr_expected"], 0)

    def test_exact_after_a_run_and_free_when_everything_is_cached(self):
        first = Run(FakeProvider())
        first()
        prov = FakeProvider()
        d = Run(prov, dry_run=True, cache_dir=first.cache_dir)
        res = d()
        self.assertEqual(prov.calls, [])
        self.assertTrue(res["find_cached"])
        self.assertEqual(res["n_moments_valid"], 10)
        row = res["projection"]["rows"]["no_cap"]
        self.assertEqual((row["n_build_expected"], row["n_build_uncached"], row["inr_expected"], row["inr_worst"]),
                         (10, 0, 0.0, 0.0))
        # the dry run writes every rendered build task, exactly as it would be sent
        self.assertEqual(len(list((d.out / "tasks" / "vid").glob("*.txt"))), 10)

    def test_ws3_payoffs_reuse_is_ws3s_exact_call(self):
        cfg = ws.deep_merge(E.DEFAULTS, {"find_prompt": "ws3_payoffs"})
        self.assertIs(E.find_schema(cfg), ws3.PAYOFFS_SCHEMA)
        a = E.ctx_cfg(cfg)
        self.assertEqual(a["models"]["ws3_payoffs"], ws3.DEFAULTS["models"]["ws3_payoffs"])
        self.assertEqual(a["prompts"]["ws3_payoffs"], "ws3_payoffs")
        ep = episode()
        self.assertEqual(ws3.task_text(a, "ws3_payoffs", episode_len="1:00.0", n_lines=3),
                         ws3.task_text(M.cfg_a(M.DEFAULTS), "ws3_payoffs", episode_len="1:00.0", n_lines=3))
        self.assertEqual(ws3.context_prompt(ep, a), ws3.context_prompt(ep, M.cfg_a(M.DEFAULTS)))

    def test_new_find_schema_limits_feelings(self):
        s = E.find_schema(E.DEFAULTS)
        self.assertEqual(s["properties"]["payoffs"]["items"]["properties"]["emotion"]["enum"], E.FEELINGS)
        self.assertIn("other", ws3.PAYOFFS_SCHEMA["properties"]["payoffs"]["items"]["properties"]["emotion"]["enum"])


# ============================================================================ merged helpers unchanged
class MergedHelpers(unittest.TestCase):
    def test_lift_ok_near_default_window_is_merged_config(self):
        ep = episode()
        lift = ws.liftable_lines(ep, ws.DEFAULTS["cut"])
        p = {"line": L(30)}
        self.assertEqual(M.lift_ok_near(ep, lift, p, M.DEFAULTS),
                         M.lift_ok_near(ep, lift, p, {}, window=M.DEFAULTS["missed"]))

    def test_dedupe_new_unchanged(self):
        ep = episode()

        def x(i, a, b, inten, order, strength=4):
            c = ws.build_clip(ep, {"purpose": "standalone", "start_line": L(a), "end_line": L(b),
                                   "cold_open_lines": [], "drops": []}, ws.DEFAULTS["cut"])
            return {"id": i, "clip": c, "payoff": {"intensity": inten, "order": order},
                    "moment": {"strength": strength}}
        v1 = [{"id": "w01", "time_segments": x("v", 2, 12, 4, 0)["clip"]["time_segments"]}]
        cands = [x("m1", 3, 11, 5, 0), x("m2", 20, 30, 3, 1), x("m3", 21, 31, 4, 2), x("m4", 40, 50, 4, 3)]
        kept, dropped = M.dedupe_new(cands, v1, 0.5)
        self.assertEqual([k["id"] for k in kept], ["m3", "m4"])
        self.assertEqual([d["reject_reasons"][0][:2] for d in dropped], ["D2", "D3"])
        self.assertTrue(dropped[1]["reject_reasons"][0].startswith("D3: overlaps new clip m3"))


if __name__ == "__main__":
    unittest.main()
