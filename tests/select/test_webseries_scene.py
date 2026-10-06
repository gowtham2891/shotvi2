"""Scene-by-scene discovery arm (shotvi/select/scenes.py + webseries discover_mode "scene"), offline:
1. the splitter breaks at long silences with a shot change (music lowers the bar), merges tiny scenes across their
   weaker edge, splits long ones at their strongest internal gap, and never crosses an excluded zone;
2. context windows: the nearest lines always, more only within the time cap, never an excluded line;
3. a moment must start/end on lines its call showed and contain lines of its scene;
4. chunked discover with a fake provider: one call per scene, moments merged in scene order, the >dup_overlap
   duplicate across two calls keeps the stronger, one review; dry run and spend guard make no call;
5. the arm is off by default and a scene prompt cannot run in episode mode (or the reverse)."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shotvi.llm import Ledger, LLMClient
from shotvi.llm.base import RawResponse
from shotvi.select import scenes as SC
from shotvi.select import webseries as ws
from tests.select.test_webseries_cut import make_ep

NO_ZONES = {"opening": {"hard": [], "story_start": None}, "sponsors": []}
SCENE_CFG = ws.deep_merge(ws.DEFAULTS, {"discover_mode": "scene", "prompts": {"ws_discover": "ws_discover_b_scene"}})


def block(t0, n, tag, step=3.0, dur=2.0):
    """n spoken lines of 3 words, one every `step` s starting at t0."""
    return [(round(t0 + k * step, 3), round(t0 + k * step + dur, 3), f"{tag}{k}a {tag}{k}b {tag}{k}c")
            for k in range(n)]


def shot_cuts(spec, every=2):
    """A shot change in every `every`-th gap between lines, plus one in every gap longer than 3 s."""
    out = []
    for k, (a, b) in enumerate(zip(spec, spec[1:])):
        g0, g1 = a[1], b[0]
        if g1 - g0 > 3 or k % every == 0:
            out.append(round((g0 + g1) / 2, 3))
    return out


def three_scenes():
    # scene A 10-76 s (22 lines), 14 s gap, scene B 90-165 s (25 lines), 13 s gap, scene C 178-244 s (22 lines)
    spec = block(10, 22, "a") + block(90, 25, "b") + block(178, 22, "c")
    return spec, shot_cuts(spec)


class Splitter(unittest.TestCase):
    def test_breaks_only_at_long_gaps_with_a_shot_change(self):
        spec, cuts = three_scenes()
        sc = SC.split_scenes(make_ep(spec, cuts, duration=260.0))
        self.assertEqual([(s["first_line"], s["last_line"]) for s in sc],
                         [("L0001", "L0022"), ("L0023", "L0047"), ("L0048", "L0069")])
        self.assertEqual([s["no"] for s in sc], [1, 2, 3])
        self.assertEqual(sc[1]["opened_by"]["gap_s"], 15.02)
        # the same silences held on one shot are acted pauses: one scene (244 s > max: split once at its best gap)
        no_cut = [c for c in cuts if not (76 < c < 90 or 165 < c < 178)]
        sc2 = SC.split_scenes(make_ep(spec, no_cut, duration=260.0))
        self.assertLess(len(sc2), 3)

    def test_music_in_a_medium_gap_breaks(self):
        spec = block(10, 25, "a") + block(89.5, 25, "b")      # 4.5 s silence between the blocks
        cuts = shot_cuts(spec)
        self.assertEqual(len(SC.split_scenes(make_ep(spec, cuts, duration=200.0))), 1)
        ep = make_ep(spec, cuts, events=[(85.5, 89.0, "[బ్యాక్ గ్రౌండ్ మ్యూజిక్]")], duration=200.0)
        sc = SC.split_scenes(ep)
        self.assertEqual(len(sc), 2)
        self.assertTrue(sc[1]["opened_by"]["music"])

    def test_tiny_scene_merges_across_its_weaker_edge(self):
        # 70 s scene | 15 s silence | 30 s scene | 8 s silence | 70 s scene: the tiny one joins the third
        spec = block(10, 24, "a") + block(96, 10, "b") + block(134, 24, "c")
        sc = SC.split_scenes(make_ep(spec, shot_cuts(spec), duration=250.0))
        self.assertEqual(len(sc), 2)
        self.assertEqual(sc[1]["first_line"], "L0025")
        self.assertGreaterEqual(min(s["duration_s"] for s in sc), SC.MIN_S)

    def test_long_scene_splits_at_its_strongest_gap_with_both_parts_long_enough(self):
        # 300 s of dialogue; a 4 s gap at ~40 s (would leave a 40 s part) and a 3.5 s gap at ~150 s
        spec = block(10, 10, "a") + block(42 + 4, 35, "b") + block(46 + 35 * 3 + 3.5, 52, "c")
        cuts = shot_cuts(spec)
        sc = SC.split_scenes(make_ep(spec, cuts, duration=330.0))
        self.assertEqual(len(sc), 2)
        self.assertEqual(sc[1]["first_line"], "L0046")
        self.assertTrue(all(s["duration_s"] >= SC.MIN_S for s in sc))

    def test_excluded_zones_are_never_in_a_scene_and_never_crossed(self):
        spec, cuts = three_scenes()
        ep = make_ep(spec, cuts, duration=260.0)
        # story starts at 22 s; a sponsor read over 80-176 s (all of scene B)
        ex = SC.excluded_of({"opening": {"hard": [(0.0, 21.5)], "story_start": 22.0},
                             "sponsors": [{"start": 80.0, "end": 176.0}]})
        sc = SC.split_scenes(ep, ex)
        for s in sc:
            lines = [ep.lines[q] for q in range(s["p0"], s["p1"] + 1) if ep.is_speech(ep.lines[q])]
            self.assertTrue(all(ep.w0(r) >= 22.0 - 0.05 for r in lines))
            self.assertFalse(any(ep.w0(r) < 176.0 and ep.w1(r) > 80.0 for r in lines))
            self.assertFalse(s["start"] < 80.0 < s["end"])
        self.assertEqual([(s["first_line"], s["last_line"]) for s in sc], [("L0005", "L0022"), ("L0048", "L0069")])
        short = [s for s in sc if s["duration_s"] < SC.MIN_S]
        self.assertTrue(short and all("note" in s for s in short))   # a stretch's only scene stays, flagged

    def test_no_shot_data_lets_every_gap_break(self):
        spec, _ = three_scenes()
        self.assertEqual(len(SC.split_scenes(make_ep(spec, [], duration=260.0))), 3)


class Windows(unittest.TestCase):
    def test_context_nearest_lines_then_time_cap(self):
        spec, cuts = three_scenes()
        ep = make_ep(spec, cuts, duration=260.0)
        sc = SC.split_scenes(ep)
        w = SC.scene_windows(ep, sc)
        self.assertEqual(w[0]["before"], [])
        ids = SC.window_ids(ep, w[1])
        # 15 s silence before scene B: lines of scene A within 30 s of B's start (90 s) = those starting >= 60 s
        self.assertEqual(ids["before"], ["L0018", "L0019", "L0020", "L0021", "L0022"])
        # after scene A: B's lines ending within 30 s of A's end (75 s)
        self.assertEqual(SC.window_ids(ep, w[0])["after"], ["L0023", "L0024", "L0025", "L0026", "L0027"])
        self.assertTrue(all(ep.w0(ep.by_id[i]) >= 60.0 - 1e-6 for i in ids["before"]))
        self.assertEqual(ids["after"][0], "L0048")

    def test_context_min_lines_across_a_long_transition(self):
        spec = block(10, 25, "a") + block(160, 25, "b")      # 75 s of silence between
        ep = make_ep(spec, shot_cuts(spec), duration=260.0)
        w = SC.scene_windows(ep, SC.split_scenes(ep))
        self.assertEqual(len(w[1]["before"]), SC.CONTEXT_MIN_LINES)
        self.assertEqual(len(w[0]["after"]), SC.CONTEXT_MIN_LINES)

    def test_context_never_shows_an_excluded_line(self):
        spec, cuts = three_scenes()
        ep = make_ep(spec, cuts, duration=260.0)
        ex = SC.excluded_of({"opening": {"hard": [], "story_start": None},
                             "sponsors": [{"start": 80.0, "end": 100.0}]})
        sc = SC.split_scenes(ep, ex)
        for w in SC.scene_windows(ep, sc, ex):
            for q in w["before"] + w["after"]:
                r = ep.lines[q]
                self.assertFalse(r["start"] < 100.0 and r["end"] > 80.0, r)


class MomentCheck(unittest.TestCase):
    def setUp(self):
        spec, cuts = three_scenes()
        self.ep = make_ep(spec, cuts, duration=260.0)
        self.w = SC.scene_windows(self.ep, SC.split_scenes(self.ep))[1]      # scene B: L0023-L0047

    def test_inside_and_spanning_into_context_are_taken(self):
        self.assertIsNone(SC.check_moment(self.ep, {"start_line": "L0025", "end_line": "L0035"}, self.w))
        self.assertIsNone(SC.check_moment(self.ep, {"start_line": "L0020", "end_line": "L0030"}, self.w))
        self.assertIsNone(SC.check_moment(self.ep, {"start_line": "L0040", "end_line": "L0049"}, self.w))

    def test_outside_the_window_or_only_context_is_rejected(self):
        self.assertTrue(SC.check_moment(self.ep, {"start_line": "L0005", "end_line": "L0030"}, self.w)
                        .startswith("S1"))
        self.assertTrue(SC.check_moment(self.ep, {"start_line": "L0019", "end_line": "L0022"}, self.w)
                        .startswith("S2"))
        self.assertTrue(SC.check_moment(self.ep, {"start_line": "L0016", "end_line": "L0030"}, self.w)
                        .startswith("S1"))      # L0016 is before the context B's call showed
        self.assertTrue(SC.check_moment(self.ep, {"start_line": "x", "end_line": "L0030"}, self.w)
                        .startswith("S1"))


def mom(a, b, strength, purpose="standalone"):
    return {"purpose": purpose, "start_line": a, "end_line": b, "what_happens": "x", "hook": "h", "payoff": "p",
            "opening_strength": "strong", "cold_open_lines": [], "cold_open_why": "", "drops": [],
            "spoiler": False, "characters": [], "visual_dependency": "low", "category": "humour",
            "strength": strength}


class FakeProvider:
    """Discover: answers per scene (read from the prompt); review: scores every clip it is shown."""
    def __init__(self, per_scene):
        self.per_scene = per_scene
        self.prompts = []

    def generate(self, spec, system, prompt, schema, media=None):
        self.prompts.append(prompt)
        if "clips" in schema["properties"]:
            ids = re.findall(r"^### (w\d+) \|", prompt, re.M)
            obj = {"clips": [{"id": i, "hook": 4, "standalone": 4, "payoff": 4, "value_emotion": 4, "overall": 4,
                              "keep_cold_open": False, "cold_open_note": "", "hook_without_cold_open": 4,
                              "overall_without_cold_open": 4, "bad_cuts": [], "cut_note": "", "title": "శీర్షిక",
                              "why_it_works": "w", "category": "humour"} for i in ids]}
        else:
            no = int(re.search(r"This call: scene (\d+) of", prompt).group(1))
            obj = {"episode_notes": f"notes {no}", "moments": self.per_scene.get(no, [])}
        return RawResponse(json.dumps(obj, ensure_ascii=False), 1000, 500, 0, "m")

    def count_tokens(self, spec, text):
        return len(text) // 3


def _fake_load(ep):
    tmp = Path(tempfile.mkdtemp()) / "t.json"
    tmp.write_text("{}", encoding="utf-8")
    return lambda vid, with_loudness=True: (ep, None, {}, tmp)


class ChunkedDiscover(unittest.TestCase):
    def setUp(self):
        spec, cuts = three_scenes()
        self.ep = make_ep(spec, cuts, duration=260.0)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.per_scene = {
            # scene A: a clip that runs into scene B's first lines (via A's context after)
            1: [mom("L0012", "L0025", 3)],
            # scene B: the same moment seen from B (starts in B's context before), stronger; a moment wholly in
            # B's context (S2); one with ids B never showed (S1); and one plain moment
            2: [mom("L0018", "L0026", 5), mom("L0019", "L0022", 4), mom("L0001", "L0030", 4),
                mom("L0032", "L0042", 4)],
            3: [],
        }

    def run_ep(self, prov, dry_run=False, guard=None, ledger=0.0):
        captured = {}

        def fake_doc(ep, tp, props, rej, cfg, llm, n_moments, n_passed, t0, debug):
            captured.update(props=props, rejected=rej, n_moments=n_moments, debug=debug)
            return {"proposals": props, "rejected": rej}
        with mock.patch.object(ws, "load_episode", _fake_load(self.ep)), \
                mock.patch.object(SC, "load_zones", return_value=NO_ZONES), \
                mock.patch("shotvi.llm.client.get_provider", return_value=prov), \
                mock.patch("shotvi.llm.spend.total_inr", return_value=ledger), \
                mock.patch.object(ws, "run_document", fake_doc), mock.patch.object(ws, "clips_md", lambda *a: ""):
            llm = LLMClient(Ledger(), cache_dir=self.tmp.name)
            r = ws.run_episode("vid", ws.deep_merge(SCENE_CFG, {}), llm, guard or ws.Guard(None),
                               Path(self.tmp.name) / "out", log=lambda *_: None, dry_run=dry_run)
        return r, captured

    def test_one_call_per_scene_merge_and_dedupe(self):
        prov = FakeProvider(self.per_scene)
        _, cap = self.run_ep(prov)
        disc = [p for p in prov.prompts if "This call: scene" in p]
        self.assertEqual(len(disc), 3)
        self.assertEqual(len(prov.prompts), 4)                       # + one review
        for k, p in enumerate(disc, 1):
            self.assertIn(f"This call: scene {k} of 3", p)
            self.assertEqual(p.count("<scene>"), 1)
        self.assertIn("L0023", disc[0].split("<context_after>")[1])  # scene B's first lines are A's context
        self.assertNotIn("<scene>\nL0023", disc[0])
        rej = {r["id"]: r["reject_reasons"][0] for r in cap["debug"]["rejected"]}
        self.assertTrue(rej["s02m2"].startswith("S2"))
        self.assertTrue(rej["s02m3"].startswith("S1"))
        # the cross-scene duplicate: A's (strength 3) loses to B's (strength 5)
        moments = cap["debug"]["discover"]["moments"]
        self.assertEqual([(m["start_line"], m["scene"]) for m in moments],
                         [("L0012", 1), ("L0018", 2), ("L0032", 2)])
        d1 = [r for r in rej.values() if r.startswith("D1")]
        self.assertEqual(len(d1), 1)
        self.assertIn("w01", [k for k, v in rej.items() if v.startswith("D1")])
        shown = [p["start_line"] for p in cap["props"]]
        self.assertEqual(sorted(shown), ["L0018", "L0032"])
        self.assertEqual({p["discovery"]["scene"] for p in cap["props"]}, {2})
        self.assertEqual(cap["n_moments"], 5)          # 3 taken + 2 outside their window
        self.assertEqual(len(cap["debug"]["scenes"]["scenes"]), 3)

    def test_dry_run_makes_no_call(self):
        prov = FakeProvider(self.per_scene)
        with mock.patch.object(prov, "generate", side_effect=AssertionError("called")):
            r, _ = self.run_ep(prov, dry_run=True)
        self.assertEqual(r["first_paid_stage"], "ws_discover")
        self.assertEqual(r["n_scenes"], 3)
        self.assertLess(r["would_bill_inr_expected"], r["would_bill_inr_worst"])
        self.assertLessEqual(r["would_bill_inr_worst"], r["would_bill_inr_guard"] + 1e-6)
        self.assertLessEqual(r["would_bill_inr_guard"], r["would_bill_inr_hard_max"])
        self.assertFalse((Path(self.tmp.name) / "out").exists())

    def test_guard_stops_before_the_first_scene_call(self):
        prov = FakeProvider(self.per_scene)
        with self.assertRaises(ws.BudgetStop) as e:
            self.run_ep(prov, guard=ws.Guard(1000.5), ledger=1000.0)
        self.assertIn("scene discover calls", str(e.exception))
        self.assertEqual(prov.prompts, [])

    def test_cached_rerun_is_free(self):
        self.run_ep(FakeProvider(self.per_scene))
        prov = FakeProvider(self.per_scene)
        with mock.patch.object(prov, "generate", side_effect=AssertionError("called")):
            r, _ = self.run_ep(prov, dry_run=True)
        self.assertIsNone(r["first_paid_stage"])


class ArmOff(unittest.TestCase):
    def test_default_is_episode_mode(self):
        self.assertFalse(ws.scene_mode(ws.DEFAULTS))
        self.assertNotIn("discover_mode", ws.DEFAULTS)   # the baseline run document stays byte-identical

    def test_prompt_and_mode_must_match(self):
        with self.assertRaises(ValueError):
            ws.check_prompt_mode(ws.deep_merge(ws.DEFAULTS, {"discover_mode": "scene"}))
        with self.assertRaises(ValueError):
            ws.check_prompt_mode(ws.deep_merge(ws.DEFAULTS, {"prompts": {"ws_discover": "ws_discover_b_scene"}}))
        ws.check_prompt_mode(ws.DEFAULTS)
        ws.check_prompt_mode(SCENE_CFG)
        with self.assertRaises(SystemExit):
            ws.main(["vid", "--dry-run", "--discover-mode", "scene"])

    def test_scene_prompt_is_the_baseline_plus_insertions(self):
        from shotvi.select.prompts import load_prompt
        a, b = load_prompt("ws_discover"), load_prompt("ws_discover_b_scene")
        for part in ("system", "user"):
            x, y = getattr(a, part), getattr(b, part)
            self.assertTrue(all(w in y for w in x.split("\n")), part)
        self.assertIn("$scene_no", b.user)


if __name__ == "__main__":
    unittest.main()
