import React from "react";
import { useAppStore } from "@/store/useAppStore";
import { ElementBody } from "@/components/editor/ElementBodies";
import { PreviewWatermark } from "@/components/editor/PreviewWatermark";

/**
 * Read-only positioned-elements overlay — same positioning math as
 * ElementRenderer (normalized x/y -> % position, centered, rotate+scale),
 * without drag/select/TransformBox. Used by any non-interactive canvas
 * preview; currently the Export page's live draft preview, so the same
 * caption/headline/progress/logo rendering the editor canvas shows
 * appears there too, reusing ElementBody rather than duplicating it.
 *
 * The free-tier watermark rides along for the same reason, but it is NOT one
 * of `elements` and never will be — see PreviewWatermark.jsx.
 */
export const StaticElementLayer = ({ canvasH }) => {
  const elements = useAppStore((s) => s.elements);
  const visible = elements.filter((el) => el.visible);

  return (
    <>
      <PreviewWatermark canvasH={canvasH} />
      {visible.map((el) => (
        <div
          key={el.id}
          className="absolute select-none pointer-events-none"
          style={{
            left: `${el.x * 100}%`,
            top: `${el.y * 100}%`,
            transform: `translate(-50%, -50%) rotate(${el.rotation}deg) scale(${el.scale})`,
            zIndex: 20 + elements.findIndex((e) => e.id === el.id),
          }}
        >
          <ElementBody element={el} canvasH={canvasH} />
        </div>
      ))}
    </>
  );
};

export default StaticElementLayer;
