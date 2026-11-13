import { create } from "zustand";
import { toast } from "sonner";
import {
  PROJECTS as MOCK_PROJECTS,
  getClipsForProject as getMockClips,
  WORD_TRANSCRIPT,
  CLIP_DURATION,
  CAPTION_PRESETS,
  INITIAL_ELEMENTS,
  PILL_DEFAULTS_BY_PRESET,
  MOCK_EMOJI_SUGGESTIONS,
} from "@/data/mockData";
import { USE_MOCKS, outputFileUrl, isQuotaError } from "@/api/client";
import { isPaidPlanKey, isPaidAccount, PAID_PLAN_FALLBACK_NAME } from "@/lib/plan";
import { createJobFromUrl, createJobFromFile, getJob, listJobs } from "@/api/uploads";
import { mapClipToUi, saveDraft, loadDraft, generateClipMetadata } from "@/api/clips";
import {
  getTranscript,
  getSegmentSidecar,
  buildClipTranscript,
  isMultiSegmentClip,
  canRemapMultiSegment,
} from "@/api/transcripts";
import {
  buildRerenderRequest,
  startRerender,
  DEFAULT_STYLE_ID,
  isKnownStyle,
  isKnownCaptionFont,
  presetLatinFont,
  resolveCaptionFont,
  DEFAULT_LATIN_CAPTION_FONT,
  CAPTION_DEFAULT_POSITION,
} from "@/api/renders";
import { getCaptionTemplate, putCaptionTemplate } from "@/api/templates";
import { uploadOverlayImage } from "@/api/overlays";
import {
  listProjects as registryList,
  upsertProject as registryUpsert,
  removeProject as registryRemove,
  getProjectEntry,
} from "@/lib/projectRegistry";
import { assertEditDocumentNormalized } from "@/lib/editDocumentValidation";
import {
  createEmptyTranscriptEdits,
  sanitizeTranscriptEdits,
} from "@/lib/transcriptEdits";
import {
  clampCropBox,
  boxesAlmostEqual,
  inferMasterAspect,
  initialWindowForAspect,
  roundCropBox,
} from "@/lib/cropWindow";
import { fetchTanglish } from "@/api/tanglish";
import { fetchTransliterations } from "@/api/transliterate";
import { supabase, AUTH_ENABLED, mapSupabaseUser, DEV_USER } from "@/lib/supabaseClient";
import { buildEmojiOverlayElements } from "@/lib/emojiOverlays";
import { generatePunchPoints, punchesToKeyframes, togglePunch } from "@/lib/autoZoom";
import { detectRemovableSpans, wordInSpans, spansToPairs } from "@/lib/fillerRemoval";
import { setUnauthorizedHandler } from "@/api/client";
import { getBillingStatus, createSubscription, cancelSubscription } from "@/api/billing";
import { loadRazorpay, openSubscriptionCheckout } from "@/lib/razorpayCheckout";

// Utility: clamp
const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

let elementIdCounter = 100;
const nextElementId = (type) => `el_${type}_${++elementIdCounter}`;

// Element types the editor can render/burn. Drafts saved before a type was
// removed (e.g. the retired `sticker`) may still reference it — filter those
// out on load so an old draft neither crashes nor renders a ghost element.
const KNOWN_ELEMENT_TYPES = new Set(["caption", "headline", "progress", "logo", "image", "emoji"]);
const sanitizeDraftElements = (elements) =>
  (elements || []).filter((el) => el && KNOWN_ELEMENT_TYPES.has(el.type));

// ── Per-clip document defaults ─────────────────────────────────────
// Elements and exportSettings are DOCUMENT state: they autosave into the
// open clip's draft, so opening a clip must start both from a clean slate
// (never the previous clip's overlays/pill/aspect) before that clip's own
// draft — or the saved caption template — repopulates them. Deep-cloned so
// no clip ever mutates the shared INITIAL_ELEMENTS constant.
const initialElements = () => JSON.parse(JSON.stringify(INITIAL_ELEMENTS));

const DEFAULT_EXPORT_SETTINGS = {
  format: "9:16", // FORMAT_CONFIG keys in api/worker.py — the ASPECT, not the container
  background: "blur", // blur | black | white | color
  bgColor: "#000000",
  useAutocrop: true,
  burnInCaptions: true,
  // Telugu ⇄ Tanglish caption toggle. Lives in exportSettings ON PURPOSE:
  // it's part of the document, so it draft-persists (autosave deps cover
  // exportSettings), sits inside undo history (documentSnapshot covers it),
  // and serializes to export with zero new plumbing. Display-only switch
  // over stored data — flipping never touches transcript/edit state.
  captionScript: "telugu", // 'telugu' | 'tanglish'
  // Clip boundary trim, seconds within the clip's own video. Backend
  // contract (api/models.py trim_start/trim_end): 0 / -1 sentinels mean
  // "untrimmed", and buildRerenderRequest always sends both fields, so the
  // defaults keep trim-untouched export payloads byte-identical.
  trimStart: 0,
  trimEnd: -1,
  // Sprint 4 — per-aspect crop windows over the 16:9 master, keyed by
  // aspect ("9:16" | "1:1" | "16:9") with {x,y,w,h} 0–1 fraction values.
  // ONLY aspects the user actually dragged get an entry; an absent key means
  // "use the derived default" (initialWindowForAspect — the AI face crop for
  // 9:16). Empty = fully untouched, which keeps the 9:16 export payload —
  // and therefore its output — byte-identical to before this sprint.
  cropWindows: {},
  // Feature #13 — auto-zoom / punch-ins. Sorted clip-local times (seconds)
  // where the frame punches in (1.0→1.12→1.0). null = "not yet initialized"
  // → openClip auto-seeds from word beats; an array (incl. []) is the user's
  // final say (add/remove on the timeline). Part of the document, so it
  // draft-persists, undoes, and serializes to export like every other field.
  // Points → crop_keyframes happens only at the wire boundary (buildRerenderRequest).
  punchPoints: null,
  // Feature #14 — filler/silence removal. Active cut spans [{start,end,kind}]
  // (clip-local seconds). null = feature off (no cuts). Enabling seeds it from
  // detection; the user restores individual spans (struck-through transcript).
  // Sent to the backend as cut_spans [[start,end],...]; the burn drops those
  // words + remaps the rest. Part of the document → draft/undo/autosave.
  cutSpans: null,
};
const defaultExportSettings = () => ({
  ...DEFAULT_EXPORT_SETTINGS, cropWindows: {}, punchPoints: null, cutSpans: null,
});

// Stale-run token for openClip: rapid clip switching interleaves two async
// runs, and the slower one's set() calls must not land on the newer clip's
// editor. Module-level on purpose — not reactive state, never rendered.
let _openSeq = 0;

// JobStatus (backend enum) → dashboard project status keys (STATUS_META)
const JOB_TO_PROJECT_STATUS = {
  pending: "uploading",
  downloading: "uploading",
  transcribing: "transcribing",
  selecting: "selecting_clips",
  cutting: "selecting_clips",
  cropping: "selecting_clips",
  captioning: "selecting_clips",
  done: "ready",
  failed: "failed",
};

const timeAgo = (ts) => {
  if (!ts) return "";
  const mins = Math.floor((Date.now() - ts) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} hour${hrs > 1 ? "s" : ""} ago`;
  const days = Math.floor(hrs / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
};

// JobOut + registry entry → dashboard project card shape
function jobToProject(job, entry) {
  const firstClip = job.clips?.[0];
  return {
    id: job.job_id,
    title: entry?.title || job.video_id || `Job ${job.job_id.slice(0, 8)}`,
    thumbnail: firstClip ? mapClipToUi(firstClip, 0, job.job_id).thumbnail : null,
    duration: "",
    createdAt: timeAgo(entry?.createdAt),
    status: JOB_TO_PROJECT_STATUS[job.status] || "uploading",
    progress: job.progress,
    clipsCount: job.clips?.length || 0,
    language: entry?.language || "te",
    videoId: job.video_id || entry?.videoId || null,
    error: job.error || null,
    currentStage: job.current_stage || "",
  };
}

const surfaceError = (err, fallback) => {
  const msg = err?.message || fallback;
  toast.error(msg);
  return msg;
};

// Quota-aware variant. A 402 is a paywall, not a fault: the server's `detail`
// already reads as user-facing copy ("You've used X of Y render minutes this
// month."), and every 402 site pairs it with an inline upgrade panel — a red
// error toast on top of that reads as a crash and gives the user nothing to
// act on. So 402 skips the toast; everything else behaves exactly as before.
// Returns {message, status} so the slice can persist the status and decide to
// render the upgrade affordance.
const recordError = (err, fallback) => {
  const message = err?.message || fallback;
  if (!isQuotaError(err)) toast.error(message);
  return { message, status: err?.status ?? null };
};

// ── Undo/redo history internals ────────────────────────────────────
// Snapshot = the document's three independent slices — exactly what
// getEditDocument serializes and applyDraft restores. Deep-copied via JSON
// (all three are JSON-safe by construction: they round-trip through Redis
// drafts already); a shared nested reference here would corrupt history
// silently the next time the live slice mutates.
const HISTORY_LIMIT = 50; // frames — bounds memory
const documentSnapshot = (s) =>
  JSON.parse(
    JSON.stringify({
      elements: s.elements,
      exportSettings: s.exportSettings,
      transcriptEdits: s.transcriptEdits,
    })
  );

// Sliding-window coalescing: continuous gestures (drag ticks, slider
// drags, held-arrow nudges) hit pushHistory dozens of times per second
// with the same key — only the FIRST call pushes (capturing the pre-
// gesture state); the rest just refresh the window, so a gesture of any
// length is ONE undo frame. endHistoryCoalescing() (pointerup / arrow
// keyup) closes the gesture so the next one pushes fresh. Module-level on
// purpose: not reactive state, never rendered.
const COALESCE_MS = 800;
let _coalesce = { key: null, at: 0 };

// ── Draft version history internals ────────────────────────────────
// A version is captured only from a COMMITTED save (saveDraftNow success),
// and an empty/default document is never captured — so a wiped autosave can
// never become a restorable "version". Bounded per clip to cap memory.
// Versions are addressed by a monotonic id, not their timestamp — two saves
// can land in the same millisecond.
const DRAFT_VERSION_LIMIT = 15;
let _versionSeq = 0;

const deepEqual = (a, b) => {
  if (a === b) return true;
  if (typeof a !== typeof b || a === null || b === null) return false;
  if (typeof a !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  return ka.every((k) => deepEqual(a[k], b[k]));
};

// "Default" = what openClip resets to before a draft/template lands: no
// transcript edits and both other slices at their pristine per-clip values.
const isDefaultEditDocument = (doc) => {
  const te = doc?.transcriptEdits || {};
  const hasEdits =
    Object.keys(te.wordEdits || {}).length > 0 ||
    (te.lineSplits || []).length > 0 ||
    (te.mergedGroups || []).length > 0 ||
    Object.keys(te.lineRealignments || {}).length > 0;
  if (hasEdits) return false;
  return (
    deepEqual(doc?.elements, initialElements()) &&
    deepEqual(doc?.exportSettings, defaultExportSettings())
  );
};

export const useAppStore = create((set, get) => ({
  // ============ AUTH (Supabase) ============
  // user: the identity shape the UI renders; session: the raw Supabase
  // session (token lifetime is supabase-js's job, not ours). Dev mode — no
  // REACT_APP_SUPABASE_* env — permanently signs in a fake dev user and all
  // auth actions are no-ops, pairing with the backend's DEV_MODE flag.
  user: AUTH_ENABLED ? null : DEV_USER,
  session: null,
  authEnabled: AUTH_ENABLED,
  authLoading: AUTH_ENABLED, // true until the persisted session is restored
  passwordRecovery: false,   // true after following a reset-password email link

  // Called once at app mount. Restores the persisted session, subscribes to
  // auth changes, and registers the API client's 401 handler (a dead session
  // mid-flight → clear auth state → route guard shows the auth screen).
  // Returns an unsubscribe cleanup for the effect.
  initAuth: () => {
    if (!AUTH_ENABLED) return () => {};
    // Only invoked for a GENUINELY dead session — api/client.js classifies
    // 401s first: a request that raced a just-created session (post-login)
    // is retried once with the fresh token and never reaches this handler.
    // We only get here when no usable session exists locally, or a retry
    // carrying a token the auth client considers live was still rejected
    // (revoked server-side). Global sign-out is intentional for those cases.
    setUnauthorizedHandler(() => {
      supabase.auth.signOut().catch(() => {});
      set({ user: null, session: null });
      toast.error("Session expired — please sign in again");
    });
    supabase.auth.getSession().then(({ data }) => {
      set({
        session: data?.session || null,
        user: mapSupabaseUser(data?.session?.user),
        authLoading: false,
      });
    });
    const { data: sub } = supabase.auth.onAuthStateChange((event, session) => {
      set({
        session: session || null,
        user: mapSupabaseUser(session?.user),
        authLoading: false,
        ...(event === "PASSWORD_RECOVERY" ? { passwordRecovery: true } : {}),
      });
    });
    return () => sub?.subscription?.unsubscribe();
  },

  signInWithPassword: async (email, password) => {
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) throw new Error(error.message);
  },

  signUpWithPassword: async (email, password, name) => {
    const { data, error } = await supabase.auth.signUp({
      email,
      password,
      options: { data: { full_name: name || "" } },
    });
    if (error) throw new Error(error.message);
    // With email confirmation on (Supabase default) there is no session yet.
    return { needsConfirmation: !data?.session };
  },

  signInWithGoogle: async () => {
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/dashboard` },
    });
    if (error) throw new Error(error.message);
  },

  resetPassword: async (email) => {
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/auth`,
    });
    if (error) throw new Error(error.message);
  },

  // Completes the reset-password email flow (PASSWORD_RECOVERY session).
  updatePassword: async (password) => {
    const { error } = await supabase.auth.updateUser({ password });
    if (error) throw new Error(error.message);
    set({ passwordRecovery: false });
  },

  signOut: async () => {
    if (AUTH_ENABLED) await supabase.auth.signOut().catch(() => {});
    set({ user: AUTH_ENABLED ? null : DEV_USER, session: null, billingStatus: null });
  },

  // ============ BILLING (Razorpay — the Creator plan) ============
  // Plan status read from GET /billing/status. This slice records who is paid
  // and drives the sidebar upgrade card — it GATES NOTHING (no paid features
  // exist yet); a future feature reads billingStatus.plan for a one-line gate.
  // configured=false (Razorpay env absent on the server) → the card shows a
  // "not set up" state instead of an upgrade button, so the app never crashes
  // or dead-ends when billing isn't configured.
  billingStatus: null,          // {plan, subscription_status, subscription_id, configured, plan_info} | null
  billingLoading: false,
  billingActionPending: false,  // checkout or cancel in flight → buttons disable

  loadBillingStatus: async () => {
    if (USE_MOCKS) {
      set({
        billingStatus: {
          plan: "free", subscription_status: "", subscription_id: "",
          configured: false, plan_info: null,
          // Tier entitlements + usage (features #17-20). Two SEPARATE meters:
          // render minutes (output, cheap) and source minutes (input — the one
          // that spends ASR/Gemini money and blocks POST /jobs with a 402).
          watermark: true, render_minutes_used: 0, render_minutes_budget: 30,
          source_minutes_used: 0, source_minutes_budget: 10,
          expiry_hours: 24,
        },
      });
      return;
    }
    set({ billingLoading: true });
    try {
      const status = await getBillingStatus();
      set({ billingStatus: status, billingLoading: false });
    } catch (err) {
      // Non-fatal and ambient (not a user action) — don't toast; the card just
      // won't show plan state.
      set({ billingLoading: false });
    }
  },

  // Paid test goes through lib/plan.js — NOT a literal plan-key compare. The
  // purchasable key is now `creator`; `studio` is the legacy alias still
  // persisted in live Redis hashes, and both must read as paid. isPaidAccount
  // also falls back to the resolved entitlements the server already returns.
  isPaidPlan: () => isPaidAccount(get().billingStatus),

  // Start the Razorpay checkout: create a subscription, load + open the modal,
  // and on successful payment refetch status (the webhook flips it server-
  // side). billingActionPending stays true across the modal's lifetime;
  // success, dismiss, or failure all clear it.
  startUpgrade: async () => {
    const s = get();
    if (s.billingActionPending) return;
    if (!s.billingStatus?.configured) {
      toast.error("Billing isn't set up on this server yet.");
      return;
    }
    set({ billingActionPending: true });
    let out;
    try {
      out = await createSubscription();
      await loadRazorpay();
    } catch (err) {
      set({ billingActionPending: false });
      surfaceError(err, "Could not start checkout");
      return;
    }
    try {
      openSubscriptionCheckout({
        keyId: out.key_id,
        subscriptionId: out.subscription_id,
        description: out.plan?.name || PAID_PLAN_FALLBACK_NAME,
        email: s.user?.email || "",
        onSuccess: async () => {
          toast.success(
            `Payment received — activating your ${out.plan?.name || PAID_PLAN_FALLBACK_NAME}…`
          );
          await get().refreshBillingUntilPaid();
          set({ billingActionPending: false });
        },
        onDismiss: () => set({ billingActionPending: false }),
      });
    } catch (err) {
      set({ billingActionPending: false });
      surfaceError(err, "Could not open checkout");
    }
  },

  // Poll status after checkout so the UI reflects the new plan without a full
  // reload. The activation webhook is usually near-instant but can lag a beat.
  refreshBillingUntilPaid: async (attempts = 6, intervalMs = 2000) => {
    for (let i = 0; i < attempts; i++) {
      try {
        const status = await getBillingStatus();
        set({ billingStatus: status });
        if (isPaidPlanKey(status.plan)) return;
      } catch {
        // transient — keep trying
      }
      await new Promise((r) => setTimeout(r, intervalMs));
    }
  },

  // Cancel the current subscription. The webhook confirms the flip to free; we
  // refetch to show the interim state. The confirm prompt lives in the caller.
  cancelPlan: async () => {
    const s = get();
    if (s.billingActionPending) return;
    set({ billingActionPending: true });
    try {
      await cancelSubscription();
      toast.success(
        `Your ${s.billingStatus?.plan_info?.name || PAID_PLAN_FALLBACK_NAME} will be cancelled.`
      );
      await get().loadBillingStatus();
    } catch (err) {
      surfaceError(err, "Could not cancel your subscription");
    } finally {
      set({ billingActionPending: false });
    }
  },

  // ============ PROJECTS (localStorage registry + GET /jobs/{id}) ============
  projects: USE_MOCKS ? MOCK_PROJECTS : [],
  projectsLoading: false,
  currentProjectId: null,
  setCurrentProject: (id) => set({ currentProjectId: id }),
  getProject: (id) => get().projects.find((p) => p.id === id),
  addProject: (project) => set((s) => ({ projects: [project, ...s.projects] })),

  // Hydrate the dashboard from GET /jobs — the backend returns ONLY the
  // caller's jobs (ownership is enforced server-side; this list is just
  // displayed). The localStorage registry supplies titles and keeps showing
  // jobs that fell out of Redis (24h TTL) as "expired" instead of silently
  // vanishing. Registry entries are stamped with the creating user's id so a
  // shared browser never shows one account's titles to another; un-stamped
  // legacy entries only surface in dev mode (clean-slate decision).
  loadProjects: async () => {
    if (USE_MOCKS) {
      set({ projects: MOCK_PROJECTS, projectsLoading: false });
      return;
    }
    set({ projectsLoading: true });
    try {
      const jobs = await listJobs();
      const live = jobs.map((job) => {
        const entry = getProjectEntry(job.job_id);
        if (entry && job.video_id && job.video_id !== entry.videoId) {
          registryUpsert({ jobId: job.job_id, videoId: job.video_id });
        }
        return jobToProject(job, entry);
      });

      const liveIds = new Set(jobs.map((j) => j.job_id));
      const userId = get().user?.id || null;
      const expired = registryList()
        .filter((e) => !liveIds.has(e.jobId))
        .filter((e) => (e.userId ? e.userId === userId : userId === "dev-user"))
        .map((entry) => ({
          id: entry.jobId,
          title: entry.title || entry.jobId.slice(0, 8),
          thumbnail: null,
          duration: "",
          createdAt: timeAgo(entry.createdAt),
          status: "expired",
          clipsCount: 0,
          language: entry.language || "te",
          videoId: entry.videoId || null,
          error: "Job expired — jobs are kept for 24 hours",
        }));

      set({ projects: [...live, ...expired], projectsLoading: false });
    } catch (err) {
      surfaceError(err, "Could not load your projects");
      set({ projectsLoading: false });
    }
  },

  removeProjectEntry: (jobId) => {
    registryRemove(jobId);
    set((s) => ({ projects: s.projects.filter((p) => p.id !== jobId) }));
  },

  // ============ JOB SUBMISSION (Upload page) ============
  // The Upload page drives live progress via useJobPolling(activeJobId).
  activeJobId: null,
  activeJob: null,
  uploadPercent: 0, // browser→server transfer (axios), not pipeline progress
  submitError: null,
  // HTTP status behind submitError. 402 = the monthly SOURCE-minute quota
  // rejected this job — the very first action a new user takes can hit it, so
  // the UI must show the server's copy + an upgrade path, not "request failed".
  submitErrorStatus: null,

  submitYouTubeUrl: async ({ url, language }) => {
    set({ submitError: null, submitErrorStatus: null, uploadPercent: 100 });
    try {
      const job = await createJobFromUrl({ url, language });
      registryUpsert({
        jobId: job.job_id,
        title: `YouTube import ${job.video_id || ""}`.trim(),
        language,
        source: "youtube",
        url,
        videoId: job.video_id || null,
        userId: get().user?.id || null,
      });
      set({ activeJobId: job.job_id, activeJob: job });
      return job;
    } catch (err) {
      const e = recordError(err, "Could not submit the YouTube URL");
      set({ submitError: e.message, submitErrorStatus: e.status });
      throw err;
    }
  },

  submitFile: async ({ file, language }) => {
    set({ submitError: null, submitErrorStatus: null, uploadPercent: 0 });
    try {
      const job = await createJobFromFile({
        file,
        language,
        onUploadProgress: (e) => {
          if (e.total) set({ uploadPercent: Math.round((e.loaded / e.total) * 100) });
        },
      });
      registryUpsert({
        jobId: job.job_id,
        title: file.name,
        language,
        source: "upload",
        videoId: job.video_id || null,
        userId: get().user?.id || null,
      });
      set({ activeJobId: job.job_id, activeJob: job, uploadPercent: 100 });
      return job;
    } catch (err) {
      const e = recordError(err, "Upload failed");
      set({ submitError: e.message, submitErrorStatus: e.status });
      throw err;
    }
  },

  // Poll callback: keep activeJob + the matching project card fresh
  applyJobUpdate: (job) => {
    set((s) => ({
      activeJob: s.activeJobId === job.job_id ? job : s.activeJob,
      projects: s.projects.map((p) =>
        p.id === job.job_id ? jobToProject(job, getProjectEntry(job.job_id)) : p
      ),
    }));
    if (job.status === "done") {
      const entry = getProjectEntry(job.job_id);
      if (entry && job.video_id && entry.videoId !== job.video_id) {
        registryUpsert({ jobId: job.job_id, videoId: job.video_id });
      }
      get().cacheJobClips(job);
    }
  },

  resetSubmission: () =>
    set({
      activeJobId: null,
      activeJob: null,
      uploadPercent: 0,
      submitError: null,
      submitErrorStatus: null,
    }),

  // ============ CLIPS (per job) ============
  clipsByJob: {}, // jobId → [uiClip]
  clipsLoading: false,
  clipsError: null,
  currentJobId: null,

  cacheJobClips: (job) => {
    if (!job.clips?.length) return;
    set((s) => ({
      clipsByJob: {
        ...s.clipsByJob,
        [job.job_id]: job.clips.map((c, i) => mapClipToUi(c, i, job.job_id)),
      },
    }));
  },

  // Load a project's clips for the gallery (and cache the job meta)
  loadProjectClips: async (jobId) => {
    if (USE_MOCKS) {
      set((s) => ({
        currentJobId: jobId,
        clipsByJob: { ...s.clipsByJob, [jobId]: getMockClips(jobId) },
      }));
      return;
    }
    set({ clipsLoading: true, clipsError: null, currentJobId: jobId });
    try {
      const job = await getJob(jobId);
      get().cacheJobClips(job);
      set((s) => ({
        clipsLoading: false,
        projects: s.projects.some((p) => p.id === jobId)
          ? s.projects.map((p) => (p.id === jobId ? jobToProject(job, getProjectEntry(jobId)) : p))
          : [jobToProject(job, getProjectEntry(jobId)), ...s.projects],
      }));
    } catch (err) {
      set({
        clipsLoading: false,
        clipsError:
          err.status === 404
            ? "This project has expired (jobs are kept for 24 hours). Re-submit the video to regenerate clips."
            : surfaceError(err, "Could not load clips"),
      });
    }
  },

  getClips: (jobId) => get().clipsByJob[jobId] || [],

  // ============ EDITOR — CURRENT CLIP ============
  currentClipId: USE_MOCKS ? "clip_001" : null,
  currentClip: null,
  setCurrentClip: (id) => set({ currentClipId: id }),

  // Resolve a clip id to its clip + job (deep links: /editor/:clipId).
  // Checks loaded jobs first, then hydrates registry jobs (videoId prefix
  // match narrows the search — clip ids are `${video_id}_c${n}`).
  resolveClip: async (clipId) => {
    const { clipsByJob } = get();
    for (const [jobId, clips] of Object.entries(clipsByJob)) {
      const hit = clips.find((c) => c.id === clipId);
      if (hit) return { clip: hit, jobId };
    }
    if (USE_MOCKS) return null;
    const entries = registryList();
    const ranked = [
      ...entries.filter((e) => e.videoId && clipId.startsWith(e.videoId)),
      ...entries.filter((e) => !e.videoId || !clipId.startsWith(e.videoId)),
    ];
    for (const entry of ranked) {
      try {
        const job = await getJob(entry.jobId);
        if (!job.clips?.length) continue;
        get().cacheJobClips(job);
        const clips = job.clips.map((c, i) => mapClipToUi(c, i, job.job_id));
        const hit = clips.find((c) => c.id === clipId);
        if (hit) return { clip: hit, jobId: job.job_id };
      } catch {
        // expired/unreachable entry — keep searching
      }
    }
    // Registry miss ≠ clip gone: the localStorage registry only knows jobs
    // CREATED in this browser. A cold deep link from another device/browser
    // (or after clearing storage) lands here with a valid clip whose job the
    // backend can list. Fall back to the caller's own job list — the same
    // backend-ownership-enforced GET /jobs the dashboard renders — before
    // declaring the clip not found.
    try {
      const jobs = await listJobs();
      for (const job of jobs) {
        if (!job.clips?.length) continue;
        const clips = job.clips.map((c, i) => mapClipToUi(c, i, job.job_id));
        const hit = clips.find((c) => c.id === clipId);
        if (hit) {
          get().cacheJobClips(job);
          // Adopt into the local registry (same pattern as pollJob) so the
          // transcript load below finds the videoId and the NEXT cold load
          // resolves through the fast registry path.
          if (job.video_id) {
            registryUpsert({ jobId: job.job_id, videoId: job.video_id });
          }
          return { clip: hit, jobId: job.job_id };
        }
      }
    } catch {
      // backend unreachable — fall through to not-found
    }
    return null;
  },

  // Open a clip in the editor: resolve it, load its Telugu word-level
  // transcript (backend word boundaries are authoritative — rendered
  // verbatim), and restore any saved draft.
  openClip: async (clipId) => {
    // History is per-clip and session-only: opening a clip always starts
    // with clean undo/redo stacks (no cross-clip bleed).
    get().resetHistory();
    // Stale-run guard: every set() after an await below must prove this run
    // still owns the editor, or a slow clip-A open landing after a fast
    // clip-B open would apply A's transcript/duration/draft onto B.
    const seq = ++_openSeq;
    const stale = () => _openSeq !== seq;
    if (USE_MOCKS) {
      set({
        currentClipId: clipId,
        transcript: WORD_TRANSCRIPT,
        duration: CLIP_DURATION,
        transcriptStatus: "ready",
        transcriptEdits: createEmptyTranscriptEdits(),
        elements: initialElements(),
        selectedElementId: "el_caption_1",
        selectedIds: ["el_caption_1"],
        exportSettings: defaultExportSettings(),
        masterDims: null,
        reframeMode: false,
        draftLoadStatus: "ready",
      });
      // Feature #30 — seed the mock clip's emoji overlays (mock mode has no
      // currentClip, so pass the suggestions directly).
      get().seedEmojiOverlays(MOCK_EMOJI_SUGGESTIONS);
      return;
    }
    set({
      currentClipId: clipId,
      currentClip: null,
      transcript: [],
      transcriptStatus: "loading",
      transcriptError: null,
      transcriptWarning: null,
      exportWarnings: [],
      // The WHOLE document is per-clip: edits, canvas elements, and export
      // settings all start clean, then the draft restore below repopulates
      // them for THIS clip (never leak the previous clip's state — elements
      // and exportSettings would otherwise autosave into the wrong draft).
      transcriptEdits: createEmptyTranscriptEdits(),
      elements: initialElements(),
      selectedElementId: "el_caption_1",
      selectedIds: ["el_caption_1"],
      exportSettings: defaultExportSettings(),
      // Per-clip session view state: the master's measured dimensions and
      // the reframe overlay never survive a clip switch.
      masterDims: null,
      reframeMode: false,
      // Draft round-trip now in flight: saveDraftNow refuses to persist and
      // the Editor keeps autosave disarmed until this settles, so the empty
      // document above can never overwrite the persisted draft.
      draftLoadStatus: "loading",
      draftStatus: "idle",
    });
    const found = await get().resolveClip(clipId);
    if (stale()) return;
    if (!found) {
      set({
        transcriptStatus: "error",
        transcriptError: "Clip not found — the job may have expired (24h retention).",
        // Not a confirmed no-draft — keep autosave off for this dead end.
        draftLoadStatus: "error",
      });
      return;
    }
    const { clip, jobId } = found;
    set({
      currentClip: clip,
      currentJobId: jobId,
      // Caption-sync fix: the cut file actually spans the refined boundaries
      // when the backend provides them (raw CTC span otherwise).
      duration:
        clip.refined_start != null && clip.refined_end != null
          ? clip.refined_end - clip.refined_start
          : clip.end - clip.start,
    });

    // Feature #5 (auto hook headline) REMOVED: the headline burned the Telugu
    // hook in the Latin-only Outfit font (tofu) and cluttered the frame. No
    // headline is seeded anymore; the type stays supported for old drafts only.

    try {
      const project = get().projects.find((p) => p.id === jobId);
      const videoId = project?.videoId || getProjectEntry(jobId)?.videoId;
      if (!videoId) throw new Error("No video id on this job — cannot load transcript");

      const transcript = await getTranscript(videoId);
      // Sidecar is legacy-defensive: nothing currently writes it; multi-segment
      // clips are plain concat, so sentence-time stacking matches the video.
      const sidecar = await getSegmentSidecar(clip.verticalPath);
      if (stale()) return;
      const words = buildClipTranscript(transcript, clip, sidecar);
      const duration = words.length ? Math.max(clip.duration || 0, words[words.length - 1].end) : clip.duration;

      // Multi-segment safety net: if a dead zone was cut out but ClipOut did
      // not carry the segment ranges, getClipWords falls back to the full
      // span — which would include the cut region and drift every word after
      // it. Detect that case and warn rather than show a silently-wrong
      // overlay. (Correct remap kicks in automatically once segments arrive.)
      let transcriptWarning = null;
      if (isMultiSegmentClip(clip) && !canRemapMultiSegment(clip)) {
        transcriptWarning =
          "This clip is stitched from multiple segments (a section was cut out). " +
          "Word-level caption timing may be misaligned in the editor until multi-segment " +
          "transcript support is enabled on the backend. Playback and export are unaffected.";
      }
      set({ transcript: words, transcriptStatus: "ready", duration, transcriptWarning });
    } catch (err) {
      if (stale()) return;
      set({
        transcriptStatus: "error",
        transcriptError: err.message,
      });
      surfaceError(err, "Could not load the transcript");
    }

    // Restore draft; the saved caption template is fetched alongside so a
    // confirmed no-draft clip can start from the user's style.
    const [tplResult, draftResult] = await Promise.allSettled([
      get().ensureCaptionTemplate(),
      loadDraft(jobId, clipId),
    ]);
    if (stale()) return;
    if (draftResult.status === "fulfilled") {
      const draft = draftResult.value;
      if (draft) {
        // A clip's OWN draft always wins over the template.
        get().applyDraft(draft);
        // A restored draft may carry Tanglish-view edits whose Telugu never
        // resolved (service was down at commit) — retry now, fire-and-forget.
        get().resolvePendingTelugu();
      } else {
        // Confirmed no-draft: a clean clip starts from the saved style...
        if (tplResult.status === "fulfilled" && tplResult.value) {
          get().applyCaptionTemplateToCleanClip(tplResult.value);
        }
        // ...and gets its Gemini-suggested emoji seeded as timed overlays
        // (feature #30). A clip's OWN draft (the `if` branch above) already
        // carries whatever emoji the user kept/moved, so this only fires on a
        // clip the user hasn't edited yet — same stance as the hook headline.
        get().seedEmojiOverlays();
      }
      set({ draftLoadStatus: "ready" });
    } else {
      // A FAILED load is not a confirmed no-draft: autosave stays disarmed
      // (Editor gates on 'ready') so it can never overwrite a draft we
      // couldn't read. Manual save still works — that's an explicit action.
      set({ draftLoadStatus: "error" });
    }
  },

  // THE single document-restore path. Drafts (sanitized, via applyDraft)
  // and undo/redo snapshots both land here, so history restore can never
  // diverge from what a draft reload produces. Null slice = leave as-is.
  applyDocumentState: ({ elements, exportSettings, transcriptEdits }) =>
    set((s) => ({
      ...(elements ? { elements } : {}),
      ...(exportSettings
        ? { exportSettings: { ...s.exportSettings, ...exportSettings } }
        : {}),
      ...(transcriptEdits ? { transcriptEdits } : {}),
    })),

  // Feature #30 — seed one TIMED emoji overlay per Gemini suggestion, each
  // timed to the caption line holding its anchor word. Idempotent: skips if any
  // emoji element already exists (a restored draft, or a second call), so a
  // clip is never double-seeded. Plain set (no history frame) — part of the
  // clip's INITIAL document (same stance the removed hook-headline seed used).
  // `override` lets
  // mock mode (no currentClip) inject suggestions directly.
  seedEmojiOverlays: (override) => {
    const s = get();
    const suggestions = override || s.currentClip?.emoji_suggestions || [];
    if (!suggestions.length || !s.transcript?.length) return;
    if (s.elements.some((el) => el.type === "emoji")) return;
    const emojiEls = buildEmojiOverlayElements(suggestions, s.transcript, {
      lineSplits: s.transcriptEdits.lineSplits,
      lineRealignments: s.transcriptEdits.lineRealignments,
    });
    if (!emojiEls.length) return;
    set((st) => ({ elements: [...st.elements, ...emojiEls] }));
  },

  // Apply a persisted draft to editor state. Extracted from openClip so the
  // draft → state mapping is unit-testable without the network round-trip.
  // Untrusted input: each slice is sanitized before the shared restore path
  // (drop retired element types; normalize old transcriptEdits shapes).
  applyDraft: (draft) => {
    if (!draft) return;
    get().applyDocumentState({
      elements: draft.elements?.length ? sanitizeDraftElements(draft.elements) : null,
      exportSettings: draft.exportSettings || null,
      transcriptEdits: draft.transcriptEdits
        ? sanitizeTranscriptEdits(draft.transcriptEdits)
        : null,
    });
  },

  // ============ UNDO / REDO ============
  // Full-document snapshots (past[]/future[]) of the same three slices
  // getEditDocument covers. Session-only: never serialized into drafts
  // (getEditDocument doesn't read it), reset per clip in openClip.
  history: { past: [], future: [] },

  // Push the CURRENT (pre-mutation) document as an undo frame. Call at the
  // top of every committed document mutation, after its validity guards —
  // a no-op action must not leave a phantom frame. Any new committed
  // action clears future[] (standard redo invalidation). `coalesceKey`
  // merges a continuous same-target burst into one frame (see _coalesce).
  pushHistory: (coalesceKey = null) => {
    const now = Date.now();
    if (coalesceKey && _coalesce.key === coalesceKey && now - _coalesce.at < COALESCE_MS) {
      _coalesce.at = now;
      return;
    }
    _coalesce = { key: coalesceKey, at: now };
    set((s) => ({
      history: {
        past: [...s.history.past, documentSnapshot(s)].slice(-HISTORY_LIMIT),
        future: [],
      },
    }));
  },

  // Gesture boundary (pointerup, arrow keyup): the next same-key mutation
  // starts a NEW frame instead of merging into the finished gesture.
  endHistoryCoalescing: () => {
    _coalesce = { key: null, at: 0 };
  },

  undo: () => {
    const s = get();
    const { past, future } = s.history;
    if (!past.length) return;
    _coalesce = { key: null, at: 0 };
    const current = documentSnapshot(s);
    set({
      history: { past: past.slice(0, -1), future: [...future, current].slice(-HISTORY_LIMIT) },
    });
    get().applyDocumentState(past[past.length - 1]);
  },

  redo: () => {
    const s = get();
    const { past, future } = s.history;
    if (!future.length) return;
    _coalesce = { key: null, at: 0 };
    const current = documentSnapshot(s);
    set({
      history: { past: [...past, current].slice(-HISTORY_LIMIT), future: future.slice(0, -1) },
    });
    get().applyDocumentState(future[future.length - 1]);
  },

  resetHistory: () => {
    _coalesce = { key: null, at: 0 };
    set({ history: { past: [], future: [] } });
  },

  retryTranscript: () => {
    const id = get().currentClipId;
    if (id) get().openClip(id);
  },

  // ============ TRANSCRIPT ============
  transcript: USE_MOCKS ? WORD_TRANSCRIPT : [],
  transcriptStatus: USE_MOCKS ? "ready" : "idle", // idle|loading|ready|error
  transcriptError: null,
  transcriptWarning: null, // multi-segment alignment caveat (see openClip)
  getActiveWordIndex: () => {
    const t = get().currentTime;
    return get().transcript.findIndex((w) => t >= w.start && t < w.end);
  },

  // ============ TRANSCRIPT EDITS ============
  // Full storage shape now, even though no UI populates it yet: wordEdits is
  // keyed by word id (ids derive from the word's global transcript ref — see
  // lib/transcriptEdits.js), mergedGroups/lineSplits are the backend's int
  // lists. The split/undo/Tanglish/word-edit passes mutate this slice; this
  // pass owns only storage, persistence, and the export path. NOTE: this is
  // the STORE shape — serializeTranscriptEdits converts to the backend wire
  // shape at the export boundary; never send it raw.
  transcriptEdits: createEmptyTranscriptEdits(),

  // THE word-text resolver. Every read of a word's display text (preview
  // canvas, transcript panel, metadata) goes through here — one source of
  // truth, so preview and export can never disagree about an edited word.
  // Components must also subscribe to transcriptEdits for reactivity.
  effectiveWord: (id) => {
    const edit = get().transcriptEdits.wordEdits[id];
    if (typeof edit?.text === "string") return edit.text;
    return get().transcript.find((w) => w.id === id)?.text ?? "";
  },

  // THE display-script resolver (Telugu ⇄ Tanglish toggle). What the caption
  // canvas and the transcript panel SHOW: in tanglish view, an edited word's
  // re-derived tanglish wins (wordEdits[id].text_tanglish, populated async on
  // commit via POST /tanglish), then the word's stored text_tanglish (stale-
  // fallback when the endpoint was unreachable at commit), then the Telugu
  // text (degrade, never blank). Any captionScript other than 'tanglish'
  // (including junk from an old draft) renders Telugu via effectiveWord.
  // Editing always operates on the TELUGU source — this resolver is display-
  // only; timestamps and edit addressing are script-independent.
  displayWord: (id) => {
    const s = get();
    if (s.exportSettings.captionScript !== "tanglish") return s.effectiveWord(id);
    const edit = s.transcriptEdits.wordEdits[id];
    if (typeof edit?.text_tanglish === "string" && edit.text_tanglish) {
      return edit.text_tanglish;
    }
    const word = s.transcript.find((w) => w.id === id);
    return word?.text_tanglish || s.effectiveWord(id);
  },

  // Line splits. A lineSplits entry stores the RAW INDEX of the word that
  // ENDS a caption line — the forced break lands AFTER transcript[rawIndex],
  // so transcript[rawIndex + 1] starts the next line (the exact contract of
  // group_words_with_splits in services/apply_transcript_edits.py). The raw
  // index space is the clip's empty-text-filtered word list, which IS the
  // store's transcript array — getWordsForRange mirrors the backend's
  // get_words_for_clip one-to-one (same window operators, same empty-text
  // drop) — so array position is the wire address; no ref mapping needed.
  //
  // Driven by the editable transcript (EditableTranscript.jsx): Enter at a
  // caret position ADDS a split (idempotent — Enter at an existing break
  // must not remove it), Backspace at a line start REMOVES one, and the
  // line-end cut badge removes on click. The old playhead-based
  // splitAtPlayhead/Split-button path is gone — splits are caret-addressed
  // now, so they no longer depend on where playback happens to be.
  addLineSplit: (rawIndex) => {
    const st = get();
    if (
      !Number.isInteger(rawIndex) ||
      rawIndex < 0 ||
      rawIndex >= st.transcript.length - 1 ||
      st.transcriptEdits.lineSplits.includes(rawIndex)
    ) {
      return; // invalid/duplicate: no change, so no history frame either
    }
    st.pushHistory();
    set((s) => ({
      transcriptEdits: {
        ...s.transcriptEdits,
        lineSplits: [...s.transcriptEdits.lineSplits, rawIndex].sort((a, b) => a - b),
      },
    }));
  },

  removeLineSplit: (rawIndex) => {
    const st = get();
    if (!st.transcriptEdits.lineSplits.includes(rawIndex)) return;
    st.pushHistory();
    set((s) => ({
      transcriptEdits: {
        ...s.transcriptEdits,
        lineSplits: s.transcriptEdits.lineSplits.filter((i) => i !== rawIndex),
      },
    }));
  },

  // Feature #6 — keyword emphasis. Same raw-index space as lineSplits.
  // transcriptEdits.emphasisIndices === null means the user never touched
  // emphasis on this clip → the clip's Gemini-tagged auto set applies.
  // The first toggle materializes an explicit array, which is then the
  // single source of truth (drafts/undo carry it via transcriptEdits).
  getEffectiveEmphasis: () => {
    const s = get();
    const materialized = s.transcriptEdits.emphasisIndices;
    if (Array.isArray(materialized)) return materialized;
    return s.currentClip?.emphasis_indices || [];
  },

  toggleEmphasis: (rawIndex) => {
    const st = get();
    if (!Number.isInteger(rawIndex) || rawIndex < 0 || rawIndex >= st.transcript.length) {
      return;
    }
    const current = st.getEffectiveEmphasis();
    const next = current.includes(rawIndex)
      ? current.filter((i) => i !== rawIndex)
      : [...current, rawIndex].sort((a, b) => a - b);
    st.pushHistory();
    set((s) => ({
      transcriptEdits: { ...s.transcriptEdits, emphasisIndices: next },
    }));
  },

  // ── Feature #13: auto-zoom punch points ──────────────────────────────
  // exportSettings.punchPoints: null = never initialized → the auto set
  // (generated from word beats) applies; an array (incl. []) is the user's
  // final say. Effective set is what the timeline shows and export burns.
  getEffectivePunchPoints: () => {
    const s = get();
    const stored = s.exportSettings.punchPoints;
    if (Array.isArray(stored)) return stored;
    return generatePunchPoints(s.transcript);
  },

  // Add a punch at time t, or remove the nearest existing one within tol.
  togglePunchPoint: (t, tol = 0.15) => {
    const st = get();
    if (typeof t !== "number" || !Number.isFinite(t)) return;
    const next = togglePunch(st.getEffectivePunchPoints(), t, tol);
    st.pushHistory();
    set((s) => ({ exportSettings: { ...s.exportSettings, punchPoints: next } }));
  },

  // Re-seed from word beats (the "Auto" button) — materializes the auto set
  // as an explicit array so it persists and is editable from there.
  autoGeneratePunchPoints: () => {
    const st = get();
    const next = generatePunchPoints(st.transcript);
    st.pushHistory();
    set((s) => ({ exportSettings: { ...s.exportSettings, punchPoints: next } }));
  },

  // Clear all punches (materialized empty array — distinct from null/auto).
  clearPunchPoints: () => {
    get().pushHistory();
    set((s) => ({ exportSettings: { ...s.exportSettings, punchPoints: [] } }));
  },

  // ── Feature #14: filler/silence removal ──────────────────────────────
  // cutSpans null = feature off. Enabling detects candidates and removes
  // them all by default; the user restores individual spans. The effective
  // set (what the burn cuts + what shows struck-through) is exportSettings.cutSpans.
  isFillerRemovalOn: () => Array.isArray(get().exportSettings.cutSpans),

  enableFillerRemoval: () => {
    const st = get();
    const spans = detectRemovableSpans(st.transcript, st.duration);
    st.pushHistory();
    set((s) => ({ exportSettings: { ...s.exportSettings, cutSpans: spans } }));
  },

  disableFillerRemoval: () => {
    get().pushHistory();
    set((s) => ({ exportSettings: { ...s.exportSettings, cutSpans: null } }));
  },

  // Restore (un-cut) a span by its start (the struck-through "restore" click).
  restoreCutSpan: (start) => {
    const st = get();
    if (!Array.isArray(st.exportSettings.cutSpans)) return;
    const next = st.exportSettings.cutSpans.filter((s) => s.start !== start);
    st.pushHistory();
    set((s) => ({ exportSettings: { ...s.exportSettings, cutSpans: next } }));
  },

  // Is a transcript word inside an active cut span? (struck-through UI)
  isWordCut: (word) => {
    const spans = get().exportSettings.cutSpans;
    return Array.isArray(spans) && wordInSpans(word, spans);
  },

  // Commit a word-text fix from the editable transcript. Text-only by
  // contract: timestamps are NEVER touched (audio/video is never cut).
  // Committing text identical to the original (or blank) clears the edit,
  // so wordEdits only ever holds real deltas. text_tanglish stays null —
  // SEAM: telugu_to_tanglish() is not wired in this repo yet; the
  // Tanglish-toggle task owns deriving it on commit (see lib/transcriptEdits.js).
  setWordEdit: (id, text) => {
    const st = get();
    const original = st.transcript.find((w) => w.id === id);
    if (!original) return;
    const clean = typeof text === "string" ? text.trim() : "";
    const existing = st.transcriptEdits.wordEdits[id];
    const clearing = !clean || clean === original.text;
    // No-op commits (unknown word, clearing a non-edit, re-committing the
    // same fix) change nothing and push nothing. The commit itself is the
    // atomic history unit — one frame per commit, never per keystroke.
    if (clearing && !existing) return;
    if (!clearing && existing?.text === clean) return;
    st.pushHistory();
    set((s) => {
      const next = { ...s.transcriptEdits.wordEdits };
      if (clearing) delete next[id];
      else next[id] = { text: clean, text_tanglish: null };
      return { transcriptEdits: { ...s.transcriptEdits, wordEdits: next } };
    });
    // Edit seam (Tanglish toggle): re-derive the committed word's Tanglish
    // async so the tanglish view never shows stale romanization. Fire-and-
    // forget, non-blocking — fetchTanglish never throws; on null (endpoint
    // down) text_tanglish stays null and displayWord falls back to the
    // word's stored text_tanglish. NOT a history frame: derived data, and
    // the guard below drops the response if the edit changed/cleared while
    // the request was in flight.
    if (!clearing) {
      fetchTanglish([clean]).then((out) => {
        if (!out) return;
        set((s) => {
          const cur = s.transcriptEdits.wordEdits[id];
          if (!cur || cur.text !== clean) return {};
          return {
            transcriptEdits: {
              ...s.transcriptEdits,
              wordEdits: {
                ...s.transcriptEdits.wordEdits,
                [id]: { ...cur, text_tanglish: out[0] },
              },
            },
          };
        });
      });
    }
  },

  // Batch word-text commits from a LINE edit or a Tanglish-view word commit.
  // ONE history frame for the whole batch — a commit is a single user
  // action, so Ctrl+Z undoes all of it at once.
  //
  // Entry shapes:
  //   {id, text}                        — Telugu-view path, byte-identical
  //       semantics to setWordEdit (trim, clear-on-original, async tanglish
  //       re-derivation via ONE batched /tanglish call).
  //   {id, text, text_tanglish}         — Tanglish-view path: Telugu resolved
  //       at commit (picked suggestion / transliterate top-1 / typed script),
  //       text_tanglish is the user's TYPED romanization verbatim — their
  //       spelling wins over re-derivation, so NO /tanglish call fires.
  //   {id, text: null, text_tanglish, pending: true} — Tanglish typed but
  //       /transliterate unreachable: the edit commits with the typed
  //       romanization and NO Telugu yet (effectiveWord falls back to the
  //       original source); resolvePendingTelugu upgrades it async/later.
  setWordEditsBatch: (edits) => {
    const st = get();
    const real = [];
    for (const { id, text, text_tanglish, pending } of edits || []) {
      const original = st.transcript.find((w) => w.id === id);
      if (!original) continue;
      const existing = st.transcriptEdits.wordEdits[id];
      if (pending) {
        const cleanTa = typeof text_tanglish === "string" ? text_tanglish.trim() : "";
        if (!cleanTa) continue;
        if (existing?.pendingTelugu && existing.text_tanglish === cleanTa) continue;
        real.push({ id, entry: { text: null, text_tanglish: cleanTa, pendingTelugu: true } });
        continue;
      }
      const clean = typeof text === "string" ? text.trim() : "";
      const cleanTa = typeof text_tanglish === "string" ? text_tanglish.trim() : "";
      if (cleanTa) {
        // Tanglish-view entry: a true no-op only when BOTH scripts match the
        // pristine word — same Telugu with a different typed spelling is a
        // real (display) delta and must persist.
        const clearing =
          !clean || (clean === original.text && cleanTa === (original.text_tanglish || ""));
        if (clearing && !existing) continue;
        if (!clearing && existing?.text === clean && existing?.text_tanglish === cleanTa) continue;
        real.push({ id, entry: clearing ? null : { text: clean, text_tanglish: cleanTa } });
        continue;
      }
      const clearing = !clean || clean === original.text;
      if (clearing && !existing) continue;
      if (!clearing && existing?.text === clean) continue;
      real.push({
        id,
        clean,
        entry: clearing ? null : { text: clean, text_tanglish: null },
        derive: !clearing,
      });
    }
    if (!real.length) return;
    st.pushHistory();
    set((s) => {
      const next = { ...s.transcriptEdits.wordEdits };
      for (const r of real) {
        if (r.entry === null) delete next[r.id];
        else next[r.id] = r.entry;
      }
      return { transcriptEdits: { ...s.transcriptEdits, wordEdits: next } };
    });
    // Async tanglish derivation for Telugu-typed entries only (a typed
    // Tanglish spelling is verbatim and never re-derived) — same guard as
    // setWordEdit: a response only lands on a word whose edit is still the
    // one we derived for.
    const toDerive = real.filter((r) => r.derive);
    if (toDerive.length) {
      fetchTanglish(toDerive.map((r) => r.clean)).then((out) => {
        if (!out || out.length !== toDerive.length) return;
        set((s) => {
          const next = { ...s.transcriptEdits.wordEdits };
          let changed = false;
          toDerive.forEach((r, i) => {
            const cur = next[r.id];
            if (cur && cur.text === r.clean && out[i]) {
              next[r.id] = { ...cur, text_tanglish: out[i] };
              changed = true;
            }
          });
          return changed
            ? { transcriptEdits: { ...s.transcriptEdits, wordEdits: next } }
            : {};
        });
      });
    }
  },

  // Resolve words committed in Tanglish view while /transliterate was
  // unreachable (wordEdits entries flagged pendingTelugu): re-ask the
  // service for each pending token's top-1 Telugu and upgrade the entry.
  // NOT a history frame — the user's commit (the typed tanglish) is already
  // in history; this only fills in derived data, same rule as the async
  // /tanglish backfills. Guarded per word: a response lands only if the
  // pending edit is still the one we resolved for. Fire-and-forget from
  // every Tanglish commit and from draft restore ("retry async or on next
  // commit"); failures simply stay pending for the next retry.
  resolvePendingTelugu: async () => {
    const pend = Object.entries(get().transcriptEdits.wordEdits).filter(
      ([, e]) => e?.pendingTelugu && e.text_tanglish
    );
    await Promise.all(
      pend.map(async ([id, e]) => {
        const list = await fetchTransliterations(e.text_tanglish);
        const top = typeof list?.[0] === "string" && list[0].trim() ? list[0].trim() : null;
        if (!top) return;
        set((s) => {
          const cur = s.transcriptEdits.wordEdits[id];
          if (!cur?.pendingTelugu || cur.text_tanglish !== e.text_tanglish) return {};
          return {
            transcriptEdits: {
              ...s.transcriptEdits,
              wordEdits: {
                ...s.transcriptEdits.wordEdits,
                [id]: { text: top, text_tanglish: cur.text_tanglish },
              },
            },
          };
        });
      })
    );
  },

  // Commit a line re-alignment (line edit with CHANGED word count): the
  // record replaces that line's words — text AND per-word timing within the
  // line's fixed span — in every caption surface. ONE history frame; the
  // whole line edit is a single undoable action.
  setLineRealignment: (key, record) => {
    if (!record || !Array.isArray(record.words) || !record.words.length) return;
    get().pushHistory();
    set((s) => ({
      transcriptEdits: {
        ...s.transcriptEdits,
        lineRealignments: { ...s.transcriptEdits.lineRealignments, [key]: record },
      },
    }));
  },

  // Revert a realigned line to its pristine transcript words (the user typed
  // the original text back). Also clears any wordEdits riding on the line's
  // original word ids so "revert" really means untouched. ONE history frame.
  clearLineRealignment: (key, wordIdsToClear = []) => {
    const st = get();
    if (!st.transcriptEdits.lineRealignments[key]) return;
    st.pushHistory();
    set((s) => {
      const nextRealign = { ...s.transcriptEdits.lineRealignments };
      delete nextRealign[key];
      const nextEdits = { ...s.transcriptEdits.wordEdits };
      for (const id of wordIdsToClear) delete nextEdits[id];
      return {
        transcriptEdits: {
          ...s.transcriptEdits,
          lineRealignments: nextRealign,
          wordEdits: nextEdits,
        },
      };
    });
  },

  // Backfill word_tanglish onto a committed realignment (client-side even-
  // distribution fallback commits with nulls, then derives async). NOT a
  // history frame — derived data, same rule as setWordEdit's async fill; the
  // guard drops the response if the record changed while in flight. Fills
  // ONLY missing slots: a word_tanglish already present is the user's TYPED
  // romanization (Tanglish-view commit) and their spelling always wins over
  // re-derivation.
  setLineRealignmentTanglish: (key, tanglishList) =>
    set((s) => {
      const rec = s.transcriptEdits.lineRealignments[key];
      if (!rec || !Array.isArray(tanglishList) || rec.words.length !== tanglishList.length) {
        return {};
      }
      return {
        transcriptEdits: {
          ...s.transcriptEdits,
          lineRealignments: {
            ...s.transcriptEdits.lineRealignments,
            [key]: {
              ...rec,
              words: rec.words.map((w, i) => ({
                ...w,
                word_tanglish: w.word_tanglish || tanglishList[i] || null,
              })),
            },
          },
        },
      };
    }),

  // ============ PLAYBACK ============
  isPlaying: false,
  togglePlay: () => set((s) => ({ isPlaying: !s.isPlaying })),
  setPlaying: (v) => set({ isPlaying: v }),
  currentTime: 0,
  duration: USE_MOCKS ? CLIP_DURATION : 0,
  // Seeks clamp into the trimmed window so the playhead always reflects what
  // the export will contain. Untrimmed clips behave exactly as before
  // (bounds collapse to [0, duration]).
  seek: (t) => {
    const { start, end } = get().getTrimBounds();
    set({ currentTime: clamp(t, start, end) });
  },
  setDuration: (d) => set({ duration: d }),

  // ============ CLIP TRIM (timeline handles) ============
  // trimStart/trimEnd live in exportSettings (draft-persisted, undoable,
  // exported — see DEFAULT_EXPORT_SETTINGS). These helpers own the sentinel
  // math so no component touches -1 directly.

  // Effective playable window in clip-seconds: trimEnd -1 → clip duration.
  getTrimBounds: () => {
    const s = get();
    const dur = s.duration || 0;
    const start = clamp(s.exportSettings.trimStart || 0, 0, dur);
    const rawEnd = s.exportSettings.trimEnd;
    const end = rawEnd > 0 ? clamp(rawEnd, start, dur) : dur;
    return { start, end };
  },

  isTrimmed: () => {
    const s = get().exportSettings;
    return (s.trimStart || 0) > 0 || s.trimEnd > 0;
  },

  // Set both bounds, clamped to the clip's own video ([0, duration] — the
  // backend trims the already-cut clip file, so extension isn't possible)
  // with a minimum kept length. Values at the clip edges store as the 0/-1
  // sentinels, keeping an untrimmed document byte-identical to before.
  // Coalesced per gesture: a handle drag is ONE undo frame
  // (endHistoryCoalescing on pointerup closes it).
  setTrimRange: (start, end) => {
    const st = get();
    const dur = st.duration;
    if (!dur) return;
    const MIN_KEEP = 0.5; // seconds — a zero-length export is never valid
    const ts = clamp(start ?? 0, 0, Math.max(0, dur - MIN_KEEP));
    const te = clamp(end == null || end < 0 ? dur : end, ts + MIN_KEEP, dur);
    const tsStored = ts <= 0.01 ? 0 : Math.round(ts * 100) / 100;
    const teStored = te >= dur - 0.01 ? -1 : Math.round(te * 100) / 100;
    if (
      st.exportSettings.trimStart === tsStored &&
      st.exportSettings.trimEnd === teStored
    ) {
      return;
    }
    st.pushHistory("trim-range");
    set((s) => ({
      exportSettings: { ...s.exportSettings, trimStart: tsStored, trimEnd: teStored },
      // Keep the playhead inside the new window immediately.
      currentTime: clamp(s.currentTime, tsStored, teStored > 0 ? teStored : dur),
    }));
  },

  resetTrim: () => {
    const s = get();
    if (!s.isTrimmed()) return;
    s.pushHistory();
    set((st) => ({ exportSettings: { ...st.exportSettings, trimStart: 0, trimEnd: -1 } }));
  },

  // ============ DRAFTS (PATCH/GET /jobs/{job}/clips/{clip}/draft) ============
  draftStatus: "idle", // idle|saving|saved|error
  // Lifecycle of the CURRENT clip's draft restore (openClip round-trip):
  // idle|loading|ready|error. 'ready' = draft applied OR confirmed no-draft;
  // 'error' = the load failed, so whether a draft exists is UNKNOWN. Autosave
  // (Editor.jsx) arms only on 'ready'; saveDraftNow refuses while 'loading'.
  // Together these guarantee an autosave can never PATCH the freshly-reset
  // empty document over a persisted draft during a slow clip open.
  draftLoadStatus: "idle",
  saveDraftNow: async () => {
    const s = get();
    if (USE_MOCKS || !s.currentClip) return;
    if (s.draftLoadStatus === "loading") return; // draft restore in flight — never clobber it
    const clipId = s.currentClipId;
    // Built synchronously with the ids above, so a clip switch mid-PATCH
    // can't mix clip A's document into clip B's draft key.
    const doc = s.getEditDocument();
    set({ draftStatus: "saving" });
    try {
      await saveDraft(s.currentJobId, clipId, doc);
      set({ draftStatus: "saved" });
      // Version history captures COMMITTED saves only — see captureDraftVersion.
      get().captureDraftVersion(clipId, doc);
    } catch (err) {
      set({ draftStatus: "error" });
      surfaceError(err, "Could not save draft");
    }
  },

  // ============ DRAFT VERSION HISTORY (session-only, per clip) ============
  // Bounded newest-first snapshots of the clip's committed draft, so the
  // user can restore "from N minutes ago". Captured ONLY from a successful
  // saveDraftNow (never from the raw autosave trigger) and never for an
  // empty/default document — a bad wipe can't become a restorable version.
  draftVersions: {}, // clipId → [{ts, doc}]

  captureDraftVersion: (clipId, doc) => {
    if (!clipId || !doc) return;
    if (isDefaultEditDocument(doc)) return;
    const snap = { id: ++_versionSeq, ts: Date.now(), doc: JSON.parse(JSON.stringify(doc)) };
    set((s) => {
      const list = s.draftVersions[clipId] || [];
      // Dedupe echo saves (same content saved again) against the newest.
      if (list[0] && deepEqual(list[0].doc, snap.doc)) return {};
      return {
        draftVersions: {
          ...s.draftVersions,
          [clipId]: [snap, ...list].slice(0, DRAFT_VERSION_LIMIT),
        },
      };
    });
  },

  // Restore a snapshot through the NORMAL draft-apply path (sanitize +
  // applyDocumentState) — and push history first, so the restore itself is
  // one undoable action.
  restoreDraftVersion: (versionId) => {
    const s = get();
    const version = (s.draftVersions[s.currentClipId] || []).find((v) => v.id === versionId);
    if (!version) return;
    s.pushHistory();
    get().applyDraft(JSON.parse(JSON.stringify(version.doc)));
  },

  // ============ EDIT DOCUMENT ============
  // The canonical outgoing payload (drafts + export). COORDINATES STAY
  // NORMALIZED 0–1 end-to-end — never pixels. The only unit conversion in
  // the entire frontend is caption_position (0–1 → percent) inside
  // buildRerenderRequest, per the backend's documented contract.
  getEditDocument: () => {
    const s = get();
    const caption = s.getCaptionElement();
    return {
      version: 1,
      clipId: s.currentClipId,
      elements: s.elements.map((el) => ({ ...el, props: { ...el.props } })),
      // cropWindows is nested state — deep-copy it so a draft payload can't
      // alias (and later mutate through) the live store slice.
      exportSettings: {
        ...s.exportSettings,
        cropWindows: JSON.parse(JSON.stringify(s.exportSettings.cropWindows || {})),
      },
      style: caption?.props?.presetId || DEFAULT_STYLE_ID,
      // Caption font (one of the three bundled Telugu fonts). Null when there is
      // no caption element so the backend falls back to its default; otherwise a
      // straight pass-through — buildRerenderRequest omits it when it's the
      // default, keeping default exports byte-identical.
      captionFont: caption?.props?.font ?? null,
      // A caption element's OWN x/y always wins — never re-defaulted here, so a
      // stored (dragged, draft-restored, or template-applied) position survives
      // a change to CAPTION_DEFAULT_POSITION untouched. The fallback only fires
      // when the document has no caption element at all.
      captionX: caption ? caption.x : CAPTION_DEFAULT_POSITION.x,
      captionY: caption ? caption.y : CAPTION_DEFAULT_POSITION.y,
      // BUG-001 partial fix: expose the caption's fontSize (0–1 canvas
      // fraction — same units the preview scales by) and pill so they can
      // reach the burn. Nulls when there is no caption element so the
      // backend can fall back to preset defaults; otherwise straight
      // pass-through of the Inspector state.
      captionFontSize: caption?.props?.fontSize ?? null,
      captionPill: caption?.props?.pill ?? null,
      // Feature #15 — caption reveal animation preset ('karaoke' default).
      captionAnimation: caption?.props?.animation ?? "karaoke",
      // Store shape (wordEdits keyed by word id) — persisted verbatim in
      // drafts; converted to the backend wire shape only at the export
      // boundary (serializeTranscriptEdits inside buildRerenderRequest).
      // Deep-copied so draft payloads can't alias live store state.
      transcriptEdits: {
        wordEdits: Object.fromEntries(
          Object.entries(s.transcriptEdits.wordEdits).map(([id, e]) => [id, { ...e }])
        ),
        mergedGroups: [...s.transcriptEdits.mergedGroups],
        lineSplits: [...s.transcriptEdits.lineSplits],
        lineRealignments: Object.fromEntries(
          // `|| {}`: a slice set directly from a pre-realignment shape (old
          // test fixtures, hot-reload) must not crash the document build.
          Object.entries(s.transcriptEdits.lineRealignments || {}).map(([k, r]) => [
            k,
            { ...r, words: r.words.map((w) => ({ ...w })) },
          ])
        ),
        // Feature #6: null (untouched → auto set) round-trips as null;
        // a materialized array copies.
        emphasisIndices: Array.isArray(s.transcriptEdits.emphasisIndices)
          ? [...s.transcriptEdits.emphasisIndices]
          : null,
      },
      // Feature #6: the EFFECTIVE emphasis set (materialized toggles, or the
      // clip's auto set) — what both the preview shows and the burn renders.
      emphasisIndices: s.getEffectiveEmphasis(),
    };
  },

  // ============ EXPORT ============
  // Shape + rationale live on DEFAULT_EXPORT_SETTINGS (top of file); openClip
  // resets to the same defaults per clip.
  exportSettings: defaultExportSettings(),
  setExportSetting: (key, val) => {
    if (get().exportSettings[key] === val) return;
    // Coalesced per key: a color-picker drag is one frame, not hundreds.
    get().pushHistory(`export-${key}`);
    set((s) => ({ exportSettings: { ...s.exportSettings, [key]: val } }));
  },

  // Feature #16 — flipping the caption script must reconcile the caption font:
  // Telugu script can only render a Telugu font and Tanglish only a Latin one
  // (the other script's glyphs tofu). If the current font is invalid for the
  // new script, snap it to that script's default (resolveCaptionFont mirrors
  // the backend). One coalesced history entry covers both changes so undo
  // restores script AND font together.
  setCaptionScript: (script) => {
    const s = get();
    if (s.exportSettings.captionScript === script) return;
    const caption = s.getCaptionElement();
    // ONE history frame for the whole toggle: snapshot the pre-flip state, then
    // apply the script flip AND the font reconcile in a single set() so a
    // single undo restores both together (a second updateElementProps would
    // push its own frame, splitting the undo). No coalesce key — a script flip
    // is a discrete click, so each toggle is its own undo step.
    get().pushHistory();
    let reconciled = caption
      ? resolveCaptionFont(caption.props.font, script)
      : null;
    // Entering Tanglish from a Telugu font falls back to the bare Latin default
    // (Montserrat). Prefer the current preset's recommended Latin font instead,
    // so a script flip lands on the same font that picking the preset would —
    // no lag until the user re-clicks the preset.
    if (caption && script === "tanglish" && reconciled === DEFAULT_LATIN_CAPTION_FONT) {
      const latin = presetLatinFont(caption.props.presetId);
      if (latin) reconciled = latin;
    }
    set((st) => ({
      exportSettings: { ...st.exportSettings, captionScript: script },
      elements:
        caption && reconciled !== caption.props.font
          ? st.elements.map((el) =>
              el.id === caption.id
                ? { ...el, props: { ...el.props, font: reconciled } }
                : el
            )
          : st.elements,
    }));
  },

  // ============ CROP WINDOW (Sprint 4 — drag-to-reframe) ============
  // The editor previews the 16:9 master through a per-aspect fractional
  // window (lib/cropWindow.js owns the math). Windows live in
  // exportSettings.cropWindows → draft-persisted, undoable, exported.
  // masterDims/reframeMode are session-only view state (never serialized).
  masterDims: null, // {w,h} of the loaded master video (loadedmetadata)
  setMasterDims: (dims) => {
    const cur = get().masterDims;
    if (cur && dims && cur.w === dims.w && cur.h === dims.h) return;
    set({ masterDims: dims });
  },
  reframeMode: false,
  setReframeMode: (v) => set({ reframeMode: !!v }),

  // Effective window for an aspect: the user's stored window, else the
  // derived default (the AI face crop for 9:16 — what _vertical.mp4 bakes).
  getEffectiveCropWindow: (aspectArg) => {
    const s = get();
    const aspect = aspectArg || s.exportSettings.format;
    const stored = (s.exportSettings.cropWindows || {})[aspect];
    if (stored) return stored;
    const box = s.currentClip?.defaultCropBox || null;
    return initialWindowForAspect(aspect, inferMasterAspect(box, s.masterDims), box);
  },

  // "Touched" = a stored window that differs from the derived default.
  // Dragging back onto the exact default counts as untouched again, so the
  // byte-identical 9:16 path re-engages.
  isCropTouched: (aspectArg) => {
    const s = get();
    const aspect = aspectArg || s.exportSettings.format;
    const stored = (s.exportSettings.cropWindows || {})[aspect];
    if (!stored) return false;
    const box = s.currentClip?.defaultCropBox || null;
    const initial = initialWindowForAspect(aspect, inferMasterAspect(box, s.masterDims), box);
    return !boxesAlmostEqual(stored, initial, 1e-3);
  },

  // Coalesced like every other drag gesture: one reframe drag = one undo
  // frame (CropReframeOverlay calls endHistoryCoalescing on pointerup).
  setCropWindow: (aspect, box) => {
    const st = get();
    const clamped = clampCropBox(box);
    const prev = (st.exportSettings.cropWindows || {})[aspect];
    if (prev && boxesAlmostEqual(prev, clamped, 1e-9)) return;
    st.pushHistory("crop-window");
    set((s) => ({
      exportSettings: {
        ...s.exportSettings,
        cropWindows: { ...(s.exportSettings.cropWindows || {}), [aspect]: clamped },
      },
    }));
  },

  // Reset framing → drop the stored window so the derived default applies
  // again (and the 9:16 export goes back to the byte-identical path).
  resetCropWindow: (aspectArg) => {
    const st = get();
    const aspect = aspectArg || st.exportSettings.format;
    if (!(st.exportSettings.cropWindows || {})[aspect]) return;
    st.pushHistory();
    set((s) => {
      const rest = { ...(s.exportSettings.cropWindows || {}) };
      delete rest[aspect];
      return { exportSettings: { ...s.exportSettings, cropWindows: rest } };
    });
  },

  exportJobId: null,
  exportStatus: "idle", // idle|submitting|rendering|done|failed
  exportJob: null,
  exportError: null,
  // HTTP status behind exportError. 402 = the render-minute budget is spent or
  // the document selected a premium preset this plan can't burn — a paywall the
  // Export page renders as an upgrade panel, not a "retry export" failure.
  exportErrorStatus: null,
  exportWarnings: [],
  exportResultPath: null, // server path of the rendered file (for /clips/download)
  exportTargetClipId: null, // which clip this export is for — so applyExportUpdate knows what to refresh

  startExport: async (clip) => {
    const s = get();
    const target = clip || s.currentClip;
    if (!target) {
      toast.error("No clip selected to export");
      return null;
    }
    set({
      exportStatus: "submitting",
      exportError: null,
      exportErrorStatus: null,
      exportResultPath: null,
      exportWarnings: [],
      exportTargetClipId: target.id,
    });
    const doc = s.getEditDocument();
    try {
      // Pixel-consistency invariant: refuse to export a document that has
      // drifted from normalized 0–1 coordinates.
      assertEditDocumentNormalized(doc);
    } catch (err) {
      set({ exportStatus: "failed", exportError: err.message, exportErrorStatus: null });
      toast.error(err.message);
      return null;
    }
    // Sprint 4 crop-window branch — THE byte-identical rule:
    //   untouched window + 9:16  → crop_mode 'auto', NO crop_box. The wire
    //   payload is exactly the pre-sprint payload, the worker reads the same
    //   pre-baked vertical_path through the same chain → identical bytes.
    //   touched window OR non-9:16 → crop_mode 'manual' + the effective
    //   window; the worker crops the 16:9 master via _prepare_source.
    const aspect = s.exportSettings.format;
    const cropActive = aspect !== "9:16" || s.isCropTouched(aspect);
    const req = buildRerenderRequest({
      style: doc.style,
      format: s.exportSettings.format,
      background: s.exportSettings.background,
      bgColor: s.exportSettings.bgColor,
      useAutocrop: s.exportSettings.useAutocrop,
      cropMode: cropActive ? "manual" : "auto",
      cropBox: cropActive ? roundCropBox(s.getEffectiveCropWindow(aspect)) : null,
      // Clip boundary trim (timeline handles). Defaults 0/-1 mean untrimmed
      // and produce the exact pre-trim-feature payload.
      trimStart: s.exportSettings.trimStart ?? 0,
      trimEnd: s.exportSettings.trimEnd ?? -1,
      captionFont: doc.captionFont,
      captionX: doc.captionX,
      captionY: doc.captionY,
      // BUG-001 partial fix — thread the caption Size + Background Pill.
      captionFontSize: doc.captionFontSize,
      captionPill: doc.captionPill,
      // Feature #15 — caption reveal animation preset.
      captionAnimation: doc.captionAnimation,
      // Telugu ⇄ Tanglish toggle — burned export renders the same script the
      // preview shows (omitted from the wire when 'telugu', the default).
      captionScript: s.exportSettings.captionScript,
      elements: doc.elements,
      // BUG-003 fix — transcript edits reach the export payload. Store shape
      // in, wire shape out: buildRerenderRequest serializes (and omits the
      // field entirely when there are no edits).
      transcriptEdits: doc.transcriptEdits,
      // Feature #6 — the effective emphasis set (user toggles win over the
      // clip's Gemini auto set) reaches the burn as its own wire field.
      // Omitted (null) when there's nothing to say — no auto set and no
      // user toggles — so pre-feature payloads stay byte-identical.
      emphasisIndices:
        doc.emphasisIndices.length > 0 ||
        Array.isArray(doc.transcriptEdits.emphasisIndices)
          ? doc.emphasisIndices
          : null,
      // Feature #13 — effective punch points → crop_keyframes at the wire
      // boundary (pulse math mirrors the backend). Omitted when there are no
      // punches so a zoom-free export stays byte-identical.
      cropKeyframes: punchesToKeyframes(s.getEffectivePunchPoints(), s.duration),
      // Feature #14 — active cut spans → [[start,end],...]. Null (feature off)
      // omits the field; the backend drops these spans + remaps captions.
      cutSpans: Array.isArray(s.exportSettings.cutSpans)
        ? spansToPairs(s.exportSettings.cutSpans)
        : null,
    });
    try {
      const rerenderJobId = await startRerender(target.jobId, target.index, req);
      set({ exportJobId: rerenderJobId, exportStatus: "rendering", exportJob: null });
      return rerenderJobId;
    } catch (err) {
      const e = recordError(err, "Could not start the export");
      set({ exportStatus: "failed", exportError: e.message, exportErrorStatus: e.status });
      return null;
    }
  },

  // Poll callback for the export rerender job
  applyExportUpdate: (job) => {
    const patch = { exportJob: job };
    if (job.status === "done") {
      patch.exportStatus = "done";
      patch.exportResultPath = job.captioned_path || job.vertical_path || null;
      patch.exportWarnings = job.warnings || [];
      if (job.warnings?.includes("transcript_edits_skipped_multi_segment")) {
        toast.warning(
          "Caption line merges/splits were skipped: this clip is stitched from multiple segments and the backend cannot apply them yet."
        );
      }
    } else if (job.status === "failed") {
      patch.exportStatus = "failed";
      patch.exportError = job.error || "Render failed";
      toast.error(`Export failed: ${job.error || "unknown error"}`);
    }

    set((s) => {
      if (job.status !== "done" || !s.exportTargetClipId) return patch;

      // Root cause of "preview shows stale video": the clip's previewUrl was
      // computed once from the ORIGINAL pipeline ClipOut and never refreshed
      // after a rerender — the export page read that same clip object, so it
      // never saw the newly rendered file. Refresh previewUrl here so every
      // consumer picks it up.
      //
      // Also cache-busted: the backend derives rerender output filenames
      // deterministically from export settings (format/background/style),
      // so re-exporting with identical settings overwrites the same path —
      // same URL, browser serves the cached old video without this.
      //
      // Editor canvas bug fix (return-from-export "double sticker"): the
      // editor's `videoUrl` MUST stay pointed at the pipeline's ORIGINAL
      // raw 9:16 crop (no overlays, no captions burned in). The rerender's
      // `job.vertical_path` is the POST-overlay pre-caption composite, so
      // refreshing `videoUrl` to it meant the editor canvas showed the
      // sticker/progress-bar/logo TWICE — once burned into the pixels of
      // the video the browser played, once as the live overlay element on
      // top. Only refresh previewUrl (used by the Export page's captioned
      // preview) + captionedPath/verticalPath (path bookkeeping for the
      // download endpoint); videoUrl stays immutable across rerenders.
      const bust = job.job_id;
      const newPreviewUrl = outputFileUrl(job.captioned_path || job.vertical_path || null);
      const bustedPreview = newPreviewUrl ? `${newPreviewUrl}?v=${bust}` : undefined;

      const refreshClip = (c) =>
        c.id === s.exportTargetClipId
          ? {
              ...c,
              captionedPath: job.captioned_path || c.captionedPath,
              verticalPath: job.vertical_path || c.verticalPath,
              previewUrl: bustedPreview || c.previewUrl,
              // videoUrl intentionally NOT refreshed — see comment above.
            }
          : c;

      return {
        ...patch,
        currentClip: s.currentClip ? refreshClip(s.currentClip) : s.currentClip,
        clipsByJob: Object.fromEntries(
          Object.entries(s.clipsByJob).map(([jobId, clips]) => [jobId, clips.map(refreshClip)])
        ),
      };
    });
  },

  failExport: (message) =>
    set({ exportStatus: "failed", exportError: message, exportErrorStatus: null }),
  resetExport: () =>
    set({
      exportJobId: null,
      exportStatus: "idle",
      exportJob: null,
      exportError: null,
      exportErrorStatus: null,
      exportWarnings: [],
      exportResultPath: null,
      exportTargetClipId: null,
    }),

  // ============ METADATA (AI title/description/hashtags) ============
  clipMetadata: null,
  metadataStatus: "idle",
  generateMetadata: async (clip) => {
    const s = get();
    const target = clip || s.currentClip;
    if (!target || USE_MOCKS) return;
    // Word text always reads through the resolver, so metadata reflects edits.
    const text = s.transcript.map((w) => s.effectiveWord(w.id)).join(" ");
    if (!text) return;
    set({ metadataStatus: "loading" });
    try {
      const meta = await generateClipMetadata(target.jobId, target.index, text);
      set({ clipMetadata: meta, metadataStatus: "ready" });
    } catch (err) {
      set({ metadataStatus: "error" });
      surfaceError(err, "Metadata generation failed");
    }
  },

  // ============ CANVAS ELEMENTS ============
  elements: initialElements(),
  selectedElementId: "el_caption_1",
  // Feature #9 — multi-select. selectedIds is the FULL selection;
  // selectedElementId stays the PRIMARY (last-clicked) member so every
  // single-selection consumer (CanvasArea's frozen nudge/delete cases,
  // TransformBox, Inspector) keeps working unchanged. Invariant:
  // selectedElementId ∈ selectedIds, or both empty.
  selectedIds: ["el_caption_1"],

  setSelected: (id) => set({ selectedElementId: id, selectedIds: id ? [id] : [] }),
  clearSelection: () => set({ selectedElementId: null, selectedIds: [] }),

  // Shift-click: add/remove from the selection. Removing the primary
  // promotes the last remaining member; removing the last clears.
  toggleInSelection: (id) =>
    set((s) => {
      if (!s.elements.some((el) => el.id === id)) return {};
      if (s.selectedIds.includes(id)) {
        const rest = s.selectedIds.filter((x) => x !== id);
        return {
          selectedIds: rest,
          selectedElementId:
            s.selectedElementId === id ? rest[rest.length - 1] ?? null : s.selectedElementId,
        };
      }
      return { selectedIds: [...s.selectedIds, id], selectedElementId: id };
    }),

  // Marquee: replace the whole selection at once. Primary = explicit, or
  // the last id in the list.
  setSelection: (ids, primaryId = null) =>
    set((s) => {
      const valid = (ids || []).filter((id) => s.elements.some((el) => el.id === id));
      return {
        selectedIds: valid,
        selectedElementId: valid.includes(primaryId)
          ? primaryId
          : valid[valid.length - 1] ?? null,
      };
    }),

  // Group move (relative): one delta applied to the given ids, clamped like
  // a drag. Coalesced so a held-arrow nudge burst is ONE history frame
  // (CanvasArea's Arrow keyup calls endHistoryCoalescing globally).
  moveElementsBy: (ids, dx, dy, coalesceKey = "group-move") => {
    if (!ids?.length) return;
    get().pushHistory(coalesceKey);
    set((st) => ({
      elements: st.elements.map((el) =>
        ids.includes(el.id) && !el.locked
          ? { ...el, x: clamp(el.x + dx, 0.02, 0.98), y: clamp(el.y + dy, 0.02, 0.98) }
          : el
      ),
    }));
  },

  // Group move (absolute): drag positions computed from drag-start snapshots
  // — no incremental clamp drift on fast pointer moves.
  moveElementsTo: (positions, coalesceKey = "group-move") => {
    if (!positions || !Object.keys(positions).length) return;
    get().pushHistory(coalesceKey);
    set((st) => ({
      elements: st.elements.map((el) =>
        positions[el.id] && !el.locked
          ? {
              ...el,
              x: clamp(positions[el.id].x, 0.02, 0.98),
              y: clamp(positions[el.id].y, 0.02, 0.98),
            }
          : el
      ),
    }));
  },

  // Group delete: every selected non-caption element in ONE history frame.
  // (EditorHotkeys calls this INSTEAD of relying on CanvasArea's single-
  // element delete composing — see removeSelectedExceptPrimary for the
  // listener-composition variant.)
  removeSelectedExceptPrimary: () => {
    const s = get();
    const doomed = s.selectedIds.filter(
      (id) => id !== s.selectedElementId &&
        s.elements.some((el) => el.id === id && el.type !== "caption")
    );
    if (!doomed.length) return;
    get().pushHistory("group-delete");
    set((st) => ({
      elements: st.elements.filter((el) => !doomed.includes(el.id)),
      selectedIds: st.selectedIds.filter((id) => !doomed.includes(id)),
    }));
  },

  // BUG-005 containment: keep `rotation` at 0 for element types whose
  // rotation the export path cannot render yet (progress). This is a UI-only
  // guard — the composite filtergraph is unchanged. When rotation is wired
  // through the composite path, drop the type from this Set.
  //
  // Applied at the store boundary (not just the TransformBox UI) so keyboard
  // nudges, mock data, and any future callers all funnel through here.

  // History note: updateElement/updateElementProps are also the per-tick
  // primitives of continuous gestures (canvas drags, Inspector sliders,
  // held-arrow nudges), so they push with a per-target coalesce key — the
  // whole gesture lands as ONE undo frame capturing the pre-gesture state.
  // Pointerup/keyup call endHistoryCoalescing so separate gestures never
  // merge. Discrete actions (add/remove/visibility/reorder) push plainly.

  updateElement: (id, patch) => {
    get().pushHistory(`element-${id}`);
    set((s) => ({
      elements: s.elements.map((el) => {
        if (el.id !== id) return el;
        const next = { ...el, ...patch };
        if (ROTATION_LOCKED_TYPES.has(next.type)) next.rotation = 0;
        return next;
      }),
    }));
  },

  updateElementProps: (id, propsPatch) => {
    get().pushHistory(`props-${id}:${Object.keys(propsPatch).sort().join(",")}`);
    set((s) => ({
      elements: s.elements.map((el) =>
        el.id === id ? { ...el, props: { ...el.props, ...propsPatch } } : el
      ),
    }));
  },

  addElement: (type, overrides = {}) => {
    get().pushHistory();
    const defaults = defaultElementForType(type);
    const el = { ...defaults, ...overrides, id: nextElementId(type) };
    set((s) => ({
      elements: [...s.elements, el],
      selectedElementId: el.id,
      selectedIds: [el.id],
    }));
    return el.id;
  },

  // Upload a user image and drop it on the canvas as an overlay element.
  // The element carries ONLY the opaque image_id (never a path/URL) — the
  // preview derives its /outputs URL from it and the burn resolves it
  // server-side, pinned to this job's video. Multiple image elements are
  // fine (each addElement mints a fresh id). One history frame via
  // addElement — the upload itself is not an undoable document change.
  addImageOverlay: async (file) => {
    const s = get();
    if (USE_MOCKS) {
      toast.error("Image overlays need a real backend (mock mode is on)");
      return null;
    }
    if (!s.currentJobId || !file) {
      toast.error("Open a clip before adding an image");
      return null;
    }
    try {
      const out = await uploadOverlayImage(s.currentJobId, file);
      return get().addElement("image", {
        props: { image_id: out.image_id, height: 0.18, opacity: 1 },
      });
    } catch (err) {
      surfaceError(err, "Could not upload the image");
      return null;
    }
  },

  removeElement: (id) => {
    if (!get().elements.some((el) => el.id === id)) return;
    get().pushHistory();
    set((s) => ({
      elements: s.elements.filter((el) => el.id !== id),
      selectedElementId:
        s.selectedElementId === id ? null : s.selectedElementId,
      selectedIds: s.selectedIds.filter((x) => x !== id),
    }));
  },

  toggleElementVisibility: (id) => {
    if (!get().elements.some((el) => el.id === id)) return;
    get().pushHistory();
    set((s) => ({
      elements: s.elements.map((el) =>
        el.id === id ? { ...el, visible: !el.visible } : el
      ),
    }));
  },

  // ── Feature #8: duplicate / copy / paste ─────────────────────────────
  // The caption element is excluded from all three — the document model is
  // single-caption by contract (deleteSelected already enforces the same).
  // The clipboard is session-only view state: never part of the document,
  // so it survives clip switches but is not drafted/undoable itself.
  elementClipboard: null,

  duplicateElement: (id) => {
    const src = get().elements.find((el) => el.id === id);
    if (!src || src.type === "caption") return null;
    get().pushHistory();
    const el = {
      ...src,
      props: { ...src.props },
      id: nextElementId(src.type),
      // Slight offset so the copy is visibly a copy, clamped like a drag.
      x: clamp(src.x + 0.03, 0.02, 0.98),
      y: clamp(src.y + 0.03, 0.02, 0.98),
    };
    set((s) => ({ elements: [...s.elements, el], selectedElementId: el.id, selectedIds: [el.id] }));
    return el.id;
  },

  copyElement: (id) => {
    const src = get().elements.find((el) => el.id === id);
    if (!src || src.type === "caption") return false;
    set({ elementClipboard: JSON.parse(JSON.stringify(src)) });
    return true;
  },

  pasteElement: () => {
    const src = get().elementClipboard;
    if (!src) return null;
    get().pushHistory();
    const el = {
      ...src,
      props: { ...src.props },
      id: nextElementId(src.type),
      x: clamp((src.x ?? 0.5) + 0.03, 0.02, 0.98),
      y: clamp((src.y ?? 0.5) + 0.03, 0.02, 0.98),
    };
    set((s) => ({ elements: [...s.elements, el], selectedElementId: el.id, selectedIds: [el.id] }));
    return el.id;
  },

  bringForward: (id) => {
    const els = get().elements;
    const idx = els.findIndex((el) => el.id === id);
    if (idx === -1 || idx === els.length - 1) return;
    get().pushHistory();
    set((s) => {
      const next = [...s.elements];
      [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
      return { elements: next };
    });
  },
  sendBackward: (id) => {
    const idx = get().elements.findIndex((el) => el.id === id);
    if (idx <= 0) return;
    get().pushHistory();
    set((s) => {
      const next = [...s.elements];
      [next[idx], next[idx - 1]] = [next[idx - 1], next[idx]];
      return { elements: next };
    });
  },

  // Element convenience getters
  getElement: (id) => get().elements.find((el) => el.id === id),
  getCaptionElement: () =>
    get().elements.find((el) => el.type === "caption"),

  // ============ CAPTION PRESET SHORTCUTS ============
  // These act on the current caption element for backwards compat.
  setCaptionPreset: (presetId) => {
    const caption = get().getCaptionElement();
    if (!caption) return;
    const pillDefaults = PILL_DEFAULTS_BY_PRESET[presetId] || {};
    const patch = {
      presetId,
      pill: { ...caption.props.pill, ...pillDefaults },
    };
    // Feature #16 — in Tanglish mode each preset carries a recommended Latin
    // display font (Anton for Punch, Bebas Neue for Reel, …). Adopt it on pick
    // so the WYSIWYG preview and burn match the preset's intended look. Telugu
    // mode leaves the user's Telugu font untouched (presets are colour-only there).
    const script = get().exportSettings.captionScript;
    if (script === "tanglish") {
      const latin = presetLatinFont(presetId);
      if (latin) patch.font = latin;
    }
    get().updateElementProps(caption.id, patch);
  },
  getPresetClass: () => {
    const caption = get().getCaptionElement();
    const id = caption?.props?.presetId;
    // Backend style ids map to preview CSS classes (caption-<id> in
    // index.css); legacy mock preset ids keep working via CAPTION_PRESETS.
    if (isKnownStyle(id)) return `caption-${id}`;
    return (
      CAPTION_PRESETS.find((p) => p.id === id)?.className ||
      "caption-clean-white"
    );
  },

  // ============ POSITION PRESETS (for the selected element) ============
  applyPositionPreset: (name) => {
    const id = get().selectedElementId;
    if (!id) return;
    let x = 0.5,
      y = 0.5;
    switch (name) {
      case "top":
        y = 0.08;
        break;
      case "center":
        y = 0.5;
        break;
      case "lower-third":
        y = 0.66;
        break;
      case "bottom-safe":
        y = 0.78; // above the bottom-20% safe-zone
        break;
      default:
        break;
    }
    get().updateElement(id, { x, y });
  },

  // ============ SAFE ZONES ============
  safeZoneMode: "off", // 'off' | 'instagram' | 'youtube'
  setSafeZoneMode: (m) => set({ safeZoneMode: m }),

  // ============ SMART GUIDES (during drag) ============
  activeGuides: { vertical: [], horizontal: [] },
  setActiveGuides: (g) => set({ activeGuides: g }),

  // ============ CANVAS ZOOM & PAN ============
  canvasZoom: 1,
  setCanvasZoom: (z) => set({ canvasZoom: clamp(z, 0.4, 3) }),
  zoomIn: () => set((s) => ({ canvasZoom: clamp(s.canvasZoom + 0.1, 0.4, 3) })),
  zoomOut: () => set((s) => ({ canvasZoom: clamp(s.canvasZoom - 0.1, 0.4, 3) })),
  // CanvasArea owns the actual viewport measurement (it holds the DOM ref);
  // bumping this counter is how the toolbar's "Fit" button asks it to
  // recompute, without coupling the store to the DOM.
  fitRequestId: 0,
  fitScreen: () => set((s) => ({ fitRequestId: s.fitRequestId + 1 })),
  canvasPan: { x: 0, y: 0 },
  setCanvasPan: (p) => set({ canvasPan: p }),

  // ============ SAVED CAPTION TEMPLATE ("My Style") ============
  // ONE named style {name, presetId, font, fontSize, pill, x, y} per user,
  // persisted server-side on the user:{id} Redis hash (same layer billing
  // uses — GET/PUT /users/me/caption-template). Auto-applied ONLY to a clip
  // with a CONFIRMED empty draft (openClip's no-draft branch): a clip's own
  // restored draft always wins. Fetched once per session, cached here.
  captionTemplate: null,
  captionTemplateLoaded: false,
  captionTemplateSaving: false,

  ensureCaptionTemplate: async () => {
    const s = get();
    if (USE_MOCKS || s.captionTemplateLoaded) return s.captionTemplate;
    try {
      const template = await getCaptionTemplate();
      set({ captionTemplate: template || null, captionTemplateLoaded: true });
      return template || null;
    } catch {
      // Not latched — retried on the next clip open. A template we couldn't
      // fetch just means the clip opens with plain defaults.
      return null;
    }
  },

  // Snapshot the current caption element as the user's saved style.
  saveMyStyle: async (name) => {
    const s = get();
    const caption = s.getCaptionElement();
    if (!caption || s.captionTemplateSaving) return;
    const template = {
      name: (typeof name === "string" && name.trim() ? name.trim() : "My style").slice(0, 60),
      presetId: caption.props.presetId,
      font: caption.props.font,
      fontSize: caption.props.fontSize,
      pill: { ...caption.props.pill },
      x: caption.x,
      y: caption.y,
    };
    const prev = { template: s.captionTemplate, loaded: s.captionTemplateLoaded };
    set({ captionTemplate: template, captionTemplateLoaded: true, captionTemplateSaving: true });
    if (USE_MOCKS) {
      set({ captionTemplateSaving: false });
      return;
    }
    try {
      await putCaptionTemplate(template);
      toast.success(`Saved "${template.name}" — new clips will start with this style`);
    } catch (err) {
      set({ captionTemplate: prev.template, captionTemplateLoaded: prev.loaded });
      surfaceError(err, "Could not save your style");
    } finally {
      set({ captionTemplateSaving: false });
    }
  },

  clearMyStyle: async () => {
    const s = get();
    if (s.captionTemplateSaving) return;
    const prev = { template: s.captionTemplate, loaded: s.captionTemplateLoaded };
    set({ captionTemplate: null, captionTemplateLoaded: true, captionTemplateSaving: true });
    if (USE_MOCKS) {
      set({ captionTemplateSaving: false });
      return;
    }
    try {
      await putCaptionTemplate(null);
    } catch (err) {
      set({ captionTemplate: prev.template, captionTemplateLoaded: prev.loaded });
      surfaceError(err, "Could not remove your saved style");
    } finally {
      set({ captionTemplateSaving: false });
    }
  },

  // Fill a CLEAN clip's caption element from the template. NOT a history
  // frame and not an "edit": it is the clip's starting state, exactly like
  // a restored draft. Field-validated so a stale template (retired preset,
  // junk coords) degrades to defaults instead of corrupting the document.
  applyCaptionTemplateToCleanClip: (template) => {
    if (!template || typeof template !== "object") return;
    const unit = (v) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1;
    set((s) => ({
      elements: s.elements.map((el) => {
        if (el.type !== "caption") return el;
        const next = { ...el, props: { ...el.props } };
        if (isKnownStyle(template.presetId)) next.props.presetId = template.presetId;
        if (isKnownCaptionFont(template.font)) next.props.font = template.font;
        if (unit(template.fontSize) && template.fontSize > 0) {
          next.props.fontSize = template.fontSize;
        }
        if (template.pill && typeof template.pill === "object") {
          next.props.pill = { ...next.props.pill, ...template.pill };
        }
        if (unit(template.x)) next.x = template.x;
        if (unit(template.y)) next.y = template.y;
        return next;
      }),
    }));
  },

  // Manual re-apply to the current clip (Inspector button) — a real user
  // action, so it IS one undoable history frame.
  applyMyStyleToCurrentClip: () => {
    const s = get();
    if (!s.captionTemplate) return;
    s.pushHistory();
    get().applyCaptionTemplateToCleanClip(s.captionTemplate);
  },
}));

// React-side entry point to the word-text resolver: components render word
// text via this hook so every read goes through the store's effectiveWord
// (edits win over the original — the single source of truth; no scattered
// wordEdits lookups) AND re-render when an edit lands (the wordEdits
// subscription below is solely for that reactivity).
export const useEffectiveWord = () => {
  useAppStore((s) => s.transcriptEdits.wordEdits);
  return useAppStore((s) => s.effectiveWord);
};

// Script-aware sibling of useEffectiveWord for DISPLAY surfaces (caption
// canvas, transcript panel rows): resolves through displayWord so the
// Telugu ⇄ Tanglish toggle flips them instantly. Subscribes to wordEdits
// (edits + async tanglish derivations) AND captionScript for reactivity.
// Editing inputs keep using useEffectiveWord — edits are Telugu-source.
export const useDisplayWord = () => {
  useAppStore((s) => s.transcriptEdits.wordEdits);
  useAppStore((s) => s.exportSettings.captionScript);
  return useAppStore((s) => s.displayWord);
};

// -----------------------------------------------------------------
// Element type factory — defaults per type
// -----------------------------------------------------------------

// BUG-005 containment: element types whose editor rotation the export path
// cannot render yet. Store-level guard: any updateElement patch on these
// types silently coerces `rotation` to 0. Progress bar has computed-but-
// unapplied rotation in services/overlay_renderer.py.
const ROTATION_LOCKED_TYPES = new Set(["progress"]);

function defaultElementForType(type) {
  const base = {
    x: 0.5,
    y: 0.5,
    scale: 1,
    rotation: 0,
    visible: true,
    locked: false,
    props: {},
  };
  switch (type) {
    case "caption":
      return {
        ...base,
        type: "caption",
        // Single source of truth for the caption anchor (api/renders.js) — the
        // burn's CAPTION_DEFAULT_{X,Y}_FRAC mirrors it. A DEFAULT, not a bound.
        x: CAPTION_DEFAULT_POSITION.x,
        y: CAPTION_DEFAULT_POSITION.y,
        props: {
          presetId: "bold-yellow",
          font: "Noto Sans Telugu", // one of the three bundled caption fonts; backend default
          fontSize: 0.055, // fraction of canvas height
          animation: "karaoke", // 'none'|'pop'|'fade'|'bounce'|'karaoke'
          pill: {
            enabled: false,
            color: "#000000",
            opacity: 0.55,
            // Feature #4: fraction of canvas height (8px at the 640px 9:16
            // stage), same unit as fontSize — see lib/pillUnits.js.
            padding: 8 / 640,
            radius: 8 / 640,
          },
        },
      };
    case "headline":
      return {
        ...base,
        type: "headline",
        y: 0.14,
        props: {
          text: "94 · VIRAL",
          font: "Outfit",
          fontSize: 0.06,
          color: "#3DBE7A",
          weight: 900,
          italic: false,
          uppercase: true,
          stroke: true,
        },
      };
    case "progress":
      return {
        ...base,
        type: "progress",
        // Top edge by default: research shows a top bar clears TikTok/Reels/
        // Shorts UI, while the bottom third holds the caption + platform buttons.
        y: 0.04,
        props: {
          color: "#E9A825",
          width: 0.94,
          height: 0.01,          // thicker → actually visible (was a 0.006 hairline)
          trackOpacity: 0.25,
          direction: "grow",     // 'grow' fills L→R; 'countdown' empties (urgency)
        },
      };
    case "logo":
      return {
        ...base,
        type: "logo",
        x: 0.16,
        y: 0.07,
        props: {
          text: "@rahul_creator",
          avatar: "R",
          font: "Manrope",
          fontSize: 0.02,
        },
      };
    case "image":
      // User-uploaded overlay image. image_id is the opaque server handle
      // (set by addImageOverlay after upload); height is a fraction of
      // canvas height (width follows the image's natural ratio — the same
      // contract _prepare_image_layer burns with); opacity maps 1:1.
      return {
        ...base,
        type: "image",
        props: {
          image_id: null,
          height: 0.18,
          opacity: 1,
        },
      };
    default:
      return base;
  }
}

export { clamp };
