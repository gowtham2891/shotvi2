"""Does a critic's Q3 answer name the expected payoff? (founder, 2026-10-29)

Stage 5 counts the moment as found, regardless of clip time, when the critic's best-moment text
- QUOTES the payoff line: the critic writes Telugu in Latin letters ("<name> leaks", "nuvvu ivvalra naku ..."),
  so the line's Telugu-script words are transliterated and both sides are normalised (doubled letters, aspiration,
  v/w, ...) before a fuzzy token match; or
- DESCRIBES it unmistakably: at least ``min_shared`` content words in common with the beat sheet's English note for the
  payoff line ("X points the camera at Y changing behind a wall ..."), names excluded (a cold viewer
  does not know them).
Pure code, no model call: re-comparing stored answers costs nothing.
"""
from __future__ import annotations

import difflib
import math
import re

_CONS = {
    "క": "k", "ఖ": "kh", "గ": "g", "ఘ": "gh", "ఙ": "n", "చ": "ch", "ఛ": "ch", "జ": "j", "ఝ": "jh", "ఞ": "n",
    "ట": "t", "ఠ": "t", "డ": "d", "ఢ": "d", "ణ": "n", "త": "t", "థ": "th", "ద": "d", "ధ": "dh", "న": "n",
    "ప": "p", "ఫ": "ph", "బ": "b", "భ": "bh", "మ": "m", "య": "y", "ర": "r", "ఱ": "r", "ల": "l", "ళ": "l", "ఴ": "l",
    "వ": "v", "శ": "sh", "ష": "sh", "స": "s", "హ": "h",
}
_VOW = {"అ": "a", "ఆ": "aa", "ఇ": "i", "ఈ": "ee", "ఉ": "u", "ఊ": "oo", "ఋ": "ru", "ఎ": "e", "ఏ": "e", "ఐ": "ai", "ఒ": "o", "ఓ": "o", "ఔ": "au"}
_SIGN = {"ా": "aa", "ి": "i", "ీ": "ee", "ు": "u", "ూ": "oo", "ృ": "ru", "ె": "e", "ే": "e", "ై": "ai", "ొ": "o", "ో": "o", "ౌ": "au"}
_VIRAMA, _ANUSVARA, _VISARGA = "్", "ం", "ః"

_NUM = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}

# words that carry no identity of a moment (English glue, viewer vocabulary, and the commonest Telugu function words)
STOP = set("""a an the this that these those is are was were be been being it its of in on at to for from with by as and or but not no
so if then than too very just only also there here what which who whom whose when where why how about into over under up down out off
some any all each both few more most other such own same can will would could should may might must do does did doing done have has had
having get gets got getting go goes went going come comes came coming make makes made making say says said saying tell tells told see sees
seen saw look looks looked looking while after before again still even really back right left away around one two moment scene clip
video when he him his she her they them their we our you your i me my guy guys man men woman women boy girl friend friends someone person
people funny emotional hilarious cute sweet moment line says said saying asks asked asking yells yelling shouts shouting reacts reaction
laugh laughs laughing laughter
ra ro re ani ane anta ante ga gaa la lo ki ku nu ni ne naa na nee nuvvu nenu adi idi aa ee oka kuda kada kadaa ledu undi unnadu emi enti
sir madam bro""".split())


def translit(text: str) -> str:
    """Telugu script to plain Latin letters (rough, for matching only); Latin text passes through."""
    out: list[str] = []
    chars = list(text or "")
    i = 0
    while i < len(chars):
        ch = chars[i]
        if ch in _CONS:
            out.append(_CONS[ch])
            nxt = chars[i + 1] if i + 1 < len(chars) else ""
            if nxt in _SIGN:
                out.append(_SIGN[nxt]); i += 2; continue
            if nxt == _VIRAMA:
                i += 2; continue
            out.append("a")
        elif ch in _VOW:
            out.append(_VOW[ch])
        elif ch == _ANUSVARA:
            out.append("n")
        elif ch == _VISARGA:
            out.append("h")
        elif ch in _SIGN:
            out.append(_SIGN[ch])
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def norm_token(tok: str) -> str:
    """One spelling for the many ways a Telugu word is romanised: lower case, no aspiration, v=w, z=j, doubled letters
    collapsed ("ivvaali" / "ivvali" / "ivali" -> "ivali")."""
    t = translit(tok).lower()
    t = "".join(_NUM.get(c, c) for c in t)
    t = re.sub(r"[^a-z]", "", t)
    t = t.replace("w", "v").replace("z", "j").replace("sh", "s").replace("ch", "c").replace("ph", "f")
    t = re.sub(r"([kgtdpbcj])h", r"\1", t)
    t = re.sub(r"(.)\1+", r"\1", t)
    return t


def _stem(t: str) -> str:
    for suf in ("ing", "ers", "ies", "er", "es", "ed", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            t = t[: -len(suf)]
            break
    return t[:-1] if t.endswith("e") and len(t) > 4 else t


def content_tokens(text: str, names: set[str] | None = None) -> list[str]:
    toks = []
    for raw in re.findall(r"[A-Za-zఀ-౿0-9]+", text or ""):
        low = translit(raw).lower()
        if low in STOP or (names and low in names):
            continue
        n = norm_token(raw)
        if len(n) < 3 or n in STOP:
            continue
        toks.append(n)
    return toks


def _tok_match(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < 4:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def quoted_match(answer: str, line_text: str, min_ratio: float = 0.5, prev_text: str = "") -> dict:
    """The answer quotes the line: >= max(2, ceil(min_ratio x n)) of the line's DISTINCTIVE content tokens appear
    (fuzzily) in the answer; a one-token line needs that token (>= 4 letters). Tokens also in ``prev_text`` (the
    setup line just before the payoff) are not distinctive: quoting "five star rating koncham ivvandi" is the setup,
    not the payoff "nuvvu ivvaali naaku five star rating"."""
    prev = set(content_tokens(prev_text))
    want = [w for w in content_tokens(line_text) if not any(_tok_match(w, p) for p in prev)]
    have = content_tokens(answer)
    hits = [w for w in want if any(_tok_match(w, h) for h in have)]
    n = len(want)
    need = 1 if n == 1 else max(2, math.ceil(min_ratio * n))
    ok = n > 0 and len(hits) >= need and (n > 1 or len(want[0]) >= 4)
    return {"match": bool(ok), "hits": hits, "need": need, "of": n}


def description_match(answer: str, note: str, names: set[str] | None = None, min_shared: int = 3) -> dict:
    """The answer describes the beat: >= min_shared content words (stemmed, names excluded) in common with the beat
    sheet's English note for that line."""
    a = {_stem(t) for t in content_tokens(answer, names)}
    b = {_stem(t) for t in content_tokens(note, names)}
    shared = sorted(a & b)
    return {"match": len(shared) >= int(min_shared), "shared": shared}


def names_of(facts: dict) -> set[str]:
    """Lower-case Latin forms of every cast name and alias (excluded from description matching)."""
    out: set[str] = set()
    for k, aliases in (((facts.get("cast_list") or {}).get("names")) or {}).items():
        for a in [k] + list(aliases or []):
            for w in re.findall(r"[A-Za-zఀ-౿]+", a):
                out.add(translit(w).lower())
    return out
