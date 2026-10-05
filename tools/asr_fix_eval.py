"""Score runs/asr_fix/<id>/corrections.json against the human ASR answer key (Couple Goals Ep1/Ep2).

The key lives ONLY here (never in prompts or pipeline code). Usage:
    PYTHONIOENCODING=utf-8 python tools/asr_fix_eval.py [--json out.json]

Per key item: which layer surfaced it (flag at p<0.5/0.6/0.7, context read, relisten), the text-only guess, the
relisten verdict, and whether the applied correction is exact. Then every correction NOT in the key, for manual
precision review (annotated when the reviewer's low-impact table in runs/gt/review_r2_strategist.md lists it).
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shotvi.segment.asr_fix import load_corrections, squash, wconf  # noqa: E402

# (video, id, time, heard words (first word is searched near time), expected text for those words, uncertain?)
KEY = [
    ("QWIvgoTQGKk", "E2-187 ప్రతికించారు", 187.9, "ప్రతికించారు", ["బ్రతికించారు"], False),
    ("QWIvgoTQGKk", "E2-254 blood గుర పడితే", 254.7, "గుర పడితే", ["group అడిగితే"], False),
    ("QWIvgoTQGKk", "E2-371 కుక్కను", 371.8, "కుక్కను", ["cook ను", "cookను"], False),
    ("QWIvgoTQGKk", "E2-1224 బిస్మిల్లా బాత్", 1224.9, "బిస్మిల్లా", ["బిసిబేళ", "బిసిబేళే", "బిసిబెళ", "బిసిబెల్లా",
                                                                       "బిసిబెళే"], True),
    ("QWIvgoTQGKk", "E2-972 hop off", 972.6, "hop off", ["half"], True),
    ("QWIvgoTQGKk", "E2-290 ఏం వండవు", 290.3, "ఏం వండవు", ["ఏం వండావు"], False),
    ("QWIvgoTQGKk", "E2-775 ఇరుకుదా", 775.8, "ఇరుకుదా", ["ఇవ్వొచ్చు కదా", "ఇవ్వచ్చు కదా", "ఇవ్వొచ్చుకదా"], False),
    ("j14Om1SaFYE", "E1-902 కూరంటా", 902.6, "కూరంటా", ["గోరింటాకు", "గోరింట"], False),
    ("j14Om1SaFYE", "E1-916 కూరంటా", 916.3, "కూరంటా", ["గోరింటాకు", "గోరింట"], False),
    ("j14Om1SaFYE", "E1-1052 ఈ week చివరనే", 1052.0, "week", ["వీధి"], False),
    ("j14Om1SaFYE", "E1-1094 మటన్ గోలా", 1094.4, "గోలా", ["కీమా"], False),
    ("j14Om1SaFYE", "E1-571 విగించాలా", 571.2, "విగించాలా", ["బిగించాలా"], False),
    ("j14Om1SaFYE", "E1-287 తుమ్ము మొహం దానా", 287.5, "తుమ్ము", None, True),   # unknown: report what is heard
]
# Reviewer's low-impact suspects (same review table); used only to annotate non-key corrections.
EXTRA = [
    ("QWIvgoTQGKk", 420.6, "ఏనే", "పదానికే"), ("QWIvgoTQGKk", 422.5, "ఎరికిన్చేసావెంటమ్మా", "ఇరికించేసావేంటమ్మా"),
    ("QWIvgoTQGKk", 788.4, "మదక్", "మొద్దు"), ("QWIvgoTQGKk", 386.7, "మండే", "మండిపడిపోతాడు"),
    ("QWIvgoTQGKk", 347.0, "ఏలేసి", "లేచి"), ("QWIvgoTQGKk", 471.5, "ఓడకి", "వాడికి"),
    ("QWIvgoTQGKk", 508.5, "మే", "మీ"), ("QWIvgoTQGKk", 1054.1, "with", "ది"), ("QWIvgoTQGKk", 1228.6, "Anty", "Aunty"),
    ("j14Om1SaFYE", 1071.9, "అజ్జన్", "అర్జెంట్"), ("j14Om1SaFYE", 1090.1, "appalam", "అప్పడం"),
    ("j14Om1SaFYE", 222.2, "గొర్ర", "గొర్రె"), ("j14Om1SaFYE", 561.6, "గంభీరం", "? (uncertain)"),
]


def locate(ws, t, heard, tol=4.0):
    toks = heard.split()
    for i, w in enumerate(ws):
        if abs(w["start"] - t) <= tol and squash(w["word"]) == squash(toks[0]):
            j = i + len(toks) - 1
            if all(squash(ws[i + k]["word"]) == squash(toks[k]) for k in range(len(toks))):
                return i, j
    raise KeyError(f"{heard} near {t}")


def overl(a0, a1, b0, b1):
    return a0 <= b1 and b0 <= a1


def judge(ws, k0, k1, c, expected):
    """Exact if the corrected union span equals Scribe's text there with the key span replaced by an expected text."""
    u0, u1 = min(k0, c["start_word"]), max(k1, c["end_word"])
    got = " ".join([ws[i]["word"] for i in range(u0, c["start_word"])] + [c["corrected"]]
                   + [ws[i]["word"] for i in range(c["end_word"] + 1, u1 + 1)])
    best = 0.0
    for e in expected:
        exp = " ".join([ws[i]["word"] for i in range(u0, k0)] + [e] + [ws[i]["word"] for i in range(k1 + 1, u1 + 1)])
        if squash(got) == squash(exp):
            return "exact", 1.0, got
        best = max(best, difflib.SequenceMatcher(None, squash(got), squash(exp)).ratio())
    return "differs", round(best, 2), got


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--dir", default=str(ROOT / "runs" / "asr_fix"))
    ap.add_argument("--band-dir", default=str(ROOT / "runs" / "asr_fix_band"),
                    help="optional extra run (e.g. the 0.5-0.6 band, flag+relisten only); its rows are tagged band")
    a = ap.parse_args(argv)
    out = {"key": [], "non_key": []}
    key_spans = {}
    for vid in sorted({k[0] for k in KEY}):
        tr = json.loads((ROOT / "scribe_dataset" / vid / f"{vid}_scribe_transcript.json").read_text(encoding="utf-8"))
        ws = tr["word_timestamps"]
        res = load_corrections(Path(a.dir) / vid / "corrections.json")
        if not res.get("corrections") and not res.get("uncertain"):
            print(f"{vid}: no corrections.json")
            continue
        band = load_corrections(Path(a.band_dir) / vid / "corrections.json") if a.band_dir else {}
        for c in band.get("corrections", []):
            c["band"] = True
        corr = res["corrections"] + [c for c in band.get("corrections", []) if not any(
            overl(c["start_word"], c["end_word"], x["start_word"], x["end_word"]) for x in res["corrections"])]
        sus = res.get("suspects", []) + [dict(x, found_by=["flag(band)"]) for x in band.get("suspects", [])]
        ctx_rej = [r for r in res.get("rejected", []) if r.get("layer") == "context"]
        print(f"\n=== {vid}  ({len(corr)} corrections, {sum(c['applied'] for c in corr)} applied, "
              f"{len(res.get('uncertain', []))} uncertain)")
        for v, name, t, heard, expected, unsure in [k for k in KEY if k[0] == vid]:
            k0, k1 = locate(ws, t, heard)
            key_spans.setdefault(vid, []).append((k0, k1))
            confs = [wconf(ws[i]) for i in range(k0, k1 + 1)]
            mc = min(c for c in confs if c is not None)
            flag = {th: mc < th for th in (0.5, 0.6, 0.7)}
            s = [x for x in sus if overl(x["start_word"], x["end_word"], k0, k1)]
            found = sorted({f for x in s for f in x["found_by"]})
            guess = next((x["guess"] for x in s if x.get("guess")), "")
            cs = [c for c in corr if overl(c["start_word"], c["end_word"], k0, k1)]
            app = next((c for c in cs if c["applied"]), None)
            ctxc = next((c for c in cs if c["source"] == "context"), None)
            unc = next((u for u in res.get("uncertain", []) + band.get("uncertain", [])
                        if overl(u["start_word"], u["end_word"], k0, k1)), None)
            row = {"video": vid, "item": name, "uncertain_key": unsure, "words": [k0, k1], "min_conf": round(mc, 2),
                   "flag@0.5": flag[0.5], "flag@0.6": flag[0.6], "flag@0.7": flag[0.7], "found_by": found,
                   "context_guess": guess}
            if expected and guess:
                row["context_guess_verdict"] = judge(ws, k0, k1, {"start_word": s[0]["start_word"],
                                                                   "end_word": s[0]["end_word"],
                                                                   "corrected": guess}, expected)[:2]
            if app:
                row["applied"] = {"corrected": app["corrected"], "span": [app["start_word"], app["end_word"]],
                                  "conf": app["confidence"], "reason": app["reason"], "band": app.get("band", False)}
                if expected:
                    row["applied_verdict"] = judge(ws, k0, k1, app, expected)
            rv = (ctxc or {}).get("relisten") or (unc or {}).get("relisten_low") or {}
            if not app:
                row["relisten"] = (ctxc or {}).get("relisten", {}).get("verdict") or (unc or {}).get("verdict") or \
                    ("not a suspect" if not s else "?")
                if rv.get("corrected"):
                    row["relisten_low"] = {"corrected": rv["corrected"], "conf": rv.get("confidence")}
                    if expected:
                        row["relisten_low_verdict"] = judge(ws, k0, k1, {"start_word": (ctxc or unc)["start_word"],
                                                                          "end_word": (ctxc or unc)["end_word"],
                                                                          "corrected": rv["corrected"]}, expected)[:2]
            out["key"].append(row)
            print(json.dumps(row, ensure_ascii=False))
        for c in corr:
            if any(overl(c["start_word"], c["end_word"], k0, k1) for k0, k1 in key_spans[vid]):
                continue
            ex = next((e for e in EXTRA if e[0] == vid and abs(ws[c["start_word"]]["start"] - e[1]) < 3
                       and any(squash(e[2]) in squash(ws[i]["word"]) for i in range(c["start_word"], c["end_word"] + 1))),
                      None)
            a0, a1 = max(0, c["start_word"] - 4), min(len(ws) - 1, c["end_word"] + 4)
            row = {"video": vid, "t": round(c["start"], 1), "words": [c["start_word"], c["end_word"]],
                   "heard": c["heard"], "corrected": c["corrected"], "source": c["source"], "applied": c["applied"],
                   "conf": c["confidence"], "found_by": c["found_by"], "reason": c["reason"],
                   "context": " ".join(ws[i]["word"] for i in range(a0, a1 + 1)),
                   "relisten": c.get("relisten"), "reviewer_table": ex[3] if ex else None, "band": c.get("band", False)}
            out["non_key"].append(row)
    print("\n=== corrections NOT in the key")
    for r in out["non_key"]:
        print(json.dumps({k: v for k, v in r.items() if k != "relisten"}, ensure_ascii=False))
    kk = out["key"]
    print(f"\nkey items: {len(kk)}; surfaced (any layer): {sum(bool(r['found_by']) for r in kk)}; applied exact: "
          f"{sum(r.get('applied_verdict', ('',))[0] == 'exact' for r in kk)}; applied (any): "
          f"{sum('applied' in r for r in kk)}")
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
