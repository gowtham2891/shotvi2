import { useCallback, useEffect, useRef, useState } from "react";
import {
  getJob,
  isTerminalStatus,
  POLL_INTERVAL_MS,
  STALL_TIMEOUT_MS,
  MAX_BACKOFF_MS,
} from "@/api/uploads";

/**
 * Reusable Celery job polling hook.
 *
 * Polls GET /jobs/{jobId} every 2s until the job reaches a terminal status
 * (done/failed). Handles the failure modes the backend can't push to us:
 *  - network blips → exponential backoff, keeps polling
 *  - 404 → job expired (24h Redis TTL), stops with error
 *  - no progress change for 10 min → stall timeout, stops with error
 *
 * Returns { job, error, isPolling, restart }. `restart` re-arms polling after
 * an error (retryable jobs) or when the same jobId is resubmitted.
 */
export function useJobPolling(jobId, { enabled = true, onUpdate, onDone, onFailed } = {}) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [isPolling, setIsPolling] = useState(false);
  const [epoch, setEpoch] = useState(0); // bump to force a restart

  // Keep latest callbacks without re-arming the poll loop
  const cbRef = useRef({ onUpdate, onDone, onFailed });
  cbRef.current = { onUpdate, onDone, onFailed };

  const restart = useCallback(() => {
    setError(null);
    setJob(null);
    setEpoch((e) => e + 1);
  }, []);

  useEffect(() => {
    if (!jobId || !enabled) {
      setIsPolling(false);
      return undefined;
    }

    let cancelled = false;
    let timer = null;
    let backoff = POLL_INTERVAL_MS;
    let lastProgressKey = null;
    let lastProgressAt = Date.now();

    setIsPolling(true);
    setError(null);

    const tick = async () => {
      if (cancelled) return;
      let next;
      try {
        next = await getJob(jobId);
        backoff = POLL_INTERVAL_MS;
      } catch (err) {
        if (cancelled) return;
        if (err.status === 404) {
          setError({ type: "expired", message: "This job has expired (jobs are kept for 24 hours)." });
          setIsPolling(false);
          return;
        }
        if (!err.retryable) {
          setError({ type: "request", message: err.message });
          setIsPolling(false);
          return;
        }
        backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
        timer = setTimeout(tick, backoff);
        return;
      }

      if (cancelled) return;
      setJob(next);
      cbRef.current.onUpdate?.(next);

      if (isTerminalStatus(next.status)) {
        setIsPolling(false);
        if (next.status === "done") cbRef.current.onDone?.(next);
        else cbRef.current.onFailed?.(next);
        return;
      }

      const progressKey = `${next.status}:${next.progress}`;
      if (progressKey !== lastProgressKey) {
        lastProgressKey = progressKey;
        lastProgressAt = Date.now();
      } else if (Date.now() - lastProgressAt > STALL_TIMEOUT_MS) {
        setError({
          type: "timeout",
          message: "The job appears stalled — no progress for 10 minutes. You can retry.",
          job: next,
        });
        setIsPolling(false);
        return;
      }

      timer = setTimeout(tick, POLL_INTERVAL_MS);
    };

    tick();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, enabled, epoch]);

  return { job, error, isPolling, restart };
}
