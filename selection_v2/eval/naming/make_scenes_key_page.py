"""Scene answer-key page (founder, 2026-11-03): every place where the text step (1B), the video step checking 1B's scenes
(A) or the video step finding scenes alone (B) starts a new scene, grouped when they are a few lines apart. The page
does not say who proposed which change. Per place the founder picks the line where the new scene starts, or "no scene
change here", and can add changes all three missed. Choices download as CSV (-> data/human/scenes_key_*.csv).

Usage: python -m selection_v2.eval.naming.make_scenes_key_page <out.html> <vid>=<title> ...
Reads runs/name_test/<vid>/: the 1B facts the runs used, episode_facts.v3_scenes_check.json, episode_facts.v3_scenes_find.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from selection_v2.eval.naming import score as SC
from selection_v2.eval.naming.name_test import FACTS_1B, OUT, load
from selection_v2.stage1_facts import fmt_ts

WINDOW = 3          # lines shown before / after a proposed change
GROUP = 3           # proposals this many lines apart or closer are one place to check


def proposals(vid: str) -> tuple[dict, dict[str, list[str]]]:
    """facts (1B) and system -> the first line of every scene after the first."""
    f = load(OUT / vid / FACTS_1B.get(vid, "facts_1b.json"))
    out = {"1B": [s["first_line"] for s in f["episode_pass"]["derived"]["scenes"]][1:]}
    for name, tag in (("A", "scenes_check"), ("B", "scenes_find")):
        p = OUT / vid / f"episode_facts.v3_{tag}.json"
        if p.exists():
            out[name] = [s["first_line"] for s in load(p)["identity_pass"]["scenes"]][1:]
    return f, out


def places(f: dict, props: dict[str, list[str]]) -> list[dict]:
    pos = {r["id"]: i for i, r in enumerate(f["lines"])}
    hits = sorted({pos[l] for ls in props.values() for l in ls})
    groups = []
    for p in hits:
        if groups and p - groups[-1][-1] <= GROUP:
            groups[-1].append(p)
        else:
            groups.append([p])
    return [{"lo": max(0, g[0] - WINDOW), "hi": min(len(f["lines"]) - 1, g[-1] + WINDOW), "props": g} for g in groups]


def episode(vid: str, title: str) -> dict:
    f, props = proposals(vid)
    items, _, _ = SC.key_items(f, vid)
    who = {it["lid"]: it["speaker"] for it in items}
    # lines the answer key does not cover: the video step's (B) name, so every line shows someone
    b = OUT / vid / "episode_facts.v3_scenes_find.json"
    if b.exists():
        fb = load(b)
        nm = {x["key"]: x.get("name") for x in fb["episode_pass"]["speakers"]}
        for lid, k in (fb["episode_pass"]["derived"].get("line_speakers") or {}).items():
            who.setdefault(lid, nm.get(k) or k)
    lines = [{"id": r["id"], "t": round(r["start"], 2), "e": round(r.get("end", r["start"]), 2), "ts": fmt_ts(r["start"]),
              "te": fmt_ts(r.get("end", r["start"])), "sound": r["kind"] != "speech",
              "who": "" if r["kind"] != "speech" else who.get(r["id"], "?"), "text": r.get("text") or r.get("tag") or ""} for r in f["lines"]]
    return {"vid": vid, "title": title, "video": f"media/{vid}.mp4", "lines": lines, "places": places(f, props),
            "n_props": {k: len(v) for k, v in props.items()}}


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scene Changes Key</title>
<style>
:root{--bg:#f6f6f3;--panel:#fff;--ink:#1c1c1a;--ink2:#55544f;--muted:#86857e;--line:#e2e2dc;--accent:#2f5d8a;--ok:#2e7d4f;--okbg:#e3f3e9;--bad:#b3412f;--badbg:#f8e3df}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161615;--panel:#1f1f1d;--ink:#ecebe7;--ink2:#bdbab2;--muted:#8d8a83;--line:#34332f;--accent:#8ab4e0;--ok:#7fcf9c;--okbg:#1d3326;--bad:#f2a49c;--badbg:#3a1f1c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans Telugu",sans-serif}
header{padding:14px 16px 0;max-width:1400px;margin:0 auto}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--ink2);font-size:13px;margin:0 0 10px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--line)}
.tab{border:1px solid var(--line);border-bottom:none;background:var(--bg);color:var(--ink2);padding:8px 12px;border-radius:8px 8px 0 0;cursor:pointer;font:inherit}
.tab.on{background:var(--panel);color:var(--ink);font-weight:600}
main{max-width:1400px;margin:0 auto;padding:12px 16px 70px}
.split{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:12px;align-items:start}
.side{position:sticky;top:8px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px}
.side video{width:100%;border-radius:8px;background:#000}
.row{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px;margin-bottom:8px}
.row.ok{border-left:5px solid var(--ok)}.row.no{border-left:5px solid var(--bad)}
.ln{display:flex;gap:8px;align-items:baseline;padding:3px 4px;border-radius:6px;cursor:pointer}
.ln:hover{background:var(--bg)}.ln.sug{border-left:3px dashed var(--accent)}
.tag{font-size:11px;color:var(--accent);border:1px solid var(--accent);border-radius:10px;padding:0 6px;white-space:nowrap;margin-left:auto}.ln.pick{background:var(--okbg);outline:2px solid var(--ok)}
.ln .t{font-size:12px;color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums}
.ln .w{font-size:12px;font-weight:600;min-width:110px;color:var(--ink2)}.ln.snd .x{color:var(--muted);font-style:italic}
.top{display:flex;gap:10px;align-items:center;justify-content:space-between;margin-bottom:4px}
.play{font:inherit;font-size:12px;border:1px solid var(--accent);color:var(--accent);background:var(--panel);border-radius:6px;padding:1px 7px;cursor:pointer}
.ctl{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:6px}
.ctl button{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:6px;padding:3px 10px;cursor:pointer}
.ctl button.sel{background:var(--badbg);border-color:var(--bad);color:var(--bad)}
.meta{font-size:12px;color:var(--ink2)}
.q{font-size:16px;font-weight:600;margin:4px 0 2px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.q button{font:inherit;font-size:14px;font-weight:600;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:6px;padding:3px 14px;cursor:pointer}
.q button.selok{background:var(--okbg);border-color:var(--ok);color:var(--ok)}.q button.sel{background:var(--badbg);border-color:var(--bad);color:var(--bad)}
.nt{width:100%;margin-top:6px;font:inherit;font-size:13px;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:4px 6px;resize:vertical}
input{font:inherit;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:3px 6px}
.bar{position:fixed;z-index:10;bottom:0;left:0;right:0;background:var(--panel);border-top:1px solid var(--line);padding:8px 16px;display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.bar button{font:inherit;background:var(--accent);color:var(--bg);border:none;border-radius:8px;padding:7px 14px;cursor:pointer}
@media (max-width:900px){.split{grid-template-columns:1fr}.side{position:static}}
</style></head><body>
<header><h1>Where do the scenes change?</h1>
<p class="sub">The AI marked these places as "a new scene starts here". For each box: press <b>▶ Play this part</b> (5 s before to 5 s after), then answer the question: <b>Yes</b> if a new scene really starts there, <b>No, same scene</b> if it is the same place, time and people continuing. If it does change but on a different line, click that line (it turns green). A scene = one place and time with one group of people; music or a pause inside the same conversation is not a change. If you see a scene change the AI missed, type its line id (for example L0123) on the left and press Add. Your answers stay in this browser. When done, press <b>Download</b> and send me the file.</p>
<nav class="tabs" id="tabs"></nav></header>
<main id="main"></main>
<div class="bar"><span id="count" class="meta"></span><button id="dl">Download (CSV)</button></div>
<script>
const DATA = __DATA__;
const SKEY = "scenes_key_v1_" + DATA.map(e=>e.vid).join("_");
let store = {}; try { store = JSON.parse(localStorage.getItem(SKEY) || "{}"); } catch (e) { store = {}; }
const save = () => { try { localStorage.setItem(SKEY, JSON.stringify(store)); } catch (e) {} count(); };
let cur = 0;
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function st(vid){ return store[vid] || (store[vid] = {picks:{}, added:[]}); }
function count(){
  const n = DATA.map(e => `${e.title}: ${Object.keys(st(e.vid).picks).length}/${e.places.length} checked, ${st(e.vid).added.length} added`);
  document.getElementById("count").textContent = n.join(" · ");
}
function tabs(){
  document.getElementById("tabs").innerHTML = DATA.map((e,i)=>`<button class="tab ${i===cur?"on":""}" data-i="${i}">${esc(e.title)}</button>`).join("");
  document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>{cur=+b.dataset.i;tabs();render();});
}
function seek(t, stop){
  // wait for the video to load before jumping (a jump before that is lost and it plays from 0:00)
  const v = document.getElementById("v");
  const go = () => { v.currentTime = Math.max(0, t); stopAt = stop; v.play(); };
  if (v.readyState >= 1) go(); else { v.addEventListener("loadedmetadata", go, {once:true}); v.load(); }
}
let stopAt = null;
const fmt = t => { t = Math.max(0, t); return `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`; };
function render(){
  // the video is built once per tab; clicks only redraw the list, so the video never restarts
  const e = DATA[cur];
  document.getElementById("main").innerHTML = `<div class="split"><div class="side"><video id="v" controls preload="auto" src="${e.video}"></video>
    <div class="meta" id="now"></div>
    <div class="ctl"><input id="addid" placeholder="line id, e.g. L0123"><button id="add">Add a missed change</button></div>
    <div class="meta" id="addlist"></div></div><div id="list"></div></div>`;
  const v = document.getElementById("v");
  v.ontimeupdate = () => { if (stopAt !== null && v.currentTime >= stopAt) { v.pause(); stopAt = null; }
    const m = Math.floor(v.currentTime / 60), sec = (v.currentTime % 60).toFixed(1).padStart(4, "0");
    document.getElementById("now").textContent = `Video at ${m}:${sec}`; };
  document.getElementById("add").onclick=()=>{ const s = st(e.vid), x=document.getElementById("addid").value.trim().toUpperCase();
    if(!e.lines.some(l=>l.id===x)){ alert("No line "+x+" in this episode"); return; } s.added.push(x); save(); list(); };
  list();
}
function list(){
  const e = DATA[cur], s = st(e.vid);
  document.getElementById("addlist").innerHTML = s.added.map((a,i)=>`${esc(a)} <a href="#" data-r="${i}">remove</a>`).join(" · ") || "No added changes.";
  let h = "";
  e.places.forEach((p, k) => {
    const pick = s.picks[k], a = e.lines[p.props[0]], b = e.lines[p.props[p.props.length - 1]];
    // play 5 s before the suggested change to 5 s after it
    const sugs = p.props.map(i => `${e.lines[i].id} (${e.lines[i].ts})`).join(" or ");
    h += `<div class="row ${pick===undefined?"":pick==="none"?"no":"ok"}"><div class="top"><span class="meta">Check ${k+1} of ${e.places.length} in this episode</span>
      <button class="play" data-t="${a.t - 5}" data-e="${b.e + 5}">▶ Play this part (${fmt(a.t - 5)}–${fmt(b.e + 5)})</button></div>
      <div class="q">Does a new scene start at ${sugs}?
        ${p.props.length === 1 ? `<button data-yes="${k}" data-id="${a.id}" class="${pick===a.id?"selok":""}">Yes</button>` : ""}
        <button data-none="${k}" class="${pick==="none"?"sel":""}">No, same scene</button></div>
      <div class="meta">${p.props.length === 1 ? "If the new scene starts at a different line, click that line below." : "If yes, click the line below where the new scene starts."}</div>`;
    for (let i = p.lo; i <= p.hi; i++) {
      const l = e.lines[i], sug = p.props.includes(i);
      h += `<div class="ln ${l.sound?"snd":""} ${pick===l.id?"pick":""} ${sug?"sug":""}" data-k="${k}" data-id="${l.id}"><button class="play" data-t="${l.t - 0.3}" data-e="${l.e + 0.3}">▶</button>
        <span class="t">${l.id} ${l.ts}–${l.te}</span><span class="w">${esc(l.sound?"(sound)":l.who)}</span><span class="x">${esc(l.text)}</span>${sug?'<span class="tag">suggested change</span>':""}</div>`;
    }
    h += `<textarea class="nt" data-note="${k}" rows="1" placeholder="Notes (optional): anything wrong here, e.g. the transcript has words but she is only humming">${esc((s.notes||{})[k]||"")}</textarea></div>`;
  });
  const box = document.getElementById("list");
  box.innerHTML = h;
  box.querySelectorAll(".play").forEach(b=>b.onclick=ev=>{ ev.stopPropagation(); seek(+b.dataset.t, b.dataset.e ? +b.dataset.e : null); });
  box.querySelectorAll(".ln").forEach(d=>d.onclick=()=>{ s.picks[d.dataset.k]=d.dataset.id; save(); list(); });
  // typing a note saves it without redrawing (the cursor stays in the box)
  box.querySelectorAll("[data-note]").forEach(t=>t.oninput=()=>{ (s.notes = s.notes || {})[t.dataset.note]=t.value; save(); });
  box.querySelectorAll("[data-yes]").forEach(b=>b.onclick=()=>{ s.picks[b.dataset.yes]=b.dataset.id; save(); list(); });
  box.querySelectorAll("[data-none]").forEach(b=>b.onclick=()=>{ s.picks[b.dataset.none]="none"; save(); list(); });
  document.querySelectorAll("[data-r]").forEach(a=>a.onclick=ev=>{ev.preventDefault(); s.added.splice(+a.dataset.r,1); save(); list();});
}
document.getElementById("dl").onclick=()=>{
  const rows=[["vid","place","lines","new_scene_starts_at","notes"]];
  DATA.forEach(e=>{ const s=st(e.vid);
    e.places.forEach((p,k)=>rows.push([e.vid,k+1,`${e.lines[p.lo].id}-${e.lines[p.hi].id}`,s.picks[k]===undefined?"":s.picks[k],(s.notes||{})[k]||""]));
    s.added.forEach(a=>rows.push([e.vid,"added","",a,""])); });
  const csv=rows.map(r=>r.map(x=>`"${String(x).replace(/"/g,'""')}"`).join(",")).join("\n");
  const a=document.createElement("a"); a.href=URL.createObjectURL(new Blob(["﻿"+csv],{type:"text/csv"})); a.download="scenes_key.csv"; a.click(); };
tabs(); render(); count();
</script></body></html>"""


if __name__ == "__main__":
    out = Path(sys.argv[1])
    eps = [episode(*a.split("=", 1)) for a in sys.argv[2:]]
    for e in eps:
        print(e["vid"], "proposed changes:", e["n_props"], "-> places to check:", len(e["places"]))
    out.write_text(PAGE.replace("__DATA__", json.dumps(eps, ensure_ascii=False)), encoding="utf-8")
    print("wrote", out)
