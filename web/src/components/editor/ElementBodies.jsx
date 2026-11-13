import React, { useMemo } from "react";
import { useAppStore, useDisplayWord } from "@/store/useAppStore";
import { outputFileUrl } from "@/api/client";
import { getCaptionStylePreview, getCaptionFontStack } from "@/data/captionStylePreview";
import {
  buildCaptionLinesWithRealignments,
  findActiveLine,
  findActiveWordIndex,
} from "@/lib/captionLines";
import { normalizePillUnits } from "@/lib/pillUnits";
import { emojiAssetUrl, EMOJI_DEFAULT_HEIGHT } from "@/lib/emojiOverlays";

/**
 * Per-type element content renderers — extracted from ElementRenderer so
 * both the interactive editor canvas (drag/select/TransformBox) and any
 * read-only preview (currently: the Export page's live draft preview) can
 * render identical element content without duplicating caption/overlay
 * logic. Pure content only — no drag handlers, no positioning wrapper.
 */
export const ElementBody = ({ element, canvasH }) => {
  switch (element.type) {
    case "caption":
      return <CaptionBody element={element} canvasH={canvasH} />;
    case "headline":
      return <HeadlineBody element={element} canvasH={canvasH} />;
    case "progress":
      return <ProgressBody element={element} canvasH={canvasH} />;
    case "logo":
      return <LogoBody element={element} canvasH={canvasH} />;
    case "image":
      return <ImageBody element={element} canvasH={canvasH} />;
    case "emoji":
      return <EmojiBody element={element} canvasH={canvasH} />;
    default:
      return null;
  }
};

// Aspect ratio of the current canvas (w/h) — the editor stage and the burn
// both follow exportSettings.format, so width-fraction props scale off the
// REAL canvas width, not the retired 9:16-only canvasW=canvasH*9/16 shortcut.
const ASPECT_WH = { "9:16": 9 / 16, "1:1": 1, "16:9": 16 / 9 };

// Stable empty array so the emphasis selector doesn't re-render every frame.
const EMPTY_EMPHASIS = [];
const useCanvasAspectWH = () => {
  const format = useAppStore((s) => s.exportSettings.format);
  return ASPECT_WH[format] || ASPECT_WH["9:16"];
};

export const animationClass = (anim) => {
  switch (anim) {
    case "pop":
      return "anim-pop";
    case "fade":
      return "anim-fade";
    case "slide-up":
      return "anim-slide-up";
    case "bounce": // legacy alias (old drafts) → slide-up motion
      return "anim-bounce";
    default:
      return "";
  }
};

// Feature #16 — glow (spotlight preset). The backend gives the ACTIVE word a
// white core (color_unspoken fill) + a coloured neon halo (\3c + \blur3 in
// color_highlight). CSS approximation: stacked blurred text-shadows in the
// highlight colour behind a white fill. Only the active karaoke word glows.
const glowShadow = (hlColor) =>
  `0 0 4px ${hlColor}, 0 0 9px ${hlColor}, 0 0 16px ${hlColor}`;

const CaptionBody = ({ element, canvasH }) => {
  const transcript = useAppStore((s) => s.transcript);
  const currentTime = useAppStore((s) => s.currentTime);
  // Word display text ALWAYS reads through the store's script-aware resolver
  // (edits win over the original; the Telugu ⇄ Tanglish toggle picks the
  // script) — never w.text directly. Karaoke timing is script-independent:
  // only the rendered text changes when the toggle flips.
  const displayWord = useDisplayWord();

  const { presetId, font, fontSize, animation, pill } = element.props;
  const preview = getCaptionStylePreview(presetId);
  const isKaraoke = animation === "karaoke";
  // Font is chosen in the Inspector (one of the three bundled caption fonts),
  // decoupled from the preset — mirrors the backend, where the style drives
  // only colors and caption_font selects the family. getCaptionFontStack loads
  // the SAME @font-face .ttf the backend burns via fontsdir, so preview == export.
  const fontFamily = getCaptionFontStack(font);

  // Mirrors services/caption_renderer.py's line grouping: chunk into
  // wordsPerLine-word lines (4, or 2 for big-bold), each held on screen
  // for [lineStart, lineEnd) — not a fast sliding word-window — so preview
  // line breaks and pacing match export. lineSplits (forced breaks from
  // the Split button) re-group exactly like the backend's
  // group_words_with_splits, so a split re-breaks the preview live.
  const lineSplits = useAppStore((s) => s.transcriptEdits.lineSplits);
  // Line re-alignments overlay after grouping (applyLineRealignments — the
  // same lockstep mirror of the backend the transcript panel uses), so a
  // line edited with added/removed words plays its fresh karaoke timing in
  // the preview exactly as the burn will render it.
  const lineRealignments = useAppStore((s) => s.transcriptEdits.lineRealignments);
  const captionScript = useAppStore((s) => s.exportSettings.captionScript);
  const lines = useMemo(
    () =>
      buildCaptionLinesWithRealignments(
        transcript,
        preview.wordsPerLine,
        lineSplits,
        lineRealignments
      ),
    [transcript, preview.wordsPerLine, lineSplits, lineRealignments]
  );
  const activeLine = findActiveLine(lines, currentTime);
  const activeWordIdx = findActiveWordIndex(activeLine, currentTime);

  // Feature #6 — keyword emphasis. Effective set = user toggles when
  // materialized, else the clip's Gemini auto set (getEffectiveEmphasis).
  // Indices address the transcript array; membership is checked by word id
  // because grouped lines don't carry raw positions.
  const emphasisIndices = useAppStore((s) =>
    Array.isArray(s.transcriptEdits.emphasisIndices)
      ? s.transcriptEdits.emphasisIndices
      : s.currentClip?.emphasis_indices || EMPTY_EMPHASIS
  );
  const emphasisIds = useMemo(
    () => new Set(emphasisIndices.map((i) => transcript[i]?.id).filter(Boolean)),
    [emphasisIndices, transcript]
  );

  // User's manual pill toggle overrides the preset's own box (an explicit
  // customization); otherwise fall back to the backend style's real
  // back_color/border_style-derived box, so the untouched default already
  // matches what caption_renderer.py burns in.
  // Feature #4: padding/radius are FRACTIONS of canvas height (same unit as
  // fontSize) so the pill scales with the text on every aspect and matches
  // the burn. normalizePillUnits converts legacy absolute-px drafts once.
  const pillN = pill?.enabled ? normalizePillUnits(pill) : null;
  const pillStyle = pillN
    ? {
        background: hexToRgba(pillN.color, pillN.opacity),
        padding: `${pillN.padding * canvasH}px ${pillN.padding * canvasH * 1.6}px`,
        borderRadius: `${pillN.radius * canvasH}px`,
      }
    : preview.box || {};

  // No line is on screen at this instant (a real gap between lines, just
  // like the backend's ASS timeline) — show nothing, matching export.
  if (!activeLine) return null;

  return (
    <div
      className="flex flex-wrap justify-center gap-x-2 gap-y-1 max-w-[70vw] text-center"
      style={{ ...pillStyle, width: "max-content", maxWidth: 380 }}
    >
      {activeLine.words.map((w, gIdx) => {
        // Karaoke 3-state progression, mirroring the backend's
        // color_unspoken -> color_highlight -> color_spoken transition.
        // Non-karaoke animations show every word in its base (unspoken)
        // color; animationClass() drives the motion instead.
        const role = !isKaraoke ? "unspoken" : gIdx < activeWordIdx ? "spoken" : gIdx === activeWordIdx ? "active" : "unspoken";
        // Feature #6: emphasized words stay in the highlight colour, bold,
        // at 112% for their whole lifetime — mirror of the ASS override
        // tags generate_ass_karaoke emits (\b1\fscx112\fscy112 + highlight).
        const emphasized = w.id != null && emphasisIds.has(w.id);
        // Feature #16 — glow preset (spotlight): the active karaoke word gets a
        // WHITE core (color_unspoken) + a coloured neon halo, mirroring the
        // backend's white-fill + \blur3 halo. Emphasis still wins (it's an
        // explicit user override).
        const glowing = preview.glow && role === "active" && isKaraoke && !emphasized;
        const color = emphasized
          ? preview.colorHighlight
          : glowing
          ? preview.colorUnspoken
          : role === "active" ? preview.colorHighlight : role === "spoken" ? preview.colorSpoken : preview.colorUnspoken;
        const textShadow = glowing
          ? `${glowShadow(preview.colorHighlight)}, ${preview.textShadow}`
          : preview.textShadow;
        return (
          <span
            key={`${gIdx}-${w.id ?? w.text}`}
            className={gIdx === activeWordIdx ? animationClass(animation) : ""}
            style={{
              fontFamily,
              fontWeight: emphasized ? 800 : preview.fontWeight,
              color,
              textShadow,
              // Feature #16 — ALL-CAPS presets uppercase ONLY in Tanglish (the
              // backend .upper()s Latin text; Telugu has no case, left as-is).
              textTransform: preview.uppercase && captionScript === "tanglish" ? "uppercase" : "none",
              fontSize: fontSize * canvasH * (emphasized ? 1.12 : 1),
              lineHeight: 1.25,
              transform: role === "active" && isKaraoke ? "scale(1.06)" : "scale(1)",
              transition: "color 120ms linear, transform 120ms linear",
            }}
          >
            {w.realigned
              ? // Realigned words are synthetic (no transcript id, so no
                // resolver): the record carries both scripts; pick by the
                // same toggle displayWord honors, falling back to Telugu.
                captionScript === "tanglish"
                ? w.text_tanglish || w.text
                : w.text
              : displayWord(w.id)}
          </span>
        );
      })}
    </div>
  );
};

const HeadlineBody = ({ element, canvasH }) => {
  const p = element.props;
  return (
    <div
      style={{
        fontFamily: p.font,
        fontSize: p.fontSize * canvasH,
        color: p.color,
        fontWeight: p.weight,
        fontStyle: p.italic ? "italic" : "normal",
        textTransform: p.uppercase ? "uppercase" : "none",
        WebkitTextStroke: p.stroke ? "1.5px rgba(0,0,0,0.85)" : "none",
        textShadow: "0 4px 18px rgba(0,0,0,0.55)",
        whiteSpace: "nowrap",
        letterSpacing: "0.02em",
      }}
    >
      {p.text}
    </div>
  );
};

const ProgressBody = ({ element, canvasH }) => {
  const currentTime = useAppStore((s) => s.currentTime);
  const duration = useAppStore((s) => s.duration);
  const aspectWH = useCanvasAspectWH();
  const p = element.props;
  const canvasW = canvasH * aspectWH;
  // WYSIWYG mirror of _prepare_progress_layer: 'countdown' starts full and
  // empties; 'grow' (default) fills as time elapses. Track opacity is a prop.
  // No boxShadow glow here — the burn draws none, so a preview glow would break
  // the WYSIWYG contract (the bar looked softer in the editor than on export).
  const elapsed = duration ? currentTime / duration : 0;
  const frac = p.direction === "countdown" ? 1 - elapsed : elapsed;
  const trackAlpha = p.trackOpacity != null ? p.trackOpacity : 0.18;
  return (
    <div
      style={{
        width: p.width * canvasW,
        height: Math.max(3, p.height * canvasH),
        background: `rgba(255,255,255,${trackAlpha})`,
        borderRadius: 999,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          width: `${Math.max(0, Math.min(1, frac)) * 100}%`,
          height: "100%",
          background: p.color,
          borderRadius: 999,
        }}
      />
    </div>
  );
};

const LogoBody = ({ element, canvasH }) => {
  const p = element.props;
  return (
    <div
      className="flex items-center gap-1.5"
      style={{ fontFamily: p.font, fontSize: p.fontSize * canvasH }}
    >
      <div
        className="rounded-full bg-gradient-to-br from-k-accent to-k-accentsoft flex items-center justify-center text-white font-bold"
        style={{
          width: p.fontSize * canvasH * 1.8,
          height: p.fontSize * canvasH * 1.8,
          fontSize: p.fontSize * canvasH * 0.9,
        }}
      >
        {p.avatar}
      </div>
      <span className="text-white/90 font-semibold drop-shadow-[0_2px_6px_rgba(0,0,0,0.6)]">
        {p.text}
      </span>
    </div>
  );
};

// User-uploaded image overlay. Sizing contract shared with the burn
// (services/overlay_renderer.py :: _prepare_image_layer): props.height is a
// fraction of canvas HEIGHT, width follows the image's natural aspect ratio
// (width: auto), element.scale multiplies via the positioning wrapper's
// transform, and props.opacity maps 1:1 to the burned alpha multiply.
const ImageBody = ({ element, canvasH }) => {
  const p = element.props;
  const src = p.src || (p.image_id ? outputFileUrl(p.image_id) : null);
  if (!src) return null;
  return (
    <img
      src={src}
      alt=""
      draggable={false}
      style={{
        height: (p.height ?? 0.18) * canvasH,
        width: "auto",
        opacity: p.opacity ?? 1,
        display: "block",
        pointerEvents: "none",
      }}
    />
  );
};

// Feature #30 — a timed color-emoji overlay (Twemoji PNG). Renders the SAME
// asset the burn composites (public/emoji/* == services/assets/emoji/*), and is
// visible ONLY inside its [start,end] window — the DOM mirror of the burn's
// overlay enable='between(t,start,end)', so preview and export show the emoji
// over the same span. An emoji with no window (both null) is always-on.
const EmojiBody = ({ element, canvasH }) => {
  const currentTime = useAppStore((s) => s.currentTime);
  const p = element.props;
  const { start, end } = p;
  const windowed = start != null && end != null;
  if (windowed && !(currentTime >= start && currentTime < end)) return null;
  if (!p.emoji) return null;
  return (
    <img
      src={emojiAssetUrl(p.emoji)}
      alt={p.emoji}
      draggable={false}
      style={{
        height: (p.height ?? EMOJI_DEFAULT_HEIGHT) * canvasH,
        width: "auto",
        opacity: p.opacity ?? 1,
        display: "block",
        pointerEvents: "none",
      }}
    />
  );
};

function hexToRgba(hex, alpha = 1) {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(full, 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return `rgba(${r},${g},${b},${alpha})`;
}

export default ElementBody;
