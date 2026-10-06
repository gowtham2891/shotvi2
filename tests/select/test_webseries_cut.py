"""Web-series cut builder (shotvi/select/webseries.py): offline, synthetic lines, words and shot cuts."""
import unittest

from eval.schema_check import check_clip_core
from shotvi.select.webseries import (DEFAULTS, Episode, Reject, build_clip, core_fields, end_hold, play_text)

CFG = DEFAULTS["cut"]


def make_ep(spec, cuts, events=(), duration=200.0, loud=None):
    """spec: [(start, end, text)] speech lines (words spread evenly); events: [(start, end, text)]."""
    words, lines = [], []
    for a, b, text in spec:
        toks = text.split()
        step = (b - a) / len(toks)
        i0 = len(words)
        for j, t in enumerate(toks):
            words.append({"word": t, "start": round(a + j * step, 3), "end": round(a + (j + 1) * step - 0.02, 3),
                          "speaker": "speaker_0"})
        lines.append({"start": a, "end": words[-1]["end"], "speaker": "speaker_0", "text": text, "kind": "speech",
                      "words": [i0, len(words) - 1]})
    for a, b, text in events:
        lines.append({"start": a, "end": b, "speaker": "event", "text": text, "kind": "event"})
    lines.sort(key=lambda r: (r["start"], r["kind"] != "event"))
    for k, r in enumerate(lines):
        r["id"] = f"L{k + 1:04d}"
    return Episode("vid", lines, words, list(cuts), duration, loud)


SCENE = [(10.0, 12.0, "a b c d"),            # L0001 start (setup)
         (12.5, 14.0, "e f g"),              # L0002
         (15.0, 17.0, "punch line here now"),  # L0003 lift candidate
         (18.0, 20.0, "h i j k"),            # L0004
         (21.0, 23.0, "side talk one two"),  # L0005
         (24.0, 26.0, "side talk three four"),  # L0006
         (27.0, 29.0, "l m n o"),            # L0007
         (30.0, 32.0, "payoff line end ok"),  # L0008 end
         (45.0, 47.0, "next scene line")]     # L0009
CUTS = [9.9, 14.6, 17.5, 20.6, 26.6, 33.5, 35.0, 44.0]


def moment(**kw):
    m = {"purpose": "standalone", "start_line": "L0001", "end_line": "L0008", "cold_open_lines": [], "drops": []}
    m.update(kw)
    return m


def words_played(ep, clip):
    return [i for p in clip["pieces"] for i in range(p["start_word"], p["end_word"] + 1)]


class Start(unittest.TestCase):
    def test_start_snaps_to_cut_inside_pad(self):
        c = build_clip(make_ep(SCENE, CUTS), moment(), CFG)
        self.assertEqual(c["time_segments"][0]["start"], 9.9)

    def test_start_pad_without_cut_is_at_most_0_3(self):
        c = build_clip(make_ep(SCENE, [c for c in CUTS if c != 9.9]), moment(), CFG)
        self.assertAlmostEqual(c["time_segments"][0]["start"], 9.7, places=3)

    def test_start_never_reaches_into_previous_word(self):
        spec = [(5.0, 9.9, "x y z")] + SCENE      # previous word ends 9.88, line starts 10.0
        ep = make_ep(spec, [])
        c = build_clip(ep, moment(start_line="L0002", end_line="L0009"), CFG)
        self.assertGreaterEqual(c["time_segments"][0]["start"], 9.88)
        self.assertLessEqual(c["time_segments"][0]["start"], 10.0)

    def test_event_start_line_moves_to_first_speech(self):
        ep = make_ep(SCENE, CUTS, events=[(8.0, 9.95, "[పాట]")])
        c = build_clip(ep, moment(start_line="L0001", end_line="L0009"), CFG)   # L0001 is now the song
        self.assertEqual(c["start_line"], "L0002")
        self.assertEqual(c["time_segments"][0]["start"], 9.9)


class ColdOpen(unittest.TestCase):
    def setUp(self):
        self.ep = make_ep(SCENE, CUTS)
        self.c = build_clip(self.ep, moment(cold_open_lines=["L0003"]), CFG)

    def test_lift_first_and_on_shot_cut(self):
        lo = self.c["cold_open"]
        self.assertIsNotNone(lo, self.c["lift_rejected"])
        seg = self.c["time_segments"][0]
        self.assertEqual(seg["kind"], "cold_open")
        self.assertEqual(seg["start"], 14.6)                 # shot cut just before the line
        self.assertAlmostEqual(seg["end"], 17.495, places=3)  # 0.005 s before the cut after the line
        self.assertTrue(1.0 <= lo["duration"] <= 3.6)

    def test_no_replay(self):
        lift = self.c["time_segments"][0]
        for s in self.c["time_segments"][1:]:
            self.assertTrue(s["end"] <= lift["start"] + 1e-6 or s["start"] >= lift["end"] - 1e-6,
                            f"scene segment {s} replays the lift {lift}")
        played = words_played(self.ep, self.c)
        self.assertEqual(len(played), len(set(played)), "a word is played twice")
        i0, i1 = self.ep.by_id["L0003"]["words"]
        self.assertEqual(self.c["pieces"][0]["start_word"], i0)
        self.assertEqual(self.c["pieces"][0]["end_word"], i1)

    def test_skip_in_place_is_a_whole_line_cut(self):
        cu = [x for x in self.c["cuts"] if x["lift_skip"]]
        self.assertEqual(len(cu), 1)
        self.assertEqual(cu[0]["removed_lines"], ["L0003"])
        self.assertEqual((cu[0]["after_line"], cu[0]["before_line"]), ("L0002", "L0004"))

    def test_schema_core_valid(self):
        clip = {"id": "x", "pieces": self.c["pieces"], **core_fields(self.ep, self.c["pieces"], 30.0)}
        errs = []
        check_clip_core("x", clip, self.ep.words, errs, 30.0)
        self.assertEqual(errs, [])
        self.assertTrue(clip["reordered"])

    def test_play_text_marks_the_skip(self):
        t = play_text(self.ep, "w01", moment(), self.c)
        self.assertIn("[COLD OPEN", t)
        self.assertIn("skipped here, not replayed", t)
        self.assertEqual(t.count("punch line here now"), 1)


class ColdOpenRejected(unittest.TestCase):
    def check_rejected(self, ep, m, why):
        c = build_clip(ep, m, CFG)
        self.assertIsNone(c["cold_open"])
        self.assertIn(why, c["lift_rejected"])
        self.assertEqual(c["time_segments"][0]["kind"], "scene")
        i0, _ = ep.by_id[m["cold_open_lines"][0]]["words"]
        self.assertIn(i0, words_played(ep, c))          # the line plays in place instead
        return c

    def test_no_shot_cut_after_line(self):
        self.check_rejected(make_ep(SCENE, [c for c in CUTS if c != 17.5]), moment(cold_open_lines=["L0003"]),
                            "no shot cut")

    def test_too_long(self):
        spec = list(SCENE)
        spec[2] = (14.5, 17.3, "a very long line that goes on and on and on")
        spec[1] = (12.5, 14.0, "e f g")
        ep = make_ep(spec, [9.9, 14.2, 17.4, 35.0])
        c = build_clip(ep, moment(cold_open_lines=["L0003"]), {**CFG, "lift_max_s": 2.5})
        self.assertIsNone(c["cold_open"])

    def test_question_lift_rejected(self):
        spec = list(SCENE)
        spec[2] = (15.0, 17.0, "why did you do it?")
        self.check_rejected(make_ep(spec, CUTS), moment(cold_open_lines=["L0003"]), "question")

    def test_answer_after_question_rejected(self):
        spec = list(SCENE)
        spec[1] = (12.5, 14.0, "who was it?")
        self.check_rejected(make_ep(spec, CUTS), moment(cold_open_lines=["L0003"]), "question")

    def test_lift_of_end_line_rejected(self):
        self.check_rejected(make_ep(SCENE, CUTS), moment(cold_open_lines=["L0008"]), "payoff")

    def test_lift_of_start_line_rejected(self):
        self.check_rejected(make_ep(SCENE, CUTS), moment(cold_open_lines=["L0001"]), "start line")

    def test_comma_end_rejected(self):
        spec = list(SCENE)
        spec[2] = (15.0, 17.0, "if you go there,")
        self.check_rejected(make_ep(spec, CUTS), moment(cold_open_lines=["L0003"]), "mid-sentence")

    def test_mid_laugh_rejected(self):
        ep = make_ep(SCENE, CUTS, events=[(16.9, 18.5, "[నవ్వు]")])
        lid = next(r["id"] for r in ep.lines if r["text"] == "punch line here now")
        self.check_rejected(ep, moment(cold_open_lines=[lid], end_line=ep.lines[-2]["id"]), "mid-laugh")

    def test_quiet_tail_rejected(self):
        def loud(a, b):
            return -40.0 if a >= 16.9 else -18.0      # the tail after the last word is near-silent
        ep = make_ep(SCENE, CUTS, loud=loud)
        self.check_rejected(ep, moment(cold_open_lines=["L0003"]), "quiet tail")

    def test_loud_tail_kept(self):
        ep = make_ep(SCENE, CUTS, loud=lambda a, b: -20.0)
        c = build_clip(ep, moment(cold_open_lines=["L0003"]), CFG)
        self.assertIsNotNone(c["cold_open"])

    def test_no_lift_option(self):
        c = build_clip(make_ep(SCENE, CUTS), moment(cold_open_lines=["L0003"]), CFG, no_lift=True)
        self.assertIsNone(c["cold_open"])
        self.assertIsNone(c["lift_rejected"])


class Drops(unittest.TestCase):
    def test_whole_chunk_drop_snapped_to_cuts(self):
        ep = make_ep(SCENE, CUTS)
        c = build_clip(ep, moment(drops=[{"first_line": "L0005", "last_line": "L0006", "why": "side talk"}]), CFG)
        segs = c["time_segments"]
        self.assertEqual(len(segs), 2)
        self.assertAlmostEqual(segs[0]["end"], 20.595, places=3)   # first cut after L0004
        self.assertEqual(segs[1]["start"], 26.6)                   # last cut before L0007
        played = set(words_played(ep, c))
        for lid in ("L0005", "L0006"):
            i0, i1 = ep.by_id[lid]["words"]
            self.assertFalse(played & set(range(i0, i1 + 1)))
        self.assertEqual(c["cuts"][0]["removed_lines"], ["L0005", "L0006"])

    def test_drop_edges_never_cut_a_kept_word(self):
        ep = make_ep(SCENE, [9.9, 19.5, 27.5, 35.0])    # cuts inside kept lines must not be used
        c = build_clip(ep, moment(drops=[{"first_line": "L0005", "last_line": "L0006", "why": ""}]), CFG)
        segs = c["time_segments"]
        self.assertGreaterEqual(segs[0]["end"], ep.w1(ep.by_id["L0004"]))
        self.assertLessEqual(segs[1]["start"], ep.w0(ep.by_id["L0007"]))

    def test_at_most_three_cuts(self):
        spec = [(10.0 + 3 * k, 12.0 + 3 * k, f"w{k} x y") for k in range(12)]
        ep = make_ep(spec, [])
        drops = [{"first_line": f"L{k:04d}", "last_line": f"L{k:04d}", "why": ""} for k in (2, 4, 6, 8)]
        c = build_clip(ep, moment(end_line="L0012", drops=drops), CFG)
        self.assertEqual(len(c["time_segments"]), 4)
        self.assertTrue(any("internal cuts" in w for w in c["warnings"]))

    def test_lift_skip_counts_as_a_cut(self):
        spec = [(10.0 + 3 * k, 12.0 + 3 * k, f"w{k} x y") for k in range(12)]
        cuts = [12.5 + 3 * k for k in range(12)]
        ep = make_ep(spec, cuts)
        drops = [{"first_line": f"L{k:04d}", "last_line": f"L{k:04d}", "why": ""} for k in (2, 4, 6)]
        c = build_clip(ep, moment(end_line="L0012", drops=drops, cold_open_lines=["L0010"]), CFG)
        self.assertIsNotNone(c["cold_open"], c["lift_rejected"])
        self.assertLessEqual(len(c["cuts"]), 3)

    def test_long_drop_is_ignored(self):
        spec = SCENE[:4] + [(25.0 + 10 * k, 27.0 + 10 * k, f"long side {k}") for k in range(6)] + \
            [(90.0, 92.0, "payoff now")]
        ep = make_ep(spec, [])
        c = build_clip(ep, moment(end_line="L0011", drops=[{"first_line": "L0005", "last_line": "L0010",
                                                            "why": ""}]), {**CFG, "max_drop_s": 45.0})
        self.assertEqual(len(c["time_segments"]), 1)
        self.assertTrue(any("scene stitch" in w for w in c["warnings"]))

    def test_drop_touching_start_or_end_ignored(self):
        ep = make_ep(SCENE, CUTS)
        c = build_clip(ep, moment(drops=[{"first_line": "L0001", "last_line": "L0002", "why": ""}]), CFG)
        self.assertEqual(len(c["time_segments"]), 1)
        self.assertEqual(c["start_line"], "L0001")

    def test_event_only_drop_removes_the_silent_gap(self):
        spec = [(10.0, 12.0, "a b c"), (13.0, 15.0, "d e f"), (30.0, 32.0, "g h i"), (33.0, 35.0, "j k l")]
        ep = make_ep(spec, [9.9, 15.5, 22.0, 29.5, 38.0], events=[(15.2, 29.8, "[పాట]")])
        song = next(r["id"] for r in ep.lines if r["kind"] == "event")
        c = build_clip(ep, moment(end_line=ep.lines[-1]["id"],
                                  drops=[{"first_line": song, "last_line": song, "why": "song bridge"}]), CFG)
        segs = c["time_segments"]
        self.assertEqual(len(segs), 2)
        self.assertAlmostEqual(segs[0]["end"], 15.495, places=3)
        self.assertEqual(segs[1]["start"], 29.5)


class EndHold(unittest.TestCase):
    def test_hold_to_next_cut_after_min(self):
        ep = make_ep(SCENE, CUTS)
        h = end_hold(ep, ep.by_id["L0008"]["words"][1], CFG)
        self.assertAlmostEqual(h["end"], 34.995, places=3)
        self.assertTrue(h["on_shot_cut"])
        self.assertTrue(CFG["hold_min_s"] <= h["hold_s"] <= CFG["hold_max_s"])

    def test_hold_capped_without_cut(self):
        ep = make_ep(SCENE, [9.9, 60.0])
        h = end_hold(ep, ep.by_id["L0008"]["words"][1], CFG)
        self.assertFalse(h["on_shot_cut"])
        self.assertAlmostEqual(h["hold_s"], CFG["hold_default_s"], places=2)

    def test_hold_never_reaches_next_speech(self):
        spec = SCENE[:8] + [(32.8, 34.0, "stray next line")]
        ep = make_ep(spec, [9.9, 36.0])
        h = end_hold(ep, ep.by_id["L0008"]["words"][1], CFG)
        self.assertLessEqual(h["end"], 32.8 - CFG["next_speech_margin_s"] + 1e-6)

    def test_hold_short_before_long_silence(self):
        spec = SCENE[:8]                    # nothing after the payoff: credits / end of episode
        ep = make_ep(spec, [9.9, 38.0, 39.0], duration=120.0)
        h = end_hold(ep, ep.by_id["L0008"]["words"][1], CFG)
        self.assertLessEqual(h["hold_s"], CFG["long_silence_hold_max_s"] + 1e-6)
        self.assertTrue(h["long_silence_after"])

    def test_hold_through_laugh(self):
        ep = make_ep(SCENE, [9.9, 33.0, 34.5, 36.0, 44.0], events=[(32.1, 35.0, "[నవ్వు]")])
        pay = next(r for r in ep.lines if r["text"] == "payoff line end ok")
        h = end_hold(ep, pay["words"][1], CFG)
        self.assertGreaterEqual(h["end"], 35.0)
        self.assertTrue(h["laugh_after"])

    def test_hold_max_cap(self):
        ep = make_ep(SCENE, [9.9, 44.0])     # next cut 12 s after the payoff
        h = end_hold(ep, ep.by_id["L0008"]["words"][1], CFG)
        self.assertLessEqual(h["hold_s"], CFG["hold_max_s"])


class SegmentOrder(unittest.TestCase):
    def test_play_order_and_pieces(self):
        ep = make_ep(SCENE, CUTS)
        c = build_clip(ep, moment(cold_open_lines=["L0003"],
                                  drops=[{"first_line": "L0005", "last_line": "L0006", "why": ""}]), CFG)
        segs = c["time_segments"]
        self.assertEqual([s["kind"] for s in segs], ["cold_open", "scene", "scene", "scene"])
        scene = segs[1:]
        for a, b in zip(scene, scene[1:]):
            self.assertLessEqual(a["end"], b["start"])
        self.assertAlmostEqual(c["video_duration"], sum(s["end"] - s["start"] for s in segs), places=2)
        # each piece lies inside its segment, in play order
        k = 0
        for p in c["pieces"]:
            while not (segs[k]["start"] - 0.01 <= p["start"] and p["end"] <= segs[k]["end"] + 0.01):
                k += 1
                self.assertLess(k, len(segs), f"piece {p} outside the segments")
        self.assertEqual(len(c["cuts"]), 2)

    def test_unknown_ids_reject(self):
        with self.assertRaises(Reject):
            build_clip(make_ep(SCENE, CUTS), moment(start_line="L0999"), CFG)
        with self.assertRaises(Reject):
            build_clip(make_ep(SCENE, CUTS), moment(start_line="L0005", end_line="L0002"), CFG)


if __name__ == "__main__":
    unittest.main()
