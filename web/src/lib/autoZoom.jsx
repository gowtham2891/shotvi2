/**
 * Feature #13 — auto-zoom / punch-ins (frontend mirror of
 * services/auto_zoom.py). Keep the two in lockstep: the editor stores punch
 * POINTS (times), and converts them to crop_keyframes at the export boundary
 * with the SAME pulse shape the backend zoompan builder consumes.
 */

export const PUNCH_ZOOM = 1.12;
export const PUNCH_RISE = 0.2;
export const PUNCH_FALL = 0.2;
export const MIN_SPACING = 2.5;
export const GAP_THRESHOLD = 0.35;
export const MAX_PUNCHES = 12;

// Word timestamps → auto punch beats: a word starting after a pause (fresh
// sentence/emphasis beat), throttled so beats never sit closer than minSpacing.
export function generatePunchPoints(
  words,
  { minSpacing = MIN_SPACING, gapThreshold = GAP_THRESHOLD, maxPunches = MAX_PUNCHES } = {}
) {
  const punches = [];
  let last = -Infinity;
  let prevEnd = null;
  for (const w of words || []) {
    const s = w?.start;
    if (typeof s !== "number" || !Number.isFinite(s)) continue;
    // First word has no preceding pause → never an auto-punch (mirror of
    // services/auto_zoom.py: a clip must not open mid-zoom).
    const gap = prevEnd == null ? -1 : s - prevEnd;
    prevEnd = typeof w.end === "number" ? w.end : s;
    if (s - last < minSpacing) continue;
    if (gap >= gapThreshold) {
      punches.push(round3(s));
      last = s;
      if (punches.length >= maxPunches) break;
    }
  }
  return punches;
}

function pulse(t, tp, rise, fall, zoom) {
  if (t <= tp - rise || t >= tp + fall) return 1.0;
  if (t <= tp) return 1.0 + (zoom - 1.0) * (t - (tp - rise)) / rise;
  return 1.0 + (zoom - 1.0) * ((tp + fall) - t) / fall;
}

// Punch times → crop_keyframes [{time,x,y,w,h}] (centered zoom). Overlapping
// punches combine via max(). Byte-for-byte mirror of punches_to_keyframes.
export function punchesToKeyframes(
  punches,
  duration,
  { zoom = PUNCH_ZOOM, rise = PUNCH_RISE, fall = PUNCH_FALL } = {}
) {
  if (!punches?.length || !duration || duration <= 0) return [];
  const breakpoints = new Set();
  for (const tp of punches) {
    for (const t of [tp - rise, tp, tp + fall]) {
      breakpoints.add(round3(Math.max(0, Math.min(duration, t))));
    }
  }
  const out = [];
  for (const t of [...breakpoints].sort((a, b) => a - b)) {
    const z = Math.max(...punches.map((tp) => pulse(t, tp, rise, fall, zoom)));
    const w = round5(1 / z);
    const off = round5((1 - w) / 2);
    out.push({ time: t, x: off, y: off, w, h: w });
  }
  return out;
}

// Add/remove a punch at `t` — nearest existing within `tol` toggles OFF,
// else it's inserted. Returns a NEW sorted array.
export function togglePunch(points, t, tol = 0.15) {
  const near = (points || []).find((p) => Math.abs(p - t) <= tol);
  if (near != null) return points.filter((p) => p !== near);
  return [...(points || []), round3(t)].sort((a, b) => a - b);
}

const round3 = (n) => Math.round(n * 1000) / 1000;
const round5 = (n) => Math.round(n * 100000) / 100000;
