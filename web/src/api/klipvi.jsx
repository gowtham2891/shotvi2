// The Klipvi API (backend/api.py). Every call carries the Supabase sign-in token (api/client.jsx adds it).
import { API_BASE_URL, client, toApiError } from "@/api/client";

const call = async (request, fallback) => {
  try {
    return (await request()).data;
  } catch (err) {
    throw toApiError(err, fallback);
  }
};

export const getMe = () => call(() => client.get("/me"), "Couldn't load your account.");

export const listEpisodes = (workspaceId) =>
  call(() => client.get(`/workspaces/${workspaceId}/episodes`), "Couldn't load your episodes.");

export const submitEpisode = (workspaceId, sourceUrl) =>
  call(
    () => client.post(`/workspaces/${workspaceId}/episodes`, { source_url: sourceUrl }, { timeout: 60_000 }),
    "Couldn't add that episode.",
  );

export const getEpisode = (episodeId) => call(() => client.get(`/episodes/${episodeId}`), "Couldn't load this episode.");

/** Addresses for one rendered video, valid for an hour: { url, poster } (poster = a still frame, may be null). */
export const renderLinks = async (renderId, download = false) => {
  const data = await call(() => client.get(`/renders/${renderId}/link`, { params: { download } }), "Couldn't open this video.");
  return { url: `${API_BASE_URL}${data.url}`, poster: data.poster ? `${API_BASE_URL}${data.poster}` : null };
};

export const listCaptionTemplates = (workspaceId) =>
  call(() => client.get("/caption-templates", { params: workspaceId ? { workspace_id: workspaceId } : {} }), "Couldn't load caption styles.");

/** Ask for a captioned version of a clip: { style: "crop" | "fit", template_id?, settings? } -> render (maybe "waiting"). */
export const requestRender = (clipId, body) =>
  call(() => client.post(`/clips/${clipId}/renders`, body), "Couldn't add captions.");

export const getRender = (renderId) => call(() => client.get(`/renders/${renderId}`), "Couldn't check the video.");

/** The caption editor's data for one clip: { clip, words (clip clock), draft, videos: {crop, fit}, defaults }. */
export const getClipEditor = (clipId) => call(() => client.get(`/clips/${clipId}/editor`), "Couldn't open this clip.");

/** Keep the editor's state for this clip: { style, template_id, settings, words?, breaks? }. */
export const saveDraft = (clipId, draft) => call(() => client.put(`/clips/${clipId}/draft`, draft), "Couldn't save your changes.");

/** Make the downloadable video for exactly what is on screen (captions burned in), then start the download.
 *  `body` = { style, template_id?, settings?, words?, breaks? } (no settings = the plain video). */
export async function downloadClip(clipId, body, plainRenderId, onStatus = () => {}) {
  let renderId = plainRenderId;
  if (body.settings || body.template_id) {
    onStatus("Preparing your video…");
    let r = await requestRender(clipId, body);
    const t0 = Date.now();
    while (r.status === "waiting" || r.status === "making") {
      if (Date.now() - t0 > 10 * 60_000) throw new Error("This is taking longer than usual. Try again in a few minutes.");
      await new Promise((ok) => setTimeout(ok, 2000));
      r = await getRender(r.id);
    }
    if (r.status !== "ready") throw new Error("Couldn't make this video. Try again, or pick another caption style.");
    renderId = r.id;
  }
  onStatus("Downloading…");
  window.location.assign((await renderLinks(renderId, true)).url);
}

/** Move where a clip starts and ends (episode seconds); its videos are made again. */
/** The parts of the episode the clip plays ([{start, end}] episode seconds): edges and cuts. */
export const setTiming = (clipId, parts) =>
  call(() => client.put(`/clips/${clipId}/timing`, { pieces: parts.map((p) => [p.start, p.end]) }), "Couldn't change the clip's timing.");

/** One ZIP of these ready videos of an episode: starts the download. */
export async function downloadZip(episodeId, renderIds) {
  const data = await call(() => client.post(`/episodes/${episodeId}/zip-link`, { render_ids: renderIds }), "Couldn't prepare the download.");
  window.location.assign(`${API_BASE_URL}${data.url}`);
}

/** The ready version of a clip for `body` ({ style, template_id?, settings?, words?, breaks? }): makes it if needed. */
export async function readyRender(clipId, body, plainRenderId) {
  if (!body.settings && !body.template_id) return plainRenderId;
  let r = await requestRender(clipId, body);
  const t0 = Date.now();
  while (r.status === "waiting" || r.status === "making") {
    if (Date.now() - t0 > 15 * 60_000) throw new Error("This is taking longer than usual. Try again in a few minutes.");
    await new Promise((ok) => setTimeout(ok, 2000));
    r = await getRender(r.id);
  }
  if (r.status !== "ready") throw new Error("Couldn't make one of the videos. Try again, or pick another caption style.");
  return r.id;
}
