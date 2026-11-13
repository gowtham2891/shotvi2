/**
 * SPRINT 4 — crop-window math + THE PREVIEW ≡ BURN PARITY GATE.
 *
 * lib/cropWindow.js is the frontend mirror of the backend burn chain:
 *   _prepare_source (api/worker.py): crop=in_w*w:in_h*h:in_w*x:in_h*y
 *   _apply_canvas   (api/worker.py): scale=W:H:force_original_aspect_ratio=
 *                    decrease + centered pad; 'blur' bg = the CROPPED video
 *                    stretched (scale=W:H) to the canvas and blurred.
 * The parity tests below model that chain in render pixels and assert the
 * preview layout equals it at stage scale, across aspects, windows, and
 * master dimensions. If either side changes, this file must fail.
 */
import {
  OUTPUT_ASPECTS,
  MIN_CROP_W,
  inferMasterAspect,
  clampCropBox,
  boxesAlmostEqual,
  cropRatioK,
  initialWindowForAspect,
  moveCropBox,
  resizeCropBox,
  containFit,
  cropVideoLayout,
  cropFillLayout,
  roundCropBox,
} from "@/lib/cropWindow";
import { STAGE_DIMS } from "@/components/editor/CanvasArea";

// FORMAT_CONFIG in api/worker.py — the burn's output dimensions.
const RENDER_DIMS = {
  "9:16": { w: 1080, h: 1920 },
  "1:1": { w: 1080, h: 1080 },
  "16:9": { w: 1920, h: 1080 },
};

// The backend geometry: crop the master, contain-fit into the canvas, center.
function backendGeometry(box, masterW, masterH, renderW, renderH) {
  const cw = masterW * box.w; // _prepare_source crop
  const ch = masterH * box.h;
  const sf = Math.min(renderW / cw, renderH / ch); // force_original_aspect_ratio=decrease
  const fw = cw * sf;
  const fh = ch * sf;
  return { fw, fh, padX: (renderW - fw) / 2, padY: (renderH - fh) / 2 };
}

// The pipeline's real face-crop shape on a 1920x1080 master:
// crop_width = int(1080 * 0.5625) = 607, face-centred at x=1197.
const FACE_CROP_1080P = { x: 1197 / 1920, y: 0, w: 607 / 1920, h: 1 };

describe("preview ≡ burn parity (cropVideoLayout vs _prepare_source→_apply_canvas)", () => {
  const CASES = [];
  for (const aspect of ["9:16", "1:1", "16:9"]) {
    for (const master of [{ w: 1920, h: 1080 }, { w: 1280, h: 720 }]) {
      for (const box of [
        { x: 0, y: 0, w: 1, h: 1 },                 // full frame
        FACE_CROP_1080P,                             // the AI 9:16 crop
        { x: 0.1, y: 0.05, w: 0.5, h: 0.9 },        // arbitrary manual window
        { x: 0.4375, y: 0, w: 0.5625, h: 1 },       // right-edge square-ish
      ]) {
        CASES.push([aspect, master, box]);
      }
    }
  }

  test.each(CASES)(
    "aspect %s master %j window %j — stage layout equals burn layout at stage scale",
    (aspect, master, box) => {
      const stage = STAGE_DIMS[aspect];
      const render = RENDER_DIMS[aspect];
      // STAGE_DIMS are exact scale models of the render canvas per aspect.
      const scale = stage.h / render.h;
      expect(stage.w / render.w).toBeCloseTo(scale, 10);

      const burn = backendGeometry(box, master.w, master.h, render.w, render.h);
      const fg = cropVideoLayout(box, stage.w, stage.h, master.w / master.h);

      // The fitted (cropped) video's rect on the canvas, at stage scale.
      expect(fg.box.width).toBeCloseTo(burn.fw * scale, 6);
      expect(fg.box.height).toBeCloseTo(burn.fh * scale, 6);
      expect(fg.box.left).toBeCloseTo(burn.padX * scale, 6);
      expect(fg.box.top).toBeCloseTo(burn.padY * scale, 6);

      // And the master <video> inside the viewport shows EXACTLY the window:
      // the window's region spans the whole viewport, offset to its x/y.
      expect(fg.video.width * box.w).toBeCloseTo(fg.box.width, 6);
      expect(fg.video.height * box.h).toBeCloseTo(fg.box.height, 6);
      expect(-fg.video.left).toBeCloseTo(box.x * fg.video.width, 6);
      expect(-fg.video.top).toBeCloseTo(box.y * fg.video.height, 6);
      // No distortion: the element keeps the master's own aspect.
      expect(fg.video.width / fg.video.height).toBeCloseTo(master.w / master.h, 6);
    }
  );

  test("blur fill stretches the CROPPED region across the whole stage (scale=W:H semantics)", () => {
    for (const aspect of ["9:16", "1:1", "16:9"]) {
      const stage = STAGE_DIMS[aspect];
      const box = { x: 0.2, y: 0.1, w: 0.5, h: 0.8 };
      const bg = cropFillLayout(box, stage.w, stage.h);
      // The window's region maps exactly onto the full stage.
      expect(bg.width * box.w).toBeCloseTo(stage.w, 6);
      expect(bg.height * box.h).toBeCloseTo(stage.h, 6);
      expect(-bg.left).toBeCloseTo(box.x * bg.width, 6);
      expect(-bg.top).toBeCloseTo(box.y * bg.height, 6);
    }
  });

  test("UNTOUCHED 9:16 shows what _vertical.mp4 bakes: the AI window fills the stage", () => {
    // The vertical file IS the face-crop window scaled to 1080x1920. An
    // untouched editor must show that same region filling the 9:16 stage.
    const stage = STAGE_DIMS["9:16"];
    const fg = cropVideoLayout(FACE_CROP_1080P, stage.w, stage.h, 1920 / 1080);
    // 607/1080 vs 607.5/1080 → sub-pixel letterbox only (int truncation in
    // the cropper); the window fills the stage to within half a preview px.
    expect(fg.box.width).toBeCloseTo(stage.w, 0);
    expect(fg.box.height).toBeCloseTo(stage.h, 0);
    expect(Math.abs(fg.box.left)).toBeLessThan(0.5);
    expect(Math.abs(fg.box.top)).toBeLessThan(0.5);
  });
});

describe("window derivation + master aspect inference", () => {
  test("initial 9:16 window IS default_crop_box (the AI framing, verbatim)", () => {
    expect(initialWindowForAspect("9:16", 16 / 9, FACE_CROP_1080P)).toEqual(
      clampCropBox(FACE_CROP_1080P)
    );
  });

  test("initial windows for other aspects: largest aspect-locked window centred on the face", () => {
    const masterAR = 16 / 9;
    const faceCx = FACE_CROP_1080P.x + FACE_CROP_1080P.w / 2;

    const w169 = initialWindowForAspect("16:9", masterAR, FACE_CROP_1080P);
    expect(w169).toEqual({ x: 0, y: 0, w: 1, h: 1 }); // 16:9 over 16:9 = full frame

    const w11 = initialWindowForAspect("1:1", masterAR, FACE_CROP_1080P);
    expect(w11.h).toBeCloseTo(1, 6); // full height
    expect(w11.w).toBeCloseTo(9 / 16, 6); // square over 16:9 master
    // Centred on the face, clamped inside the frame.
    const expectedX = Math.min(Math.max(faceCx - w11.w / 2, 0), 1 - w11.w);
    expect(w11.x).toBeCloseTo(expectedX, 6);
    // The window really is square in pixels.
    expect((w11.w / w11.h) * masterAR).toBeCloseTo(1, 6);
  });

  test("no default box → centred windows", () => {
    const w = initialWindowForAspect("9:16", 16 / 9, null);
    expect(w.x).toBeCloseTo((1 - w.w) / 2, 6);
    expect((w.w / w.h) * (16 / 9)).toBeCloseTo(9 / 16, 6);
  });

  test("master aspect: measured metadata wins, else recovered from the 9:16-shaped default box", () => {
    expect(inferMasterAspect(null, { w: 1280, h: 720 })).toBeCloseTo(16 / 9, 6);
    // Recovery: masterAR = (9/16)·h/w for the cropper's 9:16-shaped box.
    expect(inferMasterAspect({ x: 0, y: 0, w: 0.31640625, h: 1 }, null)).toBeCloseTo(16 / 9, 6);
    // int-truncated real box stays within a hundredth of the truth.
    expect(inferMasterAspect(FACE_CROP_1080P, null)).toBeCloseTo(16 / 9, 2);
    expect(inferMasterAspect(null, null)).toBeCloseTo(16 / 9, 6); // pipeline default
  });
});

describe("drag clamps and aspect-locked resize", () => {
  const K1 = cropRatioK("16:9", 16 / 9); // = 1 — neutral lock for mechanics
  const K916 = cropRatioK("9:16", 16 / 9); // ≈ 3.16 — height-limited lock

  test("moveCropBox clamps inside the frame", () => {
    const box = { x: 0.4, y: 0, w: 0.3, h: 1 };
    expect(moveCropBox(box, 10, 0).x).toBeCloseTo(0.7, 6); // right edge
    expect(moveCropBox(box, -10, 0).x).toBeCloseTo(0, 6);  // left edge
    expect(moveCropBox(box, 0.1, 0).x).toBeCloseTo(0.5, 6);
    expect(moveCropBox(box, 0, 5).y).toBe(0); // h=1 → no vertical travel
  });

  test("clampCropBox pins out-of-bounds windows and enforces the minimum size", () => {
    expect(clampCropBox({ x: 0.9, y: 0, w: 0.3, h: 1 }).x).toBeCloseTo(0.7, 6);
    expect(clampCropBox({ x: -0.2, y: -0.2, w: 0.5, h: 0.5 })).toEqual(
      { x: 0, y: 0, w: 0.5, h: 0.5 }
    );
    const tiny = clampCropBox({ x: 0.5, y: 0.5, w: 0.001, h: 0.001 });
    expect(tiny.w).toBe(MIN_CROP_W);
    expect(tiny.h).toBe(MIN_CROP_W);
  });

  test("resizeCropBox keeps the aspect lock and anchors the opposite corner", () => {
    const start = { x: 0.4, y: 0.2, w: 0.2, h: 0.2 };
    const grown = resizeCropBox(start, "br", 0.1, 0, K1);
    expect(grown.w).toBeCloseTo(0.3, 6);
    expect(grown.h).toBeCloseTo(0.3, 6);
    expect(grown.x).toBeCloseTo(0.4, 6); // tl anchored
    expect(grown.y).toBeCloseTo(0.2, 6);

    const tl = resizeCropBox(start, "tl", -0.1, 0, K1);
    expect(tl.w).toBeCloseTo(0.3, 6);
    // br corner anchored
    expect(tl.x + tl.w).toBeCloseTo(start.x + start.w, 6);
    expect(tl.y + tl.h).toBeCloseTo(start.y + start.h, 6);
  });

  test("resizeCropBox clamps to the frame through the anchor and never collapses", () => {
    const start = { x: 0.7, y: 0.2, w: 0.2, h: 0.2 };
    const grown = resizeCropBox(start, "br", 10, 10, K1); // drag way outside
    expect(grown.x + grown.w).toBeLessThanOrEqual(1 + 1e-9);
    expect(grown.y + grown.h).toBeLessThanOrEqual(1 + 1e-9);
    expect(grown.h / grown.w).toBeCloseTo(K1, 6);

    const shrunk = resizeCropBox(start, "br", -10, -10, K1);
    expect(shrunk.w).toBeGreaterThanOrEqual(MIN_CROP_W - 1e-9);
  });

  test("resize under the 9:16 lock stays 9:16-shaped in pixels through every clamp", () => {
    const start = initialWindowForAspect("9:16", 16 / 9, FACE_CROP_1080P);
    for (const [corner, dx, dy] of [["br", 0.2, 0], ["tl", -0.4, -0.4], ["tr", 5, -5], ["bl", -0.02, 0.5]]) {
      const r = resizeCropBox(start, corner, dx, dy, K916);
      expect(r.h / r.w).toBeCloseTo(K916, 6); // lock held
      expect(r.x).toBeGreaterThanOrEqual(-1e-9);
      expect(r.y).toBeGreaterThanOrEqual(-1e-9);
      expect(r.x + r.w).toBeLessThanOrEqual(1 + 1e-9);
      expect(r.y + r.h).toBeLessThanOrEqual(1 + 1e-9);
    }
  });

  test("roundCropBox emits stable, normalized wire fractions", () => {
    const r = roundCropBox({ x: 0.1234567891, y: 0, w: 0.5, h: 1.0000001 });
    expect(r.x).toBeCloseTo(0.123457, 9);
    expect(r.h).toBe(1);
    for (const v of Object.values(r)) {
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(1);
    }
  });

  test("boxesAlmostEqual tolerance", () => {
    const a = { x: 0.1, y: 0, w: 0.5, h: 1 };
    expect(boxesAlmostEqual(a, { ...a, x: 0.1005 }, 1e-3)).toBe(true);
    expect(boxesAlmostEqual(a, { ...a, x: 0.11 }, 1e-3)).toBe(false);
    expect(boxesAlmostEqual(a, null)).toBe(false);
  });

  test("containFit models scale=W:H:force_original_aspect_ratio=decrease + centered pad", () => {
    // Wide content into a tall box → width-limited.
    const f = containFit(16 / 9, 360, 640);
    expect(f.width).toBe(360);
    expect(f.height).toBeCloseTo(360 / (16 / 9), 6);
    expect(f.top).toBeCloseTo((640 - f.height) / 2, 6);
    // Content matching the box fills it exactly.
    const g = containFit(9 / 16, 360, 640);
    expect(g.width).toBe(360);
    expect(g.height).toBe(640);
    expect(OUTPUT_ASPECTS["9:16"]).toBeCloseTo(9 / 16, 10);
  });
});
