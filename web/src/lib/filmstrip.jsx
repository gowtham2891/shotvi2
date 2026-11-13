/**
 * Feature #12 — filmstrip timeline helpers (pure, DOM-free, testable).
 *
 * - thumbnailTimestamps: evenly-spaced sample times across the clip, each at
 *   the MIDDLE of its cell (so a cell's image represents its span, not its
 *   left edge — reads better under the playhead).
 * - subtitleBlocks: caption lines → [0,1] fraction spans for the block strip,
 *   reusing the SAME lineStart/lineEnd the preview + burn agree on.
 */

// count sample points across [0, duration], each centered in its cell.
export function thumbnailTimestamps(duration, count) {
  if (!duration || duration <= 0 || count <= 0) return [];
  const cell = duration / count;
  const out = [];
  for (let i = 0; i < count; i++) out.push((i + 0.5) * cell);
  return out;
}

// lines (buildCaptionLines output: {lineStart, lineEnd, words}) → block specs
// as [0,1] fractions of the timeline. Zero/negative-width spans are dropped;
// widths clamp so a block never spills past the track end.
export function subtitleBlocks(lines, duration) {
  if (!duration || duration <= 0) return [];
  const out = [];
  for (const l of lines || []) {
    const start = Math.max(0, l.lineStart ?? 0);
    const end = Math.min(duration, l.lineEnd ?? 0);
    if (!(end > start)) continue;
    out.push({
      left: start / duration,
      width: (end - start) / duration,
      lineStart: l.lineStart,
      lineEnd: l.lineEnd,
      text: (l.words || []).map((w) => w.text ?? "").join(" "),
    });
  }
  return out;
}
