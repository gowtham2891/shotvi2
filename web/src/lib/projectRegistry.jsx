// localStorage registry of submitted jobs. Since PHASE 2 BUILD 1 the
// dashboard's source of truth is GET /jobs (backend-enforced, owner-scoped);
// this registry only supplies display titles and keeps jobs that fell out of
// Redis (24h TTL) visible as "expired" cards. Entries are stamped with the
// creating user's id (`userId`) so a shared browser never shows one
// account's titles to another.

const KEY = "shotvi.projects.v1";

function read() {
  try {
    const raw = localStorage.getItem(KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

function write(list) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    // Storage full/unavailable — registry is best-effort
  }
}

export function listProjects() {
  return read().sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
}

// entry: { jobId, title, language, source: 'upload'|'youtube', createdAt, videoId? }
export function upsertProject(entry) {
  const list = read();
  const idx = list.findIndex((p) => p.jobId === entry.jobId);
  if (idx >= 0) list[idx] = { ...list[idx], ...entry };
  else list.push({ createdAt: Date.now(), ...entry });
  write(list);
  return entry;
}

export function removeProject(jobId) {
  write(read().filter((p) => p.jobId !== jobId));
}

export function getProjectEntry(jobId) {
  return read().find((p) => p.jobId === jobId) || null;
}
