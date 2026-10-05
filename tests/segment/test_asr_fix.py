"""ASR correction layer with a fake LLM client (never calls a real API)."""
import base64
import io
import json
import math
import tempfile
import unittest
import wave
from pathlib import Path

from shotvi.segment import asr_fix as A

from .helpers import build

LOW = math.log(0.3)    # confidence 0.3
HIGH = math.log(0.95)


def transcript(low_words=()):
    """Two speakers; words listed in ``low_words`` get confidence 0.3. Builds sentences[] like Scribe."""
    segs = [("s0", "నేను ఈ రోజు అన్నం వండాను", 0.0),
            ("s1", "ఏం కూర చేశావు ?", 0.8),
            ("s0", "పప్పు చేశాను hop off చేశాను", 0.8),
            ("s1", "ఉమ్", 0.8),
            ("s0", "బాగుంది కదా", 0.8)]
    d = build(segs, logprob=HIGH)
    for w in d["word_timestamps"]:
        if w["word"] in low_words:
            w["logprob"] = LOW
    # sentences: one per segment
    sents, i = [], 0
    for k, (spk, text, _) in enumerate(segs):
        n = len(text.split())
        ws = d["word_timestamps"][i:i + n]
        sents.append({"id": k, "text": text, "start": ws[0]["start"], "end": ws[-1]["end"], "speaker": spk,
                      "words": [i, i + n - 1]})
        i += n
    d["sentences"] = sents
    d["audio_events"] = [{"text": "[నవ్వు]", "start": 1.0, "end": 1.2}]
    return d


class Raw:
    def __init__(self, n_in=100, n_out=20, audio=0):
        self.input_tokens, self.output_tokens, self.thought_tokens = n_in, n_out, 0
        self.prompt_modalities = {"AUDIO": audio} if audio else {}


class FakeClient:
    """Answers by role; records every call."""

    def __init__(self, context=None, relisten=None):
        self.context, self.relisten, self.calls = context, relisten, []

    def generate_json(self, role, spec, system, prompt, schema, meta=None, want_raw=False, media=None):
        self.calls.append({"role": role, "system": system, "prompt": prompt, "schema": schema, "media": media})
        if role == "asr_context":
            obj = self.context(prompt) if callable(self.context) else self.context
        else:
            obj = self.relisten(prompt, media) if callable(self.relisten) else self.relisten
        return (obj, Raw()) if want_raw else obj


def wav_file(seconds=10.0, sr=16000):
    f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(f, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * int(seconds * sr))
    f.close()
    return f.name


def line_id(tr, text_start):
    for ln in A.build_lines(tr):
        if ln.kind == "speech" and ln.text.startswith(text_start):
            return ln
    raise KeyError(text_start)


class TestLinesAndFlags(unittest.TestCase):
    def test_lines_include_events_and_map_words(self):
        tr = transcript()
        lines = A.build_lines(tr)
        self.assertEqual([ln.id for ln in lines], [f"L{k:04d}" for k in range(1, len(lines) + 1)])
        self.assertEqual(sum(ln.kind == "event" for ln in lines), 1)
        speech = [ln for ln in lines if ln.kind == "speech"]
        self.assertEqual(speech[0].i0, 0)
        self.assertEqual(speech[-1].i1, len(tr["word_timestamps"]) - 1)

    def test_fallback_lines_without_sentences(self):
        tr = transcript()
        tr.pop("sentences")
        speech = [ln for ln in A.build_lines(tr) if ln.kind == "speech"]
        self.assertEqual(len(speech), 5)   # split on speaker change / 0.8 s gaps

    def test_flag_threshold_and_missing_logprob(self):
        tr = transcript(low_words=("కూర", "off"))
        self.assertEqual([tr["word_timestamps"][i]["word"] for i in A.flag_words(tr, 0.5)], ["కూర", "off"])
        self.assertEqual(A.flag_words(tr, 0.2), [])
        for w in tr["word_timestamps"]:
            w.pop("logprob")
        self.assertEqual(A.flag_words(tr, 0.99), [])
        rep = A.threshold_report(tr)
        self.assertFalse(rep["has_logprob"])

    def test_contiguous_flags_merge_per_line(self):
        tr = transcript(low_words=("hop", "off", "ఉమ్"))
        lines = A.build_lines(tr)
        sus = A.flag_suspects(tr, lines, A.flag_words(tr))
        self.assertEqual([(s.i1 - s.i0 + 1) for s in sus], [2, 1])

    def test_render_marks_flagged_words(self):
        tr = transcript(low_words=("off",))
        lines = A.build_lines(tr)
        _, user = A.context_request(tr, lines, A.flag_words(tr))
        self.assertIn("hop off(?) చేశాను", user)
        self.assertIn("(audio event) [నవ్వు]", user)
        self.assertNotIn("$lines", user)


class TestContextParse(unittest.TestCase):
    def test_positions_echo_and_fallback_search(self):
        tr = transcript(low_words=("off",))
        lines = A.build_lines(tr)
        ln = line_id(tr, "పప్పు")
        ans = {"suspects": [
            {"line": ln.id, "first": 3, "last": 4, "heard": "hop off", "guess": "half", "kind": "flagged", "why": "x"},
            {"line": line_id(tr, "నేను").id, "first": 9, "last": 9, "heard": "అన్నం", "guess": "అప్పం",
             "kind": "context", "why": "y"},          # wrong position, unique echo -> found
            {"line": ln.id, "first": 1, "last": 1, "heard": "nothing", "guess": "", "kind": "context", "why": "z"},
            {"line": "L9999", "first": 1, "last": 1, "heard": "x", "guess": "y", "kind": "context", "why": "z"},
        ]}
        sus, rej = A.parse_context(ans, tr, lines, A.flag_words(tr))
        self.assertEqual(len(sus), 2)
        self.assertEqual(len(rej), 2)
        self.assertEqual((sus[0].i0, sus[0].i1), (ln.idx(3), ln.idx(4)))
        self.assertEqual(sus[0].found_by, {"context", "flag"})
        self.assertEqual(tr["word_timestamps"][sus[1].i0]["word"], "అన్నం")
        self.assertEqual(sus[1].found_by, {"context"})

    def test_merge_with_flags(self):
        tr = transcript(low_words=("off",))
        lines = A.build_lines(tr)
        ln = line_id(tr, "పప్పు")
        ctx = [A.Suspect(ln.id, ln.idx(3), ln.idx(4), {"context"}, guess="half")]
        m = A.merge_suspects(A.flag_suspects(tr, lines, A.flag_words(tr)), ctx)
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0].found_by, {"flag", "context"})
        self.assertEqual(m[0].guess, "half")


class TestRelisten(unittest.TestCase):
    def setUp(self):
        self.tr = transcript(low_words=("off",))
        self.lines = A.build_lines(self.tr)
        self.ln = line_id(self.tr, "పప్పు")
        sus = A.flag_suspects(self.tr, self.lines, A.flag_words(self.tr))
        self.items = A.plan_items(self.tr, self.lines, sus)
        self.batch = self.items

    def answer(self, fixes, heard_line="పప్పు చేశాను half చేశాను", clip=1):
        return {"clips": [{"clip": clip, "heard_line": heard_line, "fixes": fixes}]}

    def test_accepts_confident_aligned_fix_with_one_word_extension(self):
        fx, rej, ck = A.parse_relisten(self.answer([{"first": 3, "last": 4, "corrected": "half", "confidence": 0.9,
                                                      "reason": "clear"}]), self.tr, self.batch)
        self.assertEqual(rej, [])
        self.assertEqual(len(fx), 1)
        self.assertTrue(fx[0]["accepted"])
        self.assertEqual((fx[0]["start_word"], fx[0]["end_word"]), (self.ln.idx(3), self.ln.idx(4)))
        self.assertEqual(ck, {self.ln.id})

    def test_low_confidence_is_recorded_not_accepted(self):
        fx, _, _ = A.parse_relisten(self.answer([{"first": 4, "last": 4, "corrected": "of", "confidence": 0.6,
                                                   "reason": "?"}], heard_line="పప్పు చేశాను hop of చేశాను"),
                                    self.tr, self.batch)
        self.assertFalse(fx[0]["accepted"])

    def test_rejections(self):
        cases = [
            ({"first": 1, "last": 1, "corrected": "పప్పులు", "confidence": 0.95, "reason": ""}, "not on a suspect"),
            ({"first": 2, "last": 4, "corrected": "x", "confidence": 0.95, "reason": ""}, "not on a suspect"),
            ({"first": 4, "last": 9, "corrected": "x", "confidence": 0.95, "reason": ""}, "outside the line"),
            ({"first": 4, "last": 4, "corrected": "bus", "confidence": 0.95, "reason": ""}, "not in the model"),
        ]
        for fix, why in cases:
            _, rej, _ = A.parse_relisten(self.answer([fix]), self.tr, self.batch)
            self.assertEqual(len(rej), 1, fix)
            self.assertIn(why, rej[0]["why_rejected"])
        # heard_line from a different line -> no alignment
        _, rej, _ = A.parse_relisten(self.answer([{"first": 4, "last": 4, "corrected": "half", "confidence": 0.95,
                                                    "reason": ""}], heard_line="half అని ఏదో వేరే మాట చెప్పారు అంతే"),
                                     self.tr, self.batch)
        self.assertIn("does not align", rej[0]["why_rejected"])

    def test_no_op_fix_is_ignored(self):
        fx, rej, ck = A.parse_relisten(self.answer([{"first": 4, "last": 4, "corrected": "off.", "confidence": 0.99,
                                                      "reason": ""}], heard_line="పప్పు చేశాను hop off చేశాను"),
                                       self.tr, self.batch)
        self.assertEqual((fx, rej), ([], []))
        self.assertEqual(ck, {self.ln.id})

    def test_unknown_clip(self):
        _, rej, ck = A.parse_relisten(self.answer([], clip=7), self.tr, self.batch)
        self.assertEqual(len(rej), 1)
        self.assertEqual(ck, set())

    def test_request_has_inline_audio_and_labels(self):
        path = wav_file(10.0)
        system, user, media = A.relisten_request(self.tr, self.lines, self.batch, path, "vid:a.wav")
        self.assertIn("Clip 1", user)
        self.assertIn("<SUSPECT>", user)
        self.assertEqual(media[0], {"text": "Clip 1:"})
        self.assertEqual(media[1]["mime_type"], "audio/wav")
        it = self.batch[0]
        with wave.open(io.BytesIO(base64.b64decode(media[1]["data"]))) as w:
            self.assertAlmostEqual(w.getnframes() / w.getframerate(), it.t1 - it.t0, delta=0.01)
        self.assertGreaterEqual(it.t1 - it.t0, A.MIN_CLIP_S - 1e-6)
        self.assertLessEqual(it.t0, self.ln.start)

    def test_batches_respect_limits(self):
        its = [A.Item(self.ln, [], k * 10.0, k * 10.0 + 5.0) for k in range(20)]
        bs = A.batches(its, max_items=8, max_audio_s=30.0)
        self.assertTrue(all(len(b) <= 6 for b in bs))
        self.assertEqual(sum(len(b) for b in bs), 20)


class TestRunAndApply(unittest.TestCase):
    def run_fake(self, ctx_answer, rel_answer, layers=("flag", "context", "relisten")):
        tr = transcript(low_words=("off",))
        fc = FakeClient(ctx_answer, rel_answer)
        res = A.run(tr, "vid", fc, None, layers, audio=wav_file(10.0), log=lambda *a: None)
        return tr, res, fc

    def test_end_to_end(self):
        tr = transcript(low_words=("off",))
        ln = line_id(tr, "పప్పు")
        cur = line_id(tr, "ఏం")
        ctx = {"suspects": [
            {"line": ln.id, "first": 3, "last": 4, "heard": "hop off", "guess": "half", "kind": "flagged", "why": "a"},
            {"line": cur.id, "first": 2, "last": 2, "heard": "కూర", "guess": "కూరలు", "kind": "context", "why": "b"},
        ]}

        def rel(prompt, media):
            clips = []
            for k in range(1, prompt.count("## Clip ") + 1):
                if "hop" in prompt.split(f"## Clip {k}")[1].split("## Clip")[0]:
                    clips.append({"clip": k, "heard_line": "పప్పు చేశాను half చేశాను",
                                  "fixes": [{"first": 3, "last": 4, "corrected": "half", "confidence": 0.92,
                                             "reason": "clear"}]})
                else:
                    clips.append({"clip": k, "heard_line": "ఏం కూర చేశావు", "fixes": []})
            return {"clips": clips}
        tr, res, fc = self.run_fake(ctx, rel)
        self.assertEqual([c["role"] for c in fc.calls], ["asr_context", "asr_relisten"])
        self.assertNotIn("half", fc.calls[1]["prompt"], "the relisten prompt must not leak the text-only guess")
        applied = [c for c in res["corrections"] if c["applied"]]
        self.assertEqual(len(applied), 1)
        c = applied[0]
        self.assertEqual((c["source"], c["heard"], c["corrected"]), ("relisten", "hop off", "half"))
        self.assertEqual(c["found_by"], ["context", "flag"])
        ctx_only = [x for x in res["corrections"] if x["source"] == "context"]
        self.assertEqual(len(ctx_only), 1)
        self.assertFalse(ctx_only[0]["applied"])
        self.assertEqual(ctx_only[0]["relisten"]["verdict"], "relisten_kept")
        # display text: transcript unchanged, timings unchanged, corrected text shown
        dw = A.apply_corrections(tr["word_timestamps"], res)
        self.assertEqual(len(dw), len(tr["word_timestamps"]))
        self.assertEqual(dw[c["start_word"]]["text"], "half")
        self.assertEqual(dw[c["end_word"]]["text"], "")
        self.assertEqual(dw[c["end_word"]]["start"], tr["word_timestamps"][c["end_word"]]["start"])
        self.assertIn("పప్పు చేశాను half చేశాను", A.display_text(tr["word_timestamps"], res))
        self.assertEqual(tr["word_timestamps"][c["start_word"]]["word"], "hop", "transcript never modified")
        # context guesses can be shown on request
        self.assertIn("కూరలు", A.display_text(tr["word_timestamps"], res, sources=("relisten", "context")))
        json.dumps(res, ensure_ascii=False)   # serializable

    def test_flag_only_needs_no_client(self):
        tr = transcript(low_words=("off",))
        res = A.run(tr, "vid", None, None, ("flag",), log=lambda *a: None)
        self.assertEqual(res["corrections"], [])
        self.assertEqual(len(res["uncertain"]), 1)
        self.assertEqual(res["uncertain"][0]["verdict"], "not_relistened")
        self.assertEqual(res["uncertain"][0]["source"], "flag")

    def test_budget_guard_stops_before_calling(self):
        tr = transcript(low_words=("off",))
        fc = FakeClient({"suspects": []}, {"clips": []})

        class G(A.Guard):
            def check(self, projected_inr, what):
                raise A.BudgetStop("over")
        res = A.run(tr, "vid", fc, None, audio=wav_file(), guard=G(1.0), log=lambda *a: None)
        self.assertEqual(fc.calls, [])
        self.assertEqual(res["stopped"], "over")

    def test_trailing_punctuation_kept_and_overlaps_resolved(self):
        words = [{"word": w, "start": k, "end": k + 0.5} for k, w in enumerate("ఏం వండవు. సరే".split())]
        corr = {"corrections": [
            {"start_word": 0, "end_word": 1, "corrected": "ఏం వండావు.", "confidence": 0.9, "applied": True},
            {"start_word": 1, "end_word": 1, "corrected": "x", "confidence": 0.5, "applied": True},
        ]}
        self.assertEqual(A.display_text(words, corr), "ఏం వండావు. సరే")
        self.assertEqual(A._keep_trailing_punct("వండవు.", "వండావు"), "వండావు.")

    def test_load_missing_layer_is_empty(self):
        self.assertEqual(A.load_corrections(Path(tempfile.gettempdir()) / "nope" / "corrections.json")["corrections"],
                         [])

    def test_projection_is_free_and_positive(self):
        tr = transcript(low_words=("off", "కూర"))
        lines = A.build_lines(tr)
        p = A.project(tr, lines, A.flag_words(tr), {"flag", "context", "relisten"})
        self.assertGreater(p["layers"]["context"]["input_tokens"], 0)
        self.assertGreater(p["layers"]["relisten"]["audio_tokens"], 0)
        self.assertGreater(p["inr"], 0)


class TestGuards(unittest.TestCase):
    def test_style_only_changes(self):
        S = A.style_only_change
        self.assertIn("interjection", S("ఆ", "హాహా"))
        self.assertIn("interjection", S("ఊ", "ఉహ్"))
        self.assertIn("script", S("Super", "సూపర్"))
        self.assertIn("script", S("గా Kumari Anty", "గా కుమారి ఆంటీ"))   # shared word trimmed first
        for h, c in [("మే", "మీ"), ("ఏం వండవు", "ఏం వండావు"), ("week", "వీధి"), ("ఆనికి నేమ్", "nickname"),
                     ("అజ్జన్", "urgent"), ("గోలా", "కీమా"), ("hop off", "half")]:
            self.assertIsNone(S(h, c), (h, c))

    def test_interjection_lexicon_is_not_greedy(self):
        for w in ("ఆ", "ఊ", "ఉమ్", "హహహ", "మ్", "um", "haha"):
            self.assertTrue(A.is_interjection(w), w)
        for w in ("మీ", "అమ్మా", "ఏం", "home", "కదా", "me"):
            self.assertFalse(A.is_interjection(w), w)

    def _one_line(self, text, low, nxt=None):
        segs = [("s0", text, 0.0)] + ([("s1", nxt, 0.1)] if nxt else [])
        d = build(segs, logprob=HIGH)
        n = len(text.split())
        for i in low:
            d["word_timestamps"][i]["logprob"] = LOW
        d["sentences"] = [{"id": 0, "words": [0, n - 1]}] + ([{"id": 1, "words": [n, len(d["word_timestamps"]) - 1]}]
                                                             if nxt else [])
        lines = A.build_lines(d)
        items = A.plan_items(d, lines, A.flag_suspects(d, lines, A.flag_words(d)))
        return d, items

    def test_fix_that_absorbs_the_next_word_extends_the_span(self):
        d, items = self._one_line("ఈ పట్టు ఇమ్మా ఏంటమ్మా!", low=(1, 2))
        ans = {"clips": [{"clip": 1, "heard_line": "ఈ పట్టింపులేంటమ్మా",
                          "fixes": [{"first": 2, "last": 3, "corrected": "పట్టింపులేంటమ్మా", "confidence": 0.95,
                                     "reason": ""}]}]}
        fx, rej, _ = A.parse_relisten(ans, d, items)
        self.assertEqual(rej, [])
        self.assertEqual((fx[0]["start_word"], fx[0]["end_word"]), (1, 3))
        self.assertIn("ఈ పట్టింపులేంటమ్మా", A.display_text(d["word_timestamps"], {"corrections": [
            {**fx[0], "applied": True}]}))

    def test_fix_that_is_the_next_lines_word_is_rejected(self):
        d, items = self._one_line("మటన్ గోలా", low=(1,), nxt="కొట్టినట్టు కొట్టాలనిపిస్తుంది")
        items = [it for it in items if it.line.text.startswith("మటన్")]
        ans = {"clips": [{"clip": 1, "heard_line": "మటన్ కొట్ట",
                          "fixes": [{"first": 2, "last": 2, "corrected": "కొట్ట", "confidence": 0.95, "reason": ""}]}]}
        fx, rej, _ = A.parse_relisten(ans, d, items)
        self.assertEqual(fx, [])
        self.assertIn("neighbouring word", rej[0]["why_rejected"])

    def test_echo_with_two_occurrences_takes_the_nearest(self):
        d, _ = self._one_line("ఛా ఛా బుర్ర లేని మదక్ బుర్ర లేని మదక్", low=(4,))
        lines = A.build_lines(d)
        ans = {"suspects": [{"line": lines[0].id, "first": 4, "last": 4, "heard": "మదక్", "guess": "x",
                             "kind": "flagged", "why": ""}]}
        sus, rej = A.parse_context(ans, d, lines, A.flag_words(d))
        self.assertEqual((sus[0].i0, rej), (4, []))

    def test_band_and_interjection_skip(self):
        tr = transcript()
        ws = tr["word_timestamps"]
        ws[1]["logprob"] = math.log(0.55)              # "ఈ" in the band
        ws[0]["logprob"] = math.log(0.3)               # below the band
        self.assertEqual(A.flag_words(tr, 0.6, 0.5), [1])
        k = next(i for i, w in enumerate(ws) if w["word"] == "ఉమ్")
        ws[k]["logprob"] = LOW
        fc = FakeClient({"suspects": []}, lambda p, m: {"clips": []})
        res = A.run(tr, "vid", fc, None, ("flag", "relisten"), audio=wav_file(10.0), log=lambda *a: None)
        prompt = fc.calls[0]["prompt"]
        self.assertNotIn("ఉమ్<SUSPECT>", prompt)
        self.assertIn("skipped_interjection", [u["verdict"] for u in res["uncertain"]])


class TestPromptsAreGeneric(unittest.TestCase):
    def test_no_answer_key_words_in_prompts(self):
        banned = ["బ్రతికించారు", "ప్రతికించారు", "blood", "కుక్క", "cook", "బిస్మిల్లా", "బిసిబేళ", "hop off", "half",
                  "వండావు", "ఇరుకుదా", "కూరంటా", "గోరింట", "వీధి", "week", "గోలా", "కీమా", "విగించాలా", "బిగించాలా",
                  "తుమ్ము", "dog"]
        for p in (A.CONTEXT_PROMPT, A.RELISTEN_PROMPT):
            txt = p.read_text(encoding="utf-8")
            for b in banned:
                self.assertNotIn(b, txt, f"{p.name} mentions {b!r}")


class TestMediaPart(unittest.TestCase):
    def test_inline_and_text_parts_and_unchanged_file_parts(self):
        from shotvi.llm.client import _media_key
        from shotvi.llm.gemini import _media_part
        self.assertEqual(_media_part({"data": "QUJD", "mime_type": "audio/wav", "key": "k"}),
                         {"inline_data": {"mime_type": "audio/wav", "data": "QUJD"}})
        self.assertEqual(_media_part({"text": "Clip 1:"}), {"text": "Clip 1:"})
        f = {"file_uri": "u", "mime_type": "video/mp4", "start_s": 1, "end_s": 2, "key": "v"}
        self.assertEqual(_media_part(f)["file_data"], {"mime_type": "video/mp4", "file_uri": "u"})
        self.assertEqual(_media_key(f), {"mime_type": "video/mp4", "start_s": 1, "end_s": 2, "key": "v"})
        self.assertEqual(_media_key({"data": "QUJD", "mime_type": "audio/wav", "key": "k"}),
                         {"mime_type": "audio/wav", "key": "k"})
        self.assertIn("data_sha", _media_key({"data": "QUJD", "mime_type": "audio/wav"}))


if __name__ == "__main__":
    unittest.main()
