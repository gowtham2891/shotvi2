import React, { useEffect, useRef } from "react";
import { Loader2, Scissors, Undo2 } from "lucide-react";
import { bringBackAround, cutLine, cutsOf, partOf, partsLength, sameParts, setClipEnd, setClipStart } from "@/lib/clipParts";

const fmt = (t) => {
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
};

/** One edge (start or end) with its half-second nudges. Outside the panel so its buttons survive re-renders. */
function Edge({ label, value, which, nudge }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-sm text-k-muted w-12">{label}</span>
      <div className="flex items-center gap-1">
        <button type="button" onClick={() => nudge(which, -0.5)} className="rounded-md border border-k-line px-2 py-0.5 text-sm hover:border-k-line2" aria-label={`${label}: half a second earlier`}>
          −½s
        </button>
        <span className="w-16 text-center tabular-nums font-semibold">{fmt(value)}</span>
        <button type="button" onClick={() => nudge(which, 0.5)} className="rounded-md border border-k-line px-2 py-0.5 text-sm hover:border-k-line2" aria-label={`${label}: half a second later`}>
          +½s
        </button>
      </div>
    </div>
  );
}

const small = "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs";

/**
 * The clip on the transcript around it (like a text-based editor): every line can become the new start or end, a line
 * that plays can be cut out (with the pause around it), a cut can be brought back, and each edge can be nudged by half
 * a second (to keep a reaction or a music beat). `parts` / `setParts`: the parts that play ({start, end}).
 * `onApply(parts)` makes the new version (the videos are remade).
 */
export default function TimingPanel({ timing, busy, onApply, parts, setParts }) {
  const length = partsLength(parts);
  const changed = !sameParts(parts, timing.pieces);
  const tooLong = length > timing.max_s;
  const start = parts[0].start;
  const end = parts[parts.length - 1].end;
  const cuts = cutsOf(parts);
  const firstPlayed = useRef(null);
  useEffect(() => {
    firstPlayed.current?.scrollIntoView({ block: "start" }); // open at the clip, not a minute before it
  }, []);
  const firstIdx = timing.lines.findIndex((l) => partOf(parts, l) >= 0);
  const nudge = (which, d) => {
    if (which === "start") setParts(setClipStart(parts, Math.max(0, +(start + d).toFixed(2))));
    else setParts(setClipEnd(parts, Math.min(timing.episode_s || Infinity, +(end + d).toFixed(2))));
  };

  return (
    <div className="px-2">
      <p className="text-sm text-k-subtle mb-3">
        Pick a line to start or end on, cut a line out, or bring a cut back. Nudge an edge by half a second to keep a reaction.
      </p>
      <div className="lg:sticky lg:top-[3.15rem] z-10 space-y-2 rounded-xl border border-k-line bg-k-raised p-3 shadow-[0_8px_16px_-8px_rgba(0,0,0,0.5)]">
        <Edge label="Starts" value={start} which="start" nudge={nudge} />
        <Edge label="Ends" value={end} which="end" nudge={nudge} />
        <p className={`text-sm ${tooLong ? "text-k-bad" : "text-k-muted"}`}>
          {changed ? "New length" : "Length"}: <span className="font-semibold text-k-text">{Math.round(length)} s</span>
          {cuts.length > 0 && `, ${cuts.length === 1 ? "1 cut" : `${cuts.length} cuts`} inside`}
          {tooLong && ` (the most is ${timing.max_s} s)`}
        </p>
        <div className="flex gap-2 pt-1">
          <button
            type="button"
            disabled={!changed || tooLong || busy}
            onClick={() => onApply(parts)}
            className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg bg-k-accent text-k-ink font-bold py-1.5 hover:bg-k-accent2 disabled:opacity-50"
          >
            {busy && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
            {busy ? "Making the new version…" : "Apply"}
          </button>
          <button
            type="button"
            disabled={!changed || busy}
            onClick={() => setParts(timing.pieces.map((p) => ({ start: p.start, end: p.end })))}
            className="rounded-lg border border-k-line px-3 py-1.5 text-sm hover:border-k-line2 disabled:opacity-40"
          >
            Reset
          </button>
        </div>
      </div>

      <ol className="mt-4 space-y-1">
        {timing.lines.map((l, i) => {
          const plays = partOf(parts, l) >= 0;
          const inCut = !plays && cuts.some((c) => l.start < c.end && l.end > c.start); // (partly) inside a cut
          const edgeStart = Math.abs(l.start - start) < 0.15;
          const edgeEnd = Math.abs(l.end - end) < 0.15;
          return (
            <li
              key={l.id}
              ref={i === firstIdx ? firstPlayed : undefined}
              style={i === firstIdx ? { scrollMarginTop: "15rem" } : undefined}
              className={`group rounded-lg px-2 py-1.5 ${plays ? "bg-k-raised/70" : "opacity-55 hover:opacity-100 focus-within:opacity-100"}`}>
              <div className="flex items-start gap-2">
                <span className="shrink-0 w-11 pt-0.5 text-xs tabular-nums text-k-subtle">{fmt(l.start).replace(/\.\d$/, "")}</span>
                <span className={`flex-1 text-[0.93rem] leading-normal ${plays ? "text-k-text" : "text-k-muted"} ${inCut ? "line-through decoration-k-subtle" : ""}`}>{l.text}</span>
                {inCut && <span className="shrink-0 rounded px-1.5 text-[0.7rem] font-semibold text-k-muted ring-1 ring-k-line">cut</span>}
              </div>
              <div className="pl-[3.25rem] mt-1 flex flex-wrap gap-1.5 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100">
                {inCut ? (
                  <button type="button" onClick={() => setParts(bringBackAround(parts, l.start, l.end))} className={`${small} border-k-line hover:border-k-line2`}>
                    <Undo2 size={12} aria-hidden="true" />
                    Bring back this cut
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={() => setParts(setClipStart(parts, l.start))}
                      className={`${small} ${edgeStart ? "border-k-accent text-k-accentsoft" : "border-k-line hover:border-k-line2"}`}
                    >
                      Start here
                    </button>
                    <button
                      type="button"
                      onClick={() => setParts(setClipEnd(parts, l.end))}
                      className={`${small} ${edgeEnd ? "border-k-accent text-k-accentsoft" : "border-k-line hover:border-k-line2"}`}
                    >
                      End after this
                    </button>
                    {plays && (
                      <button type="button" onClick={() => setParts(cutLine(parts, l, timing.lines))} className={`${small} border-k-line hover:border-k-line2`}>
                        <Scissors size={12} aria-hidden="true" />
                        Cut this line
                      </button>
                    )}
                  </>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
