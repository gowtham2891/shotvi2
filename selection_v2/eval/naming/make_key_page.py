"""Build an answer-key check page (default review/key_check.html): every answer-key line (data/speakers/<vid>.json) for the
founder to confirm or correct: who speaks (the voice) and who is on screen (tick boxes, 2026-11-02), with a 4-frame strip
over the line, a play button for exactly that line, the key's note, the people list with where each name comes from, and
a filter for lines where the cold-start pipeline (runs/name_test) disagrees. Corrections download as CSV.

Usage: make_key_page.py [<out.html> <vid>=<title> ...]"""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path("/Users/ganeshgowtham/projects/projects/shotvi")
sys.path.insert(0, str(REPO))
from selection_v2 import speaker_accuracy as SA  # noqa: E402
from selection_v2.stage1_facts import fmt_ts  # noqa: E402

EPISODES = [("j14Om1SaFYE", "Couple Goals Ep 1"), ("p3XTZlZ6-xw", "Surya Ep 1"), ("fjGrX8aBVAc", "Love Brew Ep 6")]
OUT = REPO / "review"
STRIPS = OUT / "key_media"


def strip(vid, i, start, end):
    """4 frames evenly over the line (a short line gets frames around it), side by side, 4 x 200 px."""
    d = STRIPS / vid; d.mkdir(parents=True, exist_ok=True)
    dst = d / f"k{i:03d}.jpg"
    if dst.exists():
        return f"key_media/{vid}/{dst.name}"
    a, b = float(start), float(end)
    if b - a < 1.2:
        mid = (a + b) / 2; a, b = mid - 0.6, mid + 0.6
    ts = [a + (b - a) * (k + 0.5) / 4 for k in range(4)]
    src = str(REPO / "scribe_dataset" / vid / f"{vid}.mp4")
    parts = []
    for k, t in enumerate(ts):
        p = d / f"_k{i:03d}_{k}.jpg"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(t, 0):.2f}", "-i", src, "-frames:v", "1",
                        "-vf", "scale=200:-2", "-q:v", "5", str(p)], check=True)
        parts.append(p)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + sum((["-i", str(p)] for p in parts), [])
                   + ["-filter_complex", "hstack=inputs=4", "-q:v", "5", str(dst)], check=True)
    for p in parts:
        p.unlink(missing_ok=True)
    return f"key_media/{vid}/{dst.name}"


def pipeline_view(vid, labels):
    """Per key label: does the cold-start pipeline give the line to the same person (under the best person mapping)?"""
    d0 = REPO / "runs" / "name_test" / vid
    p = next((d0 / n for n in ("episode_facts.v3_full.json", "episode_facts.v3.json", "episode_facts.json") if (d0 / n).exists()), None)
    if p is None:
        return [None] * len(labels)
    f = json.loads(p.read_text(encoding="utf-8"))
    ls = f["episode_pass"]["derived"].get("line_speakers") or {}
    speech = [r for r in f["lines"] if r["kind"] == "speech" and not r.get("zero_len")]
    keys = []
    for l in labels:
        best, bo = None, 0.0
        for r in speech:
            ov = min(r["end"], l["end"]) - max(r["start"], l["start"])
            if ov > bo:
                best, bo = r, ov
        keys.append((ls.get(best["id"]) or best.get("speaker_key")) if best is not None and bo > 0 else None)
    truth = [l["speaker"].split(":", 1)[-1] for l in labels]
    mp = SA.ES.best_one_to_one([(k, t) for k, t in zip(keys, truth) if not t.startswith("UNKNOWN")])
    return [None if t.startswith("UNKNOWN") or k is None else mp.get(k) != t for k, t in zip(keys, truth)]


def episode(vid, title):
    k = json.loads((REPO / "data" / "speakers" / f"{vid}.json").read_text(encoding="utf-8"))
    labels = k["labels"]
    with ThreadPoolExecutor(8) as ex:
        strips = list(ex.map(lambda a: strip(vid, *a), [(i, l["start"], l["end"]) for i, l in enumerate(labels)]))
    dis = pipeline_view(vid, labels)
    chars = [c["name"] if isinstance(c, dict) else c for c in k.get("characters") or []]
    rows = []
    for i, l in enumerate(labels):
        sp = l["speaker"]
        rows.append({"i": i, "s": round(l["start"], 2), "e": round(l["end"], 2), "ts": fmt_ts(l["start"]), "text": l.get("text") or "",
                     "who": sp.split(":", 1)[-1], "off": sp.startswith("OFFSCREEN:"), "conf": l.get("confidence"), "note": l.get("note") or "",
                     "img": strips[i], "dis": dis[i], "seen": l.get("on_screen"), "partly": l.get("on_screen_partly") or [],
                     "voices": [f"{', '.join(v['speakers'])} from '{v['from_text']}'" for v in l.get("voices") or []]})
    people = [{"name": c["name"], "desc": c.get("desc", ""), "src": c.get("name_source", ""), "where": c.get("name_evidence") or c.get("evidence") or ""}
              for c in k.get("characters") or [] if isinstance(c, dict)]
    return {"vid": vid, "title": title, "video": f"media/{vid}.mp4", "chars": chars, "rows": rows, "people": people,
            "descs": {c["name"]: c.get("desc", "") for c in k.get("characters") or [] if isinstance(c, dict)}}


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Answer Key Check</title>
<style>
:root{--bg:#f7f6f3;--panel:#fff;--ink:#1d1d1b;--ink2:#55534e;--muted:#8a877f;--line:#e4e1da;--accent:#2f5d8a;
--ok:#2e7d4f;--okbg:#e6f3ea;--bad:#b3261e;--badbg:#fbe9e7;--warn:#8a5a00;--warnbg:#fff3d6}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161615;--panel:#1f1f1d;--ink:#ecebe7;--ink2:#bdbab2;--muted:#8d8a83;
--line:#34332f;--accent:#8ab4e0;--ok:#7fcf9c;--okbg:#1d3326;--bad:#f2a49c;--badbg:#3a1f1c;--warn:#f0c46a;--warnbg:#3a3018}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans Telugu",sans-serif}
header{padding:14px 16px 0;max-width:1400px;margin:0 auto}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--ink2);font-size:13px;margin:0 0 10px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--line)}
.tab{border:1px solid var(--line);border-bottom:none;background:var(--bg);color:var(--ink2);padding:8px 12px;border-radius:8px 8px 0 0;cursor:pointer;font:inherit}
.tab.on{background:var(--panel);color:var(--ink);font-weight:600}
main{max-width:1400px;margin:0 auto;padding:12px 16px 70px}
.split{display:grid;grid-template-columns:minmax(0,4fr) minmax(0,8fr);gap:12px;align-items:start}
.side{position:sticky;top:8px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px}
.side video{width:100%;border-radius:8px;background:#000}
.cast{font-size:13px;color:var(--ink2);max-height:30vh;overflow:auto;margin-top:8px}.cast b{color:var(--ink)}
.prog{font-size:13px;margin:8px 0}
.filters{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 8px}.filters button{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--panel);color:var(--ink2);border-radius:14px;padding:3px 10px;cursor:pointer}
.filters button.on{background:var(--accent);border-color:var(--accent);color:var(--bg)}
.row{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px;margin-bottom:8px}
.row.ok{border-left:5px solid var(--ok)}.row.fix{border-left:5px solid var(--bad)}
.top{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.play{font:inherit;font-size:13px;border:1px solid var(--accent);color:var(--accent);background:var(--panel);border-radius:6px;padding:2px 8px;cursor:pointer}
.tx{font-size:17px}.who{font-weight:600}.meta{font-size:12px;color:var(--ink2)}
.badge{display:inline-block;font-size:11px;padding:1px 6px;border-radius:10px;margin-left:4px}
.lo{background:var(--warnbg);color:var(--warn)}.dis{background:var(--badbg);color:var(--bad)}.offs{border:1px solid var(--line);color:var(--ink2)}
.strip{width:100%;max-width:800px;border-radius:6px;margin:6px 0;display:block;cursor:pointer}
.note{font-size:12px;color:var(--muted)}.tick{font-size:13px;margin-right:6px;white-space:nowrap}.pp{margin:6px 0;padding-bottom:6px;border-bottom:1px solid var(--line)}.cast{max-height:45vh}
.ctl{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:6px}
.ctl button{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:6px;padding:3px 10px;cursor:pointer}
.ctl button.sel-ok{background:var(--okbg);border-color:var(--ok);color:var(--ok)}.ctl button.sel-fix{background:var(--badbg);border-color:var(--bad);color:var(--bad)}
select,input{font:inherit;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:3px 6px}
input.n{flex:1 1 220px}
.bar{position:fixed;z-index:10;bottom:0;left:0;right:0;background:var(--panel);border-top:1px solid var(--line);padding:8px 16px;display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.bar button{font:inherit;background:var(--accent);color:var(--bg);border:none;border-radius:8px;padding:7px 14px;cursor:pointer}
@media (max-width:900px){.split{grid-template-columns:1fr}.side{position:static}}
</style></head><body>
<header><h1>Answer key check</h1>
<p class="sub">These are the answer keys the naming pipeline is scored against. First check the <b>people</b> (are the names right, and where they come from). Then for each line: press <b>▶ play</b> (it plays just that line) and look at the pictures. Check two things: <b>who speaks</b> (the voice: press Correct or Change) and <b>who is on screen</b> (tick everyone you see during the line; untick anyone who is not there). Your choices stay in this browser. When you finish, press <b>Download my corrections</b> and send me the file.</p>
<nav class="tabs" id="tabs"></nav></header>
<main id="main"></main>
<div class="bar"><span id="count" class="meta"></span><button id="dl">Download my corrections (CSV)</button></div>
<script>
const DATA = __DATA__;
const SKEY = "key_check_v2_" + DATA.map(e=>e.vid).join("_");
let store = {}; try { store = JSON.parse(localStorage.getItem(SKEY) || "{}"); } catch (e) { store = {}; }
const save = () => { try { localStorage.setItem(SKEY, JSON.stringify(store)); } catch (e) {} count(); };
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));
let cur = 0, filt = "all", stopAt = null;
function count(){ let ok=0, fix=0, n=0; DATA.forEach(e=>{ n+=e.rows.length; const s=store[e.vid]||{}; for(const k in s){ if(k.startsWith("person:")) continue; if(s[k].v==="ok") ok++; else if(s[k].v==="fix") fix++; } });
  document.getElementById("count").textContent = `${ok+fix} of ${n} lines checked · ${ok} correct · ${fix} changed`; }
function render(){ const t=document.getElementById("tabs"); t.innerHTML="";
  DATA.forEach((e,i)=>{ const b=document.createElement("button"); b.className="tab"+(i===cur?" on":""); const s=store[e.vid]||{};
    b.textContent=`${e.title} (${Object.keys(s).filter(k=>!k.startsWith("person:")).length}/${e.rows.length})`; b.onclick=()=>{cur=i;filt="all";render();}; t.appendChild(b); });
  const e=DATA[cur], m=document.getElementById("main"); store[e.vid]=store[e.vid]||{}; const S=store[e.vid];
  const fl=[["all","All lines"],["todo","Not checked yet"],["lo","Low confidence"],["dis","Pipeline disagrees"],["off","Off-screen"],["fix","Changed by me"]];
  const show = r => filt==="all" || (filt==="todo"&&!S[r.i]) || (filt==="lo"&&r.conf<3) || (filt==="dis"&&r.dis) || (filt==="off"&&r.off) || (filt==="fix"&&S[r.i]&&S[r.i].v==="fix");
  const opts = cur => e.chars.map(c=>`<option ${cur===c?"selected":""}>${esc(c)}</option>`).join("") + ["someone else","two people in this line","can't tell"].map(c=>`<option ${cur===c?"selected":""}>${c}</option>`).join("");
  let h=`<div class="split"><div class="side"><video id="v" controls preload="metadata" src="${e.video}"></video>
    <div class="prog">${Object.keys(S).filter(k=>!k.startsWith("person:")).length} of ${e.rows.length} lines checked in this episode</div>
    <div class="cast"><b>People in the key</b><br>${e.people.map(p=>{ const r=S["person:"+p.name]||{}; return `<div class="pp"><b>${esc(p.name)}</b>${p.src?` <span class="badge offs">name: ${esc(p.src)}</span>`:""}<br>${esc(p.desc)}${p.where?`<br><i>${esc(p.where)}</i>`:""}<br><select data-pv="${esc(p.name)}"><option value="">— is this right? —</option>${["right","wrong name","two people mixed","not a real person"].map(o=>`<option ${r.v===o?"selected":""}>${o}</option>`).join("")}</select> <input data-pn="${esc(p.name)}" placeholder="correct name / note" value="${esc(r.note||"")}"></div>`; }).join("")}</div></div><div>
    <div class="filters">${fl.map(([k,l])=>`<button data-f="${k}" class="${filt===k?"on":""}">${l}</button>`).join("")}</div>`;
  e.rows.filter(show).forEach(r=>{ const s=S[r.i]||{};
    h+=`<div class="row ${s.v||""}" id="r${r.i}"><div class="top"><button class="play" data-s="${r.s}" data-e="${r.e}">▶ play ${r.ts}</button>
      <span class="tx">${esc(r.text)}</span></div>
      <div class="meta">Key says: <span class="who">${esc(r.who)}</span>${r.off?'<span class="badge offs">off-screen</span>':""}${r.conf<3?`<span class="badge lo">confidence ${r.conf} of 3</span>`:""}${r.dis?'<span class="badge dis">pipeline disagrees</span>':""}</div>
      <img class="strip" loading="lazy" src="${r.img}" alt="4 frames over the line at ${r.ts}" data-s="${r.s}" data-e="${r.e}">
      ${r.voices.length?`<div class="meta">Two voices / group: ${esc(r.voices.join(" → "))}</div>`:""}
      ${r.seen?`<div class="ctl"><span class="meta">On screen:</span>${e.chars.map(c=>{ const on=(s.seen||r.seen).includes(c); return `<label class="tick"><input type="checkbox" data-seen="${r.i}" value="${esc(c)}" ${on?"checked":""}> ${esc(c)}</label>`; }).join("")}${r.partly.length?`<span class="meta">(partly: ${esc(r.partly.join(", "))})</span>`:""}</div>`:""}
      <div class="note">Key's note: ${esc(r.note)}</div>
      <div class="ctl"><button data-ok="${r.i}" class="${s.v==="ok"?"sel-ok":""}">✓ Correct</button><button data-fx="${r.i}" class="${s.v==="fix"?"sel-fix":""}">✗ Change</button>
      ${s.v==="fix"?`<span>Who really says it:</span><select data-sel="${r.i}"><option value="">— pick —</option>${opts(s.to)}</select>`:""}
      <input class="n" data-note="${r.i}" placeholder="note (optional)" value="${esc(s.note||"")}"></div></div>`; });
  h+=`</div></div>`; m.innerHTML=h;
  const v=document.getElementById("v");
  v.ontimeupdate=()=>{ if(stopAt!==null && v.currentTime>=stopAt){ v.pause(); stopAt=null; } };
  m.querySelectorAll("[data-s]").forEach(b=>b.onclick=()=>{ v.currentTime=Math.max(0,+b.dataset.s-0.4); stopAt=+b.dataset.e+0.4; v.play(); });
  m.querySelectorAll("[data-f]").forEach(b=>b.onclick=()=>{ filt=b.dataset.f; render(); });
  const keep = fn => { const y=window.scrollY; fn(); save(); render(); window.scrollTo(0,y); };
  m.querySelectorAll("[data-ok]").forEach(b=>b.onclick=()=>keep(()=>{ const i=b.dataset.ok; S[i]={...(S[i]||{}), v:"ok"}; delete S[i].to; }));
  m.querySelectorAll("[data-fx]").forEach(b=>b.onclick=()=>keep(()=>{ const i=b.dataset.fx; S[i]={...(S[i]||{}), v:"fix"}; }));
  m.querySelectorAll("[data-sel]").forEach(x=>x.onchange=()=>{ S[x.dataset.sel].to=x.value; save(); });
  m.querySelectorAll("[data-note]").forEach(x=>x.oninput=()=>{ const i=x.dataset.note; S[i]={...(S[i]||{}), note:x.value}; save(); });
  m.querySelectorAll("[data-seen]").forEach(x=>x.onchange=()=>{ const i=x.dataset.seen; const r=e.rows.find(q=>String(q.i)===i);
    const cur=new Set((S[i]&&S[i].seen)||r.seen); x.checked?cur.add(x.value):cur.delete(x.value); S[i]={...(S[i]||{}), seen:[...cur]}; save(); });
  m.querySelectorAll("[data-pv]").forEach(x=>x.onchange=()=>{ const k="person:"+x.dataset.pv; S[k]={...(S[k]||{}), v:x.value}; save(); });
  m.querySelectorAll("[data-pn]").forEach(x=>x.oninput=()=>{ const k="person:"+x.dataset.pn; S[k]={...(S[k]||{}), note:x.value}; save(); });
  count(); }
document.getElementById("dl").onclick=()=>{ const rows=[["episode","key_index","time","text","key_says","verdict","correct_speaker","key_on_screen","my_on_screen","note"]];
  DATA.forEach(e=>{ const S=store[e.vid]||{}; e.people.forEach(p=>{ const s=S["person:"+p.name]; if(s) rows.push([e.vid,"person","",p.name,p.name,s.v||"","","","",s.note||""]); });
    e.rows.forEach(r=>{ const s=S[r.i]; if(!s) return;
    rows.push([e.vid,r.i,r.ts,r.text,r.who,s.v||"",s.v==="fix"?(s.to||""):"",(r.seen||[]).join("; "),s.seen?s.seen.join("; "):"",s.note||""]); }); });
  const csv=rows.map(r=>r.map(x=>`"${String(x).replace(/"/g,'""')}"`).join(",")).join("\n");
  const a=document.createElement("a"); a.href=URL.createObjectURL(new Blob(["\ufeff"+csv],{type:"text/csv"})); a.download="key_corrections.csv"; a.click(); };
render();
</script></body></html>"""


def main():
    out, eps = ("key_check.html", EPISODES) if len(sys.argv) < 3 else (sys.argv[1], [tuple(a.split("=", 1)) for a in sys.argv[2:]])
    data = [episode(v, t) for v, t in eps]
    (OUT / out).write_text(PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
    for e in data:
        print(e["title"], len(e["rows"]), "lines |", sum(1 for r in e["rows"] if r["conf"] and r["conf"] < 3), "low confidence |",
              sum(1 for r in e["rows"] if r["dis"]), "pipeline disagrees")
    print("wrote", OUT / out)


if __name__ == "__main__":
    main()
