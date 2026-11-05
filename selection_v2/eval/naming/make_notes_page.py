"""Scene descriptions page (founder, 2026-11-03): Stage 2's notes per scene next to the video, to read and judge.
Notes per scene download as CSV (-> data/human/scene_notes_review_*.csv).

Usage: python -m selection_v2.eval.naming.make_notes_page <out.html> <vid>=<title> ...   (reads runs/name_test/<vid>/scene_notes.v4.json)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from selection_v2.eval.naming.name_test import OUT, load
from selection_v2.stage1_facts import fmt_ts


def episode(vid: str, title: str) -> dict:
    sn = load(OUT / vid / "scene_notes.v4.json")
    f = load(OUT / vid / "episode_facts.v4_scenes_find.json")
    tm = {r["id"]: fmt_ts(r["start"]) for r in f["lines"]}
    for s in sn["scenes"]:
        s["ts"], s["te"] = fmt_ts(s["start"]), fmt_ts(s["end"])
        for x in s["situations"]:
            x["ts"] = f"{tm[x['first']]}–{tm[x['last']]}"
        for x in s["reactions"]:
            x["ts"] = tm[x["after"]]
    return {"vid": vid, "title": title, "video": f"media/{vid}.mp4", "episode": sn["episode"], "scenes": sn["scenes"]}


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scene Descriptions</title>
<style>
:root{--bg:#f6f6f3;--panel:#fff;--ink:#1c1c1a;--ink2:#55544f;--muted:#86857e;--line:#e2e2dc;--accent:#2f5d8a}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161615;--panel:#1f1f1d;--ink:#ecebe7;--ink2:#bdbab2;--muted:#8d8a83;--line:#34332f;--accent:#8ab4e0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans Telugu",sans-serif}
header{padding:14px 16px 0;max-width:1400px;margin:0 auto}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--ink2);font-size:13px;margin:0 0 10px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--line)}
.tab{border:1px solid var(--line);border-bottom:none;background:var(--bg);color:var(--ink2);padding:8px 12px;border-radius:8px 8px 0 0;cursor:pointer;font:inherit}
.tab.on{background:var(--panel);color:var(--ink);font-weight:600}
main{max-width:1400px;margin:0 auto;padding:12px 16px 70px}
.split{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:12px;align-items:start}
.side{position:sticky;top:8px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px}
.side video{width:100%;border-radius:8px;background:#000}
.ep{font-size:14px;color:var(--ink2);margin-top:8px;max-height:40vh;overflow:auto}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:10px}
.card h2{font-size:16px;margin:0 0 4px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.lbl{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.03em;margin-top:8px}
ul{margin:2px 0 0;padding-left:18px}li{margin:2px 0}.t{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.play{font:inherit;font-size:12px;border:1px solid var(--accent);color:var(--accent);background:var(--panel);border-radius:6px;padding:1px 8px;cursor:pointer}
textarea{width:100%;margin-top:8px;font:inherit;font-size:13px;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:4px 6px;resize:vertical}
.bar{position:fixed;bottom:0;left:0;right:0;background:var(--panel);border-top:1px solid var(--line);padding:8px 16px;display:flex;justify-content:flex-end}
.bar button{font:inherit;background:var(--accent);color:var(--bg);border:none;border-radius:8px;padding:7px 14px;cursor:pointer}
@media (max-width:900px){.split{grid-template-columns:1fr}.side{position:static}}
</style></head><body>
<header><h1>Stage 2: what happens in each scene</h1>
<p class="sub">The AI watched the whole episode and described every scene (facts only; it does not pick clips). Press <b>▶</b> to play a scene. If something is wrong or missing, write it in the scene's note box. Notes stay in this browser; press <b>Download notes</b> when done and send me the file.</p>
<nav class="tabs" id="tabs"></nav></header>
<main id="main"></main>
<div class="bar"><button id="dl">Download notes (CSV)</button></div>
<script>
const DATA = __DATA__;
const SKEY = "scene_notes_v1_" + DATA.map(e=>e.vid).join("_");
let store = {}; try { store = JSON.parse(localStorage.getItem(SKEY) || "{}"); } catch (e) { store = {}; }
const save = () => { try { localStorage.setItem(SKEY, JSON.stringify(store)); } catch (e) {} };
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let cur = 0, stopAt = null;
function tabs(){ document.getElementById("tabs").innerHTML = DATA.map((e,i)=>`<button class="tab ${i===cur?"on":""}" data-i="${i}">${esc(e.title)}</button>`).join("");
  document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>{cur=+b.dataset.i;tabs();render();}); }
const list = (xs, f) => xs.length ? `<ul>${xs.map(x=>`<li>${f(x)}</li>`).join("")}</ul>` : `<div class="t">none</div>`;
function render(){
  const e = DATA[cur], st = store[e.vid] = store[e.vid] || {};
  let h = `<div class="split"><div class="side"><video id="v" controls preload="auto" src="${e.video}"></video><div class="lbl">The whole episode</div><div class="ep">${esc(e.episode)}</div></div><div>`;
  e.scenes.forEach(s => {
    h += `<div class="card"><h2>Scene ${s.scene_id} · ${esc(s.where)} <span class="t">${s.ts}–${s.te}</span><button class="play" data-t="${s.start}" data-e="${s.end}">▶ Play scene</button></h2>
      <div>${esc(s.story)}</div>
      <div class="lbl">Mood</div><div>${esc(s.mood)}</div>
      <div class="lbl">What happens, step by step</div>${list(s.situations, x=>`<span class="t">${x.ts}</span> ${esc(x.what_happens)}`)}
      <div class="lbl">Reactions</div>${list(s.reactions, x=>`<span class="t">${x.ts}</span> <b>${esc(x.who)}</b>: ${esc(x.how)}`)}
      <div class="lbl">Seen without words</div>${list(s.visual_moments, x=>`<span class="t">${esc(x.range)}</span> ${esc(x.what_is_seen)}`)}
      <div class="lbl">Silences</div>${list(s.silences, x=>`<span class="t">${esc(x.range)}</span> ${esc(x.what_happens)}${x.music?` (music: ${esc(x.music)})`:""}`)}
      <div class="lbl">A new viewer needs to know</div><div>${esc(s.context_needed) || '<span class="t">nothing</span>'}</div>
      <div class="lbl">Links to other scenes</div>${list(s.connections, x=>`Scene ${x.scene}: ${esc(x.how)}`)}
      <div class="lbl">Who they are to each other</div><div>${esc(s.relationships)}</div>
      <textarea rows="2" data-s="${s.scene_id}" placeholder="Note (optional): what is wrong or missing in this description">${esc(st[s.scene_id]||"")}</textarea></div>`;
  });
  document.getElementById("main").innerHTML = h + `</div></div>`;
  const v = document.getElementById("v");
  v.ontimeupdate = () => { if (stopAt !== null && v.currentTime >= stopAt) { v.pause(); stopAt = null; } };
  document.querySelectorAll(".play").forEach(b=>b.onclick=()=>{ const go=()=>{ v.currentTime=+b.dataset.t; stopAt=+b.dataset.e+0.5; v.play(); };
    if (v.readyState >= 1) go(); else { v.addEventListener("loadedmetadata", go, {once:true}); v.load(); } });
  document.querySelectorAll("textarea").forEach(t=>t.oninput=()=>{ st[t.dataset.s]=t.value; save(); });
}
document.getElementById("dl").onclick=()=>{ const rows=[["vid","scene","note"]];
  DATA.forEach(e=>Object.entries(store[e.vid]||{}).forEach(([k,v])=>{ if(v) rows.push([e.vid,k,v]); }));
  const csv=rows.map(r=>r.map(x=>`"${String(x).replace(/"/g,'""')}"`).join(",")).join("\n");
  const a=document.createElement("a"); a.href=URL.createObjectURL(new Blob(["﻿"+csv],{type:"text/csv"})); a.download="scene_notes_review.csv"; a.click(); };
tabs(); render();
</script></body></html>"""


if __name__ == "__main__":
    out = Path(sys.argv[1])
    eps = [episode(*a.split("=", 1)) for a in sys.argv[2:]]
    out.write_text(PAGE.replace("__DATA__", json.dumps(eps, ensure_ascii=False)), encoding="utf-8")
    print("wrote", out)
