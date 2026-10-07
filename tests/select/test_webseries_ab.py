"""A/B arm support for the web-series pipeline (shotvi/select/webseries.py), offline:
1. every B prompt (prompts/ws_discover_b_*.md) is the baseline ws_discover.md word for word plus insertions only;
2. a prompt-file override changes exactly the discover prompt and is recorded;
3. the spend guard runs before EVERY billed attempt (retries included), and once per episode before an uncached
   discover (discover + review together), so no discover is paid whose review the limit would refuse;
4. a dry run makes no provider call of any kind (countTokens included) and writes nothing;
5. the expected-cost projection uses a calibration run's token counts (review only when its settings match);
6. a B prompt may declare optional answer fields (front matter ``answer_fields``): the baseline schema object (part
   of the cache key) is untouched, the arm's schema only adds optional fields, and they reach the output docs."""
import difflib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shotvi.llm import Ledger, LLMClient, ModelSpec
from shotvi.llm.base import RawResponse, RetryableError
from shotvi.select import webseries as ws
from shotvi.select.prompts import PROMPT_DIR, load_prompt
from tests.select.test_webseries_cut import CUTS, SCENE, make_ep

SPEC = ModelSpec.from_dict({"provider": "gemini", "model": "gemini-3.8-flash"})


class BPromptsAreBaselinePlusInsertions(unittest.TestCase):
    def test_b_prompts_only_insert_text(self):
        base = load_prompt("ws_discover")
        arms = sorted(PROMPT_DIR.glob("ws_discover_b_*.md"))
        self.assertTrue(arms, "no B prompt found")
        for f in arms:
            b = load_prompt(f.stem)
            self.assertEqual(b.name, f.stem)
            self.assertNotEqual(b.version, base.version, f"{f.name}: bump the version")
            for part in ("system", "user"):
                x, y = getattr(base, part), getattr(b, part)
                ops = [o for o in difflib.SequenceMatcher(None, x, y, autojunk=False).get_opcodes()
                       if o[0] != "equal"]
                self.assertTrue(all(o[0] == "insert" for o in ops), f"{f.name} {part}: not insert-only: {ops}")
            self.assertNotEqual(base.system + base.user, b.system + b.user, f"{f.name}: no change")


class PayoffArmAnswerFields(unittest.TestCase):
    ARM = "ws_discover_b_payoff"

    def test_baseline_schema_is_the_same_object(self):
        self.assertIs(ws.discover_schema(ws.DEFAULTS), ws.DISCOVER_SCHEMA)
        self.assertNotIn("payoff_type", ws.DISCOVER_SCHEMA["properties"]["moments"]["items"]["properties"])

    def test_arm_adds_optional_fields_only(self):
        import jsonschema
        cfg = ws.deep_merge(ws.DEFAULTS, {"prompts": {"ws_discover": self.ARM}})
        s = ws.discover_schema(cfg)
        item, base = s["properties"]["moments"]["items"], ws.DISCOVER_SCHEMA["properties"]["moments"]["items"]
        self.assertEqual(item["required"], base["required"])
        self.assertEqual({k: v for k, v in item["properties"].items() if k not in ws.DISCOVER_EXTRA_FIELDS},
                         base["properties"])
        self.assertEqual(list(item["properties"])[:2], ["payoff_line", "payoff_type"])
        m = {"purpose": "standalone", "start_line": "L0001", "end_line": "L0008", "what_happens": "", "hook": "",
             "payoff": "", "opening_strength": "weak", "cold_open_lines": [], "cold_open_why": "", "drops": [],
             "spoiler": False, "characters": [], "visual_dependency": "low", "category": "story", "strength": 3}
        jsonschema.validate({"episode_notes": "", "moments": [m]}, s)                     # baseline-shaped answer
        jsonschema.validate({"episode_notes": "", "moments": [{**m, "payoff_type": "punchline",
                                                                 "payoff_line": "x"}]}, s)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"episode_notes": "", "moments": [{**m, "payoff_type": "joke"}]}, s)

    def test_unknown_answer_field_is_refused(self):
        with mock.patch("shotvi.select.prompts.load_prompt",
                        return_value=mock.Mock(meta={"answer_fields": "payoff_type, mood"})):
            with self.assertRaises(ValueError):
                ws.discover_schema(ws.DEFAULTS)

    def test_fields_reach_the_proposal_and_the_md(self):
        from tests.select.test_webseries_cut import moment
        from tests.select.test_webseries_v02 import review
        ep = make_ep(SCENE, CUTS)
        cfg = ws.deep_merge(ws.DEFAULTS, {})
        mm = moment(payoff_type="punchline", payoff_line="payoff line end ok", strength=4)
        c = ws.build_clip(ep, mm, cfg["cut"])
        x = {"id": "w01", "moment": mm, "clip": c}
        ws.apply_review(ep, [x], {"w01": review(hook=4, standalone=4, payoff=4, overall=4,
                                                hook_without_cold_open=4, overall_without_cold_open=4)}, cfg)
        rejected = []
        shown = ws.rank_clips([x], rejected, cfg)
        doc = ws.proposal_doc(ep, shown[0], 1, cfg, {})
        self.assertEqual(doc["discovery"]["payoff_type"], "punchline")
        self.assertEqual(doc["discovery"]["payoff_line"], "payoff line end ok")
        self.assertNotIn("payoff_type", ws.proposal_doc(ep, {**x, "moment": moment()}, 1, cfg, {})["discovery"])
        rj = ws.rejected_doc(ep, {"id": "w02", "moment": mm, "reject_reasons": ["x"]}, cfg)
        self.assertEqual(rj["moment"]["payoff_type"], "punchline")
        md = ws.clips_md(ep, {"video_id": "v", "run_id": "r", "cost": {"usd": 0.0}, "proposals": [doc], "rejected": []})
        self.assertIn("- payoff (punchline): payoff line end ok", md)


class PromptOverride(unittest.TestCase):
    def test_override_swaps_only_the_discover_prompt(self):
        ep = make_ep(SCENE, CUTS)
        cfg_b = ws.deep_merge(ws.DEFAULTS, {"prompts": {"ws_discover": "ws_discover_b_setup"}})
        s_a, u_a = ws.discover_prompt(ep, None, ws.DEFAULTS)
        s_b, u_b = ws.discover_prompt(ep, None, cfg_b)
        self.assertEqual(u_a, u_b)
        self.assertNotEqual(s_a, s_b)
        self.assertEqual(s_b, load_prompt("ws_discover_b_setup").system)
        self.assertEqual(ws.prompt_file(cfg_b, "ws_review"), "ws_review")
        self.assertEqual(ws.review_prompt(["x"], cfg_b), ws.review_prompt(["x"], ws.DEFAULTS))

    def test_cli_rejects_unknown_role_or_file(self):
        for bad in ("ws_discover=no_such_prompt_file", "nope=ws_discover"):
            with self.assertRaises(SystemExit):
                ws.main(["vid", "--dry-run", "--prompt", bad])


class _FlakyProvider:
    """First attempt fails with a retryable error, then answers."""
    def __init__(self):
        self.calls = 0

    def generate(self, spec, system, prompt, schema, media=None):
        self.calls += 1
        if self.calls == 1:
            raise RetryableError("503")
        return RawResponse('{"ok": true}', 10, 5, 0, "m")


class GuardOnEveryAttempt(unittest.TestCase):
    def test_retry_is_guarded(self):
        prov = _FlakyProvider()
        ledger = iter([1300.0, 1308.5])   # the failed first attempt "cost" money: the retry would cross 1309
        guard = ws.Guard(1309.0)
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("shotvi.llm.client.get_provider", return_value=prov), \
                mock.patch("shotvi.llm.spend.total_inr", side_effect=lambda: next(ledger)), \
                mock.patch("shotvi.llm.client.time.sleep"):
            llm = LLMClient(Ledger(), cache_dir=d)
            llm.before_call = lambda role, spec: guard.check(role, 1.0)
            with self.assertRaises(ws.BudgetStop):
                llm.generate_json("ws_discover", SPEC, "s", "p", None)
        self.assertEqual(prov.calls, 1)

    def test_cache_hit_never_reaches_the_guard(self):
        prov = _FlakyProvider()
        seen = []
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("shotvi.llm.client.get_provider", return_value=prov), \
                mock.patch("shotvi.llm.client.time.sleep"):
            llm = LLMClient(Ledger(), cache_dir=d)
            llm.before_call = lambda role, spec: seen.append(role)
            llm.generate_json("r", SPEC, "s", "p", None)
            n = len(seen)
            llm.generate_json("r", SPEC, "s", "p", None)      # cached now
        self.assertEqual(n, 2)                                # the failed attempt and the retry
        self.assertEqual(len(seen), 2)


def _fake_load(ep):
    tmp = Path(tempfile.mkdtemp()) / "t.json"
    tmp.write_text("{}", encoding="utf-8")
    return lambda vid, with_loudness=True: (ep, None, {}, tmp)


class EpisodeRuns(unittest.TestCase):
    def setUp(self):
        self.ep = make_ep(SCENE, CUTS)
        self.cfg = ws.deep_merge(ws.DEFAULTS, {"prompts": {"ws_discover": "ws_discover_b_setup"}})
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def llm(self):
        return LLMClient(Ledger(), cache_dir=self.tmp.name)

    def test_dry_run_makes_no_call_and_writes_nothing(self):
        out = Path(self.tmp.name) / "out"
        with mock.patch.object(ws, "load_episode", _fake_load(self.ep)), \
                mock.patch("shotvi.llm.client.get_provider") as gp:
            r = ws.run_episode("vid", self.cfg, self.llm(), ws.Guard(None), out, log=lambda *_: None, dry_run=True)
            gp.assert_not_called()
        self.assertEqual(r["first_paid_stage"], "ws_discover")
        self.assertGreater(r["would_bill_inr_guard"], r["would_bill_inr_expected"])
        self.assertGreater(r["would_bill_inr_expected"], 0)
        self.assertFalse(out.exists())

    def test_episode_precheck_stops_before_an_unaffordable_discover(self):
        # discover alone (~₹12) fits under the limit, discover + review (~₹15.5) does not: stop before paying
        with mock.patch.object(ws, "load_episode", _fake_load(self.ep)), \
                mock.patch("shotvi.llm.client.get_provider") as gp, \
                mock.patch("shotvi.llm.spend.total_inr", return_value=1295.0):
            llm = self.llm()
            llm.count_tokens = mock.Mock(return_value=12000)
            with self.assertRaises(ws.BudgetStop) as e:
                ws.run_episode("vid", self.cfg, llm, ws.Guard(1309.0), Path(self.tmp.name) / "o",
                               log=lambda *_: None)
            gp.assert_not_called()
        self.assertIn("episode", str(e.exception))


class Projection(unittest.TestCase):
    def calib(self, review_models):
        return {"run": "base", "n_reviewed": 8, "prompt_files": {}, "prompt_versions": {},
                "by_role": {"ws_discover": {"input_tokens": 1000, "output_tokens": 20000},
                            "ws_review": {"input_tokens": 5000, "output_tokens": 14000}},
                "models": {"ws_review": review_models}}

    def test_calibrated_expected_cost(self):
        ep = make_ep(SCENE, CUTS)
        cfg = ws.deep_merge(ws.DEFAULTS, {"prompts": {"ws_discover": "ws_discover_b_setup"}})
        s, u = ws.discover_prompt(ep, None, cfg)
        p = ws.project_episode(ep, None, cfg, s, u, calib=self.calib(cfg["models"]["ws_review"]), log=lambda *_: None)
        d = p["ws_discover"]
        self.assertGreater(d["in"], 1000)                     # the B prompt is longer than the calibration prompt
        self.assertEqual(d["out_expected"], 20000)
        self.assertAlmostEqual(d["inr_expected"], round(ws.inr(ws.project_usd(d["in"], 20000)), 2))
        self.assertEqual(p["ws_review"]["out_expected"], 14000)
        self.assertLess(p["episode"]["inr_expected"], p["episode"]["inr"])

    def test_review_calibration_ignored_when_settings_differ(self):
        ep = make_ep(SCENE, CUTS)
        s, u = ws.discover_prompt(ep, None, ws.DEFAULTS)
        other = {**ws.DEFAULTS["models"]["ws_review"], "params": {"thinking_level": "high"}}
        p = ws.project_episode(ep, None, ws.DEFAULTS, s, u, calib=self.calib(other), log=lambda *_: None)
        self.assertEqual(p["ws_review"]["expected_from"], "defaults")


if __name__ == "__main__":
    unittest.main()
