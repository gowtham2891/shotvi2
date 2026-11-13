/**
 * Caption-sync fix — mirrors tests/test_caption_sync.py exactly.
 *
 * The cutter trims clips at energy-refined boundaries (refine_boundary), up
 * to ~0.5s away from the raw CTC clip start. Captions must use the refined
 * start (served as clip.refined_start / clip.refined_segments) as t=0 so the
 * editor preview matches the actual cut file. Word SELECTION still windows
 * on [clip.start, clip.end] — the filtered word array is the index space for
 * lineSplits/wordEdits and must not change; only the time base shifts.
 */
import { getWordsForRange, getClipWords } from "@/api/transcripts";

// Same fixture values as the backend suite (tests/test_caption_sync.py).
const TRANSCRIPT = {
  word_timestamps: [
    { word: "దీన్ని", start: 10.0, end: 10.5 },
    { word: "control", start: 10.5, end: 11.0 },
    { word: "చూడు", start: 11.0, end: 11.5 },
  ],
  sentences: [
    { id: 0, text: "", start: 10.0, end: 11.0 },
    { id: 1, text: "", start: 11.0, end: 11.5 },
  ],
};

const times = (words) => words.map((w) => [w.start, w.end]);

describe("getWordsForRange with timeZero", () => {
  test("no timeZero → byte-identical to pre-fix behavior", () => {
    expect(times(getWordsForRange(TRANSCRIPT, 10.0, 12.0))).toEqual([
      [0.0, 0.5],
      [0.5, 1.0],
      [1.0, 1.5],
    ]);
  });

  test("timeZero shifts timestamps only, never the word set", () => {
    const plain = getWordsForRange(TRANSCRIPT, 10.0, 12.0);
    const shifted = getWordsForRange(TRANSCRIPT, 10.0, 12.0, 9.6);
    expect(shifted.map((w) => w.text)).toEqual(plain.map((w) => w.text));
    // ids/refs (the wordEdits address space) are untouched by the shift
    expect(shifted.map((w) => w.id)).toEqual(plain.map((w) => w.id));
    expect(times(shifted)).toEqual([
      [0.4, 0.9],
      [0.9, 1.4],
      [1.4, 1.9],
    ]);
  });

  test("selection still windows on [clipStart, clipEnd], not timeZero", () => {
    const words = getWordsForRange(TRANSCRIPT, 10.4, 12.0, 9.0);
    expect(words.map((w) => w.text)).toEqual(["దీన్ని", "control", "చూడు"]);
    expect(words[0].start).toBeCloseTo(1.4, 3);
  });

  test("never produces negative times", () => {
    const words = getWordsForRange(TRANSCRIPT, 10.0, 12.0, 10.2);
    for (const w of words) {
      expect(w.start).toBeGreaterThanOrEqual(0);
      expect(w.end).toBeGreaterThanOrEqual(0);
    }
  });
});

describe("getClipWords with refined fields", () => {
  const SEGMENTS = [
    { start_sent_id: 0, end_sent_id: 0 },
    { start_sent_id: 1, end_sent_id: 1 },
  ];

  test("single-segment clip uses refined_start as t=0", () => {
    const clip = { start: 10.0, end: 12.0, refined_start: 9.6, segments: [] };
    expect(times(getClipWords(TRANSCRIPT, clip))).toEqual([
      [0.4, 0.9],
      [0.9, 1.4],
      [1.4, 1.9],
    ]);
  });

  test("multi-segment without refined_segments matches pre-fix stacking", () => {
    const clip = { start: 10.0, end: 11.5, segments: SEGMENTS };
    expect(times(getClipWords(TRANSCRIPT, clip))).toEqual([
      [0.0, 0.5],
      [0.5, 1.0],
      [1.0, 1.5],
    ]);
  });

  test("multi-segment uses refined per-segment zero and stacking", () => {
    // Same numbers as test_multiseg_uses_refined_segments_for_zero_and_stacking
    const clip = {
      start: 10.0,
      end: 11.5,
      segments: SEGMENTS,
      refined_segments: [
        { start: 9.7, end: 11.1 },
        { start: 10.9, end: 11.6 },
      ],
    };
    expect(times(getClipWords(TRANSCRIPT, clip))).toEqual([
      [0.3, 0.8],
      [0.8, 1.3],
      [1.5, 2.0],
    ]);
  });

  test("refined_segments length mismatch falls back to raw spans", () => {
    const clip = {
      start: 10.0,
      end: 11.5,
      segments: SEGMENTS,
      refined_segments: [{ start: 9.7, end: 11.1 }],
    };
    expect(times(getClipWords(TRANSCRIPT, clip))).toEqual([
      [0.0, 0.5],
      [0.5, 1.0],
      [1.0, 1.5],
    ]);
  });
});
