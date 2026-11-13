// Guards the pixel-consistency invariant: every canvas element coordinate in
// an outgoing EditDocument payload must be a normalized 0–1 fraction of
// canvas size — never pixels. The FFmpeg export and the preview only stay
// aligned if both sides speak fractions. Enforced by unit tests and by
// startExport at runtime.

const inUnit = (v) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1;

// Returns [{ path, value }] for every coordinate outside [0, 1].
export function collectCoordinateViolations(doc) {
  const violations = [];
  const check = (path, value) => {
    if (!inUnit(value)) violations.push({ path, value });
  };

  (doc?.elements || []).forEach((el, i) => {
    const tag = `elements[${i}](${el.type || "?"})`;
    check(`${tag}.x`, el.x);
    check(`${tag}.y`, el.y);
    // Size-like props are also canvas fractions when present
    if (el.props?.width != null) check(`${tag}.props.width`, el.props.width);
    if (el.props?.height != null) check(`${tag}.props.height`, el.props.height);
    if (el.props?.fontSize != null) check(`${tag}.props.fontSize`, el.props.fontSize);
  });

  if (doc?.captionY != null) check("captionY", doc.captionY);

  if (doc?.cropBox) {
    for (const k of ["x", "y", "w", "h"]) {
      check(`cropBox.${k}`, doc.cropBox[k]);
    }
  }

  // Sprint 4 — per-aspect crop windows are fractional windows over the 16:9
  // master; the backend multiplies them straight into an FFmpeg crop filter,
  // so pixels here would crop garbage.
  const windows = doc?.exportSettings?.cropWindows;
  if (windows && typeof windows === "object") {
    for (const [aspect, box] of Object.entries(windows)) {
      for (const k of ["x", "y", "w", "h"]) {
        check(`exportSettings.cropWindows[${aspect}].${k}`, box?.[k]);
      }
    }
  }

  return violations;
}

export function isEditDocumentNormalized(doc) {
  return collectCoordinateViolations(doc).length === 0;
}

// Throws with a readable list of offending paths — used before export.
export function assertEditDocumentNormalized(doc) {
  const violations = collectCoordinateViolations(doc);
  if (violations.length) {
    const detail = violations
      .map((v) => `${v.path} = ${JSON.stringify(v.value)}`)
      .join(", ");
    throw new Error(
      `EditDocument contains non-normalized coordinates (must be 0–1 fractions, never pixels): ${detail}`
    );
  }
}
