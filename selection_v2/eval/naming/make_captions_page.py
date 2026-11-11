"""Review page for caption styles (founder, 2026-11-10: several styles and fonts, the user picks). Per clip: the
caption styles side by side on one vertical style (switchable fit / crop); the founder marks the styles worth
offering and notes problems (size, timing, placement). Download -> CSV.

Usage: python -m selection_v2.eval.naming.make_captions_page <out.html> <vid>=<title> ...
       (reads runs/selection_v2/<vid>/flow_v2.json and review/flow_clips/<vid>/<clip>_<fit|crop>_cap_<style>.mp4)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from selection_v2 import captions as C
from selection_v2 import flow_v2 as FV
from selection_v2.stage1_facts import fmt_ts


def episode(vid: str, title: str) -> dict:
    f = FV.load(vid, FV.cfg_for(vid))
    cl = {c["id"]: c for c in f["clips"]["clips"]}
    order = (f.get("ranking") or {}).get("order") or list(cl)
    clips = []
    for cid in order:
        c = cl[cid]
        styles = {}
        for vs in ("crop", "fit"):
            have = {st: f"flow_clips/{vid}/{cid}_{vs}_cap_{st}.mp4" for st in C.TEMPLATES if Path(f"review/flow_clips/{vid}/{cid}_{vs}_cap_{st}.mp4").exists()}
            if have:
                styles[vs] = have
        if not styles:
            continue
        clips.append({"id": cid, "title": c.get("title") or "", "duration": c["duration"], "start": fmt_ts(c["start_time"]), "styles": styles})
    return {"vid": vid, "title": title, "clips": clips, "labels": {k: v["label"] for k, v in C.TEMPLATES.items()}}


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Caption Styles</title>
<style>
:root{--bg:#f6f6f3;--panel:#fff;--ink:#1c1c1a;--ink2:#55544f;--muted:#86857e;--line:#e2e2dc;--accent:#2f5d8a;--ok:#2e7d4f;--okbg:#e3f3e9}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161615;--panel:#1f1f1d;--ink:#ecebe7;--ink2:#bdbab2;--muted:#8d8a83;--line:#34332f;--accent:#8ab4e0;--ok:#7fcf9c;--okbg:#1d3326}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans Telugu",sans-serif}
header{padding:14px 16px 0;max-width:1600px;margin:0 auto}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--ink2);font-size:13px;margin:0 0 10px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--line)}
.tab{border:1px solid var(--line);border-bottom:none;background:var(--bg);color:var(--ink2);padding:8px 12px;border-radius:8px 8px 0 0;cursor:pointer;font:inherit}.tab.on{background:var(--panel);color:var(--ink);font-weight:600}
main{max-width:1600px;margin:0 auto;padding:12px 16px 70px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:12px}
.card h2{font-size:17px;margin:0 0 6px}.meta{font-size:12px;color:var(--ink2)}
.vs{display:flex;gap:6px;margin:6px 0}.vs button{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:6px;padding:2px 10px;cursor:pointer}.vs button.on{background:var(--okbg);border-color:var(--ok);color:var(--ok)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}
.col h3{font-size:12.5px;margin:0 0 4px;color:var(--ink2);font-weight:600;min-height:2.6em}
video{width:100%;background:#000;border-radius:8px;max-height:60vh}
.pick{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:6px}
.pick button{font:inherit;font-size:13px;font-weight:600;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:6px;padding:2px 10px;cursor:pointer}.pick button.on{background:var(--okbg);border-color:var(--ok);color:var(--ok)}
textarea{width:100%;margin-top:6px;font:inherit;font-size:13px;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:4px 6px;resize:vertical}
.bar{position:fixed;bottom:0;left:0;right:0;background:var(--panel);border-top:1px solid var(--line);padding:8px 16px;display:flex;gap:10px;justify-content:space-between;align-items:center;flex-wrap:wrap}
.bar button{font:inherit;background:var(--accent);color:var(--bg);border:none;border-radius:8px;padding:7px 14px;cursor:pointer}
</style></head><body>
<header><h1>Caption templates: which ones should we offer?</h1>
<p class="sub">Each clip shows the same captions in every template (a saved bundle of settings: font, size, colours, outline or box, word highlight, animation, words per line, position; any of these can be changed per user later). Switch between the crop and fit pictures. Under each style press <b>Offer</b> if it is good enough to give users (several can be on), and note anything wrong: size, speed, placement, a wrong line break. Then <b>Download</b>.</p>
<nav class="tabs" id="tabs"></nav></header>
<main id="main"></main>
<div class="bar"><span id="count" class="meta"></span><button id="dl">Download (CSV)</button></div>
<script>
const DATA = __DATA__;
const SKEY = "captions_v1_" + DATA.map(e=>e.vid).join("_");
let store = {}; try { store = JSON.parse(localStorage.getItem(SKEY) || "{}"); } catch (e) { store = {}; }
const save = () => { try { localStorage.setItem(SKEY, JSON.stringify(store)); } catch (e) {} count(); };
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let cur = 0, vsel = {};
function st(vid){ return store[vid] || (store[vid] = {}); }
function count(){ const n = Object.values(store).flatMap(e=>Object.values(e)).filter(x=>x.offer).length; document.getElementById("count").textContent = `${n} style picks marked "offer"`; }
function tabs(){ document.getElementById("tabs").innerHTML = DATA.map((e,i)=>`<button class="tab ${i===cur?"on":""}" data-i="${i}">${esc(e.title)}</button>`).join("");
  document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>{cur=+b.dataset.i;tabs();render();}); }
function render(){
  const e = DATA[cur], s = st(e.vid); let h = "";
  e.clips.forEach(c => {
    const vs = vsel[c.id] || Object.keys(c.styles)[0]; const styles = c.styles[vs];
    h += `<div class="card" id="card-${c.id}"><h2>${esc(c.title)}</h2><div class="meta">${c.id} · ${Math.round(c.duration)} s · from ${c.start}</div>
      <div class="vs">${Object.keys(c.styles).map(k=>`<button class="${k===vs?"on":""}" data-vs="${k}" data-c="${c.id}">${k==="crop"?"Crop picture":"Fit picture"}</button>`).join("")}</div>
      <div class="grid">${Object.entries(styles).map(([k,src])=>{ const key=c.id+"|"+k, x=s[key]||{};
        return `<div class="col"><h3>${esc(e.labels[k]||k)}</h3><video controls preload="metadata" src="${src}"></video>
          <div class="pick"><button class="${x.offer?"on":""}" data-o="${key}">Offer</button></div>
          <textarea rows="2" data-n="${key}" placeholder="Note">${esc(x.n||"")}</textarea></div>`; }).join("")}</div></div>`; });
  document.getElementById("main").innerHTML = h;
  document.querySelectorAll("video").forEach(v=>v.onplay=()=>document.querySelectorAll("video").forEach(o=>{ if(o!==v) o.pause(); }));
  document.querySelectorAll("[data-vs]").forEach(b=>b.onclick=()=>{ vsel[b.dataset.c]=b.dataset.vs; render(); });
  document.querySelectorAll("[data-o]").forEach(b=>b.onclick=()=>{ const x=(s[b.dataset.o]=s[b.dataset.o]||{}); x.offer=!x.offer; save(); b.classList.toggle("on", x.offer); });
  document.querySelectorAll("[data-n]").forEach(t=>t.oninput=()=>{ (s[t.dataset.n]=s[t.dataset.n]||{}).n=t.value; save(); });
}
document.getElementById("dl").onclick=()=>{ const rows=[["vid","clip","style","offer","note"]];
  DATA.forEach(e=>{ const s=st(e.vid); e.clips.forEach(c=>Object.keys(e.labels).forEach(k=>{ const x=s[c.id+"|"+k]||{}; rows.push([e.vid,c.id,k,x.offer?"yes":"",x.n||""]); })); });
  const csv=rows.map(r=>r.map(x=>`"${String(x).replace(/"/g,'""')}"`).join(",")).join("\n");
  const a=document.createElement("a"); a.href=URL.createObjectURL(new Blob(["﻿"+csv],{type:"text/csv"})); a.download="caption_styles.csv"; a.click(); };
tabs(); render(); count();
</script></body></html>"""


if __name__ == "__main__":
    out = Path(sys.argv[1])
    eps = [episode(*a.split("=", 1)) for a in sys.argv[2:]]
    out.write_text(PAGE.replace("__DATA__", json.dumps(eps, ensure_ascii=False)), encoding="utf-8")
    print("wrote", out, [len(e["clips"]) for e in eps], "clips")
