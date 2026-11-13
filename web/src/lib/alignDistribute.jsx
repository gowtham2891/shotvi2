/**
 * Feature #10 — align & distribute for multi-selected elements.
 *
 * Pure functions over the document's normalized coordinate system: element
 * x/y are CENTER fractions (0–1) of the canvas, so alignment operates on
 * centers (the same points smart guides snap to) and returns {id: {x, y}}
 * patches for useAppStore.moveElementsTo — which clamps and records ONE
 * history frame. No DOM, no pixels.
 */

const span = (vals) => [Math.min(...vals), Math.max(...vals)];

// axis: 'x' | 'y'; mode: 'min' | 'center' | 'max'
export function alignPatches(elements, ids, axis, mode) {
  const targets = elements.filter((el) => ids.includes(el.id) && !el.locked);
  if (targets.length < 2) return {};
  const vals = targets.map((el) => el[axis]);
  const [lo, hi] = span(vals);
  const to = mode === "min" ? lo : mode === "max" ? hi : (lo + hi) / 2;
  const patches = {};
  for (const el of targets) {
    patches[el.id] = { x: el.x, y: el.y, [axis]: to };
  }
  return patches;
}

// Evenly space centers along an axis, keeping the outermost two in place.
export function distributePatches(elements, ids, axis) {
  const targets = elements.filter((el) => ids.includes(el.id) && !el.locked);
  if (targets.length < 3) return {};
  const sorted = [...targets].sort((a, b) => a[axis] - b[axis]);
  const lo = sorted[0][axis];
  const hi = sorted[sorted.length - 1][axis];
  const step = (hi - lo) / (sorted.length - 1);
  const patches = {};
  sorted.forEach((el, i) => {
    patches[el.id] = { x: el.x, y: el.y, [axis]: lo + step * i };
  });
  return patches;
}
