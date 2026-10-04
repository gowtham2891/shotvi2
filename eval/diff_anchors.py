"""Per-Short diff between two anchors files (e.g. before / after a mapper change).

usage: python -m eval.diff_anchors <old.json> <new.json>

Reports, per Short: kept episode words added / removed (with the words), piece count, edited duration,
n_blocks, and flags that appeared or disappeared. Shorts present in only one file are listed.
"""
import argparse

from .common import episode_for, kept_set, load_json, utf8_stdout


def diff(old, new):
    ep = episode_for(new)
    W = ep.W
    a = {c["id"]: c for c in old.get("clips", [])}
    b = {c["id"]: c for c in new.get("clips", [])}
    L = [f"## {new['video_id']}", ""]
    for sid in sorted(set(a) | set(b), key=str.lower):
        if sid not in b:
            um = next((u for u in new.get("unmapped", []) if u["short_id"] == sid), None)
            L.append(f"- **{sid}**: REMOVED from clips" + (f" (unmapped: {um['reason']})" if um else ""))
            continue
        if sid not in a:
            L.append(f"- **{sid}**: NEW clip")
            continue
        x, y = a[sid], b[sid]
        ka, kb = kept_set(x), kept_set(y)
        plus, minus = sorted(kb - ka), sorted(ka - kb)
        fa = set(x["mapping"].get("flags") or [])
        fb = set(y["mapping"].get("flags") or [])
        parts = []
        if plus:
            parts.append(f"+{len(plus)} words " + ", ".join(f"{j} {W[j]['word']}" for j in plus[:8]))
        if minus:
            parts.append(f"-{len(minus)} words " + ", ".join(f"{j} {W[j]['word']}" for j in minus[:8]))
        if len(x["pieces"]) != len(y["pieces"]):
            parts.append(f"pieces {len(x['pieces'])} -> {len(y['pieces'])}")
        if abs(x["edited_duration"] - y["edited_duration"]) > 0.005:
            parts.append(f"edited {x['edited_duration']:.2f}s -> {y['edited_duration']:.2f}s")
        if x["n_blocks"] != y["n_blocks"]:
            parts.append(f"n_blocks {x['n_blocks']} -> {y['n_blocks']}")
        for f in sorted(fb - fa):
            parts.append(f"new flag: {f[:140]}")
        for f in sorted(fa - fb):
            parts.append(f"flag gone: {f[:140]}")
        L.append(f"- **{sid}**: " + ("; ".join(parts) if parts else "unchanged"))
    for u in new.get("unmapped", []):
        L.append(f"- unmapped in new file: {u}")
    return "\n".join(L)


def main(argv=None):
    utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new")
    a = ap.parse_args(argv)
    print(diff(load_json(a.old), load_json(a.new)))


if __name__ == "__main__":
    main()
