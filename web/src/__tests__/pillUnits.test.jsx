/**
 * Feature #4 — pill padding/radius unit contract (frontend side).
 *
 * Canonical unit is a fraction of canvas height (same as caption fontSize);
 * legacy absolute px (>1, designed against the 640px-tall 9:16 stage)
 * convert once, idempotently. Mirrors services/caption_renderer.py::
 * _pill_padding_px — keep the two in lockstep.
 */
import {
  LEGACY_PILL_STAGE_H,
  normalizePillScalar,
  normalizePillUnits,
  pillFracToSliderPx,
  pillSliderPxToFrac,
} from "@/lib/pillUnits";

test("fractions pass through untouched", () => {
  expect(normalizePillScalar(8 / 640)).toBeCloseTo(8 / 640, 10);
  expect(normalizePillScalar(0.05)).toBe(0.05);
  expect(normalizePillScalar(1)).toBe(1 / LEGACY_PILL_STAGE_H === 1 ? 1 : 1); // 1 is a fraction (100%), not px
});

test("legacy px convert against the 640px stage exactly once (idempotent)", () => {
  const once = normalizePillScalar(10);
  expect(once).toBeCloseTo(10 / 640, 10);
  expect(normalizePillScalar(once)).toBeCloseTo(once, 10); // stable under re-application
});

test("junk and negatives are safe", () => {
  expect(normalizePillScalar(undefined)).toBe(0);
  expect(normalizePillScalar(null)).toBe(0);
  expect(normalizePillScalar("junk")).toBe(0);
  expect(normalizePillScalar(-4)).toBe(0);
});

test("normalizePillUnits converts both fields and never mutates", () => {
  const legacy = { enabled: true, color: "#000", opacity: 0.5, padding: 12, radius: 8 };
  const n = normalizePillUnits(legacy);
  expect(n.padding).toBeCloseTo(12 / 640, 10);
  expect(n.radius).toBeCloseTo(8 / 640, 10);
  expect(n.color).toBe("#000");
  expect(legacy.padding).toBe(12); // untouched input
});

test("slider round-trip: px ↔ fraction is lossless at slider granularity", () => {
  for (let px = 0; px <= 24; px++) {
    expect(pillFracToSliderPx(pillSliderPxToFrac(px))).toBe(px);
  }
  // Legacy px values display as themselves too
  expect(pillFracToSliderPx(10)).toBe(10);
});
