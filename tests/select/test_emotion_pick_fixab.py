"""emotion_pick/0.2 founder fixes (2026-10-22), offline:
A. the minimum played length is 10 s in 0.2 (and the code_only config) through emotion_pick's own guardrails; the
   v1 config and webseries.DEFAULTS stay at 15 s;
B1. a stop line followed by the same speaker's lines: the whole same-speaker run is lifted as one cold open when its
    speech is <= 3.6 s and it passes every lift check (not over 3.6 s, not touching start / end line); the in-place
    skip removes the whole run (one cut, nothing replayed); the strict lift-ok filter offers the run as ONE item
    ('L0005+L0006 ("..." "...")') and an answer naming the run maps to its first line;
B2. a refused lift whose stop would play after 3 s: start_line advances (<= 4 spoken lines skipped, stop within 5 s,
    not a filler start, no skipped question whose reply still plays, min length kept); else stop_not_first."""
import json
import unittest

from shotvi.select import emotion_pick as E
from shotvi.select import webseries as ws
from tests.select.test_emotion_pick import V1, L, build
from tests.select.test_emotion_pick_v2 import alternate, cfg2, payoff
from tests.select.test_webseries_cut import make_ep

V2 = E.DEFAULTS


def built(ep, p, ans, cfg, lift_ok, vcfg=None):
    x = {"id": "e01", "payoff": p}
    x["moment"], x["emo"], x["flags"] = E.validate_build(ep, p, ans, cfg)
    c = E.build_v2(ep, x, vcfg or ws.DEFAULTS, cfg, set(lift_ok))
    return x, c


def played_words(c):
    return [i for pc in c["pieces"] for i in range(pc["start_word"], pc["end_word"] + 1)]


# ============================================================================ A minimum length
class MinLength(unittest.TestCase):
    def clip(self, d):
        return {"video_duration": d, "time_segments": [{"start": 100.0, "end": 100.0 + d, "kind": "scene"}],
                "pieces": [{"start_word": 0, "end_word": 0}]}

    def test_10_s_in_02_and_code_only(self):
        self.assertEqual(E.v1_cfg(cfg2())["guardrails"]["min_s"], 10.0)
        co = json.loads((ws.ROOT / "shotvi/select/configs/emotion_pick_code_only.json").read_text(encoding="utf-8"))
        self.assertEqual(E.v1_cfg(ws.deep_merge(V2, co))["guardrails"]["min_s"], 10.0)
        ep = make_ep([(10.0, 12.0, "a b")], [9.9])
        self.assertEqual(ws.hard_rules(ep, self.clip(12.5), E.v1_cfg(cfg2())), [])
        self.assertTrue(ws.hard_rules(ep, self.clip(9.5), E.v1_cfg(cfg2()))[0].startswith("L1"))

    def test_15_s_in_the_v1_config_and_webseries(self):
        v1 = E.v1_cfg(ws.deep_merge(V2, V1))
        self.assertEqual(v1["guardrails"]["min_s"], 15.0)
        self.assertEqual(ws.DEFAULTS["guardrails"]["min_s"], 15.0)     # v1 web-series / merged untouched
        ep = make_ep([(10.0, 12.0, "a b")], [9.9])
        self.assertTrue(ws.hard_rules(ep, self.clip(12.5), v1)[0].startswith("L1: plays 12.5 s < min 15 s"))


# ============================================================================ B1 turn lift
def turn_ep(l6_end=23.0, cuts=(9.9, 23.3, 31.5)):
    """L0005 + L0006 one speaker's turn (speaker_0, 20.0 .. l6_end); a shot change only after L0006 (23.3 s)."""
    spec = [(10.0, 12.0, "a b c d"), (12.5, 14.5, "e f g h"), (15.0, 17.0, "i j k l"), (17.5, 19.5, "m n o p"),
            (20.0, 21.2, "q r s"), (21.5, l6_end, "t u v w"), (24.0, 26.0, "x y z"), (26.5, 28.5, "aa bb cc"),
            (29.0, 31.0, "dd ee ff"), (32.0, 34.0, "gg hh ii")]
    ep = alternate(make_ep(spec, list(cuts), duration=60.0))
    ep.by_id[L(6)]["speaker"] = ep.by_id[L(5)]["speaker"]
    for k in range(7, 11):                                           # alternate again after the turn
        ep.by_id[L(k)]["speaker"] = f"speaker_{k % 2}"
    return ep


class TurnLift(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg2()
        self.p = payoff(9)

    def test_two_line_turn_lifted_whole(self):
        ep = turn_ep()
        self.assertEqual(E.turn_run(ep, L(5)), [L(5), L(6)])
        x, c = built(ep, self.p, build(1, 9, stop=5, land=9), self.cfg, {L(5)})
        self.assertEqual((x["emo"]["stop"]["case"], x["emo"]["stop"]["hook_fix"]), ("B1_turn_lift", "B1_turn_lift"))
        self.assertEqual(c["cold_open"]["lines"], [L(5), L(6)])
        self.assertEqual(x["emo"]["stop"]["lines"], [L(5), L(6)])
        self.assertTrue(x["emo"]["stop"]["cold_open"])
        w = played_words(c)
        self.assertEqual(len(w), len(set(w)))                          # nothing replayed (S2)
        self.assertEqual(ws.hard_rules(ep, c, ws.DEFAULTS), [])
        self.assertEqual(sum(1 for cu in c["cuts"] if cu["lift_skip"]), 1)   # the whole run = one in-place cut
        self.assertEqual(len(c["cuts"]), 1)
        a, b = ep.by_id[L(6)]["words"]
        self.assertEqual(w[:b - ep.by_id[L(5)]["words"][0] + 1], list(range(ep.by_id[L(5)]["words"][0], b + 1)))
        ck = E.check_built(ep, c, x["emo"], self.cfg)
        self.assertLess(ck["stop_play_s"], 0.5)
        self.assertNotIn("stop_not_first", ck)

    def test_turn_over_3_6_s_not_lifted(self):
        ep = turn_ep(l6_end=23.9, cuts=(9.9, 24.2, 31.5))              # 20.0 .. 23.88: 3.9 s of speech
        x, c = built(ep, self.p, build(1, 9, stop=5, land=9), self.cfg, {L(5)})
        self.assertFalse(c["cold_open"])
        self.assertNotEqual(x["emo"]["stop"]["case"], "B1_turn_lift")
        self.assertTrue(any("the turn L0005+L0006 is 3.9 s of speech (> 3.6 s)" in f for f in x["flags"]))

    def test_turn_touching_the_end_line_not_lifted(self):
        ep = turn_ep()
        p = payoff(4)
        x, c = built(ep, p, build(1, 6, stop=5, land=4, reaction=L(5)), self.cfg, {L(5)})
        self.assertEqual(x["moment"]["end_line"], L(6))
        self.assertFalse(c["cold_open"])
        self.assertTrue(any("the turn L0005+L0006 reaches the end line L0006" in f for f in x["flags"]))

    def test_turn_lift_off_keeps_the_old_refusal(self):
        ep = turn_ep()
        off = cfg2({"cold_open": {"turn_lift": False, "start_advance": False}})
        x, c = built(ep, self.p, build(1, 9, stop=5, land=9), off, {L(5)})
        self.assertFalse(c["cold_open"])
        self.assertEqual(x["emo"]["stop"]["case"], "B_refused_play_start")
        self.assertTrue(any("same speaker continues in L0006 (Scribe label speaker_0)); played in place" in f
                            for f in x["flags"]))

    def test_lift_ok_offers_the_run_as_one_item(self):
        ep = turn_ep()
        lc = dict(V2["lift_ok"])
        runs = {}
        ids = E.strict_liftable_lines(ep, ws.DEFAULTS["cut"], lc, runs_out=runs)
        self.assertIn(L(5), ids)
        self.assertNotIn(L(6), runs)
        self.assertEqual(runs, {L(5): [L(5), L(6)]})
        txt = E.lift_ok_near(ep, ids, payoff(9), self.cfg, runs)
        self.assertIn('L0005+L0006 ("q r s" "t u v w")', txt)
        task = E.task_build(self.cfg, ep, payoff(9), ids, "", runs)
        self.assertIn('L0005+L0006 ("q r s" "t u v w")', task)
        self.assertNotIn(L(5), E.strict_liftable_lines(ep, ws.DEFAULTS["cut"], {**lc, "turn_runs": False}))
        # the turn over 3.6 s is not offered
        long = turn_ep(l6_end=23.9, cuts=(9.9, 24.2, 31.5))
        r2 = {}
        self.assertNotIn(L(5), E.strict_liftable_lines(long, ws.DEFAULTS["cut"], lc, runs_out=r2))
        self.assertEqual(r2, {})
        # no run offered: the text is merged.lift_ok_near's, byte for byte
        self.assertEqual(E.lift_ok_near(ep, [L(3)], payoff(9), self.cfg, {}),
                         E.M.lift_ok_near(ep, [L(3)], payoff(9), self.cfg, window=self.cfg["build"]))

    def test_answer_naming_the_run_maps_to_its_first_line(self):
        ep = turn_ep()
        ans = build(1, 9, stop=5, land=9)
        ans["stop"]["line"] = "L0005+L0006"
        x, c = built(ep, self.p, ans, self.cfg, {L(5)})
        self.assertEqual(x["emo"]["stop"]["line"], L(5))
        self.assertEqual(c["cold_open"]["lines"], [L(5), L(6)])


# ============================================================================ B2 start advance
def grid_ep(n=12, texts=None, speakers=None):
    """n spoken lines, line k at 10 + 3 (k - 1) s, 2 s long; no shot change inside the scene (no lift possible)."""
    spec = [(10.0 + 3 * k, 12.0 + 3 * k, (texts or {}).get(k + 1, f"w{k}a w{k}b w{k}c")) for k in range(n)]
    ep = alternate(make_ep(spec, [9.9, 12.0 + 3 * n + 2.5], duration=200.0))
    for k, s in (speakers or {}).items():
        ep.by_id[L(k)]["speaker"] = s
    return ep


class StartAdvance(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg2()

    def test_advance_within_4_lines_and_5_s(self):
        ep = grid_ep()
        p = payoff(11)
        x, c = built(ep, p, build(1, 11, stop=5, land=11), self.cfg, set())
        # from L0002 / L0003 the stop plays at 9.3 / 6.3 s (> 5 s); from L0004 at 3.3 s
        self.assertEqual((x["emo"]["stop"]["case"], c["start_line"], x["moment"]["start_line"]),
                         ("B2_start_advance", L(4), L(4)))
        self.assertEqual(x["emo"]["stop"]["hook_fix"], "B2_start_advance")
        self.assertTrue(any("start advanced L0001 -> L0004 (3 spoken line(s) skipped)" in f for f in x["flags"]))
        ck = E.check_built(ep, c, x["emo"], self.cfg)
        self.assertLessEqual(ck["stop_play_s"], 5.0)
        self.assertNotIn("stop_not_first", ck)

    def test_no_advance_past_4_lines(self):
        ep = grid_ep()
        p = payoff(11)
        x, c = built(ep, p, build(1, 11, stop=7, land=11), self.cfg, set())
        self.assertEqual((x["emo"]["stop"]["case"], c["start_line"]), ("B_refused_play_start", L(1)))
        self.assertEqual(x["emo"]["stop"]["hook_fix"], "stop_not_first")
        self.assertTrue(any("the stop is 6 spoken lines after L0001" in f for f in x["flags"]))
        self.assertIn("stop_not_first", E.check_built(ep, c, x["emo"], self.cfg))

    def test_refused_when_a_skipped_questions_reply_is_kept(self):
        # L0002 asks (speaker_1), L0003 the same speaker goes on, L0004 (speaker_0, the stop) replies: every start
        # that skips L0002 leaves its reply in the clip
        ep = grid_ep(texts={2: "w1a w1b nijama?"}, speakers={3: "speaker_1", 4: "speaker_0"})
        p = payoff(11)
        x, c = built(ep, p, build(1, 11, stop=4, land=11), self.cfg, set())
        self.assertEqual((x["emo"]["stop"]["case"], c["start_line"]), ("B_refused_play_start", L(1)))
        self.assertEqual(x["emo"]["stop"]["hook_fix"], "stop_not_first")
        self.assertTrue(any("L0003: skipped question L0002 is answered by L0004, still in the clip" in f
                            for f in x["flags"]))
        # the same lines without the question mark: the advance is allowed
        ep2 = grid_ep(speakers={3: "speaker_1", 4: "speaker_0"})
        x2, c2 = built(ep2, p, build(1, 11, stop=4, land=11), self.cfg, set())
        self.assertEqual((x2["emo"]["stop"]["case"], c2["start_line"]), ("B2_start_advance", L(3)))

    def test_refused_when_the_clip_would_fall_under_min_length(self):
        ep = grid_ep()
        p = payoff(7)
        # from L0004 the clip plays L0004..L0007 (~12.6 s): under the 15 s of ws.DEFAULTS, over 0.2's 10 s
        x, c = built(ep, p, build(1, 7, stop=5, land=7), self.cfg, set())
        self.assertEqual((x["emo"]["stop"]["case"], c["start_line"]), ("B_refused_play_start", L(1)))
        self.assertTrue(any("(< min 15 s)" in f for f in x["flags"]))
        x2, c2 = built(ep, p, build(1, 7, stop=5, land=7), self.cfg, set(), vcfg=E.v1_cfg(self.cfg))
        self.assertEqual((x2["emo"]["stop"]["case"], c2["start_line"]), ("B2_start_advance", L(4)))
        self.assertGreaterEqual(c2["video_duration"], 10.0)

    def test_filler_start_is_skipped(self):
        ep = grid_ep(texts={4: "ఉమ్"})
        p = payoff(11)
        x, c = built(ep, p, build(1, 11, stop=5, land=11), self.cfg, set())
        self.assertTrue(any("passed over: L0002: stop plays at 9.3 s (> 5 s); L0003: stop plays at 6.3 s (> 5 s); "
                            "L0004: filler / acknowledgement only" in f for f in x["flags"]))
        self.assertEqual((x["emo"]["stop"]["case"], c["start_line"]), ("B2_start_advance", L(5)))

    def test_stop_already_within_3_s_no_advance(self):
        ep = grid_ep()
        p = payoff(11)
        # stop L0003 is 2 lines / 6 s after L0001 (not Case A): lift refused, plays at 6.3 s -> advance to L0002
        x, c = built(ep, p, build(1, 11, stop=3, land=11), self.cfg, set())
        self.assertEqual(c["start_line"], L(2))
        off = cfg2({"cold_open": {"start_advance": False}})
        x2, c2 = built(ep, p, build(1, 11, stop=3, land=11), off, set())
        self.assertEqual((x2["emo"]["stop"]["case"], c2["start_line"]), ("B_refused_play_start", L(1)))


if __name__ == "__main__":
    unittest.main()
