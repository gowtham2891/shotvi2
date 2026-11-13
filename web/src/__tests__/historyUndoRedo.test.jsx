/**
 * Full-document undo/redo regression suite.
 *
 * History model: past[]/future[] of deep-copied snapshots of the document's
 * three independent slices (elements, exportSettings, transcriptEdits) —
 * the same shape getEditDocument serializes and applyDraft restores; both
 * history and drafts land through the single applyDocumentState path.
 * Frames are pushed BEFORE each committed mutation; continuous gestures
 * coalesce into one frame; no-op actions push nothing; history is
 * session-only (never in the draft payload).
 */
import { useAppStore } from "@/store/useAppStore";
import { createEmptyTranscriptEdits } from "@/lib/transcriptEdits";
import { isTextEditingTarget } from "@/lib/editableTranscript";

const TRANSCRIPT = [
  { id: "w_flat_0", ref: { type: "flat", index: 0 }, text: "ఒకటి", start: 0, end: 0.5 },
  { id: "w_flat_1", ref: { type: "flat", index: 1 }, text: "రెండు", start: 0.6, end: 1.4 },
  { id: "w_flat_2", ref: { type: "flat", index: 2 }, text: "మూడు", start: 2.0, end: 2.5 },
  { id: "w_flat_3", ref: { type: "flat", index: 3 }, text: "నాలుగు", start: 2.6, end: 3.1 },
];

const CAPTION = {
  id: "el_caption_1",
  type: "caption",
  x: 0.5,
  // Arbitrary sample start position — this suite is about undo/redo frames,
  // not caption placement. Deliberately off the default anchor so the history
  // assertions below never silently track CAPTION_DEFAULT_POSITION.
  y: 0.61,
  scale: 1,
  rotation: 0,
  visible: true,
  locked: false,
  props: {
    presetId: "bold-yellow",
    font: "Noto Sans Telugu",
    fontSize: 0.055,
    animation: "karaoke",
    pill: { enabled: false, color: "#000000", opacity: 0.55, padding: 8, radius: 8 },
  },
};

const EXPORT_SETTINGS = {
  format: "9:16",
  background: "blur",
  bgColor: "#000000",
  useAutocrop: true,
  burnInCaptions: true,
};

const past = () => useAppStore.getState().history.past;
const future = () => useAppStore.getState().history.future;
const caption = () => useAppStore.getState().elements.find((el) => el.id === "el_caption_1");

beforeEach(() => {
  useAppStore.getState().resetHistory();
  useAppStore.setState({
    transcript: TRANSCRIPT,
    transcriptEdits: createEmptyTranscriptEdits(),
    elements: [JSON.parse(JSON.stringify(CAPTION))],
    exportSettings: { ...EXPORT_SETTINGS },
    selectedElementId: "el_caption_1",
  });
});

describe("VERIFY 1 — full sequence: split → word fix → font → drag; undo×4 / redo×4 / redo invalidation", () => {
  const runSequence = () => {
    const s = useAppStore.getState();
    s.addLineSplit(1); // frame 1
    s.setWordEdit("w_flat_2", "లోకం"); // frame 2
    s.updateElementProps("el_caption_1", { font: "Mandali" }); // frame 3
    s.endHistoryCoalescing();
    // drag gesture: several ticks, one frame
    s.updateElement("el_caption_1", { x: 0.4, y: 0.7 });
    s.updateElement("el_caption_1", { x: 0.35, y: 0.6 });
    s.updateElement("el_caption_1", { x: 0.3, y: 0.5 }); // frame 4 (coalesced)
    s.endHistoryCoalescing();
  };

  test("four undos restore each edit in reverse; four redos replay them", () => {
    runSequence();
    expect(past()).toHaveLength(4);
    const s = useAppStore.getState();

    s.undo(); // drag back
    expect(caption().x).toBe(0.5);
    expect(caption().y).toBe(CAPTION.y);
    expect(caption().props.font).toBe("Mandali"); // font change still applied

    s.undo(); // font back
    expect(caption().props.font).toBe("Noto Sans Telugu");
    expect(useAppStore.getState().effectiveWord("w_flat_2")).toBe("లోకం");

    s.undo(); // word fix back
    expect(useAppStore.getState().effectiveWord("w_flat_2")).toBe("మూడు");
    expect(useAppStore.getState().transcriptEdits.lineSplits).toEqual([1]);

    s.undo(); // split back
    expect(useAppStore.getState().transcriptEdits.lineSplits).toEqual([]);
    expect(past()).toHaveLength(0);
    expect(future()).toHaveLength(4);

    s.redo();
    expect(useAppStore.getState().transcriptEdits.lineSplits).toEqual([1]);
    s.redo();
    expect(useAppStore.getState().effectiveWord("w_flat_2")).toBe("లోకం");
    s.redo();
    expect(caption().props.font).toBe("Mandali");
    s.redo();
    expect(caption().x).toBe(0.3);
    expect(future()).toHaveLength(0);
    expect(past()).toHaveLength(4);
  });

  test("a new committed edit after two undos clears the redo stack", () => {
    runSequence();
    const s = useAppStore.getState();
    s.undo();
    s.undo();
    expect(future()).toHaveLength(2);
    s.setExportSetting("background", "black"); // new edit → redo invalidated
    expect(future()).toHaveLength(0);
    expect(useAppStore.getState().exportSettings.background).toBe("black");
  });
});

describe("VERIFY 2 — a drag is ONE frame", () => {
  test("twenty move ticks push a single frame; one undo returns to the pre-drag position", () => {
    const s = useAppStore.getState();
    for (let i = 1; i <= 20; i++) {
      s.updateElement("el_caption_1", { x: 0.5 - i * 0.01, y: CAPTION.y - i * 0.01 });
    }
    s.endHistoryCoalescing();
    expect(past()).toHaveLength(1);
    expect(caption().x).toBeCloseTo(0.3);
    s.undo();
    expect(caption().x).toBe(0.5);
    expect(caption().y).toBe(CAPTION.y);
  });

  test("two separate gestures (boundary between them) are two frames", () => {
    const s = useAppStore.getState();
    s.updateElement("el_caption_1", { x: 0.4 });
    s.updateElement("el_caption_1", { x: 0.3 });
    s.endHistoryCoalescing(); // pointerup
    s.updateElement("el_caption_1", { x: 0.2 });
    s.endHistoryCoalescing();
    expect(past()).toHaveLength(2);
    s.undo();
    expect(caption().x).toBe(0.3); // back to end of gesture 1
    s.undo();
    expect(caption().x).toBe(0.5);
  });
});

describe("VERIFY 3 — word-fix commit is atomic", () => {
  test("one commit = one frame; undo restores the pre-edit word", () => {
    // The 6 keystrokes live in the input's draft state, never in history —
    // only the commit calls setWordEdit.
    useAppStore.getState().setWordEdit("w_flat_1", "మార్పు");
    expect(past()).toHaveLength(1);
    expect(useAppStore.getState().effectiveWord("w_flat_1")).toBe("మార్పు");
    useAppStore.getState().undo();
    expect(useAppStore.getState().effectiveWord("w_flat_1")).toBe("రెండు");
    expect(useAppStore.getState().transcriptEdits.wordEdits).toEqual({});
  });
});

describe("VERIFY 4 — input-focus guard predicate", () => {
  test("text-editing contexts are recognized (Ctrl+Z stays native there)", () => {
    expect(isTextEditingTarget({ tagName: "INPUT" })).toBe(true);
    expect(isTextEditingTarget({ tagName: "TEXTAREA" })).toBe(true);
    expect(isTextEditingTarget({ tagName: "SELECT" })).toBe(true);
    expect(isTextEditingTarget({ tagName: "DIV", isContentEditable: true })).toBe(true);
  });

  test("non-editing contexts are not (document undo applies)", () => {
    expect(isTextEditingTarget({ tagName: "DIV" })).toBe(false);
    expect(isTextEditingTarget({ tagName: "BUTTON", isContentEditable: false })).toBe(false);
    expect(isTextEditingTarget(null)).toBe(false);
    expect(isTextEditingTarget(undefined)).toBe(false);
  });
});

describe("VERIFY 5 — session-only history", () => {
  test("the draft payload never carries history", () => {
    useAppStore.getState().addLineSplit(1);
    const doc = useAppStore.getState().getEditDocument();
    expect(doc.history).toBeUndefined();
    expect(JSON.stringify(doc)).not.toContain('"past"');
  });

  test("restoring a draft pushes NO history frames (a reload starts clean)", () => {
    useAppStore.getState().applyDraft({
      elements: [JSON.parse(JSON.stringify(CAPTION))],
      exportSettings: { background: "black" },
      transcriptEdits: { wordEdits: {}, mergedGroups: [], lineSplits: [2] },
    });
    expect(past()).toHaveLength(0);
    expect(useAppStore.getState().transcriptEdits.lineSplits).toEqual([2]); // …but the document applied
    expect(useAppStore.getState().exportSettings.background).toBe("black");
  });

  test("resetHistory (openClip) empties both stacks", () => {
    const s = useAppStore.getState();
    s.addLineSplit(1);
    s.undo();
    expect(future()).toHaveLength(1);
    s.resetHistory();
    expect(past()).toHaveLength(0);
    expect(future()).toHaveLength(0);
  });
});

describe("VERIFY 6 — stack-driven affordance (what the buttons subscribe to)", () => {
  test("canUndo/canRedo flip with the stacks", () => {
    expect(past().length > 0).toBe(false);
    expect(future().length > 0).toBe(false);
    useAppStore.getState().addLineSplit(1);
    expect(past().length > 0).toBe(true);
    expect(future().length > 0).toBe(false);
    useAppStore.getState().undo();
    expect(past().length > 0).toBe(false);
    expect(future().length > 0).toBe(true);
  });

  test("undo/redo on empty stacks are safe no-ops", () => {
    const before = useAppStore.getState().transcriptEdits;
    useAppStore.getState().undo();
    useAppStore.getState().redo();
    expect(useAppStore.getState().transcriptEdits).toEqual(before);
  });
});

describe("frame discipline — no phantom frames, bounded memory", () => {
  test("no-op actions push nothing", () => {
    const s = useAppStore.getState();
    s.addLineSplit(99); // invalid index
    s.addLineSplit(-1);
    s.removeLineSplit(2); // nothing there
    s.setWordEdit("w_flat_999", "ఏదో"); // unknown word
    s.setWordEdit("w_flat_1", "రెండు"); // identical to original
    s.setExportSetting("background", "blur"); // same value
    s.removeElement("el_nope"); // unknown element
    s.toggleElementVisibility("el_nope");
    s.bringForward("el_caption_1"); // already last
    s.sendBackward("el_caption_1"); // already first
    expect(past()).toHaveLength(0);
  });

  test("undoing an export-settings change restores it (autosave watches the same slice)", () => {
    useAppStore.getState().setExportSetting("format", "1:1");
    useAppStore.getState().undo();
    expect(useAppStore.getState().exportSettings.format).toBe("9:16");
  });

  test("snapshots are deep copies — later mutations cannot corrupt older frames", () => {
    const s = useAppStore.getState();
    s.setWordEdit("w_flat_1", "మొదటిమార్పు");
    s.setWordEdit("w_flat_1", "రెండోమార్పు"); // overwrites the live entry
    s.undo();
    expect(useAppStore.getState().effectiveWord("w_flat_1")).toBe("మొదటిమార్పు");
    s.undo();
    expect(useAppStore.getState().effectiveWord("w_flat_1")).toBe("రెండు");
  });

  test("the stack is capped at 50 frames", () => {
    const s = useAppStore.getState();
    for (let i = 0; i < 60; i++) {
      s.setWordEdit("w_flat_1", `మార్పు${i}`);
    }
    expect(past().length).toBeLessThanOrEqual(50);
    expect(past()).toHaveLength(50);
  });
});
