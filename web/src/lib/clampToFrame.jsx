/**
 * Frame-safe center clamp for draggable caption elements (Commit 5 — Known
 * Issue 3 fix).
 *
 * The editor stores each element's position as a 0–1 CENTER fraction of the
 * canvas. A blanket clamp of `(0.02, 0.98)` (see ElementRenderer.jsx) only
 * keeps the CENTER point inside the frame — the *rendered text bounding box*
 * can (and did, on wide Telugu multi-word captions) still spill past the
 * frame edge, because the center is the anchor, not the bounds.
 *
 * clampToFrame computes the correct min/max center for each axis from the
 * measured bounding box + current video dims, so no caption edge is ever
 * dragged outside the frame in the editor — and because the export burn uses
 * the exact same (caption_x, caption_y) that the drag settled on, the export
 * frame also never clips (WYSIWYG holds; see BURNIN_NOTES §4).
 *
 * Oversized text (elWidth > canvasWidth, or elHeight > canvasHeight) has no
 * "safe" center — the box is bigger than the frame. In that degenerate case
 * we pin the center to 0.5 on the oversized axis so the overflow is
 * symmetric (matches the preview's center-anchored render); the caption is
 * as centered as it can be, and both left/right or top/bottom edges spill
 * equally. Alternative "clamp to top-left corner in-frame" behaviour would
 * hide half the text off the right / bottom edge — worse UX than a
 * symmetric overflow.
 */
export function clampToFrame(
  x,
  y,
  elWidth,
  elHeight,
  canvasWidth,
  canvasHeight,
) {
  // Guard against zero/negative dims (unmeasured element, unmounted canvas —
  // fall back to the plain 2%–98% clamp so the drag still moves the element).
  if (!(canvasWidth > 0) || !(canvasHeight > 0)) {
    return { x: clamp01(x, 0.02, 0.98), y: clamp01(y, 0.02, 0.98) };
  }

  const halfW = Math.max(elWidth, 0) / 2 / canvasWidth;   // as 0–1 fraction
  const halfH = Math.max(elHeight, 0) / 2 / canvasHeight;

  // Normal case: half-width fits — the center may live anywhere in [half, 1-half].
  // Oversized case: 2*halfW > 1 → range collapses; pin to 0.5 for symmetric overflow.
  const [minX, maxX] = halfW * 2 <= 1 ? [halfW, 1 - halfW] : [0.5, 0.5];
  const [minY, maxY] = halfH * 2 <= 1 ? [halfH, 1 - halfH] : [0.5, 0.5];

  return { x: clamp01(x, minX, maxX), y: clamp01(y, minY, maxY) };
}

function clamp01(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}
