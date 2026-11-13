// Mirrors services/caption_renderer.py's line-grouping + timing exactly
// (READ-ONLY reference: cap_word_durations, group_words_into_lines /
// apply_transcript_edits.group_words_with_splits, generate_ass_karaoke's
// per-word event timing), so the canvas preview's line breaks and pacing
// match what the export burns in.
//
// Pure functions of (words, style, lineSplits) -> lines, and
// (lines, time) -> the active line/word — no React, no store.
//
// lineSplits contract (services/apply_transcript_edits.py ::
// group_words_with_splits): each entry is the RAW INDEX of the word that
// ENDS a line — the forced break lands AFTER words[rawIndex], so
// words[rawIndex + 1] starts the next line. The raw index space is the
// clip's empty-text-filtered word list (the store's transcript array;
// getWordsForRange mirrors get_words_for_clip one-to-one). With no splits
// the walk below flushes exactly every wordsPerLine words — identical to
// the old modulo chunking and to the backend's group_words_into_lines.

export const MAX_WORDS_PER_LINE = 4;
export const MAX_WORD_DURATION = 1.5; // cap single-word duration (seconds)
export const MAX_LINE_DURATION = 4.0; // cap single line display duration (seconds)

// cap_word_durations: silence gaps can make a word's own (start,end) span
// unrealistically long; clip it so it doesn't stretch a line's timing.
export function capWordDurations(words, maxWordDuration = MAX_WORD_DURATION) {
  return words.map((w) =>
    w.end - w.start > maxWordDuration ? { ...w, end: w.start + maxWordDuration } : w
  );
}

// group_words_with_splits / group_words_into_lines: walk the words in
// order, flushing a line when a forced split lands on this word, the line
// is full (wordsPerLine), or the words run out; line span is [first word's
// start, last word's end], capped at maxLineDuration; then trim overlaps
// against the next line by 0.05s, exactly as the backend does.
export function groupWordsIntoLines(
  words,
  wordsPerLine = MAX_WORDS_PER_LINE,
  maxLineDuration = MAX_LINE_DURATION,
  lineSplits = null
) {
  const splits = lineSplits instanceof Set ? lineSplits : new Set(lineSplits || []);
  const lines = [];
  let current = [];
  for (let rawIdx = 0; rawIdx < words.length; rawIdx++) {
    current.push(words[rawIdx]);
    const isLast = rawIdx === words.length - 1;
    const forcedBreak = splits.has(rawIdx);
    const fullLine = current.length >= wordsPerLine;
    if (forcedBreak || fullLine || isLast) {
      const lineStart = current[0].start;
      let lineEnd = current[current.length - 1].end;
      if (lineEnd - lineStart > maxLineDuration) lineEnd = lineStart + maxLineDuration;
      lines.push({ words: current, lineStart, lineEnd });
      current = [];
    }
  }
  for (let i = 0; i < lines.length - 1; i++) {
    if (lines[i].lineEnd > lines[i + 1].lineStart) {
      lines[i] = { ...lines[i], lineEnd: lines[i + 1].lineStart - 0.05 };
    }
  }
  return lines;
}

// Convenience: the full pure pipeline the backend runs before ASS generation.
export function buildCaptionLines(words, wordsPerLine = MAX_WORDS_PER_LINE, lineSplits = null) {
  return groupWordsIntoLines(capWordDurations(words), wordsPerLine, MAX_LINE_DURATION, lineSplits);
}

// Line-level re-alignment overlay — frontend mirror of services/
// apply_transcript_edits.py :: apply_line_realignments; the two must stay in
// lockstep so preview karaoke == burned karaoke.
//
// Walks the grouped lines with a cumulative raw-word cursor and, when a line
// spans exactly a realignment's [startIdx, endIdx] (the ORIGINAL words it
// covered — same raw index space as lineSplits), replaces that line's words
// with the realigned set: {text, text_tanglish, start, end, realigned:true},
// clamped into the line's FIXED [lineStart, lineEnd]. Every line (matched or
// not) gets startIdx/endIdx annotated — the editor's line addressing. An
// entry whose range no longer matches any grouped line (style wordsPerLine
// changed, split added inside it) is inert: the line renders its original
// words. Line boundaries never move.
export function applyLineRealignments(lines, lineRealignments) {
  const recs = lineRealignments || {};
  let cursor = 0;
  return lines.map((line) => {
    const startIdx = cursor;
    const endIdx = cursor + line.words.length - 1;
    cursor += line.words.length;
    const rec = recs[`${startIdx}:${endIdx}`];
    if (!rec || !Array.isArray(rec.words) || !rec.words.length) {
      return { ...line, startIdx, endIdx };
    }
    const words = rec.words.map((w) => {
      const start = Math.min(Math.max(w.start, line.lineStart), line.lineEnd);
      return {
        text: w.word,
        text_tanglish: w.word_tanglish || null,
        start,
        end: Math.min(Math.max(w.end, start), line.lineEnd),
        realigned: true,
      };
    });
    return {
      ...line,
      words,
      startIdx,
      endIdx,
      realigned: true,
      approximate: !!rec.approximate,
    };
  });
}

// The full derived-lines pipeline every caption surface renders: grouping on
// the ORIGINAL word list (raw index space intact), then realigned lines
// overlaid. One call so the transcript panel, the canvas preview, and the
// export can never disagree about a realigned line.
export function buildCaptionLinesWithRealignments(
  words,
  wordsPerLine = MAX_WORDS_PER_LINE,
  lineSplits = null,
  lineRealignments = null
) {
  return applyLineRealignments(
    buildCaptionLines(words, wordsPerLine, lineSplits),
    lineRealignments
  );
}

// Which line (if any) is on screen at `time`. Faithful to the backend: a
// gap between one line's (overlap-trimmed) lineEnd and the next line's
// lineStart shows nothing, exactly like a real ASS timeline gap.
export function findActiveLine(lines, time) {
  return lines.find((l) => time >= l.lineStart && time < l.lineEnd) || null;
}

// Per-word event timing within a line (generate_ass_karaoke): word i is
// "active" from its own start until the NEXT word's start (not its own
// end) — so highlight persists through any natural pause between words,
// clamped to the line's end for the last word. Equivalent to: the active
// word is the last one whose start <= time.
export function findActiveWordIndex(line, time) {
  if (!line || !line.words.length) return -1;
  let idx = 0;
  for (let i = 0; i < line.words.length; i++) {
    if (line.words[i].start <= time) idx = i;
    else break;
  }
  return idx;
}
