"""lines.build_lines / LineIndex asr_fix option: off is byte-identical to the code before the option existed."""
import json
import math
import unittest
from pathlib import Path

from shotvi.segment.asr_fix import ASR_LEGEND, corrections_path
from shotvi.select.lines import LineIndex, build_lines, render


def build_lines_before_asr_fix(video_id: str) -> list[dict]:
    """Verbatim copy of lines.build_lines before the asr_fix option (2026-10-06): the reference for 'off'."""
    tr = json.loads(Path(f"scribe_dataset/{video_id}/{video_id}_scribe_transcript.json").read_text(encoding="utf-8"))
    rows = [{"start": s["start"], "end": s["end"], "speaker": s.get("speaker", "?"), "text": s["text"],
             "kind": "speech", "words": s.get("words")} for s in tr["sentences"]]
    rows += [{"start": e["start"], "end": e["end"], "speaker": "event", "text": e["text"], "kind": "event"}
             for e in tr.get("audio_events", [])]
    rows.sort(key=lambda r: (r["start"], r["kind"] != "event"))
    for k, r in enumerate(rows):
        r["id"] = f"L{k + 1:04d}"
    return rows


LOW, HIGH = math.log(0.3), math.log(0.95)


def synthetic():
    words = ["ఏం", "కూర", "చేశావు?", "పప్పు", "చేశాను", "hop", "off", "చేశాను."]
    low = {1, 5, 6}
    ws = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4, "speaker": "s0" if i < 3 else "s1",
           "logprob": LOW if i in low else HIGH} for i, w in enumerate(words)]
    sents = [{"id": 0, "text": "ఏం కూర చేశావు?", "start": 0.0, "end": 1.4, "speaker": "s0", "words": [0, 2]},
             {"id": 1, "text": "పప్పు చేశాను hop off చేశాను.", "start": 1.5, "end": 3.9, "speaker": "s1",
              "words": [3, 7]}]
    return {"word_timestamps": ws, "sentences": sents, "audio_events": [{"text": "[నవ్వు]", "start": 4.0, "end": 4.5}]}


LAYER = {"corrections": [{"start_word": 5, "end_word": 6, "heard": "hop off", "corrected": "half off", "reason": "r",
                          "source": "relisten", "confidence": 0.9, "applied": True}], "uncertain": []}


class LinesAsrFix(unittest.TestCase):
    def test_off_equals_on_without_marks_and_text_marked_when_on(self):
        tr = synthetic()
        off = build_lines("v", transcript=tr)
        on = build_lines("v", asr_fix=LAYER, transcript=tr)
        self.assertEqual([r["text"] for r in off], ["ఏం కూర చేశావు?", "పప్పు చేశాను hop off చేశాను.", "[నవ్వు]"])
        self.assertEqual([r["text"] for r in on],
                         ["ఏం కూర(?) చేశావు?", "పప్పు చేశాను {fixed: half off} చేశాను.", "[నవ్వు]"])
        strip = lambda rs: [{k: v for k, v in r.items() if k != "text"} for r in rs]  # noqa: E731
        self.assertEqual(strip(off), strip(on))                     # ids, times, speakers, word ranges unchanged

    def test_line_index_passes_option(self):
        ix = LineIndex(lines=build_lines("v", asr_fix=LAYER, transcript=synthetic()), cuts=[])
        self.assertIn("{fixed: half off}", render(ix.lines))
        self.assertIn("{fixed: ...}", ASR_LEGEND)

    def test_default_is_off(self):
        self.assertFalse(LineIndex.__init__.__defaults__[-1])
        self.assertFalse(build_lines.__defaults__[0])

    def test_real_videos_off_byte_identical(self):
        vids = [v for v in ("j14Om1SaFYE", "QWIvgoTQGKk")
                if Path(f"scribe_dataset/{v}/{v}_scribe_transcript.json").exists()]
        if not vids:
            self.skipTest("sample transcripts not present")
        for v in vids:
            ref = build_lines_before_asr_fix(v)
            self.assertEqual(build_lines(v), ref)
            self.assertEqual(render(build_lines(v, asr_fix=False)), render(ref))
            if corrections_path(v).exists():
                on = build_lines(v, asr_fix=True)
                self.assertEqual([(r["id"], r["start"], r["end"]) for r in on],
                                 [(r["id"], r["start"], r["end"]) for r in ref])
                self.assertTrue(any("{fixed: " in r["text"] for r in on))


if __name__ == "__main__":
    unittest.main()
