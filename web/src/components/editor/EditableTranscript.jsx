import React, { useMemo, useRef, useState } from "react";
import { Scissors, Pencil, Loader2 } from "lucide-react";
import { useAppStore, useEffectiveWord, useDisplayWord } from "@/store/useAppStore";
import { buildCaptionLines, applyLineRealignments, findActiveLine } from "@/lib/captionLines";
import { enterSplitIndex, backspaceMergeIndex } from "@/lib/editableTranscript";
import {
  LATIN_TOKEN,
  tokenAt,
  replaceToken,
  tokenizeLine,
  diffLineEdit,
  evenlyDistribute,
  resolveTokensToTelugu,
} from "@/lib/lineEdit";
import { realignmentKey } from "@/lib/transcriptEdits";
import { fetchTransliterations } from "@/api/transliterate";
import { fetchTanglish } from "@/api/tanglish";
import { realignLine } from "@/api/realign";
import { getCaptionStylePreview } from "@/data/captionStylePreview";
import { EDITOR } from "@/constants/testIds";

/**
 * EditableTranscript — the CapCut-style editable text surface that replaced
 * the playhead+Split-button interaction.
 *
 * Sentences shown ARE the caption lines: buildCaptionLines with the live
 * lineSplits — the same grouping the preview canvas renders and the backend
 * burns (group_words_with_splits) — then applyLineRealignments overlays
 * line-level re-timings. One source of truth; the rows here, the preview
 * breaks, and the export can never disagree.
 *
 * TWO edit granularities:
 *   Word (unchanged) — click a word input and type. Text-only wordEdits;
 *            timestamps never touched. Good for single-word quick fixes.
 *   Line (new) — the pencil button on a row (hover-revealed; always visible
 *            on re-timed lines) swaps the row for ONE free-text input. On
 *            commit the edit routes by what changed (lib/lineEdit.js):
 *            same word count → ordinary wordEdits; count changed →
 *            POST /realign-line re-derives per-word karaoke timing inside
 *            the line's FIXED span (Descript model); pristine text typed
 *            back → the line reverts to untouched. The pencil was chosen
 *            over double-click because every word is already an input —
 *            double-click lands in the word editor and can't be repurposed
 *            without breaking the quick-fix path; a visible affordance is
 *            also more discoverable than a hidden gesture.
 *
 * Keyboard model (per word input):
 *   Enter  — caret at word start: this word starts a new caption line;
 *            caret anywhere else: the NEXT word starts a new line.
 *            (When an edit is pending, Enter commits it instead — see below.)
 *   Backspace at the very start of a line — removes the forced split above
 *            (merges back). Natural wordsPerLine breaks are not edits and
 *            are not removable.
 *   Typing — fixes the word's text (wordEdits, text-only; timestamps are
 *            never touched). Suggestions key off the CURRENT TOKEN at the
 *            caret (lib/lineEdit.js tokenAt) — never the whole input and
 *            never empty text — so multi-word typing suggests per word.
 *            Picking a candidate commits a single-token draft (or replaces
 *            just that token in a multi-token one). Blur commits, Escape
 *            reverts. When a typed edit is pending, Enter commits the
 *            highlighted candidate/typed text — split only happens on a
 *            clean (unedited) word, so one key can't do two things at once.
 */
export const EditableTranscript = () => {
  const transcript = useAppStore((s) => s.transcript);
  const lineSplits = useAppStore((s) => s.transcriptEdits.lineSplits);
  const lineRealignments = useAppStore((s) => s.transcriptEdits.lineRealignments);
  const currentTime = useAppStore((s) => s.currentTime);
  // Same wordsPerLine the caption preview uses (2 for big-bold, else 4) so
  // the rows here mirror the on-canvas lines exactly.
  const presetId = useAppStore(
    (s) => s.elements.find((el) => el.type === "caption")?.props?.presetId
  );
  const wordsPerLine = getCaptionStylePreview(presetId)?.wordsPerLine || 4;

  // startIdx of the line being edited as free text, or null.
  const [editingLine, setEditingLine] = useState(null);

  const lines = useMemo(() => {
    const built = buildCaptionLines(transcript, wordsPerLine, lineSplits);
    // Annotate each word with its raw index (flat position in the clip's
    // word list — the lineSplits/backend address space) BEFORE the
    // realignment overlay: realigned lines swap in synthetic words, but
    // their startIdx/endIdx keep addressing the original words they cover.
    let raw = 0;
    const annotated = built.map((l) => ({
      ...l,
      words: l.words.map((w) => ({ ...w, rawIdx: raw++ })),
    }));
    return applyLineRealignments(annotated, lineRealignments);
  }, [transcript, wordsPerLine, lineSplits, lineRealignments]);

  const activeLine = findActiveLine(lines, currentTime);

  if (!transcript.length) return null;

  return (
    <div className="space-y-1">
      {lines.map((line, li) => {
        const endsWithForcedSplit = lineSplits.includes(line.endIdx);
        if (editingLine === line.startIdx) {
          return (
            <LineEditor
              key={`edit-${line.startIdx}`}
              line={line}
              onDone={() => setEditingLine(null)}
            />
          );
        }
        return (
          <div
            key={line.words[0]?.id ?? `line-${line.startIdx ?? li}`}
            className={`group flex flex-wrap items-center gap-1 rounded-md px-1.5 py-1 border transition-colors ${
              line === activeLine
                ? "border-k-accent/40 bg-k-accent/5"
                : "border-transparent hover:border-k-raised"
            }`}
          >
            {line.realigned
              ? line.words.map((w, wi) => (
                  <RealignedWordToken
                    key={`rw-${line.startIdx}-${wi}`}
                    word={w}
                    startIdx={line.startIdx}
                    index={wi}
                  />
                ))
              : line.words.map((w, wi) => (
                  <WordToken
                    key={w.id ?? w.rawIdx}
                    word={w}
                    wordCount={transcript.length}
                    isLineStart={wi === 0}
                    lineSplits={lineSplits}
                  />
                ))}
            {/* "timing approximate" flag: the re-alignment fell back to even
                distribution (aligner unreachable/implausible) — the user's
                text is intact, only the karaoke pacing is an estimate. */}
            {line.realigned && line.approximate && (
              <span
                data-testid={EDITOR.lineApproxBadge(line.startIdx)}
                title="Word timing is approximate — the aligner could not time this line exactly"
                className="px-1.5 py-0.5 rounded bg-amber-500/10 border border-amber-500/40 text-amber-400 font-mono text-[9px]"
              >
                ~timing
              </span>
            )}
            {/* Line edit affordance — hover-revealed; stays visible on
                re-timed lines (their words are no longer per-word editable,
                so this is their only edit path). */}
            <button
              data-testid={EDITOR.editLineBtn(line.startIdx)}
              onClick={() => setEditingLine(line.startIdx)}
              aria-label="Edit line"
              title="Edit this caption line as text (words can be added or removed)"
              className={`inline-flex items-center px-1.5 py-0.5 rounded border transition-all ${
                line.realigned
                  ? "opacity-100 bg-k-accent/15 border-k-accent/50 text-k-accentsoft hover:bg-k-accent/30 hover:text-white"
                  : "opacity-0 group-hover:opacity-100 focus:opacity-100 bg-transparent border-k-line text-k-subtle hover:text-white hover:border-k-accent/40"
              }`}
            >
              <Pencil size={10} />
            </button>
            {/* Cut badge: this line ends with a USER split (word endIdx ends
                the line by edit, not by wordsPerLine) — click to remove.
                Rendered straight off lineSplits, same array as everything else. */}
            {endsWithForcedSplit && (
              <button
                data-testid={EDITOR.splitMarker(line.endIdx)}
                onClick={() => useAppStore.getState().removeLineSplit(line.endIdx)}
                aria-label="Remove line split"
                title="Line break (your edit) — click to remove"
                className="group/split inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-k-accent/15 border border-k-accent/50 text-k-accentsoft hover:bg-k-accent/30 hover:text-white transition-colors"
              >
                <Scissors size={10} />
                <span className="font-mono text-[9px]">↵</span>
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
};

// ── Re-timed line words ─────────────────────────────────────────────
// Words of a realigned line are SYNTHETIC — they exist only in the
// realignment record (no transcript id), so wordEdits cannot address them
// and they render as plain read-only tokens. Click seeks; the line's pencil
// re-opens the line editor for any further change (which re-aligns).
const RealignedWordToken = ({ word, startIdx, index }) => {
  const isActive = useAppStore(
    (s) => s.currentTime >= word.start && s.currentTime < word.end
  );
  const isTanglishView = useAppStore(
    (s) => s.exportSettings.captionScript === "tanglish"
  );
  const text = isTanglishView ? word.text_tanglish || word.text : word.text;
  return (
    <button
      data-testid={EDITOR.realignedWord(startIdx, index)}
      onClick={() => useAppStore.getState().seek(word.start + 0.02)}
      title="Re-timed word — use the pencil to edit this line"
      className={`px-1.5 py-1 rounded text-xs font-medium text-center border transition-all ${
        isActive
          ? "bg-k-accent text-k-ink border-k-accent shadow-[0_0_12px_rgba(233,168,37,0.6)]"
          : "bg-k-surface text-k-accentsoft border-k-accent/60 hover:text-white"
      }`}
    >
      {text}
    </button>
  );
};

// ── Line editor ─────────────────────────────────────────────────────
// One free-text input for the whole line. Commit (Enter/blur) routes via
// diffLineEdit; Escape cancels. Transliteration suggestions key off the
// token at the caret and a pick replaces ONLY that token.
//
// SCRIPT-AWARE: in Tanglish view the input pre-fills, displays, and accepts
// TANGLISH — the user never sees Telugu here. On commit each changed token
// resolves to Telugu (resolveTokensToTelugu: Telugu script as-is; a known
// display tanglish reuses its word's Telugu without a round-trip; Latin →
// /transliterate top-1; unreachable → pending / typed-text degrade). Telugu
// remains the stored source of truth — only what the input shows changes.
const LineEditor = ({ line, onDone }) => {
  const transcript = useAppStore((s) => s.transcript);
  const effectiveWord = useEffectiveWord();
  const displayWord = useDisplayWord();
  const isTanglishView = useAppStore(
    (s) => s.exportSettings.captionScript === "tanglish"
  );

  // The pristine transcript words the line covers — raw index space IS the
  // store's transcript array position (getWordsForRange mirrors the backend
  // one-to-one), so a plain slice addresses them.
  const originalWords = transcript.slice(line.startIdx, line.endIdx + 1);
  // Change/revert detection happens in the DISPLAY script (what the input
  // pre-filled), so "unchanged" and "typed the pristine text back" compare
  // like with like in either view.
  const originalTexts = originalWords.map((w) =>
    isTanglishView ? w.text_tanglish || w.text : w.text
  );
  // What the line currently SHOWS — the edit baseline AND the prefill.
  const effectiveTexts = line.realigned
    ? line.words.map((w) => (isTanglishView ? w.text_tanglish || w.text : w.text))
    : line.words.map((w) => (isTanglishView ? displayWord(w.id) : effectiveWord(w.id)));

  const [value, setValue] = useState(effectiveTexts.join(" "));
  const [busy, setBusy] = useState(false);
  const [sugg, setSugg] = useState([]);
  const [hi, setHi] = useState(-1);
  const tokenRange = useRef({ token: "", start: 0, end: 0 });
  const fetchSeq = useRef(0);
  const fetchTimer = useRef(null);
  const inputRef = useRef(null);

  const closeSugg = () => {
    setSugg([]);
    setHi(-1);
    clearTimeout(fetchTimer.current);
  };

  const onChange = (e) => {
    const v = e.target.value;
    setValue(v);
    setHi(-1);
    clearTimeout(fetchTimer.current);
    // Per-token suggestions: only the token AT THE CARET, only when it's
    // Latin, never empty/whitespace (kills the 422s on empty keystrokes).
    const range = tokenAt(v, e.target.selectionStart);
    tokenRange.current = range;
    if (LATIN_TOKEN.test(range.token) && range.token.length >= 2) {
      const seq = ++fetchSeq.current;
      fetchTimer.current = setTimeout(async () => {
        const list = await fetchTransliterations(range.token);
        if (seq === fetchSeq.current) setSugg(list);
      }, 160);
    } else {
      setSugg([]);
    }
  };

  // A pick replaces ONLY the current token; editing continues (multi-word
  // input — committing the whole line on a pick would lose the rest).
  const pick = (candidate) => {
    const { start, end } = tokenRange.current;
    const next = replaceToken(value, start, end, candidate);
    setValue(next);
    closeSugg();
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (el) {
        el.focus();
        const caret = start + candidate.length;
        el.setSelectionRange(caret, caret);
      }
    });
  };

  // Tanglish-view resolution context: display romanization → Telugu source
  // for every word the line covers, so a typed token that matches a word's
  // shown tanglish reuses its Telugu without any transliteration round-trip.
  const buildKnownTelugu = () => {
    const known = new Map();
    if (!isTanglishView) return known;
    if (line.realigned) {
      line.words.forEach((w) => {
        if (w.text_tanglish) known.set(w.text_tanglish, w.text);
      });
    } else {
      line.words.forEach((w) => {
        const ta = displayWord(w.id);
        const te = effectiveWord(w.id);
        if (ta && ta !== te) known.set(ta, te);
      });
    }
    return known;
  };

  const commit = async () => {
    if (busy) return;
    const store = useAppStore.getState();
    const newTokens = tokenizeLine(value);
    const route = diffLineEdit({
      newTokens,
      effectiveTexts,
      originalTexts,
      isRealigned: !!line.realigned,
    });
    const key = realignmentKey(line.startIdx, line.endIdx);

    if (route.type === "cancel" || route.type === "noop") {
      onDone();
      return;
    }
    if (route.type === "revert") {
      store.clearLineRealignment(key, originalWords.map((w) => w.id));
      onDone();
      return;
    }
    if (route.type === "wordEdits") {
      // Same word count: plain text fixes — the existing wordEdits path
      // (timestamps untouched), batched into ONE history frame.
      if (!isTanglishView) {
        store.setWordEditsBatch(
          line.words.map((w, i) => ({ id: w.id, text: newTokens[i] }))
        );
        onDone();
        return;
      }
      // Tanglish view: ONLY changed tokens become edits (an unchanged token
      // must never round-trip through transliteration — rule 3d), each
      // resolved to Telugu; the typed spelling rides along verbatim.
      const changed = [];
      newTokens.forEach((token, i) => {
        if (token !== effectiveTexts[i]) changed.push({ i, token });
      });
      setBusy(true);
      const resolved = await resolveTokensToTelugu(
        changed.map((c) => c.token),
        buildKnownTelugu(),
        fetchTransliterations
      );
      store.setWordEditsBatch(
        changed.map((c, k) => {
          const r = resolved[k];
          if (r.pending) {
            return { id: line.words[c.i].id, text: null, text_tanglish: r.tanglish, pending: true };
          }
          if (r.tanglish == null) {
            // Telugu typed directly — as-is path, romanization re-derived.
            return { id: line.words[c.i].id, text: r.telugu };
          }
          return { id: line.words[c.i].id, text: r.telugu, text_tanglish: r.tanglish };
        })
      );
      store.resolvePendingTelugu();
      setBusy(false);
      onDone();
      return;
    }

    // Word count changed → re-align this line's audio span with the new
    // words. The span is FIXED: the pristine words' [first.start, last.end]
    // — never the (duration-capped / overlap-trimmed) display span, and
    // never moved by the edit.
    setBusy(true);
    // In Tanglish view the tokens are romanized — resolve each to the TELUGU
    // the record stores/burns (realignment words carry both scripts; there
    // is no pending state here, so an unresolvable Latin token degrades to
    // the typed text rather than losing the edit).
    let teluguWords = newTokens;
    let typedTanglish = null;
    if (isTanglishView) {
      const resolved = await resolveTokensToTelugu(
        newTokens,
        buildKnownTelugu(),
        fetchTransliterations
      );
      teluguWords = resolved.map((r) => r.telugu || r.tanglish);
      typedTanglish = resolved.map((r) => r.tanglish);
    }
    const lineStart = originalWords[0].start;
    const lineEnd = originalWords[originalWords.length - 1].end;
    const clip = store.currentClip;
    const res =
      clip && store.currentJobId
        ? await realignLine(store.currentJobId, clip.index, {
            lineStart,
            lineEnd,
            words: teluguWords,
          })
        : null;

    // The user's typed romanization wins over the server's re-derivation —
    // their spelling is what the Tanglish view (and a tanglish burn) shows.
    const withTypedTanglish = (words) =>
      words.map((w, i) => ({
        ...w,
        word_tanglish: typedTanglish?.[i] ?? w.word_tanglish,
      }));

    if (res) {
      store.setLineRealignment(key, {
        startIdx: line.startIdx,
        endIdx: line.endIdx,
        words: withTypedTanglish(res.words),
        approximate: res.approximate,
      });
    } else {
      // API unreachable — NEVER lose the edit: commit with client-side even
      // distribution (approximate), then backfill Tanglish async.
      store.setLineRealignment(key, {
        startIdx: line.startIdx,
        endIdx: line.endIdx,
        words: withTypedTanglish(evenlyDistribute(teluguWords, lineStart, lineEnd)),
        approximate: true,
      });
      // Derive romanization for slots without a typed spelling (all of them
      // in Telugu view; only Telugu-typed tokens in Tanglish view — the
      // fill-missing-only rule in setLineRealignmentTanglish protects the
      // user's verbatim spellings).
      fetchTanglish(teluguWords).then((out) => {
        if (out) useAppStore.getState().setLineRealignmentTanglish(key, out);
      });
    }
    setBusy(false);
    onDone();
  };

  const onKeyDown = (e) => {
    if (e.key === "Escape") {
      onDone();
      return;
    }
    if (sugg.length && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      const d = e.key === "ArrowDown" ? 1 : -1;
      setHi((h) => (h + d + sugg.length) % sugg.length);
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (hi >= 0 && sugg[hi]) {
        pick(sugg[hi]);
        return;
      }
      commit();
    }
  };

  return (
    <div className="relative flex items-center gap-1.5 rounded-md px-1.5 py-1 border border-k-accent bg-k-surface">
      <input
        ref={inputRef}
        data-testid={EDITOR.lineEditorInput(line.startIdx)}
        value={value}
        disabled={busy}
        autoFocus
        onChange={onChange}
        onKeyDown={onKeyDown}
        onBlur={() => (busy ? null : commit())}
        spellCheck={false}
        autoComplete="off"
        className="flex-1 min-w-0 bg-transparent outline-none text-xs font-medium text-white px-1 py-1"
      />
      {busy && (
        <span className="inline-flex items-center gap-1 text-[10px] text-k-accentsoft">
          <Loader2 size={11} className="animate-spin" />
          re-timing…
        </span>
      )}
      {!busy && sugg.length > 0 && (
        <div className="absolute left-0 top-full mt-1 z-30 min-w-[150px] rounded-md border border-k-line bg-k-surface shadow-[0_8px_24px_rgba(0,0,0,0.6)] overflow-hidden">
          {sugg.map((s, i) => (
            <button
              key={`${s}-${i}`}
              data-testid={EDITOR.translitSuggestion(i)}
              // mousedown (not click): keep the input from blurring first,
              // which would commit the raw draft before the pick lands.
              onMouseDown={(e) => {
                e.preventDefault();
                pick(s);
              }}
              className={`block w-full text-left px-2.5 py-1.5 text-sm text-white transition-colors ${
                hi === i ? "bg-k-accent/30" : "hover:bg-k-accent/20"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

const WordToken = ({ word, wordCount, isLineStart, lineSplits }) => {
  const effectiveWord = useEffectiveWord();
  const displayWord = useDisplayWord();
  const isEdited = useAppStore((s) => !!s.transcriptEdits.wordEdits[word.id]);
  const isPendingTelugu = useAppStore(
    (s) => !!s.transcriptEdits.wordEdits[word.id]?.pendingTelugu
  );
  const isActive = useAppStore(
    (s) => s.currentTime >= word.start && s.currentTime < word.end
  );
  const isTanglishView = useAppStore(
    (s) => s.exportSettings.captionScript === "tanglish"
  );
  // Feature #6 — keyword emphasis. Effective set = materialized user toggles
  // (transcriptEdits.emphasisIndices) or the clip's Gemini auto set.
  const isEmphasized = useAppStore((s) => {
    const set = Array.isArray(s.transcriptEdits.emphasisIndices)
      ? s.transcriptEdits.emphasisIndices
      : s.currentClip?.emphasis_indices || [];
    return set.includes(word.rawIdx);
  });
  // Feature #14 — filler/silence removal. A word inside an active cut span
  // renders struck-through; click restores that span. Returns the span's
  // start (the restore key) or null.
  const cutSpanStart = useAppStore((s) => {
    const spans = s.exportSettings.cutSpans;
    if (!Array.isArray(spans)) return null;
    const mid = (word.start + word.end) / 2;
    const hit = spans.find((sp) => mid >= sp.start && mid < sp.end);
    return hit ? hit.start : null;
  });

  // Two texts per word: the Telugu source (what edits STORE — Telugu remains
  // the source of truth) and the display text (script-aware — follows the
  // Telugu ⇄ Tanglish toggle). The input shows AND accepts the display
  // script: in Tanglish view the user sees/types romanized text only; the
  // commit path resolves it to Telugu (picked suggestion / /transliterate
  // top-1 / typed script — see commitTanglish).
  const committed = effectiveWord(word.id);
  const displayed = displayWord(word.id);
  const [draft, setDraft] = useState(null); // null = not editing
  const [sugg, setSugg] = useState([]);
  const [hi, setHi] = useState(-1); // highlighted dropdown row, -1 = none
  const tokenRange = useRef({ token: "", start: 0, end: 0 });
  const fetchSeq = useRef(0);
  const fetchTimer = useRef(null);

  const value = draft ?? displayed;
  // Dirty compares against what the input PRE-FILLED (the display text) —
  // an unchanged Tanglish token is NOT an edit and must never round-trip
  // through transliteration. In Telugu view displayed === committed and
  // this is byte-identical to the old behavior.
  const baseline = isTanglishView ? displayed : committed;
  const dirty = draft !== null && draft.trim() !== baseline;
  // Dropdown keys off the CURRENT TOKEN at the caret (never the whole input,
  // never empty text): Telugu candidates + always "keep as typed" last.
  const curToken = tokenRange.current.token;
  const dropdownOpen = dirty && !!curToken && LATIN_TOKEN.test(curToken);
  const rows = dropdownOpen ? [...sugg, draft.trim()] : [];

  const reset = () => {
    setDraft(null);
    setSugg([]);
    setHi(-1);
    tokenRange.current = { token: "", start: 0, end: 0 };
    clearTimeout(fetchTimer.current);
  };

  const commit = (text) => {
    useAppStore.getState().setWordEdit(word.id, text);
    reset();
  };

  // Tanglish-view commit of TYPED text (no suggestion picked). Per token:
  // empty → reset-to-original (existing rule); unchanged from the prefill →
  // not an edit; Telugu script typed directly → store as-is (romanization
  // re-derived); Latin → resolve to Telugu top-1 AT COMMIT through the SAME
  // resolver the line editor uses (resolveTokensToTelugu → /transliterate),
  // storing the resolved Telugu with the typed romanization kept verbatim.
  // Only a genuine service failure degrades to pending (retried on clip
  // reopen). Resolving at commit — instead of always committing pending and
  // relying on the async retry — is why a healthy 200 now actually lands on
  // the word. The editor never blocks: the input closes immediately and the
  // resolved word appears when the response does. ONE history frame either way.
  const commitTanglish = async (text) => {
    const token = typeof text === "string" ? text.trim() : "";
    const store = useAppStore.getState();
    if (!token) {
      store.setWordEdit(word.id, ""); // empty commit = reset to original
      reset();
      return;
    }
    if (token === displayed) {
      reset(); // unchanged — keep the original word untouched
      return;
    }
    if (!LATIN_TOKEN.test(token)) {
      store.setWordEdit(word.id, token); // Telugu typed directly — as-is
      reset();
      return;
    }
    reset(); // close the editor now — resolution is fire-and-forget
    const [r] = await resolveTokensToTelugu([token], new Map(), fetchTransliterations);
    if (r.pending) {
      // /transliterate unreachable/empty at commit — keep the typed tanglish,
      // no Telugu yet; resolvePendingTelugu upgrades it on the next reopen.
      store.setWordEditsBatch([
        { id: word.id, text: null, text_tanglish: r.tanglish, pending: true },
      ]);
    } else {
      // Top-1 Telugu applied as the word's source of truth; the user's typed
      // spelling rides along verbatim as text_tanglish.
      store.setWordEditsBatch([
        { id: word.id, text: r.telugu, text_tanglish: r.tanglish },
      ]);
    }
    // Retry any words still stuck pending from an earlier service outage —
    // "resolves on next commit" (a no-op when nothing is pending).
    store.resolvePendingTelugu();
  };

  // Script-aware commit dispatch for typed (non-picked) text.
  const commitTyped = (text) => {
    if (isTanglishView) commitTanglish(text);
    else commit(text);
  };

  // A picked candidate replaces ONLY the current token. The common case —
  // the draft IS a single token — commits immediately (the pre-existing
  // pick-to-commit flow); a multi-token draft keeps editing so the other
  // tokens aren't lost. In Tanglish view a picked candidate's Telugu is
  // already known — it stores directly, with the TYPED romanization kept
  // verbatim as the word's tanglish (the user's spelling wins).
  const pickCandidate = (candidate) => {
    if (tokenizeLine(draft).length <= 1) {
      if (isTanglishView) {
        useAppStore.getState().setWordEditsBatch([
          { id: word.id, text: candidate, text_tanglish: draft.trim() },
        ]);
        reset();
      } else {
        commit(candidate);
      }
      return;
    }
    const { start, end } = tokenRange.current;
    setDraft(replaceToken(draft, start, end, candidate));
    setSugg([]);
    setHi(-1);
  };

  const onChange = (e) => {
    const v = e.target.value;
    setDraft(v);
    setHi(-1);
    clearTimeout(fetchTimer.current);
    const range = tokenAt(v, e.target.selectionStart);
    tokenRange.current = range;
    const token = range.token;
    if (LATIN_TOKEN.test(token) && token.length >= 2) {
      // Debounced; stale responses are dropped via the sequence counter.
      // A dead service resolves to [] — the dropdown just shows "keep as
      // typed" and editing keeps working. Empty/whitespace tokens never
      // reach the network (the LATIN_TOKEN gate above).
      const seq = ++fetchSeq.current;
      fetchTimer.current = setTimeout(async () => {
        const list = await fetchTransliterations(token);
        if (seq === fetchSeq.current) setSugg(list);
      }, 160);
    } else {
      setSugg([]);
    }
  };

  const onKeyDown = (e) => {
    if (e.key === "Escape") {
      reset();
      e.target.blur();
      return;
    }
    if (dropdownOpen && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      const d = e.key === "ArrowDown" ? 1 : -1;
      setHi((h) => (h + d + rows.length) % rows.length);
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (dirty) {
        // A pending edit: Enter commits — a highlighted CANDIDATE goes
        // through the pick path (its Telugu is known); the keep-as-typed
        // row or no highlight commits the typed text (script-aware).
        if (hi >= 0 && hi < sugg.length) pickCandidate(sugg[hi]);
        else commitTyped(draft);
        return;
      }
      // Clean word: Enter splits at the caret.
      const caretAtStart = e.target.selectionStart === 0 && e.target.selectionEnd === 0;
      const idx = enterSplitIndex(word.rawIdx, caretAtStart, wordCount);
      if (idx !== null) useAppStore.getState().addLineSplit(idx);
      return;
    }
    if (
      e.key === "Backspace" &&
      e.target.selectionStart === 0 &&
      e.target.selectionEnd === 0 &&
      isLineStart
    ) {
      const idx = backspaceMergeIndex(word.rawIdx, lineSplits);
      if (idx !== null) {
        e.preventDefault();
        useAppStore.getState().removeLineSplit(idx);
      }
    }
  };

  return (
    <span className="relative inline-flex">
      <input
        data-testid={EDITOR.timelineWord(word.rawIdx)}
        value={value}
        onChange={onChange}
        onKeyDown={onKeyDown}
        onClick={(e) => {
          // Feature #14: a struck-through (cut) word — click restores its span.
          if (cutSpanStart != null) {
            e.preventDefault();
            useAppStore.getState().restoreCutSpan(cutSpanStart);
            return;
          }
          // Feature #6: Alt+click toggles keyword emphasis (bigger/bolder/
          // highlighted in captions). Plain click keeps the existing seek.
          if (e.altKey) {
            e.preventDefault();
            useAppStore.getState().toggleEmphasis(word.rawIdx);
            return;
          }
          if (!dirty) useAppStore.getState().seek(word.start + 0.02);
        }}
        onBlur={() => (dirty ? commitTyped(draft) : reset())}
        spellCheck={false}
        autoComplete="off"
        readOnly={cutSpanStart != null}
        title={
          cutSpanStart != null
            ? "Removed on export (filler/silence) — click to restore"
            : isEmphasized
              ? "Emphasized in captions — Alt+click to remove"
              : "Alt+click to emphasize in captions"
        }
        style={{ width: `${Math.max(value.length, 2) + 1.5}ch` }}
        className={`px-1.5 py-1 rounded text-xs font-medium text-center border outline-none transition-all ${
          cutSpanStart != null
            ? "bg-k-raised text-k-faint border-k-bad/50 line-through opacity-70 hover:opacity-100"
            : isActive
            ? "bg-k-accent text-k-ink border-k-accent shadow-[0_0_12px_rgba(233,168,37,0.6)]"
            : isEmphasized
              ? "bg-amber-500/10 text-amber-300 border-amber-500/60 font-bold hover:text-amber-200"
              : isEdited
                ? "bg-k-surface text-k-accentsoft border-k-accent/60"
                : "bg-k-surface text-k-muted border-k-line hover:text-white hover:border-k-accent/40"
        } focus:border-k-accent focus:text-white focus:bg-k-surface`}
      />
      {/* Edited-word tick: this word carries a wordEdits delta. Amber =
          Tanglish committed but its Telugu is still pending resolution
          (/transliterate was unreachable — retried async / on next commit). */}
      {isEdited && !dirty && (
        <span
          title={isPendingTelugu ? "Telugu pending — will resolve when the suggestion service is reachable" : undefined}
          className={`absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full pointer-events-none ${
            isPendingTelugu
              ? "bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.9)]"
              : "bg-k-accent shadow-[0_0_6px_rgba(233,168,37,0.9)]"
          }`}
        />
      )}
      {dropdownOpen && (
        <div className="absolute left-0 top-full mt-1 z-30 min-w-[150px] rounded-md border border-k-line bg-k-surface shadow-[0_8px_24px_rgba(0,0,0,0.6)] overflow-hidden">
          {sugg.map((s, i) => (
            <button
              key={`${s}-${i}`}
              data-testid={EDITOR.translitSuggestion(i)}
              // mousedown (not click): keep the input from blurring first,
              // which would commit the raw draft before the pick lands.
              onMouseDown={(e) => {
                e.preventDefault();
                pickCandidate(s);
              }}
              className={`block w-full text-left px-2.5 py-1.5 text-sm text-white transition-colors ${
                hi === i ? "bg-k-accent/30" : "hover:bg-k-accent/20"
              }`}
            >
              {s}
            </button>
          ))}
          <button
            data-testid={EDITOR.translitKeepTyped}
            onMouseDown={(e) => {
              e.preventDefault();
              commitTyped(draft);
            }}
            className={`block w-full text-left px-2.5 py-1.5 text-[11px] border-t border-k-raised transition-colors ${
              hi === rows.length - 1
                ? "bg-k-accent/30 text-white"
                : "text-k-subtle hover:bg-white/5 hover:text-white"
            }`}
          >
            keep as typed · “{draft.trim()}”
          </button>
        </div>
      )}
    </span>
  );
};

export default EditableTranscript;
