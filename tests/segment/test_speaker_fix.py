"""speaker_fix: rendering, answer validation (never invents lines), spend guard (retries count). No network."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shotvi.llm.base import RawResponse
from shotvi.segment import speaker_fix as F

from .helpers import build


def transcript():
    d = build([("speaker_0", "ఏంటి Asha ఇంత late", 0.5), ("speaker_0", "బయట వర్షం", 0.8),
               ("speaker_1", "అవును అమ్మా", 0.8), ("speaker_0", "సరే తిను", 0.8)],
              events=[("[నవ్వు]", 2.0, 2.4)])
    d["sentences"] = [{"id": 0, "text": "ఏంటి Asha ఇంత late", "start": 0.5, "end": 2.0, "speaker": "speaker_0",
                       "words": [0, 3]},
                      {"id": 1, "text": "బయట వర్షం", "start": 2.8, "end": 3.45, "speaker": "speaker_0",
                       "words": [4, 5]},
                      {"id": 2, "text": "అవును అమ్మా", "start": 4.25, "end": 4.9, "speaker": "speaker_1",
                       "words": [6, 7]},
                      {"id": 3, "text": "సరే తిను", "start": 5.7, "end": 6.35, "speaker": "speaker_0",
                       "words": [8, 9]}]
    return d


class TestRender(unittest.TestCase):
    def test_lines_and_events(self):
        txt, n = F.render(transcript())
        self.assertEqual(n, 4)
        rows = txt.splitlines()
        self.assertEqual(rows[0], "0 [0:00] s0| ఏంటి Asha ఇంత late")
        self.assertEqual(rows[1], "(0:02 [నవ్వు])")
        self.assertTrue(rows[3].startswith("2 [0:04] s1| "))

    def test_asr_corrections_applied(self):
        corr = {"corrections": [{"start_word": 4, "end_word": 5, "heard": "బయట వర్షం", "corrected": "బయట వాన",
                                 "applied": True, "confidence": 0.9}]}
        self.assertEqual(F.line_texts(transcript(), corr)[1], "బయట వాన")
        self.assertEqual(F.line_texts(transcript(), None)[1], "బయట వర్షం")

    def test_prompt_fills(self):
        tr = transcript()
        ch = F.plan_chunks(tr)[0]
        tag, system, user = F.chunk_request(tr, ch, 1, {}, F.line_texts(tr, None))
        self.assertIn("lines 0 to 3", user)
        self.assertIn("No speakers identified yet", user)
        cast = {"A": {"key": "A", "name": "Asha", "desc": "student", "aka": []}}
        _, _, user2 = F.chunk_request(tr, ch, 1, cast, F.line_texts(tr, None))
        self.assertIn("A: Asha (student)", user2)
        for ph in ("$n_lines", "$last_id", "$lines"):
            self.assertNotIn(ph, user)
        self.assertTrue(tag.startswith("2+"))


class TestParse(unittest.TestCase):
    def test_valid_answer(self):
        ans = {"cast": [{"key": "A", "name": "Mother", "desc": "Asha's mother"},
                        {"key": "B", "name": "Asha", "desc": "student"}],
               "turns": ["0-1:A", "2:B?", "3:A??"]}
        r = F.parse_answer(ans, transcript())
        self.assertEqual([l["name"] for l in r["lines"]], ["Mother", "Mother", "Asha", "Mother"])
        self.assertEqual([l["confidence"] for l in r["lines"]], [0.9, 0.9, 0.6, 0.3])
        self.assertEqual(r["coverage"]["assigned"], 4)
        self.assertEqual(r["coverage"]["missing"], 0)
        self.assertEqual(r["cast"][0]["n_lines"], 3)
        self.assertEqual(r["cast"][0]["n_words"], 8)
        self.assertEqual(r["lines"][2]["words"], [6, 7])
        self.assertEqual(r["lines"][2]["scribe"], "speaker_1")

    def test_invalid_items_never_invent_lines(self):
        ans = {"cast": [{"key": "A", "name": "Asha", "desc": ""}, {"key": "A", "name": "Dup", "desc": ""}],
               "turns": ["0:A", "0:B", "1-9:A", "garbage", "2:Z", "1:X", "3 : A ?"]}
        r = F.parse_answer(ans, transcript())
        cov = r["coverage"]
        self.assertEqual(len(r["lines"]), 4)
        self.assertEqual(len(r["cast"]), 1)                       # duplicate key dropped
        self.assertIn("1-9:A", cov["invalid_items"])              # beyond the transcript
        self.assertIn("garbage", cov["invalid_items"])
        self.assertEqual(cov["bad_keys"], ["B", "Z"])
        self.assertEqual(cov["duplicate_ids"], 1)                 # 0:B after 0:A
        self.assertIsNone(r["lines"][1]["name"])                  # X
        self.assertEqual(cov["unknown"], 2)                       # line 1 (X) and line 2 (bad key Z)
        self.assertEqual(r["lines"][3]["name"], "Asha")
        self.assertEqual(r["lines"][3]["confidence"], 0.6)
        self.assertEqual(cov["missing"], 0)

    def test_missing_lines(self):
        r = F.parse_answer({"cast": [{"key": "A", "name": "Asha", "desc": ""}], "turns": ["0:A"]}, transcript())
        self.assertEqual(r["coverage"]["missing"], 3)
        self.assertEqual(F.word_names(r, 10)[:5], ["Asha"] * 4 + [None])


def long_transcript(n=300, gap_every=None):
    """n one-word lines, 1 s apart; a 9 s silence before each id in ``gap_every``."""
    gaps = set(gap_every or ())
    segs = [(f"speaker_{i % 2}", f"w{i}", 9.0 if i in gaps else 0.7) for i in range(n)]
    d = build(segs)
    d["sentences"] = [{"id": i, "text": f"w{i}", "start": w["start"], "end": w["end"], "speaker": w["speaker"],
                       "words": [i, i]} for i, w in enumerate(d["word_timestamps"])]
    return d


class TestChunks(unittest.TestCase):
    def test_plan_covers_once_and_cuts_at_silence(self):
        tr = long_transcript(300, gap_every=[95, 205])
        ch = F.plan_chunks(tr)
        cores = [i for c in ch for i in range(c["core"][0], c["core"][1] + 1)]
        self.assertEqual(cores, list(range(300)))
        self.assertEqual(ch[1]["core"][0], 95)                    # the long silence wins
        for c in ch:
            n = c["core"][1] - c["core"][0] + 1
            self.assertLessEqual(n, F.LAST_MAX)
            self.assertEqual(c["shown"][0], max(0, c["core"][0] - F.MARGIN))
        self.assertEqual(len(F.plan_chunks(long_transcript(50))), 1)

    def test_merge_prefers_core_then_margin(self):
        tr = long_transcript(30)
        chunks = [{"part": 1, "core": [0, 14], "shown": [0, 19]}, {"part": 2, "core": [15, 29], "shown": [10, 29]}]
        cast = {"A": {"key": "A", "name": "Asha", "desc": "", "aka": []},
                "B": {"key": "B", "name": "Ravi", "desc": "", "aka": []}}
        ans1 = {i: ("A", "") for i in range(20)}
        ans2 = {i: ("B", "") for i in range(10, 30) if i != 16}   # part 2 skipped line 16
        r = F.merge(tr, chunks, {1: ans1, 2: ans2}, cast)
        names = [l["name"] for l in r["lines"]]
        self.assertEqual(names[12], "Asha")                       # core of part 1 beats part 2's margin
        self.assertEqual(names[17], "Ravi")
        self.assertEqual(names[16], "Asha")                       # fallback: part 1's margin answer
        self.assertTrue(r["lines"][16]["from_margin"])
        self.assertEqual(r["coverage"]["from_margin"], 1)
        self.assertEqual(r["coverage"]["missing"], 0)

    def test_cast_rename_and_carry(self):
        cast = {}
        F.parse_chunk({"cast": [{"key": "A", "name": "the roommate", "desc": "rich"}], "turns": ["0-1:A"]},
                      range(0, 2), cast)
        got, cov = F.parse_chunk({"cast": [{"key": "A", "name": "Dhana", "desc": ""},
                                           {"key": "B", "name": "Asha", "desc": "x"}],
                                  "turns": ["2:A", "3:B", "9:A"]}, range(2, 4), cast)
        self.assertEqual(cast["A"]["name"], "Dhana")
        self.assertEqual(cast["A"]["aka"], ["the roommate"])
        self.assertEqual(cast["A"]["desc"], "rich")               # empty desc keeps the old one
        self.assertEqual(got, {2: ("A", ""), 3: ("B", "")})
        self.assertEqual(cov["invalid_items"], ["9:A"])           # not shown in this chunk

    def test_run_chunked_one_attempt_and_failure_fallback(self):
        from shotvi.llm.base import LLMError, ModelSpec
        tr = long_transcript(300, gap_every=[95, 205])

        class C:
            before_call = None

            def __init__(self):
                self.prompts = []

            def generate_json(self, role, spec, system, prompt, schema, meta=None, want_raw=False):
                self.prompts.append(prompt)
                if meta["part"] == 2:
                    raise LLMError("boom")
                ids = [int(l.split(" ", 1)[0]) for l in prompt.splitlines() if l[:1].isdigit()]
                ans = {"cast": [{"key": "A", "name": "Asha", "desc": "d"}], "turns": [f"{ids[0]}-{ids[-1]}:A"]}
                return ans, RawResponse("{}", 10, 5, 5, None)

        c = C()
        res = F.run(tr, "v", c, ModelSpec.from_dict(F.MODEL), log=lambda *a: None)
        self.assertEqual(len(c.prompts), 3)                        # one attempt per chunk, failure not retried
        self.assertIn("A: Asha (d)", c.prompts[2])                 # cast carried into later parts
        self.assertEqual(len(res["failures"]), 1)
        cov = res["coverage"]
        self.assertGreater(cov["missing"], 0)                      # part 2's core minus the neighbours' margins
        self.assertEqual(cov["from_margin"], 2 * F.MARGIN)


class FakeClient:
    def __init__(self, ans):
        self.ans, self.calls, self.before_call = ans, [], None

    def generate_json(self, role, spec, system, prompt, schema, meta=None, want_raw=False):
        if self.before_call:
            self.before_call(role, spec)
        self.calls.append(prompt)
        raw = RawResponse(json.dumps(self.ans), 100, 20, 30, None)
        return (self.ans, raw) if want_raw else self.ans


class TestRunAndGuard(unittest.TestCase):
    def test_run_with_fake_client(self):
        from shotvi.llm.base import ModelSpec
        c = FakeClient({"cast": [{"key": "A", "name": "Asha", "desc": ""}], "turns": ["0-3:A"]})
        res = F.run(transcript(), "vid", c, ModelSpec.from_dict(F.MODEL), log=lambda *a: None)
        self.assertEqual(res["schema"], F.SCHEMA_VERSION)
        self.assertEqual(res["coverage"]["assigned"], 4)
        self.assertIn("projected", res["cost"])

    def test_guard_counts_in_flight_retries(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(F, "SPEND_FILE", Path(d) / "s.json"):
            F.add_session_spend(2.0, "earlier")
            g = F.make_guard(5.0, None, 10.0, in_flight=lambda: 0.0)
            g("r", None)                                          # 2 + 5 <= 10
            g = F.make_guard(5.0, None, 10.0, in_flight=lambda: 6.0)
            with self.assertRaises(F.SpeakerFixStop):              # 2 + 6 (a failed attempt) + 5 > 10
                g("r", None)

    def test_guard_ledger(self):
        with mock.patch("shotvi.llm.spend.total_inr", return_value=1496.0):
            with self.assertRaises(F.SpeakerFixStop):
                F.make_guard(5.0, 1500.0, None)("r", None)
            F.make_guard(3.9, 1500.0, None)("r", None)


if __name__ == "__main__":
    unittest.main()
