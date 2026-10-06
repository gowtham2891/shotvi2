"""ASR correction layer as DISPLAY: marks, segment rendering (opt-in) and the review page. Offline, no API."""
import copy
import json
import sys
import unittest
from pathlib import Path

from shotvi.segment import asr_fix as A
from shotvi.segment import segment_dict

from .test_asr_fix import transcript

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import preview_clips  # noqa: E402
import preview_page  # noqa: E402

LOW_WORDS = ("hop", "off", "కూర", "బాగుంది")
# words: 0 నేను 1 ఈ 2 రోజు 3 అన్నం 4 వండాను | 5 ఏం 6 కూర 7 చేశావు 8 ? | 9 పప్పు 10 చేశాను 11 hop 12 off 13 చేశాను
#        | 14 ఉమ్ | 15 బాగుంది 16 కదా


def layer():
    return {"schema": A.SCHEMA_VERSION, "corrections": [
        {"start_word": 11, "end_word": 12, "heard": "hop off", "corrected": "half off", "reason": "clearly says half",
         "source": "relisten", "confidence": 0.9, "applied": True},
        {"start_word": 6, "end_word": 6, "heard": "కూర", "corrected": "కూరలు", "reason": "context", "source": "context",
         "confidence": None, "applied": False, "relisten": {"verdict": "not_relistened"}}],
        "uncertain": [{"start_word": 15, "end_word": 15, "heard": "బాగుంది", "source": "flag", "confidence": 0.3,
                       "verdict": "relisten_kept"}]}


# render(legend=True) of the synthetic transcript, produced by the segmenter BEFORE the asr_fix option existed
TODAY_OFF = ("Each line is one thought unit: U<id> [minutes:seconds] SPEAKER: text. H=host, G=guest (G1/G2 for several "
             "guests), S=solo speaker, S1/S2=speakers when there is no clear host, O=other. '|' marks a speaker change "
             "inside a unit; [X: ...] is a short interjection by X; (laugh)/(censored)/(music) are audio events. "
             "Listener acknowledgements are omitted.\n\n"
             "U0 [0:00] G: నేను ఈ రోజు (laugh) అన్నం వండాను\n"
             "U1 [0:02] H: ఏం కూర చేశావు ? | G: పప్పు చేశాను hop off చేశాను | H: ఉమ్ | G: బాగుంది కదా")


class Marks(unittest.TestCase):
    def setUp(self):
        self.tr = transcript(LOW_WORDS)
        self.ws = self.tr["word_timestamps"]

    def test_applied_fix_and_leftover_low_confidence(self):
        m = A.word_marks(self.ws, layer())
        self.assertEqual({i for i, x in m.items() if x["kind"] == "fixed"}, {11, 12})
        self.assertEqual(m[11]["corrected"], "half off")
        self.assertEqual(m[11]["heard"], "hop off")
        self.assertEqual({i for i, x in m.items() if x["kind"] == "unsure"}, {6, 15})   # p<0.5, no applied fix
        self.assertIn("not applied", m[6]["note"])                    # the unapplied text-only guess is shown
        self.assertIn("agrees", m[15]["note"])                        # re-listen kept Scribe's word

    def test_unapplied_correction_is_not_a_correction(self):
        lay = layer()
        lay["corrections"][0]["applied"] = False
        m = A.word_marks(self.ws, lay)
        self.assertFalse(any(x["kind"] == "fixed" for x in m.values()))

    def test_no_logprob_or_punctuation_never_marked(self):
        ws = copy.deepcopy(self.ws)
        for w in ws:
            w.pop("logprob")
        self.assertEqual(A.word_marks(ws, {"corrections": [], "uncertain": []}), {})
        ws = copy.deepcopy(self.ws)
        ws[8]["logprob"] = -5.0                                      # "?" alone: nothing to read
        self.assertNotIn(8, A.word_marks(ws, {"corrections": [], "uncertain": []}))

    def test_stale_correction_skipped(self):
        lay = layer()
        lay["corrections"][0]["heard"] = "something else"
        self.assertEqual(len(A.stale_corrections(self.ws, lay)), 1)
        m = A.word_marks(self.ws, lay)
        self.assertFalse(any(x["kind"] == "fixed" for x in m.values()))
        self.assertEqual(m[11]["kind"], "unsure")                     # still low-confidence

    def test_marked_text_and_partial_span(self):
        m = A.word_marks(self.ws, layer())
        self.assertEqual(A.marked_text(self.ws, m, range(9, 14)), "పప్పు చేశాను {fixed: half off} చేశాను")
        self.assertEqual(A.marked_text(self.ws, m, range(5, 9)), "ఏం కూర(?) చేశావు ?")
        # a range that cuts the corrected span shows Scribe's words, marked uncertain (never half a correction)
        self.assertEqual(A.marked_text(self.ws, m, range(12, 14)), "off(?) చేశాను")
        toks = A.marked_tokens(self.ws, m, range(12, 14))
        self.assertTrue(toks[0]["mark"]["partial"])
        self.assertIn("crosses", A.mark_tip(toks[0]))
        self.assertEqual(A.display_map(self.ws, m, range(9, 14)), {11: "{fixed: half off}", 12: ""})


class SegmentRender(unittest.TestCase):
    def test_off_is_byte_identical_to_today(self):
        tr = transcript(LOW_WORDS)
        seg = segment_dict(tr, video_id="vidx", detect_intro="off")
        self.assertEqual(seg.render(legend=True), TODAY_OFF)
        self.assertIsNone(seg.asr_fix)
        self.assertNotIn("asr_fix", seg.to_dict())

    def test_on_marks_text_only(self):
        tr = transcript(LOW_WORDS)
        off = segment_dict(copy.deepcopy(tr), video_id="vidx", detect_intro="off")
        on = segment_dict(copy.deepcopy(tr), video_id="vidx", detect_intro="off", asr_fix=layer())
        txt = on.render(legend=True)
        self.assertIn(A.ASR_LEGEND, txt)
        self.assertIn("ఏం కూర(?) చేశావు ? | G: పప్పు చేశాను {fixed: half off} చేశాను | H: ఉమ్ | G: బాగుంది(?) కదా", txt)
        self.assertEqual(on.asr_fix, {"corrections_shown": 1, "low_conf_marked": 2, "stale_skipped": 0})
        d_off, d_on = off.to_dict(), on.to_dict()
        for k in ("turns", "qa_links", "intro_ranges", "speakers"):
            self.assertEqual(d_off[k], d_on[k])
        strip = lambda us: [{k: v for k, v in u.items() if k not in ("text", "est_tokens")} for u in us]  # noqa: E731
        self.assertEqual(strip(d_off["units"]), strip(d_on["units"]))     # ids, words, times, signals unchanged

    def test_on_without_a_layer_renders_like_off_and_warns(self):
        tr = transcript(LOW_WORDS)
        seg = segment_dict(tr, video_id="no_such_video_xyz", detect_intro="off", asr_fix=True)
        self.assertEqual(seg.render(legend=True), TODAY_OFF)
        self.assertTrue(any("asr_fix" in w for w in seg.transcript.warnings))


def clip(pieces):
    return {"rank": 1, "id": "c1", "title": "t", "pieces": pieces, "edited_duration": 3.0,
            "region": {"start": pieces[0]["start"], "end": pieces[-1]["end"]}}


class ReviewPage(unittest.TestCase):
    def setUp(self):
        self.ws = transcript(LOW_WORDS)["word_timestamps"]
        ws = self.ws
        self.c = clip([{"start_word": 5, "end_word": 7, "start": ws[5]["start"], "end": ws[7]["end"]},
                       {"start_word": 11, "end_word": 16, "start": ws[11]["start"], "end": ws[16]["end"]}])
        self.segs = preview_clips.padded_segments(self.c["pieces"], ws)

    def test_off_lines_unchanged(self):
        lines = preview_clips.transcript_lines(self.c, self.segs, self.ws, {})
        says = [ln for ln in lines if ln["kind"] == "say"]
        self.assertEqual(says[0]["text"], "ఏం కూర చేశావు")
        self.assertTrue(all("parts" not in ln for ln in lines))
        txt = preview_clips.transcript_text(preview_clips.clip_meta({**self.c, "scores": {}}), lines, "x.mp4")
        self.assertNotIn("ASR", txt)
        data = [{"video_id": "v", "n_total": 1, "clips": [{"lines": lines}]}]
        page = preview_page.build_page("r", data)
        plain = preview_page.TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
                                              ).replace("__RUN__", "r")
        self.assertEqual(page, plain)                                    # no ASR css / js without marks

    def test_on_lines_parts_tips_and_txt(self):
        marks = A.word_marks(self.ws, layer())
        lines = preview_clips.transcript_lines(self.c, self.segs, self.ws, {}, marks)
        says = [ln for ln in lines if ln["kind"] == "say"]
        self.assertEqual(says[0]["text"], "ఏం కూర చేశావు")              # plain text keeps the words
        self.assertEqual(says[0]["parts"][1]["k"], "u")
        fix = next(p for ln in says for p in ln.get("parts", []) if p.get("k") == "fix")
        self.assertEqual(fix["t"], "half off")
        self.assertIn("Scribe heard: hop off", fix["tip"])
        self.assertIn("clearly says half", fix["tip"])
        self.assertTrue(any(ln["text"].startswith("half off") for ln in says))
        txt = preview_clips.transcript_text(preview_clips.clip_meta({**self.c, "scores": {}}), lines, "x.mp4")
        self.assertIn("[✎ half off]", txt)
        self.assertIn("కూర(?)", txt)
        self.assertIn("ASR fixes", txt)
        page = preview_page.build_page("r", [{"video_id": "v", "n_total": 1, "clips": [{"lines": lines}]}])
        self.assertIn("asrParts(ln.parts)", page)
        self.assertIn(".fix{", page)

    def test_cut_text_is_marked_too(self):
        ws = self.ws
        c = clip([{"start_word": 0, "end_word": 4, "start": ws[0]["start"], "end": ws[4]["end"]},
                  {"start_word": 13, "end_word": 16, "start": ws[13]["start"], "end": ws[16]["end"]}])
        segs = preview_clips.padded_segments(c["pieces"], ws)
        cut = next(ln for ln in preview_clips.transcript_lines(c, segs, ws, {}, A.word_marks(ws, layer()))
                   if ln["kind"] == "cut")                                # words 5-12 cut
        self.assertEqual(cut["text"], "ఏం కూర చేశావు ? పప్పు చేశాను half off")
        self.assertEqual([p.get("k") for p in cut["parts"]], [None, "u", None, "fix"])
        off = next(ln for ln in preview_clips.transcript_lines(c, segs, ws, {}) if ln["kind"] == "cut")
        self.assertEqual(off["text"], "ఏం కూర చేశావు ? పప్పు చేశాను hop off")


if __name__ == "__main__":
    unittest.main()
