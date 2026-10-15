"""Emotion select (shotvi/select/emotion_select.py), offline (fake provider, temp cache):
1. renderer: clips rendered from time_segments alone, in PLAY order (a cold open first), with clip-relative times,
   line ids, inline audio events, cut / cold-open markers and the final picture hold;
2. code checks: the stop line must start in the first 3 s, the land line must be one of the last 3 spoken lines, an
   id not in the clip makes that part none; verdict yes / maybe / no is decided by code;
3. extra input fields (labels) and the caller's clip ids never reach the prompt, and come back unchanged;
4. safety block: the batch is split; a clip still blocked alone is "unchecked"; dry run makes no generate call;
5. ordering: yes, maybe, no, unchecked, stable inside each group."""
import json
import re
import tempfile
import unittest
from unittest import mock

from shotvi.llm import Ledger, LLMClient
from shotvi.llm.base import RawResponse, SafetyBlocked
from shotvi.select import emotion_select as es
from shotvi.select import webseries as ws
from tests.select.test_webseries_cut import make_ep

# ten 2 s lines, one every 3 s from 10 s (L0001 at 10 s ... L0010 at 37 s) + a laugh after L0008
SPEC = [(10.0 + 3 * k, 12.0 + 3 * k, f"w{k}a w{k}b w{k}c w{k}d") for k in range(10)]
EVENTS = [(33.6, 34.4, "[నవ్వు]")]


def episode():
    return make_ep(SPEC, [], events=EVENTS, duration=60.0)


def lid(ep, k):
    """Id of the k-th spoken line (0-based) in this episode (event lines shift the numbering)."""
    return [r for r in ep.lines if r["kind"] == "speech"][k]["id"]


def answer(alias, stop="L0001", land="L0003", stay_q="does it work?", levels=("clear", "clear", "clear"),
           reaction="hold", feeling="laugh"):
    return {"id": alias,
            "stop": {"line": stop, "feeling": feeling, "why": "q", "level": levels[0]},
            "stay": {"question": stay_q, "level": levels[1]},
            "land": {"line": land, "feeling": feeling, "reaction": reaction, "why": "q", "level": levels[2]},
            "reason": "Viewer stops because x, stays to find out y, and it ends on z (laugh)."}


class FakeProvider:
    """Answers every clip it is shown (``per_alias`` overrides by alias); blocks any batch with a blocked alias."""

    def __init__(self, per_alias=None, block=()):
        self.per_alias = per_alias or {}
        self.block = set(block)
        self.prompts = []

    def generate(self, spec, system, prompt, schema, media=None):
        self.prompts.append((system, prompt))
        ids = re.findall(r"^### (C\d+) \|", prompt, re.M)
        if self.block & set(ids):
            raise SafetyBlocked("blocked", "PROHIBITED_CONTENT", response=RawResponse("", 500, 0))
        obj = {"clips": [self.per_alias.get(a) or answer(a) for a in ids]}
        return RawResponse(json.dumps(obj, ensure_ascii=False), 1000, 300, 200, "m")

    def count_tokens(self, spec, text):
        return len(text) // 3


class Renderer(unittest.TestCase):
    def setUp(self):
        self.ep = episode()

    def test_cold_open_plays_first_then_the_scene_skipping_it_in_place(self):
        ep = self.ep
        l6 = [r for r in ep.lines if r["kind"] == "speech"][6]          # 28-30 s: the lifted line
        segs = [{"start": 27.9, "end": 30.1, "kind": "cold_open"},       # lift first
                {"start": 9.9, "end": 26.5},                             # scene from its start (L0001-L0006)
                {"start": 30.5, "end": 40.0}]                            # after the lift (L0008-L0010 + laugh)
        rd = es.render_clip(ep, {"time_segments": segs, "cold_open": True}, "C1")
        ids = [r["id"] for r in rd.speech]
        self.assertEqual(ids[0], l6["id"])
        self.assertEqual(ids[1:], [lid(ep, k) for k in (0, 1, 2, 3, 4, 5, 7, 8, 9)])
        self.assertEqual(ids.count(l6["id"]), 1)                         # never replayed
        rows = rd.text.splitlines()
        self.assertTrue(rows[0].startswith("### C1 | plays "))
        self.assertTrue(rows[1].startswith(f"[0:00.1] {l6['id']} [speaker_0]: w6a"), rows[1])
        self.assertEqual(rows[2], "[cold open ends; scene starts]")
        self.assertTrue(rows[3].startswith(f"[0:02.3] {lid(ep, 0)} "), rows[3])   # 2.2 s lift + 0.1 s pad
        self.assertIn("[cut: 4.0 s removed; the opening line is skipped here, not replayed]", rows)
        self.assertIn("(audio event) [నవ్వు]", rd.text)
        laugh = next(k for k, r in enumerate(rows) if "నవ్వు" in r)
        self.assertLess(rows.index(next(r for r in rows if lid(ep, 7) in r)), laugh)   # the laugh plays after L8
        self.assertTrue(rows[-1].startswith("[picture holds "))
        # hold = last segment end (40.0) - last word end (L0010: 37-39 s, last word ends 38.98)
        self.assertEqual(rows[-1], "[picture holds 1.0 s]")
        self.assertAlmostEqual(rd.duration, 2.2 + 16.6 + 9.5, places=2)
        self.assertNotIn("C1 |", "\n".join(rows[1:]))

    def test_forward_cut_marker_and_hold(self):
        segs = [{"start": 9.9, "end": 15.0}, {"start": 27.9, "end": 34.0}]
        rd = es.render_clip(self.ep, {"time_segments": segs, "cold_open": False}, "C2")
        self.assertIn("[cut: 13 s removed]", rd.text)
        self.assertNotIn("cold open", rd.text)
        self.assertEqual([r["id"] for r in rd.speech], [lid(self.ep, k) for k in (0, 1, 6, 7)])
        # last line L0008 (31-33 s) ends 32.98; the segment runs to 34.0
        self.assertEqual(rd.text.splitlines()[-1], "[picture holds 1.0 s]")

    def test_partly_played_line_shows_only_its_played_words(self):
        rd = es.render_clip(self.ep, {"time_segments": [{"start": 9.9, "end": 11.0}], "cold_open": False}, "C1")
        self.assertIn("w0a w0b …", rd.text)
        self.assertNotIn("w0d", rd.text)
        self.assertTrue(rd.speech[0]["partial"])

    def test_asr_marks_are_stripped(self):
        self.assertEqual(es.clean_text("చూడు {fixed: రక్కావో} word(?) x"), "చూడు రక్కావో word x")


class Checks(unittest.TestCase):
    def setUp(self):
        self.ep = episode()
        # plays L1..L10 from 9.9 s: L1 at 0.1 s, L2 at 3.1 s, ..., L10 at 27.1 s
        self.rd = es.render_clip(self.ep, {"time_segments": [{"start": 9.9, "end": 40.0}], "cold_open": False}, "C1")
        self.ecfg = es.emo_cfg({})

    def chk(self, **kw):
        return es.check_answer(answer("C1", **kw), self.rd, self.ecfg)

    def test_all_clear_is_yes(self):
        r = self.chk(stop=lid(self.ep, 0), land=lid(self.ep, 9))
        self.assertEqual(r["verdict"], "yes")
        self.assertEqual(r["code_notes"], [])
        self.assertEqual(r["stop"]["line_play_s"], 0.1)

    def test_stop_line_after_the_first_3_s_is_none(self):
        r = self.chk(stop=lid(self.ep, 1), land=lid(self.ep, 9))       # starts at 3.1 s
        self.assertEqual(r["stop"]["level"], "none")
        self.assertEqual(r["stop"]["model_level"], "clear")
        self.assertTrue(any("stop line not in first 3 s" in n for n in r["code_notes"]), r["code_notes"])
        self.assertEqual(r["verdict"], "no")

    def test_land_must_be_among_the_last_3_spoken_lines(self):
        ok = self.chk(stop=lid(self.ep, 0), land=lid(self.ep, 7))       # 3rd from last
        self.assertEqual(ok["land"]["level"], "clear")
        bad = self.chk(stop=lid(self.ep, 0), land=lid(self.ep, 6))      # 4th from last
        self.assertEqual(bad["land"]["level"], "none")
        self.assertTrue(any("land line not among the last 3" in n for n in bad["code_notes"]))
        self.assertEqual(bad["verdict"], "no")

    def test_ids_not_in_the_clip_make_that_part_none(self):
        r = self.chk(stop="L0999", land=lid(self.ep, 9))
        self.assertEqual(r["stop"]["level"], "none")
        r = self.chk(stop=lid(self.ep, 0), land="L0999")
        self.assertEqual(r["land"]["level"], "none")
        r = self.chk(stop=lid(self.ep, 0), land=lid(self.ep, 8), reaction="L0999")
        self.assertEqual(r["land"]["level"], "none")
        r = self.chk(stop=lid(self.ep, 0), land=lid(self.ep, 8), reaction=lid(self.ep, 9))
        self.assertEqual(r["land"]["level"], "clear")

    def test_a_level_without_a_pointer_is_none(self):
        r = self.chk(stop="none", land=lid(self.ep, 9))
        self.assertEqual(r["stop"]["level"], "none")
        r = self.chk(stop=lid(self.ep, 0), land=lid(self.ep, 9), stay_q="none")
        self.assertEqual(r["stay"]["level"], "none")
        r = self.chk(stop=lid(self.ep, 0), land=lid(self.ep, 9), feeling="none")
        self.assertEqual((r["stop"]["level"], r["land"]["level"]), ("none", "none"))

    def test_verdict_logic(self):
        V = es.verdict_of
        p = lambda a, b, c: ({"level": a}, {"level": b}, {"level": c})   # noqa: E731
        self.assertEqual(V(*p("clear", "clear", "clear")), "yes")
        self.assertEqual(V(*p("weak", "clear", "clear")), "maybe")
        self.assertEqual(V(*p("clear", "clear", "weak")), "maybe")
        self.assertEqual(V(*p("weak", "weak", "clear")), "no")
        self.assertEqual(V(*p("clear", "none", "clear")), "no")
        self.assertEqual(V(*p("none", "none", "none")), "no")

    def test_model_weak_is_kept_and_never_raised(self):
        r = self.chk(stop=lid(self.ep, 0), land=lid(self.ep, 9), levels=("weak", "clear", "clear"))
        self.assertEqual(r["stop"]["level"], "weak")
        self.assertEqual(r["verdict"], "maybe")


def clips_for(n, extra=True):
    out = []
    for k in range(n):
        c = {"video_id": "vid", "clip_id": f"SECRET_ID_{k}", "cold_open": False,
             "time_segments": [{"start": 9.9 + 3 * (k % 3), "end": 40.0}]}
        if extra:
            c.update(founder_label=f"SECRET_LABEL_{k}", notes={"founder": "SECRET_NOTE"})
        out.append(c)
    return out


class Calls(unittest.TestCase):
    def setUp(self):
        self.ep = episode()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ecfg = es.emo_cfg({})

    def run_clips(self, prov, clips, dry_run=False):
        with mock.patch("shotvi.llm.client.get_provider", return_value=prov), \
                mock.patch("shotvi.llm.spend.total_inr", return_value=0.0):
            llm = LLMClient(Ledger(), cache_dir=self.tmp.name)
            return es.run_clips(clips, {"vid": self.ep}, llm, self.ecfg, ws.Guard(None), log=lambda *_: None,
                                dry_run=dry_run, count_api=False)

    def test_extra_fields_and_clip_ids_never_reach_the_prompt(self):
        prov = FakeProvider()
        out = self.run_clips(prov, clips_for(3))
        self.assertEqual(len(prov.prompts), 1)
        sent = "\n".join(s + "\n" + u for s, u in prov.prompts)
        self.assertNotIn("SECRET", sent)
        self.assertNotIn("founder_label", sent)
        for k, r in enumerate(out["results"]):
            self.assertEqual(r["clip_id"], f"SECRET_ID_{k}")
            self.assertEqual(r["founder_label"], f"SECRET_LABEL_{k}")
            self.assertEqual(r["notes"], {"founder": "SECRET_NOTE"})
            self.assertIn(r["verdict"], ("yes", "maybe", "no"))
            self.assertIsNotNone(r["raw"])

    def test_batches_of_at_most_8_balanced_and_one_episode_each(self):
        self.assertEqual([len(b) for b in es.batches_of(9, 8)], [5, 4])
        self.assertEqual([len(b) for b in es.batches_of(16, 8)], [8, 8])
        self.assertEqual([len(b) for b in es.batches_of(3, 8)], [3])
        prov = FakeProvider()
        self.run_clips(prov, clips_for(9, extra=False))
        self.assertEqual([len(re.findall(r"^### C\d+", u, re.M)) for _, u in prov.prompts], [5, 4])

    def test_rerun_is_a_free_cache_replay(self):
        prov = FakeProvider()
        a = self.run_clips(prov, clips_for(3))
        b = self.run_clips(prov, clips_for(3))
        self.assertEqual(len(prov.prompts), 1)
        self.assertEqual([r["verdict"] for r in a["results"]], [r["verdict"] for r in b["results"]])

    def test_blocked_clip_alone_is_unchecked_others_judged(self):
        prov = FakeProvider(block={"C2"})
        out = self.run_clips(prov, clips_for(4, extra=False))
        v = [r["verdict"] for r in out["results"]]
        self.assertEqual(v[1], "unchecked")
        self.assertTrue(all(x != "unchecked" for k, x in enumerate(v) if k != 1))
        self.assertTrue(any("blocked" in c["status"] for c in out["calls"]))

    def test_dry_run_makes_no_generate_call(self):
        class NoCall(FakeProvider):
            def generate(self, *a, **k):
                raise AssertionError("dry run called the provider")
        out = self.run_clips(NoCall(), clips_for(3), dry_run=True)
        self.assertEqual({r["verdict"] for r in out["results"]}, {"not_run"})
        self.assertEqual(len(out["projection"]["dry_run_calls"]), 1)
        self.assertGreater(out["projection"]["inr_worst"], out["projection"]["inr_expected"])

    def test_code_not_the_model_decides_the_verdict(self):
        ep = self.ep
        # the model claims all clear, but its stop line plays at 3.1 s: code says no
        prov = FakeProvider(per_alias={"C1": answer("C1", stop=lid(ep, 1), land=lid(ep, 9))})
        out = self.run_clips(prov, clips_for(1, extra=False))
        r = out["results"][0]
        self.assertEqual(r["raw"]["stop"]["level"], "clear")
        self.assertEqual(r["verdict"], "no")


class Ordering(unittest.TestCase):
    def test_yes_maybe_no_unchecked_stable(self):
        xs = [("a", "no"), ("b", "yes"), ("c", "unchecked"), ("d", "maybe"), ("e", "yes"), ("f", "no")]
        got = es.order_by_verdict(xs, lambda x: x[1])
        self.assertEqual([x[0] for x in got], ["b", "e", "d", "a", "f", "c"])

    def test_pipeline_flag_is_off_by_default(self):
        self.assertFalse(ws.emotion_on(ws.DEFAULTS))
        self.assertNotIn("emotion_select", ws.DEFAULTS)
        self.assertTrue(ws.emotion_on({"emotion_select": {"enabled": True}}))


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------------------------------------------- pipeline (stage 5c)
from tests.select.test_webseries_safety import Base as _SafetyBase  # noqa: E402
from tests.select.test_webseries_safety import FakeProvider as _SafetyFake  # noqa: E402


class PipelineFake(_SafetyFake):
    """v1 discover + review as in the safety tests; ws_emotion: per alias, stop = first line, land = last line
    (clear), except aliases in ``no`` (stop none) and ``maybe`` (stay weak)."""

    def __init__(self, no=(), maybe=()):
        super().__init__()
        self.no, self.maybe = set(no), set(maybe)
        self.emotion_prompts = []

    def generate(self, spec, system, prompt, schema, media=None):
        item = (schema["properties"].get("clips") or {}).get("items", {}).get("properties", {})
        if "stop" not in item:
            return super().generate(spec, system, prompt, schema, media)
        self.emotion_prompts.append(prompt)
        out = []
        for blk in re.split(r"^(?=### C\d+ \|)", prompt, flags=re.M):
            m = re.match(r"### (C\d+) \|", blk)
            if not m:
                continue
            a = m.group(1)
            ids = re.findall(r"^\[\d+:\d+\.\d\] (L\d{4}) ", blk, re.M)
            ent = answer(a, stop=ids[0], land=ids[-1])
            if a in self.no:
                ent["stop"].update(line="none", level="none")
            if a in self.maybe:
                ent["stay"]["level"] = "weak"
            out.append(ent)
        return RawResponse(json.dumps({"clips": out}, ensure_ascii=False), 1000, 300, 200, "m")


class PipelineStage(_SafetyBase):
    def setUp(self):
        super().setUp()
        self.cfg = ws.deep_merge(ws.DEFAULTS, {"emotion_select": {"enabled": True}})

    def test_verdicts_on_proposals_and_shown_order_yes_maybe_no(self):
        prov = PipelineFake(no={"C1", "C3"}, maybe={"C2"})
        cap = self.run_ep(prov)
        self.assertEqual(len(prov.emotion_prompts), 1)
        props = cap["props"]
        # v1 order was w01..w05 (C1..C5); yes = w04, w05; maybe = w02; no = w01, w03 (v1 order inside each group)
        self.assertEqual([p["id"] for p in props], ["w04", "w05", "w02", "w01", "w03"])
        self.assertEqual([p["emotion_verdict"] for p in props], ["yes", "yes", "maybe", "no", "no"])
        self.assertEqual([p["rank"] for p in props], [1, 2, 3, 4, 5])
        self.assertTrue(all(p["emotion_reason"] for p in props))
        self.assertIn("retention: yes", cap["md"])
        self.assertEqual(len(props), 5)                                  # nothing hidden

    def test_off_by_default_no_call_no_fields(self):
        self.cfg = ws.deep_merge(ws.DEFAULTS, {})
        prov = PipelineFake()
        cap = self.run_ep(prov)
        self.assertEqual(prov.emotion_prompts, [])
        self.assertTrue(all("emotion_verdict" not in p for p in cap["props"]))

    def test_dry_run_projects_the_stage(self):
        prov = PipelineFake()
        self.run_ep(prov)                                                # caches discover + review + emotion
        r = self.run_ep(PipelineFake(), dry_run=True)["result"]
        self.assertIsNone(r["first_paid_stage"])                         # everything cached: Rs 0
        self.assertEqual(r["emotion_select"]["dry_run_calls"], [])

    def test_dry_run_names_the_stage_when_it_would_bill(self):
        on = self.cfg
        self.cfg = ws.deep_merge(ws.DEFAULTS, {})
        self.run_ep(PipelineFake())                                      # v1 cached, emotion not
        self.cfg = on

        class NoCall(PipelineFake):
            def generate(self, *a, **k):
                raise AssertionError("dry run called the provider")
        r = self.run_ep(NoCall(), dry_run=True)["result"]
        self.assertEqual(r["first_paid_stage"], "ws_emotion")
        self.assertGreater(r["would_bill_inr_expected"], 0)
        self.assertGreater(r["would_bill_inr_guard"], r["would_bill_inr_expected"])
