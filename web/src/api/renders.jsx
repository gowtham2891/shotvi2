import { client, toApiError } from "@/api/client";
import { serializeTranscriptEdits } from "@/lib/transcriptEdits";
import { normalizePillUnits } from "@/lib/pillUnits";

// ── Caption styles ───────────────────────────────────────────────
// The backend exposes no styles endpoint; these ids mirror STYLES in
// services/caption_renderer.py exactly. Sending an unknown id would make the
// backend fall back silently, so the UI must only offer these.
// No hand-maintained swatch color here — the Inspector derives each button's
// swatch from captionStylePreview.js's decoded colorHighlight, so it can
// never drift from what the canvas (and export) actually renders.
export const CAPTION_STYLES = [
  { id: "bold-yellow", name: "Bold Yellow" },
  { id: "white-minimal", name: "White Minimal" },
  { id: "red-pop", name: "Red Pop" },
  { id: "clean-dark", name: "Clean Dark" },
  { id: "hormozi", name: "Hormozi" },
  { id: "fire-gradient", name: "Fire Gradient" },
  { id: "neon-green", name: "Neon Green" },
  { id: "outline-only", name: "Outline Only" },
  { id: "big-bold", name: "Big Bold" },
  { id: "typewriter", name: "Typewriter" },
  { id: "split-color", name: "Split Color" },
  // Feature #16 (research-grounded) — Replix's 9 named presets + the market
  // "Hormozi formula". Each maps to a recommended Latin font for Tanglish mode
  // (latinFont); Telugu mode keeps the user's Telugu pick.
  { id: "classic", name: "Classic", latinFont: "Poppins" },
  { id: "yellow", name: "Yellow", latinFont: "Poppins" },
  { id: "minimal", name: "Minimal", latinFont: "Inter" },
  { id: "dark", name: "Dark", latinFont: "Montserrat" },
  { id: "punch", name: "Punch", latinFont: "Anton" },
  { id: "cove", name: "Cove", latinFont: "Poppins" },
  { id: "spotlight", name: "Spotlight", latinFont: "Montserrat" },
  { id: "reel", name: "Reel", latinFont: "Bebas Neue" },
  { id: "noir", name: "Noir", latinFont: "Oswald" },
  { id: "hormozi-caps", name: "Hormozi Caps", latinFont: "Montserrat" },
];
export const DEFAULT_STYLE_ID = "bold-yellow";
export const isKnownStyle = (id) => CAPTION_STYLES.some((s) => s.id === id);
export const presetLatinFont = (id) =>
  CAPTION_STYLES.find((s) => s.id === id)?.latinFont || null;

// Feature #21 — premium presets (mirror of api/tiers.PREMIUM_PRESETS). Free
// users SEE them in the gallery but the export gate (backend 402) blocks
// exporting with one; the Inspector marks them "PRO". Mirrors Replix's own
// free/paid split.
export const PREMIUM_PRESET_IDS = new Set([
  "punch", "cove", "spotlight", "reel", "noir",
]);
export const isPremiumPreset = (id) => PREMIUM_PRESET_IDS.has(id);

// ── Caption fonts (script-aware — feature #16) ───────────────────
// Telugu-script captions use the 3 bundled Telugu fonts; Tanglish (Latin
// script) captions use the 6 bundled Latin display fonts. Both sets resolve
// deterministically via libass fontsdir and load in the preview from the SAME
// .ttf via public/fonts.css. Mirrors services/fonts.py exactly.
export const TELUGU_CAPTION_FONTS = [
  "Noto Sans Telugu", "Ramabhadra", "Mandali", "NTR", "Mallanna", "Tenali Ramakrishna",
  // Expanded library — bold display faces first, then varied regulars.
  "Baloo Tammudu 2", "Hind Guntur", "Anek Telugu", "Chathura",
  "Sirivennela", "Ponnala", "Akaya Telivigala", "Suranna", "Sree Krushnadevaraya",
  "Ravi Prakash", "Lakki Reddy", "Gidugu", "Gurajada", "Ramaraja",
  "Dhurjati", "Peddana", "Timmana", "Suravaram",
];
export const LATIN_CAPTION_FONTS = ["Montserrat", "Anton", "Bebas Neue", "Oswald", "Poppins", "Inter"];
export const DEFAULT_CAPTION_FONT = "Noto Sans Telugu";       // Telugu mode default
export const DEFAULT_LATIN_CAPTION_FONT = "Montserrat";       // Tanglish default (Montserrat Black)

// Legacy alias: the full set (both scripts). isKnownCaptionFont stays permissive.
export const CAPTION_FONTS = [...TELUGU_CAPTION_FONTS, ...LATIN_CAPTION_FONTS];
export const isKnownCaptionFont = (name) => CAPTION_FONTS.includes(name);
export const isLatinCaptionFont = (name) => LATIN_CAPTION_FONTS.includes(name);

// The dropdown list for a given script.
export const fontsForScript = (script) =>
  script === "tanglish" ? LATIN_CAPTION_FONTS : TELUGU_CAPTION_FONTS;

// Script-aware resolution mirror (services/fonts.py :: resolve_caption_font):
// a font invalid for the active script falls back to that script's default,
// NEVER the other script's set (which would tofu).
export const resolveCaptionFont = (name, script) => {
  if (script === "tanglish") {
    return LATIN_CAPTION_FONTS.includes(name) ? name : DEFAULT_LATIN_CAPTION_FONT;
  }
  return TELUGU_CAPTION_FONTS.includes(name) ? name : DEFAULT_CAPTION_FONT;
};

// Formats supported by FORMAT_CONFIG in api/worker.py. Output is always mp4
// at fixed 1080-based canvas sizes — resolution/container are NOT selectable.
export const EXPORT_FORMATS = ["9:16", "1:1", "16:9"];
export const BACKGROUND_OPTIONS = ["blur", "black", "white", "color"];

// Caption default center position — THE frontend source of truth. useAppStore.js
// imports this module (never the reverse — that would be circular), so
// defaultElementForType('caption') and getEditDocument's captionY fallback both
// read from here. The one remaining hand-copy is INITIAL_ELEMENTS in
// data/mockData.js, a leaf data module kept free of api/ imports; it is pinned to
// this constant by editDocument.test.js.
//
// y = 0.74 (was 0.82): 0.82 centered the caption 346px from the bottom of a
// 1080x1920 frame — inside Instagram Reels' bottom overlay zone (~310-450px,
// widened ~50px in late 2025 by the audio bar) and inside the editor's own
// Instagram safe-zone guide, which tints y > 0.8 red. 0.74 → 499px, clear of it.
// This is a DEFAULT only: clampToFrame.js bounds drags by the caption's measured
// bounding box and never reads this value, so the user can still drag to 0.82 or
// lower. Backend mirror: CAPTION_DEFAULT_{X,Y}_FRAC in services/caption_renderer.py
// (parity enforced by tests/test_caption_default_position_parity.py).
export const CAPTION_DEFAULT_POSITION = { x: 0.5, y: 0.74 };
const CAPTION_POS_EPS = 1e-3;

// Overlay element types the backend can burn (services/overlay_renderer.py ::
// _PREPARERS). The `caption` element is intentionally excluded — its position
// flows via caption_x/caption_y and the backend burns captions in a separate
// pass (render_elements ignores non-overlay types anyway; excluding here just
// keeps the payload clean). `image` carries an opaque image_id the backend
// resolves + burns through the same single composite pass. `emoji` (feature
// #30) carries a palette emoji + a [start,end] display window the backend
// composites time-bounded (overlay enable='between(t,start,end)').
export const OVERLAY_ELEMENT_TYPES = new Set(["progress", "logo", "headline", "image", "emoji"]);

// ── EditDocument → RerenderRequest ───────────────────────────────
//
// COORDINATE CONTRACT: the editor stores all positions as 0–1 CENTER fractions of
// canvas size, and they stay normalized in every outgoing payload — never pixels.
// Stage 6: the caption's center x/y flow as caption_x/caption_y (0–1); the backend
// converts to pixels at render resolution (canvas_coords.to_pixel_center). They are
// sent ONLY when the caption was dragged off its default (epsilon-guarded) — an
// untouched caption omits them so the backend renders its default path, byte-
// identical to today. crop_box is already 0–1 fractions on both sides.
export function buildRerenderRequest(editDoc = {}) {
  const {
    style = DEFAULT_STYLE_ID,
    format = "9:16",
    background = "blur",
    bgColor = "#000000",
    useAutocrop = true,
    trimStart = 0.0,
    trimEnd = -1.0,
    cropMode = "auto",
    cropBox = null, // {x, y, w, h} as 0–1 fractions
    selectedSubject = null,
    transcriptEdits = null, // STORE shape: {wordEdits: {[wordId]: {text, …}}, mergedGroups, lineSplits} — serialized to the backend list shape below
    captionFont = null, // one of CAPTION_FONTS; sent as caption_font only when non-default (see below)
    captionX = null, // 0–1 fraction of canvas width (caption center)
    captionY = null, // 0–1 fraction of canvas height (caption center)
    // BUG-001 partial fix — caption Size (0–1 fraction of canvas height, same
    // units the preview scales by) and Background Pill (or null when no pill).
    // Sent to the backend as caption_font_size and caption_pill; when both are
    // null / left at defaults, the payload OMITS them so old backends keep
    // rendering the preset defaults byte-identically to today.
    captionFontSize = null,
    captionPill = null,
    // Telugu ⇄ Tanglish toggle: sent as caption_script ONLY when 'tanglish'.
    // 'telugu' is the backend default, so omitting it keeps script-untouched
    // export payloads byte-identical to before the toggle existed; junk
    // values (old drafts) are also omitted → backend renders telugu.
    captionScript = null,
    elements = null, // full EditDocument elements (0–1 coords); filtered below
    // Feature #6 — the EFFECTIVE emphasis set (clip auto set ∪ user toggles),
    // clip-local raw indices. Sent as emphasis_indices whenever it's an array;
    // null/undefined (pre-feature callers) omits the field → the backend
    // falls back to the clip's own Gemini-tagged set.
    emphasisIndices = null,
    // Feature #13 — animated punch-in crop keyframes [{time,x,y,w,h}].
    // Non-empty → sent as crop_keyframes; empty/absent omits the field so a
    // zoom-free export payload stays byte-identical to before.
    cropKeyframes = null,
    // Feature #14 — filler/silence cut spans [[start,end],...] clip-local.
    // Non-empty → sent as cut_spans; empty/null omits it (no cuts).
    cutSpans = null,
    // Feature #15 — caption reveal animation. 'karaoke' is the backend
    // default, so omit it to keep animation-untouched payloads byte-identical.
    captionAnimation = null,
  } = editDoc;

  const req = {
    style: isKnownStyle(style) ? style : DEFAULT_STYLE_ID,
    format: EXPORT_FORMATS.includes(format) ? format : "9:16",
    background,
    bg_color: bgColor,
    use_autocrop: useAutocrop,
    trim_start: trimStart,
    trim_end: trimEnd,
    crop_mode: cropMode,
  };
  if (cropBox) req.crop_box = cropBox;
  if (selectedSubject) req.selected_subject = selectedSubject;
  // Caption font: serialize ONLY when it's a known caption font other than the
  // default. The default (Noto Sans Telugu) is what the backend renders when
  // caption_font is omitted, so leaving it out keeps default exports byte-
  // identical; an unknown/legacy value (e.g. an old draft's "Outfit") is also
  // omitted so the backend falls back to its own default rather than crashing.
  if (isKnownCaptionFont(captionFont) && captionFont !== DEFAULT_CAPTION_FONT) {
    req.caption_font = captionFont;
  }
  // Transcript edits cross the wire in the backend's TranscriptEdits shape
  // (wordEdits as a ref-addressed LIST — api/models.py:39-42), never the
  // store's id-keyed dict: pydantic 422s on a dict, even an empty one. The
  // serializer returns null when there's nothing to send, so the field is
  // omitted and an edit-free export payload stays byte-identical to today.
  const wireTranscriptEdits = serializeTranscriptEdits(transcriptEdits);
  if (wireTranscriptEdits) req.transcript_edits = wireTranscriptEdits;
  // Feature #6: an explicit array — including [] (user removed every
  // emphasis) — is the editor's final say and always crosses the wire.
  if (Array.isArray(emphasisIndices)) {
    req.emphasis_indices = emphasisIndices.filter(Number.isInteger);
  }
  // Feature #13: only send crop_keyframes when there's an actual zoom to
  // apply — an empty list is the same as "no zoom", so omit it to keep
  // zoom-free payloads byte-identical.
  if (Array.isArray(cropKeyframes) && cropKeyframes.length) {
    req.crop_keyframes = cropKeyframes;
  }
  // Feature #14: only send cut_spans when there are actual cuts.
  if (Array.isArray(cutSpans) && cutSpans.length) {
    req.cut_spans = cutSpans;
  }
  // Feature #15: send caption_animation only for a non-default (non-karaoke)
  // preset, so karaoke exports stay byte-identical to before this existed.
  const KNOWN_ANIMS = ["karaoke", "none", "pop", "fade", "slide-up"];
  if (KNOWN_ANIMS.includes(captionAnimation) && captionAnimation !== "karaoke") {
    req.caption_animation = captionAnimation;
  }
  // Send caption center only when moved from default (both coords required by the
  // backend); epsilon stops float-noise from flipping default↔positioned paths.
  if (captionX != null && captionY != null) {
    const moved =
      Math.abs(captionX - CAPTION_DEFAULT_POSITION.x) >= CAPTION_POS_EPS ||
      Math.abs(captionY - CAPTION_DEFAULT_POSITION.y) >= CAPTION_POS_EPS;
    if (moved) {
      req.caption_x = captionX;
      req.caption_y = captionY;
    }
  }
  // BUG-001 partial fix — carry the caption's editor size + background pill.
  // Only serialize when set (non-null) so old drafts / captions-untouched
  // exports produce the pre-fix payload byte-for-byte, keeping every existing
  // regression test green.
  if (typeof captionFontSize === "number" && captionFontSize > 0) {
    req.caption_font_size = captionFontSize;
  }
  if (captionScript === "tanglish") {
    req.caption_script = "tanglish";
  }
  if (captionPill && captionPill.enabled) {
    // Snake-case for the API; only the fields the backend consumes.
    // Feature #4: padding/radius ride the wire as fractions of canvas height
    // (normalized here so legacy absolute-px drafts convert exactly once);
    // the backend scales them by the real render height.
    const pillN = normalizePillUnits(captionPill);
    req.caption_pill = {
      enabled: true,
      color: pillN.color,
      opacity: pillN.opacity,
      padding: pillN.padding,
      radius: pillN.radius,
    };
  }
  // Serialize the visible overlay elements (progress/logo/headline) for
  // the backend burn-in pass. Coords stay 0–1 normalized — no pixel math here
  // (the server converts in canvas_coords). Omit the field entirely when there
  // are no visible overlays so a captions-only draft's payload is unchanged
  // (backend then takes the tested render_elements noop path).
  if (Array.isArray(elements)) {
    const overlays = elements.filter(
      (el) => OVERLAY_ELEMENT_TYPES.has(el.type) && el.visible !== false
    );
    if (overlays.length) req.elements = overlays;
  }
  return req;
}

export async function startRerender(jobId, clipIndex, request) {
  try {
    const { data } = await client.post(
      `/jobs/${encodeURIComponent(jobId)}/clips/${clipIndex}/rerender`,
      request
    );
    return data.rerender_job_id;
  } catch (err) {
    throw toApiError(err, "Could not start the export render");
  }
}
