/**
 * Regression suite for the backend ASS-color decode used by the caption
 * canvas preview. Written after a real visual bug report ("dark, barely
 * visible captions") whose prime suspect was an inverted alpha channel.
 * Re-verification (hand arithmetic, an independent Python cross-check, and
 * this suite) all confirm the SAME convention: 0x00 = opaque, 0xFF =
 * transparent. The actual bug was -webkit-text-stroke overwhelming dense
 * Telugu glyphs, not the color decode — see ElementRenderer.jsx's CaptionBody.
 */
import { decodeAssColor } from "@/lib/assColor";
import { RAW_STYLES, CAPTION_STYLE_PREVIEW, getCaptionFontStack } from "@/data/captionStylePreview";

describe("decodeAssColor — the &HAABBGGRR parser itself", () => {
  test("alpha byte 0x00 is fully opaque, 0xFF is fully transparent", () => {
    expect(decodeAssColor("&H00FFFFFF").opacity).toBe(1);
    expect(decodeAssColor("&HFFFFFFFF").opacity).toBe(0);
  });

  test("mid-range alpha bytes invert correctly (byte/255, then 1-x)", () => {
    // 0xCC = 204 -> 1 - 204/255 = 0.2 (near-transparent, not near-opaque)
    expect(decodeAssColor("&HCC000000").opacity).toBeCloseTo(0.2, 2);
    // 0x14 = 20 -> 1 - 20/255 = 0.922 (near-opaque)
    expect(decodeAssColor("&H14000000").opacity).toBeCloseTo(0.922, 2);
  });

  test("BGR byte order is reversed to RGB hex", () => {
    // &H0000FFFF: AA=00 BB=00 GG=FF RR=FF -> RGB(255,255,0) = yellow
    expect(decodeAssColor("&H0000FFFF").hex).toBe("#ffff00");
    // &H000000FF: AA=00 BB=00 GG=00 RR=FF -> RGB(255,0,0) = red
    expect(decodeAssColor("&H000000FF").hex).toBe("#ff0000");
    // &H0041FF00: AA=00 BB=41 GG=FF RR=00 -> RGB(0,255,65)
    expect(decodeAssColor("&H0041FF00").hex).toBe("#00ff41");
  });
});

describe("CAPTION_STYLE_PREVIEW — sanity-checked against style names, not just math", () => {
  test("bold-yellow: bright yellow highlight on white/grey text", () => {
    const s = CAPTION_STYLE_PREVIEW["bold-yellow"];
    expect(s.colorHighlight).toBe("#ffff00");
    expect(s.colorUnspoken).toBe("#ffffff");
    expect(s.colorSpoken).toBe("#aaaaaa");
    expect(s.box).toBeNull(); // border_style 1 — no box
  });

  test("white-minimal: white is reserved for the active word only", () => {
    const s = CAPTION_STYLE_PREVIEW["white-minimal"];
    expect(s.colorHighlight).toBe("#ffffff");
    expect(s.box).toBeNull();
  });

  test("red-pop: red highlight", () => {
    expect(CAPTION_STYLE_PREVIEW["red-pop"].colorHighlight).toBe("#ff0000");
  });

  test("clean-dark: cyan/blue highlight (backend comment says 'Cyan')", () => {
    expect(CAPTION_STYLE_PREVIEW["clean-dark"].colorHighlight).toBe("#00d7ff");
  });

  test("hormozi: gold/yellow highlight, opaque box (matches the well-known Hormozi caption style)", () => {
    const s = CAPTION_STYLE_PREVIEW.hormozi;
    expect(s.colorHighlight).toBe("#ffe500");
    expect(s.box).not.toBeNull();
    expect(s.box.background).toMatch(/0\.92/);
  });

  test("fire-gradient: orange highlight (dominant color, ASS has no gradients)", () => {
    expect(CAPTION_STYLE_PREVIEW["fire-gradient"].colorHighlight).toBe("#ff6b00");
  });

  test("neon-green: bright green highlight, dark green spoken, opaque-ish box", () => {
    const s = CAPTION_STYLE_PREVIEW["neon-green"];
    expect(s.colorHighlight).toBe("#00ff41");
    expect(s.colorSpoken).toBe("#006616");
    expect(s.box).not.toBeNull();
  });

  test("outline-only: white text, no background", () => {
    const s = CAPTION_STYLE_PREVIEW["outline-only"];
    expect(s.colorHighlight).toBe("#ffffff");
    expect(s.box).toBeNull();
  });

  test("big-bold: gold highlight, no background", () => {
    const s = CAPTION_STYLE_PREVIEW["big-bold"];
    expect(s.colorHighlight).toBe("#ffd700");
    expect(s.box).toBeNull();
  });

  test("typewriter: exact Courier New match, future words literally invisible", () => {
    const s = CAPTION_STYLE_PREVIEW.typewriter;
    expect(s.fontFamily).toMatch(/Courier New/);
    expect(s.colorUnspoken).toBe("transparent");
    expect(s.colorSpoken).toBe("#ffffff");
  });

  test("split-color: pink/magenta highlight", () => {
    expect(CAPTION_STYLE_PREVIEW["split-color"].colorHighlight).toBe("#ff6ec7");
  });

  test("every style resolves to a defined, non-black-on-black text color", () => {
    for (const [id, style] of Object.entries(CAPTION_STYLE_PREVIEW)) {
      expect(style.colorHighlight).toBeTruthy();
      expect(style.colorUnspoken === "transparent" || style.colorUnspoken).toBeTruthy();
      // Black text is only legible ON A LIGHT BOX. The "classic" preset
      // (feature #16) deliberately burns black text onto an opaque white box —
      // legit and legible. Every BOX-LESS style must still use bright text so
      // it reads on dark video.
      if (style.box) continue;
      expect(style.colorHighlight).not.toBe("#000000");
      if (style.colorUnspoken !== "transparent") {
        expect(style.colorUnspoken).not.toBe("#000000");
      }
    }
  });
});

describe("red-pop / clean-dark box opacity (KNOWN_ISSUES.md finding)", () => {
  test("both decode to a fairly transparent box despite comments claiming otherwise", () => {
    const redPop = decodeAssColor(RAW_STYLES["red-pop"].backColor);
    const cleanDark = decodeAssColor(RAW_STYLES["clean-dark"].backColor);
    expect(redPop.opacity).toBeCloseTo(0.2, 2);
    expect(cleanDark.opacity).toBeCloseTo(0.133, 2);
    // For contrast: styles whose comments DO match their decoded opacity.
    expect(decodeAssColor(RAW_STYLES.hormozi.backColor).opacity).toBeCloseTo(0.922, 2);
    expect(decodeAssColor(RAW_STYLES["neon-green"].backColor).opacity).toBeCloseTo(0.878, 2);
  });
});

describe("Deterministic Telugu font stack (Known Issue 2 — WYSIWYG preview)", () => {
  // Regression gate for the "preview doesn't match export on machines without
  // Noto Sans Telugu installed" bug. The bundled Noto/Ramabhadra/Mandali .ttf
  // files are now loaded via @font-face from /fonts/* (see index.css), which
  // are symlinks to services/assets/fonts/* — the same .ttf the backend hands
  // libass via `fontsdir`. So the preview must:
  //   1. put the deterministic web-font family FIRST in the stack, and
  //   2. NOT include OS-fallback Telugu fonts (Nirmala UI etc.) that render
  //      with different metrics from Noto and reintroduce the drift.
  test("every non-typewriter preset leads with Noto Sans Telugu", () => {
    for (const [id, style] of Object.entries(CAPTION_STYLE_PREVIEW)) {
      if (id === "typewriter") continue;
      expect(style.fontFamily.startsWith('"Noto Sans Telugu"')).toBe(true);
    }
  });

  test("Telugu stack does not silently fall back to a non-Noto Telugu font", () => {
    for (const style of Object.values(CAPTION_STYLE_PREVIEW)) {
      // Nirmala UI is the Windows Telugu default — historically it rendered
      // when Noto was absent, silently drifting the preview from the export.
      expect(style.fontFamily).not.toMatch(/Nirmala/i);
    }
  });

  test("getCaptionFontStack exposes the three bundled families and defaults safely", () => {
    expect(getCaptionFontStack("Noto Sans Telugu")).toMatch(/^"Noto Sans Telugu"/);
    expect(getCaptionFontStack("Ramabhadra")).toMatch(/^"Ramabhadra"/);
    expect(getCaptionFontStack("Mandali")).toMatch(/^"Mandali"/);
    // Unknown / undefined → deterministic Noto stack (never OS Telugu font).
    expect(getCaptionFontStack("SomeUnknown")).toMatch(/^"Noto Sans Telugu"/);
    expect(getCaptionFontStack(undefined)).toMatch(/^"Noto Sans Telugu"/);
  });

  test("getCaptionFontStack leads with each bundled Latin family (feature #16)", () => {
    // Tanglish mode selects one of the 6 bundled Latin display fonts; each
    // stack must lead with its own @font-face family (byte-identical .ttf to
    // the burn) before any OS fallback.
    expect(getCaptionFontStack("Montserrat")).toMatch(/^"Montserrat"/);
    expect(getCaptionFontStack("Anton")).toMatch(/^"Anton"/);
    expect(getCaptionFontStack("Bebas Neue")).toMatch(/^"Bebas Neue"/);
    expect(getCaptionFontStack("Oswald")).toMatch(/^"Oswald"/);
    expect(getCaptionFontStack("Poppins")).toMatch(/^"Poppins"/);
    expect(getCaptionFontStack("Inter")).toMatch(/^"Inter"/);
  });
});
