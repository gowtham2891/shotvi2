"""Validate a Shotvi clip file (proposals / gold / anchors) against docs/spec.

usage: python -m eval.schema_check <file.json> [...]

Checks: JSON Schema (docs/spec/clip_proposal.schema.json), transcript sha256 pin, span bounds, seconds derived
from word indices (within 0.01s), and derived fields (region, edited_duration, reordered, n_blocks) recomputed
as docs/spec/output_schema.md defines them. Proposal-only rules (hook_line, edit_notes, ranks) are TODO.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_S = 30.0   # clip_spec v0.2 S4; schema 1.1 default when derived_params.skip_s is absent (1.0 files: 20)
SCHEMA = ROOT / "docs" / "spec" / "clip_proposal.schema.json"
TIME_TOL = 0.01


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_span(where, sp, W, errs):
    a, b = sp["start_word"], sp["end_word"]
    if not (0 <= a <= b < len(W)):
        errs.append(f"{where}: bad word range {a}-{b} (n_words {len(W)})")
        return False
    if abs(sp["start"] - W[a]["start"]) > TIME_TOL or abs(sp["end"] - W[b]["end"]) > TIME_TOL:
        errs.append(f"{where}: seconds {sp['start']}-{sp['end']} do not match words "
                    f"({W[a]['start']}-{W[b]['end']})")
    return True


def skip_s_of(doc):
    """output_schema 1.1: header derived_params.skip_s; default 30s for 1.1+, 20s for 1.0 files."""
    dp = doc.get("derived_params") or {}
    if dp.get("skip_s") is not None:
        return float(dp["skip_s"])
    return 20.0 if str(doc.get("schema_version", "1.0")) == "1.0" else SKIP_S


def check_clip_core(where, clip, W, errs, skip_s=30.0):
    pieces = clip["pieces"]
    if not pieces:
        errs.append(f"{where}: no pieces")
        return
    # check every span (a list, not all(): all() would stop at the first bad span and hide the others)
    ok = [_check_span(f"{where}.pieces[{k}]", p, W, errs) for k, p in enumerate(pieces)]
    if not all(ok):
        return
    lo = min(p["start_word"] for p in pieces)
    hi = max(p["end_word"] for p in pieces)
    r = clip["region"]
    if (r["start_word"], r["end_word"]) != (lo, hi):
        errs.append(f"{where}.region {r['start_word']}-{r['end_word']} != derived {lo}-{hi}")
    _check_span(f"{where}.region", r, W, errs)
    edited = sum(W[p["end_word"]]["end"] - W[p["start_word"]]["start"] for p in pieces)
    if abs(clip["edited_duration"] - edited) > TIME_TOL:
        errs.append(f"{where}.edited_duration {clip['edited_duration']} != derived {edited:.3f}")
    reordered = any(b["start_word"] <= a["end_word"] for a, b in zip(pieces, pieces[1:]))
    if clip["reordered"] != reordered:
        errs.append(f"{where}.reordered {clip['reordered']} != derived {reordered}")
    jumps = sum(1 for a, b in zip(pieces, pieces[1:])
                if b["start_word"] <= a["end_word"] or W[b["start_word"]]["start"] - W[a["end_word"]]["end"] > skip_s)
    if clip["n_blocks"] != 1 + jumps:
        errs.append(f"{where}.n_blocks {clip['n_blocks']} != derived {1 + jumps}")


def check_doc(doc, base=ROOT):
    errs = []
    if not isinstance(doc, dict) or doc.get("kind") not in ("proposals", "gold", "anchors"):
        return ["not a Shotvi clip file (kind must be proposals, gold or anchors)"]
    try:
        import jsonschema
    except ImportError:
        errs.append("jsonschema not installed (pip install -r requirements.txt): JSON Schema not checked")
    else:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        # validate against the definition for this file's kind so errors name the failing field
        # (the top-level oneOf only reports "not valid under any of the given schemas")
        sub = {"proposals": "proposalsFile", "gold": "goldFile", "anchors": "anchorsFile"}.get(doc.get("kind"))
        if sub:
            schema = {"$schema": schema["$schema"], "$ref": f"#/$defs/{sub}", "$defs": schema["$defs"]}
        v = jsonschema.Draft202012Validator(schema)
        for e in sorted(v.iter_errors(doc), key=lambda e: list(e.path)):
            errs.append(f"schema: {'/'.join(map(str, e.path)) or '<root>'}: {e.message[:300]}")
    t = doc.get("transcript") or {}
    if not t.get("path"):
        errs.append("transcript.path missing")
        return errs
    tp = Path(t["path"])
    if not tp.is_absolute():
        tp = base / tp
    if not tp.exists():
        errs.append(f"transcript not found: {tp}")
        return errs
    if _sha256(tp) != t.get("sha256"):
        errs.append("transcript sha256 does not match the file on disk: indices are stale, remap")
    W = json.loads(tp.read_text(encoding="utf-8"))["word_timestamps"]
    if t.get("n_words") != len(W):
        errs.append(f"transcript.n_words {t.get('n_words')} != {len(W)}")
    ids = set()
    for key in ("clips", "proposals", "rejected"):
        for k, clip in enumerate(doc.get(key) or []):
            where = f"{key}[{k}]({clip.get('id')})"
            if clip.get("id") in ids:
                errs.append(f"{where}: duplicate id")
            ids.add(clip.get("id"))
            try:
                check_clip_core(where, clip, W, errs, skip_s_of(doc))
            except (KeyError, TypeError) as e:
                errs.append(f"{where}: cannot check core fields ({e})")
    return errs


def check_file(path):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"cannot read JSON: {e}"]
    return check_doc(doc)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    for p in argv:
        errs = check_file(p)
        print(f"{p}: {'OK' if not errs else str(len(errs)) + ' problem(s)'}")
        for e in errs:
            print("  -", e)
        bad += bool(errs)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
