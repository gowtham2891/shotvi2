"""Speaker-label accuracy against a frame-based answer key (data/speakers/<id>.json).

Key (written by clip-gt-editor)::

    {"characters": [{"name", "desc", "aliases"?}],
     "labels": [{"start", "end", "speaker", "confidence"?, ...}]}

``speaker`` is a character name, ``OFFSCREEN:<name>`` (speaker not visible; scored as <name>, also reported
separately) or ``UNKNOWN`` (skipped). Labels are keyed by TIME, so any transcript of the same audio can be scored.

For each key line, the transcript words overlapping its time span vote; the line's predicted label is the majority
word label (ties: more overlapping seconds). Systems:

- Scribe labels (original transcript, re-transcriptions with other diarization settings): anonymous labels, so
  ``one_to_one`` = accuracy under the best one-to-one label -> character mapping (Hungarian; a label may map to
  nothing), and ``purity`` = accuracy when every label maps to its majority character (many-to-one; merging
  characters under one label is NOT penalised there, so it is an upper bound).
- LLM speaker names (runs/speaker_fix/<id>.json): ``name_acc`` = names matched to the key's characters (normalised;
  aliases from the key and an optional alias file), plus ``one_to_one`` / ``purity`` on the names as anonymous labels
  (naming-independent views).

Lines with no overlapping words count as wrong (``no_words``) in every view: a system that drops speech is not
rewarded.

CLI::

    python -m eval.speakers H41fJJJYTqo fjGrX8aBVAc qIdAPG8UulE
    python -m eval.speakers <id> --system mine=path/to/transcript.json --aliases aliases.json
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY_DIR = ROOT / "data" / "speakers"
OFFSCREEN = "OFFSCREEN:"
UNKNOWN = "UNKNOWN"
# generic relation words -> the word the key uses (only applied when names are compared, never to the key itself)
REL_SYNONYMS = {"mother": "parent", "father": "parent", "mom": "parent", "dad": "parent", "amma": "parent",
                "nanna": "parent", "mum": "parent"}


# ------------------------------------------------------------------ key
def load_key(path) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    chars = []
    for c in d.get("characters") or []:
        c = {"name": c} if isinstance(c, str) else dict(c)
        chars.append({"name": c["name"], "aliases": list(c.get("aliases") or [])})
    lines = []
    for i, l in enumerate(d.get("labels") or []):
        sp = str(l.get("speaker") or UNKNOWN).strip()
        off = sp.upper().startswith(OFFSCREEN)
        if off:
            sp = sp[len(OFFSCREEN):].strip()
        lines.append({"i": i, "start": float(l["start"]), "end": float(l["end"]), "speaker": sp,
                      "offscreen": off, "unknown": sp.upper() == UNKNOWN or not sp,
                      "confidence": l.get("confidence")})
    known = {c["name"] for c in chars}
    for l in lines:   # characters used in labels but missing from the list
        if not l["unknown"] and l["speaker"] not in known:
            chars.append({"name": l["speaker"], "aliases": []})
            known.add(l["speaker"])
    return {"video_id": d.get("video_id"), "characters": chars, "lines": lines}


# ------------------------------------------------------------------ predicted labels
def word_track(words, labels=None) -> list[tuple[float, float, object]]:
    """(start, end, label) per word, sorted by start. ``labels`` overrides the words' own ``speaker``."""
    out = []
    for i, w in enumerate(words):
        lab = labels[i] if labels is not None else w.get("speaker")
        out.append((float(w["start"]), float(w["end"]), lab))
    out.sort(key=lambda t: t[0])
    return out


def scribe_track(transcript: dict):
    return word_track(transcript["word_timestamps"])


def llm_track(fix: dict, transcript: dict):
    from shotvi.segment.speaker_fix import word_names
    words = transcript["word_timestamps"]
    return word_track(words, word_names(fix, len(words)))


def line_label(track, start: float, end: float):
    """Majority label of the words overlapping [start, end] (ties: more overlapping seconds). None if no word
    overlaps or none has a label."""
    starts = getattr(track, "_starts", None) or [t[0] for t in track]
    # words are short: begin a little before ``start`` so a word that started earlier but overlaps is included
    j = max(0, bisect_left(starts, start - 30.0))
    votes, secs = Counter(), defaultdict(float)
    for s, e, lab in track[j:]:
        if s >= end:
            break
        ov = min(e, end) - max(s, start)
        if ov <= 0 and not (s == e and start <= s < end):
            continue
        if lab is None:
            continue
        votes[lab] += 1
        secs[lab] += max(ov, 0.0)
    if not votes:
        return None
    return max(votes, key=lambda k: (votes[k], secs[k]))


class Track(list):
    """A word track with cached start times (fast repeated lookups)."""
    def __init__(self, items):
        super().__init__(items)
        self._starts = [t[0] for t in self]


# ------------------------------------------------------------------ mapping
def hungarian_max(w: list[list[float]]) -> list[int]:
    """Assignment maximising total weight. ``w[r][c]``; returns col index per row (-1 = unassigned). Rectangular
    matrices are padded with zeros. O(n^3) Kuhn-Munkres (potentials), no dependencies."""
    nr = len(w)
    nc = len(w[0]) if nr else 0
    n = max(nr, nc)
    if n == 0:
        return []
    big = max((x for row in w for x in row), default=0)
    cost = [[(big - (w[r][c] if r < nr and c < nc else 0)) for c in range(n)] for r in range(n)]
    INF = float("inf")
    u, v, p, way = [0.0] * (n + 1), [0.0] * (n + 1), [0] * (n + 1), [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    ans = [-1] * nr
    for j in range(1, n + 1):
        r, c = p[j] - 1, j - 1
        if r < nr and c < nc and w[r][c] > 0:
            ans[r] = c
    return ans


def best_one_to_one(pairs) -> dict:
    """pairs = [(pred_label or None, true_char)] -> {label: char} maximising agreement, one char per label."""
    conf = defaultdict(Counter)
    for p, t in pairs:
        if p is not None:
            conf[p][t] += 1
    labs = sorted(conf, key=str)
    chars = sorted({t for _, t in pairs})
    if not labs or not chars:
        return {}
    a = hungarian_max([[conf[l][c] for c in chars] for l in labs])
    return {l: chars[a[k]] for k, l in enumerate(labs) if a[k] >= 0}


def majority_map(pairs) -> dict:
    conf = defaultdict(Counter)
    for p, t in pairs:
        if p is not None:
            conf[p][t] += 1
    return {l: c.most_common(1)[0][0] for l, c in conf.items()}


# ------------------------------------------------------------------ names
def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s)).lower().replace("’", "'")
    s = re.sub(r"\(.*?\)", " ", s)                     # "Akshara (delivery girl)" -> "akshara"
    s = re.sub(r"[^\w' ]+", " ", s)
    toks = [t for t in s.split() if t not in ("the", "a", "an")]
    toks = [REL_SYNONYMS.get(t, t) for t in toks]
    return " ".join(toks)


def alias_table(characters: list[dict], extra: dict | None = None) -> dict[str, str]:
    """normalised alias -> character name: full name, key aliases, and extra {alias: name}."""
    tab = {}
    for c in characters:
        for a in [c["name"], *c.get("aliases", [])]:
            tab.setdefault(norm_name(a), c["name"])
    for a, name in (extra or {}).items():
        tab[norm_name(a)] = name
    return tab


def match_name(name, table: dict[str, str]):
    """Key character for a predicted name: exact normalised alias, else the longest alias that appears as a whole
    token run inside the name ("Meenakshi Rao" -> Meenakshi; "Akshara's mother" never -> Akshara because the token
    is "akshara's"). None if nothing matches."""
    if not name:
        return None
    n = norm_name(name)
    if n in table:
        return table[n]
    toks = n.split()
    best, blen = None, 0
    for a, c in table.items():
        at = a.split()
        if not at or len(at) <= blen:
            continue
        if any(toks[i:i + len(at)] == at for i in range(len(toks) - len(at) + 1)):
            best, blen = c, len(at)
    return best


# ------------------------------------------------------------------ scoring
def _acc(lines, mapped) -> dict:
    n = len(lines)
    ok = sum(1 for l in lines if mapped.get(l["i"]) == l["speaker"])
    return {"n": n, "correct": ok, "acc": round(ok / n, 3) if n else None}


def score_track(key: dict, track, names: bool = False, table: dict | None = None) -> dict:
    """Accuracy of one system on the key. ``names`` = labels are names to match against the key's characters."""
    track = track if isinstance(track, Track) else Track(track)
    lines = [l for l in key["lines"] if not l["unknown"]]
    pred = {l["i"]: line_label(track, l["start"], l["end"]) for l in lines}
    pairs = [(pred[l["i"]], l["speaker"]) for l in lines]
    one = best_one_to_one(pairs)
    maj = majority_map(pairs)

    def views(mapping):
        mapped = {l["i"]: mapping.get(pred[l["i"]]) for l in lines}
        return {"all": _acc(lines, mapped),
                "onscreen": _acc([l for l in lines if not l["offscreen"]], mapped),
                "offscreen": _acc([l for l in lines if l["offscreen"]], mapped),
                "conf2plus": _acc([l for l in lines if (l["confidence"] or 0) >= 2], mapped)}

    conf = defaultdict(Counter)
    for p, t in pairs:
        conf[str(p)][t] += 1
    out = {"n_key_lines": len(key["lines"]), "n_unknown_skipped": len(key["lines"]) - len(lines),
           "n_offscreen": sum(l["offscreen"] for l in lines),
           "no_words": sum(1 for p, _ in pairs if p is None),
           "n_labels": len({p for p, _ in pairs if p is not None}),
           "one_to_one": views(one), "purity": views(maj),
           "mapping_one_to_one": {str(k): v for k, v in one.items()},
           "confusion": {k: dict(v.most_common()) for k, v in sorted(conf.items())}}
    if names:
        table = table if table is not None else alias_table(key["characters"])
        nm = {p: match_name(p, table) for p, _ in pairs if p is not None}
        out["name_acc"] = views(nm)
        out["name_map"] = {str(k): v for k, v in sorted(nm.items(), key=lambda kv: str(kv[0]))}
        out["unmatched_names"] = sorted(str(k) for k, v in nm.items() if v is None)
    return out


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def default_systems(vid: str) -> dict[str, tuple[str, Path]]:
    """name -> (kind, path) for what exists: original Scribe, A1 (num_speakers), A2 (threshold 0.15), LLM fix."""
    cand = {"scribe": ("scribe", ROOT / "scribe_dataset" / vid / f"{vid}_scribe_transcript.json"),
            "A1_num_speakers": ("scribe", ROOT / "runs" / "diar" / f"{vid}_ns.json"),
            "A2_threshold_0.15": ("scribe", ROOT / "runs" / "diar" / f"{vid}_th015.json"),
            "llm_fix": ("llm", ROOT / "runs" / "speaker_fix" / f"{vid}.json")}
    return {k: v for k, v in cand.items() if v[1].exists()}


def evaluate(vid: str, key_path=None, systems: dict | None = None, aliases: dict | None = None) -> dict:
    key = load_key(key_path or KEY_DIR / f"{vid}.json")
    table = alias_table(key["characters"], aliases)
    res = {"video_id": vid, "characters": [c["name"] for c in key["characters"]], "systems": {}}
    for name, (kind, path) in (systems or default_systems(vid)).items():
        if kind == "llm":
            fix = load_json(path)
            tr = load_json(ROOT / fix["transcript"] if not Path(fix["transcript"]).is_absolute()
                           else fix["transcript"]) if fix.get("transcript") else \
                load_json(ROOT / "scribe_dataset" / vid / f"{vid}_scribe_transcript.json")
            res["systems"][name] = {"path": str(path), **score_track(key, llm_track(fix, tr), True, table)}
        else:
            res["systems"][name] = {"path": str(path), **score_track(key, scribe_track(load_json(path)))}
    return res


def overall(results: list[dict]) -> dict:
    """Pooled accuracy per system and view over episodes (lines summed). Systems can cover different episodes:
    ``episodes`` lists which ones each pooled number includes."""
    tot = defaultdict(lambda: [0, 0])
    eps = defaultdict(list)
    for r in results:
        for sname, s in r["systems"].items():
            eps[sname].append(r["video_id"])
            for view in ("one_to_one", "purity", "name_acc"):
                if view in s:
                    for sub in ("all", "onscreen", "offscreen"):
                        t = tot[(sname, view, sub)]
                        t[0] += s[view][sub]["correct"]
                        t[1] += s[view][sub]["n"]
    out = defaultdict(dict)
    for (sname, view, sub), (c, n) in sorted(tot.items()):
        out[sname][f"{view}.{sub}"] = {"correct": c, "n": n, "acc": round(c / n, 3) if n else None}
    for sname in out:
        out[sname]["episodes"] = eps[sname]
    return dict(out)


def _fmt(v):
    return "  -  " if v is None else f"{v:.3f}"


def main(argv=None):
    from .common import utf8_stdout
    utf8_stdout()
    ap = argparse.ArgumentParser(description="Speaker-label accuracy against data/speakers/<id>.json")
    ap.add_argument("video_ids", nargs="*", default=["H41fJJJYTqo", "fjGrX8aBVAc", "qIdAPG8UulE"])
    ap.add_argument("--key", help="answer key path (one id only)")
    ap.add_argument("--system", action="append", default=[],
                    help="name=path (a Scribe-format transcript) or name=llm:path (a speaker_fix result); "
                         "replaces the defaults (one id only)")
    ap.add_argument("--aliases", help='json {"<video_id>": {"predicted name": "key character"}} or a flat dict')
    ap.add_argument("--out", default=str(ROOT / "runs" / "eval_speakers" / "report.json"))
    ap.add_argument("--verbose", action="store_true", help="print confusion tables")
    a = ap.parse_args(argv)
    if (a.key or a.system) and len(a.video_ids) != 1:
        ap.error("--key / --system need exactly one video id")
    systems = None
    if a.system:
        systems = {}
        for s in a.system:
            name, path = s.split("=", 1)
            kind = "llm" if path.startswith("llm:") else "scribe"
            systems[name] = (kind, Path(path[4:] if kind == "llm" else path))
    alias_all = load_json(a.aliases) if a.aliases else {}
    results = []
    for vid in a.video_ids:
        kp = Path(a.key) if a.key else KEY_DIR / f"{vid}.json"
        if not kp.exists():
            print(f"{vid}: no answer key at {kp}; skipped")
            continue
        al = alias_all.get(vid, alias_all) if isinstance(alias_all.get(vid), dict) else alias_all
        r = evaluate(vid, kp, systems, al)
        results.append(r)
        print(f"\n{vid}: {len(r['characters'])} characters")
        print(f"  {'system':20} {'labels':>6} {'lines':>5} {'noword':>6} {'1:1':>6} {'purity':>6} {'names':>6}"
              f"  {'1:1 on/off-screen':>18}")
        for sname, s in r["systems"].items():
            o = s["one_to_one"]
            print(f"  {sname:20} {s['n_labels']:>6} {o['all']['n']:>5} {s['no_words']:>6} {_fmt(o['all']['acc']):>6} "
                  f"{_fmt(s['purity']['all']['acc']):>6} {_fmt(s.get('name_acc', {}).get('all', {}).get('acc')):>6}"
                  f"  {_fmt(o['onscreen']['acc'])} / {_fmt(o['offscreen']['acc'])} (n={o['offscreen']['n']})")
            if s.get("unmatched_names"):
                print(f"  {'':20} unmatched names: {s['unmatched_names']}")
            if a.verbose:
                print(f"  {'':20} mapping {s['mapping_one_to_one']}")
                for lab, row in s["confusion"].items():
                    print(f"  {'':22}{lab}: {row}")
    if results:
        ov = overall(results)
        print("\noverall (lines pooled):")
        for sname, v in ov.items():
            print(f"  {sname:20} " + "  ".join(f"{k} {_fmt(x['acc'])} ({x['correct']}/{x['n']})"
                                               for k, x in v.items() if k.endswith(".all"))
                  + f"  [{', '.join(v['episodes'])}]")
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"episodes": results, "overall": ov}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
