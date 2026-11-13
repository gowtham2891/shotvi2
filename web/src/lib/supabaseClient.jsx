// Supabase browser client (PHASE 2 BUILD 1).
//
// Initialized from env only — never hardcode project values (SETUP_AUTH.md
// walks the owner through filling these). When the vars are absent the app
// FAILS GRACEFULLY into dev mode: `supabase` is null, AUTH_ENABLED is false,
// the route guard lets everyone through, and API calls carry no token —
// pairing with the backend's DEV_MODE flag so local workflows keep working
// without a Supabase project.
//
// supabase-js persists the session in localStorage and auto-refreshes the
// access token; nothing else in the app manages token lifetime.

import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL || "";
const anonKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY || "";

export const AUTH_ENABLED = Boolean(url && anonKey);

export const supabase = AUTH_ENABLED ? createClient(url, anonKey) : null;

// ── Synchronous session cache ────────────────────────────────────
// Post-login race fix: right after SIGNED_IN, supabase-js notifies listeners
// while still holding its internal auth lock — an await of getSession() from
// code triggered by that very transition (navigate → dashboard mount → jobs
// fetch) can resolve null and send the request out without a token. This
// cache is written SYNCHRONOUSLY by a subscription registered at module load,
// i.e. before any app listener can react to the transition, so by the time
// login-triggered code runs, the fresh token is already readable without
// touching the locked auth client. SIGNED_OUT clears it.
let cachedSession = null;
export const getCachedSession = () => cachedSession;

if (AUTH_ENABLED) {
  supabase.auth.onAuthStateChange((_event, session) => {
    cachedSession = session || null;
  });
  // Cold start: INITIAL_SESSION also flows through the subscription above,
  // but prime the cache here too in case a request fires before it lands.
  supabase.auth.getSession().then(({ data }) => {
    if (!cachedSession) cachedSession = data?.session || null;
  });
}

if (!AUTH_ENABLED) {
  // eslint-disable-next-line no-console
  console.warn(
    "[auth] REACT_APP_SUPABASE_URL / REACT_APP_SUPABASE_ANON_KEY are not set — " +
      "running WITHOUT authentication (dev mode). The backend must be started " +
      "with DEV_MODE=true for API calls to succeed. See SETUP_AUTH.md."
  );
}

// The identity shape the app renders (AppShell user pill, etc.).
export function mapSupabaseUser(sbUser) {
  if (!sbUser) return null;
  const email = sbUser.email || "";
  return {
    id: sbUser.id,
    email,
    name:
      sbUser.user_metadata?.full_name ||
      sbUser.user_metadata?.name ||
      (email ? email.split("@")[0] : "Creator"),
    plan: "Creator", // single paid tier arrives with the billing build
  };
}

// Dev-mode stand-in so the UI has something to render without Supabase.
export const DEV_USER = {
  id: "dev-user",
  email: "dev@localhost",
  name: "Dev Mode",
  plan: "Creator",
};
