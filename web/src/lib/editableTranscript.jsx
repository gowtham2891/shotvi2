// Caret-position → lineSplits-index math for the editable transcript
// (EditableTranscript.jsx). Pure functions so the CapCut-style keyboard
// semantics are testable without mounting a component.
//
// lineSplits contract (services/apply_transcript_edits.py ::
// group_words_with_splits, mirrored by lib/captionLines.js): an entry is
// the raw index of the word that ENDS a line — the break lands AFTER
// words[rawIndex], so words[rawIndex + 1] starts the next line.

// Enter pressed inside the word at rawIdx. The caret decides which side of
// the word the cut falls on: caret at the word's START means "break before
// me" (this word starts the new line); caret anywhere else means "break
// after me" (the NEXT word starts the new line) — together they cover
// "cursor between two words" from either side of the gap. Returns the
// lineSplits index to ADD, or null when the cut has no room (before word 0
// / after the last word).
export function enterSplitIndex(rawIdx, caretAtStart, wordCount) {
  const newLineStart = caretAtStart ? rawIdx : rawIdx + 1;
  if (newLineStart <= 0 || newLineStart >= wordCount) return null;
  return newLineStart - 1;
}

// Backspace with the caret at the very start of the word at rawIdx: merge
// this line back into the previous one by removing the forced split ending
// there. Returns the lineSplits index to REMOVE, or null when there is
// nothing removable (word 0, or the boundary above is natural — a plain
// wordsPerLine break, not a user split; natural boundaries are not edits
// and cannot be "removed").
export function backspaceMergeIndex(rawIdx, lineSplits) {
  if (!Number.isInteger(rawIdx) || rawIdx <= 0) return null;
  return (lineSplits || []).includes(rawIdx - 1) ? rawIdx - 1 : null;
}

// True when keyboard focus sits in a text-editing context (a word input,
// any form field, or contentEditable). Global shortcuts that collide with
// native text editing — document-level Ctrl+Z/Ctrl+Y above all — must
// yield there: inside an input, Ctrl+Z is the browser undoing the user's
// TYPING, not the app undoing the document. Hijacking it would feel broken
// instantly.
export function isTextEditingTarget(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || !!el.isContentEditable;
}
