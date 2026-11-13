/**
 * Editor keyboard map regression gate.
 *
 * Space play/pause broke silently while two independent window listeners
 * with separate typing guards owned different keys and NEITHER bound
 * Space→togglePlay. The map is now one pure function behind one guard;
 * this suite pins every binding so a future handler change can't drop one
 * without failing here.
 */
import { resolveEditorKey } from "@/lib/editorKeymap";
import { isTextEditingTarget } from "@/lib/editableTranscript";

const key = (overrides) => ({
  code: "",
  key: "",
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
  repeat: false,
  ...overrides,
});

const SPACE = key({ code: "Space", key: " " });

describe("Space — the regression", () => {
  test("Space when NOT editing → togglePlay (with preventDefault)", () => {
    expect(resolveEditorKey(SPACE, false)).toEqual({
      action: "togglePlay",
      preventDefault: true,
    });
  });

  test("Space when editing (word input focused) → passthrough: it types a space", () => {
    expect(resolveEditorKey(SPACE, true)).toEqual({ action: "passthrough" });
  });

  test("held-Space auto-repeat arms panning but does NOT re-toggle playback", () => {
    expect(resolveEditorKey({ ...SPACE, repeat: true }, false)).toEqual({
      action: "armPan",
      preventDefault: true,
    });
  });
});

describe("undo/redo bindings (must not regress from consolidation)", () => {
  test("Ctrl+Z when NOT editing → undo", () => {
    expect(resolveEditorKey(key({ key: "z", ctrlKey: true }), false)).toEqual({
      action: "undo",
      preventDefault: true,
    });
    // Cmd+Z (mac)
    expect(resolveEditorKey(key({ key: "z", metaKey: true }), false).action).toBe("undo");
  });

  test("Ctrl+Shift+Z and Ctrl+Y → redo", () => {
    expect(
      resolveEditorKey(key({ key: "z", ctrlKey: true, shiftKey: true }), false).action
    ).toBe("redo");
    expect(resolveEditorKey(key({ key: "y", ctrlKey: true }), false).action).toBe("redo");
    // Browsers report uppercase Z with shift held — must still redo.
    expect(
      resolveEditorKey(key({ key: "Z", ctrlKey: true, shiftKey: true }), false).action
    ).toBe("redo");
  });

  test("Ctrl+Z when editing → passthrough (native input undo owns it)", () => {
    expect(resolveEditorKey(key({ key: "z", ctrlKey: true }), true)).toEqual({
      action: "passthrough",
    });
  });

  test("other Ctrl combos stay native", () => {
    expect(resolveEditorKey(key({ key: "s", ctrlKey: true }), false)).toEqual({
      action: "passthrough",
    });
  });
});

describe("prior bindings unchanged", () => {
  test("Escape → clearSelection; Delete/Backspace → deleteSelected; Arrows → nudge", () => {
    expect(resolveEditorKey(key({ key: "Escape" }), false).action).toBe("clearSelection");
    expect(resolveEditorKey(key({ key: "Delete" }), false).action).toBe("deleteSelected");
    expect(resolveEditorKey(key({ key: "Backspace" }), false).action).toBe("deleteSelected");
    for (const k of ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]) {
      expect(resolveEditorKey(key({ key: k }), false).action).toBe("nudge");
    }
  });

  test("when editing, ALL of those pass through to the input", () => {
    for (const k of ["Escape", "Delete", "Backspace", "ArrowLeft", "ArrowRight"]) {
      expect(resolveEditorKey(key({ key: k }), true).action).toBe("passthrough");
    }
  });

  test("unmapped keys pass through", () => {
    expect(resolveEditorKey(key({ key: "a" }), false).action).toBe("passthrough");
    expect(resolveEditorKey(key({ key: "Enter" }), false).action).toBe("passthrough");
  });
});

describe("feature #8 — duplicate / copy-paste / z-order", () => {
  test("Ctrl/Cmd+D → duplicateSelected with preventDefault (blocks bookmark)", () => {
    expect(resolveEditorKey(key({ key: "d", ctrlKey: true }), false)).toEqual({
      action: "duplicateSelected",
      preventDefault: true,
    });
    expect(resolveEditorKey(key({ key: "d", metaKey: true }), false).action).toBe(
      "duplicateSelected"
    );
  });

  test("Ctrl+C / Ctrl+V → copy/paste WITHOUT preventDefault (native copy unharmed)", () => {
    expect(resolveEditorKey(key({ key: "c", ctrlKey: true }), false)).toEqual({
      action: "copySelected",
    });
    expect(resolveEditorKey(key({ key: "v", ctrlKey: true }), false)).toEqual({
      action: "pasteElement",
    });
  });

  test("[ / ] → z-order backward/forward", () => {
    expect(resolveEditorKey(key({ key: "[" }), false).action).toBe("zOrderBackward");
    expect(resolveEditorKey(key({ key: "]" }), false).action).toBe("zOrderForward");
  });

  test("all of them pass through while editing text", () => {
    expect(resolveEditorKey(key({ key: "d", ctrlKey: true }), true).action).toBe("passthrough");
    expect(resolveEditorKey(key({ key: "c", ctrlKey: true }), true).action).toBe("passthrough");
    expect(resolveEditorKey(key({ key: "v", ctrlKey: true }), true).action).toBe("passthrough");
    expect(resolveEditorKey(key({ key: "[" }), true).action).toBe("passthrough");
    expect(resolveEditorKey(key({ key: "]" }), true).action).toBe("passthrough");
  });

  test("Ctrl+Shift+C/V stay native (devtools etc.)", () => {
    expect(resolveEditorKey(key({ key: "c", ctrlKey: true, shiftKey: true }), false).action)
      .toBe("passthrough");
    expect(resolveEditorKey(key({ key: "v", ctrlKey: true, shiftKey: true }), false).action)
      .toBe("passthrough");
  });
});

describe("the one shared guard", () => {
  test("isTextEditingTarget is the isEditing source: inputs yes, buttons/canvas no", () => {
    // The word inputs of the editable transcript:
    expect(isTextEditingTarget({ tagName: "INPUT" })).toBe(true);
    // The Play button / empty canvas / body after a click:
    expect(isTextEditingTarget({ tagName: "BUTTON" })).toBe(false);
    expect(isTextEditingTarget({ tagName: "BODY" })).toBe(false);
    expect(isTextEditingTarget(null)).toBe(false);
  });
});
