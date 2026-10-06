"""Word-level A/B diff of two Scribe transcripts of the same audio (e.g. without vs with keyterms).

Aligns normalized words with difflib, then reports: every changed span (A words -> B words, times, confidences),
speaker relabelling on aligned words (B labels mapped to A by majority), timestamp shifts on aligned words,
audio-event differences, and totals (words, mean confidence, speakers).

usage: python tools/transcript_diff.py A.json B.json [--out diff.json] [--window 1.0]
"""
from __future__ import annotations

import argparse
import difflib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shotvi.segment.text import norm  # noqa: E402


def conf(w):
    lp = w.get("logprob")
    return None if lp is None else math.exp(min(0.0, lp))


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def diff(a: dict, b: dict) -> dict:
    wa, wb = a["word_timestamps"], b["word_timestamps"]
    na, nb = [norm(w["word"]) for w in wa], [norm(w["word"]) for w in wb]
    sm = difflib.SequenceMatcher(a=na, b=nb, autojunk=False)
    changes, pairs = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            pairs += list(zip(range(i1, i2), range(j1, j2)))
            continue
        t0 = wa[i1]["start"] if i1 < i2 else (wb[j1]["start"] if j1 < j2 else None)
        changes.append({
            "op": op, "a_words": [i1, i2 - 1] if i2 > i1 else None, "b_words": [j1, j2 - 1] if j2 > j1 else None,
            "t": round(t0, 2) if t0 is not None else None,
            "a": " ".join(w["word"] for w in wa[i1:i2]), "b": " ".join(w["word"] for w in wb[j1:j2]),
            "a_conf": [round(conf(w), 2) for w in wa[i1:i2] if conf(w) is not None],
            "b_conf": [round(conf(w), 2) for w in wb[j1:j2] if conf(w) is not None],
            "a_spk": sorted({w.get("speaker") for w in wa[i1:i2]} - {None}),
            "b_spk": sorted({w.get("speaker") for w in wb[j1:j2]} - {None}),
        })
    # speakers: map each B label to the A label it most often co-occurs with on aligned words
    co = defaultdict(Counter)
    for i, j in pairs:
        co[wb[j].get("speaker")][wa[i].get("speaker")] += 1
    mapping = {sb: c.most_common(1)[0][0] for sb, c in co.items()}
    spk_changes = [(i, j) for i, j in pairs if mapping.get(wb[j].get("speaker")) != wa[i].get("speaker")]
    ds = [abs(wb[j]["start"] - wa[i]["start"]) for i, j in pairs]
    de = [abs(wb[j]["end"] - wa[i]["end"]) for i, j in pairs]
    same_text = sum(1 for i, j in pairs if wa[i]["word"] == wb[j]["word"])

    def evs(t):
        return [(e["text"], e["start"], e["end"]) for e in t.get("audio_events") or []]

    ea, eb = evs(a), evs(b)
    used, ev_same, ev_moved, ev_only_a = set(), 0, [], []
    for e in ea:
        best = None
        for k, f in enumerate(eb):
            if k in used or f[0] != e[0]:
                continue
            ov = min(e[2], f[2]) - max(e[1], f[1])
            if ov > 0 or abs(f[1] - e[1]) < 0.5:
                best = k
                break
        if best is None:
            ev_only_a.append(e)
        else:
            used.add(best)
            f = eb[best]
            if abs(f[1] - e[1]) < 0.05 and abs(f[2] - e[2]) < 0.05:
                ev_same += 1
            else:
                ev_moved.append({"a": e, "b": f})
    ev_only_b = [f for k, f in enumerate(eb) if k not in used]

    def mean_conf(ws):
        c = [conf(w) for w in ws if conf(w) is not None]
        return round(sum(c) / len(c), 4) if c else None

    def low(ws, th=0.5):
        return sum(1 for w in ws if conf(w) is not None and conf(w) < th)

    def talk(t):
        c = Counter()
        for w in t["word_timestamps"]:
            c[w.get("speaker")] += w["end"] - w["start"]
        return {k: round(v, 1) for k, v in c.most_common()}

    return {
        "totals": {
            "a_words": len(wa), "b_words": len(wb), "aligned_equal": len(pairs), "aligned_same_surface": same_text,
            "a_mean_conf": mean_conf(wa), "b_mean_conf": mean_conf(wb),
            "a_low_conf_words(<0.5)": low(wa), "b_low_conf_words(<0.5)": low(wb),
            "a_sentences": len(a.get("sentences") or []), "b_sentences": len(b.get("sentences") or []),
            "a_speakers": a.get("speakers"), "b_speakers": b.get("speakers"),
            "a_ghosts": a.get("ghost_speakers"), "b_ghosts": b.get("ghost_speakers"),
            "a_talk_s": talk(a), "b_talk_s": talk(b),
        },
        "n_changes": len(changes),
        "changes": changes,
        "speaker_map_b_to_a": mapping,
        "speaker_changed_on_aligned": [{"a_word": i, "b_word": j, "t": wa[i]["start"], "word": wa[i]["word"],
                                        "a": wa[i].get("speaker"), "b": wb[j].get("speaker"),
                                        "b_mapped": mapping.get(wb[j].get("speaker"))} for i, j in spk_changes],
        "timestamps": {
            "start_shift_median": round(statistics.median(ds), 3) if ds else None,
            "start_shift_p95": round(sorted(ds)[int(0.95 * (len(ds) - 1))], 3) if ds else None,
            "start_shift_max": round(max(ds), 3) if ds else None,
            "end_shift_max": round(max(de), 3) if de else None,
            "n_shift_over_0.1s": sum(1 for x in ds if x > 0.1),
            "n_shift_over_0.5s": sum(1 for x in ds if x > 0.5),
        },
        "audio_events": {"a": len(ea), "b": len(eb), "same": ev_same, "moved": ev_moved,
                         "only_a": ev_only_a, "only_b": ev_only_b},
    }


def window(t: dict, t0: float, t1: float) -> str:
    return " ".join(w["word"] for w in t["word_timestamps"] if w["end"] >= t0 and w["start"] <= t1)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    d = diff(load(a.a), load(a.b))
    if a.out:
        Path(a.out).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(d["totals"], ensure_ascii=False))
    print(f"{d['n_changes']} changed spans; timestamps {d['timestamps']}")
    for c in d["changes"]:
        print(f"{c['t']:>8} {c['op']:<7} A[{c['a']}] {c['a_conf']}  ->  B[{c['b']}] {c['b_conf']}")


if __name__ == "__main__":
    main()
