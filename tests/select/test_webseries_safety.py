"""Safety-block fallback in the web-series review stage (shotvi/select/webseries.py stage 3b), offline (fake provider):
1. nothing blocked: one combined review call, the same prompt as before the fallback existed, no fallback fields;
2. Gemini blocks any review batch containing clip X: the clips are reviewed in halves down to X alone; only X ends
   ``unchecked`` (no scores, shown last with the note), every other clip is scored as usual; every split call goes
   through the spend guard; the fallback is projected (~2x the review) and refused up front when it cannot fit;
3. a blocked discover fails with a clear message naming the block reason;
4. client / provider: a promptFeedback.blockReason is SafetyBlocked, never retried, remembered in the cache."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shotvi.llm import Ledger, LLMClient, ModelSpec
from shotvi.llm.base import RawResponse, SafetyBlocked
from shotvi.select import webseries as ws
from tests.select.test_webseries_cut import make_ep
from tests.select.test_webseries_scene import block, mom, shot_cuts

# five ~25 s moments in one long episode (lines every 3 s), far enough apart not to be duplicates
MOMENTS = [mom("L0002", "L0009", 4), mom("L0012", "L0019", 4), mom("L0024", "L0031", 4), mom("L0036", "L0043", 4),
           mom("L0050", "L0057", 4)]
REASON = "PROHIBITED_CONTENT"
REAL_MD = ws.clips_md


def episode():
    spec = block(10, 60, "a")
    return make_ep(spec, shot_cuts(spec), duration=200.0)


class FakeProvider:
    """Discover: MOMENTS (or a block); review: scores every clip shown, unless the batch contains a blocked id."""

    def __init__(self, block_ids=(), block_discover=False):
        self.block_ids = set(block_ids)
        self.block_discover = block_discover
        self.reviews = []        # clip ids of every review call, in call order
        self.n_discover = 0

    def generate(self, spec, system, prompt, schema, media=None):
        if "clips" in schema["properties"]:
            ids = re.findall(r"^### (w\d+) \|", prompt, re.M)
            self.reviews.append(ids)
            if self.block_ids & set(ids):
                raise SafetyBlocked("blocked", REASON, response=RawResponse("", 900, 0))
            obj = {"clips": [{"id": i, "hook": 4, "standalone": 4, "payoff": 4, "value_emotion": 4,
                              "overall": 4 if i != "w05" else 3.0,
                              "keep_cold_open": False, "cold_open_note": "", "hook_without_cold_open": 4,
                              "overall_without_cold_open": 4 if i != "w05" else 3.0, "bad_cuts": [], "cut_note": "",
                              "title": f"శీర్షిక {i}", "why_it_works": "w", "category": "humour"}
                             for i in ids]}
        else:
            self.n_discover += 1
            if self.block_discover:
                raise SafetyBlocked("blocked", REASON, response=RawResponse("", 9000, 0))
            obj = {"episode_notes": "n", "moments": MOMENTS}
        return RawResponse(json.dumps(obj, ensure_ascii=False), 1000, 500, 0, "m")

    def count_tokens(self, spec, text):
        return len(text) // 3


class RecGuard(ws.Guard):
    def __init__(self, limit=None):
        super().__init__(limit)
        self.checks = []

    def check(self, what, projected_inr):
        self.checks.append((what, projected_inr))
        super().check(what, projected_inr)


def _fake_load(ep):
    tmp = Path(tempfile.mkdtemp()) / "t.json"
    tmp.write_text("{}", encoding="utf-8")
    return lambda vid, with_loudness=True: (ep, None, {}, tmp)


class Base(unittest.TestCase):
    def setUp(self):
        self.ep = episode()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = ws.deep_merge(ws.DEFAULTS, {})

    def run_ep(self, prov, guard=None, dry_run=False, ledger=0.0):
        cap = {}

        def fake_doc(ep, tp, props, rej, cfg, llm, n_moments, n_passed, t0, debug):
            doc = {"video_id": "vid", "run_id": "t", "cost": {"usd": 0.0}, "proposals": props,
                   "rejected": [r for r in rej if r], "episode_verdict": ws.episode_verdict(props, cfg["thresholds"]),
                   "cutoff": {"show_rule": cfg["thresholds"]["show_rule"], "t_hide": cfg["thresholds"]["t_hide"]}}
            cap.update(props=props, rejected=rej, debug=debug, doc=doc, md=REAL_MD(ep, doc))
            return doc
        with mock.patch.object(ws, "load_episode", _fake_load(self.ep)), \
                mock.patch("shotvi.llm.client.get_provider", return_value=prov), \
                mock.patch("shotvi.llm.spend.total_inr", return_value=ledger), \
                mock.patch.object(ws, "run_document", fake_doc), mock.patch.object(ws, "clips_md", lambda *a: ""):
            llm = LLMClient(Ledger(), cache_dir=self.tmp.name)
            r = ws.run_episode("vid", ws.deep_merge(self.cfg, {}), llm, guard or RecGuard(None),
                               Path(self.tmp.name) / "out", log=lambda *_: None, dry_run=dry_run)
        cap["result"] = r
        return cap


class NothingBlocked(Base):
    def test_one_combined_review_and_no_fallback_fields(self):
        prov = FakeProvider()
        cap = self.run_ep(prov)
        self.assertEqual(prov.reviews, [["w01", "w02", "w03", "w04", "w05"]])
        self.assertNotIn("review_fallback", cap["debug"])
        # the review prompt is exactly the combined prompt of all clips (same cache key as before the fallback)
        s, u = ws.review_prompt(cap["debug"]["review_input"], self.cfg)
        llm = LLMClient(Ledger(), cache_dir=self.tmp.name)
        self.assertTrue(ws.is_cached(llm, ws.spec_of(self.cfg, "ws_review"), s, u, ws.REVIEW_SCHEMA))
        props = cap["props"]
        self.assertEqual(len(props), 5)
        for p in props:
            self.assertNotIn("review_status", p)
            self.assertNotIn("review_blocked", p["flags"])
            self.assertNotEqual(p["tier"], "unchecked")
        self.assertEqual(cap["doc"]["episode_verdict"]["unchecked"], 0)
        self.assertNotIn("unchecked", cap["md"])


class OneClipBlocked(Base):
    def test_only_the_blocked_clip_ends_unchecked(self):
        prov = FakeProvider(block_ids={"w03"})
        guard = RecGuard(None)
        cap = self.run_ep(prov, guard)
        # combined (blocked) -> [w01 w02] ok, [w03 w04 w05] blocked -> [w03] blocked, [w04 w05] ok
        self.assertEqual(prov.reviews, [["w01", "w02", "w03", "w04", "w05"], ["w01", "w02"],
                                        ["w03", "w04", "w05"], ["w03"], ["w04", "w05"]])
        props = {p["id"]: p for p in cap["props"]}
        self.assertEqual(set(props), {"w01", "w02", "w03", "w04", "w05"})       # never silently dropped
        x = props["w03"]
        self.assertEqual((x["tier"], x["review_status"], x["review_block_reason"]), ("unchecked", "blocked", REASON))
        self.assertEqual(x["scores"], {})
        self.assertIn(ws.BLOCKED_NOTE, x["why_it_works"])
        self.assertEqual(x["review_note"], ws.BLOCKED_NOTE)
        self.assertIn("review_blocked", x["flags"])
        self.assertTrue(x["title"].strip())
        self.assertEqual(ws.shown_field_problems(cap["props"]), [])
        # everyone else is scored as usual; the unchecked clip is last, after the other candidates
        for i in ("w01", "w02", "w04", "w05"):
            self.assertEqual(props[i]["scores"]["overall"], 4.0 if i != "w05" else 3.0)
            self.assertNotIn("review_status", props[i])
        self.assertEqual([p["id"] for p in cap["props"]][-2:], ["w05", "w03"])
        self.assertEqual([p["tier"] for p in cap["props"]][-2:], ["worth_a_look", "unchecked"])
        fb = cap["debug"]["review_fallback"]
        self.assertEqual(fb["combined_block"], REASON)
        self.assertEqual(fb["blocked_clips"], {"w03": REASON})
        self.assertEqual([c["status"] for c in fb["calls"]], ["ok", "blocked", "blocked", "ok"])
        v = cap["doc"]["episode_verdict"]
        self.assertEqual((v["strong_clips"], v["worth_a_look"], v["unchecked"]), (3, 1, 1))
        self.assertIn("NOTE: Gemini's safety check blocked this clip; review it manually", cap["md"])
        self.assertIn("unchecked, no review scores", cap["md"])
        # every billed review attempt went through the guard (combined + 4 splits), plus the two up-front checks
        per_call = [c for c in guard.checks if c[0] == "vid ws_review"]
        self.assertEqual(len(per_call), 5)
        self.assertEqual(sum("split fallback (worst case)" in c[0] for c in guard.checks), 1)
        self.assertEqual(sum("ws_review (+ split fallback if blocked)" in c[0] for c in guard.checks), 1)

    def test_rerun_replays_the_blocks_from_the_cache_for_free(self):
        self.run_ep(FakeProvider(block_ids={"w03"}))
        prov = FakeProvider(block_ids={"w03"})
        with mock.patch.object(prov, "generate", side_effect=AssertionError("called")):
            cap = self.run_ep(prov)
        self.assertEqual([p["tier"] for p in cap["props"]][-1], "unchecked")

    def test_dry_run_reports_the_split_as_the_paid_stage(self):
        # the combined review is a cached block; the halves were never asked
        prov = FakeProvider(block_ids={"w03"})
        self.run_ep(prov)
        for f in Path(self.tmp.name).rglob("*.json"):
            d = json.loads(f.read_text(encoding="utf-8"))
            if (d.get("meta") or {}).get("split"):
                f.unlink()
        prov2 = FakeProvider(block_ids={"w03"})
        with mock.patch.object(prov2, "generate", side_effect=AssertionError("called")):
            cap = self.run_ep(prov2, dry_run=True)
        r = cap["result"]
        self.assertEqual(r["first_paid_stage"], "ws_review_split")
        self.assertEqual(r["review_blocked"], REASON)
        self.assertGreater(r["would_bill_inr_guard"], 0)

    def test_single_blocked_clip_needs_no_split_call(self):
        global MOMENTS
        keep = MOMENTS
        try:
            MOMENTS = keep[:1]
            prov = FakeProvider(block_ids={"w01"})
            cap = self.run_ep(prov)
        finally:
            MOMENTS = keep
        self.assertEqual(prov.reviews, [["w01"]])
        self.assertEqual([(p["id"], p["tier"]) for p in cap["props"]], [("w01", "unchecked")])

    def test_unchecked_can_be_hidden_by_config(self):
        self.cfg["thresholds"]["show_unchecked"] = False
        cap = self.run_ep(FakeProvider(block_ids={"w03"}))
        self.assertNotIn("w03", [p["id"] for p in cap["props"]])
        self.assertIn("R0", [r for r in cap["rejected"] if r["id"] == "w03"][0]["reject_reasons"][0])


class SpendGuard(Base):
    def test_fallback_is_projected_at_about_twice_the_review(self):
        rspec = ws.spec_of(self.cfg, "ws_review")
        for n in (4, 8, 12, 15, 20):
            one = ws.project_usd(ws.REVIEW_IN_BASE + ws.REVIEW_IN_PER_CLIP * n, ws.review_out_guard(rspec.params, n),
                                 rspec.model)
            ratio = ws.review_fallback_usd(rspec, n) / one
            self.assertTrue(1.4 <= ratio <= 2.2, (n, ratio))   # 1.5x at 4 clips, ~1.9x from 12
        self.assertEqual(ws.review_split_sizes(12), [6, 6, 3, 3, 1, 2, 1, 1])
        self.assertEqual(ws.review_split_sizes(1), [])
        proj = ws.project_episode(self.ep, None, self.cfg, "s", "u" * 4000)
        self.assertGreater(proj["ws_review"]["inr_fallback"], proj["ws_review"]["inr"])
        self.assertAlmostEqual(proj["episode"]["inr"], proj["ws_discover"]["inr"] + proj["ws_review"]["inr"]
                               + proj["ws_review"]["inr_fallback"] + proj["video_end_check"]["inr"], places=1)

    def test_review_refused_when_its_fallback_would_not_fit(self):
        self.run_ep(FakeProvider())                        # caches discover (and a review we then forget)
        for f in Path(self.tmp.name).rglob("*.json"):
            if (json.loads(f.read_text(encoding="utf-8")).get("meta") or {}).get("role") == "ws_review":
                f.unlink()
        g = RecGuard(None)
        self.run_ep(FakeProvider(), g)                     # measure the review's own and fallback projections
        what, both = next(c for c in g.checks if "split fallback if blocked" in c[0])
        for f in Path(self.tmp.name).rglob("*.json"):
            if (json.loads(f.read_text(encoding="utf-8")).get("meta") or {}).get("role") == "ws_review":
                f.unlink()
        prov = FakeProvider()
        with self.assertRaises(ws.BudgetStop) as e:
            self.run_ep(prov, RecGuard(1000.0 + both - 0.01), ledger=1000.0)
        self.assertIn("split fallback", str(e.exception))
        self.assertEqual(prov.reviews, [])                 # nothing billed

    def test_split_calls_stop_on_the_guard(self):
        # the combined review is blocked; the ledger limit leaves room for nothing more: the fallback stops before
        # its first split call (a BudgetStop, never a silent drop of the clips)
        self.run_ep(FakeProvider(block_ids={"w03"}))
        for f in Path(self.tmp.name).rglob("*.json"):
            if (json.loads(f.read_text(encoding="utf-8")).get("meta") or {}).get("split"):
                f.unlink()
        prov = FakeProvider(block_ids={"w03"})
        with self.assertRaises(ws.BudgetStop) as e:
            self.run_ep(prov, RecGuard(1000.01), ledger=1000.0)
        self.assertIn("split fallback (worst case)", str(e.exception))
        self.assertEqual(prov.reviews, [])


class DiscoverBlocked(Base):
    def test_clear_error_naming_the_block_reason(self):
        prov = FakeProvider(block_discover=True)
        with self.assertRaises(SafetyBlocked) as e:
            self.run_ep(prov)
        msg = str(e.exception)
        self.assertIn("ws_discover", msg)
        self.assertIn(REASON, msg)
        self.assertIn("no clips", msg)
        self.assertEqual(e.exception.reason, REASON)
        self.assertEqual(prov.n_discover, 1)               # never retried
        self.assertEqual(prov.reviews, [])


class ClientAndProvider(unittest.TestCase):
    SPEC = ModelSpec("gemini", "gemini-3.8-flash", {"thinking_level": "low"})

    def test_gemini_block_reason_is_safety_blocked_and_billed(self):
        from shotvi.llm import gemini
        p = gemini.GeminiProvider(api_key="x")
        resp = {"promptFeedback": {"blockReason": REASON}, "usageMetadata": {"promptTokenCount": 1234}}
        with mock.patch.object(p, "_post", return_value=resp), mock.patch.object(gemini, "spend") as sp:
            with self.assertRaises(SafetyBlocked) as e:
                p.generate(self.SPEC, "s", "p", {"type": "object"})
        self.assertEqual(e.exception.reason, REASON)
        self.assertEqual(e.exception.response.input_tokens, 1234)
        sp.record.assert_called_once()
        # no candidates WITHOUT a block reason stays a retryable error (behaves as before)
        from shotvi.llm.base import RetryableError
        with mock.patch.object(p, "_post", return_value={"usageMetadata": {}}), mock.patch.object(gemini, "spend"):
            with self.assertRaises(RetryableError):
                p.generate(self.SPEC, "s", "p", {"type": "object"})

    def test_client_never_retries_a_block_and_remembers_it(self):
        class Prov:
            n = 0

            def generate(self, spec, system, prompt, schema, media=None):
                Prov.n += 1
                raise SafetyBlocked("b", REASON, response=RawResponse("", 50, 0))
        with tempfile.TemporaryDirectory() as d, mock.patch("shotvi.llm.client.get_provider", return_value=Prov()), \
                mock.patch("shotvi.llm.client.time.sleep"):
            led = Ledger()
            llm = LLMClient(led, cache_dir=d, max_retries=4)
            with self.assertRaises(SafetyBlocked):
                llm.generate_json("r", self.SPEC, "s", "p", {"type": "object"})
            self.assertEqual(Prov.n, 1)
            self.assertEqual(led.summary()["input_tokens"], 50)         # the refused prompt's billed input
            llm2 = LLMClient(Ledger(), cache_dir=d)
            llm2.cache_only = True
            with self.assertRaises(SafetyBlocked) as e:                 # cached block: no call, even cache-only
                llm2.generate_json("r", self.SPEC, "s", "p", {"type": "object"})
            self.assertEqual(Prov.n, 1)
            self.assertIn("cached block", str(e.exception))


if __name__ == "__main__":
    unittest.main()
