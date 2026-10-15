"""Self-contained review page for preview clips (open review.html by double-clicking).

Video beside its transcript; clicking a line seeks the video, the playing line is
highlighted, cuts between pieces are shown inline. Ratings autosave in the browser
(localStorage, per run) and export with "Download ratings.csv".
"""
import json

TEMPLATE = r"""<!doctype html>
<html lang="te">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shotvi Clip Review</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Telugu:wght@400;600&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#f6f5f2; --panel:#ffffff; --ink:#1d1d1b; --muted:#6b6a66; --line:#e4e2dc;
  --accent:#c2410c; --accent-soft:#fdeee6; --good:#15803d; --bad:#b91c1c; --cut:#8a8780;
  --hl:#fff4d6; --chip:#efece6; --shadow:0 1px 2px rgba(0,0,0,.05),0 4px 16px rgba(0,0,0,.05);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#141412; --panel:#1d1c1a; --ink:#ecebe7; --muted:#a09e98; --line:#2e2d2a;
    --accent:#fb923c; --accent-soft:#3a2416; --good:#4ade80; --bad:#f87171; --cut:#77756f;
    --hl:#3a3220; --chip:#2a2926; --shadow:none;
  }
}
:root[data-theme="dark"]{
  --bg:#141412; --panel:#1d1c1a; --ink:#ecebe7; --muted:#a09e98; --line:#2e2d2a;
  --accent:#fb923c; --accent-soft:#3a2416; --good:#4ade80; --bad:#f87171; --cut:#77756f;
  --hl:#3a3220; --chip:#2a2926; --shadow:none;
}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.5 Inter,"Noto Sans Telugu",system-ui,-apple-system,"Segoe UI",sans-serif}
.te{font-family:"Noto Sans Telugu",Inter,system-ui,sans-serif}
header{position:sticky;top:0;z-index:5;display:flex;gap:12px;align-items:center;flex-wrap:wrap;
  padding:12px 20px;background:var(--panel);border-bottom:1px solid var(--line)}
header h1{font-size:16px;margin:0;font-weight:700;letter-spacing:-.01em}
header .run{color:var(--muted);font-size:13px}
header .sp{flex:1}
.progress{font-size:13px;color:var(--muted)}
.bar{width:120px;height:6px;background:var(--chip);border-radius:3px;overflow:hidden;display:inline-block;vertical-align:middle;margin-left:6px}
.bar i{display:block;height:100%;background:var(--good);width:0}
button{font:inherit;cursor:pointer}
.btn{border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:7px 12px;border-radius:8px;font-size:13px;font-weight:500}
.btn:hover{border-color:var(--muted)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.wrap{display:grid;grid-template-columns:280px 1fr;min-height:calc(100vh - 57px)}
nav{border-right:1px solid var(--line);background:var(--panel);overflow:auto;max-height:calc(100vh - 57px);position:sticky;top:57px}
nav h2{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:16px 16px 6px;font-weight:600}
nav a{display:flex;gap:10px;align-items:flex-start;padding:9px 16px;color:inherit;text-decoration:none;border-left:3px solid transparent}
nav a:hover{background:var(--bg)}
nav a.on{background:var(--accent-soft);border-left-color:var(--accent)}
nav .rk{font-weight:700;color:var(--muted);min-width:22px;font-variant-numeric:tabular-nums}
nav .tt{font-size:14px;line-height:1.35}
nav .st{margin-left:auto;font-size:12px;color:var(--good);white-space:nowrap}
main{padding:20px;min-width:0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}
.head{padding:16px 20px;border-bottom:1px solid var(--line)}
.head h2{margin:0 0 6px;font-size:20px;line-height:1.3}
.chips{display:flex;gap:6px;flex-wrap:wrap;font-size:12px}
.chip{background:var(--chip);padding:2px 8px;border-radius:999px;color:var(--muted)}
.chip.flag{background:var(--accent-soft);color:var(--accent)}
.chip.badge{background:#1f6f43;color:#fff;font-weight:600}
.chip.ret-yes{background:#15803d;color:#fff;font-weight:600}.chip.ret-maybe{background:#b45309;color:#fff;font-weight:600}
.chip.feel{background:#6d28d9;color:#fff;font-weight:600}
.chip.ret-no{background:#b91c1c;color:#fff;font-weight:600}.chip.ret-unchecked{background:#6b6a66;color:#fff;font-weight:600}
nav .sec{margin:10px 0 4px;padding:3px 8px;font-size:12px;font-weight:700;color:#fff;background:#1f6f43;border-radius:6px}
.meta{margin-top:10px;font-size:14px;display:grid;gap:4px}
.meta b{color:var(--muted);font-weight:600;margin-right:6px;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:0}
.player{padding:16px 20px;border-right:1px solid var(--line)}
video{width:100%;border-radius:8px;background:#000;display:block;aspect-ratio:16/9}
.navrow{display:flex;gap:8px;margin-top:10px;align-items:center}
.navrow .hint{font-size:12px;color:var(--muted);margin-left:auto}
.tx{padding:12px 8px 12px 12px;max-height:520px;overflow:auto}
.tx h3,.rate h3{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:4px 8px 8px;font-weight:600}
.ln{display:grid;grid-template-columns:52px 1fr;gap:8px;padding:6px 8px;border-radius:6px;cursor:pointer}
.ln:hover{background:var(--bg)}
.ln.now{background:var(--hl)}
.ln .t{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums;padding-top:2px}
.ln .t small{display:block;font-size:10.5px;opacity:.8}
.ln .s{font-weight:700;margin-right:6px;font-size:12px;color:var(--accent)}
.ln .s.H{color:#2563eb}
.cut{margin:2px 8px 2px 68px;font-size:12.5px;color:var(--cut);border-left:2px dashed var(--line);padding:2px 0 2px 10px}
.cut summary{cursor:pointer;list-style:none}
.cut summary::-webkit-details-marker{display:none}
.cut .ct{font-style:italic;margin-top:2px}
.jump{margin:4px 8px 4px 68px;font-size:12.5px;color:var(--accent)}
.rate{border-top:1px solid var(--line);padding:16px 20px;display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px 24px}
.q label{display:block;font-size:13px;font-weight:600;margin-bottom:6px}
.q label span{font-weight:400;color:var(--muted);font-size:12px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{border:0;background:var(--panel);color:var(--ink);padding:6px 12px;font-size:14px;border-right:1px solid var(--line);min-width:38px}
.seg button:last-child{border-right:0}
.seg button:hover{background:var(--bg)}
.seg button.on{background:var(--accent);color:#fff}
.seg.yn button.on[data-v="yes"]{background:var(--good)}
.seg.yn button.on[data-v="no"]{background:var(--bad)}
textarea{width:100%;min-height:64px;border:1px solid var(--line);border-radius:8px;padding:8px;background:var(--panel);color:var(--ink);font:inherit;font-size:14px;resize:vertical}
.notes{grid-column:1/-1}
.saved{font-size:12px;color:var(--good);opacity:0;transition:opacity .3s}
.saved.show{opacity:1}
@media (max-width:980px){
  .wrap{grid-template-columns:1fr}
  nav{position:static;max-height:none;border-right:0;border-bottom:1px solid var(--line)}
  .grid{grid-template-columns:1fr}
  .player{border-right:0;border-bottom:1px solid var(--line)}
  .tx{max-height:none}
}
@media (max-width:520px){ main{padding:12px 0} .card{border-radius:0;border-left:0;border-right:0} header{padding:10px 16px} }
</style>
</head>
<body>
<header>
  <h1>Shotvi · Clip Review</h1><span class="run" id="run"></span>
  <span class="sp"></span>
  <span class="progress" id="progress"></span>
  <button class="btn" id="theme" title="Toggle light/dark">◐</button>
  <button class="btn primary" id="dl">Download ratings.csv</button>
</header>
<div class="wrap">
  <nav id="nav"></nav>
  <main><div class="card" id="clip"></div></main>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const RUN = "__RUN__";
const DATA = JSON.parse(document.getElementById("data").textContent);
const KEY = "shotvi-ratings:" + RUN;
const QS = [["hook","Hook","grabs in the first 3s?"],["standalone","Stands alone","makes sense without the podcast?"],
            ["payoff","Payoff","ends on a real ending?"],["overall","Overall","as a Reel/Short"]];
const clips = [];
DATA.forEach(v => v.clips.forEach(c => clips.push({...c, video_id: v.video_id})));
const keyOf = c => c.video_id + "/" + c.id;

let store = {};
try { store = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { store = {}; }
function save() { try { localStorage.setItem(KEY, JSON.stringify(store)); } catch (e) {} flash(); progress(); navMarks(); }
function flash() { const s = document.querySelector(".saved"); if (!s) return; s.classList.add("show"); clearTimeout(s._t); s._t = setTimeout(() => s.classList.remove("show"), 900); }
const esc = s => String(s ?? "").replace(/[&<>"]/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[ch]));
const mmss = t => { t = Math.max(0, t || 0); const m = Math.floor(t / 60), s = Math.floor(t % 60); return m + ":" + String(s).padStart(2, "0"); };
const isDone = c => { const r = store[keyOf(c)] || {}; return r.overall && r.post; };

function progress() {
  const n = clips.filter(isDone).length;
  document.getElementById("progress").innerHTML = `${n} / ${clips.length} rated <span class="bar"><i style="width:${100 * n / clips.length}%"></i></span>`;
}
function navMarks() {
  document.querySelectorAll("nav a").forEach(a => {
    const c = clips[+a.dataset.i], r = store[keyOf(c)] || {};
    a.querySelector(".st").textContent = isDone(c) ? (r.post === "yes" ? "✓ post" : r.post === "no" ? "✗ skip" : "✓") : "";
  });
}
function buildNav() {
  let h = "", i = 0;
  DATA.forEach(v => {
    h += `<h2>${esc(v.video_id)} · top ${v.clips.length} of ${v.n_total}</h2>`;
    if (v.episode_note) h += `<div class="verdict">${esc(v.episode_note)}</div>`;
    if (v.verdict) h += `<div class="verdict">Episode verdict: ${esc(v.verdict.verdict)} — ${v.verdict.strong_clips} strong candidate${v.verdict.strong_clips === 1 ? "" : "s"}, ${v.verdict.worth_a_look} other candidate${v.verdict.worth_a_look === 1 ? "" : "s"}${v.verdict.unchecked ? `, ${v.verdict.unchecked} unchecked` : ""}. ${esc(v.verdict.note)}</div>`;
    let sec = "";
    v.clips.forEach(c => { if (c.section && c.section !== sec) h += `<div class="sec">${esc(c.section)}</div>`; sec = c.section || sec; h += `<a href="#" data-i="${i}"><span class="rk">#${c.rank}</span><span class="tt te">${esc(c.title)}</span><span class="st"></span></a>`; i++; });
  });
  const nav = document.getElementById("nav"); nav.innerHTML = h;
  nav.onclick = e => { const a = e.target.closest("a"); if (!a) return; e.preventDefault(); show(+a.dataset.i); };
  navMarks();
}

let cur = 0;
function show(i) {
  cur = Math.max(0, Math.min(clips.length - 1, i));
  const c = clips[cur], r = store[keyOf(c)] || {};
  document.querySelectorAll("nav a").forEach(a => a.classList.toggle("on", +a.dataset.i === cur));
  const chips = (c.feeling ? `<span class="chip feel">feeling: ${esc(c.feeling)}</span>` : "") + (c.retention ? `<span class="chip ret-${esc(c.retention)}">retention: ${esc(c.retention)}</span>` : "") + (c.badge ? `<span class="chip badge">${esc(c.badge)}</span>` : "") + [`${c.duration}s`, `${c.n_pieces} pieces`, `source ${mmss(c.src_start)}–${mmss(c.src_end)}`, c.category,
                 (c.score != null ? `pipeline ${c.score} · ${c.tier_label || c.tier}` : (c.tier_label || c.tier || null))].filter(Boolean).map(x => `<span class="chip">${esc(x)}</span>`).join("")
              + c.flags.map(f => `<span class="chip flag">${esc(f.replace(/_/g, " "))}</span>`).join("");
  let tx = "";
  c.lines.forEach((ln, k) => {
    if (ln.kind === "cut") {
      tx += `<details class="cut"><summary>✂ cut ${ln.dur}s — show words</summary><div class="ct te">${esc(ln.text)}</div></details>`;
    } else if (ln.kind === "jump") {
      tx += `<div class="jump">↺ jumps back to ${mmss(ln.src)} (reordered)</div>`;
    } else {
      tx += `<div class="ln" data-t="${ln.t}"><div class="t">${mmss(ln.t)}<small>@${mmss(ln.src)}</small></div>` +
            `<div class="te"><span class="s ${esc(ln.spk)}">${esc(ln.spk)}</span>${esc(ln.text)}</div></div>`;
    }
  });
  const seg = (q) => `<div class="seg" data-q="${q}">` + [1,2,3,4,5].map(n => `<button data-v="${n}" class="${r[q] == n ? "on" : ""}">${n}</button>`).join("") + `</div>`;
  const rate = QS.map(([q, lab, sub]) => `<div class="q"><label>${lab} <span>${sub}</span></label>${seg(q)}</div>`).join("") +
    `<div class="q"><label>Would you post it?</label><div class="seg yn" data-q="post">` +
    ["yes", "maybe", "no"].map(v => `<button data-v="${v}" class="${r.post === v ? "on" : ""}">${v}</button>`).join("") + `</div></div>` +
    `<div class="q notes"><label>Notes <span>what works, what's off (cut too early, missing context, weak ending…)</span> <span class="saved">saved</span></label>` +
    `<textarea id="notes" class="te" placeholder="optional">${esc(r.notes || "")}</textarea></div>`;
  document.getElementById("clip").innerHTML = `
    <div class="head">
      <h2 class="te">#${c.rank} ${esc(c.title)}</h2>
      <div class="chips">${chips}</div>
      <div class="meta">
        ${c.review_note ? `<div><b>Note</b>${esc(c.review_note)}</div>` : ""}
        ${c.retention ? `<div><b>Retention</b>${esc(c.retention)}${c.retention_reason ? ": " + esc(c.retention_reason) : ""}</div>` : ""}
        ${c.reason ? `<div><b>Reason</b>${esc(c.reason)}</div>` : ""}
        ${c.stop ? `<div><b>Stop</b><span class="te">${esc(c.stop)}</span></div>` : ""}
        ${c.stay ? `<div><b>Stay</b>${esc(c.stay)}</div>` : ""}
        ${c.land ? `<div><b>Land</b><span class="te">${esc(c.land)}</span></div>` : ""}
        <div><b>Hook</b><span class="te">${esc(c.hook)}</span></div>
        ${c.payoff ? `<div><b>Payoff</b><span class="te">${esc(c.payoff)}</span>${c.payoff_type ? ` <small>(${esc(c.payoff_type)})</small>` : ""}</div>` : ""}
        <div><b>Why</b>${esc(c.why)}</div>
        ${c.edit_notes.filter(Boolean).map(n => `<div><b>Edit</b>${esc(n)}</div>`).join("")}
      </div>
    </div>
    <div class="grid">
      <div class="player">
        <video id="v" controls preload="metadata" src="${esc(c.video)}"></video>
        <div class="navrow">
          <button class="btn" id="prev">← Prev</button><button class="btn" id="next">Next →</button>
          <span class="hint">click a transcript line to jump · ←/→ to switch clips</span>
        </div>
      </div>
      <div class="tx"><h3>Transcript (preview time · @episode time)</h3>${tx}</div>
    </div>
    <div class="rate">${rate}</div>`;
  const v = document.getElementById("v");
  document.querySelectorAll(".ln").forEach(el => el.onclick = () => { v.currentTime = +el.dataset.t; v.play(); });
  const lns = [...document.querySelectorAll(".ln")];
  v.ontimeupdate = () => {
    let on = -1;
    lns.forEach((el, k) => { if (+el.dataset.t <= v.currentTime + 0.05) on = k; });
    lns.forEach((el, k) => el.classList.toggle("now", k === on));
  };
  document.getElementById("prev").onclick = () => show(cur - 1);
  document.getElementById("next").onclick = () => show(cur + 1);
  document.querySelectorAll(".seg").forEach(s => s.onclick = e => {
    const b = e.target.closest("button"); if (!b) return;
    const rec = store[keyOf(c)] = store[keyOf(c)] || {};
    const val = s.dataset.q === "post" ? b.dataset.v : +b.dataset.v;
    rec[s.dataset.q] = rec[s.dataset.q] === val ? undefined : val;
    s.querySelectorAll("button").forEach(x => x.classList.toggle("on", String(rec[s.dataset.q]) === x.dataset.v));
    save();
  });
  const ta = document.getElementById("notes");
  ta.oninput = () => { (store[keyOf(c)] = store[keyOf(c)] || {}).notes = ta.value; clearTimeout(ta._t); ta._t = setTimeout(save, 400); };
  try { localStorage.setItem(KEY + ":cur", cur); } catch (e) {}
}

document.addEventListener("keydown", e => {
  if (e.target.matches("textarea,input")) return;
  if (e.key === "ArrowRight") show(cur + 1);
  if (e.key === "ArrowLeft") show(cur - 1);
});

document.getElementById("dl").onclick = () => {
  const cols = ["video_id","rank","clip_id","title","file","pipeline_overall","hook_1to5","standalone_1to5","payoff_1to5","overall_1to5","would_post","notes"];
  const q = x => { x = String(x ?? ""); return /[",\n]/.test(x) ? '"' + x.replace(/"/g, '""') + '"' : x; };
  const rows = clips.map(c => { const r = store[keyOf(c)] || {};
    return [c.video_id, c.rank, c.id, c.title, c.video, c.score, r.hook, r.standalone, r.payoff, r.overall, r.post, r.notes].map(q).join(","); });
  const blob = new Blob(["﻿" + cols.join(",") + "\n" + rows.join("\n") + "\n"], {type: "text/csv;charset=utf-8"});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "ratings.csv"; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
};

const root = document.documentElement;
try { const t = localStorage.getItem("shotvi-theme"); if (t) root.dataset.theme = t; } catch (e) {}
document.getElementById("theme").onclick = () => {
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("shotvi-theme", root.dataset.theme); } catch (e) {}
};

document.getElementById("run").textContent = "run " + RUN;
buildNav(); progress();
let start = 0; try { start = +(localStorage.getItem(KEY + ":cur") || 0); } catch (e) {}
show(start);
</script>
</body>
</html>
"""


# ASR correction marks: injected only when some line carries "parts" (preview_clips.transcript_lines with an ASR
# layer), so a page without them is byte-identical to the plain template.
ASR_CSS = r"""
:root{--fix:#0f766e;--fix-bg:#ddf3ef}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){--fix:#5eead4;--fix-bg:#13322e} }
:root[data-theme="dark"]{--fix:#5eead4;--fix-bg:#13322e}
.fix{color:var(--fix);background:var(--fix-bg);border-radius:3px;padding:0 2px;cursor:help;
  text-decoration:underline dotted 1.5px;text-underline-offset:3px}
.unsure{cursor:help}
.qm{color:var(--muted);font-size:.72em;opacity:.8;margin-left:1px;vertical-align:.15em}
.asrkey{display:block;margin:-4px 8px 8px;font-size:11.5px;color:var(--muted)}
.asrtip{position:fixed;z-index:50;display:none;max-width:300px;padding:7px 9px;border-radius:7px;font-size:12.5px;
  line-height:1.45;white-space:pre-line;background:var(--ink);color:var(--panel);box-shadow:0 4px 16px rgba(0,0,0,.2);
  pointer-events:none}
"""

ASR_JS = r"""
const asrTip = document.createElement("div"); asrTip.className = "asrtip te"; document.body.appendChild(asrTip);
function asrParts(ps) {
  return ps.map(p => p.k === "fix" ? `<span class="fix" tabindex="0" data-tip="${esc(p.tip)}">${esc(p.t)}</span>`
    : p.k === "u" ? `<span class="unsure" tabindex="0" data-tip="${esc(p.tip)}">${esc(p.t)}<span class="qm">(?)</span></span>`
    : esc(p.t)).join(" ");
}
const asrKey = c => c.lines.some(l => l.parts) ? `<span class="asrkey"><span class="fix">corrected</span> = ASR fix, hover or tap to see what Scribe heard · <span class="qm">(?)</span> = low ASR confidence</span>` : "";
function asrShow(el) {
  asrTip.textContent = el.dataset.tip; asrTip.style.display = "block";
  const r = el.getBoundingClientRect(), w = asrTip.offsetWidth, h = asrTip.offsetHeight;
  let y = r.bottom + 6; if (y + h > innerHeight - 8) y = r.top - h - 6;
  asrTip.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + "px"; asrTip.style.top = Math.max(8, y) + "px";
}
const asrHide = () => { asrTip.style.display = "none"; };
document.addEventListener("mouseover", e => { const el = e.target.closest && e.target.closest("[data-tip]"); if (el) asrShow(el); });
document.addEventListener("mouseout", e => { if (e.target.closest && e.target.closest("[data-tip]")) asrHide(); });
document.addEventListener("focusin", e => { if (e.target.dataset && e.target.dataset.tip) asrShow(e.target); });
document.addEventListener("focusout", asrHide);
document.addEventListener("scroll", asrHide, true);
// tap / click on a marked word shows its note and does not seek the video
document.addEventListener("click", e => { const el = e.target.closest && e.target.closest("[data-tip]");
  if (el) { e.stopPropagation(); asrShow(el); } else asrHide(); }, true);
"""

_ASR_SWAPS = [
    ("</style>", ASR_CSS + "</style>"),
    ("function buildNav() {", ASR_JS + "function buildNav() {"),
    ('<div class="te"><span class="s ${esc(ln.spk)}">${esc(ln.spk)}</span>${esc(ln.text)}</div></div>`;',
     '<div class="te"><span class="s ${esc(ln.spk)}">${esc(ln.spk)}</span>${ln.parts ? asrParts(ln.parts) : esc(ln.text)}</div></div>`;'),
    ('<div class="ct te">${esc(ln.text)}</div>', '<div class="ct te">${ln.parts ? asrParts(ln.parts) : esc(ln.text)}</div>'),
    ("<h3>Transcript (preview time · @episode time)</h3>${tx}", "<h3>Transcript (preview time · @episode time)</h3>${asrKey(c)}${tx}"),
]


def _has_parts(data):
    return any(ln.get("parts") for v in data for c in v.get("clips", []) for ln in c.get("lines", []))


def build_page(run_name, data):
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE
    if _has_parts(data):
        for a, b in _ASR_SWAPS:
            assert page.count(a) == 1, f"review page template changed; ASR marks cannot hook into {a!r}"
            page = page.replace(a, b)
    return page.replace("__DATA__", blob).replace("__RUN__", run_name.replace('"', ""))
