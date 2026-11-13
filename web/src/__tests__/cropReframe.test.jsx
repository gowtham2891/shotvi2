/**
 * SPRINT 4 — crop-window store state, drag-to-reframe UI, and THE
 * BYTE-IDENTICAL EXPORT GATE.
 *
 * The gate (most important): a clip exported at 9:16 with an UNTOUCHED crop
 * window must produce the EXACT pre-sprint payload — crop_mode 'auto', no
 * crop_box, the same key set — so the worker keeps selecting vertical_path
 * and the output bytes cannot change. Touched window OR non-9:16 aspect →
 * crop_mode 'manual' + crop_box over the 16:9 master.
 */
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { useAppStore } from "@/store/useAppStore";
import { createEmptyTranscriptEdits } from "@/lib/transcriptEdits";
import { startRerender } from "@/api/renders";
import { CanvasArea, STAGE_DIMS } from "@/components/editor/CanvasArea";
import Export from "@/pages/Export";
import { cropVideoLayout, initialWindowForAspect } from "@/lib/cropWindow";
import { collectCoordinateViolations } from "@/lib/editDocumentValidation";
import { EDITOR, EXPORT } from "@/constants/testIds";

jest.mock("@/api/renders", () => {
  const actual = jest.requireActual("@/api/renders");
  return { ...actual, startRerender: jest.fn() };
});
jest.mock("@/components/editor/CanvasToolbar", () => ({
  __esModule: true,
  CanvasToolbar: () => null,
}));
jest.mock("@/components/editor/SmartGuides", () => ({ __esModule: true, SmartGuides: () => null }));
jest.mock("@/components/editor/SafeZoneOverlay", () => ({ __esModule: true, SafeZoneOverlay: () => null }));
jest.mock("@/components/editor/ElementRenderer", () => ({
  __esModule: true,
  ElementRenderer: () => null,
}));
// Export-page collaborators irrelevant to the crop-preview contract.
jest.mock("@/components/shotvi/AppShell", () => ({
  __esModule: true,
  AppShell: ({ children }) => <div>{children}</div>,
}));
jest.mock("@/hooks/useJobPolling", () => ({
  __esModule: true,
  useJobPolling: () => ({ error: null }),
}));
jest.mock("@/components/editor/StaticElementLayer", () => ({
  __esModule: true,
  StaticElementLayer: () => null,
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

beforeAll(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  Object.defineProperty(HTMLMediaElement.prototype, "play", {
    configurable: true,
    value: () => Promise.resolve(),
  });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", {
    configurable: true,
    value: () => {},
  });
});

const PRISTINE_ELEMENTS = JSON.parse(JSON.stringify(useAppStore.getState().elements));
const PRISTINE_EXPORT = JSON.parse(JSON.stringify(useAppStore.getState().exportSettings));

// The pipeline's real face-crop on a 1920x1080 master (int-truncated 607px
// slice at x=1197), exactly as the cropper persists it.
const FACE_CROP = { x: 1197 / 1920, y: 0, w: 607 / 1920, h: 1 };

const BASE_CLIP = {
  id: "clipA",
  jobId: "job1",
  index: 0,
  videoUrl: "http://x/raw-master.mp4",
  rawPath: "storage/outputs/vid_clip1_t.mp4",
  verticalPath: "storage/outputs/vid_clip1_t_vertical.mp4",
  defaultCropBox: FACE_CROP,
  duration: 12,
};

const st = () => useAppStore.getState();

beforeEach(() => {
  st().resetHistory();
  useAppStore.setState({
    transcript: [],
    transcriptStatus: "ready",
    transcriptEdits: createEmptyTranscriptEdits(),
    elements: JSON.parse(JSON.stringify(PRISTINE_ELEMENTS)),
    exportSettings: JSON.parse(JSON.stringify(PRISTINE_EXPORT)),
    selectedElementId: "el_caption_1",
    currentClipId: "clipA",
    currentClip: { ...BASE_CLIP },
    currentJobId: "job1",
    duration: 12,
    currentTime: 0,
    isPlaying: false,
    draftLoadStatus: "ready",
    exportStatus: "idle",
    masterDims: { w: 1920, h: 1080 },
    reframeMode: false,
  });
  startRerender.mockReset();
  startRerender.mockResolvedValue("rr_1");
});

// ═══════════════════ THE BYTE-IDENTICAL GATE ═══════════════════

describe("byte-identical export gate", () => {
  test("UNTOUCHED window + 9:16 → the exact pre-sprint payload (no crop_box, crop_mode auto)", async () => {
    await st().startExport();
    const sent = startRerender.mock.calls[0][2];
    expect(sent.format).toBe("9:16");
    expect(sent.crop_mode).toBe("auto");
    expect(sent.use_autocrop).toBe(true);
    expect(sent).not.toHaveProperty("crop_box");
    // Pin the ENTIRE key set to what startExport sent for a pristine
    // document BEFORE this sprint — any new key on the untouched path would
    // break byte-identical output. (caption_font_size/caption_pill are the
    // pristine caption element's own defaults, serialized pre-sprint too.)
    expect(Object.keys(sent).sort()).toEqual(
      ["background", "bg_color", "caption_font_size", "caption_pill", "crop_mode",
       "format", "style", "trim_end", "trim_start", "use_autocrop"].sort()
    );
  });

  test("dragging the window back onto the default re-engages the byte-identical path", async () => {
    st().setCropWindow("9:16", { ...FACE_CROP, x: FACE_CROP.x - 0.2 });
    st().endHistoryCoalescing();
    expect(st().isCropTouched("9:16")).toBe(true);
    st().setCropWindow("9:16", { ...FACE_CROP });
    st().endHistoryCoalescing();
    expect(st().isCropTouched("9:16")).toBe(false);

    await st().startExport();
    const sent = startRerender.mock.calls[0][2];
    expect(sent.crop_mode).toBe("auto");
    expect(sent).not.toHaveProperty("crop_box");
  });

  test("TOUCHED 9:16 window → crop_mode manual + the stored window over the master", async () => {
    const moved = { ...FACE_CROP, x: 0.1 };
    st().setCropWindow("9:16", moved);
    st().endHistoryCoalescing();

    await st().startExport();
    const sent = startRerender.mock.calls[0][2];
    expect(sent.crop_mode).toBe("manual");
    expect(sent.crop_box.x).toBeCloseTo(0.1, 5);
    expect(sent.crop_box.w).toBeCloseTo(FACE_CROP.w, 5);
    expect(sent.crop_box.h).toBe(1);
    // Normalized wire fractions, never pixels.
    for (const v of Object.values(sent.crop_box)) {
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(1);
    }
  });

  test("non-9:16 aspect ALWAYS exports through the master + crop window (even untouched)", async () => {
    st().setExportSetting("format", "16:9");
    await st().startExport();
    const sent = startRerender.mock.calls[0][2];
    expect(sent.format).toBe("16:9");
    expect(sent.crop_mode).toBe("manual");
    // 16:9 over the 16:9 master, untouched → the full frame.
    expect(sent.crop_box).toEqual({ x: 0, y: 0, w: 1, h: 1 });
  });

  test("1:1 untouched default: full-height square centred on the face", async () => {
    st().setExportSetting("format", "1:1");
    await st().startExport();
    const sent = startRerender.mock.calls[0][2];
    expect(sent.crop_mode).toBe("manual");
    expect(sent.crop_box.h).toBe(1);
    expect(sent.crop_box.w).toBeCloseTo(9 / 16, 5);
    const faceCx = FACE_CROP.x + FACE_CROP.w / 2;
    const expectedX = Math.min(Math.max(faceCx - 9 / 32, 0), 1 - 9 / 16);
    expect(sent.crop_box.x).toBeCloseTo(expectedX, 5);
  });
});

// ═══════════════════ Store state: clamp, undo, draft, reset ═══════════════════

describe("crop-window store state", () => {
  test("getEffectiveCropWindow for an untouched 9:16 clip IS default_crop_box", () => {
    expect(st().getEffectiveCropWindow("9:16")).toEqual(FACE_CROP);
    expect(st().isCropTouched("9:16")).toBe(false);
  });

  test("setCropWindow clamps out-of-bounds drags to valid bounds", () => {
    st().setCropWindow("9:16", { ...FACE_CROP, x: 5 });
    const w = st().exportSettings.cropWindows["9:16"];
    expect(w.x).toBeCloseTo(1 - FACE_CROP.w, 6);
    st().setCropWindow("9:16", { ...FACE_CROP, x: -5 });
    expect(st().exportSettings.cropWindows["9:16"].x).toBe(0);
  });

  test("a drag gesture is ONE undo frame; undo restores the previous window", () => {
    const before = st().getEffectiveCropWindow("9:16");
    // Coalesced burst — like pointermove ticks during one drag.
    st().setCropWindow("9:16", { ...FACE_CROP, x: 0.3 });
    st().setCropWindow("9:16", { ...FACE_CROP, x: 0.2 });
    st().setCropWindow("9:16", { ...FACE_CROP, x: 0.1 });
    st().endHistoryCoalescing();

    expect(st().getEffectiveCropWindow("9:16").x).toBeCloseTo(0.1, 6);
    st().undo();
    // One undo unwinds the whole gesture back to the untouched default.
    expect(st().getEffectiveCropWindow("9:16")).toEqual(before);
    expect(st().isCropTouched("9:16")).toBe(false);
  });

  test("windows are PER ASPECT — touching 1:1 leaves 9:16 untouched", () => {
    st().setCropWindow("1:1", { x: 0.1, y: 0, w: 0.5625, h: 1 });
    st().endHistoryCoalescing();
    expect(st().isCropTouched("1:1")).toBe(true);
    expect(st().isCropTouched("9:16")).toBe(false);
    expect(st().exportSettings.cropWindows).not.toHaveProperty("9:16");
  });

  test("crop windows round-trip through the draft document", () => {
    const moved = { ...FACE_CROP, x: 0.05 };
    st().setCropWindow("9:16", moved);
    st().endHistoryCoalescing();
    const doc = st().getEditDocument();
    expect(doc.exportSettings.cropWindows["9:16"].x).toBeCloseTo(0.05, 6);
    // The draft payload must not alias live store state.
    expect(doc.exportSettings.cropWindows).not.toBe(st().exportSettings.cropWindows);

    useAppStore.setState({ exportSettings: JSON.parse(JSON.stringify(PRISTINE_EXPORT)) });
    expect(st().isCropTouched("9:16")).toBe(false);
    st().applyDraft({ exportSettings: doc.exportSettings });
    expect(st().getEffectiveCropWindow("9:16").x).toBeCloseTo(0.05, 6);
    expect(st().isCropTouched("9:16")).toBe(true);
  });

  test("an OLD draft without cropWindows restores as fully untouched", () => {
    st().applyDraft({ exportSettings: { format: "9:16", background: "blur" } });
    expect(st().exportSettings.cropWindows).toEqual({});
    expect(st().isCropTouched("9:16")).toBe(false);
  });

  test("resetCropWindow restores the default and is undoable", () => {
    st().setCropWindow("9:16", { ...FACE_CROP, x: 0.1 });
    st().endHistoryCoalescing();
    st().resetCropWindow("9:16");
    expect(st().isCropTouched("9:16")).toBe(false);
    expect(st().getEffectiveCropWindow("9:16")).toEqual(FACE_CROP);
    st().undo();
    expect(st().getEffectiveCropWindow("9:16").x).toBeCloseTo(0.1, 6);
  });

  test("coordinate validation rejects pixel-valued crop windows", () => {
    expect(
      collectCoordinateViolations({
        elements: [],
        exportSettings: { cropWindows: { "9:16": { x: 120, y: 0, w: 0.5, h: 1 } } },
      })
    ).toHaveLength(1);
    expect(
      collectCoordinateViolations({
        elements: [],
        exportSettings: { cropWindows: { "9:16": FACE_CROP } },
      })
    ).toHaveLength(0);
  });
});

// ═══════════════════ Canvas crop simulation (DOM) ═══════════════════

let container;
let root;
const mount = async (jsx) => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(jsx));
};
const unmount = async () => {
  await act(async () => root.unmount());
  container.remove();
};
const byTestId = (id) => container.querySelector(`[data-testid="${id}"]`);

describe("canvas crop simulation", () => {
  test("untouched 9:16: the crop viewport shows the AI window filling the stage (≡ old vertical view)", async () => {
    await mount(<CanvasArea currentClip={st().currentClip} />);
    const vp = byTestId(EDITOR.cropViewport);
    expect(vp).toBeTruthy();
    const layout = cropVideoLayout(FACE_CROP, STAGE_DIMS["9:16"].w, STAGE_DIMS["9:16"].h, 1920 / 1080);
    // Sub-pixel letterbox only — the AI window fills the 9:16 stage like the
    // baked vertical did.
    expect(parseFloat(vp.style.width)).toBeCloseTo(layout.box.width, 3);
    expect(parseFloat(vp.style.height)).toBeCloseTo(STAGE_DIMS["9:16"].h, 3);
    expect(Math.abs(parseFloat(vp.style.left))).toBeLessThan(0.5);

    // The master <video> inside is offset so the window's region fills it.
    const video = byTestId(EDITOR.canvasVideo);
    expect(video.getAttribute("src")).toBe(BASE_CLIP.videoUrl); // the MASTER
    expect(parseFloat(video.style.width)).toBeCloseTo(layout.video.width, 3);
    expect(parseFloat(video.style.left)).toBeCloseTo(layout.video.left, 3);
    expect(video.style.objectFit).toBe("fill");
    await unmount();
  });

  test("blur fill clone shows the CROPPED region stretched over the stage", async () => {
    await mount(<CanvasArea currentClip={st().currentClip} />);
    const blur = byTestId(EDITOR.canvasFillBlur);
    expect(blur).toBeTruthy();
    expect(blur.style.objectFit).toBe("fill");
    expect(blur.style.filter).toMatch(/blur\(/);
    // The window's region spans the full stage width inside the clone.
    expect(parseFloat(blur.style.width) * FACE_CROP.w).toBeCloseTo(STAGE_DIMS["9:16"].w, 3);
    expect(-parseFloat(blur.style.left)).toBeCloseTo(FACE_CROP.x * parseFloat(blur.style.width), 3);
    await unmount();
  });

  test("a dragged window re-lays-out the simulation to the new region", async () => {
    const moved = { ...FACE_CROP, x: 0.05 };
    await act(async () => {
      st().setCropWindow("9:16", moved);
      st().endHistoryCoalescing();
    });
    await mount(<CanvasArea currentClip={st().currentClip} />);
    const video = byTestId(EDITOR.canvasVideo);
    const layout = cropVideoLayout(moved, STAGE_DIMS["9:16"].w, STAGE_DIMS["9:16"].h, 1920 / 1080);
    expect(parseFloat(video.style.left)).toBeCloseTo(layout.video.left, 3);
    await unmount();
  });

  test("reframe mode: master contain-fitted, crop rect + handles, Done exits, Reset restores default", async () => {
    await act(async () => st().setReframeMode(true));
    await mount(<CanvasArea currentClip={st().currentClip} />);

    expect(byTestId(EDITOR.cropRect)).toBeTruthy();
    for (const c of ["tl", "tr", "bl", "br"]) {
      expect(byTestId(EDITOR.cropHandle(c))).toBeTruthy();
    }
    // Untouched → Reset disabled.
    expect(byTestId(EDITOR.reframeReset).disabled).toBe(true);

    // Touch the window → Reset enables and restores the default.
    await act(async () => {
      st().setCropWindow("9:16", { ...FACE_CROP, x: 0.1 });
      st().endHistoryCoalescing();
    });
    const reset = byTestId(EDITOR.reframeReset);
    expect(reset.disabled).toBe(false);
    await act(async () => reset.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(st().isCropTouched("9:16")).toBe(false);

    // Done exits reframe mode.
    await act(async () =>
      byTestId(EDITOR.reframeDone).dispatchEvent(new MouseEvent("click", { bubbles: true }))
    );
    expect(st().reframeMode).toBe(false);
    await unmount();
  });

  test("loadedmetadata records the master's true dimensions in the store", async () => {
    useAppStore.setState({ masterDims: null });
    await mount(<CanvasArea currentClip={st().currentClip} />);
    const video = byTestId(EDITOR.canvasVideo);
    Object.defineProperty(video, "videoWidth", { configurable: true, value: 1280 });
    Object.defineProperty(video, "videoHeight", { configurable: true, value: 720 });
    await act(async () => video.dispatchEvent(new Event("loadedmetadata")));
    expect(st().masterDims).toEqual({ w: 1280, h: 720 });
    await unmount();
  });

  test("legacy clip (no defaultCropBox, no metadata): centred default window still renders", async () => {
    await act(async () => {
      useAppStore.setState({
        currentClip: { ...BASE_CLIP, defaultCropBox: null },
        masterDims: null,
      });
    });
    await mount(<CanvasArea currentClip={st().currentClip} />);
    const vp = byTestId(EDITOR.cropViewport);
    const expected = initialWindowForAspect("9:16", 16 / 9, null);
    const layout = cropVideoLayout(expected, STAGE_DIMS["9:16"].w, STAGE_DIMS["9:16"].h, 16 / 9);
    expect(parseFloat(vp.style.width)).toBeCloseTo(layout.box.width, 3);
    await unmount();
  });
});

// ═══════════════════ Export page live-draft preview (regression) ═══════════════════
//
// BUG: after Sprint 4 repointed videoUrl at the 16:9 master, the Export
// page's "Preview (live draft)" rendered it object-contain — the full
// landscape master letterboxed into the 9:16 box — instead of the cropped
// vertical. Preview-only (the burn selects vertical_path/raw_path in the
// worker, pinned above), but the preview must run the SAME crop simulation
// as the editor canvas.

describe("Export page live-draft preview crops the master", () => {
  const mountExport = () =>
    mount(
      <MemoryRouter initialEntries={["/export/clipA"]}>
        <Routes>
          <Route path="/export/:clipId" element={<Export />} />
        </Routes>
      </MemoryRouter>
    );

  // The preview box measures 0 in jsdom → falls back to canvasH 640, and its
  // width derives from the aspect: 9:16 → 360 (same numbers as the stage).
  const PREVIEW_H = 640;

  test("9:16 live draft shows the AI crop window filling the box — NOT the letterboxed master", async () => {
    await mountExport();
    const video = byTestId(EXPORT.livePreviewVideo);
    expect(video).toBeTruthy();
    expect(video.getAttribute("src")).toBe(BASE_CLIP.videoUrl); // the master…
    expect(video.style.objectFit).toBe("fill"); // …but never object-contain

    const previewW = PREVIEW_H * (9 / 16);
    const layout = cropVideoLayout(FACE_CROP, previewW, PREVIEW_H, 1920 / 1080);
    const vp = byTestId(EXPORT.livePreviewViewport);
    // The crop viewport fills the 9:16 box (sub-pixel letterbox only)…
    expect(parseFloat(vp.style.width)).toBeCloseTo(layout.box.width, 3);
    expect(parseFloat(vp.style.height)).toBeCloseTo(PREVIEW_H, 3);
    expect(Math.abs(parseFloat(vp.style.left))).toBeLessThan(0.5);
    // …and the master inside is offset so the window's region is what shows.
    expect(parseFloat(video.style.width)).toBeCloseTo(layout.video.width, 3);
    expect(parseFloat(video.style.left)).toBeCloseTo(layout.video.left, 3);
    // No "bars fill at render" note — the aspect-locked window leaves none.
    expect(container.textContent).not.toMatch(/Bars fill with/);
    await unmount();
  });

  test("a dragged window changes what the Export preview shows (parity with the canvas)", async () => {
    const moved = { ...FACE_CROP, x: 0.05 };
    await act(async () => {
      st().setCropWindow("9:16", moved);
      st().endHistoryCoalescing();
    });
    await mountExport();
    const video = byTestId(EXPORT.livePreviewVideo);
    const previewW = PREVIEW_H * (9 / 16);
    const layout = cropVideoLayout(moved, previewW, PREVIEW_H, 1920 / 1080);
    expect(parseFloat(video.style.left)).toBeCloseTo(layout.video.left, 3);
    await unmount();
  });

  test("16:9 live draft shows the full master frame edge to edge", async () => {
    await act(async () => st().setExportSetting("format", "16:9"));
    await mountExport();
    const video = byTestId(EXPORT.livePreviewVideo);
    const vp = byTestId(EXPORT.livePreviewViewport);
    const previewW = PREVIEW_H * (16 / 9);
    // Untouched 16:9 over the 16:9 master = full frame: viewport IS the box
    // and the video fills it exactly (no offset, no letterbox).
    expect(parseFloat(vp.style.width)).toBeCloseTo(previewW, 3);
    expect(parseFloat(vp.style.height)).toBeCloseTo(PREVIEW_H, 3);
    expect(parseFloat(video.style.width)).toBeCloseTo(previewW, 3);
    expect(parseFloat(video.style.left)).toBeCloseTo(0, 3);
    await unmount();
  });
});
