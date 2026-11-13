import { client, toApiError } from "@/api/client";

// ── User overlay images ──────────────────────────────────────────
// POST /jobs/{job_id}/overlay-images — owner-scoped (404 for strangers,
// like every job route). The response's image_id is the OPAQUE handle the
// image element carries in props (and in the rerender payload); it doubles
// as the /outputs basename for preview display. Server-side limits: 5 MB,
// real PNG/JPEG only (bytes are sniffed, the declared type is not trusted).

export async function uploadOverlayImage(jobId, file) {
  const form = new FormData();
  form.append("file", file);
  try {
    const { data } = await client.post(
      `/jobs/${encodeURIComponent(jobId)}/overlay-images`,
      form
    );
    return data; // { image_id, path }
  } catch (err) {
    throw toApiError(err, "Could not upload the image");
  }
}
