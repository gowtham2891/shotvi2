import { client } from "@/api/client";

// ── Tanglish derivation adapter ──────────────────────────────────
//
// THE seam between the word-edit commit path and the backend's deterministic
// Telugu→Tanglish engine (POST /tanglish — services/tanglish.py). Called when
// a word-fix commits new Telugu text so the Tanglish caption view never shows
// stale romanization. The OPPOSITE direction from api/transliterate.js
// (Latin→Telugu suggestions, IndicXlit stub) — do not merge the two.
//
// Backend contract: POST /tanglish {words: [..]} → {tanglish: [..]},
// order-preserving (tanglish[i] romanizes words[i]).
//
// GRACEFUL DEGRADE, by construction: NEVER throws. Dead/absent service,
// non-2xx, or a malformed/length-mismatched payload all resolve to null —
// the caller leaves text_tanglish null and the Tanglish view falls back to
// the word's stored word_tanglish. Editing must never break on this call.

export async function fetchTanglish(words) {
  const list = (Array.isArray(words) ? words : [words]).map((w) => `${w ?? ""}`);
  if (!list.length) return null;
  try {
    const { data } = await client.post("/tanglish", { words: list });
    const out = data?.tanglish;
    if (!Array.isArray(out) || out.length !== list.length) return null;
    if (!out.every((t) => typeof t === "string")) return null;
    return out;
  } catch {
    return null;
  }
}
