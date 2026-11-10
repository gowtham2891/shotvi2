"""Review page for the two vertical styles (founder, 2026-11-10): per clip, fit vs crop side by side, the crop's
check video (16:9 with the chosen slice boxed: green decided, red undecided) and the shot-by-shot reasons. The founder
picks a style per clip and notes problems; Download gives a CSV (-> data/human/vertical_*.csv).

Usage: python -m selection_v2.eval.naming.make_vertical_page <out.html> <vid>=<title> ...
       (reads runs/selection_v2/<vid>/flow_v2.json and review/flow_clips/<vid>/<clip>_{fit,crop,crop_debug}.mp4 + _crop_plan.json)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from selection_v2 import flow_v2 as FV
from selection_v2.stage1_facts import fmt_ts


def episode(vid: str, title: str) -> dict:
    f = FV.load(vid, FV.cfg_for(vid))
    cl = {c["id"]: c for c in f["clips"]["clips"]}
    order = (f.get("ranking") or {}).get("order") or list(cl)
    clips = []
    for cid in order:
        c = cl[cid]
        plan_p = Path(f"review/flow_clips/{vid}/{cid}_crop_plan.json")
        if not plan_p.exists():
            continue
        plan = json.loads(plan_p.read_text(encoding="utf-8"))
        shots = [{"t": f"{fmt_ts(s['start'])}-{fmt_ts(s['end'])}", "why": s["why"], "decided": s.get("decided", True),
                  "talk": ", ".join(s.get("talkers") or []), "seen": ", ".join(s.get("seen") or [])} for s in plan]
        clips.append({"id": cid, "title": c.get("title") or "", "duration": c["duration"], "start": fmt_ts(c["start_time"]),
                      "fit": f"flow_clips/{vid}/{cid}_fit.mp4", "crop": f"flow_clips/{vid}/{cid}_crop.mp4", "debug": f"flow_clips/{vid}/{cid}_crop_debug.mp4",
                      "fixed": f"flow_clips/{vid}/{cid}_cropfixed.mp4" if Path(f"review/flow_clips/{vid}/{cid}_cropfixed.mp4").exists() else "",
                      "n_moving": sum(1 for x in shots if "follows them" in x["why"]),
                      "shots": shots, "n_undecided": sum(1 for s in shots if not s["decided"])})
    return {"vid": vid, "title": title, "clips": clips}


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vertical Styles</title>
<style>
:root{--bg:#f6f6f3;--panel:#fff;--ink:#1c1c1a;--ink2:#55544f;--muted:#86857e;--line:#e2e2dc;--accent:#2f5d8a;--ok:#2e7d4f;--okbg:#e3f3e9;--bad:#b3412f;--badbg:#f8e3df}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161615;--panel:#1f1f1d;--ink:#ecebe7;--ink2:#bdbab2;--muted:#8d8a83;--line:#34332f;--accent:#8ab4e0;--ok:#7fcf9c;--okbg:#1d3326;--bad:#f2a49c;--badbg:#3a1f1c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans Telugu",sans-serif}
header{padding:14px 16px 0;max-width:1500px;margin:0 auto}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--ink2);font-size:13px;margin:0 0 10px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--line)}
.tab{border:1px solid var(--line);border-bottom:none;background:var(--bg);color:var(--ink2);padding:8px 12px;border-radius:8px 8px 0 0;cursor:pointer;font:inherit}.tab.on{background:var(--panel);color:var(--ink);font-weight:600}
main{max-width:1500px;margin:0 auto;padding:12px 16px 70px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:12px}
.card h2{font-size:17px;margin:0 0 6px}.meta{font-size:12px;color:var(--ink2)}
.row{display:grid;grid-template-columns:1fr 1fr 1fr 2fr;gap:12px;align-items:start}.row.three{grid-template-columns:1fr 1fr 2fr}
.col h3{font-size:13px;margin:0 0 4px;color:var(--ink2);font-weight:600}
video{width:100%;background:#000;border-radius:8px}.row .col:not(:last-child) video{max-height:70vh}
.shots{font-size:12.5px;max-height:70vh;overflow:auto;border:1px solid var(--line);border-radius:8px;padding:6px 8px;background:var(--bg)}
.shots div{padding:2px 0;border-bottom:1px solid var(--line)}.shots .u{color:var(--bad)}.shots b{color:var(--muted);margin-right:6px;font-weight:600}
.pick{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:8px}
.pick button{font:inherit;font-size:14px;font-weight:600;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:6px;padding:3px 14px;cursor:pointer}
.pick button.on{background:var(--okbg);border-color:var(--ok);color:var(--ok)}
textarea{width:100%;margin-top:6px;font:inherit;font-size:13px;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:4px 6px;resize:vertical}
.bar{position:fixed;bottom:0;left:0;right:0;background:var(--panel);border-top:1px solid var(--line);padding:8px 16px;display:flex;gap:10px;justify-content:space-between;align-items:center;flex-wrap:wrap}
.bar button{font:inherit;background:var(--accent);color:var(--bg);border:none;border-radius:8px;padding:7px 14px;cursor:pointer}
@media (max-width:1100px){.row,.row.three{grid-template-columns:1fr 1fr}.row .col:last-child{grid-column:1/-1}}
</style></head><body>
<header><h1>Vertical 9:16: which style would you post?</h1>
<p class="sub"><b>Fit</b> keeps the whole picture with a blurred fill. <b>Crop</b> follows the person who matters, chosen from who talks and who is on screen (approach B), and now <b>tracks them when they move</b> inside a shot; <b>Crop (fixed)</b> is the earlier version with one slice position per shot, for comparison. The third video shows the crop's choice on the 16:9 picture: <span style="color:var(--ok)">green</span> = decided, <span style="color:var(--bad)">red</span> = undecided (no face matched a known person). Pick a style per clip, note anything wrong (wrong person, jumpy, cut off), then <b>Download</b>.</p>
<nav class="tabs" id="tabs"></nav></header>
<main id="main"></main>
<div class="bar"><span id="count" class="meta"></span><button id="dl">Download (CSV)</button></div>
<script>
const DATA = __DATA__;
const SKEY = "vertical_v1_" + DATA.map(e=>e.vid).join("_");
let store = {}; try { store = JSON.parse(localStorage.getItem(SKEY) || "{}"); } catch (e) { store = {}; }
const save = () => { try { localStorage.setItem(SKEY, JSON.stringify(store)); } catch (e) {} count(); };
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let cur = 0;
function st(vid){ return store[vid] || (store[vid] = {}); }
function count(){ document.getElementById("count").textContent = DATA.map(e => `${e.title}: ${Object.values(st(e.vid)).filter(x=>x.s).length}/${e.clips.length} picked`).join(" · "); }
function tabs(){ document.getElementById("tabs").innerHTML = DATA.map((e,i)=>`<button class="tab ${i===cur?"on":""}" data-i="${i}">${esc(e.title)}</button>`).join("");
  document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>{cur=+b.dataset.i;tabs();render();}); }
function render(){
  const e = DATA[cur], s = st(e.vid); let h = "";
  e.clips.forEach(c => { const p = (s[c.id]||{}).s || "";
    h += `<div class="card" id="card-${c.id}"><h2>${esc(c.title)}</h2>
      <div class="meta">${c.id} · ${Math.round(c.duration)} s · from ${c.start} · ${c.shots.length} shots, ${c.n_undecided} undecided, the slice moves in ${c.n_moving}</div>
      <div class="row ${c.fixed?"":"three"}">
        <div class="col"><h3>Fit + blurred fill</h3><video controls preload="metadata" src="${c.fit}"></video></div>
        <div class="col"><h3>Crop, tracking the person</h3><video controls preload="metadata" src="${c.crop}"></video></div>
        ${c.fixed?`<div class="col"><h3>Crop (fixed per shot)</h3><video controls preload="metadata" src="${c.fixed}"></video></div>`:""}
        <div class="col"><h3>What the crop chose (check)</h3><video controls preload="metadata" src="${c.debug}"></video>
          <div class="shots">${c.shots.map(x=>`<div class="${x.decided?"":"u"}"><b>${esc(x.t)}</b>${esc(x.why)}${x.talk?` <span class="meta">(talking: ${esc(x.talk)}${x.seen?`; on screen: ${esc(x.seen)}`:""})</span>`:""}</div>`).join("")}</div></div>
      </div>
      <div class="pick"><span class="meta">Which style?</span>${["fit","crop","crop fixed","neither"].map(x=>`<button class="${p===x?"on":""}" data-s="${x}" data-c="${c.id}">${x[0].toUpperCase()+x.slice(1)}</button>`).join("")}</div>
      <textarea rows="2" data-n="${c.id}" placeholder="Note (optional): wrong person, jumpy, cut off, ...">${esc((s[c.id]||{}).n||"")}</textarea></div>`; });
  document.getElementById("main").innerHTML = h;
  document.querySelectorAll("video").forEach(v=>v.onplay=()=>document.querySelectorAll("video").forEach(o=>{ if(o!==v) o.pause(); }));
  document.querySelectorAll("[data-s]").forEach(b=>b.onclick=()=>{ const x=(s[b.dataset.c]=s[b.dataset.c]||{}); x.s=b.dataset.s; save();
    document.querySelectorAll(`[data-c="${b.dataset.c}"]`).forEach(y=>y.classList.toggle("on", y.dataset.s===x.s)); });
  document.querySelectorAll("[data-n]").forEach(t=>t.oninput=()=>{ (s[t.dataset.n]=s[t.dataset.n]||{}).n=t.value; save(); });
}
document.getElementById("dl").onclick=()=>{ const rows=[["vid","clip","title","style","note","undecided_shots"]];
  DATA.forEach(e=>e.clips.forEach(c=>{ const x=st(e.vid)[c.id]||{}; rows.push([e.vid,c.id,c.title,x.s||"",x.n||"",c.n_undecided]); }));
  const csv=rows.map(r=>r.map(x=>`"${String(x).replace(/"/g,'""')}"`).join(",")).join("\n");
  const a=document.createElement("a"); a.href=URL.createObjectURL(new Blob(["﻿"+csv],{type:"text/csv"})); a.download="vertical_styles.csv"; a.click(); };
tabs(); render(); count();
</script></body></html>"""


if __name__ == "__main__":
    out = Path(sys.argv[1])
    eps = [episode(*a.split("=", 1)) for a in sys.argv[2:]]
    out.write_text(PAGE.replace("__DATA__", json.dumps(eps, ensure_ascii=False)), encoding="utf-8")
    print("wrote", out, [len(e["clips"]) for e in eps], "clips")
