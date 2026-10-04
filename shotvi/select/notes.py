"""Editor's notes: who is in the episode, what it is about, which topics are of public interest (web-grounded).

usage: python -m shotvi.select.notes <video_id> [...] [--max-searches 2] [--budget-usd 0.05]

Transcript-driven (founder's design):
1. map call (flash-lite, no search) reads the whole compact episode map: brief + on-mic identity evidence + up to 8
   lookup questions (one identity question) + up to 2 current-interest questions, each tied to unit ids;
2. one grounded plain-text research call (3.8-flash + Google Search) answers the questions (search cap in the prompt;
   searches are metered at the conservative paid rate);
3. formatter call (flash-lite, no search) builds the notes JSON citing S-ids of the research call's grounding chunks;
4. code checks: S-ids must be grounding chunks of THIS episode's search; source classes (strong = encyclopedia /
   official / major news, weak = blogs / social / video, self = the episode's own page); a fact whose only source is
   the episode's own page is dropped; weak-only facts are "unconfirmed"; relations are "verify"; allegations go to a
   flagged list only; the guest name must match the title/description or the host's on-mic address; facts the
   transcript contradicts are excluded (transcript wins). No view or engagement data is requested or kept.
Saved to runs/editor_notes/<id>.json.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from shotvi.llm import Ledger, LLMClient, ModelSpec

from .config import load_config
from .context import load_ctx
from .guest import ALLEGATION, cite_notes, conflict_check, load_meta, name_candidates, name_matches, resolve
from .prompts import load_prompt

OUT = Path("runs/editor_notes")
LITE = "gemini-3.5-flash-lite"
STRONG_SITES = ("wikipedia.org", "britannica.com", "thehindu.com", "indianexpress.com", "newindianexpress.com",
                "timesofindia.indiatimes.com", "hindustantimes.com", "ndtv.com", "deccanchronicle.com",
                "thenewsminute.com", "livemint.com", "economictimes.indiatimes.com", "business-standard.com",
                "moneycontrol.com", "reuters.com", "bbc.com", "imdb.com", "eenadu.net", "sakshi.com", "gov.in",
                "indiatoday.in", "cinemaexpress.com", "nih.gov", "who.int", "thelancet.com", "bmj.com", "nature.com")
WEAK_SITES = ("youtube.com", "youtu.be", "facebook.com", "instagram.com", "x.com", "twitter.com", "threads.net",
              "linkedin.com", "medium.com", "blogspot.com", "wordpress.com", "mystrikingly.com", "quora.com",
              "reddit.com", "famousbirthdays.com", "vercel.app")

MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "identity_evidence": {"type": "array", "items": {"type": "object", "properties": {
            "quote": {"type": "string"}, "unit_id": {"type": "integer"}}, "required": ["quote", "unit_id"]}},
        "brief": {"type": "object", "properties": {
            "guest": {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
                      "required": ["name", "role"]},
            "host": {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
                     "required": ["name", "role"]},
            "topics": {"type": "array", "items": {"type": "object", "properties": {
                "label": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
                "unit_ids": {"type": "array", "items": {"type": "integer"}}}, "required": ["label", "start", "end"]}},
            "key_names": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "what": {"type": "string"}}, "required": ["name", "what"]}},
            "recurring": {"type": "array", "items": {"type": "string"}},
            "known_for": {"type": "string"}},
            "required": ["guest", "host", "topics", "key_names", "recurring", "known_for"]},
        "questions": {"type": "array", "items": {"type": "object", "properties": {
            "q": {"type": "string"}, "kind": {"type": "string", "enum": ["identity", "lookup", "current_interest"]},
            "why": {"type": "string"}, "unit_ids": {"type": "array", "items": {"type": "integer"}}},
            "required": ["q", "kind", "why", "unit_ids"]}},
    },
    "required": ["identity_evidence", "brief", "questions"],
}
FACT = {"type": "object", "properties": {"text": {"type": "string"},
                                         "sources": {"type": "array", "items": {"type": "string"}}},
        "required": ["text", "sources"]}
NOTES_SCHEMA = {
    "type": "object",
    "properties": {
        "guest": {"type": "object", "properties": {
            "name_en": {"type": "string"}, "name_te": {"type": "string"},
            "roles": {"type": "array", "items": FACT}, "known_for": {"type": "array", "items": FACT}},
            "required": ["name_en", "name_te", "roles", "known_for"]},
        "host": {"type": "object", "properties": {"name": {"type": "string"},
                                                  "facts": {"type": "array", "items": FACT}},
                 "required": ["name", "facts"]},
        "channel": {"type": "object", "properties": {"name": {"type": "string"},
                                                     "facts": {"type": "array", "items": FACT}},
                    "required": ["name", "facts"]},
        "people": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "relation": {"type": "string"}, "facts": {"type": "array", "items": FACT}},
            "required": ["name", "relation", "facts"]}},
        "interest": {"type": "array", "items": {"type": "object", "properties": {
            "topic": {"type": "string"}, "why": {"type": "string"},
            "sources": {"type": "array", "items": {"type": "string"}},
            "unit_ids": {"type": "array", "items": {"type": "integer"}}},
            "required": ["topic", "why", "sources", "unit_ids"]}},
        "correct_spellings": {"type": "array", "items": {"type": "object", "properties": {
            "canonical": {"type": "string"}, "kind": {"type": "string"},
            "asr_variants": {"type": "array", "items": {"type": "string"}}},
            "required": ["canonical", "kind", "asr_variants"]}},
        "claims_to_flag": {"type": "array", "items": {"type": "object", "properties": {
            "claim": {"type": "string"}, "kind": {"type": "string", "enum": ["health", "finance", "legal", "other"]},
            "unit_ids": {"type": "array", "items": {"type": "integer"}}, "web_note": {"type": "string"},
            "sources": {"type": "array", "items": {"type": "string"}}},
            "required": ["claim", "kind", "unit_ids", "web_note", "sources"]}},
        "allegations": {"type": "array", "items": FACT},
        "not_confident": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["guest", "host", "channel", "people", "interest", "correct_spellings", "claims_to_flag",
                 "allegations", "not_confident"],
}


def _domain(u: str) -> str:
    h = urlparse(u if "://" in u else "https://" + u).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def classify(src: dict, vid: str, official_tokens: set[str]) -> str:
    url = src.get("resolved") or src.get("uri", "")
    d = _domain(url) or _domain("https://" + src.get("title", ""))
    if vid in url:
        return "self"
    if any(d == s or d.endswith("." + s) for s in STRONG_SITES):
        return "strong"
    if any(d == s or d.endswith("." + s) for s in WEAK_SITES):
        return "weak"
    base = d.split(".")[0] if d else ""
    if base and any(t in base for t in official_tokens if len(t) >= 4):
        return "strong"   # official site of the guest / company / channel (domain contains their name)
    return "weak"         # unknown sites (blogs, aggregators) count as weak


def verify(notes: dict, sources: list[dict], vid: str, official_tokens: set[str]) -> list[dict]:
    """In place. Returns dropped items with reasons."""
    by_id = {x["id"]: x for x in sources}
    for x in sources:
        x["class"] = classify(x, vid, official_tokens)
    dropped = []

    def check(item: dict, where: str, text_key: str = "text") -> bool:
        ids = [str(s).strip().strip("[]").upper() for s in item.get("sources") or []]
        ok_ids = [i for i in ids if i in by_id]
        if not ok_ids:
            dropped.append({"where": where, "item": item, "why": f"cites {ids or 'nothing'}: no grounding chunk"})
            return False
        classes = {by_id[i]["class"] for i in ok_ids}
        if classes == {"self"}:
            dropped.append({"where": where, "item": item, "why": "only source is the episode's own page"})
            return False
        item["sources"] = ok_ids
        item["source_urls"] = [by_id[i].get("resolved") or by_id[i]["uri"] for i in ok_ids]
        item["status"] = "confirmed" if "strong" in classes else "unconfirmed"
        return True

    flagged = []

    def keep(lst, where):
        out = []
        for f in lst or []:
            if not check(f, where):
                continue
            if ALLEGATION.search(f.get("text", "")):
                flagged.append({**f, "moved_from": where})
                continue
            out.append(f)
        return out

    g = notes.setdefault("guest", {})
    for k in ("roles", "known_for"):
        g[k] = keep(g.get(k), f"guest.{k}")
    for sec in ("host", "channel"):
        notes.setdefault(sec, {})["facts"] = keep(notes.get(sec, {}).get("facts"), f"{sec}.facts")
    people = []
    for p in notes.get("people", []):
        p["facts"] = keep(p.get("facts"), f"people[{p.get('name')}]")
        if p["facts"]:
            p["relation_status"] = "verify"   # family / personal relationships are always checked by a human
            people.append(p)
    notes["people"] = people
    notes["interest"] = [x for x in notes.get("interest", []) if check(x, "interest")]
    notes["claims_to_flag"] = [x for x in notes.get("claims_to_flag", []) if check(x, "claims_to_flag")]
    for c in notes["claims_to_flag"]:
        c["flag"] = "flag for creator review"
    notes["allegations"] = [x for x in notes.get("allegations", []) if check(x, "allegations")] + flagged
    for a in notes["allegations"]:
        a["flag"] = "flag for creator review (never in titles or why)"
    return dropped


NICK = re.compile(r"(?:\bas|called|nicknamed|dubbed|title of)\s+(?:the\s+)?(?=[A-Z])"
                  r"([^,.;()]+)", re.I)
QUOTED = re.compile(r"[\"'\u2018\u2019\u201c\u201d]([^\"'\u2018\u2019\u201c\u201d]{3,40})[\"'\u2018\u2019\u201c\u201d]")
PAGES = OUT / "pages"


def page_text(url: str) -> str:
    """Plain-HTTP fetch of a cited page (no LLM), tags stripped, cached on disk. '' if it cannot be fetched."""
    import hashlib
    import html as _html
    PAGES.mkdir(parents=True, exist_ok=True)
    f = PAGES / (hashlib.sha256(url.encode()).hexdigest()[:20] + ".txt")
    if f.exists():
        return f.read_text(encoding="utf-8")
    try:
        import requests
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (shotvi fact check)"})
        t = r.text if r.status_code == 200 else ""
    except Exception:  # noqa: BLE001 - unverifiable page = unsupported phrase
        t = ""
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
    t = _html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = " ".join(t.split())
    f.write_text(t, encoding="utf-8")
    return t


def nickname_phrases(text: str) -> list[str]:
    out = [m.strip() for m in QUOTED.findall(text)]
    for m in NICK.findall(text):
        for part in re.split(r"\s+and\s+(?:the\s+)?|\s+or\s+", m):
            part = part.strip(" \"'\u2018\u2019\u201c\u201d")
            if part and part[0].isupper() and len(part.split()) <= 6:
                out.append(part)
    return list(dict.fromkeys(out))


def verify_phrases(notes: dict, sources: list[dict]) -> list[dict]:
    """A nickname or quoted name in a fact must appear verbatim (case-insensitive) in the text of at least one of
    the fact's cited pages; otherwise the fact is dropped as unsupported."""
    by_id = {x["id"]: x for x in sources}
    dropped = []

    def ok(f, where):
        phrases = nickname_phrases(f.get("text", ""))
        if not phrases:
            return True
        texts = [page_text(by_id[i].get("resolved") or by_id[i]["uri"]).lower() for i in f.get("sources", [])
                 if i in by_id]
        missing = [ph for ph in phrases if not any(ph.lower() in t for t in texts)]
        if missing:
            dropped.append({"where": where, "item": f, "why": f"phrase(s) {missing} not found in the text of the "
                                                             f"cited page(s)"})
            return False
        f["phrase_check"] = f"found in cited page: {phrases}"
        return True

    g = notes.get("guest", {})
    for k in ("roles", "known_for"):
        g[k] = [f for f in g.get(k, []) if ok(f, f"guest.{k}")]
    for sec in ("host", "channel"):
        notes.get(sec, {})["facts"] = [f for f in notes.get(sec, {}).get("facts", []) if ok(f, f"{sec}.facts")]
    for pp in notes.get("people", []):
        pp["facts"] = [f for f in pp.get("facts", []) if ok(f, f"people[{pp.get('name')}]")]
    notes["people"] = [pp for pp in notes.get("people", []) if pp["facts"]]
    notes["interest"] = [x for x in notes.get("interest", []) if ok({**x, "text": x.get("why", "")}, "interest")]
    return dropped


def research_text(meta: dict, questions: list[dict], max_searches: int) -> str:
    qs = "\n".join(f"{k + 1}. {q['q']}" for k, q in enumerate(questions))
    return (f"Search the web to answer these questions about a Telugu podcast episode titled \"{meta.get('title', '')}\" "
            f"on the channel \"{meta.get('channel', '')}\" (uploaded {meta.get('upload_date', '')}). Description "
            f"excerpt: {(meta.get('description') or '')[:500]!r}\n\nQuestions:\n{qs}\n\n"
            f"Answer each question with short bullet facts as of the upload date. Leave out anything you cannot find on "
            f"a web page; do not guess relationships, ages or money figures. Do not report view counts, subscriber "
            f"numbers or engagement statistics. Use at most {max_searches} searches.")


def build_notes(vid: str, cfg: dict, llm, max_searches: int = 2, conflict: bool = True, n_lookup: int = 7,
                n_interest: int = 2) -> dict:
    meta = load_meta(vid)
    ctx = load_ctx(f"scribe_dataset/{vid}/{vid}_scribe_transcript.json", cfg)
    full = ctx.seg.render(legend=True)
    legend, _, body = full.partition("\n\n")
    pm = load_prompt("notes_map")
    lite = ModelSpec("gemini", LITE, {"thinking_level": "low"})
    mp = llm.generate_json("notes_map", lite, pm.system, pm.render(
        title=meta.get("title", ""), channel=meta.get("channel", ""), upload_date=meta.get("upload_date", ""),
        description=(meta.get("description") or "")[:1500], legend=legend, transcript=body), MAP_SCHEMA,
        meta={"video": vid})
    look = [q for q in mp.get("questions", []) if q.get("kind") == "lookup"]
    if pm.version == "1":   # v1 rounds: identity first, lookups by units affected
        qs = [q for q in mp.get("questions", []) if q.get("kind") == "identity"][:1]
        qs += sorted(look, key=lambda q: -len(q.get("unit_ids") or []))[:n_lookup]
    else:                   # v2 (founder's focus): mentioned people/companies/events first, in the model's priority
        qs = look[:n_lookup] + [q for q in mp.get("questions", []) if q.get("kind") == "identity"][:1]
    qs += [q for q in mp.get("questions", []) if q.get("kind") == "current_interest"][:n_interest]
    spec1 = ModelSpec("gemini", "gemini-3.8-flash", {"thinking_level": "low", "google_search": True})
    text, raw = llm.generate_json("notes_research", spec1, "", research_text(meta, qs, max_searches), None,
                                  meta={"video": vid}, want_raw=True, text_ok=True)
    cited, sources = cite_notes(str(text), raw.grounding)
    for x in sources:
        x["resolved"] = resolve(x["uri"])
    res = {"video_id": vid, "upload_date": meta.get("upload_date"), "map": mp, "questions": qs,
           "search_queries": (raw.grounding or {}).get("queries", []), "research_notes": cited,
           "sources": sources, "prompts": {"notes_map": pm.tag}}
    if not sources:
        res.update({"notes": None, "dropped": [{"where": "notes", "why": "research call did not search"}]})
        return res
    pf = load_prompt("notes_format")
    cands = name_candidates(ctx)
    brief = mp.get("brief", {})
    notes = llm.generate_json("notes_format", lite, pf.system, pf.render(
        title=meta.get("title", ""), channel=meta.get("channel", ""), upload_date=meta.get("upload_date", ""),
        brief=json.dumps(brief, ensure_ascii=False), candidates="\n".join(f"{t} ({n})" for t, n in cands),
        notes=cited, sources="\n".join(f"{x['id']}: {x['title']}" for x in sources)), NOTES_SCHEMA,
        meta={"video": vid})
    res["prompts"]["notes_format"] = pf.tag
    g = notes.get("guest", {})
    meta_text = f"{meta.get('title', '')}\n{meta.get('description', '')}"
    ok, why = name_matches(g.get("name_en", ""), g.get("name_te", ""), meta_text)
    if not ok:   # host's on-mic address is identity evidence too (verbatim in the transcript)
        tr = " ".join(w.text for w in ctx.words)
        for ev in mp.get("identity_evidence", []):
            qte = " ".join(str(ev.get("quote", "")).split())
            if qte and qte in tr:
                ok2, why2 = name_matches(g.get("name_en", ""), g.get("name_te", ""), qte)
                if ok2:
                    ok, why = True, f"on-mic address in the transcript: \"{qte}\" ({why2})"
                    break
    res["name_check"] = {"ok": ok, "why": why}
    if not ok:
        res.update({"notes": None, "dropped": [{"where": "notes", "why": "guest name mismatch: " + why}]})
        return res
    official = {t for t in re.findall(r"[a-z]+", (g.get("name_en", "") + " " + notes.get("channel", {}).get("name", "")
                                                    + " " + meta.get("channel", "")).lower())}
    dropped = verify(notes, sources, vid, official)
    dropped += verify_phrases(notes, sources)
    present = {w.text.strip(".,?!\"'()") for w in ctx.words}
    for s in notes.get("correct_spellings", []):
        s["asr_variants"] = [v for v in s.get("asr_variants", []) if v != s.get("canonical") and
                             (v in present or all(t in present for t in v.split()))]
    if conflict:
        # transcript wins: facts the transcript contradicts are excluded (guest.conflict_check reads the same fields)
        prof = {"guest": notes["guest"], "host": notes["host"], "channel": notes["channel"],
                "people": notes["people"], "sensitive": [], "correct_spellings": notes["correct_spellings"]}
        for f in prof["guest"].get("roles", []) + prof["guest"].get("known_for", []):
            f.setdefault("source", (f.get("sources") or [""])[0])
        dropped += conflict_check(prof, ctx, llm, lite)
    notes["topics"] = brief.get("topics", [])
    notes["recurring"] = brief.get("recurring", [])
    res.update({"notes": notes, "dropped": dropped, "name_check": res["name_check"]})
    return res


def notes_text(res: dict | None, interest: bool = True) -> str:
    """Compact text of verified notes for the writer / second discovery / interest prompts. Never includes
    allegations or flagged claims."""
    n = (res or {}).get("notes")
    if not n:
        return ""
    g = n.get("guest", {})
    L = [f"Guest: {g.get('name_en', '')} ({g.get('name_te', '')})"
         + (": " + "; ".join(f["text"] for f in g.get("roles", [])) if g.get("roles") else "")]
    if g.get("known_for"):
        L.append("Known for: " + "; ".join(f["text"] + ("" if f.get("status") == "confirmed" else " (unconfirmed)")
                                           for f in g["known_for"]))
    if n.get("host", {}).get("name"):
        L.append(f"Host: {n['host']['name']}")
    if n.get("channel", {}).get("name"):
        L.append(f"Channel / company: {n['channel']['name']}")
    if n.get("people"):
        L.append("People: " + "; ".join(f"{p['name']} ({p['relation']}, verify)" for p in n["people"]))
    if n.get("topics"):
        L.append("Topics: " + "; ".join(f"{t['start']}-{t['end']} {t['label']}" for t in n["topics"][:10]))
    if n.get("recurring"):
        L.append("Recurring stories: " + "; ".join(n["recurring"][:6]))
    if interest and n.get("interest"):
        L.append("Topics of public interest: " + "; ".join(f"{x['topic']} ({x['why']})" for x in n["interest"]))
    sp = [s for s in n.get("correct_spellings", [])]
    if sp:
        L.append("Name spellings (canonical; Telugu script is fine in titles): " + "; ".join(
            s["canonical"] + (f" (in the transcript as: {', '.join(s['asr_variants'])})" if s.get("asr_variants")
                              else "") for s in sp))
    return "\n".join(L)


def load_notes(vid: str) -> dict | None:
    f = OUT / f"{vid}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="+")
    ap.add_argument("--max-searches", type=int, default=2)
    ap.add_argument("--lookups", type=int, default=7, help="lookup questions sent to research (most units first)")
    ap.add_argument("--interest-qs", type=int, default=2)
    ap.add_argument("--opening-mode", default="rules", help="segment opening detector (must match the select run)")
    ap.add_argument("--budget-usd", type=float, default=0.06)
    a = ap.parse_args(argv)
    load_dotenv()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config(None, {"segment_opening_mode": a.opening_mode})
    OUT.mkdir(parents=True, exist_ok=True)
    for vid in a.videos:
        ledger = Ledger(max_usd=a.budget_usd)
        llm = LLMClient(ledger, cache_dir=cfg["cache_dir"])
        res = build_notes(vid, cfg, llm, a.max_searches, n_lookup=a.lookups, n_interest=a.interest_qs)
        res["cost"] = ledger.summary()
        target = OUT / f"{vid}.json"
        if not res.get("notes") and target.exists() and (load_notes(vid) or {}).get("notes"):
            target = OUT / f"{vid}.failed.json"   # never overwrite good notes with a failed run
        target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        n = res.get("notes") or {}
        print(f"{vid}: searches {len(res['search_queries'])} sources {len(res['sources'])} | "
              f"name {res.get('name_check')} | interest {len(n.get('interest', []))} | "
              f"flagged claims {len(n.get('claims_to_flag', []))} | dropped {len(res.get('dropped', []))} | "
              f"${res['cost']['usd']:.4f}")


if __name__ == "__main__":
    main()
