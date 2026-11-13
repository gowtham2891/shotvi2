import React, { useRef, useCallback } from "react";
import { useAppStore } from "@/store/useAppStore";
import {
  containFit,
  cropRatioK,
  moveCropBox,
  resizeCropBox,
} from "@/lib/cropWindow";
import { EDITOR } from "@/constants/testIds";

/**
 * CropReframeOverlay — Sprint 4's drag-to-reframe surface.
 *
 * Rendered on top of the stage while reframe mode is on. The stage behind it
 * shows the WHOLE 16:9 master contain-fitted; this overlay draws the
 * per-aspect crop window as a movable/resizable rect over that fitted master
 * (dimming everything outside it), in the same fractional space the backend
 * crops with. Drag inside the rect to move; corner handles resize with the
 * window's aspect locked to the output aspect (lib/cropWindow.resizeCropBox).
 *
 * All writes go through setCropWindow (clamped, undo-coalesced per gesture —
 * pointerup calls endHistoryCoalescing so one drag = one undo frame).
 */
export const CropReframeOverlay = ({ stageW, stageH, masterAR }) => {
  const rootRef = useRef(null);
  const aspect = useAppStore((s) => s.exportSettings.format);
  // Subscribe to the stored window so drags re-render; fall back to the
  // derived default through the store selector.
  useAppStore((s) => s.exportSettings.cropWindows?.[s.exportSettings.format]);
  const box = useAppStore.getState().getEffectiveCropWindow(aspect);
  const touched = useAppStore((s) => s.isCropTouched(s.exportSettings.format));

  const mFit = containFit(masterAR, stageW, stageH);
  const rect = {
    left: mFit.left + box.x * mFit.width,
    top: mFit.top + box.y * mFit.height,
    width: box.w * mFit.width,
    height: box.h * mFit.height,
  };

  // Pointer px → master-fraction deltas. The overlay lives inside the zoomed
  // stage, so its own bounding rect already carries the zoom scale.
  const fracDeltas = useCallback(
    (e, start) => {
      const el = rootRef.current;
      if (!el) return { dx: 0, dy: 0 };
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height || !mFit.width || !mFit.height) return { dx: 0, dy: 0 };
      const scaleX = r.width / stageW;
      const scaleY = r.height / stageH;
      return {
        dx: (e.clientX - start.x) / scaleX / mFit.width,
        dy: (e.clientY - start.y) / scaleY / mFit.height,
      };
    },
    [stageW, stageH, mFit.width, mFit.height]
  );

  const startGesture = (e, apply) => {
    e.preventDefault();
    e.stopPropagation();
    const start = { x: e.clientX, y: e.clientY };
    const startBox = { ...useAppStore.getState().getEffectiveCropWindow(aspect) };
    const move = (ev) => {
      const { dx, dy } = fracDeltas(ev, start);
      useAppStore.getState().setCropWindow(aspect, apply(startBox, dx, dy));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      useAppStore.getState().endHistoryCoalescing();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const onMoveStart = (e) =>
    startGesture(e, (b, dx, dy) => moveCropBox(b, dx, dy));

  const onResizeStart = (corner) => (e) =>
    startGesture(e, (b, dx, dy) =>
      resizeCropBox(b, corner, dx, dy, cropRatioK(aspect, masterAR))
    );

  const HANDLES = [
    { corner: "tl", style: { left: -5, top: -5, cursor: "nwse-resize" } },
    { corner: "tr", style: { right: -5, top: -5, cursor: "nesw-resize" } },
    { corner: "bl", style: { left: -5, bottom: -5, cursor: "nesw-resize" } },
    { corner: "br", style: { right: -5, bottom: -5, cursor: "nwse-resize" } },
  ];

  return (
    <div
      ref={rootRef}
      className="absolute inset-0 z-40"
      onClick={(e) => e.stopPropagation()}
    >
      {/* Dim everything outside the window (4 shade panels around the rect) */}
      {[
        { left: 0, top: 0, width: stageW, height: rect.top },
        { left: 0, top: rect.top + rect.height, width: stageW, height: Math.max(0, stageH - rect.top - rect.height) },
        { left: 0, top: rect.top, width: rect.left, height: rect.height },
        { left: rect.left + rect.width, top: rect.top, width: Math.max(0, stageW - rect.left - rect.width), height: rect.height },
      ].map((s, i) => (
        <div key={i} className="absolute bg-black/60 pointer-events-none" style={s} />
      ))}

      {/* The crop window rect — drag to move */}
      <div
        data-testid={EDITOR.cropRect}
        onPointerDown={onMoveStart}
        className="absolute border-2 border-k-accent shadow-[0_0_0_1px_rgba(255,255,255,0.35)] cursor-move"
        style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }}
      >
        {/* Rule-of-thirds guides */}
        <div className="absolute inset-y-0 left-1/3 w-px bg-white/25 pointer-events-none" />
        <div className="absolute inset-y-0 left-2/3 w-px bg-white/25 pointer-events-none" />
        <div className="absolute inset-x-0 top-1/3 h-px bg-white/25 pointer-events-none" />
        <div className="absolute inset-x-0 top-2/3 h-px bg-white/25 pointer-events-none" />
        {HANDLES.map((h) => (
          <div
            key={h.corner}
            data-testid={EDITOR.cropHandle(h.corner)}
            onPointerDown={onResizeStart(h.corner)}
            className="absolute w-2.5 h-2.5 rounded-sm bg-white border border-k-accent"
            style={h.style}
          />
        ))}
      </div>

      {/* Action bar */}
      <div className="absolute top-2 left-1/2 -translate-x-1/2 flex items-center gap-1.5 px-2 py-1 rounded-lg bg-black/70 backdrop-blur border border-k-line">
        <span className="text-[10px] font-mono text-k-accentsoft pr-1">
          Reframe {aspect}
        </span>
        <button
          data-testid={EDITOR.reframeReset}
          disabled={!touched}
          onClick={() => useAppStore.getState().resetCropWindow(aspect)}
          className="px-2 py-0.5 rounded text-[10px] text-k-soft hover:text-white hover:bg-white/10 disabled:opacity-40 disabled:hover:bg-transparent transition-colors"
        >
          Reset framing
        </button>
        <button
          data-testid={EDITOR.reframeDone}
          onClick={() => useAppStore.getState().setReframeMode(false)}
          className="px-2 py-0.5 rounded text-[10px] bg-k-accent text-k-ink hover:bg-k-accent2 transition-colors"
        >
          Done
        </button>
      </div>
    </div>
  );
};

export default CropReframeOverlay;
