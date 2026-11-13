import React from "react";
import {
  Camera,
  Forward,
  Heart,
  MessageCircle,
  MessageSquareText,
  MoreHorizontal,
  MoreVertical,
  Music2,
  Repeat2,
  Search,
  Send,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";

/**
 * Where Instagram Reels and YouTube Shorts cover the video with their own buttons and text, as fractions of the 9:16
 * frame. Neither platform publishes exact numbers; these are on the cautious side of the published guides (Nov 2026):
 * top bar, the bottom block (name, caption, music / title, subscribe) and the right column (like, comment, share).
 */
export const ZONES = {
  instagram: [
    { key: "ig-top", x0: 0, x1: 1, y0: 0, y1: 0.13, what: "Instagram's top bar" },
    { key: "ig-bottom", x0: 0, x1: 1, y0: 0.78, y1: 1, what: "Instagram's name, caption and music" },
    { key: "ig-right", x0: 0.89, x1: 1, y0: 0.42, y1: 0.78, what: "Instagram's like, comment and share buttons" },
  ],
  youtube: [
    { key: "yt-top", x0: 0, x1: 1, y0: 0, y1: 0.08, what: "YouTube's top bar" },
    { key: "yt-bottom", x0: 0, x1: 1, y0: 0.8, y1: 1, what: "YouTube's title and subscribe button" },
    { key: "yt-right", x0: 0.82, x1: 1, y0: 0.4, y1: 0.8, what: "YouTube's like, comment and share buttons" },
  ],
};

/** platform: "all" | "instagram" | "youtube" | "none" */
export const zonesFor = (platform) =>
  platform === "all" || platform === "both" ? [...ZONES.instagram, ...ZONES.youtube] : ZONES[platform] || [];

/** The zones a box (fractions {x0, x1, y0, y1}) runs into. */
export const overlaps = (box, platform) =>
  zonesFor(platform).filter((z) => box.x0 < z.x1 && box.x1 > z.x0 && box.y0 < z.y1 && box.y1 > z.y0);

// ---------------------------------------------------------------------------------------------- the phone's own UI
// A light, see-through sketch of what each app draws over the video (our own simplified drawing, not a copy).
const ghost = "text-white/80 drop-shadow-[0_1px_2px_rgba(0,0,0,0.6)]";

function Stat({ icon: Icon, label, u }) {
  return (
    <div className="flex flex-col items-center" style={{ gap: u * 0.25 }}>
      <Icon size={u * 1.55} strokeWidth={2} className={ghost} />
      {label && (
        <span className={`${ghost} font-semibold`} style={{ fontSize: u * 0.62, lineHeight: 1 }}>
          {label}
        </span>
      )}
    </div>
  );
}

const Bar = ({ w, h, u, className = "" }) => (
  <div className={`rounded-full bg-white/35 ${className}`} style={{ width: w, height: (h ?? 0.55) * u }} />
);

function InstagramMock({ u }) {
  return (
    <>
      <div className="absolute left-[5%] right-[5%] flex items-center justify-between" style={{ top: "3.5%" }}>
        <span className={`${ghost} font-bold`} style={{ fontSize: u * 1.05 }}>
          Reels
        </span>
        <Camera size={u * 1.4} className={ghost} />
      </div>
      <div className="absolute flex flex-col items-center" style={{ right: "2.4%", top: "50%", gap: u * 1.05 }}>
        <Stat icon={Heart} label="1.2L" u={u} />
        <Stat icon={MessageCircle} label="3.4K" u={u} />
        <Stat icon={Send} label="8.1K" u={u} />
        <Stat icon={MoreHorizontal} u={u} />
        <div className="rounded-md border-2 border-white/70 bg-white/25" style={{ width: u * 1.5, height: u * 1.5 }} />
      </div>
      <div className="absolute left-[4%] right-[16%] flex flex-col" style={{ bottom: "4%", gap: u * 0.55 }}>
        <div className="flex items-center" style={{ gap: u * 0.5 }}>
          <div className="rounded-full bg-white/45 border border-white/70" style={{ width: u * 1.5, height: u * 1.5 }} />
          <Bar w="34%" u={u} />
          <div className="rounded-md border border-white/70" style={{ width: u * 3.2, height: u * 1.05 }} />
        </div>
        <Bar w="88%" u={u} />
        <Bar w="60%" u={u} />
        <div className="flex items-center" style={{ gap: u * 0.4 }}>
          <Music2 size={u * 0.9} className={ghost} />
          <Bar w="42%" u={u} h={0.45} />
        </div>
      </div>
    </>
  );
}

function YoutubeMock({ u }) {
  return (
    <>
      <div className="absolute right-[4%] flex items-center" style={{ top: "2.5%", gap: u * 1.1 }}>
        <Search size={u * 1.35} className={ghost} />
        <MoreVertical size={u * 1.35} className={ghost} />
      </div>
      <div className="absolute flex flex-col items-center" style={{ right: "3%", top: "44%", gap: u * 1.05 }}>
        <Stat icon={ThumbsUp} label="45K" u={u} />
        <Stat icon={ThumbsDown} label="Dislike" u={u} />
        <Stat icon={MessageSquareText} label="812" u={u} />
        <Stat icon={Forward} label="Share" u={u} />
        <Stat icon={Repeat2} label="Remix" u={u} />
        <div className="rounded-md border-2 border-white/70 bg-white/25" style={{ width: u * 1.6, height: u * 1.6 }} />
      </div>
      <div className="absolute left-[4%] right-[20%] flex flex-col" style={{ bottom: "4.5%", gap: u * 0.55 }}>
        <Bar w="92%" u={u} />
        <Bar w="70%" u={u} />
        <div className="flex items-center" style={{ gap: u * 0.5 }}>
          <div className="rounded-full bg-white/45 border border-white/70" style={{ width: u * 1.5, height: u * 1.5 }} />
          <Bar w="30%" u={u} />
          <div className="rounded-full bg-white/80" style={{ width: u * 4.4, height: u * 1.3 }} />
        </div>
      </div>
    </>
  );
}

/**
 * The covered areas over the video (pointer-transparent). With one platform chosen, a see-through sketch of that
 * app's buttons and text is drawn too. `hit` = keys of zones the caption runs into (drawn stronger). `h` = frame height.
 */
export default function SafeZones({ platform = "all", hit = [], h = 0 }) {
  if (platform === "none") return null;
  const on = new Set(hit);
  const u = Math.max(7, h * 0.0215); // the sketch's unit, scaled with the frame
  return (
    <div className="absolute inset-0 pointer-events-none" aria-hidden="true">
      {zonesFor(platform).map((z) => (
        <div
          key={z.key}
          className={`absolute border border-dashed ${on.has(z.key) ? "bg-k-mark/30 border-k-mark" : "bg-black/15 border-white/30"}`}
          style={{ left: `${z.x0 * 100}%`, width: `${(z.x1 - z.x0) * 100}%`, top: `${z.y0 * 100}%`, height: `${(z.y1 - z.y0) * 100}%` }}
        />
      ))}
      {h > 0 && platform === "instagram" && <InstagramMock u={u} />}
      {h > 0 && platform === "youtube" && <YoutubeMock u={u} />}
    </div>
  );
}
