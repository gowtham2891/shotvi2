import {
  client,
  toApiError,
  outputFileUrl,
  thumbnailFileUrl,
  downloadFileUrl,
  pathBasename,
} from "@/api/client";

// ── ClipOut → UI shape ───────────────────────────────────────────

const pad2 = (n) => String(n).padStart(2, "0");

export function formatTimecode(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0 ? `${h}:${pad2(m)}:${pad2(sec)}` : `${m}:${pad2(sec)}`;
}

// Map a backend ClipOut (api/models.py) onto the shape the gallery/editor
// components render. Keeps the raw backend paths around — the export flow
// needs them verbatim (download endpoint takes full server paths).
export function mapClipToUi(clipOut, index, jobId) {
  return {
    id: clipOut.clip_id,
    index, // clip_index for rerender/metadata endpoints
    jobId,
    hook: clipOut.hook_text || "",
    hookEn: clipOut.why || "",
    engagementType: clipOut.engagement_type || "",
    // Feature #7: the backend's virality_score is 0-10 (compute_virality_score
    // caps at 10.0); every display — gauge colour thresholds, "scored X/100"
    // copy, avg % — speaks 0-100, so scale once here.
    virality: Math.min(Math.round((clipOut.virality_score || 0) * 10), 100),
    rank: clipOut.rank,
    duration: Math.round(clipOut.duration || 0),
    start: clipOut.start,
    end: clipOut.end,
    // Sentence-id ranges for multi-segment clips (dead zone cut out of the
    // middle). Required for correct clip-local transcript remap. Empty until
    // the backend exposes segments on ClipOut — see proposed diff.
    segments: clipOut.segments || [],
    // Caption-sync fix: energy-refined cut boundaries — t=0 of the actual cut
    // file. snake_case on purpose: getClipWords/getWordsForRange mirror the
    // backend byte-for-byte and read these under their wire names.
    refined_start: clipOut.refined_start ?? null,
    refined_end: clipOut.refined_end ?? null,
    refined_segments: clipOut.refined_segments || [],
    // Feature #6: Gemini-tagged punch words (clip-local raw indices, the
    // lineSplits index space). Auto-set fallback when the user hasn't
    // materialized transcriptEdits.emphasisIndices yet.
    emphasis_indices: clipOut.emphasis_indices || [],
    // Feature #30: Gemini-suggested emoji anchored to the same word-index
    // space ([{emoji, word_index}]). openClip seeds one TIMED emoji overlay per
    // suggestion (timed to the caption line holding word_index) on a clean clip.
    emoji_suggestions: clipOut.emoji_suggestions || [],
    startAt: formatTimecode(clipOut.start),
    thumbnail: thumbnailFileUrl(clipOut.thumbnail_path),
    // Playable URLs via the static /outputs mount.
    previewUrl: outputFileUrl(clipOut.captioned_path || clipOut.vertical_path || clipOut.raw_path),
    // Sprint 4: the editor canvas plays the 16:9 MASTER (raw_path) and
    // simulates the crop window client-side (lib/cropWindow.js), so every
    // aspect can reframe without re-fetching media. Vertical fallback keeps
    // legacy records (no raw on disk) playable — their crop simulation then
    // runs over the already-cropped file, which degrades to a full-frame
    // window. Like before, this URL is set once per clip and NEVER refreshed
    // by rerenders (see applyExportUpdate's videoUrl immutability).
    videoUrl: outputFileUrl(clipOut.raw_path || clipOut.vertical_path),
    // The AI face-crop as a 0–1 window over the master — the editor's
    // default framing; null on pre-Sprint-4 jobs (centered default applies).
    defaultCropBox: clipOut.default_crop_box || null,
    // Raw server paths (needed for download + sidecar lookup)
    rawPath: clipOut.raw_path || "",
    verticalPath: clipOut.vertical_path || "",
    captionedPath: clipOut.captioned_path || "",
  };
}

export const clipDownloadUrl = (serverPath) => downloadFileUrl(serverPath);

// Authenticated download. GET /clips/download requires the Supabase bearer
// token, and a plain <a href> browser navigation cannot carry it (the click
// 401s with "Missing bearer token"). Fetch the file through the SAME authed
// axios client every API call uses, then hand the bytes to the browser as a
// one-shot object-URL save.
export async function downloadClip(serverPath) {
  try {
    const { data } = await client.get("/clips/download", {
      params: { path: serverPath },
      responseType: "blob",
    });
    const name = pathBasename(serverPath) || "shotvi-clip.mp4";
    const url = URL.createObjectURL(data);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    return name;
  } catch (err) {
    throw toApiError(err, "Could not download the clip");
  }
}

// ── Editor drafts (7-day Redis TTL server-side) ──────────────────

export async function saveDraft(jobId, clipId, draft) {
  try {
    const { data } = await client.patch(
      `/jobs/${encodeURIComponent(jobId)}/clips/${encodeURIComponent(clipId)}/draft`,
      draft
    );
    return data;
  } catch (err) {
    throw toApiError(err, "Could not save draft");
  }
}

export async function loadDraft(jobId, clipId) {
  try {
    const { data } = await client.get(
      `/jobs/${encodeURIComponent(jobId)}/clips/${encodeURIComponent(clipId)}/draft`
    );
    return data?.draft ?? null;
  } catch (err) {
    throw toApiError(err, "Could not load draft");
  }
}

// ── AI metadata (title / description / hashtags) ─────────────────

export async function generateClipMetadata(jobId, clipIndex, transcriptText) {
  try {
    const { data } = await client.post(
      `/jobs/${encodeURIComponent(jobId)}/clips/${clipIndex}/metadata`,
      { transcript_text: transcriptText }
    );
    return data; // { title, description, hashtags[] }
  } catch (err) {
    throw toApiError(err, "Metadata generation failed");
  }
}
