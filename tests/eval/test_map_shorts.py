"""Regression tests for eval.map_shorts (code-review fixes, 2026-09-27) on a synthetic episode.

Run: python -m unittest tests.eval.test_map_shorts
"""
import random
import unittest

from eval import map_shorts as M
from eval.schema_check import check_clip_core


def _vocab(n, seed):
    rng = random.Random(seed)
    syll = ["ka", "ra", "ma", "ni", "lo", "te", "vu", "pa", "di", "su", "go", "ba", "je", "ha", "yo", "chi"]
    out = set()
    while len(out) < n:
        out.add("".join(rng.choice(syll) for _ in range(rng.randint(2, 4))))
    return sorted(out)


def make_episode(n=3000, seed=0, vocab=900):
    """Pseudo-words with podcast-like timing: ~0.3s words, small gaps, a longer pause every ~12 words,
    speaker turns every ~40 words. Words repeat (Zipf-ish), so chance matches exist."""
    rng = random.Random(seed)
    V = _vocab(vocab, seed)
    W, t, spk = [], 0.0, "speaker_0"
    for k in range(n):
        w = V[min(int(rng.paretovariate(1.2)) - 1, len(V) - 1)] if rng.random() < 0.3 else rng.choice(V)
        d = rng.uniform(0.18, 0.45)
        W.append({"word": w, "start": round(t, 3), "end": round(t + d, 3), "speaker": spk})
        t += d + (rng.uniform(0.4, 0.9) if k % 12 == 11 else rng.uniform(0.02, 0.12))
        if k % 40 == 39:
            spk = "speaker_1" if spk == "speaker_0" else "speaker_0"
    return prep(W)


def prep(ws):
    for w in ws:
        w["n"] = M.norm(M.expand_numbers(w["word"]))
        w["n_alt"] = None
    return ws


def cut(E, ranges, speed=1.0, gap=0.3, replace=None):
    """A Short built from episode word ranges (playback order), re-timed from 0 at `speed`."""
    out, t = [], 0.0
    for a, b in ranges:
        base = E[a]["start"]
        for k in range(a, b):
            w = E[k]
            out.append({"word": w["word"], "start": round(t + (w["start"] - base) / speed, 3),
                        "end": round(t + (w["end"] - base) / speed, 3), "speaker": w["speaker"]})
        t = out[-1]["end"] + gap
    for pos, word in (replace or {}).items():
        out[pos]["word"] = word
    return prep(out)


def mapped(E, S, demote=None):
    ep = M.EpisodeIndex(E, demote_ranges=demote)
    res = M.map_short("t", S, ep, M.Sim())
    clip, reason = M.to_anchor_clip(res, E, {})
    core = [(p["start_word"], p["end_word"]) for p in clip["pieces"]] if clip else []
    return res, clip, reason, core


class MapShortsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.E = make_episode()

    def test_contiguous(self):
        res, clip, _, core = mapped(self.E, cut(self.E, [(1000, 1100)]))
        self.assertEqual(core, [(1000, 1099)])
        self.assertEqual(clip["n_blocks"], 1)

    def test_tightened_cut_kept(self):
        _, _, _, core = mapped(self.E, cut(self.E, [(1000, 1040), (1070, 1120)]))
        self.assertEqual(core, [(1000, 1039), (1070, 1119)])

    def test_real_far_insertion_survives(self):
        # a line from far away inserted between A and its continuation B: B's offset shifts by the inserted
        # duration, so it is not a chance excursion and must be kept
        _, _, _, core = mapped(self.E, cut(self.E, [(1000, 1040), (2500, 2525), (1040, 1090)]))
        self.assertIn((2500, 2524), core)
        self.assertEqual(core[0], (1000, 1039))

    def test_chance_excursion_rejected(self):
        # the Short ASR "hears" a K-gram that also occurs far away, inside audio that plays continuously
        for K in (5, 6, 8):
            far = {50 + t: self.E[2600 + t]["word"] for t in range(K)}
            res, _, _, core = mapped(self.E, cut(self.E, [(1000, 1100)], replace=far))
            self.assertEqual(core, [(1000, 1099)], f"K={K}")

    def test_speed_up_rescaled(self):
        res, _, _, core = mapped(self.E, cut(self.E, [(1000, 1180)], speed=1.2))
        self.assertEqual(core, [(1000, 1179)])
        self.assertAlmostEqual(res["time_scale"], 1.2, delta=0.02)
        self.assertTrue(any("x episode speed" in f for f in res["flags"]))

    def test_normal_speed_not_rescaled(self):
        res, _, _, _ = mapped(self.E, cut(self.E, [(1000, 1040), (1070, 1120)], gap=0.1))
        self.assertEqual(res["time_scale"], 1.0)

    def test_teaser_tie_prefers_body(self):
        # the episode opens with an exact copy of the moment (teaser); equal evidence -> the later copy
        E = self.E
        teaser = [dict(w) for w in E[1000:1100]]
        base = teaser[0]["start"]
        for w in teaser:
            w["start"], w["end"] = round(w["start"] - base + 1.0, 3), round(w["end"] - base + 1.0, 3)
        shift = teaser[-1]["end"] + 5.0
        body = [dict(w, start=round(w["start"] + shift, 3), end=round(w["end"] + shift, 3)) for w in E]
        E2 = prep(teaser + body)
        res, _, _, core = mapped(E2, cut(E, [(1000, 1100)]))
        self.assertEqual(core, [(1100, 1199)])
        self.assertTrue(any(f.startswith("ambiguous source") for f in res["flags"]))

    def test_teaser_demoted_even_if_it_matches_better(self):
        # teaser copy matches all 100 words; the body copy has 4 ASR differences (>= 90% as many): with the
        # teaser range demoted, the body copy wins
        E = self.E
        teaser = [dict(w) for w in E[1000:1100]]
        base = teaser[0]["start"]
        for w in teaser:
            w["start"], w["end"] = round(w["start"] - base + 1.0, 3), round(w["end"] - base + 1.0, 3)
        shift = teaser[-1]["end"] + 5.0
        body = [dict(w, start=round(w["start"] + shift, 3), end=round(w["end"] + shift, 3)) for w in E]
        for k in (1020, 1045, 1070, 1090):
            body[k]["word"] = "zzqx" + str(k)
        E2 = prep(teaser + body)
        S = cut(E, [(1000, 1100)])
        _, _, _, core = mapped(E2, S, demote=[(0, 99)])
        self.assertTrue(core and core[0][0] >= 100, core)
        _, _, _, core0 = mapped(E2, S)
        self.assertEqual(core0, [(0, 99)])     # without teaser info the better text match wins

    def test_doubtful_pieces_not_in_core(self):
        # foreign audio (not in the episode) with a filler inside; the filler may match a nearby episode filler
        E = [dict(w) for w in self.E]
        E[1052]["word"] = "ఉమ్"
        E = prep(E)
        S = cut(E, [(1000, 1050)])
        t = S[-1]["end"] + 0.3
        for k, word in enumerate(["qqa", "qqb", "qqc", "ఉమ్", "qqd", "qqe"]):
            S.append({"word": word, "start": round(t + 0.35 * k, 3), "end": round(t + 0.35 * k + 0.25, 3),
                      "speaker": "speaker_0"})
        S = prep(S)
        tail = cut(E, [(1300, 1350)])
        t0 = S[-1]["end"] + 0.3
        S += [dict(w, start=round(w["start"] + t0, 3), end=round(w["end"] + t0, 3)) for w in tail]
        res, clip, _, core = mapped(E, S)
        self.assertEqual(core, [(1000, 1049), (1300, 1349)])
        for d in res["details"]:
            if (d["episode_words"][0], d["episode_words"][1]) not in core:
                self.assertEqual(d["quality"], "doubtful")
                self.assertFalse(d["in_core"])

    def test_unmapped_reason(self):
        S = prep([{"word": f"nowhere{k}", "start": 0.4 * k, "end": 0.4 * k + 0.3, "speaker": "speaker_0"}
                  for k in range(40)])
        res, clip, reason, _ = mapped(self.E, S)
        self.assertIsNone(clip)
        self.assertEqual(reason, "no seed matches in this episode")


class HelpersTest(unittest.TestCase):
    def test_droppable(self):
        dr = lambda w: M.is_droppable(prep([{"word": w, "start": 0, "end": 0.1}])[0])  # noqa: E731
        for w in ("ఉమ్", "a", "I", "so"):
            self.assertTrue(dr(w), w)
        for w in ("రా", "నా", "మీ", "ఈ", "the", "కాదు"):
            self.assertFalse(dr(w), w)

    def test_short_id_from_url(self):
        f = M.short_id_from_url
        self.assertEqual(f("https://youtube.com/shorts/AbC_12-x?feature=share"), "AbC_12-x")
        self.assertEqual(f("https://www.youtube.com/watch?v=XyZ&t=5s"), "XyZ")
        self.assertEqual(f("https://youtu.be/QqQ/"), "QqQ")

    def test_schema_check_empty_and_all_spans(self):
        W = [{"word": "a", "start": 0.0, "end": 0.2}, {"word": "b", "start": 0.3, "end": 0.5}]
        errs = []
        check_clip_core("c", {"pieces": []}, W, errs)
        self.assertEqual(errs, ["c: no pieces"])
        errs = []
        bad = {"start_word": 0, "end_word": 9, "start": 0.0, "end": 0.5}
        check_clip_core("c", {"pieces": [bad, dict(bad, start_word=5)]}, W, errs)
        self.assertEqual(len(errs), 2)   # both bad spans reported, not just the first


if __name__ == "__main__":
    unittest.main()
