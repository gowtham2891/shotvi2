import React, { useRef } from "react";
import { useAppStore, clamp } from "@/store/useAppStore";
import { EDITOR } from "@/constants/testIds";

/**
 * TransformBox — selection frame + corner scale handles + rotate handle.
 * Rendered as a child of the rotated element wrapper, so it inherits rotation.
 * Uses uniform scale-from-center for corner handles (invariant to rotation).
 *
 * BUG-005 containment: the progress element's rotation is computed in the
 * layout math (services/overlay_renderer.py :: _prepare_progress_layer) but
 * NOT applied in the composite filtergraph on export. Until that is wired,
 * keep progress rotation at 0 in the editor so preview and export cannot
 * silently disagree. This is UI containment only — no export-side change.
 */
const ROTATION_DISABLED_TYPES = new Set(["progress"]);
const isRotationDisabled = (element) =>
  ROTATION_DISABLED_TYPES.has(element?.type);

export const TransformBox = ({ element, canvasRect, elementRect }) => {
  const updateElement = useAppStore((s) => s.updateElement);
  const dragRef = useRef(null);

  const beginResize = (e, corner) => {
    e.stopPropagation();
    e.preventDefault();
    if (!elementRect) return;
    const cx = elementRect.left + elementRect.width / 2;
    const cy = elementRect.top + elementRect.height / 2;
    const startDist = Math.hypot(e.clientX - cx, e.clientY - cy);
    const startScale = element.scale;

    const move = (ev) => {
      const d = Math.hypot(ev.clientX - cx, ev.clientY - cy);
      const ratio = d / Math.max(startDist, 1);
      const newScale = clamp(startScale * ratio, 0.2, 4);
      updateElement(element.id, { scale: newScale });
    };
    const up = () => {
      // One undo frame per resize gesture — close it on release.
      useAppStore.getState().endHistoryCoalescing();
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const beginRotate = (e) => {
    e.stopPropagation();
    e.preventDefault();
    // BUG-005 containment — rotation not supported on progress (export can't
    // render it yet). Ignore the drag; the handle is hidden below too.
    if (isRotationDisabled(element)) return;
    if (!elementRect) return;
    const cx = elementRect.left + elementRect.width / 2;
    const cy = elementRect.top + elementRect.height / 2;
    const startAngle = Math.atan2(e.clientY - cy, e.clientX - cx);
    const startRot = element.rotation;

    const move = (ev) => {
      const a = Math.atan2(ev.clientY - cy, ev.clientX - cx);
      let deg = startRot + ((a - startAngle) * 180) / Math.PI;
      // Snap to 15° increments when shift held
      if (ev.shiftKey) deg = Math.round(deg / 15) * 15;
      // Normalize -180..180
      deg = ((deg + 180) % 360) - 180;
      updateElement(element.id, { rotation: deg });
    };
    const up = () => {
      // One undo frame per rotate gesture — close it on release.
      useAppStore.getState().endHistoryCoalescing();
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const handleClass =
    "absolute w-3 h-3 bg-white border-2 border-k-accent rounded-sm shadow-[0_0_10px_rgba(233,168,37,0.6)] pointer-events-auto cursor-nwse-resize";

  return (
    <div
      data-testid={EDITOR.transformBox}
      className="absolute inset-[-6px] pointer-events-none z-[70]"
      ref={dragRef}
    >
      {/* Outline */}
      <div
        className="absolute inset-0 border border-k-accent rounded-[3px]"
        style={{ boxShadow: "0 0 0 1px rgba(233,168,37,0.35) inset" }}
      />

      {/* Rotation handle line — hidden for elements whose rotation the
          export path cannot render (BUG-005 containment). */}
      {!isRotationDisabled(element) && (
        <>
          <div className="absolute left-1/2 -top-6 w-px h-6 bg-k-accent" />
          {/* Rotate handle */}
          <div
            data-testid={EDITOR.transformHandle("rot")}
            onPointerDown={beginRotate}
            className="absolute left-1/2 -translate-x-1/2 -top-9 w-5 h-5 rounded-full bg-k-accent border-2 border-white shadow-[0_0_12px_rgba(233,168,37,0.8)] pointer-events-auto cursor-grab flex items-center justify-center"
            style={{ cursor: "grab" }}
          >
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none">
              <path
                d="M4 4v6h6M20 20v-6h-6M20 10a8 8 0 0 0-14.9-3M4 14a8 8 0 0 0 14.9 3"
                stroke="white"
                strokeWidth="3"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
        </>
      )}

      {/* Corner handles — uniform scale-from-center */}
      <div
        data-testid={EDITOR.transformHandle("tl")}
        onPointerDown={(e) => beginResize(e, "tl")}
        className={`${handleClass} -top-1.5 -left-1.5`}
        style={{ cursor: "nwse-resize" }}
      />
      <div
        data-testid={EDITOR.transformHandle("tr")}
        onPointerDown={(e) => beginResize(e, "tr")}
        className={`${handleClass} -top-1.5 -right-1.5`}
        style={{ cursor: "nesw-resize" }}
      />
      <div
        data-testid={EDITOR.transformHandle("bl")}
        onPointerDown={(e) => beginResize(e, "bl")}
        className={`${handleClass} -bottom-1.5 -left-1.5`}
        style={{ cursor: "nesw-resize" }}
      />
      <div
        data-testid={EDITOR.transformHandle("br")}
        onPointerDown={(e) => beginResize(e, "br")}
        className={`${handleClass} -bottom-1.5 -right-1.5`}
        style={{ cursor: "nwse-resize" }}
      />

      {/* Live info badge */}
      <LiveBadge element={element} />
    </div>
  );
};

const LiveBadge = ({ element }) => (
  <div className="absolute -bottom-8 left-1/2 -translate-x-1/2 px-2 py-1 rounded bg-k-accent text-k-ink text-[10px] font-mono whitespace-nowrap shadow-[0_4px_10px_rgba(233,168,37,0.5)] pointer-events-none">
    {Math.round(element.scale * 100)}% · {Math.round(element.rotation)}°
  </div>
);

export default TransformBox;
