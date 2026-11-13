/**
 * Pixel-consistency regression suite.
 *
 * The editor stores canvas element positions as 0–1 fractions of canvas
 * size, and outgoing EditDocument payloads must keep them normalized so the
 * FFmpeg export and the browser preview stay aligned. Any regression to
 * pixel values must fail here.
 */
import {
  collectCoordinateViolations,
  assertEditDocumentNormalized,
  isEditDocumentNormalized,
} from "@/lib/editDocumentValidation";
import { INITIAL_ELEMENTS } from "@/data/mockData";
import { useAppStore } from "@/store/useAppStore";
import { buildRerenderRequest, DEFAULT_STYLE_ID, CAPTION_DEFAULT_POSITION } from "@/api/renders";

describe("EditDocument coordinate normalization", () => {
  test("the store's outgoing EditDocument has all coordinates within [0, 1]", () => {
    const doc = useAppStore.getState().getEditDocument();
    expect(doc.elements.length).toBeGreaterThan(0);
    expect(collectCoordinateViolations(doc)).toEqual([]);
    expect(() => assertEditDocumentNormalized(doc)).not.toThrow();
  });

  test("every default canvas element ships with normalized coordinates", () => {
    // captionY here is an arbitrary in-range sample — this test is about
    // coordinate NORMALIZATION, not the default anchor. Kept off the default.
    const doc = { elements: INITIAL_ELEMENTS, captionY: 0.61 };
    expect(collectCoordinateViolations(doc)).toEqual([]);
  });

  test("elements moved through store actions stay normalized", () => {
    const store = useAppStore.getState();
    const captionId = store.getCaptionElement().id;
    store.updateElement(captionId, { x: 0.31, y: 0.9 });
    store.applyPositionPreset("bottom-safe");
    const doc = useAppStore.getState().getEditDocument();
    expect(collectCoordinateViolations(doc)).toEqual([]);
  });

  test("pixel-valued coordinates are rejected", () => {
    const bad = {
      elements: [
        { id: "el_x", type: "caption", x: 540, y: 0.5, props: {} }, // 540px, not a fraction
      ],
    };
    const violations = collectCoordinateViolations(bad);
    expect(violations).toHaveLength(1);
    expect(violations[0].path).toContain(".x");
    expect(isEditDocumentNormalized(bad)).toBe(false);
    expect(() => assertEditDocumentNormalized(bad)).toThrow(/never pixels/);
  });

  test("negative and non-numeric coordinates are rejected", () => {
    const bad = {
      elements: [{ id: "a", type: "logo", x: -0.1, y: "0.5", props: {} }],
    };
    expect(collectCoordinateViolations(bad)).toHaveLength(2);
  });

  test("crop box fractions are validated", () => {
    expect(
      collectCoordinateViolations({ elements: [], cropBox: { x: 0.1, y: 0, w: 0.8, h: 1 } })
    ).toEqual([]);
    expect(
      collectCoordinateViolations({ elements: [], cropBox: { x: 120, y: 0, w: 0.8, h: 1 } })
    ).toHaveLength(1);
  });
});

describe("buildRerenderRequest (EditDocument → backend schema)", () => {
  test("caption at default position omits caption_x/caption_y (untouched → backend default path)", () => {
    const req = buildRerenderRequest({
      captionX: CAPTION_DEFAULT_POSITION.x,
      captionY: CAPTION_DEFAULT_POSITION.y,
    });
    expect(req).not.toHaveProperty("caption_x");
    expect(req).not.toHaveProperty("caption_y");
    expect(req).not.toHaveProperty("caption_position"); // old field removed
  });

  test("dragged caption sends both caption_x/caption_y as 0–1 fractions (never pixels, never percent)", () => {
    const req = buildRerenderRequest({ captionX: 0.31, captionY: 0.7 });
    expect(req.caption_x).toBeCloseTo(0.31);
    expect(req.caption_y).toBeCloseTo(0.7);
    for (const v of [req.caption_x, req.caption_y]) {
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(1);
    }
  });

  test("sub-epsilon nudge from default stays on the default path (no path flip on float noise)", () => {
    const req = buildRerenderRequest({
      captionX: CAPTION_DEFAULT_POSITION.x + 0.0005,
      captionY: CAPTION_DEFAULT_POSITION.y - 0.0005,
    });
    expect(req).not.toHaveProperty("caption_x");
    expect(req).not.toHaveProperty("caption_y");
  });

  test("moving only one axis still sends both coords (backend requires the pair)", () => {
    const req = buildRerenderRequest({ captionX: 0.5, captionY: 0.4 });
    expect(req.caption_x).toBeCloseTo(0.5);
    expect(req.caption_y).toBeCloseTo(0.4);
  });

  test("crop_box passes through as 0–1 fractions untouched", () => {
    const cropBox = { x: 0.05, y: 0.1, w: 0.9, h: 0.8 };
    const req = buildRerenderRequest({ cropBox, cropMode: "manual" });
    expect(req.crop_box).toEqual(cropBox);
    expect(Object.values(req.crop_box).every((v) => v >= 0 && v <= 1)).toBe(true);
  });

  test("unknown styles/formats fall back to backend-supported values", () => {
    const req = buildRerenderRequest({ style: "does-not-exist", format: "4:5" });
    expect(req.style).toBe(DEFAULT_STYLE_ID);
    expect(req.format).toBe("9:16");
  });

  test("omits optional fields the user did not set", () => {
    const req = buildRerenderRequest({});
    expect(req).not.toHaveProperty("crop_box");
    expect(req).not.toHaveProperty("transcript_edits");
    expect(req).not.toHaveProperty("caption_position");
    expect(req).not.toHaveProperty("caption_x");
    expect(req).not.toHaveProperty("caption_y");
    expect(req).not.toHaveProperty("elements");
  });

  test("captions-only draft (no visible overlays) omits elements entirely", () => {
    // caption is excluded by design; hidden overlays don't count
    const elements = [
      { id: "c", type: "caption", x: 0.5, y: 0.61, visible: true, props: {} },
      { id: "p", type: "progress", x: 0.5, y: 0.95, visible: false, props: {} },
    ];
    const req = buildRerenderRequest({ elements });
    expect(req).not.toHaveProperty("elements");
  });

  test("visible overlay elements are serialized (caption + hidden excluded), coords stay 0–1", () => {
    const elements = [
      { id: "c", type: "caption", x: 0.5, y: 0.61, visible: true, props: {} },
      { id: "p", type: "progress", x: 0.5, y: 0.96, visible: true, props: { color: "#7c3aed" } },
      { id: "h", type: "headline", x: 0.5, y: 0.14, visible: true, props: { text: "hi", color: "#22ff9c" } },
      { id: "l", type: "logo", x: 0.2, y: 0.1, visible: false, props: {} }, // hidden
    ];
    const req = buildRerenderRequest({ elements });
    expect(req.elements.map((e) => e.type)).toEqual(["progress", "headline"]);
    // normalized 0–1 preserved, never pixels
    expect(collectCoordinateViolations({ elements: req.elements })).toEqual([]);
    // pass-through: the serialized element is the original (coords untouched)
    expect(req.elements[0]).toEqual(elements[1]);
  });
});

describe("Commit 4 — untouched-caption anchor matches export contract", () => {
  // Regression gate for Known Issue 1: preview and export must center the
  // untouched caption at the SAME normalized point. This file asserts the
  // frontend half — the constants + INITIAL_ELEMENTS defaults used by the
  // preview. The backend half is asserted by tests/test_caption_positioning.py,
  // and tests/test_caption_default_position_parity.py reads THIS file's source
  // constants from the Python suite so the two surfaces cannot drift apart.
  test("CAPTION_DEFAULT_POSITION is (0.5, 0.74) — the export contract anchor", () => {
    // 0.82 → 0.74: 0.82 centred the caption 346px from the bottom of a
    // 1080x1920 frame, inside Instagram Reels' bottom overlay band. Changing
    // this literal alone is a bug — the burn constant must move with it.
    expect(CAPTION_DEFAULT_POSITION).toEqual({ x: 0.5, y: 0.74 });
  });

  test("the INITIAL caption element sits at CAPTION_DEFAULT_POSITION", () => {
    // Read from INITIAL_ELEMENTS (not the live store, which may have been
    // moved by an earlier test in this file — Zustand is a singleton). The
    // initial element is what the preview renders on a fresh /editor visit.
    const caption = INITIAL_ELEMENTS.find((el) => el.type === "caption");
    expect(caption).toBeTruthy();
    expect(caption.x).toBeCloseTo(CAPTION_DEFAULT_POSITION.x);
    expect(caption.y).toBeCloseTo(CAPTION_DEFAULT_POSITION.y);
  });
});

describe("moving the default must not move already-positioned captions", () => {
  // The mechanism: buildRerenderRequest omits caption_x/caption_y only when the
  // caption is AT the default (epsilon-guarded), and the backend falls back to
  // CAPTION_DEFAULT_{X,Y}_FRAC exactly when they are absent. So any stored
  // position that is not the current default rides the wire explicitly and wins.

  test("a caption stored at the OLD default (0.82) ships caption_y and keeps 0.82", () => {
    const req = buildRerenderRequest({ captionX: 0.5, captionY: 0.82 });
    expect(req.caption_y).toBeCloseTo(0.82);
    expect(req.caption_x).toBeCloseTo(0.5);
  });

  test("positions on BOTH sides of the new default survive the round trip", () => {
    // Above and below 0.74, including the old default — none is re-defaulted,
    // none is clamped away. The default is a default, not a bound.
    for (const y of [0.1, 0.5, 0.78, 0.82, 0.9]) {
      const req = buildRerenderRequest({ captionX: 0.5, captionY: y });
      expect(req.caption_y).toBeCloseTo(y);
    }
  });

  test("only a caption AT the new default omits the fields (backend default path)", () => {
    const req = buildRerenderRequest({
      captionX: CAPTION_DEFAULT_POSITION.x,
      captionY: CAPTION_DEFAULT_POSITION.y,
    });
    expect(req).not.toHaveProperty("caption_x");
    expect(req).not.toHaveProperty("caption_y");
  });

  test("a draft's stored caption position reaches getEditDocument unmodified", () => {
    // applyDraft → sanitizeDraftElements is a type filter only: it never merges
    // element defaults, so a persisted y is handed to the payload verbatim.
    const captionEl = {
      id: "el_caption_1", type: "caption", x: 0.42, y: 0.82,
      scale: 1, rotation: 0, visible: true, locked: false,
      props: { presetId: "bold-yellow", font: "Noto Sans Telugu", fontSize: 0.055 },
    };
    useAppStore.getState().applyDraft({ elements: [captionEl] });
    const doc = useAppStore.getState().getEditDocument();
    expect(doc.captionX).toBe(0.42);
    expect(doc.captionY).toBe(0.82);
    const req = buildRerenderRequest(doc);
    expect(req.caption_y).toBeCloseTo(0.82);
  });
});

describe("BUG-001 partial fix — caption Size / Pill reach the export payload", () => {
  // Pre-fix, the Inspector's Size slider and Background Pill toggle updated
  // only the preview; the outgoing rerender payload never carried them, so
  // the burned export ignored both edits (WYSIWYG break). Guards the
  // frontend half of the wired chain — the backend half is asserted by
  // tests/test_caption_size_pill.py.

  test("captionFontSize is serialized as caption_font_size only when set", () => {
    // Omitted → payload key absent (byte-identical to today's export).
    const bare = buildRerenderRequest({});
    expect("caption_font_size" in bare).toBe(false);

    // Set → payload carries it.
    const withSize = buildRerenderRequest({ captionFontSize: 0.075 });
    expect(withSize.caption_font_size).toBeCloseTo(0.075);
  });

  test("caption_pill only serializes when the pill is enabled", () => {
    // No pill at all → key absent.
    expect("caption_pill" in buildRerenderRequest({})).toBe(false);
    // Pill toggled off → key still absent (equivalent to no pill).
    const offReq = buildRerenderRequest({
      captionPill: { enabled: false, color: "#ff0000", opacity: 1, padding: 8, radius: 4 },
    });
    expect("caption_pill" in offReq).toBe(false);

    // Enabled → payload carries the exact snake-case shape the API expects.
    // Feature #4: padding/radius ride the wire as fractions of canvas height;
    // legacy absolute-px values (this draft predates the unit change) are
    // normalized against the 640px 9:16 stage exactly once (lib/pillUnits.js).
    const onReq = buildRerenderRequest({
      captionPill: { enabled: true, color: "#7c3aed", opacity: 0.6, padding: 10, radius: 6 },
    });
    expect(onReq.caption_pill).toEqual({
      enabled: true,
      color: "#7c3aed",
      opacity: 0.6,
      padding: 10 / 640,
      radius: 6 / 640,
    });

    // Already-fraction values (new-unit drafts) pass through untouched.
    const fracReq = buildRerenderRequest({
      captionPill: { enabled: true, color: "#7c3aed", opacity: 0.6, padding: 8 / 640, radius: 8 / 640 },
    });
    expect(fracReq.caption_pill.padding).toBeCloseTo(8 / 640, 10);
    expect(fracReq.caption_pill.radius).toBeCloseTo(8 / 640, 10);
  });

  test("size + pill are ignored for zero/negative fontSize (guard against jitter)", () => {
    const req = buildRerenderRequest({
      captionFontSize: 0,
      captionPill: { enabled: false, color: "#000000", opacity: 1, padding: 0, radius: 0 },
    });
    expect("caption_font_size" in req).toBe(false);
    expect("caption_pill" in req).toBe(false);
  });
});

describe("Caption font dropdown — selection reaches the export payload", () => {
  // The Inspector's Font dropdown offers exactly the three bundled caption
  // fonts. A non-default pick must serialize as caption_font; the default and
  // any legacy/unknown value must be OMITTED so the backend renders its own
  // default (byte-identical to before the dropdown existed). The backend half
  // (ASS Fontname) is asserted in tests/test_caption_font.py.

  test("a non-default caption font is serialized as caption_font", () => {
    expect(buildRerenderRequest({ captionFont: "Ramabhadra" }).caption_font).toBe("Ramabhadra");
    expect(buildRerenderRequest({ captionFont: "Mandali" }).caption_font).toBe("Mandali");
  });

  test("the default caption font (Noto Sans Telugu) is omitted", () => {
    // Omitting the default keeps a default export identical to today's payload.
    expect("caption_font" in buildRerenderRequest({ captionFont: "Noto Sans Telugu" })).toBe(false);
  });

  test("no selection at all omits caption_font", () => {
    expect("caption_font" in buildRerenderRequest({})).toBe(false);
    expect("caption_font" in buildRerenderRequest({ captionFont: null })).toBe(false);
  });

  test("an unknown/legacy font value is omitted (backend falls back to default)", () => {
    // Old drafts carried non-caption fonts like "Outfit"; never send those —
    // the backend would only fall back anyway, and omitting avoids drift.
    expect("caption_font" in buildRerenderRequest({ captionFont: "Outfit" })).toBe(false);
  });

  test("getEditDocument exposes the caption element's font as captionFont", () => {
    const store = useAppStore.getState();
    const caption = store.getCaptionElement();
    store.updateElementProps(caption.id, { font: "Mandali" });
    const doc = useAppStore.getState().getEditDocument();
    expect(doc.captionFont).toBe("Mandali");
    // …and it flows through into the rerender payload.
    const req = buildRerenderRequest({ captionFont: doc.captionFont });
    expect(req.caption_font).toBe("Mandali");
  });
});
