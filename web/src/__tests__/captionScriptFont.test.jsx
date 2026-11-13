/**
 * Feature #16 — script-aware caption font wiring in the store.
 *
 * Telugu script can only render a Telugu font; Tanglish only a Latin one (the
 * other script tofus). Two store actions enforce this:
 *   - setCaptionScript reconciles the caption element's font on toggle
 *     (invalid-for-script → that script's default), coalesced with the script
 *     flip into ONE undo entry.
 *   - setCaptionPreset seeds the preset's recommended Latin font in Tanglish
 *     mode; in Telugu mode presets are colour-only (font untouched).
 *
 * Mirrors services/fonts.py :: resolve_caption_font and the backend STYLES.
 */
import { useAppStore } from "@/store/useAppStore";
import {
  DEFAULT_CAPTION_FONT,
  DEFAULT_LATIN_CAPTION_FONT,
  presetLatinFont,
} from "@/api/renders";

// A minimal caption element in the store's element shape.
const captionEl = (font = "Noto Sans Telugu", presetId = "bold-yellow") => ({
  id: "el_caption",
  type: "caption",
  x: 0.5,
  // Arbitrary sample position — this suite is about font resolution, not
  // placement. Deliberately NOT the default anchor, so it cannot silently
  // track CAPTION_DEFAULT_POSITION.
  y: 0.61,
  scale: 1,
  rotation: 0,
  visible: true,
  locked: false,
  props: {
    presetId,
    font,
    fontSize: 0.055,
    animation: "karaoke",
    pill: { enabled: false, color: "#000000", opacity: 0.55, padding: 0.01, radius: 0.01 },
  },
});

const reset = (font, presetId) => {
  useAppStore.setState({
    elements: [captionEl(font, presetId)],
    exportSettings: { ...useAppStore.getState().exportSettings, captionScript: "telugu" },
    history: { past: [], future: [] },
  });
};

const getFont = () => useAppStore.getState().getCaptionElement().props.font;
const getScript = () => useAppStore.getState().exportSettings.captionScript;

beforeEach(() => reset());

describe("setCaptionScript reconciles the caption font", () => {
  test("telugu → tanglish snaps a Telugu font to the Latin default (Montserrat)", () => {
    reset("Ramabhadra");
    useAppStore.getState().setCaptionScript("tanglish");
    expect(getScript()).toBe("tanglish");
    expect(getFont()).toBe(DEFAULT_LATIN_CAPTION_FONT);
    expect(getFont()).toBe("Montserrat");
  });

  test("tanglish → telugu snaps a Latin font back to the Telugu default (Noto)", () => {
    reset("Anton");
    // start in tanglish (font already valid there)
    useAppStore.setState((s) => ({
      exportSettings: { ...s.exportSettings, captionScript: "tanglish" },
    }));
    useAppStore.getState().setCaptionScript("telugu");
    expect(getFont()).toBe(DEFAULT_CAPTION_FONT);
    expect(getFont()).toBe("Noto Sans Telugu");
  });

  test("a font already valid for the target script is left untouched", () => {
    reset("Mandali");
    // switching to tanglish then back leaves a still-valid Telugu font as-is
    useAppStore.getState().setCaptionScript("tanglish"); // → Montserrat
    useAppStore.setState((s) => ({ // pretend user picked Bebas Neue
      elements: [{ ...s.getCaptionElement(), props: { ...s.getCaptionElement().props, font: "Bebas Neue" } }],
    }));
    // Bebas Neue is valid in tanglish → no reconcile on a no-op re-set
    const before = getFont();
    useAppStore.getState().setCaptionScript("tanglish"); // same script → early return
    expect(getFont()).toBe(before);
  });

  test("script flip + font reconcile is ONE undo entry", () => {
    reset("Ramabhadra");
    useAppStore.getState().setCaptionScript("tanglish");
    expect(getScript()).toBe("tanglish");
    expect(getFont()).toBe("Montserrat");
    // A single undo restores BOTH the Telugu script and the Telugu font.
    useAppStore.getState().undo();
    expect(getScript()).toBe("telugu");
    expect(getFont()).toBe("Ramabhadra");
  });
});

describe("setCaptionPreset seeds the recommended Latin font in Tanglish", () => {
  test("tanglish: picking 'punch' adopts its Latin font (Anton)", () => {
    reset("Montserrat");
    useAppStore.setState((s) => ({
      exportSettings: { ...s.exportSettings, captionScript: "tanglish" },
    }));
    useAppStore.getState().setCaptionPreset("punch");
    expect(useAppStore.getState().getCaptionElement().props.presetId).toBe("punch");
    expect(getFont()).toBe(presetLatinFont("punch"));
    expect(getFont()).toBe("Anton");
  });

  test("telugu: picking a preset does NOT change the Telugu font", () => {
    reset("Ramabhadra");
    useAppStore.getState().setCaptionPreset("punch");
    expect(useAppStore.getState().getCaptionElement().props.presetId).toBe("punch");
    expect(getFont()).toBe("Ramabhadra"); // font untouched in Telugu mode
  });

  test("tanglish: a legacy preset with no latinFont leaves the font as-is", () => {
    reset("Bebas Neue");
    useAppStore.setState((s) => ({
      exportSettings: { ...s.exportSettings, captionScript: "tanglish" },
    }));
    // 'bold-yellow' is a legacy preset (no latinFont) → font stays Bebas Neue
    useAppStore.getState().setCaptionPreset("bold-yellow");
    expect(getFont()).toBe("Bebas Neue");
  });
});
