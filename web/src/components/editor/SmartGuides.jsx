import React from "react";
import { useAppStore } from "@/store/useAppStore";

/**
 * SmartGuides — violet alignment lines rendered during drag.
 * Reads activeGuides from store. Positions are normalized 0–1 relative to canvas.
 */
export const SmartGuides = () => {
  const guides = useAppStore((s) => s.activeGuides);

  if (!guides || (guides.vertical.length === 0 && guides.horizontal.length === 0)) {
    return null;
  }

  return (
    <div className="absolute inset-0 pointer-events-none z-[80]">
      {guides.vertical.map((v, i) => (
        <div
          key={`v-${i}-${v}`}
          className="absolute top-0 bottom-0 w-px"
          style={{
            left: `${v * 100}%`,
            background:
              "linear-gradient(to bottom, transparent 0%, #E9A825 5%, #E9A825 95%, transparent 100%)",
            boxShadow: "0 0 8px rgba(233,168,37,0.85)",
          }}
        />
      ))}
      {guides.horizontal.map((h, i) => (
        <div
          key={`h-${i}-${h}`}
          className="absolute left-0 right-0 h-px"
          style={{
            top: `${h * 100}%`,
            background:
              "linear-gradient(to right, transparent 0%, #E9A825 5%, #E9A825 95%, transparent 100%)",
            boxShadow: "0 0 8px rgba(233,168,37,0.85)",
          }}
        />
      ))}
    </div>
  );
};

export default SmartGuides;
