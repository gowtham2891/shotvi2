/**
 * SPRINT 3, Parts A + C — aspect lives in the editor; background fill is
 * previewed live on the canvas.
 *
 * A: the 9:16/1:1/16:9 selector is IN the editor (Inspector Export tab +
 *    canvas toolbar); the Export page renders the choice read-only and can
 *    no longer set it. The value stays exportSettings.format, so draft
 *    persistence, undo, trim, and the export payload are unchanged.
 * C: the canvas stage takes the chosen aspect's shape and shows the same
 *    fill _apply_canvas burns (blur = stretched+blurred source clone;
 *    black/white/color = solid), behind an object-contain video.
 */
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { useAppStore } from "@/store/useAppStore";
import { createEmptyTranscriptEdits } from "@/lib/transcriptEdits";
import { buildRerenderRequest, startRerender } from "@/api/renders";
import { STAGE_DIMS, stageDimsForAspect, CanvasArea } from "@/components/editor/CanvasArea";
import Inspector from "@/components/editor/Inspector";
import Export from "@/pages/Export";
import { EDITOR, EXPORT } from "@/constants/testIds";

jest.mock("@/api/renders", () => {
  const actual = jest.requireActual("@/api/renders");
  return { ...actual, startRerender: jest.fn() };
});
jest.mock("@/api/clips", () => {
  const actual = jest.requireActual("@/api/clips");
  return { ...actual, loadDraft: jest.fn(), saveDraft: jest.fn(), generateClipMetadata: jest.fn() };
});
jest.mock("@/api/templates", () => ({
  getCaptionTemplate: jest.fn(),
  putCaptionTemplate: jest.fn(),
}));
// Export page collaborators irrelevant to the aspect contract.
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
// CanvasArea internals that aren't the stage/fill under test.
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
const PRISTINE_EXPORT = { ...useAppStore.getState().exportSettings };

const st = () => useAppStore.getState();

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
const click = async (el) =>
  act(async () => el.dispatchEvent(new MouseEvent("click", { bubbles: true })));

beforeEach(() => {
  st().resetHistory();
  useAppStore.setState({
    transcript: [],
    transcriptStatus: "ready",
    transcriptEdits: createEmptyTranscriptEdits(),
    elements: JSON.parse(JSON.stringify(PRISTINE_ELEMENTS)),
    exportSettings: { ...PRISTINE_EXPORT },
    selectedElementId: "el_caption_1",
    currentClipId: "clipA",
    currentClip: { id: "clipA", jobId: "job1", index: 0, videoUrl: "http://x/v.mp4", duration: 12 },
    currentJobId: "job1",
    duration: 12,
    currentTime: 0,
    isPlaying: false,
    draftLoadStatus: "ready",
    exportStatus: "idle",
    captionTemplate: null,
    captionTemplateSaving: false,
  });
  startRerender.mockResolvedValue("rr_1");
});

describe("A — aspect is set in the editor", () => {
  test("Inspector Export tab sets exportSettings.format; trim and the rest are untouched", async () => {
    st().setTrimRange(2, 10);
    st().endHistoryCoalescing();
    await mount(<MemoryRouter><Inspector defaultTab="export" /></MemoryRouter>);
    await click(byTestId(EDITOR.aspectBtn("1:1")));
    expect(st().exportSettings.format).toBe("1:1");
    expect(st().exportSettings).toMatchObject({ trimStart: 2, trimEnd: 10 });
    expect(st().exportSettings.background).toBe(PRISTINE_EXPORT.background);
    // Undoable like any exportSettings change.
    await act(async () => st().undo());
    expect(st().exportSettings.format).toBe("9:16");
    await unmount();
  });

  test("aspect round-trips through the draft document and reaches the export payload", async () => {
    st().setExportSetting("format", "16:9");
    const doc = st().getEditDocument();
    expect(doc.exportSettings.format).toBe("16:9");
    useAppStore.setState({ exportSettings: { ...PRISTINE_EXPORT } });
    st().applyDraft({ exportSettings: doc.exportSettings });
    expect(st().exportSettings.format).toBe("16:9");

    await st().startExport();
    expect(startRerender.mock.calls[0][2].format).toBe("16:9");
  });

  test("the Export page renders the aspect read-only — clicking cannot change it", async () => {
    await mount(
      <MemoryRouter initialEntries={["/export/clipA"]}>
        <Routes>
          <Route path="/export/:clipId" element={<Export />} />
        </Routes>
      </MemoryRouter>
    );
    const chip = byTestId(EXPORT.aspectBtn("1:1"));
    expect(chip).toBeTruthy();
    expect(chip.getAttribute("aria-disabled")).toBe("true");
    await click(chip);
    expect(st().exportSettings.format).toBe("9:16"); // unchanged
    await unmount();
  });

  test("stage dimensions follow the aspect and keep its true ratio", () => {
    expect(stageDimsForAspect("9:16")).toEqual(STAGE_DIMS["9:16"]);
    for (const [aspect, [w, h]] of [["9:16", [9, 16]], ["1:1", [1, 1]], ["16:9", [16, 9]]]) {
      const d = stageDimsForAspect(aspect);
      expect(d.w / d.h).toBeCloseTo(w / h, 2);
    }
    expect(stageDimsForAspect("junk")).toEqual(STAGE_DIMS["9:16"]); // old-draft junk → default
  });
});

describe("C — background fill previews live on the canvas", () => {
  const renderCanvas = () =>
    mount(<CanvasArea currentClip={st().currentClip} />);

  test("default blur fill renders the stretched blurred source clone", async () => {
    await renderCanvas();
    const stage = byTestId(EDITOR.canvasStage);
    expect(stage.style.width).toBe("360px"); // 9:16 default
    expect(stage.style.height).toBe("640px");
    const blur = byTestId(EDITOR.canvasFillBlur);
    expect(blur).toBeTruthy();
    expect(blur.style.objectFit).toBe("fill"); // _apply_canvas stretches, so does the preview
    expect(blur.style.filter).toMatch(/blur\(/);
    expect(byTestId(EDITOR.canvasFillColor)).toBeNull();
    await unmount();
  });

  test("solid fills render the chosen color; stage reshapes with the aspect", async () => {
    await act(async () => {
      st().setExportSetting("format", "16:9");
      st().setExportSetting("background", "color");
      st().setExportSetting("bgColor", "#123456");
    });
    await renderCanvas();
    const stage = byTestId(EDITOR.canvasStage);
    const dims = stageDimsForAspect("16:9");
    expect(stage.style.width).toBe(`${dims.w}px`);
    expect(stage.style.height).toBe(`${dims.h}px`);
    const fill = byTestId(EDITOR.canvasFillColor);
    expect(fill).toBeTruthy();
    expect(fill.style.background).toBe("rgb(18, 52, 86)"); // #123456
    expect(byTestId(EDITOR.canvasFillBlur)).toBeNull();
    await unmount();
  });

  test("white and black fills map exactly", async () => {
    await act(async () => st().setExportSetting("background", "white"));
    await renderCanvas();
    expect(byTestId(EDITOR.canvasFillColor).style.background).toBe("rgb(255, 255, 255)");
    await unmount();
    await act(async () => st().setExportSetting("background", "black"));
    await renderCanvas();
    expect(byTestId(EDITOR.canvasFillColor).style.background).toBe("rgb(0, 0, 0)");
    await unmount();
  });

  test("fill choice persists to the draft and the payload stays byte-identical on defaults", async () => {
    // Defaults: payload identical to the pre-sprint baseline.
    const req = buildRerenderRequest({});
    expect(req.format).toBe("9:16");
    expect(req.background).toBe("blur");
    expect(req.bg_color).toBe("#000000");
    expect(Object.keys(req).sort()).toEqual(
      ["background", "bg_color", "crop_mode", "format", "style", "trim_end", "trim_start", "use_autocrop"].sort()
    );

    // A chosen fill rides the same keys it always did.
    st().setExportSetting("background", "color");
    st().setExportSetting("bgColor", "#ff0000");
    const doc = st().getEditDocument();
    useAppStore.setState({ exportSettings: { ...PRISTINE_EXPORT } });
    st().applyDraft({ exportSettings: doc.exportSettings });
    expect(st().exportSettings.background).toBe("color");
    expect(st().exportSettings.bgColor).toBe("#ff0000");
    await st().startExport();
    const sent = startRerender.mock.calls[0][2];
    expect(sent.background).toBe("color");
    expect(sent.bg_color).toBe("#ff0000");
  });
});
