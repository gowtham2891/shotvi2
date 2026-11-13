// Client-side YouTube URL validation for the first-run onboarding flow.
// Mirrors api/main.py's _extract_video_id formats (watch?v=, youtu.be/<id>,
// shorts/<id>, embed/<id>, v/<id>) so a URL this accepts is one the backend
// can actually extract a video id from — an obviously-wrong paste gets an
// immediate inline error instead of a job that runs and fails at download.
export function isValidYoutubeUrl(url) {
  if (typeof url !== "string") return false;
  const trimmed = url.trim();
  if (!trimmed) return false;

  let parsed;
  try {
    parsed = new URL(trimmed);
  } catch {
    return false; // not a URL at all
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;

  const host = parsed.hostname.toLowerCase().replace(/^www\./, "");

  if (host === "youtu.be") {
    return parsed.pathname.replace(/^\//, "").length > 0;
  }

  if (host.endsWith("youtube.com")) {
    if (parsed.searchParams.get("v")) return true;
    const parts = parsed.pathname.replace(/^\//, "").split("/");
    return parts.length >= 2 && ["shorts", "embed", "v"].includes(parts[0]) && !!parts[1];
  }

  return false;
}
