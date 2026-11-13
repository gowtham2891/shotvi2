import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Type,
  Download,
  Eye,
  EyeOff,
  ChevronUp,
  ChevronDown,
  Trash2,
  Sparkles,
  MessageSquare,
  BarChart3,
  AtSign,
  Bookmark,
  Image as ImageIcon,
} from "lucide-react";
import { useAppStore } from "@/store/useAppStore";
// Caption presets mirror the backend's caption style ids (STYLES in
// services/caption_renderer.py) so exports always burn the selected style.
// CAPTION_FONTS are the three bundled Telugu caption fonts the backend
// resolves deterministically via fontsdir (services/fonts.py :: CAPTION_FONTS).
import {
  CAPTION_STYLES,
  EXPORT_FORMATS,
  BACKGROUND_OPTIONS,
  isPremiumPreset,
  fontsForScript,
} from "@/api/renders";
import { getCaptionFontStack } from "@/data/captionStylePreview";
import { getCaptionStylePreview } from "@/data/captionStylePreview";
// PILL_DEFAULTS_BY_PRESET is the same preset→pill config setCaptionPreset
// applies on selection; the swatch reads it so the grid matches the canvas.
import { PILL_DEFAULTS_BY_PRESET } from "@/data/mockData";
import { emojiAssetUrl } from "@/lib/emojiOverlays";
import { pillFracToSliderPx, pillSliderPxToFrac } from "@/lib/pillUnits";
import { alignPatches, distributePatches } from "@/lib/alignDistribute";
import { EDITOR } from "@/constants/testIds";

const TABS = [
  { key: "style", label: "Style", icon: Type, testId: EDITOR.tabStyle },
  { key: "export", label: "Export", icon: Download, testId: EDITOR.tabExport },
];

const TYPE_ICONS = {
  caption: Type,
  headline: MessageSquare,
  progress: BarChart3,
  logo: AtSign,
  image: ImageIcon,
};

// Feature #15 — caption reveal animations. 'karaoke' is the per-word highlight
// (default); the other three are line-reveal motions burned via ASS override
// tags (services/caption_renderer.py). Order = button order.
const ANIMATIONS = [
  { id: "karaoke", label: "Karaoke" },
  { id: "pop", label: "Pop" },
  { id: "fade", label: "Fade" },
  { id: "slide-up", label: "Slide up" },
  { id: "none", label: "None" },
];

const POSITION_PRESETS = [
  { id: "top", label: "Top" },
  { id: "center", label: "Center" },
  { id: "lower-third", label: "Lower ⅓" },
  { id: "bottom-safe", label: "Bottom Safe" },
];

/**
 * Inspector — right panel. Style / Export tabs.
 * `defaultTab` only sets the initial tab (used by tests and deep links).
 */
export const Inspector = ({ defaultTab = "style" }) => {
  const [tab, setTab] = useState(defaultTab);

  return (
    <aside className="border-l border-k-raised bg-k-bg flex flex-col overflow-hidden">
      {/* Tabs */}
      <div className="flex border-b border-k-raised">
        {TABS.map(({ key, label, icon: Icon, testId }) => (
          <button
            key={key}
            data-testid={testId}
            onClick={() => setTab(key)}
            className={`flex-1 flex items-center justify-center gap-1.5 py-3 text-xs font-semibold transition-colors ${
              tab === key
                ? "text-k-accentsoft bg-k-accent/10 border-b-2 border-k-accent"
                : "text-k-faint hover:text-k-muted"
            }`}
          >
            <Icon size={13} />
            {label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-5">
        {tab === "style" && <StyleTab />}
        {tab === "export" && <ExportTab />}
      </div>
    </aside>
  );
};

/* ============================ STYLE ============================ */

/* Feature #10 — align & distribute. Visible only with 2+ selected elements
   (shift-click / marquee, feature #9). Pure math in lib/alignDistribute.js
   over normalized centers; moveElementsTo applies each action as ONE
   history frame (coalesce key per click via unique action names). */
const ALIGN_ACTIONS = [
  { id: "align-left", label: "⇤", title: "Align left", axis: "x", mode: "min" },
  { id: "align-center-x", label: "⇹", title: "Align horizontal centers", axis: "x", mode: "center" },
  { id: "align-right", label: "⇥", title: "Align right", axis: "x", mode: "max" },
  { id: "align-top", label: "⤒", title: "Align top", axis: "y", mode: "min" },
  { id: "align-center-y", label: "⇳", title: "Align vertical centers", axis: "y", mode: "center" },
  { id: "align-bottom", label: "⤓", title: "Align bottom", axis: "y", mode: "max" },
];

const AlignDistributeSection = () => {
  const selectedIds = useAppStore((s) => s.selectedIds);
  const elements = useAppStore((s) => s.elements);
  if (selectedIds.length < 2) return null;

  const apply = (patches) => {
    if (Object.keys(patches).length) {
      useAppStore.getState().moveElementsTo(patches, null); // null key: distinct frame per click
    }
  };

  return (
    <div>
      <SectionTitle>Align · {selectedIds.length} selected</SectionTitle>
      <div className="grid grid-cols-6 gap-1 mb-2">
        {ALIGN_ACTIONS.map((a) => (
          <button
            key={a.id}
            data-testid={`editor-${a.id}`}
            title={a.title}
            onClick={() => apply(alignPatches(elements, selectedIds, a.axis, a.mode))}
            className="py-1.5 rounded border border-k-line text-k-muted hover:text-white hover:border-k-accent/50 text-sm leading-none transition-colors"
          >
            {a.label}
          </button>
        ))}
      </div>
      {selectedIds.length >= 3 && (
        <div className="grid grid-cols-2 gap-1">
          <button
            data-testid="editor-distribute-x"
            title="Distribute horizontally (even center spacing)"
            onClick={() => apply(distributePatches(elements, selectedIds, "x"))}
            className="py-1.5 rounded border border-k-line text-[11px] text-k-muted hover:text-white hover:border-k-accent/50 transition-colors"
          >
            Distribute ↔
          </button>
          <button
            data-testid="editor-distribute-y"
            title="Distribute vertically (even center spacing)"
            onClick={() => apply(distributePatches(elements, selectedIds, "y"))}
            className="py-1.5 rounded border border-k-line text-[11px] text-k-muted hover:text-white hover:border-k-accent/50 transition-colors"
          >
            Distribute ↕
          </button>
        </div>
      )}
    </div>
  );
};

const StyleTab = () => {
  const elements = useAppStore((s) => s.elements);
  const selectedId = useAppStore((s) => s.selectedElementId);
  const selected = elements.find((el) => el.id === selectedId);

  return (
    <>
      <ElementList />
      <AlignDistributeSection />
      <PositionSection />
      {selected?.type === "caption" && <CaptionSection element={selected} />}
      {selected?.type === "headline" && <HeadlineSection element={selected} />}
      {selected?.type === "image" && <ImageSection element={selected} />}
      {selected?.type === "progress" && <ProgressSection element={selected} />}
      {selected?.type === "emoji" && <EmojiSection element={selected} />}
      {!selected && (
        <p className="text-[11px] text-k-faint leading-relaxed">
          Select an element on the canvas — or toggle one on above — to edit
          its style.
        </p>
      )}
    </>
  );
};

const SectionTitle = ({ children }) => (
  <p className="text-[10px] uppercase tracking-[0.22em] text-k-faint mb-2">
    {children}
  </p>
);

const ElementList = () => {
  const elements = useAppStore((s) => s.elements);
  const selectedId = useAppStore((s) => s.selectedElementId);
  const setSelected = useAppStore((s) => s.setSelected);
  const toggleVisibility = useAppStore((s) => s.toggleElementVisibility);
  const bringForward = useAppStore((s) => s.bringForward);
  const sendBackward = useAppStore((s) => s.sendBackward);
  const removeElement = useAppStore((s) => s.removeElement);

  return (
    <div>
      <SectionTitle>Elements</SectionTitle>
      <div className="space-y-1">
        {[...elements].reverse().map((el) => {
          const Icon = TYPE_ICONS[el.type] || Sparkles;
          const isSel = el.id === selectedId;
          return (
            <div
              key={el.id}
              onClick={() => setSelected(el.id)}
              className={`group flex items-center gap-2 px-2 py-1.5 rounded-md cursor-pointer border transition-colors ${
                isSel
                  ? "bg-k-accent/12 border-k-accent/40"
                  : "border-transparent hover:bg-white/[0.03]"
              }`}
            >
              <Icon size={12} className={isSel ? "text-k-accentsoft" : "text-k-faint"} />
              <span className={`flex-1 text-[11px] capitalize ${el.visible ? "text-k-soft" : "text-k-line2 line-through"}`}>
                {el.type}
              </span>
              <button
                onClick={(e) => { e.stopPropagation(); sendBackward(el.id); }}
                className="opacity-0 group-hover:opacity-100 text-k-faint hover:text-white transition-opacity"
                title="Send backward"
              >
                <ChevronDown size={12} />
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); bringForward(el.id); }}
                className="opacity-0 group-hover:opacity-100 text-k-faint hover:text-white transition-opacity"
                title="Bring forward"
              >
                <ChevronUp size={12} />
              </button>
              {el.type !== "caption" && (
                <button
                  onClick={(e) => { e.stopPropagation(); removeElement(el.id); }}
                  className="opacity-0 group-hover:opacity-100 text-k-faint hover:text-red-400 transition-opacity"
                  title="Delete"
                >
                  <Trash2 size={12} />
                </button>
              )}
              <button
                onClick={(e) => { e.stopPropagation(); toggleVisibility(el.id); }}
                className="text-k-faint hover:text-white"
                title={el.visible ? "Hide" : "Show"}
              >
                {el.visible ? <Eye size={13} /> : <EyeOff size={13} />}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
};

// Preset → the y fraction applyPositionPreset writes (x is always 0.5). Used
// only to light up the button matching the element's CURRENT placement; after
// a manual drag no preset matches and none is shown active (honest, not wrong).
const POSITION_PRESET_Y = { top: 0.08, center: 0.5, "lower-third": 0.66, "bottom-safe": 0.78 };

const PositionSection = () => {
  const applyPositionPreset = useAppStore((s) => s.applyPositionPreset);
  const selectedId = useAppStore((s) => s.selectedElementId);
  const selected = useAppStore((s) =>
    s.elements.find((el) => el.id === s.selectedElementId)
  );
  const activeId =
    selected && Math.abs((selected.x ?? 0.5) - 0.5) < 0.01
      ? POSITION_PRESETS.find(
          (p) => Math.abs((selected.y ?? 0.5) - POSITION_PRESET_Y[p.id]) < 0.01
        )?.id
      : undefined;
  return (
    <div>
      <SectionTitle>Position</SectionTitle>
      <div className="grid grid-cols-2 gap-1.5">
        {POSITION_PRESETS.map((p) => {
          const active = p.id === activeId;
          return (
            <button
              key={p.id}
              data-testid={EDITOR.positionPresetBtn(p.id)}
              disabled={!selectedId}
              aria-pressed={active}
              onClick={() => applyPositionPreset(p.id)}
              className={`px-2 py-1.5 rounded-md text-[11px] font-medium border disabled:opacity-40 disabled:cursor-not-allowed transition-colors ${
                active
                  ? "border-k-accent bg-k-accent/12 text-white"
                  : "bg-k-surface border-k-raised text-k-muted hover:border-k-accent/50 hover:text-white"
              }`}
            >
              {p.label}
            </button>
          );
        })}
      </div>
    </div>
  );
};

const hexToRgba = (hex, a) => {
  const h = (hex || "#000000").replace("#", "");
  const r = parseInt(h.slice(0, 2), 16) || 0;
  const g = parseInt(h.slice(2, 4), 16) || 0;
  const b = parseInt(h.slice(4, 6), 16) || 0;
  return `rgba(${r},${g},${b},${a})`;
};

/* A tiny WYSIWYG caption-style chip for the preset grid. Renders a two-word
   karaoke sample (highlighted word + unspoken word) in the preset's real font,
   colours, outline, caps and glow. The BACKGROUND mirrors exactly what picking
   the preset produces on the canvas (ElementBodies): the preset's default pill
   if it enables one, otherwise the style's own border-derived box. Without this,
   presets that differ ONLY by their default pill (e.g. Bold Yellow, which has a
   pill, vs Yellow, which doesn't) rendered as visual duplicates in the grid. */
const PresetSwatch = ({ preview, pill }) => {
  const {
    colorHighlight,
    colorUnspoken,
    fontFamily,
    fontWeight,
    textShadow,
    box,
    uppercase,
    glow,
  } = preview;
  const shadow = glow ? `${textShadow}, 0 0 7px ${colorHighlight}` : textShadow;
  // A few presets (e.g. Typewriter) make the not-yet-spoken word fully
  // transparent by design — faithful on the canvas, but in a 36px chip it
  // would show only "Aa". Fall back to a dim white so the swatch stays legible.
  const unspoken = colorUnspoken === "transparent" ? "rgba(255,255,255,0.45)" : colorUnspoken;
  // Same precedence as ElementBodies: enabled pill wins, else the border box.
  const bg = pill?.enabled
    ? { background: hexToRgba(pill.color, pill.opacity), padding: "0.14em 0.5em", borderRadius: 6 }
    : box || null;
  return (
    <span className="flex items-center justify-center h-9 rounded bg-black/40 overflow-hidden px-1">
      <span
        style={{
          fontFamily,
          fontWeight,
          textShadow: shadow,
          textTransform: uppercase ? "uppercase" : "none",
          fontSize: 13,
          lineHeight: 1,
          whiteSpace: "nowrap",
          ...(bg || {}),
        }}
      >
        <span style={{ color: colorHighlight }}>Aa</span>{" "}
        <span style={{ color: unspoken }}>Bb</span>
      </span>
    </span>
  );
};

const CaptionSection = ({ element }) => {
  const setCaptionPreset = useAppStore((s) => s.setCaptionPreset);
  const updateElementProps = useAppStore((s) => s.updateElementProps);
  const captionScript = useAppStore((s) => s.exportSettings.captionScript);
  const { presetId, font, fontSize, animation, pill } = element.props;

  const patch = (p) => updateElementProps(element.id, p);
  const patchPill = (p) => patch({ pill: { ...pill, ...p } });

  // Feature #16 — the font list is script-scoped: Telugu script shows the 3
  // Telugu fonts, Tanglish the 6 Latin display fonts (services/fonts.py mirror).
  const fontOptions = fontsForScript(captionScript);
  // Replix parity: reveal animations run only on background-OFF (outline)
  // styles; a boxed style plays no motion (the box would jitter). getPreview's
  // bgOff flag is the single source of truth, mirrored from the backend STYLES.
  const animatable = getCaptionStylePreview(presetId).bgOff;

  return (
    <>
      {/* Presets — each tile renders a LIVE mini preview of the actual style
          (real font, karaoke highlight vs. unspoken colour, box/outline, caps,
          glow) using the same getCaptionStylePreview() the canvas + burn read,
          so the grid is WYSIWYG at a glance instead of a bare colour dot. */}
      <div>
        <SectionTitle>Caption Preset</SectionTitle>
        <div className="grid grid-cols-2 gap-1.5">
          {CAPTION_STYLES.map((p) => (
            <button
              key={p.id}
              data-testid={EDITOR.presetBtn(p.id)}
              onClick={() => setCaptionPreset(p.id)}
              title={p.name}
              className={`group flex flex-col items-stretch gap-1 p-1 rounded-md border transition-colors ${
                presetId === p.id
                  ? "border-k-accent bg-k-accent/12"
                  : "border-k-raised bg-k-surface hover:border-k-accent/40"
              }`}
            >
              <PresetSwatch
                preview={getCaptionStylePreview(p.id)}
                pill={PILL_DEFAULTS_BY_PRESET[p.id]}
              />
              <span className="flex items-center gap-1 px-1 min-w-0">
                <span
                  className={`truncate text-[11px] font-medium ${
                    presetId === p.id ? "text-white" : "text-k-muted group-hover:text-white"
                  }`}
                >
                  {p.name}
                </span>
                {/* Feature #21 — premium presets carry a PRO tag; free users can
                    select+preview but the export gate (402) blocks the render. */}
                {isPremiumPreset(p.id) && (
                  <span className="ml-auto text-[8px] font-bold tracking-wide text-k-ok bg-k-ok/12 px-1 py-0.5 rounded shrink-0">
                    PRO
                  </span>
                )}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Font — script-scoped (feature #16). Telugu script → the 3 bundled
          Telugu fonts; Tanglish → the 6 Latin display fonts (Montserrat Black
          default). The backend resolves the selection deterministically via
          libass fontsdir (services/fonts.py) and the preview renders the same
          @font-face family from /public/fonts, so the dropdown is true WYSIWYG.
          Decoupled from the preset (Stage 5): font is chosen here, style only
          drives colors — except a Tanglish preset seeds its recommended Latin
          font on pick (setCaptionPreset). */}
      <div>
        <SectionTitle>Font{captionScript === "tanglish" ? " (Latin)" : " (Telugu)"}</SectionTitle>
        <select
          data-testid={EDITOR.fontSelect}
          value={font}
          onChange={(e) => patch({ font: e.target.value })}
          className="w-full bg-k-surface border border-k-raised rounded-md px-2 py-2 text-xs text-k-soft outline-none focus:border-k-accent/60"
        >
          {fontOptions.map((f) => (
            <option key={f} value={f} style={{ fontFamily: getCaptionFontStack(f) }}>
              {f}
            </option>
          ))}
        </select>
      </div>

      {/* Size — value shown as % of frame height (the fontSize fraction), so
          the readout is meaningful and consistent with the pill sliders below
          which all print their live value. */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <SectionTitle>Size</SectionTitle>
          <span className="text-[10px] text-k-muted font-mono">
            {(fontSize * 100).toFixed(1)}%
          </span>
        </div>
        <input
          data-testid={EDITOR.sizeSlider}
          type="range"
          min={0.03}
          max={0.09}
          step={0.002}
          value={fontSize}
          onChange={(e) => patch({ fontSize: parseFloat(e.target.value) })}
          className="w-full accent-k-accent"
        />
      </div>

      {/* Feature #15 + #16 — caption reveal animation. The selection rides
          caption_animation to the burn: 'karaoke' keeps the per-word highlight;
          pop/fade/slide-up are line-reveal motions. Replix parity — reveal
          MOTION runs only on background-OFF styles; a boxed style keeps just
          karaoke/none (the box would jitter under a line reveal). */}
      <div>
        <SectionTitle>Animation</SectionTitle>
        <div className="flex flex-wrap gap-1.5">
          {ANIMATIONS.map((a) => {
            const active = (animation || "karaoke") === a.id;
            // karaoke/none always available; the 3 line-reveal motions are
            // gated to bg-off presets (animatable).
            const isMotion = a.id !== "karaoke" && a.id !== "none";
            const disabled = isMotion && !animatable;
            return (
              <button
                key={a.id}
                data-testid={EDITOR.animationBtn(a.id)}
                onClick={() => !disabled && patch({ animation: a.id })}
                disabled={disabled}
                title={
                  disabled
                    ? `${a.label} needs a background-off style (this preset has a background box)`
                    : `Caption reveal: ${a.label}`
                }
                className={`px-2.5 py-1.5 rounded-md text-[11px] font-medium border transition-colors ${
                  active
                    ? "border-k-accent bg-k-accent/12 text-white"
                    : "border-k-raised bg-k-surface text-k-muted hover:text-white hover:border-k-accent/40"
                } ${disabled ? "opacity-35 cursor-not-allowed hover:border-k-raised hover:text-k-muted" : ""}`}
              >
                {a.label}
              </button>
            );
          })}
        </div>
        <p className="text-[10px] text-k-faint mt-1.5">
          {animatable
            ? "Karaoke highlights each word as spoken; Pop / Fade / Slide up animate each line as it appears."
            : "This style has a background box — only Karaoke and None are available. Pick a background-off style to enable reveal motion."}
        </p>
      </div>

      {/* Background pill */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <SectionTitle>Background Pill</SectionTitle>
          <button
            data-testid={EDITOR.pillToggle}
            onClick={() => patchPill({ enabled: !pill.enabled })}
            className={`relative w-8 h-4.5 rounded-full transition-colors ${
              pill.enabled ? "bg-k-accent" : "bg-k-raised"
            }`}
            style={{ height: 18 }}
          >
            <span
              className="absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white transition-all"
              style={{ left: pill.enabled ? 16 : 2 }}
            />
          </button>
        </div>
        {pill.enabled && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <input
                data-testid={EDITOR.pillColor}
                type="color"
                value={pill.color}
                onChange={(e) => patchPill({ color: e.target.value })}
                className="w-8 h-8 rounded cursor-pointer bg-transparent border border-k-raised"
              />
              <span className="text-[11px] text-k-muted font-mono">{pill.color}</span>
            </div>
            <LabeledSlider
              testId={EDITOR.pillOpacity}
              label="Opacity" min={0} max={1} step={0.05}
              value={pill.opacity}
              onChange={(v) => patchPill({ opacity: v })}
            />
            {/* Feature #4: stored as fraction-of-canvas-height (same unit as
                text Size) so pill and text scale together on every aspect and
                in the burn; the slider still edits familiar px-at-9:16 numbers. */}
            <LabeledSlider
              testId={EDITOR.pillPadding}
              label="Padding" min={0} max={24} step={1}
              value={pillFracToSliderPx(pill.padding)}
              onChange={(v) => patchPill({ padding: pillSliderPxToFrac(v) })}
            />
            <LabeledSlider
              testId={EDITOR.pillRadius}
              label="Radius" min={0} max={24} step={1}
              value={pillFracToSliderPx(pill.radius)}
              onChange={(v) => patchPill({ radius: pillSliderPxToFrac(v) })}
            />
          </div>
        )}
      </div>

      <MyStyleSection />
    </>
  );
};

/* ============================ MY STYLE ============================ */

// Saved caption template: ONE named style (preset, font, size, pill,
// position) per user, persisted server-side. New clips WITHOUT a draft start
// from it automatically (openClip's no-draft branch); a clip's own draft
// always wins — see the store's caption template slice.
const MyStyleSection = () => {
  const template = useAppStore((s) => s.captionTemplate);
  const saving = useAppStore((s) => s.captionTemplateSaving);
  const saveMyStyle = useAppStore((s) => s.saveMyStyle);
  const clearMyStyle = useAppStore((s) => s.clearMyStyle);
  const applyMyStyleToCurrentClip = useAppStore((s) => s.applyMyStyleToCurrentClip);

  const templateName = template?.name;
  const [name, setName] = useState(templateName || "My style");
  // A template saved/fetched elsewhere refreshes the field — it only tracks
  // the persisted name.
  useEffect(() => {
    if (templateName) setName(templateName);
  }, [templateName]);

  return (
    <div>
      <SectionTitle>My Style</SectionTitle>
      <p className="text-[10px] text-k-faint leading-relaxed mb-2">
        Save this caption look (preset, font, size, pill, position). New clips
        you haven't edited yet will start with it.
      </p>
      <input
        data-testid={EDITOR.myStyleName}
        value={name}
        onChange={(e) => setName(e.target.value)}
        maxLength={60}
        placeholder="Style name"
        className="w-full bg-k-surface border border-k-raised rounded-md px-2 py-2 text-xs text-k-soft outline-none focus:border-k-accent/60 mb-2"
      />
      <div className="flex flex-wrap gap-1.5">
        <button
          data-testid={EDITOR.myStyleSave}
          onClick={() => saveMyStyle(name)}
          disabled={saving}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[11px] font-semibold bg-k-accent hover:bg-k-accent2 text-k-ink disabled:opacity-50 transition-colors"
        >
          <Bookmark size={11} />
          {template ? "Update style" : "Save as my style"}
        </button>
        {template && (
          <>
            <button
              data-testid={EDITOR.myStyleApply}
              onClick={applyMyStyleToCurrentClip}
              disabled={saving}
              className="px-2.5 py-1.5 rounded-md text-[11px] font-medium bg-k-surface border border-k-raised text-k-muted hover:border-k-accent/50 hover:text-white disabled:opacity-50 transition-colors"
              title="Apply your saved style to this clip"
            >
              Apply here
            </button>
            <button
              data-testid={EDITOR.myStyleClear}
              onClick={clearMyStyle}
              disabled={saving}
              className="px-2.5 py-1.5 rounded-md text-[11px] font-medium bg-k-surface border border-k-raised text-k-muted hover:border-red-400/60 hover:text-red-300 disabled:opacity-50 transition-colors"
            >
              Remove
            </button>
          </>
        )}
      </div>
    </div>
  );
};

// User image overlay — size (height fraction of canvas, width follows the
// image's own ratio) and opacity, both burned identically by
// services/overlay_renderer.py :: _prepare_image_layer.
const ImageSection = ({ element }) => {
  const updateElementProps = useAppStore((s) => s.updateElementProps);
  const p = element.props;
  const patch = (np) => updateElementProps(element.id, np);
  return (
    <div className="space-y-3">
      <div>
        <SectionTitle>Image Size</SectionTitle>
        <input
          data-testid={EDITOR.imageSize}
          type="range"
          min={0.05}
          max={0.6}
          step={0.01}
          value={p.height ?? 0.18}
          onChange={(e) => patch({ height: parseFloat(e.target.value) })}
          className="w-full accent-k-accent"
        />
      </div>
      <LabeledSlider
        testId={EDITOR.imageOpacity}
        label="Opacity" min={0.05} max={1} step={0.05}
        value={p.opacity ?? 1}
        onChange={(v) => patch({ opacity: v })}
      />
    </div>
  );
};

// Feature #30 — a timed emoji overlay. Size/opacity mirror ImageSection; the
// emoji itself + its [start,end] window come from the Gemini suggestion (auto-
// timed to the caption line) and are shown read-only. Delete/reposition ride
// the shared element controls (ElementList, drag, hotkeys).
const EmojiSection = ({ element }) => {
  const updateElementProps = useAppStore((s) => s.updateElementProps);
  const p = element.props;
  const patch = (np) => updateElementProps(element.id, np);
  const fmt = (t) => (typeof t === "number" ? `${t.toFixed(1)}s` : "—");
  return (
    <div className="space-y-3">
      <div>
        <SectionTitle>Emoji</SectionTitle>
        <div className="flex items-center gap-2 text-xs text-k-muted">
          <img src={emojiAssetUrl(p.emoji)} alt={p.emoji} className="w-6 h-6" />
          <span>
            Shows {fmt(p.start)}–{fmt(p.end)} (its caption line)
          </span>
        </div>
      </div>
      <div>
        <SectionTitle>Emoji Size</SectionTitle>
        <input
          data-testid={EDITOR.emojiSize}
          type="range"
          min={0.05}
          max={0.4}
          step={0.01}
          value={p.height ?? 0.12}
          onChange={(e) => patch({ height: parseFloat(e.target.value) })}
          className="w-full accent-k-accent"
        />
      </div>
      <LabeledSlider
        testId={EDITOR.emojiOpacity}
        label="Opacity" min={0.05} max={1} step={0.05}
        value={p.opacity ?? 1}
        onChange={(v) => patch({ opacity: v })}
      />
    </div>
  );
};

const HeadlineSection = ({ element }) => {
  const updateElementProps = useAppStore((s) => s.updateElementProps);
  const p = element.props;
  const patch = (np) => updateElementProps(element.id, np);
  return (
    <div>
      <SectionTitle>Headline</SectionTitle>
      <input
        value={p.text}
        onChange={(e) => patch({ text: e.target.value })}
        className="w-full bg-k-surface border border-k-raised rounded-md px-2 py-2 text-xs text-k-soft outline-none focus:border-k-accent/60 mb-2"
      />
      <div className="flex items-center gap-2">
        <input
          type="color"
          value={p.color}
          onChange={(e) => patch({ color: e.target.value })}
          className="w-8 h-8 rounded cursor-pointer bg-transparent border border-k-raised"
        />
        <span className="text-[11px] text-k-muted font-mono">{p.color}</span>
      </div>
    </div>
  );
};

const LabeledSlider = ({ label, min, max, step, value, onChange, testId }) => (
  <div>
    <div className="flex justify-between text-[10px] text-k-faint mb-1">
      <span>{label}</span>
      <span className="font-mono">{value}</span>
    </div>
    <input
      data-testid={testId}
      type="range"
      min={min} max={max} step={step} value={value}
      onChange={(e) => onChange(parseFloat(e.target.value))}
      className="w-full accent-k-accent"
    />
  </div>
);

/* ===================== PROGRESS BAR ===================== */

const PROGRESS_DIRECTIONS = [
  { id: "grow", label: "Grow" },
  { id: "countdown", label: "Countdown" },
];

/* Progress-bar controls — previously the progress element had NO Inspector
   section at all (you could add one but not restyle it). Color, thickness,
   width, track opacity, fill direction (grow vs countdown), and a top/bottom
   safe-area placement, all mirrored 1:1 by ProgressBody + the burn. */
const ProgressSection = ({ element }) => {
  const updateElementProps = useAppStore((s) => s.updateElementProps);
  const updateElement = useAppStore((s) => s.updateElement);
  const {
    color = "#E9A825",
    width = 0.94,
    height = 0.01,
    trackOpacity = 0.25,
    direction = "grow",
  } = element.props;
  const patch = (p) => updateElementProps(element.id, p);

  return (
    <>
      <div>
        <SectionTitle>Progress Bar</SectionTitle>
        <div className="flex items-center gap-2">
          <input
            data-testid="editor-progress-color"
            type="color"
            value={color}
            onChange={(e) => patch({ color: e.target.value })}
            className="w-8 h-8 rounded cursor-pointer bg-transparent border border-k-raised"
          />
          <span className="text-[11px] text-k-muted font-mono">{color}</span>
        </div>
      </div>

      <div>
        <SectionTitle>Fill</SectionTitle>
        <div className="flex gap-1.5">
          {PROGRESS_DIRECTIONS.map((d) => {
            const active = (direction || "grow") === d.id;
            return (
              <button
                key={d.id}
                data-testid={`editor-progress-dir-${d.id}`}
                onClick={() => patch({ direction: d.id })}
                aria-pressed={active}
                className={`flex-1 px-2 py-1.5 rounded-md text-[11px] font-medium border transition-colors ${
                  active
                    ? "border-k-accent bg-k-accent/12 text-white"
                    : "border-k-raised bg-k-surface text-k-muted hover:text-white hover:border-k-accent/40"
                }`}
              >
                {d.label}
              </button>
            );
          })}
        </div>
        <p className="text-[10px] text-k-faint mt-1.5">
          {direction === "countdown"
            ? "Starts full and empties as the clip plays — a countdown that adds urgency."
            : "Fills left → right as the clip plays."}
        </p>
      </div>

      <LabeledSlider
        testId="editor-progress-thickness"
        label="Thickness" min={0.004} max={0.03} step={0.001}
        value={height} onChange={(v) => patch({ height: v })}
      />
      <LabeledSlider
        testId="editor-progress-width"
        label="Width" min={0.4} max={1} step={0.02}
        value={width} onChange={(v) => patch({ width: v })}
      />
      <LabeledSlider
        testId="editor-progress-track"
        label="Track opacity" min={0} max={0.6} step={0.02}
        value={trackOpacity} onChange={(v) => patch({ trackOpacity: v })}
      />

      <div>
        <SectionTitle>Placement</SectionTitle>
        <div className="grid grid-cols-2 gap-1.5">
          <button
            data-testid="editor-progress-top"
            onClick={() => updateElement(element.id, { x: 0.5, y: 0.04 })}
            className="px-2 py-1.5 rounded-md text-[11px] font-medium bg-k-surface border border-k-raised text-k-muted hover:border-k-accent/50 hover:text-white transition-colors"
          >
            Top edge
          </button>
          <button
            data-testid="editor-progress-bottom"
            onClick={() => updateElement(element.id, { x: 0.5, y: 0.965 })}
            className="px-2 py-1.5 rounded-md text-[11px] font-medium bg-k-surface border border-k-raised text-k-muted hover:border-k-accent/50 hover:text-white transition-colors"
          >
            Bottom edge
          </button>
        </div>
        <p className="text-[10px] text-k-faint mt-1.5">
          Top clears the TikTok/Reels UI; the bottom third holds the caption and
          platform buttons.
        </p>
      </div>
    </>
  );
};

/* ============================ EXPORT ============================ */

// NOTE: this tab deliberately has NO resolution/container rows. The pipeline
// outputs 1080-based MP4 only (see Export.jsx), and a container row here once
// wrote "mp4"/"mov" into exportSettings.format — the key that stores the
// ASPECT ("9:16"|"1:1"|"16:9") — silently destroying the aspect choice.
//
// The ASPECT and BACKGROUND FILL are chosen HERE (Sprint 3): the canvas
// preview reflects both live (CanvasArea stage shape + fill layer), and the
// Export page only displays them read-only. Same exportSettings keys as
// always — draft persistence, undo, and the export payload are unchanged.
const ASPECT_LABELS = { "9:16": "Vertical", "1:1": "Square", "16:9": "Landscape" };

const ExportTab = () => {
  const navigate = useNavigate();
  const exportSettings = useAppStore((s) => s.exportSettings);
  const setExportSetting = useAppStore((s) => s.setExportSetting);
  const currentClipId = useAppStore((s) => s.currentClipId);

  return (
    <>
      <div>
        <SectionTitle>Canvas Aspect</SectionTitle>
        <div className="flex flex-wrap gap-1.5">
          {EXPORT_FORMATS.map((v) => (
            <button
              key={v}
              data-testid={EDITOR.aspectBtn(v)}
              onClick={() => setExportSetting("format", v)}
              title={ASPECT_LABELS[v]}
              className={`px-2.5 py-1.5 rounded-md text-[11px] font-mono font-semibold border transition-colors ${
                exportSettings.format === v
                  ? "border-k-accent bg-k-accent/12 text-white"
                  : "border-k-raised bg-k-surface text-k-muted hover:border-k-accent/40"
              }`}
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      <div>
        <SectionTitle>Background Fill</SectionTitle>
        <div className="flex flex-wrap gap-1.5 items-center">
          {BACKGROUND_OPTIONS.map((b) => (
            <button
              key={b}
              data-testid={EDITOR.bgFillBtn(b)}
              onClick={() => setExportSetting("background", b)}
              className={`px-2.5 py-1.5 rounded-md text-[11px] font-medium capitalize border transition-colors ${
                exportSettings.background === b
                  ? "border-k-accent bg-k-accent/12 text-white"
                  : "border-k-raised bg-k-surface text-k-muted hover:border-k-accent/40"
              }`}
            >
              {b}
            </button>
          ))}
          {exportSettings.background === "color" && (
            <input
              data-testid={EDITOR.bgFillColor}
              type="color"
              value={exportSettings.bgColor}
              onChange={(e) => setExportSetting("bgColor", e.target.value)}
              className="w-7 h-7 rounded cursor-pointer bg-transparent border border-k-raised"
            />
          )}
        </div>
        <p className="text-[10px] text-k-faint mt-1.5 leading-relaxed">
          Fills the bars when the footage doesn't match the canvas shape —
          shown live on the canvas, burned the same way on export.
        </p>
      </div>

      <div className="flex items-center justify-between">
        <SectionTitle>Burn-in Captions</SectionTitle>
        <button
          onClick={() => setExportSetting("burnInCaptions", !exportSettings.burnInCaptions)}
          className={`relative w-8 rounded-full transition-colors ${exportSettings.burnInCaptions ? "bg-k-accent" : "bg-k-raised"}`}
          style={{ height: 18 }}
        >
          <span
            className="absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white transition-all"
            style={{ left: exportSettings.burnInCaptions ? 16 : 2 }}
          />
        </button>
      </div>
      <button
        onClick={() => navigate(`/export/${currentClipId}`)}
        className="w-full py-2.5 rounded-lg bg-k-accent hover:bg-k-accent2 text-k-ink text-xs font-bold tracking-wide transition-colors shadow-[0_8px_24px_rgba(233,168,37,0.35)]"
      >
        Export Clip →
      </button>
    </>
  );
};

export default Inspector;
