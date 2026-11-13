/**
 * End-to-end multi-segment verification against REAL pipeline output.
 *
 * Reads the actual transcript + clips JSON the backend produced for video
 * CC8V0PwlQ4o and drives the real shipping frontend code (transcripts.js +
 * mapClipToUi) exactly as the editor does. Proves that once ClipOut carries
 * `segments`, clip 4's words land clip-locally with zero drift, and the
 * amber-banner detector stays silent.
 */
import fs from "fs";
import path from "path";
import {
  getWordsForRange,
  getClipWords,
  isMultiSegmentClip,
  canRemapMultiSegment,
} from "@/api/transcripts";
import { mapClipToUi } from "@/api/clips";

const STORAGE = path.resolve(__dirname, "../../../storage/uploads");
const TRANSCRIPT_PATH = path.join(STORAGE, "CC8V0PwlQ4o_audio_transcript.json");
const CLIPS_PATH = path.join(STORAGE, "CC8V0PwlQ4o_audio_clips.json");

const haveRealData =
  fs.existsSync(TRANSCRIPT_PATH) && fs.existsSync(CLIPS_PATH);
const d = haveRealData ? describe : describe.skip;

// Simulate the ClipOut → UI mapping (post-diff: the clip dict carries segments)
function clipOutFromPipeline(clip, i) {
  return {
    clip_id: clip.clip_id || `c${i}`,
    rank: clip.confidence_rank || i,
    why: clip.why || "",
    hook_text: clip.hook_text || "",
    virality_score: clip.virality_score || 0,
    engagement_type: clip.engagement_type || "",
    start: clip.start,
    end: clip.end,
    duration: clip.duration,
    segments: clip.segments || [], // ← the newly-exposed field
  };
}

d("CC8V0PwlQ4o real pipeline data", () => {
  const transcript = JSON.parse(fs.readFileSync(TRANSCRIPT_PATH, "utf-8"));
  const clipsData = JSON.parse(fs.readFileSync(CLIPS_PATH, "utf-8"));
  const rawClip4 = clipsData.clips[3]; // clip 4 (0-indexed) — known multi-segment
  const uiClip4 = mapClipToUi(clipOutFromPipeline(rawClip4, 4), 3, "job-test");

  test("mapClipToUi carries the segment ranges through", () => {
    expect(uiClip4.segments.length).toBe(2);
    expect(uiClip4.segments[0]).toHaveProperty("start_sent_id");
  });

  test("AFTER (with segments): words land clip-locally, zero drift", () => {
    const words = getClipWords(transcript, uiClip4);
    const lastEnd = words[words.length - 1].end;

    // Matches the actual stitched video duration (65.4s), NOT the 75.4s span
    expect(lastEnd).toBeCloseTo(65.44, 1);
    expect(lastEnd).toBeLessThanOrEqual(rawClip4.duration + 0.2);

    // Segment 2's first word sits right after segment 1 (~32.5s), not at the
    // naive 42.4s — the 9.9s dead-zone drift is gone.
    const sentById = new Map(transcript.sentences.map((s) => [s.id, s]));
    const seg1 = rawClip4.segments[0];
    const seg1Dur =
      sentById.get(Number(seg1.end_sent_id)).end -
      sentById.get(Number(seg1.start_sent_id)).start;
    const seg2FirstWord = words.find((w) => w.start >= seg1Dur - 0.05);
    expect(seg2FirstWord.start).toBeCloseTo(seg1Dur, 0);
    expect(seg2FirstWord.start).toBeLessThan(35); // NOT ~42.4
  });

  test("BEFORE (no segments): naive path drifts past the video end — the bug this fixes", () => {
    const naiveClip = { ...uiClip4, segments: [] };
    const naive = getClipWords(transcript, naiveClip); // falls back to full span
    const lastEnd = naive[naive.length - 1].end;
    // Overshoots the real 65.4s video by ~10s and pulls in dead-zone words
    expect(lastEnd).toBeGreaterThan(rawClip4.duration + 5);
    expect(naive.length).toBeGreaterThan(getClipWords(transcript, uiClip4).length);
  });

  test("detector STAYS SILENT once real segment data is present", () => {
    expect(isMultiSegmentClip(uiClip4)).toBe(true); // it IS multi-segment
    expect(canRemapMultiSegment(uiClip4)).toBe(true); // …and we CAN remap → no warning
    // Store's banner condition: isMultiSegment && !canRemap
    expect(isMultiSegmentClip(uiClip4) && !canRemapMultiSegment(uiClip4)).toBe(false);
  });

  test("detector WARNS for a recovered old job that lacks segments (defense-in-depth)", () => {
    const legacyClip = { ...uiClip4, segments: [] };
    expect(isMultiSegmentClip(legacyClip)).toBe(true); // duration-gap detection
    expect(canRemapMultiSegment(legacyClip)).toBe(false);
    expect(isMultiSegmentClip(legacyClip) && !canRemapMultiSegment(legacyClip)).toBe(true);
  });

  test("single-segment clip unchanged by the new field (no regression)", () => {
    // clip 2 (index 1) and clip 3 (index 2) are single-segment
    const single = clipsData.clips.findIndex((c) => (c.segments || []).length <= 1);
    expect(single).toBeGreaterThanOrEqual(0);
    const raw = clipsData.clips[single];
    const ui = mapClipToUi(clipOutFromPipeline(raw, single), single, "job-test");
    expect(isMultiSegmentClip(ui)).toBe(false);
    // With [] segments, getClipWords is byte-identical to plain range slicing
    expect(getClipWords(transcript, ui)).toEqual(
      getWordsForRange(transcript, raw.start, raw.end)
    );
  });
});
