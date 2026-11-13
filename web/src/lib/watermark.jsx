/**
 * Free-tier watermark geometry — the PREVIEW side of the WYSIWYG mirror for
 * services/overlay_renderer.py's WATERMARK_* block.
 *
 * Read that block before touching any number here. Both surfaces move together
 * or neither does; tests/test_watermark_parity.py reads THIS FILE from the
 * Python suite and fails if a constant drifts on one side only.
 *
 * What is deliberately different from every other overlay in this codebase:
 * the watermark is NOT an element. It never enters the Zustand `elements`
 * array, so it is not in the edit document, not in undo history, not in drafts,
 * not in the layers list, and — the reason that matters — not in the rerender
 * payload. `watermark` is derived server-side from the owner's plan
 * (api/main.py -> tiers.has_watermark) and injected inside render_elements, so
 * a crafted `elements` list can neither delete it nor add one. This module only
 * decides how to DRAW the preview of a decision the server already made.
 *
 * Coordinates are EDGE INSETS, not the 0-1 centre fractions the rest of the
 * canvas uses, because the mark's width is measured (text metrics) rather than
 * declared — there is no centre fraction to state. They are still frame-
 * relative; they just anchor to the right/top edges.
 */

// Mirror of FORMAT_CONFIG (api/worker.py) — the real burn dimensions each
// aspect renders at. Preview px = burn px x (canvasH / renderH).
export const WATERMARK_RENDER_DIMS = {
  "9:16": { w: 1080, h: 1920 },
  "1:1": { w: 1080, h: 1080 },
  "16:9": { w: 1920, h: 1080 },
};

export const WATERMARK_TEXT = "Klipvi";

// S = min(W, H) / 12. Shorter side, NOT height: every format above is 1080 on
// its short side, so S is 90px on all three with no per-aspect branch. The old
// burn rule was fontsize=h/26 — fraction of HEIGHT — which gave 74px on 9:16
// but only 41px on 1:1 and 16:9, silently shrinking the mark by ~45% on the
// wide formats. Shorter-side is the perceptual constant.
export const WATERMARK_SIZE_DIVISOR = 12;

// Right edge, upper band. Bottom-right is disqualified twice over: it is the
// intersection of both platforms' deepest UI dead zones AND where captions now
// sit (CAPTION_DEFAULT_POSITION y = 0.74). Top-LEFT belongs to the creator's
// own logo element. The literal top-right at y ~ 0.07 collides with the
// full-width progress element.
export const WATERMARK_RIGHT_INSET = 86; // burn px from the right frame edge
export const WATERMARK_TOP_INSET_TALL = 288; // 9:16 — below the vertical feed's top chrome
export const WATERMARK_TOP_INSET_WIDE = 65; // 1:1 / 16:9 — no such chrome

export const WATERMARK_OPACITY = 0.85; // was 0.55; free output must be distinguishable

// Soft drop shadow, replacing the burn's old hard shadowx=2/shadowy=2 offset
// (invisible under a 90px mark, useless over the bright/white-heavy footage
// Telugu studio and outdoor content produces). The burn blurs an offset black
// duplicate at gaussian radius = 0.16 x S with alpha 0.55; CSS text-shadow's
// blur-radius is ~3x a gaussian radius, the same 18-CSS-px : 6-gaussian-px
// conversion HeadlineBody and _prepare_headline_layer already agree on.
export const WATERMARK_SHADOW_ALPHA = 0.55;
export const WATERMARK_SHADOW_OFFSET_RATIO = 0.1; // of S
export const WATERMARK_SHADOW_BLUR_RATIO = 0.16; // of S, in GAUSSIAN radius
const SHADOW_BLUR_CSS_FACTOR = 3; // gaussian radius -> CSS text-shadow blur

/**
 * Burn-space geometry for an aspect — the exact mirror of
 * services/overlay_renderer.py :: watermark_geometry(). Returns render pixels.
 */
export const watermarkRenderGeometry = (aspect) => {
  const dims = WATERMARK_RENDER_DIMS[aspect] || WATERMARK_RENDER_DIMS["9:16"];
  return {
    size: Math.max(Math.round(Math.min(dims.w, dims.h) / WATERMARK_SIZE_DIVISOR), 1),
    rightInset: WATERMARK_RIGHT_INSET,
    topInset: dims.h > dims.w ? WATERMARK_TOP_INSET_TALL : WATERMARK_TOP_INSET_WIDE,
  };
};

/**
 * Burn geometry scaled into a preview box of `canvasH` CSS px → the style
 * values PreviewWatermark applies. The preview box always holds the export
 * aspect exactly, so canvasH/renderH == canvasW/renderW and one ratio scales
 * both axes.
 *
 * The burn anchors the text's INK box (ox = W - inset - measured ink width,
 * oy = topInset), while CSS anchors the line/advance box. Measured against the
 * bundled Montserrat-Black.ttf at S=90: the right side bearing of "Shotvi" is
 * 0.03px and, at line-height 1, the cap top sits 3.5px below the line-box top
 * — i.e. under 1.5 CSS px of difference at preview scale on either axis. No
 * compensation term is applied; this was checked, not assumed.
 */
export const watermarkPreviewLayout = (aspect, canvasH) => {
  const dims = WATERMARK_RENDER_DIMS[aspect] || WATERMARK_RENDER_DIMS["9:16"];
  const g = watermarkRenderGeometry(aspect);
  const ratio = (canvasH || 0) / dims.h;
  const fontSize = g.size * ratio;
  return {
    fontSize,
    right: g.rightInset * ratio,
    top: g.topInset * ratio,
    shadowOffset: fontSize * WATERMARK_SHADOW_OFFSET_RATIO,
    shadowBlur: fontSize * WATERMARK_SHADOW_BLUR_RATIO * SHADOW_BLUR_CSS_FACTOR,
  };
};
