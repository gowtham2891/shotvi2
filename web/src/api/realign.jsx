import { client } from "@/api/client";

// ── Line re-alignment adapter ────────────────────────────────────
//
// THE seam between the line editor and POST /jobs/{job}/clips/{i}/realign-line
// (the pipeline's MMS CTC forced aligner run on just one line's audio span).
//
// GRACEFUL DEGRADE, by construction: never throws. Any failure — network,
// non-2xx, malformed payload, word-count mismatch — resolves to null and the
// caller falls back to client-side even distribution (lib/lineEdit.js ::
// evenlyDistribute), so the user's text is never lost and the editor never
// blocks on the aligner. A healthy backend already degrades server-side too
// (approximate=true), so null here means "couldn't even reach the API".
export async function realignLine(jobId, clipIndex, { lineStart, lineEnd, words }) {
  const clean = (words || []).map((w) => (w || "").trim()).filter(Boolean);
  if (!clean.length || !(lineEnd > lineStart)) return null;
  try {
    const { data } = await client.post(
      `/jobs/${encodeURIComponent(jobId)}/clips/${clipIndex}/realign-line`,
      { line_start: lineStart, line_end: lineEnd, words: clean }
    );
    const out = data?.words;
    if (!Array.isArray(out) || out.length !== clean.length) return null;
    const safe = out.every(
      (w) =>
        w &&
        typeof w.word === "string" &&
        typeof w.start === "number" &&
        typeof w.end === "number"
    );
    if (!safe) return null;
    return {
      words: out.map((w) => ({
        word: w.word,
        start: w.start,
        end: w.end,
        word_tanglish: typeof w.word_tanglish === "string" ? w.word_tanglish : null,
      })),
      approximate: !!data.approximate,
    };
  } catch {
    return null;
  }
}
