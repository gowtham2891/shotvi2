// Telugu words in English letters, the way people type Telugu on WhatsApp ("ఏంటి" -> "enti"). The SAME rules as
// selection_v2/romanize.py (the renderer): change both together; tests/v2/romanize_cases.json checks both.
const VOWELS = { "అ": "a", "ఆ": "aa", "ఇ": "i", "ఈ": "ee", "ఉ": "u", "ఊ": "oo", "ఋ": "ru", "ౠ": "roo", "ఎ": "e", "ఏ": "e", "ఐ": "ai", "ఒ": "o", "ఓ": "o", "ఔ": "au" };
const SIGNS = { "ా": "aa", "ి": "i", "ీ": "ee", "ు": "u", "ూ": "oo", "ృ": "ru", "ౄ": "roo", "ె": "e", "ే": "e", "ై": "ai", "ొ": "o", "ో": "o", "ౌ": "au" };
const CONSONANTS = {
  "క": "k", "ఖ": "kh", "గ": "g", "ఘ": "gh", "ఙ": "n", "చ": "ch", "ఛ": "chh", "జ": "j", "ఝ": "jh", "ఞ": "n",
  "ట": "t", "ఠ": "th", "డ": "d", "ఢ": "dh", "ణ": "n", "త": "t", "థ": "th", "ద": "d", "ధ": "dh", "న": "n",
  "ప": "p", "ఫ": "ph", "బ": "b", "భ": "bh", "మ": "m", "య": "y", "ర": "r", "ఱ": "r", "ల": "l", "ళ": "l",
  "వ": "v", "శ": "sh", "ష": "sh", "స": "s", "హ": "h", "ౘ": "ts", "ౙ": "dz",
};
const VIRAMA = "్";
const ANUSVARA = "ం";
const VISARGA = "ః";
const CANDRABINDU = "ఁ";
const LABIALS = new Set(["ప", "ఫ", "బ", "భ", "మ"]);
const SKIP = new Set(["‌", "‍", "ౕ", "ౖ"]);
const digit = (c) => {
  const d = c.codePointAt(0) - 0x0c66;
  return d >= 0 && d <= 9 ? String(d) : null;
};

/** One word (or any text) in casual English letters; non-Telugu characters are kept as they are. */
export function romanize(word) {
  const chars = [...word].filter((c) => !SKIP.has(c));
  const out = [];
  for (let i = 0; i < chars.length; i++) {
    const c = chars[i];
    const nxt = chars[i + 1] || "";
    if (c in CONSONANTS) {
      out.push(CONSONANTS[c]);
      if (nxt === VIRAMA) {
        i += 1;
        continue;
      }
      if (nxt in SIGNS) {
        out.push(SIGNS[nxt]);
        i += 1;
        continue;
      }
      out.push("a"); // the inherent vowel
    } else if (c in VOWELS) {
      out.push(VOWELS[c]);
    } else if (c === ANUSVARA || c === CANDRABINDU) {
      // m before p/b/m and at the end of a word (సంబంధం -> sambandham)
      out.push(LABIALS.has(nxt) || !/[\u0c00-\u0c7f]/.test(nxt) ? "m" : "n");
    } else if (c === VISARGA) {
      out.push("h");
    } else if (digit(c) !== null) {
      out.push(digit(c));
    } else if (c in SIGNS || c === VIRAMA) {
      // a stray sign (bad input): drop it
    } else {
      out.push(c);
    }
  }
  // a long "aa" at the end of a word is written "a" (raa -> ra), keeping punctuation after it
  return out.join("").replace(/(?<=[^\Wa])aa(?=[^\w]*$)/, "a");
}

export const hasTelugu = (text) => /[ఀ-౿]/.test(text);
