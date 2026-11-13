import { useEffect, useState } from "react";
import {
  HIRES_BUCKETS,
  computePeaks,
  normalizePeaks,
  resamplePeaks,
} from "@/lib/waveform";

/**
 * Feature #11 — decode a clip's audio to a normalized peak array via WebAudio.
 *
 * The editor's clip media is served from the unauthenticated /outputs static
 * mount, so a plain fetch (no bearer header) reaches it — same origin the
 * <video> tag already loads. decodeAudioData extracts the mp4's audio track.
 *
 * Hi-res peaks are cached per URL (module-level, survives clip switches and
 * re-mounts); the hook resamples to `buckets` for display so a viewport
 * resize never re-decodes. When WebAudio is unavailable (jsdom tests, ancient
 * browsers) or the decode fails, status settles to 'error' and the caller
 * shows a flat baseline — never fabricated bars.
 */

// url -> { peaks: number[] (hi-res, normalized) } | { failed: true } | Promise
const _cache = new Map();

async function decodeHiRes(url) {
  const AC = typeof window !== "undefined" && (window.AudioContext || window.webkitAudioContext);
  if (!AC) throw new Error("WebAudio unavailable");
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`waveform fetch ${resp.status}`);
  const bytes = await resp.arrayBuffer();
  const ctx = new AC();
  try {
    // decodeAudioData is callback-style on old Safari; wrap for both.
    const audio = await new Promise((resolve, reject) => {
      const p = ctx.decodeAudioData(bytes, resolve, reject);
      if (p && typeof p.then === "function") p.then(resolve, reject);
    });
    return normalizePeaks(computePeaks(audio.getChannelData(0), HIRES_BUCKETS));
  } finally {
    if (typeof ctx.close === "function") ctx.close();
  }
}

export function useWaveform(url, buckets = 120) {
  const [state, setState] = useState({ status: "idle", peaks: null });

  useEffect(() => {
    if (!url) {
      setState({ status: "idle", peaks: null });
      return;
    }
    let alive = true;

    const cached = _cache.get(url);
    if (cached && Array.isArray(cached.peaks)) {
      setState({ status: "ready", peaks: resamplePeaks(cached.peaks, buckets) });
      return;
    }
    if (cached && cached.failed) {
      setState({ status: "error", peaks: null });
      return;
    }

    setState({ status: "loading", peaks: null });
    const promise =
      cached instanceof Promise ? cached : decodeHiRes(url);
    if (!(cached instanceof Promise)) _cache.set(url, promise);

    promise.then(
      (peaks) => {
        _cache.set(url, { peaks });
        if (alive) setState({ status: "ready", peaks: resamplePeaks(peaks, buckets) });
      },
      () => {
        _cache.set(url, { failed: true });
        if (alive) setState({ status: "error", peaks: null });
      }
    );

    return () => {
      alive = false;
    };
  }, [url, buckets]);

  return state;
}
