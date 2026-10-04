import json
import tempfile
import unittest
from pathlib import Path

from shotvi.segment import estimate_tokens, segment_dict
from shotvi.segment.__main__ import main as cli_main

from .helpers import build, monologue


def interview():
    """Host asks (speaker_0, appears first), guest (speaker_1) answers at length."""
    segs = [("speaker_0", "hello sir welcome. మీ first movie ఎలా వచ్చింది?", 0)]
    segs += monologue("speaker_1", 6, prefix="a")
    segs += [("speaker_0", "ఉమ్", 0.1)]
    segs += monologue("speaker_1", 3, prefix="b")
    segs += [("speaker_0", "super story", 0.4)]           # short non-question comment
    segs += monologue("speaker_1", 3, prefix="c")
    segs += [("speaker_0", "and then ఏం జరిగింది?", 0.6)]  # next question
    segs += monologue("speaker_1", 5, prefix="d")
    segs += [("speaker_0", "నిన్న మీ పాట విన్నాను చాలా బాగుంది sir మీరే పాడారంట", 0.6)]  # prompt
    segs += monologue("speaker_1", 6, prefix="e")
    return segs


class QATest(unittest.TestCase):
    def setUp(self):
        self.seg = segment_dict(build(interview()))

    def test_host_inferred_from_behaviour(self):
        self.assertEqual(self.seg.host, "speaker_0")
        self.assertEqual(self.seg.roles["speaker_1"].role, "guest")
        self.assertGreater(self.seg.host_confidence, 0)

    def test_answer_runs_through_short_comments_until_next_question(self):
        links = self.seg.links
        self.assertGreaterEqual(len(links), 3)
        l0 = links[0]
        ws = self.seg.transcript.words
        self.assertEqual(l0.kind, "question")
        self.assertEqual(ws[l0.a_i0].text, "a0x0")
        self.assertEqual(ws[l0.a_i1].text, "c2x7.")
        self.assertEqual(l0.end_reason, "next_question")
        self.assertEqual(ws[l0.q_core_i0].text, "మీ")
        self.assertEqual(links[-1].kind, "prompt")
        for l in links:
            self.assertTrue(l.a_units and l.q_units)

    def test_monologue_has_no_links(self):
        seg = segment_dict(build(monologue("A", 20)))
        self.assertEqual(seg.links, [])


class SignalsTest(unittest.TestCase):
    def test_signals(self):
        d = build([("A", "నేను happy గా ఉన్నా. " * 4, 0)],
                  events=[("[నవ్వు]", 1.0, 1.5), ("[సెన్సార్డ్]", 2.0, 3.0)], logprob=-1.2)
        seg = segment_dict(d)
        s = seg.units[0].signals
        self.assertEqual(s["laughter"], 1)
        self.assertTrue(s["censored"])
        self.assertAlmostEqual(s["censored_s"], 1.0, places=2)
        self.assertEqual(s["latin_frac"], 0.25)
        self.assertEqual(s["low_conf_frac"], 1.0)
        self.assertIsNotNone(s["speech_rate_rel"])

    def test_no_logprob_no_events(self):
        seg = segment_dict(build(monologue("A", 5), drop=("logprob",)))
        s = seg.units[0].signals
        self.assertIsNone(s["logprob_mean"])
        self.assertEqual((s["laughter"], s["censored"]), (0, False))

    def test_question_flag(self):
        seg = segment_dict(build(interview()))
        self.assertTrue(seg.unit_of_word(0).signals["question"])


class SnapTest(unittest.TestCase):
    def setUp(self):
        self.seg = segment_dict(build(interview(), events=[]))
        self.ws = self.seg.transcript.words

    def test_mid_sentence_snaps_to_sentence_boundary(self):
        w = self.ws
        i = next(k for k, x in enumerate(w) if x.text == "a2x3")
        j = next(k for k, x in enumerate(w) if x.text == "a4x4")
        r = self.seg.snap(w[i].start + 0.1, w[j].end - 0.1)
        self.assertTrue(r.clean)
        self.assertTrue(w[r.start_word - 1].text.endswith("."))
        self.assertTrue(w[r.end_word].text.endswith("."))
        self.assertLessEqual(abs(r.start_shift), 4.0)

    def test_never_starts_or_ends_on_backchannel_and_rolls_stay_clear(self):
        bc = {i for t in self.seg.turns for b in t.backchannels for i in range(b.i0, b.i1 + 1)}
        self.assertTrue(bc)
        for t0 in [x / 2 for x in range(0, int(self.seg.transcript.duration * 2) - 20, 3)]:
            r = self.seg.snap(t0, t0 + 8)
            self.assertNotIn(r.start_word, bc)
            self.assertNotIn(r.end_word, bc)
            self.assertLessEqual(r.start_word, r.end_word)
            if r.start_word > 0:
                self.assertGreaterEqual(r.cut_in, self.ws[r.start_word - 1].end)
            if r.end_word + 1 < len(self.ws):
                self.assertLessEqual(r.cut_out, self.ws[r.end_word + 1].start)
            self.assertLessEqual(r.cut_in, r.start)
            self.assertGreaterEqual(r.cut_out, r.end)

    def test_expand_mode_contains_request(self):
        r = self.seg.snap(10.3, 20.7, mode="expand")
        self.assertLessEqual(r.start, 10.3 + 0.25)
        self.assertGreaterEqual(r.end, 20.7 - 0.25)

    def test_trailing_laughter_kept(self):
        segs = monologue("A", 3) + [("A", "z0 z z z z.", 2.5)]
        d = build(segs)
        end = d["word_timestamps"][23]["end"]
        d["audio_events"] = [{"text": "[నవ్వు]", "start": end + 0.1, "end": end + 1.2}]
        seg = segment_dict(d)
        r = seg.snap_words(8, 23)
        self.assertAlmostEqual(r.cut_out, end + 1.4, places=2)


class RenderTest(unittest.TestCase):
    def test_render_collapses_backchannels_and_marks_events(self):
        segs = [("H", "మీ story ఏంటి?", 0)] + [("G", "g1 g2 g3 g4 g5 g6.", 0.3), ("H", "ఉమ్", 0.1),
                                               ("G", "g7 g8 g9 g10 g11.", 0.1), ("H", "ఇది కావాలి", 0.1),
                                               ("G", "g12 g13 g14 g15 g16 g17 g18.", 0.1)]
        d = build(segs, events=[("[నవ్వు]", 3.2, 3.4)])
        seg = segment_dict(d)
        text = seg.render()
        self.assertNotIn("ఉమ్", text)
        self.assertIn("[H: ఇది కావాలి]", text)
        self.assertIn("(laugh)", text)
        self.assertTrue(text.startswith("U0 [0:00] H:"))
        self.assertIn("| G:", text)

    def test_chunks_cover_all_units_with_overlap(self):
        seg = segment_dict(build(monologue("A", 300, prefix="తెలుగు")))
        chunks = seg.render_chunks(max_tokens=600, overlap_tokens=100)
        self.assertGreater(len(chunks), 3)
        self.assertEqual(chunks[0].first_unit, 0)
        self.assertEqual(chunks[-1].last_unit, len(seg.units) - 1)
        for a, b in zip(chunks, chunks[1:]):
            self.assertLessEqual(b.first_unit, a.last_unit + 1)
            self.assertGreater(b.first_unit, a.first_unit)
            self.assertTrue(b.overlap_units)
        self.assertTrue(all(c.est_tokens <= 600 for c in chunks))

    def test_token_estimate(self):
        self.assertGreater(estimate_tokens("తెలుగు"), estimate_tokens("telugu"))

    def test_to_dict_json_and_cli(self):
        d = build(interview(), events=[("[నవ్వు]", 5.0, 5.5)])
        seg = segment_dict(d)
        out = seg.to_dict()
        json.dumps(out, ensure_ascii=False)
        self.assertEqual(out["schema_version"], "shotvi.segment/1")
        self.assertEqual(out["units"][0]["words"][0], 0)
        self.assertEqual(out["units"][-1]["words"][1], len(d["word_timestamps"]) - 1)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "vid_scribe_transcript.json"
            src.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            dst = Path(tmp) / "r" / "segments.json"
            self.assertEqual(cli_main([str(src), "--out", str(dst), "--quiet"]), 0)
            got = json.loads(dst.read_text(encoding="utf-8"))
            self.assertEqual(got["source"]["video_id"], "vid")
            self.assertTrue((dst.parent / "transcript.txt").exists())


if __name__ == "__main__":
    unittest.main()
