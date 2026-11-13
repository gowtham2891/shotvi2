import React, { useMemo, useRef } from "react";
import {
  Play,
  Pause,
  Rewind,
  FastForward,
  Scissors,
} from "lucide-react";
import { useAppStore } from "@/store/useAppStore";
import { EDITOR } from "@/constants/testIds";
import EditableTranscript from "@/components/editor/EditableTranscript";
import { useWaveform } from "@/hooks/useWaveform";
import { wordTickFractions } from "@/lib/waveform";
import { useFilmstrip } from "@/hooks/useFilmstrip";
import { subtitleBlocks } from "@/lib/filmstrip";
import { buildCaptionLines } from "@/lib/captionLines";
import { getCaptionStylePreview } from "@/data/captionStylePreview";
import { generatePunchPoints } from "@/lib/autoZoom";
import { Zap } from "lucide-react";

const WAVE_BARS = 120;
const FILM_THUMBS = 14;
// Flat baseline shown while decoding, or when audio can't be decoded — a
// clearly-inert line, never the old fabricated sine curve.
const FLAT_PEAKS = Array.from({ length: WAVE_BARS }, () => 0.1);

const clamp01 = (v) => Math.max(0, Math.min(1, v));

const formatTime = (t) => {
  const m = Math.floor(t / 60)
    .toString()
    .padStart(2, "0");
  const s = Math.floor(t % 60)
    .toString()
    .padStart(2, "0");
  const ms = Math.floor((t % 1) * 10);
  return `${m}:${s}.${ms}`;
};

/**
 * TimelineRow — transport controls + waveform scrubber + word-level chips.
 */
export const TimelineRow = () => {
  const currentTime = useAppStore((s) => s.currentTime);
  const duration = useAppStore((s) => s.duration);
  const isPlaying = useAppStore((s) => s.isPlaying);
  const togglePlay = useAppStore((s) => s.togglePlay);
  const seek = useAppStore((s) => s.seek);
  const transcriptStatus = useAppStore((s) => s.transcriptStatus);
  const transcriptError = useAppStore((s) => s.transcriptError);
  const transcriptWarning = useAppStore((s) => s.transcriptWarning);
  const retryTranscript = useAppStore((s) => s.retryTranscript);
  const trimStart = useAppStore((s) => s.exportSettings.trimStart);
  const trimEnd = useAppStore((s) => s.exportSettings.trimEnd);
  const resetTrim = useAppStore((s) => s.resetTrim);
  const transcript = useAppStore((s) => s.transcript);
  // The clip's own media file (raw cut / vertical). Its audio track is what
  // the waveform decodes; its duration equals the editor's clip duration
  // (both use the refined cut boundaries — feature #1), so peaks and word
  // ticks share the timeline's [0, duration] axis.
  const waveUrl = useAppStore((s) => s.currentClip?.videoUrl || s.currentClip?.previewUrl || null);

  const barRef = useRef(null);

  // Feature #11: real peaks (WebAudio) replace the retired Math.sin bars.
  const { peaks, status: waveStatus } = useWaveform(waveUrl, WAVE_BARS);
  const displayPeaks = peaks && peaks.length ? peaks : FLAT_PEAKS;
  // Word ticks from existing clip-local word timestamps.
  const tickFractions = useMemo(
    () => wordTickFractions(transcript, duration),
    [transcript, duration]
  );

  // Feature #12: filmstrip thumbnails (client-side frame capture) + subtitle
  // blocks from the SAME caption lines the preview/burn agree on.
  const { frames, status: filmStatus } = useFilmstrip(waveUrl, duration, FILM_THUMBS);
  const lineSplits = useAppStore((s) => s.transcriptEdits.lineSplits);
  const presetId = useAppStore(
    (s) => s.elements.find((el) => el.type === "caption")?.props?.presetId
  );
  const blocks = useMemo(() => {
    const wpl = getCaptionStylePreview(presetId)?.wordsPerLine || 4;
    const lines = buildCaptionLines(transcript, wpl, lineSplits);
    return subtitleBlocks(lines, duration);
  }, [transcript, presetId, lineSplits, duration]);

  // Feature #13 — effective punch points (user set, or auto from word beats).
  // Subscribed to both inputs so the markers re-derive when either changes.
  const punchStored = useAppStore((s) => s.exportSettings.punchPoints);
  const punchPoints = useMemo(
    () => (Array.isArray(punchStored) ? punchStored : generatePunchPoints(transcript)),
    [punchStored, transcript]
  );

  // Feature #14 — filler/silence removal state.
  const cutSpans = useAppStore((s) => s.exportSettings.cutSpans);
  const fillerOn = Array.isArray(cutSpans);
  const removedSecs = fillerOn
    ? cutSpans.reduce((a, s) => a + (s.end - s.start), 0)
    : 0;

  // Effective trim window (trimEnd -1 sentinel → clip end) — same math as
  // the store's getTrimBounds, kept local for render.
  const effStart = duration ? Math.min(Math.max(trimStart || 0, 0), duration) : 0;
  const effEnd = duration ? (trimEnd > 0 ? Math.min(trimEnd, duration) : duration) : 0;
  const trimmed = effStart > 0 || (duration > 0 && effEnd < duration);
  const startPct = duration ? (effStart / duration) * 100 : 0;
  const endPct = duration ? (effEnd / duration) * 100 : 100;

  // Drag a trim handle: pointer x → clip time → store (clamped there). The
  // whole drag coalesces into ONE undo frame; pointerup closes the gesture.
  const startTrimDrag = (which) => (e) => {
    if (!duration) return;
    e.preventDefault();
    e.stopPropagation();
    const rect = barRef.current?.getBoundingClientRect();
    if (!rect) return;
    const move = (ev) => {
      const t = clamp01((ev.clientX - rect.left) / rect.width) * duration;
      const s = useAppStore.getState();
      const { start, end } = s.getTrimBounds();
      if (which === "start") s.setTrimRange(t, end);
      else s.setTrimRange(start, t);
    };
    const up = () => {
      useAppStore.getState().endHistoryCoalescing();
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    move(e); // apply the grab position immediately
  };

  const playheadPct = Math.max(
    0,
    Math.min(100, (currentTime / duration) * 100)
  );

  return (
    <div className="border-t border-k-raised bg-k-bg shrink-0">
      {/* Transport */}
      <div className="h-12 flex items-center justify-between px-4 border-b border-k-raised">
        <div className="flex items-center gap-1">
          <button
            data-testid={EDITOR.seekBackBtn}
            onClick={() => seek(currentTime - 1)}
            className="w-8 h-8 rounded-md hover:bg-white/5 text-k-muted hover:text-white flex items-center justify-center transition-colors"
          >
            <Rewind size={14} />
          </button>
          <button
            data-testid={EDITOR.playBtn}
            onClick={togglePlay}
            className="w-10 h-10 rounded-full bg-k-accent hover:bg-k-accent2 text-k-ink flex items-center justify-center transition-colors shadow-[0_0_20px_rgba(233,168,37,0.5)]"
          >
            {isPlaying ? (
              <Pause size={16} fill="white" />
            ) : (
              <Play size={16} fill="white" className="ml-0.5" />
            )}
          </button>
          <button
            data-testid={EDITOR.seekFwdBtn}
            onClick={() => seek(currentTime + 1)}
            className="w-8 h-8 rounded-md hover:bg-white/5 text-k-muted hover:text-white flex items-center justify-center transition-colors"
          >
            <FastForward size={14} />
          </button>
        </div>

        <div className="font-mono text-xs text-k-muted">
          <span className="text-white">{formatTime(currentTime)}</span>
          <span className="text-k-faint mx-1.5">/</span>
          {formatTime(duration)}
        </div>

        <div className="text-[11px] text-k-subtle flex items-center gap-2">
          <kbd className="px-1.5 py-0.5 rounded bg-k-surface border border-k-line font-mono text-[9px]">
            Space
          </kbd>
          <span>Play</span>
          <kbd className="px-1.5 py-0.5 rounded bg-k-surface border border-k-line font-mono text-[9px]">
            ← →
          </kbd>
          <span>Nudge</span>
        </div>
      </div>

      {/* Feature #12 — filmstrip band: thumbnails + subtitle blocks + scrub.
          Shares the timeline's [0, duration] axis with the waveform below;
          click anywhere to seek, playhead + trim dimming mirror the scrubber. */}
      <div className="relative h-14 border-b border-k-raised px-4 flex items-center">
        <div
          data-testid={EDITOR.filmstrip}
          data-film-status={filmStatus}
          className="relative flex-1 h-12 bg-k-bg rounded overflow-hidden cursor-pointer"
          title="Click to seek · Alt+click to add/remove an auto-zoom punch"
          onClick={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            const t = ((e.clientX - rect.left) / rect.width) * duration;
            // Feature #13: Alt+click toggles a punch here; plain click seeks.
            if (e.altKey) {
              useAppStore.getState().togglePunchPoint(t);
              return;
            }
            seek(t);
          }}
        >
          {/* Thumbnail row — tiled evenly; neutral cells until frames decode. */}
          <div className="absolute inset-0 flex">
            {(frames.length ? frames : Array.from({ length: FILM_THUMBS })).map((src, i) => (
              <div key={i} className="h-full flex-1 border-r border-black/40 overflow-hidden bg-k-surface">
                {src && (
                  <img src={src} alt="" draggable={false}
                    className="h-full w-full object-cover select-none" />
                )}
              </div>
            ))}
          </div>

          {/* Trim dimming (mirror of the scrubber) */}
          {startPct > 0 && (
            <div className="absolute top-0 bottom-0 left-0 bg-black/60 pointer-events-none z-10"
              style={{ width: `${startPct}%` }} />
          )}
          {endPct < 100 && (
            <div className="absolute top-0 bottom-0 right-0 bg-black/60 pointer-events-none z-10"
              style={{ width: `${100 - endPct}%` }} />
          )}

          {/* Feature #13 — punch markers (auto-zoom points). Click a marker to
              remove it; Alt+click empty timeline to add. */}
          <div data-testid={EDITOR.punchMarkers} className="absolute inset-x-0 top-0 h-full pointer-events-none z-20">
            {duration > 0 && punchPoints.map((t, i) => (
              <button
                key={i}
                data-testid={EDITOR.punchMarker(i)}
                title={`Auto-zoom punch at ${formatTime(t)} — click to remove`}
                onClick={(e) => { e.stopPropagation(); useAppStore.getState().togglePunchPoint(t); }}
                className="absolute top-0 -ml-1.5 w-3 h-3 pointer-events-auto cursor-pointer"
                style={{ left: `${(t / duration) * 100}%` }}
              >
                <span className="block w-0 h-0 border-l-[6px] border-r-[6px] border-t-[7px] border-l-transparent border-r-transparent border-t-k-ok drop-shadow-[0_0_4px_rgba(34,255,156,0.9)]" />
              </button>
            ))}
          </div>

          {/* Playhead */}
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-k-accent pointer-events-none z-20"
            style={{ left: `${playheadPct}%`, boxShadow: "0 0 10px rgba(233,168,37,0.9)" }}
          />
        </div>
      </div>

      {/* Feature #13 — auto-zoom controls */}
      <div className="flex items-center gap-2 px-4 py-1.5 border-b border-k-raised text-[11px] text-k-muted">
        <Zap size={11} className="text-k-ok" />
        <span>
          Auto-zoom: <span className="font-mono text-white">{punchPoints.length}</span> punch
          {punchPoints.length === 1 ? "" : "es"}
        </span>
        <span className="text-k-line2">·</span>
        <span className="text-k-subtle">
          <kbd className="px-1 py-0.5 rounded bg-k-surface border border-k-line font-mono text-[9px] mr-1">Alt</kbd>
          +click timeline to add/remove
        </span>
        <button
          data-testid={EDITOR.punchAuto}
          onClick={() => useAppStore.getState().autoGeneratePunchPoints()}
          className="ml-auto text-[10px] font-semibold text-k-accentsoft hover:text-white transition-colors"
        >
          Auto
        </button>
        <button
          data-testid={EDITOR.punchClear}
          onClick={() => useAppStore.getState().clearPunchPoints()}
          className="text-[10px] font-semibold text-k-subtle hover:text-white transition-colors"
        >
          Clear
        </button>
      </div>

      {/* Feature #14 — filler/silence removal controls */}
      <div className="flex items-center gap-2 px-4 py-1.5 border-b border-k-raised text-[11px] text-k-muted">
        <Scissors size={11} className={fillerOn ? "text-k-ok" : "text-k-faint"} />
        <span>
          Filler / silence:{" "}
          {fillerOn ? (
            <>
              <span className="font-mono text-white">{cutSpans.length}</span> cut
              {cutSpans.length === 1 ? "" : "s"}
              <span className="text-k-faint"> · saves </span>
              <span className="font-mono text-white">{removedSecs.toFixed(1)}s</span>
            </>
          ) : (
            <span className="text-k-subtle">off</span>
          )}
        </span>
        {fillerOn && (
          <span className="text-k-subtle">
            <span className="text-k-line2">·</span> click a{" "}
            <span className="line-through text-k-faint">struck word</span> to restore
          </span>
        )}
        <button
          data-testid={EDITOR.fillerToggle}
          onClick={() =>
            fillerOn
              ? useAppStore.getState().disableFillerRemoval()
              : useAppStore.getState().enableFillerRemoval()
          }
          className="ml-auto text-[10px] font-semibold text-k-accentsoft hover:text-white transition-colors"
        >
          {fillerOn ? "Turn off" : "Detect fillers"}
        </button>
      </div>

      {/* Waveform / playhead scrubber + trim handles */}
      <div className="relative h-12 border-b border-k-raised px-4 flex items-center">
        <div
          ref={barRef}
          data-testid={EDITOR.waveform}
          data-wave-status={waveStatus}
          className="relative flex-1 h-8 bg-k-surface rounded overflow-hidden"
        >
          {/* Real peaks (WebAudio-decoded) — bars past the playhead dim.
              Height floors at 6% so silence still reads as a baseline. */}
          <div className="absolute inset-0 flex items-center gap-[2px] px-1">
            {displayPeaks.map((p, i) => (
              <div
                key={i}
                className="wave-bar flex-1"
                style={{
                  height: `${Math.max(6, Math.min(100, p * 100))}%`,
                  opacity: (i / displayPeaks.length) * 100 < playheadPct ? 1 : 0.35,
                }}
              />
            ))}
          </div>
          {/* Word ticks — one mark per word start, from the clip's existing
              word-level timestamps. Subtle so they read as a guide, not clutter. */}
          <div
            data-testid={EDITOR.waveformTicks}
            className="absolute inset-x-1 bottom-0 top-0 pointer-events-none"
          >
            {tickFractions.map((f, i) => (
              <div
                key={i}
                className="absolute bottom-0 w-px h-1.5 bg-white/25"
                style={{ left: `${f * 100}%` }}
              />
            ))}
          </div>
          <div
            data-testid={EDITOR.timelinePlayhead}
            className="absolute top-0 bottom-0 w-0.5 bg-k-accent pointer-events-none z-10"
            style={{
              left: `${playheadPct}%`,
              boxShadow: "0 0 12px rgba(233,168,37,1)",
            }}
          >
            <div className="absolute -top-1 -left-1.5 w-3.5 h-3.5 bg-k-accent rounded-sm rotate-45 shadow-[0_0_10px_rgba(233,168,37,0.9)]" />
          </div>
          <div
            className="absolute inset-0 cursor-pointer"
            onClick={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const x = (e.clientX - rect.left) / rect.width;
              seek(x * duration);
            }}
          />

          {/* Trimmed-out regions (excluded from playback + export) */}
          {startPct > 0 && (
            <div
              className="absolute top-0 bottom-0 left-0 bg-black/60 pointer-events-none z-10"
              style={{ width: `${startPct}%` }}
            />
          )}
          {endPct < 100 && (
            <div
              className="absolute top-0 bottom-0 right-0 bg-black/60 pointer-events-none z-10"
              style={{ width: `${100 - endPct}%` }}
            />
          )}

          {/* Trim handles — drag to contract the clip's start/end. The
              backend re-cuts the clip's own file (trim_start/trim_end in
              api/renders.js), so handles only move inward from the bounds. */}
          {duration > 0 && (
            <>
              <div
                data-testid={EDITOR.trimStartHandle}
                onPointerDown={startTrimDrag("start")}
                title="Drag to trim the clip start"
                className="absolute top-0 bottom-0 w-2 -ml-1 z-20 cursor-ew-resize group"
                style={{ left: `${startPct}%` }}
              >
                <div className="absolute inset-y-0 left-1/2 -translate-x-1/2 w-1 rounded bg-k-ok group-hover:bg-white transition-colors" />
              </div>
              <div
                data-testid={EDITOR.trimEndHandle}
                onPointerDown={startTrimDrag("end")}
                title="Drag to trim the clip end"
                className="absolute top-0 bottom-0 w-2 -ml-1 z-20 cursor-ew-resize group"
                style={{ left: `${endPct}%` }}
              >
                <div className="absolute inset-y-0 left-1/2 -translate-x-1/2 w-1 rounded bg-k-ok group-hover:bg-white transition-colors" />
              </div>
            </>
          )}
        </div>
      </div>

      {/* Caption track — the caption LINES as duration-proportional pills on
          the SAME [0, duration] axis as the seek slider directly above, so the
          captions read as a track UNDER the video slider (the CapCut / Descript
          / VEED layout). Click a pill (or empty track) to seek; the active line
          lights up as the playhead crosses it; trim dimming + playhead mirror
          the slider so the whole group reads as one timeline. */}
      <div className="relative h-9 border-b border-k-raised px-4 flex items-center">
        <div
          data-testid={EDITOR.subtitleBlocks}
          className="relative flex-1 h-7 bg-k-bg rounded overflow-hidden cursor-pointer"
          title="Caption track — click to seek"
          onClick={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            seek(((e.clientX - rect.left) / rect.width) * duration);
          }}
        >
          {blocks.length === 0 && (
            <span className="absolute inset-0 flex items-center justify-center text-[10px] text-k-faint pointer-events-none">
              Caption lines appear here
            </span>
          )}
          {blocks.map((b, i) => {
            const active = currentTime >= b.lineStart && currentTime < b.lineEnd;
            return (
              <div
                key={i}
                title={b.text}
                onClick={(e) => {
                  e.stopPropagation();
                  seek(b.lineStart);
                }}
                className={`absolute top-1 bottom-1 rounded border truncate text-[10px] leading-5 px-1.5 flex items-center cursor-pointer transition-colors ${
                  active
                    ? "bg-k-accent/85 border-k-accentsoft text-white"
                    : "bg-k-accent/35 border-k-accent/50 text-white/85 hover:bg-k-accent/55"
                }`}
                style={{
                  left: `calc(${b.left * 100}% + 1px)`,
                  width: `calc(${b.width * 100}% - 2px)`,
                }}
              >
                {b.text}
              </div>
            );
          })}
          {/* Trim dimming — mirror of the slider. */}
          {startPct > 0 && (
            <div
              className="absolute top-0 bottom-0 left-0 bg-black/60 pointer-events-none z-10"
              style={{ width: `${startPct}%` }}
            />
          )}
          {endPct < 100 && (
            <div
              className="absolute top-0 bottom-0 right-0 bg-black/60 pointer-events-none z-10"
              style={{ width: `${100 - endPct}%` }}
            />
          )}
          {/* Playhead — same axis as the slider directly above. */}
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-k-accent pointer-events-none z-20"
            style={{ left: `${playheadPct}%`, boxShadow: "0 0 10px rgba(233,168,37,0.9)" }}
          />
        </div>
      </div>

      {/* Trim readout — only when a trim is active */}
      {trimmed && (
        <div className="flex items-center gap-2 px-4 py-1.5 border-b border-k-raised text-[11px] text-k-muted">
          <Scissors size={11} className="text-k-ok" />
          <span>
            Trimmed: <span className="font-mono text-white">{formatTime(effStart)}</span>
            {" – "}
            <span className="font-mono text-white">{formatTime(effEnd)}</span>
            {" · exports "}
            <span className="font-mono text-white">{(effEnd - effStart).toFixed(1)}s</span>
          </span>
          <button
            data-testid={EDITOR.trimReset}
            onClick={resetTrim}
            className="ml-auto text-[10px] font-semibold text-k-accentsoft hover:text-white transition-colors"
          >
            Reset trim
          </button>
        </div>
      )}

      {/* Editable transcript (caption lines = preview/export grouping) */}
      <div className="px-4 py-3 min-h-[76px] max-h-[132px] overflow-y-auto">
        <div className="flex items-center justify-between mb-2">
          <p className="text-[10px] uppercase tracking-[0.22em] text-k-faint">
            Transcript · Editable
          </p>
          <span className="text-[10px] text-k-subtle">
            <kbd className="px-1 py-0.5 rounded bg-k-surface border border-k-line font-mono text-[9px] mr-1">Enter</kbd>
            split
            <span className="mx-1.5 text-k-line2">·</span>
            <kbd className="px-1 py-0.5 rounded bg-k-surface border border-k-line font-mono text-[9px] mr-1">⌫</kbd>
            merge
            <span className="mx-1.5 text-k-line2">·</span>
            type to fix
            <span className="mx-1.5 text-k-line2">·</span>
            <kbd className="px-1 py-0.5 rounded bg-k-surface border border-k-line font-mono text-[9px] mr-1">Alt</kbd>
            +click emphasize
          </span>
        </div>
        {transcriptStatus === "loading" && (
          <p className="text-xs text-k-subtle py-2">Loading transcript…</p>
        )}
        {transcriptStatus === "error" && (
          <div className="flex items-center gap-3 py-2">
            <p className="text-xs text-k-bad">{transcriptError}</p>
            <button
              onClick={retryTranscript}
              className="text-[11px] font-semibold text-k-ink bg-k-accent hover:bg-k-accent2 px-2.5 py-1 rounded-md transition-colors"
            >
              Retry
            </button>
          </div>
        )}
        {transcriptStatus === "ready" && transcriptWarning && (
          <div className="mb-2 rounded-md border border-k-warn/40 bg-k-warn/10 px-2.5 py-1.5 text-[11px] text-k-accentsoft leading-snug">
            {transcriptWarning}
          </div>
        )}
        {transcriptStatus === "ready" && <EditableTranscript />}
      </div>
    </div>
  );
};

export default TimelineRow;
