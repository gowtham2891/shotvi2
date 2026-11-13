/**
 * Feature #11 — real waveform peaks + word ticks.
 *
 * Pure, DOM-free helpers so the peak math and tick placement are unit-
 * testable in jsdom (where WebAudio does not exist). The actual decode
 * (fetch → AudioContext.decodeAudioData) lives in hooks/useWaveform.js and
 * calls these.
 *
 * Peaks are stored at a fixed HI-RES bucket count once per URL, then
 * resampled down to whatever the timeline currently renders — so a resize
 * never re-decodes the audio.
 */

export const HIRES_BUCKETS = 1024;

// Float32 mono samples → `buckets` per-window peak (max |amplitude|). Raw,
// un-normalized. Empty input / non-positive buckets → [].
export function computePeaks(samples, buckets) {
  const n = samples?.length || 0;
  if (!n || buckets <= 0) return [];
  const size = n / buckets;
  const out = new Array(buckets).fill(0);
  for (let b = 0; b < buckets; b++) {
    const start = Math.floor(b * size);
    const end = Math.min(Math.floor((b + 1) * size), n);
    let peak = 0;
    for (let i = start; i < Math.max(end, start + 1); i++) {
      const a = Math.abs(samples[i]);
      if (a > peak) peak = a;
    }
    out[b] = peak;
  }
  return out;
}

// Scale so the loudest bucket = 1. All-silent (max 0) → all zeros (a flat
// baseline, never NaN).
export function normalizePeaks(peaks) {
  let max = 0;
  for (const p of peaks) if (p > max) max = p;
  if (max <= 0) return peaks.map(() => 0);
  return peaks.map((p) => p / max);
}

// Downsample a hi-res peak array to `buckets` display bars (max within each
// window — the perceptually-correct reduction for a waveform, unlike a mean
// which would flatten transients). Identity when counts already match.
export function resamplePeaks(peaks, buckets) {
  if (!peaks?.length || buckets <= 0) return [];
  if (peaks.length === buckets) return peaks.slice();
  const size = peaks.length / buckets;
  const out = new Array(buckets);
  for (let b = 0; b < buckets; b++) {
    const start = Math.floor(b * size);
    const end = Math.min(Math.floor((b + 1) * size), peaks.length);
    let peak = 0;
    for (let i = start; i < Math.max(end, start + 1); i++) {
      if (peaks[i] > peak) peak = peaks[i];
    }
    out[b] = peak;
  }
  return out;
}

// Word start times → [0,1] fractions across the clip timeline, for the tick
// overlay. Out-of-range / non-numeric starts are dropped. Duration ≤ 0 → [].
export function wordTickFractions(words, duration) {
  if (!duration || duration <= 0) return [];
  const out = [];
  for (const w of words || []) {
    if (typeof w.start !== "number" || !Number.isFinite(w.start)) continue;
    const f = w.start / duration;
    if (f >= 0 && f <= 1) out.push(f);
  }
  return out;
}
