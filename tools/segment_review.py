"""Segmentation review page: judge thought units, opening detection and Q->A links by eye.

usage: python tools/segment_review.py <transcript.json> [--segments runs/.../segments.json] [--intro rules|llm|off]
writes runs/segment_review/<video_id>/review.html (double-click to open)

- Video on the left (played from scribe_dataset by relative path, nothing copied or re-encoded).
- Unit list on the right: id, time, speaker labels, text; absorbed listener backchannels inline and greyed.
  Click a unit to seek; the playing unit is highlighted and followed.
- Teaser / music / intro ranges are tinted, with what decided them (LLM reason or repeat rules);
  Q->A links show as "Q ->" / "<- A" chips that jump to the other side; YouTube chapters are dividers.
- Per unit: "boundary wrong here" flag + note. Saved in the browser (localStorage);
  "Download notes.csv" exports them. Save the CSV under data/human/.

Without --segments it reuses runs/segment_r3|r2/<id>/segments.json if present, else segments the
transcript itself with --intro (default "rules": offline, no LLM spend).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shotvi.segment import load, segment  # noqa: E402
from shotvi.segment.chapters import load_chapters  # noqa: E402
from shotvi.segment.loader import video_id_from_path  # noqa: E402


def _segments(transcript: Path, seg_path: str | None, intro: str) -> tuple[dict, str]:
    vid = video_id_from_path(transcript)
    cands = [Path(seg_path)] if seg_path else [ROOT / "runs" / r / vid / "segments.json"
                                               for r in ("segment_r3", "segment_r2")]
    for p in cands:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")), str(p)
    if seg_path:
        raise SystemExit(f"segments file not found: {seg_path}")
    seg = segment(load(transcript), detect_intro=intro)
    return seg.to_dict(), f"computed now (--intro {intro})"


def build_data(transcript: Path, d: dict, source: str) -> dict:
    tw = json.loads(transcript.read_text(encoding="utf-8"))["word_timestamps"]
    label = {s["id"]: s["label"] for s in d["speakers"]}
    units = []
    for u in d["units"]:
        i0, i1 = u["words"]
        bc = {i for a, b in u["backchannel_words"] for i in range(a, b + 1)}
        runs, cur = [], None
        for i in range(i0, i1 + 1):
            w = tw[i]
            key = (label.get(w.get("speaker"), "O"), i in bc)
            if cur and cur[0] == key[0] and cur[1] == key[1]:
                cur[2].append(w["word"])
            else:
                cur = [key[0], key[1], [w["word"]]]
                runs.append(cur)
        units.append({"id": u["id"], "s": u["start"], "e": u["end"], "d": u["duration"],
                      "spk": label.get(u["speaker"], "O"), "kind": u["kind"],
                      "intro": u.get("intro_id"), "q": u["qa_question_of"], "a": u["qa_answer_of"],
                      "bb": u["boundary_before"],
                      "runs": [[r[0], r[1], " ".join(r[2])] for r in runs]})
    links = {l["id"]: {"q": l["question"]["unit_ids"], "a": l["answer"]["unit_ids"], "kind": l["kind"],
                       "end": l["end_reason"]} for l in d["qa_links"]}
    ranges = [{"id": r["id"], "kind": r["kind"], "s": r["start"], "e": r["end"], "words": r["words"],
               "conf": r["confidence"], "by": r["evidence"].get("decided_by", "rules"),
               "reason": r["evidence"].get("llm_reason") or "",
               "ev": {k: v for k, v in r["evidence"].items() if k not in ("llm_reason",)},
               "nfrag": len(r.get("fragments") or [])} for r in d.get("intro_ranges", [])]
    return {"vid": d["source"]["video_id"], "source": source, "host": d["host"], "opening": d.get("opening", {}),
            "speakers": d["speakers"], "units": units, "links": links, "ranges": ranges,
            "chapters": load_chapters(transcript) or [], "stats": d["stats"]}


TEMPLATE = r"""<!doctype html>
<html lang="te"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Segmentation review: __VID__</title>
<style>
:root{--bg:#f6f5f2;--panel:#fff;--ink:#1d1d1b;--muted:#6b6a66;--line:#e4e2dc;--accent:#c2410c;--hl:#fff4d6;
--teaser:#fde2e2;--intro:#e0ecff;--music:#efe2ff;--bc:#a8a59e;--chip:#efece6;--flag:#b91c1c}
@media (prefers-color-scheme: dark){:root{--bg:#141412;--panel:#1d1c1a;--ink:#ecebe7;--muted:#a09e98;--line:#2e2d2a;
--hl:#3a3220;--teaser:#3b2020;--intro:#1d2a40;--music:#2d2140;--bc:#77756f;--chip:#2a2926}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 Inter,"Noto Sans Telugu","Nirmala UI",system-ui,sans-serif}
header{position:sticky;top:0;z-index:5;display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:10px 16px;
background:var(--panel);border-bottom:1px solid var(--line)}
header h1{font-size:16px;margin:0}.muted{color:var(--muted);font-size:13px}.sp{flex:1}
.btn{border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:6px 11px;border-radius:8px;font:inherit;font-size:13px;cursor:pointer}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.wrap{display:grid;grid-template-columns:minmax(360px,42%) 1fr;gap:0}
.left{position:sticky;top:53px;align-self:start;padding:14px;max-height:calc(100vh - 53px);overflow:auto;border-right:1px solid var(--line)}
video{width:100%;background:#000;border-radius:8px;aspect-ratio:16/9}
.box{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-top:12px;font-size:13.5px}
.box h3{margin:0 0 6px;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.rg{padding:6px 8px;border-radius:6px;margin:6px 0;cursor:pointer}
.rg.teaser{background:var(--teaser)}.rg.intro{background:var(--intro)}.rg.music{background:var(--music)}
textarea{width:100%;min-height:60px;font:inherit;font-size:13px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);padding:6px}
.list{padding:8px 14px 60vh}
.u{display:grid;grid-template-columns:64px 1fr;gap:10px;padding:7px 8px;border-radius:8px;border-left:4px solid transparent;margin:2px 0}
.u:hover{background:var(--chip)}.u.now{background:var(--hl)}
.u.teaser{border-left-color:#e05a5a}.u.intro{border-left-color:#4b7bd8}.u.music{border-left-color:#8b5cf6}
.u.flagged{outline:2px solid var(--flag);outline-offset:-2px}
.u .t{font-size:12px;color:var(--muted);cursor:pointer;font-variant-numeric:tabular-nums}
.u .t b{display:block;color:var(--ink);font-size:13px}
.tx{cursor:pointer}.lb{font-weight:700;font-size:12px;color:var(--accent);margin-right:4px}
.bc{color:var(--bc);font-size:.92em}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px;font-size:12px;align-items:center}
.chip{background:var(--chip);padding:1px 8px;border-radius:999px;color:var(--muted);cursor:default}
.chip.go{cursor:pointer;color:var(--accent)}
.chip.kind{background:transparent;border:1px solid var(--line)}
.fl{display:flex;gap:6px;align-items:center;font-size:12px;color:var(--muted)}
.note{flex:1;min-width:160px;font:inherit;font-size:12.5px;border:1px solid var(--line);border-radius:6px;padding:2px 6px;background:var(--bg);color:var(--ink)}
.div{margin:16px 0 6px;padding:4px 8px;font-size:12px;font-weight:600;color:var(--muted);border-top:2px solid var(--line)}
.div.rgd{color:var(--ink);border-top-color:#e05a5a}
.saved{opacity:0;transition:opacity .3s;color:#15803d;font-size:12px}.saved.show{opacity:1}
</style></head><body>
<header><h1>Segmentation review &middot; __VID__</h1><span class="muted" id="sum"></span><span class="sp"></span>
<label class="muted"><input type="checkbox" id="follow" checked> follow video</label>
<span class="saved">saved</span><span class="muted" id="cnt"></span>
<button class="btn primary" id="dl">Download notes.csv</button></header>
<div class="wrap"><div class="left">
<video id="v" controls preload="metadata" src="__VIDEO__"></video>
<div class="muted" id="vmsg"></div>
<div class="box"><h3>Opening</h3><div id="opening"></div></div>
<div class="box"><h3>How to review</h3>Click a unit's text or time to play it. Tick <b>boundary wrong</b> on a unit
when its <i>start</i> is in the wrong place (split too early/late, or should be merged with the unit above), and say why in the note.
Grey text in brackets is a listener backchannel absorbed into the speaker's turn. Red/blue/purple bars mark teaser / intro / opening music.
<b>Q&rarr;</b> jumps to the answer of a question; <b>&larr;A</b> back to its question.</div>
<div class="box"><h3>General notes</h3><textarea id="gen" placeholder="Anything about this episode's segmentation..."></textarea></div>
<div class="box"><h3>Stats</h3><div id="stats" class="muted"></div></div>
</div><div class="list" id="list"></div></div>
<script>
const D = __DATA__;
const KEY = "shotvi-segreview:" + D.vid;
let store = {}; try { store = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
store.units = store.units || {};
const v = document.getElementById("v");
v.onerror = () => { document.getElementById("vmsg").textContent = "Video not found at " + v.getAttribute("src") + " (open the page from its folder)."; };
const fmt = t => { t = Math.max(0, t); const m = Math.floor(t / 60), s = Math.floor(t % 60); return m + ":" + String(s).padStart(2, "0"); };
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const rangeById = {}; D.ranges.forEach(r => rangeById[r.id] = r);
function save() { try { localStorage.setItem(KEY, JSON.stringify(store)); } catch (e) {} const s = document.querySelector(".saved"); s.classList.add("show"); clearTimeout(s._t); s._t = setTimeout(() => s.classList.remove("show"), 800); count(); }
function count() { const n = Object.values(store.units).filter(x => x.flag).length; const m = Object.values(store.units).filter(x => x.note).length; document.getElementById("cnt").textContent = n + " flagged, " + m + " notes"; }
function seek(t) { v.currentTime = Math.max(0, t - 0.2); v.play().catch(() => {}); }
function jump(uid) { const el = document.getElementById("u" + uid); if (!el) return; el.scrollIntoView({block: "center"}); seek(+el.dataset.s); }

// opening panel
const op = D.opening || {};
let oh = "<div class='muted'>mode <b>" + esc(op.mode || "?") + "</b>, decided by <b>" + esc(op.decided_by || "?") + "</b>" +
  (op.content_start_word != null ? ", content starts at word " + op.content_start_word : "") + "</div>";
if (op.reason) oh += "<div>LLM: " + esc(op.reason) + "</div>";
if (op.fallback_reason) oh += "<div class='muted'>LLM not used: " + esc(op.fallback_reason) + "</div>";
D.ranges.forEach(r => {
  oh += "<div class='rg " + r.kind + "' onclick='seek(" + r.s + ")'><b>" + r.kind + "</b> " + fmt(r.s) + "&ndash;" + fmt(r.e) +
    " &middot; words " + r.words[0] + "&ndash;" + r.words[1] + " &middot; conf " + r.conf + " &middot; by " + esc(r.by) +
    (r.nfrag ? " &middot; " + r.nfrag + " excerpts recur later" : "") +
    (r.reason ? "<div class='muted'>" + esc(r.reason) + "</div>" : "") +
    (r.ev && r.ev.cues ? "<div class='muted'>cues: " + esc(r.ev.cues.join(", ")) + "</div>" : "") + "</div>";
});
if (!D.ranges.length) oh += "<div class='muted'>No teaser / intro detected.</div>";
if (D.chapters.length) oh += "<div class='muted'>YouTube chapters: " + D.chapters.slice(0, 3).map(c => esc(c.title) + " " + fmt(c.start)).join(" &middot; ") + " ...</div>";
document.getElementById("opening").innerHTML = oh;
const st = D.stats; const hostLbl = D.host && D.host.speaker ? D.host.speaker + " (conf " + D.host.confidence + ")" : "none";
document.getElementById("sum").textContent = D.units.length + " units, " + Object.keys(D.links).length + " Q→A links, host " + hostLbl;
document.getElementById("stats").innerHTML = "segments: " + esc(D.source) + "<br>speakers: " + D.speakers.map(s => s.label + "=" + esc(s.id) + " " + Math.round(100 * s.talk_share) + "%").join(", ") +
  "<br>unit duration p10/p50/p90: " + st.unit_duration.p10 + " / " + st.unit_duration.p50 + " / " + st.unit_duration.p90 + " s" +
  "<br>backchannels absorbed: " + st.n_backchannels_absorbed;

// unit list
const list = document.getElementById("list");
let ch = 0, lastRange = null; const parts = [];
D.units.forEach(u => {
  while (ch < D.chapters.length && D.chapters[ch].start <= u.s + 0.5) {
    parts.push("<div class='div'>&#9654; Chapter: " + esc(D.chapters[ch].title) + " (" + fmt(D.chapters[ch].start) + ")</div>"); ch++; }
  const r = u.intro != null ? rangeById[u.intro] : null;
  if (r && lastRange !== r.id) parts.push("<div class='div rgd'>" + r.kind.toUpperCase() + " " + fmt(r.s) + "&ndash;" + fmt(r.e) + " (by " + esc(r.by) + ", conf " + r.conf + ")</div>");
  if (!r && lastRange != null) parts.push("<div class='div'>content</div>");
  lastRange = r ? r.id : null;
  const tx = u.runs.map(x => x[1] ? "<span class='bc'>[" + esc(x[0]) + ": " + esc(x[2]) + "]</span>" : "<span class='lb'>" + esc(x[0]) + "</span>" + esc(x[2])).join(" ");
  const qa = u.q.map(id => "<span class='chip go' title='jump to the answer' onclick='jump(" + D.links[id].a[0] + ")'>Q&rarr; U" + D.links[id].a[0] + " (" + D.links[id].kind + ")</span>").join("") +
    u.a.filter(id => D.links[id].a[0] === u.id).map(id => "<span class='chip go' title='jump to the question' onclick='jump(" + D.links[id].q[0] + ")'>&larr;A of U" + D.links[id].q[0] + "</span>").join("");
  const sv = store.units[u.id] || {};
  parts.push("<div class='u " + (r ? r.kind : "") + (sv.flag ? " flagged" : "") + "' id='u" + u.id + "' data-s='" + u.s + "' data-e='" + u.e + "'>" +
    "<div class='t' onclick='seek(" + u.s + ")'><b>U" + u.id + "</b>" + fmt(u.s) + "<br>" + u.d.toFixed(1) + "s</div>" +
    "<div><div class='tx te' onclick='seek(" + u.s + ")'>" + tx + "</div><div class='chips'>" +
    "<span class='chip kind'>" + u.kind + "</span>" + qa +
    "<label class='fl'><input type='checkbox' data-u='" + u.id + "' class='flag'" + (sv.flag ? " checked" : "") + "> boundary wrong here</label>" +
    "<input class='note' data-u='" + u.id + "' placeholder='note' value='" + esc(sv.note || "") + "'></div></div></div>");
});
list.innerHTML = parts.join("");
list.addEventListener("change", e => { const t = e.target; if (!t.dataset.u) return; const id = t.dataset.u;
  const s = store.units[id] = store.units[id] || {};
  if (t.classList.contains("flag")) { s.flag = t.checked; document.getElementById("u" + id).classList.toggle("flagged", t.checked); }
  if (t.classList.contains("note")) s.note = t.value; save(); });
const gen = document.getElementById("gen"); gen.value = store.general || ""; gen.onchange = () => { store.general = gen.value; save(); };

// follow playback
const els = [...document.querySelectorAll(".u")]; const starts = els.map(e => +e.dataset.s); let cur = -1;
v.ontimeupdate = () => { const t = v.currentTime; let lo = 0, hi = starts.length - 1, k = -1;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (starts[m] <= t + 0.05) { k = m; lo = m + 1; } else hi = m - 1; }
  if (k !== cur) { if (cur >= 0) els[cur].classList.remove("now"); cur = k; if (k >= 0) { els[k].classList.add("now");
    if (document.getElementById("follow").checked) els[k].scrollIntoView({block: "center", behavior: "smooth"}); } } };

document.getElementById("dl").onclick = () => {
  const q = s => '"' + String(s == null ? "" : s).replace(/"/g, '""') + '"';
  const rows = [["video_id", "unit_id", "start", "end", "intro_kind", "boundary_wrong", "note", "text"].join(",")];
  D.units.forEach(u => { const s = store.units[u.id]; if (!s || (!s.flag && !s.note)) return;
    const r = u.intro != null ? rangeById[u.intro].kind : "";
    rows.push([q(D.vid), u.id, u.s, u.e, q(r), s.flag ? 1 : 0, q(s.note), q(u.runs.filter(x => !x[1]).map(x => x[2]).join(" ").slice(0, 160))].join(",")); });
  if (store.general) rows.push([q(D.vid), q("general"), "", "", "", "", q(store.general), ""].join(","));
  const blob = new Blob(["﻿" + rows.join("\n") + "\n"], {type: "text/csv;charset=utf-8"});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "segment_notes_" + D.vid + ".csv"; a.click(); };
count();
</script></body></html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("transcript")
    ap.add_argument("--segments", help="segments.json (default: runs/segment_r3|r2/<id>/segments.json)")
    ap.add_argument("--intro", choices=("rules", "llm", "off"), default="rules",
                    help="opening mode when segmenting here (default rules: offline, no spend)")
    ap.add_argument("--out", help="output dir (default runs/segment_review/<id>)")
    a = ap.parse_args(argv)
    tp = Path(a.transcript).resolve()
    d, source = _segments(tp, a.segments, a.intro)
    data = build_data(tp, d, source)
    vid = data["vid"]
    out = Path(a.out) if a.out else ROOT / "runs" / "segment_review" / vid
    out.mkdir(parents=True, exist_ok=True)
    video = tp.parent / f"{vid}.mp4"
    rel = os.path.relpath(video, out).replace(os.sep, "/")
    page = (TEMPLATE.replace("__VID__", html.escape(vid)).replace("__VIDEO__", quote(rel))
            .replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")))
    (out / "review.html").write_text(page, encoding="utf-8")
    print(f"wrote {out / 'review.html'} ({len(page) // 1024} KB; video {'found' if video.exists() else 'MISSING'}: {rel}; "
          f"segments: {source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
