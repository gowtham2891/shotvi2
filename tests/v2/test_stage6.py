"""Stage 6, final selection: set rules on synthetic moments, moment building on synthetic verdicts, the mocked text
call, the package writer and the acceptance kinds. No live calls, no ffmpeg."""
from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from selection_v2 import acceptance, stage5_critic as S5, stage6_select as S6
from selection_v2.runner import validate_facts
from tests.v2.test_stage5 import CFG, FakeMedia, critic_answer, facts_after_4, run5

C6 = copy.deepcopy(CFG["stage6"])


def mom(key, scene, kind="story", type_="comedy", strength=3, combined=8.0, start=None, dur=30.0, series_pull=8, fan=False, recurring=True, payoff=None):
    start = float(scene * 100 if start is None else start)
    p = {"id": key, "type": type_, "variant": "x", "final_duration": dur, "verdict": "PASS" if strength >= 2 else "BORDERLINE", "n_pass": strength, "n_runs": 3,
         "stage4": {"combined": combined, "fan_clip": fan, "series_pull": series_pull}, "segments": [[start, start + dur]],
         "located_s": [5.0] * 3, "best": [{"what": "a look"}] * 3, "expected": {"clip_start": 5.0, "text": "payoff"}, "swipe": [[]] * 3,
         "missing": [{"missing": "none"}] * 3, "run_reasons": [[]] * 3}
    return {"key": key, "scene_id": scene, "payoff_line": payoff or f"P{key}", "kind": kind, "type": type_, "primary": p, "alternates": [], "variants": [p],
            "strength": strength, "combined": combined, "series_pull": series_pull, "fan_clip": fan, "start": start, "end": start + dur,
            "recurring": recurring, "label": key, "payoff_text": "", "cast": []}


def rules(**kw):
    c6 = copy.deepcopy(C6)
    c6["rules"].update(kw)
    return c6


class TestSetRules(unittest.TestCase):
    EP = 1500.0

    def pool(self):
        # eight comedies in eight scenes, spread over the episode, strictly ordered by combined
        return [mom(f"c{i}", i, combined=9.0 - i * 0.1, start=i * 180.0) for i in range(8)]

    def test_ranking_best_version_combined_then_shorter(self):
        """founder, 2026-10-30: moments rank by their best version's Stage 4 combined (no critic bonus), then shorter."""
        ms = [mom("a", 1, strength=2, combined=9.5), mom("b", 2, strength=3, combined=7.0), mom("c", 3, strength=3, combined=8.0, dur=40),
              mom("d", 4, strength=3, combined=8.0, dur=20), mom("e", 5, strength=1, combined=9.9)]
        for m in ms:
            m["rank_score"] = m["combined"]
        self.assertEqual([m["key"] for m in sorted(ms, key=S6.rank_key)], ["e", "a", "d", "c", "b"])
        self.assertNotIn("critic_bonus", C6)

    def test_walk_takes_top_six_then_slots(self):
        sel = S6.select_set(self.pool(), rules(require_types=[], series_pull={}, recurring_cast_share=None, spread={}), self.EP)
        self.assertEqual([m["key"] for m in sel["main"]], [f"c{i}" for i in range(6)])
        self.assertEqual(sel["why"]["c6"]["rule"], "slots"); self.assertEqual(sel["why"]["c7"]["rank"], 8)

    def test_same_scene_and_overlap(self):
        """founder, 2026-10-30: at most 2 main moments per scene, never overlapping; never a whole scene beside its situations."""
        ms = [mom("a", 1, combined=9, start=0), mom("b", 1, combined=8, start=500), mom("c", 2, combined=7, start=10, dur=30),
              mom("d", 1, combined=6.5, start=900)]
        sel = S6.select_set(ms, rules(require_types=[], series_pull={}, recurring_cast_share=None, spread={}), self.EP)
        self.assertEqual([m["key"] for m in sel["main"]], ["a", "b"])
        self.assertEqual(sel["why"]["d"]["rule"], "same_scene"); self.assertEqual(sel["why"]["c"]["rule"], "overlap")
        w = mom("w", 5, combined=9.5, start=1200, dur=60); w["whole_scene"] = True
        s5 = mom("s", 5, combined=9.0, start=1300, dur=20)
        sel3 = S6.select_set([w, s5], rules(require_types=[], series_pull={}, recurring_cast_share=None, spread={}), self.EP)
        self.assertEqual([m["key"] for m in sel3["main"]], ["w"]); self.assertEqual(sel3["why"]["s"]["rule"], "scene_with_situation")
        sel2 = S6.select_set(ms, rules(require_types=[], series_pull={}, recurring_cast_share=None, spread={}, same_scene=True, overlap=True), self.EP)
        self.assertEqual(len(sel2["main"]), 3)

    def test_spread_over_thirds(self):
        ms = [mom(f"e{i}", i, combined=9 - i * 0.1, start=i * 60.0) for i in range(5)] + [mom("late", 9, combined=5, start=1400)]
        sel = S6.select_set(ms, rules(require_types=[], series_pull={}, recurring_cast_share=None), self.EP)
        early = [m["key"] for m in sel["main"] if m["start"] < 500]
        self.assertEqual(len(early), 3); self.assertEqual(sel["why"]["e3"]["rule"], "spread"); self.assertIn("late", [m["key"] for m in sel["main"]])

    def test_fan_cap(self):
        ms = [mom("f1", 1, fan=True, combined=9, start=0), mom("f2", 2, fan=True, combined=8.5, start=600), mom("n", 3, combined=8, start=1200)]
        sel = S6.select_set(ms, rules(require_types=[], series_pull={}, recurring_cast_share=None), self.EP)
        self.assertEqual([m["key"] for m in sel["main"]], ["f1", "n"]); self.assertEqual(sel["why"]["f2"], {"rule": "fan_cap", "other": "f1"})

    def test_backfill_required_types_swaps_the_lowest_ranked(self):
        ms = self.pool() + [mom("hk", 20, kind="hook", type_="hook", combined=5.0, start=1450, dur=10),
                            mom("em", 21, type_="emotional", combined=4.0, start=1300)]
        sel = S6.select_set(ms, rules(series_pull={}, recurring_cast_share=None, spread={}), self.EP)
        keys = [m["key"] for m in sel["main"]]
        self.assertEqual(len(keys), 6); self.assertIn("hk", keys); self.assertIn("em", keys)
        self.assertNotIn("c5", keys); self.assertNotIn("c4", keys)                     # the two lowest-ranked comedies went
        self.assertEqual(sel["why"]["c5"]["rule"], "backfill"); self.assertEqual(sel["why"]["c4"]["rule"], "backfill")
        self.assertTrue(all(r["ok"] for r in sel["requirements"]))
        no_hook = S6.select_set(self.pool(), rules(series_pull={}, recurring_cast_share=None, spread={}), self.EP)
        self.assertEqual([r["name"] for r in no_hook["requirements"]], [])        # no hook / emotional shippable: no requirement

    def test_backfill_evicts_the_same_scene_blocker_when_needed(self):
        ms = [mom("c0", 0, combined=9, start=0)] + [mom(f"c{i}", i, combined=8.5 - i * 0.1, start=i * 200.0) for i in range(1, 6)] + \
             [mom("hk", 5, kind="hook", type_="hook", combined=5, start=1100, dur=10)]
        sel = S6.select_set(ms, rules(series_pull={}, recurring_cast_share=None, spread={}), self.EP)
        keys = [m["key"] for m in sel["main"]]
        self.assertIn("hk", keys); self.assertNotIn("c5", keys)                      # c5 shares scene 5 with the only hook

    def test_people_rules(self):
        ms = [mom(f"c{i}", i, combined=9 - i * 0.1, start=i * 200.0, series_pull=5, recurring=(i < 2)) for i in range(7)] + \
             [mom("sp1", 10, combined=4, start=1450, series_pull=8, recurring=True), mom("sp2", 11, combined=3.9, start=1300, series_pull=9, recurring=True)]
        sel = S6.select_set(ms, rules(require_types=[], spread={}, same_scene=False), self.EP)
        main = sel["main"]
        self.assertGreaterEqual(sum(1 for m in main if m["series_pull"] >= 7), 2)
        self.assertGreaterEqual(sum(1 for m in main if m["recurring"]), 3)
        self.assertTrue(all(r["ok"] for r in sel["requirements"]), sel["requirements"])
        impossible = S6.select_set([mom("x", 1, series_pull=3)], rules(require_types=[], spread={}, recurring_cast_share=None), self.EP)
        self.assertTrue(impossible["warnings"]); self.assertFalse(impossible["requirements"][0]["ok"])

    def test_bonus_loops(self):
        main = [mom("m1", 1, combined=9, start=0, payoff="P1")]
        loops = [mom("l1", 1, kind="loop", type_="loop", combined=8, start=5, dur=8, payoff="P1"),         # same moment as the main post
                 mom("l2", 2, kind="loop", type_="loop", combined=7, start=300, dur=8),
                 mom("l3", 3, kind="loop", type_="loop", combined=6, start=600, dur=8),
                 mom("l4", 4, kind="loop", type_="loop", combined=5, start=900, dur=8)]
        sel = S6.select_set(main + loops, rules(require_types=[], series_pull={}, recurring_cast_share=None), self.EP)
        self.assertEqual([m["key"] for m in sel["bonus"]], ["l2", "l3"])
        self.assertEqual(sel["why"]["l1"]["rule"], "same_moment_as_main"); self.assertEqual(sel["why"]["l4"]["rule"], "bonus_slots")
        self.assertEqual([m["key"] for m in sel["main"]], ["m1"])                     # loops never take main slots

    def test_bonus_loop_may_share_a_moment_with_a_main_hook_only(self):
        """founder, 2026-10-29: the hook stops before the punchline, the loop is the punchline: different posts."""
        hook = mom("h1", 1, kind="hook", type_="hook", combined=9, start=0, dur=7, payoff="P1")
        loop = mom("l1", 1, kind="loop", type_="loop", combined=8, start=3, dur=9, payoff="P1")
        sel = S6.select_set([hook, loop], rules(require_types=[], series_pull={}, recurring_cast_share=None), self.EP)
        self.assertEqual([m["key"] for m in sel["main"]], ["h1"]); self.assertEqual([m["key"] for m in sel["bonus"]], ["l1"])
        story = mom("s1", 1, type_="emotional", combined=9, start=0, dur=20, payoff="P1")
        sel2 = S6.select_set([story, loop], rules(require_types=[], series_pull={}, recurring_cast_share=None), self.EP)
        self.assertEqual(sel2["bonus"], []); self.assertEqual(sel2["why"]["l1"]["rule"], "same_moment_as_main")


    def test_loops_off_by_content_type(self):
        """founder, 2026-10-29: stage6 loops off by default for scripted series; other content types keep bonus loops."""
        cfg = {"stage6": copy.deepcopy(C6)}
        cfg["stage6"]["loops_by_content_type"] = {"scripted_series": False, "default": True}
        series = S6.stage6_cfg({"episode_pass": {"content_type": "scripted_series"}}, cfg)
        podcast = S6.stage6_cfg({"episode_pass": {"content_type": "podcast"}}, cfg)
        self.assertEqual((series["bonus_loops"], series["loops_off_for"]), (0, "scripted_series"))
        self.assertEqual(podcast["bonus_loops"], C6["bonus_loops"]); self.assertNotIn("loops_off_for", podcast)
        self.assertNotIn("loops_off_for", cfg["stage6"])                               # the config itself is not changed
        main = [mom("m1", 1, combined=9, start=0)]
        loops = [mom("l2", 2, kind="loop", type_="loop", combined=7, start=300, dur=8)]
        series["rules"].update(require_types=[], series_pull={}, recurring_cast_share=None)
        sel = S6.select_set(main + loops, series, self.EP)
        self.assertEqual(sel["bonus"], []); self.assertEqual(sel["why"]["l2"]["rule"], "loops_off")
        self.assertIn("loops are off for scripted_series", S6.reason_text(sel["why"]["l2"], {}, 6, 0))
        sel["moments"] = main + loops
        self.assertNotIn("l2", [x["key"] for x in S6.creator_picks(sel, series)])         # no loop picks where loops are off

def rec(verdict, n_pass, second=1.0, why="pace"):
    ans = critic_answer(second=second)
    fail = ["swipe stretch over the limit: 2-14 s"] if why == "pace" else ["missing context: major (who is he)"]
    runs = [{"run": i + 1, "answers": copy.deepcopy(ans), "pass": i < n_pass, "reasons": [] if i < n_pass else list(fail),
             "located_s": second} for i in range(3)]
    for r in runs[n_pass:]:
        r["answers"]["swipe"] = [{"from_s": 2, "to_s": 14, "why": "walking"}]
    return {"verdict": verdict, "n_pass": n_pass, "n_runs": 3, "runs": runs, "located_s": [second] * 3, "expected": {"clip_start": 5.0, "text": "payoff"},
            "swipe": [r["answers"]["swipe"] for r in runs], "caption_context": {"needs_caption_context": False}, "usage": {"cost_inr": 1.0}}


class TestMoments(unittest.TestCase):
    def setUp(self):
        self.f, self.x = facts_after_4()
        for s in S5.S4.active_run(self.f)["candidates"]:
            s["status"] = "pass"
        self.f["scores"] = S5.S4.active_run(self.f)

    def with_records(self, recs):
        orig = S5.judged_records
        S5.judged_records = lambda facts: recs
        try:
            return S6.build_moments(self.f, CFG)
        finally:
            S5.judged_records = orig

    def test_typical_length_primary_pace_fail_ships_confusion_fail_never(self):
        """founder, 2026-10-30: the primary is the shippable version closest to the typical length (ties: Stage 4 combined,
        then critic passes); founder, 2026-10-29: a pace FAIL is shippable with the critic's note; a confusion FAIL never."""
        cs = {c["variant"]: c for c in self.f["candidates"] if c["scene_id"] == 1 and c["type"] == "comedy"}
        recs = {cs["full_arc"]["id"]: rec("PASS", 2), cs["full_arc_trimmed"]["id"]: rec("FAIL", 0), cs["tight"]["id"]: rec("PASS", 3),
                cs["tight_trimmed"]["id"]: rec("BORDERLINE", 1), cs["cold_open"]["id"]: rec("FAIL", 0, why="confusion")}
        ms = self.with_records(recs)
        m = next(m for m in ms if m["scene_id"] == 1 and m["kind"] == "story")
        v = {x["id"]: x for x in m["variants"]}
        ship = [x for x in v.values() if x["shippable"]]
        best = max(x["stage4"]["combined"] for x in ship)
        want = min((x for x in ship if x["stage4"]["combined"] >= best - 0.5), key=lambda x: (round(abs(x["final_duration"] - 65.0), 3), -(x["stage4"]["combined"] or 0), -x["n_pass"], x["id"]))
        self.assertEqual(m["primary"]["id"], want["id"]); self.assertEqual(m["typical_length"]["target_s"], 65.0)
        pace = v[cs["full_arc_trimmed"]["id"]]
        self.assertTrue(pace["shippable"]); self.assertEqual(pace["reason_class"], "pace"); self.assertIn("2-14 s", pace["critic_note"])
        conf = v[cs["cold_open"]["id"]]
        self.assertFalse(conf["shippable"]); self.assertEqual(conf["reason_class"], "confusion"); self.assertIn("confusion", conf["not_shipped_why"])
        self.assertNotIn(conf["id"], [a["id"] for a in m["alternates"]])
        self.assertAlmostEqual(m["rank_score"], max(x["stage4"]["combined"] for x in ship))     # the best shippable version
        self.assertEqual(len(m["variants"]), 5)

    def test_borderline_only_primary_and_dropped_moment(self):
        cs = {c["variant"]: c for c in self.f["candidates"] if c["scene_id"] == 1 and c["type"] == "comedy"}
        ms = self.with_records({cs["tight"]["id"]: rec("BORDERLINE", 1), cs["full_arc"]["id"]: rec("BORDERLINE", 1)})
        m = next(m for m in ms if m["scene_id"] == 1)
        self.assertEqual(m["primary"]["id"], cs["full_arc"]["id"]); self.assertEqual(m["strength"], 1)    # the one closer to 65 s
        ms2 = self.with_records({cs["tight"]["id"]: rec("FAIL", 0, why="confusion")})
        self.assertIsNone(ms2[0]["primary"]); self.assertIn("FAIL", ms2[0]["variants"][0]["not_shipped_why"])
        ms3 = self.with_records({cs["tight"]["id"]: rec("FAIL", 0)})                       # a pace-only FAIL ships, annotated
        self.assertEqual(ms3[0]["primary"]["id"], cs["tight"]["id"]); self.assertEqual(ms3[0]["strength"], 0)
        self.assertTrue(ms3[0]["primary"]["critic_note"].startswith("critic: pace FAIL"))

    def test_stage4_rejected_or_ineligible_never_shipped(self):
        cs = {c["variant"]: c for c in self.f["candidates"] if c["scene_id"] == 1 and c["type"] == "comedy"}
        s = next(s for s in S5.S4.active_run(self.f)["candidates"] if s["id"] == cs["tight"]["id"]); s["status"] = "rejected"
        ms = self.with_records({cs["tight"]["id"]: rec("PASS", 3)})
        self.assertIsNone(ms[0]["primary"]); self.assertIn("Stage 4 status rejected", ms[0]["variants"][0]["not_shipped_why"])


class FakeText:
    """The text call: answers for the moment ids in the prompt; the first answer can be made invalid."""
    def __init__(self, bad_first=False):
        self.calls, self.bad_first = [], bad_first

    def generate_json(self, role, spec, system, prompt, schema, meta=None, want_raw=False, media=None):
        self.calls.append({"role": role, "system": system, "prompt": prompt, "media": media})
        ids = re.search(r"# CHOSEN MOMENTS \(\d+\): ([^\n]+)", prompt).group(1).split(", ")
        out = []
        for i in ids:
            blk = prompt[prompt.index(f"## MOMENT {i} "):]
            cta = re.search(r"cta_target (\w+)", blk).group(1)
            fan = re.search(r"fan_clip (\w+)", blk).group(1) == "true"
            out.append({"moment_id": i, "q9_vs_dynamic": "agrees", "q9_note": "mother and son, as in the scene",
                        "hook_text": "Delivery boy vs customer, 12 AM" if not (self.bad_first and len(self.calls) == 1) else "one two three four five six seven eight nine ten",
                        "caption": "Amma tho job news 😂", "title": "Job vachindi amma", "hashtags": ["#telugu", "#comedy", "#webseries"],
                        "context_line": "3 years jobless engineer" if fan else None, "cta_target": cta, "people_in_text": []})
        obj = {"moments": out}
        raw = SimpleNamespace(text=json.dumps(obj, ensure_ascii=False), input_tokens=40000, output_tokens=2400, thought_tokens=100, cached_tokens=0)
        return (obj, raw) if want_raw else obj


def facts_after_5():
    f, x = facts_after_4()
    sel = S5.select_clips(f, CFG); cands = {c["id"]: c for c in f["candidates"]}
    answers = {}
    for s_ in sel:
        c = cands[s_["id"]]; exp = S5.expected_moment(f, c)
        answers[c["id"]] = critic_answer(second=1.0, kind="other") if c["type"] == "hook" else critic_answer(second=(exp["clip_start"] or 0) + 0.5)
    out, _, _, _ = run5(f, answers)
    return out, x


def run6(f, client=None, cfg=None):
    cfg = copy.deepcopy(cfg or CFG)
    cfg["stage6"]["rules"]["recurring_cast_share"] = None          # the synthetic series has no face bank
    from tests.v2.test_stage3 import KEYS, make_inputs, transcript
    inputs = make_inputs(transcript=transcript(), keys=KEYS)
    fm = FakeMedia()
    shrunk = []

    def shrinker(src, dst, h, crf):
        Path(dst).parent.mkdir(parents=True, exist_ok=True); Path(dst).write_bytes(b"small"); shrunk.append((Path(src).name, h, crf)); return Path(dst)

    td = tempfile.mkdtemp()
    inputs.paths = SimpleNamespace(run_dir=Path(td), mp4=Path(td) / "ep.mp4", wav=Path(td) / "a.wav", title=lambda: "x")
    inputs.shots = {"cuts": [5.0, 20.0, 32.4]}
    (Path(td) / "prefix_stage2.txt").write_text("PREFIX BYTES\n", encoding="utf-8")
    cfg["paths"]["runs_log"] = str(Path(td) / "runs.csv")
    client = client or FakeText()
    out = S6.run(inputs, f, cfg, client=client, renderer=fm.renderer, shrinker=shrinker)
    validate_facts(out)
    return out, client, fm, shrunk, Path(td)


class TestRunAndPackage(unittest.TestCase):
    def test_refuses_without_matching_stage5(self):
        f, _ = facts_after_4()
        with self.assertRaises(RuntimeError):
            S6.check_inputs(f)                                          # no critic yet

    def test_text_call_retry_package_and_report(self):
        f, x = facts_after_5()
        out, client, fm, shrunk, td = run6(f, FakeText(bad_first=True))
        fin = out["final"]
        self.assertEqual(len(client.calls), 2)                              # the 10-word hook_text was rejected once
        self.assertIn("hook_text has 10 words", client.calls[1]["prompt"])
        self.assertEqual(client.calls[0]["media"], [{"text": "PREFIX BYTES\n"}])     # the cached Stage 2 prefix first
        self.assertTrue(fin["main"])
        for m in fin["main"] + fin["bonus"]:
            self.assertTrue(m["hook_text"] and m["caption"] and len(m["hashtags"]) == 3)
            self.assertIn(m["cta_target"], ("episode", "series"))
            self.assertTrue(m["crop_hint"]["segments"]); self.assertIn("follow", m["crop_hint"]["segments"][0])
            self.assertNotEqual(m["primary"]["verdict"], "FAIL"); self.assertTrue(all(a["verdict"] != "FAIL" for a in m["alternates"]))
        pkg = td / "final"
        self.assertTrue((pkg / "final.json").exists() and (pkg / "rejection_report.md").exists() and (pkg / "review.html").exists())
        tr = (pkg / "transcripts.md").read_text(encoding="utf-8")
        self.assertEqual(tr.count("### "), len(fin["main"]) + len(fin["bonus"]))                    # one section per selected clip
        self.assertIn("| clip | episode | speaker | line |", tr)
        page = (pkg / "review.html").read_text(encoding="utf-8")
        self.assertIn("data:video/mp4;base64,", page)
        self.assertIsNone(re.search(r"(src|href)=\"https?://", page))       # no external resources
        self.assertIn("Rejection report", page)
        shipped = {v["id"] for m in fin["main"] + fin["bonus"] for v in [m["primary"]] + m["alternates"]}
        picked = {pk["variant"]["id"] for pk in fin["creator_picks"]}
        self.assertEqual({p.stem for p in (pkg / "clips").glob("*.mp4")}, shipped | picked)
        self.assertEqual({r[0] for r in fm.rendered}, shipped | picked)   # nothing was rendered yet in the temp dir: all rendered now
        self.assertEqual(out["stages"]["6"]["summary"]["main"], len(fin["main"]))
        self.assertGreater(out["stages"]["6"]["usage"]["cost_inr"], 0)
        self.assertIn("# Rejection report", fin["rejection_report_md"])
        dbg = json.loads((td / "stage6_debug.json").read_text(encoding="utf-8"))
        self.assertEqual(len(dbg["attempts"]), 2)

    def test_validation_rules(self):
        f, _ = facts_after_5()
        sel = S6.plan_set(f, CFG)
        chosen = sel["main"] + sel["bonus"]
        good = FakeText().generate_json("r", None, "", S6.text_prompt(f, chosen, C6), {}, want_raw=False)
        self.assertEqual(S6.validate_text(good, chosen, C6), [])
        bad = copy.deepcopy(good); e = bad["moments"][0]
        e.update({"caption": "x" * 151, "title": "t" * 61, "hashtags": ["#a", "b"], "cta_target": "series" if e["cta_target"] == "episode" else "episode",
                  "hook_text": "అమ్మ job", "context_line": "ctx" if not chosen[0]["fan_clip"] else None})
        errs = " | ".join(S6.validate_text(bad, chosen, C6))
        for frag in ("caption has 151", "title has 61", "hashtags must be exactly 3", "cta_target must be", "Latin letters", "context_line"):
            self.assertIn(frag, errs)

    def test_rejection_report_explains_in_plain_language(self):
        ms = [mom("a", 1, combined=9, start=0), mom("b", 1, combined=8, start=500)]
        fail = mom("z", 7, start=900)
        fail["primary"] = None
        fail["variants"] = [{"id": "C7a", "type": "comedy", "variant": "extended", "final_duration": 106, "verdict": "FAIL", "n_pass": 0, "n_runs": 3, "strength": 0,
                             "not_shipped_why": "critic verdict FAIL (0 of 3 runs passed)", "located_s": [27, 44, 44],
                             "best": [{"what": "10 kaadu 15 lakhs"}] * 3, "expected": {"clip_start": 100.8, "text": "ఇంజనీర్లు"},
                             "swipe": [[{"from_s": 65, "to_s": 85, "why": "the dad rambling"}]] * 3, "missing": [{"missing": "none"}] * 3}]
        sel = S6.select_set(ms + [fail], rules(require_types=[], series_pull={}, recurring_cast_share=None, max_per_scene=1), 1500.0)
        sel["moments"] = ms + [fail]
        for i, m in enumerate(sel["main"]):
            m["id"] = f"M{i + 1}"
        md = S6.rejection_report(sel, C6, "Ep")
        self.assertIn("another moment from the same scene ranked higher", md)
        self.assertIn("Best moment for the three viewers: 0:27, 0:44, 0:44", md)
        self.assertIn("Where they would swipe away: 1:05-1:25", md)
        self.assertIn("1:40", md)                                          # the payoff the cut was built on


class TestHookRuleAndPicks(unittest.TestCase):
    def test_hook_text_may_not_quote_a_line_inside_the_clip(self):
        f, x = facts_after_5()
        sel = S6.plan_set(f, CFG)
        m, inside = next((m, r) for m in sel["main"] + sel["bonus"] for r in f["lines"] if r["kind"] == "speech"
                         and any(a <= r["start"] and r["end"] <= b for a, b in m["primary"]["segments"]) and len(S6.TM.content_tokens(r["text"])) >= 2)
        quote = S6.TM.translit(inside["text"]).replace(".", "")
        self.assertIsNotNone(S6.hook_quotes_clip(quote, f, m["primary"]))
        self.assertIsNone(S6.hook_quotes_clip("Delivery boy vs customer, 12 AM", f, m["primary"]))
        chosen = sel["main"] + sel["bonus"]
        ans = FakeText().generate_json("r", None, "", S6.text_prompt(f, chosen, C6), {}, want_raw=False)
        next(e for e in ans["moments"] if e["moment_id"] == m["id"])["hook_text"] = quote
        errs = S6.validate_text(ans, chosen, C6, f)
        self.assertTrue(any("quotes a line spoken inside the clip" in e for e in errs), errs)
        self.assertFalse(any("quotes" in e for e in S6.validate_text(ans, chosen, C6)))       # without facts: not checked

    def test_creator_picks_top_rejected_by_stage4_marked_promote(self):
        ms = [mom("a", 1, combined=9, start=0), mom("b", 1, combined=8.5, start=500), mom("c", 2, combined=7, start=700),
              mom("d", 3, combined=6, start=1000), mom("e", 4, combined=5, start=1200)]
        loop_dup = mom("l", 1, kind="loop", type_="loop", combined=9.9, start=2, dur=8, payoff="Pa")
        ms[0]["payoff_line"] = "Pa"
        fail = mom("z", 7, combined=0, start=1300); fail["primary"] = None
        fail["variants"] = [dict(ms[2]["primary"], id="Z1", verdict="FAIL", n_pass=0, stage4={"combined": 8.8, "fan_clip": False, "series_pull": 5},
                                 located_s=[3, 3, 3], best=[{"what": "x"}] * 3, expected={"clip_start": 20, "text": "t"},
                                 swipe=[[{"from_s": 2, "to_s": 14, "why": "walking"}]] * 3, missing=[{"missing": "none"}] * 3, not_shipped_why="critic verdict FAIL")]
        c6 = rules(require_types=[], series_pull={}, recurring_cast_share=None, spread={}, max_per_scene=1)
        c6["main_slots"] = 2
        sel = S6.select_set(ms + [loop_dup, fail], c6, 1500.0)
        sel["moments"] = ms + [loop_dup, fail]
        for i, m in enumerate(sel["main"]):
            m["id"] = f"M{i + 1}"
        picks = S6.creator_picks(sel, c6)
        self.assertEqual([p["key"] for p in picks], ["z", "b", "d"])                    # by Stage 4 combined; the duplicate loop is not a pick
        self.assertTrue(all(p["promote"] is False for p in picks))
        self.assertIn("no version passed", picks[0]["reason"]); self.assertIn("same scene", picks[1]["reason"])
        self.assertTrue(any("swipe" in x for x in picks[0]["critic_story"]))

    def test_package_has_picks_and_regenerate_changes_only_the_named_moment(self):
        f, x = facts_after_5()
        out, client, fm, shrunk, td = run6(f)
        fin = out["final"]
        self.assertTrue(fin["creator_picks"]); self.assertLessEqual(len(fin["creator_picks"]), 3)
        page = (td / "final" / "review.html").read_text(encoding="utf-8")
        self.assertIn("Creator's picks", page); self.assertIn('class="promote"', page); self.assertIn("Copy choices", page)
        for pk in fin["creator_picks"]:
            self.assertTrue((td / "final" / "clips" / f"{pk['variant']['id']}.mp4").exists())

        class NewWords(FakeText):
            def generate_json(self, *a, **k):
                obj, raw = super().generate_json(*a, **k)
                for e in obj["moments"]:
                    e["hook_text"] = "Ee birthday ki amma reaction enti?"
                return obj, raw

        target = fin["main"][0]["id"]
        from tests.v2.test_stage3 import KEYS, make_inputs, transcript
        inputs = make_inputs(transcript=transcript(), keys=KEYS)
        inputs.paths = SimpleNamespace(run_dir=td, mp4=td / "ep.mp4", wav=None, title=lambda: "x")
        cfg = copy.deepcopy(CFG); cfg["paths"]["runs_log"] = str(td / "runs.csv")
        nw = NewWords()
        out2 = S6.regenerate(inputs, out, cfg, [target], client=nw, renderer=fm.renderer, shrinker=lambda s_, d, h, c: (Path(d).parent.mkdir(parents=True, exist_ok=True), Path(d).write_bytes(b"s"), Path(d))[2])
        validate_facts(out2)
        self.assertEqual(len(nw.calls), 1); self.assertIn(f"# CHOSEN MOMENTS (1): {target}", nw.calls[0]["prompt"])
        m2 = next(m for m in out2["final"]["main"] if m["id"] == target)
        self.assertEqual(m2["hook_text"], "Ee birthday ki amma reaction enti?"); self.assertEqual(m2["previous_words"]["hook_text"], fin["main"][0]["hook_text"])
        others = [m for m in out2["final"]["main"] if m["id"] != target]
        self.assertTrue(all(m["hook_text"] == next(o for o in fin["main"] if o["id"] == m["id"])["hook_text"] for m in others))
        self.assertEqual(out2["final"]["regenerated"][-1]["moments"], [target])
        page2 = (td / "final" / "review.html").read_text(encoding="utf-8")
        self.assertIn("Ee birthday ki amma reaction enti?", page2)
        self.assertIn('class="dyn"', page2); self.assertIn("the people in this clip are:", page2)       # the people line under every hook
        self.assertTrue(all(m.get("people_line") for m in out2["final"]["main"] + out2["final"]["bonus"]))


class TestPeople(unittest.TestCase):
    """founder, 2026-10-29: the people in the clip and their relationships come from the data; the text call reports
    who its words refer to, and code checks that against the roles, the scene line and the cast list."""
    PEOPLE = [{"name": "Meghana", "role": "Surya's younger sister", "data": []}, {"name": "Surya", "role": "protagonist", "data": []}]

    def people(self):
        from selection_v2 import people as PP
        ps = copy.deepcopy(self.PEOPLE)
        rel = "Surya and Meghana are affectionate siblings."
        for p_ in ps:
            p_["data"] = [dict(t, source="role") for t in PP.kin_terms(p_["role"])] + \
                         [dict(t, source="dynamic", gender=None, age=None) for t in PP.kin_terms(rel) if p_["name"] in rel]
        return PP, ps, rel

    def test_kin_terms(self):
        from selection_v2 import people as PP
        self.assertEqual([(t["word"], t["cat"], t["gender"], t["age"]) for t in PP.kin_terms("Surya's younger sister and her amma")],
                         [("sister", "sibling", "f", "younger"), ("amma", "parent", "f", None)])
        self.assertEqual(PP.kin_terms("Akka chellella birthday")[0]["age"], "elder")

    def test_people_in_text_checked_against_roles_and_cast(self):
        PP, ps, rel = self.people()
        cast = {"meghana", "surya", "anjali"}
        self.assertEqual(PP.check_people_in_text([{"name": "Meghana", "relationship": "Surya's younger sister (chelli)"}], ps, cast, rel), [])
        self.assertTrue(any("younger one" in e for e in PP.check_people_in_text([{"name": "Meghana", "relationship": "akka"}], ps, cast, rel)))
        self.assertTrue(any("female" in e for e in PP.check_people_in_text([{"name": "Meghana", "relationship": "brother"}], ps, cast, rel)))
        self.assertTrue(any("does not speak in this clip" in e for e in PP.check_people_in_text([{"name": "Anjali", "relationship": "girlfriend"}], ps, cast, rel)))
        self.assertTrue(any("neither in this clip nor in the cast" in e for e in PP.check_people_in_text([{"name": "Ramesh", "relationship": "friend"}], ps, cast, rel)))
        self.assertEqual(PP.check_people_in_text([{"name": "Surya", "relationship": "brother"}], ps, cast, rel), [])     # no data on his gender: not flagged

    def test_cast_list_gender_used_by_people_in_text(self):
        """founder, 2026-10-29: an optional gender on cast entries ({aka, gender}) rules out the other gender's words."""
        from selection_v2 import people as PP
        cast_cfg = {"series": {"s": {"cast": {"Surya": {"aka": ["సూర్య", "Surya"], "gender": "m"}, "Meghana": ["మేఘన", "Meghana"]}}}}
        facts = {"cast_list": {"series_key": "s"}}
        g = PP.cast_genders(facts, cast_cfg)
        self.assertEqual(g, {"surya": "m", "సూర్య": "m"})                                       # list entries carry no gender
        self.assertEqual(PP.gender_of("Surya", g), "m"); self.assertIsNone(PP.gender_of("Meghana", g))
        self.assertEqual(PP.gender_of("V Surya", g), "m")                                       # a word of the name
        _, ps, rel = self.people()
        for p_ in ps:
            p_["gender"] = PP.gender_of(p_["name"], g)
        errs = PP.check_people_in_text([{"name": "Surya", "relationship": "elder sister (akka)"}], ps, {"surya", "meghana"}, rel)
        self.assertTrue(errs and all("male in the cast list" in e for e in errs), errs)
        self.assertEqual(PP.check_people_in_text([{"name": "Surya", "relationship": "brother (annayya)"}], ps, {"surya", "meghana"}, rel), [])

    def test_text_kin_words_need_someone_they_fit(self):
        PP, ps, rel = self.people()
        self.assertTrue(PP.check_text_kin("hook_text", "Chelli birthday plans", ps, rel) == [])
        self.assertTrue(PP.check_text_kin("hook_text", "Amma ki surprise", ps, "") == [])                   # nobody holds a parent role: nothing to contradict
        mom = [{"name": "Mother", "role": "their mother", "data": [{"word": "mother", "cat": "parent", "gender": "f", "age": None, "source": "role"}]}]
        self.assertTrue(PP.check_text_kin("caption", "Nanna isthe lekka", mom, ""))                        # the only parent is the mother
        self.assertEqual(PP.check_text_kin("caption", "Nanna isthe lekka", mom, "an anxious father who cares"), [])   # the father is in the scene line

    def test_validation_uses_people_and_the_prompt_lists_them(self):
        f, _ = facts_after_5()
        sel = S6.plan_set(f, CFG)
        chosen = sel["main"] + sel["bonus"]
        prompt = S6.text_prompt(f, chosen, C6)
        self.assertIn("people: the people in this clip are:", prompt)
        ans = FakeText().generate_json("r", None, "", prompt, {}, want_raw=False)
        self.assertEqual(S6.validate_text(ans, chosen, C6, f), [])
        ans["moments"][0]["people_in_text"] = [{"name": "Nobody Here", "relationship": "friend"}]
        self.assertTrue(any("neither in this clip nor in the cast list" in e for e in S6.validate_text(ans, chosen, C6, f)))


class TestPrimaryRule(unittest.TestCase):
    def v(self, i, dur, comb, n_pass):
        return {"id": i, "final_duration": dur, "stage4": {"combined": comb}, "n_pass": n_pass, "strength": 3 if n_pass == 3 else 2}

    def test_score_window_then_closest_to_typical(self):
        """founder, 2026-10-30: versions within 0.5 of the best combined; among them the one closest to the typical length."""
        vs = [self.v("a", 30, 9.0, 3), self.v("b", 65, 7.0, 3), self.v("c", 60, 8.7, 2), self.v("d", 64, 8.6, 1)]
        self.assertEqual(S6.pick_primary(vs, C6, 65.0)["id"], "d")          # a, c, d are within 0.5; d is closest; b is too weak
        self.assertEqual(S6.pick_primary(vs, dict(C6, primary={"score_window": 0.2}), 65.0)["id"], "a")   # only a, c ... c is 0.3 below
        self.assertEqual(S6.pick_primary([self.v("x", 60, 8.0, 2), self.v("y", 70, 8.0, 3)], C6, 65.0)["id"], "y")   # tie on distance: passes
        self.assertIsNone(S6.pick_primary([], C6, 65.0))

    def test_typical_length_from_scripted_channel_shorts_only(self):
        import tempfile
        cfg = {"stage6": C6, "paths": {"runs_dir": "runs"}}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "data" / "channel_shorts").mkdir(parents=True)
            for vid, ct, durs in (("S1", "scripted_series", [50, 60, 100]), ("P1", "podcast", [20, 20, 20])):
                (root / "runs" / vid).mkdir(parents=True)
                (root / "runs" / vid / "episode_facts.json").write_text(json.dumps({"episode_pass": {"content_type": ct}}))
                (root / "data" / "channel_shorts" / f"{vid}.json").write_text(json.dumps({"episode_id": vid, "accepted": [{"channel": "Chan ", "duration": d} for d in durs]}))
            t = S6.typical_length({"episode": {"channel": "chan"}}, cfg, root)
            self.assertEqual((t["median_s"], t["target_s"], t["n"]), (60.0, 60.0, 3))      # the podcast's 20 s Shorts never count
            t2 = S6.typical_length({"episode": {"channel": "other"}}, cfg, root)
            self.assertEqual((t2["target_s"], t2["n"]), (65.0, 0))
            (root / "data" / "channel_shorts" / "S1.json").write_text(json.dumps({"episode_id": "S1", "accepted": [{"channel": "Chan", "duration": 200}]}))
            self.assertEqual(S6.typical_length({"episode": {"channel": "chan"}}, cfg, root)["target_s"], 200.0)  # founder, 2026-10-30: no band clamp


class TestGoldenRecall(unittest.TestCase):
    def test_full_partial_missed(self):
        """founder, 2026-10-30: golden recall in the main set, full or partial."""
        f, x = facts_after_4()
        f["episode"]["video_id"] = "EPX"
        gs = {"episodes": {"EPX": {"moments": [
            {"id": "m01", "start": 1.0, "end": 36.0, "label": "whole scene", "sources": [{"lines": [x["X1"], x["X11"]]}]},
            {"id": "m02", "start": 44.0, "end": 60.0, "label": "shop", "sources": [{"lines": [x["Y1"], x["Y9"]]}]},
            {"id": "m03", "start": 20.0, "end": 36.0, "label": "end part", "sources": [{"lines": [x["X5"], x["X11"]]}]}]}}}
        full = {"id": "M1", "primary": {"id": "Ca", "segments": [[0.7, 36.2]]}}
        piece = {"id": "M2", "primary": {"id": "Cb", "segments": [[52.0, 55.2]]}}
        rec = {r["id"]: r for r in acceptance.golden_recall(f, [full, piece], gs)}
        self.assertEqual(rec["m01"]["status"], "full"); self.assertEqual(rec["m02"]["status"], "partial"); self.assertEqual(rec["m03"]["status"], "full")
        rec2 = {r["id"]: r for r in acceptance.golden_recall(f, [piece], gs)}
        self.assertEqual(rec2["m01"]["status"], "missed")
        row = {}
        f["final"] = {"main": [full, piece]}
        acceptance._eval_stage6(f, {"kind": "final_golden_recall", "min": 3, "_golden_set": gs}, row)
        self.assertEqual(row["status"], "agree", row)
        acceptance._eval_stage6(f, {"kind": "final_golden_recall", "min": 4, "_golden_set": gs}, row)
        self.assertEqual(row["status"], "disagree")


class TestAcceptance6(unittest.TestCase):
    def test_kinds(self):
        f, x = facts_after_5()
        out, _, _, _, _ = run6(f)
        fin = out["final"]
        m0 = fin["main"][0]
        checks = [{"kind": "final_set_shape", "main": len(fin["main"]), "bonus_max": 2, "primary_max_s": 60},
                  {"kind": "final_rules", "series_pull_min": 0, "count": 1, "max_per_scene": 2},
                  {"kind": "final_moment", "in": "main", "payoff_line": m0["payoff_line"], "moment_kind": m0["kind"]},
                  {"kind": "final_any_of", "in": "main", "options": [{"type": "nope"}, {"payoff_line": m0["payoff_line"]}]},
                  {"kind": "final_hook_text_mentions", "in": "main", "payoff_line": m0["payoff_line"], "words": ["delivery"]},
                  {"kind": "final_hook_text_mentions", "in": "main", "payoff_line": m0["payoff_line"], "words": ["zebra"]},
                  {"kind": "final_set_shape", "main": 99, "bonus_max": 2, "primary_max_s": 60},
                  {"kind": "final_report_mentions", "scenes": ["0:10"], "words_any": ["zzz-never"]}]
        rows = acceptance.evaluate_1b(out, {"stage": "6", "checks": checks})
        self.assertEqual([r["status"] for r in rows], ["agree", "agree", "agree", "agree", "agree", "disagree", "disagree", "disagree"], rows)
        none = acceptance.evaluate_1b(f, {"stage": "6", "checks": checks[:1]})
        self.assertEqual(none[0]["status"], "missing")


if __name__ == "__main__":
    unittest.main()
