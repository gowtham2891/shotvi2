/**
 * Feature #13 — auto-zoom (frontend mirror of services/auto_zoom.py).
 * Fixtures match tests/test_auto_zoom.py so the two stay in lockstep.
 */
import {
  generatePunchPoints,
  punchesToKeyframes,
  togglePunch,
  PUNCH_ZOOM,
} from "@/lib/autoZoom";

describe("generatePunchPoints (mirror of generate_punch_points)", () => {
  test("first word never punches; beats follow a real pause", () => {
    const words = [
      { start: 0.0, end: 0.4 },
      { start: 0.9, end: 1.3 },
      { start: 1.3, end: 1.7 },
      { start: 5.0, end: 5.4 },
    ];
    expect(generatePunchPoints(words)).toEqual([0.9, 5.0]);
  });

  test("minSpacing throttles close beats", () => {
    const words = [
      { start: 1.0, end: 1.2 },
      { start: 2.0, end: 2.2 },
      { start: 2.8, end: 3.0 },
      { start: 5.5, end: 5.7 },
    ];
    expect(generatePunchPoints(words, { minSpacing: 2.5 })).toEqual([2.0, 5.5]);
  });

  test("maxPunches cap", () => {
    const words = Array.from({ length: 20 }, (_, i) => ({ start: i * 3, end: i * 3 + 0.2 }));
    expect(generatePunchPoints(words, { maxPunches: 5 })).toHaveLength(5);
  });

  test("no words / no starts → []", () => {
    expect(generatePunchPoints([])).toEqual([]);
    expect(generatePunchPoints([{ end: 1 }])).toEqual([]);
  });
});

describe("punchesToKeyframes (mirror of punches_to_keyframes)", () => {
  test("single punch = centered zoom bump", () => {
    const kfs = punchesToKeyframes([2.0], 5.0);
    expect(kfs.map((k) => k.time)).toEqual([1.8, 2.0, 2.2]);
    const peak = kfs[1];
    expect(peak.w).toBeCloseTo(1 / PUNCH_ZOOM, 4);
    expect(peak.h).toBeCloseTo(peak.w, 6);
    expect(peak.x).toBeCloseTo((1 - peak.w) / 2, 4);
    expect(peak.x).toBeCloseTo(peak.y, 6);
    expect(kfs[0].w).toBeCloseTo(1.0, 4);
    expect(kfs[2].w).toBeCloseTo(1.0, 4);
  });

  test("times clamp into the clip", () => {
    const kfs = punchesToKeyframes([0.1], 5.0);
    expect(kfs[0].time).toBe(0);
    expect(kfs.every((k) => k.time >= 0 && k.time <= 5)).toBe(true);
  });

  test("overlapping punches combine via max (trough stays zoomed)", () => {
    const kfs = punchesToKeyframes([2.0, 2.2], 5.0);
    const mid = kfs.reduce((a, b) => (Math.abs(b.time - 2.1) < Math.abs(a.time - 2.1) ? b : a));
    expect(1 / mid.w).toBeGreaterThan(1.0);
  });

  test("empty punches / bad duration → []", () => {
    expect(punchesToKeyframes([], 5)).toEqual([]);
    expect(punchesToKeyframes([2], 0)).toEqual([]);
  });
});

describe("togglePunch", () => {
  test("adds when none near, sorted", () => {
    expect(togglePunch([1.0, 3.0], 2.0)).toEqual([1.0, 2.0, 3.0]);
  });
  test("removes the nearest within tolerance", () => {
    expect(togglePunch([1.0, 3.0], 1.05, 0.15)).toEqual([3.0]);
  });
  test("outside tolerance adds instead of removing", () => {
    expect(togglePunch([1.0], 1.3, 0.15)).toEqual([1.0, 1.3]);
  });
  test("handles null/empty input", () => {
    expect(togglePunch(null, 2.0)).toEqual([2.0]);
  });
});
