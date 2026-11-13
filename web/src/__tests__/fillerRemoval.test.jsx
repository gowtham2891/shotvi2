/**
 * Feature #14 — filler/silence detection + caption-remap mirror (frontend).
 * Remap fixtures match tests/test_filler_removal.py so the two stay locked.
 */
import {
  detectRemovableSpans,
  mergeSpans,
  removedBefore,
  wordInSpans,
  remapTimeAfterCuts,
  applyCutsToWords,
  spansToPairs,
} from "@/lib/fillerRemoval";

describe("detectRemovableSpans", () => {
  test("flags filler words by their own span", () => {
    const words = [
      { text: "hello", start: 0, end: 0.5 },
      { text: "um", start: 0.6, end: 0.9 },
      { text: "world", start: 1.0, end: 1.5 },
    ];
    const spans = detectRemovableSpans(words, 2, { minGap: 5 }); // suppress silence
    expect(spans).toHaveLength(1);
    expect(spans[0]).toMatchObject({ start: 0.6, end: 0.9, kind: "filler", wordIndex: 1 });
  });

  test("flags a silence gap between words (padded interior)", () => {
    const words = [
      { text: "a", start: 0, end: 0.5 },
      { text: "b", start: 2.0, end: 2.5 }, // 1.5s gap
    ];
    const spans = detectRemovableSpans(words, 3, { minGap: 0.6, pad: 0.1 });
    expect(spans).toHaveLength(1);
    expect(spans[0].kind).toBe("silence");
    expect(spans[0].start).toBeCloseTo(0.6, 5); // 0.5 + pad
    expect(spans[0].end).toBeCloseTo(1.9, 5); // 2.0 - pad
  });

  test("no fillers, tight timing → nothing removed", () => {
    const words = [
      { text: "a", start: 0, end: 0.5 },
      { text: "b", start: 0.6, end: 1.0 },
    ];
    expect(detectRemovableSpans(words, 2)).toEqual([]);
  });
});

describe("mergeSpans", () => {
  test("merges overlapping/adjacent spans", () => {
    expect(mergeSpans([{ start: 1, end: 3 }, { start: 2, end: 4 }])).toEqual([
      { start: 1, end: 4 },
    ]);
  });
  test("drops zero/negative width", () => {
    expect(mergeSpans([{ start: 2, end: 2 }, { start: 3, end: 1 }])).toEqual([]);
  });
});

describe("remap (mirror of services/filler_removal.py)", () => {
  const cuts = [{ start: 1, end: 2 }, { start: 4, end: 5 }];

  test("removedBefore accounts for full + partial spans", () => {
    expect(removedBefore(0.5, cuts)).toBe(0);
    expect(removedBefore(3.0, cuts)).toBe(1);
    expect(removedBefore(1.5, cuts)).toBe(0.5); // inside first cut
    expect(removedBefore(5.5, cuts)).toBe(2);
  });

  test("remapTimeAfterCuts shifts survivors earlier", () => {
    expect(remapTimeAfterCuts(0.5, cuts)).toBe(0.5);
    expect(remapTimeAfterCuts(3.0, cuts)).toBe(2.0);
    expect(remapTimeAfterCuts(5.5, cuts)).toBe(3.5);
  });

  test("wordInSpans uses the word midpoint", () => {
    expect(wordInSpans({ start: 1.2, end: 1.6 }, cuts)).toBe(true);
    expect(wordInSpans({ start: 0.0, end: 0.5 }, cuts)).toBe(false);
  });

  test("applyCutsToWords drops cut words + remaps + keeps fields", () => {
    const words = [
      { text: "a", start: 0.0, end: 0.5 },
      { text: "um", start: 1.2, end: 1.6 },
      { text: "b", start: 3.0, end: 3.5, emphasis: true },
      { text: "c", start: 5.5, end: 6.0 },
    ];
    const out = applyCutsToWords(words, cuts);
    expect(out.map((w) => w.text)).toEqual(["a", "b", "c"]);
    expect(out[1]).toMatchObject({ start: 2.0, end: 2.5, emphasis: true });
    expect(out[2]).toMatchObject({ start: 3.5, end: 4.0 });
  });

  test("no spans → identity", () => {
    const words = [{ text: "a", start: 0, end: 0.5 }];
    expect(applyCutsToWords(words, [])).toBe(words);
  });
});

describe("spansToPairs", () => {
  test("strips meta to [start,end] pairs", () => {
    expect(spansToPairs([{ start: 1, end: 2, kind: "filler" }])).toEqual([[1, 2]]);
  });
});
