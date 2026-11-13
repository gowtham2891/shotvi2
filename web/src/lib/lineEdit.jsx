// Line-level caption editing — pure logic (no React, no store, no network).
//
// The Descript/LYRC model: a caption line is edited as free text; on commit
// the edit is ROUTED by what actually changed —
//   - same word count, texts changed  → ordinary wordEdits (text-only, the
//     existing per-word path; timestamps untouched)
//   - word count changed              → POST /realign-line re-derives per-word
//     karaoke timing inside the line's FIXED span
//   - text matches the pristine line  → revert (drop the realignment and any
//     word edits on the line)
// EditableTranscript.jsx drives these; everything here is unit-testable.

// Pure-Latin tokens are the only ones worth transliterating (mirrors
// api/transliterate.js — Telugu-script input skips the round-trip).
export const LATIN_TOKEN = /^[A-Za-z]+$/;

// The token at the caret: the maximal non-whitespace run containing (or
// immediately left/right of) the caret. Caret sitting between two spaces →
// empty token, so a just-typed space never fires a fetch. This is what makes
// multi-word typing suggest per-word: "rendu m|" suggests for "m", not for
// the whole input.
export function tokenAt(text, caret) {
  const t = text ?? "";
  const c = Math.max(0, Math.min(caret ?? t.length, t.length));
  let start = c;
  while (start > 0 && !/\s/.test(t[start - 1])) start--;
  let end = c;
  while (end < t.length && !/\s/.test(t[end])) end++;
  return { token: t.slice(start, end), start, end };
}

// Replace [start, end) with `replacement` — used when a suggestion is picked
// so ONLY the current token changes, never the rest of the line.
export function replaceToken(text, start, end, replacement) {
  return text.slice(0, start) + replacement + text.slice(end);
}

// Free text → word list. Whitespace-only input tokenizes to [] (the caller
// treats that as cancel — a line edit can never delete a line).
export function tokenizeLine(text) {
  return (text || "").trim().split(/\s+/).filter(Boolean);
}

const arraysEqual = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

// Route a committed line edit. Inputs:
//   newTokens      — tokenized committed text
//   effectiveTexts — what the line currently shows (wordEdits/realignment applied)
//   originalTexts  — the pristine transcript words the line covers
//   isRealigned    — the line already carries a realignment record
// Returns {type: 'cancel' | 'noop' | 'revert' | 'wordEdits' | 'realign'}.
export function diffLineEdit({ newTokens, effectiveTexts, originalTexts, isRealigned }) {
  if (!newTokens.length) return { type: "cancel" };
  if (arraysEqual(newTokens, effectiveTexts)) return { type: "noop" };
  // Typing the pristine text back reverts the line to untouched — the
  // realignment (and any word edits riding on the line) drop out so the
  // original backend timing returns.
  if (isRealigned && arraysEqual(newTokens, originalTexts)) return { type: "revert" };
  if (isRealigned) {
    // Realigned words are synthetic (no transcript ids), so wordEdits cannot
    // address them — ANY further change re-aligns, which also refreshes the
    // timing for the changed words.
    return { type: "realign" };
  }
  if (newTokens.length === effectiveTexts.length) return { type: "wordEdits" };
  return { type: "realign" };
}

// Tanglish-view commit resolution: map each typed token to the TELUGU that
// gets stored (Telugu stays the source of truth; the input merely accepts
// romanized text). Per token:
//   - Telugu script typed directly     → use as-is (tanglish: null — the
//     caller re-derives display romanization as usual)
//   - matches a known display tanglish → reuse that word's Telugu; NO
//     transliteration round-trip (an unchanged/moved word must never turn
//     into a spurious edit or altered Telugu)
//   - Latin, unknown                   → transliterate() top-1
//   - transliterate unreachable/empty  → pending: the typed tanglish is kept
//     and the Telugu resolves later (resolvePendingTelugu) — the edit is
//     NEVER lost and the editor never blocks
// Returns [{telugu: string|null, tanglish: string|null, pending: bool}],
// order-preserving. `transliterate` is injected (fetchTransliterations in
// prod) so this stays unit-testable without the network.
export async function resolveTokensToTelugu(tokens, knownTeluguByTanglish, transliterate) {
  return Promise.all(
    (tokens || []).map(async (token) => {
      if (!LATIN_TOKEN.test(token)) {
        return { telugu: token, tanglish: null, pending: false };
      }
      const known = knownTeluguByTanglish?.get?.(token);
      if (known) return { telugu: known, tanglish: token, pending: false };
      try {
        const list = await transliterate(token);
        const top =
          Array.isArray(list) && typeof list[0] === "string" && list[0].trim()
            ? list[0].trim()
            : null;
        if (top) return { telugu: top, tanglish: token, pending: false };
      } catch {
        // fall through to pending — same degrade as an empty list
      }
      return { telugu: null, tanglish: token, pending: true };
    })
  );
}

// Client-side mirror of the backend's even_distribution fallback
// (services/realign_line.py): used when /realign-line itself is unreachable,
// so the user's text is NEVER lost — the line commits with approximate
// timing and the same shape a server response would have.
export function evenlyDistribute(tokens, start, end) {
  const n = tokens.length;
  if (!n) return [];
  const span = Math.max(end - start, 0);
  const step = span / n;
  const round3 = (v) => Math.round(v * 1000) / 1000;
  return tokens.map((word, i) => ({
    word,
    start: round3(start + i * step),
    end: round3(start + (i + 1) * step),
    word_tanglish: null,
  }));
}
