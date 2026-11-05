"""Video step per line + naming check (founder, 2026-11-01 / 2026-11-02): the video step says per spoken line who talks
and who is on screen; one text call decides who says every line; code only checks ids / keys / quoted words and writes
the answer. Synthetic data, fake client (no network)."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from selection_v2 import cast_sheet as CS, identity_pass as IDP, naming_check as NC
from selection_v2.runner import validate_facts
from tests.v2.synthetic import make_inputs
from tests.v2.test_hybrid import CFG as CFG_HYBRID, facts_1b
from tests.v2.test_stage1b import FakeClient, _cutter, _deleter, _uploader

CFG = copy.deepcopy(CFG_HYBRID); CFG["naming_check"]["enabled"] = True


def speech(f):
    return [r for r in f["lines"] if r["kind"] == "speech"]


def text_keys(f, base):
    return sorted({NC._key_of(r, base) for r in speech(f)})


def video_answer(f, base=None):
    """The video step agreeing with the text step on every line, each person alone on screen while talking."""
    base = base or NC.base_of(f)
    people = [{"seen": "face", "key": k, "name": f"Person {k}", "role": "r", "gender": "m", "look": f"{k} in a blue shirt"} for k in text_keys(f, base)]
    lines = [{"id": r["id"], "talk": [NC._key_of(r, base)], "see": [NC._key_of(r, base)]} for r in speech(f)]
    return {"people": people, "lines": lines}


def identity_record(f, ans=None):
    ans = ans or video_answer(f)
    errs, warns, lines = IDP.validate(ans, f, NC.base_of(f))
    assert not errs, errs
    return {"people": ans["people"], "lines": lines, "warnings": warns}


def check_answer(f, base):
    names = NC._names(f, base)
    return {"people": [{"key": k, "name": names[k] or k, "role": "r", "gender": "m", "aka": [], "video_keys": [k], "main": i < 2}
                       for i, k in enumerate(text_keys(f, base))], "changes": [], "splits": [], "keep_text": [], "not_sure": []}


def two_word_line(f):
    return next(r for r in speech(f) if len(r["text"].split()) >= 2)


class TestVideoStepFormat(unittest.TestCase):
    def test_checks_are_format_only(self):
        f = facts_1b(); base = NC.base_of(f)
        good = video_answer(f)
        self.assertEqual(IDP.validate(good, f, base)[0], [])
        bad = copy.deepcopy(good); bad["people"] = bad["people"][1:]
        self.assertTrue(any("missing" in e for e in IDP.validate(bad, f, base)[0]))
        bad = copy.deepcopy(good); bad["people"].append({"seen": "x", "key": "stranger", "name": "S", "role": "", "gender": "m", "look": ""})
        self.assertTrue(any("new key V1" in e for e in IDP.validate(bad, f, base)[0]))
        bad = copy.deepcopy(good); bad["lines"][0]["see"] = ["V9"]
        self.assertTrue(any("not in people" in e for e in IDP.validate(bad, f, base)[0]))
        bad = copy.deepcopy(good); bad["lines"] = bad["lines"][: len(bad["lines"]) // 2]
        self.assertTrue(any("have no entry" in e for e in IDP.validate(bad, f, base)[0]))
        few = copy.deepcopy(good); few["lines"] = few["lines"][1:]
        errs, warns, _ = IDP.validate(few, f, base)
        self.assertEqual(errs, []); self.assertTrue(any("without an entry" in w for w in warns))   # a slip is a warning

    def test_turns_find_the_quoted_word_or_are_dropped(self):
        f = facts_1b(); base = NC.base_of(f)
        r = two_word_line(f); w = r["text"].split()[1]
        ans = video_answer(f)
        e = next(x for x in ans["lines"] if x["id"] == r["id"])
        e["turns"] = [{"at": w, "talk": [text_keys(f, base)[0]]}]; e["why"] = "second voice"
        _, warns, lines = IDP.validate(ans, f, base)
        self.assertEqual(lines[r["id"]]["turns"][0]["word"], 1)
        e["turns"] = [{"at": "nosuchword", "talk": [text_keys(f, base)[0]]}]
        _, warns, lines = IDP.validate(ans, f, base)
        self.assertEqual(lines[r["id"]]["turns"], []); self.assertTrue(any("turns whose word" in w for w in warns))


class TestNamingCheck(unittest.TestCase):
    def test_disagreements_must_be_answered(self):
        f = facts_1b(); base = NC.base_of(f)
        keys = text_keys(f, base)
        vans = video_answer(f)
        r0 = speech(f)[0]
        other = next(k for k in keys if k != NC._key_of(r0, base))
        vans["lines"][0]["talk"] = [other]; vans["lines"][0]["why"] = "mouth"
        idn = identity_record(f, vans)
        self.assertEqual(NC.disagreements(f, base, idn), {r0["id"]})
        self.assertIn(f"{r0['id']} ≠", NC.transcript_block(f, base, idn))
        ans = check_answer(f, base)
        self.assertTrue(any("not answered" in e for e in NC.validate(ans, f, base, idn)))
        ans["keep_text"] = [{"lines": [r0["id"]], "why": "the transcript says so"}]
        self.assertEqual(NC.validate(ans, f, base, idn), [])

    def test_video_keys_and_split_words_are_checked(self):
        f = facts_1b(); base = NC.base_of(f)
        vans = video_answer(f)
        vans["people"].append({"seen": "a stranger", "key": "V1", "name": "Delivery man", "role": "", "gender": "m", "look": ""})
        vans["lines"][0]["see"] = vans["lines"][0]["see"] + ["V1"]
        idn = identity_record(f, vans)
        ans = check_answer(f, base)
        self.assertTrue(any("belong to no person" in e for e in NC.validate(ans, f, base, idn)))
        ans["people"].append({"key": "N1", "name": "Delivery man", "role": "stranger", "gender": "m", "aka": [], "video_keys": ["V1"], "main": False})
        self.assertEqual(NC.validate(ans, f, base, idn), [])
        ans["splits"] = [{"line": two_word_line(f)["id"], "at": "nosuchword", "to": ["N1"], "why": "x"}]
        self.assertTrue(any("is not in line" in e for e in NC.validate(ans, f, base, idn)))

    def test_apply_groups_splits_on_screen_and_alone(self):
        f = facts_1b(); base = NC.base_of(f)
        keys = text_keys(f, base)
        self.assertGreaterEqual(len(keys), 2)
        r = two_word_line(f); n = len(r["text"].split()); w = r["text"].split()[-1]
        k_r = NC._key_of(r, base); other = next(k for k in keys if k != k_r)
        vans = video_answer(f)
        vans["people"].append({"seen": "a stranger", "key": "V1", "name": "Delivery man", "role": "", "gender": "m", "look": "red cap"})
        e = next(x for x in vans["lines"] if x["id"] == r["id"])
        e.update({"see": ["V1", other], "turns": [{"at": w, "talk": [other]}], "why": "a second voice on the last word"})
        idn = identity_record(f, vans)
        ans = check_answer(f, base)
        ans["people"].append({"key": "N1", "name": "Delivery man", "role": "stranger", "gender": "m", "aka": [], "video_keys": ["V1"], "main": False})
        group = next(x for x in speech(f) if x["id"] != r["id"])
        ans["changes"] = [{"why": "they say it together", "lines": [group["id"]], "to": [keys[0], keys[1]]}]
        ans["splits"] = [{"line": r["id"], "at": w, "to": [other], "why": "the last word is the other voice"}]
        self.assertEqual(NC.validate(ans, f, base, idn), [])
        out, summ = NC.apply(f, ans, base, idn)
        validate_facts(out)
        d = out["episode_pass"]["derived"]
        self.assertEqual(d["line_voices"][r["id"]], [{"word": 0, "keys": [k_r]}, {"word": n - 1, "keys": [other]}])
        self.assertEqual(d["line_speakers"][r["id"]], k_r)                      # the longer part (or, on a tie, the first) is the main speaker
        self.assertEqual(d["line_voices"][group["id"]], [{"word": 0, "keys": [keys[0], keys[1]]}])
        self.assertEqual(d["on_screen"][r["id"]], ["N1", other])                 # the video key maps through video_keys
        self.assertNotIn(r["id"], summ["alone_on_screen"].get(other, []))
        plain = next(x for x in speech(f) if x["id"] not in (r["id"], group["id"]))
        self.assertIn(plain["id"], summ["alone_on_screen"][NC._key_of(plain, base)])
        self.assertEqual(next(c for c in summ["cast"] if c["key"] == "N1")["look"], "red cap")
        self.assertEqual(d["line_speakers_text"], base["line_speakers"])

    def test_same_as_moves_lines_and_rerun_starts_from_base(self):
        f = facts_1b(); base = NC.base_of(f); keys = text_keys(f, base)
        idn = identity_record(f)
        ans = check_answer(f, base)
        ans["people"][1]["same_as"] = keys[0]; ans["people"][1]["video_keys"] = []
        ans["people"][0]["video_keys"] = [keys[0], keys[1]]
        self.assertEqual(NC.validate(ans, f, base, idn), [])
        out, summ = NC.apply(f, ans, base, idn)
        self.assertNotIn(keys[1], set(out["episode_pass"]["derived"]["line_speakers"].values()))
        self.assertEqual(summ["status"][keys[1]], "merged")
        with tempfile.TemporaryDirectory() as td:
            cfg = copy.deepcopy(CFG); cfg["paths"]["runs_log"] = str(Path(td) / "runs.csv")
            once = NC.check("VID", f, idn, cfg, Path(td), client=FakeClient([ans]))
            twice = NC.check("VID", once, idn, cfg, Path(td), client=FakeClient([check_answer(f, base)]))
        self.assertEqual(twice["naming_check"]["base"], base)
        self.assertIn(keys[1], set(twice["episode_pass"]["derived"]["line_speakers"].values()))


class TestVideoStepThenNamingCheck(unittest.TestCase):
    def run_identity(self, answers):
        f = facts_1b()
        inputs = make_inputs()
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        inputs.paths = SimpleNamespace(run_dir=Path(td.name), mp4=Path(td.name) / "ep.mp4", title=lambda: "Synthetic")
        cfg = copy.deepcopy(CFG); cfg["paths"]["runs_log"] = str(Path(td.name) / "runs.csv")
        client = FakeClient(answers(f))
        return f, IDP.run(inputs, f, cfg, client=client, cutter=_cutter, uploader=_uploader, deleter=_deleter), client

    def test_video_first_then_text_only_check(self):
        f, out, client = self.run_identity(lambda f: [video_answer(f), check_answer(f, NC.base_of(f))])
        validate_facts(out)
        self.assertEqual([c["role"] for c in client.calls], ["identity_pass", "naming_check"])
        parts = client.calls[0]["media"]
        self.assertIn("file_uri", parts[0])                                   # the video first, then the text about it
        ctx = parts[1]["text"]
        self.assertIn("PART 1: PEOPLE THE TEXT STEP FOUND", ctx); self.assertIn("PART 2: TRANSCRIPT BY SCENE", ctx)
        sc = f["episode_pass"]["derived"]["scenes"][0]
        self.assertNotIn(str(sc.get("label") or "§never§"), ctx)              # no scene titles for the video step
        self.assertIsNone(client.calls[1]["media"])
        self.assertIn("PART C: TRANSCRIPT BY SCENE", client.calls[1]["prompt"])
        self.assertEqual(len(out["identity_pass"]["lines"]), len(speech(f)))
        self.assertIn("on_screen", out["episode_pass"]["derived"])
        self.assertIn("naming_check", out)

    def test_failed_check_keeps_the_paid_video_answer(self):
        bad = {"people": [], "changes": [], "splits": [], "keep_text": [], "not_sure": []}
        f, out, client = self.run_identity(lambda f: [video_answer(f), bad, bad])
        self.assertEqual(len(client.calls), 3)                                # video + check + one retry
        self.assertTrue(out["identity_pass"]["lines"])
        self.assertTrue(any("naming check failed" in w for w in out["identity_pass"]["warnings"])); self.assertNotIn("naming_check", out)

    def test_bad_video_answer_retries_then_fails(self):
        bad = {"people": [], "lines": []}
        with self.assertRaises(IDP.IdentityPassError):
            self.run_identity(lambda f: [bad, bad])


class TestVideoAnswerIsFinal(unittest.TestCase):
    """founder, 2026-11-02: no naming check; the video step's answer is final (bookkeeping only)."""
    def test_video_answer_written_as_the_speaker_map(self):
        f = facts_1b(); base = NC.base_of(f); keys = text_keys(f, base)
        r = two_word_line(f); w = r["text"].split()[-1]
        other = next(k for k in keys if k != NC._key_of(r, base))
        vans = video_answer(f)
        vans["people"][0]["name"] = "Gowtham"
        vans["people"].append({"seen": "a stranger", "key": "V1", "name": "Delivery man", "role": "", "gender": "m", "look": "red cap"})
        e = next(x for x in vans["lines"] if x["id"] == r["id"])
        e.update({"talk": ["V1"], "see": ["V1"], "turns": [{"at": w, "talk": [other]}], "why": "seen"})
        cant = next(x for x in vans["lines"] if x["id"] != r["id"]); cant["talk"] = []
        out = IDP.finalize(f, identity_record(f, vans), base)
        validate_facts(out)
        d = out["episode_pass"]["derived"]
        self.assertEqual(d["line_voices"][r["id"]][0]["keys"], ["V1"]); self.assertEqual(d["line_voices"][r["id"]][1]["keys"], [other])
        self.assertEqual(d["line_speakers"][cant["id"]], NC._key_of(next(x for x in speech(f) if x["id"] == cant["id"]), base))   # cannot tell: text step's person
        self.assertEqual({s["key"]: s["name"] for s in out["episode_pass"]["speakers"]}[keys[0]], "Gowtham")
        self.assertIn(r["id"], NC.speaker_summary(out)["alone_on_screen"]["V1"])
        self.assertEqual(NC.base_of(out), base)                                   # a rerun starts from the text step again

    def test_a_person_left_out_keeps_their_name(self):
        f = facts_1b(); base = NC.base_of(f); keys = text_keys(f, base)
        vans = video_answer(f)
        gone = keys[-1]
        vans["people"] = [x for x in vans["people"] if x["key"] != gone]
        for e in vans["lines"]:
            if e["talk"] == [gone]:
                e["talk"], e["see"] = [], []                                     # it could not tell: the text step's person stays
        errs, warns, _ = IDP.validate(vans, f, base)
        self.assertEqual(errs, []); self.assertTrue(any("not in people" in w for w in warns))
        out = IDP.finalize(f, identity_record(f, vans), base)
        self.assertEqual({s["key"]: s["name"] for s in out["episode_pass"]["speakers"]}[gone], NC._names(f, base)[gone])

    def test_run_without_naming_check_makes_one_call(self):
        f = facts_1b()
        inputs = make_inputs()
        with tempfile.TemporaryDirectory() as td:
            inputs.paths = SimpleNamespace(run_dir=Path(td), mp4=Path(td) / "ep.mp4", title=lambda: "Synthetic")
            cfg = copy.deepcopy(CFG); cfg["paths"]["runs_log"] = str(Path(td) / "runs.csv"); cfg["naming_check"]["enabled"] = False
            client = FakeClient([video_answer(f)])
            out = IDP.run(inputs, f, cfg, client=client, cutter=_cutter, uploader=_uploader, deleter=_deleter)
        self.assertEqual(len(client.calls), 1)
        validate_facts(out)
        self.assertIn("speakers_from_video", out); self.assertNotIn("naming_check", out)
        self.assertTrue(CS.candidate_lines(out, cfg))


class TestCastSheetUsesAloneLines(unittest.TestCase):
    def test_candidates_only_where_alone_on_screen(self):
        f = facts_1b(); base = NC.base_of(f)
        out, summ = NC.apply(f, check_answer(f, base), base, identity_record(f))
        out["naming_check"] = summ
        cands = CS.candidate_lines(out, CFG)
        alone = {x for ids in summ["alone_on_screen"].values() for x in ids}
        self.assertTrue(cands)
        self.assertTrue(all(c["line"] in alone for cs in cands.values() for c in cs))
        out["naming_check"] = {**summ, "alone_on_screen": {}}
        self.assertEqual(CS.candidate_lines(out, CFG), {})                     # never seen alone: no frame


if __name__ == "__main__":
    unittest.main()


class TestVideoStepScenes(unittest.TestCase):
    """founder, 2026-11-03 test: the video step also gives the scenes, checking the text step's split (check) or
    finding them with no split sent (find). Code checks ids only."""
    def run_mode(self, mode, scenes):
        f = facts_1b()
        inputs = make_inputs()
        with tempfile.TemporaryDirectory() as td:
            inputs.paths = SimpleNamespace(run_dir=Path(td), mp4=Path(td) / "ep.mp4", title=lambda: "Synthetic")
            cfg = copy.deepcopy(CFG); cfg["paths"]["runs_log"] = str(Path(td) / "runs.csv"); cfg["naming_check"]["enabled"] = False
            cfg["identity_pass"]["scenes"] = mode
            ans = video_answer(f); ans["scenes"] = scenes(f)
            client = FakeClient([ans])
            out = IDP.run(inputs, f, cfg, client=client, cutter=_cutter, uploader=_uploader, deleter=_deleter)
        return f, out, client

    def test_find_sends_no_scene_split_and_stores_scenes(self):
        mid = lambda f: f["lines"][len(f["lines"]) // 2]["id"]
        f, out, client = self.run_mode("find", lambda f: [{"first": f["lines"][0]["id"], "where": "home", "who": [], "why": "start"},
                                                            {"first": mid(f), "where": "office", "who": [], "why": "new place"}])
        ctx = client.calls[0]["media"][1]["text"]
        self.assertIn("TRANSCRIPT IN TIME ORDER", ctx); self.assertNotIn("== Scene", ctx)
        self.assertIn("in time order", client.calls[0]["prompt"]); self.assertIn("split the whole episode into scenes", client.calls[0]["prompt"])
        sc = out["identity_pass"]["scenes"]
        self.assertEqual([s["first_line"] for s in sc], [f["lines"][0]["id"], mid(f)])
        self.assertEqual(sc[-1]["last_line"], f["lines"][-1]["id"])
        self.assertEqual(out["identity_pass"]["scenes_mode"], "find")

    def test_check_sends_the_text_split(self):
        f, out, client = self.run_mode("check", lambda f: [{"first": f["lines"][0]["id"], "where": "home", "who": [], "why": "start"}])
        self.assertIn("== Scene", client.calls[0]["media"][1]["text"])
        self.assertIn("came from the text step", client.calls[0]["prompt"])

    def test_scene_ids_are_checked(self):
        f = facts_1b()
        errs, warns, sc = IDP.validate_scenes({"scenes": [{"first": "nope", "who": []}]}, f, set())
        self.assertTrue(errs)
        ids = [r["id"] for r in f["lines"]]
        errs, warns, sc = IDP.validate_scenes({"scenes": [{"first": ids[3], "who": ["Z"]}, {"first": ids[1], "who": []}, {"first": "x", "who": []}]}, f, set())
        self.assertEqual(errs, [])
        self.assertEqual([s["first_line"] for s in sc], [ids[0], ids[3]])     # sorted; lines before the first go to scene 1
        self.assertEqual(len(warns), 4)

    def test_scene_ends_leave_gaps(self):
        f = facts_1b(); ids = [r["id"] for r in f["lines"]]
        ans = {"scenes": [{"first": ids[0], "last": ids[2], "who": [], "after": "title card"},
                          {"first": ids[5], "last": ids[1], "who": []}]}                 # a last before its first: a slip
        errs, warns, sc = IDP.validate_scenes(ans, f, set())
        self.assertEqual(errs, [])
        self.assertEqual((sc[0]["last_line"], sc[0]["gap"], sc[0]["after"]), (ids[2], [ids[3], ids[4]], "title card"))
        self.assertEqual((sc[1]["last_line"], sc[1]["gap"]), (ids[-1], None))
        self.assertTrue(any("last line" in w for w in warns))
