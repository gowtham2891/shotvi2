"""Speaker-label layer: who speaks each Scribe word and line, from a chosen SOURCE, as ANONYMOUS labels rendered
exactly like Scribe's (``speaker_0``, ``speaker_1``, ...). An additive layer; the Scribe transcript is never modified
(founder's speaker-source experiment, 2026-10-23: the same clip selection with three label sources).

Sources:
  scribe   Scribe's own diarization labels, unchanged (identity mapping; the default everywhere).
  sarvam   Sarvam diarization (``scribe_dataset/<id>/<id>_sarvam_raw.json``, ``tools/transcribe_sarvam.py``): each
           Scribe word takes the ``speaker_id`` of the diarized segment it overlaps most in time; a word overlapping
           no segment takes the NEAREST segment when the gap is <= ``GAP_S`` (0.5 s), else it keeps its Scribe label.
  llm_fix  the Gemini speaker fix (``runs/speaker_fix/<id>.json``, ``python -m shotvi.segment.speaker_fix``): every
           word of a line takes that line's cast KEY (never the name); a line the fix left unknown (``X`` / missing)
           or that does not match the transcript keeps its Scribe label.

Lines = Scribe ``sentences[]`` (line structure and ids never change). A line's label is the MAJORITY of its words'
labels (ties: more speech time, then the earlier word). A line whose words carry 2+ labels is recorded in
``split`` (label counts and the word runs) for later; it is not split.

Anonymous ids: each source id (``sarvam:<speaker_id>``, ``llm_fix:<key>``; a word that kept its Scribe label is
``scribe:<label>``, a speaker of its own, since Scribe's numbering is unrelated to the source's) is mapped to
``speaker_<n>`` in order of first appearance over the LINE labels in transcript order, then any id that only labels
a minority of some line's words, in word order. The table is in ``mapping``. Source ``scribe`` keeps Scribe's own
strings (no renumbering), so it is byte-identical to the transcript.

Output ``runs/speaker_layer/<id>_<source>.json`` (schema speaker_layer/1)::

    {"schema", "video_id", "source", "source_file", "source_sha8", "transcript", "transcript_sha8", "params",
     "mapping": [{label, source_id, n_lines, n_words}], "n_speakers",
     "words": [label per Scribe word], "word_via": {overlap, nearest, scribe_fallback, line},
     "lines": [{sentence, words: [i0, i1], start, speaker, scribe, split?: {counts, runs: [[i0, i1, label]]}}],
     "stats": {n_lines, n_split_lines, n_lines_changed_vs_scribe_partition, unmatched_lines}}

CLI (free, no API call)::

    python -m shotvi.segment.speaker_layer p3XTZlZ6-xw --source sarvam [--show 6:00-7:00]
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "speaker_layer/1"
SOURCES = ("scribe", "sarvam", "llm_fix")
OUT_DIR = ROOT / "runs" / "speaker_layer"
GAP_S = 0.5


class LayerMissing(FileNotFoundError):
    """The source file of a speaker layer does not exist yet (Sarvam job / speaker_fix not run)."""


# ------------------------------------------------------------------------------------------------ paths
def transcript_path(vid: str) -> Path:
    return ROOT / "scribe_dataset" / vid / f"{vid}_scribe_transcript.json"


def sarvam_raw_path(vid: str) -> Path:
    return ROOT / "scribe_dataset" / vid / f"{vid}_sarvam_raw.json"


def speaker_fix_path(vid: str) -> Path:
    return ROOT / "runs" / "speaker_fix" / f"{vid}.json"


def source_path(vid: str, source: str) -> Path:
    if source == "sarvam":
        return sarvam_raw_path(vid)
    if source == "llm_fix":
        return speaker_fix_path(vid)
    if source == "scribe":
        return transcript_path(vid)
    raise ValueError(f"speaker source {source!r}: expected one of {SOURCES}")


def layer_path(vid: str, source: str) -> Path:
    return OUT_DIR / f"{vid}_{source}.json"


def _sha8(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:8]


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(p)


# ------------------------------------------------------------------------------------------------ word mapping
def sarvam_segments(raw: dict) -> list[dict]:
    """Sarvam's diarized entries -> [{start, end, id}] (time order; empty / inverted entries dropped)."""
    out = []
    for e in ((raw.get("diarized_transcript") or {}).get("entries")) or []:
        try:
            s, t = float(e["start_time_seconds"]), float(e["end_time_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        if t <= s:
            continue
        out.append({"start": s, "end": t, "id": str(e.get("speaker_id"))})
    out.sort(key=lambda x: (x["start"], x["end"]))
    return out


def words_to_segments(words: list[dict], segs: list[dict], gap_s: float = GAP_S) -> tuple[list[str], list[str]]:
    """Per Scribe word: (raw source id, how). The segment with the largest time overlap wins (ties: the segment whose
    centre is nearer the word's centre, then the earlier one); no overlap (a gap, or a zero-length word): the nearest
    segment when the distance is <= ``gap_s``; else ``scribe:<the word's Scribe label>``.
    how = "overlap" | "nearest" | "scribe_fallback"."""
    starts = [s["start"] for s in segs]
    pmax, m = [], float("-inf")          # running max of segment ends (segments may overlap)
    for s in segs:
        m = max(m, s["end"])
        pmax.append(m)
    ids, how = [], []
    for w in words:
        a, b = float(w["start"]), float(w["end"])
        k = bisect.bisect_right(starts, b + gap_s) - 1
        best, best_key = None, None
        near, near_d = None, None
        c = (a + b) / 2
        while k >= 0 and pmax[k] >= a - gap_s:
            s = segs[k]
            ov = min(b, s["end"]) - max(a, s["start"])
            if ov > 1e-9:
                key = (ov, -abs((s["start"] + s["end"]) / 2 - c), -s["start"])
                if best_key is None or key > best_key:
                    best, best_key = s, key
            else:
                d = max(s["start"] - b, a - s["end"], 0.0)
                dk = (d, abs((s["start"] + s["end"]) / 2 - c), s["start"])
                if near_d is None or dk < near_d:
                    near, near_d = s, dk
            k -= 1
        if best is not None:
            ids.append(f"sarvam:{best['id']}")
            how.append("overlap")
        elif near is not None and near_d[0] <= gap_s + 1e-9:
            ids.append(f"sarvam:{near['id']}")
            how.append("nearest")
        else:
            ids.append(f"scribe:{w.get('speaker', '?')}")
            how.append("scribe_fallback")
    return ids, how


def llm_fix_word_ids(tr: dict, fix: dict, log=None) -> tuple[list[str], list[str], int]:
    """Per Scribe word: (raw id, how) from the speaker fix's per-line cast key; a line with no key (X / missing) or
    no matching fix line (words range and start) keeps Scribe's label. Returns (ids, how, unmatched lines)."""
    if fix.get("schema") != "speaker_fix/2":
        raise ValueError(f"speaker_fix schema {fix.get('schema')!r}, expected 'speaker_fix/2'")
    by_words = {tuple(ln["words"]): ln for ln in fix.get("lines") or [] if ln.get("words")}
    W = tr["word_timestamps"]
    ids = [f"scribe:{w.get('speaker', '?')}" for w in W]
    how = ["scribe_fallback"] * len(W)
    unmatched = 0
    for s in tr["sentences"]:
        i0, i1 = s["words"]
        ln = by_words.get((i0, i1))
        if ln is None or abs(float(ln.get("start", s["start"])) - float(s["start"])) > 0.01:
            unmatched += 1
            continue
        key = str(ln.get("key") or "").strip()
        if not key or key.upper() == "X":
            continue
        for i in range(i0, i1 + 1):
            ids[i] = f"llm_fix:{key}"
            how[i] = "line"
    if unmatched and log:
        log(f"warning: {unmatched} Scribe lines have no matching speaker_fix line (stale layer?); they keep Scribe's "
            f"label")
    return ids, how, unmatched


# ------------------------------------------------------------------------------------------------ lines
def majority(word_ids: list[str], words: list[dict], i0: int, i1: int) -> tuple[str, dict | None]:
    """A line's label from its words: most words; ties -> more speech time, then the earliest word. Returns (id,
    split) where split = {"counts": {id: n}, "runs": [[i0, i1, id], ...]} when the words carry 2+ ids, else None."""
    cnt = Counter(word_ids[i0:i1 + 1])
    dur: dict[str, float] = {}
    first: dict[str, int] = {}
    for i in range(i0, i1 + 1):
        x = word_ids[i]
        dur[x] = dur.get(x, 0.0) + max(0.0, float(words[i]["end"]) - float(words[i]["start"]))
        first.setdefault(x, i)
    lab = max(cnt, key=lambda x: (cnt[x], round(dur[x], 6), -first[x]))
    if len(cnt) < 2:
        return lab, None
    runs = []
    for i in range(i0, i1 + 1):
        if runs and runs[-1][2] == word_ids[i]:
            runs[-1][1] = i
        else:
            runs.append([i, i, word_ids[i]])
    return lab, {"counts": dict(cnt), "runs": runs}


def anon_mapping(line_ids: list[str], word_ids: list[str]) -> dict[str, str]:
    """source id -> speaker_<n>: first appearance over line labels (transcript order), then word-only ids."""
    out: dict[str, str] = {}
    for x in list(line_ids) + list(word_ids):
        if x not in out:
            out[x] = f"speaker_{len(out)}"
    return out


def build(tr: dict, source: str, src_doc: dict | None = None, gap_s: float = GAP_S, log=None) -> dict:
    """The layer for transcript ``tr`` (the Scribe dict) from ``source`` (``src_doc`` = the Sarvam raw JSON or the
    speaker_fix document; unused for scribe). Pure: no file access."""
    W, S = tr["word_timestamps"], tr["sentences"]
    if source == "scribe":
        ids = [str(w.get("speaker", "?")) for w in W]
        how = ["scribe"] * len(W)
        unmatched = 0
    elif source == "sarvam":
        ids, how = words_to_segments(W, sarvam_segments(src_doc or {}), gap_s)
        unmatched = 0
    elif source == "llm_fix":
        ids, how, unmatched = llm_fix_word_ids(tr, src_doc or {}, log)
    else:
        raise ValueError(f"speaker source {source!r}: expected one of {SOURCES}")
    raw_lines = []
    for s in S:
        i0, i1 = s["words"]
        lab, split = majority(ids, W, i0, i1)
        raw_lines.append((s, lab, split))
    if source == "scribe":
        amap = {x: x for x in dict.fromkeys([lab for _, lab, _ in raw_lines] + ids)}
    else:
        amap = anon_mapping([lab for _, lab, _ in raw_lines], ids)
    words_out = [amap[x] for x in ids]
    lines = []
    n_lines_by: Counter = Counter()
    for k, (s, lab, split) in enumerate(raw_lines):
        row = {"sentence": k, "words": list(s["words"]), "start": s["start"], "speaker": amap[lab],
               "scribe": s.get("speaker")}
        if split:
            row["split"] = {"counts": {amap[x]: n for x, n in split["counts"].items()},
                            "runs": [[a, b, amap[x]] for a, b, x in split["runs"]]}
        lines.append(row)
        n_lines_by[amap[lab]] += 1
    n_words_by = Counter(words_out)
    inv = {v: k for k, v in amap.items()}
    mapping = [{"label": lab, "source_id": inv[lab], "n_lines": n_lines_by.get(lab, 0),
                "n_words": n_words_by.get(lab, 0)} for lab in amap.values()]
    return {"schema": SCHEMA, "source": source, "params": {"gap_s": gap_s} if source == "sarvam" else {},
            "mapping": mapping, "n_speakers": len(mapping), "words": words_out, "word_via": dict(Counter(how)),
            "lines": lines,
            "stats": {"n_lines": len(lines), "n_split_lines": sum(1 for r in lines if "split" in r),
                      "n_lines_changed_vs_scribe_partition": changed_vs_scribe(lines),
                      "unmatched_lines": unmatched}}


def changed_vs_scribe(lines: list[dict]) -> int:
    """Lines where the speaker CHANGE pattern differs from Scribe's: consecutive lines same/different speaker
    under the layer vs under Scribe (label names differ between sources, so names are not compared)."""
    n = 0
    for a, b in zip(lines, lines[1:]):
        if (a["speaker"] == b["speaker"]) != (a["scribe"] == b["scribe"]):
            n += 1
    return n


# ------------------------------------------------------------------------------------------------ files
def make(vid: str, source: str, write: bool = True, log=None) -> dict:
    """Build the layer from the source file (free) and write runs/speaker_layer/<id>_<source>.json. Raises
    LayerMissing when the source file does not exist."""
    tp = transcript_path(vid)
    tr = json.loads(tp.read_text(encoding="utf-8"))
    sp = source_path(vid, source)
    if not sp.exists():
        raise LayerMissing(f"{vid}: speaker source {source!r} needs {_rel(sp)}, which does not exist "
                           + ("(run tools/transcribe_sarvam.py " if source == "sarvam" else
                              "(run python -m shotvi.segment.speaker_fix ") + f"{vid})")
    src_doc = None if source == "scribe" else json.loads(sp.read_text(encoding="utf-8"))
    doc = build(tr, source, src_doc, log=log)
    doc = {"schema": SCHEMA, "video_id": vid, "source": source, "source_file": _rel(sp), "source_sha8": _sha8(sp),
           "transcript": _rel(tp), "transcript_sha8": _sha8(tp), **{k: v for k, v in doc.items()
                                                                    if k not in ("schema", "source")}}
    if write:
        p = layer_path(vid, source)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=None, separators=(",", ":")) + "\n",
                     encoding="utf-8")
    return doc


def load(vid: str, source: str, build_missing: bool = True, log=None) -> dict:
    """The layer for (vid, source): the saved file when it is current (same transcript and source file hashes),
    else rebuilt from the source (free) and saved. Raises LayerMissing when the source file does not exist."""
    p = layer_path(vid, source)
    if p.exists():
        doc = json.loads(p.read_text(encoding="utf-8"))
        sp, tp = source_path(vid, source), transcript_path(vid)
        if doc.get("schema") == SCHEMA and sp.exists() and doc.get("source_sha8") == _sha8(sp) \
                and doc.get("transcript_sha8") == _sha8(tp):
            return doc
    if not build_missing:
        raise LayerMissing(f"{vid}: {_rel(p)} does not exist or is stale")
    return make(vid, source, log=log)


def placeholder(vid: str, source: str) -> dict:
    """A STAND-IN for a layer whose source file does not exist yet (dry run only): Scribe's labels pushed through the
    same anonymous mapping (``scribe:<label>`` ids). Same line structure and nearly the same rendered size as the
    real layer; the labels are NOT the source's. Marked ``placeholder``: callers must treat every prompt as uncached
    and never run a paid call with it."""
    tr = json.loads(transcript_path(vid).read_text(encoding="utf-8"))
    W = tr["word_timestamps"]
    ids = [f"scribe:{w.get('speaker', '?')}" for w in W]
    raw_lines = [majority(ids, W, *s["words"]) for s in tr["sentences"]]
    amap = anon_mapping([lab for lab, _ in raw_lines], ids)
    doc = build(tr, "scribe")
    doc["words"] = [amap[x] for x in ids]
    for r, (lab, _) in zip(doc["lines"], raw_lines):
        r["speaker"] = amap[lab]
    inv = {v: k for k, v in amap.items()}
    doc["mapping"] = [{"label": v, "source_id": inv[v], "n_lines": sum(1 for r in doc["lines"] if r["speaker"] == v),
                       "n_words": doc["words"].count(v)} for v in amap.values()]
    return {**doc, "video_id": vid, "source": source, "placeholder": True,
            "source_file": _rel(source_path(vid, source)) + " (MISSING: placeholder = Scribe labels, anonymised)"}


# ------------------------------------------------------------------------------------------------ applying
def line_labels(layer: dict) -> dict[tuple[int, int], tuple[float, str]]:
    """(i0, i1) -> (start, label) for every line of the layer."""
    return {tuple(r["words"]): (float(r["start"]), r["speaker"]) for r in layer["lines"]}


def apply(lines: list[dict], words: list[dict] | None, layer: dict) -> int:
    """Relabel IN PLACE: every speech line (``shotvi.select.lines`` rows, matched by Scribe word range and start)
    gets the layer's line label in ``speaker``; ``words`` (the episode's word dicts, a loaded copy, never the file)
    get the per-word labels. Raises ValueError when a speech line has no layer line (a stale layer). Returns the
    number of speech lines relabelled."""
    by = line_labels(layer)
    n = 0
    for r in lines:
        if r.get("kind") != "speech" or not r.get("words"):
            continue
        hit = by.get(tuple(r["words"]))
        if hit is None or abs(hit[0] - float(r["start"])) > 0.01:
            raise ValueError(f"{layer.get('video_id')}: line {r.get('id')} words {r['words']} has no line in the "
                             f"{layer.get('source')} speaker layer (stale layer?)")
        r["speaker"] = hit[1]
        n += 1
    if words is not None:
        wl = layer["words"]
        if len(wl) != len(words):
            raise ValueError(f"{layer.get('video_id')}: speaker layer has {len(wl)} word labels, transcript "
                             f"{len(words)} words (stale layer?)")
        for w, lab in zip(words, wl):
            w["speaker"] = lab
    return n


def public(layer: dict) -> dict:
    """What a run document records about the layer it used (the mapping table, no per-word data)."""
    return {"source": layer["source"], "file": _rel(layer_path(layer["video_id"], layer["source"]))
            if not layer.get("placeholder") else None,
            "source_file": layer.get("source_file"), "source_sha8": layer.get("source_sha8"),
            "placeholder": bool(layer.get("placeholder")), "n_speakers": layer["n_speakers"],
            "mapping": layer["mapping"], "word_via": layer.get("word_via"), "stats": layer.get("stats")}


def run_source(doc: dict) -> str:
    """The speaker source a run document used: its ``speaker_layer`` record, else its config's ``speaker_source``,
    else "scribe" (every run made before the option existed)."""
    sl = doc.get("speaker_layer") or {}
    return sl.get("source") or ((doc.get("pipeline") or {}).get("config") or {}).get("speaker_source") or "scribe"


def for_run(doc: dict, vid: str | None = None, log=None) -> dict | None:
    """The layer a run used (None for Scribe's labels), loaded (or rebuilt, free) for the tools that show a run's
    clips (tools/preview_clips.py, tools/episode_dump.py)."""
    src = run_source(doc)
    if src == "scribe":
        return None
    return load(vid or doc["video_id"], src, log=log)


# ------------------------------------------------------------------------------------------------ CLI
def _parse_mmss(x: str) -> float:
    m, s = x.split(":")
    return int(m) * 60 + float(s)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Speaker-label layer (free; never edits the transcript)")
    ap.add_argument("video_ids", nargs="+")
    ap.add_argument("--source", choices=SOURCES, required=True)
    ap.add_argument("--show", default=None, metavar="M:SS-M:SS",
                    help="print the rendered context lines of that stretch with Scribe's and the layer's labels")
    a = ap.parse_args(argv)
    for st in (sys.stdout, sys.stderr):
        if hasattr(st, "reconfigure"):
            st.reconfigure(encoding="utf-8")
    rc = 0
    for vid in a.video_ids:
        try:
            doc = make(vid, a.source, log=lambda m: print(m, file=sys.stderr))
        except LayerMissing as e:
            print(f"MISSING: {e}")
            rc = 1
            continue
        st = doc["stats"]
        print(f"{vid} {a.source}: {doc['n_speakers']} speakers; words {doc['word_via']}; lines {st['n_lines']}, "
              f"{st['n_split_lines']} split, {st['n_lines_changed_vs_scribe_partition']} speaker changes differ from "
              f"Scribe's -> {_rel(layer_path(vid, a.source))}")
        print("  mapping: " + "; ".join(f"{m['label']} = {m['source_id']} ({m['n_lines']} lines, {m['n_words']} words)"
                                        for m in doc["mapping"]))
        if a.show:
            t0, t1 = (_parse_mmss(x) for x in a.show.split("-"))
            from shotvi.select.lines import build_lines, render
            before = [r for r in build_lines(vid, asr_fix=True) if t0 <= r["start"] < t1]
            after = [dict(r) for r in before]
            by = line_labels(doc)
            for r in after:
                if r["kind"] == "speech":
                    r["speaker"] = by[tuple(r["words"])][1]
            print(f"  before (Scribe):\n    " + render(before).replace("\n", "\n    "))
            print(f"  after ({a.source}):\n    " + render(after).replace("\n", "\n    "))
    return rc


if __name__ == "__main__":
    sys.exit(main())
