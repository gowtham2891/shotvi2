"""Before / after table of two emotion_pick runs (same episodes, e.g. runs/emotion1 vs runs/emotion2/code_only).

Per episode, every clip id of either run: start_line, end_line, stop.line (+ case, hook_fix), land.line, title, play length
and status (shown / rejected with its code), before -> after, marked KEPT NOW (dropped before), DROPPED NOW (shown
before), CHANGED (boundaries / stop / land / length) or same. Reads only the two run documents.

    python tools/emotion_pick_diff.py runs/emotion1 runs/emotion2/code_only [--out runs/emotion2/code_only/diff.md]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def rows_of(doc: dict) -> dict:
    out = {}
    for p in doc.get("proposals") or []:
        out[p["id"]] = {"status": "shown", "start": p.get("start_line"), "end": p.get("end_line"),
                        "stop": (p.get("stop") or {}).get("line"), "case": (p.get("stop") or {}).get("case"),
                        "hook_fix": (p.get("stop") or {}).get("hook_fix"),
                        "cold": bool(p.get("cold_open")), "land": (p.get("land") or {}).get("line"),
                        "title": p.get("title") or "", "plays": p.get("video_duration"),
                        "moment": (p.get("moment") or {}).get("line"), "flags": p.get("build_flags") or []}
    for r in doc.get("rejected") or []:
        bm = r.get("build_moment") or {}
        out[r["id"]] = {"status": "rejected " + r["reject_reasons"][0].split(":")[0],
                        "reason": r["reject_reasons"][0],
                        "start": r.get("start_line") or bm.get("start_line"),
                        "end": r.get("end_line") or bm.get("end_line"),
                        "stop": (r.get("stop") or {}).get("line"), "case": (r.get("stop") or {}).get("case"),
                        "cold": bool(r.get("cold_open")), "land": (r.get("land") or {}).get("line"),
                        "title": r.get("title") or "", "plays": r.get("video_duration"),
                        "moment": (r.get("moment") or {}).get("line"), "flags": []}
    return out


def cell(r: dict | None) -> str:
    if r is None:
        return "-"
    pl = f"{r['plays']:.1f}s" if isinstance(r.get("plays"), (int, float)) else "-"
    case = r.get("case") or ""
    if r.get("hook_fix") and r["hook_fix"] != case:
        case = f"{case}; {r['hook_fix']}" if case else r["hook_fix"]
    stop = (r.get("stop") or "-") + (" (cold open)" if r.get("cold") else "") + (f" [{case}]" if case else "")
    return f"{r.get('start') or '-'}..{r.get('end') or '-'} | stop {stop} | land {r.get('land') or '-'} | {pl} | {r['status']}"


def mark(a: dict | None, b: dict | None) -> str:
    if a is None or b is None:
        return "only before" if b is None else "only after"
    sa, sb = a["status"] == "shown", b["status"] == "shown"
    if sb and not sa:
        return "KEPT NOW (dropped before)"
    if sa and not sb:
        return "DROPPED NOW (shown before)"
    keys = ("start", "end", "stop", "land", "plays", "cold", "status")
    diff = [k for k in keys if a.get(k) != b.get(k)]
    return ("CHANGED: " + ", ".join(diff)) if diff else "same"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    B, A = Path(a.before), Path(a.after)
    lines = [f"# {B} vs {A}\n"]
    for f in sorted(A.glob("*.json")):
        if f.name.startswith("dry_run"):
            continue
        pb = B / f.name
        if not pb.exists():
            continue
        db, da = (json.loads(x.read_text(encoding="utf-8")) for x in (pb, f))
        rb, ra = rows_of(db), rows_of(da)
        sb = sum(1 for r in rb.values() if r["status"] == "shown")
        sa = sum(1 for r in ra.values() if r["status"] == "shown")
        lines.append(f"\n## {f.stem}: shown {sb} -> {sa}\n")
        lines.append("| clip | moment | before: start..end, stop, land, plays, status | after | change | title (after) |")
        lines.append("|---|---|---|---|---|---|")
        ids = sorted(set(rb) | set(ra))
        for i in ids:
            x, y = rb.get(i), ra.get(i)
            t = (y or x)["title"].replace("|", "/")
            mo = (y or x).get("moment") or "-"
            lines.append(f"| {i} | {mo} | {cell(x)} | {cell(y)} | {mark(x, y)} | {t} |")
        notes = [(i, ra[i]) for i in ids if i in ra and (ra[i].get("flags") or ra[i].get("reason"))
                 and mark(rb.get(i), ra[i]) != "same"]
        for i, r in notes:
            if r.get("reason"):
                lines.append(f"\n- {i}: {r['reason']}")
            for fl in r.get("flags") or []:
                lines.append(f"\n- {i}: {fl}")
    text = "\n".join(lines) + "\n"
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
