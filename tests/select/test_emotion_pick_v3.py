"""emotion_pick/0.3 (founder, 2026-10-22), offline with the fake provider of test_emotion_pick:
1. feelings are config (``find.feelings``): the find schema's emotion enum and the validation follow it; 0.3 adds
   relatable_banter and awkward_cringe (both humour); the v1 / v2 configs pin the old five; ws3's schema is untouched;
   the find prompt v3 names the new feelings, the pinned v2 copy does not;
2. build max_output_tokens 16384 (the v1 / v2 configs pin 8192); a build call ending on MAX_TOKENS is retried ONCE at
   2x (never more), recorded, guard-checked with its worst case plus the build calls still planned; the give-up is
   remembered in the response cache so a rerun replays the failure and the retry for free; retry off in v1 / v2;
3. the dry-run projection uses the new limit for the worst case and shows the retry's extra cost."""
import json
import re
import unittest
from pathlib import Path
from unittest import mock

from shotvi.llm.base import RawResponse, RetryableError
from shotvi.select import emotion_pick as E
from shotvi.select import webseries as ws
from shotvi.select import ws3
from shotvi.select.prompts import load_prompt
from tests.select.test_emotion_pick import V1, FakeProvider, L, Run, moment, roles
from tests.select.test_emotion_pick_v2 import alternate

CONFIGS = ws.ROOT / "shotvi" / "select" / "configs"
V2 = json.loads((CONFIGS / "emotion_pick_v2.json").read_text(encoding="utf-8"))
NO_CLUSTER = {"cluster": {"enabled": False}}


def cfg_of(*overs):
    c = E.DEFAULTS
    for o in overs:
        c = ws.deep_merge(c, o)
    return c


def run3(prov, over=None, base=None, **kw):
    """A run of the current DEFAULTS (or ``base`` over DEFAULTS) with ``over`` on top."""
    r = Run(prov, **kw)
    r.ep = alternate(r.ep)
    r.cfg = cfg_of(base or {}, NO_CLUSTER, over or {})
    for k in ("find", "build"):
        r.cfg["models"][k]["provider"] = "fake"
    r.cfg["run_id"] = "t3"
    return r


def enum_of(cfg):
    return E.find_schema(cfg)["properties"]["payoffs"]["items"]["properties"]["emotion"]["enum"]


class MaxTokensProvider(FakeProvider):
    """Build calls for the lines in ``fail`` end on finishReason=MAX_TOKENS while max_output_tokens < ok_at (billed,
    as Gemini does); ``always``: every attempt for those lines ends on MAX_TOKENS."""

    def __init__(self, moments=None, fail=(), ok_at=32768, always=False):
        super().__init__(moments)
        self.fail, self.ok_at, self.always = set(fail), ok_at, always
        self.limits = []

    def generate(self, spec, system, prompt, schema, media=None, cached_content=None):
        if "no_clip" in schema["properties"]:
            line = re.search(r"The moment: line (L\d+)", prompt).group(1)
            n = int(spec.params.get("max_output_tokens"))
            self.limits.append((line, n))
            if line in self.fail and (self.always or n < self.ok_at):
                self.calls.append({"role": "build", "system": system, "prompt": prompt, "cached": cached_content})
                e = RetryableError("gemini finishReason=MAX_TOKENS")
                e.response = RawResponse("", 5000, n, 0, "m")
                raise e
        return super().generate(spec, system, prompt, schema, media, cached_content)


# ============================================================================ 1 feelings
class Feelings(unittest.TestCase):
    def test_default_enum_is_the_config_list_with_the_two_new_feelings(self):
        self.assertEqual(E.DEFAULTS["find"]["feelings"], ["laugh", "aww", "shock", "tension", "tears",
                                                          "relatable_banter", "awkward_cringe"])
        self.assertEqual(enum_of(E.DEFAULTS), E.DEFAULTS["find"]["feelings"])
        self.assertEqual(enum_of(cfg_of({"find": {"feelings": ["laugh", "aww"]}})), ["laugh", "aww"])
        # ws3's own schema (shared with ws3 / merged replays) is not touched
        self.assertEqual(ws3.PAYOFFS_SCHEMA["properties"]["payoffs"]["items"]["properties"]["emotion"]["enum"],
                         ["laugh", "aww", "shock", "tension", "tears", "other"])
        self.assertIs(E.find_schema(cfg_of({"find_prompt": "ws3_payoffs"})), ws3.PAYOFFS_SCHEMA)

    def test_v1_and_v2_configs_pin_the_old_five(self):
        for name in ("emotion_pick_v1.json", "emotion_pick_v2.json", "emotion_pick_v2_pre_fixab.json",
                     "emotion_pick_code_only.json"):
            cfg = cfg_of(json.loads((CONFIGS / name).read_text(encoding="utf-8")))
            self.assertEqual(enum_of(cfg), ["laugh", "aww", "shock", "tension", "tears"], name)
        # the schema object the 0.2 find call was cached with
        old = cfg_of(V2)
        s = E.find_schema(old)
        self.assertEqual(s["properties"]["payoffs"]["items"]["properties"]["type"]["enum"],
                         ["punchline", "reveal", "quotable_line", "emotional_peak", "cliffhanger", "other"])

    def test_new_feelings_map_to_humour(self):
        self.assertEqual((E.EMOTION_CATEGORY["relatable_banter"], E.EMOTION_CATEGORY["awkward_cringe"]),
                         ("humour", "humour"))
        for f in E.FEELINGS_V2:
            self.assertEqual(E.EMOTION_CATEGORY[f], ws3.EMOTION_CATEGORY[f])
        self.assertNotIn("relatable_banter", ws3.EMOTION_CATEGORY)       # ws3 untouched

    def test_validation_follows_the_config(self):
        ms = [{"line": L(10), "emotion": "relatable_banter"}, {"line": L(20), "emotion": "laugh"}]
        kept, rej = E.validate_feelings(ms, E.DEFAULTS)
        self.assertEqual((len(kept), rej), (2, []))
        kept, rej = E.validate_feelings(ms, cfg_of(V2))
        self.assertEqual([p["line"] for p in kept], [L(20)])
        self.assertEqual(rej[0]["reason"], "feeling 'relatable_banter' is not in find.feelings")
        # ws3_payoffs keeps ws3's own emotions ("other" included)
        kept, rej = E.validate_feelings([{"line": L(1), "emotion": "other"}], cfg_of({"find_prompt": "ws3_payoffs"}))
        self.assertEqual((len(kept), rej), (1, []))

    def test_full_run_with_the_new_feelings(self):
        ms = [moment(10, 5, emotion="relatable_banter"), moment(47, 4, emotion="awkward_cringe")]
        prov = FakeProvider(ms)
        doc = run3(prov)()
        s = doc["episode_summary"]
        self.assertEqual(s["moments_by_feeling"], {"relatable_banter": 1, "awkward_cringe": 1})
        self.assertEqual(s["n_moments_rejected"], 0)
        feel = {p["moment"]["line"]: p["feeling"] for p in doc["proposals"]}
        self.assertEqual(feel.get(L(10)), "relatable_banter")
        self.assertEqual({r["feeling"] for r in doc["moment_table"]}, {"relatable_banter", "awkward_cringe"})
        # the build tasks name the feelings as found
        builds = [c["prompt"] for c in prov.calls if c["role"] == "build"]
        self.assertTrue(any("(feeling: relatable_banter;" in t for t in builds))
        self.assertTrue(any("(feeling: awkward_cringe;" in t for t in builds))

    def test_build_task_names_the_new_feeling(self):
        ep = alternate(Run(FakeProvider()).ep)
        p = {**moment(10, 5, emotion="awkward_cringe"), "id": "p01", "text": "x", "order": 0, "flags": []}
        t = E.task_build(E.DEFAULTS, ep, p, [])
        self.assertIn("The moment: line L0010 (feeling: awkward_cringe; intensity 5 of 5;", t)

    def test_a_feeling_outside_the_list_is_rejected_in_a_run(self):
        # the schema's enum (checked by the client with jsonschema) fails such an answer as a whole
        ms = [moment(10, 5), moment(47, 4, emotion="awkward_cringe")]
        with self.assertRaises(E.EpisodeStop) as cm:
            run3(FakeProvider(ms), over={"find": {"feelings": E.FEELINGS_V2}})()
        self.assertIn("'awkward_cringe' is not one of ['laugh', 'aww', 'shock', 'tension', 'tears']", str(cm.exception))
        # without that check (no jsonschema; or a --find-estimate answer, never schema-checked) the validation rejects
        # just that moment
        with mock.patch("shotvi.llm.client.jsonschema", None):
            doc = run3(FakeProvider(ms), over={"find": {"feelings": E.FEELINGS_V2}})()
        s = doc["episode_summary"]
        self.assertEqual((s["n_moments_valid"], s["n_moments_rejected"]), (1, 1))
        self.assertEqual(s["moments_rejected"], [{"line": L(47),
                                                  "reason": "feeling 'awkward_cringe' is not in find.feelings"}])

    def test_find_prompt_v3_and_the_pinned_v2_copy(self):
        f3, f2 = load_prompt("ws_emotion_find"), load_prompt("ws_emotion_find_v2")
        self.assertEqual((f3.version, f2.name, f2.version), ("3", "ws_emotion_find_v2", "2"))
        self.assertIn("`tears`, `relatable_banter` or `awkward_cringe`", f3.system)
        self.assertIn("smiles at relatable banter or cringes at an awkward moment", f3.system)
        self.assertNotIn("relatable", f2.system)
        self.assertEqual(f3.user, f2.user)
        b2, b = load_prompt("ws_emotion_build_v2"), load_prompt("ws_emotion_build")
        self.assertEqual((b2.system, b2.user, b2.version), (b.system, b.user, b.version))   # same text, pinned


# ============================================================================ 2 build limit and the MAX_TOKENS retry
class Limits(unittest.TestCase):
    def test_defaults_and_pins(self):
        self.assertEqual(E.PIPELINE_VERSION, "emotion_pick/0.3")
        self.assertEqual(E.DEFAULTS["models"]["build"]["params"]["max_output_tokens"], 16384)
        self.assertTrue(E.DEFAULTS["build"]["max_tokens_retry"])
        for name in ("emotion_pick_v1.json", "emotion_pick_v2.json", "emotion_pick_v2_pre_fixab.json",
                     "emotion_pick_code_only.json"):
            cfg = cfg_of(json.loads((CONFIGS / name).read_text(encoding="utf-8")))
            self.assertEqual(cfg["models"]["build"]["params"], {"thinking_level": "medium", "max_output_tokens": 8192},
                             name)
            self.assertFalse(cfg["build"]["max_tokens_retry"], name)
        v2 = cfg_of(V2)
        self.assertEqual((v2["pipeline_version"], v2["find_prompt"], v2["prompts"][E.ROLE_BUILD]),
                         ("emotion_pick/0.2", "ws_emotion_find_v2", "ws_emotion_build_v2"))

    def test_retry_spec_doubles_only_the_limit(self):
        s = E.spec_build(E.DEFAULTS)
        s2 = E.retry_spec(s)
        self.assertEqual((s2.provider, s2.model), (s.provider, s.model))
        self.assertEqual(s2.params, {**s.params, "max_output_tokens": 32768})
        self.assertEqual(s.params["max_output_tokens"], 16384)


class MaxTokensRetry(unittest.TestCase):
    MS = [moment(10, 5), moment(47, 4)]

    def test_one_retry_at_2x_answers_and_is_recorded(self):
        prov = MaxTokensProvider(self.MS, fail={L(10)})
        r = run3(prov)
        doc = r()
        self.assertEqual(prov.limits, [(L(10), 16384), (L(10), 32768), (L(47), 16384)])
        p = {q["moment"]["line"]: q for q in doc["proposals"]}[L(10)]
        self.assertEqual(p["build_flags"][0], "build call: finishReason=MAX_TOKENS at 16384 output tokens; answered by "
                                              "the one retry at 32768")
        dbg = json.loads((r.out / "debug" / "vid.json").read_text(encoding="utf-8"))
        rec = {c["line"]: c for c in dbg["calls"]}[L(10)]
        self.assertTrue(rec["max_tokens_retry"]["ok"])
        self.assertEqual(rec["max_tokens_retry"]["max_output_tokens"], 32768)
        self.assertIn("finishReason=MAX_TOKENS", rec["max_tokens_retry"]["first"])
        self.assertNotIn("max_tokens_retry", {c["line"]: c for c in dbg["calls"]}[L(47)])
        self.assertEqual(doc["episode_summary"]["n_call_failed"], 0)

    def test_never_more_than_one_retry(self):
        prov = MaxTokensProvider(self.MS, fail={L(10)}, always=True)
        doc = run3(prov)()
        self.assertEqual([x for x in prov.limits if x[0] == L(10)], [(L(10), 16384), (L(10), 32768)])
        rej = {x["moment"]["line"]: x for x in doc["rejected"]}[L(10)]
        msg = rej["reject_reasons"][0]
        self.assertTrue(msg.startswith("C1: ws_emotion_build/gemini-3.8-flash: gave up after 1 attempts: gemini "
                                       "finishReason=MAX_TOKENS; the one retry at 32768 output tokens failed: "), msg)

    def test_no_retry_when_off(self):
        prov = MaxTokensProvider(self.MS, fail={L(10)})
        doc = run3(prov, over={"build": {"max_tokens_retry": False}})()
        self.assertEqual([x for x in prov.limits if x[0] == L(10)], [(L(10), 16384)])
        rej = {x["moment"]["line"]: x for x in doc["rejected"]}[L(10)]
        self.assertEqual(rej["reject_reasons"], ["C1: ws_emotion_build/gemini-3.8-flash: gave up after 1 attempts: "
                                                 "gemini finishReason=MAX_TOKENS"])

    def test_v2_config_does_not_retry_at_8192(self):
        prov = MaxTokensProvider(self.MS, fail={L(10)}, ok_at=10 ** 9)
        r = run3(prov, base=V2)
        r.cfg["build"]["max_moments"] = None
        doc = r()
        self.assertEqual([x for x in prov.limits if x[0] == L(10)], [(L(10), 8192)])
        self.assertEqual(doc["episode_summary"]["n_call_failed"], 1)

    def test_rerun_replays_the_failure_and_the_retry_for_free(self):
        first = run3(MaxTokensProvider(self.MS, fail={L(10)}))
        d1 = first()
        from shotvi.llm import LLMClient
        init = LLMClient.__init__

        def cache_only_init(self_, *a, **kw):
            init(self_, *a, **kw)
            self_.cache_only = True

        for cache_only in (False, True):
            prov = MaxTokensProvider(self.MS, fail={L(10)})
            r = run3(prov, cache_dir=first.cache_dir)
            if cache_only:          # --cache-only: a miss would fail; the remembered give-up leads to the retry
                with mock.patch.object(LLMClient, "__init__", cache_only_init):
                    d2 = r()
                self.assertTrue(r.llm.cache_only)
            else:
                d2 = r()
            self.assertEqual(prov.calls, [])                              # nothing called, nothing billed
            for k in ("proposals", "rejected", "moment_table", "episode_summary"):
                self.assertEqual(d2[k], d1[k], k)
        # a failure that was never retried (retry off) also replays as the same C1 for free
        first = run3(MaxTokensProvider(self.MS, fail={L(10)}), over={"build": {"max_tokens_retry": False}})
        a = first()
        prov = MaxTokensProvider(self.MS, fail={L(10)})
        b = run3(prov, over={"build": {"max_tokens_retry": False}}, cache_dir=first.cache_dir)()
        self.assertEqual(prov.calls, [])
        self.assertEqual(a["rejected"], b["rejected"])

    def test_failure_entries_are_ignored_by_roles_not_opted_in(self):
        first = run3(MaxTokensProvider(self.MS, fail={L(10)}), over={"build": {"max_tokens_retry": False}})
        first()
        hits = [json.loads(f.read_text(encoding="utf-8")) for f in Path(first.cache_dir).rglob("*.json")]
        failed = [h for h in hits if "failed" in h]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["meta"]["role"], E.ROLE_BUILD)
        # client level: an entry written by remember_failure replays as the same LLMError for an opted-in role; a
        # role not opted in (every other pipeline) treats it as a miss and calls
        from shotvi.llm import Ledger, LLMClient, LLMError, ModelSpec

        class Echo:
            name = "fake"

            def __init__(self):
                self.n = 0

            def generate(self, spec, system, prompt, schema, media=None, cached_content=None):
                self.n += 1
                return RawResponse('{"a": 1}', 10, 5, 0, "m")

        spec = ModelSpec("fake", "gemini-3.8-flash", {"max_output_tokens": 8})
        schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
        prov = Echo()
        with mock.patch("shotvi.llm.client.get_provider", return_value=prov):
            llm = LLMClient(Ledger(max_usd=5.0), cache_dir=first.cache_dir, max_retries=0, schema_retries=0)
            self.assertEqual(llm.remember_max_tokens, set())
            llm.remember_failure("r", spec, "s", "p", schema, "r/m: gave up after 1 attempts: gemini "
                                                                "finishReason=MAX_TOKENS")
            llm.remember_max_tokens = {"r"}
            with self.assertRaises(LLMError) as cm:
                llm.generate_json("r", spec, "s", "p", schema)
            self.assertEqual(str(cm.exception), "r/m: gave up after 1 attempts: gemini finishReason=MAX_TOKENS")
            self.assertEqual(prov.n, 0)
            llm.remember_max_tokens = set()
            self.assertEqual(llm.generate_json("r", spec, "s", "p", schema), {"a": 1})
            self.assertEqual(prov.n, 1)
            # the answer replaced the entry; remember_failure never overwrites an answer
            llm.remember_failure("r", spec, "s", "p", schema, "x")
            llm.remember_max_tokens = {"r"}
            self.assertEqual(llm.generate_json("r", spec, "s", "p", schema), {"a": 1})
            self.assertEqual(prov.n, 1)

    def test_retry_is_guard_checked_with_the_planned_calls_reserved(self):
        seen = []

        def check(self_, what, projected):
            seen.append((what, projected))
            if "MAX_TOKENS retry" in what:
                raise ws.BudgetStop(f"{what}: over")

        prov = MaxTokensProvider(self.MS, fail={L(10)})
        with mock.patch.object(ws.Guard, "check", check):
            r = run3(prov)
            doc = r()
        # the retry was refused before any call: one build call for L0010, the planned L0047 still built
        self.assertEqual(prov.limits, [(L(10), 16384), (L(47), 16384)])
        rej = {x["moment"]["line"]: x for x in doc["rejected"]}[L(10)]
        self.assertIn("the one retry at 32768 output tokens was refused by the spend guard", rej["reject_reasons"][0])
        self.assertIn(L(47), [p["moment"]["line"] for p in doc["proposals"]])
        dbg = json.loads((r.out / "debug" / "vid.json").read_text(encoding="utf-8"))
        rec = {c["line"]: c for c in dbg["calls"]}[L(10)]["max_tokens_retry"]
        self.assertIn("refused", rec)
        # worst case of the retry (2x limit) plus L0047's planned build (still to come) is what the guard saw
        retry = [p for w, p in seen if "MAX_TOKENS retry" in w]
        self.assertEqual(len(retry), 1)
        self.assertGreater(rec["reserved_inr"], 0)
        self.assertAlmostEqual(retry[0], rec["worst_inr"] + rec["reserved_inr"], places=1)
        base = [p for w, p in seen if w.endswith(E.ROLE_BUILD)]
        self.assertTrue(base and rec["worst_inr"] > max(base))          # a 2x call costs more than a planned one

    def test_before_call_prices_a_build_call_at_its_own_limit(self):
        seen = []

        def check(self_, what, projected):
            seen.append((what, projected))

        with mock.patch.object(ws.Guard, "check", check):
            run3(MaxTokensProvider(self.MS, fail={L(10)}))()
        builds = [p for w, p in seen if w == f"vid {E.ROLE_BUILD}"]
        self.assertEqual(len(builds), 3)                                 # two base calls and the retry
        self.assertGreater(builds[1], builds[0])                          # the retry: 32768 output tokens
        self.assertAlmostEqual(builds[0], builds[2])


# ============================================================================ 3 dry run
class DryRun(unittest.TestCase):
    def test_projection_uses_the_new_limit_and_shows_the_retry(self):
        prov = FakeProvider()
        res = run3(prov, dry_run=True)()
        self.assertEqual(prov.calls, [])
        b = res["projection"]["build"]
        self.assertEqual((b["out_max"], b["retry"]["out_max"]), (16384, 32768))
        self.assertGreater(b["retry"]["inr_worst_each"], b["inr_worst_each"])
        res2 = run3(FakeProvider(), base=V2, dry_run=True)()
        b2 = res2["projection"]["build"]
        self.assertEqual(b2["out_max"], 8192)
        self.assertNotIn("retry", b2)
        self.assertGreater(b["inr_worst_each"], b2["inr_worst_each"])
        self.assertGreater(res["projection"]["rows"]["no_cap"]["build_inr_worst"],
                           res2["projection"]["rows"]["no_cap"]["build_inr_worst"])
        self.assertIn("one MAX_TOKENS retry at 32768 adds Rs", E.projection_text("vid", res))


if __name__ == "__main__":
    unittest.main()
