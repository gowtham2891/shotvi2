import React, { useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Play,
  Edit3,
  Download,
  Clock,
  TrendingUp,
  Share2,
  Flame,
  Loader2,
  AlertTriangle,
  FileVideo,
  PartyPopper,
  X,
} from "lucide-react";
import { AppShell } from "@/components/shotvi/AppShell";
import { useAppStore } from "@/store/useAppStore";
import { CLIPS, CLIPS_CUE } from "@/constants/testIds";
import { hasSeenFirstClipCue, markFirstClipCueSeen } from "@/lib/onboarding";

const ViralityGauge = ({ score }) => {
  const color =
    score >= 85 ? "#3DBE7A" : score >= 70 ? "#facc15" : "#fb923c";
  return (
    <div className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-black/60 backdrop-blur-sm border border-white/10">
      <Flame size={12} style={{ color }} />
      <span
        className="font-mono text-xs font-bold"
        style={{ color }}
      >
        {score}
      </span>
      <span className="text-[10px] text-white/60">viral</span>
    </div>
  );
};

const ClipCard = ({ clip, onEdit, onPreview, onExport, rank }) => {
  return (
    <div
      data-testid={CLIPS.card(clip.id)}
      className="group rounded-xl border border-k-line bg-k-bg overflow-hidden hover:border-k-accent/50 transition-all"
    >
      {/* 9:16 preview */}
      <div className="relative aspect-[9/16] bg-black overflow-hidden">
        {clip.thumbnail ? (
          <img
            src={clip.thumbnail}
            alt={clip.hook}
            className="absolute inset-0 w-full h-full object-cover opacity-90 group-hover:scale-105 transition-transform duration-500"
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center bg-gradient-to-br from-k-surface to-k-raised">
            <FileVideo size={32} className="text-k-line" />
          </div>
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-black via-black/40 to-black/40" />

        {/* Rank pill */}
        <div className="absolute top-3 left-3 flex items-center gap-2">
          <div className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-black/60 backdrop-blur-sm text-white/80 border border-white/10">
            #{String(rank).padStart(2, "0")}
          </div>
          <ViralityGauge score={clip.virality} />
        </div>

        {/* Duration */}
        <div className="absolute top-3 right-3 text-[11px] font-mono text-white/90 flex items-center gap-1 bg-black/60 px-2 py-0.5 rounded backdrop-blur-sm border border-white/10">
          <Clock size={10} /> {clip.duration}s
        </div>

        {/* Caption preview */}
        <div className="absolute inset-x-4 bottom-16 text-center">
          <div className="caption-bold-yellow text-lg leading-tight inline">
            {clip.hook}
          </div>
        </div>

        {/* Hover play */}
        <button
          data-testid={CLIPS.preview(clip.id)}
          onClick={() => onPreview(clip)}
          className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
        >
          <div className="w-14 h-14 rounded-full bg-k-accent/90 backdrop-blur-sm flex items-center justify-center shadow-[0_0_30px_rgba(233,168,37,0.7)]">
            <Play size={20} className="text-white ml-0.5" fill="white" />
          </div>
        </button>

        {/* Source timecode */}
        <div className="absolute bottom-3 left-3 text-[10px] font-mono text-white/60 bg-black/50 px-1.5 py-0.5 rounded">
          from {clip.startAt}
        </div>
      </div>

      {/* Meta */}
      <div className="p-4">
        <p className="text-[11px] uppercase tracking-widest text-k-subtle mb-1.5">
          Hook line
        </p>
        <p className="text-sm text-white font-medium leading-snug line-clamp-2 mb-1">
          {clip.hook}
        </p>
        <p className="text-xs text-k-subtle line-clamp-1 mb-4">
          {clip.hookEn}
        </p>
        <div className="flex gap-2">
          <button
            data-testid={CLIPS.edit(clip.id)}
            onClick={() => onEdit(clip)}
            className="flex-1 inline-flex items-center justify-center gap-1.5 bg-k-accent hover:bg-k-accent2 text-k-ink text-xs font-semibold py-2 rounded-md transition-colors"
          >
            <Edit3 size={12} /> Edit
          </button>
          <button
            data-testid={CLIPS.export(clip.id)}
            onClick={() => onExport(clip)}
            className="inline-flex items-center justify-center gap-1.5 bg-k-surface border border-k-line hover:border-k-accent/50 text-white text-xs font-medium px-3 py-2 rounded-md transition-colors"
          >
            <Download size={12} /> Export
          </button>
          <button className="inline-flex items-center justify-center bg-k-surface border border-k-line hover:border-k-accent/50 text-white px-2.5 py-2 rounded-md transition-colors">
            <Share2 size={12} />
          </button>
        </div>
      </div>
    </div>
  );
};

export default function ClipsGallery() {
  const { projectId } = useParams();
  const navigate = useNavigate();

  const loadProjectClips = useAppStore((s) => s.loadProjectClips);
  const clipsLoading = useAppStore((s) => s.clipsLoading);
  const clipsError = useAppStore((s) => s.clipsError);
  const clips = useAppStore((s) => s.clipsByJob[projectId]) || [];
  const project = useAppStore((s) => s.getProject(projectId)) || {
    id: projectId,
    title: "Project",
    status: "ready",
  };
  const user = useAppStore((s) => s.user);

  const setCurrentClip = useAppStore((s) => s.setCurrentClip);

  useEffect(() => {
    loadProjectClips(projectId);
  }, [projectId, loadProjectClips]);

  // First-clip completion cue (PHASE 2 BUILD 3): a brand-new user's first
  // arrival here — right after their first job finishes — gets a one-time
  // "your first clips are ready" pointer. Eligibility is read once on mount
  // (before clips even load) so a re-render after clips arrive can't flip it
  // back on; markFirstClipCueSeen fires the instant it's shown, so it never
  // appears again for this user, on this project or any later one.
  const cueEligibleRef = useRef(!hasSeenFirstClipCue(user?.id));
  const [showFirstClipCue, setShowFirstClipCue] = useState(false);
  useEffect(() => {
    if (clips.length > 0 && cueEligibleRef.current) {
      cueEligibleRef.current = false;
      setShowFirstClipCue(true);
      markFirstClipCueSeen(user?.id);
    }
  }, [clips.length, user?.id]);

  const onEdit = (clip) => {
    setCurrentClip(clip.id);
    navigate(`/editor/${clip.id}`);
  };
  const onPreview = (clip) => {
    setCurrentClip(clip.id);
    navigate(`/editor/${clip.id}`);
  };
  const onExport = (clip) => {
    setCurrentClip(clip.id);
    navigate(`/export/${clip.id}`);
  };

  const avgVirality = clips.length
    ? Math.round(clips.reduce((a, c) => a + c.virality, 0) / clips.length)
    : 0;

  if (clipsLoading && clips.length === 0) {
    return (
      <AppShell title={project.title} subtitle="Loading clips…">
        <div data-testid={CLIPS.root} className="p-8 flex justify-center">
          <Loader2 size={28} className="text-k-accentsoft animate-spin mt-16" />
        </div>
      </AppShell>
    );
  }

  // Still processing / failed / expired → informative empty states
  if (!clipsLoading && (clipsError || clips.length === 0)) {
    const stillProcessing =
      !clipsError && project.status && !["ready", "failed", "expired"].includes(project.status);
    return (
      <AppShell
        title={project.title}
        subtitle={clipsError ? "Unavailable" : stillProcessing ? "Processing…" : "No clips"}
        actions={
          <button
            onClick={() => navigate("/dashboard")}
            className="inline-flex items-center gap-1.5 bg-k-surface border border-k-line hover:border-k-accent text-white text-sm px-3 py-2 rounded-md transition-colors"
            data-testid={CLIPS.backBtn}
          >
            <ArrowLeft size={14} /> All projects
          </button>
        }
      >
        <div data-testid={CLIPS.root} className="p-8 max-w-xl mx-auto text-center">
          <div className="rounded-2xl border border-k-line bg-k-bg p-10">
            {clipsError ? (
              <>
                <AlertTriangle size={28} className="text-k-warn mx-auto mb-4" />
                <p className="text-sm text-k-muted mb-6">{clipsError}</p>
                <div className="flex justify-center gap-3">
                  <button
                    onClick={() => loadProjectClips(projectId)}
                    className="text-xs font-semibold bg-k-accent hover:bg-k-accent2 text-k-ink px-4 py-2 rounded-md transition-colors"
                  >
                    Retry
                  </button>
                  <button
                    onClick={() => navigate("/upload")}
                    className="text-xs font-semibold bg-k-surface border border-k-line text-white px-4 py-2 rounded-md"
                  >
                    New project
                  </button>
                </div>
              </>
            ) : stillProcessing ? (
              <>
                <Loader2 size={28} className="text-k-accentsoft mx-auto mb-4 animate-spin" />
                <p className="text-sm font-semibold text-white mb-1">
                  This project is still processing
                </p>
                <p className="text-xs text-k-muted">
                  {project.currentStage || "Clips will appear here when the pipeline finishes."}
                </p>
              </>
            ) : (
              <p className="text-sm text-k-muted">No clips were generated for this project.</p>
            )}
          </div>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell
      title={project.title}
      subtitle={`${clips.length} AI-selected clips · Avg virality ${avgVirality}%`}
      actions={
        <button
          onClick={() => navigate("/dashboard")}
          className="inline-flex items-center gap-1.5 bg-k-surface border border-k-line hover:border-k-accent text-white text-sm px-3 py-2 rounded-md transition-colors"
          data-testid={CLIPS.backBtn}
        >
          <ArrowLeft size={14} /> All projects
        </button>
      }
    >
      <div data-testid={CLIPS.root} className="p-8 max-w-[1400px] mx-auto">
        {/* First-clip completion cue — one-time only, never shown again */}
        {showFirstClipCue && (
          <div
            data-testid={CLIPS_CUE.root}
            className="mb-6 rounded-xl border border-k-ok/30 bg-gradient-to-r from-k-ok/10 via-k-accent/5 to-transparent p-5 flex items-center gap-4"
          >
            <div className="w-11 h-11 rounded-full bg-k-ok/15 border border-k-ok/40 flex items-center justify-center shrink-0">
              <PartyPopper size={18} className="text-k-ok" />
            </div>
            <div className="flex-1">
              <p className="text-sm font-semibold text-white mb-0.5">
                Your first clips are ready 🎉
              </p>
              <p className="text-xs text-k-muted">
                Click any clip to edit captions, toggle Telugu/Tanglish, and export.
              </p>
            </div>
            <button
              data-testid={CLIPS_CUE.dismiss}
              onClick={() => setShowFirstClipCue(false)}
              className="text-k-subtle hover:text-white p-1 shrink-0"
              title="Dismiss"
            >
              <X size={15} />
            </button>
          </div>
        )}

        {/* Insight banner */}
        <div className="mb-8 rounded-xl border border-k-accent/30 bg-gradient-to-r from-k-accent/10 via-k-accent/5 to-transparent p-5 flex items-center gap-4">
          <div className="w-11 h-11 rounded-full bg-k-accent/20 border border-k-accent/40 flex items-center justify-center">
            <TrendingUp size={18} className="text-k-accentsoft" />
          </div>
          <div className="flex-1">
            <p className="text-sm font-semibold text-white mb-0.5">
              We found {clips.length} shareable moments in this video
            </p>
            <p className="text-xs text-k-muted">
              Top pick scored{" "}
              <span className="text-k-ok font-mono font-bold">
                {clips[0]?.virality}
              </span>
              /100 based on hook strength, punchline density & audio energy.
            </p>
          </div>
          <button
            onClick={() => navigate(`/editor/${clips[0].id}`)}
            className="text-xs font-semibold bg-white text-black px-4 py-2 rounded-md hover:bg-k-soft transition-colors"
          >
            Open top clip
          </button>
        </div>

        {/* Filters */}
        <div className="flex items-center justify-between mb-5">
          <div className="flex gap-2 text-xs">
            {["All", "Top scoring", "< 30s", "30-45s", "> 45s"].map(
              (f, i) => (
                <button
                  key={f}
                  className={`px-3 py-1.5 rounded-md border transition-colors ${
                    i === 0
                      ? "bg-k-accent/15 border-k-accent/40 text-white"
                      : "bg-k-bg border-k-line text-k-muted hover:text-white"
                  }`}
                >
                  {f}
                </button>
              )
            )}
          </div>
          <div className="flex items-center gap-2 text-xs text-k-subtle">
            <span>Sort by</span>
            <select className="bg-k-bg border border-k-line rounded-md px-2 py-1.5 text-white outline-none">
              <option>Virality score</option>
              <option>Duration</option>
              <option>Timeline order</option>
            </select>
          </div>
        </div>

        {/* Grid */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-5">
          {clips.map((clip, i) => (
            <ClipCard
              key={clip.id}
              clip={clip}
              rank={i + 1}
              onEdit={onEdit}
              onPreview={onPreview}
              onExport={onExport}
            />
          ))}
        </div>
      </div>
    </AppShell>
  );
}
