"""emotion_pick ``speaker_source`` (founder's speaker-source experiment, 2026-10-23), offline:
- the arm configs are emotion_pick_v2.json plus ONE key (speaker_source);
- the default "scribe" changes nothing (no key in DEFAULTS, nothing relabelled, the context renders Scribe's labels);
- a layer relabels the episode BEFORE the context is rendered, and every speaker rule reads it: the same-speaker
  cold-open refusal / B1 turn lift, turn runs in the lift-ok list, the rendered clip text;
- a missing source file: placeholder in a dry run only."""
import json
import unittest
from unittest import mock

from shotvi.segment import speaker_layer as SL
from shotvi.select import emotion_pick as E
from shotvi.select import webseries as ws
from shotvi.select.lines import render
from tests.select.test_emotion_pick import L, build
from tests.select.test_emotion_pick_fixab import built, turn_ep
from tests.select.test_emotion_pick_v2 import cfg2, payoff

CONFIGS = ws.ROOT / "shotvi" / "select" / "configs"


def layer_for(ep, labels: dict[str, str], source="sarvam") -> dict:
    """A speaker layer for a test episode: line id -> raw source id (others keep their current label as a source
    id), built through speaker_layer.build like a real one (anonymous mapping included)."""
    sents = [r for r in ep.lines if ep.is_speech(r)]
    words = [dict(w) for w in ep.words]
    for r in sents:
        for i in range(r["words"][0], r["words"][1] + 1):
            words[i]["speaker"] = r["speaker"]
    tr = {"word_timestamps": words,
          "sentences": [{"words": r["words"], "start": r["start"], "speaker": r["speaker"]} for r in sents]}
    fix = {"schema": "speaker_fix/2",
           "lines": [{"id": k, "words": r["words"], "start": r["start"], "key": labels.get(r["id"], r["speaker"])}
                     for k, r in enumerate(sents)]}
    doc = SL.build(tr, "llm_fix", fix)
    return {**doc, "source": source, "video_id": ep.video_id}


class Configs(unittest.TestCase):
    def test_arm_configs_differ_from_v2_only_in_speaker_source(self):
        v2 = json.loads((CONFIGS / "emotion_pick_v2.json").read_text(encoding="utf-8"))
        for name, src in (("emotion_pick_v2_sarvam.json", "sarvam"), ("emotion_pick_v2_llmfix.json", "llm_fix")):
            c = json.loads((CONFIGS / name).read_text(encoding="utf-8"))
            self.assertEqual(c.pop("speaker_source"), src)
            self.assertEqual(c, v2, name)
            # the text too: v2's bytes plus the one line
            t = (CONFIGS / name).read_text(encoding="utf-8")
            self.assertEqual(t.replace(f' "speaker_source": "{src}",\n', ""),
                             (CONFIGS / "emotion_pick_v2.json").read_text(encoding="utf-8"))

    def test_default_is_scribe_and_not_in_defaults(self):
        self.assertNotIn("speaker_source", E.DEFAULTS)       # replays keep their run document's config byte-identical
        for name in ("emotion_pick_v1.json", "emotion_pick_v2.json", "emotion_pick_code_only.json"):
            c = ws.deep_merge(E.DEFAULTS, json.loads((CONFIGS / name).read_text(encoding="utf-8")))
            self.assertEqual(E.speaker_source(c), "scribe")
        self.assertEqual(E.speaker_source(cfg2({"speaker_source": "sarvam"})), "sarvam")
        with self.assertRaises(ValueError):
            E.speaker_source(cfg2({"speaker_source": "names"}))


class ScribeDefault(unittest.TestCase):
    def test_scribe_changes_nothing(self):
        ep = turn_ep()
        before = json.dumps([ep.lines, ep.words], sort_keys=True)
        ctx_before = render(ep.lines)
        with mock.patch.object(SL, "load", side_effect=AssertionError("no layer for scribe")):
            self.assertIsNone(E.apply_speaker_source(ep, cfg2(), E.ctx_cfg(cfg2()), dry_run=False, log=lambda m: None))
        self.assertEqual(json.dumps([ep.lines, ep.words], sort_keys=True), before)
        self.assertEqual(render(ep.lines), ctx_before)
        self.assertEqual(E.label_kind(ep), "Scribe label")

    def test_names_and_a_speaker_source_together_refused(self):
        ep = turn_ep()
        cfg = cfg2({"speaker_source": "sarvam", "ctx": {"speaker_names": True}})
        with self.assertRaises(ValueError):
            E.apply_speaker_source(ep, cfg, E.ctx_cfg(cfg), dry_run=True, log=lambda m: None)


class LayerApplied(unittest.TestCase):
    def apply(self, ep, labels, dry_run=False):
        lay = layer_for(ep, labels)
        cfg = cfg2({"speaker_source": "sarvam"})
        with mock.patch.object(SL, "load", return_value=lay):
            out = E.apply_speaker_source(ep, cfg, E.ctx_cfg(cfg), dry_run=dry_run, log=lambda m: None)
        return out

    def test_context_renders_the_layer_labels_anonymously(self):
        ep = turn_ep()
        self.apply(ep, {L(k): ("X9" if k % 3 else "Q") for k in range(1, 11)})
        txt = render(ep.lines)
        self.assertIn("L0001 [0:10.0] [speaker_0]:", txt)      # X9 first -> speaker_0
        self.assertIn("L0003 [0:15.0] [speaker_1]:", txt)      # Q -> speaker_1
        self.assertNotIn("X9", txt)
        self.assertNotIn("Q]", txt)
        self.assertEqual(ep.words[ep.by_id[L(3)]["words"][0]]["speaker"], "speaker_1")
        self.assertEqual(E.label_kind(ep), "sarvam label")

    def test_same_speaker_rule_reads_the_layer_not_scribe(self):
        # Scribe: L0005 + L0006 one speaker's turn -> B1 turn lift. The layer gives L0006 another speaker -> the
        # stop L0005 is lifted ALONE (no turn), exactly as for an alternating exchange.
        cfg = cfg2()
        ep = turn_ep()
        self.assertEqual(E.same_speaker_next(ep, L(5)), L(6))
        x, c = built(ep, payoff(9), build(1, 9, stop=5, land=9), cfg, {L(5)})
        self.assertEqual(x["emo"]["stop"]["case"], "B1_turn_lift")
        ep2 = turn_ep()
        labels = {r["id"]: r["speaker"] for r in ep2.lines if ep2.is_speech(r)}
        labels[L(6)] = "other"
        self.apply(ep2, labels)
        self.assertIsNone(E.same_speaker_next(ep2, L(5)))
        self.assertEqual(E.turn_run(ep2, L(5)), [L(5)])
        x2, c2 = built(ep2, payoff(9), build(1, 9, stop=5, land=9), cfg, {L(5)})
        self.assertNotEqual(x2["emo"]["stop"]["case"], "B1_turn_lift")
        self.assertNotEqual((c2.get("cold_open") or {}).get("lines"), [L(5), L(6)])

    def test_layer_can_create_a_same_speaker_turn(self):
        # the reverse: Scribe alternates L0006 / L0007; the layer says one speaker -> refusal note names the layer
        cfg = cfg2({"cold_open": {"turn_lift": False, "start_advance": False}})
        ep = turn_ep()
        labels = {r["id"]: r["speaker"] for r in ep.lines if ep.is_speech(r)}
        labels[L(5)] = labels[L(6)] = "other"
        self.apply(ep, labels)
        x, c = built(ep, payoff(9), build(1, 9, stop=5, land=9), cfg, {L(5)})
        self.assertEqual(x["emo"]["stop"]["case"], "B_refused_play_start")
        spk = ep.by_id[L(5)]["speaker"]
        self.assertTrue(spk.startswith("speaker_"))
        self.assertTrue(any(f"same speaker continues in L0006 (sarvam label {spk})" in f for f in x["flags"]),
                        x["flags"])

    def test_lift_ok_turn_runs_follow_the_layer(self):
        lc = dict(E.DEFAULTS["lift_ok"])
        ep = turn_ep()
        runs = {}
        E.strict_liftable_lines(ep, ws.DEFAULTS["cut"], lc, runs_out=runs)
        self.assertEqual(runs, {L(5): [L(5), L(6)]})
        ep2 = turn_ep()
        labels = {r["id"]: r["speaker"] for r in ep2.lines if ep2.is_speech(r)}
        labels[L(6)] = "other"
        self.apply(ep2, labels)
        r2 = {}
        E.strict_liftable_lines(ep2, ws.DEFAULTS["cut"], lc, runs_out=r2)
        self.assertEqual(r2, {})

    def test_rendered_clip_text_uses_the_layer(self):
        from shotvi.select.emotion_select import render_clip
        ep = turn_ep()
        labels = {r["id"]: "zz" for r in ep.lines if ep.is_speech(r)}
        self.apply(ep, labels)
        x, c = built(ep, payoff(9), build(1, 9, stop=1, land=9), cfg2(), set())
        txt = render_clip(ep, {"time_segments": c["time_segments"], "cold_open": bool(c["cold_open"])}, "C1").text
        self.assertIn("speaker_0", txt)
        self.assertNotIn("speaker_1", txt)

    def test_missing_source_placeholder_in_dry_run_only(self):
        ep = turn_ep()
        cfg = cfg2({"speaker_source": "llm_fix"})
        miss = SL.LayerMissing("no file")
        ph = {**layer_for(ep, {}), "placeholder": True}
        with mock.patch.object(SL, "load", side_effect=miss), mock.patch.object(SL, "placeholder", return_value=ph):
            got = E.apply_speaker_source(ep, cfg, E.ctx_cfg(cfg), dry_run=True, log=lambda m: None)
            self.assertTrue(got["placeholder"])
            with self.assertRaises(SL.LayerMissing):
                E.apply_speaker_source(turn_ep(), cfg, E.ctx_cfg(cfg), dry_run=False, log=lambda m: None)


if __name__ == "__main__":
    unittest.main()
