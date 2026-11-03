"""Stage 5 (cold-viewer critic) with a mocked client and fake renderer / uploader: the clip time map, family selection
with the floor rule, compare logic per type, the 2-of-3 verdict, schema / validation, the acceptance checker,
refusal without an active Stage 4 run. No live calls, no ffmpeg."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from selection_v2 import acceptance, preview as PV, stage4_scoring as S4, stage5_critic as S5
from selection_v2.runner import validate_facts
from tests.v2.test_stage4 import CFG, FakeClient as FakeClient4, answer_for, by_scene, facts_after_3, run4

CFG["stage5"]["mode"] = "critic"        # these tests cover the cold critic (stage5.mode critic); the editor has its own tests


def facts_after_4(cfg=None, overrides=None):
    f, x = facts_after_3(cfg)
    sc = by_scene(f)
    out, _, _ = run4(f, {sid: answer_for(cs, (overrides or {}).get(sid)) for sid, cs in sc.items()}, cfg, media="none")
    return out, x


def critic_answer(second=30.0, kind="funny", missing="none", swipe=None, want_next=True, more=True, caption="dosa ఇవ్వు sir", what="a look between them"):
    return {"what_is_happening": "A son asks his mother for something; she lands a line.", "first_3s": {"understood": True, "thought_it_was": "a mother and son"},
            "best_moment": {"second": second, "what": what, "kind": kind}, "swipe": swipe or [],
            "want_next": {"yes": want_next, "why": "I want to see what she does next about it" if want_next else "no"},
            "want_more_people": {"yes": more, "why": "their banter"}, "confusion": {"missing": missing, "what": "" if missing == "none" else "who is he"},
            "caption": caption, "relationship": "mother and son", "attention": {"source": "seen", "what": "her raised eyebrow"}}


class FakeClient5:
    def __init__(self, answers):          # {clip_id: [ans_run1, ans_run2, ans_run3]} or {clip_id: ans}
        self.answers = answers; self.calls = []; self.ledger = SimpleNamespace(summary=lambda: {"usd": 0.0})

    def generate_json(self, role, spec, system, prompt, schema, meta=None, want_raw=False, media=None):
        self.calls.append({"role": role, "system": system, "prompt": prompt, "meta": meta, "media": media})
        a = self.answers[meta["clip"]]
        obj = a[meta["run"] - 1] if isinstance(a, list) else a
        obj = copy.deepcopy(obj)
        raw = SimpleNamespace(text=json.dumps(obj, ensure_ascii=False), input_tokens=5000, output_tokens=600, thought_tokens=100, cached_tokens=0)
        return (obj, raw) if want_raw else obj


class FakeMedia:
    def __init__(self):
        self.rendered, self.uploads, self.deleted = [], [], []

    def renderer(self, src, run_dir, c, max_height=480, log=None):
        self.rendered.append((c["id"], tuple(map(tuple, c["segments"])), max_height))
        p = Path(run_dir) / "clips" / f"{c['id']}.mp4"; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"x"); return p

    def uploader(self, path, mode):
        self.uploads.append(str(path)); return {"uri": f"files/clip{len(self.uploads)}", "name": f"files/clip{len(self.uploads)}", "mime_type": "video/mp4"}

    def deleter(self, up):
        self.deleted.append(up["name"]); return True


def run5(f, answers, cfg=None, clip_ids=None, fm=None):
    cfg = copy.deepcopy(cfg or CFG)
    from tests.v2.test_stage3 import KEYS, make_inputs, transcript
    inputs = make_inputs(transcript=transcript(), keys=KEYS)
    fm = fm or FakeMedia()
    with tempfile.TemporaryDirectory() as td:
        inputs.paths = SimpleNamespace(run_dir=Path(td), mp4=Path(td) / "ep.mp4", wav=Path(td) / "a.wav", title=lambda: "x")
        cfg["paths"]["runs_log"] = str(Path(td) / "runs.csv")
        client = FakeClient5(answers)
        out = S5.run(inputs, f, cfg, client=client, clip_ids=clip_ids, renderer=fm.renderer, uploader=fm.uploader, deleter=fm.deleter)
        validate_facts(out)  # the runner validates the written facts; a stage key the episode schema rejects fails here, not in a billed run
        dbg = json.loads((Path(td) / "stage5_debug.json").read_text(encoding="utf-8"))
        return out, client, fm, dbg


class TestTimeMapAndSelection(unittest.TestCase):
    def test_time_map_both_ways(self):
        tm = PV.time_map([[10.0, 20.0], [30.0, 35.0]])
        self.assertEqual(tm[1]["clip_start"], 10.0); self.assertEqual(tm[1]["clip_end"], 15.0)
        self.assertEqual(PV.to_clip_time(tm, 32.0), 12.0); self.assertEqual(PV.to_episode_time(tm, 12.0), 32.0)
        self.assertIsNone(PV.to_clip_time(tm, 25.0))                                            # cut out

    def test_selection_families_twins_cap_and_floor(self):
        f, x = facts_after_4()
        sel = S5.select_clips(f, CFG)
        ids = [s["id"] for s in sel]
        self.assertEqual(len(ids), len(set(ids)))
        tops = [s for s in sel if s["role"] in ("top", "floor")]
        fams = [s["family"] for s in tops]; self.assertEqual(len(fams), len(set(fams)))             # one top per family
        for s in sel:
            if s["role"] == "twin":
                top = next(t for t in tops if t["family"] == s["family"]); self.assertNotEqual(top["id"], s["id"])
        combos = [s["combined"] for s in sel if s["role"] == "top"]
        self.assertEqual(combos, sorted(combos, reverse=True))                                  # combined order
        cfg = copy.deepcopy(CFG); cfg["stage5"]["per_episode"] = 3; cfg["stage5"]["floor"] = {"hook": 1}; cfg["stage5"]["coverage_per_scene"] = False
        g, _ = facts_after_4(overrides={1: {}})
        sel2 = S5.select_clips(g, cfg)
        sel2 = [s for s in sel2 if s["variant"] not in S5.ALWAYS_VARIANTS]                       # always-judged extras sit outside the cap
        self.assertLessEqual(len(sel2), 3 + 2)                                                  # the cap is checked before each family; a family is never split
        if any(s["status"] == "pass" and s["type"] == "hook" for s in g["scores"]["candidates"]):
            self.assertTrue(any(s["type"] == "hook" for s in sel2))                             # the floor pulls a hook in
        cands = {c["id"]: c for c in f["candidates"]}
        self.assertTrue(all(cands[s["id"]]["eligible_for_final"] for s in sel))

    def test_selection_adds_the_shortest_passing_variant(self):
        """founder, 2026-10-28: per family the top, its twin and the shortest passing variant when that is a third clip."""
        f, x = facts_after_4()
        sel = S5.select_clips(f, CFG)
        roles = {s["role"] for s in sel}
        self.assertTrue(roles <= {"top", "twin", "shortest", "floor", "coverage", "scene", "thread", "critic_trim"})
        for s in sel:
            if s["role"] == "shortest":
                fam = [t for t in sel if t["family"] == s["family"]]
                self.assertTrue(all(s["final_duration"] <= t["final_duration"] + 1e-6 for t in fam))
                self.assertNotIn(s["id"], [t["id"] for t in fam if t["role"] != "shortest"])
        # a family whose shortest passing member is neither the top nor its twin contributes three clips
        scored = {z["id"]: z for z in f["scores"]["candidates"] if z["status"] == "pass"}
        from selection_v2 import stage3_candidates as S3
        cands = {c["id"]: c for c in f["candidates"]}
        fams = {}
        for sid, z in scored.items():
            fams.setdefault("|".join(str(k) for k in S3.slot_key(cands[sid])), []).append(z)
        top_of = lambda m: max(m, key=lambda z: (z["combined"], z["id"]))                       # the selection's tie-break
        short_of = lambda m: min(m, key=lambda z: (z["final_duration"], -z["combined"], z["id"]))
        three = [k for k, m in fams.items() if top_of(m)["id"] != short_of(m)["id"]
                 and cands[top_of(m)["id"]].get("trimmed_sibling") != short_of(m)["id"] and cands[top_of(m)["id"]].get("untrimmed_sibling") != short_of(m)["id"]]
        for k in three:
            self.assertIn("shortest", [t["role"] for t in sel if t["family"] == k], k)

    def test_extras_always_judged_and_loops_skipped_when_off(self):
        """founder, 2026-10-29: thread and critic_trimmed candidates are always judged, outside the cap; loop families are
        not judged when Stage 6 loops are off for the content type."""
        f, x = facts_after_4()
        cfg = copy.deepcopy(CFG); cfg["stage5"]["per_episode"] = 1
        run = S5.S4.active_run(f)
        for c in f["candidates"]:
            if c["scene_id"] == 2 and c["type"] != "loop":
                c["variant"] = "thread"; break
        thr = next(c for c in f["candidates"] if c["variant"] == "thread")
        next(s for s in run["candidates"] if s["id"] == thr["id"])["status"] = "rejected"     # always judged, any Stage 4 status
        sel = S5.select_clips(f, cfg)
        self.assertIn(("thread", thr["id"]), [(s["role"], s["id"]) for s in sel])
        f["episode_pass"] = dict(f.get("episode_pass") or {}, content_type="scripted_series")
        cfg["stage5"]["per_episode"] = 99
        self.assertFalse([s for s in S5.select_clips(f, cfg) if s["type"] == "loop"])
        f["episode_pass"]["content_type"] = "podcast"
        self.assertTrue([s for s in S5.select_clips(f, cfg) if s["type"] == "loop"])

    def test_coverage_judges_every_scene_top(self):
        """founder, 2026-10-30: every scene's top Stage 4 candidate is judged regardless of rank."""
        f, x = facts_after_4()
        cfg = copy.deepcopy(CFG); cfg["stage5"]["per_episode"] = 1; cfg["stage5"]["floor"] = {}
        sel = S5.select_clips(f, cfg)
        scenes = {c["scene_id"] for c in f["candidates"]}
        self.assertEqual({next(c for c in f["candidates"] if c["id"] == s["id"])["scene_id"] for s in sel} & scenes, scenes)
        self.assertTrue(any(s["role"] == "coverage" for s in sel))
        cfg["stage5"]["coverage_per_scene"] = False
        self.assertFalse(any(s["role"] == "coverage" for s in S5.select_clips(f, cfg)))

    def test_thread_moment_is_the_payoff_scene_part(self):
        """founder, 2026-10-30: a thread's critic moment counts anywhere inside its payoff-scene part."""
        f, x = facts_after_4()
        c = {"id": "C2z", "scene_id": 2, "type": "comedy", "variant": "thread", "segments": [[0.7, 8.8], [43.7, 50.2]], "start": 0.7, "end": 50.2,
             "final_duration": 14.6, "payoff_line": x["Y3"], "anchor_line": x["Y3"], "thread": {"payoff_scene": 2, "setup_scene": 1}}
        exp = S5.expected_moment(f, c)
        self.assertAlmostEqual(exp["clip_start"], 8.1, places=1); self.assertAlmostEqual(exp["clip_end"], 14.6, places=1)
        self.assertTrue(S5.compare_run(c, critic_answer(second=9.0), exp, CFG["stage5"])["pass"])
        self.assertFalse(S5.compare_run(c, critic_answer(second=2.0), exp, CFG["stage5"])["pass"])

    def test_typical_variant_per_moment_and_channel_short_rule(self):
        """founder, 2026-10-30: each judged moment adds its variant closest to the typical length (+ twin); a channel Short
        is judged without a moment check."""
        f, x = facts_after_4()
        sel = S5.select_clips(f, CFG)
        cands = {c["id"]: c for c in f["candidates"]}
        target = S5.typical_length(f, CFG)["target_s"]
        scored = {s["id"]: s for s in S5.S4.active_run(f)["candidates"] if s["status"] == "pass" and s.get("eligible_for_final", True)
                  and cands[s["id"]]["variant"] not in S5.ALWAYS_VARIANTS}
        moments = {(cands[s["id"]]["scene_id"], cands[s["id"]].get("payoff_line")) for s in sel if s["id"] in scored}
        ids = {s["id"] for s in sel}
        for mk in moments:
            mem = [s for s in scored.values() if (cands[s["id"]]["scene_id"], cands[s["id"]].get("payoff_line")) == mk]
            near = min(mem, key=lambda s: (abs(s["final_duration"] - target), -s["combined"], s["id"]))
            self.assertIn(near["id"], ids)
        c = dict(next(c for c in f["candidates"] if c["variant"] == "full_arc"), channel_short={"short_id": "s"})
        exp = S5.expected_moment(f, c)
        self.assertTrue(S5.compare_run(c, critic_answer(second=1.0), exp, CFG["stage5"])["pass"])     # anywhere: no expected payoff

    def test_reason_class(self):
        from selection_v2 import critic_class as CC
        pace = {"pass": False, "reasons": ["swipe stretch over the limit: 2-14 s; 30-40 s"]}
        conf = {"pass": False, "reasons": ["critic's moment at 3 s is 20 s away from the expected 23-25 s (tolerance 3.0 s)", "swipe stretch over the limit: 2-14 s"]}
        ok = {"pass": True, "reasons": ["moment found by text (quoted: x) at 3 s"]}
        self.assertEqual(CC.reason_class([ok, pace, pace]), "pace"); self.assertEqual(CC.reason_class([ok, pace, conf]), "confusion")
        self.assertIsNone(CC.reason_class([ok, ok]))
        self.assertEqual(CC.pace_stretches([pace, {"pass": False, "long_swipes": [{"from_s": 12, "to_s": 20}]}]), [(2.0, 20.0), (30.0, 40.0)])
        self.assertEqual(CC.classify_reason("missing context: major (x)"), "confusion"); self.assertIsNone(CC.classify_reason("moment found by text (y)"))

    def test_refuses_without_active_matching_stage4(self):
        f, x = facts_after_3()
        with self.assertRaises(RuntimeError):
            S5.select_clips(f, CFG)
        g, _ = facts_after_4(); g["scores"]["stage3_generated_at"] = "older"; g["score_runs"][0]["stage3_generated_at"] = "older"
        with self.assertRaises(RuntimeError):
            S5.check_inputs(g)


class TestCompare(unittest.TestCase):
    def setUp(self):
        self.f, self.x = facts_after_4()
        self.cands = {c["id"]: c for c in self.f["candidates"]}
        self.c5 = CFG["stage5"]

    def comedy(self):
        return next(c for c in self.f["candidates"] if c["type"] == "comedy" and c["variant"] == "full_arc" and c["scene_id"] == 1)

    def test_comedy_pass_and_fail_reasons(self):
        c = self.comedy(); exp = S5.expected_moment(self.f, c)
        self.assertEqual(exp["line"], self.x["X9"]); self.assertAlmostEqual(exp["clip_start"], 30.5 - c["start"], places=2)
        ok = S5.compare_run(c, critic_answer(second=exp["clip_start"] + 1.0), exp, self.c5); self.assertTrue(ok["pass"], ok)
        far = S5.compare_run(c, critic_answer(second=exp["clip_start"] - 10.0), exp, self.c5); self.assertFalse(far["pass"]); self.assertIn("away from the expected", far["reasons"][0])
        miss = S5.compare_run(c, critic_answer(second=exp["clip_start"], missing="major"), exp, self.c5); self.assertFalse(miss["pass"])
        minor = S5.compare_run(c, critic_answer(second=exp["clip_start"], missing="minor"), exp, self.c5); self.assertTrue(minor["pass"])
        sw = S5.compare_run(c, critic_answer(second=exp["clip_start"], swipe=[{"from_s": 2, "to_s": 25, "why": "walking"}]), exp, self.c5)
        self.assertFalse(sw["pass"]); self.assertTrue(sw["long_swipes"])                                     # founder, 2026-10-30: > 20 s
        mid = S5.compare_run(c, critic_answer(second=exp["clip_start"], swipe=[{"from_s": 2, "to_s": 15, "why": "walking"}]), exp, self.c5)
        self.assertTrue(mid["pass"])                                                                          # 13 s: under the calibrated limit
        short = S5.compare_run(c, critic_answer(second=exp["clip_start"], swipe=[{"from_s": 2, "to_s": 6, "why": "walking"}]), exp, self.c5); self.assertTrue(short["pass"])

    def test_hook_rules(self):
        """founder, 2026-10-29: open-question end-variants are judged with the hook rule."""
        h = next(c for c in self.f["candidates"] if c["variant"] == "open_question")
        self.assertTrue(S5.is_open_question(h))
        exp = S5.expected_moment(self.f, h)
        ok = S5.compare_run(h, critic_answer(second=2.0, kind="other", want_next=True), exp, self.c5); self.assertTrue(ok["pass"], ok)
        no = S5.compare_run(h, critic_answer(second=2.0, kind="other", want_next=False), exp, self.c5); self.assertFalse(no["pass"])
        # founder, 2026-10-28: the no-punchline-tail check applies only to hooks longer than hook_tail_check_min_len_s
        c5short = copy.deepcopy(self.c5); c5short["compare"]["hook_tail_check_min_len_s"] = h["final_duration"] + 1.0
        punch = S5.compare_run(h, critic_answer(second=h["final_duration"] - 1.0, kind="funny", want_next=True), exp, c5short)
        self.assertTrue(punch["pass"], "below the tail-check length a hook is one funny line; Q5 yes decides")
        c5long = copy.deepcopy(self.c5); c5long["compare"]["hook_tail_check_min_len_s"] = 5.0
        punch2 = S5.compare_run(h, critic_answer(second=h["final_duration"] - 1.0, kind="funny", want_next=True), exp, c5long)
        self.assertFalse(punch2["pass"]); self.assertIn("punchline", punch2["reasons"][0])
        early_funny = S5.compare_run(h, critic_answer(second=1.0, kind="funny", want_next=True), exp, self.c5); self.assertTrue(early_funny["pass"])
        major = S5.compare_run(h, critic_answer(second=2.0, kind="other", missing="major"), exp, self.c5); self.assertTrue(major["pass"])   # noted, not failing

    def test_q3_text_naming_the_payoff_counts_regardless_of_time(self):
        """founder, 2026-10-29: quoted payoff words (transliterated, not the setup line's words) or a description sharing
        >= 3 content words with the beat note count the moment as found at any clip time."""
        c = self.comedy(); exp = S5.expected_moment(self.f, c)
        far = exp["clip_start"] - 15.0
        quoted = S5.compare_run(c, critic_answer(second=far, what="the mother says 'nuvvu ivvaalira naaku' and he freezes"), exp, self.c5)
        self.assertTrue(quoted["pass"], quoted); self.assertIn("quoted", quoted["found_by_text"])
        setup_only = S5.compare_run(c, critic_answer(second=far, what="he asks 'five star rating ivvu'"), exp, self.c5)
        self.assertFalse(setup_only["pass"]); self.assertIsNone(setup_only["found_by_text"])
        exp2 = dict(exp, note="Mother refuses the rating and turns the demand back on her son")
        described = S5.compare_run(c, critic_answer(second=far, what="his mother turns the rating demand back on him"), exp2, self.c5)
        self.assertTrue(described["pass"], described); self.assertIn("described", described["found_by_text"])
        c5 = copy.deepcopy(self.c5); c5["compare"]["text_match"] = False
        self.assertFalse(S5.compare_run(c, critic_answer(second=far, what="the mother says 'nuvvu ivvaalira naaku'"), exp, c5)["pass"])

    def test_tolerance_scales_with_length_and_loops_accept_the_tail(self):
        """founder, 2026-10-28: tolerance = max(3 s, 4% of the clip); a loop's moment anywhere in its last 5 s counts."""
        c = copy.deepcopy(self.comedy()); exp = S5.expected_moment(self.f, c)
        c["final_duration"] = 150.0                                           # pretend a long clip: tolerance 6 s
        r = S5.compare_run(c, critic_answer(second=exp["clip_start"] - 5.0), exp, self.c5); self.assertTrue(r["pass"], r)
        r2 = S5.compare_run(c, critic_answer(second=exp["clip_start"] - 7.0), exp, self.c5); self.assertFalse(r2["pass"]); self.assertIn("tolerance 6.0 s", r2["reasons"][0])
        lp = next(c for c in self.f["candidates"] if c["type"] == "loop"); expl = S5.expected_moment(self.f, lp)
        tail = S5.compare_run(lp, critic_answer(second=max(0.0, lp["final_duration"] - 1.0)), expl, self.c5); self.assertTrue(tail["pass"], tail)
        c5 = copy.deepcopy(self.c5); c5["compare"]["loop_tail_ok_s"] = 0.0
        far_exp = dict(expl, clip_start=0.0, clip_end=0.5)
        self.assertFalse(S5.compare_run(lp, critic_answer(second=max(0.0, lp["final_duration"] - 1.0)), far_exp, c5)["pass"])

    def test_caption_context_counts_not_understood_runs_and_stores_q9(self):
        """founder, 2026-10-29: needs_caption_context only when Q2 says "not understood" in >= 2 runs; Q9 (relationship) is
        stored for Stage 6's comparison with the sheet's dynamic; wording overlap is no longer judged here."""
        c = self.comedy(); sheet = next(sh for sh in self.f["beat_sheets"] if sh["scene_id"] == c["scene_id"])
        runs = [{"answers": {"first_3s": {"understood": True, "thought_it_was": "roommates fighting over food"}, "relationship": "flatmates"}},
                {"answers": {"first_3s": {"understood": True, "thought_it_was": "two people arguing"}, "relationship": "friends"}},
                {"answers": {"first_3s": {"understood": True, "thought_it_was": "a son and his mother"}, "relationship": "mother and son"}}]
        cc = S5.caption_context(self.f, c, runs, self.c5)
        self.assertFalse(cc["needs_caption_context"]); self.assertEqual(cc["runs_not_understood"], 0)
        self.assertEqual([x["relationship"] for x in cc["framings"]], ["flatmates", "friends", "mother and son"])
        self.assertEqual(cc["dynamic"], sheet.get("dynamic") or {}); self.assertIsNone(cc["q9_vs_dynamic"])
        runs[0]["answers"]["first_3s"]["understood"] = False; runs[1]["answers"]["first_3s"]["understood"] = False
        cc2 = S5.caption_context(self.f, c, runs, self.c5)
        self.assertTrue(cc2["needs_caption_context"]); self.assertEqual(cc2["runs_not_understood"], 2)
        self.assertFalse(hasattr(S5, "framing_disagrees"))

    def test_verdict_two_of_three(self):
        self.assertEqual(S5.verdict_of(3, 3, self.c5), "PASS"); self.assertEqual(S5.verdict_of(2, 3, self.c5), "PASS")
        self.assertEqual(S5.verdict_of(1, 3, self.c5), "BORDERLINE"); self.assertEqual(S5.verdict_of(0, 3, self.c5), "FAIL")

    def test_validation(self):
        good = critic_answer(second=10.0); self.assertEqual(S5.validate(good, 30.0), [])
        self.assertTrue(S5.validate(critic_answer(second=200.0), 30.0))
        bad = critic_answer(); bad.pop("caption"); self.assertTrue(S5.schema_errors(bad))


class TestRun(unittest.TestCase):
    def test_media_cache_key_survives_reupload(self):
        """Uploads are deleted after each clip, so the Files API name changes on a rerun; the response-cache key must
        come from the clip bytes, or every rerun is billed again (Surya C10f was billed twice on 2026-10-28)."""
        f, _ = facts_after_4()
        one = S5.select_clips(f, CFG)[0]["id"]
        ans = critic_answer(second=(S5.expected_moment(f, f["candidates"][[c["id"] for c in f["candidates"]].index(one)])["clip_start"] or 0) + 0.5)
        out1, c1, fm1, _ = run5(f, {one: ans}, clip_ids=[one])
        fm2 = FakeMedia(); fm2.uploads = ["earlier"] * 5                        # the rerun gets a different upload name
        out2, c2, _, _ = run5(out1, {one: ans}, clip_ids=[one], fm=fm2)
        k1 = c1.calls[0]["media"][0]["key"]; k2 = c2.calls[0]["media"][0]["key"]
        self.assertNotEqual(c1.calls[0]["media"][0]["file_uri"], c2.calls[0]["media"][0]["file_uri"])
        self.assertEqual(k1, k2); self.assertTrue(k1.startswith("sha:")); self.assertNotIn("files/clip", k1)

    def test_full_run_renders_uploads_asks_three_times_and_records(self):
        f, x = facts_after_4()
        sel = S5.select_clips(f, CFG)
        cands = {c["id"]: c for c in f["candidates"]}
        answers = {}
        for s in sel:
            c = cands[s["id"]]; exp = S5.expected_moment(f, c)
            if c["type"] == "hook" or S5.is_open_question(c):
                answers[c["id"]] = [critic_answer(second=1.0, kind="other"), critic_answer(second=1.0, kind="other", want_next=False), critic_answer(second=1.5, kind="other")]
            else:
                good = critic_answer(second=(exp["clip_start"] or 0) + 0.5)
                answers[c["id"]] = [good, good, critic_answer(second=0.0)]
        fm = FakeMedia()
        out, client, fm, dbg = run5(f, answers, fm=fm)
        cr = out["critic"]
        self.assertEqual(len(cr["clips"]), len(sel))
        self.assertEqual(len(client.calls), 3 * len(sel))
        self.assertTrue(all(len(c["media"]) == 1 and c["media"][0]["file_uri"].startswith("files/clip") for c in client.calls))   # the clip only
        self.assertTrue(all("PREFIX" not in c["prompt"] and "beat" not in c["system"].lower() for c in client.calls))          # no episode material
        self.assertEqual(len({c["prompt"] for c in client.calls[:3]}), 3)                                                   # three distinct answer sheets
        self.assertEqual(len(fm.rendered), len(sel)); self.assertEqual(len(fm.deleted), len(sel))
        z = next(z for z in cr["clips"] if z["type"] != "hook" and not S5.is_open_question(z))
        self.assertEqual(z["verdict"], "PASS"); self.assertEqual(z["n_pass"], 2); self.assertEqual(len(z["runs"]), 3); self.assertGreater(z["usage"]["cost_inr"], 0)
        self.assertEqual(len(z["captions"]), 3); self.assertEqual(len(z["located_s"]), 3)
        h = next((z for z in cr["clips"] if z["type"] == "hook" or S5.is_open_question(z)), None)
        if h:
            self.assertEqual(h["verdict"], "PASS"); self.assertEqual(h["n_pass"], 2)
        self.assertEqual(out["stages"]["5"]["summary"]["clips_done"], len(sel))
        self.assertIn(list(dbg["clips"])[0], {s["id"] for s in sel})
        # a partial rerun of one clip keeps the others
        one = sel[0]["id"]
        out2, client2, _, _ = run5(out, {one: answers[one]}, clip_ids=[one])
        self.assertEqual(len(client2.calls), 3); self.assertEqual(len(out2["critic"]["clips"]), len(sel))
        with self.assertRaises(RuntimeError):
            run5(out2, answers)                                                                  # nothing left to run

    def test_acceptance_kinds(self):
        f, x = facts_after_4()
        sel = S5.select_clips(f, CFG)
        cands = {c["id"]: c for c in f["candidates"]}
        answers = {}
        for s in sel:
            c = cands[s["id"]]; exp = S5.expected_moment(f, c)
            answers[c["id"]] = critic_answer(second=1.0, kind="other") if (c["type"] == "hook" or S5.is_open_question(c)) else critic_answer(second=(exp["clip_start"] or 0) + 0.5)
        out, _, _, _ = run5(f, answers)
        ref = {"episode": "synthetic", "stage": "5", "checks": [
            {"kind": "critic_verdict", "scene_containing": "0:10", "type": "comedy", "verdict_any": ["PASS"], "min_pass_runs": 3, "moment_near_line": x["X9"], "tol": 3.0, "min_runs": 2, "q6_positive_min_runs": 2, "no_missing_min_runs": 2, "note": "comedy"},
            {"kind": "critic_record", "scene_containing": "0:10", "type": "comedy", "if_fail_q7_mentions": ["job"], "note": "record"},
            {"kind": "critic_all_recorded", "note": "all"},
            {"kind": "critic_verdict", "scene_containing": "0:10", "type": "comedy", "verdict_any": ["FAIL"], "note": "should disagree"}]}
        if any(s["type"] == "hook" for s in sel):
            ref["checks"].append({"kind": "critic_hook_q5", "scene_containing": "0:10", "type": "hook", "min_runs": 2, "note": "hook q5"})
        rows = acceptance.evaluate_1b(out, ref)
        st = [r["status"] for r in rows]
        self.assertEqual(st[:3], ["agree"] * 3, rows); self.assertEqual(st[3], "disagree")
        if len(rows) > 4:
            self.assertEqual(st[4], "agree", rows[4])
        g = copy.deepcopy(out); g.pop("critic")
        self.assertTrue(all(r["status"] == "missing" for r in acceptance.evaluate_1b(g, ref)))

    def test_acceptance_moment_short_variant_kind(self):
        f, x = facts_after_4()
        sel = S5.select_clips(f, CFG); cands = {c["id"]: c for c in f["candidates"]}
        answers = {s_["id"]: critic_answer(second=1.0, kind="other") for s_ in sel}
        out, _, _, _ = run5(f, answers)
        ok = acceptance.evaluate_1b(out, {"stage": "5", "checks": [{"kind": "critic_moment_short_variant", "max_len_s": 45}]})[0]
        mom = lambda z: (z["scene_id"], cands[z["id"]]["payoff_line"])
        longest_short = max(min(z["final_duration"] for z in out["critic"]["clips"] if mom(z) == m) for m in {mom(z) for z in out["critic"]["clips"]})
        self.assertEqual(ok["status"], "agree" if longest_short <= 45 else "disagree", ok)
        tight = acceptance.evaluate_1b(out, {"stage": "5", "checks": [{"kind": "critic_moment_short_variant", "max_len_s": 1}]})[0]
        self.assertEqual(tight["status"], "disagree"); self.assertIn("no judged variant <= 1 s", tight["got"])

    def test_manual_clip_outside_selection(self):
        cfg = copy.deepcopy(CFG); cfg["stage5"]["per_episode"] = 2; cfg["stage5"]["floor"] = {}
        f, x = facts_after_4()
        sel = S5.select_clips(f, cfg); have = {s["id"] for s in sel}
        passing = [s["id"] for s in f["scores"]["candidates"] if s["status"] == "pass" and s["id"] not in have]
        self.assertTrue(passing)
        extra = passing[0]
        cands = {c["id"]: c for c in f["candidates"]}
        exp = S5.expected_moment(f, cands[extra])
        ans = critic_answer(second=1.0, kind="other") if cands[extra]["type"] == "hook" else critic_answer(second=(exp["clip_start"] or 0) + 0.5)
        out, client, _, _ = run5(f, {extra: ans}, cfg=cfg, clip_ids=[extra])
        self.assertEqual([z["id"] for z in out["critic"]["clips"]], [extra]); self.assertEqual(len(client.calls), 3)
        self.assertEqual(next(s for s in out["critic"]["selected"] if s["id"] == extra)["role"], "manual")
        rejected = next((s["id"] for s in f["scores"]["candidates"] if s["status"] == "rejected"), None)
        if rejected:
            with self.assertRaises(RuntimeError):
                run5(f, {rejected: ans}, cfg=cfg, clip_ids=[rejected])

    def test_carry_over_recompares_unchanged_clips_and_runs_changed_ones(self):
        """founder, 2026-10-28: after a Stage 3/4 rerun, a clip whose cut is unchanged is carried (its stored answers
        re-compared with the current rules, no call, ₹0); a clip whose segments changed is run again."""
        f, x = facts_after_4()
        sel = S5.select_clips(f, CFG); cands = {c["id"]: c for c in f["candidates"]}
        answers = {}
        for s_ in sel:
            c = cands[s_["id"]]; exp = S5.expected_moment(f, c)
            answers[c["id"]] = critic_answer(second=1.0, kind="other") if c["type"] == "hook" else critic_answer(second=(exp["clip_start"] or 0) + 0.5)
        out, client, fm, _ = run5(f, answers)
        n = len(out["critic"]["clips"]); self.assertEqual(len(client.calls), 3 * n)
        self.assertTrue(all(z.get("content_key") and z.get("caption_context") is not None for z in out["critic"]["clips"]))
        self.assertTrue(all(z["relationships"] == ["mother and son"] * 3 for z in out["critic"]["clips"]))
        # a new Stage 3 + 4: one selected clip's segments change, ids shift; the Stage 4 run key changes
        g = copy.deepcopy(out)
        victim = next(z for z in out["critic"]["clips"] if z["type"] != "hook")
        vc = next(c for c in g["candidates"] if c["id"] == victim["id"])
        vc["segments"] = [[vc["segments"][0][0], vc["segments"][0][1] - 0.2]] + vc["segments"][1:]; vc["final_duration"] = round(vc["final_duration"] - 0.2, 3)
        g["stages"]["3"]["generated_at"] = "2099-01-01T00:00:00"
        run4k = g["scores"]; run4k["stage3_generated_at"] = "2099-01-01T00:00:00"; run4k["run_key"] = "none@2099-01-01T00:00:00"
        g["score_runs"] = [run4k]; g["scores_active"] = run4k["run_key"]
        pl = S5.plan(g, CFG)
        acts = {p_["id"]: p_["action"] for p_ in pl}
        self.assertEqual(acts[victim["id"]], "run"); self.assertTrue(all(a == "carry" for i, a in acts.items() if i != victim["id"]))
        est = S5.estimate(g, CFG, pl); self.assertEqual(est["n_run"], 1); self.assertEqual(est["n_carry"], len(pl) - 1)
        self.assertEqual(sum(1 for r in est["clips"] if r["inr"] == 0), len(pl) - 1)
        out2, client2, fm2, _ = run5(g, {victim["id"]: answers[victim["id"]]})
        self.assertEqual(len(client2.calls), 3); self.assertEqual(fm2.rendered[0][0], victim["id"]); self.assertEqual(len(fm2.rendered), 1)
        cr2 = out2["critic"]; by = {z["id"]: z for z in cr2["clips"]}
        self.assertEqual(len(by), len(pl)); self.assertEqual(cr2["n_run"], 1); self.assertEqual(cr2["n_carried"], len(pl) - 1)
        self.assertNotIn("carried_from", by[victim["id"]])
        other = next(z for z in cr2["clips"] if z["id"] != victim["id"])
        self.assertTrue(other.get("carried_from")); self.assertEqual(other["usage"]["cost_inr"], 0.0); self.assertGreater(other["usage"]["paid_cost_inr"], 0)
        self.assertEqual(len(other["runs"]), 3); self.assertIn(other["verdict"], ("PASS", "BORDERLINE", "FAIL"))
        self.assertEqual(cr2["usage"]["cost_inr"], by[victim["id"]]["usage"]["cost_inr"])
        # 2026-10-29: a record whose clip falls out of a later selection is archived, not dropped, and carried back for free
        g2 = copy.deepcopy(out2); cfg_small = copy.deepcopy(CFG); cfg_small["stage5"]["per_episode"] = 2; cfg_small["stage5"]["floor"] = {}
        one3 = next(iter(S5.select_clips(g2, cfg_small)))["id"]
        out3, client3, _, _ = run5(g2, {one3: answers.get(one3) or critic_answer(second=1.0, kind="other")}, cfg=cfg_small, clip_ids=[one3])
        kept_keys = {json.dumps(z["content_key"]) for z in out3["critic"]["clips"]} | {json.dumps(z["content_key"]) for z in out3["critic"]["archive"]}
        self.assertLessEqual({json.dumps(z["content_key"]) for z in out2["critic"]["clips"]}, kept_keys)            # nothing lost
        self.assertTrue(out3["critic"]["archive"])
        pl3 = S5.plan(out3, CFG)
        self.assertTrue(all(p_["action"] == "carry" for p_ in pl3 if json.dumps(S5.clip_key(cands_now := {c["id"]: c for c in out3["candidates"]}[p_["id"]])) in kept_keys))
        # a named id is always run again, even when unchanged
        pl2 = S5.plan(out2, CFG, clip_ids=[other["id"]]); self.assertEqual(next(p_ for p_ in pl2 if p_["id"] == other["id"])["action"], "run")
        with self.assertRaises(RuntimeError):
            run5(out2, answers)                                                                  # everything carried: nothing to run

    def test_estimate(self):
        f, x = facts_after_4()
        est = S5.estimate(f, CFG)
        self.assertEqual(est["runs_per_clip"], 3); self.assertGreater(est["total_inr"], 0)
        self.assertTrue(all(r["input_tokens"] > 0 for r in est["clips"]))


if __name__ == "__main__":
    unittest.main()
