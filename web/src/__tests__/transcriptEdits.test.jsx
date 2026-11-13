/**
 * Transcript-edits storage layer regression suite.
 *
 * Covers the frontend half of the transcript-edits contract:
 *  - the store slice initializes to the full empty shape,
 *  - effectiveWord is the single word-text resolver (edit wins, original
 *    otherwise),
 *  - transcriptEdits round-trips through the draft save/load path,
 *  - startExport carries transcript_edits in the wire payload (BUG-003
 *    regression guard) in the BACKEND shape (ref-addressed list, api/models.py
 *    :: TranscriptEdits) — never the store's id-keyed dict,
 *  - zero edits ⇒ the transcript_edits field is omitted entirely (an empty
 *    dict would 422 pydantic's List[Any]),
 *  - word ids derive from the GLOBAL transcript ref, so an edit addresses the
 *    right word even across multi-segment clips / dropped empty words where
 *    clip-local index ≠ global index.
 */
import { useAppStore } from "@/store/useAppStore";
import { buildRerenderRequest, startRerender } from "@/api/renders";
import { buildClipTranscript } from "@/api/transcripts";
import { buildCaptionLines } from "@/lib/captionLines";
import { enterSplitIndex, backspaceMergeIndex } from "@/lib/editableTranscript";
import {
  createEmptyTranscriptEdits,
  wordIdFromRef,
  refFromWordId,
  serializeTranscriptEdits,
} from "@/lib/transcriptEdits";

// Keep buildRerenderRequest (and everything else) real; stub only the network
// call so startExport's outgoing request body is observable. The resolved
// value is (re)applied in beforeEach because CRA's jest config resets mock
// implementations between tests (resetMocks: true).
jest.mock("@/api/renders", () => ({
  ...jest.requireActual("@/api/renders"),
  startRerender: jest.fn(),
}));

const SAMPLE_EDITS = {
  wordEdits: {
    w_flat_4: { text: "సవరించిన", text_tanglish: null },
    w_seg_1_2: { text: "కొత్తపదం", text_tanglish: "kottapadam" },
  },
  mergedGroups: [1],
  lineSplits: [7],
  lineRealignments: {},
  // Feature #6: null = user never touched emphasis (the clip's auto set
  // applies); part of the persisted shape so drafts round-trip it.
  emphasisIndices: null,
};

beforeEach(() => {
  startRerender.mockResolvedValue("rerender_job_1");
  useAppStore.setState({ transcriptEdits: createEmptyTranscriptEdits() });
});

describe("transcriptEdits store slice", () => {
  test("initializes to the full empty shape — every key present from day one", () => {
    expect(useAppStore.getState().transcriptEdits).toEqual({
      wordEdits: {},
      mergedGroups: [],
      lineSplits: [],
      lineRealignments: {},
      emphasisIndices: null,
    });
  });

  test("a fresh EditDocument carries the empty transcriptEdits shape", () => {
    const doc = useAppStore.getState().getEditDocument();
    expect(doc.transcriptEdits).toEqual({
      wordEdits: {},
      mergedGroups: [],
      lineSplits: [],
      lineRealignments: {},
      emphasisIndices: null,
    });
  });
});

describe("effectiveWord — the single word-text resolver", () => {
  const TRANSCRIPT = [
    { id: "w_flat_0", ref: { type: "flat", index: 0 }, text: "నమస్తే", start: 0, end: 0.4 },
    { id: "w_flat_2", ref: { type: "flat", index: 2 }, text: "ప్రపంచం", start: 0.4, end: 0.9 },
  ];

  test("returns the edit when present, the original when absent", () => {
    useAppStore.setState({
      transcript: TRANSCRIPT,
      transcriptEdits: {
        wordEdits: { w_flat_2: { text: "లోకం", text_tanglish: null } },
        mergedGroups: [],
        lineSplits: [],
      },
    });
    const { effectiveWord } = useAppStore.getState();
    expect(effectiveWord("w_flat_2")).toBe("లోకం"); // edited
    expect(effectiveWord("w_flat_0")).toBe("నమస్తే"); // untouched original
  });

  test("unknown id resolves to empty string, never throws", () => {
    useAppStore.setState({ transcript: TRANSCRIPT });
    expect(useAppStore.getState().effectiveWord("w_flat_999")).toBe("");
  });
});

describe("draft persistence round-trip", () => {
  test("transcriptEdits survives save → JSON (Redis) → load", () => {
    useAppStore.setState({ transcriptEdits: SAMPLE_EDITS });

    // Save side: the slice is part of the outgoing draft payload.
    const doc = useAppStore.getState().getEditDocument();
    expect(doc.transcriptEdits).toEqual(SAMPLE_EDITS);

    // The backend stores the payload verbatim as JSON (api/main.py:283-298),
    // so a JSON round-trip is exactly what loadDraft hands back.
    const persisted = JSON.parse(JSON.stringify(doc));

    // Load side: wipe the slice, rehydrate via the same path openClip uses.
    useAppStore.setState({ transcriptEdits: createEmptyTranscriptEdits() });
    useAppStore.getState().applyDraft(persisted);
    expect(useAppStore.getState().transcriptEdits).toEqual(SAMPLE_EDITS);
  });

  test("the draft payload deep-copies the slice (no aliasing of live state)", () => {
    useAppStore.setState({ transcriptEdits: SAMPLE_EDITS });
    const doc = useAppStore.getState().getEditDocument();
    doc.transcriptEdits.wordEdits.w_flat_4.text = "mutated";
    doc.transcriptEdits.lineSplits.push(99);
    expect(useAppStore.getState().transcriptEdits.wordEdits.w_flat_4.text).toBe("సవరించిన");
    expect(useAppStore.getState().transcriptEdits.lineSplits).toEqual([7]);
  });

  test("an old draft without transcriptEdits leaves the slice untouched", () => {
    useAppStore.setState({ transcriptEdits: SAMPLE_EDITS });
    useAppStore.getState().applyDraft({ exportSettings: { format: "1:1" } });
    expect(useAppStore.getState().transcriptEdits).toEqual(SAMPLE_EDITS);
  });
});

describe("BUG-003 regression guard — startExport carries transcript_edits", () => {
  const CLIP = { id: "clip_x", jobId: "job_1", index: 0 };

  test("edits reach the rerender request body in the backend wire shape", async () => {
    useAppStore.setState({ currentClip: CLIP, transcriptEdits: SAMPLE_EDITS });
    const jobId = await useAppStore.getState().startExport();
    expect(jobId).toBe("rerender_job_1");

    expect(startRerender).toHaveBeenCalledTimes(1);
    const [calledJobId, clipIndex, req] = startRerender.mock.calls[0];
    expect(calledJobId).toBe("job_1");
    expect(clipIndex).toBe(0);

    // Wire shape: ref-addressed LIST, internal text → word — matching
    // api/models.py::TranscriptEdits / services/apply_transcript_edits.py.
    // A resolved edit that carries a typed romanization ships it verbatim as
    // word_tanglish (mirrors lineRealignments); one without omits the field.
    expect(req.transcript_edits).toEqual({
      wordEdits: [
        { ref: { type: "flat", index: 4 }, word: "సవరించిన" },
        {
          ref: { type: "segment", segIndex: 1, wordIndex: 2 },
          word: "కొత్తపదం",
          word_tanglish: "kottapadam",
        },
      ],
      mergedGroups: [1],
      lineSplits: [7],
    });
  });

  test("word_tanglish rides the wire verbatim only for edits that have one", () => {
    const wire = serializeTranscriptEdits({
      wordEdits: {
        w_flat_0: { text: "తెలుగు", text_tanglish: null }, // absent → field omitted
        w_flat_1: { text: "పదం", text_tanglish: "padam" }, // present → verbatim
      },
      mergedGroups: [],
      lineSplits: [],
      lineRealignments: {},
    });
    expect(wire.wordEdits).toEqual([
      { ref: { type: "flat", index: 0 }, word: "తెలుగు" },
      { ref: { type: "flat", index: 1 }, word: "పదం", word_tanglish: "padam" },
    ]);
  });

  test("pending Tanglish edits (Telugu unresolved) stay off the wire entirely", () => {
    // A pendingTelugu edit has text === null; the burn falls back to the
    // original word, so it must never serialize — carrying its text_tanglish
    // would render an edit whose Telugu source was never committed.
    const wire = serializeTranscriptEdits({
      wordEdits: {
        w_flat_0: { text: null, text_tanglish: "padam", pendingTelugu: true },
      },
      mergedGroups: [],
      lineSplits: [1], // something else present so the payload isn't null
      lineRealignments: {},
    });
    expect(wire.wordEdits).toEqual([]);
    expect(JSON.stringify(wire)).not.toContain("padam");
  });

  test("zero edits ⇒ no transcript_edits field at all (empty dict would 422 pydantic)", async () => {
    useAppStore.setState({ currentClip: CLIP, transcriptEdits: createEmptyTranscriptEdits() });
    await useAppStore.getState().startExport();
    expect(startRerender).toHaveBeenCalledTimes(1);
    expect(startRerender.mock.calls[0][2]).not.toHaveProperty("transcript_edits");
  });

  test("buildRerenderRequest itself omits transcript_edits for empty/missing edits", () => {
    expect("transcript_edits" in buildRerenderRequest({})).toBe(false);
    expect(
      "transcript_edits" in buildRerenderRequest({ transcriptEdits: createEmptyTranscriptEdits() })
    ).toBe(false);
  });
});

describe("Enter-split / Backspace-merge — caret→lineSplits (editable transcript)", () => {
  const TRANSCRIPT = [
    { id: "w_flat_0", ref: { type: "flat", index: 0 }, text: "ఒకటి", start: 0, end: 0.5 },
    { id: "w_flat_1", ref: { type: "flat", index: 1 }, text: "రెండు", start: 0.6, end: 1.4 },
    { id: "w_flat_2", ref: { type: "flat", index: 2 }, text: "మూడు", start: 2.0, end: 2.5 },
    { id: "w_flat_3", ref: { type: "flat", index: 3 }, text: "నాలుగు", start: 2.6, end: 3.1 },
  ];

  beforeEach(() => {
    useAppStore.setState({ transcript: TRANSCRIPT, duration: 4 });
  });

  test("enterSplitIndex: caret at word start breaks BEFORE it, caret elsewhere breaks AFTER it", () => {
    expect(enterSplitIndex(2, true, 4)).toBe(1); // w2 starts the new line
    expect(enterSplitIndex(2, false, 4)).toBe(2); // w3 starts the new line
    expect(enterSplitIndex(0, true, 4)).toBeNull(); // nothing before word 0
    expect(enterSplitIndex(3, false, 4)).toBeNull(); // nothing after the last word
  });

  test("Enter is idempotent: re-splitting an existing boundary never removes it", () => {
    useAppStore.getState().addLineSplit(1);
    useAppStore.getState().addLineSplit(1); // Enter again at the same boundary
    expect(useAppStore.getState().transcriptEdits.lineSplits).toEqual([1]);
  });

  test("addLineSplit guards the ends and keeps the list sorted", () => {
    const { addLineSplit } = useAppStore.getState();
    addLineSplit(2);
    addLineSplit(0);
    addLineSplit(-1); // before the first word — invalid
    addLineSplit(3); // the last word cannot END a line ahead of anything — invalid
    expect(useAppStore.getState().transcriptEdits.lineSplits).toEqual([0, 2]);
  });

  test("backspaceMergeIndex removes only USER splits, never natural boundaries", () => {
    expect(backspaceMergeIndex(2, [1])).toBe(1); // forced split above → removable
    expect(backspaceMergeIndex(2, [])).toBeNull(); // natural wordsPerLine break → not an edit
    expect(backspaceMergeIndex(0, [1])).toBeNull(); // first word: nothing above it
  });

  test("Backspace at a split line start rejoins the previous line", () => {
    useAppStore.getState().addLineSplit(1); // w2 starts a line
    const idx = backspaceMergeIndex(2, useAppStore.getState().transcriptEdits.lineSplits);
    expect(idx).toBe(1);
    useAppStore.getState().removeLineSplit(idx);
    expect(useAppStore.getState().transcriptEdits.lineSplits).toEqual([]);
  });

  test("splits leave the other edit kinds untouched", () => {
    useAppStore.setState({ transcriptEdits: { ...SAMPLE_EDITS, lineSplits: [3] } });
    useAppStore.getState().addLineSplit(1);
    const edits = useAppStore.getState().transcriptEdits;
    expect(edits.lineSplits).toEqual([1, 3]);
    expect(edits.wordEdits).toEqual(SAMPLE_EDITS.wordEdits);
    expect(edits.mergedGroups).toEqual(SAMPLE_EDITS.mergedGroups);
  });

  test("a split persists through the draft save → JSON → load round-trip", () => {
    useAppStore.getState().addLineSplit(1);
    const persisted = JSON.parse(JSON.stringify(useAppStore.getState().getEditDocument()));
    useAppStore.setState({ transcriptEdits: createEmptyTranscriptEdits() });
    useAppStore.getState().applyDraft(persisted);
    expect(useAppStore.getState().transcriptEdits.lineSplits).toEqual([1]);
  });
});

describe("GATE 1 — Enter→index→grouping: an Enter split must actually change the grouping", () => {
  // Successor of the playhead-era anti-no-op gate (the [34,38,42]/33-lines
  // field bug): a caret position must map to a lineSplits entry that REALLY
  // re-breaks the lines, matching group_words_with_splits. Realistic
  // 130-word clip (0.3s words, 0.1s gaps).
  const WORDS_130 = Array.from({ length: 130 }, (_, i) => ({
    id: `w_flat_${i}`,
    ref: { type: "flat", index: i },
    text: `w${i}`,
    start: Math.round(i * 0.4 * 1000) / 1000,
    end: Math.round((i * 0.4 + 0.3) * 1000) / 1000,
  }));
  const lineStarts = (lines) => lines.map((l) => l.words[0].text);

  beforeEach(() => {
    useAppStore.setState({ transcript: WORDS_130, duration: 52 });
  });

  test("Enter with caret at the start of mid-line word 33: line count 33→34, w33 starts a line", () => {
    const baseline = buildCaptionLines(WORDS_130, 4);
    expect(baseline).toHaveLength(33); // 130/4 plain chunking, as in the field log

    // Word 33 sits mid-line in [w32..w35]; caret before it + Enter.
    const idx = enterSplitIndex(33, true, WORDS_130.length);
    expect(idx).toBe(32);
    useAppStore.getState().addLineSplit(idx);

    const splits = useAppStore.getState().transcriptEdits.lineSplits;
    const lines = buildCaptionLines(WORDS_130, 4, splits);
    expect(lines).toHaveLength(34); // NOT 33 — the split is not a no-op
    expect(lineStarts(lines)).toContain("w33"); // w33 now STARTS a line…
    expect(lineStarts(baseline)).not.toContain("w33"); // …which it never did before
  });

  test("Enter with caret at the END of word 32 lands the same cut (both sides of one gap)", () => {
    const idx = enterSplitIndex(32, false, WORDS_130.length);
    expect(idx).toBe(32); // identical entry — cursor between w32 and w33 from either side
  });

  test("Backspace at the new line start merges back to the exact baseline grouping", () => {
    useAppStore.getState().addLineSplit(32);
    const idx = backspaceMergeIndex(33, useAppStore.getState().transcriptEdits.lineSplits);
    expect(idx).toBe(32);
    useAppStore.getState().removeLineSplit(idx);
    const lines = buildCaptionLines(WORDS_130, 4, useAppStore.getState().transcriptEdits.lineSplits);
    expect(lineStarts(lines)).toEqual(lineStarts(buildCaptionLines(WORDS_130, 4)));
  });

  test("panel rows, preview, and export all read the SAME lineSplits array (one source of truth)", () => {
    useAppStore.getState().addLineSplit(32);
    const s = useAppStore.getState();
    // Panel: EditableTranscript rows re-group from this array; the cut badge
    // renders where lineSplits contains the line's last raw index.
    expect(s.transcriptEdits.lineSplits.includes(32)).toBe(true);
    // Preview: CaptionBody feeds the same array to buildCaptionLines (asserted above).
    // Export: the same array serializes onto the wire untouched.
    expect(serializeTranscriptEdits(s.transcriptEdits).lineSplits).toEqual([32]);
    // Removing (Backspace or the cut badge) clears all three at once.
    s.removeLineSplit(32);
    expect(useAppStore.getState().transcriptEdits.lineSplits).toEqual([]);
  });
});

describe("GATE 2 — type-to-fix: setWordEdit commits, previews, persists, exports", () => {
  const TRANSCRIPT = [
    { id: "w_flat_0", ref: { type: "flat", index: 0 }, text: "నమస్తే", start: 0, end: 0.5 },
    { id: "w_flat_2", ref: { type: "flat", index: 2 }, text: "ప్రపంచం", start: 0.6, end: 1.1 },
  ];

  beforeEach(() => {
    useAppStore.setState({ transcript: TRANSCRIPT, duration: 2 });
  });

  test("commits an id-keyed wordEdits entry that effectiveWord resolves", () => {
    useAppStore.getState().setWordEdit("w_flat_2", "లోకం");
    const s = useAppStore.getState();
    expect(s.transcriptEdits.wordEdits.w_flat_2).toEqual({ text: "లోకం", text_tanglish: null });
    expect(s.effectiveWord("w_flat_2")).toBe("లోకం");
    expect(s.effectiveWord("w_flat_0")).toBe("నమస్తే"); // untouched
  });

  test("text_tanglish stays null on commit — telugu_to_tanglish is NOT wired in this repo (Tanglish-task seam)", () => {
    useAppStore.getState().setWordEdit("w_flat_2", "లోకం");
    expect(useAppStore.getState().transcriptEdits.wordEdits.w_flat_2.text_tanglish).toBeNull();
  });

  test("re-committing the original text (or blank) clears the edit", () => {
    useAppStore.getState().setWordEdit("w_flat_2", "లోకం");
    useAppStore.getState().setWordEdit("w_flat_2", "ప్రపంచం"); // back to original
    expect(useAppStore.getState().transcriptEdits.wordEdits).toEqual({});
    useAppStore.getState().setWordEdit("w_flat_2", "లోకం");
    useAppStore.getState().setWordEdit("w_flat_2", "   "); // blank = revert
    expect(useAppStore.getState().transcriptEdits.wordEdits).toEqual({});
  });

  test("text-only contract: the transcript's timestamps are never touched", () => {
    useAppStore.getState().setWordEdit("w_flat_2", "లోకం");
    const w = useAppStore.getState().transcript.find((x) => x.id === "w_flat_2");
    expect(w).toEqual({ id: "w_flat_2", ref: { type: "flat", index: 2 }, text: "ప్రపంచం", start: 0.6, end: 1.1 });
  });

  test("unknown word id is ignored", () => {
    useAppStore.getState().setWordEdit("w_flat_999", "ఏదో");
    expect(useAppStore.getState().transcriptEdits.wordEdits).toEqual({});
  });

  test("survives draft save → JSON (Redis) → load", () => {
    useAppStore.getState().setWordEdit("w_flat_2", "లోకం");
    const persisted = JSON.parse(JSON.stringify(useAppStore.getState().getEditDocument()));
    useAppStore.setState({ transcriptEdits: createEmptyTranscriptEdits() });
    useAppStore.getState().applyDraft(persisted);
    expect(useAppStore.getState().transcriptEdits.wordEdits.w_flat_2).toEqual({
      text: "లోకం",
      text_tanglish: null,
    });
  });

  test("reaches the export wire as a ref-addressed {ref, word} entry", () => {
    useAppStore.getState().setWordEdit("w_flat_2", "లోకం");
    const wire = serializeTranscriptEdits(useAppStore.getState().transcriptEdits);
    expect(wire.wordEdits).toEqual([{ ref: { type: "flat", index: 2 }, word: "లోకం" }]);
  });
});

describe("word ids derive from the global ref (id ↔ wire address share one source)", () => {
  test("wordIdFromRef / refFromWordId are exact inverses", () => {
    const flat = { type: "flat", index: 42 };
    const seg = { type: "segment", segIndex: 3, wordIndex: 7 };
    expect(refFromWordId(wordIdFromRef(flat))).toEqual(flat);
    expect(refFromWordId(wordIdFromRef(seg))).toEqual(seg);
    // Non-derived ids can never serialize into a bogus backend address.
    for (const bad of ["w_042", "w_flat_", "0", "", null, undefined]) {
      expect(refFromWordId(bad)).toBeNull();
    }
  });

  test("a ref-derived id maps to the correct GLOBAL word across a multi-segment clip", () => {
    // Global transcript: segment A [0–2] (with an empty word the frontend
    // drops), a dead zone [5–6] the clip cuts out, then segment B [10–12].
    // Clip-local positions therefore diverge from global indices twice over —
    // exactly the drift the ref-derived ids exist to prevent.
    const transcript = {
      word_timestamps: [
        { word: "మొదటి", start: 0, end: 1 }, // global 0 → clip word 0
        { word: "", start: 1, end: 1.2 }, // global 1 — dropped (empty text)
        { word: "రెండవ", start: 1.2, end: 2 }, // global 2 → clip word 1
        { word: "చనిపోయిన", start: 5, end: 6 }, // global 3 — dead zone, cut out
        { word: "మూడవ", start: 10, end: 11 }, // global 4 → clip word 2
        { word: "నాల్గవ", start: 11, end: 12 }, // global 5 → clip word 3
      ],
      sentences: [
        { id: 1, text: "a", start: 0, end: 2 },
        { id: 2, text: "dead", start: 5, end: 6 },
        { id: 3, text: "b", start: 10, end: 12 },
      ],
    };
    const clip = {
      start: 0,
      end: 12,
      duration: 4,
      segments: [
        { start_sent_id: 1, end_sent_id: 1 },
        { start_sent_id: 3, end_sent_id: 3 },
      ],
    };

    const words = buildClipTranscript(transcript, clip);
    expect(words.map((w) => w.text)).toEqual(["మొదటి", "రెండవ", "మూడవ", "నాల్గవ"]);
    // Refs point at GLOBAL indices, not clip-local positions [0,1,2,3].
    expect(words.map((w) => w.ref.index)).toEqual([0, 2, 4, 5]);
    expect(words.map((w) => w.id)).toEqual(
      [0, 2, 4, 5].map((index) => wordIdFromRef({ type: "flat", index }))
    );
    // Second segment's words sit on the stitched output timeline (offset by
    // segment A's 2s length) while keeping their global address.
    expect(words[2].start).toBeCloseTo(2);

    // An edit stored under the clip's 3rd word serializes to global index 4…
    const wire = serializeTranscriptEdits({
      wordEdits: { [words[2].id]: { text: "సవరించిన", text_tanglish: null } },
      mergedGroups: [],
      lineSplits: [],
    });
    expect(wire.wordEdits).toEqual([{ ref: { type: "flat", index: 4 }, word: "సవరించిన" }]);
    // …which is exactly where the backend will find the word being replaced.
    expect(transcript.word_timestamps[wire.wordEdits[0].ref.index].word).toBe("మూడవ");
  });
});
