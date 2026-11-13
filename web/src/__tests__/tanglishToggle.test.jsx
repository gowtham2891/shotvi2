/**
 * Telugu ⇄ Tanglish caption toggle — frontend regression suite.
 *
 * Pins the display-resolver order (edit's re-derived tanglish → word's stored
 * tanglish → Telugu fallback), the wire contract (caption_script sent only
 * when 'tanglish'), the edit seam (commit re-derives via POST /tanglish and
 * degrades to stale-fallback when the endpoint is down), undo/draft coverage
 * (captionScript rides exportSettings), and text_tanglish flowing through
 * buildClipTranscript.
 */
import { useAppStore } from "@/store/useAppStore";
import { buildRerenderRequest } from "@/api/renders";
import { getWordsForRange } from "@/api/transcripts";
import { fetchTanglish } from "@/api/tanglish";

jest.mock("@/api/tanglish", () => ({ fetchTanglish: jest.fn() }));

const flush = () => new Promise((r) => setTimeout(r, 0));

// Two-word clip transcript in the store shape buildClipTranscript produces.
const WORDS = [
  { id: "w_flat_0", ref: { type: "flat", index: 0 }, text: "దీన్ని", text_tanglish: "deenni", start: 0, end: 0.5 },
  { id: "w_flat_1", ref: { type: "flat", index: 1 }, text: "control", text_tanglish: "control", start: 0.5, end: 1 },
];

const resetStore = () => {
  useAppStore.setState({
    transcript: WORDS.map((w) => ({ ...w })),
    transcriptEdits: { wordEdits: {}, mergedGroups: [], lineSplits: [] },
    exportSettings: { ...useAppStore.getState().exportSettings, captionScript: "telugu" },
    history: { past: [], future: [] },
  });
};

beforeEach(() => {
  fetchTanglish.mockReset();
  fetchTanglish.mockResolvedValue(null);
  resetStore();
});

describe("displayWord resolver", () => {
  test("telugu view returns the effective (Telugu) text", () => {
    expect(useAppStore.getState().displayWord("w_flat_0")).toBe("దీన్ని");
  });

  test("tanglish view returns the word's stored text_tanglish", () => {
    useAppStore.getState().setExportSetting("captionScript", "tanglish");
    expect(useAppStore.getState().displayWord("w_flat_0")).toBe("deenni");
    expect(useAppStore.getState().displayWord("w_flat_1")).toBe("control");
  });

  test("edited word: the edit's re-derived tanglish wins over the stored one", () => {
    useAppStore.setState((s) => ({
      transcriptEdits: {
        ...s.transcriptEdits,
        wordEdits: { w_flat_0: { text: "చూడు", text_tanglish: "choodu" } },
      },
      exportSettings: { ...s.exportSettings, captionScript: "tanglish" },
    }));
    expect(useAppStore.getState().displayWord("w_flat_0")).toBe("choodu");
  });

  test("edited word with null tanglish (endpoint was down) falls back to the STORED word tanglish — degrade, never break", () => {
    useAppStore.setState((s) => ({
      transcriptEdits: {
        ...s.transcriptEdits,
        wordEdits: { w_flat_0: { text: "చూడు", text_tanglish: null } },
      },
      exportSettings: { ...s.exportSettings, captionScript: "tanglish" },
    }));
    expect(useAppStore.getState().displayWord("w_flat_0")).toBe("deenni");
  });

  test("word with no text_tanglish at all falls back to Telugu (never blank)", () => {
    useAppStore.setState((s) => ({
      transcript: [{ ...s.transcript[0], text_tanglish: null }],
      exportSettings: { ...s.exportSettings, captionScript: "tanglish" },
    }));
    expect(useAppStore.getState().displayWord("w_flat_0")).toBe("దీన్ని");
  });

  test("junk captionScript behaves as telugu", () => {
    useAppStore.setState((s) => ({
      exportSettings: { ...s.exportSettings, captionScript: "klingon" },
    }));
    expect(useAppStore.getState().displayWord("w_flat_0")).toBe("దీన్ని");
  });

  test("toggle is lossless — flip there and back restores identical display", () => {
    const st = () => useAppStore.getState();
    const before = WORDS.map((w) => st().displayWord(w.id));
    st().setExportSetting("captionScript", "tanglish");
    st().setExportSetting("captionScript", "telugu");
    expect(WORDS.map((w) => st().displayWord(w.id))).toEqual(before);
  });
});

describe("wire contract (buildRerenderRequest)", () => {
  test("caption_script sent only when tanglish", () => {
    expect(buildRerenderRequest({ captionScript: "tanglish" }).caption_script).toBe("tanglish");
    expect(buildRerenderRequest({ captionScript: "telugu" })).not.toHaveProperty("caption_script");
    expect(buildRerenderRequest({})).not.toHaveProperty("caption_script");
    expect(buildRerenderRequest({ captionScript: "junk" })).not.toHaveProperty("caption_script");
  });

  test("telugu payload is byte-identical to a pre-toggle payload", () => {
    expect(buildRerenderRequest({ captionScript: "telugu" })).toEqual(buildRerenderRequest({}));
  });
});

describe("edit seam (setWordEdit → POST /tanglish)", () => {
  test("commit re-derives the word's tanglish asynchronously", async () => {
    fetchTanglish.mockResolvedValue(["choodu"]);
    useAppStore.getState().setWordEdit("w_flat_0", "చూడు");
    expect(fetchTanglish).toHaveBeenCalledWith(["చూడు"]);
    await flush();
    const edit = useAppStore.getState().transcriptEdits.wordEdits.w_flat_0;
    expect(edit).toEqual({ text: "చూడు", text_tanglish: "choodu" });
  });

  test("endpoint down → text_tanglish stays null, the edit itself commits fine", async () => {
    fetchTanglish.mockResolvedValue(null);
    useAppStore.getState().setWordEdit("w_flat_0", "చూడు");
    await flush();
    const edit = useAppStore.getState().transcriptEdits.wordEdits.w_flat_0;
    expect(edit).toEqual({ text: "చూడు", text_tanglish: null });
  });

  test("stale response is dropped when the edit changed while in flight", async () => {
    let resolveFirst;
    fetchTanglish.mockReturnValueOnce(new Promise((r) => (resolveFirst = r)));
    fetchTanglish.mockResolvedValueOnce(["shakti"]);
    useAppStore.getState().setWordEdit("w_flat_0", "చూడు");
    useAppStore.getState().setWordEdit("w_flat_0", "శక్తి");
    resolveFirst(["choodu"]); // late reply for the superseded text
    await flush();
    const edit = useAppStore.getState().transcriptEdits.wordEdits.w_flat_0;
    expect(edit.text).toBe("శక్తి");
    expect(edit.text_tanglish).toBe("shakti");
  });

  test("clearing an edit never calls the derivation endpoint", () => {
    useAppStore.getState().setWordEdit("w_flat_0", "చూడు");
    fetchTanglish.mockClear();
    useAppStore.getState().setWordEdit("w_flat_0", "దీన్ని"); // back to original → clears
    expect(fetchTanglish).not.toHaveBeenCalled();
    expect(useAppStore.getState().transcriptEdits.wordEdits.w_flat_0).toBeUndefined();
  });
});

describe("document integration", () => {
  test("captionScript sits in undo history (documented flip is undoable)", () => {
    const st = () => useAppStore.getState();
    st().setExportSetting("captionScript", "tanglish");
    expect(st().exportSettings.captionScript).toBe("tanglish");
    st().undo();
    expect(st().exportSettings.captionScript).toBe("telugu");
    st().redo();
    expect(st().exportSettings.captionScript).toBe("tanglish");
  });

  test("captionScript rides exportSettings through getEditDocument and applyDraft (draft reload survives)", () => {
    const st = () => useAppStore.getState();
    st().setExportSetting("captionScript", "tanglish");
    const doc = st().getEditDocument();
    expect(doc.exportSettings.captionScript).toBe("tanglish");
    st().setExportSetting("captionScript", "telugu");
    st().applyDraft({ exportSettings: doc.exportSettings });
    expect(st().exportSettings.captionScript).toBe("tanglish");
  });
});

describe("transcript build carries the tanglish sibling", () => {
  test("getWordsForRange maps word_tanglish → text_tanglish (null when absent — old clip before backfill)", () => {
    const transcript = {
      word_timestamps: [
        { word: "దీన్ని", word_tanglish: "deenni", start: 0, end: 0.5 },
        { word: "పంపు", start: 0.5, end: 1 }, // no word_tanglish stored
      ],
    };
    const words = getWordsForRange(transcript, 0, 1);
    expect(words[0].text_tanglish).toBe("deenni");
    expect(words[1].text_tanglish).toBeNull();
  });
});
