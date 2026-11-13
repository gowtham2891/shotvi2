import { client, toApiError } from "@/api/client";

// Job lifecycle: submit (URL or file) → poll GET /jobs/{job_id} until a
// terminal status. JobStatus enum mirrors api/models.py.
export const JOB_STATUSES = [
  "pending",
  "downloading",
  "transcribing",
  "selecting",
  "cutting",
  "cropping",
  "captioning",
  "done",
  "failed",
];

export const isTerminalStatus = (status) => status === "done" || status === "failed";

// Submit a YouTube URL. Backend dedupes by video id and may return an
// already-done job instantly (Redis/storage cache hit).
export async function createJobFromUrl({ url, language = "te", email = null }) {
  try {
    const payload = { url, language };
    if (email) payload.email = email;
    const { data } = await client.post("/jobs", payload);
    return data; // JobOut
  } catch (err) {
    throw toApiError(err, "Could not submit the YouTube URL");
  }
}

// Submit a video file as multipart form data. Browser-side upload progress
// comes from axios; pipeline progress comes from polling afterwards.
export async function createJobFromFile({ file, language = "te", onUploadProgress }) {
  const form = new FormData();
  form.append("file", file);
  form.append("language", language);
  try {
    const { data } = await client.post("/jobs/upload", form, {
      timeout: 0, // large files — no client timeout on the upload itself
      onUploadProgress,
    });
    return data; // JobOut
  } catch (err) {
    throw toApiError(err, "Upload failed");
  }
}

export async function getJob(jobId) {
  try {
    const { data } = await client.get(`/jobs/${encodeURIComponent(jobId)}`);
    return data;
  } catch (err) {
    throw toApiError(err, "Could not fetch job status");
  }
}

// Backend-enforced job list: only jobs owned by the authenticated caller
// come back (GET /jobs). The dashboard displays exactly this population.
export async function listJobs() {
  try {
    const { data } = await client.get("/jobs");
    return data; // JobOut[], newest first
  } catch (err) {
    throw toApiError(err, "Could not fetch your projects");
  }
}

export async function setJobEmail(jobId, email) {
  try {
    const { data } = await client.patch(`/jobs/${encodeURIComponent(jobId)}`, { email });
    return data;
  } catch (err) {
    throw toApiError(err, "Could not save notification email");
  }
}

// Promise-based polling for store actions (the hook variant lives in
// src/hooks/useJobPolling.js and shares these timing constants).
export const POLL_INTERVAL_MS = 2000;
export const STALL_TIMEOUT_MS = 10 * 60 * 1000; // no progress change for 10 min → timeout
export const MAX_BACKOFF_MS = 30_000;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function pollJobUntilDone(jobId, { onUpdate, signal } = {}) {
  let lastProgressKey = null;
  let lastProgressAt = Date.now();
  let backoff = POLL_INTERVAL_MS;

  for (;;) {
    if (signal?.aborted) {
      const e = new Error("Polling cancelled");
      e.name = "AbortError";
      throw e;
    }
    let job;
    try {
      job = await getJob(jobId);
      backoff = POLL_INTERVAL_MS; // reset backoff on success
    } catch (err) {
      if (err.status === 404) throw err; // job expired (24h Redis TTL) — not retryable
      if (!err.retryable) throw err;
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS); // network blip — back off and retry
      await sleep(backoff);
      continue;
    }

    onUpdate?.(job);
    if (isTerminalStatus(job.status)) return job;

    const progressKey = `${job.status}:${job.progress}`;
    if (progressKey !== lastProgressKey) {
      lastProgressKey = progressKey;
      lastProgressAt = Date.now();
    } else if (Date.now() - lastProgressAt > STALL_TIMEOUT_MS) {
      const e = new Error("Job appears stalled — no progress for 10 minutes");
      e.name = "JobStallError";
      e.job = job;
      throw e;
    }

    await sleep(POLL_INTERVAL_MS);
  }
}
