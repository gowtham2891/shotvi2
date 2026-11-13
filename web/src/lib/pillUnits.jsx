/**
 * Caption pill units (feature #4).
 *
 * Canonical unit for pill.padding / pill.radius is a FRACTION of canvas
 * height — the same unit caption fontSize already uses — so the pill scales
 * with the text on every stage size (9:16 640px / 1:1 560px / 16:9 540px)
 * and on the burn (1920/1080/1080), instead of being absolute CSS px that
 * only looked right on the original 640px-tall 9:16 stage.
 *
 * Legacy values (old drafts, saved "My Style" templates, in-flight export
 * payloads) were absolute px designed against that 640px stage. Anything > 1
 * is unambiguously legacy px (no sane pill pads 100%+ of the canvas height),
 * so normalization divides by 640 exactly once, idempotently.
 *
 * Mirrored by services/caption_renderer.py's _pill_padding_frac — keep the
 * two in lockstep.
 */

export const LEGACY_PILL_STAGE_H = 640;

// A single scalar: legacy px (>1) → fraction; fractions (≤1) pass through.
export const normalizePillScalar = (v) => {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return n > 1 ? n / LEGACY_PILL_STAGE_H : n;
};

// Whole-pill normalization; returns a NEW object, never mutates.
export const normalizePillUnits = (pill) => {
  if (!pill || typeof pill !== "object") return pill;
  return {
    ...pill,
    padding: normalizePillScalar(pill.padding),
    radius: normalizePillScalar(pill.radius),
  };
};

// Inspector slider display: fractions are edited as "px at the 9:16 stage"
// (0–24), the numbers users have always seen.
export const pillFracToSliderPx = (v) =>
  Math.round(normalizePillScalar(v) * LEGACY_PILL_STAGE_H);
export const pillSliderPxToFrac = (px) => (Number(px) || 0) / LEGACY_PILL_STAGE_H;
