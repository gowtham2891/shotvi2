import React, { useRef, useEffect, useCallback } from "react";
import { useAppStore, clamp } from "@/store/useAppStore";
import { resolveEditorKey } from "@/lib/editorKeymap";
import { isTextEditingTarget } from "@/lib/editableTranscript";
import { CanvasToolbar } from "@/components/editor/CanvasToolbar";
import { SafeZoneOverlay } from "@/components/editor/SafeZoneOverlay";
import { SmartGuides } from "@/components/editor/SmartGuides";
import { ElementRenderer } from "@/components/editor/ElementRenderer";
import { CropReframeOverlay } from "@/components/editor/CropReframeOverlay";
import {
  inferMasterAspect,
  initialWindowForAspect,
  cropVideoLayout,
  cropFillLayout,
  containFit,
} from "@/lib/cropWindow";
import { EDITOR } from "@/constants/testIds";

const NUDGE = 1; // px
const NUDGE_BIG = 10; // px with shift

// Per-aspect stage dimensions (CSS px at 100% zoom). Heights mirror the
// backend render's aspect-relative proportions (FORMAT_CONFIG in
// api/worker.py: 1080x1920 / 1080x1080 / 1920x1080): every element sizes as
// a fraction of canvas HEIGHT, so preview px = burn px × (stageH/renderH)
// per aspect — the same parity relationship the fixed 9:16 stage had.
export const STAGE_DIMS = {
  "9:16": { w: 360, h: 640 },
  "1:1": { w: 560, h: 560 },
  "16:9": { w: 960, h: 540 },
};
// Matches FORMAT_CONFIG in api/worker.py — used to scale preview effects
// (blur radius) from render-resolution values.
const RENDER_DIMS = {
  "9:16": { w: 1080, h: 1920 },
  "1:1": { w: 1080, h: 1080 },
  "16:9": { w: 1920, h: 1080 },
};
export const stageDimsForAspect = (aspect) => STAGE_DIMS[aspect] || STAGE_DIMS["9:16"];

const FIT_PADDING_PX = 32; // breathing room on each side at 100%-fit zoom

// Largest zoom that keeps the full stage inside the viewport, padded.
const computeFitZoom = (viewportEl, stageW, stageH) => {
  if (!viewportEl) return 1;
  const { width, height } = viewportEl.getBoundingClientRect();
  const availW = Math.max(width - FIT_PADDING_PX * 2, 40);
  const availH = Math.max(height - FIT_PADDING_PX * 2, 40);
  return clamp(Math.min(availW / stageW, availH / stageH), 0.4, 3);
};

/**
 * CanvasArea — the center panel: toolbar + zoomable/pannable 9:16 stage.
 * Also hosts the editor's ONE global keyboard listener (see onKeyDown):
 * - Space play/pause (space+drag or middle mouse to pan)
 * - Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y undo/redo
 * - arrows nudge selected element (shift = 10px), Delete removes
 *   non-caption elements, Escape clears selection
 * - ctrl/cmd + scroll to zoom; click empty stage → clear selection
 */
export const CanvasArea = ({ currentClip }) => {
  const stageRef = useRef(null);
  const viewportRef = useRef(null);
  const spaceHeld = useRef(false);
  const videoRef = useRef(null);
  const bgVideoRef = useRef(null); // blurred-fill clone, kept in sync below

  const elements = useAppStore((s) => s.elements);
  const selectedElementId = useAppStore((s) => s.selectedElementId);
  const clearSelection = useAppStore((s) => s.clearSelection);
  const removeElement = useAppStore((s) => s.removeElement);
  const updateElement = useAppStore((s) => s.updateElement);
  const zoom = useAppStore((s) => s.canvasZoom);
  const setCanvasZoom = useAppStore((s) => s.setCanvasZoom);
  const pan = useAppStore((s) => s.canvasPan);
  const setCanvasPan = useAppStore((s) => s.setCanvasPan);
  const currentTime = useAppStore((s) => s.currentTime);
  const isPlaying = useAppStore((s) => s.isPlaying);
  const fitRequestId = useAppStore((s) => s.fitRequestId);
  // Live canvas shape + fill (WYSIWYG for _apply_canvas in api/worker.py).
  const aspect = useAppStore((s) => s.exportSettings.format);
  const background = useAppStore((s) => s.exportSettings.background);
  const bgColor = useAppStore((s) => s.exportSettings.bgColor);
  const videoUrl = currentClip?.videoUrl || currentClip?.previewUrl || null;

  const stage = stageDimsForAspect(aspect);
  const render = RENDER_DIMS[aspect] || RENDER_DIMS["9:16"];
  // The burn blurs at boxblur=20 on the full-res canvas; scale that radius
  // down by the preview's px ratio so the preview blur reads the same.
  const previewBlurPx = Math.max((20 * stage.h) / render.h, 2);

  // ---------- Crop window over the 16:9 master (Sprint 4) ----------
  // The video IS the master (raw_path); the stage shows the per-aspect crop
  // window's region exactly as _prepare_source → _apply_canvas will burn it.
  // All math lives in lib/cropWindow.js — the parity-tested mirror of the
  // backend chain.
  const masterDims = useAppStore((s) => s.masterDims);
  const reframeMode = useAppStore((s) => s.reframeMode);
  const storedWindow = useAppStore((s) => s.exportSettings.cropWindows?.[s.exportSettings.format]);
  const defaultCropBox = currentClip?.defaultCropBox || null;
  const masterAR = inferMasterAspect(defaultCropBox, masterDims);
  const cropBox = storedWindow || initialWindowForAspect(aspect, masterAR, defaultCropBox);
  const fg = cropVideoLayout(cropBox, stage.w, stage.h, masterAR);
  const bgFill = cropFillLayout(cropBox, stage.w, stage.h);
  // Reframe mode shows the WHOLE master (contain-fitted) with the window
  // rect drawn over it, so there's context to drag within.
  const masterFit = containFit(masterAR, stage.w, stage.h);

  const onVideoMetadata = (e) => {
    const v = e.currentTarget;
    if (v.videoWidth > 0 && v.videoHeight > 0) {
      useAppStore.getState().setMasterDims({ w: v.videoWidth, h: v.videoHeight });
    }
  };

  // ---------- Real video sync ----------
  // The <video> drives store.currentTime while playing; store.isPlaying
  // drives play/pause; external seeks (timeline clicks) push back into the
  // video when they diverge by more than 300ms. The blurred background
  // clone (muted) follows the same signals so the fill stays in step.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const bg = bgVideoRef.current;
    if (isPlaying) {
      v.play().catch(() => useAppStore.getState().setPlaying(false));
      bg?.play().catch(() => {});
    } else {
      v.pause();
      bg?.pause();
    }
  }, [isPlaying, videoUrl, background]);

  useEffect(() => {
    const v = videoRef.current;
    if (v && Math.abs(v.currentTime - currentTime) > 0.3) {
      v.currentTime = currentTime;
    }
    const bg = bgVideoRef.current;
    if (bg && Math.abs(bg.currentTime - currentTime) > 0.3) {
      bg.currentTime = currentTime;
    }
  }, [currentTime]);

  // ---------- Keyboard ----------
  // THE editor's one global keydown listener. All shortcuts — Space
  // play/pause (+ space+drag pan while held), Escape, Delete, arrow nudges,
  // AND Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y undo/redo — dispatch through the
  // pure keymap (lib/editorKeymap.js) behind the one shared typing guard
  // (isTextEditingTarget). Do NOT add a second window keydown listener
  // elsewhere in the editor: two listeners with separate guards is exactly
  // how Space silently broke. Lives here (not Editor.jsx) because nudges
  // need the stage rect and panning needs spaceHeld.
  //
  // Known tradeoff: Space both toggles playback and arms space+drag
  // panning, so starting a space-pan also toggles play once. Play/pause is
  // the primary meaning (spec); middle-mouse drag pans without side effects.
  const onKeyDown = useCallback(
    (e) => {
      const decision = resolveEditorKey(e, isTextEditingTarget(document.activeElement));
      if (decision.preventDefault) e.preventDefault();

      switch (decision.action) {
        case "togglePlay":
          spaceHeld.current = true;
          useAppStore.getState().togglePlay();
          return;
        case "armPan": // held-Space auto-repeat: keep pan armed, no re-toggle
          spaceHeld.current = true;
          return;
        case "undo":
          useAppStore.getState().undo();
          return;
        case "redo":
          useAppStore.getState().redo();
          return;
        case "clearSelection":
          clearSelection();
          return;
        case "deleteSelected": {
          const s = useAppStore.getState();
          const sel = s.elements.find((el) => el.id === s.selectedElementId);
          if (sel && sel.type !== "caption") removeElement(sel.id);
          return;
        }
        case "nudge": {
          const s = useAppStore.getState();
          const sel = s.elements.find((el) => el.id === s.selectedElementId);
          if (!sel) return;
          const rect = stageRef.current?.getBoundingClientRect();
          if (!rect) return;
          const step = e.shiftKey ? NUDGE_BIG : NUDGE;
          const dx = step / rect.width;
          const dy = step / rect.height;

          let moved = false;
          let { x, y } = sel;
          if (e.key === "ArrowLeft") { x = clamp(x - dx, 0.02, 0.98); moved = true; }
          if (e.key === "ArrowRight") { x = clamp(x + dx, 0.02, 0.98); moved = true; }
          if (e.key === "ArrowUp") { y = clamp(y - dy, 0.02, 0.98); moved = true; }
          if (e.key === "ArrowDown") { y = clamp(y + dy, 0.02, 0.98); moved = true; }
          if (moved) {
            e.preventDefault();
            updateElement(sel.id, { x, y });
          }
          return;
        }
        default:
          return;
      }
    },
    [clearSelection, removeElement, updateElement]
  );

  const onKeyUp = useCallback((e) => {
    if (e.code === "Space") spaceHeld.current = false;
    // A held-arrow nudge burst coalesces into one undo frame; releasing the
    // key ends the "gesture" so the next burst starts a new frame.
    if (e.key?.startsWith("Arrow")) useAppStore.getState().endHistoryCoalescing();
  }, []);

  useEffect(() => {
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, [onKeyDown, onKeyUp]);

  // ---------- Fit-to-viewport zoom ----------
  // Default/initial state (not a one-time constant): recomputed on clip
  // load, on "Fit" clicks (fitRequestId, bumped from the toolbar), and on
  // window resize, since the right zoom depends on the actual viewport
  // size, not a fixed percentage. Manual +/- zoom keeps working between
  // these events.
  const applyFit = useCallback(() => {
    const z = computeFitZoom(viewportRef.current, stage.w, stage.h);
    setCanvasZoom(z);
    setCanvasPan({ x: 0, y: 0 });
  }, [setCanvasZoom, setCanvasPan, stage.w, stage.h]);

  // Refit on clip load, "Fit" clicks, AND aspect changes — a 16:9 stage in a
  // viewport fitted for 9:16 would spill outside it.
  useEffect(() => {
    applyFit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentClip?.id, fitRequestId, aspect]);

  useEffect(() => {
    let raf = null;
    const onResize = () => {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(applyFit);
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [applyFit]);

  // ---------- Zoom (ctrl/cmd + wheel) ----------
  useEffect(() => {
    const vp = viewportRef.current;
    if (!vp) return;
    const onWheel = (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      const s = useAppStore.getState();
      s.setCanvasZoom(s.canvasZoom - Math.sign(e.deltaY) * 0.08);
    };
    vp.addEventListener("wheel", onWheel, { passive: false });
    return () => vp.removeEventListener("wheel", onWheel);
  }, []);

  // ---------- Pan (space+drag or middle mouse) ----------
  const onViewportPointerDown = (e) => {
    const isPanGesture = spaceHeld.current || e.button === 1;
    if (!isPanGesture) return;
    e.preventDefault();
    const startX = e.clientX;
    const startY = e.clientY;
    const startPan = useAppStore.getState().canvasPan;
    const move = (ev) => {
      setCanvasPan({
        x: startPan.x + (ev.clientX - startX),
        y: startPan.y + (ev.clientY - startY),
      });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <div className="flex-1 flex min-h-0 relative">
      <CanvasToolbar />

      <div
        ref={viewportRef}
        data-testid={EDITOR.canvas}
        onPointerDown={onViewportPointerDown}
        onClick={() => clearSelection()}
        className="flex-1 min-h-0 flex items-center justify-center overflow-hidden relative"
        style={{
          background:
            "radial-gradient(ellipse 60% 50% at 50% 40%, rgba(233,168,37,0.07), transparent 70%), #060608",
        }}
      >
        {/* Zoom/pan wrapper */}
        <div
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transition: "transform 60ms linear",
          }}
        >
          {/* Stage — shape follows the chosen export aspect (WYSIWYG) */}
          <div
            ref={stageRef}
            data-testid={EDITOR.canvasStage}
            onClick={(e) => e.stopPropagation()}
            className="relative rounded-xl overflow-hidden shadow-[0_24px_80px_rgba(0,0,0,0.65),0_0_0_1px_rgba(233,168,37,0.22)]"
            style={{ width: stage.w, height: stage.h }}
          >
            {videoUrl ? (
              <>
              {/* Background fill — mirrors _apply_canvas exactly: 'blur' is
                  the CROPPED region stretched to the canvas (scale=w:h, no
                  aspect preserved) and blurred; the others are solid fills.
                  Only visible where the fitted video doesn't cover the
                  canvas. The clone plays the master, so an overflow-hidden
                  wrapper + offset sizing shows just the crop window. */}
              {background === "blur" ? (
                <div className="absolute inset-0 overflow-hidden">
                  <video
                    ref={bgVideoRef}
                    data-testid={EDITOR.canvasFillBlur}
                    src={videoUrl}
                    style={{
                      position: "absolute",
                      left: bgFill.left,
                      top: bgFill.top,
                      width: bgFill.width,
                      height: bgFill.height,
                      maxWidth: "none",
                      maxHeight: "none",
                      objectFit: "fill",
                      filter: `blur(${previewBlurPx}px)`,
                    }}
                    muted
                    playsInline
                    preload="auto"
                  />
                </div>
              ) : (
                <div
                  data-testid={EDITOR.canvasFillColor}
                  className="absolute inset-0"
                  style={{
                    background:
                      background === "white" ? "#ffffff"
                      : background === "color" ? bgColor
                      : "#000000",
                  }}
                />
              )}
              {/* The 16:9 master, viewed through the crop window. Normal
                  mode: an overflow-hidden viewport at the fitted position
                  shows exactly the window's region (≡ the burn). Reframe
                  mode: the whole master contain-fitted, with the draggable
                  window rect layered above (CropReframeOverlay). */}
              <div
                data-testid={EDITOR.cropViewport}
                className="absolute overflow-hidden"
                style={
                  reframeMode
                    ? { left: 0, top: 0, width: stage.w, height: stage.h }
                    : { left: fg.box.left, top: fg.box.top, width: fg.box.width, height: fg.box.height }
                }
              >
              <video
                ref={videoRef}
                src={videoUrl}
                data-testid={EDITOR.canvasVideo}
                style={
                  reframeMode
                    ? {
                        position: "absolute",
                        left: masterFit.left,
                        top: masterFit.top,
                        width: masterFit.width,
                        height: masterFit.height,
                        maxWidth: "none",
                        maxHeight: "none",
                        objectFit: "fill",
                      }
                    : {
                        position: "absolute",
                        left: fg.video.left,
                        top: fg.video.top,
                        width: fg.video.width,
                        height: fg.video.height,
                        maxWidth: "none",
                        maxHeight: "none",
                        objectFit: "fill",
                      }
                }
                playsInline
                preload="auto"
                onLoadedMetadata={onVideoMetadata}
                onTimeUpdate={(e) => {
                  const s = useAppStore.getState();
                  if (!s.isPlaying) return;
                  const t = e.currentTarget.currentTime;
                  // Trimmed clip: playback stops at the trim end and parks
                  // back on the trim start, mirroring what the export keeps.
                  const { start, end } = s.getTrimBounds();
                  if (end > 0 && end < s.duration && t >= end) {
                    s.setPlaying(false);
                    s.seek(start);
                    return;
                  }
                  s.seek(t);
                }}
                onEnded={() => {
                  const s = useAppStore.getState();
                  s.setPlaying(false);
                  s.seek(0);
                }}
                onError={() => {
                  // Non-fatal: keep the editor usable without the preview
                  console.warn("Clip video failed to load:", videoUrl);
                }}
              />
              </div>
              {reframeMode && (
                <CropReframeOverlay stageW={stage.w} stageH={stage.h} masterAR={masterAR} />
              )}
              </>
            ) : (
              <>
                {/* Mock video frame (no real clip loaded) */}
                <div
                  className="absolute inset-0"
                  style={{
                    background:
                      "linear-gradient(160deg, #17102b 0%, #0d0a18 45%, #1a0f2e 100%)",
                  }}
                />
                <div className="absolute inset-0 opacity-40"
                  style={{
                    background:
                      "radial-gradient(circle at 30% 30%, rgba(167,139,250,0.25), transparent 55%), radial-gradient(circle at 72% 68%, rgba(233,168,37,0.2), transparent 50%)",
                  }}
                />
                {/* Speaker silhouette placeholder */}
                <div className="absolute left-1/2 top-[38%] -translate-x-1/2 -translate-y-1/2 w-40 h-40 rounded-full bg-gradient-to-b from-k-raised to-k-surface border border-white/5 flex items-center justify-center">
                  <span className="text-5xl">🎙️</span>
                </div>
              </>
            )}
            {/* Virality badge */}
            {currentClip && (
              <div className="absolute top-3 right-3 z-30 px-2 py-1 rounded-md bg-black/55 backdrop-blur border border-k-ok/30 text-k-ok text-[11px] font-mono font-bold pointer-events-none">
                {currentClip.virality ?? currentClip.score ?? "—"} · VIRAL
              </div>
            )}
            {/* Timecode */}
            <div className="absolute bottom-2 left-3 z-30 text-[10px] font-mono text-white/45 pointer-events-none">
              {currentTime.toFixed(1)}s
            </div>

            {/* Elements */}
            {elements.map((el) => (
              <ElementRenderer key={el.id} element={el} canvasRef={stageRef} />
            ))}

            {/* Overlays */}
            <SmartGuides />
            <SafeZoneOverlay />
          </div>
        </div>

        {/* Selection hint */}
        {!selectedElementId && (
          <div className="absolute bottom-3 left-1/2 -translate-x-1/2 text-[11px] text-k-faint pointer-events-none">
            Click an element to select · drag to move · ⌘/Ctrl+scroll to zoom · Space+drag to pan
          </div>
        )}
      </div>
    </div>
  );
};

export default CanvasArea;
