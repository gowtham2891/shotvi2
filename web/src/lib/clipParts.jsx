/**
 * A clip is a list of parts of the episode that play ({start, end} in episode seconds); between two parts is a cut.
 * These helpers change that list the way the editor's timeline and transcript do. The backend
 * (backend/clipedit.py repiece) checks the result again and keeps the same rules: a part plays at least MIN_PART_S,
 * parts closer than MERGE_GAP_S are one part.
 */
export const MIN_PART_S = 0.2;
export const MERGE_GAP_S = 0.05;

const r2 = (t) => +(+t).toFixed(2);
export const copyParts = (parts) => parts.map((p) => ({ start: r2(p.start), end: r2(p.end) }));

/** Played length (seconds, without the small padding the video maker adds), as the backend counts it. */
export const partsLength = (parts) => parts.reduce((s, p) => s + (p.end - p.start), 0);

export const sameParts = (a, b) =>
  a.length === b.length && a.every((p, i) => Math.abs(p.start - b[i].start) < 0.01 && Math.abs(p.end - b[i].end) < 0.01);

/** The cuts between parts: [{ i, start, end }] where i is the part before the cut. */
export const cutsOf = (parts) => parts.slice(1).map((p, i) => ({ i, start: parts[i].end, end: p.start }));

/** Sorted, with touching or overlapping parts joined. */
export function tidy(parts) {
  const out = [];
  for (const p of [...parts].sort((a, b) => a.start - b.start)) {
    const last = out[out.length - 1];
    if (last && p.start <= last.end + MERGE_GAP_S) last.end = Math.max(last.end, p.end);
    else out.push({ start: r2(p.start), end: r2(p.end) });
  }
  return out;
}

/** Start the clip at `t`: parts that would end before it are dropped. */
export function setClipStart(parts, t) {
  const keep = parts.filter((p) => p.end - MIN_PART_S > t);
  if (!keep.length) return parts;
  return [{ ...keep[0], start: r2(Math.max(0, t)) }, ...keep.slice(1)];
}

/** End the clip at `t`: parts that would start after it are dropped. */
export function setClipEnd(parts, t) {
  const keep = parts.filter((p) => p.start + MIN_PART_S < t);
  if (!keep.length) return parts;
  const n = keep.length - 1;
  return [...keep.slice(0, n), { ...keep[n], end: r2(t) }];
}

/** Bring back the cut after part i (join parts i and i + 1). */
export const bringBack = (parts, i) =>
  i < 0 || i >= parts.length - 1 ? parts : [...parts.slice(0, i), { start: parts[i].start, end: parts[i + 1].end }, ...parts.slice(i + 2)];

/** Bring back every cut that touches [a, b] (a line that sits inside a cut). */
export function bringBackAround(parts, a, b) {
  const c = cutsOf(parts).find((x) => a < x.end && b > x.start);
  return c ? bringBack(parts, c.i) : parts;
}

/** Where a line plays: the index of the part that holds it, or -1. */
export const partOf = (parts, line) => parts.findIndex((p) => line.start >= p.start - 0.05 && line.end <= p.end + 0.05);

/**
 * Cut one transcript line out of the clip: the cut runs from the end of the line before it to the start of the line
 * after it (inside the same part), so the pause around the line goes too and nothing of it is heard. The first or
 * last line of a part moves that part's edge instead; a part's only line removes the part (not the clip's last one).
 */
export function cutLine(parts, line, lines) {
  const k = partOf(parts, line);
  if (k < 0) return parts;
  const p = parts[k];
  const inPart = lines.filter((l) => l.id !== line.id && l.start >= p.start - 0.05 && l.end <= p.end + 0.05);
  const before = inPart.filter((l) => l.end <= line.start + 0.01);
  const after = inPart.filter((l) => l.start >= line.end - 0.01);
  const prevEnd = before.length ? Math.max(...before.map((l) => l.end)) : null;
  const nextStart = after.length ? Math.min(...after.map((l) => l.start)) : null;
  let repl;
  if (prevEnd == null && nextStart == null) repl = [];
  else if (prevEnd == null) repl = [{ start: nextStart, end: p.end }];
  else if (nextStart == null) repl = [{ start: p.start, end: prevEnd }];
  else repl = [{ start: p.start, end: prevEnd }, { start: nextStart, end: p.end }];
  repl = repl.filter((x) => x.end - x.start >= MIN_PART_S);
  const out = [...parts.slice(0, k), ...repl, ...parts.slice(k + 1)];
  return out.length ? tidy(out) : parts;
}

/**
 * How far one edge can move: part k's "start" or "end", kept inside [w0, w1], away from its neighbours, each part at
 * least MIN_PART_S, and the whole clip at most maxS long. Returns [lo, hi].
 */
export function edgeLimits(parts, k, side, w0, w1, maxS) {
  const p = parts[k];
  const other = partsLength(parts) - (p.end - p.start);
  const room = maxS - other;
  if (side === "start") {
    const lo = Math.max(w0, 0, k > 0 ? parts[k - 1].end : -Infinity, p.end - room);
    return [lo, p.end - MIN_PART_S];
  }
  const hi = Math.min(w1, k < parts.length - 1 ? parts[k + 1].start : Infinity, p.start + room);
  return [p.start + MIN_PART_S, hi];
}

/** Move one edge to `t` (clamped by edgeLimits). A cut dragged closed is joined by `tidy` when the drag ends. */
export function moveEdge(parts, k, side, t, limits) {
  const [lo, hi] = limits;
  const v = r2(Math.min(hi, Math.max(lo, t)));
  return parts.map((p, i) => (i === k ? { ...p, [side]: v } : p));
}
