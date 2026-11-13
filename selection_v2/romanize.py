"""Telugu words in English letters, the way people type Telugu on WhatsApp (founder, 2026-11-12: "ఏంటి" -> "enti").

Rule-based and free (no AI call), letter by letter, so the live preview (web/src/lib/romanize.jsx, the same rules) and the
burned-in captions always agree; tests/v2/test_romanize.py checks both against the same word list
(tests/v2/romanize_cases.json). Casual spelling, not a scholarly scheme:
  * long vowels doubled (naaku, meeru, ooru), but a final long "aa" is written "a" (raa -> ra, ammaa -> amma)
  * ఏ/ఎ -> e, ఓ/ఒ -> o, ఐ -> ai, ఔ -> au
  * aspirates and retroflexes in the simple form people use (ట/త -> t, ఠ/థ -> th, డ/ద -> d, ఢ/ధ -> dh, శ/ష -> sh)
  * the nasal ం -> m before p/b/m sounds and at the end of a word, else n (అంబ -> amba, సంబంధం -> sambandham,
    ఏంటి -> enti)
English words, digits and punctuation pass through unchanged; Telugu digits become 0-9.
"""
from __future__ import annotations

VOWELS = {"అ": "a", "ఆ": "aa", "ఇ": "i", "ఈ": "ee", "ఉ": "u", "ఊ": "oo", "ఋ": "ru", "ౠ": "roo", "ఎ": "e", "ఏ": "e",
          "ఐ": "ai", "ఒ": "o", "ఓ": "o", "ఔ": "au"}
SIGNS = {"ా": "aa", "ి": "i", "ీ": "ee", "ు": "u", "ూ": "oo", "ృ": "ru", "ౄ": "roo", "ె": "e", "ే": "e", "ై": "ai",
         "ొ": "o", "ో": "o", "ౌ": "au"}
CONSONANTS = {"క": "k", "ఖ": "kh", "గ": "g", "ఘ": "gh", "ఙ": "n", "చ": "ch", "ఛ": "chh", "జ": "j", "ఝ": "jh",
              "ఞ": "n", "ట": "t", "ఠ": "th", "డ": "d", "ఢ": "dh", "ణ": "n", "త": "t", "థ": "th", "ద": "d", "ధ": "dh",
              "న": "n", "ప": "p", "ఫ": "ph", "బ": "b", "భ": "bh", "మ": "m", "య": "y", "ర": "r", "ఱ": "r", "ల": "l",
              "ళ": "l", "వ": "v", "శ": "sh", "ష": "sh", "స": "s", "హ": "h", "ౘ": "ts", "ౙ": "dz"}
VIRAMA, ANUSVARA, VISARGA, CANDRABINDU = "్", "ం", "ః", "ఁ"
LABIALS = {"ప", "ఫ", "బ", "భ", "మ"}
DIGITS = {chr(0x0C66 + i): str(i) for i in range(10)}
SKIP = {"‌", "‍", "ౕ", "ౖ"}   # joiners, length marks


def romanize(word: str) -> str:
    """One word (or any text) in casual English letters; non-Telugu characters are kept as they are."""
    out: list[str] = []
    chars = [c for c in word if c not in SKIP]
    i, n = 0, len(chars)
    while i < n:
        c = chars[i]
        nxt = chars[i + 1] if i + 1 < n else ""
        if c in CONSONANTS:
            out.append(CONSONANTS[c])
            if nxt == VIRAMA:
                i += 2
                continue
            if nxt in SIGNS:
                out.append(SIGNS[nxt])
                i += 2
                continue
            out.append("a")                      # the inherent vowel
        elif c in VOWELS:
            out.append(VOWELS[c])
        elif c == ANUSVARA or c == CANDRABINDU:     # m before p/b/m and at the end of a word (సంబంధం -> sambandham)
            out.append("m" if (nxt in LABIALS or not ("\u0c00" <= nxt <= "\u0c7f")) else "n")
        elif c == VISARGA:
            out.append("h")
        elif c in DIGITS:
            out.append(DIGITS[c])
        elif c in SIGNS or c == VIRAMA:
            pass                                  # a stray sign (bad input): drop it
        else:
            out.append(c)
        i += 1
    text = "".join(out)
    return _final_aa(text)


def _final_aa(text: str) -> str:
    """A long "aa" at the end of a word is written "a" (raa -> ra), keeping punctuation after it."""
    import re   # ASCII word characters, as in the browser copy (JavaScript's \w is ASCII-only)
    return re.sub(r"(?<=[^\Wa])aa(?=[^\w]*$)", "a", text, flags=re.ASCII)


def has_telugu(text: str) -> bool:
    return any("ఀ" <= ch <= "౿" for ch in text)
