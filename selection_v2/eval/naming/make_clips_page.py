"""Clip review page (founder, 2026-11-04): every Stage 3 clip as its own cut video file (render_clips.py: cold open
first, drops left out); the founder rates would-post yes / maybe / no with a note. Ratings download as CSV
(-> data/human/ratings_stage3_*.csv). The page does not show whether a clip matches the approved list.

Usage: python -m selection_v2.eval.naming.make_clips_page <out.html> <vid>=<title> ...   (reads runs/name_test/<vid>/stage3.<STAGE3_TAG, default v4>.json)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from selection_v2.eval.naming.name_test import OUT, S3_TAG, load
from selection_v2.stage1_facts import fmt_ts


CLIP_DIR = "clips" if S3_TAG == "v4" else f"clips_{S3_TAG}"   # render_clips.DEST, relative to review/


def episode(vid: str, title: str) -> dict:
    s3 = load(OUT / vid / f"stage3.{S3_TAG}.json")
    f = load(OUT / vid / "episode_facts.v4_scenes_find.json")
    clips = s3["clips"]["clips"]
    rk_path = OUT / vid / f"stage4.{S3_TAG}.json"
    ep_extra = {}
    if rk_path.exists():      # Stage 4's order, hidden behind the "show the AI's" tick box (the founder marks theirs first)
        rk = load(rk_path)
        at = {x: i for i, x in enumerate(rk["order"])}
        clips = [{**x, "rank": at.get(x["id"], len(at)) + 1, "post_first_ai": x["id"] in rk["post_first"], "rank_why": rk["why"].get(x["id"], "")} for x in clips]
        ep_extra = {"verdict": rk["verdict"], "verdict_why": rk["verdict_why"]}
    return {**episode_from(f, clips, vid, title, f"{CLIP_DIR}/{vid}"), **ep_extra}


def episode_from(f: dict, clip_list: list[dict], vid: str, title: str, clip_dir: str) -> dict:
    """The page's data for one episode: ``f`` = the facts (lines, people), clip files at review/<clip_dir>/<id>.mp4."""
    L = {r["id"]: r for r in f["lines"]}
    nm = {s["key"]: s.get("name") or s["key"] for s in f["episode_pass"]["speakers"]}
    who = f["episode_pass"]["derived"].get("line_speakers") or {}
    clips = []
    for c in clip_list:
        text = []
        for p in c["pieces"]:
            for lid in p["lines"]:
                r = L[lid]
                if r["kind"] == "speech":
                    text.append({"who": nm.get(who.get(lid), who.get(lid) or ""), "text": r["text"], "cold": bool(p.get("cold_open"))})
        clips.append({"id": c["id"], "file": f"{clip_dir}/{c['id']}.mp4", "title": c.get("title") or "", "kind": c.get("kind"), "spoiler": c.get("spoiler"),
                      "works": c.get("works"), "why_not": c.get("why_not") or "", "hook": c.get("hook") or "", "why": c.get("why") or "",
                      "duration": c["duration"], "start": fmt_ts(c["start_time"]), "pieces": [[p["start"], p["end"]] for p in c["pieces"]],
                      "cold_open": bool(c.get("cold_open")), "n_drops": len(c.get("drops") or []), "text": text,
                      "start_why": (c.get("start") or {}).get("why") or "", "end_why": (c.get("end") or {}).get("why") or "",
                      "hold": c.get("hold_s") or 0, "cuts": [f"{d['first']}..{d['last']}: {d.get('why') or ''}" for d in c.get("drops") or []],
                      "cold_why": (c.get("cold_open") or {}).get("why") or "", "emotion": c.get("emotion") or "", "strength": c.get("strength") or "", "strength_why": c.get("strength_why") or "",
                      "rank": c.get("rank"), "post_first_ai": bool(c.get("post_first_ai")), "rank_why": c.get("rank_why") or "",
                      "was": (c.get("found") or {}).get("duration"), "split_why": c.get("split_why") or ""})
    return {"vid": vid, "title": title, "video": f"media/{vid}.mp4", "clips": clips}


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stage 3 Clips</title>
<style>
:root{--bg:#f6f6f3;--panel:#fff;--ink:#1c1c1a;--ink2:#55544f;--muted:#86857e;--line:#e2e2dc;--accent:#2f5d8a;--ok:#2e7d4f;--okbg:#e3f3e9;--mid:#9a6b00;--midbg:#fbf1d9;--bad:#b3412f;--badbg:#f8e3df}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161615;--panel:#1f1f1d;--ink:#ecebe7;--ink2:#bdbab2;--muted:#8d8a83;--line:#34332f;--accent:#8ab4e0;--ok:#7fcf9c;--okbg:#1d3326;--mid:#f0c46a;--midbg:#3a3018;--bad:#f2a49c;--badbg:#3a1f1c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans Telugu",sans-serif}
header{padding:14px 16px 0;max-width:1400px;margin:0 auto}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--ink2);font-size:13px;margin:0 0 10px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--line)}
.tab{border:1px solid var(--line);border-bottom:none;background:var(--bg);color:var(--ink2);padding:8px 12px;border-radius:8px 8px 0 0;cursor:pointer;font:inherit}
.tab.on{background:var(--panel);color:var(--ink);font-weight:600}
main{max-width:1400px;margin:0 auto;padding:12px 16px 70px}
.grid{display:grid;grid-template-columns:minmax(0,4fr) minmax(0,6fr);gap:14px;align-items:start}
.grid video{width:100%;border-radius:8px;background:#000;max-height:60vh}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:10px}
.card.yes{border-left:5px solid var(--ok)}.card.maybe{border-left:5px solid var(--mid)}.card.no{border-left:5px solid var(--bad)}
.card h2{font-size:17px;margin:0 0 4px}
.meta{font-size:12px;color:var(--ink2)}.lbl{font-size:12px;font-weight:600;color:var(--muted);margin-top:6px}
.badge{display:inline-block;font-size:11px;padding:1px 7px;border-radius:10px;border:1px solid var(--line);margin-right:4px;color:var(--ink2)}
.badge.warn{border-color:var(--bad);color:var(--bad)}
.play{font:inherit;font-size:14px;font-weight:600;border:1px solid var(--accent);color:var(--accent);background:var(--panel);border-radius:6px;padding:3px 12px;cursor:pointer;margin:6px 0}
details{margin-top:6px}summary{cursor:pointer;font-size:13px;color:var(--ink2)}
.ln{font-size:14px;margin:2px 0}.ln b{font-size:12px;color:var(--ink2);margin-right:6px}.ln.cold{background:var(--midbg);border-radius:4px;padding:0 4px}
.rate{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:8px}
.rate button{font:inherit;font-size:14px;font-weight:600;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:6px;padding:3px 14px;cursor:pointer}
.rate button.on.yes{background:var(--okbg);border-color:var(--ok);color:var(--ok)}.rate button.on.maybe{background:var(--midbg);border-color:var(--mid);color:var(--mid)}.rate button.on.no{background:var(--badbg);border-color:var(--bad);color:var(--bad)}
textarea{width:100%;margin-top:6px;font:inherit;font-size:13px;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:4px 6px;resize:vertical}
.bar{position:fixed;bottom:0;left:0;right:0;background:var(--panel);border-top:1px solid var(--line);padding:8px 16px;display:flex;gap:10px;justify-content:space-between;align-items:center;flex-wrap:wrap}
.bar button{font:inherit;background:var(--accent);color:var(--bg);border:none;border-radius:8px;padding:7px 14px;cursor:pointer}
.rate button.pf{margin-left:10px}.rate button.pf.on{background:var(--accent);border-color:var(--accent);color:var(--bg)}
.card.first{box-shadow:0 0 0 2px var(--accent)}.ai{display:none}.showai .ai{display:block}
@media (max-width:900px){.grid{grid-template-columns:1fr}}
</style></head><body>
<header><h1>Stage 3 clips: would you post this?</h1>
<p class="sub">Each clip is cut as it would be posted (the cold-open line first when there is one, dropped parts left out). Play it, then press <b>Yes</b>, <b>Maybe</b> or <b>No</b> for "would I post this?", and write a note if something is off (start, end, a cut, the cold open). Also press <b>Post first</b> on the <b>3 clips you would post first</b> from this episode. Your answers stay in this browser; press <b>Download</b> when done and send me the file.</p>
<nav class="tabs" id="tabs"></nav></header>
<main id="main"></main>
<div class="bar"><span id="count" class="meta"></span><label class="meta"><input type="checkbox" id="showai"> show the AI's strength (after you have marked yours)</label><button id="dl">Download ratings (CSV)</button></div>
<script>
const DATA = __DATA__;
const SKEY = "stage3_clips___TAG___" + DATA.map(e=>e.vid).join("_");
let store = {}; try { store = JSON.parse(localStorage.getItem(SKEY) || "{}"); } catch (e) { store = {}; }
const save = () => { try { localStorage.setItem(SKEY, JSON.stringify(store)); } catch (e) {} count(); };
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let cur = 0;
function st(vid){ return store[vid] || (store[vid] = {}); }
function count(){ document.getElementById("count").textContent = DATA.map(e => `${e.title}: ${Object.values(st(e.vid)).filter(x=>x.r).length}/${e.clips.length} rated, ${Object.values(st(e.vid)).filter(x=>x.p).length} post first`).join(" · "); }
function tabs(){ document.getElementById("tabs").innerHTML = DATA.map((e,i)=>`<button class="tab ${i===cur?"on":""}" data-i="${i}">${esc(e.title)}</button>`).join("");
  document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>{cur=+b.dataset.i;tabs();render();}); }
function render(){
  const e = DATA[cur], s = st(e.vid);
  let h = e.verdict ? `<div class="card ai"><b>Episode verdict: ${esc(e.verdict)}</b> · ${esc(e.verdict_why||"")}</div>` : "";
  e.clips.forEach(c => {
    const r = (s[c.id]||{}).r || "";
    const pf = !!(s[c.id]||{}).p;
    h += `<div class="card ${r} ${pf?"first":""}" id="card-${c.id}"><div class="grid"><video controls preload="metadata" src="${c.file}"></video><div>
      <h2>${esc(c.title)}</h2>
      <div><span class="badge">${c.id}</span><span class="badge">${c.kind}</span><span class="badge">${Math.round(c.duration)} s</span>${c.was?`<span class="badge">found ${Math.round(c.was)} s</span>`:""}<span class="badge">from ${c.start}</span>
      ${c.cold_open?'<span class="badge">cold open</span>':""}${c.n_drops?`<span class="badge">${c.n_drops} cut${c.n_drops>1?"s":""}</span>`:""}
      ${c.spoiler?'<span class="badge warn">spoiler</span>':""}${c.works===false?'<span class="badge warn">AI: may not land</span>':""}</div>
      <div class="lbl">Hook (first 3 seconds)</div><div>${esc(c.hook)}</div>
      <div class="lbl">Why it works</div><div>${esc(c.why)}</div>
      ${c.split_why?`<div class="lbl">Split in two because</div><div>${esc(c.split_why)}</div>`:""}
      ${c.emotion?`<div class="lbl">What the viewer feels</div><div>${esc(c.emotion)}</div>`:""}
      ${c.start_why?`<div class="lbl">Starts here because</div><div>${esc(c.start_why)}</div>`:""}
      ${c.end_why?`<div class="lbl">Ends on${c.hold>0.5?` (holds ${c.hold.toFixed(1)} s after the last word)`:""}</div><div>${esc(c.end_why)}</div>`:""}
      ${c.cold_why?`<div class="lbl">Cold open because</div><div>${esc(c.cold_why)}</div>`:""}
      ${c.cuts.length?`<div class="lbl">Cuts</div>${c.cuts.map(x=>`<div>${esc(x)}</div>`).join("")}`:""}${c.works===false?`<div class="lbl">Why it may not land</div><div>${esc(c.why_not)}</div>`:""}
      ${c.rank?`<div class="lbl ai">AI posting order: #${c.rank}${c.post_first_ai?" (post first)":""}</div><div class="ai">${esc(c.rank_why)}</div>`:""}
      ${c.strength?`<div class="lbl ai">AI's strength</div><div class="ai">${esc(c.strength.replace("_"," "))}: ${esc(c.strength_why)}</div>`:""}
      <details><summary>What is said, in play order</summary>${c.text.map(t=>`<div class="ln ${t.cold?"cold":""}"><b>${esc(t.who)}</b>${esc(t.text)}</div>`).join("")}</details>
      <div class="rate"><span class="meta">Would you post it?</span>${["yes","maybe","no"].map(x=>`<button class="${x} ${r===x?"on":""}" data-r="${x}" data-c="${c.id}">${x[0].toUpperCase()+x.slice(1)}</button>`).join("")}<button class="pf ${pf?"on":""}" data-p="${c.id}">Post first</button></div>
      <textarea rows="2" data-n="${c.id}" placeholder="Note (optional): what to change">${esc((s[c.id]||{}).n||"")}</textarea></div></div></div>`;
  });
  document.getElementById("main").innerHTML = h;
  document.querySelectorAll("video").forEach(v=>v.onplay=()=>document.querySelectorAll("video").forEach(o=>{ if(o!==v) o.pause(); }));
  document.querySelectorAll("[data-r]").forEach(b=>b.onclick=()=>{ const x=(s[b.dataset.c]=s[b.dataset.c]||{}); x.r=b.dataset.r; save();
    const card=document.getElementById("card-"+b.dataset.c); card.className="card "+x.r;
    card.querySelectorAll("[data-r]").forEach(y=>y.classList.toggle("on", y.dataset.r===x.r)); });
  document.querySelectorAll("[data-n]").forEach(t=>t.oninput=()=>{ (s[t.dataset.n]=s[t.dataset.n]||{}).n=t.value; save(); });
  document.querySelectorAll("[data-p]").forEach(b=>b.onclick=()=>{ const x=(s[b.dataset.p]=s[b.dataset.p]||{}); x.p=!x.p; save();
    b.classList.toggle("on", x.p); document.getElementById("card-"+b.dataset.p).classList.toggle("first", x.p); });
}
document.getElementById("showai").onchange=e=>document.body.classList.toggle("showai", e.target.checked);
if (DATA.some(e=>e.show_ai)) { document.getElementById("showai").checked = true; document.body.classList.add("showai"); }
document.getElementById("dl").onclick=()=>{ const rows=[["vid","clip","title","would_post","post_first","note","ai_strength","ai_rank"]];
  DATA.forEach(e=>e.clips.forEach(c=>{ const x=st(e.vid)[c.id]||{}; rows.push([e.vid,c.id,c.title,x.r||"",x.p?"yes":"",x.n||"",c.strength||"",c.rank||""]); }));
  const csv=rows.map(r=>r.map(x=>`"${String(x).replace(/"/g,'""')}"`).join(",")).join("\n");
  const a=document.createElement("a"); a.href=URL.createObjectURL(new Blob(["﻿"+csv],{type:"text/csv"})); a.download="ratings_stage3___TAG__.csv"; a.click(); };
tabs(); render(); count();
</script></body></html>"""


if __name__ == "__main__":
    out = Path(sys.argv[1])
    eps = [episode(*a.split("=", 1)) for a in sys.argv[2:]]
    out.write_text(PAGE.replace("__DATA__", json.dumps(eps, ensure_ascii=False)).replace("__TAG__", "v1" if S3_TAG == "v4" else S3_TAG), encoding="utf-8")
    print("wrote", out, [len(e["clips"]) for e in eps], "clips")
