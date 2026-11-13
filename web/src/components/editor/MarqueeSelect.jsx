import React, { useEffect, useRef, useState } from "react";
import { useAppStore } from "@/store/useAppStore";

/**
 * Feature #9 — marquee (rubber-band) selection.
 *
 * Window-level pointer listeners (CanvasArea is frozen, so this cannot live
 * in the stage's own handlers). A marquee starts ONLY when:
 *   - left button, no Shift/Ctrl/Alt,
 *   - Space is not held (space+drag is CanvasArea's pan gesture — tracked
 *     here independently, same key events),
 *   - the pointerdown landed on the STAGE BACKGROUND: inside
 *     [data-testid="editor-canvas-stage"] but NOT inside any canvas element
 *     ([data-testid^="canvas-el-"]) — element drags stay ElementRenderer's.
 * The band only materializes after a 4px threshold, so plain clicks keep
 * their existing meaning. Selection = every visible element whose on-screen
 * rect intersects the band (client coords on both sides — zoom-safe).
 * A capture-phase click swallows the one click that follows a real marquee,
 * so CanvasArea's click-to-clear cannot wipe the fresh selection when the
 * drag ends outside the stage.
 */
const THRESHOLD_PX = 4;

export const MarqueeSelect = () => {
  const [band, setBand] = useState(null); // {left, top, width, height} client px
  const drag = useRef(null);
  const spaceHeld = useRef(false);

  useEffect(() => {
    const onKey = (e) => {
      if (e.code === "Space") spaceHeld.current = e.type === "keydown";
    };

    const onPointerDown = (e) => {
      if (e.button !== 0 || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
      if (spaceHeld.current) return;
      const stage = document.querySelector('[data-testid="editor-canvas-stage"]');
      if (!stage || !stage.contains(e.target)) return;
      if (e.target.closest?.('[data-testid^="canvas-el-"]')) return;
      drag.current = { x0: e.clientX, y0: e.clientY, active: false };
    };

    const onPointerMove = (e) => {
      const d = drag.current;
      if (!d) return;
      const dx = e.clientX - d.x0;
      const dy = e.clientY - d.y0;
      if (!d.active && Math.hypot(dx, dy) < THRESHOLD_PX) return;
      d.active = true;
      setBand({
        left: Math.min(d.x0, e.clientX),
        top: Math.min(d.y0, e.clientY),
        width: Math.abs(dx),
        height: Math.abs(dy),
      });
    };

    const onPointerUp = (e) => {
      const d = drag.current;
      drag.current = null;
      if (!d?.active) return;
      setBand(null);

      const r = {
        left: Math.min(d.x0, e.clientX),
        top: Math.min(d.y0, e.clientY),
        right: Math.max(d.x0, e.clientX),
        bottom: Math.max(d.y0, e.clientY),
      };
      const hits = [];
      for (const node of document.querySelectorAll('[data-testid^="canvas-el-"]')) {
        const b = node.getBoundingClientRect();
        const intersects =
          b.left < r.right && b.right > r.left && b.top < r.bottom && b.bottom > r.top;
        if (intersects) {
          hits.push(node.getAttribute("data-testid").replace("canvas-el-", ""));
        }
      }
      useAppStore.getState().setSelection(hits);

      // Swallow exactly the click this drag generates — if the pointer ended
      // outside the stage, that click would hit CanvasArea's viewport
      // clear-selection handler and wipe what we just selected.
      const swallow = (ce) => {
        ce.stopPropagation();
        window.removeEventListener("click", swallow, true);
      };
      window.addEventListener("click", swallow, true);
      // Defensive: if no click follows (edge cases), drop the trap.
      setTimeout(() => window.removeEventListener("click", swallow, true), 120);
    };

    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKey);
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKey);
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };
  }, []);

  if (!band) return null;
  return (
    <div
      data-testid="editor-marquee-band"
      className="fixed z-[90] pointer-events-none border border-k-accent bg-k-accent/10 rounded-sm"
      style={band}
    />
  );
};

export default MarqueeSelect;
