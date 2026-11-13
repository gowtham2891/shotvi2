import React, { useRef, useState } from "react";
import { useAppStore, clamp } from "@/store/useAppStore";
import { ElementBody } from "@/components/editor/ElementBodies";
import { TransformBox } from "@/components/editor/TransformBox";
import { EDITOR } from "@/constants/testIds";
import { clampToFrame } from "@/lib/clampToFrame";

const SNAP_PX = 6;

/**
 * ElementRenderer — positions + renders a single canvas element.
 * - Normalized coords (x/y ∈ 0–1) → % positioning, centered on the point.
 * - Drag with smart-guide snapping (canvas center, other elements,
 *   safe-zone boundaries when a safe-zone mode is active).
 * - Renders TransformBox when selected.
 * Content rendering itself lives in ElementBodies.jsx, shared with any
 * read-only preview (e.g. the Export page's live draft preview).
 */
export const ElementRenderer = ({ element, canvasRef }) => {
  const selectedId = useAppStore((s) => s.selectedElementId);
  const selectedIds = useAppStore((s) => s.selectedIds);
  const setSelected = useAppStore((s) => s.setSelected);
  const updateElement = useAppStore((s) => s.updateElement);
  const setActiveGuides = useAppStore((s) => s.setActiveGuides);
  const safeZoneMode = useAppStore((s) => s.safeZoneMode);
  const elements = useAppStore((s) => s.elements);

  const wrapRef = useRef(null);
  const [rects, setRects] = useState({ canvas: null, el: null });

  // Feature #9: primary drives TransformBox/Inspector; any other selected
  // member renders a dashed ring. Both derive from the same selection state.
  const isPrimary = selectedId === element.id;
  const isSelected = selectedIds.includes(element.id);
  if (!element.visible) return null;

  // ---------- Drag with snapping ----------
  const onPointerDown = (e) => {
    if (element.locked) return;
    e.stopPropagation();

    // Feature #9: shift-click is a pure selection gesture — toggle
    // membership, never start a drag.
    if (e.shiftKey) {
      useAppStore.getState().toggleInSelection(element.id);
      return;
    }
    // Clicking a member of a multi-selection KEEPS the group (so it can be
    // group-dragged); clicking anything else selects it solo.
    if (!useAppStore.getState().selectedIds.includes(element.id)) {
      setSelected(element.id);
    } else {
      useAppStore.setState({ selectedElementId: element.id });
    }

    const canvasRect = canvasRef.current?.getBoundingClientRect();
    if (!canvasRect) return;

    const startX = e.clientX;
    const startY = e.clientY;
    const startNX = element.x;
    const startNY = element.y;

    // Group-drag snapshot: start positions of every selected element (this
    // one included). Deltas apply to these absolutes — no clamp drift.
    const groupIds = useAppStore.getState().selectedIds;
    const groupStart = {};
    if (groupIds.length > 1) {
      for (const el of useAppStore.getState().elements) {
        if (groupIds.includes(el.id)) groupStart[el.id] = { x: el.x, y: el.y };
      }
    }

    // Snap targets (normalized)
    const others = elements.filter(
      (el) => el.id !== element.id && el.visible
    );
    const snapX = [0.5, ...others.map((el) => el.x)];
    const snapY = [0.5, ...others.map((el) => el.y)];
    if (safeZoneMode !== "off") {
      snapY.push(0.1, 0.8); // safe-zone boundaries — natural resting points
    }

    const snapTolX = SNAP_PX / canvasRect.width;
    const snapTolY = SNAP_PX / canvasRect.height;

    // Commit 5: caption drag stays fully inside the frame.
    // Measure the caption's own rendered bounding box now (before the drag
    // starts) — Telugu wraps at wildly different widths depending on
    // conjunct density, so a hardcoded margin does not work. Non-caption
    // elements keep the historical 2%–98% center clamp.
    const elRect = wrapRef.current?.getBoundingClientRect();
    const isCaption = element.type === "caption";
    const elW = elRect?.width || 0;
    const elH = elRect?.height || 0;

    const move = (ev) => {
      const rawX = startNX + (ev.clientX - startX) / canvasRect.width;
      const rawY = startNY + (ev.clientY - startY) / canvasRect.height;
      let nx, ny;
      if (isCaption) {
        const clamped = clampToFrame(rawX, rawY, elW, elH, canvasRect.width, canvasRect.height);
        nx = clamped.x;
        ny = clamped.y;
      } else {
        nx = clamp(rawX, 0.02, 0.98);
        ny = clamp(rawY, 0.02, 0.98);
      }

      const guides = { vertical: [], horizontal: [] };
      for (const t of snapX) {
        if (Math.abs(nx - t) < snapTolX) {
          nx = t;
          guides.vertical.push(t);
          break;
        }
      }
      for (const t of snapY) {
        if (Math.abs(ny - t) < snapTolY) {
          ny = t;
          guides.horizontal.push(t);
          break;
        }
      }
      setActiveGuides(guides);
      if (Object.keys(groupStart).length > 1) {
        // Feature #9: group drag — the grabbed element lands on its snapped
        // (nx, ny); every other member moves by the same delta.
        const ddx = nx - startNX;
        const ddy = ny - startNY;
        const positions = {};
        for (const [id, p] of Object.entries(groupStart)) {
          positions[id] = id === element.id ? { x: nx, y: ny } : { x: p.x + ddx, y: p.y + ddy };
        }
        useAppStore.getState().moveElementsTo(positions);
      } else {
        updateElement(element.id, { x: nx, y: ny });
      }
    };
    const up = () => {
      setActiveGuides({ vertical: [], horizontal: [] });
      // Gesture over: the whole drag was ONE undo frame; the next drag of
      // this element must start a new one.
      useAppStore.getState().endHistoryCoalescing();
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);

    // Measure rects for TransformBox
    requestAnimationFrame(() => {
      setRects({
        canvas: canvasRect,
        el: wrapRef.current?.getBoundingClientRect() || null,
      });
    });
  };

  const onClick = (e) => {
    e.stopPropagation();
    // Feature #9: selection is decided entirely in onPointerDown (shift-click
    // toggles; a plain click on a group member keeps the group so it can be
    // group-dragged). Re-running setSelected here would clobber that — the
    // click only needs to (re)measure rects for the TransformBox.
    const canvasRect = canvasRef.current?.getBoundingClientRect();
    setRects({
      canvas: canvasRect || null,
      el: wrapRef.current?.getBoundingClientRect() || null,
    });
  };

  // Zoom² bug fix: element bodies size fonts as fraction-of-canvas-height in
  // LAYOUT px, and the whole stage then scales visually by the zoom wrapper's
  // scale(zoom). getBoundingClientRect() returns the VISUAL (post-transform)
  // height — stage.h × zoom — so fonts rendered at frac × visualH × zoom grew
  // by zoom². offsetHeight is transform-immune: always the stage's layout
  // height (STAGE_DIMS[aspect].h), zoom scales everything exactly once.
  const canvasH = canvasRef.current?.offsetHeight || 640;

  return (
    <div
      ref={wrapRef}
      data-testid={EDITOR.canvasElement ? EDITOR.canvasElement(element.id) : `canvas-el-${element.id}`}
      onPointerDown={onPointerDown}
      onClick={onClick}
      className="absolute select-none"
      style={{
        left: `${element.x * 100}%`,
        top: `${element.y * 100}%`,
        transform: `translate(-50%, -50%) rotate(${element.rotation}deg) scale(${element.scale})`,
        cursor: element.locked ? "default" : "grab",
        zIndex: 20 + elements.findIndex((el) => el.id === element.id),
        // Feature #9: non-primary members of a multi-selection get a dashed
        // ring; the primary keeps the full TransformBox below.
        outline:
          isSelected && !isPrimary ? "1.5px dashed rgba(233,168,37,0.9)" : undefined,
        outlineOffset: isSelected && !isPrimary ? 2 : undefined,
      }}
    >
      <ElementBody element={element} canvasH={canvasH} />
      {isPrimary && !element.locked && (
        <TransformBox
          element={element}
          canvasRect={rects.canvas}
          elementRect={rects.el}
        />
      )}
    </div>
  );
};

export default ElementRenderer;
