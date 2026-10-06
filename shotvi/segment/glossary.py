"""Series glossary for ElevenLabs Scribe ``keyterms``: built from episodes already processed, passed when
transcribing the next episode of the same series.

No LLM. Frequency + simple rules over the prior episodes' Scribe transcripts (with their applied ASR corrections),
their YouTube titles, and an optional *background* corpus (transcripts of unrelated videos). The background tells
general Telugu/Tenglish (pronouns, verbs, "okay") from words that are specific to this series (character names,
dishes, running gags). The episode being transcribed must never be an input: not as a prior episode, not as
background.

Term kinds (each term keeps every source that produced it):
  ``correction``  the corrected text of an applied ASR correction (Scribe misheard it once; bias it next time)
  ``name``        a token directly before an honorific/kinship address ("అమృత గారు"), meta title names, caller names
  ``term``        a Telugu or English token that recurs in the series and is rare in the background
  ``english``     an English word that never occurs in the background (domain vocabulary, e.g. dishes)
  ``phrase``      a 2-4 word n-gram that recurs in the series and carries at least one series-specific token
  ``manual``      added by hand (the caller passes them with a reason)

Words a correction replaced (what Scribe wrongly heard) and low-confidence words are never terms: biasing toward a
past mishearing would repeat it.

API limits (ElevenLabs speech-to-text, checked 2026-10-06): at most 1000 terms, each < 50 characters, at most
5 words, none of ``< > { } [ ] \\``; +20% cost.

CLI::

    python -m shotvi.segment.glossary --prior j14Om1SaFYE --target QWIvgoTQGKk --out runs/keyterms/x.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import lexicon as L
from .text import norm, script_of

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "glossary/1"

MAX_TERMS_API = 1000
MAX_CHARS = 49            # "less than 50 characters"
MAX_WORDS = 5
FORBIDDEN = set("<>{}[]\\")

MIN_COUNT = 2             # a series term must recur
MIN_RATIO = 8.0           # series rate / background rate, for ``term`` and phrase anchors
MIN_CONF = 0.7            # mean word confidence of a term's occurrences
MIN_TE_LEN = 3            # code points; shorter Telugu tokens are particles/interjections
MIN_EN_LEN = 3
MIN_EN_HAPAX_LEN = 4
COMMON_TOP_K = 300        # the background's most frequent tokens are function words
MAX_BG_PER_10K = 1.0     # a ``term``/phrase anchor must also be rare in absolute terms in the background
MAX_NGRAM = 5
CORRECTION_MAX_BG_PER_10K = 1.0   # a correction to a very common word ("ఏమి") is not a glossary term
DEFAULT_MAX_TERMS = 300

# Only unambiguous honorifics: "అన్న", "అమ్మ", "sir" also follow ordinary words ("ఊరికే sir").
HONORIFICS = {"గారు", "గారూ", "గారి", "గారికి", "గారిని", "గారితో", "గారిది", "garu", "gaaru"}
EN_STOP = {
    "a", "an", "the", "and", "or", "but", "so", "if", "of", "to", "in", "on", "at", "for", "with", "from", "by",
    "is", "am", "are", "was", "were", "be", "been", "being", "do", "does", "did", "have", "has", "had", "will",
    "would", "can", "could", "should", "shall", "may", "might", "must", "i", "me", "my", "we", "us", "our",
    "you", "your", "he", "him", "his", "she", "her", "it", "its", "they", "them", "their", "this", "that",
    "these", "those", "what", "which", "who", "when", "where", "why", "how", "not", "no", "yes", "ok", "okay",
    "all", "any", "some", "one", "two", "very", "just", "also", "then", "there", "here", "about", "like",
    "let's", "lets", "it's", "i'm", "don't", "dont", "want", "make", "made", "say", "thing", "something",
    "everything", "whatever", "time", "day", "good", "best", "please", "sorry", "hello", "bye", "super",
    "thank", "thanks", "really", "actually", "basically", "only", "more", "much", "many", "think", "know",
    "get", "got", "go", "going", "come", "see", "look", "tell", "said", "okay", "hi",
}
_SPLIT = re.compile(r"\s+")


@dataclass
class Episode:
    """One prior episode. ``corrections`` = runs/asr_fix/<id>/corrections.json (or None); ``meta`` =
    youtube_meta.json (only the title is read: the description is mostly crew credits, never spoken)."""
    video_id: str
    transcript: dict
    corrections: dict | None = None
    meta: dict | None = None


@dataclass
class _Tok:
    n: str            # normalized
    conf: float       # 1.0 when the word came from a verified correction or has no logprob
    sent: int         # sentence id, n-grams never cross it


@dataclass
class _Term:
    term: str
    kind: str
    sources: list = field(default_factory=list)
    count: int = 0
    bg_count: int = 0
    score: float = 0.0

    def as_dict(self):
        return {"term": self.term, "kind": self.kind, "sources": sorted(set(self.sources)), "count": self.count,
                "bg_count": self.bg_count, "score": round(self.score, 2)}


def clean_term(t: str) -> str | None:
    """Normalize a candidate to API-legal form: punctuation stripped per word, forbidden chars removed,
    <= 5 words, < 50 chars. None if nothing usable is left."""
    words = [norm(w) for w in _SPLIT.split(t or "")]
    words = ["".join(ch for ch in w if ch not in FORBIDDEN) for w in words]
    words = [w for w in words if w]
    if not words or len(words) > MAX_WORDS:
        return None
    s = " ".join(words)
    if len(s) > MAX_CHARS:
        return None
    return s


def _conf(w: dict) -> float:
    lp = w.get("logprob")
    return 1.0 if lp is None else math.exp(min(0.0, float(lp)))


def _sent_of(tr: dict) -> list[int]:
    n = len(tr.get("word_timestamps") or [])
    out = list(range(n))       # no sentences: every word alone (only single tokens count then)
    for s in tr.get("sentences") or []:
        a, b = s["words"]
        for i in range(a, min(b, n - 1) + 1):
            out[i] = 10_000_000 + s["id"]
    return out


def episode_tokens(ep: Episode) -> tuple[list[_Tok], set[str]]:
    """Tokens of an episode with applied corrections in place, plus the normalized tokens that corrections
    replaced (misheard words: never glossary terms)."""
    words = ep.transcript.get("word_timestamps") or []
    sent = _sent_of(ep.transcript)
    corr = [c for c in ((ep.corrections or {}).get("corrections") or []) if c.get("applied")]
    by_start = {}
    misheard = set()
    for c in corr:
        a, b = int(c["start_word"]), int(c["end_word"])
        if 0 <= a <= b < len(words):
            by_start[a] = (b, c["corrected"])
            for w in _SPLIT.split(c.get("heard") or ""):
                if norm(w):
                    misheard.add(norm(w))
    toks, i = [], 0
    while i < len(words):
        if i in by_start:
            b, text = by_start[i]
            for w in _SPLIT.split(text):
                if norm(w):
                    toks.append(_Tok(norm(w), 1.0, sent[i]))
            i = b + 1
            continue
        n = norm(words[i].get("word", ""))
        if n:
            toks.append(_Tok(n, _conf(words[i]), sent[i]))
        i += 1
    # a misheard form that also occurs as a verified correction elsewhere stays usable
    verified = {norm(w) for c in corr for w in _SPLIT.split(c["corrected"])}
    return toks, misheard - verified


def background_counts(transcripts) -> Counter:
    c = Counter()
    for tr in transcripts:
        for w in tr.get("word_timestamps") or []:
            n = norm(w.get("word", ""))
            if n:
                c[n] += 1
    return c


def title_names(meta: dict | None) -> list[str]:
    """Name-like segments of a YouTube title: "Show || Episode - 2 || Actor || Actor || Telugu Web Series ||
    Channel" -> the show and the people. Generic segments (episode numbers, genre words, the channel) dropped."""
    if not meta or not meta.get("title"):
        return []
    channel = norm(meta.get("channel") or "")
    generic = re.compile(r"(episode|ep\b|part|season|telugu|web ?series|short ?film|full|promo|trailer|podcast|"
                         r"interview|media|official|latest|new|20\d\d|#)", re.I)
    out = []
    for seg in re.split(r"\|\||\|", meta["title"]):
        s = seg.strip()
        if not s or generic.search(s) or norm(s) == channel or len(s.split()) > 4:
            continue
        if sum(ch.isalpha() for ch in s) < 3:
            continue
        out.append(s)
    return out


def build_glossary(episodes: list[Episode], background=(), names=(), manual=(), drop=(),
                   max_terms: int = DEFAULT_MAX_TERMS, min_count: int = MIN_COUNT, min_ratio: float = MIN_RATIO,
                   min_conf: float = MIN_CONF) -> dict:
    """``background``: transcripts of unrelated videos. ``names``: [(name, source)] from the caller (e.g. a
    character map). ``manual``: [(term, reason)] hand additions. ``drop``: [(term, reason)] hand removals
    (e.g. a mishearing no correction caught). Returns the glossary dict (``terms`` ranked)."""
    if not episodes:
        raise ValueError("a glossary needs at least one prior episode")
    bg = background_counts(background)
    n_bg = sum(bg.values()) or 1
    common = {t for t, _ in bg.most_common(COMMON_TOP_K)}

    toks, misheard = [], set()
    per_ep = []
    for ep in episodes:
        t, m = episode_tokens(ep)
        per_ep.append((ep, t))
        toks += t
        misheard |= m
    n_s = len(toks) or 1
    cnt = Counter(t.n for t in toks)
    confs = defaultdict(list)
    for t in toks:
        confs[t.n].append(t.conf)
    mean_conf = {k: sum(v) / len(v) for k, v in confs.items()}
    eps_with = defaultdict(set)
    for ep, ts in per_ep:
        for t in ts:
            eps_with[t.n].add(ep.video_id)

    def ratio(tok: str) -> float:
        return (cnt[tok] / n_s) / ((bg[tok] + 0.5) / n_bg)

    def specific(tok: str) -> bool:
        """Recurs in the series much more than in the background, and is rare there in absolute terms."""
        return ratio(tok) >= min_ratio and bg[tok] / n_bg * 10_000 <= MAX_BG_PER_10K

    def is_lexical(tok: str) -> bool:
        if tok in common or tok in L.FILLERS or tok in L.BACKCHANNEL_TOKENS or tok in misheard:
            return False
        sc = script_of(tok)
        if sc == "latin":
            return len(tok) >= MIN_EN_LEN and tok not in EN_STOP
        if sc in ("te", "mixed"):
            return len(tok) >= MIN_TE_LEN
        return False

    terms: dict[str, _Term] = {}

    def add(raw: str, kind: str, source: str, score: float, count: int = 0):
        t = clean_term(raw)
        if not t:
            return
        cur = terms.get(t)
        if cur is None:
            bgc = bg[t] if " " not in t else 0
            terms[t] = _Term(t, kind, [source], count, bgc, score)
        else:
            cur.sources.append(source)
            cur.score = max(cur.score, score)
            cur.count = max(cur.count, count)

    # 1. applied corrections
    for ep in episodes:
        for c in (ep.corrections or {}).get("corrections") or []:
            if not c.get("applied"):
                continue
            t = clean_term(c["corrected"])
            if not t:
                continue
            ws = t.split()
            if len(ws) == 1 and bg[t] / n_bg * 10_000 > CORRECTION_MAX_BG_PER_10K:
                continue
            if len(ws) == 1 and (t in L.FILLERS or t in L.BACKCHANNEL_TOKENS):
                continue
            add(t, "correction", f"correction:{ep.video_id}:w{c['start_word']}", 100.0, cnt.get(t, 1))

    # 2. names: token before an honorific; title names; caller names
    for ep, ts in per_ep:
        for a, b in zip(ts, ts[1:]):
            if b.n in HONORIFICS and a.sent == b.sent and a.conf >= min_conf and is_lexical(a.n) \
                    and bg[a.n] / n_bg * 10_000 <= MAX_BG_PER_10K and a.n not in HONORIFICS:
                add(a.n, "name", f"honorific:{ep.video_id}", 90.0, cnt[a.n])
        for nm in title_names(ep.meta):
            add(nm, "name", f"meta_title:{ep.video_id}", 80.0)
    for nm, src in names:
        add(nm, "name", src, 95.0)

    # 3. single tokens: recurring and series-specific
    for tok, c in cnt.items():
        if c < min_count or not is_lexical(tok) or mean_conf[tok] < min_conf:
            continue
        if specific(tok):
            add(tok, "term", "freq:" + ",".join(sorted(eps_with[tok])), 10.0 * math.log(ratio(tok)) + c, c)

    # 4. English words absent from the background (domain vocabulary), even if said once
    for tok, c in cnt.items():
        if script_of(tok) != "latin" or tok in EN_STOP or len(tok) < MIN_EN_HAPAX_LEN or bg[tok] > 0:
            continue
        if tok in misheard or mean_conf[tok] < max(min_conf, 0.85) or not tok.isalpha():
            continue
        add(tok, "english", "english_rare:" + ",".join(sorted(eps_with[tok])), 20.0 + c, c)

    # 5. recurring n-grams with a series-specific anchor
    grams = Counter()
    gram_conf = defaultdict(list)
    for ep, ts in per_ep:
        for n in range(2, MAX_NGRAM + 1):
            for i in range(len(ts) - n + 1):
                win = ts[i:i + n]
                if len({t.sent for t in win}) > 1:
                    continue
                g = tuple(t.n for t in win)
                grams[g] += 1
                gram_conf[g].append(min(t.conf for t in win))
    kept = []
    for g, c in grams.items():
        if c < min_count or len(set(g)) < len(g):
            continue
        if sum(gram_conf[g]) / len(gram_conf[g]) < min_conf or any(t in misheard for t in g):
            continue
        if g[0] in L.FILLERS or g[-1] in L.FILLERS or g[0] in L.BACKCHANNEL_TOKENS:
            continue
        anchors = [t for t in g if is_lexical(t) and specific(t)]
        if not anchors:
            continue
        kept.append((g, c, max(ratio(t) for t in anchors)))
    # drop an n-gram contained in a longer kept one that recurs as often, and sliding windows of one longer
    # repeat ("have the best meals" / "the best meals with"): same count, overlapping by all but one token
    kept.sort(key=lambda x: (-len(x[0]), -x[1], x[0]))
    final = []
    for g, c, r in kept:
        s = " ".join(g)
        if any(c <= c2 and f" {s} " in f" {' '.join(g2)} " for g2, c2, _ in final):
            continue
        if any(c == c2 and len(g2) == len(g) and (g[1:] == g2[:-1] or g[:-1] == g2[1:]) for g2, c2, _ in final):
            continue
        final.append((g, c, r))
    for g, c, r in final:
        add(" ".join(g), "phrase", "ngram:" + str(c), 10.0 * math.log(r) + c + len(g), c)

    # 6. manual
    for t, why in manual:
        add(t, "manual", f"manual:{why}", 200.0)

    dropped_by_hand = []
    for t, why in drop:
        ct = clean_term(t)
        if ct in terms:
            del terms[ct]
            dropped_by_hand.append({"term": ct, "reason": why})

    rows = sorted(terms.values(), key=lambda x: (-x.score, x.term))
    cap = min(max_terms, MAX_TERMS_API)
    dropped = len(rows) - cap
    rows = rows[:cap]
    return {
        "schema": SCHEMA,
        "episodes": [ep.video_id for ep in episodes],
        "params": {"min_count": min_count, "min_ratio": min_ratio, "min_conf": min_conf,
                   "common_top_k": COMMON_TOP_K, "max_terms": cap, "background_words": n_bg if background else 0,
                   "series_words": n_s},
        "n_terms": len(rows),
        "n_dropped_by_cap": max(0, dropped),
        "by_kind": dict(Counter(r.kind for r in rows)),
        "excluded_misheard": sorted(misheard),
        "dropped_by_hand": dropped_by_hand,
        "terms": [r.as_dict() for r in rows],
    }


def keyterms(glossary: dict) -> list[str]:
    """The API list, validated against the documented limits."""
    out, seen = [], set()
    for row in glossary.get("terms", []):
        t = clean_term(row["term"] if isinstance(row, dict) else row)
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    if len(out) > MAX_TERMS_API:
        out = out[:MAX_TERMS_API]
    return out


# ------------------------------------------------------------------ CLI


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def load_episode(video_id: str, data_dir: Path = ROOT / "scribe_dataset",
                 asr_dir: Path = ROOT / "runs" / "asr_fix") -> Episode:
    d = data_dir / video_id
    tr = _load(d / f"{video_id}_scribe_transcript.json")
    if tr is None:
        raise SystemExit(f"no transcript for {video_id}")
    return Episode(video_id, tr, _load(asr_dir / video_id / "corrections.json"), _load(d / "youtube_meta.json"))


def load_background(exclude: set[str], data_dir: Path = ROOT / "scribe_dataset") -> tuple[list[dict], list[str]]:
    trs, ids = [], []
    for p in sorted(data_dir.glob("*/*_scribe_transcript.json")):
        vid = p.parent.name
        if vid in exclude or p.parent.name == "creator_shorts":
            continue
        trs.append(json.loads(p.read_text(encoding="utf-8")))
        ids.append(vid)
    return trs, ids


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--prior", nargs="+", required=True, help="video ids of episodes already processed")
    ap.add_argument("--target", required=True, help="the episode to be transcribed: never read")
    ap.add_argument("--exclude-background", nargs="*", default=[],
                    help="more ids to keep out of the background (other episodes of the same series)")
    ap.add_argument("--names", default="", help="json file: [[name, source], ...]")
    ap.add_argument("--manual", default="", help="json file: [[term, reason], ...]")
    ap.add_argument("--drop", default="", help="json file: [[term, reason], ...]")
    ap.add_argument("--max-terms", type=int, default=DEFAULT_MAX_TERMS)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.target in a.prior:
        raise SystemExit("the target episode cannot be a prior episode")
    eps = [load_episode(v) for v in a.prior]
    bg, bg_ids = load_background({a.target, *a.prior, *a.exclude_background})
    names = [tuple(x) for x in _load(Path(a.names))] if a.names else []
    manual = [tuple(x) for x in _load(Path(a.manual))] if a.manual else []
    drop = [tuple(x) for x in _load(Path(a.drop))] if a.drop else []
    g = build_glossary(eps, bg, names=names, manual=manual, drop=drop, max_terms=a.max_terms)
    g["target"] = a.target
    g["background_ids"] = bg_ids
    g["keyterms"] = keyterms(g)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{g['n_terms']} terms {g['by_kind']} -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
