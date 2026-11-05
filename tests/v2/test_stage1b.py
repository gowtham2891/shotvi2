"""Stage 1B tests with a fake LLM client (no network): prefix determinism, the validator rules, the retry path,
derivation, glossary merge, the acceptance checker."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from selection_v2 import acceptance, glossary, prefix, stage1_episode_pass as E, stage1_facts as S
from selection_v2.config import load_cast_lists, load_config
from tests.v2.synthetic import make_inputs

CFG = copy.deepcopy(load_config()); CFG["stage1a"]["context_window_s"] = 8
# the fixture answer below is RAW-mode shaped (the one-off experiment); the production default is prior: fix, text only
CFG["stage1b"]["prior"] = "raw"; CFG["stage1b"]["video"] = True
CAST = load_cast_lists()


def facts():
    return S.run(make_inputs(), None, CFG, CAST)


def dlg(f):
    return E.dialogue_ids(f)


def good_answer(f):
    """A valid RAW-mode answer for the synthetic episode: keys = raw labels (speaker_0 ...), one split (speaker_5's
    first line is a stranger), one merge (speaker_6 and speaker_7 are the same customer)."""
    d = dlg(f)
    by_text = {r["text"]: r["id"] for r in f["lines"] if r["kind"] == "speech"}
    s2_first = by_text["స్వామి ఏం చేస్తున్నావ్ రా?"]
    s3_first = by_text["Sir నాకు ఆకలి వేస్తుంది"]
    i2, i3 = d.index(s2_first), d.index(s3_first)
    labels = sorted({r["speaker"] for r in f["lines"] if r["kind"] == "speech"})
    names = {"speaker_0": "Surya", "speaker_1": "Lakshmi", "speaker_2": "Swami", "speaker_3": "Siva", "speaker_4": "Meghana",
             "speaker_5": "Arjun", "speaker_6": "Customer", "speaker_7": "Customer"}
    cast = [{"label": lab, "name": names[lab], "status": "confirmed", "evidence": f"seen: {lab}"} for lab in labels]
    same = [{"labels": ["speaker_6", "speaker_7"], "name": "Customer", "evidence": "same face at the door"}]
    split = [{"label": "speaker_5", "people": [{"name": "delivery customer", "lines": s3_first}], "evidence": "a different man says the first line"}]
    ref = [{"name": names[lab], "times": ["0:05.0", "0:40.0"]} for lab in labels if lab != "speaker_7"]
    final_keys = [lab for lab in labels if lab != "speaker_7"]          # speaker_7 merged into speaker_6
    speakers = [{"key": k, "evidence_against_prior": "none", "name": names[k], "status": "confirmed", "evidence": f"{k} lines"} for k in final_keys]
    speakers.append({"key": "new_1", "evidence_against_prior": s3_first, "name": "delivery customer", "status": "new", "evidence": "a stranger"})
    return {
        "cast": cast, "same_person": same, "split_labels": split, "reference_times": ref,
        "speakers": speakers, "anchor_checks": [],
        "scenes": [{"id": 1, "first_line": d[0], "last_line": d[i2 - 1], "label": "home", "register": "comedy", "speakers": ["speaker_0", "speaker_1"]},
                   {"id": 2, "first_line": d[i2], "last_line": d[i3 - 1], "label": "friends", "register": "comedy", "speakers": ["speaker_2", "speaker_3", "speaker_4"]},
                   {"id": 3, "first_line": d[i3], "last_line": d[-1], "label": "delivery", "register": "comedy", "speakers": ["new_1", "speaker_6"]}],
        "suspect_resolutions": [{"line": by_text["హా"], "labelled": "speaker_7", "likely": "speaker_6", "likely_key": "speaker_6", "reason": "the customer grunts"}],
        "threads": [],
        "possible_non_story": [{"lines": by_text["Happy birthday to you"], "kind": "song", "signals": ["lyrics"], "reason": "singing"}],
        "content_type": "scripted_series",
        "glossary": {"premise": "jobless engineer", "nicknames": [{"name": "Arjun", "nickname": "Virgin"}],
                     "running_bits": ["cashew packet as currency"], "catchphrases": [], "relationships": ["Surya loves Anjali"]},
    }


class FakeClient:
    """Returns canned answers in order; records the prompts it was given."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.ledger = SimpleNamespace(summary=lambda: {"usd": 0.001, "usd_billed": 0.001})

    def generate_json(self, role, spec, system, prompt, schema, meta=None, want_raw=False, media=None):
        self.calls.append({"role": role, "system": system, "prompt": prompt, "schema": schema, "meta": meta, "media": media})
        obj = self.answers.pop(0)
        raw = SimpleNamespace(text=json.dumps(obj, ensure_ascii=False), input_tokens=20000, output_tokens=3000,
                              thought_tokens=2000, cached_tokens=0, model_version="fake")
        return (obj, raw) if want_raw else obj


def _cutter(src, media_dir, sid, start, end, mode, max_h, log=None):
    return Path(media_dir) / f"episode_{mode}.mp4"


def _uploader(path, mode):
    return {"uri": "files/fake-episode", "name": "files/fake-episode", "mime_type": "video/mp4"}


def _deleter(uploaded):
    return True


def run_with(f, answers, cfg=None):
    cfg = cfg or CFG
    inputs = make_inputs()
    with tempfile.TemporaryDirectory() as td:
        inputs.paths = SimpleNamespace(run_dir=Path(td), mp4=Path(td) / "ep.mp4", title=lambda: "Synthetic Surya Web Series")
        c2 = copy.deepcopy(cfg); c2["stage1b"]["glossary_dir"] = td   # keep test glossaries out of data/
        client = FakeClient(answers)
        out = E.run(inputs, f, c2, client=client, cutter=_cutter, uploader=_uploader, deleter=_deleter)
        dbg = json.loads((Path(td) / "stage1b_debug.json").read_text(encoding="utf-8"))
        pre = (Path(td) / "prefix.txt").read_text(encoding="utf-8")
        return out, client, dbg, pre


class TestPrefix(unittest.TestCase):
    def test_byte_identical_and_mentions_everything(self):
        f = facts()
        a, b = prefix.build_prefix(f, None, CFG), prefix.build_prefix(copy.deepcopy(f), None, CFG)
        self.assertEqual(a, b)
        self.assertIn("## LINE TABLE", a)
        self.assertNotIn("SCENE-EDGE PROPOSALS", a)            # the program no longer guesses scenes (2026-11-03)
        self.assertIn("[speaker_0]:", a)                  # raw labels are the prior in the line table
        self.assertIn("speaker_0: A Surya x11", a)        # the fix's names only in the LOW-TRUST block
        self.assertNotIn("ANCHOR CHECKS", a)
        self.assertNotIn("## SERIES GLOSSARY", a)
        c = prefix.build_prefix(f, {"premise": "x"}, CFG)
        self.assertIn("## SERIES GLOSSARY", c)


class TestValidator(unittest.TestCase):
    def test_good_answer_passes(self):
        f = facts(); a = good_answer(f)
        self.assertEqual(E.validate_tier0(a, f, 92.0), [])
        self.assertEqual(E.validate_tier1(a, f, a["possible_non_story"], raw=True), [])
        self.assertEqual(E.validate_tier2(a, f, CFG), [])

    def test_a_scene_gap_is_asked_again_never_fixed_in_code(self):
        """founder, 2026-11-03: even a one-line gap between scenes goes back to the model."""
        f = facts(); a = good_answer(f)
        dlg = E.dialogue_ids(f)
        sc = a["scenes"]
        i = dlg.index(sc[1]["first_line"])
        sc[1]["first_line"] = dlg[i + 1]                                   # one line left between scene 1 and scene 2
        errs = E.validate_tier1(a, f, a["possible_non_story"], raw=True)
        self.assertTrue(any("belong to no scene" in e for e in errs), errs)
        self.assertFalse(hasattr(E, "repair_scene_gaps"))

    def test_threads_rules(self):
        """founder, 2026-10-29: threads = at most 3 setups paying off in a LATER scene."""
        f = facts(); a = good_answer(f)
        sc = a["scenes"]
        a["threads"] = [{"setup_lines": f"{sc[0]['first_line']}-{sc[0]['last_line']}", "payoff_scene": sc[-1]["id"], "why": "w"}]
        self.assertEqual(E.validate_tier1(a, f, a["possible_non_story"], raw=True), [])
        b = dict(a); b["threads"] = [{"setup_lines": f"{sc[-1]['first_line']}-{sc[-1]['last_line']}", "payoff_scene": sc[0]["id"], "why": "w"}]
        self.assertTrue(any("must come after the setup" in e for e in E.validate_tier1(b, f, a["possible_non_story"], raw=True)))
        b = dict(a); b["threads"] = [{"setup_lines": "L9998-L9999", "payoff_scene": 99, "why": "w"}]
        self.assertTrue(any("threads[1]" in e for e in E.validate_tier1(b, f, a["possible_non_story"], raw=True)))
        b = dict(a); b["threads"] = a["threads"] * 4
        self.assertTrue(any("at most 3" in e for e in E.validate_tier1(b, f, a["possible_non_story"], raw=True)))
        self.assertIn("threads", E.TIER1)

    def test_tier0_rules(self):
        f = facts(); a = good_answer(f)
        a["cast"] = a["cast"][1:]
        self.assertTrue(any("cast: no entry for raw label(s)" in e for e in E.validate_tier0(a, f, 92.0)))
        a = good_answer(f); a["same_person"] = [{"labels": ["speaker_6", "speaker_99"], "name": "x", "evidence": "x"}]
        self.assertTrue(any("unknown raw label" in e for e in E.validate_tier0(a, f, 92.0)))
        a = good_answer(f); a["split_labels"][0]["people"][0]["lines"] = "L9999"
        self.assertTrue(any("split_labels speaker_5" in e for e in E.validate_tier0(a, f, 92.0)))
        a = good_answer(f); a["reference_times"][0]["times"] = ["9:99.0"]
        self.assertTrue(any("outside the episode" in e or "cannot read" in e for e in E.validate_tier0(a, f, 92.0)))
        a = good_answer(f); a["speakers"] = [x for x in a["speakers"] if x["key"] != "new_1"]
        self.assertTrue(any("needs a speakers entry" in e for e in E.validate_tier1(a, f, a["possible_non_story"], raw=True)))

    def test_missing_speaker_key(self):
        f = facts(); a = good_answer(f)
        a["speakers"] = [s for s in a["speakers"] if s["key"] != "speaker_1"]
        errs = E.validate_tier1(a, f, a["possible_non_story"], raw=True)
        self.assertTrue(any("no speakers entry for key(s) ['speaker_1']" in e for e in errs))

    def test_scene_edge_must_be_dialogue_line(self):
        f = facts(); a = good_answer(f)
        tag = next(r["id"] for r in f["lines"] if r["kind"] == "event")
        a["scenes"][0]["last_line"] = tag
        errs = E.validate_tier1(a, f, a["possible_non_story"], raw=True)
        self.assertTrue(any("not a dialogue line" in e for e in errs))

    def test_scene_order_and_coverage(self):
        f = facts(); a = good_answer(f)
        d = dlg(f)
        a["scenes"][1]["first_line"] = d[2]          # overlaps scene 1
        errs = E.validate_tier1(a, f, a["possible_non_story"], raw=True)
        self.assertTrue(any("must not overlap" in e for e in errs))
        a = good_answer(f)
        a["scenes"][1]["last_line"] = d[len(d) - 12]  # a hole before scene 3
        errs = E.validate_tier1(a, f, a["possible_non_story"], raw=True)
        self.assertTrue(any("belong to no scene" in e for e in errs))
        # the same hole is fine when those lines are non-story
        hole = d[d.index(a["scenes"][1]["last_line"]) + 1:d.index(a["scenes"][2]["first_line"])]
        ns = [{"lines": f"{hole[0]}-{hole[-1]}", "type": "tv_or_radio", "reason": "tv"}]
        self.assertEqual(E.validate_tier1(a, f, ns, raw=True), [])

    def test_unknown_line_and_bad_key(self):
        f = facts(); a = good_answer(f)
        a["suspect_resolutions"].append({"line": "L9999", "labelled": "A", "likely": "B", "reason": "x"})
        a["suspect_resolutions"].append({"line": dlg(f)[0], "labelled": "A", "likely": "ZZ", "likely_key": "ZZ", "reason": "x"})
        errs = E.validate_tier1(a, f, a["possible_non_story"], raw=True)
        self.assertTrue(any("unknown line id 'L9999'" in e for e in errs))
        self.assertTrue(any("likely_key 'ZZ'" in e for e in errs))
        a = good_answer(f)
        a["speakers"].append({"key": "new_2", "evidence_against_prior": "x", "name": "nobody", "status": "new", "evidence": "x"})
        self.assertTrue(any("must list the line ranges" in e for e in E.validate_tier1(a, f, a["possible_non_story"], raw=True)))

    def test_tier2_rules(self):
        f = facts(); a = good_answer(f)
        a["possible_non_story"].append({"lines": "L0001-L9999", "kind": "song", "signals": [], "reason": "x"})
        a["glossary"]["running_bits"] = [f"bit {i}" for i in range(20)]
        errs = E.validate_tier2(a, f, CFG)
        self.assertTrue(any("possible_non_story: unknown line id" in e for e in errs))
        self.assertTrue(any("keep at most 15" in e for e in errs))

    def test_expand_lines(self):
        ids = [f"L{i:04d}" for i in range(1, 11)]
        self.assertEqual(E.expand_lines("L0002-L0004, L0009", ids), (["L0002", "L0003", "L0004", "L0009"], None))
        self.assertIsNotNone(E.expand_lines("L0004-L0002", ids)[1])
        self.assertIsNotNone(E.expand_lines("line 3", ids)[1])


class TestRun(unittest.TestCase):
    def test_single_call_happy_path(self):
        f = facts()
        out, client, dbg, pre = run_with(f, [good_answer(f)])
        self.assertEqual(len(client.calls), 1)
        parts = client.calls[0]["media"]
        self.assertEqual(parts[0], {"text": pre})                         # prefix text first, byte-identical
        self.assertIn("file_uri", parts[1])                               # then the whole-episode video
        self.assertIn("# PART 2", client.calls[0]["prompt"])
        self.assertIn("## LOW-TRUST SUGGESTIONS", pre)
        self.assertIn("[speaker_0]:", pre)                                 # raw labels in the line table
        ep = out["episode_pass"]; c = ep["derived"]["counts"]
        self.assertEqual(ep["prior"], "raw")
        self.assertEqual(c["n_scenes"], 3)
        self.assertEqual(c["n_new_speakers"], 1)
        self.assertEqual((c["n_merges"], c["n_splits"]), (1, 1))
        ls = ep["derived"]["line_speakers"]
        hb = next(r for r in f["lines"] if r["text"] == "హా")
        self.assertEqual(ls[hb["id"]], "speaker_6")                       # speaker_7 merged into speaker_6
        sir = next(r for r in f["lines"] if r["text"] == "Sir నాకు ఆకలి వేస్తుంది")
        self.assertEqual(ls[sir["id"]], "new_1")                           # split out of speaker_5
        self.assertTrue(all(r["id"] in ls for r in f["lines"] if r["kind"] == "speech"))   # every line has a final key
        self.assertEqual(out["stages"]["1b"]["usage"]["input_tokens"], 20000)
        self.assertEqual(out["stages"]["1b"]["usage"]["output_tokens"], 5000)   # thinking billed as output
        self.assertGreater(out["stages"]["1b"]["usage"]["cost_inr"], 0)
        self.assertEqual(dbg["attempts"][0]["tier"], "all")
        self.assertEqual(dbg["attempts"][0]["validation_tier1"], [])
        self.assertNotIn("proposals", ep["derived"])
        self.assertIn("Named 8 speakers", E.plain_summary_1b(ep))
        self.assertIn("label merges", E.plain_summary_1b(ep))

    def test_short_scenes_are_kept(self):
        """founder, 2026-11-03: the model's scenes are used as given; code never merges a short scene."""
        f = facts(); a = good_answer(f)
        dl = E.dialogue_ids(f)
        a["scenes"][2]["last_line"] = dl[-4]
        a["scenes"].append({"id": 4, "first_line": dl[-3], "last_line": dl[-2], "label": "tiny", "register": "comedy", "speakers": ["speaker_6"]})
        a["scenes"].append({"id": 5, "first_line": dl[-1], "last_line": dl[-1], "label": "tail", "register": "comedy", "speakers": ["speaker_5"]})
        self.assertEqual(E.validate_tier1(a, f, a["possible_non_story"], raw=True), [])
        d = E.derive(a, f, raw=True)
        self.assertEqual([x["id"] for x in d["scenes"]], [1, 2, 3, 4, 5])
        self.assertNotIn("merged_short", d)

    def test_tier1_retry_then_pass(self):
        f = facts(); good = good_answer(f)
        d_ = dlg(f); bad = copy.deepcopy(good)
        bad["scenes"][1]["last_line"] = d_[d_.index(good["scenes"][2]["first_line"]) - 5]   # a 4-line hole: too big for the gap repair
        out, client, dbg, _ = run_with(f, [bad, {k: good[k] for k in E.TIER1}])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[1]["meta"]["tier"], "1")
        self.assertIn("belong to no scene", client.calls[1]["prompt"])   # the validator's errors were shown
        self.assertEqual(sorted(client.calls[1]["schema"]["properties"]), sorted(E.TIER0 + E.TIER1))
        self.assertEqual(out["stages"]["1b"]["n_attempts"], 2)
        self.assertEqual(out["stages"]["1b"]["usage"]["input_tokens"], 40000)

    def test_tier1_gives_up_after_max_retries(self):
        f = facts(); good = good_answer(f)
        bad = copy.deepcopy(good); bad["speakers"] = bad["speakers"][1:]
        c2 = copy.deepcopy(CFG); c2["stage1b"]["max_retries_per_tier"] = 2
        with self.assertRaises(E.EpisodePassError) as cm:
            run_with(f, [bad, {k: bad[k] for k in E.TIER0 + E.TIER1}, {k: bad[k] for k in E.TIER0 + E.TIER1}], cfg=c2)
        self.assertIn("Tier 1 failed validation after 2 retries", str(cm.exception))

    def test_tier2_retry_only(self):
        f = facts(); good = good_answer(f)
        bad = copy.deepcopy(good); bad["glossary"]["premise"] = ""
        out, client, dbg, _ = run_with(f, [bad, {k: good[k] for k in E.TIER2}])
        self.assertEqual(client.calls[1]["meta"]["tier"], "2")
        self.assertEqual(out["episode_pass"]["glossary"]["premise"], "jobless engineer")

    def test_refuses_without_current_stage1a(self):
        f = facts(); f["stages"]["1a"]["version"] = "stage1a/0.0"
        with self.assertRaises(RuntimeError) as cm:
            run_with(f, [good_answer(f)])
        self.assertIn("re-run Stage 1A", str(cm.exception))
        with self.assertRaises(RuntimeError):
            run_with({"stages": {}}, [])

    def test_estimate_cost_without_network(self):
        f = facts()
        est = E.estimate_cost(f, CFG, counter=lambda t: len(t) // 3)
        self.assertGreater(est["input_tokens"], est["prefix_tokens"] > 0)
        self.assertGreater(est["video_tokens"], 0)                        # the whole-episode video is in the estimate
        self.assertGreater(est["inr_worst"], est["inr_expected"] > 0)


class TestGlossary(unittest.TestCase):
    def test_merge_appends_never_deletes(self):
        old = {"premise": "p", "nicknames": [{"name": "Arjun", "nickname": "Virgin"}], "running_bits": ["a"], "episodes": ["v1"]}
        new = {"premise": "other", "nicknames": [{"name": "arjun", "nickname": "virgin"}, {"name": "Surya", "nickname": "sleeper cell"}],
               "running_bits": ["a", "b"], "catchphrases": ["c"], "relationships": []}
        m = glossary.merge_glossary(old, new, "v2")
        self.assertEqual(m["premise"], "p")
        self.assertEqual(len(m["nicknames"]), 2)
        self.assertEqual(m["running_bits"], ["a", "b"])
        self.assertEqual(m["catchphrases"], ["c"])
        self.assertEqual(m["episodes"], ["v1", "v2"])
        m2 = glossary.merge_glossary(m, new, "v2")
        self.assertEqual(len(m2["nicknames"]), 2)

    def test_glossary_saved_and_used_as_prior(self):
        f = facts()
        inputs = make_inputs()
        with tempfile.TemporaryDirectory() as td:
            inputs.paths = SimpleNamespace(run_dir=Path(td), mp4=Path(td) / "ep.mp4", title=lambda: "x")
            c2 = copy.deepcopy(CFG); c2["stage1b"]["glossary_dir"] = td
            E.run(inputs, f, c2, client=FakeClient([good_answer(f)]), cutter=_cutter, uploader=_uploader, deleter=_deleter)
            self.assertTrue((Path(td) / "surya.json").exists())
            # second episode of the same series: the prior is in the prefix
            client = FakeClient([good_answer(f)])
            E.run(inputs, f, c2, client=client, cutter=_cutter, uploader=_uploader, deleter=_deleter)
            self.assertIn("## SERIES GLOSSARY FROM EARLIER EPISODES", client.calls[0]["media"][0]["text"])


class TestAcceptance(unittest.TestCase):
    def test_checker_kinds(self):
        f = facts()
        out, _, _, _ = run_with(f, [good_answer(f)])
        by_text = {r["text"]: r["id"] for r in f["lines"] if r["kind"] == "speech"}
        s3 = by_text["Sir నాకు ఆకలి వేస్తుంది"]
        ref = {"checks": [
            {"kind": "content_type", "expect": "scripted_series"},
            {"kind": "scene_count", "min": 3, "max": 3},
            {"kind": "scene_edge_at", "t": 60.0, "tol": 1.0},
            {"kind": "no_scene_edge_at", "t": 45.0, "tol": 1.0},
            {"kind": "lines_not_speaker", "lines": s3, "not_name": "Arjun", "min_share": 1.0, "new_speaker": True},
            {"kind": "line_not_speaker", "line": by_text["హా"], "not_name": "Nobody", "expect_any": ["customer"]},
            {"kind": "line_reviewed", "line": by_text["హా"]},
            {"kind": "line_reviewed", "line": by_text["ఏంటి?"]},
            {"kind": "non_story_lines", "lines": [by_text["Happy birthday to you"]], "type_any": ["song"]},
            {"kind": "story_lines", "lines": by_text["ఏంటి?"]},
            {"kind": "story_lines", "lines": by_text["Happy birthday to you"]},
            {"kind": "same_person", "line_a": by_text["హా"], "line_b": by_text["ఏంటి?"]},
            {"kind": "speaker_names_include", "names": ["Surya", "Lakshmi|Amma", "delivery customer|customer", "Nobody"]},
            {"kind": "bogus"},
        ]}
        rows = acceptance.evaluate_1b(out, ref)
        st = {r["#"]: r["status"] for r in rows}
        self.assertEqual([st[i] for i in range(1, 8)], ["agree"] * 7, rows[:7])
        self.assertEqual(st[8], "disagree")        # 'ఏంటి?' was never reviewed
        self.assertEqual(st[9], "agree")
        self.assertEqual(st[10], "agree")          # 'ఏంటి?' is story dialogue
        self.assertEqual(st[11], "disagree")       # the song line is flagged non-story, so it is not story dialogue
        self.assertEqual(st[12], "agree")          # 'హా' (speaker_7) and 'ఏంటి?' (speaker_6) merged into one person
        self.assertEqual(st[13], "disagree")       # 'Nobody' is not a speaker
        self.assertEqual(st[14], "error")
        self.assertEqual(acceptance.summary(rows)["total"], 14)
        self.assertEqual(acceptance.evaluate_1b(f, ref)[0]["status"], "missing")   # no 1B output yet


if __name__ == "__main__":
    unittest.main()
