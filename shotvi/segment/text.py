"""Token-level text helpers: normalization, script detection, question cues."""
from __future__ import annotations

import re
import unicodedata

from . import lexicon as L

_PUNCT_STRIP = " \t\"'`“”‘’.,!?;:।॥…()[]{}<>-–—*/\\|~"
SENT_END_RE = re.compile(r"[.?!।॥]+[\"'”’)]*$")
QMARK_RE = re.compile(r"\?[\"'”’)]*$")
CUTOFF_RE = re.compile(r"(-{1,2}|—|–)$")  # "కలిపి-", "విరిగి--": speaker broke off


def norm(tok: str) -> str:
    """Lowercase and strip surrounding punctuation. Keeps inner hyphens."""
    return tok.strip(_PUNCT_STRIP).lower()


def is_telugu_char(ch: str) -> bool:
    return "ఀ" <= ch <= "౿"


def is_latin_char(ch: str) -> bool:
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("À" <= ch <= "ɏ")


def script_of(tok: str) -> str:
    """'te', 'latin', 'mixed', or 'other' (digits/punct)."""
    te = lat = 0
    for ch in tok:
        if is_telugu_char(ch):
            te += 1
        elif is_latin_char(ch):
            lat += 1
    if te and lat:
        return "mixed"
    if te:
        return "te"
    if lat:
        return "latin"
    return "other"


def ends_sentence(tok: str) -> bool:
    return bool(SENT_END_RE.search(tok.strip()))


def has_qmark(tok: str) -> bool:
    return bool(QMARK_RE.search(tok.strip()))


def is_cutoff(tok: str) -> bool:
    return bool(CUTOFF_RE.search(tok.strip()))


def is_filler(tok: str) -> bool:
    return norm(tok) in L.FILLERS


def is_backchannel_text(tokens: list[str]) -> bool:
    """True if a short run consists only of acknowledgement tokens/phrases."""
    ns = [norm(t) for t in tokens]
    ns = [t for t in ns if t]
    if not ns:
        return False
    phrase = " ".join(ns)
    if phrase in L.BACKCHANNEL_PHRASES or (phrase in L.BACKCHANNEL_TOKENS and phrase not in L.BACKCHANNEL_WEAK):
        return True
    if not all(t in L.BACKCHANNEL_TOKENS or t in L.BACKCHANNEL_PHRASES for t in ns):
        return False
    return any(t not in L.BACKCHANNEL_WEAK for t in ns)


def _is_te_wh_token(n: str) -> bool:
    if not n:
        return False
    for stem in L.TELUGU_WH_STEMS:
        if stem in L.TELUGU_WH_EXACT_ONLY:
            if n == stem:
                return True
            continue
        if n.startswith(stem):
            rest = n[len(stem):]
            if any(n.endswith(sfx) for sfx in L.QUESTION_STEM_EXCLUDE_SUFFIXES) and rest:
                continue
            # allow case suffixes like ఎందుకు -> ఎందుకని is fine; keep it simple
            return True
    return False


def _is_yesno_token(n: str) -> bool:
    """Telugu yes/no question marker: a verb ending in '-ా' (ఉందా, చేశారా, తెలుసా, వచ్చావా).

    Only syllables that commonly carry the interrogative are accepted; adverbs
    in -గా (నిజంగా), -లా (అలా), colloquial 1st person -తా/-టా (చెప్తా, తింటా)
    and most nouns are excluded.
    """
    if len(n) < 3 or script_of(n) != "te" or n in L.YESNO_FALSE_FRIENDS:
        return False
    return n.endswith(L.YESNO_ENDINGS)


def _english_question_start(ns: list[str]) -> bool:
    """'how was ...', 'what is ...', 'do you ...', 'so what was your ...'."""
    k = 1 if ns and ns[0] in L.ENGLISH_OPENERS and len(ns) > 2 else 0
    if len(ns) < k + 2:
        return False
    a, b = ns[k], ns[k + 1]
    if a in L.ENGLISH_WH:
        return b in L.ENGLISH_AUX_START or b in L.ENGLISH_PRONOUN_AFTER_WH
    if a in L.ENGLISH_WH_CONTRACTED:
        return True
    return a in L.ENGLISH_AUX_START and b in L.ENGLISH_SUBJECTS


def question_score(tokens: list[str], turn_final: bool = False) -> float:
    """Heuristic 0..1 score that a clause (list of raw tokens) is a question.

    Cues, strongest first: a '?' on the last tokens; a Telugu interrogative near
    the end (Telugu is verb-final) or an English question opening; a yes/no
    '-ా' verb or a tag 'కదా' at the end. Being the last clause before another
    speaker takes over adds a small bonus. If the ASR ended the clause with a
    period or '!', the ASR's own punctuation wins and the score is capped below
    the threshold.
    """
    toks = [t for t in tokens if norm(t)]
    if not toks:
        return 0.0
    ns = [norm(t) for t in toks]
    core = list(ns)  # strip honorific tails for "end" checks: "... ఉందా sir"
    while len(core) > 1 and core[-1] in L.TAIL_TOKENS:
        core.pop()
    score = 0.0
    if any(has_qmark(t) for t in toks[-3:]):
        score = 1.0
    elif any(has_qmark(t) for t in toks):
        score = 0.7
    else:
        if any(_is_te_wh_token(n) for n in core[-4:]):
            # "ఎవరు ఉన్నా" (whoever), "ఏం చేసినా" (whatever): concessive, not a question
            score = max(score, 0.2 if core[-1].endswith(L.CONCESSIVE_ENDINGS) else 0.6)
        if _english_question_start(ns):
            score = max(score, 0.6)
        concessive = core[-1].endswith(L.CONCESSIVE_ENDINGS)
        if not concessive and (_is_yesno_token(core[-1]) or (
                len(core) > 1 and _is_yesno_token(core[-2]) and core[-2].endswith(L.YESNO_STRONG))):
            score = max(score, 0.45)  # "ఉందా", or inverted "తెలుసా మీకు"
        if core[-1] == "కదా":
            score = max(score, 0.3)  # tag "isn't it": usually rhetorical confirmation
    if turn_final and score > 0:
        score = min(1.0, score + 0.2)
    last = toks[-1].rstrip("\"'”’)")
    if score < 1.0 and last.endswith((".", "!", "।")):
        score = min(score, 0.4)
    return round(score, 3)


QUESTION_THRESHOLD = 0.55


def latin_fraction(tokens: list[str]) -> float | None:
    """Share of script-bearing tokens that are Latin (code-mixing ratio)."""
    te = lat = 0
    for t in tokens:
        s = script_of(t)
        if s == "te":
            te += 1
        elif s == "latin":
            lat += 1
        elif s == "mixed":
            te += 0.5
            lat += 0.5
    tot = te + lat
    return None if tot == 0 else round(lat / tot, 3)


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)
