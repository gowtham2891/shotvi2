/**
 * Feature #8 — store half: duplicateElement / copyElement / pasteElement.
 * Caption is excluded from all three (single-caption document contract,
 * same stance as deleteSelected). Duplicates/pastes mint fresh ids, offset
 * slightly, and select the new element; each is ONE undo frame.
 */
import { useAppStore } from "@/store/useAppStore";

const st = () => useAppStore.getState();

const ELEMENTS = [
  {
    id: "el_caption_1", type: "caption", x: 0.5, y: 0.8, scale: 1, rotation: 0,
    visible: true, locked: false, props: { presetId: "bold-yellow" },
  },
  {
    id: "el_headline_1", type: "headline", x: 0.5, y: 0.14, scale: 1, rotation: 0,
    visible: true, locked: false, props: { text: "hook", font: "Inter", fontSize: 0.05 },
  },
];

beforeEach(() => {
  useAppStore.setState({
    elements: JSON.parse(JSON.stringify(ELEMENTS)),
    selectedElementId: null,
    elementClipboard: null,
  });
  st().resetHistory();
});

describe("duplicateElement", () => {
  test("clones with a fresh id, slight offset, and selects the clone", () => {
    const newId = st().duplicateElement("el_headline_1");
    expect(newId).toBeTruthy();
    expect(newId).not.toBe("el_headline_1");
    const clone = st().elements.find((el) => el.id === newId);
    expect(clone.type).toBe("headline");
    expect(clone.props.text).toBe("hook");
    expect(clone.x).toBeCloseTo(0.53, 5);
    expect(clone.y).toBeCloseTo(0.17, 5);
    expect(st().selectedElementId).toBe(newId);
    // Deep copy: mutating the clone's props never touches the source.
    clone.props.text = "mutated";
    expect(st().elements.find((el) => el.id === "el_headline_1").props.text).toBe("hook");
  });

  test("caption cannot be duplicated (single-caption contract)", () => {
    expect(st().duplicateElement("el_caption_1")).toBeNull();
    expect(st().elements).toHaveLength(2);
  });

  test("one undo frame: undo removes the clone", () => {
    const newId = st().duplicateElement("el_headline_1");
    expect(st().elements.some((el) => el.id === newId)).toBe(true);
    st().undo();
    expect(st().elements.some((el) => el.id === newId)).toBe(false);
  });
});

describe("copy / paste", () => {
  test("copy stores a deep snapshot; paste mints a fresh offset element", () => {
    expect(st().copyElement("el_headline_1")).toBe(true);
    // Later mutation of the live element must not affect the snapshot.
    useAppStore.setState({
      elements: st().elements.map((el) =>
        el.id === "el_headline_1" ? { ...el, props: { ...el.props, text: "changed" } } : el
      ),
    });
    const pastedId = st().pasteElement();
    const pasted = st().elements.find((el) => el.id === pastedId);
    expect(pasted.props.text).toBe("hook"); // the snapshot, not the mutation
    expect(pasted.x).toBeCloseTo(0.53, 5);
    expect(st().selectedElementId).toBe(pastedId);
  });

  test("copying the caption refuses; pasting with empty clipboard no-ops", () => {
    expect(st().copyElement("el_caption_1")).toBe(false);
    expect(st().elementClipboard).toBeNull();
    expect(st().pasteElement()).toBeNull();
    expect(st().elements).toHaveLength(2);
  });

  test("paste twice yields two distinct elements", () => {
    st().copyElement("el_headline_1");
    const a = st().pasteElement();
    const b = st().pasteElement();
    expect(a).not.toBe(b);
    expect(st().elements).toHaveLength(4);
  });
});
