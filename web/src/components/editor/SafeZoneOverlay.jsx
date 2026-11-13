import React from "react";
import { Heart, MessageCircle, Send, Bookmark, MoreHorizontal, Music, ThumbsUp, ThumbsDown, Share2 } from "lucide-react";
import { useAppStore } from "@/store/useAppStore";
import { PreviewWatermark } from "@/components/editor/PreviewWatermark";

/**
 * SafeZoneOverlay — renders Instagram Reels or YouTube Shorts UI ghost shapes.
 * When a selected element is inside an unsafe zone, that zone tints red.
 *
 * Also the stage's ALWAYS-MOUNTED chrome host. CanvasArea.jsx is frozen
 * (Sprint 4), and this is the only unconditional child of the stage that can
 * still be edited — so non-element, frame-anchored canvas chrome mounts from
 * here rather than from CanvasArea. Currently that means PreviewWatermark,
 * which must render regardless of safeZoneMode; the safe-zone ghosts keep
 * their own `mode === "off"` guard below.
 */
export const SafeZoneOverlay = () => {
  const mode = useAppStore((s) => s.safeZoneMode);
  const selectedId = useAppStore((s) => s.selectedElementId);
  const selected = useAppStore((s) =>
    s.elements.find((el) => el.id === selectedId)
  );

  if (mode === "off") return <PreviewWatermark />;

  // Zones (normalized): [top, bottom] fractions of canvas height
  const topZone = [0, 0.1]; // header area (~10%)
  const bottomZone = [0.8, 1.0]; // captions + actions (~20%)

  const selInTop =
    selected && selected.y > topZone[0] && selected.y < topZone[1];
  const selInBottom =
    selected && selected.y > bottomZone[0] && selected.y < bottomZone[1];

  return (
    <>
      {/* Outside the pointer-events-none wrapper below: the watermark is a
          click target (it is the upgrade CTA), unlike the ghost chrome. */}
      <PreviewWatermark />
      <div className="absolute inset-0 pointer-events-none z-[60] overflow-hidden">
        {mode === "instagram" && (
          <InstagramGhost unsafeTop={selInTop} unsafeBottom={selInBottom} />
        )}
        {mode === "youtube" && (
          <YoutubeGhost unsafeTop={selInTop} unsafeBottom={selInBottom} />
        )}

        {/* Legend badge */}
        <div className="absolute top-3 left-1/2 -translate-x-1/2 px-3 py-1 rounded-full bg-black/70 backdrop-blur-md border border-white/10 text-[10px] uppercase tracking-widest text-white/80 flex items-center gap-2">
          <span
            className="w-1.5 h-1.5 rounded-full"
            style={{ background: mode === "instagram" ? "#e1306c" : "#ff0000" }}
          />
          {mode === "instagram" ? "Instagram Reels" : "YouTube Shorts"} · Safe zone
        </div>
      </div>
    </>
  );
};

// ---------------------------------------------------------------
// Instagram Reels ghost UI
// ---------------------------------------------------------------
const InstagramGhost = ({ unsafeTop, unsafeBottom }) => (
  <>
    {/* Top: back arrow + reels title + camera */}
    <div
      className={`absolute inset-x-0 top-0 h-[10%] flex items-center justify-between px-4 border-b border-dashed transition-colors ${
        unsafeTop
          ? "bg-red-500/15 border-red-500/60"
          : "bg-black/25 border-white/12"
      }`}
    >
      <div className="w-4 h-4 rounded border border-white/40" />
      <div className="text-white/60 text-xs font-semibold">Reels</div>
      <div className="w-4 h-4 rounded-full border border-white/40" />
    </div>

    {/* Bottom-left: username + caption + music */}
    <div
      className={`absolute left-0 right-14 bottom-0 h-[20%] px-4 pb-4 pt-3 flex flex-col justify-end gap-2 border-t border-dashed transition-colors ${
        unsafeBottom
          ? "bg-red-500/15 border-red-500/60"
          : "bg-gradient-to-t from-black/60 to-transparent border-white/12"
      }`}
    >
      <div className="flex items-center gap-2">
        <div className="w-6 h-6 rounded-full border border-white/50 bg-white/10" />
        <div className="h-2 w-16 rounded-full bg-white/25" />
        <div className="h-4 w-12 rounded-md border border-white/40 text-[8px] flex items-center justify-center text-white/60">
          Follow
        </div>
      </div>
      <div className="h-2 w-3/4 rounded-full bg-white/15" />
      <div className="flex items-center gap-1.5 text-white/50">
        <Music size={9} />
        <div className="h-1.5 w-24 rounded-full bg-white/15" />
      </div>
    </div>

    {/* Right action column */}
    <div
      className={`absolute right-2 bottom-[10%] flex flex-col items-center gap-4 transition-colors ${
        unsafeBottom ? "opacity-100" : "opacity-70"
      }`}
    >
      {[Heart, MessageCircle, Send, Bookmark, MoreHorizontal].map((Icon, i) => (
        <div key={i} className="flex flex-col items-center gap-0.5">
          <div
            className={`w-9 h-9 rounded-full border flex items-center justify-center backdrop-blur-sm ${
              unsafeBottom
                ? "bg-red-500/20 border-red-500/60"
                : "bg-black/40 border-white/25"
            }`}
          >
            <Icon size={16} className="text-white/70" />
          </div>
          {i < 3 && (
            <div className="h-1 w-5 rounded-full bg-white/25" />
          )}
        </div>
      ))}
      {/* Rotating record indicator */}
      <div className="w-8 h-8 rounded-md border border-white/25 bg-black/40 backdrop-blur-sm animate-[spin_6s_linear_infinite] flex items-center justify-center">
        <Music size={12} className="text-white/70" />
      </div>
    </div>
  </>
);

// ---------------------------------------------------------------
// YouTube Shorts ghost UI
// ---------------------------------------------------------------
const YoutubeGhost = ({ unsafeTop, unsafeBottom }) => (
  <>
    {/* Top: search + more */}
    <div
      className={`absolute inset-x-0 top-0 h-[10%] flex items-center justify-between px-4 border-b border-dashed transition-colors ${
        unsafeTop
          ? "bg-red-500/15 border-red-500/60"
          : "bg-black/25 border-white/12"
      }`}
    >
      <div className="text-white/60 text-xs font-semibold">Shorts</div>
      <div className="flex items-center gap-3">
        <div className="w-4 h-4 rounded-full border border-white/40" />
        <div className="w-4 h-4 border border-white/40" />
      </div>
    </div>

    {/* Bottom-left: channel + description */}
    <div
      className={`absolute left-0 right-14 bottom-0 h-[20%] px-4 pb-4 pt-3 flex flex-col justify-end gap-2 border-t border-dashed transition-colors ${
        unsafeBottom
          ? "bg-red-500/15 border-red-500/60"
          : "bg-gradient-to-t from-black/60 to-transparent border-white/12"
      }`}
    >
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 rounded-full border border-white/50 bg-white/10" />
        <div className="h-2 w-20 rounded-full bg-white/25" />
        <div className="h-5 w-14 rounded-full bg-white/90 text-[8px] flex items-center justify-center text-black font-bold">
          Subscribe
        </div>
      </div>
      <div className="h-2 w-3/5 rounded-full bg-white/15" />
      <div className="flex items-center gap-1.5 text-white/50">
        <Music size={9} />
        <div className="h-1.5 w-24 rounded-full bg-white/15" />
      </div>
    </div>

    {/* Right actions */}
    <div
      className={`absolute right-2 bottom-[10%] flex flex-col items-center gap-4 transition-colors ${
        unsafeBottom ? "opacity-100" : "opacity-70"
      }`}
    >
      {[ThumbsUp, ThumbsDown, MessageCircle, Share2, MoreHorizontal].map(
        (Icon, i) => (
          <div key={i} className="flex flex-col items-center gap-0.5">
            <div
              className={`w-9 h-9 rounded-full border flex items-center justify-center backdrop-blur-sm ${
                unsafeBottom
                  ? "bg-red-500/20 border-red-500/60"
                  : "bg-black/40 border-white/25"
              }`}
            >
              <Icon size={16} className="text-white/70" />
            </div>
            {i < 4 && <div className="h-1 w-5 rounded-full bg-white/25" />}
          </div>
        )
      )}
      <div className="w-8 h-8 rounded-full border border-white/25 bg-black/40 backdrop-blur-sm flex items-center justify-center">
        <Music size={12} className="text-white/70" />
      </div>
    </div>
  </>
);

export default SafeZoneOverlay;
