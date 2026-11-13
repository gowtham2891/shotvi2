/**
 * Feature #9 — multi-select store contracts.
 *
 * selectedIds is the full selection; selectedElementId stays the PRIMARY
 * member (CanvasArea's frozen nudge/delete cases, TransformBox and the
 * Inspector all key off it). Invariant everywhere: primary ∈ selectedIds,
 * or both empty.
 */
import { useAppStore } from "@/store/useAppStore";

const st = () => useAppStore.getState();

const ELEMENTS = [
  { id: "el_caption_1", type: "caption", x: 0.5, y: 0.8, scale: 1, rotation: 0, visible: true, locked: false, props: {} },
  { id: "el_headline_1", type: "headline", x: 0.3, y: 0.2, scale: 1, rotation: 0, visible: true, locked: false, props: {} },
  { id: "el_logo_1", type: "logo", x: 0.7, y: 0.1, scale: 1, rotation: 0, visible: true, locked: false, props: {} },
];

beforeEach(() => {
  useAppStore.setState({
    elements: JSON.parse(JSON.stringify(ELEMENTS)),
    selectedElementId: null,
    selectedIds: [],
  });
  st().resetHistory();
});

describe("selection invariant", () => {
  test("setSelected selects solo; clearSelection empties both", () => {
    st().setSelected("el_logo_1");
    expect(st().selectedIds).toEqual(["el_logo_1"]);
    expect(st().selectedElementId).toBe("el_logo_1");
    st().clearSelection();
    expect(st().selectedIds).toEqual([]);
    expect(st().selectedElementId).toBeNull();
  });

  test("toggleInSelection adds (becoming primary) and removes (promoting last)", () => {
    st().setSelected("el_headline_1");
    st().toggleInSelection("el_logo_1");
    expect(st().selectedIds).toEqual(["el_headline_1", "el_logo_1"]);
    expect(st().selectedElementId).toBe("el_logo_1"); // newest = primary
    st().toggleInSelection("el_logo_1"); // remove the primary
    expect(st().selectedIds).toEqual(["el_headline_1"]);
    expect(st().selectedElementId).toBe("el_headline_1"); // promoted
  });

  test("toggling an unknown id is a no-op", () => {
    st().toggleInSelection("el_ghost");
    expect(st().selectedIds).toEqual([]);
  });

  test("setSelection (marquee) replaces wholesale and filters unknowns", () => {
    st().setSelection(["el_headline_1", "el_ghost", "el_logo_1"]);
    expect(st().selectedIds).toEqual(["el_headline_1", "el_logo_1"]);
    expect(st().selectedElementId).toBe("el_logo_1"); // last = primary
    st().setSelection([]);
    expect(st().selectedElementId).toBeNull();
  });

  test("removeElement drops the id from selectedIds too", () => {
    st().setSelection(["el_headline_1", "el_logo_1"]);
    st().removeElement("el_headline_1");
    expect(st().selectedIds).toEqual(["el_logo_1"]);
  });
});

describe("group transforms", () => {
  test("moveElementsBy shifts only the given ids, clamped", () => {
    st().moveElementsBy(["el_headline_1", "el_logo_1"], 0.4, 0.0);
    const byId = Object.fromEntries(st().elements.map((el) => [el.id, el]));
    expect(byId.el_headline_1.x).toBeCloseTo(0.7, 5);
    expect(byId.el_logo_1.x).toBeCloseTo(0.98, 5); // clamped at 0.98
    expect(byId.el_caption_1.x).toBeCloseTo(0.5, 5); // untouched
  });

  test("moveElementsTo applies absolute positions in one undo frame", () => {
    st().moveElementsTo(
      { el_headline_1: { x: 0.1, y: 0.1 }, el_logo_1: { x: 0.9, y: 0.9 } },
      null
    );
    const byId = Object.fromEntries(st().elements.map((el) => [el.id, el]));
    expect(byId.el_headline_1.x).toBeCloseTo(0.1, 5);
    expect(byId.el_logo_1.y).toBeCloseTo(0.9, 5);
    st().undo();
    const back = Object.fromEntries(st().elements.map((el) => [el.id, el]));
    expect(back.el_headline_1.x).toBeCloseTo(0.3, 5);
    expect(back.el_logo_1.y).toBeCloseTo(0.1, 5);
  });

  test("locked elements never move", () => {
    useAppStore.setState({
      elements: st().elements.map((el) =>
        el.id === "el_logo_1" ? { ...el, locked: true } : el
      ),
    });
    st().moveElementsBy(["el_logo_1"], 0.1, 0.1);
    expect(st().elements.find((el) => el.id === "el_logo_1").x).toBeCloseTo(0.7, 5);
  });

  test("removeSelectedExceptPrimary deletes the rest, never caption/primary", () => {
    st().setSelection(["el_caption_1", "el_headline_1", "el_logo_1"], "el_logo_1");
    st().removeSelectedExceptPrimary();
    const ids = st().elements.map((el) => el.id);
    expect(ids).toContain("el_caption_1"); // caption immune
    expect(ids).toContain("el_logo_1"); // primary immune (CanvasArea's job)
    expect(ids).not.toContain("el_headline_1");
    expect(st().selectedIds).toEqual(["el_caption_1", "el_logo_1"]);
  });
});
