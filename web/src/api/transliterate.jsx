import { client } from "@/api/client";

// ── Transliteration adapter ──────────────────────────────────────
//
// THE seam between the editable transcript and the phonetic-suggestion
// service. The UI calls ONLY fetchTransliterations(); everything about the
// service (endpoint, model, availability) hides behind it.
//
// Backend contract: POST /transliterate {text, lang} → {suggestions: [..]}.
// Today the route is a STUB returning [] — a later task stands up the real
// AI4Bharat IndicXlit service (Python 3.10 + pip<24.1 + torch
// weights_only=False patch, isolated from the ASR env) behind this exact
// endpoint; nothing in the frontend changes when it does.
//
// GRACEFUL DEGRADE, by construction: this function NEVER throws and never
// hangs the editor — dead/absent service, non-2xx, malformed payload, or
// non-Latin input all resolve to []. An empty suggestion list must leave
// the editable transcript fully usable (typed text still commits directly).

// Only pure-Latin tokens are worth transliterating; Telugu-keyboard input
// (or anything already non-Latin) skips the network round-trip entirely.
const LATIN_TOKEN = /^[A-Za-z]+$/;

export async function fetchTransliterations(latin, lang = "te") {
  const text = (latin || "").trim();
  if (!text || !LATIN_TOKEN.test(text)) return [];
  try {
    const { data } = await client.post("/transliterate", { text, lang });
    if (!Array.isArray(data?.suggestions)) return [];
    return data.suggestions.filter((s) => typeof s === "string" && s.trim());
  } catch {
    return [];
  }
}
