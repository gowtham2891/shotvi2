"""Stage 3 (candidate generation, pure code) on a synthetic beat sheet: boundary snapping, trims, beat protection,
non-story handling, caps, dedupe, schema validation."""
import copy
import unittest

from selection_v2 import stage1_facts as S, stage3_candidates as S3
from selection_v2.config import load_cast_lists, load_config
from tests.v2.synthetic import _w, make_inputs

CFG = copy.deepcopy(load_config()); CFG["stage1a"]["context_window_s"] = 8
CFG["stage3"]["cold_open_min_before_payoff_s"] = 10      # the synthetic scenes are short
CAST = load_cast_lists()
KEYS = {"speaker_0": ("A", "Surya", 0.9), "speaker_1": ("B", "Mother", 0.9), "speaker_2": ("C", "Swami", 0.9),
        "speaker_3": ("D", "Siva", 0.9), "speaker_4": ("E", "TV", 0.6)}


def transcript():
    """Scene X 1-36 s (comedy, a 12 s music stretch, a 3 s hold, a laugh after the payoff); a long silence; scene Y
    44-60 s (comedy_to_emotional, three payoffs, one non-story device line in the middle)."""
    words, sents = [], []

    def line(text, start, end, speaker):
        span = _w(words, text, start, end, speaker)
        sents.append({"id": len(sents), "text": text, "start": start, "end": end, "speaker": speaker, "words": span})
    line("అమ్మా లేమ్మా", 1.0, 2.0, "speaker_0")                 # X1 entry
    line("ఏంటే అర్ధరాత్రి?", 2.1, 3.6, "speaker_1")            # X2 setup (gap before it only 0.1 s)
    line("ఒక చిన్న విషయం", 4.0, 5.5, "speaker_0")              # X3 skippable (jump cut)
    line("నాకు job వచ్చింది", 6.0, 8.0, "speaker_0")           # X4 turn; then a 12 s music stretch
    line("నిజంగా?", 20.0, 21.0, "speaker_1")                   # X5 escalation (also listed skippable: must never be cut)
    line("అవును అమ్మా", 21.3, 22.5, "speaker_0")               # X6 skippable (clean)
    line("చాలా happy గా ఉంది", 23.0, 25.0, "speaker_1")        # X7; then a 3 s reaction hold
    line("five star rating ఇవ్వు", 28.0, 30.0, "speaker_0")   # X8 setup_for_payoff
    line("నువ్వు ఇవ్వాలిరా నాకు", 30.5, 32.0, "speaker_1")     # X9 payoff; laugh 32.2-34.0
    line("thank you", 34.2, 35.0, "speaker_0")                 # X10 reaction beat
    line("bye", 35.5, 36.0, "speaker_1")                       # X11 scene end
    line("స్వామి ఏం చేస్తున్నావ్", 44.0, 46.0, "speaker_3")     # Y1 entry
    line("కూర్చున్నా", 46.3, 47.0, "speaker_2")                 # Y2
    line("job అంటే పండగ", 47.5, 49.5, "speaker_2")             # Y3 payoff 1
    line("హహ", 50.2, 50.6, "speaker_3")                        # Y4; laugh 50.7-51.5
    line("ఏం ముందుచూపు రా", 52.0, 53.5, "speaker_3")           # Y5 setup
    line("చూస్తూ ఉండు", 54.0, 55.0, "speaker_2")               # Y6 payoff 2
    line("వార్తలు ఇప్పుడు", 56.0, 58.0, "speaker_4")           # Y7 NON-STORY (TV)
    line("పద", 58.5, 59.0, "speaker_3")                        # Y8 setup
    line("సరే", 59.3, 60.0, "speaker_2")                       # Y9 payoff 3
    events = [{"text": "[పాట]", "start": 8.2, "end": 19.8}, {"text": "[నవ్వు]", "start": 32.2, "end": 34.0},
              {"text": "[నిశబ్దం]", "start": 37.0, "end": 43.0}, {"text": "[నవ్వు]", "start": 50.7, "end": 51.5}]
    return {"sentences": sents, "word_timestamps": words, "audio_events": events, "audio_duration_secs": 62.0,
            "speakers": sorted({w["speaker"] for w in words})}


def facts_with_sheets(cfg=None):
    cfg = cfg or CFG
    f = S.run(make_inputs(cuts=(11.2, 40.0), duration=62.0, transcript=transcript(), keys=KEYS), None, cfg, CAST)
    by_text = {r["text"]: r["id"] for r in f["lines"] if r["kind"] == "speech"}
    L = lambda t: by_text[t]
    x = {"X1": L("అమ్మా లేమ్మా"), "X2": L("ఏంటే అర్ధరాత్రి?"), "X3": L("ఒక చిన్న విషయం"), "X4": L("నాకు job వచ్చింది"), "X5": L("నిజంగా?"),
         "X6": L("అవును అమ్మా"), "X7": L("చాలా happy గా ఉంది"), "X8": L("five star rating ఇవ్వు"), "X9": L("నువ్వు ఇవ్వాలిరా నాకు"),
         "X10": L("thank you"), "X11": L("bye"), "Y1": L("స్వామి ఏం చేస్తున్నావ్"), "Y2": L("కూర్చున్నా"), "Y3": L("job అంటే పండగ"),
         "Y4": L("హహ"), "Y5": L("ఏం ముందుచూపు రా"), "Y6": L("చూస్తూ ఉండు"), "Y7": L("వార్తలు ఇప్పుడు"), "Y8": L("పద"), "Y9": L("సరే")}
    sheet_x = {"scene_id": 1, "start": 1.0, "end": 36.0, "start_str": "0:01.0", "end_str": "0:36.0", "label": "home", "register": "comedy",
               "self_contained": True, "setup": "s",
               "beats": [{"line": x["X1"], "type": "entry"}, {"line": x["X2"], "type": "setup"}, {"line": x["X4"], "type": "turn"},
                         {"line": x["X5"], "type": "escalation"}, {"line": x["X8"], "type": "setup_for_payoff"}, {"line": x["X9"], "type": "payoff"},
                         {"line": x["X10"], "type": "reaction"}],
               "payoff": {"line": x["X9"], "note": "n", "more_than_one": False},
               "reaction": {"type": "laugh", "after_line": x["X9"], "tag_line": "", "by": "A", "note": "laugh"},
               "context_required": [], "callbacks": ["earlier scene"], "dynamic": {"characters": ["A", "B"], "relationship": "mother and son", "reveals": "r"},
               "clip_hints": {"natural_open": [x["X1"]], "natural_close": [x["X10"]]},
               "non_dialogue": [{"after_line": x["X4"], "before_line": x["X5"], "kind": "action_beat", "bgm": "song", "recommend": "shorten_to", "target_s": 3.0,
                                 "reason": "montage", "start": 8.0, "end": 20.0, "dur_s": 12.0, "range": "0:08.0-0:20.0"},
                                {"after_line": x["X7"], "before_line": x["X8"], "kind": "reaction_hold", "bgm": "none", "recommend": "keep",
                                 "reason": "stare", "start": 25.0, "end": 28.0, "dur_s": 3.0, "range": "0:25.0-0:28.0"}],
               "skippable_lines": [{"line": x["X3"], "reason": "filler"},
                                   {"line": x["X6"], "reason": "filler"},
                                   {"line": x["X5"], "reason": "a beat listed by mistake"}],
               "speakers_seen": [], "non_story_confirm": [], "other_device_audio": [], "confidence": 0.9, "needs_video_default_res": False,
               "thin_diarization": False, "hook_moments": [{"line": x["X9"], "why": "the demand is left hanging"}]}
    sheet_y = {"scene_id": 2, "start": 44.0, "end": 60.0, "start_str": "0:44.0", "end_str": "1:00.0", "label": "shop", "register": "comedy_to_emotional",
               "self_contained": True, "setup": "s",
               "beats": [{"line": x["Y1"], "type": "entry"}, {"line": x["Y3"], "type": "payoff"}, {"line": x["Y5"], "type": "setup"},
                         {"line": x["Y6"], "type": "payoff"}, {"line": x["Y8"], "type": "setup"}, {"line": x["Y9"], "type": "payoff"}],
               "payoff": {"line": x["Y6"], "note": "n", "more_than_one": True},
               "reaction": {"type": "laugh", "after_line": x["Y4"], "tag_line": "", "by": "D", "note": "laugh"},
               "context_required": ["c"], "callbacks": [], "dynamic": {"characters": ["C", "D"], "relationship": "friends", "reveals": "r"},
               "clip_hints": {"natural_open": [x["Y1"]], "natural_close": [x["Y9"]]},
               "non_dialogue": [], "skippable_lines": [], "speakers_seen": [], "non_story_confirm": [], "other_device_audio": [],
               "confidence": 0.9, "needs_video_default_res": False, "thin_diarization": False,
               "hook_moments": [{"line": x["Y3"], "why": "open"}, {"line": x["Y6"], "why": "open"}]}
    f["beat_sheets"] = [sheet_x, sheet_y]
    f["speaker_map"] = {"non_story_confirmed": [x["Y7"]], "corrections": [], "line_names": {}}
    f["stages"]["2"] = {"version": "stage2/0.3", "generated_at": "t", "duration_s": 0, "usage": {"input_tokens": 0, "output_tokens": 0, "cost_inr": 0}}
    f["episode_pass"] = {"derived": {"scenes": [{"id": 1, "start": 1.0, "end": 36.0, "start_str": "0:01.0", "end_str": "0:36.0", "first_line": x["X1"], "last_line": x["X11"]},
                                                {"id": 2, "start": 44.0, "end": 60.0, "start_str": "0:44.0", "end_str": "1:00.0", "first_line": x["Y1"], "last_line": x["Y9"]}],
                                     "non_story_lines": []}, "speakers": []}
    return f, x


def run(cfg=None):
    f, x = facts_with_sheets(cfg)
    out = S3.run(make_inputs(transcript=transcript(), keys=KEYS), f, cfg or CFG)
    return out, x


def pick(out, scene, ctype, variant):
    return [c for c in out["candidates"] if c["scene_id"] == scene and c["type"] == ctype and c["variant"] == variant]


class TestBoundaries(unittest.TestCase):
    def test_full_arc_start_pad_and_end_after_laugh(self):
        f, x = facts_with_sheets()
        f["beat_sheets"][0]["button_line"] = None                          # a new sheet that names no button: payoff + laugh is the end
        out = S3.run(make_inputs(transcript=transcript(), keys=KEYS), f, CFG)
        c = pick(out, 1, "comedy", "full_arc")[0]
        self.assertAlmostEqual(c["start"], 0.7, places=2)                   # 0.3 s before the first word
        self.assertAlmostEqual(c["end"], 34.2, places=2)                    # the whole laugh, before the next line
        self.assertEqual(c["trim_level"], "none")
        self.assertEqual(c["segments"], [[0.7, 34.2]])
        self.assertTrue(c["end_note"].startswith("laugh/hold"))
        self.assertEqual(c["reaction"], "0:32.0")
        self.assertNotIn("button_line", c)

    def test_button_rule_for_sheets_without_the_key(self):
        """founder, 2026-10-29: a line by ANOTHER speaker right after the payoff's laugh (within 1.5 s, <= 4 s long) is the
        button: the clip ends after it (then the usual pause / tail rule). The loop still ends on the punchline."""
        out, x = run()
        c = pick(out, 1, "comedy", "full_arc")[0]
        self.assertEqual(c["button_line"], x["X10"]); self.assertEqual(c["anchor_line"], x["X10"])
        self.assertAlmostEqual(c["end"], 35.5, places=2)                    # 'thank you' 34.2-35.0 + tail, capped by the next line
        self.assertIn("button", c["end_note"])
        f, x = facts_with_sheets()
        f["beat_sheets"][0]["button_line"] = x["X11"]                      # the sheet's own button wins
        c2 = pick(S3.run(make_inputs(transcript=transcript(), keys=KEYS), f, CFG), 1, "comedy", "full_arc")[0]
        self.assertEqual(c2["button_line"], x["X11"]); self.assertEqual(c2["button_why"], "sheet button_line")
        ctx = S3.Ctx(f, CFG)
        sheet = dict(f["beat_sheets"][0]); sheet.pop("button_line")
        self.assertEqual(S3.button_line(ctx, sheet, x["X9"], x["X9"], None)[0], x["X10"])
        cfg = copy.deepcopy(CFG); cfg["stage3"]["end"]["button"]["max_len_s"] = 0.5          # 'thank you' lasts 0.8 s: too long
        self.assertIsNone(S3.button_line(S3.Ctx(f, cfg), sheet, x["X9"], x["X9"], None)[0])
        cfg = copy.deepcopy(CFG); cfg["stage3"]["end"]["button"]["within_s"] = 0.1           # starts 0.2 s after the laugh ends
        self.assertIsNone(S3.button_line(S3.Ctx(f, cfg), sheet, x["X9"], x["X9"], None)[0])
        g = copy.deepcopy(f); g["speaker_map"]["line_names"] = {x["X10"]: "Mother"}       # same speaker as the payoff: no button
        self.assertIsNone(S3.button_line(S3.Ctx(g, CFG), sheet, x["X9"], x["X9"], None)[0])

    def test_start_walks_back_over_a_tiny_pause(self):
        f, x = facts_with_sheets()
        ctx = S3.Ctx(f, CFG)
        start, extra, info = S3.snap_start(ctx, x["X2"], f["beat_sheets"][0])
        self.assertEqual(extra, [x["X1"]])                                    # 0.1 s before X2 is under near_gap_s (0.15): X1 pulled in
        self.assertAlmostEqual(start, 0.7, places=2)
        self.assertEqual(info["rule"], "scene edge")

    def test_start_prefers_a_near_small_pause_over_a_far_big_one(self):
        f, x = facts_with_sheets()
        cfg = copy.deepcopy(CFG); cfg["stage3"]["start"]["near_gap_s"] = 0.1       # the 0.1 s pause before X2 now qualifies
        ctx = S3.Ctx(f, cfg)
        start, extra, info = S3.snap_start(ctx, x["X2"], f["beat_sheets"][0])
        self.assertEqual(extra, [])
        self.assertAlmostEqual(start, 2.0, places=2)                           # inside the 2.0-2.1 pause, clipped to its start
        self.assertEqual(info["rule"], "near pause")
        cfg["stage3"]["start"]["near_gap_s"] = 0.5                             # the 0.3 s pause before X6 is too small ...
        ctx = S3.Ctx(f, cfg)
        start, extra, info = S3.snap_start(ctx, x["X6"], f["beat_sheets"][0])
        self.assertEqual(info["rule"], "near pause"); self.assertEqual(extra, [x["X5"]])   # ... the 12 s pause 1.3 s earlier is within the window
        self.assertAlmostEqual(start, 19.7, places=2)
        cfg["stage3"]["start"]["near_window_s"] = 0.0                          # no window: only the walk-back to >= 0.3 s remains
        ctx = S3.Ctx(f, cfg)
        start, extra, info = S3.snap_start(ctx, x["X6"], f["beat_sheets"][0])
        self.assertEqual(info["rule"], "walk back"); self.assertEqual(extra, [])
        self.assertAlmostEqual(start, 21.0, places=2)

    def test_eligible_for_final_flag(self):
        cfg = copy.deepcopy(CFG); cfg["stage3"]["platform_max_s"] = 20
        out, x = run(cfg)
        full = pick(out, 1, "comedy", "full_arc")[0]
        self.assertFalse(full["eligible_for_final"]); self.assertTrue(full["flags"]["over_platform_max"])
        self.assertTrue(all(c["eligible_for_final"] for c in out["candidates"] if not c["flags"]["over_platform_max"]))

    def test_end_extends_to_the_first_real_pause(self):
        f, x = facts_with_sheets()
        ctx = S3.Ctx(f, CFG)
        end, extra, info = S3.snap_end(ctx, x["X5"], f["beat_sheets"][0])
        self.assertEqual(extra, [x["X6"]])                                    # 0.3 s after X5 is too short; 0.5 s after X6 qualifies
        self.assertAlmostEqual(end, 23.0, places=2)                           # 22.5 + 0.8 tail, capped by the next line

    def test_open_question_end_variant_and_loop(self):
        cfg = copy.deepcopy(CFG); cfg["stage3"]["caps"]["per_scene"] = 10     # no cap
        out, x = run(cfg)
        self.assertFalse([c for c in out["candidates"] if c["type"] == "hook"])   # founder, 2026-10-29: no hook templates
        oq = pick(out, 1, "comedy", "open_question")[0]
        self.assertAlmostEqual(oq["start"], 0.7, places=2)                    # the moment's natural start
        self.assertAlmostEqual(oq["end"], 32.3, places=2)                     # ends on the open-question line: 0.3 s tail, laugh excluded
        self.assertEqual(oq["anchor_line"], x["X9"]); self.assertEqual(oq["payoff_line"], x["X9"])
        tw = pick(out, 1, "comedy", "open_question_trimmed")[0]
        self.assertEqual(tw["untrimmed_sibling"], oq["id"]); self.assertLess(tw["final_duration"], oq["final_duration"])
        lp = pick(out, 1, "loop", "one")[0]
        self.assertAlmostEqual(lp["start"], 27.7, places=2)
        self.assertAlmostEqual(lp["end"], 34.2, places=2)                     # loop ends right after the punchline, laugh included
        self.assertEqual(lp["target_s"], [7.0, 15.0])

    def test_tight_and_cold_open(self):
        out, x = run()
        t = pick(out, 1, "comedy", "tight")[0]
        self.assertAlmostEqual(t["start"], 5.7, places=2)                     # the turn beat
        co = pick(out, 1, "comedy", "cold_open")[0]
        self.assertAlmostEqual(co["start"], 19.7, places=2)                   # last escalation >= 10 s before the payoff, inside the music pause
        self.assertEqual(co["end"], t["end"])


class TestTrims(unittest.TestCase):
    def test_stretch_shortened_lines_skipped_hold_and_beats_untouched(self):
        out, x = run()
        c = pick(out, 1, "comedy", "full_arc_trimmed")[0]
        sh = c["removed"]["gaps_shortened"]
        self.assertEqual(len(sh), 1)
        # founder, 2026-10-28: the stretch sits under a [song] (8.2-19.8), so its kept head is capped at the song start, with
        # the 0.5 s tail after the line kept (8.5), instead of 8.0 + 3 s snapped to the shot cut at 11.2
        self.assertEqual(sh[0]["cut_at"], 8.5)
        self.assertEqual(sh[0]["cut_quality"], "before song")
        self.assertEqual(sh[0]["lands_before"], x["X5"]); self.assertEqual(sh[0]["lands_at"], 20.0)   # the one cut lands just before the next spoken line
        self.assertEqual(c["removed"]["lines_skipped"], [x["X6"], x["X3"]])   # shortest line first; X5 is a beat and never cut
        self.assertNotIn(x["X5"], c["removed"]["lines_skipped"])
        segs = c["segments"]
        self.assertEqual(segs[0], [0.7, 4.0])                                 # X3 removed: the segment ends at X2's end + the 0.5 s tail, capped by X3's start (4.0)
        self.assertTrue(any(abs(b - 8.5) < 0.01 for a, b in segs) and not any(abs(a - 8.5) < 0.01 for a, b in segs))
        self.assertTrue(any(a <= 25.0 and b >= 28.0 for a, b in segs))        # the reaction hold is inside one kept segment
        self.assertLess(c["final_duration"], c["raw_duration"])
        n_lines = c["n_lines"]
        self.assertLessEqual(len(c["removed"]["lines_skipped"]), int(0.25 * n_lines))

    def test_chunk_rule_no_song_keeps_target_and_remove_keeps_default(self):
        """founder, 2026-10-28: without a song the kept head is target_s (no shot-cut snapping since 2026-11-02); a "remove" stretch keeps
        remove_keep_s; the single cut lands just before the next spoken line; reaction_hold is never touched."""
        f, x = facts_with_sheets()
        f = dict(f); f["lines"] = [r for r in f["lines"] if not (r["kind"] == "event" and r.get("tag_class") == "music")]
        ctx = S3.Ctx(f, CFG)
        sheet = copy.deepcopy(f["beat_sheets"][0])
        segs, removed = S3.apply_trims(ctx, sheet, 0.7, 34.2, "stretches", set())
        g = removed["gaps_shortened"][0]
        self.assertEqual((g["cut_at"], g["cut_quality"], g["lands_at"]), (11.0, "inside stretch", 20.0))
        self.assertIn([11.0, 20.0], [[a, b] for a, b in zip([s[1] for s in segs], [s[0] for s in segs[1:]])])
        sheet["non_dialogue"][0]["recommend"] = "remove"; sheet["non_dialogue"][0].pop("target_s", None)
        segs2, removed2 = S3.apply_trims(ctx, sheet, 0.7, 34.2, "stretches", set())
        g2 = removed2["gaps_removed"][0]
        self.assertEqual((g2["to_s"], g2["cut_at"], g2["cut_quality"]), (3.0, 11.0, "inside stretch"))   # 8.0 + 3 s
        self.assertEqual(g2["lands_before"], x["X5"])
        self.assertFalse(removed2["gaps_shortened"])
        self.assertTrue(any(a <= 25.0 and b >= 28.0 for a, b in segs2))       # the reaction hold stays inside a kept segment
        sheet["non_dialogue"][0]["recommend"] = "keep"
        segs3, removed3 = S3.apply_trims(ctx, sheet, 0.7, 34.2, "stretches", set())
        self.assertEqual(segs3, [[0.7, 34.2]]); self.assertFalse(removed3["gaps_removed"] or removed3["gaps_shortened"])

    def test_build_up_hold_shortened_in_trimmed_twins_only(self):
        """founder, 2026-10-29: a "keep" build-up stretch > 6 s before the payoff keeps 4 s (head) in trimmed twins; one cut
        lands before the next spoken line; after the payoff, or in an untrimmed twin, it is never touched."""
        f, x = facts_with_sheets()
        f = dict(f); f["lines"] = [r for r in f["lines"] if not (r["kind"] == "event" and r.get("tag_class") == "music")]
        ctx = S3.Ctx(f, CFG)
        sheet = copy.deepcopy(f["beat_sheets"][0])
        sheet["non_dialogue"][0].update({"kind": "reaction_hold", "recommend": "keep"}); sheet["non_dialogue"][0].pop("target_s", None)
        segs, removed = S3.apply_trims(ctx, sheet, 0.7, 34.2, "stretches", set(), payoff_t=30.5)
        g = removed["gaps_shortened"][0]
        self.assertEqual((g["cut_at"], g["cut_quality"], g["lands_at"], g["lands_before"]), (12.0, "inside stretch", 20.0, x["X5"]))   # 8.0 + 4 s
        self.assertIn("build-up", g["recommend"])
        self.assertTrue(any(a <= 25.0 and b >= 28.0 for a, b in segs))       # the 3 s hold (< 6 s) is untouched
        after, rem2 = S3.apply_trims(ctx, sheet, 0.7, 34.2, "stretches", set(), payoff_t=5.0)        # the stretch is after the payoff
        self.assertFalse(rem2["gaps_shortened"]); self.assertEqual(after, [[0.7, 34.2]])
        untr, rem3 = S3.apply_trims(ctx, sheet, 0.7, 34.2, "none", set(), payoff_t=30.5)             # untrimmed twin
        self.assertEqual(untr, [[0.7, 34.2]]); self.assertFalse(rem3["gaps_shortened"])
        cfg = copy.deepcopy(CFG); cfg["stage3"]["trims"]["build_up_kinds"] = ["action_beat"]
        _, rem4 = S3.apply_trims(S3.Ctx(f, cfg), sheet, 0.7, 34.2, "stretches", set(), payoff_t=30.5)
        self.assertFalse(rem4["gaps_shortened"])                                # a kind outside the list is kept

    def test_clip_end_never_runs_into_a_song(self):
        """founder, 2026-10-28: a [song] starting after the last line caps the clip end at the song start (tail kept)."""
        f, x = facts_with_sheets()
        out0, _ = run()
        c0 = pick(out0, 1, "comedy", "full_arc")[0]
        f2 = copy.deepcopy(f)
        f2["lines"].append({"id": "L0990", "kind": "event", "tag_class": "music", "tag": "[పాట]", "text": "[పాట]", "start": 32.6, "end": 36.0,
                            "speaker": None, "zero_len": False, "filler": False})
        ctx = S3.Ctx(f2, CFG)
        end, extra, info = S3.snap_end(ctx, x["X9"], f2["beat_sheets"][0])    # X9 ends 32.0; its laugh 32.2-34.0 used to be included (end 34.2)
        self.assertTrue(info.get("song_capped")); self.assertLess(end, c0["end"])
        self.assertEqual(end, 32.8)                                             # max(last line end + 0.8 s tail, song start 32.6)
        cfg = copy.deepcopy(CFG); cfg["stage3"]["end"]["never_into_song"] = False
        end2, _, info2 = S3.snap_end(S3.Ctx(f2, cfg), x["X9"], f2["beat_sheets"][0])
        self.assertFalse(info2.get("song_capped")); self.assertEqual(end2, 34.2)     # the laugh, as before the song

    def test_skip_cap_share(self):
        cfg = copy.deepcopy(CFG); cfg["stage3"]["trims"]["skippable_max_share"] = 0.12
        out, x = run(cfg)
        c = pick(out, 1, "comedy", "full_arc_trimmed")[0]
        self.assertEqual(c["n_lines"], 10)                                     # the button line 'thank you' is in (2026-10-29)
        self.assertEqual(c["removed"]["lines_skipped"], [x["X6"]])            # floor(0.12 * 10) = 1


class TestProtectionAndNonStory(unittest.TestCase):
    def test_beats_included_and_valid(self):
        out, x = run()
        f, _ = facts_with_sheets()
        ctx = S3.Ctx(f, CFG)
        for c in out["candidates"]:
            sheet = next(s for s in f["beat_sheets"] if s["scene_id"] == c["scene_id"])
            self.assertEqual(S3.validate_candidate(ctx, c, sheet, S3.sheet_beats(ctx, sheet)), [], c["id"])
            self.assertEqual(S3.schema_errors(c), [], c["id"])
        full = pick(out, 1, "comedy", "full_arc")[0]
        for b in ("X1", "X2", "X4", "X5", "X8", "X9"):
            self.assertIn(x[b], full["beats_included"])

    def test_non_story_clips_the_tail_and_blocks_spanning_arcs(self):
        out, x = run()
        y2 = [c for c in out["candidates"] if c["scene_id"] == 2 and c["payoff_line"] == x["Y6"]]
        self.assertTrue(y2)
        for c in y2:
            self.assertLessEqual(c["end"], 56.0 + 1e-3)                        # clipped before the TV line
            self.assertNotIn(x["Y7"], c["lines"])
        notes = out["stages"]["3"]["not_generated"]
        self.assertTrue(any("extended" in n["why"] for n in notes))             # an arc over all three payoffs would contain the TV line
        self.assertTrue(any(c["scene_id"] == 2 and c["payoff_line"] == x["Y9"] for c in out["candidates"]))

    def test_broken_candidate_fails_validation(self):
        out, x = run()
        f, _ = facts_with_sheets()
        ctx = S3.Ctx(f, CFG)
        c = copy.deepcopy(pick(out, 1, "comedy", "full_arc")[0])
        c["segments"] = [[0.7, 31.0]]; c["end"] = 31.0; c["raw_duration"] = 30.3; c["final_duration"] = 30.3   # inside the payoff line
        errs = S3.validate_candidate(ctx, c, f["beat_sheets"][0], S3.sheet_beats(ctx, f["beat_sheets"][0]))
        self.assertTrue(any("inside" in e for e in errs))
        bad = dict(c, type="song")
        self.assertTrue(S3.schema_errors(bad))


class TestCapsAndDedupe(unittest.TestCase):
    def test_per_scene_cap_keeps_untrimmed_full_and_best_short(self):
        cfg = copy.deepcopy(CFG); cfg["stage3"]["caps"]["per_scene"] = 2; cfg["stage3"]["caps"]["protect_short_per_scene"] = 1; cfg["stage3"]["caps"]["protect_shortest_story_per_scene"] = 1
        out, x = run(cfg)
        s1 = [c for c in out["candidates"] if c["scene_id"] == 1]
        variants = {(c["type"], c["variant"]) for c in s1}
        self.assertIn(("comedy", "full_arc"), variants)
        self.assertIn(("comedy", "full_arc_trimmed"), variants)                # the twin shares the slot
        self.assertIn(("loop", "one"), variants)                               # best short: ends on the laugh
        self.assertNotIn(("comedy", "tight"), variants)
        dropped = [d for d in out["stages"]["3"]["dropped"] if d["scene_id"] == 1]
        self.assertTrue(all("per-scene cap" in d["why"] for d in dropped))
        ids = [c["id"] for c in s1]
        self.assertEqual(ids, sorted(ids)); self.assertTrue(all(i.startswith("C1") for i in ids))

    def test_per_scene_cap_protects_the_shortest_story_family(self):
        """founder, 2026-10-29: the shortest comedy / emotional family of a scene survives the per-scene cap (it has the
        fewest beats, so it used to be dropped first)."""
        cfg = copy.deepcopy(CFG); cfg["stage3"]["caps"]["per_scene"] = 3; cfg["stage3"]["caps"]["protect_short_per_scene"] = 1
        out, x = run(cfg)
        kept = [c for c in out["candidates"] if c["scene_id"] == 1 and c["type"] in ("comedy", "emotional")]
        all_story = [c for c in S3.run(make_inputs(transcript=transcript(), keys=KEYS), facts_with_sheets()[0], copy.deepcopy(CFG))["candidates"]
                     if c["scene_id"] == 1 and c["type"] in ("comedy", "emotional")]
        shortest = min(all_story, key=lambda c: c["final_duration"])
        self.assertIn((shortest["type"], shortest["variant"], shortest["start"]), [(c["type"], c["variant"], c["start"]) for c in kept])
        cfg2 = copy.deepcopy(cfg); cfg2["stage3"]["caps"]["protect_shortest_story_per_scene"] = 0
        out2, _ = run(cfg2)
        kept2 = [(c["type"], c["variant"], c["start"]) for c in out2["candidates"] if c["scene_id"] == 1 and c["type"] in ("comedy", "emotional")]
        self.assertNotIn((shortest["type"], shortest["variant"], shortest["start"]), kept2)

    def test_episode_cap_prunes_fullest_scene_first(self):
        # 3 protected families per scene (primary arc, best short, shortest story) x 2 scenes = 6: the floor the episode cap can reach
        cfg = copy.deepcopy(CFG); cfg["stage3"]["caps"]["per_episode"] = 6; cfg["stage3"]["caps"]["per_scene"] = 10
        cfg["stage3"]["caps"]["protect_short_per_scene"] = 1; cfg["stage3"]["caps"]["protect_shortest_story_per_scene"] = 1
        out, x = run(cfg)
        capped = [c for c in out["candidates"] if c["variant"] not in ("scene", "scene_trimmed", "thread", "critic_trimmed")]   # extras: outside the caps
        self.assertLessEqual(len({S3.slot_key(c) for c in capped}), 6)        # slots = sibling families
        for sid in (1, 2):                                                     # the floor: an untrimmed arc and a short per scene
            self.assertTrue(any(c["scene_id"] == sid and c["variant"] in ("full_arc", "full") and c["trim_level"] == "none" for c in out["candidates"]))
            self.assertTrue(any(c["scene_id"] == sid and c["type"] in S3.SHORT_TYPES for c in out["candidates"]))
        self.assertTrue(any("episode cap" in d["why"] for d in out["stages"]["3"]["dropped"]))

    def test_exact_duplicates_merged(self):
        out, x = run()
        s2 = [c for c in out["candidates"] if c["scene_id"] == 2]
        keys = [tuple(map(tuple, c["segments"])) for c in s2]
        self.assertEqual(len(keys), len(set(keys)))                            # comedy full and emotional full of the same moment merged
        self.assertTrue(any(c.get("also") for c in s2) or out["stages"]["3"]["merged"])

    def test_untrimmed_full_always_kept_even_over_platform_max(self):
        cfg = copy.deepcopy(CFG); cfg["stage3"]["platform_max_s"] = 15
        out, x = run(cfg)
        full = pick(out, 1, "comedy", "full_arc")
        self.assertTrue(full and full[0]["flags"]["over_platform_max"])
        self.assertFalse(pick(out, 1, "comedy", "full_arc_trimmed"))           # the ~19 s trimmed version is over the hard limit too
        self.assertTrue(any("platform maximum" in n["why"] for n in out["stages"]["3"]["not_generated"]))

    def test_trimmed_candidates_record_their_untrimmed_sibling(self):
        out, x = run()
        full = pick(out, 1, "comedy", "full_arc")[0]; tw = pick(out, 1, "comedy", "full_arc_trimmed")[0]
        self.assertEqual(tw["untrimmed_sibling"], full["id"]); self.assertEqual(full["trimmed_sibling"], tw["id"])
        self.assertIsNone(full["untrimmed_sibling"])
        for c in out["candidates"]:
            if c["trim_level"] == "none" and c["variant"] not in ("extended_untrimmed",):
                self.assertIsNone(c["untrimmed_sibling"])
            if c["trim_level"] == "lines":
                self.assertIsNotNone(c["untrimmed_sibling"], c["id"])            # every trimmed candidate has its untrimmed sibling
        t = pick(out, 1, "comedy", "tight")[0]; tt = pick(out, 1, "comedy", "tight_trimmed")[0]
        self.assertEqual(t["trim_level"], "none"); self.assertEqual(tt["untrimmed_sibling"], t["id"]); self.assertEqual(t["segments"][0][0], tt["segments"][0][0])

    def test_skippable_lines_apply_to_emotional_variants_with_beats_and_holds_protected(self):
        f, x = facts_with_sheets()
        f["beat_sheets"][0]["register"] = "emotional"                          # same scene read as emotional
        out = S3.run(make_inputs(transcript=transcript(), keys=KEYS), f, CFG)
        tw = pick(out, 1, "emotional", "full_trimmed")[0]
        self.assertEqual(tw["removed"]["lines_skipped"], [x["X6"], x["X3"]])   # skippable lines dropped ...
        self.assertNotIn(x["X5"], tw["removed"]["lines_skipped"])              # ... never a beat
        self.assertTrue(any(a <= 25.0 and b >= 28.0 for a, b in tw["segments"]))   # the hold stays whole
        self.assertEqual(tw["untrimmed_sibling"], pick(out, 1, "emotional", "full")[0]["id"])

    def test_no_hook_moments_no_open_question_variants(self):
        """founder, 2026-10-29: only Stage 2 hook_moments add open-question end-variants; an old sheet without the key gets
        none (the payoff-based hooks are gone)."""
        f, x = facts_with_sheets()
        for sh in f["beat_sheets"]:
            sh.pop("hook_moments", None)
        out = S3.run(make_inputs(transcript=transcript(), keys=KEYS), f, CFG)
        self.assertFalse([c for c in out["candidates"] if c["type"] == "hook" or c["variant"].startswith("open_question")])

    def test_loop_ends_at_first_pause_or_its_laugh_and_has_an_earlier_start(self):
        cfg = copy.deepcopy(CFG); cfg["stage3"]["caps"]["per_scene"] = 20; cfg["stage3"]["caps"]["protect_short_per_scene"] = 10
        cfg["stage3"]["cold_open_min_before_payoff_s"] = 100     # else the early loop is an exact duplicate of the cold open and merges into it
        out, x = run(cfg)
        lp = {c["variant"]: c for c in out["candidates"] if c["scene_id"] == 1 and c["type"] == "loop"}
        self.assertAlmostEqual(lp["one"]["end"], 34.2, places=2)                   # the laugh starts 0.2 s after the punchline: included
        self.assertEqual(lp["one"]["extra_lines"]["end"], [])                      # never extends to a later pause
        self.assertEqual(lp["one"]["wider_sibling"], lp["one_early"]["id"]); self.assertLess(lp["one_early"]["start"], lp["one"]["start"])
        f, x2 = facts_with_sheets()
        ctx = S3.Ctx(f, cfg)
        end, extra, info = S3.snap_end(ctx, x2["X5"], f["beat_sheets"][0], kind="loop")   # 0.3 s pause after X5 qualifies, no extension
        self.assertEqual(extra, []); self.assertAlmostEqual(end, 21.3, places=2)
        end, extra, info = S3.snap_end(ctx, x2["X1"], f["beat_sheets"][0], kind="loop")   # 0.1 s pause: end inside it, still no extension
        self.assertEqual(extra, []); self.assertAlmostEqual(end, 2.1, places=2)

    def test_thread_candidate_joins_setup_and_payoff_scene(self):
        """founder, 2026-10-29: setup lines (natural start to the setup's last line) + one cut + the payoff scene from its
        natural start to its end line; outside the caps; always a valid cut on pauses."""
        f, x = facts_with_sheets()
        f["episode_pass"]["threads"] = [{"setup_lines": f"{x['X1']}-{x['X4']}", "payoff_scene": 2, "payoff_end_line": x["Y3"], "why": "w"}]
        out = S3.run(make_inputs(transcript=transcript(), keys=KEYS), f, CFG)
        th = [c for c in out["candidates"] if c["variant"] == "thread"]
        self.assertEqual(len(th), 1); t = th[0]
        self.assertEqual(t["segments"], [[0.7, 8.8], [43.7, 50.2]])
        self.assertEqual(t["scene_id"], 2); self.assertEqual(t["payoff_line"], x["Y3"]); self.assertEqual(t["thread"]["setup_scene"], 1)
        self.assertEqual(t["lines"][:4], [x["X1"], x["X2"], x["X3"], x["X4"]])
        self.assertEqual(out["stages"]["3"]["counts"]["threads"], 1)
        bad = copy.deepcopy(f); bad["episode_pass"]["threads"] = [{"setup_lines": f"{x['Y1']}-{x['Y2']}", "payoff_scene": 1, "why": "w"}]
        out2 = S3.run(make_inputs(transcript=transcript(), keys=KEYS), bad, CFG)
        self.assertFalse([c for c in out2["candidates"] if c["variant"] == "thread"])
        self.assertTrue(any("later scene" in n["why"] for n in out2["stages"]["3"]["not_generated"]))

    def test_scene_variants_whole_scene_trimmed_twin_and_split(self):
        """founder, 2026-10-30: every scene as a clip (first line to last line), trimmed + untrimmed twin, outside the caps;
        a second entry beat after a payoff splits the scene into two situations."""
        cfg = copy.deepcopy(CFG); cfg["stage3"]["caps"]["per_episode"] = 2
        out, x = run(cfg)
        sv = {c["variant"]: c for c in out["candidates"] if c["scene_id"] == 1 and c["variant"] in ("scene", "scene_trimmed")}
        self.assertEqual(set(sv), {"scene", "scene_trimmed"})
        self.assertAlmostEqual(sv["scene"]["start"], 0.7, places=2); self.assertEqual(sv["scene"]["lines"][-1], x["X11"])
        self.assertEqual(sv["scene_trimmed"]["untrimmed_sibling"], sv["scene"]["id"]); self.assertEqual(sv["scene"]["trimmed_sibling"], sv["scene_trimmed"]["id"])
        self.assertLess(sv["scene_trimmed"]["final_duration"], sv["scene"]["final_duration"])
        self.assertEqual(sv["scene"]["payoff_line"], x["X9"])
        self.assertEqual(out["stages"]["3"]["counts"]["scene_variants"], sum(1 for c in out["candidates"] if c["variant"].startswith("scene")))
        f, x = facts_with_sheets()
        f["beat_sheets"][1]["beats"].insert(2, {"line": x["Y5"], "type": "entry"})          # a new situation starts after payoff 1
        f["beat_sheets"][1]["beats"] = [b for b in f["beat_sheets"][1]["beats"] if not (b["line"] == x["Y5"] and b["type"] == "setup")]
        out2 = S3.run(make_inputs(transcript=transcript(), keys=KEYS), f, CFG)
        notes = [n["why"] for n in out2["stages"]["3"]["not_generated"] if n["scene_id"] == 2]
        self.assertTrue(any("non-story" in n and "situation 2" in n for n in notes))            # part 2 holds the TV line: not generated
        self.assertTrue(any(n.startswith("scene variant scene is the same cut as") for n in notes))   # part 1 = an existing cut: judged once
        self.assertEqual([p[1][:16] for p in S3.scene_parts(S3.Ctx(f, CFG), f["beat_sheets"][1])], ["situation 1 of 2", "situation 2 of 2"])

    def test_situations_visual_open_teaser_and_scene_length_cap(self):
        """founder, 2026-10-30: situations (untrimmed + trimmed), a visual-beat start, a teaser_open
        from a strong silent visual beat before the payoff, whole scenes only up to max_scene_s."""
        f, x = facts_with_sheets()
        sh = f["beat_sheets"][0]
        sh["situations"] = [{"start_line": x["X1"], "end_line": x["X4"], "what_happens": "the news", "strength": 5, "t": 1.0, "end": 8.0},
                            {"start_line": x["X5"], "end_line": x["X10"], "what_happens": "the rating", "strength": 8, "t": 20.0, "end": 35.0}]
        sh["visual_beats"] = [{"range": "0:12.0-0:14.0", "what_is_seen": "mother freezes", "role": "feeling", "strength": 6, "start": 12.0, "end": 14.0},
                              {"range": "0:25.0-0:28.0", "what_is_seen": "a long stare", "role": "joke", "strength": 9, "start": 25.0, "end": 28.0}]
        out = S3.run(make_inputs(transcript=transcript(), keys=KEYS), f, CFG)
        sits = [c for c in out["candidates"] if c["variant"].startswith("situation")]
        two = [c for c in sits if c["situation"]["index"] == 2]
        self.assertTrue(two); self.assertTrue(all(abs(c["start"] - 12.0) < 1e-6 for c in two))          # starts on the beat at 12.0
        self.assertEqual(two[0]["start_note"][:11], "visual beat")
        tz = [c for c in out["candidates"] if c["variant"] == "teaser_open"]
        self.assertEqual(len(tz), 1); self.assertEqual(tz[0]["segments"][0], [25.0, 27.5])                # the strength-9 stare, 2.5 s
        self.assertGreater(tz[0]["segments"][1][0], 0)
        self.assertEqual(next(c for c in out["candidates"] if c["variant"] == "cold_open")["situation"]["index"], 2)   # tagged alternate
        cfg = copy.deepcopy(CFG); cfg["stage3"]["scene_variant"]["max_scene_s"] = 20
        out2 = S3.run(make_inputs(transcript=transcript(), keys=KEYS), f, cfg)
        self.assertFalse([c for c in out2["candidates"] if c["scene_id"] == 1 and c["variant"].startswith("scene")])
        self.assertTrue(any("over 20 s" in n["why"] for n in out2["stages"]["3"]["not_generated"]))

    def test_no_loops_where_loops_are_off(self):
        f, x = facts_with_sheets()
        f["episode_pass"]["content_type"] = "scripted_series"
        out = S3.run(make_inputs(transcript=transcript(), keys=KEYS), f, CFG)
        self.assertFalse([c for c in out["candidates"] if c["type"] == "loop"])

    def test_channel_short_reference_candidates(self):
        import json, tempfile
        from pathlib import Path
        f, x = facts_with_sheets()
        f["episode"]["video_id"] = "EPX"
        old = S3.CHANNEL_SHORTS_DIR
        with tempfile.TemporaryDirectory() as td:
            S3.CHANNEL_SHORTS_DIR = Path(td)
            (Path(td) / "EPX.json").write_text(json.dumps({"accepted": [{"short_id": "s1", "views": 10, "duration": 12, "segments": [
                {"episode_start": 28.0, "episode_end": 34.0, "short_start": 0.0}, {"episode_start": 44.0, "episode_end": 50.0, "short_start": 6.0}]}]}))
            try:
                out = S3.run(make_inputs(transcript=transcript(), keys=KEYS), f, CFG)
            finally:
                S3.CHANNEL_SHORTS_DIR = old
        cs = [c for c in out["candidates"] if c["variant"] == "channel_short"]
        self.assertEqual(len(cs), 1); self.assertFalse(cs[0]["eligible_for_final"])
        self.assertEqual(cs[0]["segments"], [[28.0, 34.0], [44.0, 50.0]]); self.assertEqual(cs[0]["channel_short"]["scenes"], [1, 2])

    def test_sheet_override_sets_the_button_line(self):
        """founder, 2026-10-30: data/sheet_overrides/<id>.yaml can set a scene's button_line (applied by Stage 3)."""
        import tempfile
        from pathlib import Path
        f, x = facts_with_sheets()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ov.yaml"; p.write_text(f"scenes:\n  1: {{button_line: {x['X11']}, note: n}}\n", encoding="utf-8")
            over = S3.sheet_overrides("any", p)
        sheets, applied = S3.apply_sheet_overrides(f["beat_sheets"], over)
        self.assertEqual(sheets[0]["button_line"], x["X11"]); self.assertEqual(applied[0]["scene_id"], 1)
        self.assertNotIn("button_line", f["beat_sheets"][0])                     # the facts' own sheet is not changed
        self.assertEqual(S3.sheet_overrides("no-such-episode"), {})

    def test_critic_trimmed_sibling_for_a_pace_flagged_variant(self):
        """founder, 2026-10-29: a FAIL on swipe stretches only gets a critic_trimmed sibling: the flagged stretch keeps 3 s,
        then one cut lands before the next spoken line; nothing from the payoff on is ever cut."""
        out, x = run()
        base = pick(out, 1, "comedy", "full_arc")[0]
        tmap = [{"clip_start": 0.0, "clip_end": base["final_duration"], "ep_start": base["start"], "ep_end": base["end"]}]
        def rec(swipes, reasons):
            return {"id": base["id"], "type": "comedy", "variant": "full_arc", "verdict": "FAIL", "n_pass": 0, "n_runs": 1, "time_map": tmap,
                    "content_key": ["comedy", "full_arc", [[round(a, 3), round(b, 3)] for a, b in base["segments"]]],
                    "runs": [{"pass": False, "reasons": reasons, "long_swipes": [{"from_s": a, "to_s": b} for a, b in swipes]}]}
        f = copy.deepcopy(out)
        f["critic"] = {"clips": [rec([(9.0, 19.0)], ["swipe stretch over the limit: 9-19 s"])], "archive": []}
        out2 = S3.run(make_inputs(transcript=transcript(), keys=KEYS), f, CFG)
        ct = [c for c in out2["candidates"] if c["variant"] == "critic_trimmed"]
        self.assertEqual(len(ct), 1); t = ct[0]
        self.assertEqual(t["segments"], [[0.7, 12.7], [20.0, 35.5]])           # 3 s kept after 9 s of clip time, lands before X5
        self.assertEqual(t["critic_trim_of"], pick(out2, 1, "comedy", "full_arc")[0]["id"]); self.assertEqual(t["type"], "comedy")
        self.assertEqual(t["removed"]["critic_cut"][0]["lands_before"], x["X5"])
        f["critic"] = {"clips": [rec([(25.0, 33.0)], ["swipe stretch over the limit: 25-33 s"])], "archive": []}   # runs into the payoff
        out3 = S3.run(make_inputs(transcript=transcript(), keys=KEYS), f, CFG)
        self.assertFalse([c for c in out3["candidates"] if c["variant"] == "critic_trimmed"])
        f["critic"] = {"clips": [rec([(9.0, 19.0)], ["swipe stretch over the limit: 9-19 s", "missing context: major (x)"])], "archive": []}
        out4 = S3.run(make_inputs(transcript=transcript(), keys=KEYS), f, CFG)       # confusion: no trim
        self.assertFalse([c for c in out4["candidates"] if c["variant"] == "critic_trimmed"])

    def test_stage_entry_and_counts(self):
        out, x = run()
        st = out["stages"]["3"]
        self.assertEqual(st["usage"]["cost_inr"], 0.0)
        self.assertEqual(st["counts"]["kept"], len(out["candidates"]))
        self.assertGreaterEqual(st["counts"]["kept"], 6)


if __name__ == "__main__":
    unittest.main()
