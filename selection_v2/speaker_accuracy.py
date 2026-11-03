"""Speaker-map accuracy against a recorded TRUE map (founder, 2026-10-26), for episodes that have one:
``data/speakers/<id>.json`` (the frame-checked answer key: labels by time with character names, same file
``eval.speakers`` scores) or a hand file ``selection_v2/reference/true_map/<id>.yaml`` ({line_id: name}).

For each checked line, the predicted name under each stage is compared with the truth:
  fix       the speaker-fix name on the line
  1b        the Stage 1B map (final names + reassigned lines)
  2         the Stage 2 map (speakers_seen corrections) on top of 1B, for the scenes run so far
Names are matched with ``eval.speakers`` rules (normalised aliases, ``'s mother``-style relations from the key's
descriptions, an optional ``data/speakers/aliases.json``). Unmatched predicted names count as wrong and are listed,
so the founder can add an alias instead of guessing.
"""
from __future__ import annotations

import json
import unicodedata
import re
from pathlib import Path

from .config import ROOT


def _load_es():
    """eval/speakers.py by path (the name 'eval' is shadowed by tests/eval under pytest)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("shotvi_eval_speakers", ROOT / "eval" / "speakers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ES = _load_es()

ALIASES = ROOT / "data" / "speakers" / "aliases.json"
TRUE_DIR = Path(__file__).resolve().parent / "reference" / "true_map"


def key_path(vid: str) -> Path:
    return ROOT / "data" / "speakers" / f"{vid}.json"


def has_truth(vid: str) -> bool:
    return key_path(vid).exists() or (TRUE_DIR / f"{vid}.yaml").exists()


def _table(raw: dict, vid: str) -> dict:
    chars = [{"name": c["name"] if isinstance(c, dict) else c, "aliases": list((c.get("aliases") if isinstance(c, dict) else None) or [])}
             for c in raw.get("characters") or []]
    extra = {}
    for c in raw.get("characters") or []:
        if not isinstance(c, dict):
            continue
        desc = str(c.get("desc") or "")
        for m in re.findall(r"([A-Za-z]+'s (?:mother|father|sister|brother|wife|husband|friend|son|daughter|boss))", desc):
            extra.setdefault(m, c["name"])
    if ALIASES.exists():
        try:
            a = json.loads(ALIASES.read_text(encoding="utf-8"))
            extra.update(a.get(vid) or {k: v for k, v in a.items() if isinstance(v, str)})
        except (OSError, ValueError):
            pass
    return ES.alias_table(chars, extra)


def predictions(facts: dict, lid: str) -> dict:
    """{fix, 1b, 2}: (name, description) predicted for a line under each stage (None when the stage has no output).
    The description (the fix's cast desc, 1B's role) is a second string to match, e.g. 'Mother' + '<Name>'s mother'."""
    r = next((x for x in facts["lines"] if x["id"] == lid), None)
    if r is None:
        return {"fix": None, "1b": None, "2": None}
    fix_desc = {c["key"]: c.get("desc") for c in facts["episode"].get("speakers") or []}
    out = {"fix": (r.get("speaker_name"), fix_desc.get(r.get("speaker_key"))), "1b": None, "2": None}
    ep = facts.get("episode_pass")
    out["combined"] = None
    if ep:
        sp = {s["key"]: s for s in ep.get("speakers") or []}
        d = ep.get("derived") or {}
        text_map = d.get("line_speakers_text") if "line_speakers_text" in d else d.get("line_speakers", {})
        key = (text_map or {}).get(lid) or r.get("speaker_key")
        e = sp.get(key)
        out["1b"] = (e.get("name"), e.get("role") or fix_desc.get(key)) if e else out["fix"]
        if "line_speakers_text" in d:                           # the hybrid was applied: combined = text + identity findings
            ck = d.get("line_speakers", {}).get(lid) or r.get("speaker_key")
            ce = sp.get(ck)
            out["combined"] = (ce.get("name"), ce.get("role") or fix_desc.get(ck)) if ce else out["1b"]
        sm = facts.get("speaker_map")
        if sm:
            n2 = (sm.get("line_names") or {}).get(lid)
            out["2"] = (n2, None) if n2 else (out["combined"] or out["1b"])
    return out


def _n(s) -> str:
    s = unicodedata.normalize("NFKC", str(s or "")).lower().replace("\u2019", "'")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^\w' \u0c00-\u0c7f]+", " ", s)
    return " ".join(t for t in s.split() if t not in ("the", "a", "an"))


def _loose(s) -> str:
    """Spelling-insensitive: y/i, sh/s, ph/f, aspirates (th/t, dh/d, kh/k, bh/b) and doubled letters are folded."""
    t = _n(s).replace("'s", "").replace("sh", "s").replace("ph", "f").replace("y", "i")
    for a, b in (("th", "t"), ("dh", "d"), ("kh", "k"), ("bh", "b")):
        t = t.replace(a, b)
    t = re.sub(r"(.)\1+", r"\1", t)
    return re.sub(r"[aeiou]", "", t)


RELATIONS = {"mother": "mother", "mom": "mother", "amma": "mother", "father": "father", "dad": "father", "nanna": "father",
             "sister": "sister", "brother": "brother", "wife": "wife", "husband": "husband", "friend": "friend",
             "son": "son", "daughter": "daughter", "boss": "boss", "girlfriend": "girlfriend", "boyfriend": "boyfriend"}
_REL_RE = re.compile(r"([\w\u0c00-\u0c7f]+)(?:\s+and\s+([\w\u0c00-\u0c7f]+))?'s\s+(" + "|".join(RELATIONS) + r")\b")


def _relations(text: str) -> list[tuple[str, str]]:
    """[(relative, relation)] from 'X and Y's mother' -> [(x, mother), (y, mother)]."""
    out = []
    for a, b, rel in _REL_RE.findall(_n(text)):
        for x in (a, b):
            if x:
                out.append((x, RELATIONS[rel]))
    return out


def match(pred, table: dict | None = None, chars: list[dict] | None = None, aliases: dict | None = None) -> str | None:
    """Key character for (name, desc), in this order: a name in parentheses ('... (<Name>)'); the name itself
    (exact or spelling-insensitive); a relation in the name or desc ('X and Y's mother' -> the character
    whose name or description says X's mother); a single-token name equal to a character's first name."""
    name, desc = (pred if isinstance(pred, tuple) else (pred, None))
    chars = chars or []
    for a, target in (aliases or {}).items():            # founder-recorded aliases win (data/speakers/aliases.json)
        if name and _n(a) == _n(name):
            return target
    by_n = {_n(c["name"]): c["name"] for c in chars}
    by_l = {_loose(c["name"]): c["name"] for c in chars if _loose(c["name"])}
    for c in chars:                                       # a key name like 'Coworker (Sindhu)' also answers to 'Sindhu'
        for inner in re.findall(r"\((.*?)\)", c["name"]):
            by_n.setdefault(_n(inner), c["name"]); by_l.setdefault(_loose(inner), c["name"])
    parens = re.findall(r"\((.*?)\)", f"{name or ''} {desc or ''}")
    for t in [name] + parens:
        if not t:
            continue
        if _n(t) in by_n:
            return by_n[_n(t)]
        if _loose(t) in by_l:
            return by_l[_loose(t)]
    # the key's own aliases (2026-11-02): how a viewer may call a character, e.g. an unnamed groom = "the groom"; used
    # only when exactly one character carries the alias
    for t in [name] + parens:
        hits = {c["name"] for c in chars for a in c.get("aliases") or [] if t and (_n(a) == _n(t) or (_loose(a) and _loose(a) == _loose(t)))}
        if len(hits) == 1:
            return hits.pop()
    for t in (name, desc):
        rels = _relations(t or "")
        if not rels:
            continue
        hits = set()
        for c in chars:
            blob = _loose(f"{c['name']} {c.get('desc') or ''}")
            for x, rel in rels:
                if _loose(f"{x}'s {rel}") in blob:
                    hits.add(c["name"])
        if len(hits) == 1:
            return hits.pop()
    if name:
        toks = [_loose(t) for t in _n(name).split()]
        firsts = {}
        for c in chars:
            ft = _n(c["name"]).split()
            if ft:
                firsts.setdefault(_loose(ft[0]), set()).add(c["name"])
        hits = set()
        for t in toks:
            if t and t in firsts:
                hits |= firsts[t]
        if len(hits) == 1:
            return hits.pop()
    if table is not None:
        return ES.match_name(name, table)
    return None


def evaluate(facts: dict, vid: str) -> dict | None:
    """Per-stage accuracy on the checked lines; None when no truth file exists."""
    kp, hp = key_path(vid), TRUE_DIR / f"{vid}.yaml"
    speech = [r for r in facts["lines"] if r["kind"] == "speech" and not r.get("zero_len")]
    items = []   # (line id, true name, offscreen)
    if kp.exists():
        raw = json.loads(kp.read_text(encoding="utf-8"))
        key = ES.load_key(kp)
        table = _table(raw, vid)
        chars = [c for c in raw.get("characters") or [] if isinstance(c, dict)]
        aliases = {}
        if ALIASES.exists():
            try:
                a = json.loads(ALIASES.read_text(encoding="utf-8"))
                aliases = a.get(vid) or {k: v for k, v in a.items() if isinstance(v, str)}
            except (OSError, ValueError):
                aliases = {}
        for l in key["lines"]:
            if l["unknown"]:
                continue
            best, bo = None, 0.0
            for r in speech:
                ov = min(r["end"], l["end"]) - max(r["start"], l["start"])
                if ov > bo:
                    best, bo = r, ov
            if best is not None and bo > 0:
                items.append((best["id"], l["speaker"], l["offscreen"]))
    elif hp.exists():
        import yaml
        d = yaml.safe_load(hp.read_text(encoding="utf-8")) or {}
        chars = [{"name": c} for c in sorted({str(v) for v in d.values()})]
        table = ES.alias_table(chars, None)
        aliases = {}
        items = [(str(k), str(v), False) for k, v in d.items() if str(k).startswith("L")]
    else:
        return None
    stages = {"fix": True, "1b": bool(facts.get("episode_pass")),
              "combined": "line_speakers_text" in (((facts.get("episode_pass") or {}).get("derived")) or {}),
              "2": bool(facts.get("speaker_map"))}
    src = kp if kp.exists() else hp
    try:
        src_s = str(src.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        src_s = str(src)
    res = {"video_id": vid, "n_checked": len(items), "source": src_s,
           "stages": {}, "unmatched": {}, "wrong": {}}
    for st, present in stages.items():
        if not present:
            res["stages"][st] = None
            continue
        ok, wrong, unmatched = 0, [], {}
        for lid, truth, off in items:
            pred = predictions(facts, lid).get(st)
            matched = match(pred, None, chars, aliases)
            pname = pred[0] if isinstance(pred, tuple) else pred
            if matched is None and pname:
                unmatched[pname] = unmatched.get(pname, 0) + 1
            if matched == truth:
                ok += 1
            else:
                wrong.append({"line": lid, "true": truth, "pred": pname, "matched_as": matched, "offscreen": off})
        res["stages"][st] = {"ok": ok, "n": len(items), "acc": round(ok / len(items), 3) if items else None}
        res["unmatched"][st] = unmatched
        res["wrong"][st] = wrong
    sheets = facts.get("beat_sheets") or []
    n_sc = len(((facts.get("episode_pass") or {}).get("derived") or {}).get("scenes") or [])
    res["stage2_scenes"] = f"{len(sheets)} of {n_sc}" if sheets else None
    return res


def one_line(res: dict | None) -> str | None:
    if not res:
        return None
    parts = []
    for st, label in (("fix", "speaker fix"), ("1b", "text pass"), ("combined", "text + identity findings"), ("2", "Stage 2")):
        s = res["stages"].get(st)
        if s:
            extra = f" ({res['stage2_scenes']} scenes)" if st == "2" and res.get("stage2_scenes") else ""
            parts.append(f"{label}{extra} {s['acc']:.0%} ({s['ok']}/{s['n']})")
    un = {k for st in res["unmatched"].values() for k in st}
    tail = f"; predicted names with no match in the key (counted wrong): {sorted(un)}" if un else ""
    return f"Speaker map accuracy vs the recorded true map ({res['n_checked']} checked lines): " + " · ".join(parts) + tail
