"""Unit tests for selection_v2.stage1_facts on the synthetic transcript (tests/v2/synthetic.py).

Run: venv/bin/python -m pytest tests/v2 -q
"""
import json
import unittest

from selection_v2 import stage1_facts as S
from selection_v2.config import load_cast_lists, load_config
from selection_v2.runner import validate_facts
from tests.v2.synthetic import make_inputs

import copy

CFG = copy.deepcopy(load_config())
CFG["stage1a"]["context_window_s"] = 8     # the synthetic episode is 90 s long; the real default (30 s) would smear
CAST = load_cast_lists()


def facts():
    return S.run(make_inputs(), None, CFG, CAST)


def line(f, lid):
    return next(r for r in f["lines"] if r["id"] == lid)


def by_text(f, text):
    return next(r for r in f["lines"] if r["kind"] == "speech" and r["text"] == text)


class TestHelpers(unittest.TestCase):
    def test_parse_ts_repairs_carry(self):
        self.assertEqual(S.parse_ts("1:60.0"), (120.0, "seconds >= 60 in '1:60.0'"))
        self.assertEqual(S.parse_ts("13:60.0")[0], 840.0)
        self.assertEqual(S.parse_ts("2:00.0"), (120.0, None))
        self.assertEqual(S.parse_ts(12.5), (12.5, None))

    def test_fmt_ts(self):
        self.assertEqual(S.fmt_ts(924.8), "15:24.8")
        self.assertEqual(S.fmt_ts(0.0), "0:00.0")

    def test_tag_classes(self):
        t = CFG["tags"]
        self.assertEqual(S.classify_tag("[నవ్వు]", t), "laugh")
        self.assertEqual(S.classify_tag("[laughs]", t), "laugh")
        self.assertEqual(S.classify_tag("[నిశబ్దం]", t), "silence")
        self.assertEqual(S.classify_tag("[బ్యాక్ గ్రౌండ్ మ్యూజిక్]", t), "music")
        self.assertEqual(S.classify_tag("[పాట]", t), "music")
        self.assertEqual(S.classify_tag("[నిట్టూర్పు]", t), "sigh")
        self.assertEqual(S.classify_tag("[door bell]", t), "sfx")
        self.assertEqual(S.classify_tag("[ఫోన్ రింగింగ్]", t), "sfx")
        self.assertEqual(S.classify_tag("[vehicle horn]", t), "ambient")
        self.assertEqual(S.classify_tag("[సెన్సార్డ్]", t), "censored")
        self.assertEqual(S.classify_tag("[వడసన]", t), "other")


class TestNormalize(unittest.TestCase):
    def test_zero_len_and_stretched_word(self):
        f = facts()
        z = by_text(f, "ఆ")
        self.assertTrue(z["zero_len"])
        s = by_text(f, "thank you sir")
        self.assertLess(s["end"], 90.0)
        self.assertAlmostEqual(s["end"], 72.0 + 12.0 + CFG["stage1a"]["word_max_s"], places=2)  # last word starts at 84.0
        reasons = {x["field"] for x in f["normalization_fixes"]}
        self.assertIn("zero_len", reasons)
        self.assertIn("end", reasons)

    def test_zero_len_line_is_skipped_by_gap_math(self):
        f = facts()
        z = by_text(f, "ఆ")
        self.assertFalse(any(g["prev"] == z["id"] or g["next"] == z["id"] for g in f["gaps"]))

    def test_layers_and_episode(self):
        f = facts()
        self.assertFalse(f["layers"]["asr_fix"])
        self.assertTrue(f["layers"]["speaker_fix"])
        self.assertEqual(f["episode"]["duration"], 92.0)
        self.assertAlmostEqual(f["episode"]["first_dialogue"], 1.0)
        self.assertAlmostEqual(f["episode"]["last_dialogue"], 86.0, places=2)
        self.assertEqual(f["episode"]["series_key"], "surya")   # by title regex


class TestGapTable(unittest.TestCase):
    def test_scene_edge_gap(self):
        f = facts()
        a, b = by_text(f, "వెళ్తున్నా అమ్మా"), by_text(f, "స్వామి ఏం చేస్తున్నావ్ రా?")
        g = next(g for g in f["gaps"] if g["prev"] == a["id"])
        self.assertEqual(g["next"], b["id"])
        self.assertAlmostEqual(g["gap"], 5.0)
        self.assertEqual(g["filled_by"], "silence")
        self.assertEqual(g["spk_before"], ["A", "B"])
        self.assertEqual(g["spk_after"], ["C", "D"])
        self.assertNotIn("shot_cuts", g)                # shot cuts removed (founder, 2026-11-02: unreliable)
        self.assertGreaterEqual(g["gap_pct"], 0.97)  # the longest gap of the episode (strictly-shorter share)
        self.assertIn("tag:silence", g["boundary_evidence"])
        self.assertIn("turnover: total speaker change", g["boundary_evidence"])
        self.assertGreaterEqual(g["boundary_score"], 0.9)

    def test_hard_cut_with_zero_gap(self):
        f = facts()
        a = by_text(f, "సరే")   # last of scene 2 ends at 60.0 ... first "సరే" is scene 1; pick the 59.2 one
        a = next(r for r in f["lines"] if r["kind"] == "speech" and r["text"] == "సరే" and r["start"] == 59.2)
        g = next(g for g in f["gaps"] if g["prev"] == a["id"])
        self.assertEqual(g["gap"], 0.0)
        self.assertIn("hard_cut", g["boundary_evidence"])
        self.assertFalse(any(e.startswith("shot_cut") for e in g["boundary_evidence"]))
        self.assertTrue(g["qualifies"])          # total turnover alone qualifies, whatever the score

    def test_percentiles_are_relative(self):
        f = facts()
        pcts = [g["gap_pct"] for g in f["gaps"]]
        self.assertGreaterEqual(max(pcts), 0.97)
        self.assertEqual(min(pcts), 0.0)          # the shortest gap(s) rank at the bottom
        self.assertTrue(all(0 <= p < 1 for p in pcts))
        tied = [g["gap_pct"] for g in f["gaps"] if g["gap"] == 0.5]
        self.assertEqual(len(set(tied)), 1)       # equal gaps share one percentile

    def test_overlap_recorded(self):
        f = facts()
        h = by_text(f, "హా")
        g = next(g for g in f["gaps"] if g["next"] == h["id"])
        self.assertTrue(g["overlap"])
        self.assertEqual(g["gap"], 0.0)
        self.assertLess(g["gap_raw"], 0)


class TestProposals(unittest.TestCase):
    def test_both_scene_edges_proposed_and_small_gaps_not(self):
        f = facts()
        ts = [p["t"] for p in f["boundary_proposals"]]
        self.assertIn(34.0, ts)      # the next line's start (no shot-cut snapping)
        self.assertIn(60.0, ts)
        for p in f["boundary_proposals"]:
            if p["selected_by"] == "score":
                self.assertTrue(p["score"] >= CFG["stage1a"]["boundary"]["min_score"] and len(p["evidence"]) >= 2, p)
            else:
                self.assertIn(p["selected_by"], ("total_turnover", "cast_swap", "coverage"), p)
        self.assertLessEqual(len(f["boundary_proposals"]), 6)
        swap = next(p for p in f["boundary_proposals"] if p["t"] == 60.0)
        self.assertTrue(swap["cast_swap"])                       # C, D leave; F enters
        self.assertTrue(any(e.startswith("cast swap: +F -D") for e in swap["evidence"]), swap["evidence"])
        top2 = sorted(f["boundary_proposals"], key=lambda p: -p["score"])[:2]
        self.assertEqual(sorted(p["t"] for p in top2), [34.0, 60.0])
        for p in f["boundary_proposals"]:
            self.assertTrue(p["evidence"])
            self.assertEqual(p["t_str"], S.fmt_ts(p["t"]))

    def test_ranks_are_in_time_order(self):
        f = facts()
        ranks = [p["rank"] for p in f["boundary_proposals"]]
        self.assertEqual(ranks, sorted(ranks))


class TestFiller(unittest.TestCase):
    def test_filler_lines_marked_and_ignored_in_speaker_sets(self):
        f = facts()
        self.assertTrue(by_text(f, "హా")["filler"])
        self.assertTrue(by_text(f, "ఆ")["filler"])          # zero-length
        self.assertFalse(by_text(f, "ఏంటి?")["filler"])
        # the stray 'హా' by H must not put H into the speaker set after the hard cut at 60.0
        a = next(r for r in f["lines"] if r["kind"] == "speech" and r["text"] == "సరే" and r["start"] == 59.2)
        g = next(g for g in f["gaps"] if g["prev"] == a["id"])
        self.assertNotIn("H", g["spk_after"])
        self.assertEqual(g["boundary_components"]["turnover"], 1.0)

    def test_lone_turnover_without_total_change_does_not_qualify(self):
        f = facts()
        weak = [g for g in f["gaps"] if g["boundary_evidence"] and all(e.startswith("turnover 0.") for e in g["boundary_evidence"])]
        self.assertTrue(weak)
        self.assertFalse(any(g["qualifies"] for g in weak))


class TestTagEvents(unittest.TestCase):
    def test_events_with_preceding_line(self):
        f = facts()
        laugh = next(e for e in f["tag_events"] if e["class"] == "laugh")
        self.assertEqual(laugh["after"], by_text(f, "హహ")["id"])
        self.assertEqual(laugh["after_content_line"], by_text(f, "హహ")["id"])
        cens = next(e for e in f["tag_events"] if e["class"] == "censored")
        self.assertEqual(cens["inside"], by_text(f, "ఛీ [bleep] వెళ్ళు")["id"])        # the bleep sits inside this line
        self.assertEqual(cens["after"], by_text(f, "five star rating ఇవ్వండి")["id"])  # literal: last line ended before it
        music = next(e for e in f["tag_events"] if e["class"] == "music")
        self.assertEqual(music["after"], by_text(f, "ఊ")["id"])               # literal: the 'ఊ' filler
        self.assertEqual(music["after_content_line"], by_text(f, "ఛీ [bleep] వెళ్ళు")["id"])   # 'ఊ' skipped
        self.assertAlmostEqual(laugh["t"], 44.7)
        self.assertEqual([e["class"] for e in f["tag_events"]], ["silence", "laugh", "censored", "music"])
        self.assertEqual(f["stages"]["1a"]["summary"]["n_censored"], 1)
        # "other" tags are kept as lines but never become tag events
        self.assertTrue(any(r["tag_class"] == "other" for r in f["lines"]))


class TestCastList(unittest.TestCase):
    def test_cast_list_merges_config_and_fix(self):
        f = facts()
        names = f["cast_list"]["names"]
        self.assertIn("సూర్య", names["Surya"])      # from config
        self.assertIn("Virgin", names["Arjun"])     # aka from speaker_fix
        self.assertEqual(f["cast_list"]["series_key"], "surya")


class TestSuspects(unittest.TestCase):
    def test_lone_speaker_flagged(self):
        f = facts()
        hb = by_text(f, "Happy birthday to you")
        lone = [s for s in f["structural_suspects"] if s["reason"] == "lone_speaker" and s["label_space"] == "fix"]
        self.assertIn(hb["id"], [s["line"] for s in lone])
        self.assertEqual(next(s for s in lone if s["line"] == hb["id"])["speaker"], "E")
        self.assertTrue({s["speaker"] for s in lone} <= {"E", "H"})   # H's single overlapping 'హా' may be lone too

    def test_overlap_and_filler_only_in_both_label_spaces(self):
        f = facts()
        h = by_text(f, "హా")
        ov = [s for s in f["structural_suspects"] if s["reason"] == "overlaps_previous_line"]
        self.assertEqual(sorted((s["line"], s["label_space"]) for s in ov), [(h["id"], "fix"), (h["id"], "scribe")])
        fo = [s for s in f["structural_suspects"] if s["reason"] == "filler_only_speaker"]
        self.assertEqual(sorted((s["speaker"], s["label_space"]) for s in fo), [("H", "fix"), ("speaker_7", "scribe")])
        self.assertEqual(set(f["stages"]["1a"]["summary"]["n_structural_suspects"]), {"fix", "scribe"})

    def test_filler_island(self):
        f = facts()
        h = by_text(f, "హా")     # H's only line, filler, and H never speaks in the next segment
        fi = [s for s in f["structural_suspects"] if s["reason"] == "filler_island" and s["label_space"] == "fix"]
        self.assertIn(h["id"], [s["line"] for s in fi])
        self.assertNotIn(by_text(f, "ఊ")["id"], [s["line"] for s in fi])   # F has content lines in that segment

    def test_speaker_segments(self):
        f = facts()
        segs = {d["key"]: d for d in f["speaker_segments"]}
        self.assertEqual(segs["A"]["n_lines"], 11)
        self.assertEqual(segs["A"]["scribe_labels"], ["speaker_0"])
        self.assertTrue(segs["E"]["isolated"])
        self.assertTrue(segs["E"]["isolated"])
        self.assertIn("G", segs["F"]["exclusive_partners"])    # the customer G only ever appears next to F
        self.assertIn("G*", segs["F"]["segments_str"])
        self.assertTrue(all(d["isolated"] for d in f["speaker_segments"] if d["key"] in "ABEGH"))

    def test_confidence_flags(self):
        f = facts()
        flags = {x["line"]: x["flag"] for x in f["speaker_flags"]}
        self.assertEqual(flags[by_text(f, "Happy birthday to you")["id"]], "suspect")
        self.assertEqual(flags[by_text(f, "హా")["id"]], "suspect")
        self.assertEqual(flags[by_text(f, "ఏంటి?")["id"]], "low")
        self.assertIn(by_text(f, "హా")["id"], f["structural_suspect_ids"])


class TestDocument(unittest.TestCase):
    def test_schema_valid_and_json_serialisable(self):
        f = facts()
        validate_facts(f)
        json.dumps(f, ensure_ascii=False)
        self.assertEqual(f["stages"]["1a"]["usage"]["cost_inr"], 0.0)
        self.assertEqual(f["stages"]["1a"]["version"], S.VERSION)

    def test_rerun_warns_about_dropped_stages(self):
        f = facts()
        f["stages"]["2"] = {"version": "x"}
        g = S.run(make_inputs(), f, CFG, CAST)
        self.assertTrue(any("dropped" in w for w in g["stages"]["1a"]["warnings"]))
        self.assertNotIn("2", g["stages"])

    def test_no_shot_cuts_and_no_name_votes(self):
        """founder, 2026-11-02: shot cuts (never checked, missed real cuts) and the name / kinship votes (a code rule) are gone."""
        f = S.run(make_inputs(), None, CFG, CAST)
        validate_facts(f)
        self.assertTrue(all("shot_cuts" not in g for g in f["gaps"]))
        self.assertFalse({"name_votes", "role_votes", "name_vote_events", "name_vote_check"} & set(f))
        self.assertNotIn("shots", f["layers"])


if __name__ == "__main__":
    unittest.main()
