/**
 * Feature #11 — waveform peak math + word-tick placement (pure lib).
 * The WebAudio decode itself (hooks/useWaveform.js) is browser-only and is
 * exercised in the Playwright pass, not here.
 */
import {
  computePeaks,
  normalizePeaks,
  resamplePeaks,
  wordTickFractions,
} from "@/lib/waveform";

describe("computePeaks", () => {
  test("per-window peak is the max |amplitude|", () => {
    // 8 samples, 2 buckets → windows [0..3], [4..7]. Float32 storage rounds,
    // so compare per-element with tolerance.
    const s = new Float32Array([0.1, -0.5, 0.2, 0.0, -0.9, 0.3, 0.1, -0.2]);
    const p = computePeaks(s, 2);
    expect(p[0]).toBeCloseTo(0.5, 5);
    expect(p[1]).toBeCloseTo(0.9, 5);
  });

  test("empty input or non-positive buckets → []", () => {
    expect(computePeaks(new Float32Array([]), 4)).toEqual([]);
    expect(computePeaks(new Float32Array([1, 2]), 0)).toEqual([]);
  });

  test("more buckets than samples still produces one peak per bucket", () => {
    const p = computePeaks(new Float32Array([0.4, 0.8]), 4);
    expect(p).toHaveLength(4);
    expect(Math.max(...p)).toBeCloseTo(0.8, 5);
  });
});

describe("normalizePeaks", () => {
  test("scales the loudest bucket to 1", () => {
    expect(normalizePeaks([0.25, 0.5, 0.1])).toEqual([0.5, 1, 0.2]);
  });
  test("all-silent input stays all zeros (no NaN)", () => {
    expect(normalizePeaks([0, 0, 0])).toEqual([0, 0, 0]);
  });
});

describe("resamplePeaks", () => {
  test("downsamples by taking the window max", () => {
    // 4 → 2: windows [0,1]=max(0.2,0.9)=0.9, [2,3]=max(0.1,0.5)=0.5
    expect(resamplePeaks([0.2, 0.9, 0.1, 0.5], 2)).toEqual([0.9, 0.5]);
  });
  test("identity when counts match; empty guards", () => {
    expect(resamplePeaks([0.3, 0.7], 2)).toEqual([0.3, 0.7]);
    expect(resamplePeaks([], 4)).toEqual([]);
    expect(resamplePeaks([0.1], 0)).toEqual([]);
  });
});

describe("wordTickFractions", () => {
  const WORDS = [
    { start: 0.0, end: 0.4 },
    { start: 1.0, end: 1.5 },
    { start: 3.0, end: 3.5 },
  ];
  test("maps word starts to [0,1] fractions of duration", () => {
    expect(wordTickFractions(WORDS, 4)).toEqual([0, 0.25, 0.75]);
  });
  test("drops out-of-range and non-numeric starts", () => {
    const w = [{ start: -1 }, { start: 5 }, { start: "x" }, { start: 2 }];
    expect(wordTickFractions(w, 4)).toEqual([0.5]);
  });
  test("duration ≤ 0 → []", () => {
    expect(wordTickFractions(WORDS, 0)).toEqual([]);
    expect(wordTickFractions(WORDS, -3)).toEqual([]);
  });
});
