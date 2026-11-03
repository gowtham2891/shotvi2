"""Stage 2 tests with fakes (no network, no ffmpeg): prefix determinism, ffmpeg command, validation, retry path,
speaker-map corrections, thin_diarization, estimate, acceptance kinds."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from selection_v2 import acceptance, media, prefix, stage1_facts as S, stage2_beats as S2
from selection_v2.config import load_cast_lists, load_config
from tests.v2.synthetic import make_inputs
from tests.v2.test_stage1b import FakeClient, good_answer, run_with as run_1b

CFG = copy.deepcopy(load_config()); CFG["stage1a"]["context_window_s"] = 8
CAST = load_cast_lists()


def facts_after_1b():
    f = S.run(make_inputs(), None, CFG, CAST)
    out, _, _, _ = run_1b(f, [good_answer(f)])
    return out


def scene(f, i):
    return f["episode_pass"]["derived"]["scenes"][i]


def good_sheet(f, sc):
    dlg = S2.scene_dialogue_ids(f, sc)
    rows = S2.scene_lines(f, sc)
    names, over = S2.map_names(f)
    keys = sorted({S2.line_key(r, over) for r in rows if r["kind"] == "speech" and S2.line_key(r, over)})
    gaps = [g for g in f["gaps"] if g["prev"] in set(dlg) and g["next"] in set(dlg) and g["gap"] >= 0.3]
    nd = [{"after_line": g["prev"], "before_line": g["next"], "kind": "action_beat", "bgm": "none", "recommend": "keep", "reason": "x"} for g in gaps[:2]]
    return {"scene_id": sc["id"], "register": "comedy", "self_contained": True, "setup": "s",
            "beats": [{"line": dlg[0], "type": "setup"}, {"line": dlg[-1], "type": "payoff"}],
            "payoff": {"line": dlg[-1], "note": "n"}, "reaction": {"type": "laugh", "after_line": dlg[-1], "by": "all"},
            "context_required": [], "callbacks": [], "dynamic": {"characters": keys, "relationship": "friends", "reveals": "r"},
            "delivery_notes": "d", "clip_hints": {"natural_open": [dlg[0]], "natural_close": [dlg[-1]]},
            "non_dialogue": nd, "skippable_lines": [{"line": dlg[1], "reason": "filler"}] if len(dlg) > 2 else [],
            "speakers_seen": [{"key": k, "on_screen": True, "matches_map": True, "voice_source": "on_screen", "who": names.get(k, k), "evidence": "seen"} for k in keys],
            "non_story_confirm": [], "other_device_audio": [], "confidence": 0.9, "needs_video_default_res": False}


class FakeMedia:
    def __init__(self):
        self.cuts, self.uploads = [], []

    def cutter(self, src, media_dir, sid, start, end, mode, max_h, log=None):
        self.cuts.append((sid, start, end, mode, max_h))
        return Path(media_dir) / f"scene_{sid}.{mode}"

    def uploader(self, path, mode):
        self.uploads.append((str(path), mode))
        return {"uri": f"files/fake{len(self.uploads)}", "name": f"files/fake{len(self.uploads)}", "mime_type": "video/mp4"}

    def deleter(self, uploaded):
        self.deleted = getattr(self, "deleted", []) + [uploaded.get("name")]
        return True

    def framer(self, src, media_dir, key, t, width=320, log=None):
        p = Path(media_dir) / f"ref_{key}_{t:.1f}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\xff\xd8fakejpeg\xff\xd9")
        return p


def run_s2(f, answers, scene_ids=None, cfg=None):
    inputs = make_inputs()
    with tempfile.TemporaryDirectory() as td:
        inputs.paths = SimpleNamespace(run_dir=Path(td), mp4=Path(td) / "ep.mp4", title=lambda: "x")
        (Path(td) / "prefix.txt").write_text(prefix.build_prefix(f, None, cfg or CFG), encoding="utf-8")
        fm = FakeMedia(); client = FakeClient(answers)
        c2 = copy.deepcopy(cfg or CFG); c2["paths"]["runs_log"] = str(Path(td) / "runs.csv")
        out = S2.run(inputs, f, c2, client=client, scene_ids=scene_ids, cutter=fm.cutter, uploader=fm.uploader, framer=fm.framer, deleter=fm.deleter)
        dbg = json.loads((Path(td) / "stage2_debug.json").read_text(encoding="utf-8"))
        p2 = (Path(td) / "prefix_stage2.txt").read_text(encoding="utf-8")
        log = (Path(td) / "runs.csv").read_text(encoding="utf-8")
        return out, client, fm, dbg, p2, log


class TestPrefixAndMedia(unittest.TestCase):
    def test_prefix2_is_prefix1_plus_results_and_deterministic(self):
        f = facts_after_1b()
        p1 = prefix.build_prefix(f, None, CFG)
        a, b = prefix.build_prefix_stage2(f, p1), prefix.build_prefix_stage2(copy.deepcopy(f), p1)
        self.assertEqual(a, b)
        self.assertTrue(a.startswith(p1.rstrip("\n")))
        self.assertIn("# PART 1B: EPISODE PASS RESULTS", a)
        self.assertIn("## SPEAKER MAP", a)
        self.assertIn("## SCENES", a)

    def test_ffmpeg_cmd(self):
        cmd = media.ffmpeg_cmd(Path("in.mp4"), Path("out.mp4"), 10.0, 20.5, "video", 480)
        self.assertEqual(cmd[:2], ["ffmpeg", "-y"])
        self.assertIn("-ss", cmd); self.assertEqual(cmd[cmd.index("-ss") + 1], "10.000")
        self.assertEqual(cmd[cmd.index("-to") + 1], "20.500")
        self.assertIn("scale=-2:'min(480,ih)'", cmd)
        self.assertIn("libx264", cmd); self.assertIn("aac", cmd)
        a = media.ffmpeg_cmd(Path("in.mp4"), Path("out.mp3"), 0, 5, "audio")
        self.assertIn("-vn", a); self.assertIn("16000", a); self.assertIn("libmp3lame", a)
        with self.assertRaises(ValueError):
            media.ffmpeg_cmd(Path("a"), Path("b"), 5, 5)
        self.assertEqual(media.scene_media_path(Path("m"), 3, 10.0, 20.0, "video").name, "scene_03_10.0-20.0_video.mp4")
        part = media.media_part({"uri": "files/x", "name": "files/x"}, "video", 1)
        self.assertEqual(part["file_uri"], "files/x"); self.assertEqual(part["fps"], 1.0); self.assertIn("key", part)


class TestThinDiarization(unittest.TestCase):
    def test_stats_and_flag(self):
        f = facts_after_1b()
        sc = scene(f, 2)      # the delivery scene in the synthetic: new_1 reassigned the first line
        st = S2.scene_stats(f, sc, CFG)
        self.assertGreaterEqual(st["n_reassigned"], 1)
        self.assertIn("reassigned_fraction", st)
        lines = [{"id": "a", "kind": "speech", "zero_len": False, "speaker": "speaker_5", "speaker_key": "J"},
                 {"id": "b", "kind": "speech", "zero_len": False, "speaker": "speaker_5", "speaker_key": "D"},
                 {"id": "c", "kind": "speech", "zero_len": False, "speaker": "speaker_5", "speaker_key": "J"}]
        st = S.diarization_stats(lines, ["a", "b", "c"])
        self.assertEqual((st["n_raw_labels"], st["n_map_speakers"], st["n_reassigned"]), (1, 2, 1))
        self.assertTrue(S.is_thin_diarization(st))      # one label, two people
        lines[1]["speaker"] = "speaker_6"
        st = S.diarization_stats(lines, ["a", "b", "c"])
        self.assertFalse(S.is_thin_diarization(st))


class TestAnchorCriterion(unittest.TestCase):
    def test_anchored_segment_makes_scene_thin(self):
        f = facts_after_1b()
        sc = scene(f, 1)       # the friends' scene: C and D are mutual exclusive partners -> anchored in 1A
        st = S2.scene_stats(f, sc, CFG)
        self.assertTrue(st["anchored"])
        self.assertTrue(st["thin"])
        self.assertTrue(any(r.startswith("1A anchor") for r in st["thin_reasons"]))
        c2 = copy.deepcopy(CFG); c2["stage2"]["thin_diarization"]["anchored_segments"] = False
        st2 = S2.scene_stats(f, sc, c2)
        self.assertEqual(st2["anchored"], [])


class TestReferenceFrames(unittest.TestCase):
    def test_reference_lines_prefer_clean_scenes(self):
        f = facts_after_1b()
        refs = S2.reference_lines(f, CFG)
        self.assertTrue(set(refs) >= {"speaker_0", "speaker_1"})
        self.assertNotIn("speaker_2", refs); self.assertNotIn("speaker_3", refs)   # their lines are 1A-anchored: no reference
        self.assertTrue(all(r["t"] > 0 and r["line"].startswith("L") for rs in refs.values() for r in rs))
        self.assertTrue(all(len(rs) <= 3 for rs in refs.values()))
        import yaml, tempfile, copy as _c
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "synthetic.yaml").write_text(yaml.safe_dump({"speaker_0": ["0:05.0", "0:10.0"]}), encoding="utf-8")
            c2 = _c.deepcopy(CFG); c2["stage2"]["reference_frames"]["manual_dir"] = td
            man = S2.manual_references(f, c2)
            self.assertEqual([r["t"] for r in man["speaker_0"]], [5.0, 10.0])
            self.assertEqual(man["speaker_0"][0]["why"], "pinned by hand")
        self.assertIn("frame_cmd", dir(media))
        cmd = media.frame_cmd(Path("a.mp4"), Path("f.jpg"), 12.34, 320)
        self.assertEqual(cmd[cmd.index("-ss") + 1], "12.340"); self.assertIn("scale=320:-2", cmd)


class TestValidation(unittest.TestCase):
    def test_good_sheet_passes(self):
        f = facts_after_1b(); sc = scene(f, 1)
        self.assertEqual(S2.validate(good_sheet(f, sc), f, sc), [])

    def test_rules(self):
        f = facts_after_1b(); sc = scene(f, 1)
        dlg = S2.scene_dialogue_ids(f, sc)
        other = S2.scene_dialogue_ids(f, scene(f, 0))[0]
        a = good_sheet(f, sc); a["beats"].append({"line": other, "type": "turn"})
        self.assertTrue(any("not a dialogue line of this scene" in e for e in S2.validate(a, f, sc)))
        a = good_sheet(f, sc); a["payoff"]["line"] = "L9999"
        self.assertTrue(any("payoff line" in e for e in S2.validate(a, f, sc)))
        a = good_sheet(f, sc); a["non_dialogue"] = [{"after_line": dlg[0], "before_line": dlg[-1], "kind": "transition", "bgm": "", "recommend": "remove", "reason": ""}]
        self.assertTrue(any("not a pause between two consecutive" in e for e in S2.validate(a, f, sc)))
        a = good_sheet(f, sc)
        if a["non_dialogue"]:
            a["non_dialogue"][0].update({"kind": "reaction_hold", "recommend": "remove"})
            self.assertTrue(any("never be removed" in e for e in S2.validate(a, f, sc)))
        a = good_sheet(f, sc); a["skippable_lines"] = [{"line": dlg[-1], "reason": "x"}]   # the payoff line
        self.assertTrue(any("is a beat line" in e for e in S2.validate(a, f, sc)))
        a = good_sheet(f, sc); a["speakers_seen"] = a["speakers_seen"][1:]
        self.assertTrue(any("speakers_seen has no entry" in e for e in S2.validate(a, f, sc)))
        a = good_sheet(f, sc); a["speakers_seen"].append({"key": "ZZ", "on_screen": True, "matches_map": True, "voice_source": "on_screen", "who": "x", "evidence": "x"})
        self.assertTrue(any("does not speak in this scene" in e for e in S2.validate(a, f, sc)))
        a = good_sheet(f, sc); a["scene_id"] = 99
        self.assertTrue(any("scene_id must be" in e for e in S2.validate(a, f, sc)))
        a = good_sheet(f, sc); a["button_line"] = dlg[0]                        # founder, 2026-10-29: after the payoff only
        self.assertTrue(any("must come after the payoff" in e for e in S2.validate(a, f, sc)))
        a = good_sheet(f, sc); a["button_line"] = "L9999"
        self.assertTrue(any("button_line" in e for e in S2.validate(a, f, sc)))
        a = good_sheet(f, sc); a["button_line"] = None
        self.assertEqual(S2.validate(a, f, sc), [])


class TestSituationsAndVisualBeats(unittest.TestCase):
    def test_rules(self):
        """founder, 2026-10-30: situations in order on scene dialogue lines; visual beats on the episode clock inside the scene."""
        f = facts_after_1b(); sc = scene(f, 1)
        dlg = S2.scene_dialogue_ids(f, sc)
        a = good_sheet(f, sc)
        a["situations"] = [{"start_line": dlg[0], "end_line": dlg[1], "what_happens": "x", "strength": 6},
                           {"start_line": dlg[2], "end_line": dlg[-1], "what_happens": "y", "strength": 8}]
        mid = (sc["start"] + sc["end"]) / 2
        a["visual_beats"] = [{"range": f"{S2.stage1_facts.fmt_ts(mid)}-{S2.stage1_facts.fmt_ts(mid + 1)}", "what_is_seen": "a look", "role": "joke", "strength": 7}]
        self.assertEqual(S2.validate(a, f, sc), [])
        b = good_sheet(f, sc); b["situations"] = [{"start_line": dlg[2], "end_line": dlg[0], "what_happens": "x", "strength": 5}]
        self.assertTrue(any("comes after end_line" in e for e in S2.validate(b, f, sc)))
        b = good_sheet(f, sc); b["situations"] = a["situations"][::-1]
        self.assertTrue(any("inside or before the previous situation" in e for e in S2.validate(b, f, sc)))
        b = good_sheet(f, sc); b["visual_beats"] = [{"range": "99:00.0-99:01.0", "what_is_seen": "x", "role": "joke", "strength": 7}]
        self.assertTrue(any("outside the scene" in e for e in S2.validate(b, f, sc)))


class TestRun(unittest.TestCase):
    def test_one_scene_happy_path(self):
        f = facts_after_1b(); sc = scene(f, 1)
        out, client, fm, dbg, p2, log = run_s2(f, [good_sheet(f, sc)], scene_ids=[sc["id"]])
        self.assertEqual(len(client.calls), 1)
        parts = client.calls[0]["media"]
        self.assertEqual(parts[0], {"text": p2})                                # cached prefix TEXT is the first part
        self.assertIn("file_uri", parts[1])                                     # then the scene media
        self.assertTrue(any("REFERENCE FACES" in (x.get("text") or "") for x in parts[2:]))
        self.assertTrue(any(x.get("mime_type") == "image/jpeg" for x in parts))  # then the reference faces
        self.assertIn(f"# PART 2: SCENE {sc['id']}", client.calls[0]["prompt"])  # then the scene text
        self.assertTrue(dbg["reference_frames"])
        self.assertTrue(all(r["t_str"] for r in dbg["reference_frames"]))
        self.assertEqual(fm.cuts[0][:4], (sc["id"], sc["start"], sc["end"], "video"))
        self.assertEqual(fm.uploads[0][1], "video")
        self.assertEqual(len(out["beat_sheets"]), 1)
        sh = out["beat_sheets"][0]
        self.assertEqual(sh["payoff"]["line"], S2.scene_dialogue_ids(f, sc)[-1])
        self.assertIn("t_str", sh["payoff"]); self.assertNotIn("cut_quality", sh["skippable_lines"][0])
        self.assertTrue(all("range" in x for x in sh["non_dialogue"]))
        self.assertEqual(out["stages"]["2"]["scenes_done"], [sc["id"]])
        self.assertIn("2:scene", log)
        self.assertEqual(out["speaker_map"]["n_corrections"], 0)
        self.assertGreater(out["stages"]["2"]["usage"]["cost_inr"], 0)

    def test_retry_then_fail_visibly(self):
        f = facts_after_1b(); sc = scene(f, 1)
        bad = good_sheet(f, sc); bad["payoff"]["line"] = "L9999"
        out, client, _, dbg, _, _ = run_s2(f, [bad, good_sheet(f, sc)], scene_ids=[sc["id"]])
        self.assertEqual(len(client.calls), 2)
        self.assertIn("payoff line 'L9999'", client.calls[1]["prompt"])
        self.assertEqual(len(dbg["scenes"][str(sc["id"])]["attempts"]), 2)
        with self.assertRaises(S2.BeatSheetError) as cm:
            run_s2(f, [bad, bad], scene_ids=[sc["id"]])
        self.assertIn("failed validation after 2 attempts", str(cm.exception))

    def test_thin_scene_gets_the_warning(self):
        f = facts_after_1b(); sc = scene(f, 2)
        for r in f["lines"]:                              # make the scene one Scribe label for several people
            if sc["start"] <= r["start"] <= sc["end"] and r["kind"] == "speech":
                r["speaker"] = "speaker_9"
        out, client, _, _, _, _ = run_s2(f, [good_sheet(f, sc)], scene_ids=[sc["id"]])
        self.assertIn("speaker labels in this scene are unreliable", client.calls[0]["prompt"].lower())
        self.assertTrue(out["beat_sheets"][0]["thin_diarization"])

    def test_speaker_map_corrections_and_revert(self):
        f = facts_after_1b(); sc = scene(f, 2)
        a = good_sheet(f, sc)
        names, over = S2.map_names(f)
        key_customer = next(x["key"] for x in a["speakers_seen"] if names.get(x["key"]) == "Customer")
        a["speakers_seen"] = [x if x["key"] != key_customer else {**x, "matches_map": False, "who": "unknown person", "evidence": "different face"}
                              for x in a["speakers_seen"]]
        a["other_device_audio"] = [{"line": S2.scene_dialogue_ids(f, sc)[-1], "reason": "voice from a phone"}]
        out, _, _, _, _, _ = run_s2(f, [a], scene_ids=[sc["id"]])
        sm = out["speaker_map"]
        self.assertEqual(sm["version"], 2)
        corr = [c for c in sm["corrections"] if c["key"] == key_customer and c["new_key"]]
        self.assertEqual(len(corr), 1)
        self.assertTrue(corr[0]["new_key"].startswith("s2_new_"))
        for lid in corr[0]["lines"]:
            self.assertEqual(sm["line_names"][lid], "unknown person")
        self.assertIn(S2.scene_dialogue_ids(f, sc)[-1], sm["non_story_confirmed"])
        self.assertEqual(sm["base"][key_customer], "Customer")                  # 1B's map untouched
        # revert: flag the correction and rebuild
        out["speaker_map"]["corrections"][0]["reverted"] = True
        rebuilt = S2.build_speaker_map(out)
        self.assertTrue(rebuilt["corrections"][0]["reverted"])
        self.assertNotIn(corr[0]["lines"][0], rebuilt["line_names"]) if rebuilt["corrections"][0]["id"] == corr[0]["id"] else None

    def test_refuses_without_current_1b(self):
        f = facts_after_1b(); f["stages"]["1b"]["version"] = "stage1b/0.0"
        with self.assertRaises(RuntimeError) as cm:
            run_s2(f, [], scene_ids=[1])
        self.assertIn("re-run Stage 1B", str(cm.exception))
        f = S.run(make_inputs(), None, CFG, CAST)
        with self.assertRaises(RuntimeError):
            run_s2(f, [], scene_ids=[1])

    def test_estimate(self):
        f = facts_after_1b()
        est = S2.estimate_scenes(f, CFG, counter=lambda t: len(t) // 3)
        self.assertEqual(len(est["scenes"]), 3)
        self.assertEqual(est["mode"], "video")
        self.assertFalse(est["scenes"][0]["prefix_cached"]); self.assertTrue(est["scenes"][1]["prefix_cached"])
        self.assertAlmostEqual(est["total_inr"], sum(x["inr"] for x in est["scenes"]), places=2)
        c2 = copy.deepcopy(CFG); f2 = copy.deepcopy(f); f2["episode_pass"]["content_type"] = "podcast"
        self.assertEqual(S2.estimate_scenes(f2, c2, counter=lambda t: 100)["mode"], "audio")


class TestAcceptance2(unittest.TestCase):
    def test_kinds(self):
        f = facts_after_1b(); sc = scene(f, 2)
        a = good_sheet(f, sc)
        names, _ = S2.map_names(f)
        kc = next(x["key"] for x in a["speakers_seen"] if names.get(x["key"]) == "Customer")
        a["speakers_seen"] = [x if x["key"] != kc else {**x, "matches_map": False, "who": "unknown person", "evidence": "face"} for x in a["speakers_seen"]]
        out, _, _, _, _, _ = run_s2(f, [a], scene_ids=[sc["id"]])
        dlg = S2.scene_dialogue_ids(f, sc)
        nd = out["beat_sheets"][0]["non_dialogue"][0]
        ref = {"checks": [
            {"kind": "map_corrected", "lines": f"{dlg[0]}-{dlg[-1]}", "from_name": "Customer", "min_share": 0.3},
            {"kind": "beat_payoff", "scene_containing": str(sc["start"] + 1), "line": dlg[-1]},
            {"kind": "scene_register", "scene_containing": str(sc["start"] + 1), "any": ["comedy"]},
            {"kind": "dynamic_mentions", "scene_containing": str(sc["start"] + 1), "any": ["friends"]},
            {"kind": "speakers_seen_check", "scene_containing": str(sc["start"] + 1), "key": kc, "matches_map": False},
            {"kind": "speakers_seen_line", "line": dlg[0]},
            {"kind": "non_dialogue_class", "range": nd["range"], "stretch_kind": "action_beat", "recommend_any": ["keep"]},
            {"kind": "non_dialogue_class", "range": nd["range"], "stretch_kind": "transition"},
            {"kind": "beat_payoff", "scene_containing": "0:05", "line": dlg[-1]},     # scene 1 has no sheet -> missing
        ]}
        rows = acceptance.evaluate_1b(out, ref)
        st = [r["status"] for r in rows]
        self.assertEqual(st[:7], ["agree"] * 7, rows[:7])
        self.assertEqual(st[7], "disagree")
        self.assertEqual(st[8], "missing")


if __name__ == "__main__":
    unittest.main()
