// Central data-testid registry for Shotvi
export const HOME = {
  emergentLink: "home-emergent-link",
};

export const LANDING = {
  root: "landing-root",
  navSignIn: "landing-nav-signin",
  navGetStarted: "landing-nav-getstarted",
  heroCta: "landing-hero-cta",
  heroSecondaryCta: "landing-hero-secondary-cta",
  featureCard: (id) => `landing-feature-${id}`,
  pricingCard: (id) => `landing-pricing-${id}`,
  pricingCta: (id) => `landing-pricing-cta-${id}`,
  footerCta: "landing-footer-cta",
};

export const AUTH = {
  root: "auth-root",
  tabSignIn: "auth-tab-signin",
  tabSignUp: "auth-tab-signup",
  email: "auth-email-input",
  password: "auth-password-input",
  name: "auth-name-input",
  submit: "auth-submit-button",
  googleBtn: "auth-google-button",
  backHome: "auth-back-home",
  error: "auth-error",
  notice: "auth-notice",
  forgot: "auth-forgot-link",
};

export const DASHBOARD = {
  root: "dashboard-root",
  newProject: "dashboard-new-project-button",
  projectCard: (id) => `dashboard-project-${id}`,
  projectOpen: (id) => `dashboard-project-open-${id}`,
  sidebarLink: (key) => `sidebar-link-${key}`,
  userMenu: "sidebar-user-menu",

  // First-run empty state (zero projects) — hero URL-paste moment
  firstRunRoot: "dashboard-firstrun-root",
  heroUrlInput: "dashboard-hero-url-input",
  heroSubmit: "dashboard-hero-submit-button",
  heroError: "dashboard-hero-error",
};

export const CLIPS_CUE = {
  root: "clips-firstclip-cue",
  dismiss: "clips-firstclip-cue-dismiss",
};

export const UPLOAD = {
  root: "upload-root",
  modeFile: "upload-mode-file",
  modeUrl: "upload-mode-url",
  dropZone: "upload-drop-zone",
  fileInput: "upload-file-input",
  urlInput: "upload-url-input",
  languageSelect: "upload-language-select",
  languageOption: (code) => `upload-language-${code}`,
  submit: "upload-start-button",
  progress: "upload-progress",
};

export const CLIPS = {
  root: "clips-root",
  backBtn: "clips-back-button",
  card: (id) => `clip-card-${id}`,
  edit: (id) => `clip-edit-${id}`,
  preview: (id) => `clip-preview-${id}`,
  export: (id) => `clip-export-${id}`,
};

export const EDITOR = {
  root: "editor-root",
  backBtn: "editor-back-button",
  exportBtn: "editor-export-button",
  undoBtn: "editor-undo-button",
  redoBtn: "editor-redo-button",

  // Clip list panel
  clipListItem: (id) => `editor-clip-${id}`,

  // Transport / Timeline
  canvas: "editor-canvas",
  canvasStage: "editor-canvas-stage",
  playBtn: "editor-play-button",
  seekBackBtn: "editor-seek-back",
  seekFwdBtn: "editor-seek-fwd",
  timelineWord: (idx) => `timeline-word-${idx}`,
  timelinePlayhead: "timeline-playhead",
  waveform: "timeline-waveform",
  waveformTicks: "timeline-waveform-ticks",
  filmstrip: "timeline-filmstrip",
  subtitleBlocks: "timeline-subtitle-blocks",
  punchMarkers: "timeline-punch-markers",
  punchMarker: (i) => `timeline-punch-marker-${i}`,
  punchAuto: "timeline-punch-auto",
  punchClear: "timeline-punch-clear",
  fillerToggle: "timeline-filler-toggle",
  splitMarker: (idx) => `timeline-split-marker-${idx}`,
  translitSuggestion: (i) => `translit-suggestion-${i}`,
  translitKeepTyped: "translit-keep-typed",

  // Line-level caption editing (key = the line's startIdx)
  editLineBtn: (startIdx) => `timeline-edit-line-${startIdx}`,
  lineEditorInput: (startIdx) => `timeline-line-editor-${startIdx}`,
  realignedWord: (startIdx, i) => `timeline-realigned-${startIdx}-${i}`,
  lineApproxBadge: (startIdx) => `timeline-line-approx-${startIdx}`,

  // Telugu ⇄ Tanglish caption script toggle (topbar)
  scriptToggle: "editor-script-toggle",
  scriptTelugu: "editor-script-telugu",
  scriptTanglish: "editor-script-tanglish",

  // Inspector tabs
  tabStyle: "editor-tab-style",
  tabExport: "editor-tab-export",

  // Style inspector
  presetBtn: (id) => `editor-preset-${id}`,
  fontSelect: "editor-font-select",
  sizeSlider: "editor-size-slider",
  positionBtn: (pos) => `editor-position-${pos}`, // legacy — kept for BC
  positionPresetBtn: (name) => `editor-pos-preset-${name}`,
  animationBtn: (a) => `editor-animation-${a}`,
  wordHighlightToggle: "editor-word-highlight-toggle",
  pillToggle: "editor-pill-toggle",
  pillColor: "editor-pill-color",
  pillOpacity: "editor-pill-opacity",
  pillPadding: "editor-pill-padding",
  pillRadius: "editor-pill-radius",

  // Clip trim handles (timeline)
  trimStartHandle: "editor-trim-start-handle",
  trimEndHandle: "editor-trim-end-handle",
  trimReset: "editor-trim-reset",

  // Saved caption template ("My Style")
  myStyleName: "editor-mystyle-name",
  myStyleSave: "editor-mystyle-save",
  myStyleApply: "editor-mystyle-apply",
  myStyleClear: "editor-mystyle-clear",

  // Draft version history (topbar)
  historyBtn: "editor-history-button",
  historyPanel: "editor-history-panel",
  historyRestore: (i) => `editor-history-restore-${i}`,

  // Canvas aspect + background fill (WYSIWYG canvas — Sprint 3)
  aspectBtn: (v) => `editor-aspect-${v}`,
  bgFillBtn: (v) => `editor-bgfill-${v}`,
  bgFillColor: "editor-bgfill-color",
  canvasFillBlur: "editor-canvas-fill-blur",
  canvasFillColor: "editor-canvas-fill-color",

  // Crop window / drag-to-reframe (16:9 master — Sprint 4)
  canvasVideo: "editor-canvas-video",
  cropViewport: "editor-crop-viewport",
  reframeToggle: "editor-reframe-toggle",
  reframeReset: "editor-reframe-reset",
  reframeDone: "editor-reframe-done",
  cropRect: "editor-crop-rect",
  cropHandle: (pos) => `editor-crop-handle-${pos}`, // tl|tr|bl|br

  // User image overlays
  addImageInput: "editor-add-image-input",
  imageOpacity: "editor-image-opacity",
  imageSize: "editor-image-size",
  emojiSize: "editor-emoji-size",
  emojiOpacity: "editor-emoji-opacity",

  // Canvas toolbar
  zoomIn: "editor-zoom-in",
  zoomOut: "editor-zoom-out",
  zoomFit: "editor-zoom-fit",
  zoomLevel: "editor-zoom-level",
  safeZoneToggle: "editor-safe-zone-toggle",
  safeZoneOption: (mode) => `editor-safe-zone-${mode}`,
  addElementMenu: "editor-add-element-menu",
  addElementOption: (type) => `editor-add-element-${type}`,

  // Free-tier watermark preview (#17). NOT an element — see
  // components/editor/PreviewWatermark.jsx.
  watermark: "editor-watermark",
  watermarkUpgrade: "editor-watermark-upgrade",

  // Canvas elements
  element: (id) => `canvas-element-${id}`,
  elementSelectable: (id) => `canvas-element-select-${id}`,
  transformHandle: (pos) => `transform-handle-${pos}`, // tl|tr|bl|br|rot
  transformBox: "transform-box",

  // Elements panel
  elementsList: "editor-elements-list",
  elementRow: (id) => `elements-row-${id}`,
  elementVisibility: (id) => `elements-visibility-${id}`,
  elementForward: (id) => `elements-forward-${id}`,
  elementBackward: (id) => `elements-backward-${id}`,
  elementDelete: (id) => `elements-delete-${id}`,
};

export const BILLING = {
  card: "billing-card",
  planStatus: "billing-plan-status",
  upgradeButton: "billing-upgrade-button",
  cancelButton: "billing-cancel-button",
};

// HTTP 402 quota block — job creation (source minutes) and export (render
// minutes / premium presets). One shared panel, so one set of ids.
export const QUOTA = {
  notice: "quota-notice",
  message: "quota-notice-message",
  upgrade: "quota-notice-upgrade",
};

export const EXPORT = {
  root: "export-root",
  // Live-draft preview (Sprint 4: master + crop-window simulation)
  livePreviewViewport: "export-live-preview-viewport",
  livePreviewVideo: "export-live-preview-video",
  resolutionBtn: (v) => `export-resolution-${v}`,
  formatBtn: (v) => `export-format-${v}`,
  burnToggle: "export-burn-toggle",
  aspectBtn: (v) => `export-aspect-${v}`,
  startBtn: "export-start-button",
  download: "export-download-button",
  generateMetadata: "export-generate-metadata-button",
  // Tier strip (#17-20): both monthly meters + the free-tier upgrade CTA.
  renderMeter: "export-render-meter",
  sourceMeter: "export-source-meter",
  upgradeCta: "export-upgrade-cta",
};
