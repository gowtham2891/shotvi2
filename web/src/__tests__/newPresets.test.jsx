/**
 * Feature #16 (research-grounded) — the 10 new caption presets (Replix's 9 +
 * the market Hormozi formula) exist in the dropdown AND the preview, their
 * highlight colours decode to the intended hex, and the new script-aware flags
 * (uppercase / glow / bgOff) mirror services/caption_renderer.py :: STYLES.
 */
import {
  CAPTION_STYLES,
  isKnownStyle,
  presetLatinFont,
  PREMIUM_PRESET_IDS,
  LATIN_CAPTION_FONTS,
} from "@/api/renders";
import { CAPTION_STYLE_PREVIEW } from "@/data/captionStylePreview";
import { PILL_DEFAULTS_BY_PRESET } from "@/data/mockData";

// Highlight colour each new preset decodes to (ASS &HAABBGGRR -> #RRGGBB).
const HIGHLIGHT = {
  classic: "#000000",
  yellow: "#ffff00",
  minimal: "#ffffff",
  dark: "#ffff00",
  punch: "#ec4893",
  cove: "#40d0e0",
  spotlight: "#ffff00",
  reel: "#ffff00",
  noir: "#ffffff",
  "hormozi-caps": "#ffff00",
};

const NEW_IDS = Object.keys(HIGHLIGHT);

describe("feature #16 caption presets", () => {
  test("all 10 are in the dropdown and recognized by isKnownStyle", () => {
    for (const id of NEW_IDS) {
      expect(CAPTION_STYLES.some((s) => s.id === id)).toBe(true);
      expect(isKnownStyle(id)).toBe(true);
    }
    // 11 original + 10 new = 21 (mirrors backend STYLES count).
    expect(CAPTION_STYLES.length).toBe(21);
  });

  test("each new preset's highlight colour decodes to the intended hex", () => {
    for (const [id, hex] of Object.entries(HIGHLIGHT)) {
      expect(CAPTION_STYLE_PREVIEW[id]).toBeTruthy();
      expect(CAPTION_STYLE_PREVIEW[id].colorHighlight).toBe(hex);
    }
  });

  test("bg-on presets expose a box; bg-off presets are null (border_style parity)", () => {
    // border_style 4 (feature #16 boxes) and 3 render a box; 1 does not.
    const boxed = ["classic", "dark", "punch", "cove"];
    const noBox = ["yellow", "minimal", "spotlight", "reel", "noir", "hormozi-caps"];
    for (const id of boxed) expect(CAPTION_STYLE_PREVIEW[id].box).not.toBeNull();
    for (const id of noBox) expect(CAPTION_STYLE_PREVIEW[id].box).toBeNull();
  });

  test("bgOff flag is the inverse of having a box (animation eligibility)", () => {
    for (const id of NEW_IDS) {
      const p = CAPTION_STYLE_PREVIEW[id];
      expect(p.bgOff).toBe(p.box === null);
    }
  });

  test("ALL-CAPS presets carry uppercase; only spotlight glows", () => {
    const caps = new Set(["punch", "reel", "noir", "hormozi-caps"]);
    for (const id of NEW_IDS) {
      expect(CAPTION_STYLE_PREVIEW[id].uppercase).toBe(caps.has(id));
      expect(CAPTION_STYLE_PREVIEW[id].glow).toBe(id === "spotlight");
    }
  });

  test("classic's box is WHITE (back-colour-derived, not hardcoded black)", () => {
    // classic: back_color &H00FFFFFF -> opaque white box behind black text.
    expect(CAPTION_STYLE_PREVIEW.classic.box.background).toMatch(/rgba\(255,255,255,/);
  });

  test("each new preset recommends a bundled Latin font for Tanglish mode", () => {
    for (const id of NEW_IDS) {
      const latin = presetLatinFont(id);
      expect(latin).toBeTruthy();
      expect(LATIN_CAPTION_FONTS).toContain(latin);
    }
  });

  test("each new preset carries a PILL_DEFAULTS entry that reconciles the box", () => {
    // Without an entry, selecting a preset leaves the previously-selected pill
    // in place (the QA bug: no-box presets kept a box, Classic's black text sat
    // on a stale black pill and vanished). boxed presets enable a pill matching
    // their box; bg-off presets disable it so preview.box (null) shows nothing.
    for (const id of NEW_IDS) {
      const pill = PILL_DEFAULTS_BY_PRESET[id];
      expect(pill).toBeTruthy();
      const boxed = CAPTION_STYLE_PREVIEW[id].box !== null; // classic/dark/punch/cove
      expect(pill.enabled).toBe(boxed);
    }
    // Classic's reconciled pill is an opaque WHITE box (black text needs it).
    expect(PILL_DEFAULTS_BY_PRESET.classic.enabled).toBe(true);
    expect(PILL_DEFAULTS_BY_PRESET.classic.color.toLowerCase()).toBe("#ffffff");
    expect(PILL_DEFAULTS_BY_PRESET.classic.opacity).toBe(1.0);
  });

  test("premium set matches api/tiers (punch/cove/spotlight/reel/noir)", () => {
    expect([...PREMIUM_PRESET_IDS].sort()).toEqual(
      ["cove", "noir", "punch", "reel", "spotlight"]
    );
  });
});
