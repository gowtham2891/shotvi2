/**
 * Feature #14 — filler-word & silence removal (frontend detection + the
 * caption-remap mirror).
 *
 * Detection produces candidate cut spans ([start,end] in clip-local seconds);
 * the store seeds cutSpans from these when the feature is enabled, the user
 * restores individual spans, and the effective list rides the wire to the
 * backend (which renders the cuts and remaps captions with the SAME remap
 * math mirrored in services/filler_removal.py — keep them in lockstep).
 *
 * WYSIWYG note: the editor preview plays the UNCUT media, so cuts show as
 * struck-through transcript words; the effect lands on export (Ganesh's gate).
 */

// Conservative filler set — romanized + common Telugu hesitation sounds. Kept
// small on purpose: the user reviews every struck-through word and restores
// false positives, so over-detection is worse than under-detection.
export const FILLER_WORDS = new Set([
  "um", "uh", "umm", "uhh", "uhm", "hmm", "hm", "erm", "err", "ah", "aa",
  "aah", "eh", "mm", "mmm", "like", "so",
  "ఆ", "ఆఁ", "అ", "ఉమ్", "హ్మ్", "అమ్మ",
]);

const norm = (s) =>
  String(s || "").trim().toLowerCase().replace(/[.,!?;:"'“”‘’()]/g, "");

// Silence = a gap between consecutive words longer than minGap. We keep a
// small pad on each side so speech onsets/tails aren't clipped; the removed
// span is the interior of the gap.
export const DEFAULT_MIN_GAP = 0.6;
export const SILENCE_PAD = 0.1;

// words: [{start,end,text,...}] clip-local. Returns candidate spans with meta
// so the UI can label them: {start, end, kind: 'filler'|'silence', wordIndex?}.
export function detectRemovableSpans(
  words,
  duration,
  { minGap = DEFAULT_MIN_GAP, pad = SILENCE_PAD } = {}
) {
  const spans = [];
  const ws = words || [];
  for (let i = 0; i < ws.length; i++) {
    const w = ws[i];
    if (typeof w.start !== "number" || typeof w.end !== "number") continue;
    // Filler word → remove its own span.
    if (FILLER_WORDS.has(norm(w.text))) {
      spans.push({ start: w.start, end: w.end, kind: "filler", wordIndex: i });
    }
    // Silence gap before the NEXT word.
    const next = ws[i + 1];
    if (next && typeof next.start === "number") {
      const gap = next.start - w.end;
      if (gap > minGap) {
        const s = w.end + pad;
        const e = next.start - pad;
        if (e > s) spans.push({ start: round3(s), end: round3(e), kind: "silence" });
      }
    }
  }
  return mergeSpans(spans);
}

// Merge overlapping/adjacent spans (sorted). Meta collapses to the first
// contributor's kind; the render only needs the [start,end] union.
export function mergeSpans(spans) {
  const sorted = [...(spans || [])]
    .filter((s) => s && s.end > s.start)
    .sort((a, b) => a.start - b.start);
  const out = [];
  for (const s of sorted) {
    const last = out[out.length - 1];
    if (last && s.start <= last.end + 1e-6) {
      last.end = Math.max(last.end, s.end);
    } else {
      out.push({ ...s });
    }
  }
  return out;
}

// Total removed duration strictly BEFORE time t.
export function removedBefore(t, spans) {
  let removed = 0;
  for (const s of spans || []) {
    if (s.end <= t) removed += s.end - s.start;
    else if (s.start < t) removed += t - s.start; // t lands inside this span
  }
  return removed;
}

// Is a word fully or majority inside any cut span (→ dropped from captions)?
export function wordInSpans(word, spans) {
  const mid = (word.start + word.end) / 2;
  return (spans || []).some((s) => mid >= s.start && mid < s.end);
}

// Remap a clip-local time onto the post-cut output timeline.
export function remapTimeAfterCuts(t, spans) {
  return round3(Math.max(0, t - removedBefore(t, spans)));
}

// Drop words inside cuts; shift survivors onto the post-cut timeline. Mirror
// of services/filler_removal.py::apply_cuts_to_words — WYSIWYG contract.
export function applyCutsToWords(words, spans) {
  if (!spans || !spans.length) return words || [];
  const out = [];
  for (const w of words || []) {
    if (wordInSpans(w, spans)) continue;
    out.push({
      ...w,
      start: remapTimeAfterCuts(w.start, spans),
      end: remapTimeAfterCuts(w.end, spans),
    });
  }
  return out;
}

// [start,end] pairs for the wire (backend render). Strips meta.
export function spansToPairs(spans) {
  return (spans || []).map((s) => [round3(s.start), round3(s.end)]);
}

const round3 = (n) => Math.round(n * 1000) / 1000;
