"""Who is in a clip, and does the on-screen text get their relationships right? (founder, 2026-10-29)

Everything comes from the episode's own data, never typed in:
- the people = the final speaker names of the lines kept in the clip (Stage 2 speaker map > 1B > fix);
- each person's role = the episode pass's description of that speaker ("<name>'s younger sister");
- the scene's relationship line = the beat sheet's ``dynamic.relationship``.

``kin_terms`` reads relationship words (English and romanised Telugu: akka, chelli, anna, tammudu, amma, nanna, ...)
with their category, gender and elder / younger. The checks flag only what the data CONTRADICTS: a person whose
role says "younger sister" called "akka" (elder sister), a father called a friend. Where the data does not state a
person's gender or age, nothing can be checked and nothing is flagged.
"""
from __future__ import annotations

import re

from .acceptance import final_speaker
from .config import load_cast_lists
from .faces import norm as name_norm

# word -> (category, gender, age)
KIN: dict[str, tuple[str, str | None, str | None]] = {
    "sister": ("sibling", "f", None), "sisters": ("sibling", "f", None), "brother": ("sibling", "m", None), "brothers": ("sibling", "m", None),
    "sibling": ("sibling", None, None), "siblings": ("sibling", None, None),
    "akka": ("sibling", "f", "elder"), "akkaa": ("sibling", "f", "elder"), "akkalu": ("sibling", "f", "elder"),
    "chelli": ("sibling", "f", "younger"), "chellelu": ("sibling", "f", "younger"), "chellellu": ("sibling", "f", "younger"),
    "chellella": ("sibling", "f", "younger"), "chellela": ("sibling", "f", "younger"),
    "anna": ("sibling", "m", "elder"), "annayya": ("sibling", "m", "elder"), "annaya": ("sibling", "m", "elder"), "annayyalu": ("sibling", "m", "elder"),
    "tammudu": ("sibling", "m", "younger"), "thammudu": ("sibling", "m", "younger"), "tammullu": ("sibling", "m", "younger"),
    "mother": ("parent", "f", None), "mom": ("parent", "f", None), "mummy": ("parent", "f", None), "amma": ("parent", "f", None), "ammaa": ("parent", "f", None),
    "father": ("parent", "m", None), "dad": ("parent", "m", None), "daddy": ("parent", "m", None), "nanna": ("parent", "m", None), "nannaa": ("parent", "m", None),
    "parents": ("parent", None, None), "son": ("child", "m", None), "koduku": ("child", "m", None), "daughter": ("child", "f", None), "kuthuru": ("child", "f", None),
    "girlfriend": ("partner", "f", None), "gf": ("partner", "f", None), "wife": ("partner", "f", None), "boyfriend": ("partner", "m", None), "bf": ("partner", "m", None),
    "husband": ("partner", "m", None), "lover": ("partner", None, None), "lovers": ("partner", None, None), "couple": ("partner", None, None),
    "partner": ("partner", None, None), "romantic": ("partner", None, None), "dating": ("partner", None, None),
    "friend": ("friend", None, None), "friends": ("friend", None, None), "bestie": ("friend", None, None), "dost": ("friend", None, None),
    "customer": ("customer", None, None), "courier": ("delivery", None, None), "delivery": ("delivery", None, None),
}
AGE = {"younger": "younger", "little": "younger", "elder": "elder", "older": "elder", "big": "elder"}


def cast_genders(facts: dict, cast_cfg: dict | None = None) -> dict[str, str]:
    """{normalised spelling: 'm' | 'f'} from the series' cast list (data/series_cast/cast_lists.yaml), for entries
    that carry the optional ``gender``."""
    key = (facts.get("cast_list") or {}).get("series_key") or (facts.get("episode") or {}).get("series_key")
    cast = ((((cast_cfg if cast_cfg is not None else load_cast_lists()).get("series") or {}).get(key) or {}).get("cast")) or {}
    out = {}
    for canon, entry in cast.items():
        g = entry.get("gender") if isinstance(entry, dict) else None
        if g in ("m", "f"):
            for a in [canon] + list((entry.get("aka") or [])):
                out[name_norm(a)] = g
    return out


def gender_of(name: str, genders: dict[str, str]) -> str | None:
    """The cast gender of a speaker name: the whole name, else any of its words ('V Arjun' -> Arjun)."""
    n = name_norm(name)
    if n in genders:
        return genders[n]
    hits = {genders[name_norm(w)] for w in re.findall(r"[^\s]+", name or "") if name_norm(w) in genders}
    return hits.pop() if len(hits) == 1 else None


def kin_terms(text: str) -> list[dict]:
    """Relationship words in ``text`` with category, gender and age ('younger sister' -> sibling, f, younger)."""
    toks = re.findall(r"[a-z]+", (text or "").lower())
    out = []
    for i, t in enumerate(toks):
        if t in KIN:
            cat, g, a = KIN[t]
            if a is None and i > 0 and toks[i - 1] in AGE:
                a = AGE[toks[i - 1]]
            out.append({"word": t, "cat": cat, "gender": g, "age": a})
    return out


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.;!?])\s+", text or "") if s.strip()]


def _mentions(sentence: str, name: str) -> bool:
    return bool(name) and re.search(rf"(?<![\w]){re.escape(name.lower())}(?![\w])", sentence.lower()) is not None


def clip_people(facts: dict, segments: list, scene_id: int, cast_cfg: dict | None = None) -> list[dict]:
    """[{name, key, role, n_lines, data: [kin terms from the role and the scene's relationship line]}], most lines
    first, for the speech lines kept in ``segments``."""
    roles = {s.get("key"): s.get("role") for s in ((facts.get("episode_pass") or {}).get("speakers") or [])}
    segs = [(float(a), float(b)) for a, b in segments]
    sheet = next((s for s in facts.get("beat_sheets") or [] if s["scene_id"] == scene_id), {})
    rel = ((sheet.get("dynamic") or {}).get("relationship")) or ""
    by: dict[str, dict] = {}
    for r in facts["lines"]:
        if r["kind"] != "speech" or not any(a - 1e-3 <= r["start"] and r["end"] <= b + 1e-3 for a, b in segs):
            continue
        key, name = final_speaker(facts, r["id"])
        if not name:
            continue
        p = by.setdefault(name, {"name": name, "key": key, "role": roles.get(key), "n_lines": 0})
        p["n_lines"] += 1
    people = sorted(by.values(), key=lambda p: -p["n_lines"])
    genders = cast_genders(facts, cast_cfg)
    for p in people:
        p["gender"] = gender_of(p["name"], genders)
        data = [dict(t, source="role") for t in kin_terms(p["role"] or "")]
        for s in _sentences(rel):
            if _mentions(s, p["name"]):
                data += [{"word": t["word"], "cat": t["cat"], "gender": None, "age": None, "source": "dynamic"} for t in kin_terms(s)]
        p["data"] = data
    return people


def people_line(people: list[dict], relationship: str) -> str:
    """'the people in this clip are: A (role), B (role). Relationship (beat sheet): ...'"""
    who = ", ".join(f"{p['name']} ({p['role']})" if p.get("role") else p["name"] for p in people) or "nobody speaks"
    return f"the people in this clip are: {who}." + (f" Relationship (beat sheet): {relationship}" if relationship else "")


def _contradicts(term: dict, data: list[dict], gender: str | None = None) -> str | None:
    """Why ``term`` contradicts a person's data, or None. Only the person's ROLE (the episode pass's description) can
    rule a category out; words from the scene's free-text relationship line only add allowed categories. ``gender``
    (the cast list's, when given) rules out a gendered word for the other gender."""
    if gender and term["gender"] and term["gender"] != gender:
        return f"is {'male' if gender == 'm' else 'female'} in the cast list"
    role_cats = {d["cat"] for d in data if d.get("source") == "role"}
    cats = role_cats | {d["cat"] for d in data}
    if role_cats and term["cat"] not in cats:
        return f"is {', '.join(sorted(role_cats))} in the data, not {term['cat']}"
    same = [d for d in data if d["cat"] == term["cat"]]
    if term["gender"] and any(d["gender"] and d["gender"] != term["gender"] for d in same) and not any(d["gender"] == term["gender"] for d in same):
        return f"is {'female' if term['gender'] == 'm' else 'male'} in the data ({', '.join(d['word'] for d in same if d['gender'])})"
    if term["age"] and any(d["age"] and d["age"] != term["age"] for d in same) and not any(d["age"] == term["age"] for d in same):
        return f"is the {'younger' if term['age'] == 'elder' else 'elder'} one in the data ({', '.join(d['word'] for d in same if d['age'])})"
    return None


def check_people_in_text(entries: list[dict], people: list[dict], cast_names: set[str], relationship: str) -> list[str]:
    """The model's people_in_text against the clip's people, the cast list and the data's relationships."""
    errs = []
    by = {name_norm(p["name"]): p for p in people}
    for e in entries or []:
        n = name_norm(e.get("name") or "")
        p = by.get(n)
        if p is None:
            in_rel = any(_mentions(s, e.get("name") or "") for s in _sentences(relationship))
            if n in cast_names or in_rel:
                if not in_rel:
                    errs.append(f"people_in_text names {e.get('name')!r}, who is in the cast but does not speak in this clip "
                                f"(the people in it: {', '.join(x['name'] for x in people)})")
                continue
            errs.append(f"people_in_text names {e.get('name')!r}, who is neither in this clip nor in the cast list")
            continue
        for t in kin_terms(e.get("relationship") or ""):
            why = _contradicts(t, p["data"], p.get("gender"))
            if why:
                errs.append(f"people_in_text calls {p['name']} {t['word']!r}, but {p['name']} {why} (role: {p.get('role')})")
    return errs


def check_text_kin(field: str, text: str, people: list[dict], relationship: str = "") -> list[str]:
    """A relationship word in on-screen text needs someone it can be true of: flagged only when every person in the
    clip who has data in that category contradicts it (e.g. 'akka' when the only sister is the younger one) AND the
    scene's relationship line does not name such a person (a caption may talk about a father who does not speak)."""
    errs = []
    scene_terms = kin_terms(relationship)
    for t in kin_terms(text):
        if any(x["cat"] == t["cat"] and (not t["gender"] or not x["gender"] or x["gender"] == t["gender"]) for x in scene_terms):
            continue
        holders = [p for p in people if any(d["cat"] == t["cat"] for d in p["data"])]
        if holders and all(_contradicts(t, p["data"]) for p in holders):
            errs.append(f"{field} says {t['word']!r}, which fits nobody in the clip: " + "; ".join(f"{p['name']} {_contradicts(t, p['data'])}" for p in holders))
    return errs


def cast_name_set(facts: dict) -> set[str]:
    out = set()
    for k, aliases in (((facts.get("cast_list") or {}).get("names")) or {}).items():
        for a in [k] + list(aliases or []):
            out.add(name_norm(a))
    for s in ((facts.get("episode_pass") or {}).get("speakers") or []):
        if s.get("name"):
            out.add(name_norm(s["name"]))
    return out
