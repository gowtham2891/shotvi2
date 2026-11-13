/**
 * Feature #14 — filler/silence store slice + export wire.
 * cutSpans null = off; enabling seeds from detection; restore removes one
 * span; the effective pairs cross the wire as cut_spans.
 */
import { useAppStore } from "@/store/useAppStore";
import { buildRerenderRequest } from "@/api/renders";

const st = () => useAppStore.getState();

const WORDS = [
  { text: "hello", start: 0.0, end: 0.5 },
  { text: "um", start: 0.6, end: 0.9 }, // filler
  { text: "world", start: 3.0, end: 3.5 }, // 2.1s gap before → silence
];

beforeEach(() => {
  useAppStore.setState({
    transcript: WORDS,
    duration: 4.0,
    exportSettings: { ...useAppStore.getState().exportSettings, cutSpans: null },
  });
  st().resetHistory();
});

describe("enable / disable / restore", () => {
  test("off by default (null)", () => {
    expect(st().isFillerRemovalOn()).toBe(false);
    expect(st().exportSettings.cutSpans).toBeNull();
  });

  test("enable seeds cut spans from detection (filler + silence)", () => {
    st().enableFillerRemoval();
    const spans = st().exportSettings.cutSpans;
    expect(Array.isArray(spans)).toBe(true);
    expect(spans.some((s) => s.kind === "filler")).toBe(true);
    expect(spans.some((s) => s.kind === "silence")).toBe(true);
  });

  test("isWordCut flags the filler word, not the clean ones", () => {
    st().enableFillerRemoval();
    expect(st().isWordCut(WORDS[1])).toBe(true); // "um"
    expect(st().isWordCut(WORDS[0])).toBe(false); // "hello"
  });

  test("restoreCutSpan removes one span by start", () => {
    st().enableFillerRemoval();
    const filler = st().exportSettings.cutSpans.find((s) => s.kind === "filler");
    st().restoreCutSpan(filler.start);
    expect(st().exportSettings.cutSpans.some((s) => s.start === filler.start)).toBe(false);
    expect(st().isWordCut(WORDS[1])).toBe(false); // restored
  });

  test("disable clears back to null (off)", () => {
    st().enableFillerRemoval();
    st().disableFillerRemoval();
    expect(st().exportSettings.cutSpans).toBeNull();
    expect(st().isFillerRemovalOn()).toBe(false);
  });

  test("enable is one undo frame", () => {
    st().enableFillerRemoval();
    expect(st().isFillerRemovalOn()).toBe(true);
    st().undo();
    expect(st().exportSettings.cutSpans).toBeNull();
  });
});

describe("export wire", () => {
  test("cut spans → cut_spans [[start,end]] pairs", () => {
    const req = buildRerenderRequest({
      cutSpans: [[0.6, 0.9], [1.0, 2.9]],
    });
    expect(req.cut_spans).toEqual([[0.6, 0.9], [1.0, 2.9]]);
  });

  test("null / empty omits the field (byte-identical payload)", () => {
    expect("cut_spans" in buildRerenderRequest({ cutSpans: null })).toBe(false);
    expect("cut_spans" in buildRerenderRequest({ cutSpans: [] })).toBe(false);
    expect("cut_spans" in buildRerenderRequest({})).toBe(false);
  });
});
