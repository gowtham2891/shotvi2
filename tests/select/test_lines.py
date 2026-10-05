"""Line ids -> times for video checks: shot lines inside silent stretches and the cap on imprecise spans."""
import unittest

from shotvi.select.lines import MAX_BEAT_S, LineIndex, render
from shotvi.select.video_check import convert


def sp(i, a, b, text="మాట"):
    return {"id": f"L{i:04d}", "start": a, "end": b, "speaker": "speaker_0", "text": text, "kind": "speech"}


def ev(i, a, b, text="[పాట]"):
    return {"id": f"L{i:04d}", "start": a, "end": b, "speaker": "event", "text": text, "kind": "event"}


# 10-12 s speech, a 40 s song with shots every ~5 s (plus a 0.4 s sliver at 12.4), speech again, then 50 s of
# closing music to the end of the episode.
LINES = [sp(1, 10.0, 12.0), ev(2, 12.1, 52.0), sp(3, 52.0, 54.0), ev(4, 54.0, 104.0)]
CUTS = [12.4, 17.0, 22.0, 27.0, 32.0, 37.0, 42.0, 47.0, 51.6, 60.0, 70.0, 80.0, 90.0, 100.0]


def ix(shots=False):
    return LineIndex(lines=[dict(r) for r in LINES], cuts=CUTS, shot_lines=shots)


class ShotLines(unittest.TestCase):
    def test_off_by_default(self):
        self.assertEqual(len(ix().lines), 4)

    def test_long_silence_listed_shot_by_shot(self):
        shots = [r for r in ix(True).lines if r["kind"] == "shot"]
        song = [r for r in shots if r["end"] <= 52.0]
        self.assertEqual(song[0]["start"], 12.0)          # starts where the speech ends
        self.assertEqual(song[-1]["end"], 52.0)
        self.assertTrue(all(r["end"] - r["start"] >= 1.0 for r in shots))   # the 12.4 sliver merged
        self.assertEqual(len({r["id"] for r in shots}), len(shots))         # ids unique
        self.assertTrue(all(r["text"] == "[పాట]" for r in song))

    def test_render_marks_shots(self):
        out = render(ix(True).lines)
        self.assertIn("(shot, no dialogue): [పాట]", out)

    def test_norm_accepts_both_id_kinds(self):
        x = ix(True)
        self.assertEqual(x.norm("s3"), "S0003")
        self.assertEqual(x.norm("123"), "L0123")


class GapSpan(unittest.TestCase):
    def test_beat_on_exact_shots_is_not_capped(self):
        x = ix(True)
        song = [r for r in x.lines if r["kind"] == "shot" and r["end"] <= 52.0]
        g = x.gap_span(song[1]["id"], song[4]["id"])          # shots 3-4 of the song
        self.assertEqual((g["start"], g["end"]), (song[1]["end"], song[4]["start"]))
        self.assertNotIn("capped", g)
        one = x.gap_span(song[2]["id"], song[2]["id"])         # a beat that is one shot
        self.assertEqual((one["start"], one["end"]), (song[2]["start"], song[2]["end"]))

    def test_long_beat_without_shot_lines_is_capped(self):
        g = ix().gap_span("L0001", "L0003")                    # 40 s song between two speech lines
        self.assertTrue(g["capped"])
        self.assertLessEqual(g["end"] - g["start"], MAX_BEAT_S + 1.5)
        self.assertEqual(g["full"], [12.0, 52.0])

    def test_hold_after_long_closing_music_is_capped(self):
        g = ix(True).gap_span("L0004", None)                   # "after the closing music": was 53 s
        self.assertTrue(g["capped"])
        self.assertLessEqual(g["end"], 54.0 + MAX_BEAT_S)

    def test_dialogue_lines_around_a_song_capped_even_with_shot_lines(self):
        g = ix(True).gap_span("L0001", "L0003")                # model ignored the shot lines
        self.assertTrue(g["capped"])

    def test_beat_spanning_dialogue_is_invalid(self):
        x = LineIndex(lines=[sp(i, 10.0 * i, 10.0 * i + 2) for i in range(1, 8)], cuts=CUTS)
        self.assertIn("invalid", x.gap_span("L0001", "L0007"))
        c = convert(x, {"start": {"at": {"line": "L0001"}},
                        "end": {"at": {"line": "", "after_line": "L0001", "before_line": "L0007"}},
                        "visual_beats_to_include": []})
        self.assertIn("invalid", c["end"])

    def test_beat_into_long_event_is_capped(self):
        g = ix().gap_span("L0001", "L0002")                    # before the song: reached 15 s into it
        self.assertTrue(g["capped"])

    def test_short_beat_unchanged(self):
        x = LineIndex(lines=[sp(1, 10.0, 12.0), sp(2, 16.0, 18.0)], cuts=CUTS)
        g = x.gap_span("L0001", "L0002")
        self.assertNotIn("capped", g)
        self.assertEqual(g["raw"], [12.0, 16.0])


class Convert(unittest.TestCase):
    def obj(self, start, end, beats=()):
        return {"start": {"at": start}, "end": {"at": end}, "visual_beats_to_include": list(beats)}

    def test_end_on_long_event_line_is_capped(self):
        c = convert(ix(), self.obj({"line": "L0001"}, {"line": "L0004"}))
        self.assertEqual(c["start"]["time"], 10.0)
        self.assertLessEqual(c["end"]["time"], 54.0 + MAX_BEAT_S)
        self.assertTrue(c["end"]["gap"]["capped"])

    def test_end_on_speech_line_is_its_end(self):
        c = convert(ix(), self.obj({"line": "L0001"}, {"line": "L0003"}))
        self.assertEqual(c["end"], {"time": 54.0})

    def test_end_between_lines(self):
        c = convert(ix(), self.obj({"line": "L0001"}, {"line": "", "after_line": "L0003", "before_line": "L0004"}))
        self.assertIn("gap", c["end"])


if __name__ == "__main__":
    unittest.main()
