import React, { useRef, useState } from "react";
import {
  ZoomIn,
  ZoomOut,
  Maximize2,
  Frame,
  Plus,
  BarChart3,
  ImagePlus,
  AtSign,
  Ratio,
  Check,
  Loader2,
  Crop,
} from "lucide-react";
import { useAppStore } from "@/store/useAppStore";
import { EXPORT_FORMATS } from "@/api/renders";
import { EDITOR } from "@/constants/testIds";

const SAFE_ZONES = [
  { key: "off", label: "Off" },
  { key: "instagram", label: "Instagram" },
  { key: "youtube", label: "YouTube" },
];

const ADD_TYPES = [
  // "headline" removed from the menu — it tofu'd Telugu (Latin-only Outfit font)
  // and was dropped as a product decision. Type still handled for old drafts.
  { type: "progress", label: "Progress bar", icon: BarChart3 },
  { type: "logo", label: "Logo / handle", icon: AtSign },
];

const ASPECT_LABELS = { "9:16": "Vertical", "1:1": "Square", "16:9": "Landscape" };

/**
 * CanvasToolbar — compact vertical rail on the left of the canvas area.
 * Zoom controls, safe-zone selector, add element menu, fit-to-viewport.
 * Dropdowns open to the right so they don't spill off the left edge.
 */
export const CanvasToolbar = () => {
  const canvasZoom = useAppStore((s) => s.canvasZoom);
  const zoomIn = useAppStore((s) => s.zoomIn);
  const zoomOut = useAppStore((s) => s.zoomOut);
  const fitScreen = useAppStore((s) => s.fitScreen);
  const safeZoneMode = useAppStore((s) => s.safeZoneMode);
  const setSafeZoneMode = useAppStore((s) => s.setSafeZoneMode);
  const addElement = useAppStore((s) => s.addElement);
  const addImageOverlay = useAppStore((s) => s.addImageOverlay);
  const aspect = useAppStore((s) => s.exportSettings.format);
  const setExportSetting = useAppStore((s) => s.setExportSetting);
  const reframeMode = useAppStore((s) => s.reframeMode);
  const setReframeMode = useAppStore((s) => s.setReframeMode);
  const elements = useAppStore((s) => s.elements);
  const toggleElementVisibility = useAppStore(
    (s) => s.toggleElementVisibility
  );
  const setSelected = useAppStore((s) => s.setSelected);

  const [safeOpen, setSafeOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [aspectOpen, setAspectOpen] = useState(false);
  const [imageUploading, setImageUploading] = useState(false);
  const fileInputRef = useRef(null);

  const addOrShow = (type) => {
    // If an element of that type exists but hidden — show it. Otherwise add new.
    const existing = elements.find((el) => el.type === type);
    if (existing && !existing.visible) {
      toggleElementVisibility(existing.id);
      setSelected(existing.id);
    } else {
      addElement(type);
    }
    setAddOpen(false);
  };

  // User image overlays: always a NEW element (multiple images are fine —
  // the element system has no per-type limit and neither do we).
  const onImageFilePicked = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-picking the same file later
    if (!file) return;
    setAddOpen(false);
    setImageUploading(true);
    try {
      await addImageOverlay(file);
    } finally {
      setImageUploading(false);
    }
  };

  return (
    <div className="w-12 shrink-0 flex flex-col items-center gap-1.5 bg-k-bg/85 backdrop-blur-xl border-r border-k-line py-2.5 z-[90]">
      {/* Add element */}
      <div className="relative">
        <button
          data-testid={EDITOR.addElementMenu}
          onClick={() => setAddOpen((v) => !v)}
          title="Add element"
          className="w-8 h-8 flex items-center justify-center rounded-md text-k-ink bg-k-accent hover:bg-k-accent2 transition-colors"
        >
          <Plus size={14} />
        </button>
        {addOpen && (
          <div className="absolute top-0 left-full ml-1.5 w-52 bg-k-surface border border-k-line rounded-lg shadow-2xl overflow-hidden py-1 z-[95]">
            {ADD_TYPES.map((t) => {
              const Icon = t.icon;
              return (
                <button
                  key={t.type}
                  data-testid={EDITOR.addElementOption(t.type)}
                  onClick={() => addOrShow(t.type)}
                  className="w-full flex items-center gap-2 px-3 py-2 text-xs text-k-soft hover:bg-k-accent/15 hover:text-white transition-colors"
                >
                  <Icon size={13} className="text-k-accentsoft" />
                  {t.label}
                </button>
              );
            })}
            <button
              data-testid={EDITOR.addElementOption("image")}
              onClick={() => fileInputRef.current?.click()}
              disabled={imageUploading}
              className="w-full flex items-center gap-2 px-3 py-2 text-xs text-k-soft hover:bg-k-accent/15 hover:text-white transition-colors disabled:opacity-50"
            >
              {imageUploading ? (
                <Loader2 size={13} className="text-k-accentsoft animate-spin" />
              ) : (
                <ImagePlus size={13} className="text-k-accentsoft" />
              )}
              Your image (PNG/JPG)
            </button>
          </div>
        )}
        {/* Hidden picker for the user-image overlay upload */}
        <input
          ref={fileInputRef}
          data-testid={EDITOR.addImageInput}
          type="file"
          accept="image/png,image/jpeg"
          className="hidden"
          onChange={onImageFilePicked}
        />
      </div>

      {/* Canvas aspect (also in Inspector → Export; both write
          exportSettings.format, the same key the export payload reads) */}
      <div className="relative">
        <button
          data-testid={EDITOR.aspectBtn("toolbar")}
          onClick={() => setAspectOpen((v) => !v)}
          title={`Canvas: ${aspect} (${ASPECT_LABELS[aspect] || ""})`}
          className="w-8 h-8 flex items-center justify-center rounded-md text-k-muted hover:text-white hover:bg-white/5 transition-colors"
        >
          <Ratio size={14} />
        </button>
        {aspectOpen && (
          <div className="absolute top-0 left-full ml-1.5 w-44 bg-k-surface border border-k-line rounded-lg shadow-2xl overflow-hidden py-1 z-[95]">
            {EXPORT_FORMATS.map((v) => (
              <button
                key={v}
                data-testid={EDITOR.aspectBtn(`toolbar-${v}`)}
                onClick={() => {
                  setExportSetting("format", v);
                  setAspectOpen(false);
                }}
                className={`w-full flex items-center justify-between px-3 py-2 text-xs transition-colors ${
                  aspect === v
                    ? "bg-k-accent/15 text-white"
                    : "text-k-soft hover:bg-white/5 hover:text-white"
                }`}
              >
                <span className="font-mono">{v}</span>
                <span className="text-k-subtle">{ASPECT_LABELS[v]}</span>
                {aspect === v && <Check size={12} className="text-k-accentsoft" />}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Drag-to-reframe: toggles the crop-window overlay on the canvas
          (per-aspect window over the 16:9 master — Sprint 4) */}
      <button
        data-testid={EDITOR.reframeToggle}
        onClick={() => setReframeMode(!reframeMode)}
        title="Reframe (crop window)"
        className={`w-8 h-8 flex items-center justify-center rounded-md transition-colors border ${
          reframeMode
            ? "bg-k-accent/15 border-k-accent/40 text-white"
            : "border-transparent text-k-muted hover:text-white hover:bg-white/5"
        }`}
      >
        <Crop size={14} />
      </button>

      <div className="w-6 h-px bg-k-line my-0.5" />

      {/* Zoom controls */}
      <button
        data-testid={EDITOR.zoomIn}
        onClick={zoomIn}
        title="Zoom in"
        className="w-8 h-8 flex items-center justify-center rounded-md text-k-muted hover:text-white hover:bg-white/5 transition-colors"
      >
        <ZoomIn size={14} />
      </button>
      <div
        data-testid={EDITOR.zoomLevel}
        className="text-[10px] font-mono text-white leading-none py-0.5"
      >
        {Math.round(canvasZoom * 100)}%
      </div>
      <button
        data-testid={EDITOR.zoomOut}
        onClick={zoomOut}
        title="Zoom out"
        className="w-8 h-8 flex items-center justify-center rounded-md text-k-muted hover:text-white hover:bg-white/5 transition-colors"
      >
        <ZoomOut size={14} />
      </button>
      <button
        data-testid={EDITOR.zoomFit}
        onClick={fitScreen}
        title="Fit to viewport"
        className="w-8 h-8 flex items-center justify-center rounded-md text-k-muted hover:text-white hover:bg-white/5 transition-colors"
      >
        <Maximize2 size={14} />
      </button>

      <div className="w-6 h-px bg-k-line my-0.5" />

      {/* Safe zone */}
      <div className="relative">
        <button
          data-testid={EDITOR.safeZoneToggle}
          onClick={() => setSafeOpen((v) => !v)}
          title={`Safe zone: ${SAFE_ZONES.find((s) => s.key === safeZoneMode)?.label}`}
          className={`w-8 h-8 flex items-center justify-center rounded-md transition-colors border ${
            safeZoneMode !== "off"
              ? "bg-k-accent/15 border-k-accent/40 text-white"
              : "border-transparent text-k-muted hover:text-white hover:bg-white/5"
          }`}
        >
          <Frame size={14} />
        </button>
        {safeOpen && (
          <div className="absolute bottom-0 left-full ml-1.5 w-40 bg-k-surface border border-k-line rounded-lg shadow-2xl overflow-hidden py-1 z-[95]">
            {SAFE_ZONES.map((s) => (
              <button
                key={s.key}
                data-testid={EDITOR.safeZoneOption(s.key)}
                onClick={() => {
                  setSafeZoneMode(s.key);
                  setSafeOpen(false);
                }}
                className={`w-full flex items-center justify-between px-3 py-2 text-xs transition-colors ${
                  safeZoneMode === s.key
                    ? "bg-k-accent/15 text-white"
                    : "text-k-soft hover:bg-white/5 hover:text-white"
                }`}
              >
                {s.label}
                {safeZoneMode === s.key && (
                  <Check size={12} className="text-k-accentsoft" />
                )}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default CanvasToolbar;
