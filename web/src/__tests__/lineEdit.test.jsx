/**
 * Line-level caption editing — routing, per-token suggestions, realignment
 * storage/serialization, and the store's batch/realign commit actions.
 *
 * Mirrors the backend gates in tests/test_realign_line.py and
 * tests/test_apply_transcript_edits.py: the frontend applyLineRealignments
 * and the burn's apply_line_realignments must agree line-for-line
 * (cumulative raw-index matching, fixed line spans, inert on grouping drift).
 */

import {
  tokenAt,
  replaceToken,
  tokenizeLine,
  diffLineEdit,
  evenlyDistribute,
  LATIN_TOKEN,
} from "@/lib/lineEdit";
import {
  createEmptyTranscriptEdits,
  sanitizeTranscriptEdits,
  serializeTranscriptEdits,
  realignmentKey,
  hasTranscriptEdits,
} from "@/lib/transcriptEdits";
import {
  buildCaptionLines,
  applyLineRealignments,
  buildCaptionLinesWithRealignments,
} from "@/lib/captionLines";
import { useAppStore } from "@/store/useAppStore";
import { fetchTransliterations } from "@/api/transliterate";
import { client } from "@/api/client";

jest.mock("@/api/client", () => ({
  ...jest.requireActual("@/api/client"),
  client: { post: jest.fn() },
}));

// 8 words, 0.5s each — same shape the store's transcript slice holds.
const WORDS8 = Array.from({ length: 8 }, (_, i) => ({
  id: `w_flat_${i}`,
  ref: { type: "flat", index: i },
  text: `t${i}`,
  text_tanglish: `r${i}`,
  start: i * 0.5,
  end: (i + 1) * 0.5,
}));

const REALIGN_WORDS = [
  { word: "కొత్త", start: 0.1, end: 0.8, word_tanglish: "kotta" },
  { word: "పదం", start: 0.9, end: 1.4, word_tanglish: "padam" },
  { word: "మూడు", start: 1.5, end: 1.9, word_tanglish: "moodu" },
];

const freshStore = () => {
  useAppStore.setState({
    transcript: WORDS8,
    transcriptEdits: createEmptyTranscriptEdits(),
    history: { past: [], future: [] },
  });
};

beforeEach(() => {
  jest.clearAllMocks();
  freshStore();
});

// ── tokenAt / replaceToken — per-token suggestion extraction ────────

describe("tokenAt — the token at the caret, never the whole input", () => {
  test("caret inside a word returns that word", () => {
    expect(tokenAt("rendu mukkalu", 8)).toEqual({ token: "mukkalu", start: 6, end: 13 });
  });

  test("caret at end of first word returns the first word", () => {
    expect(tokenAt("rendu mukkalu", 5)).toEqual({ token: "rendu", start: 0, end: 5 });
  });

  test("caret right after a space is an EMPTY token (no fetch fires)", () => {
    const { token } = tokenAt("rendu ", 6);
    expect(token).toBe("");
    expect(LATIN_TOKEN.test(token)).toBe(false);
  });

  test("typing a space then a new word keys off the NEW word", () => {
    expect(tokenAt("rendu mu", 8).token).toBe("mu");
  });

  test("empty and whitespace-only inputs yield empty tokens", () => {
    expect(tokenAt("", 0).token).toBe("");
    expect(tokenAt("   ", 2).token).toBe("");
  });

  test("replaceToken swaps only the token range", () => {
    const { start, end } = tokenAt("rendu mukkalu unnai", 8);
    expect(replaceToken("rendu mukkalu unnai", start, end, "ముక్కలు")).toBe(
      "rendu ముక్కలు unnai"
    );
  });
});

describe("empty/whitespace text never reaches /transliterate", () => {
  test.each(["", "   ", null, undefined, "\t"])("input %p resolves [] without network", async (input) => {
    const out = await fetchTransliterations(input);
    expect(out).toEqual([]);
    expect(client.post).not.toHaveBeenCalled();
  });

  test("Telugu-script tokens skip the network too", async () => {
    expect(await fetchTransliterations("ముక్కలు")).toEqual([]);
    expect(client.post).not.toHaveBeenCalled();
  });
});

// ── diffLineEdit — commit routing ───────────────────────────────────

describe("diffLineEdit — line commit routing", () => {
  const base = {
    effectiveTexts: ["a", "b", "c"],
    originalTexts: ["a", "b", "c"],
    isRealigned: false,
  };

  test("empty commit cancels (a line edit can never delete a line)", () => {
    expect(diffLineEdit({ ...base, newTokens: [] }).type).toBe("cancel");
  });

  test("unchanged text is a noop", () => {
    expect(diffLineEdit({ ...base, newTokens: ["a", "b", "c"] }).type).toBe("noop");
  });

  test("same word count with changed text routes to wordEdits (no re-alignment)", () => {
    expect(diffLineEdit({ ...base, newTokens: ["a", "X", "c"] }).type).toBe("wordEdits");
  });

  test("added word routes to realign", () => {
    expect(diffLineEdit({ ...base, newTokens: ["a", "b", "NEW", "c"] }).type).toBe("realign");
  });

  test("removed word routes to realign", () => {
    expect(diffLineEdit({ ...base, newTokens: ["a", "c"] }).type).toBe("realign");
  });

  test("any change on an already-realigned line re-aligns (synthetic words have no ids)", () => {
    expect(
      diffLineEdit({
        newTokens: ["p", "X"],
        effectiveTexts: ["p", "q"],
        originalTexts: ["a", "b", "c"],
        isRealigned: true,
      }).type
    ).toBe("realign");
  });

  test("typing the pristine text back reverts a realigned line", () => {
    expect(
      diffLineEdit({
        newTokens: ["a", "b", "c"],
        effectiveTexts: ["p", "q"],
        originalTexts: ["a", "b", "c"],
        isRealigned: true,
      }).type
    ).toBe("revert");
  });
});

// ── evenlyDistribute — the client-side fallback ─────────────────────

describe("evenlyDistribute — fallback timing mirrors the backend", () => {
  test("splits the fixed span exactly, contiguous and monotonic", () => {
    const out = evenlyDistribute(["x", "y", "z"], 2, 5);
    expect(out[0].start).toBe(2);
    expect(out[2].end).toBe(5);
    for (let i = 1; i < out.length; i++) {
      expect(out[i].start).toBe(out[i - 1].end);
      expect(out[i].end).toBeGreaterThan(out[i].start);
    }
    expect(out.every((w) => w.word_tanglish === null)).toBe(true);
  });

  test("empty tokens yield an empty list", () => {
    expect(evenlyDistribute([], 0, 4)).toEqual([]);
  });
});

// ── applyLineRealignments — preview overlay (backend mirror) ────────

describe("applyLineRealignments — grouped-lines overlay", () => {
  const key = realignmentKey(0, 3);
  const rec = { startIdx: 0, endIdx: 3, words: REALIGN_WORDS, approximate: false };

  test("replaces exactly the matching line; boundaries and other lines untouched", () => {
    const lines = buildCaptionLinesWithRealignments(WORDS8, 4, [], { [key]: rec });
    expect(lines[0].realigned).toBe(true);
    expect(lines[0].words.map((w) => w.text)).toEqual(["కొత్త", "పదం", "మూడు"]);
    expect(lines[0].words.every((w) => w.realigned)).toBe(true);
    // FIXED span: line boundaries come from the original grouping
    expect(lines[0].lineStart).toBe(0);
    expect(lines[0].lineEnd).toBeCloseTo(2.0);
    expect(lines[1].realigned).toBeUndefined();
    expect(lines[1].words.map((w) => w.id)).toEqual(["w_flat_4", "w_flat_5", "w_flat_6", "w_flat_7"]);
  });

  test("annotates startIdx/endIdx on every line (editor addressing)", () => {
    const lines = applyLineRealignments(buildCaptionLines(WORDS8, 4, []), {});
    expect(lines.map((l) => [l.startIdx, l.endIdx])).toEqual([
      [0, 3],
      [4, 7],
    ]);
  });

  test("inert when grouping no longer matches (wordsPerLine changed)", () => {
    const lines = buildCaptionLinesWithRealignments(WORDS8, 2, [], { [key]: rec });
    expect(lines.every((l) => !l.realigned)).toBe(true);
  });

  test("realigned word times clamp into the line's fixed span", () => {
    const wild = {
      startIdx: 0,
      endIdx: 3,
      words: [
        { word: "క", start: -5, end: 0.5, word_tanglish: null },
        { word: "ఖ", start: 1.0, end: 99, word_tanglish: null },
      ],
      approximate: true,
    };
    const [line] = buildCaptionLinesWithRealignments(WORDS8, 4, [], { [key]: wild });
    for (const w of line.words) {
      expect(w.start).toBeGreaterThanOrEqual(line.lineStart);
      expect(w.end).toBeLessThanOrEqual(line.lineEnd);
      expect(w.end).toBeGreaterThanOrEqual(w.start);
    }
    expect(line.approximate).toBe(true);
  });
});

// ── storage: sanitize / serialize / draft round-trip ────────────────

describe("lineRealignments storage contract", () => {
  const key = realignmentKey(0, 3);
  const rec = { startIdx: 0, endIdx: 3, words: REALIGN_WORDS, approximate: true };

  test("sanitize keeps valid records and drops malformed ones", () => {
    const clean = sanitizeTranscriptEdits({
      lineRealignments: {
        [key]: rec,
        "bogus-key": rec,
        "1:2": { startIdx: 1, endIdx: 2, words: [{ word: "", start: 0, end: 1 }] },
        "3:4": { startIdx: 3, endIdx: 4, words: [{ word: "ok", start: "x", end: 1 }] },
      },
    });
    expect(Object.keys(clean.lineRealignments)).toEqual([key]);
    expect(clean.lineRealignments[key].approximate).toBe(true);
    expect(clean.lineRealignments[key].words).toHaveLength(3);
  });

  test("hasTranscriptEdits counts a realignment as an edit", () => {
    const edits = createEmptyTranscriptEdits();
    expect(hasTranscriptEdits(edits)).toBe(false);
    edits.lineRealignments[key] = rec;
    expect(hasTranscriptEdits(edits)).toBe(true);
  });

  test("serialize emits the wire LIST; omitted entirely when empty", () => {
    const edits = createEmptyTranscriptEdits();
    edits.lineSplits = [1];
    expect("lineRealignments" in serializeTranscriptEdits(edits)).toBe(false);

    edits.lineRealignments[key] = rec;
    const wire = serializeTranscriptEdits(edits);
    expect(wire.lineRealignments).toEqual([
      { startIdx: 0, endIdx: 3, words: REALIGN_WORDS, approximate: true },
    ]);
  });

  test("draft round-trip: getEditDocument → sanitize → identical records", () => {
    useAppStore.getState().setLineRealignment(key, rec);
    const doc = useAppStore.getState().getEditDocument();
    // Deep copy, not aliased store state
    doc.transcriptEdits.lineRealignments[key].words[0].word = "MUTATED";
    expect(
      useAppStore.getState().transcriptEdits.lineRealignments[key].words[0].word
    ).toBe("కొత్త");

    const restored = sanitizeTranscriptEdits(
      useAppStore.getState().getEditDocument().transcriptEdits
    );
    expect(restored.lineRealignments).toEqual({ [key]: rec });
  });
});

// ── store actions: one history frame per line commit ────────────────

describe("store actions — history + realignment lifecycle", () => {
  const key = realignmentKey(0, 3);
  const rec = { startIdx: 0, endIdx: 3, words: REALIGN_WORDS, approximate: false };

  test("setWordEditsBatch: whole line commit is ONE undo frame", () => {
    useAppStore.getState().setWordEditsBatch([
      { id: "w_flat_0", text: "కొత్త" },
      { id: "w_flat_1", text: "పదం" },
      { id: "w_flat_2", text: "t2" }, // unchanged → clears/noop
    ]);
    const s = useAppStore.getState();
    expect(s.history.past).toHaveLength(1);
    expect(s.transcriptEdits.wordEdits["w_flat_0"].text).toBe("కొత్త");
    expect(s.transcriptEdits.wordEdits["w_flat_1"].text).toBe("పదం");
    expect(s.transcriptEdits.wordEdits["w_flat_2"]).toBeUndefined();
    // Undo restores the whole line at once
    s.undo();
    expect(useAppStore.getState().transcriptEdits.wordEdits).toEqual({});
  });

  test("setWordEditsBatch with only noops pushes NO phantom frame", () => {
    useAppStore.getState().setWordEditsBatch([{ id: "w_flat_0", text: "t0" }]);
    expect(useAppStore.getState().history.past).toHaveLength(0);
  });

  test("setLineRealignment: one frame; undo removes the re-timing", () => {
    useAppStore.getState().setLineRealignment(key, rec);
    let s = useAppStore.getState();
    expect(s.history.past).toHaveLength(1);
    expect(s.transcriptEdits.lineRealignments[key]).toEqual(rec);
    s.undo();
    expect(useAppStore.getState().transcriptEdits.lineRealignments).toEqual({});
    useAppStore.getState().redo();
    expect(useAppStore.getState().transcriptEdits.lineRealignments[key]).toEqual(rec);
  });

  test("clearLineRealignment reverts the line AND its word edits in one frame", () => {
    useAppStore.getState().setWordEdit("w_flat_1", "మార్పు");
    useAppStore.getState().setLineRealignment(key, rec);
    useAppStore.getState().clearLineRealignment(key, ["w_flat_0", "w_flat_1"]);
    const s = useAppStore.getState();
    expect(s.transcriptEdits.lineRealignments).toEqual({});
    expect(s.transcriptEdits.wordEdits["w_flat_1"]).toBeUndefined();
    expect(s.history.past).toHaveLength(3); // word edit + realign + revert
  });

  test("setLineRealignmentTanglish backfills without a history frame", () => {
    useAppStore.getState().setLineRealignment(key, {
      ...rec,
      words: rec.words.map((w) => ({ ...w, word_tanglish: null })),
    });
    const frames = useAppStore.getState().history.past.length;
    useAppStore.getState().setLineRealignmentTanglish(key, ["kotta", "padam", "moodu"]);
    const s = useAppStore.getState();
    expect(s.history.past).toHaveLength(frames);
    expect(s.transcriptEdits.lineRealignments[key].words.map((w) => w.word_tanglish)).toEqual([
      "kotta",
      "padam",
      "moodu",
    ]);
  });
});

// ── realignLine adapter — graceful degrade ──────────────────────────

describe("realignLine adapter", () => {
  const { realignLine } = require("@/api/realign");

  test("returns words+approximate on a healthy response", async () => {
    client.post.mockResolvedValueOnce({
      data: { words: REALIGN_WORDS, approximate: false },
    });
    const out = await realignLine("job-1", 0, {
      lineStart: 0,
      lineEnd: 2,
      words: ["కొత్త", "పదం", "మూడు"],
    });
    expect(out.approximate).toBe(false);
    expect(out.words).toHaveLength(3);
    expect(client.post).toHaveBeenCalledWith(
      "/jobs/job-1/clips/0/realign-line",
      { line_start: 0, line_end: 2, words: ["కొత్త", "పదం", "మూడు"] }
    );
  });

  test("resolves null on network failure (caller falls back, edit never lost)", async () => {
    client.post.mockRejectedValueOnce(new Error("down"));
    expect(
      await realignLine("job-1", 0, { lineStart: 0, lineEnd: 2, words: ["ఒకటి"] })
    ).toBeNull();
  });

  test("resolves null on word-count mismatch (bad payload never committed)", async () => {
    client.post.mockResolvedValueOnce({ data: { words: [REALIGN_WORDS[0]], approximate: false } });
    expect(
      await realignLine("job-1", 0, { lineStart: 0, lineEnd: 2, words: ["ఒకటి", "రెండు"] })
    ).toBeNull();
  });

  test("never posts empty/whitespace-only words", async () => {
    expect(
      await realignLine("job-1", 0, { lineStart: 0, lineEnd: 2, words: ["  ", ""] })
    ).toBeNull();
    expect(client.post).not.toHaveBeenCalled();
  });
});

// ── tokenizeLine ────────────────────────────────────────────────────

describe("tokenizeLine", () => {
  test("splits on any whitespace, drops empties", () => {
    expect(tokenizeLine("  ఒకటి   రెండు\tమూడు ")).toEqual(["ఒకటి", "రెండు", "మూడు"]);
  });
  test("whitespace-only input tokenizes to []", () => {
    expect(tokenizeLine("   ")).toEqual([]);
    expect(tokenizeLine("")).toEqual([]);
    expect(tokenizeLine(null)).toEqual([]);
  });
});
