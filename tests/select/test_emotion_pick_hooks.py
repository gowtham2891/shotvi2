"""Hook filters and the cold-open lead-in (2026-10-24), offline:
1. off by default: DEFAULTS and the 0.1-0.4 configs carry none of the new keys; strict_liftable_lines, lift_ok_near
   and build_lift behave exactly as before (replays stay byte-identical);
2. lift_ok.exclude_dangling: a lift-ok line with a dangling pronoun / demonstrative (వాడు, వీడు, అది, ఇది, అక్కడ,
   ఇక్కడ, వాళ్ళు and their common inflections) is not offered unless it mentions a name (lift_ok.names + the
   speaker-fix cast); a same-speaker run is judged on its whole text; the exclusions are reported;
3. lift_ok.prefer_questions: interrogative lift-ok items (ఎవరు, ఎందుకు, ఎలా, ఏంటి, what, why, how, or a trailing ?)
   are listed first in the build task and tagged [question];
4. cut.lift_start_pad_no_cut_s: with no shot cut within lift_start_snap_s before the lifted line, the lead-in grows
   to 0.5 s instead of 0.12 s (never past half the gap to the previous word); a shot cut in the window still wins;
5. configs/emotion_pick_v4_hooks.json (+ _llmfix) = v4 + the three keys."""
import copy
import json
import unittest

from shotvi.select import emotion_pick as E
from shotvi.select import webseries as ws
from tests.select.test_emotion_pick import L
from tests.select.test_emotion_pick_v2 import V2, alternate
from tests.select.test_webseries_cut import make_ep

CONFIGS = ws.ROOT / "shotvi" / "select" / "configs"


def cfg_file(name):
    return json.loads((CONFIGS / name).read_text(encoding="utf-8"))


def episode(texts, durs=None, gap=1.0):
    """Lines of 2 s (``durs`` overrides per index) with ``gap`` s between them and a shot change 0.5 s after each."""
    spec, t = [], 10.0
    for k, tx in enumerate(texts):
        d = (durs or {}).get(k, 2.0)
        spec.append((t, t + d, tx))
        t += d + gap
    cuts = [round(b + 0.5, 3) for _, b, _ in spec]
    return alternate(make_ep(spec, cuts, duration=t + 10))


def lc(**over):
    return {**copy.deepcopy(V2["lift_ok"]), **over}


PLAIN = ["a b c", "d e f", "g h i", "j k l", "m n o"]


# ============================================================================ 1 off by default
class OffByDefault(unittest.TestCase):
    def test_keys_absent_from_defaults_and_old_configs(self):
        for c in [E.DEFAULTS] + [cfg_file(n) for n in ("emotion_pick_v1.json", "emotion_pick_v2.json",
                                                        "emotion_pick_v4.json", "emotion_pick_v4_llmfix.json")]:
            for k in ("exclude_dangling", "prefer_questions", "names", "dangling_pronouns", "interrogatives"):
                self.assertNotIn(k, c.get("lift_ok") or {}, k)
            self.assertNotIn("lift_start_pad_no_cut_s", ((c.get("v1") or {}).get("cut") or {}))
        self.assertNotIn("lift_start_pad_no_cut_s", ws.DEFAULTS["cut"])

    def test_dangling_lines_are_offered_without_the_key(self):
        ep = episode(["a b c", "వాడు ఎక్కడ ఉన్నాడు", "అది చూశావా", "j k l", "m n o"])
        self.assertEqual(E.strict_liftable_lines(ep, ws.DEFAULTS["cut"], lc()), [L(2), L(3), L(4)])

    def test_lift_ok_near_unchanged_without_the_key(self):
        ep = episode(["a b c", "ఎందుకు వచ్చావ్", "d e f", "g h i", "m n o"])
        p = {"line": L(4)}
        cfg = {"build": E.DEFAULTS["build"], "lift_ok": lc()}
        self.assertEqual(E.lift_ok_near(ep, [L(2), L(3)], p, cfg),
                         f'{L(2)} ("ఎందుకు వచ్చావ్"); {L(3)} ("d e f")')
        self.assertNotIn("[question]", E.lift_ok_near(ep, [L(2), L(3)], p, cfg))


# ============================================================================ 2 dangling pronouns
class Dangling(unittest.TestCase):
    def test_pronoun_forms(self):
        for t in ("వాడు వచ్చాడు", "వాడికి తెలుసు", "అది నాది", "అదే కదా", "ఇక్కడే ఉండు", "వాళ్ళని అడుగు",
                  "వాళ్లు వెళ్ళారు", "దాని గురించి మాట్లాడు"):
            self.assertIsNotNone(E.dangling_pronoun(t), t)
        for t in ("అదిరింది రా", "ఇదివరకు చూశా", "సూర్య వచ్చాడు", "what is this"):
            self.assertIsNone(E.dangling_pronoun(t), t)
        self.assertEqual(E.dangling_pronoun("నేను వాడ్ని కొట్టా", ["వాడ్ని"]), "వాడ్ని")

    def test_excluded_unless_a_name_is_mentioned(self):
        ep = episode(["a b c", "వాడు ఎక్కడ ఉన్నాడు", "అది చూశావా", "ఇక్కడ సూర్య ఉన్నాడు", "m n o", "p q r"])
        ex = {}
        out = E.strict_liftable_lines(ep, ws.DEFAULTS["cut"], lc(exclude_dangling=True), excluded_out=ex)
        self.assertEqual(out, [L(5)])
        self.assertEqual(set(ex), {L(2), L(3), L(4)})
        self.assertIn("dangling", ex[L(2)])
        out = E.strict_liftable_lines(ep, ws.DEFAULTS["cut"], lc(exclude_dangling=True, names=["సూర్య"]))
        self.assertEqual(out, [L(4), L(5)])                      # the name rescues L0004 only

    def test_name_matching(self):
        self.assertTrue(E.mentions_name("Delivery Boy వచ్చాడు", ["delivery boy"]))
        self.assertTrue(E.mentions_name("ఇక్కడ సూర్య, ఉన్నాడు", ["సూర్య"]))
        self.assertFalse(E.mentions_name("Engineer అయిపోయి three years", ["surya"]))
        self.assertFalse(E.mentions_name("సూర్యకాంతి", ["సూర్య"]))     # a whole token, not a substring

    def test_same_speaker_run_is_judged_on_its_whole_text(self):
        # the run L0002+L0003 is 0.8 + 1.0 + 0.8 = 2.6 s of speech (+ pad and tail still under the 3.6 s lift cap)
        ep = episode(["a b c", "d e f", "వాడు వచ్చాడు", "g h i", "m n o"], durs={1: 0.8, 2: 0.8})
        ep.by_id[L(3)]["speaker"] = ep.by_id[L(2)]["speaker"]        # L0002 + L0003 is one turn (run)
        ep.by_id[L(4)]["speaker"] = "speaker_9"
        runs = {}
        out = E.strict_liftable_lines(ep, ws.DEFAULTS["cut"], lc(), runs_out=runs)
        self.assertIn(L(2), out)
        self.assertEqual(runs[L(2)], [L(2), L(3)])
        ex = {}
        out = E.strict_liftable_lines(ep, ws.DEFAULTS["cut"], lc(exclude_dangling=True), excluded_out=ex)
        self.assertNotIn(L(2), out)                                   # the run's second line dangles
        self.assertIn(L(2), ex)

    def test_cast_names_from_a_missing_layer_is_empty(self):
        self.assertEqual(E.cast_names("no-such-video"), [])


# ============================================================================ 3 questions first
class Questions(unittest.TestCase):
    def test_is_interrogative(self):
        for t in ("ఎవరు రా వాడు", "ఎందుకో తెలీదు", "ఎలాంటి మనిషి", "ఏంటే అర్ధరాత్రి?", "ఏమిటి ఇది", "What happened",
                  "why not", "వస్తావా?"):
            self.assertTrue(E.is_interrogative(t), t)
        for t in ("నేను వస్తా", "whatever man", "howling dog", "చాలురా యాక్టింగ్."):
            self.assertFalse(E.is_interrogative(t), t)
        self.assertTrue(E.is_interrogative("when did he come", {"telugu_stems": [], "english": ["when"]}))
        self.assertFalse(E.is_interrogative("what now", {"telugu_stems": [], "english": ["when"]}))

    def test_questions_listed_first_and_tagged(self):
        ep = episode(["a b c", "d e f", "ఎందుకు వచ్చావ్", "g h i", "what is this", "m n o"])
        p = {"line": L(6)}
        cfg = {"build": E.DEFAULTS["build"], "lift_ok": lc(prefer_questions=True)}
        got = E.lift_ok_near(ep, [L(2), L(3), L(4), L(5)], p, cfg)
        self.assertEqual(got, f'{L(3)} ("ఎందుకు వచ్చావ్") [question]; {L(5)} ("what is this") [question]; '
                              f'{L(2)} ("d e f"); {L(4)} ("g h i")')
        # a same-speaker run offered as one item is judged on its whole text
        runs = {L(2): [L(2), L(3)]}
        got = E.lift_ok_near(ep, [L(2), L(4)], p, cfg, runs)
        self.assertTrue(got.startswith(f'{L(2)}+{L(3)} ("d e f" "ఎందుకు వచ్చావ్") [question]; '))
        self.assertEqual(E.lift_ok_near(ep, [], p, cfg), "none")


# ============================================================================ 4 cold-open lead-in
class LeadIn(unittest.TestCase):
    def setUp(self):
        # lines 2 s long, 3 s apart; no shot cut before L0003's first word within 0.4 s (cuts sit 0.5 s after a line)
        self.ep = episode(["a b c", "d e f", "g h i", "j k l", "m n o"], gap=3.0)
        self.S = [r for r in self.ep.lines if self.ep.is_speech(r)]

    def lift(self, cut):
        return ws.build_lift(self.ep, self.S, [2], {0, 1, 3, 4}, cut, "standalone")

    def test_default_pad_is_unchanged(self):
        out = self.lift(ws.DEFAULTS["cut"])
        w0 = self.ep.w0(self.S[2])
        self.assertFalse(out["start_on_cut"])
        self.assertAlmostEqual(out["start"], w0 - 0.12, places=3)
        self.assertAlmostEqual(out["lead_in_s"], 0.12, places=3)

    def test_longer_lead_in_without_a_shot_cut(self):
        cut = {**ws.DEFAULTS["cut"], "lift_start_pad_no_cut_s": 0.5}
        out = self.lift(cut)
        w0 = self.ep.w0(self.S[2])
        self.assertAlmostEqual(out["start"], w0 - 0.5, places=3)
        self.assertAlmostEqual(out["lead_in_s"], 0.5, places=3)
        self.assertGreaterEqual(out["duration"], cut["lift_min_s"])

    def test_lead_in_never_past_half_the_gap(self):
        ep = episode(["a b c", "d e f", "g h i", "j k l", "m n o"], gap=0.6)   # 0.6 s between lines
        S = [r for r in ep.lines if ep.is_speech(r)]
        cut = {**ws.DEFAULTS["cut"], "lift_start_pad_no_cut_s": 0.5, "lift_start_snap_s": 0.0}
        out = ws.build_lift(ep, S, [2], {0, 1, 3, 4}, cut, "standalone")
        gap = ep.w0(S[2]) - ep.prev_end(S[2]["words"][0])
        self.assertAlmostEqual(out["lead_in_s"], gap / 2, places=3)

    def test_a_shot_cut_in_the_window_still_wins(self):
        ep = episode(["a b c", "d e f", "g h i", "j k l", "m n o"], gap=3.0)
        w0 = ep.w0([r for r in ep.lines if ep.is_speech(r)][2])
        ep.cuts = sorted(ep.cuts + [round(w0 - 0.3, 3)])
        S = [r for r in ep.lines if ep.is_speech(r)]
        out = ws.build_lift(ep, S, [2], {0, 1, 3, 4}, {**ws.DEFAULTS["cut"], "lift_start_pad_no_cut_s": 0.5},
                            "standalone")
        self.assertTrue(out["start_on_cut"])
        self.assertAlmostEqual(out["start"], w0 - 0.3, places=3)

    def test_strict_lift_ok_unchanged_by_the_pad(self):
        ep = episode(PLAIN)
        a = E.strict_liftable_lines(ep, ws.DEFAULTS["cut"], lc())
        b = E.strict_liftable_lines(ep, {**ws.DEFAULTS["cut"], "lift_start_pad_no_cut_s": 0.5}, lc())
        self.assertEqual(a, b)


# ============================================================================ 5 configs
class Configs(unittest.TestCase):
    def test_hooks_configs(self):
        v4 = cfg_file("emotion_pick_v4.json")
        for name, src in (("emotion_pick_v4_hooks.json", None), ("emotion_pick_v4_hooks_llmfix.json", "llm_fix")):
            c = cfg_file(name)
            self.assertEqual(c["lift_ok"], {"exclude_dangling": True, "prefer_questions": True})
            self.assertEqual(c["v1"], {"cut": {"lift_start_pad_no_cut_s": 0.5}})
            for k in ("find_prompt", "prompts", "words", "length", "length_cap"):
                self.assertEqual(c[k], v4[k], k)
            self.assertEqual(c.get("speaker_source"), src)
            merged = ws.deep_merge(E.DEFAULTS, c)
            self.assertEqual(merged["lift_ok"]["rule"], "strict")           # the 0.2 lift-ok rule is kept
            self.assertTrue(merged["lift_ok"]["turn_runs"])
            self.assertEqual(E.v1_cfg(merged)["cut"]["lift_start_pad_no_cut_s"], 0.5)
            self.assertEqual(E.v1_cfg(merged)["guardrails"]["min_s"], 10.0)


if __name__ == "__main__":
    unittest.main()
