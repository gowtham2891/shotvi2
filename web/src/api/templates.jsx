import { client, toApiError } from "@/api/client";

// ── Saved caption template ("My Style") ──────────────────────────
// ONE named caption style per user, stored server-side on the user:{id}
// Redis hash (no TTL — same persistence layer as billing state). The store
// fetches it once per session and auto-applies it to clips that have no
// draft yet; a clip's own draft always wins.

export async function getCaptionTemplate() {
  try {
    const { data } = await client.get("/users/me/caption-template");
    return data?.template ?? null;
  } catch (err) {
    throw toApiError(err, "Could not load your saved style");
  }
}

// Pass null to delete the saved template.
export async function putCaptionTemplate(template) {
  try {
    const { data } = await client.put("/users/me/caption-template", { template });
    return data?.template ?? null;
  } catch (err) {
    throw toApiError(err, "Could not save your style");
  }
}
