"""emotion_pick/0.4 "tight cuts" (founder, 2026-10-24), offline with the fake provider of test_emotion_pick:
1. configs: the three 0.4 keys (words.word_max_s, length, length_cap) are absent from DEFAULTS and from the v1 / v2 /
   0.3 configs (off: replays stay byte-identical); configs/emotion_pick_v4.json turns them on and pins the new prompt
   files (find v4, build v3); emotion_pick_v4_llmfix.json = v4 + speaker_source llm_fix;
2. stretched-word fix: a word's end is capped at start + 2.0 s on the loaded copy (line end follows), before the end
   hold, segments and durations; the run document records it (0.4 only);
3. minimum length: 12 s, 10 s when the moment's intensity >= 4 (0.3: 10 s for all);
4. 50 s soft cap (humour) / 65 s ceiling (drama): a clip over it is trimmed to the latest earlier found payoff line
   (intensity >= target - 1) that brings it under the cap (+ a <= 3-word reaction line), the land moves there and the
   clip is rebuilt; no such line -> kept and flagged over_soft_cap / over_ceiling; before / after recorded;
5. the preview renderer's opt-in word cap (tools/preview_clips.py --word-max-s)."""
import json
import re
import sys
import unittest

from shotvi.llm.base import RawResponse
from shotvi.select import emotion_pick as E
from shotvi.select import webseries as ws
from shotvi.select.prompts import load_prompt
from tests.select.test_emotion_pick import V1, FakeProvider, L, Run, build, moment
from tests.select.test_emotion_pick_v2 import alternate

CONFIGS = ws.ROOT / "shotvi" / "select" / "configs"


def cfg_file(name):
    return json.loads((CONFIGS / name).read_text(encoding="utf-8"))


V4 = cfg_file("emotion_pick_v4.json")
NO_CLUSTER = {"cluster": {"enabled": False}}


class BuildProvider(FakeProvider):
    """FakeProvider with its own build answers ({moment line: answer})."""

    def __init__(self, moments, builds):
        super().__init__(moments)
        self.builds = builds

    def generate(self, spec, system, prompt, schema, media=None, cached_content=None):
        if "no_clip" in schema["properties"]:
            line = re.search(r"The moment: line (L\d+)", prompt).group(1)
            self.calls.append({"role": "build", "system": system, "prompt": prompt, "cached": cached_content})
            return RawResponse(json.dumps(self.builds[line], ensure_ascii=False), 5000, 300, 400, "m",
                               cached_tokens=4000 if cached_content else 0)
        return super().generate(spec, system, prompt, schema, media, cached_content)


def run4(prov, v4=True, over=None, ep=None):
    """A run of DEFAULTS (0.3) or DEFAULTS + the v4 code keys (the v4 prompt files need no special handling: the fake
    provider answers any prompt)."""
    r = Run(prov)
    r.ep = alternate(ep or r.ep)
    c = ws.deep_merge(E.DEFAULTS, NO_CLUSTER)
    if v4:
        c = ws.deep_merge(c, V4)
    r.cfg = ws.deep_merge(c, over or {})
    for k in ("find", "build"):
        r.cfg["models"][k]["provider"] = "fake"
    r.cfg["run_id"] = "t4"
    return r


def by_line(doc):
    return {p["moment"]["line"]: p for p in doc["proposals"]}


def rejected_by_line(doc):
    return {r["moment"]["line"]: r for r in doc["rejected"]}


# ============================================================================ 1 configs
class Configs(unittest.TestCase):
    def test_off_in_defaults_and_the_old_configs(self):
        for k in ("words", "length", "length_cap"):
            self.assertNotIn(k, E.DEFAULTS)
        olds = [{}, V1, cfg_file("emotion_pick_v2.json"), cfg_file("emotion_pick_v3_llmfix.json"),
                cfg_file("emotion_pick_v3_sarvam.json"), cfg_file("emotion_pick_v2_llmfix.json")]
        for o in olds:
            c = ws.deep_merge(E.DEFAULTS, o)
            vc = E.v1_cfg(c)
            self.assertIsNone(E.word_max_s(c))
            self.assertIsNone(E.cap_of(c, "laugh"))
            self.assertIsNone(E.cap_of(c, "tears"))
            self.assertEqual(E.min_s_for(c, vc, 3), vc["guardrails"]["min_s"])
            self.assertIs(E.vcfg_for(c, vc, {"intensity": 3}), vc)
        self.assertEqual(E.PIPELINE_VERSION, "emotion_pick/0.3")
        self.assertEqual(E.DEFAULTS["find_prompt"], "ws_emotion_find")
        self.assertEqual(E.DEFAULTS["prompts"]["ws_emotion_build"], "ws_emotion_build")

    def test_v4_config(self):
        c = ws.deep_merge(E.DEFAULTS, V4)
        self.assertEqual(c["pipeline_version"], "emotion_pick/0.4")
        self.assertEqual((c["find_prompt"], c["prompts"]["ws_emotion_build"]),
                         ("ws_emotion_find_v4", "ws_emotion_build_v3"))
        self.assertEqual(E.word_max_s(c), 2.0)
        self.assertEqual(E.cap_of(c, "laugh"), ("humour", 50.0, "over_soft_cap"))
        self.assertEqual(E.cap_of(c, "relatable_banter")[1], 50.0)
        self.assertEqual(E.cap_of(c, "awkward_cringe")[1], 50.0)
        for f in ("tension", "shock", "tears", "aww"):
            self.assertEqual(E.cap_of(c, f), ("drama", 65.0, "over_ceiling"))
        lf = cfg_file("emotion_pick_v4_llmfix.json")
        self.assertEqual(lf.pop("speaker_source"), "llm_fix")
        self.assertEqual(lf, V4)
        for src in ("llmfix", "sarvam"):        # the free code-only replays: 0.3 prompts, only the code keys on
            co = cfg_file(f"emotion_pick_v4_codeonly_{src}.json")
            self.assertNotIn("find_prompt", co)
            self.assertNotIn("prompts", co)
            self.assertEqual({k: co[k] for k in ("words", "length", "length_cap")},
                             {k: V4[k] for k in ("words", "length", "length_cap")})

    def test_new_prompt_files_and_the_old_ones_kept(self):
        f4, f3 = load_prompt("ws_emotion_find_v4"), load_prompt("ws_emotion_find")
        b3, b2 = load_prompt("ws_emotion_build_v3"), load_prompt("ws_emotion_build")
        self.assertEqual((f4.name, f4.version, f3.version), ("ws_emotion_find_v4", "4", "3"))
        self.assertEqual((b3.name, b3.version, b2.version), ("ws_emotion_build_v3", "3", "2"))
        self.assertIn("No waiting / pacing / anxious buildup", f4.system)
        self.assertIn("Self-contained comedy only", f4.system)
        self.assertNotIn("No waiting", f3.system)
        self.assertEqual(f4.user, f3.user)
        self.assertIn("Target duration 18-35 seconds", b3.system)
        self.assertIn("Cut on the punchline/landing", b3.system)
        self.assertIn("Cut the slow lead-in", b3.system)
        self.assertNotIn("typically 30-75 seconds", b3.system)
        self.assertIn("typically 30-75 seconds", b2.system)
        self.assertEqual(b3.user, b2.user)


# ============================================================================ 2 stretched words
def stretched_ep(stretch_to=240.0):
    """The fake episode with the LAST word of L0060 (187-189 s) stretched to ``stretch_to`` (as Scribe stretched a
    closing word over the end music)."""
    ep = Run(FakeProvider()).ep
    r = ep.by_id[L(60)]
    i = r["words"][1]
    ep.words[i]["end"] = stretch_to
    r["end"] = stretch_to
    return ep, i


class WordCap(unittest.TestCase):
    def test_cap_words_in_place(self):
        ep, i = stretched_ep()
        before = [dict(w) for w in ep.words]
        out = E.cap_words(ep, 2.0)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["i"], out[0]["end_before"]), (i, 240.0))
        self.assertAlmostEqual(ep.words[i]["end"], ep.words[i]["start"] + 2.0, places=3)
        self.assertAlmostEqual(ep.by_id[L(60)]["end"], ep.words[i]["end"], places=3)
        self.assertEqual([w for k, w in enumerate(ep.words) if k != i], [w for k, w in enumerate(before) if k != i])
        self.assertEqual(E.cap_words(ep, 2.0), [])          # idempotent

    def test_clip_ending_on_the_stretched_word(self):
        builds = {L(60): build(52, 60)}
        ms = [moment(60, 4, emotion="aww")]
        ep, i = stretched_ep()
        d3 = run4(BuildProvider(ms, builds), v4=False, ep=ep)()
        ep, i = stretched_ep()
        d4 = run4(BuildProvider(ms, builds), ep=ep)()
        p3, p4 = by_line(d3)[L(60)], by_line(d4)[L(60)]
        self.assertGreater(p3["time_segments"][-1]["end"], 239.0)       # 0.3 plays the stretched word to its end
        self.assertLess(p4["time_segments"][-1]["end"], 200.0)           # 0.4: capped, then the normal end hold
        self.assertLess(p4["video_duration"], p3["video_duration"] - 40)
        self.assertEqual(d4["word_cap"]["word_max_s"], 2.0)
        self.assertEqual([w["i"] for w in d4["word_cap"]["capped"]], [i])
        self.assertNotIn("word_cap", d3)


# ============================================================================ 3 minimum length
class MinLength(unittest.TestCase):
    def test_min_s_by_intensity(self):
        c = ws.deep_merge(E.DEFAULTS, V4)
        vc = E.v1_cfg(c)
        self.assertEqual([E.min_s_for(c, vc, k) for k in (3, 4, 5)], [12.0, 10.0, 10.0])
        v = E.vcfg_for(c, vc, {"intensity": 3})
        self.assertEqual((v["guardrails"]["min_s"], vc["guardrails"]["min_s"]), (12.0, 10.0))   # a copy

    def test_an_11_s_clip(self):
        # L0010..L0013: 37-48 s + the end hold ~ 11 s
        ms = [moment(13, 3), moment(33, 4)]
        builds = {L(13): build(10, 13), L(33): build(30, 33)}
        d4 = run4(BuildProvider(ms, builds))()
        d3 = run4(BuildProvider(ms, builds), v4=False)()
        self.assertTrue(10.0 <= by_line(d3)[L(13)]["video_duration"] < 12.0)
        self.assertIn(L(13), by_line(d3))                                # 0.3: 10 s for all
        self.assertIn(L(33), by_line(d4))                                # intensity 4: 10 s
        self.assertIn(L(13), rejected_by_line(d4))                       # intensity 3: 12 s
        self.assertTrue(rejected_by_line(d4)[L(13)]["reject_reasons"][0].startswith("L1: plays"))
        self.assertIn("< min 12 s", rejected_by_line(d4)[L(13)]["reject_reasons"][0])


# ============================================================================ 4 soft cap / ceiling
class SoftCap(unittest.TestCase):
    # one long scene: L0004..L0030 plays ~80 s (lines every 3 s); found payoffs inside it are the trim candidates
    def run_cap(self, ms, builds, v4=True, over=None):
        return run4(BuildProvider(ms, builds), v4=v4, over=over)()

    def test_humour_clip_over_50_s_trims_to_an_earlier_landing(self):
        ms = [moment(15, 3), moment(30, 4)]
        builds = {L(30): build(4, 30), L(15): build(12, 15)}
        d3 = self.run_cap(ms, builds, v4=False)
        d4 = self.run_cap(ms, builds)
        p3 = by_line(d3)[L(30)]
        self.assertGreater(p3["video_duration"], 75.0)
        self.assertNotIn("length_cap", p3)
        p4 = by_line(d4)[L(30)]       # shown: L0015's own clip plays 11.8 s (< 12 s for intensity 3)
        lc = p4["length_cap"]
        self.assertEqual((lc["kind"], lc["cap_s"], lc["result"], lc["trimmed_to"]), ("humour", 50.0, "trimmed", L(15)))
        self.assertEqual(lc["before"]["end_line"], L(30))
        self.assertEqual(lc["before"]["plays_s"], p3["video_duration"])
        self.assertEqual(lc["after"]["end_line"], L(16))          # + the next line: a 3-word reaction
        self.assertLessEqual(lc["after"]["plays_s"], 50.0)
        self.assertEqual(lc["land_moment"]["line"], L(15))
        self.assertIn("written for the longer cut (land L0030)", lc["note"])
        self.assertEqual((p4["land"]["line"], p4["end_line"]), (L(15), L(16)))
        self.assertEqual(p4["video_duration"], lc["after"]["plays_s"])
        self.assertEqual((p4["intensity"], p4["moment"]["intensity"]), (3, 4))   # the landing's own intensity
        self.assertTrue(any(f.startswith("soft_cap trim (humour, 50 s)") for f in p4["build_flags"]))

    def test_latest_candidate_that_fits(self):
        ms = [moment(10, 4), moment(17, 4), moment(26, 4), moment(30, 4)]
        builds = {L(30): build(4, 30), L(26): build(24, 26), L(17): build(15, 17), L(10): build(8, 10)}
        d4 = self.run_cap(ms, builds)
        x = next(p for p in d4["proposals"] + d4["rejected"] if p["moment"]["line"] == L(30))
        lc = x["length_cap"]
        # L0026 (+L0027) still plays > 50 s; L0017 (+L0018) fits; L0010 is never tried
        self.assertEqual([t["line"] for t in lc["tried"]], [L(26), L(17)])
        self.assertIn("still plays", lc["tried"][0]["refused"])
        self.assertEqual(lc["trimmed_to"], L(17))

    def test_no_candidate_is_flagged_not_dropped(self):
        ms = [moment(12, 3), moment(30, 5)]                       # 3 < 5 - 1: not a landing for this clip
        builds = {L(30): build(4, 30), L(12): build(10, 12)}
        d4 = self.run_cap(ms, builds)
        p = by_line(d4)[L(30)]
        self.assertEqual((p["length_cap"]["result"], p["length_flag"]), ("flagged", "over_soft_cap"))
        self.assertEqual(p["length_cap"]["tried"], [])
        self.assertGreater(p["video_duration"], 75.0)
        self.assertTrue(any(f.startswith("over_soft_cap: plays") for f in p["build_flags"]))

    def test_reaction_only_when_short(self):
        ep = Run(FakeProvider()).ep
        r = ep.by_id[L(16)]                                       # make L0016 a 4-word line
        ep.words[r["words"][1]]["word"] = "a15c d"
        ep.by_id[L(16)]["text"] = ep.by_id[L(16)]["text"] + " d"
        ms = [moment(15, 4), moment(30, 4)]
        builds = {L(30): build(4, 30), L(15): build(12, 15)}
        d4 = run4(BuildProvider(ms, builds), ep=ep)()
        x = next(p for p in d4["proposals"] + d4["rejected"] if p["moment"]["line"] == L(30))
        self.assertEqual(x["length_cap"]["tried"][0]["reaction"], None)
        self.assertEqual(x["length_cap"]["after"]["end_line"], L(15))

    def test_drama_ceiling_65_s(self):
        # L0004..L0024 plays ~ 62 s: under the drama ceiling (65 s), over the humour cap (50 s)
        ms = [moment(14, 4, emotion="tension"), moment(24, 4, emotion="tension")]
        builds = {L(24): build(4, 24), L(14): build(12, 14)}
        d4 = self.run_cap(ms, builds)
        p = by_line(d4)[L(24)]
        self.assertTrue(50.0 < p["video_duration"] <= 65.0)
        self.assertNotIn("length_cap", p)
        # the same clip as humour is trimmed
        ms2 = [moment(14, 4), moment(24, 4)]
        d4h = self.run_cap(ms2, builds)
        x = next(p for p in d4h["proposals"] + d4h["rejected"] if p["moment"]["line"] == L(24))
        self.assertEqual(x["length_cap"]["result"], "trimmed")
        # over 65 s with no landing: flagged over_ceiling
        ms3 = [moment(30, 4, emotion="tears")]
        d4c = self.run_cap(ms3, {L(30): build(4, 30)})
        p3 = by_line(d4c)[L(30)]
        self.assertEqual((p3["length_cap"]["kind"], p3["length_flag"]), ("drama", "over_ceiling"))

    def test_drama_clip_trimmed_to_a_laugh_must_fit_the_humour_cap(self):
        # tension clip L0004..L0030 (~80 s); L0024 (laugh) would play ~62 s: under 65 s but over its own 50 s cap
        ms = [moment(14, 4), moment(24, 4), moment(30, 4, emotion="tension")]
        builds = {L(30): build(4, 30), L(24): build(22, 24), L(14): build(12, 14)}
        d4 = self.run_cap(ms, builds)
        x = next(p for p in d4["proposals"] + d4["rejected"] if p["moment"]["line"] == L(30))
        lc = x["length_cap"]
        self.assertEqual(lc["kind"], "drama")
        self.assertEqual([t["line"] for t in lc["tried"]], [L(24), L(14)])
        self.assertIn("the humour cap of its laugh landing", lc["tried"][0]["refused"])
        self.assertEqual((lc["trimmed_to"], lc["land_moment"]["feeling"]), (L(14), "laugh"))

    def test_trim_under_the_minimum_is_refused(self):
        ms = [moment(6, 4), moment(30, 4)]                         # L0004..L0007 plays ~ 11 s < 15 s
        builds = {L(30): build(4, 30), L(6): build(4, 6)}
        d4 = self.run_cap(ms, builds, over={"length": {"min_s_intense": 15.0}})
        x = next(p for p in d4["proposals"] + d4["rejected"] if p["moment"]["line"] == L(30))
        self.assertEqual(x["length_cap"]["result"], "flagged")
        self.assertIn("L1: plays", x["length_cap"]["tried"][0]["refused"])

    def test_trimmed_clip_competes_with_its_landings_intensity(self):
        # L0030's clip (strength 5) trims to L0015 (intensity 4); L0015's own clip (L0012..L0015) duplicates it: with
        # equal strength the same-scene rules pick one, never on the old 5
        ms = [moment(15, 4), moment(30, 5)]
        builds = {L(30): build(4, 30), L(15): build(12, 15)}
        d4 = self.run_cap(ms, builds)
        d1 = [r for r in d4["rejected"] if r["reject_reasons"][0].startswith("D1")]
        self.assertEqual(len(d1), 1)
        self.assertIn("strength 4 vs 4", d1[0]["reject_reasons"][0])


# ============================================================================ 5 preview opt-in
class PreviewWordCap(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ws.ROOT / "tools"))
        import preview_clips
        self.P = preview_clips

    def test_cap_words_and_segments(self):
        words = [{"word": "a", "start": 10.0, "end": 10.5}, {"word": "b", "start": 11.0, "end": 49.2},
                 {"word": "c", "start": 60.0, "end": 60.4}]
        capped = self.P.cap_words(words, 2.0)
        self.assertEqual(capped, {1: 49.2})
        self.assertEqual(words[1]["end"], 13.0)
        segs = [(9.8, 52.0), (59.9, 61.0)]
        self.assertEqual(self.P.cap_segments(segs, words, capped), [(9.8, 15.0), (59.9, 61.0)])
        # a segment that plays on past the stretched word (another word after it) is untouched
        self.assertEqual(self.P.cap_segments([(9.8, 61.0)], words, capped), [(9.8, 61.0)])
        self.assertEqual(self.P.cap_segments(segs, words, {}), segs)


if __name__ == "__main__":
    unittest.main()
