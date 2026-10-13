"""Video gate (shotvi/select/video_gate.py), offline: synthetic episode, fake LLM (no provider, no money).
Covers the fix rules (never a later start, guardrails, exactly one fix round), the line / shot id -> time conversion,
the window tags, the tiers, and that the gate is off in the default pipeline config."""
import json
import unittest

from shotvi.llm import Ledger
from shotvi.llm.base import ModelSpec, RawResponse
from shotvi.select import video_gate as vg
from shotvi.select import webseries as ws
from shotvi.select.lines import LineIndex
from shotvi.select.webseries import DEFAULTS, Reject, build_clip

from tests.select.test_webseries_cut import make_ep

try:
    import jsonschema
except ImportError:   # pragma: no cover
    jsonschema = None

# 60 two-second lines every 2.5 s from t=10 (L0001..L0060), a 15.5 s silent stretch with shot cuts, then L0061..
SPEC = [(10.0 + 2.5 * k, 12.0 + 2.5 * k, f"w{k}a w{k}b w{k}c w{k}d") for k in range(60)] + \
       [(175.0, 177.0, "n1 n2 n3 n4"), (178.0, 180.0, "n5 n6 n7 n8")]
CUTS = [162.0, 165.0, 168.0, 171.0]
CFG = ws.deep_merge(DEFAULTS, {"video_gate": {"enabled": True}})


def episode():
    ep = make_ep(SPEC, CUTS, duration=200.0)
    ixs = LineIndex(lines=[dict(r) for r in ep.lines], cuts=CUTS, shot_lines=True)
    return ep, ixs


def moment(s, e, **kw):
    return {"purpose": "standalone", "start_line": s, "end_line": e, "cold_open_lines": [], "drops": [], **kw}


def answer(verdict, hook=4, standalone=4, payoff=4, overall=4, new_start="", new_end=""):
    sc = lambda n: {"score": n, "why": "seen on screen"}  # noqa: E731
    return {"last_beat": {"line": "L0001", "on_screen": "a laugh"}, "hook": sc(hook), "standalone": sc(standalone),
            "payoff": sc(payoff), "payoff_kind": "laugh_beat", "overall": sc(overall), "verdict": verdict,
            "fix": {"new_start": new_start, "new_end": new_end, "why": "x" if verdict == "fix" else ""}}


class FakeLLM:
    """Returns queued answers (schema-validated); records every prompt."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []
        self.ledger = Ledger()
        self.cache_only = False

    def generate_json(self, role, spec, system, prompt, schema, meta=None, want_raw=False, media=None, **kw):
        obj = self.answers.pop(0)
        if jsonschema is not None:
            jsonschema.validate(obj, schema)
        self.prompts.append({"role": role, "prompt": prompt, "media": media})
        raw = RawResponse(json.dumps(obj), 1000, 200, 300)
        self.ledger.record(role, spec, raw, cached=False, latency=0.0)
        return (obj, raw) if want_raw else obj


MEDIA = {"file_uri": "files/x", "mime_type": "video/mp4", "key": "vid.mp4:1"}


def gate_one(ep, ixs, m, answers):
    c = build_clip(ep, m, CFG["cut"])
    x = {"id": "w01", "moment": m, "clip": c}
    llm = FakeLLM(answers)
    runner = vg.GateRunner(ep, ixs, CFG, llm, MEDIA, log=lambda *a: None)
    return runner.gate(x), x, llm


def whole_lines(ep, clip):
    """Every spoken line is played whole or not at all (no word trimmed inside a line)."""
    played = {i for p in clip["pieces"] for i in range(p["start_word"], p["end_word"] + 1)}
    for r in ep.speech:
        ws_ = set(range(r["words"][0], r["words"][1] + 1))
        if ws_ & played and not ws_ <= played:
            return False
    return True


class FixRules(unittest.TestCase):
    def setUp(self):
        self.ep, self.ixs = episode()
        self.m = moment("L0021", "L0030")
        self.c = build_clip(self.ep, self.m, CFG["cut"])
        _, _, self.wins, self.shown = vg.gate_prompt(self.ep, self.ixs, self.c, "w01", "standalone", vg.DEFAULTS)

    def fix(self, s="", e=""):
        return vg.resolve_fix(self.ep, self.ixs, self.c, {"new_start": s, "new_end": e, "why": ""}, self.shown)

    def test_start_later_is_rejected(self):
        with self.assertRaisesRegex(Reject, "start later"):
            self.fix(s="L0023")

    def test_start_earlier_end_later_end_earlier(self):
        self.assertEqual(self.fix(s="L0016")["changes"], ["start_earlier"])
        self.assertEqual(self.fix(e="L0033")["changes"], ["end_later"])
        self.assertEqual(self.fix(e="L0028")["changes"], ["end_earlier"])
        both = self.fix(s="L0017", e="L0032")
        self.assertEqual((both["start_line"], both["end_line"]), ("L0017", "L0032"))

    def test_id_outside_window_or_unknown_or_no_change(self):
        with self.assertRaisesRegex(Reject, "not a line of the window"):
            self.fix(s="L0001")          # before the 40 s window
        with self.assertRaisesRegex(Reject, "not a line of the window"):
            self.fix(e="L9999")
        with self.assertRaisesRegex(Reject, "changes nothing"):
            self.fix(s="L0021", e="L0030")
        with self.assertRaisesRegex(Reject, "without a new start or end"):
            self.fix()

    def test_ids_are_normalised(self):
        self.assertEqual(self.fix(s="l16")["start_line"], "L0016")

    def test_recut_starts_on_first_word_and_keeps_whole_lines(self):
        rf = self.fix(s="L0016", e="L0033")
        c2 = vg.recut(self.ep, self.m, self.c, rf, CFG["cut"], vg.DEFAULTS, CFG)
        w0 = self.ep.w0(self.ep.by_id["L0016"])
        self.assertLessEqual(w0 - c2["time_segments"][0]["start"], 0.3 + 1e-6)
        self.assertGreaterEqual(c2["time_segments"][0]["start"], self.ep.words[self.ep.by_id["L0016"]["words"][0] - 1]["end"])
        self.assertEqual((c2["start_line"], c2["end_line"]), ("L0016", "L0033"))
        self.assertTrue(whole_lines(self.ep, c2))
        # the end never plays into the next line
        nxt = self.ep.w0(self.ep.by_id["L0034"])
        self.assertLess(c2["time_segments"][-1]["end"], nxt)

    def test_recut_guardrails(self):
        short = self.fix(e="L0023")       # L0021..L0023: ~7 s
        with self.assertRaisesRegex(Reject, "< 15"):
            vg.recut(self.ep, self.m, self.c, short, CFG["cut"], vg.DEFAULTS, CFG)
        m = moment("L0011", "L0050")
        c = build_clip(self.ep, m, CFG["cut"])
        _, _, _, shown = vg.gate_prompt(self.ep, self.ixs, c, "w01", "standalone", vg.DEFAULTS)
        rf = vg.resolve_fix(self.ep, self.ixs, c, {"new_start": "", "new_end": "L0059", "why": ""}, shown)
        with self.assertRaisesRegex(Reject, "> 120"):
            vg.recut(self.ep, m, c, rf, CFG["cut"], vg.DEFAULTS, CFG)


class ShotIds(unittest.TestCase):
    def setUp(self):
        self.ep, self.ixs = episode()
        self.m = moment("L0040", "L0060")
        self.c = build_clip(self.ep, self.m, CFG["cut"])
        _, _, _, self.shown = vg.gate_prompt(self.ep, self.ixs, self.c, "w01", "standalone", vg.DEFAULTS)

    def test_shot_lines_listed_and_end_on_shot_holds_to_its_cut(self):
        shots = [i for i in self.shown if i.startswith("S")]
        self.assertTrue(shots)
        s2 = next(r for r in self.ixs.lines if r["kind"] == "shot" and abs(r["end"] - 165.0) < 1e-6)
        rf = vg.resolve_fix(self.ep, self.ixs, self.c, {"new_start": "", "new_end": s2["id"], "why": ""}, self.shown)
        self.assertEqual(rf["end_line"], "L0060")
        self.assertEqual(rf["changes"], ["end_later"])
        c2 = vg.recut(self.ep, self.m, self.c, rf, CFG["cut"], vg.DEFAULTS, CFG)
        self.assertAlmostEqual(c2["time_segments"][-1]["end"], 165.0 - CFG["cut"]["lift_cut_eps_s"], places=3)
        self.assertTrue(c2["end"]["on_shot_cut"])
        self.assertEqual(c2["end"]["rule"], "gate_hold")

    def test_shot_end_hold_is_capped(self):
        s4 = next(r for r in self.ixs.lines if r["kind"] == "shot" and abs(r["end"] - 171.0) < 1e-6)
        rf = vg.resolve_fix(self.ep, self.ixs, self.c, {"new_start": "", "new_end": s4["id"], "why": ""}, self.shown)
        c2 = vg.recut(self.ep, self.m, self.c, rf, CFG["cut"], vg.DEFAULTS, CFG)
        payoff_end = self.ep.w1(self.ep.by_id["L0060"])
        self.assertLessEqual(c2["time_segments"][-1]["end"], payoff_end + vg.DEFAULTS["max_hold_s"] + 1e-6)

    def test_window_tags_and_markers(self):
        _, user, wins, _ = vg.gate_prompt(self.ep, self.ixs, self.c, "w01", "standalone", vg.DEFAULTS)
        self.assertEqual(len(wins), 1)
        self.assertAlmostEqual(wins[0][0], self.c["time_segments"][0]["start"] - 40.0, places=2)
        self.assertIn("L0039 [", user)
        self.assertRegex(user, r"L0039 \[[^\]]+\] <BEFORE CLIP>")
        self.assertRegex(user, r"L0040 \[[^\]]+\] <IN CLIP>")
        self.assertRegex(user, r"L0061 \[[^\]]+\] <AFTER CLIP>")
        self.assertIn("==== CLIP STARTS", user)
        self.assertIn("==== CLIP ENDS", user)
        self.assertNotIn("$", user)

    def test_cut_and_cold_open_tags(self):
        m = moment("L0021", "L0035", drops=[{"first_line": "L0027", "last_line": "L0028", "why": "side"}],
                   cold_open_lines=[])
        c = build_clip(self.ep, m, CFG["cut"])
        _, user, _, _ = vg.gate_prompt(self.ep, self.ixs, c, "w01", "standalone", vg.DEFAULTS)
        self.assertRegex(user, r"L0027 \[[^\]]+\] <CUT>")
        self.assertIn("==== CUT", user)
        self.assertIn("==== CLIP RESUMES", user)


class OneRound(unittest.TestCase):
    def setUp(self):
        self.ep, self.ixs = episode()
        self.m = moment("L0021", "L0030")

    def test_keep_needs_no_recheck(self):
        rec, x, llm = gate_one(self.ep, self.ixs, self.m, [answer("keep")])
        self.assertEqual(len(llm.prompts), 1)
        self.assertEqual((rec["final"], rec["tier"], rec["gate_fixed"]), ("keep", "recommended", False))
        self.assertEqual(llm.prompts[0]["role"], "ws_gate")
        self.assertTrue(llm.prompts[0]["media"][0]["fps"] == 1.0)

    def test_inconsistent_keep_is_a_drop(self):
        rec, _, llm = gate_one(self.ep, self.ixs, self.m, [answer("keep", payoff=3)])
        self.assertEqual(rec["final"], "drop")
        self.assertEqual(len(llm.prompts), 1)

    def test_fix_then_keep(self):
        rec, x, llm = gate_one(self.ep, self.ixs, self.m, [answer("fix", payoff=3, new_end="L0032"), answer("keep")])
        self.assertEqual(len(llm.prompts), 2)
        self.assertEqual((rec["final"], rec["tier"], rec["gate_fixed"]), ("keep", "recommended", True))
        self.assertEqual(x["clip"]["end_line"], "L0032")
        self.assertIn("L0032", llm.prompts[1]["prompt"])
        self.assertRegex(llm.prompts[1]["prompt"], r"L0032 \[[^\]]+\] <IN CLIP>")

    def test_second_fix_is_never_applied(self):
        rec, x, llm = gate_one(self.ep, self.ixs, self.m, [answer("fix", payoff=3, new_end="L0032"),
                                                          answer("fix", payoff=3, new_end="L0034")])
        self.assertEqual(len(llm.prompts), 2)
        self.assertEqual(rec["final"], "drop")
        self.assertEqual(x["clip"]["end_line"], "L0030")      # the shown clip stays the original cut
        self.assertEqual(x["gate_clip"]["end_line"], "L0032")  # the one re-cut is kept for review

    def test_invalid_fix_is_a_drop_without_recheck(self):
        rec, x, llm = gate_one(self.ep, self.ixs, self.m, [answer("fix", payoff=3, new_start="L0024")])
        self.assertEqual(len(llm.prompts), 1)
        self.assertEqual(rec["final"], "drop")
        self.assertIn("start later", rec["fix_rejected"])


class PipelineHook(unittest.TestCase):
    def test_off_by_default(self):
        self.assertFalse(ws.gate_on(DEFAULTS))
        self.assertFalse(vg.gate_cfg(DEFAULTS)["enabled"])

    def test_gate_shown_tiers(self):
        ep, ixs = episode()
        xs = []
        for k, (s, e) in enumerate([("L0021", "L0030"), ("L0035", "L0045")]):
            m = moment(s, e)
            xs.append({"id": f"w0{k + 1}", "moment": m, "clip": build_clip(ep, m, CFG["cut"]), "_disc": k,
                       "tier": "worth_a_look"})
        llm = FakeLLM([answer("keep", overall=5), answer("drop", payoff=2, overall=2)])
        rejected = []
        shown = vg.gate_shown(ep, xs, rejected, CFG, llm, log=lambda *a: None, media_base=MEDIA, ixs=ixs)
        self.assertEqual([x["id"] for x in shown], ["w01"])
        self.assertEqual(shown[0]["tier"], "recommended")
        self.assertEqual(rejected[0]["id"], "w02")
        self.assertTrue(rejected[0]["reject_reasons"][0].startswith("G1"))


if __name__ == "__main__":
    unittest.main()
