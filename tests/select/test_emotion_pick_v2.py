"""emotion_pick/0.2 (the founder's fix spec, 2026-10-16), offline:
1. end clamp: an end 3 spoken lines after the land is clamped (land among the last 3), never dropped (K2);
2. the land may move forward up to 15 spoken lines (escalation), 3 back;
3. stop: Case A (start+1/+2 within 3 s: no lift), Case B lift refused -> kept from start_line, weak start -> start
   advances to the stop; a late stop is flagged (stop_not_first), not rejected;
4. cluster_moments: within 35 lines in one scene -> one cluster; a >= 4 s music event splits; target = the last payoff
   with intensity >= max - 1; $cluster_context text; one build call per cluster;
5. same-scene dedupe (spans overlap, same scene) and its keep order;
6. strict lift-ok: continuation guard, acknowledgement guard, >= 1.0 s of speech, quiet tail;
7. the lift's in-place skip counts as one of the <= 3 internal cuts;
8. 0.2 renders Scribe's speaker labels; the v1 config renders 0.1's context byte-identically; v1 prompt files kept;
9. the find schema takes "cliffhanger" (0.2 only); validate_payoffs accepts it."""
import copy
import unittest

from shotvi.select import emotion_pick as E
from shotvi.select import webseries as ws
from shotvi.select import ws3
from shotvi.select.prompts import load_prompt
from tests.select.test_emotion_pick import V1, FakeProvider, L, Run, build, episode, roles
from tests.select.test_webseries_cut import make_ep
from tests.select.test_webseries_scene import block, shot_cuts

V2 = E.DEFAULTS
NO_CLUSTER = {"cluster": {"enabled": False}}


def cfg2(over=None):
    return ws.deep_merge(V2, over or {})


def run2(prov, over=None, **kw):
    r = Run(prov, **kw)
    r.cfg = cfg2(over)
    for k in ("find", "build"):
        r.cfg["models"][k]["provider"] = "fake"
    r.cfg["run_id"] = "t2"
    return r


def payoff(k, intensity=4, text=None):
    return {"id": f"p{k:02d}", "line": L(k), "text": text or f"line {k}", "type": "punchline", "emotion": "laugh",
            "intensity": intensity, "reason": "r", "lands_on_screen": True, "order": k}


# ============================================================================ 1-3 validation and build, in isolation
class Validate(unittest.TestCase):
    def setUp(self):
        self.ep = episode()
        self.cfg = cfg2()
        self.p = payoff(10)

    def built(self, ans, p=None):
        x = {"id": "e01", "payoff": p or self.p}
        x["moment"], x["emo"], x["flags"] = E.validate_build(self.ep, p or self.p, ans, self.cfg)
        c = E.build_v2(self.ep, x, ws.DEFAULTS, self.cfg, set(ws.liftable_lines(self.ep, ws.DEFAULTS["cut"])))
        return x, c

    def test_end_three_lines_after_land_is_clamped_not_dropped(self):
        # 0.1: land L0010 with end L0013 -> K2 (test_emotion_pick.Checks); 0.2: clamped, clip kept
        m, _, flags = E.validate_build(self.ep, self.p, build(4, 13, land=10), self.cfg)
        self.assertEqual(m["end_line"], L(11))                       # hold: at most 1 after the land
        self.assertTrue(any("clamped to L0011" in f for f in flags))
        m, _, _ = E.validate_build(self.ep, self.p, build(4, 13, land=10, reaction=L(11)), self.cfg)
        self.assertEqual(m["end_line"], L(12))                       # reaction L0011: 1 after it, 2 after the land
        x, c = self.built(build(4, 13, land=10))
        ck = E.check_built(self.ep, c, x["emo"], self.cfg)
        self.assertIn(L(10), ck["last_lines"])

    def test_end_far_after_land_is_clamped_not_moved_to_the_land(self):
        m, _, _ = E.validate_build(self.ep, self.p, build(4, 25, land=10, reaction=L(11)), self.cfg)
        self.assertEqual(m["end_line"], L(12))
        v1 = ws.deep_merge(V2, V1)
        m1, _, _ = E.validate_build(self.ep, self.p, build(4, 25, land=10, reaction=L(11)), v1)
        self.assertEqual(m1["end_line"], L(10))                      # 0.1: moved to the land

    def test_land_late_after_the_hold_is_trimmed(self):
        # a clip whose played tail puts the land 4th from last is trimmed line by line, not rejected (K2)
        ep = self.ep
        x = {"id": "e01", "payoff": self.p}
        x["moment"], x["emo"], x["flags"] = E.validate_build(ep, self.p, build(4, 12, land=10, reaction=L(11)),
                                                             self.cfg)
        x["moment"]["end_line"] = L(13)                              # as if the clamp had let 3 lines through
        c = E.build_v2(ep, x, ws.DEFAULTS, self.cfg, set())
        self.assertEqual(c["end_line"], L(12))
        self.assertTrue(any("trimmed to L0012" in f for f in x["flags"]))
        self.assertIn(L(10), E.check_built(ep, c, x["emo"], self.cfg)["last_lines"])

    def test_forward_escalation_up_to_15_lines(self):
        m, emo, flags = E.validate_build(self.ep, self.p, build(4, 25, land=25), self.cfg)
        self.assertEqual((emo["land"]["line"], m["end_line"]), (L(25), L(25)))
        self.assertTrue(any("land moved from the found line L0010 to L0025" in f for f in flags))
        with self.assertRaises(E.Rejected) as cm:                    # 16 after: a different moment
            E.validate_build(self.ep, self.p, build(4, 26, land=26), self.cfg)
        self.assertEqual(cm.exception.code, "V1")
        with self.assertRaises(E.Rejected) as cm:                    # 4 before: still out
            E.validate_build(self.ep, self.p, build(2, 6, land=6), self.cfg)
        self.assertIn("before", str(cm.exception))
        with self.assertRaises(E.Rejected):                          # 0.1: 3 either way
            E.validate_build(self.ep, self.p, build(4, 14, land=14), ws.deep_merge(V2, V1))

    def test_case_a_stop_right_after_start_no_lift(self):
        # L0005 starts 3.0 s after L0004 (next spoken line): play from start_line, no cold open
        x, c = self.built(build(4, 10, stop=5))
        self.assertEqual(x["emo"]["stop"]["case"], "A_near_start")
        self.assertFalse(c["cold_open"])
        self.assertEqual(c["start_line"], L(4))
        self.assertFalse(x["emo"]["stop"]["cold_open"])

    def test_case_b_lift_kept_when_it_passes(self):
        x, c = self.built(build(4, 10, stop=7))                      # odd line: a shot change after it
        self.assertEqual(x["emo"]["stop"]["case"], "B_lift")
        self.assertEqual(c["cold_open"]["lines"], [L(7)])

    def test_refused_lift_keeps_the_clip_from_start_line(self):
        # L0008 (even line) cannot be lifted (no shot change after it): 0.1 rejected (K1); 0.2 plays from start_line
        x, c = self.built(build(4, 10, stop=8))
        self.assertEqual(x["emo"]["stop"]["case"], "B_refused_play_start")
        self.assertFalse(c["cold_open"])
        self.assertEqual(c["start_line"], L(4))
        self.assertTrue(any("cold open L0008 refused" in f for f in x["flags"]))
        ck = E.check_built(self.ep, c, x["emo"], self.cfg)          # stop plays ~12 s in: flagged, not rejected
        self.assertIn("stop_not_first", ck)
        with self.assertRaises(E.Rejected) as cm:
            E.check_built(self.ep, c, x["emo"], ws.deep_merge(self.cfg, {"check": {"stop_late": "reject"}}))
        self.assertEqual(cm.exception.code, "K1")

    def test_lift_not_offered_is_refused_when_enforced(self):
        x, c = self.built(build(4, 10, stop=7))
        self.assertTrue(c["cold_open"])
        x = {"id": "e01", "payoff": self.p}
        x["moment"], x["emo"], x["flags"] = E.validate_build(self.ep, self.p, build(4, 10, stop=7), self.cfg)
        c = E.build_v2(self.ep, x, ws.DEFAULTS, self.cfg, set())   # L0007 not offered
        self.assertFalse(c["cold_open"])
        self.assertTrue(any("not offered" in f for f in x["flags"]))

    def test_weak_start_advances_to_the_stop(self):
        spec = [(10.0, 10.6, "Hello"), (11.0, 13.0, "a b c d"), (13.5, 15.5, "e f g h"), (16.0, 18.0, "i j k l"),
                (18.5, 20.5, "m n o p"), (21.0, 23.0, "q r s t"), (23.5, 25.5, "u v w x")]
        ep = make_ep(spec, [9.9, 26.0], duration=60.0)              # no shot change after L0004: the lift fails
        p = {**payoff(6), "line": L(6)}
        x = {"id": "e01", "payoff": p}
        x["moment"], x["emo"], x["flags"] = E.validate_build(ep, p, build(1, 6, stop=4, land=6), self.cfg)
        c = E.build_v2(ep, x, ws.DEFAULTS, self.cfg, set())
        self.assertEqual(x["emo"]["stop"]["case"], "B_refused_advance_start")
        self.assertEqual((c["start_line"], x["moment"]["start_line"]), (L(4), L(4)))
        self.assertFalse(c["cold_open"])
        self.assertLess(E.check_built(ep, c, x["emo"], self.cfg)["stop_play_s"], 0.5)
        self.assertEqual(E._weak_start(ep, L(1), self.cfg["cold_open"]), "greeting")
        self.assertIsNone(E._weak_start(ep, L(2), self.cfg["cold_open"]))

    def test_strong_start_is_not_advanced(self):
        x, c = self.built(build(4, 10, stop=8))
        self.assertEqual(c["start_line"], L(4))                      # 3 words, 2 s: not weak


# ============================================================================ 4 clusters
def scene_ep():
    # scene A: 30 lines from 10 s; a 5 s song with no speech at 101-106 s; scene B: 20 lines from 110 s
    spec = block(10, 30, "a") + block(110, 20, "b")
    ep = make_ep(spec, shot_cuts(spec), events=[(100.5, 106.0, "[పాట]"), (50.0, 51.0, "[నవ్వు]")], duration=200.0)
    return ep


def lid(ep, text):
    return next(r["id"] for r in ep.lines if r.get("text", "").startswith(text))


class Clusters(unittest.TestCase):
    def setUp(self):
        self.ep = scene_ep()
        self.cfg = cfg2()

    def p(self, tag, intensity, k):
        line = lid(self.ep, tag)
        return {"id": f"p{k:02d}", "line": line, "text": ws3.line_text(self.ep, line), "type": "punchline",
                "emotion": "laugh", "intensity": intensity, "reason": "r", "lands_on_screen": True, "order": k}

    def test_same_scene_within_35_lines_merge_and_music_splits(self):
        ps = [self.p("a3a", 4, 1), self.p("a10a", 5, 2), self.p("a20a", 4, 3), self.p("a28a", 3, 4),
              self.p("b2a", 4, 5)]
        ks = E.cluster_moments(self.ep, ps, self.cfg)
        self.assertEqual([[q["id"] for q in k["members"]] for k in ks], [["p01", "p02", "p03", "p04"], ["p05"]])
        self.assertIn("music", ks[1]["break_before"])
        a = ks[0]
        self.assertEqual(a["target"]["id"], "p03")                   # last with intensity >= 5 - 1
        self.assertEqual(a["strength"], 5)
        self.assertEqual([q["id"] for q in a["earlier"]], ["p01", "p02"])
        self.assertEqual([q["id"] for q in a["absorbed"]], ["p04"])
        self.assertEqual(a["context"], 'Earlier beats in this same scene to include as setup/escalation: '
                                       f'{ps[0]["line"]} ("a3a a3b a3c"), {ps[1]["line"]} ("a10a a10b a10c")')
        self.assertEqual(ks[1]["context"], "")

    def test_far_apart_payoffs_split(self):
        spec = block(10, 60, "a", step=3.0)
        ep = make_ep(spec, shot_cuts(spec), duration=400.0)
        ps = [{**payoff(2), "line": L(2)}, {**payoff(40), "id": "p40", "line": L(40)}]
        ks = E.cluster_moments(ep, ps, self.cfg)                    # 38 lines and 114 s apart
        self.assertEqual(len(ks), 2)
        ps = [{**payoff(2), "line": L(2)}, {**payoff(18), "id": "p18", "line": L(18)}]
        self.assertEqual(len(E.cluster_moments(ep, ps, self.cfg)), 1)

    def test_short_music_does_not_split(self):
        spec = block(10, 20, "a")
        ep = make_ep(spec, shot_cuts(spec), events=[(26.2, 27.0, "[సంగీతం]")], duration=100.0)
        ps = [self.p_of(ep, "a1a", 1), self.p_of(ep, "a6a", 2)]
        self.assertEqual(len(E.cluster_moments(ep, ps, self.cfg)), 1)

    def p_of(self, ep, tag, k):
        line = lid(ep, tag)
        return {"id": f"p{k:02d}", "line": line, "text": "t", "type": "punchline", "emotion": "laugh",
                "intensity": 4, "reason": "r", "lands_on_screen": True, "order": k}

    def test_disabled_is_one_cluster_per_payoff(self):
        ps = [self.p("a3a", 4, 1), self.p("a10a", 5, 2)]
        ks = E.cluster_moments(self.ep, ps, ws.deep_merge(V2, V1))
        self.assertEqual([k["target"]["id"] for k in ks], ["p01", "p02"])
        self.assertEqual([k["context"] for k in ks], ["", ""])

    def test_build_task_fills_cluster_context(self):
        ep = episode()
        p = {**payoff(10), "text": ws3.line_text(ep, L(10))}
        t2 = E.task_build(self.cfg, ep, p, [], "Earlier beats in this same scene to include as setup/escalation: "
                                               'L0004 ("x")')
        self.assertIn('Why a stranger feels it (from the earlier pass): r\nEarlier beats in this same scene', t2)
        v1 = ws.deep_merge(V2, V1)
        t1 = E.task_build(v1, ep, p, [], "SHOULD NOT APPEAR")
        self.assertNotIn("SHOULD NOT APPEAR", t1)
        self.assertEqual(t1, E.task_build(v1, ep, p, []))


class ClusterRun(unittest.TestCase):
    def test_one_build_call_per_cluster(self):
        prov = FakeProvider()
        r = run2(prov)
        doc = r()
        # all ten moments of the fake episode sit within 35 lines of each other in one scene: one cluster,
        # target = the last with intensity >= 4 (L0052), strength 5
        self.assertEqual(roles(prov), ["find", "build"])
        task = prov.calls[1]["prompt"]
        self.assertIn("The moment: line L0052", task)
        self.assertIn("Earlier beats in this same scene to include as setup/escalation: L0010 (", task)
        s = doc["episode_summary"]
        self.assertEqual((s["n_clusters"], s["n_build_calls"]), (1, 1))
        st = {row["line"]: row["status"] for row in doc["moment_table"]}
        self.assertEqual(st[L(10)], "in_cluster")
        self.assertEqual(st[L(58)], "in_cluster")                    # absorbed (intensity 3 < 5 - 1)
        self.assertEqual(doc["pipeline"]["version"], "emotion_pick/0.2")


# ============================================================================ 0.2 full run without clusters
class FullRun2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prov = FakeProvider()
        cls.r = run2(cls.prov, NO_CLUSTER)
        cls.doc = cls.r()
        cls.props = {p["id"]: p for p in cls.doc["proposals"]}
        cls.rej = {x["id"]: x for x in cls.doc["rejected"]}

    def test_codes(self):
        code = {k: v["reject_reasons"][0].split(":")[0] for k, v in self.rej.items()}
        # 0.1 had e03 K1 (refused lift) and e09 K2 (end 3 lines after the land): e03 is shown now; e09 is built
        # (clamped) and then loses the same-scene dedupe to e08 (spans L0041..L0047 / L0046..L0053 overlap)
        self.assertEqual(code, {"e04": "X1", "e05": "L1", "e06": "N0", "e07": "D1", "e09": "D1", "e10": "V1"})
        self.assertIn("same scene", self.rej["e09"]["reject_reasons"][0])
        self.assertEqual(sorted(self.props), ["e01", "e02", "e03", "e08"])

    def test_refused_lift_kept_and_flagged(self):
        p = self.props["e03"]
        self.assertEqual(p["stop"]["case"], "B_refused_play_start")
        self.assertFalse(p["cold_open"])
        self.assertTrue(any(f.startswith("stop_not_first") for f in p["build_flags"]))

    def test_clamped_end_built(self):
        r = self.rej["e09"]
        self.assertEqual((r["end_line"], r["land"]["line"]), (L(53), L(52)))   # answered end L0055, clamped

    def test_lift_case_recorded(self):
        self.assertEqual(self.props["e02"]["stop"]["case"], "B_lift")
        self.assertEqual(self.props["e01"]["stop"]["case"], "start_line")


# ============================================================================ 5 dedupe
class Dedupe(unittest.TestCase):
    def setUp(self):
        self.ep = episode()
        self.cfg = cfg2()

    def x(self, i, a, b, land, strength=4):
        c = ws.build_clip(self.ep, {"purpose": "standalone", "start_line": L(a), "end_line": L(b),
                                    "cold_open_lines": [], "drops": []}, ws.DEFAULTS["cut"])
        return {"id": i, "clip": c, "payoff": {"id": "p" + i, "intensity": strength},
                "emo": {"land": {"line": L(land)}}}

    def test_same_scene_span_overlap_is_a_duplicate(self):
        # like L0470-L0520 vs L0517-L0526: long clip + short clip sharing a few lines (< 35% of the shorter)
        a, b = self.x("a", 4, 40, 39), self.x("b", 39, 50, 49)
        self.assertLess(ws.overlap_frac(a["clip"], b["clip"]), 0.35)
        kept, dropped = E.dedupe_scene(self.ep, [a, b], self.cfg, {"pa": 4, "pb": 4})
        self.assertEqual(len(kept), 1)
        self.assertIn("same scene", dropped[0]["reject_reasons"][0])
        # equal strength; a plays ~110 s, b ~35 s (inside 30-75 s) -> b kept
        self.assertEqual(kept[0]["id"], "b")

    def test_no_overlap_no_duplicate(self):
        a, b = self.x("a", 4, 20, 19), self.x("b", 21, 35, 34)
        kept, _ = E.dedupe_scene(self.ep, [a, b], self.cfg, {"pa": 4, "pb": 4})
        self.assertEqual(len(kept), 2)

    def test_keep_order(self):
        a, b = self.x("a", 4, 20, 19), self.x("b", 10, 24, 23)       # both ~45-50 s, heavy overlap
        kept, _ = E.dedupe_scene(self.ep, [a, b], self.cfg, {"pa": 5, "pb": 4})
        self.assertEqual(kept[0]["id"], "a")                         # higher strength
        kept, _ = E.dedupe_scene(self.ep, [a, b], self.cfg, {"pa": 4, "pb": 4})
        self.assertEqual(kept[0]["id"], "b")                         # tie, both 30-75 s: the later end
        c = self.x("c", 8, 12, 11)                                   # ~14 s: outside 30-75
        kept, _ = E.dedupe_scene(self.ep, [a, c], self.cfg, {"pa": 4, "pc": 4})
        self.assertEqual([k["id"] for k in kept], ["a"])

    def test_music_between_the_lands_is_another_scene(self):
        spec = block(10, 30, "a") + block(110, 20, "b")
        ep = make_ep(spec, shot_cuts(spec), events=[(100.5, 106.0, "[పాట]")], duration=200.0)

        def x(i, a, b, land):
            c = ws.build_clip(ep, {"purpose": "standalone", "start_line": lid(ep, a), "end_line": lid(ep, b),
                                   "cold_open_lines": [], "drops": []}, ws.DEFAULTS["cut"])
            return {"id": i, "clip": c, "payoff": {"id": "p" + i, "intensity": 4}, "emo": {"land": {"line": lid(ep, land)}}}
        a, b = x("a", "a10a", "a29a", "a28a"), x("b", "a29a", "b8a", "b7a")
        self.assertLess(ws.overlap_frac(a["clip"], b["clip"]), 0.35)
        kept, _ = E.dedupe_scene(ep, [a, b], self.cfg, {"pa": 4, "pb": 4})
        self.assertEqual(len(kept), 2)


# ============================================================================ 6 strict lift-ok
class LiftOk(unittest.TestCase):
    def ep(self, texts, durs=None, loud=None):
        spec, t = [], 10.0
        for k, tx in enumerate(texts):
            d = (durs or {}).get(k, 2.0)
            spec.append((t, t + d, tx))
            t += d + 1.0
        cuts = [round(b + 0.5, 3) for _, b, _ in spec]               # a shot change 0.5 s after every line
        return make_ep(spec, cuts, duration=t + 10, loud=loud)

    def lc(self):
        return copy.deepcopy(V2["lift_ok"])

    def test_plain_lines_are_offered(self):
        ep = self.ep(["a b c", "d e f", "g h i", "j k l", "m n o"])
        self.assertEqual(E.strict_liftable_lines(ep, ws.DEFAULTS["cut"], self.lc()), [L(2), L(3), L(4)])

    def test_continuation_guard(self):
        ep = self.ep(["a b c", "d e f", "అందుకే నేను వచ్చా", "g h i", "j k l"])
        out = E.strict_liftable_lines(ep, ws.DEFAULTS["cut"], self.lc())
        self.assertNotIn(L(2), out)                                  # L0003 continues L0002
        ep = self.ep(["a b c", "d e f", "But then he left", "g h i", "j k l"])
        self.assertNotIn(L(2), E.strict_liftable_lines(ep, ws.DEFAULTS["cut"], self.lc()))
        self.assertIn(L(2), ws.liftable_lines(ep, ws.DEFAULTS["cut"]))   # v1 offered it
        self.assertEqual(E.opens_with_continuation("సో, ఏం చేద్దాం", self.lc()["continuations"]), "సో")
        self.assertIsNone(E.opens_with_continuation("సోఫా మీద కూర్చో", self.lc()["continuations"]))

    def test_acknowledgement_guard(self):
        ep = self.ep(["a b c", "d e f", "అవునా", "g h i", "j k l"], durs={2: 1.0})
        self.assertNotIn(L(2), E.strict_liftable_lines(ep, ws.DEFAULTS["cut"], self.lc()))
        self.assertTrue(E.is_ack("సరే సరే", self.lc()["acks"], 2))
        self.assertFalse(E.is_ack("సరే నేను వస్తా", self.lc()["acks"], 2))

    def test_speech_at_least_one_second(self):
        ep = self.ep(["a b c", "d e", "g h i", "j k l"], durs={1: 0.8})
        self.assertNotIn(L(2), E.strict_liftable_lines(ep, ws.DEFAULTS["cut"], self.lc()))

    def test_quiet_tail(self):
        def loud(a, b):          # speech -20 dB, the 0.5 s tail after each line -45 dB (quiet tail)
            return -45.0 if (b - a) < 0.6 else -20.0
        ep = self.ep(["a b c", "d e f", "g h i", "j k l"], loud=loud)
        out = E.strict_liftable_lines(ep, ws.DEFAULTS["cut"], self.lc())
        self.assertEqual(out, [])
        self.assertTrue(ws.liftable_lines(ep, ws.DEFAULTS["cut"]))  # v1 never measured the tail

    def test_v1_rule_is_webseries_liftable_lines(self):
        ep = episode()
        self.assertEqual(E.lift_ok_lines(ep, ws.DEFAULTS["cut"], ws.deep_merge(V2, V1)),
                         ws.liftable_lines(ep, ws.DEFAULTS["cut"]))


# ============================================================================ 7 internal cuts incl. the lift skip
class Cuts(unittest.TestCase):
    def test_lift_skip_counts_as_a_cut(self):
        ep = episode()
        m = {"purpose": "standalone", "start_line": L(2), "end_line": L(30), "cold_open_lines": [L(15)],
             "drops": [{"first_line": L(5), "last_line": L(5), "why": "a"},
                       {"first_line": L(9), "last_line": L(9), "why": "b"},
                       {"first_line": L(22), "last_line": L(23), "why": "c"}]}
        c = ws.build_clip(ep, m, ws.DEFAULTS["cut"])
        self.assertTrue(c["cold_open"])
        self.assertEqual(len(c["cuts"]), 3)                          # 3 drops + the lift skip -> one drop ignored
        self.assertEqual(sum(1 for cu in c["cuts"] if cu["lift_skip"]), 1)
        self.assertTrue(any("more than 3 internal cuts" in w for w in c["warnings"]))
        self.assertEqual(len(c["drops_used"]), 2)


# ============================================================================ 8 speaker labels, v1 replay, prompts
class Context(unittest.TestCase):
    def setUp(self):
        self.ep = episode()
        self.ep.names = {L(1): "Meera", L(2): "Harshitha"}

    def test_v2_renders_scribe_labels(self):
        a = E.ctx_cfg(cfg2())
        self.assertFalse(a["speaker_names"])
        _, prefix = ws3.context_prompt(self.ep, a)
        self.assertNotIn("Meera", prefix)
        self.assertIn("speaker_0", prefix)

    def test_v1_config_renders_the_01_context_byte_identically(self):
        old = {k: v for k, v in V2.items()}
        old = ws.deep_merge(old, V1)
        old["ctx"] = {}                                              # what emotion_pick/0.1 ran with
        a1, a0 = E.ctx_cfg(ws.deep_merge(V2, V1)), E.ctx_cfg(old)
        self.assertEqual(a1, a0)
        self.assertEqual(ws3.context_prompt(self.ep, a1), ws3.context_prompt(self.ep, a0))
        self.assertIn("Meera", ws3.context_prompt(self.ep, a1)[1])  # 0.1 rendered the corrected names

    def test_v1_prompt_files_and_find_role(self):
        f1, b1 = load_prompt("ws_emotion_find_v1"), load_prompt("ws_emotion_build_v1")
        self.assertEqual((f1.name, f1.version, b1.name, b1.version), ("ws_emotion_find_v1", "1", "ws_emotion_build_v1",
                                                                      "1"))
        self.assertEqual((load_prompt("ws_emotion_find").version, load_prompt("ws_emotion_build").version), ("2", "2"))
        self.assertNotIn("$cluster_context", b1.user)
        self.assertIn("$cluster_context", load_prompt("ws_emotion_build").user)
        cfg = ws.deep_merge(V2, V1)
        self.assertEqual(E.find_role(cfg), E.ROLE_FIND)
        self.assertEqual(E.ctx_cfg(cfg)["prompts"][E.ROLE_FIND], "ws_emotion_find_v1")


# ============================================================================ 9 cliffhanger
class FindSchema(unittest.TestCase):
    def test_cliffhanger_in_02_only(self):
        en2 = E.find_schema(V2)["properties"]["payoffs"]["items"]["properties"]["type"]["enum"]
        self.assertEqual(en2, ["punchline", "reveal", "quotable_line", "emotional_peak", "cliffhanger", "other"])
        en1 = E.find_schema(ws.deep_merge(V2, V1))["properties"]["payoffs"]["items"]["properties"]["type"]["enum"]
        self.assertNotIn("cliffhanger", en1)
        self.assertNotIn("cliffhanger", ws3.PAYOFFS_SCHEMA["properties"]["payoffs"]["items"]["properties"]["type"]["enum"])

    def test_validate_payoffs_accepts_cliffhanger(self):
        ep = episode()
        disc = {"payoffs": [{"line": L(10), "type": "cliffhanger", "emotion": "tension", "intensity": 4,
                             "reason": "r", "lands_on_screen": True}]}
        kept, rej = ws3.validate_payoffs(ep, disc, E.ctx_cfg(cfg2()))
        self.assertEqual((len(kept), kept[0]["type"]), (1, "cliffhanger"))


if __name__ == "__main__":
    unittest.main()
