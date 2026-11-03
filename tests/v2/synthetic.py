"""A small synthetic Scribe transcript + speaker_fix layer for selection_v2 tests (no files, no network).

Timeline (seconds): three "scenes" of a scripted show.
  0-30   A (Surya) and B (Amma) at home; a zero-length filler; A says "అమ్మా"; B says "సూర్య"
  30-34  silence tag + a shot cut at 32.0          <- obvious scene edge (long gap, turnover, tag, shot)
  34-60  C (Swami) and D (Siva); a laugh tag; one line by E (lone speaker, low confidence)
  60.0   hard cut: no gap, total turnover, shot cut at 60.0
  60-90  F (customer) and G (Arjun); a censored tag; a stretched last word; an overlapping 'హా' line by H
"""
from __future__ import annotations

from selection_v2.inputs import EpisodeInputs, attach_layers
from shotvi.select.lines import build_lines


def _w(words, text, start, end, speaker):
    toks = text.split()
    n = len(toks)
    i0 = len(words)
    for k, tok in enumerate(toks):
        a = start + (end - start) * k / n
        b = start + (end - start) * (k + 1) / n if n > 1 else end
        words.append({"word": tok, "start": round(a, 3), "end": round(b, 3), "speaker": speaker, "logprob": -0.1})
    return [i0, len(words) - 1]


def make_transcript():
    words, sents = [], []

    def line(text, start, end, speaker):
        span = _w(words, text, start, end, speaker)
        sents.append({"id": len(sents), "text": text, "start": start, "end": end, "speaker": speaker, "words": span})

    # scene 1
    line("అమ్మా లేమ్మా", 1.0, 2.0, "speaker_0")          # L: A says అమ్మా -> next B => B = అమ్మ (role vote)
    line("ఏంటే అర్ధరాత్రి?", 2.4, 3.6, "speaker_1")
    line("ఆ", 3.9, 3.9, "speaker_0")                     # zero-length filler
    line("ఒక చిన్న విషయం చెప్పాలి", 4.2, 6.0, "speaker_0")
    line("సూర్య ఏంటి ఇది?", 6.5, 8.0, "speaker_1")       # B says సూర్య -> next A => A = Surya (cast vote)
    line("నాకు job వచ్చింది", 8.3, 10.0, "speaker_0")
    line("నిజంగా?", 10.4, 11.0, "speaker_1")
    line("అవును అమ్మా", 11.3, 12.5, "speaker_0")          # అమ్మా -> next B again
    line("చాలా happy గా ఉంది", 13.0, 15.0, "speaker_1")
    line("ok వెళ్తున్నా", 15.4, 17.0, "speaker_0")
    line("జాగ్రత్త", 17.3, 18.0, "speaker_1")
    line("ఉమ్", 18.2, 18.5, "speaker_0")
    line("bye", 19.0, 19.5, "speaker_1")
    line("bye అమ్మా", 20.0, 21.0, "speaker_0")
    line("వెళ్ళి రా", 21.5, 23.0, "speaker_1")
    line("సరే", 23.3, 24.0, "speaker_0")
    line("రా", 24.5, 25.0, "speaker_1")
    line("ok", 25.3, 26.0, "speaker_0")
    line("ఉమ్మ్", 26.2, 26.5, "speaker_1")
    line("వెళ్తున్నా అమ్మా", 27.0, 29.0, "speaker_0")
    # scene 2 (gap 29.0 -> 34.0 with silence tag and shot cut 32.0)
    line("స్వామి ఏం చేస్తున్నావ్ రా?", 34.0, 36.0, "speaker_3")   # D says స్వామి -> next C => C = Swami
    line("కూర్చున్నా అన్నా", 36.3, 37.5, "speaker_2")
    line("శివ నువ్వు ఇక్కడే ఉన్నావా?", 38.0, 40.0, "speaker_2")  # C says శివ -> next D => D = Siva
    line("అవును", 40.3, 41.0, "speaker_3")
    line("job అంటే పండగ రా", 41.5, 44.0, "speaker_2")
    line("హహ", 44.2, 44.6, "speaker_3")
    line("నాకు ముందుచూపు ఉంది", 45.0, 47.0, "speaker_2")
    line("ఏం ముందుచూపు రా", 47.3, 49.0, "speaker_3")
    line("చూస్తూ ఉండు", 49.3, 50.5, "speaker_2")
    line("Happy birthday to you", 51.0, 53.0, "speaker_4")   # lone speaker E inside scene 2 (suspect)
    line("సరే రా", 53.5, 54.5, "speaker_3")
    line("వెళ్దాం పద", 55.0, 56.5, "speaker_2")
    line("పద", 56.8, 57.4, "speaker_3")
    line("నువ్వే పద", 57.7, 59.0, "speaker_2")
    line("సరే", 59.2, 60.0, "speaker_3")
    # scene 3: hard cut at 60.0 (no gap, total turnover, shot cut)
    line("Sir నాకు ఆకలి వేస్తుంది", 60.0, 62.0, "speaker_5")
    line("హా", 61.8, 62.1, "speaker_7")                    # overlaps previous line, another speaker
    line("ఏంటి?", 62.4, 63.0, "speaker_6")
    line("మీ dosa నాకు ఇవ్వండి", 63.3, 65.0, "speaker_5")
    line("పిచ్చా నీకు?", 65.4, 66.5, "speaker_6")
    line("five star rating ఇవ్వండి", 67.0, 69.0, "speaker_5")
    line("ఛీ [bleep] వెళ్ళు", 69.5, 71.0, "speaker_6")
    line("ఊ", 71.2, 71.5, "speaker_5")
    line("thank you sir", 72.0, 90.0, "speaker_5")          # stretched last word (ends 18 s later)
    events = [
        {"text": "[నిశబ్దం]", "start": 29.5, "end": 33.5},
        {"text": "[నవ్వు]", "start": 44.7, "end": 45.0},
        {"text": "[సెన్సార్డ్]", "start": 69.8, "end": 70.2},
        {"text": "[వడసన]", "start": 75.0, "end": 76.0},
        {"text": "[బ్యాక్ గ్రౌండ్ మ్యూజిక్]", "start": 76.0, "end": 88.0},
    ]
    return {"sentences": sents, "word_timestamps": words, "audio_events": events, "audio_duration_secs": 92.0,
            "speakers": sorted({w["speaker"] for w in words})}


KEYS = {"speaker_0": ("A", "Surya", 0.9), "speaker_1": ("B", "Mother", 0.9), "speaker_2": ("C", "Swami", 0.9),
        "speaker_3": ("D", "Siva", 0.9), "speaker_4": ("E", "Arjun", 0.3), "speaker_5": ("F", "Arjun", 0.9),
        "speaker_6": ("G", "Customer", 0.6), "speaker_7": ("H", "Customer", 0.3)}


def make_fix(tr, keys=None):
    keys = keys or KEYS
    cast = {}
    lines = []
    for s in tr["sentences"]:
        key, name, conf = keys[s["speaker"]]
        cast.setdefault(key, {"key": key, "name": name, "desc": f"{name} (synthetic)", "aka": [], "n_lines": 0})
        cast[key]["n_lines"] += 1
        lines.append({"id": s["id"], "start": s["start"], "end": s["end"], "words": s["words"],
                      "scribe": s["speaker"], "key": key, "name": name, "confidence": conf})
    if "F" in cast:
        cast["F"]["aka"] = ["Virgin"]
    return {"video_id": "synthetic", "schema": "speaker_fix/2", "prompt": "test", "cast": list(cast.values()),
            "lines": lines, "coverage": {"n_lines": len(lines)}}


class _Paths:
    def title(self):
        return "Synthetic Surya Web Series test episode"


def make_inputs(cuts=(32.0, 60.0, 80.0), with_fix=True, duration=92.0, transcript=None, keys=None):
    tr = transcript or make_transcript()
    fix = make_fix(tr, keys)
    rows = build_lines("synthetic", asr_fix=False, transcript=tr)
    attach_layers("synthetic", rows, tr, fix, None)
    tr.setdefault("audio_duration_secs", duration)
    return EpisodeInputs("synthetic", _Paths(), tr, rows, fix, None, {"title": "Synthetic Surya Web Series"},
                         {"asr_fix": False, "speaker_fix": True}, [])
