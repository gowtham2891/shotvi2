"""Web-series clip selection v3 (shotvi/select/ws3.py), offline with a fake provider: the three-call flow over an
explicit context cache (persona + transcript cached once, calls send only their task), id validation (ids outside the
transcript rejected / mapped / clamped), de-duplication (higher intensity wins), ranking (intensity, then
watch_whole_clip, then would_stop_scrolling), the implicit-cache fallback when the provider refuses the cache, the
free rerun from the response cache, the dry run (no provider call) and the spend guard."""
import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shotvi.llm import Ledger, LLMClient
from shotvi.llm.base import CacheUnavailable, RawResponse, SafetyBlocked
from shotvi.select import webseries as ws
from shotvi.select import ws3
from tests.select.test_webseries_cut import make_ep
from tests.select.test_webseries_scene import block, shot_cuts

# one long scene: 60 spoken lines, one every 3 s from 10 s (L0001..L0060), a shot change in every other gap
SPEC = block(10, 60, "a")
CUTS = shot_cuts(SPEC)


def episode():
    return make_ep(SPEC, CUTS, duration=400.0)


def payoff(line, intensity=4, type_="punchline", emotion="laugh", lands=True):
    return {"line": line, "type": type_, "emotion": emotion, "intensity": intensity,
            "reason": f"{line} lands", "lands_on_screen": lands}


def hook(hook_line, needs_from=None, stop="yes"):
    return {"hook_line": hook_line, "needs_from_line": needs_from or hook_line, "hook_emotion": "curiosity",
            "would_stop_scrolling": stop, "reason": "hook reason"}


def clip(start, end, watch="yes", drops=(), cold_open=""):
    return {"start_line": start, "end_line": end, "drops": list(drops), "cold_open_line": cold_open,
            "title": "పరీక్ష title", "emotion_arc": "curiosity -> laugh", "watch_whole_clip": watch,
            "reason": "clip reason"}


class FakeProvider:
    """Answers call 1 with ``payoffs``, calls 2 / 3 per payoff line from ``hooks`` / ``clips`` (keyed by the payoff
    line id read from the task text). Records every call: system, prompt, cached_content; creates / deletes caches."""

    name = "fake"

    def __init__(self, payoffs, hooks, clips, cache_ok=True, block_payoffs=False):
        self.payoffs, self.hooks, self.clips = payoffs, hooks, clips
        self.cache_ok, self.block_payoffs = cache_ok, block_payoffs
        self.calls = []
        self.caches, self.deleted = [], []

    def generate(self, spec, system, prompt, schema, media=None, cached_content=None):
        self.calls.append({"role": schema_role(schema), "system": system, "prompt": prompt, "cached": cached_content,
                           "model": spec.model})
        if "payoffs" in schema["properties"]:
            if self.block_payoffs:
                raise SafetyBlocked("blocked", "PROHIBITED_CONTENT", response=RawResponse("", 100, 0))
            obj = {"episode_notes": "a sitcom", "payoffs": self.payoffs}
        else:
            m = re.search(r"Payoff: line (L\d+)", prompt)
            line = m.group(1)
            obj = self.hooks[line] if "hook_line" in schema["properties"] else self.clips[line]
        cached = 5000 if cached_content else 0
        return RawResponse(json.dumps(obj, ensure_ascii=False), 5000 + 800, 300, 400, "m", cached_tokens=cached)

    def create_cache(self, spec, system, prefix, ttl_s=3600, display_name=""):
        if not self.cache_ok:
            raise CacheUnavailable("fake: HTTP 400 explicit caching not supported for this model")
        name = f"cachedContents/fake{len(self.caches) + 1}"
        self.caches.append({"name": name, "system": system, "prefix": prefix, "ttl_s": ttl_s})
        return {"name": name, "tokens": 5000, "ttl_s": ttl_s, "expire_time": "x", "storage_usd": 0.0025,
                "model": spec.model}

    def delete_cache(self, name):
        self.deleted.append(name)
        return True


def schema_role(schema):
    p = schema["properties"]
    return "ws3_payoffs" if "payoffs" in p else ("ws3_hook" if "hook_line" in p else "ws3_clip")


def fake_load(ep):
    tmp = Path(tempfile.mkdtemp()) / "t.json"
    tmp.write_text(json.dumps({"audio_duration_secs": ep.duration}), encoding="utf-8")
    return lambda vid, with_loudness=True, speaker_names=False, require_names=True: (ep, None, {}, tmp)


class Run:
    """One offline run of ws3.run_episode with a fake provider; ``cache_dir`` shared across runs for rerun tests."""

    def __init__(self, prov, ep=None, cfg=None, limit=None, ledger_inr=0.0, dry_run=False, cache_dir=None,
                 cache_only=False):
        self.prov, self.ep = prov, ep or episode()
        self.cfg = ws.deep_merge(ws3.DEFAULTS, cfg or {})
        for role in ws3.ROLES:
            self.cfg["models"][role]["provider"] = "fake"
        self.cfg["run_id"] = "t"
        self.limit, self.ledger_inr, self.dry_run, self.cache_only = limit, ledger_inr, dry_run, cache_only
        self.cache_dir = cache_dir or tempfile.mkdtemp()
        self.out = Path(tempfile.mkdtemp())

    def __call__(self):
        llm = LLMClient(Ledger(max_usd=5.0), cache_dir=self.cache_dir, max_retries=0, schema_retries=0)
        llm.cache_only = self.cache_only or self.dry_run
        self.llm = llm
        ledger = self.ledger_inr if callable(self.ledger_inr) else (lambda: self.ledger_inr)
        with mock.patch.object(ws, "load_episode", fake_load(self.ep)), \
                mock.patch("shotvi.llm.client.get_provider", return_value=self.prov), \
                mock.patch("shotvi.llm.spend.total_inr", side_effect=ledger):
            return ws3.run_episode("vid", self.cfg, llm, ws.Guard(self.limit), self.out, log=lambda *a: None,
                                   dry_run=self.dry_run)


def simple_world(n=3, intensities=(5, 4, 3), stops=("yes", "yes", "no"), watches=("yes", "no", "yes")):
    """n far-apart clips: payoff k on line L00{10k+8}, hook 6 lines before it."""
    payoffs, hooks, clips = [], {}, {}
    for k in range(n):
        p = 10 * k + 8
        pl, hl = f"L{p:04d}", f"L{p - 6:04d}"
        payoffs.append(payoff(pl, intensities[k]))
        hooks[pl] = hook(hl, stop=stops[k])
        clips[pl] = clip(hl, pl, watch=watches[k])
    return payoffs, hooks, clips


# ============================================================================ 1. the three-call flow
class ThreeCallFlow(unittest.TestCase):
    def setUp(self):
        self.prov = FakeProvider(*simple_world())
        self.run = Run(self.prov)
        self.doc = self.run()

    def test_one_payoff_call_then_hook_and_clip_per_payoff(self):
        roles = [c["role"] for c in self.prov.calls]
        self.assertEqual(roles, ["ws3_payoffs"] + ["ws3_hook", "ws3_clip"] * 3)

    def test_explicit_cache_holds_persona_and_transcript_and_calls_send_only_the_task(self):
        self.assertEqual(len(self.prov.caches), 1)
        c = self.prov.caches[0]
        self.assertIn("senior Telugu short-form editor", c["system"])
        self.assertIn("<transcript>", c["prefix"])
        self.assertIn("L0001 ", c["prefix"])
        self.assertEqual(c["ttl_s"], 3600)
        for call in self.prov.calls:
            self.assertEqual(call["system"], "")
            self.assertEqual(call["cached"], c["name"])
            self.assertNotIn("<transcript>", call["prompt"])
            self.assertNotIn("L0001 [", call["prompt"])
        self.assertEqual(self.prov.deleted, [c["name"]])      # deleted at the end of the episode

    def test_cached_tokens_are_recorded_and_priced_at_the_cached_rate(self):
        summ = self.run.llm.ledger.summary()
        self.assertEqual(summ["cached_input_tokens"], 5000 * 7)
        cost = self.doc["cost"]
        self.assertEqual(cost["cached_input_tokens"], 5000 * 7)
        self.assertEqual(cost["cache"]["mode"], "explicit")
        self.assertEqual(cost["cache"]["tokens"], 5000)
        # one call: (800 uncached x 0.75 + 5000 cached x 0.075 + 700 out x 3.75) / 1e6, plus the storage entry
        per_call = (800 * 0.75 + 5000 * 0.075 + 700 * 3.75) / 1e6
        self.assertAlmostEqual(cost["usd"], 7 * per_call + 0.0025, places=6)
        self.assertEqual(cost["n_llm_calls"], 8)              # 7 generate calls + the cache storage entry

    def test_run_document_has_schema_core_and_ws3_fields(self):
        from eval.schema_check import check_clip_core
        props = self.doc["proposals"]
        self.assertEqual(len(props), 3)
        self.assertEqual([p["rank"] for p in props], [1, 2, 3])
        for p in props:
            errs = []
            check_clip_core(p["id"], p, self.run.ep.words, errs, 30.0)
            self.assertEqual(errs, [])
            for f in ("payoff", "hook", "intensity", "emotion", "would_stop_scrolling", "watch_whole_clip",
                      "emotion_arc", "clip_reason", "title", "hook_line", "payoff_line", "payoff_type",
                      "why_it_works", "scores", "time_segments", "end_hold", "chunk_drops"):
                self.assertIn(f, p, f)
            self.assertEqual(p["hook_line"]["start_word"], p["pieces"][0]["start_word"])
            self.assertIn("పరీక్ష", p["title"])
            self.assertEqual(p["scores"]["payoff"], p["intensity"])
        s = self.doc["episode_summary"]
        self.assertEqual((s["n_payoffs_listed"], s["n_payoffs_used"], s["n_clips_built"], s["n_shown"]), (3, 3, 3, 3))
        self.assertEqual(s["cache"]["mode"], "explicit")
        self.assertEqual(s["verdict"]["strong_clips"], 1)        # only intensity >= 4 with both "yes"
        self.assertEqual(self.doc["pipeline"]["version"], "ws3/0.1")
        self.assertEqual(set(self.doc["pipeline"]["prompt_versions"]),
                         {"ws3_context", "ws3_payoffs", "ws3_hook", "ws3_clip"})
        self.assertTrue((self.run.out / "vid.json").exists())
        self.assertTrue((self.run.out / "vid.md").exists())
        md = (self.run.out / "vid.md").read_text(encoding="utf-8")
        self.assertIn("Context cache: explicit", md)

    def test_clip_is_built_by_the_v1_cut_builder(self):
        p = self.doc["proposals"][0]
        first = self.run.ep.by_id[p["start_line"]]
        w0 = self.run.ep.words[first["words"][0]]["start"]
        self.assertLessEqual(w0 - p["time_segments"][0]["start"], 0.3 + 1e-6)     # first-word start, <= 0.3 s pad
        self.assertGreaterEqual(p["end_hold"]["hold_s"], 0.0)

    def test_rerun_is_free_from_the_response_cache(self):
        prov2 = FakeProvider(*simple_world())
        run2 = Run(prov2, cache_dir=self.run.cache_dir)
        doc2 = run2()
        self.assertEqual(prov2.calls, [])                     # every answer came from the response cache
        self.assertEqual(prov2.caches, [])                    # no provider cache was created for a free rerun
        self.assertEqual(doc2["cost"]["usd_billed"], 0.0)
        self.assertEqual([p["id"] for p in doc2["proposals"]], [p["id"] for p in self.doc["proposals"]])


# ============================================================================ 2. id validation
class IdValidation(unittest.TestCase):
    def test_payoff_ids_outside_the_transcript_are_rejected(self):
        ep = episode()
        disc = {"payoffs": [payoff("L0999"), payoff("L0008"), payoff("nonsense", 5), payoff("L0008", 3)]}
        kept, rej = ws3.validate_payoffs(ep, disc, ws3.DEFAULTS)
        self.assertEqual([p["line"] for p in kept], ["L0008"])
        reasons = " | ".join(r["reason"] for r in rej)
        self.assertIn("unknown line id 'L0999'", reasons)
        self.assertIn("unknown line id 'nonsense'", reasons)
        self.assertIn("duplicate of L0008", reasons)

    def test_event_line_payoff_maps_to_the_spoken_line_before_it(self):
        ep = make_ep(SPEC, CUTS, events=[(33.2, 33.8, "[నవ్వు]")], duration=400.0)   # after L0008 (31-33 s)
        ev = next(r for r in ep.lines if r["kind"] == "event")
        kept, rej = ws3.validate_payoffs(ep, {"payoffs": [payoff(ev["id"])]}, ws3.DEFAULTS)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["line"], "L0008")
        self.assertTrue(any("audio event" in f for f in kept[0]["flags"]))

    def test_low_intensity_and_cap(self):
        ep = episode()
        cfg = ws.deep_merge(ws3.DEFAULTS, {"payoffs": {"max_payoffs": 2}})
        disc = {"payoffs": [payoff("L0005", 3), payoff("L0010", 5), payoff("L0015", 2), payoff("L0020", 4)]}
        kept, rej = ws3.validate_payoffs(ep, disc, cfg)
        self.assertEqual([p["line"] for p in kept], ["L0010", "L0020"])          # highest intensity, transcript order
        self.assertEqual([p["id"] for p in kept], ["p01", "p02"])
        self.assertTrue(any("intensity 2 < 3" in r["reason"] for r in rej))
        self.assertTrue(any("beyond max_payoffs 2" in r["reason"] for r in rej))

    def test_hook_ids(self):
        ep = episode()
        p = {"line": "L0010", "text": "t", "intensity": 4, "type": "punchline", "emotion": "laugh", "reason": ""}
        with self.assertRaises(ValueError):
            ws3.validate_hook(ep, p, hook("L0012"))                 # after the payoff
        with self.assertRaises(ValueError):
            ws3.validate_hook(ep, p, hook("L0010"))                 # the payoff itself
        with self.assertRaises(ValueError):
            ws3.validate_hook(ep, p, hook("L0777"))                 # outside the transcript
        with self.assertRaises(ValueError):
            ws3.validate_hook(ep, p, hook("L0006", needs_from="L9999"))
        h = ws3.validate_hook(ep, p, hook("L0004", needs_from="L0007"))
        self.assertEqual(h["needs_from_line"], "L0004")               # a later needs_from is clamped to the hook
        self.assertTrue(h["flags"])

    def test_clip_ids(self):
        ep = episode()
        p = {"line": "L0010", "text": "t", "intensity": 4, "type": "punchline", "emotion": "laugh", "reason": ""}
        h = ws3.validate_hook(ep, p, hook("L0004"))
        with self.assertRaises(ValueError):
            ws3.validate_clip(ep, p, h, clip("L0999", "L0010"), ws3.DEFAULTS)
        with self.assertRaises(ValueError):
            ws3.validate_clip(ep, p, h, clip("L0004", "bogus"), ws3.DEFAULTS)
        c = ws3.validate_clip(ep, p, h, clip("L0006", "L0020", drops=[{"first_line": "L0500", "last_line": "L0501",
                                                                      "why": "x"}], cold_open="L0400"), ws3.DEFAULTS)
        m = c["moment"]
        self.assertEqual(m["start_line"], "L0004")                    # a start after the hook moves to the hook
        self.assertEqual(m["end_line"], "L0010")                      # an end 10 lines past the payoff moves back
        self.assertEqual(m["drops"], [])                              # unknown drop ids ignored
        self.assertEqual(m["cold_open_lines"], [])                    # unknown cold-open id ignored
        self.assertEqual(len(c["flags"]), 4)
        c2 = ws3.validate_clip(ep, p, h, clip("L0003", "L0011"), ws3.DEFAULTS)
        self.assertEqual((c2["moment"]["start_line"], c2["moment"]["end_line"]), ("L0003", "L0011"))  # reaction ok

    def test_bad_ids_from_the_model_skip_the_payoff_but_not_the_episode(self):
        payoffs, hooks, clips = simple_world()
        hooks["L0018"] = hook("L0019")                                 # hook after its payoff
        prov = FakeProvider(payoffs, hooks, clips)
        doc = Run(prov)()
        self.assertEqual(len(doc["proposals"]), 2)
        self.assertTrue(any(r["payoff"]["line"] == "L0018" and r["reject_reasons"][0].startswith("H1")
                            for r in doc["rejected"]))
        roles = [c["role"] for c in prov.calls]
        self.assertEqual(roles.count("ws3_clip"), 2)                   # no clip call for the failed hook


# ============================================================================ 3. dedupe and ranking
class DedupeAndRank(unittest.TestCase):
    def test_overlapping_clips_keep_the_higher_intensity(self):
        # two payoffs two lines apart with the same hook -> the clips share > 50 % of their source
        payoffs = [payoff("L0010", 3), payoff("L0012", 5), payoff("L0030", 4)]
        hooks = {"L0010": hook("L0004"), "L0012": hook("L0004"), "L0030": hook("L0025")}
        clips = {"L0010": clip("L0004", "L0010"), "L0012": clip("L0004", "L0012"), "L0030": clip("L0025", "L0030")}
        doc = Run(FakeProvider(payoffs, hooks, clips))()
        shown = [(p["payoff"]["line"], p["intensity"]) for p in doc["proposals"]]
        self.assertEqual(shown, [("L0012", 5), ("L0030", 4)])
        d = next(r for r in doc["rejected"] if r["payoff"]["line"] == "L0010")
        self.assertTrue(d["reject_reasons"][0].startswith("D1: overlaps"))

    def test_rank_intensity_then_watch_whole_then_stop_scrolling(self):
        payoffs, hooks, clips = simple_world(n=5, intensities=(3, 5, 4, 4, 4), stops=("yes", "no", "no", "yes", "yes"),
                                             watches=("yes", "yes", "yes", "no", "yes"))
        doc = Run(FakeProvider(payoffs, hooks, clips))()
        order = [(p["intensity"], p["watch_whole_clip"], p["would_stop_scrolling"]) for p in doc["proposals"]]
        self.assertEqual(order, [(5, "yes", "no"), (4, "yes", "yes"), (4, "yes", "no"), (4, "no", "yes"),
                                 (3, "yes", "yes")])
        tiers = [p["tier"] for p in doc["proposals"]]
        self.assertEqual(tiers, ["worth_a_look", "recommended", "worth_a_look", "worth_a_look", "worth_a_look"])
        self.assertEqual(doc["episode_summary"]["verdict"]["verdict"], "thin")


# ============================================================================ 4. cache fallback
class CacheFallback(unittest.TestCase):
    def test_refused_explicit_cache_falls_back_to_the_identical_prefix_in_every_call(self):
        prov = FakeProvider(*simple_world(), cache_ok=False)
        run = Run(prov)
        doc = run()
        self.assertEqual(prov.caches, [])
        self.assertEqual(len(prov.calls), 7)
        systems = {c["system"] for c in prov.calls}
        self.assertEqual(len(systems), 1)
        self.assertIn("senior Telugu short-form editor", systems.pop())
        prefixes = {c["prompt"].split("\n\n## Task")[0] for c in prov.calls}
        self.assertEqual(len(prefixes), 1, "the cached prefix must be byte-identical in every call")
        self.assertIn("<transcript>", prefixes.pop())
        for c in prov.calls:
            self.assertIsNone(c["cached"])
        self.assertEqual(doc["cost"]["cache"]["mode"], "implicit_fallback")
        self.assertIn("not supported", doc["cost"]["cache"]["fallback_reason"])
        self.assertEqual(doc["episode_summary"]["cache"]["mode"], "implicit_fallback")
        self.assertIn("implicit_fallback", (run.out / "vid.md").read_text(encoding="utf-8"))

    def test_implicit_mode_by_config_never_creates_a_cache(self):
        prov = FakeProvider(*simple_world())
        doc = Run(prov, cfg={"cache": {"mode": "implicit"}})()
        self.assertEqual(prov.caches, [])
        self.assertEqual(doc["cost"]["cache"]["mode"], "implicit")

    def test_blocked_payoff_call_stops_the_episode_and_deletes_the_cache(self):
        prov = FakeProvider(*simple_world(), block_payoffs=True)
        with self.assertRaises(ws3.EpisodeStop):
            Run(prov)()
        self.assertEqual(prov.deleted, [prov.caches[0]["name"]])


# ============================================================================ 5. dry run and spend guard
class DryRunAndGuard(unittest.TestCase):
    def test_dry_run_makes_no_call_and_projects_the_table(self):
        prov = FakeProvider(*simple_world())
        run = Run(prov, dry_run=True)
        res = run()
        self.assertEqual(prov.calls, [])
        self.assertEqual(prov.caches, [])
        self.assertTrue(res["dry_run"])
        pj = res["projection"]
        self.assertEqual([r["n"] for r in pj["table"]], [8, 12, 16])
        for r in pj["table"]:
            self.assertGreater(r["inr_worst"], r["inr_expected"])
        self.assertEqual(set(pj["calls"]), set(ws3.ROLES))
        self.assertEqual(pj["calls"]["ws3_payoffs"]["thinking_level"], "high")
        self.assertEqual(pj["calls"]["ws3_hook"]["thinking_level"], "medium")
        self.assertEqual(pj["calls"]["ws3_clip"]["thinking_level"], "low")
        self.assertGreater(pj["tokens"]["cache_total"], 0)
        self.assertGreater(pj["cache"]["storage_inr"], 0)
        self.assertEqual(pj["n_estimate"]["n"], max(1, round(400 / 105)))
        self.assertTrue((run.out / "dry_run_vid.json").exists())
        self.assertFalse((run.out / "vid.json").exists())

    def test_dry_run_after_a_real_run_knows_the_payoff_count(self):
        prov = FakeProvider(*simple_world())
        run = Run(prov)
        run()
        res = Run(FakeProvider(*simple_world()), dry_run=True, cache_dir=run.cache_dir)()
        self.assertTrue(res["call1_cached"])
        self.assertEqual(res["projection"]["n_estimate"]["n"], 3)

    def test_guard_refuses_before_any_call(self):
        prov = FakeProvider(*simple_world())
        with self.assertRaises(ws.BudgetStop):
            Run(prov, limit=10.0, ledger_inr=9.9)()
        self.assertEqual(prov.calls, [])
        self.assertEqual(prov.caches, [])

    def test_guard_cuts_the_payoff_list_after_call_1(self):
        prov = FakeProvider(*simple_world())
        run = Run(prov, limit=100.0)
        proj = ws3.project(run.ep, None, run.cfg, *ws3.context_prompt(run.ep, run.cfg), log=lambda *a: None)
        c = proj["calls"]
        per = c["ws3_hook"]["inr_worst"] + c["ws3_clip"]["inr_worst"]
        head = proj["cache"]["storage_inr"] + proj["cache"]["write_inr_worst_case"] + c["ws3_payoffs"]["inr_worst"]
        # the fake ledger bills the cache and call 1 at their worst case once call 1 was made; the limit then
        # leaves room for exactly two payoffs' worst case
        run.ledger_inr = lambda: head if any(x["role"] == "ws3_payoffs" for x in prov.calls) else 0.0
        run.limit = head + 2 * per + 0.001
        doc = run()
        roles = [c["role"] for c in prov.calls]
        self.assertEqual(roles.count("ws3_hook"), 2)
        self.assertEqual([p["intensity"] for p in doc["proposals"]], [5, 4])   # highest intensity kept
        dbg = json.loads((run.out / "debug" / "vid.json").read_text(encoding="utf-8"))
        self.assertTrue(any("spend limit" in r["reason"] for r in dbg["rejected_payoffs"]))
        self.assertEqual(doc["cutoff"]["warnings"], ["spend limit cut the payoff list to 2"])


# ============================================================================ 6. provider plumbing
class GeminiCachePlumbing(unittest.TestCase):
    def test_generate_sends_cached_content_and_no_system(self):
        from shotvi.llm import gemini
        from shotvi.llm.base import ModelSpec
        p = gemini.GeminiProvider(api_key="k")
        spec = ModelSpec("gemini", "gemini-3.8-flash", {"thinking_level": "low", "max_output_tokens": 100})
        seen = {}

        def post(url, body):
            seen["url"], seen["body"] = url, body
            return {"candidates": [{"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 6000, "cachedContentTokenCount": 5000,
                                      "candidatesTokenCount": 10, "thoughtsTokenCount": 5}}
        with mock.patch.object(p, "_post", side_effect=post), mock.patch.object(gemini, "spend") as sp:
            raw = p.generate(spec, "", "task", {"type": "object"}, cached_content="cachedContents/abc")
        self.assertEqual(seen["body"]["cachedContent"], "cachedContents/abc")
        self.assertNotIn("systemInstruction", seen["body"])
        self.assertEqual(raw.cached_tokens, 5000)
        sp.record.assert_called_once()
        self.assertEqual(sp.record.call_args.kwargs["cached_tokens"], 5000)
        with self.assertRaises(Exception):
            p.generate(spec, "sys", "task", None, cached_content="cachedContents/abc")

    def test_create_cache_posts_system_prefix_ttl_and_records_storage(self):
        from shotvi.llm import gemini
        from shotvi.llm.base import ModelSpec
        p = gemini.GeminiProvider(api_key="k")
        spec = ModelSpec("gemini", "gemini-3.8-flash", {})
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"name": "cachedContents/x1", "usageMetadata": {"totalTokenCount": 20000},
                                  "expireTime": "t"}
        with mock.patch.object(p.session, "post", return_value=resp) as post, mock.patch.object(gemini, "spend") as sp:
            info = p.create_cache(spec, "SYS", "PREFIX", ttl_s=3600, display_name="d")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "models/gemini-3.8-flash")
        self.assertEqual(body["ttl"], "3600s")
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "SYS")
        self.assertEqual(body["contents"][0]["parts"][0]["text"], "PREFIX")
        self.assertEqual(info["name"], "cachedContents/x1")
        self.assertEqual(info["tokens"], 20000)
        self.assertAlmostEqual(info["storage_usd"], 20000 * 0.5 / 1e6, places=9)     # $0.50 / 1M token-hours
        sp.record_fixed.assert_called_once()
        bad = mock.Mock(status_code=400, text="not supported")
        with mock.patch.object(p.session, "post", return_value=bad), mock.patch.object(gemini, "spend"):
            with self.assertRaises(CacheUnavailable):
                p.create_cache(spec, "SYS", "PREFIX")

    def test_pricing_cached_rate_and_storage(self):
        from shotvi.llm.pricing import cache_storage_usd, cost_usd
        full = cost_usd("gemini-3.8-flash", 30000, 1000)
        cached = cost_usd("gemini-3.8-flash", 30000, 1000, cached_tokens=28000)
        self.assertAlmostEqual(full - cached, 28000 * (0.75 - 0.075) / 1e6, places=9)
        self.assertAlmostEqual(cache_storage_usd("gemini-3.8-flash", 30000, 1.0), 0.015, places=9)
        self.assertIsNone(cache_storage_usd("gemini-2.5-flash", 30000, 1.0))


if __name__ == "__main__":
    unittest.main()
