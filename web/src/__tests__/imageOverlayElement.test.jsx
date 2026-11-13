/**
 * SPRINT 3, Part B (frontend) — user image overlays ride the EXISTING
 * element system: addImageOverlay uploads then addElement("image") with the
 * opaque image_id; the element drags/scales/deletes/undoes like any other,
 * persists in the draft (sanitizeDraftElements knows the type), and
 * serializes into the rerender payload through the same OVERLAY_ELEMENT_TYPES
 * path as progress/logo/headline. Multiple image elements are allowed.
 */
import { useAppStore } from "@/store/useAppStore";
import { createEmptyTranscriptEdits } from "@/lib/transcriptEdits";
import { buildRerenderRequest, startRerender } from "@/api/renders";
import { uploadOverlayImage } from "@/api/overlays";

jest.mock("@/api/overlays", () => ({
  uploadOverlayImage: jest.fn(),
}));
jest.mock("@/api/renders", () => {
  const actual = jest.requireActual("@/api/renders");
  return { ...actual, startRerender: jest.fn() };
});

const PRISTINE_ELEMENTS = JSON.parse(JSON.stringify(useAppStore.getState().elements));
const PRISTINE_EXPORT = { ...useAppStore.getState().exportSettings };

const st = () => useAppStore.getState();
const imageElements = () => st().elements.filter((el) => el.type === "image");
const IMAGE_ID = "vid12345_useroverlay_deadbeef.png";

beforeEach(() => {
  st().resetHistory();
  useAppStore.setState({
    transcript: [],
    transcriptEdits: createEmptyTranscriptEdits(),
    elements: JSON.parse(JSON.stringify(PRISTINE_ELEMENTS)),
    exportSettings: { ...PRISTINE_EXPORT },
    selectedElementId: "el_caption_1",
    currentClipId: "clipA",
    currentClip: { id: "clipA", jobId: "job1", index: 0 },
    currentJobId: "job1",
    draftLoadStatus: "ready",
    exportStatus: "idle",
  });
  uploadOverlayImage.mockResolvedValue({ image_id: IMAGE_ID, path: `storage/outputs/${IMAGE_ID}` });
  startRerender.mockResolvedValue("rr_1");
});

describe("adding an image overlay", () => {
  test("uploads then adds a selected image element carrying the opaque image_id", async () => {
    const file = new File(["x"], "logo.png", { type: "image/png" });
    const id = await st().addImageOverlay(file);
    expect(uploadOverlayImage).toHaveBeenCalledWith("job1", file);
    const el = st().elements.find((e) => e.id === id);
    expect(el.type).toBe("image");
    expect(el.props.image_id).toBe(IMAGE_ID);
    expect(el.props.height).toBe(0.18);
    expect(el.props.opacity).toBe(1);
    expect(el.x).toBe(0.5);
    expect(el.y).toBe(0.5);
    expect(st().selectedElementId).toBe(id);
    // One undoable action.
    st().undo();
    expect(imageElements()).toHaveLength(0);
  });

  test("multiple image overlays coexist (no forced limit)", async () => {
    await st().addImageOverlay(new File(["a"], "a.png", { type: "image/png" }));
    uploadOverlayImage.mockResolvedValue({ image_id: "vid12345_useroverlay_beefdead.jpg" });
    await st().addImageOverlay(new File(["b"], "b.jpg", { type: "image/jpeg" }));
    expect(imageElements()).toHaveLength(2);
    const ids = imageElements().map((e) => e.props.image_id);
    expect(new Set(ids).size).toBe(2);
  });

  test("a failed upload adds nothing", async () => {
    uploadOverlayImage.mockRejectedValue(new Error("413"));
    const id = await st().addImageOverlay(new File(["x"], "big.png", { type: "image/png" }));
    expect(id).toBeNull();
    expect(imageElements()).toHaveLength(0);
    expect(st().history.past).toHaveLength(0); // no phantom frame
  });

  test("without an open clip nothing uploads", async () => {
    useAppStore.setState({ currentJobId: null });
    const id = await st().addImageOverlay(new File(["x"], "a.png", { type: "image/png" }));
    expect(id).toBeNull();
    expect(uploadOverlayImage).not.toHaveBeenCalled();
  });
});

describe("editing like any other element", () => {
  test("position/size/opacity edit, coalesce, and undo through the shared paths", async () => {
    const id = await st().addImageOverlay(new File(["x"], "a.png", { type: "image/png" }));
    st().endHistoryCoalescing();
    st().updateElement(id, { x: 0.2, y: 0.8 });
    st().endHistoryCoalescing();
    st().updateElementProps(id, { opacity: 0.5, height: 0.3 });
    st().endHistoryCoalescing();
    const el = st().elements.find((e) => e.id === id);
    expect(el).toMatchObject({ x: 0.2, y: 0.8 });
    expect(el.props).toMatchObject({ opacity: 0.5, height: 0.3 });
    st().undo();
    expect(st().elements.find((e) => e.id === id).props.opacity).toBe(1);
  });

  test("removeElement deletes an image overlay (non-caption rule)", async () => {
    const id = await st().addImageOverlay(new File(["x"], "a.png", { type: "image/png" }));
    st().removeElement(id);
    expect(imageElements()).toHaveLength(0);
  });
});

describe("draft round-trip + export payload", () => {
  test("image elements survive getEditDocument → applyDraft (sanitizer knows the type)", async () => {
    const id = await st().addImageOverlay(new File(["x"], "a.png", { type: "image/png" }));
    st().updateElement(id, { x: 0.3, y: 0.7, scale: 1.5 });
    st().updateElementProps(id, { opacity: 0.6 });
    const doc = st().getEditDocument();

    useAppStore.setState({ elements: JSON.parse(JSON.stringify(PRISTINE_ELEMENTS)) });
    st().applyDraft({ elements: doc.elements });
    const restored = imageElements()[0];
    expect(restored).toMatchObject({ x: 0.3, y: 0.7, scale: 1.5 });
    expect(restored.props).toMatchObject({ image_id: IMAGE_ID, opacity: 0.6 });
  });

  test("visible image elements reach req.elements; hidden ones don't", async () => {
    const id = await st().addImageOverlay(new File(["x"], "a.png", { type: "image/png" }));
    await st().startExport();
    let sent = startRerender.mock.calls[0][2];
    const sentImage = sent.elements.find((e) => e.type === "image");
    expect(sentImage.props.image_id).toBe(IMAGE_ID);
    // 0-1 coordinate invariant still enforced on the payload.
    expect(sentImage.x).toBeGreaterThanOrEqual(0);
    expect(sentImage.x).toBeLessThanOrEqual(1);

    startRerender.mockClear();
    st().toggleElementVisibility(id);
    await st().startExport();
    sent = startRerender.mock.calls[0][2];
    expect(sent.elements).toBeUndefined(); // nothing visible → field omitted entirely
  });

  test("an image-free document's payload has no elements field (byte-identical baseline)", () => {
    const req = buildRerenderRequest({ elements: PRISTINE_ELEMENTS });
    expect(req.elements).toBeUndefined(); // defaults: headline/progress/logo all hidden
  });

  // Feature #30 — a timed emoji overlay rides the SAME OVERLAY_ELEMENT_TYPES
  // path as an image, carrying its emoji + [start,end] window to the burn.
  test("a visible emoji overlay reaches req.elements with its timing window", () => {
    const emoji = {
      id: "el_emoji_0", type: "emoji", x: 0.5, y: 0.66, scale: 1, rotation: 0,
      visible: true, locked: false,
      props: { emoji: "🔥", codepoint: "1f525", height: 0.12, opacity: 1, start: 1.8, end: 3.6 },
    };
    const req = buildRerenderRequest({ elements: [...PRISTINE_ELEMENTS, emoji] });
    const sent = (req.elements || []).find((e) => e.type === "emoji");
    expect(sent).toBeTruthy();
    expect(sent.props.emoji).toBe("🔥");
    expect(sent.props.start).toBe(1.8);
    expect(sent.props.end).toBe(3.6);
  });

  test("a hidden emoji overlay is not sent", () => {
    const emoji = {
      id: "el_emoji_0", type: "emoji", x: 0.5, y: 0.66, scale: 1, rotation: 0,
      visible: false, locked: false, props: { emoji: "🔥", start: 1, end: 2 },
    };
    const req = buildRerenderRequest({ elements: [...PRISTINE_ELEMENTS, emoji] });
    expect(req.elements).toBeUndefined();
  });
});
