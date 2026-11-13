/**
 * Transcript remap suite — mirrors services/caption_renderer.py exactly.
 *
 * Telugu word strings must pass through verbatim (never split/tokenized),
 * and multi-segment clips (dead zone cut out of the middle) must have word
 * timestamps stacked back-to-back on the output-file timeline, exactly as
 * get_words_for_multisegment_clip does on the backend.
 */
import {
  getWordsForRange,
  getClipWords,
  applySidecarRemap,
  remapTime,
  isMultiSegmentClip,
  canRemapMultiSegment,
} from "@/api/transcripts";

// Global-timeline transcript in the Sarvam shape the backend produces
const TRANSCRIPT = {
  word_timestamps: [
    { word: "నువ్వు", start: 5.16, end: 5.28 },
    { word: "తప్పుని", start: 5.38, end: 5.76 },
    { word: "తప్పు", start: 5.86, end: 6.14 },
    { word: "stage-లో", start: 8.0, end: 8.6 }, // mixed Telugu+English token
    // dead zone (sponsor read) between 10s and 20s
    { word: "బానిసత్వానికి", start: 21.0, end: 21.8 },
    { word: "అర్థం", start: 22.0, end: 22.5 },
  ],
  sentences: [
    { id: 0, text: "…", start: 5.16, end: 9.84 },
    { id: 1, text: "sponsor", start: 10.0, end: 20.0 },
    { id: 2, text: "…", start: 21.0, end: 23.0 },
  ],
};

describe("getWordsForRange (single-segment slicing)", () => {
  test("slices to the clip window and shifts to clip-local time", () => {
    const words = getWordsForRange(TRANSCRIPT, 5.0, 9.0);
    expect(words.map((w) => w.text)).toEqual(["నువ్వు", "తప్పుని", "తప్పు", "stage-లో"]);
    expect(words[0].start).toBeCloseTo(0.16, 3);
    expect(words[0].end).toBeCloseTo(0.28, 3);
    expect(words[3].start).toBeCloseTo(3.0, 3);
  });

  test("Telugu strings are passed through verbatim — no splitting or re-segmentation", () => {
    const words = getWordsForRange(TRANSCRIPT, 5.0, 23.0);
    // Byte-for-byte identity with the backend's word strings, including
    // combining marks and mixed-script tokens.
    expect(words.map((w) => w.text)).toEqual(
      TRANSCRIPT.word_timestamps.map((w) => w.word)
    );
  });

  test("words straddling the boundary are clamped, empty results for out-of-range", () => {
    const clamped = getWordsForRange(TRANSCRIPT, 5.2, 5.25);
    expect(clamped).toHaveLength(1);
    expect(clamped[0].start).toBe(0);
    expect(clamped[0].end).toBeCloseTo(0.05, 3);
    expect(getWordsForRange(TRANSCRIPT, 100, 110)).toEqual([]);
  });
});

describe("getClipWords (multi-segment stacking — backend get_words_for_multisegment_clip)", () => {
  const multiSegmentClip = {
    start: 5.16,
    end: 23.0, // full span INCLUDING the dead zone
    segments: [
      { start_sent_id: 0, end_sent_id: 0 }, // 5.16 → 9.84
      { start_sent_id: 2, end_sent_id: 2 }, // 21.0 → 23.0 (dead zone cut out)
    ],
  };

  test("words after the cut are shifted onto the stitched output timeline", () => {
    const words = getClipWords(TRANSCRIPT, multiSegmentClip);
    expect(words.map((w) => w.text)).toEqual([
      "నువ్వు",
      "తప్పుని",
      "తప్పు",
      "stage-లో",
      "బానిసత్వానికి",
      "అర్థం",
    ]);
    // Segment 1 duration = 9.84 - 5.16 = 4.68 → segment 2 words start at
    // 4.68 + (21.0 - 21.0) = 4.68, NOT at the global 15.84 offset.
    const second = words[4];
    expect(second.start).toBeCloseTo(4.68, 3);
    expect(second.end).toBeCloseTo(5.48, 3);
    // Dead-zone words must not leak in
    expect(words.some((w) => w.start > 10 && w.start < 15)).toBe(false);
  });

  test("single-segment clips fall through to plain range slicing", () => {
    const clip = { start: 5.0, end: 9.0, segments: [{ start_sent_id: 0, end_sent_id: 0 }] };
    expect(getClipWords(TRANSCRIPT, clip)).toEqual(getWordsForRange(TRANSCRIPT, 5.0, 9.0));
  });

  test("clips without a segments field behave as single-segment", () => {
    const clip = { start: 5.0, end: 9.0 };
    expect(getClipWords(TRANSCRIPT, clip)).toEqual(getWordsForRange(TRANSCRIPT, 5.0, 9.0));
  });
});

describe("multi-segment detection (safety net when ClipOut lacks segment ranges)", () => {
  // Real-data shape (CC8V0PwlQ4o clip4): full span 75.4s, but the stitched
  // video is only 65.4s because a 10s dead zone was cut out.
  const realMultiNoRanges = { start: 119.3, end: 194.7, duration: 65.4 };
  const singleSeg = { start: 265.5, end: 348.4, duration: 82.9 };

  test("detects a multi-segment clip from the duration/span gap alone", () => {
    expect(isMultiSegmentClip(realMultiNoRanges)).toBe(true);
    expect(canRemapMultiSegment(realMultiNoRanges)).toBe(false); // no ranges → warn, don't silently misalign
  });

  test("single-segment clips are not flagged", () => {
    expect(isMultiSegmentClip(singleSeg)).toBe(false);
  });

  test("segment ranges, when present, enable correct remap (no warning)", () => {
    const withRanges = {
      ...realMultiNoRanges,
      segments: [
        { start_sent_id: 31, end_sent_id: 43 },
        { start_sent_id: 47, end_sent_id: 54 },
      ],
    };
    expect(isMultiSegmentClip(withRanges)).toBe(true);
    expect(canRemapMultiSegment(withRanges)).toBe(true);
  });

  test("small encoder-rounding gaps do not trip the detector", () => {
    expect(isMultiSegmentClip({ start: 10, end: 40, duration: 39.8 })).toBe(false);
  });
});

describe("applySidecarRemap (xfade sidecar, defensive parity with backend)", () => {
  const sidecar = {
    segments: [
      { input_start: 0, input_end: 4.68, output_start: 0 },
      { input_start: 4.68, input_end: 6.68, output_start: 4.18 }, // 0.5s overlap removed
    ],
    total_output_duration: 6.18,
  };

  test("no-op without a sidecar", () => {
    const words = [{ text: "అర్థం", start: 5.0, end: 5.5 }];
    expect(applySidecarRemap(words, null)).toBe(words);
  });

  test("remaps clip-local times onto the sidecar output timeline", () => {
    expect(remapTime(2.0, sidecar)).toBeCloseTo(2.0, 3);
    expect(remapTime(5.0, sidecar)).toBeCloseTo(4.5, 3);
    expect(remapTime(99, sidecar)).toBeCloseTo(6.18, 3);
    const [w] = applySidecarRemap([{ text: "అర్థం", start: 5.0, end: 5.5 }], sidecar);
    expect(w.start).toBeCloseTo(4.5, 3);
    expect(w.end).toBeCloseTo(5.0, 3);
    expect(w.text).toBe("అర్థం");
  });
});
