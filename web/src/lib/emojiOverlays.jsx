// Feature #30 — turn Gemini's emoji suggestions into TIMED emoji overlay
// elements. Pure + unit-tested (no store/DOM), mirrors the backend contract:
//   - the codepoint filename matches services/emoji.py :: emoji_codepoint
//     (U+FE0F variation selector stripped, scalars joined by '-'),
//   - the PNG is the SAME Twemoji asset the burn composites (public/emoji/* is
//     a byte-identical copy of services/assets/emoji/* — the shared-asset
//     WYSIWYG invariant, exactly like the caption fonts),
//   - each emoji is timed to the caption LINE that holds its anchor word, so
//     the editor's EmojiBody and the burn's enable='between(t,start,end)' show
//     it over the same span.

import { buildCaptionLinesWithRealignments, MAX_WORDS_PER_LINE } from "./captionLines";

export const EMOJI_ASSET_BASE = "/emoji";

// emoji char -> Twemoji filename stem. Must match services/emoji.py exactly:
// every Unicode scalar as lowercase hex, joined by '-', U+FE0F dropped.
export const emojiCodepoint = (ch) =>
  [...String(ch || "")]
    .map((c) => c.codePointAt(0))
    .filter((cp) => cp !== 0xfe0f)
    .map((cp) => cp.toString(16))
    .join("-");

export const emojiAssetUrl = (ch) => `${EMOJI_ASSET_BASE}/${emojiCodepoint(ch)}.png`;

// Default on-canvas placement for an auto-seeded emoji: centered, just above
// the caption so it reads as a reaction over the line without covering the
// words. Draggable afterward like any element.
//
// Held at 0.66 when the caption default moved 0.82 → 0.74. The clearance is now
// tight rather than generous: at the default caption size (0.055 of canvas
// height) the caption's top edge sits at 0.7125 while this emoji's bottom edge
// reaches 0.72 (EMOJI_DEFAULT_HEIGHT/2 below centre), so a seeded emoji can
// graze the top of a caption line by ~0.008 of frame height (~14px at 1920).
// Left as-is on purpose: this is a separate product default with no backend
// mirror (emoji elements carry their own x/y in the rerender payload), and
// retuning it is a visual-taste call, not part of the caption-anchor move.
export const EMOJI_DEFAULT_Y = 0.66;
export const EMOJI_DEFAULT_HEIGHT = 0.12; // fraction of canvas height (matches burn)

/**
 * Build timed emoji overlay elements from resolved suggestions.
 *
 * @param suggestions [{emoji, word_index}] — resolved by the backend
 *   (map_emoji_to_indices), word_index in the clip's filtered word array.
 * @param transcript  the clip's word array (same index space as word_index).
 * @param opts        { lineSplits, lineRealignments, wordsPerLine } — the same
 *   grouping inputs CaptionBody uses, so emoji timing tracks the caption lines.
 * @returns emoji element objects (store element shape). One emoji PER LINE
 *   (first suggestion on a line wins) — the user asked for one emoji per line.
 */
export function buildEmojiOverlayElements(suggestions, transcript, opts = {}) {
  if (!Array.isArray(suggestions) || !suggestions.length) return [];
  if (!Array.isArray(transcript) || !transcript.length) return [];

  const { lineSplits = null, lineRealignments = null, wordsPerLine = MAX_WORDS_PER_LINE } = opts;
  const lines = buildCaptionLinesWithRealignments(
    transcript, wordsPerLine, lineSplits, lineRealignments
  );

  const elements = [];
  const usedLines = new Set();
  suggestions.forEach((sug, i) => {
    const wi = sug?.word_index;
    const word = Number.isInteger(wi) ? transcript[wi] : null;
    if (!word) return;
    // Find the caption line containing this word (by id, falling back to the
    // exact word object) — the emoji rides that line's fixed [start,end].
    const lineIdx = lines.findIndex((L) =>
      L.words.some((lw) => (word.id != null ? lw.id === word.id : lw === word))
    );
    if (lineIdx < 0 || usedLines.has(lineIdx)) return; // one emoji per line
    usedLines.add(lineIdx);
    const line = lines[lineIdx];
    elements.push({
      id: `el_emoji_${i}_${wi}`,
      type: "emoji",
      x: 0.5,
      y: EMOJI_DEFAULT_Y,
      scale: 1,
      rotation: 0,
      visible: true,
      locked: false,
      props: {
        emoji: sug.emoji,
        codepoint: emojiCodepoint(sug.emoji),
        height: EMOJI_DEFAULT_HEIGHT,
        opacity: 1,
        start: line.lineStart,
        end: line.lineEnd,
        wordIndex: wi,
      },
    });
  });
  return elements;
}
