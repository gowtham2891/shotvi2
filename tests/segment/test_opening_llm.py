"""LLM opening detection with a fake client (never calls a real API)."""
import os
import unittest
from unittest import mock

from shotvi.segment import from_dict, segment_dict
from shotvi.segment.opening_llm import (SCHEMA, OpeningLLM, OpeningLLMError, find_all_fragments, load_prompt,
                                        render_opening, validate, window_end_index)
from shotvi.segment.pauses import pause_stats
from shotvi.segment.pipeline import opening_mode

from .helpers import build
from .test_intro import GREETING, G, H, _answer, body, teaser, with_teaser


class FakeClient:
    def __init__(self, answer=None, exc=None):
        self.answer, self.exc, self.calls = answer, exc, []

    def generate_json(self, role, spec, system, prompt, schema, meta=None):
        self.calls.append({"role": role, "system": system, "prompt": prompt, "schema": schema})
        if self.exc:
            raise self.exc
        return dict(self.answer)


def ans(teaser=(-1, -1), song=(-1, -1), intro=(-1, -1), content=0, conf=0.9, reason="test"):
    return {"teaser_start_word": teaser[0], "teaser_end_word": teaser[1], "song_start_word": song[0],
            "song_end_word": song[1], "intro_start_word": intro[0], "intro_end_word": intro[1],
            "content_start_word": content, "confidence": conf, "reason": reason}


def run(d, answer=None, exc=None, mode="llm"):
    fc = FakeClient(answer, exc)
    seg = segment_dict(d, detect_intro=mode, opening_llm=OpeningLLM(client=fc, spec=None, min_duration_s=0))
    return seg, fc


class TestLLMOpening(unittest.TestCase):
    def test_llm_ranges_replace_rules_and_keep_fragments(self):
        d, n = with_teaser()
        n_greet = 6 + 6  # GREETING words
        seg, fc = run(d, ans(teaser=(0, n - 1), intro=(n, n + n_greet - 1), content=n + n_greet,
                             reason="montage then greeting"))
        self.assertEqual(len(fc.calls), 1)
        self.assertEqual(seg.opening["decided_by"], "llm")
        kinds = [(r.kind, r.i0, r.i1) for r in seg.intro_ranges]
        self.assertEqual(kinds, [("teaser", 0, n - 1), ("intro", n, n + n_greet - 1)])
        t = seg.intro_ranges[0]
        self.assertEqual(t.evidence["decided_by"], "llm")
        self.assertEqual(t.evidence["llm_reason"], "montage then greeting")
        self.assertTrue(t.fragments, "repeat fragments kept for teaser_body_copy")
        self.assertTrue(seg.teaser_body_copy(0, 5))
        for l in seg.links:
            self.assertGreaterEqual(l.q_i0, n + n_greet)
        self.assertIn("omitted: opening teaser", seg.render())

    def test_teaser_that_never_recurs(self):
        # cut footage: excerpts not in the body; the rules detector cannot see it
        cut = [(G, "x1 x2 x3 x4 x5 x6 x7 x8 x9 x10."), (H, "y1 y2 y3 y4 y5 y6 y7?"), (G, "z1 z2 z3 z4 z5 z6 z7.")]
        segs = teaser(cut) + [(s, t, 5.0 if k == 0 else g) for k, (s, t, g) in enumerate(body(greeting=GREETING))]
        d = build(segs)
        n = 24
        rules, _ = run(d, mode="rules")
        self.assertFalse([r for r in rules.intro_ranges if r.kind == "teaser"])
        seg, _ = run(d, ans(teaser=(0, n - 1), intro=(n, n + 11), content=n + 12))
        t = [r for r in seg.intro_ranges if r.kind == "teaser"]
        self.assertEqual((t[0].i0, t[0].i1), (0, n - 1))
        self.assertEqual(t[0].fragments, [])
        self.assertTrue(all(u.intro_kind == "teaser" for u in seg.units if u.i1 < n))

    def test_gap_before_content_becomes_intro(self):
        d, n = with_teaser()
        seg, _ = run(d, ans(teaser=(0, n - 1), content=n + 12))
        self.assertEqual([(r.kind, r.i0, r.i1) for r in seg.intro_ranges],
                         [("teaser", 0, n - 1), ("intro", n, n + 11)])
        self.assertEqual(seg.intro_ranges[1].evidence.get("note"), "before content start")

    def test_song_and_no_teaser(self):
        segs = [(G, "la la naa paata lyric one lyric two", 0.0)] + body(greeting=GREETING)
        d = build(segs, events=[("[పాట]", 0.0, 3.0)])
        seg, _ = run(d, ans(song=(0, 7), intro=(8, 19), content=20))
        self.assertEqual([(r.kind, r.i0, r.i1) for r in seg.intro_ranges], [("music", 0, 7), ("intro", 8, 19)])

    def test_no_opening_at_all(self):
        d = build(body())
        seg, _ = run(d, ans(content=0, reason="starts with the first question"))
        self.assertEqual(seg.intro_ranges, [])
        self.assertEqual(seg.opening["content_start_word"], 0)

    def test_llm_says_no_teaser_but_strong_repeats_keep_rules(self):
        d, n = with_teaser()
        seg, _ = run(d, ans(intro=(n, n + 11), content=n + 12))
        t = [r for r in seg.intro_ranges if r.kind == "teaser"]
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].evidence["decided_by"], "rules")
        self.assertTrue(t[0].evidence["llm_disagrees"])

    def test_invalid_answers_fall_back_to_rules(self):
        d, n = with_teaser()
        bad = [ans(teaser=(0, 99999), content=n),                 # outside the window
               ans(teaser=(5, 2), content=n),                     # reversed
               ans(teaser=(0, n - 1), intro=(n - 5, n + 3), content=n + 4),  # overlap
               ans(teaser=(0, n - 1), content=3),                 # content inside the teaser
               ans(teaser=(0, -1), content=n),                    # one end missing
               {**ans(content=n), "teaser_start_word": "0"}]      # not an integer
        rules, _ = run(d, mode="rules")
        for a in bad:
            seg, _ = run(d, a)
            self.assertEqual(seg.opening["decided_by"], "rules", a)
            self.assertIn("fallback_reason", seg.opening)
            self.assertEqual([(r.kind, r.i0, r.i1) for r in seg.intro_ranges],
                             [(r.kind, r.i0, r.i1) for r in rules.intro_ranges])

    def test_llm_errors_fall_back(self):
        from shotvi.llm import BudgetExceeded, LLMError
        d, n = with_teaser()
        for exc in (LLMError("boom"), BudgetExceeded("cap"), RuntimeError("network")):
            seg, _ = run(d, exc=exc)
            self.assertEqual(seg.opening["decided_by"], "rules")
            self.assertIn(type(exc).__name__, seg.opening["fallback_reason"])
            self.assertEqual(seg.intro_ranges[0].kind, "teaser")

    def test_modes(self):
        d, n = with_teaser()
        seg, fc = run(d, ans(content=0), mode="rules")
        self.assertEqual(fc.calls, [])
        self.assertEqual(seg.opening["decided_by"], "rules")
        seg, fc = run(d, ans(content=0), mode="off")
        self.assertEqual((fc.calls, seg.intro_ranges), ([], []))
        with mock.patch.dict(os.environ, {"SHOTVI_OPENING_MODE": "off"}):
            self.assertEqual(opening_mode(None), "off")
            self.assertEqual(opening_mode(True), "off")
        self.assertEqual(opening_mode(False), "off")
        with self.assertRaises(ValueError):
            opening_mode("maybe")

    def test_short_transcripts_never_call(self):
        d, n = with_teaser()
        fc = FakeClient(ans(content=0))
        seg = segment_dict(d, detect_intro="llm", opening_llm=OpeningLLM(client=fc))
        self.assertEqual(fc.calls, [])
        self.assertIn("shorter than", seg.opening["fallback_reason"])

    def test_missing_key_falls_back_without_network(self):
        d, n = with_teaser()
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}), \
                mock.patch("shotvi.llm.gemini.GeminiProvider.generate", side_effect=AssertionError("network!")):
            seg = segment_dict(d, detect_intro="llm", opening_llm=OpeningLLM(min_duration_s=0))
        self.assertEqual(seg.opening["decided_by"], "rules")
        self.assertIn("GEMINI_API_KEY", seg.opening["fallback_reason"])


class TestPromptAndValidation(unittest.TestCase):
    def test_prompt_has_evidence_indices_and_events(self):
        d, n = with_teaser()
        tr = from_dict(d)
        ps = pause_stats(tr)
        frs = find_all_fragments(tr)
        text, lines = render_opening(tr, ps, frs)
        self.assertTrue(text.startswith("[w0-"))
        self.assertIn(">> recurs later at", text)
        self.assertIn("(music)", text)
        self.assertEqual(lines[-1][1], window_end_index(tr))
        fc = FakeClient(ans(content=0))
        OpeningLLM(client=fc, min_duration_s=0)(tr, ps, frs)
        call = fc.calls[0]
        self.assertEqual(call["schema"], SCHEMA)
        self.assertIn("content_start_word", call["system"])
        self.assertIn("<opening>", call["prompt"])
        p = load_prompt()
        self.assertTrue(p.version)
        self.assertNotIn("$", p.system)

    def test_validate_normalises(self):
        out = validate(ans(teaser=(0, 9), intro=(10, 12), content=13, conf=1.7), 0, 50)
        self.assertEqual(out["teaser"], (0, 9))
        self.assertIsNone(out["song"])
        self.assertEqual(out["confidence"], 1.0)
        with self.assertRaises(OpeningLLMError):
            validate(ans(content=52), 0, 50)


if __name__ == "__main__":
    unittest.main()
