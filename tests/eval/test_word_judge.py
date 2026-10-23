"""eval.word_judge: pairing, blind A/B assignment, schema, aggregation, spend guard, review page. No network."""
import csv
import json
import re
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from eval import word_judge as W
from shotvi.llm.base import LLMError, ModelSpec, RawResponse, SafetyBlocked

ROOT = Path(__file__).resolve().parents[2]
VID = "p3XTZlZ6-xw"


def wav_file(seconds=30.0, sr=16000):
    f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(f, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * int(seconds * sr))
    f.close()
    return f.name


def tr_of(words):
    """words: [(text, start, end)]"""
    return {"word_timestamps": [{"word": t, "start": a, "end": b, "speaker": "speaker_0", "logprob": -0.1}
                                for t, a, b in words]}


def sv_of(segs):
    """segs: [(text, start, end)]; given out of order on purpose (pairing sorts them)."""
    return {"diarized_transcript": {"entries": [{"transcript": t, "start_time_seconds": a, "end_time_seconds": b,
                                                 "speaker_id": "0"} for t, a, b in reversed(segs)]}}


def sample():
    # seg 0 identical; seg 1 / seg 2: "అబ్బా" (1.9-2.1) overlaps seg 1 more in time but Sarvam wrote it in seg 2
    # (a boundary migration); seg 3 a real substitution; one orphan word at 9.0
    tr = tr_of([("అమ్మా!", 0.0, 0.4), ("లే.", 0.5, 0.8),
                ("ఏంటే", 1.0, 1.4), ("అబ్బా", 1.9, 2.1),
                ("ఒక", 2.2, 2.4), ("విషయం", 2.5, 2.9),
                ("cake", 4.0, 4.3), ("తిను", 4.4, 4.8),
                ("హలో", 9.0, 9.3)])
    sv = sv_of([("అమ్మా, లే.", 0.0, 0.9), ("ఏంటే", 1.0, 2.05), ("అబ్బా ఒక విషయం", 2.0, 3.0),
                ("కేక్ తిను", 3.9, 5.0)])
    return tr, sv


class FakeClient:
    """generate_json like LLMClient; ``answer(prompt, media)`` builds the reply; records calls; calls before_call."""

    def __init__(self, answer, raise_exc=None):
        self.answer, self.raise_exc, self.calls, self.before_call = answer, raise_exc, [], None

    def generate_json(self, role, spec, system, prompt, schema, meta=None, want_raw=False, media=None):
        if self.before_call:
            self.before_call(role, spec)
        self.calls.append({"system": system, "prompt": prompt, "media": media, "schema": schema})
        if self.raise_exc:
            raise self.raise_exc
        obj = self.answer(prompt, media)
        raw = RawResponse(json.dumps(obj), 1000, 200, 300)
        return (obj, raw) if want_raw else obj


def clean_answer(prompt, media):
    n = sum(1 for m in media if "data" in m or m.get("mime_type") == "audio/wav")
    return {"clips": [{"clip": k, "heard_text": "x", "a_errors": 0, "b_errors": 0, "a_error_words": [],
                       "b_error_words": [], "a_filler_diff": 0, "b_filler_diff": 0, "filler_only": True,
                       "unclear": False} for k in range(1, n + 1)]}


SPEC = ModelSpec.from_dict(W.MODEL)


class TestPairing(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(W.toks("అమ్మా, లే-అమ్మా! Cake."), ["అమ్మా", "లే", "అమ్మా", "cake"])

    def test_assignment_max_overlap_and_orphan(self):
        tr, sv = sample()
        words, segs = W.scribe_words(tr), W.sarvam_segments(sv)
        own, orphan = W.assign_words(words, segs)
        self.assertEqual(own[0], [0, 1])
        self.assertEqual(own[1], [2, 3])        # అబ్బా 1.9-2.1: 0.15 s in seg 1, 0.1 s in seg 2
        self.assertEqual(own[2], [4, 5])
        self.assertEqual(orphan, [8])

    def test_raw_counts_without_merge(self):
        tr, sv = sample()
        p = W.pairing(tr, sv, merge=False)
        self.assertEqual(p["n_sarvam_segments"], 4)
        self.assertEqual(p["raw_segments_differ"], 3)          # segs 1, 2 (migration) and 3 (script)
        self.assertEqual(p["units_differ"], 4)                 # + the orphan
        self.assertEqual(p["scribe_tokens"], 9)
        self.assertEqual(p["sarvam_tokens"], 8)

    def test_boundary_migration_merged_away(self):
        tr, sv = sample()
        p = W.pairing(tr, sv)
        self.assertEqual(p["merges"], 1)
        self.assertEqual(p["units_differ"], 2)                 # cake/కేక్ and the orphan remain
        merged = [u for u in p["units"] if len(u["seg_ids"]) == 2][0]
        self.assertFalse(merged["differs"])
        self.assertEqual(merged["scribe_text"], "ఏంటే అబ్బా ఒక విషయం")
        # every Scribe word and every Sarvam segment is in exactly one unit
        self.assertEqual(sorted(i for u in p["units"] for i in u["word_idx"]), list(range(9)))
        self.assertEqual(sorted(k for u in p["units"] for k in u["seg_ids"]), [0, 1, 2, 3])

    def test_unrelated_neighbour_diffs_not_merged(self):
        tr = tr_of([("ఒకటి", 0.0, 0.5), ("రెండు", 1.0, 1.5)])
        sv = sv_of([("ఒక్కటి", 0.0, 0.6), ("మూడు", 0.9, 1.6)])
        p = W.pairing(tr, sv)
        self.assertEqual(p["merges"], 0)
        self.assertEqual(p["units_differ"], 2)

    def test_stretched_word_end_capped_for_audio_span(self):
        tr = tr_of([("నేను", 10.0, 10.3), ("వస్తా.", 10.4, 48.0)])
        sv = sv_of([("నేను వస్తాను", 10.0, 11.0)])
        u = W.pairing(tr, sv)["units"][0]
        self.assertLessEqual(u["clip_t1"], 10.4 + W.MAX_WORD_S + W.PAD_S + 1e-6)
        self.assertAlmostEqual(u["clip_t0"], 9.7, places=2)

    @unittest.skipUnless((ROOT / "scribe_dataset" / VID / f"{VID}_sarvam_raw.json").exists(), "dataset missing")
    def test_real_episode_matches_scratch_script(self):
        tr, sv = W.load_inputs(VID)
        p = W.pairing(tr, sv, merge=False)
        self.assertEqual((p["n_sarvam_segments"], p["raw_segments_differ"]), (452, 325))
        self.assertEqual((p["scribe_tokens"], p["sarvam_tokens"], p["orphan_words"]), (2340, 2293, 23))


class TestBlind(unittest.TestCase):
    def test_assignment_stable_and_balanced(self):
        keys = [f"s{k}" for k in range(400)]
        a = [W.scribe_is_a(7, VID, k) for k in keys]
        self.assertEqual(a, [W.scribe_is_a(7, VID, k) for k in keys])
        self.assertEqual(a[::-1], [W.scribe_is_a(7, VID, k) for k in reversed(keys)])   # order-independent
        self.assertTrue(150 < sum(a) < 250)
        self.assertNotEqual(a, [W.scribe_is_a(8, VID, k) for k in keys])

    def test_request_is_blind_and_carries_audio(self):
        tr, sv = sample()
        p = W.pairing(tr, sv)
        xs = W.judge_units(p["units"], 7)
        system, user, media = W.request(xs, wav_file(12), VID, 7)
        blob = (system + user + json.dumps([{k: v for k, v in m.items() if k != "data"} for m in media])).lower()
        for name in ("scribe", "sarvam", "elevenlabs", "saaras"):
            self.assertIsNone(re.search(rf"\b{name}\b", blob), name)
        self.assertEqual([m.get("text") for m in media[::2]], [f"Clip {k}:" for k in range(1, len(xs) + 1)])
        self.assertTrue(all(m["mime_type"] == "audio/wav" and m["data"] for m in media[1::2]))
        for k, u in enumerate(xs, 1):
            a, b, sa = W.versions(u, 7, VID)
            self.assertIn(f"## Clip {k}", user)
            self.assertIn(f"A: {a}\nB: {b}", user)
            self.assertEqual(a, (u["scribe_text"] or W.EMPTY) if sa else (u["sarvam_text"] or W.EMPTY))
        self.assertIn(W.EMPTY, user)   # the orphan unit: Sarvam wrote nothing

    def test_batches_respect_limits(self):
        us = [{"clip_t0": 0.0, "clip_t1": 10.0}] * 30
        self.assertEqual([len(b) for b in W.make_batches(us, 13, 1000)], [13, 13, 4])
        self.assertEqual([len(b) for b in W.make_batches(us, 13, 25)], [2] * 15)


class TestSchemaParse(unittest.TestCase):
    def test_schema_accepts_and_rejects(self):
        import jsonschema
        ok = clean_answer("", [{"data": "x", "mime_type": "audio/wav"}])
        ok["clips"][0]["a_error_words"] = [{"type": "wrong", "written": "ఏంటే", "said": "ఏంటి"}]
        jsonschema.validate(ok, W.SCHEMA)
        bad = json.loads(json.dumps(ok))
        bad["clips"][0]["a_error_words"][0]["type"] = "spelling"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(bad, W.SCHEMA)
        del bad["clips"][0]["unclear"]
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(bad, W.SCHEMA)

    def test_parse_drops_bad_numbers_and_marks_missing(self):
        batch = [{"id": "u1"}, {"id": "u2"}, {"id": "u3"}]
        ans = clean_answer("", [{"data": "x", "mime_type": "audio/wav"}] * 2)
        ans["clips"].append(dict(ans["clips"][0], heard_text="dup"))
        ans["clips"].append(dict(ans["clips"][0], clip=9))
        got, probs = W.parse_answer(ans, batch)
        self.assertEqual(sorted(got), ["u1", "u2"])
        self.assertEqual(got["u1"]["heard_text"], "x")
        self.assertEqual(len(probs), 3)


def judged(a_items, b_items, unclear=False, a_said=None):
    return {"heard_text": "h", "unclear": unclear, "filler_only": False,
            "a_errors_said": len(a_items) if a_said is None else a_said, "b_errors_said": len(b_items),
            "a_error_words": a_items, "b_error_words": b_items, "a_filler_diff": 1, "b_filler_diff": 0}


class TestAggregate(unittest.TestCase):
    def setUp(self):
        tr, sv = sample()
        self.p = W.pairing(tr, sv)
        self.xs = [u for u in self.p["units"] if u["differs"]]

    def test_resolve_maps_back_and_aggregate(self):
        u1, u2 = self.xs
        wrong = {"type": "wrong", "written": "w", "said": "s"}
        miss = {"type": "missing", "written": "", "said": "s"}
        r1 = W.resolve(u1, judged([wrong], [], a_said=3), 7, VID)
        sa = W.scribe_is_a(7, VID, u1["key"])
        self.assertEqual(r1["scribe_errors"], 1 if sa else 0)
        self.assertEqual(r1["sarvam_errors"], 0 if sa else 1)
        self.assertEqual(r1["verdict"], "sarvam_better" if sa else "scribe_better")
        self.assertTrue(r1["count_mismatch"])
        r2 = W.resolve(u2, judged([miss], [miss, wrong], unclear=True), 7, VID)
        self.assertEqual(r2["verdict"], "unclear")
        rows = [r1, r2]
        ag = W.aggregate(self.p, rows)
        self.assertEqual((ag["judged"], ag["clear"], ag["unclear"]), (2, 1, 1))
        self.assertEqual(ag["scribe_errors"] + ag["sarvam_errors"], 1)         # unclear unit adds nothing
        self.assertEqual(ag["scribe_words"], 9)
        self.assertAlmostEqual(ag["scribe_err_per_100"], round(100 * ag["scribe_errors"] / 9, 2))
        self.assertEqual(sum(ag["error_types"]["scribe"].values()) + sum(ag["error_types"]["sarvam"].values()), 1)
        self.assertEqual(ag["count_mismatch"], 1)
        md = W.summary_md(VID, self.p, ag, [], None)
        self.assertIn("errors per 100 words", md)

    def test_tie_and_unchecked(self):
        u1, u2 = self.xs
        r1 = W.resolve(u1, judged([], []), 7, VID)
        r2 = W.resolve(u2, None, 7, VID, "unchecked")
        self.assertEqual(r1["verdict"], "tie")
        ag = W.aggregate(self.p, [r1, r2])
        self.assertEqual((ag["tie"], ag["tie_zero_errors"], ag["unchecked"]), (1, 1, 1))
        self.assertEqual(ag["words_not_judged"]["scribe"], len(u2["scribe_toks"]))

    def test_asr_fix_overlap(self):
        u = [x for x in self.xs if x["word_idx"]][0]
        corr = {"corrections": [{"start_word": u["word_idx"][0], "end_word": u["word_idx"][0], "heard": "a",
                                 "corrected": "b", "applied": True, "source": "relisten", "confidence": 0.9},
                                {"start_word": 0, "end_word": 0, "heard": "x", "corrected": "y", "applied": True}]}
        out = W.asr_fix_overlaps(self.p, [W.resolve(u, judged([], []), 7, VID)], corr)
        self.assertEqual([(x["unit"], x["corrected"], x["verdict"]) for x in out], [(u["id"], "b", "tie")])

    def test_compare_human(self):
        u1, u2 = self.xs
        wrong = {"type": "wrong", "written": "w", "said": "s"}
        rows = [W.resolve(u1, judged([wrong], []), 7, VID), W.resolve(u2, judged([], []), 7, VID)]
        f = Path(tempfile.mkdtemp()) / "h.csv"
        with f.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["n", "unit", "choice", "note"])
            w.writerow([1, u1["id"], "B better", ""])
            w.writerow([2, u2["id"], "A better", ""])
        h = W.compare_human(f, rows)
        self.assertEqual((h["n"], h["agree"]), (2, 1))
        self.assertEqual(h["disagreements"], [u2["id"]])


class TestRunAndGuard(unittest.TestCase):
    def setUp(self):
        tr, sv = sample()
        self.p = W.pairing(tr, sv)
        self.audio = wav_file(12)

    def test_run_judges_every_differing_unit(self):
        fc = FakeClient(clean_answer)
        res = W.run(self.p, VID, fc, SPEC, self.audio, log=lambda *a: None)
        self.assertEqual(len(fc.calls), 1)
        self.assertEqual({r["status"] for r in res["rows"]}, {"judged"})
        self.assertEqual(len(res["rows"]), self.p["units_differ"])

    def test_guard_stops_before_any_call(self):
        fc = FakeClient(clean_answer)
        with mock.patch("shotvi.llm.spend.total_inr", return_value=1799.99):
            res = W.run(self.p, VID, fc, SPEC, self.audio, max_ledger_inr=1800.0, log=lambda *a: None)
        self.assertEqual(fc.calls, [])
        self.assertIn("worst case", res["stopped"])
        self.assertEqual({r["verdict"] for r in res["rows"]}, {"not_run"})

    def test_guard_allows_when_room(self):
        fc = FakeClient(clean_answer)
        with mock.patch("shotvi.llm.spend.total_inr", return_value=10.0):
            res = W.run(self.p, VID, fc, SPEC, self.audio, max_ledger_inr=1800.0, log=lambda *a: None)
        self.assertIsNone(res["stopped"])
        self.assertEqual(len(fc.calls), 1)

    def test_guard_function(self):
        g = W.make_guard(5.0, 100.0)
        with mock.patch("shotvi.llm.spend.total_inr", return_value=94.0):
            g("word_judge", SPEC)
        with mock.patch("shotvi.llm.spend.total_inr", return_value=95.5):
            with self.assertRaises(W.WordJudgeStop):
                g("word_judge", SPEC)
        W.make_guard(1e9, None)("word_judge", SPEC)   # no limit: never stops

    def test_safety_block_marks_batch_unchecked(self):
        fc = FakeClient(clean_answer, SafetyBlocked("blocked", "PROHIBITED_CONTENT"))
        res = W.run(self.p, VID, fc, SPEC, self.audio, log=lambda *a: None)
        self.assertEqual({r["verdict"] for r in res["rows"]}, {"unchecked"})
        self.assertEqual(res["batches"][0]["status"], "safety_blocked")
        self.assertEqual(len(fc.calls), 1)

    def test_failure_marks_unchecked(self):
        fc = FakeClient(clean_answer, LLMError("bad"))
        res = W.run(self.p, VID, fc, SPEC, self.audio, log=lambda *a: None)
        self.assertEqual(res["batches"][0]["status"], "failed")

    def test_real_client_one_attempt_and_guard_on_every_call(self):
        """LLMClient(max_retries=0, schema_retries=0) with a fake provider: an invalid answer is not retried."""
        from shotvi.llm import client as C

        class Prov:
            name = "fakejudge"
            n = 0

            def generate(self, spec, system, prompt, schema, media=None, cached_content=None):
                Prov.n += 1
                return RawResponse("not json", 100, 10)
        C.PROVIDERS["fakejudge"] = Prov()
        try:
            spec = ModelSpec("fakejudge", "gemini-3.8-flash", {"max_output_tokens": 8192})
            cl = C.LLMClient(C.Ledger(), cache_dir=None, max_retries=0, schema_retries=0)
            seen = []
            res = W.run(self.p, VID, cl, spec, self.audio, log=lambda *a: None)
            self.assertEqual(Prov.n, 1)
            self.assertEqual(res["batches"][0]["status"], "failed")
            # the guard is installed as before_call and runs before the billed attempt
            cl2 = C.LLMClient(C.Ledger(), cache_dir=None, max_retries=0, schema_retries=0)
            with mock.patch("shotvi.llm.spend.total_inr", return_value=1799.99):
                res2 = W.run(self.p, VID, cl2, spec, self.audio, max_ledger_inr=1800.0, log=seen.append)
            self.assertEqual(Prov.n, 1)
            self.assertTrue(res2["stopped"])
        finally:
            del C.PROVIDERS["fakejudge"]

    def test_audio_surcharge_recorded_on_fresh_call(self):
        class Led:
            def __init__(self):
                self.usd = 0.0

            def billed_usd(self):
                return self.usd

        class Fresh(FakeClient):
            def generate_json(self, *a, **k):
                self.ledger.usd += 0.01
                obj, raw = super().generate_json(*a, **k)
                raw.prompt_modalities = {"AUDIO": 500}
                return obj, raw
        fc = Fresh(clean_answer)
        fc.ledger = Led()
        with mock.patch("shotvi.llm.spend.record") as rec:
            res = W.run(self.p, VID, fc, SPEC, self.audio, log=lambda *a: None)
        rec.assert_called_once()
        self.assertEqual(rec.call_args[0][1], 500)          # (factor 2 - 1) x audio tokens
        self.assertEqual(res["cost"]["fresh"], 1)

    def test_max_calls_and_projection(self):
        fc = FakeClient(clean_answer)
        res = W.run(self.p, VID, fc, SPEC, self.audio, batch_items=1, max_calls=1, log=lambda *a: None)
        self.assertEqual(len(fc.calls), 1)
        self.assertEqual(sum(r["status"] == "not_run" for r in res["rows"]), 1)
        pj = W.project(self.p, VID, SPEC, self.audio, 7, batch_items=1, count_tokens=False, log=lambda *a: None)
        self.assertEqual(pj["calls"], 2)
        self.assertGreater(pj["inr_worst"], pj["inr_expected"])
        self.assertEqual(pj["worst_output_tokens"], 2 * 8192)
        self.assertIn("32 tokens/s", pj["token_source"])


class TestReviewPage(unittest.TestCase):
    def test_page_is_blind_and_local(self):
        tr, sv = sample()
        p = W.pairing(tr, sv)
        out = Path(tempfile.mkdtemp())
        page = W.build_review(p, VID, wav_file(12), out, 7, 30)
        h = page.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\b(scribe|sarvam)\b", h.lower()))
        self.assertNotIn("http", h)                       # no external resources
        self.assertIn("A better", h)
        self.assertIn("Download CSV", h)
        self.assertEqual(len(list((out / "clips").glob("*.wav"))), p["units_differ"])
        items = W.review_sample(p, 7, 30)
        self.assertEqual([u["id"] for u in items], [u["id"] for u in W.review_sample(p, 7, 30)])


if __name__ == "__main__":
    unittest.main()
