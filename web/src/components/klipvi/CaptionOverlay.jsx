import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import SafeZones, { overlaps } from "@/components/klipvi/SafeZones";
import {
  OUT_H,
  activeWordIndex,
  cssColour,
  cueAt,
  cues as buildCues,
  displayTok,
  fontFor,
  resolvedPosY,
  withDefaults,
  wrap,
} from "@/lib/captionEngine";

/** Follows a <video>'s clock every animation frame (the overlay must move with playback and scrubbing). */
export function useVideoTime(videoRef) {
  const [t, setT] = useState(0);
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      if (v) setT((prev) => (Math.abs(prev - v.currentTime) > 0.01 ? v.currentTime : prev));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [videoRef]);
  return t;
}

/** Height of the element in CSS px (the overlay scales every renderer pixel by height / 1920). */
export function useHeight(ref) {
  const [h, setH] = useState(0);
  useEffect(() => {
    if (!ref.current) return undefined;
    const ro = new ResizeObserver(([e]) => setH(e.contentRect.height));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, [ref]);
  return h;
}

/**
 * One caption cue at time t, drawn the way the renderer burns it (selection_v2/captions.py ass_text): lines from wrap(),
 * the spoken word coloured / boxed / popped, later words hidden for "reveal", keyword words in the emphasis colour,
 * outline, glow, shadow, box per line. `scale` = renderer pixels -> CSS pixels. The block's bottom centre sits at
 * (left, top); the caller positions it.
 */
export function CaptionBlock({ cue, t, settings: s, scale, style: extra, ...rest }) {
  const fam = fontFor(s.font);
  const toks = cue.words.map((w) => displayTok(w.tok, s));
  const rows = wrap(toks, s);
  const active = activeWordIndex(cue, t);
  const perWord = Boolean(s.highlight || s.highlight_box || s.animation === "pop" || s.animation === "reveal");
  const fade = s.animation === "fade" ? Math.max(0, Math.min(1, (t - cue.start) / 0.12, (cue.end - t) / 0.12)) : 1;
  const outlinePx = (s.box ? 0 : s.outline) * scale;
  const glowPx = (s.box ? 0 : s.glow || 0) * scale;
  const shadowPx = s.shadow * scale;
  const outlineCol = cssColour(s.outline_colour);
  const shadows = [];
  if (glowPx > 0) shadows.push(`0 0 ${glowPx}px ${outlineCol}`, `0 0 ${glowPx * 1.8}px ${outlineCol}`);
  if (shadowPx > 0 && !s.box) shadows.push(`${shadowPx}px ${shadowPx}px 0 rgba(0,0,0,1)`);
  const textStyle = {
    color: cssColour(s.colour),
    WebkitTextStroke: outlinePx > 0 ? `${outlinePx * (glowPx > 0 ? 1.2 : 2)}px ${outlineCol}` : undefined,
    paintOrder: "stroke fill",
    textShadow: shadows.length ? shadows.join(", ") : undefined,
  };
  const emph = cssColour(s.emphasis || s.highlight || "#FFD400");
  const boxPad = Math.max(8, Math.round(s.size * 0.14)) * scale;
  let n = 0;
  return (
    <div
      className="absolute flex flex-col items-center"
      style={{
        fontFamily: `"${fam.family}"`,
        fontWeight: fam.weight,
        fontSize: `${s.size * scale}px`,
        lineHeight: "normal",
        whiteSpace: "nowrap",
        opacity: fade,
        ...extra,
      }}
      {...rest}
    >
      {rows.map((row, ri) => (
        <div
          key={ri}
          style={{
            ...textStyle,
            background: s.box ? cssColour(s.box) : undefined,
            padding: s.box ? `0 ${14 * scale}px` : undefined,
            boxShadow: s.box && shadowPx > 0 ? `${shadowPx}px ${shadowPx}px 0 ${cssColour(s.box)}` : undefined,
          }}
        >
          {row.map((tok, wi) => {
            const idx = n++;
            const w = cue.words[idx] || {};
            const on = perWord && idx === active;
            const hidden = s.animation === "reveal" && idx > active;
            let colour;
            if (on && s.highlight) colour = cssColour(s.highlight);
            else if (w.emph) colour = emph;
            const boxed = on && s.highlight_box;
            return (
              <React.Fragment key={wi}>
                {wi > 0 && " "}
                <span
                  key={on ? `on-${cue.start}-${idx}` : `w-${idx}`}
                  className={on && s.animation === "pop" ? "klipvi-pop" : undefined}
                  style={{
                    display: "inline-block",
                    color: colour,
                    visibility: hidden ? "hidden" : undefined,
                    // on its box the spoken word is clean: no outline or shadow (as the renderer draws it)
                    WebkitTextStroke: boxed ? "0" : undefined,
                    textShadow: boxed ? "none" : undefined,
                    background: boxed ? cssColour(s.highlight_box) : undefined,
                    // the renderer's box grows the word's box by boxPad on every side without moving the line
                    padding: boxed ? `${boxPad * 0.5}px ${boxPad}px` : undefined,
                    margin: boxed ? `${-boxPad * 0.5}px ${-boxPad}px` : undefined,
                    position: boxed ? "relative" : undefined,
                    zIndex: boxed ? 0 : undefined,
                  }}
                >
                  {tok}
                </span>
              </React.Fragment>
            );
          })}
        </div>
      ))}
    </div>
  );
}

const SNAP_PX = 10; // within this many pixels of the centre lines, the caption snaps to them
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const r3 = (v) => Math.round(v * 1000) / 1000;

/**
 * Live captions over a 9:16 video, drawn with the renderer's rules and fonts (lib/captionEngine.jsx), so the preview
 * matches the downloaded video. Put it inside the same box as the <video> (absolute, inset-0).
 * `draggable` (the editor): the caption can be moved (snapping to the centre lines, with guide lines) and resized
 * from its corners; `onMove({pos_x, pos_y})` / `onResize(size)` report the result. While it is moved or resized, or
 * when `showZones`, the areas Instagram / YouTube cover with their buttons are drawn, and a warning names any the
 * caption runs into.
 */
export default function CaptionOverlay({
  videoRef,
  words,
  settings,
  breaks,
  style = "crop",
  draggable = false,
  onMove,
  onResize,
  showZones = false,
  platform = "instagram",
}) {
  const box = useRef(null);
  const block = useRef(null);
  const h = useHeight(box);
  const t = useVideoTime(videoRef);
  const base = useMemo(() => withDefaults(settings), [settings]);
  const [drag, setDrag] = useState(null); // {pos_x, pos_y, guideX, guideY} while moving
  const [sizing, setSizing] = useState(null); // font size while resizing
  const s = useMemo(() => (sizing ? { ...base, size: sizing } : base), [base, sizing]);
  const cs = useMemo(() => buildCues(words || [], s, breaks), [words, s, breaks]);
  const [frac, setFrac] = useState(null); // the caption block's box as fractions of the frame

  const cue = cueAt(cs, t);
  const scale = h / OUT_H;
  const posX = drag ? drag.pos_x : s.pos_x;
  const posY = drag ? drag.pos_y : resolvedPosY(s, style);
  const busy = Boolean(drag || sizing);

  // measure the block after each render (for the safe-zone check)
  useLayoutEffect(() => {
    if (!draggable || !block.current || !box.current) {
      if (frac) setFrac(null);
      return;
    }
    const r = box.current.getBoundingClientRect();
    const b = block.current.getBoundingClientRect();
    const f = { x0: (b.left - r.left) / r.width, x1: (b.right - r.left) / r.width, y0: (b.top - r.top) / r.height, y1: (b.bottom - r.top) / r.height };
    if (!frac || Object.keys(f).some((k) => Math.abs(f[k] - frac[k]) > 0.002)) setFrac(f);
  });
  const zonePlatform = platform === "none" ? "all" : platform; // while placing the caption the covered areas always count
  const hits = draggable && frac && cue ? overlaps(frac, zonePlatform) : [];

  const startMove = (e) => {
    if (!draggable || !box.current || e.button > 0) return;
    e.preventDefault();
    const r = box.current.getBoundingClientRect();
    const b = block.current.getBoundingClientRect();
    // keep the point where the block was grabbed under the pointer (the position is the block's bottom centre)
    const dx = b.left + b.width / 2 - e.clientX;
    const dy = b.bottom - e.clientY;
    const bh = b.height;
    const at = (ev) => {
      let x = (ev.clientX + dx - r.left) / r.width;
      let y = (ev.clientY + dy - r.top) / r.height;
      let guideX = false;
      let guideY = false;
      if (Math.abs((x - 0.5) * r.width) < SNAP_PX) {
        x = 0.5;
        guideX = true;
      }
      const middle = y - bh / 2 / r.height; // the block's vertical centre
      if (Math.abs((middle - 0.5) * r.height) < SNAP_PX) {
        y = 0.5 + bh / 2 / r.height;
        guideY = true;
      }
      return { pos_x: r3(clamp(x, 0.05, 0.95)), pos_y: r3(clamp(y, 0.05, 0.97)), guideX, guideY };
    };
    const move = (ev) => setDrag(at(ev));
    const up = (ev) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      const { pos_x, pos_y } = at(ev);
      setDrag(null);
      onMove?.({ pos_x, pos_y });
    };
    setDrag(at(e));
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const startResize = (e) => {
    e.preventDefault();
    e.stopPropagation();
    const b = block.current.getBoundingClientRect();
    const ax = b.left + b.width / 2; // the block grows from its bottom centre
    const ay = b.bottom;
    const d0 = Math.hypot(e.clientX - ax, e.clientY - ay) || 1;
    const size0 = base.size;
    const at = (ev) => clamp(Math.round((size0 * Math.hypot(ev.clientX - ax, ev.clientY - ay)) / d0), 36, 160);
    const move = (ev) => setSizing(at(ev));
    const up = (ev) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      const size = at(ev);
      setSizing(null);
      onResize?.(size);
    };
    setSizing(size0);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const bad = hits.length > 0;
  const ring = bad ? "rgba(226,73,59,0.95)" : `rgba(233,168,37,${busy ? 0.95 : 0.5})`;
  const handle = Math.max(9, 14 * scale * 3);

  return (
    <div ref={box} className="absolute inset-0 overflow-hidden" style={{ pointerEvents: "none" }}>
      {(showZones || busy) && <SafeZones platform={busy ? zonePlatform : platform} hit={hits.map((z) => z.key)} h={h} />}
      {drag?.guideX && <div className="absolute top-0 bottom-0 left-1/2 w-px bg-k-accent shadow-[0_0_0_1px_rgba(0,0,0,0.25)]" aria-hidden="true" />}
      {drag?.guideY && <div className="absolute left-0 right-0 top-1/2 h-px bg-k-accent shadow-[0_0_0_1px_rgba(0,0,0,0.25)]" aria-hidden="true" />}
      {cue && h > 0 && (
        <div
          ref={block}
          className="absolute"
          style={{
            left: `${posX * 100}%`,
            top: `${posY * 100}%`,
            transform: "translate(-50%, -100%)",
            cursor: draggable ? (drag ? "grabbing" : "grab") : "default",
            pointerEvents: draggable ? "auto" : "none",
            outline: draggable ? `${Math.max(1, scale * 3)}px dashed ${ring}` : undefined,
            outlineOffset: `${6 * scale}px`,
            touchAction: "none",
          }}
          onPointerDown={startMove}
          aria-label={draggable ? "Caption: drag to move, drag a corner to resize" : undefined}
        >
          <CaptionBlock cue={cue} t={t} settings={s} scale={scale} style={{ position: "relative" }} />
          {draggable &&
            ["nw", "ne", "sw", "se"].map((c) => (
              <span
                key={c}
                onPointerDown={startResize}
                className="absolute rounded-sm bg-white border-2"
                style={{
                  width: handle,
                  height: handle,
                  borderColor: ring,
                  top: c[0] === "n" ? -6 * scale - handle / 2 : undefined,
                  bottom: c[0] === "s" ? -6 * scale - handle / 2 : undefined,
                  left: c[1] === "w" ? -6 * scale - handle / 2 : undefined,
                  right: c[1] === "e" ? -6 * scale - handle / 2 : undefined,
                  cursor: c === "nw" || c === "se" ? "nwse-resize" : "nesw-resize",
                }}
                aria-hidden="true"
              />
            ))}
          {sizing && (
            <span className="absolute left-1/2 -translate-x-1/2 -top-8 rounded-md bg-black/80 px-2 py-0.5 text-xs text-white whitespace-nowrap">
              Size {sizing}
            </span>
          )}
        </div>
      )}
      {bad && (
        <p className="absolute left-2 right-2 top-2 rounded-md bg-k-mark/90 px-2.5 py-1.5 text-[0.8rem] leading-snug text-white">
          Covered on the phone: {[...new Set(hits.map((z) => z.what))].join("; ")}. Move the caption out of the red area.
        </p>
      )}
    </div>
  );
}
