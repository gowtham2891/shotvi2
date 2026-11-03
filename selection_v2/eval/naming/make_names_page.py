"""Build review/names.html: per episode, the cold-start naming result (runs/name_test/<vid>/episode_facts.json) with the
video, every line's final name, what the naming check changed, its not-sure lines, the answer key's verdict per checked
line, and each person's face frames (the video step's reference times). The founder marks wrong names and downloads a CSV."""
import html
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]
REPO = Path("/Users/ganeshgowtham/projects/projects/shotvi")
sys.path.insert(0, str(REPO))
from selection_v2 import speaker_accuracy as SA  # noqa: E402
from selection_v2.stage1_facts import fmt_ts  # noqa: E402
from selection_v2 import naming_check as NC  # noqa: E402
from selection_v2.eval.naming import score as SC  # noqa: E402

EPISODES = [("j14Om1SaFYE", "Couple Goals Ep 1", True), ("p3XTZlZ6-xw", "Surya Ep 1", False), ("fjGrX8aBVAc", "Love Brew Ep 6", False)]
VARIANTS = ["episode_facts.v3.json", "episode_facts.v2_videoonly.json", "episode_facts.v2.json", "episode_facts.json"]   # newest first
OUT = REPO / "review"
MEDIA = OUT / "names_media"


def frame(vid, t, name):
    MEDIA.joinpath(vid).mkdir(parents=True, exist_ok=True)
    dst = MEDIA / vid / name
    if not dst.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(REPO / "scribe_dataset" / vid / f"{vid}.mp4"),
                        "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "4", str(dst)], check=True)
    return f"names_media/{vid}/{name}"


def episode_data(vid, title, key_reviewed):
    d0 = REPO / "runs" / "name_test" / vid
    p = next((d0 / v for v in VARIANTS if (d0 / v).exists()), None)
    if p is None:
        return None
    f = json.loads(p.read_text(encoding="utf-8"))
    ep = f["episode_pass"]; d = ep["derived"]
    summ = NC.speaker_summary(f)
    names = {s["key"]: s.get("name") for s in ep["speakers"]}
    final = d.get("line_speakers") or {}
    voices = d.get("line_voices") or {}
    onscr = d.get("on_screen") or {}
    by_id = {r["id"]: r for r in f["lines"]}
    # the answer key, per checked line
    items, chars, aliases = SC.key_items(f, vid)
    res = SC.score(f, vid)
    m = res.get("final", {}).get("map", {})
    keyl = {it["lid"]: it for it in items}
    moved = {}
    for mv in summ.get("moved") or []:
        moved.setdefault(mv["line"], mv)
    people = []
    for c in summ.get("cast") or []:
        k = c["key"]
        ids = [x for x in (summ.get("alone_on_screen") or {}).get(k, []) if x in by_id and by_id[x]["end"] - by_id[x]["start"] >= 1.0]
        pick = [ids[round(i * (len(ids) - 1) / 2)] for i in range(3)] if len(ids) >= 3 else ids
        imgs = []
        for x in dict.fromkeys(pick):
            t = (by_id[x]["start"] + by_id[x]["end"]) / 2
            imgs.append({"src": frame(vid, t, f"{k}_{t:.1f}.jpg"), "t": round(t, 2), "t_str": fmt_ts(t)})
        people.append({"key": k, "name": c["name"], "role": c.get("role") or "", "main": bool(c.get("main")), "n_lines": c.get("n_lines", 0),
                       "aka": c.get("aka") or [], "look": c.get("look") or "", "imgs": imgs})
    nm = lambda k: names.get(k) or k
    lines = []
    for r in f["lines"]:
        row = {"id": r["id"], "t": round(r["start"], 2), "ts": fmt_ts(r["start"]), "text": r.get("text") or ""}
        if r["kind"] != "speech":
            row["sound"] = True; lines.append(row); continue
        k = final.get(r["id"]) or r.get("speaker_key")
        toks = row["text"].split()
        if r["id"] in voices:
            ps = voices[r["id"]]
            row["parts"] = [{"who": " + ".join(nm(x) for x in p["keys"]), "words": " ".join(toks[p["word"]:(ps[i + 1]["word"] if i + 1 < len(ps) else len(toks))])}
                            for i, p in enumerate(ps)]
        row.update({"key": k, "name": nm(k)})
        if r["id"] in onscr:
            seen = onscr[r["id"]]
            row["seen"] = ", ".join(nm(x) for x in seen) or "nobody"
            row["off"] = k not in seen
        if r["id"] in moved:
            mv = moved[r["id"]]
            row["changed"] = {"from": " + ".join(nm(x) for x in mv["from"]), "why": mv.get("why") or ""}
        it = keyl.get(r["id"])
        if it:
            row["key_says"] = it["speaker"] + (" (off-screen)" if it["offscreen"] else "")
            row["key_ok"] = m.get(k) == it["speaker"]
            if it.get("on_screen") is not None and r["id"] in onscr:
                got = {m.get(x, nm(x)) for x in onscr[r["id"]]}
                row["key_seen"] = ", ".join(it["on_screen"]) or "nobody"
                row["key_seen_ok"] = got == set(it["on_screen"])
        lines.append(row)
    steps = {}
    for st, lab in (("text", "Text step"), ("final", "Video step (final)")):
        x = res.get(st)
        if x:
            steps[lab] = {"ok": x["right_person"], "n": x["n"], "acc": x["right_person"] / x["n"], "what": "right person"}
            if st == "final":
                steps["Names"] = {"ok": x["right_name"], "n": x["n"], "acc": x["right_name"] / x["n"], "what": "right name"}
                o = x.get("on_screen") or {}
                if o.get("n"):
                    steps["On screen"] = {"ok": o["exact"], "n": o["n"], "acc": o["exact"] / o["n"], "what": "exactly right"}
    scenes = [{"id": s["id"], "t": round(s["start"], 2), "ts": s["start_str"], "te": s["end_str"], "label": ""} for s in d["scenes"]]
    u = (f.get("identity_pass") or {}).get("usage") or {}
    return {"vid": vid, "title": title, "video": f"media/{vid}.mp4", "people": people, "merged": [], "lines": lines, "scenes": scenes,
            "steps": steps, "has_key": bool(items), "key_reviewed": key_reviewed, "run": p.name, "n_changed": len(moved),
            "n_unsure": 0, "cost": round(float(u.get("cost_inr") or 0), 2), "warnings": (f.get("identity_pass") or {}).get("warnings") or []}


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Speaker Names Review</title>
<style>
:root{--bg:#f7f6f3;--panel:#fff;--ink:#1d1d1b;--ink2:#55534e;--muted:#8a877f;--line:#e4e1da;--accent:#2f5d8a;
--ok:#2e7d4f;--okbg:#e6f3ea;--bad:#b3261e;--badbg:#fbe9e7;--warn:#8a5a00;--warnbg:#fff3d6;--chg:#5b3fa0;--chgbg:#efeafb}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161615;--panel:#1f1f1d;--ink:#ecebe7;--ink2:#bdbab2;--muted:#8d8a83;
--line:#34332f;--accent:#8ab4e0;--ok:#7fcf9c;--okbg:#1d3326;--bad:#f2a49c;--badbg:#3a1f1c;--warn:#f0c46a;--warnbg:#3a3018;--chg:#c3b2f0;--chgbg:#2c2540}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans Telugu",sans-serif}
header{padding:14px 16px 0;max-width:1400px;margin:0 auto}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--ink2);font-size:13px;margin:0 0 10px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--line)}
.tab{border:1px solid var(--line);border-bottom:none;background:var(--bg);color:var(--ink2);padding:8px 12px;border-radius:8px 8px 0 0;cursor:pointer;font:inherit}
.tab.on{background:var(--panel);color:var(--ink);font-weight:600}
main{max-width:1400px;margin:0 auto;padding:12px 16px 60px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:12px}
.stats{display:flex;gap:10px;flex-wrap:wrap}.stat{flex:1 1 180px;border:1px solid var(--line);border-radius:8px;padding:8px 10px}
.stat b{font-size:22px;display:block}.stat span{color:var(--ink2);font-size:12px}
.people{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.person{border:1px solid var(--line);border-radius:8px;padding:8px}.person .nm{font-weight:600}.person .rl{color:var(--ink2);font-size:13px}
.imgs{display:flex;gap:4px;margin:6px 0}.imgs img{width:33%;min-width:0;border-radius:4px;cursor:pointer;border:1px solid var(--line)}
.noimg{color:var(--muted);font-size:12px;margin:6px 0}
.badge{display:inline-block;font-size:11px;padding:1px 6px;border-radius:10px;border:1px solid var(--line);color:var(--ink2);margin-left:4px}
select,textarea,input{font:inherit;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:3px 6px}
textarea{width:100%;min-height:34px}
.split{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:12px;align-items:start}
.vid{position:sticky;top:8px}.vid video{width:100%;border-radius:8px;background:#000}
.filters{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}.filters button{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--panel);color:var(--ink2);border-radius:14px;padding:3px 10px;cursor:pointer}
.filters button.on{background:var(--accent);border-color:var(--accent);color:var(--bg)}
.lines{max-height:78vh;overflow:auto;border:1px solid var(--line);border-radius:8px;background:var(--panel)}
.scene{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:6px 10px;font-size:13px;font-weight:600;z-index:1}
.ln{display:grid;grid-template-columns:62px 150px minmax(0,1fr) auto;gap:8px;padding:5px 10px;border-bottom:1px solid var(--line);align-items:start}
.ln.sound{color:var(--muted);font-size:12px;padding:2px 10px}.ln.now{outline:2px solid var(--accent);outline-offset:-2px}
.ts{color:var(--accent);cursor:pointer;font-variant-numeric:tabular-nums;font-size:13px}.who{font-weight:600;font-size:13px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:middle}
.tx{min-width:0}.note{font-size:12px;margin-top:3px;padding:2px 6px;border-radius:5px}
.n-chg{background:var(--chgbg);color:var(--chg)}.n-uns{background:var(--warnbg);color:var(--warn)}.n-bad{background:var(--badbg);color:var(--bad)}.n-ok{color:var(--ok)}
.mark{font-size:12px;border:1px solid var(--line);background:var(--panel);color:var(--ink2);border-radius:6px;padding:2px 6px;cursor:pointer;white-space:nowrap}
.mark.on{background:var(--badbg);color:var(--bad);border-color:var(--bad)}
.fix{grid-column:2 / -1;display:flex;gap:6px;flex-wrap:wrap;align-items:center;font-size:13px}
.bar{position:fixed;bottom:0;left:0;right:0;background:var(--panel);border-top:1px solid var(--line);padding:8px 16px;display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.bar button{font:inherit;background:var(--accent);color:var(--bg);border:none;border-radius:8px;padding:7px 14px;cursor:pointer}
.legend{font-size:12px;color:var(--ink2)}
@media (max-width:900px){.split{grid-template-columns:1fr}.vid{position:static}.ln{grid-template-columns:52px minmax(0,1fr) auto}.ln .who{grid-column:2}.ln .tx{grid-column:2 / -1}.lines{max-height:none}}
</style></head><body>
<header><h1>Speaker names: cold-start test</h1>
<p class="sub">Each episode ran on its own (text step, then the video step watching the episode), with no cast list, no saved memory and no hand fixes. For every line you see who talks and who is on screen. Check the people, their photos and the lines. Mark what's wrong, then press <b>Download my review</b>.</p>
<nav class="tabs" id="tabs"></nav></header>
<main id="main"></main>
<div class="bar"><span class="legend" id="count"></span><button id="dl">Download my review (CSV)</button></div>
<script>
const DATA = __DATA__;
const COLORS = ["#2f6db5","#c2552b","#2e8b57","#8a4fbf","#b8860b","#d14d8a","#3a9aa8","#6b6b2f","#9c3f3f","#4f6b9c"];
let store = {};
try { store = JSON.parse(localStorage.getItem("names_review_v2") || "{}"); } catch (e) { store = {}; }
const save = () => { try { localStorage.setItem("names_review_v2", JSON.stringify(store)); } catch (e) {} updateCount(); };
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));
let cur = 0, filt = "all";
function updateCount(){ let n=0; for (const v in store) for (const k in store[v]) if (store[v][k] && (store[v][k].verdict||store[v][k].correct||store[v][k].note)) n++;
  document.getElementById("count").textContent = n ? `${n} item(s) marked so far (kept in this browser until you download)` : "Nothing marked yet"; }
function tabs(){ const t=document.getElementById("tabs"); t.innerHTML="";
  DATA.forEach((e,i)=>{ const b=document.createElement("button"); b.className="tab"+(i===cur?" on":""); b.textContent=e.title; b.onclick=()=>{cur=i;filt="all";render();}; t.appendChild(b); }); }
function render(){ tabs(); const e=DATA[cur], m=document.getElementById("main"); store[e.vid]=store[e.vid]||{};
  const col={}; e.people.forEach((p,i)=>col[p.key]=COLORS[i%COLORS.length]);
  const steps=Object.entries(e.steps).map(([k,s])=>`<div class="stat"><b>${Math.round(s.acc*100)}%</b><span>${esc(k)}: ${s.ok} of ${s.n} checked lines ${esc(s.what)}</span></div>`).join("");
  const keyNote = !e.has_key ? `<p class="sub">No answer key for this episode yet, so there's no score. Your review is the check.</p>` : (e.key_reviewed ? `<p class="sub">Scored against the answer key you reviewed. Run: ${esc(e.run)}</p>` : `<p class="note n-uns">The answer key for this episode was made by an AI agent and you haven't reviewed it, so these scores are only a rough guide. Run: ${esc(e.run)}</p>`);
  let h=`<div class="card"><div class="stats">${steps}<div class="stat"><b>${e.n_changed}</b><span>lines the video step gave to someone else than the text step</span></div><div class="stat"><b>₹${e.cost}</b><span>cost of this run</span></div></div>${keyNote}${e.warnings.length?`<p class="note n-bad">${esc(e.warnings.join("; "))}</p>`:""}</div>`;
  h+=`<div class="card"><b>People the pipeline found</b> <span class="legend">(photos: only from lines where the video step saw this person alone on screen; click a photo to jump there)</span><div class="people">`;
  e.people.forEach(p=>{ const r=store[e.vid]["person:"+p.key]||{};
    const imgs=p.imgs.length?`<div class="imgs">${p.imgs.map(im=>`<img src="${im.src}" alt="${esc(p.name)} at ${im.t_str}" title="${im.t_str}" data-t="${im.t}">`).join("")}</div>`:`<div class="noimg">never seen alone on screen, so no photo</div>`;
    h+=`<div class="person"><div class="nm"><span class="dot" style="background:${col[p.key]}"></span>${esc(p.name)}${p.main?'<span class="badge">main</span>':""}<span class="badge">${p.n_lines} lines</span></div>
    <div class="rl">${esc(p.role)}${p.aka.length?" · also called "+esc(p.aka.join(", ")):""}</div>${p.look?`<div class="rl">Looks: ${esc(p.look)}</div>`:""}${imgs}
    <select data-k="person:${p.key}" data-f="verdict"><option value="">— is this right? —</option>${["right","wrong name","wrong photos","two people mixed","not a real person"].map(o=>`<option ${r.verdict===o?"selected":""}>${o}</option>`).join("")}</select>
    <textarea data-k="person:${p.key}" data-f="note" placeholder="note (optional)">${esc(r.note||"")}</textarea></div>`; });
  h+=`</div>${e.merged.length?`<p class="sub">Merged as the same person: ${e.merged.map(x=>esc(x.name)+" → "+esc(x.into)).join("; ")}</p>`:""}</div>`;
  const fl=[["all","All lines"],["changed","Changed by the video step"],["off","Speaker off screen"],["two","Two voices / group"],["bad","Answer key disagrees"],["marked","Marked by me"]];
  h+=`<div class="split"><div class="vid card"><video id="v" controls preload="metadata" src="${e.video}"></video><p class="legend">Click a time to jump. The highlighted line follows the video.</p>
  <textarea data-k="episode" data-f="note" placeholder="Overall note for this episode">${esc((store[e.vid].episode||{}).note||"")}</textarea></div>
  <div><div class="filters">${fl.map(([k,l])=>`<button data-filter="${k}" class="${filt===k?"on":""}">${l}</button>`).join("")}</div><div class="lines" id="lines">`;
  const scenes=[...e.scenes]; let si=0;
  e.lines.forEach(l=>{ while(si<scenes.length && l.t>=scenes[si].t-0.001){ const s=scenes[si++]; if(filt==="all") h+=`<div class="scene">Scene ${s.id} · ${s.ts}–${s.te}</div>`; }
    const r=store[e.vid]["line:"+l.id]||{};
    const show = filt==="all" || (filt==="changed"&&l.changed) || (filt==="off"&&l.off) || (filt==="two"&&l.parts) || (filt==="bad"&&((l.key_says&&!l.key_ok)||(l.key_seen&&!l.key_seen_ok))) || (filt==="marked"&&r.verdict);
    if(!show || (l.sound && filt!=="all")) return;
    if(l.sound){ h+=`<div class="ln sound" data-t="${l.t}"><span class="ts" data-t="${l.t}">${l.ts}</span><span></span><span>${esc(l.text)}</span><span></span></div>`; return; }
    let notes="";
    if(l.changed) notes+=`<div class="note n-chg">Changed from ${esc(l.changed.from)}: ${esc(l.changed.why)}</div>`;
    if(l.parts) notes+=`<div class="note n-chg">Voices: ${l.parts.map(p=>esc(p.who)+": "+esc(p.words)).join(" → ")}</div>`;
    if(l.seen!==undefined) notes+=`<div class="note">On screen: ${esc(l.seen)}${l.off?" · <b>speaker off screen</b>":""}</div>`;
    if(l.key_says) notes+= l.key_ok?`<div class="note n-ok">✓ answer key agrees on the speaker</div>`:`<div class="note n-bad">✗ answer key says the speaker is ${esc(l.key_says)}</div>`;
    if(l.key_seen) notes+= l.key_seen_ok?`<div class="note n-ok">✓ answer key agrees on who is on screen</div>`:`<div class="note n-bad">✗ answer key says on screen: ${esc(l.key_seen)}</div>`;
    const opts=e.people.map(p=>`<option ${r.correct===p.name?"selected":""}>${esc(p.name)}</option>`).join("");
    h+=`<div class="ln" data-t="${l.t}" id="L-${l.id}"><span class="ts" data-t="${l.t}">${l.ts}</span>
      <span class="who"><span class="dot" style="background:${col[l.key]||"#888"}"></span>${esc(l.name)}</span>
      <span class="tx">${esc(l.text)}${notes}</span>
      <button class="mark ${r.verdict?"on":""}" data-line="${l.id}">${r.verdict?"marked wrong":"wrong?"}</button>
      ${r.verdict?`<div class="fix">Who really says it: <select data-k="line:${l.id}" data-f="correct"><option value="">— pick —</option>${opts}<option ${r.correct==="someone else"?"selected":""}>someone else</option><option ${r.correct==="can't tell"?"selected":""}>can't tell</option></select>
      <input data-k="line:${l.id}" data-f="seen" placeholder="who is really on screen (optional)" value="${esc(r.seen||"")}"><input data-k="line:${l.id}" data-f="note" placeholder="note" value="${esc(r.note||"")}"></div>`:""}</div>`; });
  h+=`</div></div></div>`; m.innerHTML=h;
  const v=document.getElementById("v");
  m.querySelectorAll("[data-t]").forEach(x=>{ if(x.classList.contains("ts")||x.tagName==="IMG") x.onclick=()=>{ v.currentTime=+x.dataset.t; v.play(); }; });
  m.querySelectorAll("[data-filter]").forEach(b=>b.onclick=()=>{ filt=b.dataset.filter; render(); });
  m.querySelectorAll("[data-k]").forEach(x=>{ x.onchange=x.oninput=()=>{ const k=x.dataset.k; store[e.vid][k]=store[e.vid][k]||{}; store[e.vid][k][x.dataset.f]=x.value; save(); }; });
  m.querySelectorAll("[data-line]").forEach(b=>b.onclick=()=>{ const k="line:"+b.dataset.line; const r=store[e.vid][k]||{};
    if(r.verdict){ delete store[e.vid][k]; } else { store[e.vid][k]={verdict:"wrong",correct:"",note:""}; } save(); const y=document.getElementById("lines").scrollTop; render(); document.getElementById("lines").scrollTop=y; });
  const rows=[...m.querySelectorAll(".ln")]; let last=null;
  v.ontimeupdate=()=>{ const t=v.currentTime; let best=null; for(const r of rows){ if(+r.dataset.t<=t+0.05) best=r; else break; }
    if(best&&best!==last){ last&&last.classList.remove("now"); best.classList.add("now"); last=best; } };
  updateCount(); }
document.getElementById("dl").onclick=()=>{ const rows=[["episode","item","what","verdict_or_correct_name","on_screen","note"]];
  DATA.forEach(e=>{ const s=store[e.vid]||{}; for(const k in s){ const r=s[k]; if(!r) continue;
    if(k==="episode"){ if(r.note) rows.push([e.vid,"episode","note","","",r.note]); continue; }
    const [kind,id]=k.split(":"); const label = kind==="person" ? (e.people.find(p=>p.key===id)||{}).name : (e.lines.find(l=>l.id===id)||{}).name;
    rows.push([e.vid, id, kind==="person"?"person "+label:"line said by "+label, kind==="person"?(r.verdict||""):(r.correct||"wrong"), r.seen||"", r.note||""]); } });
  const csv=rows.map(r=>r.map(x=>`"${String(x).replace(/"/g,'""')}"`).join(",")).join("\n");
  const a=document.createElement("a"); a.href=URL.createObjectURL(new Blob(["\ufeff"+csv],{type:"text/csv"})); a.download="names_review.csv"; a.click(); };
render();
</script></body></html>"""


def main():
    data = [x for x in (episode_data(v, t, kr) for v, t, kr in EPISODES) if x]
    (OUT / "names.html").write_text(PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
    for e in data:
        print(e["title"], "|", e["run"], "|", {k: f"{v['ok']}/{v['n']}" for k, v in e["steps"].items()}, "| people", len(e["people"]), "| photos", sum(len(p["imgs"]) for p in e["people"]))
    print("wrote", OUT / "names.html", f"{(OUT / 'names.html').stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
