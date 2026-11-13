// First-run onboarding: the "your first clips are ready" completion cue is
// shown exactly once, ever, then never again — a pure client-side flag
// (localStorage), same pattern as lib/projectRegistry.js. This gates NOTHING
// real; it's a one-time UI nicety, so losing it (private browsing, cleared
// storage) just means the cue may show again — never a dead end.

const KEY = "shotvi.onboarding.v1";

function read() {
  try {
    const raw = localStorage.getItem(KEY);
    const obj = raw ? JSON.parse(raw) : {};
    return obj && typeof obj === "object" ? obj : {};
  } catch {
    return {};
  }
}

function write(state) {
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    // Storage full/unavailable — best-effort, same as projectRegistry.
  }
}

// Scoped by user id when known, so a shared browser doesn't skip the cue for
// a second account; falls back to a single shared flag when there's no user
// yet (dev mode has no auth).
const scopeKey = (userId) => userId || "_anon";

export function hasSeenFirstClipCue(userId) {
  return !!read()[scopeKey(userId)];
}

export function markFirstClipCueSeen(userId) {
  const state = read();
  state[scopeKey(userId)] = true;
  write(state);
}
