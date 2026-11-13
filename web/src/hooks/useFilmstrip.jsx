import { useEffect, useState } from "react";
import { thumbnailTimestamps } from "@/lib/filmstrip";

/**
 * Feature #12 — client-side filmstrip thumbnails.
 *
 * A DEDICATED hidden <video> (never the playback element) seeks to each
 * sample timestamp and draws the frame to an offscreen canvas → data URL.
 * No backend stage, no re-render, no pipeline change: it runs on the clip
 * media the editor already streams from the unauthenticated /outputs mount,
 * exactly like ButterCut's live filmstrip.
 *
 * Results cache per (url,count) at module scope so a clip revisit or resize
 * is instant. When video/canvas isn't available (jsdom tests) or extraction
 * fails, status settles to 'error' and the caller shows a neutral strip —
 * never a fabricated image.
 */

const _cache = new Map(); // `${url}|${count}` -> string[] (data URLs) | Promise | {failed}

function extractFrames(url, timestamps) {
  return new Promise((resolve, reject) => {
    if (typeof document === "undefined") return reject(new Error("no DOM"));
    const video = document.createElement("video");
    video.muted = true;
    video.crossOrigin = "anonymous";
    video.preload = "auto";
    video.src = url;

    const canvas = document.createElement("canvas");
    const frames = [];
    let idx = 0;
    let settled = false;

    const cleanup = () => {
      video.removeAttribute("src");
      try { video.load(); } catch { /* ignore */ }
    };
    const fail = (e) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(e instanceof Error ? e : new Error("filmstrip extract failed"));
    };

    const seekNext = () => {
      if (idx >= timestamps.length) {
        settled = true;
        cleanup();
        resolve(frames);
        return;
      }
      try {
        video.currentTime = Math.min(timestamps[idx], (video.duration || Infinity) - 0.05);
      } catch (e) {
        fail(e);
      }
    };

    video.addEventListener("error", () => fail(new Error("video load error")));
    video.addEventListener("loadedmetadata", () => {
      // Small thumbnails: derive a 16:9-ish cell width from the source AR.
      const h = 44;
      const w = Math.max(24, Math.round(h * (video.videoWidth / video.videoHeight || 1.78)));
      canvas.width = w;
      canvas.height = h;
      seekNext();
    });
    video.addEventListener("seeked", () => {
      try {
        const ctx = canvas.getContext("2d");
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        frames.push(canvas.toDataURL("image/jpeg", 0.6));
      } catch (e) {
        // Tainted canvas / draw failure — bail rather than push garbage.
        return fail(e);
      }
      idx += 1;
      seekNext();
    });

    // Hard timeout so a stuck decode can't hang the strip forever.
    setTimeout(() => fail(new Error("filmstrip timeout")), 15000);
  });
}

export function useFilmstrip(url, duration, count = 14) {
  const [state, setState] = useState({ status: "idle", frames: [] });

  useEffect(() => {
    if (!url || !duration || duration <= 0) {
      setState({ status: "idle", frames: [] });
      return;
    }
    let alive = true;
    const key = `${url}|${count}`;

    const cached = _cache.get(key);
    if (Array.isArray(cached)) {
      setState({ status: "ready", frames: cached });
      return;
    }
    if (cached && cached.failed) {
      setState({ status: "error", frames: [] });
      return;
    }

    setState({ status: "loading", frames: [] });
    const promise =
      cached instanceof Promise
        ? cached
        : extractFrames(url, thumbnailTimestamps(duration, count));
    if (!(cached instanceof Promise)) _cache.set(key, promise);

    promise.then(
      (frames) => {
        _cache.set(key, frames);
        if (alive) setState({ status: "ready", frames });
      },
      () => {
        _cache.set(key, { failed: true });
        if (alive) setState({ status: "error", frames: [] });
      }
    );

    return () => {
      alive = false;
    };
  }, [url, duration, count]);

  return state;
}
