// Realistic Telugu/Tenglish mock data for Shotvi

export const LANGUAGES = [
  { code: "te", label: "తెలుగు (Telugu)", flag: "TE" },
  { code: "tenglish", label: "Tenglish (Roman)", flag: "TG" },
  { code: "hi", label: "हिन्दी (Hindi)", flag: "HI" },
  { code: "en", label: "English", flag: "EN" },
];

export const CAPTION_PRESETS = [
  {
    id: "bold-yellow",
    name: "Bold Yellow",
    className: "caption-bold-yellow",
    swatch: "#facc15",
  },
  {
    id: "neon-green",
    name: "Neon Green",
    className: "caption-neon-green",
    swatch: "#3DBE7A",
  },
  {
    id: "fire-gradient",
    name: "Fire Gradient",
    className: "caption-fire-gradient",
    swatch: "linear-gradient(180deg,#fde047,#fb923c,#ef4444)",
  },
  {
    id: "clean-white",
    name: "Clean White",
    className: "caption-clean-white",
    swatch: "#ffffff",
  },
];

// Default pill background per preset — user overridable
// Feature #4: padding/radius are FRACTIONS of canvas height (px at the
// 640px-tall 9:16 stage ÷ 640) — the same unit caption fontSize uses, so the
// pill scales with the text on every aspect and in the burn (lib/pillUnits.js).
export const PILL_DEFAULTS_BY_PRESET = {
  "bold-yellow": {
    enabled: true,
    color: "#000000",
    opacity: 0.5,
    padding: 10 / 640,
    radius: 8 / 640,
  },
  "neon-green": {
    enabled: false,
    color: "#000000",
    opacity: 0.6,
    padding: 8 / 640,
    radius: 12 / 640,
  },
  "fire-gradient": {
    enabled: true,
    color: "#000000",
    opacity: 0.35,
    padding: 8 / 640,
    radius: 8 / 640,
  },
  "clean-white": {
    enabled: false,
    color: "#000000",
    opacity: 0.5,
    padding: 8 / 640,
    radius: 6 / 640,
  },
  // Feature #16 — each new preset reconciles the caption box on selection
  // (setCaptionPreset merges this over the element's pill). Mirrors the
  // preset's own box: bg-ON styles enable a pill matching their back_color;
  // bg-OFF styles DISABLE the pill so no box shows. Without an entry the
  // previously-selected pill would linger (the QA bug: no-box presets kept a
  // box, and Classic's black text sat on a stale black pill → invisible).
  classic: { enabled: true, color: "#ffffff", opacity: 1.0, padding: 10 / 640, radius: 8 / 640 },
  yellow: { enabled: false, color: "#000000", opacity: 0.5, padding: 8 / 640, radius: 8 / 640 },
  minimal: { enabled: false, color: "#000000", opacity: 0.5, padding: 8 / 640, radius: 8 / 640 },
  dark: { enabled: true, color: "#000000", opacity: 0.85, padding: 10 / 640, radius: 8 / 640 },
  punch: { enabled: true, color: "#000000", opacity: 0.8, padding: 10 / 640, radius: 6 / 640 },
  cove: { enabled: true, color: "#1a2634", opacity: 0.85, padding: 10 / 640, radius: 8 / 640 },
  spotlight: { enabled: false, color: "#000000", opacity: 0.5, padding: 8 / 640, radius: 8 / 640 },
  reel: { enabled: false, color: "#000000", opacity: 0.5, padding: 8 / 640, radius: 8 / 640 },
  noir: { enabled: false, color: "#000000", opacity: 0.5, padding: 8 / 640, radius: 8 / 640 },
  "hormozi-caps": { enabled: false, color: "#000000", opacity: 0.5, padding: 8 / 640, radius: 8 / 640 },
};

// Initial canvas elements — caption visible, others hidden by default so user can toggle
export const INITIAL_ELEMENTS = [
  {
    id: "el_caption_1",
    type: "caption",
    x: 0.5,
    // Hand-copy of CAPTION_DEFAULT_POSITION in api/renders.js — this module is a
    // leaf that deliberately imports nothing from api/ (it is pulled in by
    // pricing.js and several pages). editDocument.test.js pins the two together,
    // and tests/test_caption_default_position_parity.py pins both to the backend's
    // CAPTION_DEFAULT_Y_FRAC. Read the renders.js comment before changing this.
    y: 0.74,
    scale: 1,
    rotation: 0,
    visible: true,
    locked: false,
    props: {
      presetId: "bold-yellow",
      font: "Noto Sans Telugu",
      fontSize: 0.055,
      animation: "karaoke",
      pill: PILL_DEFAULTS_BY_PRESET["bold-yellow"],
    },
  },
  // Headline element removed as a product decision: the auto hook-headline
  // burned the clip's Telugu hook in the Latin-only Outfit font (tofu), and it
  // cluttered the frame. The "headline" element type still exists in the store /
  // burn plumbing so any pre-existing draft that saved one still renders, but no
  // clip is seeded with one and the add-element menu no longer offers it.
  {
    id: "el_progress_1",
    type: "progress",
    x: 0.5,
    y: 0.05, // top-safe: the bottom third holds the caption + platform UI
    scale: 1,
    rotation: 0,
    visible: false,
    locked: false,
    props: {
      color: "#E9A825",
      width: 0.94,
      height: 0.01,
      trackOpacity: 0.25,
      direction: "grow",
    },
  },
  {
    id: "el_logo_1",
    type: "logo",
    x: 0.18,
    y: 0.07,
    scale: 1,
    rotation: 0,
    visible: false,
    locked: false,
    props: {
      text: "@rahul_creator",
      avatar: "R",
      font: "Manrope",
      fontSize: 0.022,
    },
  },
];

export const FONTS = [
  "Outfit",
  "Manrope",
  "Bebas Neue",
  "Anton",
  "Poppins",
  "Playfair Display",
];

// Projects (dashboard)
export const PROJECTS = [
  {
    id: "prj_001",
    title: "AI Tools Telugu Podcast - Ep 14",
    thumbnail:
      "https://images.pexels.com/photos/36917952/pexels-photo-36917952.jpeg",
    duration: "1:24:18",
    createdAt: "2 hours ago",
    status: "ready",
    clipsCount: 8,
    language: "te",
  },
  {
    id: "prj_002",
    title: "Stock Market Basics — Part 1",
    thumbnail:
      "https://images.pexels.com/photos/34037223/pexels-photo-34037223.jpeg",
    duration: "42:07",
    createdAt: "5 hours ago",
    status: "selecting_clips",
    progress: 78,
    clipsCount: 0,
    language: "tenglish",
  },
  {
    id: "prj_003",
    title: "My Podcast Setup Tour 2024",
    thumbnail:
      "https://images.pexels.com/photos/13027585/pexels-photo-13027585.jpeg",
    duration: "28:44",
    createdAt: "yesterday",
    status: "transcribing",
    progress: 42,
    clipsCount: 0,
    language: "tenglish",
  },
  {
    id: "prj_004",
    title: "Viral Aithe Ela — Reels Strategy",
    thumbnail:
      "https://images.unsplash.com/photo-1563089145-599997674d42",
    duration: "56:31",
    createdAt: "yesterday",
    status: "ready",
    clipsCount: 12,
    language: "te",
  },
  {
    id: "prj_005",
    title: "Interview with Startup Founder",
    thumbnail:
      "https://images.pexels.com/photos/36917952/pexels-photo-36917952.jpeg",
    duration: "1:12:00",
    createdAt: "3 days ago",
    status: "uploading",
    progress: 24,
    clipsCount: 0,
    language: "tenglish",
  },
  {
    id: "prj_006",
    title: "Motivation Mantra - Sunday Session",
    thumbnail:
      "https://images.pexels.com/photos/34037223/pexels-photo-34037223.jpeg",
    duration: "38:12",
    createdAt: "5 days ago",
    status: "ready",
    clipsCount: 6,
    language: "te",
  },
];

// Clips per project
export const CLIPS_BY_PROJECT = {
  prj_001: [
    {
      id: "clip_001",
      projectId: "prj_001",
      hook: "ఈ ఒక్క AI tool మీ life మార్చేస్తుంది!",
      hookEn: "This one AI tool will change your life!",
      duration: 42,
      virality: 94,
      thumbnail:
        "https://images.pexels.com/photos/36917952/pexels-photo-36917952.jpeg",
      startAt: "12:04",
    },
    {
      id: "clip_002",
      projectId: "prj_001",
      hook: "YouTube Shorts తో నెలకి ₹80,000?",
      hookEn: "₹80,000/month with YouTube Shorts?",
      duration: 38,
      virality: 89,
      thumbnail:
        "https://images.pexels.com/photos/34037223/pexels-photo-34037223.jpeg",
      startAt: "22:41",
    },
    {
      id: "clip_003",
      projectId: "prj_001",
      hook: "Secret trick for Instagram Reels 🔥",
      hookEn: "Secret trick for Instagram Reels",
      duration: 56,
      virality: 82,
      thumbnail:
        "https://images.pexels.com/photos/13027585/pexels-photo-13027585.jpeg",
      startAt: "35:12",
    },
    {
      id: "clip_004",
      projectId: "prj_001",
      hook: "ఎలా viral అవ్వాలి 2024 లో?",
      hookEn: "How to go viral in 2024?",
      duration: 48,
      virality: 76,
      thumbnail:
        "https://images.unsplash.com/photo-1563089145-599997674d42",
      startAt: "48:22",
    },
    {
      id: "clip_005",
      projectId: "prj_001",
      hook: "ChatGPT vs Claude — nenu use chesedi ide!",
      hookEn: "ChatGPT vs Claude — this is what I use!",
      duration: 34,
      virality: 71,
      thumbnail:
        "https://images.pexels.com/photos/36917952/pexels-photo-36917952.jpeg",
      startAt: "01:02:15",
    },
    {
      id: "clip_006",
      projectId: "prj_001",
      hook: "Free tools nuvvu miss avvakudadu",
      hookEn: "Free tools you shouldn't miss",
      duration: 44,
      virality: 68,
      thumbnail:
        "https://images.pexels.com/photos/34037223/pexels-photo-34037223.jpeg",
      startAt: "01:11:08",
    },
    {
      id: "clip_007",
      projectId: "prj_001",
      hook: "నేను Rs.10 lakhs ఎలా earn చేశాను",
      hookEn: "How I earned Rs.10 lakhs",
      duration: 52,
      virality: 65,
      thumbnail:
        "https://images.pexels.com/photos/13027585/pexels-photo-13027585.jpeg",
      startAt: "01:18:44",
    },
    {
      id: "clip_008",
      projectId: "prj_001",
      hook: "AI vaadi mana job tinesthada?",
      hookEn: "Will AI take our jobs?",
      duration: 40,
      virality: 58,
      thumbnail:
        "https://images.unsplash.com/photo-1563089145-599997674d42",
      startAt: "01:22:33",
    },
  ],
};

// Fake fallback for other projects — use prj_001 clips remapped
export const getClipsForProject = (projectId) => {
  if (CLIPS_BY_PROJECT[projectId]) return CLIPS_BY_PROJECT[projectId];
  return CLIPS_BY_PROJECT.prj_001.slice(0, 4).map((c, i) => ({
    ...c,
    id: `${projectId}_c${i}`,
    projectId,
  }));
};

// Word-level transcript for the editor — one clip's worth (mixed te/tenglish).
// Each word has: text (Telugu-script display), text_tanglish (romanized, for
// the Tanglish toggle — mirrors the live pipeline's word_tanglish), start, end.
const WORD_TRANSCRIPT_RAW = [
  { text: "ఈ", text_tanglish: "ee", start: 0.0, end: 0.28 },
  { text: "ఒక్క", text_tanglish: "okka", start: 0.28, end: 0.72 },
  { text: "AI", text_tanglish: "AI", start: 0.72, end: 1.06 },
  { text: "tool", text_tanglish: "tool", start: 1.06, end: 1.5 },
  { text: "మీ", text_tanglish: "mee", start: 1.5, end: 1.82 },
  { text: "life", text_tanglish: "life", start: 1.82, end: 2.24 },
  { text: "ni", text_tanglish: "ni", start: 2.24, end: 2.42 },
  { text: "totally", text_tanglish: "totally", start: 2.42, end: 2.98 },
  { text: "మార్చేస్తుంది!", text_tanglish: "maarchestundi!", start: 2.98, end: 3.9 },
  { text: "Nenu", text_tanglish: "Nenu", start: 4.2, end: 4.58 },
  { text: "ee", text_tanglish: "ee", start: 4.58, end: 4.8 },
  { text: "tool", text_tanglish: "tool", start: 4.8, end: 5.16 },
  { text: "ni", text_tanglish: "ni", start: 5.16, end: 5.36 },
  { text: "roju", text_tanglish: "roju", start: 5.36, end: 5.74 },
  { text: "vaadutunna,", text_tanglish: "vaadutunna,", start: 5.74, end: 6.42 },
  { text: "and", text_tanglish: "and", start: 6.42, end: 6.72 },
  { text: "trust", text_tanglish: "trust", start: 6.72, end: 7.06 },
  { text: "me,", text_tanglish: "me,", start: 7.06, end: 7.36 },
  { text: "ఇది", text_tanglish: "idi", start: 7.5, end: 7.9 },
  { text: "next", text_tanglish: "next", start: 7.9, end: 8.24 },
  { text: "level!", text_tanglish: "level!", start: 8.24, end: 8.86 },
  { text: "First", text_tanglish: "First", start: 9.2, end: 9.6 },
  { text: "ga,", text_tanglish: "ga,", start: 9.6, end: 9.86 },
  { text: "meeru", text_tanglish: "meeru", start: 9.86, end: 10.26 },
  { text: "cheyaalsindi", text_tanglish: "cheyaalsindi", start: 10.26, end: 10.98 },
  { text: "ఒకటే —", text_tanglish: "okate —", start: 10.98, end: 11.6 },
  { text: "sign", text_tanglish: "sign", start: 11.6, end: 11.92 },
  { text: "up", text_tanglish: "up", start: 11.92, end: 12.16 },
  { text: "cheyandi,", text_tanglish: "cheyandi,", start: 12.16, end: 12.68 },
  { text: "అంతే!", text_tanglish: "ante!", start: 12.68, end: 13.3 },
];

// Decorated with the id/ref shape real transcripts carry (see
// api/transcripts.js getWordsForRange + lib/transcriptEdits.js) so mock-mode
// words are addressable by transcript edits exactly like live ones. Inlined
// rather than importing wordIdFromRef to keep mockData dependency-free.
export const WORD_TRANSCRIPT = WORD_TRANSCRIPT_RAW.map((w, index) => ({
  id: `w_flat_${index}`,
  ref: { type: "flat", index },
  ...w,
}));

export const CLIP_DURATION = 13.6; // seconds — matches transcript

// Feature #30 — mock Gemini emoji suggestions ({emoji, word_index}) in the
// clip's filtered-word index space (same as emphasis). With 4 words/line these
// land on three distinct early lines (line 0: words 0-3, line 1: 4-7, line 2:
// 8-11), so the editor shows a different emoji as each line plays.
export const MOCK_EMOJI_SUGGESTIONS = [
  { emoji: "💡", word_index: 2 },  // "AI" — line 0
  { emoji: "🔥", word_index: 5 },  // "life" — line 1
  { emoji: "🚀", word_index: 9 },  // "Nenu" — line 2
];

export const STATUS_META = {
  uploading: { label: "Uploading", color: "#E9A825" },
  transcribing: { label: "Transcribing", color: "#f59e0b" },
  selecting_clips: { label: "Selecting Clips", color: "#f59e0b" },
  ready: { label: "Ready", color: "#10b981" },
  exporting: { label: "Exporting", color: "#E9A825" },
  failed: { label: "Failed", color: "#ef4444" },
  expired: { label: "Expired", color: "#71717a" },
};

// NOTE: public pricing does NOT live here. The landing page is production
// surface and was importing PRICING_TIERS from this fixture file with no mocks
// guard — real prices served from mock data. It moved to data/pricing.js.
