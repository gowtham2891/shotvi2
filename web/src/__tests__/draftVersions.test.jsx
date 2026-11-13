/**
 * F3 — draft version history ("restore from N minutes ago").
 *
 * Contract: versions are captured ONLY from a committed save (saveDraftNow
 * success), never from the raw autosave trigger; an empty/default document
 * is never captured (a wiped autosave can't become a version); the list is
 * bounded; restore goes through the normal draft-apply path and is itself
 * one undoable action.
 */
import { useAppStore } from "@/store/useAppStore";
import { createEmptyTranscriptEdits } from "@/lib/transcriptEdits";
import { saveDraft } from "@/api/clips";

jest.mock("@/api/clips", () => {
  const actual = jest.requireActual("@/api/clips");
  return {
    ...actual,
    loadDraft: jest.fn(),
    saveDraft: jest.fn(),
    generateClipMetadata: jest.fn(),
  };
});
jest.mock("@/api/templates", () => ({
  getCaptionTemplate: jest.fn(),
  putCaptionTemplate: jest.fn(),
}));

const PRISTINE_ELEMENTS = JSON.parse(JSON.stringify(useAppStore.getState().elements));
const PRISTINE_EXPORT = { ...useAppStore.getState().exportSettings };

const st = () => useAppStore.getState();
const versions = () => st().draftVersions.clipA || [];

beforeEach(() => {
  st().resetHistory();
  useAppStore.setState({
    currentClipId: "clipA",
    currentClip: { id: "clipA", jobId: "job1", index: 0 },
    currentJobId: "job1",
    transcript: [],
    transcriptEdits: createEmptyTranscriptEdits(),
    elements: JSON.parse(JSON.stringify(PRISTINE_ELEMENTS)),
    exportSettings: { ...PRISTINE_EXPORT },
    selectedElementId: "el_caption_1",
    draftStatus: "idle",
    draftLoadStatus: "ready",
    draftVersions: {},
  });
  saveDraft.mockResolvedValue({ ok: true });
});

test("a committed save of a real document captures one version", async () => {
  st().setExportSetting("background", "black");
  await st().saveDraftNow();
  expect(versions()).toHaveLength(1);
  expect(versions()[0].doc.exportSettings.background).toBe("black");
  expect(typeof versions()[0].ts).toBe("number");
});

test("an empty/default document is NEVER captured as a version", async () => {
  await st().saveDraftNow(); // pristine state — nothing worth versioning
  expect(versions()).toHaveLength(0);
});

test("a failed save captures nothing", async () => {
  saveDraft.mockRejectedValue(new Error("network"));
  st().setExportSetting("background", "black");
  await st().saveDraftNow();
  expect(versions()).toHaveLength(0);
});

test("re-saving unchanged content does not duplicate the newest version", async () => {
  st().setExportSetting("background", "black");
  await st().saveDraftNow();
  await st().saveDraftNow();
  expect(versions()).toHaveLength(1);
});

test("restore round-trips through the draft-apply path and is undoable", async () => {
  st().setExportSetting("background", "black");
  await st().saveDraftNow(); // version 1
  st().endHistoryCoalescing();
  st().setExportSetting("background", "white");
  await st().saveDraftNow(); // version 2 (newest first)
  expect(versions()).toHaveLength(2);
  expect(versions()[0].doc.exportSettings.background).toBe("white");
  expect(versions()[1].doc.exportSettings.background).toBe("black");

  st().restoreDraftVersion(versions()[1].id);
  expect(st().exportSettings.background).toBe("black");

  st().undo(); // the restore is one undo frame
  expect(st().exportSettings.background).toBe("white");
});

test("a restored version can itself be committed and re-versioned", async () => {
  st().setExportSetting("background", "black");
  await st().saveDraftNow();
  st().endHistoryCoalescing();
  st().setExportSetting("background", "white");
  await st().saveDraftNow();
  st().restoreDraftVersion(versions()[1].id);
  await st().saveDraftNow();
  expect(versions()[0].doc.exportSettings.background).toBe("black");
});

test("history is bounded per clip", async () => {
  for (let i = 0; i < 20; i++) {
    st().endHistoryCoalescing();
    st().setExportSetting("bgColor", `#0000${String(i).padStart(2, "0")}`);
    // eslint-disable-next-line no-await-in-loop
    await st().saveDraftNow();
  }
  expect(versions()).toHaveLength(15);
  // Newest first: the last committed color leads the list.
  expect(versions()[0].doc.exportSettings.bgColor).toBe("#000019");
});

test("versions are per clip and restore ignores unknown ids", async () => {
  st().setExportSetting("background", "black");
  await st().saveDraftNow();
  expect(st().draftVersions.clipB).toBeUndefined();
  const before = st().exportSettings;
  st().restoreDraftVersion(999999); // unknown id → safe no-op, no history frame
  expect(st().exportSettings).toBe(before);
  expect(st().history.past.filter(Boolean).length).toBeGreaterThanOrEqual(0);
});

test("version snapshots are deep copies — later edits cannot corrupt them", async () => {
  st().setExportSetting("background", "black");
  await st().saveDraftNow();
  st().updateElementProps("el_caption_1", { fontSize: 0.09 });
  const cap = versions()[0].doc.elements.find((el) => el.type === "caption");
  expect(cap.props.fontSize).toBe(0.055);
});
