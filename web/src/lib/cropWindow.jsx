// Sprint 4 — 16:9-master + per-aspect crop window math.
//
// The editor now loads the clip's 16:9 MASTER (raw_path) and derives every
// output aspect by cropping INWARD with a fractional window {x, y, w, h}
// (0–1 fractions of the master frame — the exact shape the backend's
// _prepare_source consumes: crop=in_w*w:in_h*h:in_w*x:in_h*y).
//
// THE PARITY CONTRACT (this sprint's WYSIWYG gate): the layout functions in
// this file must mirror the burn chain exactly —
//   _prepare_source  → crop the master to the window
//   _apply_canvas    → scale=W:H:force_original_aspect_ratio=decrease (a
//                      "contain" fit), centered with pad; 'blur' background
//                      is the CROPPED video stretched (scale=W:H, aspect NOT
//                      preserved) to the full canvas and blurred.
// cropWindow.test.js asserts preview math ≡ backend math across aspects.
// Change the burn → change this file → change the test, in lockstep.

// Output canvas aspect ratios — FORMAT_CONFIG in api/worker.py.
export const OUTPUT_ASPECTS = {
  "9:16": 9 / 16,
  "1:1": 1,
  "16:9": 16 / 9,
};

const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

export const MIN_CROP_W = 0.05; // smallest draggable window (fraction of master width)

// The default_crop_box the vertical cropper persists is 9:16-shaped over the
// master (w = (9/16)·masterH/masterW, h = 1), so the master's aspect can be
// recovered from it when the <video> metadata hasn't loaded yet:
// masterAR = (9/16)·h/w. Measured metadata always wins when available.
export function inferMasterAspect(defaultCropBox, masterDims) {
  if (masterDims?.w > 0 && masterDims?.h > 0) return masterDims.w / masterDims.h;
  if (defaultCropBox?.w > 0 && defaultCropBox?.h > 0) {
    return ((9 / 16) * defaultCropBox.h) / defaultCropBox.w;
  }
  return 16 / 9; // sources are 16:9 masters by pipeline construction
}

// Clamp a window to valid bounds: inside the frame, never below MIN_CROP_W.
export function clampCropBox(box) {
  const w = clamp(box?.w ?? 1, MIN_CROP_W, 1);
  const h = clamp(box?.h ?? 1, MIN_CROP_W, 1);
  return {
    x: clamp(box?.x ?? 0, 0, 1 - w),
    y: clamp(box?.y ?? 0, 0, 1 - h),
    w,
    h,
  };
}

export function boxesAlmostEqual(a, b, eps = 1e-3) {
  if (!a || !b) return false;
  return ["x", "y", "w", "h"].every((k) => Math.abs((a[k] ?? 0) - (b[k] ?? 0)) <= eps);
}

// The h/w lock that keeps a window's cropped region exactly the output
// aspect: (w·masterW)/(h·masterH) = outAR  ⇒  h = w · masterAR / outAR.
export function cropRatioK(aspect, masterAR) {
  const outAR = OUTPUT_ASPECTS[aspect] || OUTPUT_ASPECTS["9:16"];
  return masterAR / outAR;
}

// Largest aspect-locked window over the master, centered on a point.
function lockedWindowAt(aspect, masterAR, cx, cy) {
  const k = cropRatioK(aspect, masterAR);
  // Full height first (k ≥ 1 means the window is height-limited).
  let w = 1 / Math.max(k, 1);
  let h = w * k;
  w = Math.min(w, 1);
  h = Math.min(h, 1);
  return clampCropBox({ x: cx - w / 2, y: cy - h / 2, w, h });
}

// The window the editor starts from for an aspect. For 9:16 with a persisted
// AI crop, that IS default_crop_box — so an untouched clip previews exactly
// the region baked into _vertical.mp4. Other aspects derive the largest
// aspect-locked window centered on the AI crop's (face) center; without a
// default box, dead center.
export function initialWindowForAspect(aspect, masterAR, defaultCropBox) {
  if (aspect === "9:16" && defaultCropBox) return clampCropBox(defaultCropBox);
  const cx = defaultCropBox ? defaultCropBox.x + defaultCropBox.w / 2 : 0.5;
  const cy = defaultCropBox ? defaultCropBox.y + defaultCropBox.h / 2 : 0.5;
  return lockedWindowAt(aspect, masterAR, cx, cy);
}

// Move a window by master-fraction deltas, clamped inside the frame.
export function moveCropBox(box, dx, dy) {
  return {
    ...box,
    x: clamp(box.x + dx, 0, 1 - box.w),
    y: clamp(box.y + dy, 0, 1 - box.h),
  };
}

// Resize from a corner handle (tl|tr|bl|br), aspect-locked via ratioK
// (h = w·ratioK), opposite corner anchored, clamped to the frame.
export function resizeCropBox(box, corner, dx, dy, ratioK) {
  const left = corner.includes("l");
  const top = corner.includes("t");
  const anchorX = left ? box.x + box.w : box.x;
  const anchorY = top ? box.y + box.h : box.y;
  // Dominant-axis growth: whichever drag axis asks for the bigger window.
  const wFromX = box.w + (left ? -dx : dx);
  const wFromY = (box.h + (top ? -dy : dy)) / ratioK;
  let w = Math.max(wFromX, wFromY, MIN_CROP_W);
  // Frame clamps through the anchor, on both axes; w ≤ 1 and h = w·k ≤ 1.
  const maxWx = left ? anchorX : 1 - anchorX;
  const maxWy = (top ? anchorY : 1 - anchorY) / ratioK;
  w = Math.min(w, maxWx, maxWy, 1, 1 / Math.max(ratioK, 1));
  w = Math.max(w, MIN_CROP_W);
  const h = w * ratioK;
  return clampCropBox({
    x: left ? anchorX - w : anchorX,
    y: top ? anchorY - h : anchorY,
    w,
    h,
  });
}

// "Contain" fit — the geometry of scale=W:H:force_original_aspect_ratio=
// decrease + centered pad. contentAR = content width/height.
export function containFit(contentAR, boxW, boxH) {
  const boxAR = boxW / boxH;
  const width = contentAR >= boxAR ? boxW : boxH * contentAR;
  const height = contentAR >= boxAR ? boxW / contentAR : boxH;
  return {
    width,
    height,
    left: (boxW - width) / 2,
    top: (boxH - height) / 2,
  };
}

// Foreground layout: where the fitted (cropped) video sits in the stage, and
// how the master <video> element must be sized/offset inside that viewport so
// the crop window's region fills it exactly. Mirrors _prepare_source (crop)
// followed by _apply_canvas's contain+pad, at stage scale.
export function cropVideoLayout(cropBox, stageW, stageH, masterAR) {
  const box = clampCropBox(cropBox);
  const croppedAR = (box.w / box.h) * masterAR;
  const fit = containFit(croppedAR, stageW, stageH);
  const videoW = fit.width / box.w;
  const videoH = fit.height / box.h;
  return {
    box: { left: fit.left, top: fit.top, width: fit.width, height: fit.height },
    video: {
      left: -box.x * videoW,
      top: -box.y * videoH,
      width: videoW,
      height: videoH,
    },
  };
}

// Blur-fill layout: _apply_canvas stretches the CROPPED video (scale=W:H, no
// aspect preservation) across the whole canvas before blurring. So the blur
// clone shows the crop window's region stretched to the full stage.
export function cropFillLayout(cropBox, stageW, stageH) {
  const box = clampCropBox(cropBox);
  const videoW = stageW / box.w;
  const videoH = stageH / box.h;
  return {
    left: -box.x * videoW,
    top: -box.y * videoH,
    width: videoW,
    height: videoH,
  };
}

// Wire rounding: keep payload fractions stable across float noise.
export function roundCropBox(box) {
  const r = (v) => Math.round(v * 1e6) / 1e6;
  const c = clampCropBox(box);
  return { x: r(c.x), y: r(c.y), w: r(c.w), h: r(c.h) };
}
