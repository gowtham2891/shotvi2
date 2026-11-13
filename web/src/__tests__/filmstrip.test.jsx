/**
 * Feature #12 — filmstrip helpers (pure). The client-side frame capture
 * (hooks/useFilmstrip.js) needs a real <video> decoder and is covered in the
 * Playwright pass, not here.
 */
import { thumbnailTimestamps, subtitleBlocks } from "@/lib/filmstrip";

describe("thumbnailTimestamps", () => {
  test("evenly spaced, centered in each cell", () => {
    // 4 thumbs over 8s → cells of 2s, centers at 1,3,5,7.
    expect(thumbnailTimestamps(8, 4)).toEqual([1, 3, 5, 7]);
  });
  test("guards: non-positive duration or count → []", () => {
    expect(thumbnailTimestamps(0, 4)).toEqual([]);
    expect(thumbnailTimestamps(8, 0)).toEqual([]);
    expect(thumbnailTimestamps(-5, 4)).toEqual([]);
  });
});

describe("subtitleBlocks", () => {
  const LINES = [
    { lineStart: 0, lineEnd: 2, words: [{ text: "hello" }, { text: "world" }] },
    { lineStart: 5, lineEnd: 8, words: [{ text: "నమస్తే" }] },
  ];

  test("maps line spans to [0,1] fractions with joined text", () => {
    const b = subtitleBlocks(LINES, 10);
    expect(b).toEqual([
      { left: 0, width: 0.2, lineStart: 0, lineEnd: 2, text: "hello world" },
      { left: 0.5, width: 0.3, lineStart: 5, lineEnd: 8, text: "నమస్తే" },
    ]);
  });

  test("clamps a line that runs past the timeline end", () => {
    const b = subtitleBlocks([{ lineStart: 8, lineEnd: 20, words: [] }], 10);
    expect(b[0].left).toBeCloseTo(0.8, 10);
    expect(b[0].width).toBeCloseTo(0.2, 10); // 8→10, not 8→20
  });

  test("drops zero/negative-width spans and guards bad duration", () => {
    expect(subtitleBlocks([{ lineStart: 3, lineEnd: 3, words: [] }], 10)).toEqual([]);
    expect(subtitleBlocks(LINES, 0)).toEqual([]);
    expect(subtitleBlocks(null, 10)).toEqual([]);
  });
});
