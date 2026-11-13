import React from "react";
import { useAppStore } from "@/store/useAppStore";

// Renders styled captions on the 9:16 canvas — supports word-by-word highlight
export const CaptionRenderer = () => {
  const {
    transcript,
    currentTime,
    captionFont,
    captionSize,
    captionPosition,
    wordHighlight,
    getPresetClass,
    captionAnimation,
  } = useAppStore();

  // Group current sentence — show ~5 words around active word
  const activeIdx = transcript.findIndex(
    (w) => currentTime >= w.start && currentTime < w.end
  );
  const idx = activeIdx === -1 ? 0 : activeIdx;

  const windowStart = Math.max(0, idx - 2);
  const windowEnd = Math.min(transcript.length, idx + 4);
  const visible = transcript.slice(windowStart, windowEnd);

  const presetClass = getPresetClass();

  const posClass =
    captionPosition === "top"
      ? "top-[8%]"
      : captionPosition === "middle"
        ? "top-1/2 -translate-y-1/2"
        : "bottom-[12%]";

  const animClass =
    captionAnimation === "pop"
      ? "transition-transform duration-150"
      : captionAnimation === "fade"
        ? "transition-opacity duration-200"
        : "";

  return (
    <div
      className={`absolute left-0 right-0 ${posClass} px-6 flex justify-center pointer-events-none`}
    >
      <div
        className="text-center max-w-[92%] flex flex-wrap justify-center gap-x-2 gap-y-1 leading-[1.1]"
        style={{ fontSize: captionSize, fontFamily: captionFont }}
      >
        {visible.map((word, i) => {
          const globalIdx = windowStart + i;
          const isActive = globalIdx === idx;
          return (
            <span
              key={globalIdx}
              className={`${presetClass} ${animClass} ${
                isActive && wordHighlight ? "caption-word-highlight" : ""
              }`}
              style={{
                transform: isActive ? "scale(1.06)" : "scale(1)",
                opacity: isActive ? 1 : 0.85,
                display: "inline-block",
              }}
            >
              {word.text}
            </span>
          );
        })}
      </div>
    </div>
  );
};

export default CaptionRenderer;
