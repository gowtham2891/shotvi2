/**
 * F1 — clip boundary trim.
 *
 * trimStart/trimEnd live in exportSettings (draft-persisted, undoable) with
 * the backend's sentinels (0 / -1 = untrimmed). setTrimRange owns clamping:
 * the backend re-cuts the clip's OWN file, so the valid window is
 * [0, duration] with a minimum kept length. startExport threads the values
 * into the rerender payload via the existing renders.js contract.
 */
import { useAppStore } from "@/store/useAppStore";
import { createEmptyTranscriptEdits } from "@/lib/transcriptEdits";
import { buildRerenderRequest, startRerender } from "@/api/renders";

jest.mock("@/api/renders", () => {
  const actual = jest.requireActual("@/api/renders");
  return { ...actual, startRerender: jest.fn() };
});

const PRISTINE_ELEMENTS = JSON.parse(JSON.stringify(useAppStore.getState().elements));
const PRISTINE_EXPORT = { ...useAppStore.getState().exportSettings };

const st = () => useAppStore.getState();

beforeEach(() => {
  st().resetHistory();
  useAppStore.setState({
    transcript: [],
    transcriptEdits: createEmptyTranscriptEdits(),
    elements: JSON.parse(JSON.stringify(PRISTINE_ELEMENTS)),
    exportSettings: { ...PRISTINE_EXPORT },
    selectedElementId: "el_caption_1",
    duration: 12,
    currentTime: 0,
    currentClip: { id: "clipA", jobId: "job1", index: 0 },
    currentClipId: "clipA",
    currentJobId: "job1",
    draftLoadStatus: "ready",
    exportStatus: "idle",
  });
  startRerender.mockResolvedValue("rr_job_1");
});

describe("defaults and sentinels", () => {
  test("untrimmed defaults are the backend sentinels 0 / -1", () => {
    expect(st().exportSettings.trimStart).toBe(0);
    expect(st().exportSettings.trimEnd).toBe(-1);
    expect(st().isTrimmed()).toBe(false);
    expect(st().getTrimBounds()).toEqual({ start: 0, end: 12 });
  });

  test("values at the clip edges store back as sentinels", () => {
    st().setTrimRange(2, 10);
    expect(st().exportSettings).toMatchObject({ trimStart: 2, trimEnd: 10 });
    st().setTrimRange(0, 12); // dragged back to the edges
    expect(st().exportSettings).toMatchObject({ trimStart: 0, trimEnd: -1 });
    expect(st().isTrimmed()).toBe(false);
  });
});

describe("clamping", () => {
  test("out-of-range values clamp to the clip's own bounds", () => {
    st().setTrimRange(-5, 50);
    expect(st().exportSettings).toMatchObject({ trimStart: 0, trimEnd: -1 });
  });

  test("a minimum kept length is enforced (no zero-length export)", () => {
    st().setTrimRange(11.9, 12);
    const { trimStart } = st().exportSettings;
    expect(trimStart).toBeLessThanOrEqual(11.5);
    const { start, end } = st().getTrimBounds();
    expect(end - start).toBeGreaterThanOrEqual(0.5);
  });

  test("end can never cross start", () => {
    st().setTrimRange(6, 3);
    const { start, end } = st().getTrimBounds();
    expect(end).toBeGreaterThan(start);
  });

  test("no-duration clip: setTrimRange is a safe no-op", () => {
    useAppStore.setState({ duration: 0 });
    st().setTrimRange(1, 2);
    expect(st().exportSettings).toMatchObject({ trimStart: 0, trimEnd: -1 });
    expect(st().history.past).toHaveLength(0);
  });
});

describe("playhead + playback window", () => {
  test("seek clamps into the trimmed window", () => {
    st().setTrimRange(2, 10);
    st().seek(0);
    expect(st().currentTime).toBe(2);
    st().seek(999);
    expect(st().currentTime).toBe(10);
    st().seek(5);
    expect(st().currentTime).toBe(5);
  });

  test("trimming moves an out-of-window playhead back inside", () => {
    st().seek(1);
    st().setTrimRange(4, 10);
    expect(st().currentTime).toBe(4);
  });
});

describe("undo / persistence", () => {
  test("a handle drag (many ticks) coalesces into ONE undo frame", () => {
    for (let i = 1; i <= 15; i++) st().setTrimRange(i * 0.2, 12);
    st().endHistoryCoalescing();
    expect(st().history.past).toHaveLength(1);
    st().undo();
    expect(st().exportSettings).toMatchObject({ trimStart: 0, trimEnd: -1 });
  });

  test("resetTrim restores the sentinels and is one undoable action", () => {
    st().setTrimRange(2, 10);
    st().endHistoryCoalescing();
    st().resetTrim();
    expect(st().exportSettings).toMatchObject({ trimStart: 0, trimEnd: -1 });
    st().undo();
    expect(st().exportSettings).toMatchObject({ trimStart: 2, trimEnd: 10 });
  });

  test("trim round-trips through the draft document", () => {
    st().setTrimRange(1.5, 9);
    const doc = st().getEditDocument();
    expect(doc.exportSettings.trimStart).toBe(1.5);
    expect(doc.exportSettings.trimEnd).toBe(9);
    // Fresh clip state, then a draft reload restores the trim.
    useAppStore.setState({ exportSettings: { ...PRISTINE_EXPORT } });
    st().applyDraft({ exportSettings: doc.exportSettings });
    expect(st().exportSettings).toMatchObject({ trimStart: 1.5, trimEnd: 9 });
  });

  test("an old draft without trim keys keeps the untrimmed defaults", () => {
    st().applyDraft({ exportSettings: { background: "black" } });
    expect(st().exportSettings).toMatchObject({ trimStart: 0, trimEnd: -1 });
  });
});

describe("export payload", () => {
  test("trim_start/trim_end reach the rerender request", async () => {
    st().setTrimRange(2, 10);
    await st().startExport();
    expect(startRerender).toHaveBeenCalledTimes(1);
    const [jobId, clipIndex, req] = startRerender.mock.calls[0];
    expect(jobId).toBe("job1");
    expect(clipIndex).toBe(0);
    expect(req.trim_start).toBe(2);
    expect(req.trim_end).toBe(10);
  });

  test("untrimmed export payload is byte-identical to the pre-trim-feature one", async () => {
    await st().startExport();
    const req = startRerender.mock.calls[0][2];
    expect(req.trim_start).toBe(0);
    expect(req.trim_end).toBe(-1);
  });

  test("buildRerenderRequest passes trim values through unchanged", () => {
    const req = buildRerenderRequest({ trimStart: 3.25, trimEnd: 8.5 });
    expect(req.trim_start).toBe(3.25);
    expect(req.trim_end).toBe(8.5);
  });
});
