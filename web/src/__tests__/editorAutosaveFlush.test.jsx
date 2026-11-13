/**
 * A5 — leaving the editor flushes a pending debounced autosave, but the
 * exit-flush can never itself write an empty/default document over a good
 * draft (it only fires once the clip's draft restore reached 'ready').
 *
 * Renders the real Editor page (panels stubbed) with react-dom, since this
 * guard lives in Editor.jsx effects, not the store.
 */
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { useAppStore } from "@/store/useAppStore";
import { createEmptyTranscriptEdits } from "@/lib/transcriptEdits";
import { loadDraft, saveDraft } from "@/api/clips";
import {
  getTranscript,
  getSegmentSidecar,
  buildClipTranscript,
  isMultiSegmentClip,
  canRemapMultiSegment,
} from "@/api/transcripts";
import { getCaptionTemplate } from "@/api/templates";
import Editor from "@/pages/Editor";

jest.mock("@/api/clips", () => {
  const actual = jest.requireActual("@/api/clips");
  return {
    ...actual,
    loadDraft: jest.fn(),
    saveDraft: jest.fn(),
    generateClipMetadata: jest.fn(),
  };
});
jest.mock("@/api/transcripts", () => ({
  getTranscript: jest.fn(),
  getSegmentSidecar: jest.fn(),
  buildClipTranscript: jest.fn(),
  isMultiSegmentClip: jest.fn(),
  canRemapMultiSegment: jest.fn(),
}));
jest.mock("@/api/templates", () => ({
  getCaptionTemplate: jest.fn(),
  putCaptionTemplate: jest.fn(),
}));
// Panels are irrelevant to the autosave lifecycle — stub them out.
jest.mock("@/components/shotvi/Logo", () => ({ __esModule: true, Logo: () => null }));
jest.mock("@/components/editor/LeftClips", () => ({ __esModule: true, LeftClips: () => null, default: () => null }));
jest.mock("@/components/editor/CanvasArea", () => ({ __esModule: true, CanvasArea: () => null, default: () => null }));
jest.mock("@/components/editor/Inspector", () => ({ __esModule: true, Inspector: () => null, default: () => null }));
jest.mock("@/components/editor/TimelineRow", () => ({ __esModule: true, TimelineRow: () => null, default: () => null }));

global.IS_REACT_ACT_ENVIRONMENT = true;

const CLIP_A = { id: "clipA", jobId: "job1", index: 0, start: 0, end: 12, duration: 12, verticalPath: "", segments: [] };
const WORDS = [
  { id: "w_flat_0", ref: { type: "flat", index: 0 }, text: "ఒకటి", start: 0, end: 0.5 },
];

const PRISTINE_ELEMENTS = JSON.parse(JSON.stringify(useAppStore.getState().elements));
const PRISTINE_EXPORT = { ...useAppStore.getState().exportSettings };

const st = () => useAppStore.getState();
const flush = () => new Promise((r) => setTimeout(r, 0));

let container;
let root;

const renderEditor = async () => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={["/editor/clipA"]}>
        <Routes>
          <Route path="/editor/:clipId" element={<Editor />} />
        </Routes>
      </MemoryRouter>
    );
    await flush();
  });
};

const unmountEditor = async () => {
  await act(async () => {
    root.unmount();
    await flush();
  });
  container.remove();
};

beforeEach(() => {
  st().resetHistory();
  useAppStore.setState({
    currentClipId: null,
    currentClip: null,
    currentJobId: null,
    clipsByJob: { job1: [CLIP_A] },
    projects: [{ id: "job1", videoId: "vid1" }],
    transcript: [],
    transcriptStatus: "idle",
    transcriptEdits: createEmptyTranscriptEdits(),
    elements: JSON.parse(JSON.stringify(PRISTINE_ELEMENTS)),
    exportSettings: { ...PRISTINE_EXPORT },
    selectedElementId: "el_caption_1",
    draftStatus: "idle",
    draftLoadStatus: "idle",
    draftVersions: {},
    captionTemplate: null,
    captionTemplateLoaded: false,
    duration: 0,
    currentTime: 0,
  });
  getTranscript.mockResolvedValue({});
  getSegmentSidecar.mockResolvedValue(null);
  buildClipTranscript.mockReturnValue(WORDS);
  isMultiSegmentClip.mockReturnValue(false);
  canRemapMultiSegment.mockReturnValue(false);
  getCaptionTemplate.mockResolvedValue(null);
  saveDraft.mockResolvedValue({ ok: true });
});

test("unmount flushes a pending (not-yet-debounced) save carrying the real edits", async () => {
  loadDraft.mockResolvedValue({ exportSettings: { background: "black" } });
  await renderEditor();
  expect(st().draftLoadStatus).toBe("ready");
  saveDraft.mockClear(); // ignore any echo save scheduled by the restore itself

  // A real user edit, then exit BEFORE the 2.5s debounce fires.
  await act(async () => {
    st().updateElementProps("el_caption_1", { fontSize: 0.08 });
    await flush();
  });
  await unmountEditor();

  expect(saveDraft).toHaveBeenCalledTimes(1);
  const doc = saveDraft.mock.calls[0][2];
  const caption = doc.elements.find((el) => el.id === "el_caption_1");
  expect(caption.props.fontSize).toBe(0.08); // the pending edit survived exit
  expect(doc.exportSettings.background).toBe("black"); // …on top of the restored draft
});

test("exit while the draft is still loading flushes NOTHING (never an empty doc)", async () => {
  loadDraft.mockReturnValue(new Promise(() => {})); // restore never completes
  await renderEditor();
  expect(st().draftLoadStatus).toBe("loading");

  await unmountEditor();
  expect(saveDraft).not.toHaveBeenCalled();
});

test("beforeunload flushes the pending save once; unmount does not double-save", async () => {
  loadDraft.mockResolvedValue(null); // confirmed no-draft → autosave armed
  await renderEditor();
  expect(st().draftLoadStatus).toBe("ready");
  saveDraft.mockClear();

  await act(async () => {
    st().setExportSetting("format", "1:1");
    await flush();
  });
  await act(async () => {
    window.dispatchEvent(new Event("beforeunload"));
    await flush();
  });
  expect(saveDraft).toHaveBeenCalledTimes(1);
  expect(saveDraft.mock.calls[0][2].exportSettings.format).toBe("1:1");

  await unmountEditor();
  expect(saveDraft).toHaveBeenCalledTimes(1); // dirty flag already consumed
});
