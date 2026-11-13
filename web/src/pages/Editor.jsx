import React, { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Undo2,
  Redo2,
  Save,
  Download,
  Check,
  Loader2,
  History,
  RotateCcw,
} from "lucide-react";
import { Logo } from "@/components/shotvi/Logo";
import { useAppStore } from "@/store/useAppStore";
import { USE_MOCKS } from "@/api/client";
import { getClipsForProject } from "@/data/mockData";
import { EDITOR } from "@/constants/testIds";
import { LeftClips } from "@/components/editor/LeftClips";
import { CanvasArea } from "@/components/editor/CanvasArea";
import { EditorHotkeys } from "@/components/editor/EditorHotkeys";
import { MarqueeSelect } from "@/components/editor/MarqueeSelect";
import { Inspector } from "@/components/editor/Inspector";
import { TimelineRow } from "@/components/editor/TimelineRow";

const AUTOSAVE_MS = 2500;

/**
 * Editor — thin composition layer.
 * Layout: TopBar / [ LeftClips | CanvasArea + TimelineRow | Inspector ]
 * All state lives in useAppStore; each panel subscribes to its own slice.
 */
export default function Editor() {
  const { clipId } = useParams();
  const navigate = useNavigate();

  const currentClipId = useAppStore((s) => s.currentClipId);
  const currentClip = useAppStore((s) => s.currentClip);
  const openClip = useAppStore((s) => s.openClip);
  const saveDraftNow = useAppStore((s) => s.saveDraftNow);
  const draftStatus = useAppStore((s) => s.draftStatus);
  const elements = useAppStore((s) => s.elements);
  const transcriptEdits = useAppStore((s) => s.transcriptEdits);
  const exportSettings = useAppStore((s) => s.exportSettings);
  const canUndo = useAppStore((s) => s.history.past.length > 0);
  const canRedo = useAppStore((s) => s.history.future.length > 0);
  const undo = useAppStore((s) => s.undo);
  const redo = useAppStore((s) => s.redo);
  const captionScript = useAppStore((s) => s.exportSettings.captionScript);
  const setCaptionScript = useAppStore((s) => s.setCaptionScript);
  const draftLoadStatus = useAppStore((s) => s.draftLoadStatus);

  // Pending-autosave flush (A5). Ref-based so cleanups/beforeunload see the
  // live value; saveDraftNow's own gate plus the 'ready' check here mean a
  // flush can only ever persist a fully-restored document — never the
  // freshly-reset empty one from a clip still loading.
  const dirtyRef = useRef(false);
  const flushPendingSave = useCallback(() => {
    if (!dirtyRef.current) return;
    const s = useAppStore.getState();
    if (s.draftLoadStatus !== "ready") return;
    dirtyRef.current = false;
    s.saveDraftNow();
  }, []);

  // Flush BEFORE switching clips: this effect is declared before the
  // openClip effect, so on a clip change its cleanup runs while the OLD
  // clip's document is still intact in the store — the debounce timer is
  // about to be discarded, but the edits it was holding must not be.
  useEffect(() => {
    return () => flushPendingSave();
  }, [clipId, flushPendingSave]);

  // Route param → open the clip (resolves job, loads transcript + draft)
  useEffect(() => {
    if (clipId) openClip(clipId);
  }, [clipId, openClip]);

  // Keyboard note: ALL editor shortcuts — including Ctrl+Z/Ctrl+Shift+Z/
  // Ctrl+Y for the undo/redo buttons above — dispatch through the single
  // global listener in CanvasArea (lib/editorKeymap.js). Editor.jsx must
  // NOT register its own window keydown: two listeners with separate typing
  // guards is how Space play/pause silently broke.

  // Mock-only playback loop: with a real clip, the <video> element in
  // CanvasArea drives currentTime instead.
  useEffect(() => {
    if (!USE_MOCKS) return undefined;
    const id = setInterval(() => {
      const s = useAppStore.getState();
      if (!s.isPlaying) return;
      const next = s.currentTime + 0.1;
      s.seek(next >= s.duration ? 0 : next);
    }, 100);
    return () => clearInterval(id);
  }, []);

  // Debounced draft autosave on document edits — deps cover ALL three
  // document slices (elements, transcriptEdits, exportSettings) so every
  // change AND every undo/redo restore persists the same way. History
  // itself is session-only and never saved.
  //
  // ARMING GUARD (A1): autosave stays disarmed until this clip's draft
  // restore confirmed either an applied draft or a genuine no-draft
  // ('ready'). While loading, the freshly-reset empty document must never
  // be persisted; after a FAILED load ('error') whether a draft exists is
  // unknown, so autosave stays off too — manual save remains available.
  const autosaveTimer = useRef(null);
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (USE_MOCKS || !currentClip || draftLoadStatus !== "ready") return undefined;
    setDirty(true);
    dirtyRef.current = true;
    if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    autosaveTimer.current = setTimeout(() => {
      dirtyRef.current = false;
      saveDraftNow();
      setDirty(false);
    }, AUTOSAVE_MS);
    return () => clearTimeout(autosaveTimer.current);
  }, [elements, transcriptEdits, exportSettings, currentClip, draftLoadStatus, saveDraftNow]);

  // A5: a pending debounced save must survive leaving the editor — flush on
  // unmount and on tab close instead of silently dropping up to 2.5s of
  // edits. Both paths run through the same guarded flush.
  useEffect(() => {
    window.addEventListener("beforeunload", flushPendingSave);
    return () => {
      window.removeEventListener("beforeunload", flushPendingSave);
      flushPendingSave();
    };
  }, [flushPendingSave]);

  // Title: real clip hook, else mock fallback
  const mockClip = USE_MOCKS
    ? getClipsForProject("prj_001").find((c) => c.id === currentClipId) ||
      getClipsForProject("prj_001")[0]
    : null;
  const title = currentClip?.hook || currentClip?.hookEn || mockClip?.hookEn || "Clip";

  // Per-route document title so the browser tab reflects the clip being edited
  // instead of a static "Shotvi — Editor" (restored on unmount).
  useEffect(() => {
    const prev = document.title;
    document.title = `${title} · Klipvi`;
    return () => {
      document.title = prev;
    };
  }, [title]);

  return (
    <div
      data-testid={EDITOR.root}
      className="h-screen flex flex-col bg-k-bg text-white overflow-hidden"
    >
      {/* Top bar */}
      <header className="h-14 border-b border-k-raised bg-k-bg flex items-center justify-between px-4 shrink-0 z-30">
        <div className="flex items-center gap-4">
          <button
            data-testid={EDITOR.backBtn}
            onClick={() => navigate(-1)}
            className="w-8 h-8 rounded-md border border-k-line hover:border-k-accent text-k-muted hover:text-white flex items-center justify-center transition-colors"
          >
            <ArrowLeft size={14} />
          </button>
          <Logo size="sm" />
          <div className="h-4 w-px bg-k-line" />
          <div className="text-sm max-w-[380px] truncate">
            <span className="text-k-subtle">Editing:</span>{" "}
            <span className="font-medium">{title}</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Telugu ⇄ Tanglish caption script toggle — flagship. Flips the
              caption canvas, the transcript panel, AND the burned export
              between Telugu script and casual romanized Tanglish. Display-
              only switch over stored data: instant, lossless, undoable. */}
          <div
            data-testid={EDITOR.scriptToggle}
            role="group"
            aria-label="Caption script"
            className="flex items-center rounded-lg border border-k-line bg-k-surface p-0.5"
          >
            {[
              { id: "telugu", label: "తెలుగు", title: "Captions in Telugu script" },
              { id: "tanglish", label: "Tanglish", title: "Captions in casual romanized Telugu (deenni-style)" },
            ].map(({ id, label, title: tip }) => (
              <button
                key={id}
                data-testid={id === "telugu" ? EDITOR.scriptTelugu : EDITOR.scriptTanglish}
                onClick={() => setCaptionScript(id)}
                title={tip}
                aria-pressed={captionScript === id}
                className={`px-3 py-1 rounded-md text-xs font-semibold transition-colors ${
                  captionScript === id
                    ? "bg-k-accent text-k-ink shadow-[0_4px_14px_-4px_rgba(233,168,37,0.8)]"
                    : "text-k-muted hover:text-white"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="h-4 w-px bg-k-line mx-1" />
          <button
            data-testid={EDITOR.undoBtn}
            onClick={undo}
            disabled={!canUndo}
            title="Undo (Ctrl+Z)"
            className={`w-8 h-8 rounded-md flex items-center justify-center transition-colors ${
              canUndo
                ? "hover:bg-white/5 text-k-muted hover:text-white"
                : "text-k-line2 cursor-not-allowed"
            }`}
          >
            <Undo2 size={14} />
          </button>
          <button
            data-testid={EDITOR.redoBtn}
            onClick={redo}
            disabled={!canRedo}
            title="Redo (Ctrl+Shift+Z / Ctrl+Y)"
            className={`w-8 h-8 rounded-md flex items-center justify-center transition-colors ${
              canRedo
                ? "hover:bg-white/5 text-k-muted hover:text-white"
                : "text-k-line2 cursor-not-allowed"
            }`}
          >
            <Redo2 size={14} />
          </button>
          <div className="h-4 w-px bg-k-line mx-1" />
          <DraftHistoryMenu />
          <button
            onClick={saveDraftNow}
            className="inline-flex items-center gap-1.5 text-xs bg-k-surface border border-k-line hover:border-k-accent/50 px-3 py-1.5 rounded-md transition-colors"
          >
            {draftStatus === "saving" ? (
              <Loader2 size={12} className="animate-spin" />
            ) : draftStatus === "saved" && !dirty ? (
              <Check size={12} className="text-k-ok" />
            ) : (
              <Save size={12} />
            )}
            {draftStatus === "saving" ? "Saving…" : draftStatus === "saved" && !dirty ? "Saved" : "Save draft"}
          </button>
          <button
            data-testid={EDITOR.exportBtn}
            onClick={() => navigate(`/export/${currentClipId || clipId}`)}
            className="inline-flex items-center gap-1.5 text-xs bg-k-accent hover:bg-k-accent2 text-k-ink font-semibold px-4 py-1.5 rounded-md transition-colors shadow-[0_6px_20px_-6px_rgba(233,168,37,0.7)]"
          >
            <Download size={12} /> Export
          </button>
        </div>
      </header>

      {/* Main 3-panel grid */}
      <div className="flex-1 min-h-0 grid grid-cols-[240px_1fr_300px]">
        <LeftClips />
        <main className="flex flex-col min-h-0 min-w-0">
          <CanvasArea currentClip={currentClip || mockClip} />
          <TimelineRow />
        </main>
        <Inspector />
      </div>
      {/* Feature #8: duplicate/copy-paste/z-order hotkeys — same resolver +
          typing guard as CanvasArea's listener, disjoint action set. */}
      <EditorHotkeys />
      {/* Feature #9: rubber-band multi-select over the stage background. */}
      <MarqueeSelect />
    </div>
  );
}

const EMPTY_VERSIONS = [];

const versionAge = (ts) => {
  const mins = Math.floor((Date.now() - ts) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  return `${hrs} hour${hrs > 1 ? "s" : ""} ago`;
};

/**
 * DraftHistoryMenu — restore the clip's draft "from N minutes ago".
 * Versions are session-only snapshots captured from committed saves (see
 * captureDraftVersion in the store); a restore goes through the normal
 * draft-apply path and is itself one undoable action.
 */
const DraftHistoryMenu = () => {
  const [open, setOpen] = useState(false);
  const versions = useAppStore(
    (s) => (s.currentClipId && s.draftVersions[s.currentClipId]) || EMPTY_VERSIONS
  );
  const restoreDraftVersion = useAppStore((s) => s.restoreDraftVersion);

  return (
    <div className="relative">
      <button
        data-testid={EDITOR.historyBtn}
        onClick={() => setOpen((o) => !o)}
        title="Draft version history"
        className={`w-8 h-8 rounded-md flex items-center justify-center transition-colors ${
          open ? "bg-white/5 text-white" : "hover:bg-white/5 text-k-muted hover:text-white"
        }`}
      >
        <History size={14} />
      </button>
      {open && (
        <div
          data-testid={EDITOR.historyPanel}
          className="absolute right-0 top-10 z-40 w-72 rounded-lg border border-k-line bg-k-bg shadow-[0_16px_48px_rgba(0,0,0,0.6)] p-2"
        >
          <p className="text-[10px] uppercase tracking-[0.22em] text-k-faint px-1.5 pt-1 pb-2">
            Draft versions
          </p>
          {versions.length === 0 ? (
            <p className="text-[11px] text-k-subtle px-1.5 pb-2 leading-relaxed">
              No versions yet — one is captured every time this clip's draft
              saves.
            </p>
          ) : (
            <div className="max-h-64 overflow-y-auto space-y-0.5">
              {versions.map((v, i) => (
                <div
                  key={v.id}
                  className="flex items-center justify-between gap-2 px-1.5 py-1.5 rounded-md hover:bg-white/[0.03]"
                >
                  <span className="text-[11px] text-k-soft">
                    {versionAge(v.ts)}
                    {i === 0 && (
                      <span className="ml-1.5 text-[9px] uppercase tracking-wide text-k-faint">
                        latest
                      </span>
                    )}
                  </span>
                  <button
                    data-testid={EDITOR.historyRestore(i)}
                    onClick={() => {
                      restoreDraftVersion(v.id);
                      setOpen(false);
                    }}
                    className="inline-flex items-center gap-1 text-[10px] font-semibold text-k-accentsoft hover:text-white bg-k-accent/10 hover:bg-k-accent/25 border border-k-accent/30 px-2 py-1 rounded-md transition-colors"
                  >
                    <RotateCcw size={10} /> Restore
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
