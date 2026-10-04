"""Build docs/pipeline.html: an offline, step-by-step explainer of the Shotvi pipeline,
filled with the REAL data of one clip followed end to end (FS1DI5D_hUk "Big Comeback",
run sel_r5_flash), plus a real ending-fix example from TBlm6wcij94 (run sel_r6_flash).

usage: python tools/build_pipeline_explainer.py
No LLM calls; it only reads files already on disk.
"""
import csv
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SHOTVI_OPENING_MODE", "rules")   # never call the LLM from here

VID, RUN, CID = "FS1DI5D_hUk", "sel_r5_flash", "c000"
VID2, RUN2, CID2 = "TBlm6wcij94", "sel_r6_flash", "c014"


def jload(p):
    return json.loads((ROOT / p).read_text(encoding="utf-8"))


def mmss(t):
    return f"{int(t // 60)}:{int(t % 60):02d}"


def words_text(W, i0, i1):
    return " ".join(W[i]["word"] for i in range(i0, i1 + 1))


def main():
    T = jload(f"scribe_dataset/{VID}/{VID}_scribe_transcript.json")
    W = T["word_timestamps"]
    meta = jload(f"scribe_dataset/{VID}/youtube_meta.json") if (ROOT / f"scribe_dataset/{VID}/youtube_meta.json").exists() else {}
    prop = jload(f"runs/{RUN}/{VID}.json")
    dbg = jload(f"runs/{RUN}/debug/{VID}.json")
    seg = jload(f"runs/segment_r2/{VID}/segments.json")
    clip = next(p for p in prop["proposals"] if p["id"] == CID)
    trace = dbg["traces"][CID]
    cand = dbg["candidates"][CID]
    D = {}

    # ---------------- A. download
    mp4 = ROOT / f"scribe_dataset/{VID}/{VID}.mp4"
    wav = ROOT / f"scribe_dataset/{VID}/{VID}_audio.wav"
    D["download"] = {
        "url": f"https://youtu.be/{VID}", "title": meta.get("title", ""), "channel": meta.get("channel", ""),
        "duration": T["audio_duration_secs"], "mp4_mb": round(mp4.stat().st_size / 1e6), "wav_mb": round(wav.stat().st_size / 1e6),
        "chapters": [{"t": mmss(c["start"]), "title": c["title"]} for c in (meta.get("chapters") or [])[:8]],
        "n_chapters": len(meta.get("chapters") or []),
        "video_src": f"../scribe_dataset/{VID}/{VID}.mp4",
    }

    # ---------------- B. transcribe
    i_hook = clip["pieces"][0]["start_word"]
    region_lo = min(p["start_word"] for p in clip["pieces"])
    sample = [dict(W[i], i=i) for i in range(787, 795)]
    t0, t1 = W[781]["start"] - 6, W[860]["end"]
    sents = [s for s in T["sentences"] if t0 <= s["start"] <= t1]
    from collections import Counter
    ev = Counter(e["text"] for e in T["audio_events"])
    D["transcribe"] = {
        "n_words": T["total_words"], "n_sentences": T["total_sentences"], "speakers": T["speakers"],
        "events": ev.most_common(4), "credits": round(T["audio_duration_secs"] / 60 * 66.7),
        "sample_words": sample,
        "sentences": [{"t": mmss(s["start"]), "spk": s["speaker"][-1], "text": s["text"]} for s in sents][:16],
    }

    # ---------------- C. segment
    st = seg["stats"]
    units = {u["id"]: u for u in seg["units"]}
    txt = {}
    for line in (ROOT / f"runs/segment_r2/{VID}/transcript.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"U(\d+) ", line)
        if m:
            txt[int(m.group(1))] = line
    near = [u for u in seg["units"] if u["words"][1] >= 770 and u["words"][0] <= 905]
    link = next((l for l in seg["qa_links"] if l["answer"]["words"][0] <= 787 <= l["answer"]["words"][1]), None)
    teaser = next((r for r in seg.get("intro_ranges", []) if r["kind"] == "teaser"), None)
    host = seg["host"]
    D["segment"] = {
        "pauses": {k: seg["params"]["pauses"][k] for k in ("p50", "p90", "p97", "minor", "major")},
        "n_turns": len(seg["turns"]), "n_units": len(seg["units"]),
        "n_backchannels": sum(len(t["backchannels"]) for t in seg["turns"]),
        "n_links": len(seg["qa_links"]),
        "host": host, "speakers": [{"id": s["id"], "role": s["role"], "share": s["talk_share"]} for s in seg["speakers"]],
        "teaser": {"start": mmss(teaser["start"]), "end": mmss(teaser["end"]), "n": len(teaser.get("fragments", []))} if teaser else None,
        "units": [txt[u["id"]] for u in near if u["id"] in txt][:9],
        "link": ({"q": words_text(W, link["question"]["core_words"][0], link["question"]["core_words"][1]) if link.get("question", {}).get("core_words") else "",
                  "a_start": mmss(link["answer"]["start"]), "a_dur": round(link["answer"]["duration"])} if link else None),
        "unit_p50": st.get("unit_duration", {}).get("p50") if isinstance(st.get("unit_duration"), dict) else None,
    }

    # ---------------- D1. discover
    D["discover"] = {
        "n_chunks": len(dbg["chunks"]),
        "chunks": [{"id": c["id"], "units": c["units"], "tok": c.get("est_tokens_real"), "n": c.get("n_moments")} for c in dbg["chunks"]],
        "n_raw": len(dbg["raw_moments"]), "n_merged": len(dbg["moments"]),
        "moments": [{"type": m["type"], "strength": m["strength"], "hook": m["hook_quote"][:90],
                     "units": f"U{m['start_unit']}–U{m['end_unit']}", "ours": i == 0}
                    for i, m in enumerate(dbg["moments"])][:12],
        "ours": dbg["moments"][0],
    }

    # ---------------- D2. tighten
    tt = trace["tighten"]
    off = tt["window_blocks"][0][0]
    att = tt["attempts"][0]
    r0, r1 = off + att["region"]["first"], off + att["region"]["last"]
    cuts = []
    for c in att["cuts"]:
        a, b = off + c["first"], off + c["last"]
        cuts.append({"a": a, "b": b, "why": c["why"], "text": words_text(W, a, b),
                     "secs": round(W[b]["end"] - W[a]["start"], 1)})
    # word stream sample (what the tightener reads)
    try:
        from shotvi.select.config import load_config
        from shotvi.select.context import load_ctx
        from shotvi.select.window import build_stream
        try:
            cfg = load_config(str(ROOT / "shotvi/select/configs/flash.json"))
        except TypeError:
            cfg = load_config()
        ctx = load_ctx(str(ROOT / f"scribe_dataset/{VID}/{VID}_scribe_transcript.json"), cfg)
        stream = build_stream(ctx, [(781, 836)]).text
    except Exception as e:   # explainer must still build
        stream = f"(could not render stream: {e})"
    # region words with cut marks (first ~260 words of the region for display)
    cut_of = {}
    for k, c in enumerate(cuts):
        for i in range(c["a"], c["b"] + 1):
            cut_of[i] = k
    toks = [{"w": W[i]["word"], "c": cut_of.get(i, -1)} for i in range(r0, min(r1, r0 + 330) + 1)]
    D["tighten"] = {"window": [off, tt["window_blocks"][0][1]], "plan": att["plan"], "point": att["point"],
                    "region": [r0, r1], "region_t": [mmss(W[r0]["start"]), mmss(W[r1]["end"])],
                    "region_secs": round(W[r1]["end"] - W[r0]["start"]), "cuts": cuts, "stream": stream, "tokens": toks}

    # ---------------- D3. joins
    j = trace["joins"]
    f0 = j["round1"]["flagged"][0] if j["round1"]["flagged"] else None
    D["joins"] = {"n": j["round1"]["n_joins"], "flagged": len(j["round1"]["flagged"]),
                  "example": f0, "before": words_text(W, 787, 796), "added": words_text(W, 781, 786),
                  "round2": len(j.get("round2", {}).get("flagged", []))}

    # ---------------- D4/D5. score + opener fix
    of = trace["opener_fix"]
    res = {r["mode"]: r["scores"] for r in of["results"]}
    P0 = clip["pieces"][0]
    D["opener"] = {"weak": cand["info"]["weak_opener"]["first_words"],
                   "cold": {"reason": of["picker"]["cold_open"]["reason"], "scores": res.get("cold_open"),
                            "line": words_text(W, P0["start_word"], P0["end_word"])},
                   "earlier": {"reason": of["picker"]["earlier_start"]["reason"], "scores": res.get("earlier_start")},
                   "struck": words_text(W, *of["options"]["cold_open"]["struck_opener"]),
                   "kept": "cold_open"}
    J = cand["judge"]
    D["score"] = {k: J[k] for k in ("hook", "standalone", "payoff", "value_emotion", "overall", "first_words",
                                    "last_words", "opener_problem", "ending_problem", "critique", "relatable")}

    # ---------------- D6. ending fix (health podcast, real)
    T2 = jload(f"scribe_dataset/{VID2}/{VID2}_scribe_transcript.json")
    e = jload(f"runs/{RUN2}/debug/{VID2}.json")["traces"][CID2]["ending_fix"]
    p2 = next(p for p in jload(f"runs/{RUN2}/{VID2}.json")["proposals"] if p["id"] == CID2)
    D["ending"] = {"title": p2["title"], "question": e["picker"]["opening_question"],
                   "last_before": e["picker"]["last_sentence"], "kind": e["picker"]["last_sentence_kind"],
                   "verdict": e["picker"]["verdict_quote"], "action": e["picker"]["action"],
                   "reason": e["picker"]["reason"], "scores": e["scores"]}
    rows2 = list(csv.DictReader(open(ROOT / "data/human/ratings_sel_r5_flash.csv", encoding="utf-8-sig")))
    r5 = next((r for r in rows2 if r["video_id"] == VID2 and r["clip_id"] == "c014"), None)
    rows3 = list(csv.DictReader(open(ROOT / "data/human/ratings_sel_r6_flash.csv", encoding="utf-8-sig")))
    r6 = next((r for r in rows3 if r["clip_id"] == "c014"), None)
    D["ending"]["human"] = {"before": r5 and {"payoff": r5["payoff_1to5"], "overall": r5["overall_1to5"], "note": r5["notes"]},
                            "after": r6 and {"payoff": r6["payoff_1to5"], "overall": r6["overall_1to5"]}}

    # ---------------- D7/D8. themes + write
    D["write"] = {**cand["write"], "flags": clip.get("flags", [])}
    D["themes"] = dbg.get("themes")

    # ---------------- D9. rank
    D["rank"] = {"cutoff": prop["cutoff"],
                 "shown": [{"rank": p["rank"], "title": p["title"], "overall": p["scores"]["overall"], "tier": p["tier"],
                            "dur": round(p["edited_duration"]), "ours": p["id"] == CID} for p in prop["proposals"]],
                 "rejected": [{"id": r["id"], "why": r.get("reject_reasons") or [],
                               "overall": (r.get("scores") or {}).get("overall"),
                               "at": mmss(r["region"]["start"]), "dur": round(r["edited_duration"])}
                              for r in prop.get("rejected", [])][:6],
                 "cost_usd": prop["cost"].get("usd"), "calls": prop["cost"].get("n_llm_calls")}

    # ---------------- E. final clip + review
    pieces = clip["pieces"]
    lines = []
    for p in pieces:
        lines.append({"src": mmss(W[p["start_word"]]["start"]), "text": words_text(W, p["start_word"], p["end_word"])})
    rows = list(csv.DictReader(open(ROOT / "data/human/ratings_sel_r5_flash.csv", encoding="utf-8-sig")))
    hr = next(r for r in rows if r["video_id"] == VID and r["clip_id"] == CID)
    m = clip.get("metrics") or cand["metrics"]
    D["final"] = {"title": clip["title"], "duration": round(clip["edited_duration"], 1), "n_pieces": len(pieces),
                  "region_secs": round(cand["metrics"]["region_span"]), "keep_ratio": round(cand["metrics"]["keep_ratio"], 2),
                  "lines": lines, "video_src": f"../runs/previews/{RUN}/{VID}/01_{CID}.mp4",
                  "src_start": W[r0]["start"], "src_end": W[r1]["end"],
                  "pieces_json": pieces[:3],
                  "human": {k: hr[k] for k in ("hook_1to5", "standalone_1to5", "payoff_1to5", "overall_1to5", "would_post")}}
    D["evaluate"] = {"rounds": [
        {"r": "Round 2", "yes": 5, "film": 3.0, "health": 3.6},
        {"r": "Round 3", "yes": 3, "film": 4.0, "health": 2.8},
        {"r": "Round 4", "yes": 7, "film": 4.8, "health": 3.6},
        {"r": "Round 5", "yes": 9, "film": 4.8, "health": 4.4}]}

    data = json.dumps(D, ensure_ascii=False).replace("</", "<\\/")
    tpl = (ROOT / "tools/pipeline_explainer_template.html").read_text(encoding="utf-8")
    out = ROOT / "docs/pipeline.html"
    out.write_text(tpl.replace("__DATA__", data), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
