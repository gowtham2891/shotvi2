"""Upgrade a schema-1.0 gold / anchors / proposals file to 1.1 (spec v0.2): derived_params.skip_s = 30 and
n_blocks re-derived at 30s. Pieces, region, edited_duration and reordered do not change.

usage: python -m eval.upgrade_schema <file.json> [...] --out-dir runs/eval_upgraded

Writes upgraded copies (never in place) and validates them with eval.schema_check. The owner of data/ copies
them over. Skip notes in gold files that no longer correspond to a skip at 30s are reported, not deleted
(the owner decides: a 20-30s gap is now a tightening cut).
"""
import argparse
from pathlib import Path

from .common import load_json, write_json
from .schema_check import check_doc

SKIP_S = 30.0


def upgrade(doc):
    from .common import episode_for
    ep = episode_for(doc)
    W = ep.W
    notes = []
    doc = dict(doc)
    doc["schema_version"] = "1.1"
    doc["derived_params"] = {"skip_s": SKIP_S}
    order = ["schema_version", "kind", "video_id", "transcript", "derived_params"]
    doc = {k: doc[k] for k in order if k in doc} | {k: v for k, v in doc.items() if k not in order}
    for key in ("clips", "proposals", "rejected"):
        for c in doc.get(key) or []:
            P = c["pieces"]
            skips = [k for k, (a, b) in enumerate(zip(P, P[1:]))
                     if b["start_word"] > a["end_word"] and W[b["start_word"]]["start"] - W[a["end_word"]]["end"] > SKIP_S]
            back = [k for k, (a, b) in enumerate(zip(P, P[1:])) if b["start_word"] <= a["end_word"]]
            nb = 1 + len(skips) + len(back)
            if nb != c["n_blocks"]:
                notes.append(f"{c['id']}: n_blocks {c['n_blocks']} -> {nb}")
                c["n_blocks"] = nb
            en = c.get("edit_notes") or []
            n_skip_notes = sum(1 for n in en if n.get("type") == "skip")
            if en and n_skip_notes != len(skips):
                notes.append(f"{c['id']}: {n_skip_notes} skip notes but {len(skips)} skips at {SKIP_S:.0f}s "
                             f"(owner to decide which note to keep)")
    return doc, notes


def main(argv=None):
    from .common import utf8_stdout
    utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out-dir", default="runs/eval_upgraded")
    a = ap.parse_args(argv)
    for f in a.files:
        doc = load_json(f)
        if doc.get("schema_version") != "1.0":
            print(f"{f}: already {doc.get('schema_version')}, skipped")
            continue
        new, notes = upgrade(doc)
        out = Path(a.out_dir) / Path(f).parent.name / Path(f).name
        write_json(out, new)
        errs = check_doc(new)
        print(f"{f} -> {out}: {'valid' if not errs else str(len(errs)) + ' problem(s)'}")
        for n in notes + errs:
            print("  -", n)


if __name__ == "__main__":
    main()
