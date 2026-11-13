// The caption layout rules of the renderer (selection_v2/captions.py: cues, wrap, chars_per_line, positions),
// ported line for line so the live preview shows exactly what the burned-in video will show. Change both together.
import captionFonts from "@/data/captionFonts.json";
import { romanize } from "@/lib/romanize";

export const OUT_W = 1080;
export const OUT_H = 1920;
export const PAUSE_S = 0.45;
export const TAIL_S = 0.2;
export const MIN_CUE_S = 0.9;
export const SENT_END = ".?!।";
export const CHARS_PER_LINE_AT_72 = 20;
export const DEFAULT_POS_Y = { fit: 0.74, crop: 0.74 }; // just clear of the Instagram / YouTube buttons (as captions.py)

export const DEFAULTS = {
  font: "NotoSansTelugu-Bold.ttf",
  size: 78,
  colour: "#FFFFFF",
  outline_colour: "#000000",
  outline: 3,
  shadow: 1,
  box: null,
  highlight: null,
  highlight_box: null, // a box behind the spoken word
  emphasis: null, // colour of words marked as keywords
  glow: 0, // the outline softened into a glow
  animation: "none", // none | fade | pop | reveal
  uppercase_latin: false,
  script: "telugu", // telugu | latin (Telugu words in English letters: ఏంటి -> enti)
  max_lines: 2,
  words_per_line: 0,
  pos_x: 0.5,
  pos_y: null,
};

export const FONTS = captionFonts; // [{ file, family, weight, telugu }]
const FAMILY = Object.fromEntries(captionFonts.map((f) => [f.file, f]));
export const fontFor = (file) => FAMILY[file] || FAMILY[DEFAULTS.font];

/** Python len(): code points, so an emoji counts once (JS .length counts it twice). */
const clen = (s) => [...s].length;
const lastChar = (s) => {
  const cs = [...s];
  return cs[cs.length - 1] || "";
};

export const charsPerLine = (s) => Math.max(8, Math.round((CHARS_PER_LINE_AT_72 * 72) / Math.max(40, s.size)));

/** Words -> cues [{start, end, words}] (clip clock). Same rules as captions.cues(); `breaks` = word indices that
 *  start a new cue (line splits from the editor). */
export function cues(ws, s = DEFAULTS, breaks = []) {
  const brk = new Set(breaks || []);
  // lines are measured by the text shown (English letters can be longer than Telugu), as in captions.cues()
  const shown = (t) => clen(s.script === "latin" ? romanize(t) : t);
  const out = [];
  let cur = [];
  const maxWords = s.words_per_line ? s.words_per_line * s.max_lines : Infinity;
  const maxChars = charsPerLine(s) * s.max_lines;
  const close = () => {
    if (cur.length) {
      out.push({ words: cur });
      cur = [];
    }
  };
  ws.forEach((w, i) => {
    if (cur.length && brk.has(i)) close();
    if (cur.length) {
      const prev = cur[cur.length - 1];
      const gap = w.start - prev.end;
      const textLen = cur.reduce((n, x) => n + shown(x.tok) + 1, 0) + shown(w.tok);
      if (
        gap > PAUSE_S ||
        (SENT_END.includes(lastChar(prev.tok)) && cur.length >= 2) ||
        cur.length >= maxWords ||
        (!s.words_per_line && textLen > maxChars)
      )
        close();
    }
    cur.push(w);
  });
  close();
  out.forEach((c, i) => {
    c.start = Math.max(0, c.words[0].start - 0.05);
    let end = Math.max(c.words[c.words.length - 1].end + TAIL_S, c.start + MIN_CUE_S);
    if (i + 1 < out.length) end = Math.min(end, Math.max(c.words[c.words.length - 1].end, out[i + 1].words[0].start - 0.05));
    c.end = round3(end);
    c.start = round3(c.start);
  });
  return out;
}

const round3 = (x) => Math.round(x * 1000) / 1000;

/** Tokens -> up to max_lines balanced lines (same as captions.wrap()). */
export function wrap(toks, s = DEFAULTS) {
  if (s.words_per_line) {
    const n = s.words_per_line;
    const rows = [];
    for (let i = 0; i < toks.length; i += n) rows.push(toks.slice(i, i + n));
    const kept = rows.slice(0, s.max_lines);
    return kept.length ? kept : [toks];
  }
  const total = toks.reduce((n, t) => n + clen(t) + 1, 0);
  if (s.max_lines === 1 || toks.length === 1 || total <= charsPerLine(s)) return [toks];
  let best = 1;
  let bestDiff = Infinity;
  for (let k = 1; k < toks.length; k++) {
    const a = toks.slice(0, k).reduce((n, t) => n + clen(t) + 1, 0);
    const diff = Math.abs(a - (total - a));
    if (diff < bestDiff) {
      best = k;
      bestDiff = diff;
    }
  }
  return [toks.slice(0, best), toks.slice(best)];
}

/** The word shown as "being spoken" at time t inside a cue (the renderer gives each word the span from its start to
 *  the next word's start; the first from the cue start, the last until the cue end). */
export function activeWordIndex(cue, t) {
  let idx = 0;
  cue.words.forEach((w, i) => {
    if (w.start <= t) idx = i;
  });
  return idx;
}

export const cueAt = (cs, t) => cs.find((c) => t >= c.start && t < c.end) || null;

export const displayTok = (tok, s) => {
  const t = s.script === "latin" ? romanize(tok) : tok;
  return s.uppercase_latin && /^[\x00-\x7F]*$/.test(t) ? t.toUpperCase() : t;
};

/** '#RRGGBB' / '#RRGGBBAA' (AA = opacity) -> CSS rgba(). */
export function cssColour(hex, fallback = "#000000") {
  const h = (hex || fallback).replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  const a = h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1;
  return `rgba(${r},${g},${b},${a.toFixed(3)})`;
}

export const resolvedPosY = (s, style) => (s.pos_y == null ? DEFAULT_POS_Y[style] : s.pos_y);

/** Full settings: defaults + a bundle (template settings or a draft's), dropping keys the renderer does not know. */
export function withDefaults(settings) {
  const out = { ...DEFAULTS };
  Object.keys(DEFAULTS).forEach((k) => {
    if (settings && k in settings) out[k] = settings[k];
  });
  return out;
}
