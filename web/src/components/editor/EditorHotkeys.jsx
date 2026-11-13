import { useEffect } from "react";
import { useAppStore } from "@/store/useAppStore";
import { resolveEditorKey } from "@/lib/editorKeymap";
import { isTextEditingTarget } from "@/lib/editableTranscript";

/**
 * Feature #8 — duplicate / copy-paste / z-order hotkeys.
 *
 * A second window keydown listener EXISTS here deliberately, and it is safe
 * against the failure mode CanvasArea's single-listener warning describes:
 * that bug came from two listeners with SEPARATE decision logic and SEPARATE
 * typing guards fighting over the same keys. This listener runs the exact
 * same resolveEditorKey + isTextEditingTarget pair CanvasArea runs, and acts
 * ONLY on the actions CanvasArea's switch falls through on (its `default:`):
 *   Ctrl/Cmd+D  duplicateSelected   Ctrl/Cmd+C  copySelected
 *   Ctrl/Cmd+V  pasteElement        [ / ]       z-order backward/forward
 * One resolver, disjoint action sets → precedence cannot diverge.
 * (CanvasArea is frozen by the Sprint-4 no-touch rule, so the new actions
 * cannot live in its switch.)
 */
// Mirror of CanvasArea's nudge steps (frozen file — values are spec there).
const NUDGE = 1;
const NUDGE_BIG = 10;

export const EditorHotkeys = () => {
  useEffect(() => {
    const onKeyDown = (e) => {
      const decision = resolveEditorKey(e, isTextEditingTarget(document.activeElement));
      const s = useAppStore.getState();
      switch (decision.action) {
        case "duplicateSelected":
          if (decision.preventDefault) e.preventDefault();
          if (s.selectedElementId) s.duplicateElement(s.selectedElementId);
          return;
        case "copySelected":
          if (s.selectedElementId) s.copyElement(s.selectedElementId);
          return;
        case "pasteElement":
          s.pasteElement();
          return;
        case "zOrderBackward":
          if (s.selectedElementId) s.sendBackward(s.selectedElementId);
          return;
        case "zOrderForward":
          if (s.selectedElementId) s.bringForward(s.selectedElementId);
          return;
        // ── Feature #9: group semantics for delete/nudge ──────────────
        // CanvasArea's frozen switch handles the PRIMARY element for both;
        // this listener handles the REST of the selection. The subsets are
        // disjoint, so listener order doesn't matter.
        case "deleteSelected":
          if (s.selectedIds.length > 1) s.removeSelectedExceptPrimary();
          return;
        case "nudge": {
          const rest = s.selectedIds.filter((id) => id !== s.selectedElementId);
          if (!rest.length) return;
          const stage = document.querySelector('[data-testid="editor-canvas-stage"]');
          const rect = stage?.getBoundingClientRect();
          if (!rect) return;
          const step = e.shiftKey ? NUDGE_BIG : NUDGE;
          let dx = 0, dy = 0;
          if (e.key === "ArrowLeft") dx = -step / rect.width;
          if (e.key === "ArrowRight") dx = step / rect.width;
          if (e.key === "ArrowUp") dy = -step / rect.height;
          if (e.key === "ArrowDown") dy = step / rect.height;
          if (dx || dy) s.moveElementsBy(rest, dx, dy, "group-nudge");
          return;
        }
        default:
          return; // everything else belongs to CanvasArea's listener
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return null;
};

export default EditorHotkeys;
