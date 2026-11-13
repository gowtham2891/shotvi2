import axios from "axios";
import { supabase, AUTH_ENABLED, getCachedSession } from "@/lib/supabaseClient";

// Base URL for the Klipvi API (backend/api.py). Override via frontend .env —
// never hardcode deployment URLs here.
export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000"
).replace(/\/+$/, "");

// Offline dev path: when true, store actions serve mockData instead of
// touching the network. Default is the real API.
export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === "true";

export const client = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30_000,
});

// ── Auth wiring ──────────────────────────────────────────────────
// Every backend call carries the Supabase access token. The synchronous
// session cache (lib/supabaseClient.js) is preferred — right after SIGNED_IN,
// getSession() can race supabase-js's internal auth lock and resolve null,
// which used to send login-triggered requests out bare. getSession() remains
// the fallback for a cold cache. supabase-js owns refresh. In dev mode (no
// Supabase env) requests go out bare and the backend's DEV_MODE accepts them.
const freshestSession = async () => {
  const cached = getCachedSession();
  if (cached) return cached;
  const { data } = await supabase.auth.getSession();
  return data?.session || null;
};

// A session the auth client considers live: has a token and is not within
// 5s of its expiry (skew margin). Exported for tests.
export const isSessionUsable = (session, nowMs = Date.now()) =>
  Boolean(session?.access_token) &&
  (!session.expires_at || session.expires_at * 1000 > nowMs + 5000);

client.interceptors.request.use(async (config) => {
  if (AUTH_ENABLED) {
    const session = await freshestSession();
    if (session?.access_token) {
      config.headers.Authorization = `Bearer ${session.access_token}`;
    }
  }
  return config;
});

// 401 handling. Genuinely dead session (refresh exhausted or revoked) →
// trigger re-auth instead of leaving a silently dead app. But a 401 is NOT
// by itself proof of a dead session: a request racing a just-created session
// (post-login) may simply have been sent before the token was attached. So:
//   - a usable session exists and this request hasn't been retried
//       → retry ONCE with the fresh token attached; no logout.
//   - no session, session locally expired, or the retry ALSO came back 401
//     (token the auth client thinks is live, rejected by the backend —
//     revoked server-side) → invoke the unauthorized handler as before.
// The handler is registered by the auth store (clears state → route guard
// shows the auth screen); default is a no-op so tests/dev mode are unaffected.
let onUnauthorized = null;
export const setUnauthorizedHandler = (fn) => {
  onUnauthorized = fn;
};

client.interceptors.response.use(
  (res) => res,
  async (err) => {
    if (AUTH_ENABLED && err?.response?.status === 401) {
      const session = await freshestSession();
      if (isSessionUsable(session) && err.config && !err.config._authRetried) {
        const cfg = { ...err.config, _authRetried: true };
        cfg.headers = { ...(cfg.headers || {}), Authorization: `Bearer ${session.access_token}` };
        return client.request(cfg);
      }
      if (typeof onUnauthorized === "function") onUnauthorized();
    }
    return Promise.reject(err);
  }
);

// HTTP 402 = a quota/entitlement block, not a failure. The backend returns it
// from POST /jobs and POST /jobs/upload (monthly SOURCE-minute budget) and from
// the rerender route (RENDER-minute budget, premium-preset gate). It is the one
// error class where the server's `detail` is deliberate user-facing copy with
// the real numbers in it — so callers render `detail` verbatim next to an
// upgrade affordance instead of a generic "something went wrong".
export const QUOTA_EXCEEDED_STATUS = 402;
export const isQuotaError = (err) => err?.status === QUOTA_EXCEEDED_STATUS;

export class ApiError extends Error {
  constructor(message, { status = null, detail = null, cause = null, retryable = false } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status; // HTTP status, null for network-level failures
    this.detail = detail; // FastAPI `detail` payload when present
    this.cause = cause;
    this.retryable = retryable;
  }
}

// FastAPI's `detail` is usually a string, but an HTTPException raised with a
// dict payload (the quota routes may carry structured numbers alongside the
// copy) arrives as an object. Pull the human sentence out of either shape so
// the user never sees "[object Object]" or a generic fallback that discards
// copy the server actually wrote.
const detailMessage = (detail) => {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const msg = detail.message ?? detail.detail ?? detail.error;
    if (typeof msg === "string") return msg;
  }
  return null;
};

// Normalize axios/network/FastAPI errors into a single shape the store can
// surface to the user (toast/inline) and use to decide retryability.
export function toApiError(err, fallback = "Request failed") {
  if (err instanceof ApiError) return err;
  if (axios.isAxiosError(err)) {
    const status = err.response?.status ?? null;
    const detail = err.response?.data?.detail ?? null;
    if (err.code === "ECONNABORTED") {
      return new ApiError("Request timed out — is the backend running?", {
        status, detail, cause: err, retryable: true,
      });
    }
    if (!err.response) {
      return new ApiError(
        `Cannot reach the Klipvi server at ${API_BASE_URL}`,
        { cause: err, retryable: true }
      );
    }
    return new ApiError(detailMessage(detail) || fallback, {
      status, detail, cause: err, retryable: status >= 500,
    });
  }
  return new ApiError(err?.message || fallback, { cause: err });
}

// ── File URL helpers ─────────────────────────────────────────────
// The backend returns raw server filesystem paths (e.g.
// "storage\outputs\<file>.mp4"). Files are reachable by basename via the
// static /outputs mount and the /thumbnails route.

export const pathBasename = (p) => (p ? String(p).split(/[\\/]/).pop() : "");

export const outputFileUrl = (p) =>
  p ? `${API_BASE_URL}/outputs/${encodeURIComponent(pathBasename(p))}` : null;

export const thumbnailFileUrl = (p) =>
  p ? `${API_BASE_URL}/thumbnails/${encodeURIComponent(pathBasename(p))}` : null;

// GET /clips/download expects the full backend filesystem path, not a basename.
export const downloadFileUrl = (p) =>
  p ? `${API_BASE_URL}/clips/download?path=${encodeURIComponent(p)}` : null;
