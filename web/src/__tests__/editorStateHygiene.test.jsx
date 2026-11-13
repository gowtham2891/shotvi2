/**
 * Editor state-hygiene gate (FIX SPRINT 2, Part A — store level).
 *
 * A1 — an autosave firing while a clip's draft restore is still in flight
 *      must never PATCH the freshly-reset empty document over the persisted
 *      draft (saveDraftNow refuses while draftLoadStatus === 'loading').
 * A2 — rapid clip switching: the SLOWER openClip run's async results are
 *      discarded; they can never land on the newer clip's editor.
 * A3 — elements and exportSettings are per-clip document state: opening
 *      clip B after styling clip A starts B from clean defaults.
 */
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
import { CAPTION_DEFAULT_POSITION } from "@/api/renders";

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

const CLIP_A = { id: "clipA", jobId: "job1", index: 0, start: 0, end: 12, duration: 12, verticalPath: "", segments: [] };
const CLIP_B = { id: "clipB", jobId: "job1", index: 1, start: 20, end: 30, duration: 10, verticalPath: "", segments: [] };

const WORDS_A = [
  { id: "w_flat_0", ref: { type: "flat", index: 0 }, text: "ఒకటి", start: 0, end: 0.5 },
  { id: "w_flat_1", ref: { type: "flat", index: 1 }, text: "రెండు", start: 0.6, end: 1.4 },
];
const WORDS_B = [
  { id: "w_flat_0", ref: { type: "flat", index: 0 }, text: "మూడు", start: 0, end: 0.7 },
];

// Pristine per-clip defaults, captured at module load before any test runs.
const PRISTINE_ELEMENTS = JSON.parse(JSON.stringify(useAppStore.getState().elements));
const PRISTINE_EXPORT = { ...useAppStore.getState().exportSettings };

const st = () => useAppStore.getState();
const flush = () => new Promise((r) => setTimeout(r, 0));

const wireHappyPath = () => {
  getTranscript.mockResolvedValue({});
  getSegmentSidecar.mockResolvedValue(null);
  buildClipTranscript.mockImplementation((t, clip) => (clip.id === "clipA" ? WORDS_A : WORDS_B));
  isMultiSegmentClip.mockReturnValue(false);
  canRemapMultiSegment.mockReturnValue(false);
  getCaptionTemplate.mockResolvedValue(null);
  loadDraft.mockResolvedValue(null);
  saveDraft.mockResolvedValue({ ok: true });
};

beforeEach(() => {
  st().resetHistory();
  useAppStore.setState({
    currentClipId: null,
    currentClip: null,
    currentJobId: null,
    clipsByJob: { job1: [CLIP_A, CLIP_B] },
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
  wireHappyPath();
});

describe("A1 — autosave cannot wipe a saved draft during a slow clip open", () => {
  test("saveDraftNow refuses while the draft restore is in flight, then persists the RESTORED doc", async () => {
    let resolveDraft;
    loadDraft.mockReturnValue(new Promise((r) => { resolveDraft = r; }));

    const open = st().openClip("clipA");
    await flush(); // resolveClip + transcript settled; draft load still parked

    expect(st().currentClip?.id).toBe("clipA");
    expect(st().draftLoadStatus).toBe("loading");

    // The 2.5s debounce armed by the open-reset fires now (simulated):
    await st().saveDraftNow();
    expect(saveDraft).not.toHaveBeenCalled(); // the empty doc was NOT persisted

    // The persisted draft finally arrives and is applied.
    resolveDraft({ exportSettings: { background: "black" } });
    await open;
    expect(st().draftLoadStatus).toBe("ready");
    expect(st().exportSettings.background).toBe("black");

    // Only now may a save go through — and it carries the restored content.
    await st().saveDraftNow();
    expect(saveDraft).toHaveBeenCalledTimes(1);
    const [jobId, clipId, doc] = saveDraft.mock.calls[0];
    expect(jobId).toBe("job1");
    expect(clipId).toBe("clipA");
    expect(doc.exportSettings.background).toBe("black");
  });

  test("a confirmed no-draft clip unblocks autosave ('ready'), a FAILED load does not ('error')", async () => {
    await st().openClip("clipA"); // loadDraft resolves null
    expect(st().draftLoadStatus).toBe("ready");

    loadDraft.mockRejectedValue(new Error("redis down"));
    await st().openClip("clipB");
    expect(st().draftLoadStatus).toBe("error");
  });
});

describe("A2 — stale openClip results are discarded", () => {
  test("slow clip A landing after fast clip B applies nothing", async () => {
    let resolveTranscriptA;
    getTranscript
      .mockImplementationOnce(() => new Promise((r) => { resolveTranscriptA = r; }))
      .mockImplementation(() => Promise.resolve({}));
    loadDraft.mockImplementation((jobId, clipId) =>
      clipId === "clipB"
        ? Promise.resolve({ exportSettings: { background: "white" } })
        : new Promise(() => {}) // A's draft never arrives — A goes stale first anyway
    );

    const openA = st().openClip("clipA");
    await flush(); // A is parked on its transcript fetch, currentClip = A

    const openB = st().openClip("clipB");
    await openB;
    expect(st().currentClipId).toBe("clipB");
    expect(st().currentClip?.id).toBe("clipB");
    expect(st().transcript).toBe(WORDS_B);
    expect(st().exportSettings.background).toBe("white");
    const durationB = st().duration;

    // A's slow transcript finally lands — the stale run must apply NOTHING.
    resolveTranscriptA({});
    await openA;
    expect(st().currentClipId).toBe("clipB");
    expect(st().currentClip?.id).toBe("clipB");
    expect(st().transcript).toBe(WORDS_B);
    expect(st().duration).toBe(durationB);
    expect(st().exportSettings.background).toBe("white");
    expect(st().draftLoadStatus).toBe("ready");
  });
});

describe("A3 — no cross-clip element/exportSettings bleed", () => {
  test("opening clip B after styling clip A shows B's defaults, not A's", async () => {
    await st().openClip("clipA");
    expect(st().draftLoadStatus).toBe("ready");

    // Style clip A heavily.
    const s = st();
    s.updateElementProps("el_caption_1", { presetId: "hormozi", fontSize: 0.08 });
    s.updateElement("el_caption_1", { x: 0.2, y: 0.3 });
    s.toggleElementVisibility("el_logo_1");
    s.setExportSetting("format", "1:1");
    s.setExportSetting("background", "color");
    expect(st().elements.find((el) => el.id === "el_caption_1").props.presetId).toBe("hormozi");

    await st().openClip("clipB");
    const caption = st().elements.find((el) => el.id === "el_caption_1");
    expect(caption.props.presetId).toBe("bold-yellow");
    expect(caption.props.fontSize).toBe(0.055);
    // Asserting the DEFAULT (openClip resets a clean clip to it), so read the
    // constant rather than a literal — this follows the anchor if it moves.
    expect(caption.x).toBe(CAPTION_DEFAULT_POSITION.x);
    expect(caption.y).toBe(CAPTION_DEFAULT_POSITION.y);
    expect(st().elements.find((el) => el.id === "el_logo_1").visible).toBe(false);
    expect(st().exportSettings).toEqual(PRISTINE_EXPORT);
    // Undo history is per clip too: no frame can resurrect A's state on B.
    expect(st().history.past).toHaveLength(0);
  });

  test("clip B's OWN draft repopulates after the clean reset", async () => {
    await st().openClip("clipA");
    st().setExportSetting("format", "16:9"); // style A
    loadDraft.mockResolvedValue({ exportSettings: { format: "1:1" } }); // B's draft
    await st().openClip("clipB");
    expect(st().exportSettings.format).toBe("1:1"); // B's draft, not A's leak, not plain default
  });
});
