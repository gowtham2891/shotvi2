// Transcript-edits storage contract (frontend half of BUG-003).
//
// The STORE keeps edits in a lookup-friendly shape:
//   transcriptEdits: {
//     wordEdits:    { [wordId]: { text, text_tanglish } },  // keyed by word id
//     mergedGroups: [lineIdx, ...],
//     lineSplits:   [rawIndex, ...],
//   }
// while the BACKEND (api/models.py :: TranscriptEdits, consumed by
// services/apply_transcript_edits.py) expects wordEdits as a LIST of
// ref-addressed edits: [{ ref: {type:'flat',index} | {type:'segment',
// segIndex,wordIndex}, word }]. serializeTranscriptEdits() is the single
// converter between the two — the store shape must NEVER be sent raw
// (pydantic rejects a dict for List, so even `wordEdits: {}` would 422
// every export).
//
// Word ids are DERIVED from the backend ref (wordIdFromRef/refFromWordId are
// exact inverses), so the id a UI edits under and the wire address the burn
// applies to share one source and cannot drift — clip-local array position is
// never used as an address (empty-text words are dropped client-side and
// multi-segment clips stack ranges, so clip-local index ≠ global index).
//
// `text_tanglish` is part of the stored shape (so drafts round-trip it) AND
// now crosses the wire as `word_tanglish` for any resolved edit that carries
// one — mirroring how lineRealignments ship word_tanglish end-to-end. It only
// overrides the DISPLAYED romanization for a tanglish-script burn; Telugu
// stays the stored source of truth, so a telugu burn is byte-identical whether
// the field is present or not. Absent → omitted, and the backend derives the
// romanization deterministically (preview == export either way).

export const createEmptyTranscriptEdits = () => ({
  wordEdits: {},
  mergedGroups: [],
  lineSplits: [],
  // Line-level re-alignments (line edit with CHANGED word count): keyed by
  // "startIdx:endIdx" — the raw-index range of the ORIGINAL words the line
  // covered (same index space as lineSplits). Value: {startIdx, endIdx,
  // words: [{word, start, end, word_tanglish}], approximate}. Word times are
  // clip-relative WITHIN the line's fixed span. Applied after grouping
  // (applyLineRealignments in lib/captionLines.js; backend mirror in
  // services/apply_transcript_edits.py) — inert when grouping no longer
  // yields that exact line, so a style/split change safely degrades to the
  // original words instead of mis-timing a different line.
  lineRealignments: {},
  // Feature #6 — keyword emphasis. Raw indices into the clip's filtered word
  // array (SAME index space as lineSplits). null = "never materialized":
  // preview/wire fall back to the clip's Gemini-tagged auto set
  // (clip.emphasis_indices); the first user toggle materializes an array
  // (possibly []). NOT part of the transcript_edits wire shape —
  // serializeTranscriptEdits deliberately excludes it; emphasis rides
  // RerenderRequest.emphasis_indices as its own top-level field.
  emphasisIndices: null,
});

// Store/wire key for one realigned line.
export const realignmentKey = (startIdx, endIdx) => `${startIdx}:${endIdx}`;

// ref → id. Flat (Sarvam) refs index word_timestamps on the ORIGINAL video's
// global timeline; segment (whisper) refs address segments[segIndex].words[wordIndex].
export function wordIdFromRef(ref) {
  if (!ref) return null;
  if (ref.type === "flat") return `w_flat_${ref.index}`;
  if (ref.type === "segment") return `w_seg_${ref.segIndex}_${ref.wordIndex}`;
  return null;
}

// id → ref (exact inverse of wordIdFromRef). Returns null for anything that
// is not a derived id, so a malformed key can never serialize into a bogus
// address the backend would apply to the wrong word.
export function refFromWordId(id) {
  if (typeof id !== "string") return null;
  let m = /^w_flat_(\d+)$/.exec(id);
  if (m) return { type: "flat", index: Number(m[1]) };
  m = /^w_seg_(\d+)_(\d+)$/.exec(id);
  if (m) return { type: "segment", segIndex: Number(m[1]), wordIndex: Number(m[2]) };
  return null;
}

export const hasTranscriptEdits = (edits) =>
  !!edits &&
  (Object.keys(edits.wordEdits || {}).length > 0 ||
    (edits.mergedGroups || []).length > 0 ||
    (edits.lineSplits || []).length > 0 ||
    Object.keys(edits.lineRealignments || {}).length > 0);

// One realignment record, validated field-by-field. Returns the cleaned
// record or null — a malformed draft entry must drop out, never crash a
// reader or ship a bogus timestamp to the burn.
const REALIGN_KEY = /^(\d+):(\d+)$/;
function sanitizeRealignment(key, rec) {
  const m = REALIGN_KEY.exec(key);
  if (!m || !rec || typeof rec !== "object" || !Array.isArray(rec.words) || !rec.words.length) {
    return null;
  }
  const words = [];
  for (const w of rec.words) {
    if (
      !w ||
      typeof w.word !== "string" ||
      !w.word.trim() ||
      typeof w.start !== "number" ||
      typeof w.end !== "number" ||
      !Number.isFinite(w.start) ||
      !Number.isFinite(w.end)
    ) {
      return null;
    }
    words.push({
      word: w.word,
      start: w.start,
      end: w.end,
      word_tanglish: typeof w.word_tanglish === "string" ? w.word_tanglish : null,
    });
  }
  return {
    startIdx: Number(m[1]),
    endIdx: Number(m[2]),
    words,
    approximate: !!rec.approximate,
  };
}

// Draft-load normalization: old drafts (or ones saved before a key existed)
// must rehydrate into the full three-key shape without crashing readers.
export function sanitizeTranscriptEdits(raw) {
  const clean = createEmptyTranscriptEdits();
  if (!raw || typeof raw !== "object") return clean;
  for (const [id, edit] of Object.entries(raw.wordEdits || {})) {
    if (!refFromWordId(id) || !edit || typeof edit !== "object") continue;
    clean.wordEdits[id] = {
      text: typeof edit.text === "string" ? edit.text : null,
      text_tanglish: typeof edit.text_tanglish === "string" ? edit.text_tanglish : null,
      // Tanglish-view commit whose Telugu never resolved (service down):
      // the flag must survive a draft round-trip so resolvePendingTelugu
      // can retry on the next load. Key present only when true — old
      // fixtures/drafts keep their exact two-key shape.
      ...(edit.pendingTelugu ? { pendingTelugu: true } : {}),
    };
  }
  clean.mergedGroups = (raw.mergedGroups || []).filter(Number.isInteger);
  clean.lineSplits = (raw.lineSplits || []).filter(Number.isInteger);
  for (const [key, rec] of Object.entries(raw.lineRealignments || {})) {
    const cleanRec = sanitizeRealignment(key, rec);
    if (cleanRec) clean.lineRealignments[key] = cleanRec;
  }
  // Feature #6: a saved array (even []) is the user's materialized emphasis
  // set; anything else (old drafts) stays null so the clip's auto set applies.
  clean.emphasisIndices = Array.isArray(raw.emphasisIndices)
    ? raw.emphasisIndices.filter(Number.isInteger)
    : null;
  return clean;
}

// Store shape → backend TranscriptEdits wire shape (api/models.py:39-42).
// Returns null when there is nothing to send, so callers omit the
// transcript_edits field entirely — an empty state must never reach pydantic.
// Maps internal `text` → wire `word` and `text_tanglish` → wire
// `word_tanglish` (mirroring the lineRealignments word shape). Pending edits
// (Tanglish typed but Telugu unresolved) have text === null and are excluded
// by the guard below — the burn falls back to the original word for them.
export function serializeTranscriptEdits(edits) {
  if (!hasTranscriptEdits(edits)) return null;
  const wordEdits = [];
  for (const [id, edit] of Object.entries(edits.wordEdits || {})) {
    const ref = refFromWordId(id);
    if (!ref || typeof edit?.text !== "string") continue;
    const entry = { ref, word: edit.text };
    // Carry the user's typed romanization verbatim so a tanglish burn matches
    // the preview instead of re-deriving from the Telugu. Omitted when absent.
    if (typeof edit.text_tanglish === "string" && edit.text_tanglish.trim()) {
      entry.word_tanglish = edit.text_tanglish;
    }
    wordEdits.push(entry);
  }
  const mergedGroups = (edits.mergedGroups || []).filter(Number.isInteger);
  const lineSplits = (edits.lineSplits || []).filter(Number.isInteger);
  // Line realignments: store dict → wire LIST (api/models.py TranscriptEdits.
  // lineRealignments). Re-validated on the way out so a stale/corrupt store
  // entry can never reach the burn; word shape is already the wire shape.
  const lineRealignments = [];
  for (const [key, rec] of Object.entries(edits.lineRealignments || {})) {
    const cleanRec = sanitizeRealignment(key, rec);
    if (cleanRec) lineRealignments.push(cleanRec);
  }
  if (!wordEdits.length && !mergedGroups.length && !lineSplits.length && !lineRealignments.length) {
    return null;
  }
  const wire = { wordEdits, mergedGroups, lineSplits };
  // Omitted when empty so edit payloads without realignments stay
  // byte-identical to before this feature existed.
  if (lineRealignments.length) wire.lineRealignments = lineRealignments;
  return wire;
}
