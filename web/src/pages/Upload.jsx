import React, { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  UploadCloud,
  Youtube,
  Link2,
  FileVideo,
  ChevronDown,
  CheckCircle2,
  Loader2,
  Sparkles,
  AlertTriangle,
  RotateCcw,
} from "lucide-react";
import { AppShell } from "@/components/shotvi/AppShell";
import { QuotaNotice } from "@/components/shotvi/QuotaNotice";
import { useAppStore } from "@/store/useAppStore";
import { useJobPolling } from "@/hooks/useJobPolling";
import { USE_MOCKS, QUOTA_EXCEEDED_STATUS } from "@/api/client";
import { UPLOAD } from "@/constants/testIds";
import { LANGUAGES } from "@/data/mockData";
import { isValidYoutubeUrl } from "@/lib/youtubeUrl";

const STEPS = [
  { key: "uploading", label: "Uploading video" },
  { key: "transcribing", label: "Transcribing speech" },
  { key: "selecting_clips", label: "AI selecting viral clips" },
  { key: "ready", label: "Preparing your clips" },
];

// Backend JobStatus → step index in the 4-step UI
const STATUS_TO_STEP = {
  pending: 0,
  downloading: 0,
  transcribing: 1,
  selecting: 2,
  cutting: 3,
  cropping: 3,
  captioning: 3,
};

export default function Upload() {
  const navigate = useNavigate();
  const addProject = useAppStore((s) => s.addProject);
  const submitFile = useAppStore((s) => s.submitFile);
  const submitYouTubeUrl = useAppStore((s) => s.submitYouTubeUrl);
  const applyJobUpdate = useAppStore((s) => s.applyJobUpdate);
  const resetSubmission = useAppStore((s) => s.resetSubmission);
  const activeJobId = useAppStore((s) => s.activeJobId);
  const uploadPercent = useAppStore((s) => s.uploadPercent);
  const submitError = useAppStore((s) => s.submitError);

  const [mode, setMode] = useState("file"); // 'file' | 'url'
  const [dragOver, setDragOver] = useState(false);
  const [file, setFile] = useState(null);
  const [url, setUrl] = useState("");
  const [language, setLanguage] = useState("te");
  const [langOpen, setLangOpen] = useState(false);

  const [processing, setProcessing] = useState(false);
  const [failure, setFailure] = useState(null); // {message, retryable, quota}
  const [urlError, setUrlError] = useState(null); // inline pre-submit validation

  // Pick up a job already in flight when this page mounts (e.g. the
  // dashboard's first-run hero submits the URL and navigates straight here)
  // — jump directly to the processing view instead of showing the picker
  // form over a job that's already running.
  useEffect(() => {
    if (!USE_MOCKS && activeJobId) setProcessing(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Mock-mode simulation state (offline dev path)
  const [activeStep, setActiveStep] = useState(0);
  const [stepProgress, setStepProgress] = useState(0);
  const timerRef = useRef(null);
  const fileInputRef = useRef(null);

  // ── Real pipeline progress via polling ─────────────────────────
  const { job, error: pollError, restart } = useJobPolling(activeJobId, {
    enabled: !USE_MOCKS && processing && !!activeJobId,
    onUpdate: applyJobUpdate,
    onDone: (doneJob) => {
      setTimeout(() => navigate(`/clips/${doneJob.job_id}`), 600);
    },
    onFailed: (failedJob) => {
      setFailure({
        message: failedJob.error || "Processing failed on the server.",
        retryable: true,
      });
    },
  });

  useEffect(() => {
    if (pollError) {
      setFailure({ message: pollError.message, retryable: pollError.type !== "expired" });
    }
  }, [pollError]);

  const startProcessing = async () => {
    if (mode === "file" && !file) return;
    if (mode === "url" && !url.trim()) return;

    // Inline validation BEFORE submitting — an obviously-invalid YouTube
    // link never becomes a job that runs and fails at the download stage.
    if (mode === "url" && !USE_MOCKS && !isValidYoutubeUrl(url.trim())) {
      setUrlError("That doesn't look like a YouTube link — try a youtube.com or youtu.be URL.");
      return;
    }
    setUrlError(null);

    if (USE_MOCKS) {
      setProcessing(true);
      setActiveStep(0);
      setStepProgress(0);
      return;
    }

    setFailure(null);
    setProcessing(true);
    try {
      if (mode === "file") {
        await submitFile({ file, language });
      } else {
        await submitYouTubeUrl({ url: url.trim(), language });
      }
    } catch (err) {
      // 402 = the monthly source-minute budget rejected this video. Not
      // retryable (the same video costs the same minutes next click) and not an
      // error — it renders as the quota panel with an upgrade path instead.
      const quota = err?.status === QUOTA_EXCEEDED_STATUS;
      setFailure({ message: err.message, retryable: !quota, quota });
    }
  };

  const retry = () => {
    setFailure(null);
    resetSubmission();
    setProcessing(false);
    restart();
  };

  // ── Mock-mode simulated progress (kept for REACT_APP_USE_MOCKS=true) ──
  useEffect(() => {
    if (!USE_MOCKS || !processing) return;
    timerRef.current = setInterval(() => {
      setStepProgress((p) => {
        if (p >= 100) {
          setActiveStep((s) => {
            if (s >= STEPS.length - 1) {
              clearInterval(timerRef.current);
              const newId = "prj_" + Math.random().toString(36).slice(2, 7);
              addProject({
                id: newId,
                title:
                  mode === "url"
                    ? "YouTube import — New podcast"
                    : file?.name || "New upload",
                thumbnail:
                  "https://images.pexels.com/photos/36917952/pexels-photo-36917952.jpeg",
                duration: "42:07",
                createdAt: "just now",
                status: "ready",
                clipsCount: 8,
                language,
              });
              setTimeout(() => navigate(`/clips/prj_001`), 800);
              return s;
            }
            return s + 1;
          });
          return 0;
        }
        return p + 4;
      });
    }, 120);
    return () => clearInterval(timerRef.current);
  }, [processing, addProject, navigate, mode, file, language]);

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) setFile(f);
  };

  const langCurrent = LANGUAGES.find((l) => l.code === language);

  // Real-mode step derivation
  const jobStep = job ? (STATUS_TO_STEP[job.status] ?? 0) : 0;
  const realActiveStep = USE_MOCKS
    ? activeStep
    : activeJobId
      ? jobStep
      : 0; // still uploading bytes
  const realStepProgress = USE_MOCKS
    ? stepProgress
    : activeJobId
      ? (job?.progress ?? 0)
      : uploadPercent;
  const stageLabel = !USE_MOCKS && job?.current_stage ? job.current_stage : null;

  return (
    <AppShell
      title="New Project"
      subtitle="Upload a video or paste a YouTube URL to get started"
    >
      <div
        data-testid={UPLOAD.root}
        className="p-8 max-w-3xl mx-auto"
      >
        {!processing ? (
          <>
            {/* Mode toggle */}
            <div className="flex p-1 bg-k-bg border border-k-line rounded-md mb-6 max-w-sm">
              <button
                data-testid={UPLOAD.modeFile}
                onClick={() => setMode("file")}
                className={`flex-1 flex items-center justify-center gap-2 text-sm font-medium py-2 rounded transition-colors ${
                  mode === "file"
                    ? "bg-k-accent text-k-ink"
                    : "text-k-muted hover:text-white"
                }`}
              >
                <FileVideo size={14} /> Upload file
              </button>
              <button
                data-testid={UPLOAD.modeUrl}
                onClick={() => setMode("url")}
                className={`flex-1 flex items-center justify-center gap-2 text-sm font-medium py-2 rounded transition-colors ${
                  mode === "url"
                    ? "bg-k-accent text-k-ink"
                    : "text-k-muted hover:text-white"
                }`}
              >
                <Youtube size={14} /> YouTube URL
              </button>
            </div>

            {mode === "file" ? (
              <label
                data-testid={UPLOAD.dropZone}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={handleDrop}
                className={`relative block rounded-2xl border-2 border-dashed cursor-pointer transition-all min-h-[300px] flex flex-col items-center justify-center p-10 text-center overflow-hidden ${
                  dragOver
                    ? "border-k-accent bg-k-accent/8"
                    : "border-k-line bg-k-bg hover:border-k-accent/50"
                }`}
              >
                <div className="absolute inset-0 grid-lines opacity-40 pointer-events-none" />
                <div className="relative">
                  <div className="w-16 h-16 mx-auto mb-5 rounded-2xl bg-gradient-to-br from-k-accent to-k-mark flex items-center justify-center shadow-[0_10px_30px_-5px_rgba(233,168,37,0.5)]">
                    <UploadCloud size={26} className="text-white" />
                  </div>
                  <h3 className="font-display text-xl font-semibold mb-2">
                    {file ? file.name : "Drop your video here"}
                  </h3>
                  <p className="text-sm text-k-muted mb-6">
                    {file
                      ? `${(file.size / 1024 / 1024).toFixed(1)} MB · Ready to process`
                      : "MP4 up to 2 GB · 2 hr max (other formats best-effort)"}
                  </p>
                  <input
                    ref={fileInputRef}
                    data-testid={UPLOAD.fileInput}
                    type="file"
                    accept="video/*"
                    onChange={(e) => setFile(e.target.files?.[0])}
                    className="hidden"
                  />
                  <button
                    type="button"
                    onClick={(e) => {
                      e.preventDefault();
                      fileInputRef.current?.click();
                    }}
                    className="inline-flex items-center gap-2 bg-k-surface border border-k-line hover:border-k-accent text-white text-sm font-medium px-5 py-2.5 rounded-md transition-colors"
                  >
                    Browse files
                  </button>
                </div>
              </label>
            ) : (
              <div className="rounded-2xl border border-k-line bg-k-bg p-8">
                <div className="w-14 h-14 rounded-2xl bg-red-500/15 border border-red-500/30 flex items-center justify-center mb-5">
                  <Youtube size={22} className="text-red-500" />
                </div>
                <h3 className="font-display text-xl font-semibold mb-2">
                  Paste a YouTube link
                </h3>
                <p className="text-sm text-k-muted mb-5">
                  We'll import directly — no downloading or re-uploading.
                </p>
                <div className="relative">
                  <Link2
                    size={15}
                    className="absolute left-3 top-1/2 -translate-y-1/2 text-k-subtle"
                  />
                  <input
                    data-testid={UPLOAD.urlInput}
                    value={url}
                    onChange={(e) => {
                      setUrl(e.target.value);
                      if (urlError) setUrlError(null);
                    }}
                    type="url"
                    placeholder="https://youtube.com/watch?v=..."
                    className="w-full bg-k-surface border border-k-line rounded-md py-3 pl-10 pr-3 text-sm text-white placeholder-k-faint focus:border-k-accent focus:ring-1 focus:ring-k-accent outline-none transition-colors"
                  />
                </div>
                {urlError && (
                  <p className="mt-3 text-sm text-k-bad flex items-center gap-2">
                    <AlertTriangle size={14} /> {urlError}
                  </p>
                )}
              </div>
            )}

            {/* Submission error (pre-processing) */}
            {submitError && !processing && (
              <div className="mt-4 rounded-md border border-k-bad/40 bg-k-bad/10 p-3 text-sm text-k-bad flex items-center gap-2">
                <AlertTriangle size={14} /> {submitError}
              </div>
            )}

            {/* Language + submit */}
            <div className="mt-6 rounded-xl border border-k-line bg-k-bg p-5 flex flex-col sm:flex-row sm:items-center gap-4">
              <div className="flex-1 relative">
                <label className="text-[11px] uppercase tracking-widest text-k-subtle mb-2 block">
                  Source language
                </label>
                <button
                  data-testid={UPLOAD.languageSelect}
                  type="button"
                  onClick={() => setLangOpen((v) => !v)}
                  className="w-full flex items-center justify-between bg-k-surface border border-k-line rounded-md py-2.5 px-3 text-sm text-white hover:border-k-accent/50 transition-colors"
                >
                  <span className="flex items-center gap-2">
                    <span className="text-[11px] font-mono px-1.5 py-0.5 rounded bg-k-accent/20 text-k-accentsoft border border-k-accent/30">
                      {langCurrent.flag}
                    </span>
                    {langCurrent.label}
                  </span>
                  <ChevronDown size={14} className="text-k-subtle" />
                </button>
                {langOpen && (
                  <div className="absolute z-10 mt-2 w-full bg-k-surface border border-k-line rounded-md shadow-2xl overflow-hidden">
                    {LANGUAGES.map((l) => (
                      <button
                        key={l.code}
                        data-testid={UPLOAD.languageOption(l.code)}
                        onClick={() => {
                          setLanguage(l.code);
                          setLangOpen(false);
                        }}
                        className={`w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:bg-k-accent/15 transition-colors ${
                          language === l.code ? "bg-k-accent/10" : ""
                        }`}
                      >
                        <span className="text-[11px] font-mono px-1.5 py-0.5 rounded bg-k-line text-k-muted">
                          {l.flag}
                        </span>
                        {l.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <button
                data-testid={UPLOAD.submit}
                onClick={startProcessing}
                disabled={
                  (mode === "file" && !file) || (mode === "url" && !url.trim())
                }
                className="inline-flex items-center gap-2 bg-k-accent hover:bg-k-accent2 disabled:bg-k-line disabled:text-k-subtle disabled:cursor-not-allowed text-k-ink font-semibold px-6 py-2.5 rounded-md transition-colors shadow-[0_10px_30px_-10px_rgba(233,168,37,0.6)] sm:self-end"
              >
                <Sparkles size={15} /> Start clipping
              </button>
            </div>

            {/* Tips */}
            <div className="mt-8 grid sm:grid-cols-3 gap-3 text-xs text-k-muted">
              {[
                "For best results, use clear audio in a single language.",
                "Podcast, interview & vlog format works best.",
                "Videos 20–90 min yield the highest clip variety.",
              ].map((t, i) => (
                <div
                  key={i}
                  className="rounded-md border border-k-line bg-k-bg p-3 leading-relaxed"
                >
                  <span className="font-mono text-k-accent mr-2">
                    0{i + 1}
                  </span>
                  {t}
                </div>
              ))}
            </div>
          </>
        ) : failure?.quota ? (
          /* ── Quota block (HTTP 402): a paywall, not a failure. The server's
               copy carries the real numbers; the CTA is the same checkout the
               sidebar Upgrade button opens. ── */
          <div data-testid={UPLOAD.progress}>
            <QuotaNotice
              title="This video is over your monthly source minutes"
              message={failure.message}
            >
              <button
                onClick={() => {
                  retry();
                  navigate("/dashboard");
                }}
                className="inline-flex items-center gap-2 bg-k-surface border border-k-line hover:border-k-accent/50 text-white text-sm font-medium px-4 py-2.5 rounded-md transition-colors"
              >
                Back to dashboard
              </button>
            </QuotaNotice>
            <p className="mt-3 text-xs text-k-subtle">
              Nothing was processed and no minutes were used. A shorter video may
              still fit inside what's left this month.
            </p>
          </div>
        ) : failure ? (
          /* ── Failure view: ASR/render/download error, stall, expiry ── */
          <div
            data-testid={UPLOAD.progress}
            className="rounded-2xl border border-k-bad/40 bg-k-bg p-10 text-center"
          >
            <div className="w-16 h-16 mx-auto mb-5 rounded-2xl bg-k-bad/15 border border-k-bad/30 flex items-center justify-center">
              <AlertTriangle size={26} className="text-k-bad" />
            </div>
            <h2 className="font-display text-2xl font-bold tracking-tight mb-2">
              Processing failed
            </h2>
            <p className="text-sm text-k-muted mb-6 max-w-md mx-auto break-words">
              {failure.message}
            </p>
            <div className="flex items-center justify-center gap-3">
              {failure.retryable && (
                <button
                  onClick={retry}
                  className="inline-flex items-center gap-2 bg-k-accent hover:bg-k-accent2 text-k-ink font-semibold px-5 py-2.5 rounded-md transition-colors"
                >
                  <RotateCcw size={14} /> Try again
                </button>
              )}
              <button
                onClick={() => {
                  retry();
                  navigate("/dashboard");
                }}
                className="inline-flex items-center gap-2 bg-k-surface border border-k-line hover:border-k-accent/50 text-white font-medium px-5 py-2.5 rounded-md transition-colors"
              >
                Back to dashboard
              </button>
            </div>
          </div>
        ) : (
          <div
            data-testid={UPLOAD.progress}
            className="rounded-2xl border border-k-line bg-k-bg p-10"
          >
            <div className="text-center mb-8">
              <div className="w-16 h-16 mx-auto mb-5 rounded-2xl bg-gradient-to-br from-k-accent to-k-mark flex items-center justify-center pulse-glow">
                <Sparkles size={26} className="text-white" />
              </div>
              <h2 className="font-display text-2xl font-bold tracking-tight mb-2">
                Cooking up your viral clips
              </h2>
              <p className="text-sm text-k-muted">
                {stageLabel || "Sit tight — this usually takes 2–4 minutes."}
              </p>
            </div>

            <ol className="space-y-3">
              {STEPS.map((step, idx) => {
                const done = idx < realActiveStep;
                const active = idx === realActiveStep;
                return (
                  <li
                    key={step.key}
                    className={`flex items-center gap-4 rounded-lg border p-4 transition-colors ${
                      active
                        ? "border-k-accent/50 bg-k-accent/8"
                        : done
                          ? "border-k-ok/30 bg-k-ok/5"
                          : "border-k-line bg-k-surface"
                    }`}
                  >
                    <div
                      className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${
                        done
                          ? "bg-k-ok/20 text-k-ok"
                          : active
                            ? "bg-k-accent/20 text-k-accentsoft"
                            : "bg-k-line text-k-subtle"
                      }`}
                    >
                      {done ? (
                        <CheckCircle2 size={16} />
                      ) : active ? (
                        <Loader2 size={16} className="animate-spin" />
                      ) : (
                        <span className="text-xs font-mono">
                          0{idx + 1}
                        </span>
                      )}
                    </div>
                    <div className="flex-1">
                      <p
                        className={`text-sm font-medium ${
                          done ? "text-k-muted" : "text-white"
                        }`}
                      >
                        {step.label}
                      </p>
                      {active && (
                        <div className="mt-2 h-1 rounded-full bg-k-raised overflow-hidden">
                          <div
                            className="h-full bg-gradient-to-r from-k-accent to-k-mark rounded-full transition-all"
                            style={{ width: `${realStepProgress}%` }}
                          />
                        </div>
                      )}
                    </div>
                    {done && (
                      <span className="text-[11px] text-k-ok font-medium">
                        Done
                      </span>
                    )}
                    {active && (
                      <span className="text-[11px] text-k-accentsoft font-mono">
                        {realStepProgress}%
                      </span>
                    )}
                  </li>
                );
              })}
            </ol>
          </div>
        )}
      </div>
    </AppShell>
  );
}
