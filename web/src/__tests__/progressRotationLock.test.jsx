/**
 * BUG-005 containment gate — progress element rotation is locked to 0.
 *
 * The export path (services/overlay_renderer.py :: _composite_layers) does
 * NOT apply a `rotate=` filter for the progress overlay, even though
 * _prepare_progress_layer computes a rotated bounding box. Until the
 * composite path is wired through, allowing a non-zero editor rotation
 * would silently drift preview from export. This test guards the store-side
 * lock (UI hides the rotate handle in TransformBox.jsx :: isRotationDisabled).
 */
import { useAppStore } from "@/store/useAppStore";

describe("Progress element rotation is locked to 0 (BUG-005 containment)", () => {
  test("updateElement({rotation: N}) on a progress element is silently coerced to 0", () => {
    const store = useAppStore.getState();
    // Find or create a progress element to operate on.
    let progress = store.elements.find((el) => el.type === "progress");
    if (!progress) {
      store.addElement("progress");
      progress = useAppStore.getState().elements.find((el) => el.type === "progress");
    }
    expect(progress).toBeTruthy();
    expect(progress.rotation).toBe(0);

    // Try to rotate it — should be coerced back to 0.
    store.updateElement(progress.id, { rotation: 45 });
    const after = useAppStore.getState().elements.find((el) => el.id === progress.id);
    expect(after.rotation).toBe(0);
  });

  test("non-progress elements CAN still be rotated", () => {
    const store = useAppStore.getState();
    let headline = store.elements.find((el) => el.type === "headline");
    if (!headline) {
      store.addElement("headline");
      headline = useAppStore.getState().elements.find((el) => el.type === "headline");
    }
    expect(headline).toBeTruthy();
    store.updateElement(headline.id, { rotation: 30 });
    const after = useAppStore.getState().elements.find((el) => el.id === headline.id);
    expect(after.rotation).toBeCloseTo(30);
  });

  test("progress element default rotation is 0", () => {
    // Sanity: even at construction time, progress starts at rotation 0.
    const store = useAppStore.getState();
    store.addElement("progress");
    const created = useAppStore
      .getState()
      .elements.filter((el) => el.type === "progress")
      .slice(-1)[0];
    expect(created.rotation).toBe(0);
  });
});
