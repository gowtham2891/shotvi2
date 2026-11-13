/**
 * Feature #6 — keyword emphasis (frontend half).
 *
 * transcriptEdits.emphasisIndices: null = never touched → the clip's Gemini
 * auto set (clip.emphasis_indices) applies; the first toggle materializes an
 * explicit array (drafts/undo carry it via transcriptEdits). The wire field
 * is RerenderRequest.emphasis_indices — top-level, NEVER inside
 * transcript_edits (serializeTranscriptEdits excludes it by construction).
 */
import { useAppStore } from "@/store/useAppStore";
import {
  createEmptyTranscriptEdits,
  sanitizeTranscriptEdits,
  serializeTranscriptEdits,
} from "@/lib/transcriptEdits";
import { buildRerenderRequest } from "@/api/renders";

const WORDS = [
  { id: "w_flat_0", text: "మీరు", start: 0.0, end: 0.4 },
  { id: "w_flat_1", text: "ఒక", start: 0.4, end: 0.6 },
  { id: "w_flat_2", text: "trap-లో", start: 0.6, end: 1.0 },
  { id: "w_flat_3", text: "ఉన్నారు", start: 1.0, end: 1.5 },
];

const st = () => useAppStore.getState();

beforeEach(() => {
  useAppStore.setState({
    transcript: WORDS,
    transcriptEdits: createEmptyTranscriptEdits(),
    currentClip: { emphasis_indices: [2] },
  });
});

describe("store: effective set + toggle", () => {
  test("untouched clip falls back to the Gemini auto set", () => {
    expect(st().getEffectiveEmphasis()).toEqual([2]);
    expect(st().transcriptEdits.emphasisIndices).toBeNull();
  });

  test("first toggle materializes auto set + change", () => {
    st().toggleEmphasis(0);
    expect(st().transcriptEdits.emphasisIndices).toEqual([0, 2]);
    expect(st().getEffectiveEmphasis()).toEqual([0, 2]);
  });

  test("toggling an auto-set word off materializes its removal", () => {
    st().toggleEmphasis(2);
    expect(st().transcriptEdits.emphasisIndices).toEqual([]);
    expect(st().getEffectiveEmphasis()).toEqual([]);
  });

  test("out-of-range indices are rejected", () => {
    st().toggleEmphasis(99);
    st().toggleEmphasis(-1);
    expect(st().transcriptEdits.emphasisIndices).toBeNull(); // untouched
  });
});

describe("sanitize / serialize", () => {
  test("old drafts (no key) stay null so the auto set applies", () => {
    expect(sanitizeTranscriptEdits({ wordEdits: {} }).emphasisIndices).toBeNull();
  });

  test("a materialized array survives sanitize — including []", () => {
    expect(sanitizeTranscriptEdits({ emphasisIndices: [] }).emphasisIndices).toEqual([]);
    expect(sanitizeTranscriptEdits({ emphasisIndices: [3, 1, "x", 2.5] }).emphasisIndices)
      .toEqual([3, 1]);
  });

  test("emphasis never leaks into the transcript_edits wire shape", () => {
    const edits = {
      ...createEmptyTranscriptEdits(),
      lineSplits: [1],
      emphasisIndices: [0, 2],
    };
    const wire = serializeTranscriptEdits(edits);
    expect(wire.lineSplits).toEqual([1]);
    expect("emphasisIndices" in wire).toBe(false);
    expect("emphasis_indices" in wire).toBe(false);
  });
});

describe("buildRerenderRequest wire field", () => {
  test("array (even []) crosses as emphasis_indices", () => {
    expect(buildRerenderRequest({ emphasisIndices: [0, 2] }).emphasis_indices).toEqual([0, 2]);
    expect(buildRerenderRequest({ emphasisIndices: [] }).emphasis_indices).toEqual([]);
  });

  test("null/omitted keeps pre-feature payloads byte-identical", () => {
    expect("emphasis_indices" in buildRerenderRequest({})).toBe(false);
    expect("emphasis_indices" in buildRerenderRequest({ emphasisIndices: null })).toBe(false);
  });

  test("non-integers are filtered from the wire", () => {
    expect(buildRerenderRequest({ emphasisIndices: [1, "a", 2.5, 3] }).emphasis_indices)
      .toEqual([1, 3]);
  });
});
