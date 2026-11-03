"""Stage 4 (scoring and vetoes) with a mocked client: prompt prefix determinism, validation and retry, vetoes, the
standalone widening path, weights and the length rule, schema, the acceptance checker. No live calls."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from selection_v2 import acceptance, stage3_candidates as S3, stage4_scoring as S4
from selection_v2.config import load_config
from selection_v2.runner import validate_facts
from tests.v2.test_stage3 import CFG as CFG3, KEYS, facts_with_sheets, make_inputs, transcript

CFG = copy.deepcopy(load_config()); CFG["stage1a"]["context_window_s"] = 8
CFG["stage3"]["cold_open_min_before_payoff_s"] = 10


def facts_after_3(cfg=None):
    f, x = facts_with_sheets(cfg or CFG)
    out = S3.run(make_inputs(transcript=transcript(), keys=KEYS), f, cfg or CFG)
    out["speaker_map"]["non_story_confirmed"] = out["speaker_map"].get("non_story_confirmed", [])
    return out, x


def mark(n, ev="evidence"):
    return {"score": n, "evidence": ev}


def answer_for(cands, overrides=None):
    """A valid answer: 8s everywhere (payoff and open_question on every candidate), verdicts for twins; ``overrides`` = {id: {dim: score}}."""
    overrides = overrides or {}
    entries = []
    for c in cands:
        o = overrides.get(c["id"], {})
        e = {"id": c["id"], "hook": mark(o.get("hook", 8), "first line 'అమ్మా లేమ్మా'"), "standalone": mark(o.get("standalone", 8)),
             "pace": {"score": o.get("pace", 8), "evidence": "no dead stretch", "dead_stretches": []}, "delivery": mark(o.get("delivery", 8)),
             "series_pull": mark(o.get("series_pull", 8)), "fan_clip": bool(o.get("fan_clip", False)),
             "verdict_vs_sibling": ("the sibling is tighter" if S4.sibling_of(c) else None),
             "removed_needed": o.get("removed_needed", []), "kept_dead": o.get("kept_dead", [])}
        e["payoff"] = mark(o.get("payoff", 8), "'నువ్వు ఇవ్వాలిరా నాకు' lands")
        e["open_question"] = mark(o.get("open_question", 8), "ends on 'నువ్వు ఇవ్వాలిరా నాకు'")
        entries.append(e)
    return {"scene_id": cands[0]["scene_id"], "candidates": entries}


class FakeClient:
    def __init__(self, answers_by_scene, bad_first=None):
        self.answers = answers_by_scene       # scene_id -> answer (or list of answers for retries)
        self.calls = []
        self.ledger = SimpleNamespace(summary=lambda: {"usd": 0.0})

    def generate_json(self, role, spec, system, prompt, schema, meta=None, want_raw=False, media=None):
        self.calls.append({"role": role, "system": system, "prompt": prompt, "meta": meta, "media": media})
        a = self.answers[meta["scene"]]
        obj = a.pop(0) if isinstance(a, list) else a
        raw = SimpleNamespace(text=json.dumps(obj, ensure_ascii=False), input_tokens=30000, output_tokens=4000, thought_tokens=500, cached_tokens=20000)
        return (obj, raw) if want_raw else obj


class FakeMedia:
    def __init__(self):
        self.cuts, self.uploads, self.deleted = [], [], []

    def cutter(self, src, media_dir, sid, start, end, mode, max_h, log=None):
        self.cuts.append((sid, start, end, mode, max_h)); return Path(media_dir) / f"scene_{sid}.mp4"

    def uploader(self, path, mode):
        self.uploads.append(str(path)); return {"uri": f"files/fake{len(self.uploads)}", "name": f"files/fake{len(self.uploads)}", "mime_type": "video/mp4"}

    def deleter(self, up):
        self.deleted.append(up["name"]); return True


def run4(f, answers, cfg=None, scene_ids=None, media=None, fm=None, **kw):
    cfg = copy.deepcopy(cfg or CFG)
    inputs = make_inputs(transcript=transcript(), keys=KEYS)
    fm = fm or FakeMedia()
    with tempfile.TemporaryDirectory() as td:
        inputs.paths = SimpleNamespace(run_dir=Path(td), mp4=Path(td) / "ep.mp4", wav=Path(td) / "a.wav", title=lambda: "x")
        (Path(td) / "prefix_stage2.txt").write_text("PREFIX BYTES\n", encoding="utf-8")
        cfg["paths"]["runs_log"] = str(Path(td) / "runs.csv")
        client = FakeClient(answers)
        out = S4.run(inputs, f, cfg, client=client, scene_ids=scene_ids, media=media, cutter=fm.cutter, uploader=fm.uploader, deleter=fm.deleter, **kw)
        validate_facts(out)  # the runner validates the written facts; a stage key the episode schema rejects fails here, not in a billed run
        dbg = json.loads((Path(td) / "stage4_debug.json").read_text(encoding="utf-8"))
        return out, client, dbg


def by_scene(f):
    return {sh["scene_id"]: cs for sh, cs in S4.scenes_with_candidates(f)}


class TestPromptAndValidation(unittest.TestCase):
    def test_prefix_first_and_deterministic_scene_text(self):
        f, x = facts_after_3()
        sc = by_scene(f)
        answers = {sid: answer_for(cs) for sid, cs in sc.items()}
        out, client, dbg = run4(f, answers)
        self.assertEqual(client.calls[0]["media"], [{"text": "PREFIX BYTES\n"}])          # the cached prefix is the first part, bytes unchanged
        self.assertTrue(all(c["media"] == [{"text": "PREFIX BYTES\n"}] for c in client.calls))
        sheet = f["beat_sheets"][0]
        self.assertEqual(S4.scene_prompt(f, sheet, sc[1]), S4.scene_prompt(copy.deepcopy(f), copy.deepcopy(sheet), copy.deepcopy(sc[1])))
        p = client.calls[0]["prompt"]
        self.assertIn("# SCENE 1 BEAT SHEET", p); self.assertIn("## CANDIDATE C1a", p); self.assertIn("what remains (kept ranges with their lines):", p)
        self.assertIn("[cut: ", p)                                                              # the trimmed twin shows its cuts
        self.assertIn("shortened stretch 0:08.0-0:20.0", p)
        self.assertEqual(client.calls[0]["role"], "clip_scores")
        self.assertEqual(dbg["prefix_file"], "prefix_stage2.txt")

    def test_candidate_text_lists_what_remains(self):
        """founder, 2026-10-28: a trimmed candidate is described by its kept ranges with content; removed lines are not
        quoted, a [cut ...] marker between kept ranges says what is gone."""
        f, x = facts_after_3()
        tw = next(c for c in f["candidates"] if c["variant"] == "full_arc_trimmed")
        t = S4.candidate_text(f, tw)
        self.assertEqual(t.count("kept range "), len(tw["segments"]))
        self.assertNotIn(x["X6"] + " ", t)                                                   # the skipped line is not quoted
        self.assertIn(f"skipped line(s) {x['X6']}", t)                                       # but the cut marker names it
        self.assertIn("shortened stretch 0:08.0-0:20.0 (action_beat), its first 0.5 s kept", t)
        self.assertIn(f"{x['X9']} ", t)                                                      # the payoff line is kept and quoted
        self.assertNotIn("[removed", t)
        untr = next(c for c in f["candidates"] if c["variant"] == "full_arc")
        self.assertNotIn("[cut:", S4.candidate_text(f, untr))

    def test_validation_rules(self):
        f, x = facts_after_3()
        cs = by_scene(f)[1]
        good = answer_for(cs)
        self.assertEqual(S4.validate(good, cs), [])
        a = copy.deepcopy(good); a["candidates"].pop(0)
        self.assertTrue(any("missing candidate" in e for e in S4.validate(a, cs)))
        a = copy.deepcopy(good); a["candidates"][0]["id"] = "C9z"
        errs = S4.validate(a, cs); self.assertTrue(any("unknown" in e for e in errs) and any("missing" in e for e in errs))
        a = copy.deepcopy(good); del a["candidates"][0]["open_question"]                   # founder, 2026-10-29: on every candidate
        errs = S4.validate(a, cs); self.assertTrue(any("open_question is required" in x for x in errs))
        a = copy.deepcopy(good); a["candidates"][0]["hook"]["score"] = 11
        self.assertTrue(any("1-10" in e for e in S4.validate(a, cs)))
        tw = next(e for e in good["candidates"] if next(c for c in cs if c["id"] == e["id"]).get("untrimmed_sibling"))
        a = copy.deepcopy(good); next(z for z in a["candidates"] if z["id"] == tw["id"])["verdict_vs_sibling"] = None
        self.assertTrue(any("verdict_vs_sibling is required" in e for e in S4.validate(a, cs)))
        self.assertEqual(S4.schema_errors(good), [])
        bad = copy.deepcopy(good); bad["candidates"][0]["pace"].pop("dead_stretches")
        self.assertTrue(S4.schema_errors(bad))

    def test_retry_once_with_errors_then_fail(self):
        f, x = facts_after_3()
        sc = by_scene(f)
        bad = answer_for(sc[1]); bad["candidates"].pop()
        answers = {1: [bad, answer_for(sc[1])], 2: answer_for(sc[2])}
        out, client, dbg = run4(f, answers)
        self.assertEqual(len(client.calls), 3)
        self.assertIn("missing candidate", client.calls[1]["prompt"])                         # the retry shows the errors
        self.assertEqual(len(dbg["scenes"]["none:1"]["attempts"]), 2)
        answers = {1: [copy.deepcopy(bad), copy.deepcopy(bad)], 2: answer_for(sc[2])}
        with self.assertRaises(S4.ScoringError):
            run4(f, answers)

    def test_refuses_without_current_stage3(self):
        f, x = facts_after_3()
        g = copy.deepcopy(f); g["stages"]["3"]["version"] = "stage3/0.0"
        with self.assertRaises(RuntimeError):
            run4(g, {})
        g = copy.deepcopy(f); g.pop("candidates"); g["stages"].pop("3")
        with self.assertRaises(RuntimeError):
            run4(g, {})


class TestTracePrompts(unittest.TestCase):
    """founder, 2026-10-29: critic_trimmed siblings are scored text only in their own call; the taste block comes from
    the golden set of the same series and never shows the episode its own golden moments."""
    def test_critic_trimmed_scored_in_a_text_only_call(self):
        f, x = facts_after_3()
        base = next(c for c in f["candidates"] if c["scene_id"] == 1 and c["variant"] == "full_arc")
        ct = copy.deepcopy(base); ct.update({"id": "C1z", "variant": "critic_trimmed", "critic_trim_of": base["id"], "untrimmed_sibling": None, "trimmed_sibling": None})
        f["candidates"].append(ct)
        sc = by_scene(f)
        groups = S4.call_groups(sc[1], "video")
        self.assertEqual([m for m, _ in groups], ["video", "none"]); self.assertEqual([c["id"] for c in groups[1][1]], ["C1z"])
        self.assertEqual(S4.call_groups(sc[1], "none"), [("none", sc[1])])
        answers = {1: [answer_for([c for c in sc[1] if c["id"] != "C1z"]), answer_for([ct])], 2: answer_for(sc[2])}
        out, client, dbg = run4(f, answers, media="video")
        metas = [c["meta"]["media"] for c in client.calls if c["meta"]["scene"] == 1]
        self.assertEqual(metas, ["video", "none"])
        self.assertEqual(len(client.calls[1]["media"]), 1)                                   # the text call carries the prefix only
        self.assertIn("critic-trimmed version of " + base["id"], client.calls[1]["prompt"])
        row = next(r for r in out["scores"]["per_scene"] if r["scene_id"] == 1)
        self.assertEqual(row["media"], "video+none")
        self.assertIn("C1z", {s["id"] for s in out["scores"]["candidates"]})
        est = S4.estimate(f, CFG, media="video")
        self.assertEqual([r["media"] for r in est["scenes"] if r["scene_id"] == 1], ["video", "none"])

    def test_text_only_run_sends_no_video(self):
        f, x = facts_after_3()
        sc = by_scene(f)
        out, client, dbg = run4(f, {sid: answer_for(cs) for sid, cs in sc.items()}, media="video", text_only=True)
        self.assertTrue(all(c["meta"]["media"] == "none" and len(c["media"]) == 1 for c in client.calls))
        self.assertTrue(out["scores"]["run_key"].startswith("video@"))
        self.assertTrue(all(r["media"] == "none" for r in S4.estimate(f, CFG, media="video", text_only=True)["scenes"]))

    def test_exclude_ids_leaves_candidates_unscored(self):
        f, x = facts_after_3()
        sc = by_scene(f)
        out, _, _ = run4(f, {sid: answer_for(cs) for sid, cs in sc.items()})
        g = copy.deepcopy(out)
        new = copy.deepcopy(next(c for c in g["candidates"] if c["scene_id"] == 1 and c["variant"] == "full_arc"))
        new.update({"id": "C1z", "variant": "critic_trimmed", "critic_trim_of": new["id"], "untrimmed_sibling": None, "trimmed_sibling": None})
        g["candidates"].append(new); g["stages"]["3"]["generated_at"] = "later"
        self.assertEqual(S4.estimate(g, CFG, changed_only=True, exclude_ids=["C1z"])["scenes"], [])
        with self.assertRaises(RuntimeError):
            run4(g, {}, changed_only=True, exclude_ids=["C1z"])                                  # nothing left to score

    def test_taste_block_same_series_without_the_episode_itself(self):
        f, x = facts_after_3()
        f["episode"].update({"video_id": "EP1", "series": "Demo Series", "series_key": "demo"})
        gs = {"episodes": {
            "EP1": {"series": "Demo Series", "moments": [{"start": 0.7, "end": 35.5, "type": "comedy", "label": "own", "tier": "silver",
                                                         "sources": [{"kind": "human", "confidence": "definite"}]}]},
            "EP0": {"series": "demo", "moments": [{"start": 44.0, "end": 50.0, "type": "comedy", "label": "posted one", "tier": "gold",
                                                  "sources": [{"kind": "channel_short"}]},
                                                 {"start": 52.0, "end": 55.0, "type": "comedy", "label": "maybe", "tier": "silver",
                                                  "sources": [{"kind": "human", "confidence": "maybe"}]}]},
            "XX": {"series": "Other", "moments": [{"start": 1, "end": 5, "type": "comedy", "label": "other", "tier": "gold", "sources": [{"kind": "channel_short"}]}]}}}
        orig = S4._episode_lines
        S4._episode_lines = lambda vid, cfg, facts=None: [(r["start"], r["end"], "N", r["text"]) for r in f["lines"] if r["kind"] == "speech"]
        try:
            ex = S4.taste_examples(f, CFG, gs)
            self.assertEqual([e["label"] for e in ex], ["posted one"])                       # same series, posted; not the episode itself, not a maybe
            self.assertIn("స్వామి ఏం చేస్తున్నావ్", ex[0]["transcript"])
            block = S4.taste_block(f, CFG, gs)
            self.assertTrue(block.startswith("# " + S4.TASTE_TITLE))
            cfg = copy.deepcopy(CFG); cfg["stage4"]["taste"]["exclude_same_episode"] = False
            self.assertEqual(len(S4.taste_examples(f, cfg, gs)), 2)
            self.assertIn("(none yet for this series)", S4.taste_block(f, CFG, {"episodes": {}}))
            self.assertIn(S4.TASTE_TITLE, S4.scene_prompt(f, f["beat_sheets"][0], by_scene(f)[1], "none", block))
        finally:
            S4._episode_lines = orig


class TestTasteFromChannelShorts(unittest.TestCase):
    def test_other_episodes_of_the_series_only(self):
        """founder, 2026-10-30: Ep 1's taste block = the Shorts matched to the series' OTHER episodes, never its own."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "data" / "channel_shorts").mkdir(parents=True)
            cfg = copy.deepcopy(CFG); cfg["paths"]["runs_dir"] = "runs"
            for vid, key, ct in (("E1", "s", "scripted_series"), ("E2", "s", "scripted_series"), ("O1", "other", "scripted_series")):
                (root / "runs" / vid).mkdir(parents=True)
                (root / "runs" / vid / "episode_facts.json").write_text(json.dumps({"episode": {"video_id": vid, "series_key": key}, "episode_pass": {"content_type": ct},
                    "lines": [{"id": "L0001", "kind": "speech", "start": 1.0, "end": 2.0, "text": f"line of {vid}", "speaker_key": "A", "speaker_name": "A"}]}))
                (root / "data" / "channel_shorts" / f"{vid}.json").write_text(json.dumps({"episode_id": vid, "accepted": [
                    {"short_id": vid + "s", "views": 5, "title": vid, "segments": [{"episode_start": 0.5, "episode_end": 2.5, "short_start": 0}]}]}))
            orig = S4._episode_lines
            S4._episode_lines = lambda vid, cfg, facts=None: [(1.0, 2.0, "A", f"line of {vid}")]
            try:
                ex = S4.taste_from_channel_shorts({"episode": {"video_id": "E1", "series_key": "s"}}, cfg, root)
            finally:
                S4._episode_lines = orig
            self.assertEqual([e["short_id"] for e in ex], ["E2s"]); self.assertIn("line of E2", ex[0]["transcript"])


class TestVetoesAndCombination(unittest.TestCase):
    def test_vetoes_and_statuses(self):
        f, x = facts_after_3()
        sc = by_scene(f)
        full = next(c for c in sc[1] if c["variant"] == "full_arc"); tw = next(c for c in sc[1] if c["variant"] == "full_arc_trimmed")
        tight = next(c for c in sc[1] if c["variant"] == "tight"); hook = next(c for c in sc[1] if c["variant"] == "open_question")
        loop = next(c for c in sc[1] if c["type"] == "loop")
        ov = {tight["id"]: {"payoff": 3}, hook["id"]: {"open_question": 4, "payoff": 2}, loop["id"]: {"pace": 3},
              tw["id"]: {"standalone": 4}, full["id"]: {"standalone": 8}}
        answers = {1: answer_for(sc[1], ov), 2: answer_for(sc[2], {c["id"]: {"standalone": 3} for c in sc[2]})}
        out, client, dbg = run4(f, answers)
        s = {z["id"]: z for z in out["scores"]["candidates"]}
        self.assertEqual(s[tight["id"]]["status"], "rejected"); self.assertEqual(s[tight["id"]]["vetoes"][0]["rule"], "payoff<5")
        self.assertEqual(s[hook["id"]]["status"], "rejected"); self.assertEqual([v["rule"] for v in s[hook["id"]]["vetoes"]], ["open_question<5"])   # its payoff is not vetoed
        self.assertEqual(s[loop["id"]]["status"], "rejected"); self.assertEqual(s[loop["id"]]["vetoes"][0]["rule"], "pace<4")
        self.assertEqual(s[tw["id"]]["status"], "widened"); self.assertEqual(s[tw["id"]]["widened_to"], full["id"])   # the untrimmed twin scored >= 5
        self.assertEqual(s[full["id"]]["status"], "pass"); self.assertIn(tw["id"], s[full["id"]]["flags"]["widened_from"])
        for c in sc[2]:                                                                            # nobody wider scored >= 5: fan clip only
            self.assertEqual(s[c["id"]]["status"], "fan_clip_only")
            self.assertIsNone(s[c["id"]]["widened_to"])
        self.assertEqual(out["stages"]["4"]["usage"]["cost_inr"], sum(x["cost_inr"] for x in out["scores"]["per_scene"]))
        self.assertEqual(out["scores"]["statuses"]["rejected"], 3)

    def test_weights_and_length_rule(self):
        cfg4 = CFG["stage4"]
        c = {"type": "comedy", "final_duration": 40.0}
        w = S4.weights_for(c, cfg4); self.assertAlmostEqual(w["payoff"], 0.30); self.assertAlmostEqual(w["hook"], 0.20)
        c = {"type": "comedy", "final_duration": 110.0}                      # 65 s above 45 -> two 30 s steps -> 0.10 moved
        w = S4.weights_for(c, cfg4); self.assertAlmostEqual(w["payoff"], 0.20); self.assertAlmostEqual(w["hook"], 0.30)
        c = {"type": "comedy", "variant": "open_question", "final_duration": 80.0}   # one step: open_question 0.35 -> 0.30, hook 0.25 -> 0.30
        w = S4.weights_for(c, cfg4); self.assertAlmostEqual(w["open_question"], 0.30); self.assertAlmostEqual(w["hook"], 0.30)
        self.assertNotIn("payoff", w)
        c = {"type": "comedy", "final_duration": 400.0}                      # capped at max_shift 0.20
        w = S4.weights_for(c, cfg4); self.assertAlmostEqual(w["payoff"], 0.10); self.assertAlmostEqual(w["hook"], 0.40)
        sc = {"hook": 6, "payoff": 9, "standalone": 8, "pace": 7, "delivery": 8, "series_pull": 5}
        w = S4.weights_for({"type": "comedy", "final_duration": 30.0}, cfg4)
        self.assertAlmostEqual(S4.combined_score(sc, w), round(0.30 * 9 + 0.20 * 6 + 0.20 * 8 + 0.10 * 7 + 0.10 * 8 + 0.10 * 5, 2))
        w = S4.weights_for({"type": "loop", "final_duration": 10.0}, cfg4)
        self.assertAlmostEqual(S4.combined_score(sc, w), round((0.35 * 9 + 0.30 * 6 + 0.25 * 8 + 0.10 * 7) / 1.0, 2))

    def test_ineligible_still_scored(self):
        cfg = copy.deepcopy(CFG); cfg["stage3"]["platform_max_s"] = 20
        f, x = facts_after_3(cfg)
        sc = by_scene(f)
        out, client, dbg = run4(f, {sid: answer_for(cs) for sid, cs in sc.items()}, cfg)
        full = next(c for c in sc[1] if c["variant"] == "full_arc")
        s = next(z for z in out["scores"]["candidates"] if z["id"] == full["id"])
        self.assertFalse(s["eligible_for_final"]); self.assertTrue(s["flags"]["ineligible_over_platform_max"]); self.assertEqual(s["status"], "pass")

    def test_estimate_and_partial_runs(self):
        f, x = facts_after_3()
        est = S4.estimate(f, CFG, counter=lambda t: len(t) // 4, run_dir=Path("/nonexistent"))
        self.assertEqual(len(est["scenes"]), 2); self.assertGreater(est["total_inr"], 0); self.assertEqual(est["scenes"][0]["inr"], est["scenes"][0]["inr_if_cache_hits"])
        self.assertLess(est["scenes"][1]["inr_if_cache_hits"], est["scenes"][1]["inr"])                              # headline = no cache
        self.assertLess(est["total_inr_if_cache_hits"], est["total_inr"])
        self.assertTrue(est["calibration"]["exact_counter"]); self.assertEqual(est["calibration"]["text_factor"], 1.0)   # a passed counter is exact
        local = S4.estimate(f, CFG, run_dir=Path("/nonexistent"), media="none")
        self.assertFalse(local["calibration"]["exact_counter"]); self.assertEqual(local["calibration"]["text_factor"], CFG["stage4"]["calibration"]["text_factor"])
        sc = by_scene(f)
        out, client, dbg = run4(f, {1: answer_for(sc[1])}, scene_ids=[1])
        self.assertEqual(out["scores"]["scenes_scored"], [1]); self.assertEqual(len(client.calls), 1)
        self.assertTrue(out["stages"]["4"]["warnings"])
        out2, client2, _ = run4(out, {2: answer_for(sc[2])})                                     # the rest: only scene 2 is called
        self.assertEqual(len(client2.calls), 1); self.assertEqual(out2["scores"]["scenes_scored"], [1, 2])
        self.assertEqual(len(out2["scores"]["candidates"]), len(f["candidates"]))


class TestMediaAndRuns(unittest.TestCase):
    def test_video_mode_part_order_scene_clock_and_new_fields(self):
        f, x = facts_after_3()
        f["episode_pass"]["content_type"] = "scripted_series"                                    # auto -> video
        sc = by_scene(f)
        ov = {sc[1][1]["id"]: {"removed_needed": [{"what": "line", "ref": x["X6"], "reason": "the reply is needed"}],
                               "kept_dead": [{"range": "0:08.0-0:11.2", "note": "walking"}]}}
        fm = FakeMedia()
        out, client, dbg = run4(f, {1: answer_for(sc[1], ov), 2: answer_for(sc[2])}, fm=fm)
        self.assertEqual(S4.media_mode(f, CFG), "video")
        m = client.calls[0]["media"]
        self.assertEqual(m[0], {"text": "PREFIX BYTES\n"})                                        # prefix first ...
        self.assertEqual(m[1]["file_uri"], "files/fake1"); self.assertEqual(m[1].get("fps"), 1.0)      # ... then the scene video
        self.assertEqual(len(fm.cuts), 2); self.assertEqual(fm.cuts[0][0], 1); self.assertEqual(fm.cuts[0][4], 480)
        self.assertEqual(fm.deleted, [])                                                            # kept for the compare run
        p = client.calls[0]["prompt"]
        self.assertIn("video clock", p); self.assertIn("(video 0:", p); self.assertIn("kept segments:", p); self.assertIn("shortened stretches:", p)
        self.assertIn("WATCH the candidate", client.calls[0]["prompt"])
        s = next(z for z in out["scores"]["candidates"] if z["id"] == sc[1][1]["id"])
        self.assertEqual(s["removed_needed"][0]["ref"], x["X6"]); self.assertEqual(s["kept_dead"][0]["range"], "0:08.0-0:11.2")
        self.assertEqual(out["scores"]["media"], "video"); self.assertEqual(out["stages"]["4"]["summary"]["media"], "video")
        est = S4.estimate(f, CFG, counter=lambda t: len(t) // 4, run_dir=Path("/nonexistent"))
        self.assertEqual(est["media"], "video"); self.assertGreater(est["media_inr"], 0); self.assertGreater(est["scenes"][0]["media_tokens"], 0)
        est0 = S4.estimate(f, CFG, counter=lambda t: len(t) // 4, run_dir=Path("/nonexistent"), media="none")
        self.assertEqual(est0["media_inr"], 0); self.assertLess(est0["total_inr"], est["total_inr"])

    def test_text_mode_has_no_media_and_schema_requires_the_fields(self):
        f, x = facts_after_3()
        sc = by_scene(f)
        out, client, dbg = run4(f, {sid: answer_for(cs) for sid, cs in sc.items()}, media="none")
        self.assertEqual(client.calls[0]["media"], [{"text": "PREFIX BYTES\n"}])
        self.assertIn("No video is attached", client.calls[0]["prompt"])
        a = answer_for(sc[1]); a["candidates"][0].pop("kept_dead")
        self.assertTrue(S4.schema_errors(a))

    def test_two_runs_stored_active_switch_and_compare(self):
        f, x = facts_after_3()
        sc = by_scene(f)
        loop = next(c for c in sc[1] if c["type"] == "loop"); tight = next(c for c in sc[1] if c["variant"] == "tight")
        a_ans = {1: answer_for(sc[1], {loop["id"]: {"payoff": 3}}), 2: answer_for(sc[2])}
        outA, _, _ = run4(f, a_ans, media="none")
        b_ans = {1: answer_for(sc[1], {tight["id"]: {"pace": 2, "kept_dead": [{"range": "0:08.0-0:19.0", "note": "music only"}]},
                                       loop["id"]: {"payoff": 9, "removed_needed": []}}), 2: answer_for(sc[2])}
        outB, _, _ = run4(outA, b_ans, media="video")
        keys = [r["run_key"] for r in S4.runs_of(outB)]
        self.assertEqual(len(keys), 2); self.assertTrue(keys[0].startswith("none@") and keys[1].startswith("video@"))
        self.assertEqual(outB["scores_active"], keys[0])                                           # the first run stays active until marked
        self.assertEqual(outB["scores"]["media"], "none")
        outB2 = S4.set_active(outB, keys[1])
        self.assertEqual(outB2["scores"]["media"], "video"); self.assertEqual(outB2["scores_active"], keys[1])
        with self.assertRaises(KeyError):
            S4.set_active(outB, "nope")
        cmp = S4.compare_runs(S4.runs_of(outB)[0], S4.runs_of(outB)[1])
        self.assertEqual([r["id"] for r in cmp["rejected_a_only"]], [loop["id"]])
        self.assertEqual([r["id"] for r in cmp["rejected_b_only"]], [tight["id"]])
        self.assertTrue(cmp["top_a"] and cmp["top_b"]); self.assertNotIn(tight["id"], [t["id"] for t in cmp["top_b"]])
        self.assertEqual(cmp["b_kept_dead"][0]["id"], tight["id"])
        # a rerun of the same mode on the same Stage 3 output continues that run, not a third one
        outC, client, _ = run4(outB2, {1: answer_for(sc[1])}, media="video", scene_ids=[1])
        self.assertEqual(len(S4.runs_of(outC)), 2); self.assertEqual(len(client.calls), 1)


class TestAcceptance4(unittest.TestCase):
    def test_changed_only_rescoring_carries_unchanged_candidates(self):
        """founder, 2026-10-28: after a Stage 3 rerun only the changed candidates (+ the sibling each is judged against)
        are sent; the rest are carried over from the active run by content (scene, type, variant, segments) even when
        ids shift, and the whole run is re-vetoed."""
        f, x = facts_after_3()
        sc = by_scene(f)
        out, client, _ = run4(f, {sid: answer_for(cs) for sid, cs in sc.items()}, media="none")
        base = S4.active_run(out)
        self.assertTrue(all(s.get("_content_key") for s in base["candidates"]))
        # a new Stage 3 output: one trimmed twin changes its segments, every id in scene 1 shifts by one new candidate at the front
        g = copy.deepcopy(out)
        tw = next(c for c in g["candidates"] if c["variant"] == "full_arc_trimmed")
        tw["segments"] = [[tw["segments"][0][0], tw["segments"][0][1] - 0.3]] + tw["segments"][1:]; tw["final_duration"] = round(tw["final_duration"] - 0.3, 3)
        g["stages"]["3"]["generated_at"] = "2099-01-01T00:00:00"
        carried, changed, idmap = S4.carry_over(g, base)
        self.assertEqual([c["id"] for c in changed], [tw["id"]])
        self.assertEqual(len(carried), len(g["candidates"]) - 1)
        sends = S4.changed_scenes(g, base)
        self.assertEqual(len(sends), 1); sh, send, ch = sends[0]
        self.assertEqual({c["id"] for c in send}, {tw["id"], tw["untrimmed_sibling"]})       # the changed twin and the sibling it is judged against
        est = S4.estimate(g, CFG, media="none", changed_only=True)
        self.assertEqual(est["n_candidates"], 2); self.assertTrue(est["changed_only"])
        with self.assertRaises(RuntimeError):
            run4(out, {}, media="none", changed_only=True)                                       # nothing changed: refuses
        out2, client2, _ = run4(g, {sh["scene_id"]: answer_for(send, {tw["id"]: {"pace": 2}})}, media="none", changed_only=True)
        self.assertEqual(len(client2.calls), 1)
        self.assertIn(f"CANDIDATES OF SCENE {sh['scene_id']} (2)", client2.calls[0]["prompt"])
        self.assertEqual(out2["scores_active"], S4.run_key("none", g))                          # the changed-only run supersedes the run it carried from
        new_run = S4.active_run(out2)
        by = {s["id"]: s for s in new_run["candidates"]}
        self.assertEqual(len(by), len(g["candidates"]))                                         # every candidate scored: rescored + carried
        self.assertEqual(by[tw["id"]]["scores"]["pace"], 2); self.assertNotIn("carried_from", by[tw["id"]])
        others = [s for s in by.values() if s["id"] != tw["id"] and s["id"] != tw["untrimmed_sibling"]]
        self.assertTrue(others and all(s.get("carried_from") == base["run_key"] for s in others))
        self.assertEqual(new_run["carried_from"], base["run_key"]); self.assertEqual(new_run["n_carried"], len(g["candidates"]) - 1)
        self.assertEqual(sum(1 for r in new_run["per_scene"] if r.get("carried_from")), len(sc) - 1)
        self.assertEqual(sorted(new_run["scenes_scored"]), sorted(sc))

    def test_kinds(self):
        f, x = facts_after_3()
        sc = by_scene(f)
        loop = next(c for c in sc[1] if c["type"] == "loop"); hook = next(c for c in sc[1] if c["variant"] == "open_question")
        ov = {loop["id"]: {"payoff": 3}, hook["id"]: {"open_question": 9}}
        out, _, _ = run4(f, {1: answer_for(sc[1], ov), 2: answer_for(sc[2], {sc[2][0]["id"]: {"standalone": 2, "fan_clip": True}})})
        ref = {"episode": "synthetic", "stage": "4", "checks": [
            {"kind": "vetoed", "candidate_containing": "0:31.0", "type": "loop", "dimension": "payoff", "max_score": 4, "note": "loop vetoed"},
            {"kind": "scores_check", "contains_line": x["X9"], "variant": "open_question", "min": {"open_question": 7}, "not_vetoed": True, "note": "open question passes"},
            {"kind": "scores_check", "scene_containing": "0:10", "type": "comedy", "all": True, "min": {"payoff": 8}, "max": {"series_pull": 9}, "note": "comedy"},
            {"kind": "sibling_verdicts", "scene_containing": "0:10", "note": "verdicts"},
            {"kind": "fan_clip_or_standalone", "scene_containing": "0:50", "type_any": ["comedy", "emotional"], "standalone_min": 6, "note": "fan or standalone"},
            {"kind": "rejections_have_reasons", "note": "reasons"},
            {"kind": "vetoed", "candidate_containing": "0:31.0", "variant": "open_question", "dimension": "open_question", "max_score": 4, "note": "should disagree"}]}
        rows = acceptance.evaluate_1b(out, ref)
        st = [r["status"] for r in rows]
        self.assertEqual(st[:6], ["agree"] * 6, rows)
        self.assertEqual(st[6], "disagree")
        g = copy.deepcopy(out); g.pop("scores")
        self.assertTrue(all(r["status"] == "missing" for r in acceptance.evaluate_1b(g, ref)))


if __name__ == "__main__":
    unittest.main()
