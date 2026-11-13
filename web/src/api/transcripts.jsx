import axios from "axios";
import { client, toApiError, outputFileUrl, pathBasename } from "@/api/client";
import { wordIdFromRef } from "@/lib/transcriptEdits";

// ── Fetchers ─────────────────────────────────────────────────────

// Full transcript JSON for a source video. Sarvam shape:
//   { word_timestamps: [{word, start, end}], sentences: [{id, text, start, end}], ... }
// All times are on the ORIGINAL video's global timeline.
export async function getTranscript(videoId) {
  try {
    const { data } = await client.get(`/transcript/${encodeURIComponent(videoId)}`);
    return data;
  } catch (err) {
    throw toApiError(err, "Transcript not available yet");
  }
}

// xfade segment sidecar written by the pipeline next to *_vertical.mp4 files,
// served by the static /outputs mount. Returns null when the clip has no
// sidecar (single-segment clips, canvas/rerender outputs).
export async function getSegmentSidecar(verticalPath) {
  const base = pathBasename(verticalPath);
  if (!base || !base.includes("_vertical.mp4")) return null;
  const url = outputFileUrl(base.replace("_vertical.mp4", "_vertical_segments.json"));
  try {
    const { data } = await axios.get(url);
    return data; // { segments: [{input_start, input_end, output_start}], total_output_duration }
  } catch {
    return null; // 404 is the normal case for single-segment clips
  }
}

// ── Pure remap helpers (mirror services/caption_renderer.py) ─────
//
// CRITICAL: Telugu word strings are passed through verbatim — never split,
// tokenized, or re-segmented. Backend word boundaries are authoritative.

// caption_renderer.get_words_for_clip: words overlapping [clipStart, clipEnd],
// clamped and shifted to clip-local time. Each word carries its GLOBAL
// transcript ref (and the id derived from it) — the address transcript edits
// use on the wire (apply_transcript_edits.py). The ref must be captured here,
// before the empty-text filter below, because dropping empties means a word's
// clip-local array position no longer matches its global index.
// timeZero (caption-sync fix): absolute timestamp that is t=0 of the cut
// output file — the energy-refined start the cutter actually used (served as
// clip.refined_start). Selection still windows on [clipStart, clipEnd] so the
// filtered word array (the index space for lineSplits/wordEdits) is unchanged;
// only the time base shifts. Defaults to clipStart for pre-fix clips.
export function getWordsForRange(transcript, clipStart, clipEnd, timeZero = null) {
  const zero = timeZero ?? clipStart;
  let rawWords = (transcript?.word_timestamps || []).map((w, index) => ({
    w,
    ref: { type: "flat", index },
  }));
  if (!rawWords.length) {
    // Whisper-format fallback: words nested inside segments
    rawWords = (transcript?.segments || []).flatMap((s, segIndex) =>
      (s.words || []).map((w, wordIndex) => ({
        w,
        ref: { type: "segment", segIndex, wordIndex },
      }))
    );
  }
  const words = [];
  for (const { w, ref } of rawWords) {
    if (w.end > clipStart && w.start < clipEnd) {
      const text = (w.word ?? w.text ?? "").trim();
      if (!text) continue;
      // Tanglish sibling of the word text (Telugu ⇄ Tanglish toggle). The
      // backend serves word_tanglish on every transcript (derived at
      // transcribe time, or backfilled at serve time for old clips); null
      // only if the backfill itself failed — the display resolver then
      // falls back to the Telugu text rather than showing nothing.
      const tanglish = (w.word_tanglish ?? "").trim();
      words.push({
        id: wordIdFromRef(ref),
        ref,
        text,
        text_tanglish: tanglish || null,
        start: round3(Math.max(0, Math.max(w.start, clipStart) - zero)),
        end: round3(Math.max(0, Math.min(w.end, clipEnd) - zero)),
      });
    }
  }
  return words;
}

// caption_renderer.get_words_for_multisegment_clip: non-contiguous segments
// (dead zone cut out) are stacked back-to-back on the output timeline.
// clip.segments entries are sentence-id ranges resolved via transcript.sentences.
export function getClipWords(transcript, clip) {
  const segments = clip?.segments || [];
  if (segments.length <= 1) {
    return getWordsForRange(transcript, clip.start, clip.end, clip.refined_start ?? null);
  }
  const sentById = new Map((transcript?.sentences || []).map((s) => [s.id, s]));
  // Caption-sync fix: the cutter refines each segment's boundaries before
  // concatenating — the stitched file's segments start at refined.start and
  // last (refined.end - refined.start). Mirror of get_words_for_multisegment_clip.
  const refined =
    (clip?.refined_segments?.length || 0) === segments.length ? clip.refined_segments : null;
  const all = [];
  let outputOffset = 0;
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const sStart = sentById.get(Number(seg.start_sent_id))?.start ?? 0;
    const sEnd = sentById.get(Number(seg.end_sent_id))?.end ?? 0;
    const zero = refined ? refined[i].start : null;
    for (const w of getWordsForRange(transcript, sStart, sEnd, zero)) {
      // Spread keeps id/ref intact: the global address survives the stacking,
      // even though the word's output-timeline position has moved.
      all.push({ ...w, start: round3(outputOffset + w.start), end: round3(outputOffset + w.end) });
    }
    outputOffset += refined ? refined[i].end - refined[i].start : sEnd - sStart;
  }
  return all;
}

// caption_renderer remap_time: xfade stitching removes (n-1)*xfade_duration
// seconds, so clip-local times must be remapped onto the sidecar's output
// timeline. No-op when sidecar is null.
export function remapTime(t, sidecar) {
  if (!sidecar?.segments?.length) return t;
  const segs = sidecar.segments;
  for (const seg of segs) {
    if (seg.input_start <= t && t < seg.input_end) {
      return seg.output_start + (t - seg.input_start);
    }
  }
  if (t < segs[0].input_start) return 0.0;
  return sidecar.total_output_duration;
}

export function applySidecarRemap(words, sidecar) {
  if (!sidecar) return words;
  return words.map((w) => ({
    ...w,
    start: round3(remapTime(w.start, sidecar)),
    end: round3(remapTime(w.end, sidecar)),
  }));
}

// One-call convenience: fetch nothing, just combine the pure steps.
export function buildClipTranscript(transcript, clip, sidecar = null) {
  return applySidecarRemap(getClipWords(transcript, clip), sidecar);
}

// A multi-segment clip has a dead zone cut out of the middle, so the actual
// (stitched) video is shorter than its full source span. We can DETECT this
// from ClipOut alone — duration (sum of kept segments) < end-start (full
// span) — even when we can't correctly REMAP it (that needs the segment
// sentence-id ranges, which ClipOut does not yet expose).
const MULTI_SEGMENT_GAP_TOLERANCE_S = 0.75;

export function isMultiSegmentClip(clip) {
  if ((clip?.segments?.length || 0) > 1) return true;
  const span = (clip?.end ?? 0) - (clip?.start ?? 0);
  const dur = clip?.duration ?? span;
  return span - dur > MULTI_SEGMENT_GAP_TOLERANCE_S;
}

// True only when we actually have the segment ranges needed to remap.
export function canRemapMultiSegment(clip) {
  return (clip?.segments?.length || 0) > 1;
}

const round3 = (n) => Math.round(n * 1000) / 1000;
