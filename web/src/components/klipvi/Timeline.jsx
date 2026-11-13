import React, { useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Undo2, X } from "lucide-react";
import { bringBack, cutsOf, edgeLimits, moveEdge, partsLength, sameParts, tidy } from "@/lib/clipParts";

const PAD_IN = 0.1; // each piece plays this much before its start and PAD_OUT after its end (selection_v2/vertical.py)
const PAD_OUT = 0.3;
const SNAP_PX = 7;

export const fmtT = (t) => {
  const m = Math.floor(t / 60);
  return `${m}:${(t - m * 60).toFixed(1).padStart(4, "0")}`;
};

/** A time on the clip's own clock -> the same moment in the episode (for the playhead). */
function clipToSource(t, pieces) {
  let off = 0;
  for (const p of pieces) {
    const a = Math.max(0, p.start - PAD_IN);
    const len = p.end + PAD_OUT - a;
    if (t <= off + len) return a + (t - off);
    off += len;
  }
  return pieces.length ? pieces[pieces.length - 1].end : 0;
}

/**
 * The clip on a strip of the episode around it (frames and sound), like the trim timeline of other editors. The
 * turmeric frame is the clip; striped gaps are cuts inside it. Drag the outer handles to start or end earlier or later,
 * drag a cut's edges to make it bigger or smaller, or select a cut to bring it back. Edges snap to where lines start
 * and end (hold Alt to place freely). Clicking the strip previews that moment.
 * `parts` / `setParts`: the parts that play ({start, end} episode seconds). `onScrub(t | null)`: the episode time to
 * preview while dragging or clicking.
 */
export default function Timeline({ timing, stripUrl, parts, setParts, clipTime, onScrub, busy, onApply, onReset }) {
  const box = useRef(null);
  const [width, setWidth] = useState(0);
  const [drag, setDrag] = useState(null); // { label, snapped, t }
  const [picked, setPicked] = useState(null); // index of the selected cut
  useEffect(() => {
    if (!box.current) return undefined;
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);

  const [w0, w1] = timing.window;
  const span = Math.max(1, w1 - w0);
  const x = (t) => ((t - w0) / span) * width;
  const tAt = (clientX) => {
    const r = box.current.getBoundingClientRect();
    return w0 + ((clientX - r.left) / r.width) * span;
  };
  const length = partsLength(parts);
  const changed = !sameParts(parts, timing.pieces);
  const tooLong = length > timing.max_s;
  const cuts = cutsOf(parts);
  const cutOut = cuts.reduce((s, c) => s + (c.end - c.start), 0);
  const start = parts[0].start;
  const end = parts[parts.length - 1].end;
  const sel = picked != null && picked < cuts.length ? cuts[picked] : null;

  // pieces are stored at the lines' own edges; the video maker adds PAD_IN / PAD_OUT itself, so snap to the bare edges
  const snapsFor = useMemo(
    () => ({
      start: timing.lines.map((l) => +l.start.toFixed(2)),
      end: timing.lines.map((l) => +l.end.toFixed(2)),
    }),
    [timing.lines],
  );

  /** Drag part k's start or end edge. */
  const startDrag = (k, side, label) => (e) => {
    e.preventDefault();
    e.stopPropagation();
    const base = parts;
    const limits = edgeLimits(base, k, side, w0, w1, timing.max_s);
    let last = base;
    const move = (ev) => {
      let t = tAt(ev.clientX);
      let snapped = false;
      if (!ev.altKey) {
        const near = snapsFor[side].reduce((best, c) => (Math.abs(x(c) - x(t)) < Math.abs(x(best) - x(t)) ? c : best), Infinity);
        if (Number.isFinite(near) && Math.abs(x(near) - x(t)) < SNAP_PX) {
          t = near;
          snapped = true;
        }
      }
      last = moveEdge(base, k, side, t, limits);
      const v = last[k][side];
      setParts(last);
      setDrag({ label, snapped: snapped && Math.abs(v - t) < 0.01, t: v });
      onScrub?.(v);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setDrag(null);
      const joined = tidy(last);
      if (joined.length !== last.length) {
        setParts(joined); // a cut dragged closed is no cut
        setPicked(null);
      }
      setTimeout(() => onScrub?.(null), 1200);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    move(e);
  };

  const nudge = (k, side) => (e) => {
    const d = e.key === "ArrowLeft" ? -0.1 : e.key === "ArrowRight" ? 0.1 : 0;
    if (!d) return;
    e.preventDefault();
    e.stopPropagation();
    setParts(tidy(moveEdge(parts, k, side, parts[k][side] + d, edgeLimits(parts, k, side, w0, w1, timing.max_s))));
  };

  const ticks = [];
  for (let t = Math.ceil(w0 / 10) * 10; t <= w1; t += 10) ticks.push(t);
  const playhead = clipTime != null && !changed ? clipToSource(clipTime, timing.pieces) : null;
  const n = parts.length - 1;
  const handle = "absolute inset-y-0 grid place-items-center bg-k-accent cursor-ew-resize focus-visible:outline focus-visible:outline-2 focus-visible:outline-white";

  return (
    <div className="w-full">
      <div className="flex items-center justify-between gap-3 mb-1.5 text-sm min-h-[28px]">
        <span className="text-k-muted">
          {drag ? (
            <>
              {drag.label} <span className="font-semibold text-k-text tabular-nums">{fmtT(drag.t)}</span>
              {drag.snapped ? " (on a line edge)" : ""}
            </>
          ) : sel ? (
            <span className="inline-flex items-center gap-2">
              <span>
                Cut <span className="tabular-nums text-k-text">{fmtT(sel.start)}</span> to <span className="tabular-nums text-k-text">{fmtT(sel.end)}</span> (
                {Math.round(sel.end - sel.start)} s)
              </span>
              <button
                type="button"
                onClick={() => {
                  setParts(bringBack(parts, sel.i));
                  setPicked(null);
                }}
                className="inline-flex items-center gap-1 rounded-md border border-k-accent px-2 py-0.5 text-sm text-k-text hover:bg-k-accent/10"
              >
                <Undo2 size={13} aria-hidden="true" />
                Bring back
              </button>
              <button type="button" onClick={() => setPicked(null)} className="p-0.5 text-k-subtle hover:text-k-text" aria-label="Close">
                <X size={14} />
              </button>
            </span>
          ) : (
            <span className="tabular-nums">
              {fmtT(start)} to {fmtT(end)}
              {cuts.length > 0 && (
                <span className="ml-2">
                  {cuts.length === 1 ? "1 cut" : `${cuts.length} cuts`} ({Math.round(cutOut)} s out)
                </span>
              )}
            </span>
          )}
          {!sel && (
            <span className={`ml-2 ${tooLong ? "text-k-bad" : ""}`}>
              {changed ? "new length" : "length"} <span className="font-semibold text-k-text">{Math.round(length)} s</span>
            </span>
          )}
        </span>
        {changed && (
          <span className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setPicked(null);
                onReset();
              }}
              disabled={busy}
              className="rounded-md border border-k-line px-2.5 py-1 text-sm hover:border-k-line2 disabled:opacity-40"
            >
              Reset
            </button>
            <button
              type="button"
              onClick={() => {
                setPicked(null);
                onApply(parts);
              }}
              disabled={busy || tooLong}
              className="inline-flex items-center gap-1.5 rounded-md bg-k-accent text-k-ink font-bold px-3 py-1 text-sm hover:bg-k-accent2 disabled:opacity-50"
            >
              {busy && <Loader2 size={14} className="animate-spin" aria-hidden="true" />}
              {busy ? "Making…" : "Apply"}
            </button>
          </span>
        )}
      </div>

      <div
        ref={box}
        className="relative h-[66px] rounded-lg overflow-hidden bg-k-bg ring-1 ring-k-line select-none cursor-pointer"
        onPointerDown={(e) => {
          onScrub?.(tAt(e.clientX));
          setTimeout(() => onScrub?.(null), 1800);
        }}
        role="group"
        aria-label="Clip timeline: drag the yellow edges to change where the clip starts and ends and where the cuts are"
      >
        {/* frames + sound */}
        {stripUrl && <img src={stripUrl} alt="" className="absolute inset-0 w-full h-[46px] object-fill opacity-90" draggable={false} />}
        <svg className="absolute left-0 right-0 bottom-0 h-[20px] w-full" viewBox={`0 0 ${timing.peaks?.length || 1} 20`} preserveAspectRatio="none" aria-hidden="true">
          {(timing.peaks || []).map((v, i) => (
            <rect key={i} x={i} y={20 - Math.max(0.6, v * 19)} width={0.8} height={Math.max(0.6, v * 19)} fill="rgb(var(--k-muted))" />
          ))}
        </svg>
        {/* ten-second ticks */}
        {width > 0 &&
          ticks.map((t) => (
            <div key={t} className="absolute bottom-0 h-1.5 w-px bg-k-line2" style={{ left: x(t) }} aria-hidden="true" />
          ))}
        {width > 0 && (
          <>
            {/* outside the clip: dimmed */}
            <div className="absolute inset-y-0 left-0 bg-black/60" style={{ width: Math.max(0, x(start)) }} aria-hidden="true" />
            <div className="absolute inset-y-0 right-0 bg-black/60" style={{ width: Math.max(0, width - x(end)) }} aria-hidden="true" />
            {/* each part that plays: framed */}
            {parts.map((p, k) => (
              <div key={`p${k}`} className="absolute inset-y-0 border-y-[3px] border-k-accent pointer-events-none" style={{ left: x(p.start), width: Math.max(2, x(p.end) - x(p.start)) }} />
            ))}
            {/* cuts: striped, select one to bring it back */}
            {cuts.map((c, j) => (
              <button
                key={`c${j}`}
                type="button"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() => setPicked(picked === j ? null : j)}
                className={`absolute inset-y-0 bg-black/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white ${picked === j ? "ring-2 ring-inset ring-white" : ""}`}
                style={{
                  left: x(c.start),
                  width: Math.max(4, x(c.end) - x(c.start)),
                  backgroundImage: "repeating-linear-gradient(135deg, rgba(255,255,255,.2) 0 4px, transparent 4px 9px)",
                }}
                title="Cut out of the clip: click to bring it back"
                aria-label={`Cut from ${fmtT(c.start)} to ${fmtT(c.end)}: select to bring it back`}
              />
            ))}
            {playhead != null && <div className="absolute inset-y-0 w-0.5 bg-white pointer-events-none" style={{ left: x(playhead) }} aria-hidden="true" />}
            {/* the clip's own start and end */}
            <button
              type="button"
              onPointerDown={startDrag(0, "start", "Starts at")}
              onKeyDown={nudge(0, "start")}
              className={`${handle} w-3.5 rounded-l-md`}
              style={{ left: x(start) - 14 }}
              aria-label={`Clip start ${fmtT(start)}: drag, or use the arrow keys`}
            >
              <span className="h-5 w-0.5 rounded bg-k-ink/60" aria-hidden="true" />
            </button>
            <button
              type="button"
              onPointerDown={startDrag(n, "end", "Ends at")}
              onKeyDown={nudge(n, "end")}
              className={`${handle} w-3.5 rounded-r-md`}
              style={{ left: x(end) }}
              aria-label={`Clip end ${fmtT(end)}: drag, or use the arrow keys`}
            >
              <span className="h-5 w-0.5 rounded bg-k-ink/60" aria-hidden="true" />
            </button>
            {/* each cut's two edges (inside the parts, so the cut itself stays clickable) */}
            {cuts.map((c, j) => (
              <React.Fragment key={`e${j}`}>
                <button
                  type="button"
                  onPointerDown={startDrag(c.i, "end", "Cut starts at")}
                  onKeyDown={nudge(c.i, "end")}
                  className={`${handle} w-1.5 rounded-l-sm`}
                  style={{ left: x(c.start) - 6 }}
                  aria-label={`Cut ${j + 1} starts at ${fmtT(c.start)}: drag, or use the arrow keys`}
                />
                <button
                  type="button"
                  onPointerDown={startDrag(c.i + 1, "start", "Cut ends at")}
                  onKeyDown={nudge(c.i + 1, "start")}
                  className={`${handle} w-1.5 rounded-r-sm`}
                  style={{ left: x(c.end) }}
                  aria-label={`Cut ${j + 1} ends at ${fmtT(c.end)}: drag, or use the arrow keys`}
                />
              </React.Fragment>
            ))}
          </>
        )}
      </div>
      <div className="mt-1 flex justify-between gap-3 text-[0.7rem] tabular-nums text-k-subtle" aria-hidden="true">
        <span>{fmtT(w0)}</span>
        <span className="text-center">Drag the yellow edges. Click a striped cut to bring it back. Hold Alt to place freely.</span>
        <span>{fmtT(w1)}</span>
      </div>
    </div>
  );
}
