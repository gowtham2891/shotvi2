// Pure route-guard decision — the logic behind components/RequireAuth.jsx,
// extracted so it can be unit-tested without a DOM renderer (repo test style
// is pure-logic suites).
//
//   "allow"    → render the protected content
//   "loading"  → session restore in flight; render nothing (no auth flash)
//   "redirect" → no session; send the visitor to /auth
export function authGate({ user, authEnabled, authLoading }) {
  if (!authEnabled) return "allow"; // dev mode: guard is a pass-through
  if (authLoading) return "loading";
  if (!user) return "redirect";
  return "allow";
}
