/**
 * Tanglish view: caption editing shows and accepts TANGLISH, never Telugu.
 *
 * Telugu stays the stored source of truth — these tests pin the commit-time
 * resolution (typed romanization → Telugu via picked suggestion /
 * /transliterate top-1 / typed-script-as-is), the unchanged-token no-op rule,
 * the verbatim-spelling rule (the user's typed tanglish wins over
 * re-derivation), and the pending-Telugu degrade when /transliterate is
 * unreachable at commit (edit preserved, resolves async / on retry).
 */

import { resolveTokensToTelugu } from "@/lib/lineEdit";
import {
  createEmptyTranscriptEdits,
  sanitizeTranscriptEdits,
  serializeTranscriptEdits,
} from "@/lib/transcriptEdits";
import { useAppStore } from "@/store/useAppStore";
import { fetchTransliterations } from "@/api/transliterate";
import { client } from "@/api/client";

jest.mock("@/api/client", () => ({
  ...jest.requireActual("@/api/client"),
  client: { post: jest.fn() },
}));

const WORDS = [
  {
    id: "w_flat_0",
    ref: { type: "flat", index: 0 },
    text: "రెండు",
    text_tanglish: "rendu",
    start: 0,
    end: 0.5,
  },
  {
    id: "w_flat_1",
    ref: { type: "flat", index: 1 },
    text: "ముక్కలు",
    text_tanglish: "mukkalu",
    start: 0.5,
    end: 1.0,
  },
];

const freshStore = (captionScript = "tanglish") => {
  useAppStore.setState({
    transcript: WORDS,
    transcriptEdits: createEmptyTranscriptEdits(),
    history: { past: [], future: [] },
    exportSettings: { ...useAppStore.getState().exportSettings, captionScript },
  });
};

beforeEach(() => {
  jest.clearAllMocks();
  client.post.mockResolvedValue({ data: {} });
  freshStore();
});

// ── resolveTokensToTelugu — the commit-time resolution table ────────

describe("resolveTokensToTelugu", () => {
  const translit = jest.fn();

  beforeEach(() => translit.mockReset());

  test("Telugu script typed directly is used as-is — no service call", async () => {
    const [r] = await resolveTokensToTelugu(["కొత్త"], new Map(), translit);
    expect(r).toEqual({ telugu: "కొత్త", tanglish: null, pending: false });
    expect(translit).not.toHaveBeenCalled();
  });

  test("a known display tanglish reuses its word's Telugu — no round-trip", async () => {
    const known = new Map([["rendu", "రెండు"]]);
    const [r] = await resolveTokensToTelugu(["rendu"], known, translit);
    expect(r).toEqual({ telugu: "రెండు", tanglish: "rendu", pending: false });
    expect(translit).not.toHaveBeenCalled();
  });

  test("unknown Latin resolves to /transliterate top-1, typed spelling kept", async () => {
    translit.mockResolvedValue(["ముక్కలు", "ముకలు"]);
    const [r] = await resolveTokensToTelugu(["mukkalu"], new Map(), translit);
    expect(r).toEqual({ telugu: "ముక్కలు", tanglish: "mukkalu", pending: false });
    expect(translit).toHaveBeenCalledWith("mukkalu");
  });

  test("empty suggestions → pending (typed tanglish preserved)", async () => {
    translit.mockResolvedValue([]);
    const [r] = await resolveTokensToTelugu(["unnai"], new Map(), translit);
    expect(r).toEqual({ telugu: null, tanglish: "unnai", pending: true });
  });

  test("a throwing transliterate degrades to pending, never rejects", async () => {
    translit.mockRejectedValue(new Error("down"));
    const [r] = await resolveTokensToTelugu(["unnai"], new Map(), translit);
    expect(r).toEqual({ telugu: null, tanglish: "unnai", pending: true });
  });

  test("mixed token list resolves per token, order-preserving", async () => {
    translit.mockResolvedValue(["కొత్తది"]);
    const out = await resolveTokensToTelugu(
      ["కొత్త", "rendu", "kottadi"],
      new Map([["rendu", "రెండు"]]),
      translit
    );
    expect(out.map((r) => r.telugu)).toEqual(["కొత్త", "రెండు", "కొత్తది"]);
    expect(translit).toHaveBeenCalledTimes(1); // only the unknown Latin token
  });
});

// ── setWordEditsBatch — tanglish entries ────────────────────────────

describe("setWordEditsBatch with typed-tanglish entries", () => {
  test("picked/resolved commit stores Telugu + the TYPED tanglish verbatim; no /tanglish re-derivation", async () => {
    useAppStore.getState().setWordEditsBatch([
      { id: "w_flat_0", text: "రెండున్నర", text_tanglish: "rendunnara" },
    ]);
    const s = useAppStore.getState();
    expect(s.transcriptEdits.wordEdits["w_flat_0"]).toEqual({
      text: "రెండున్నర",
      text_tanglish: "rendunnara",
    });
    expect(s.history.past).toHaveLength(1);
    // verbatim spelling wins — the /tanglish derivation endpoint is not asked
    await Promise.resolve();
    expect(client.post).not.toHaveBeenCalledWith("/tanglish", expect.anything());
    // resolvers: Telugu view sees the new Telugu, Tanglish view the typed spelling
    expect(s.effectiveWord("w_flat_0")).toBe("రెండున్నర");
    expect(s.displayWord("w_flat_0")).toBe("rendunnara");
  });

  test("both scripts matching the pristine word is a no-op (no phantom frame, no spurious edit)", () => {
    useAppStore.getState().setWordEditsBatch([
      { id: "w_flat_0", text: "రెండు", text_tanglish: "rendu" },
    ]);
    const s = useAppStore.getState();
    expect(s.transcriptEdits.wordEdits).toEqual({});
    expect(s.history.past).toHaveLength(0);
  });

  test("same Telugu with a DIFFERENT typed spelling persists (display delta — their spelling wins)", () => {
    useAppStore.getState().setWordEditsBatch([
      { id: "w_flat_0", text: "రెండు", text_tanglish: "rendhu" },
    ]);
    const s = useAppStore.getState();
    expect(s.transcriptEdits.wordEdits["w_flat_0"]).toEqual({
      text: "రెండు",
      text_tanglish: "rendhu",
    });
    expect(s.effectiveWord("w_flat_0")).toBe("రెండు"); // Telugu unchanged
    expect(s.displayWord("w_flat_0")).toBe("rendhu");
  });

  test("pending commit: typed tanglish kept, NO Telugu yet — Telugu view falls back to the original source", () => {
    useAppStore.getState().setWordEditsBatch([
      { id: "w_flat_1", text: null, text_tanglish: "mukkalu2", pending: true },
    ]);
    const s = useAppStore.getState();
    expect(s.transcriptEdits.wordEdits["w_flat_1"]).toEqual({
      text: null,
      text_tanglish: "mukkalu2",
      pendingTelugu: true,
    });
    expect(s.history.past).toHaveLength(1); // one commit = one frame
    expect(s.displayWord("w_flat_1")).toBe("mukkalu2"); // tanglish view: typed text
    useAppStore.setState({
      exportSettings: { ...s.exportSettings, captionScript: "telugu" },
    });
    expect(useAppStore.getState().effectiveWord("w_flat_1")).toBe("ముక్కలు");
  });

  test("plain {id, text} entries keep the pre-existing Telugu-view semantics (re-derivation fires)", async () => {
    client.post.mockResolvedValue({ data: { tanglish: ["kotta"] } });
    useAppStore.getState().setWordEditsBatch([{ id: "w_flat_0", text: "కొత్త" }]);
    expect(useAppStore.getState().transcriptEdits.wordEdits["w_flat_0"].text).toBe("కొత్త");
    await new Promise((r) => setTimeout(r, 0)); // flush the fetchTanglish chain
    expect(client.post).toHaveBeenCalledWith("/tanglish", { words: ["కొత్త"] });
    expect(
      useAppStore.getState().transcriptEdits.wordEdits["w_flat_0"].text_tanglish
    ).toBe("kotta");
  });
});

// ── resolvePendingTelugu — the retry path ───────────────────────────

describe("resolvePendingTelugu", () => {
  const commitPending = () =>
    useAppStore.getState().setWordEditsBatch([
      { id: "w_flat_0", text: null, text_tanglish: "kotta", pending: true },
    ]);

  test("upgrades a pending word to top-1 Telugu WITHOUT a new history frame", async () => {
    commitPending();
    client.post.mockResolvedValue({ data: { suggestions: ["కొత్త", "కొత"] } });
    await useAppStore.getState().resolvePendingTelugu();
    const s = useAppStore.getState();
    expect(s.transcriptEdits.wordEdits["w_flat_0"]).toEqual({
      text: "కొత్త",
      text_tanglish: "kotta", // typed spelling untouched
    });
    expect(s.history.past).toHaveLength(1); // still just the commit frame
    expect(client.post).toHaveBeenCalledWith("/transliterate", {
      text: "kotta",
      lang: "te",
    });
  });

  test("service still down → stays pending for the next retry; editor state intact", async () => {
    commitPending();
    client.post.mockRejectedValue(new Error("still down"));
    await useAppStore.getState().resolvePendingTelugu();
    expect(useAppStore.getState().transcriptEdits.wordEdits["w_flat_0"]).toEqual({
      text: null,
      text_tanglish: "kotta",
      pendingTelugu: true,
    });
  });

  test("stale guard: a response never lands on an edit that changed in flight", async () => {
    commitPending();
    let release;
    client.post.mockReturnValue(new Promise((res) => (release = res)));
    const inFlight = useAppStore.getState().resolvePendingTelugu();
    // The user re-edits the word while the request is pending.
    useAppStore.getState().setWordEditsBatch([
      { id: "w_flat_0", text: null, text_tanglish: "vera", pending: true },
    ]);
    release({ data: { suggestions: ["కొత్త"] } });
    await inFlight;
    expect(useAppStore.getState().transcriptEdits.wordEdits["w_flat_0"]).toEqual({
      text: null,
      text_tanglish: "vera",
      pendingTelugu: true,
    });
  });

  test("no pending words → no network traffic", async () => {
    await useAppStore.getState().resolvePendingTelugu();
    expect(client.post).not.toHaveBeenCalled();
  });
});

// ── per-word unpicked-Latin commit (WordToken.commitTanglish) ────────
//
// Faithful replica of the component's commit orchestration for a Latin token
// the user typed but did NOT pick from the dropdown: resolve to Telugu top-1
// AT COMMIT through the shared resolver, storing the resolved Telugu with the
// typed spelling verbatim; degrade to pending ONLY on a genuine service
// failure. (Regression guard for the "healthy 200 discarded / word stuck
// Latin" bug — the old path always committed pending and leaned on the async
// retry.) No RTL in this repo, so the exact commitTanglish body is mirrored.
describe("unpicked Latin commit resolves at commit (commitTanglish)", () => {
  const flush = () => new Promise((r) => setTimeout(r, 0));

  // Mirrors EditableTranscript.jsx commitTanglish's Latin branch exactly.
  const commitUnpickedLatin = async (id, token) => {
    const store = useAppStore.getState();
    const [r] = await resolveTokensToTelugu([token], new Map(), fetchTransliterations);
    if (r.pending) {
      store.setWordEditsBatch([{ id, text: null, text_tanglish: r.tanglish, pending: true }]);
    } else {
      store.setWordEditsBatch([{ id, text: r.telugu, text_tanglish: r.tanglish }]);
    }
    store.resolvePendingTelugu();
  };

  test("mocked 200 → wordEdit carries top-1 Telugu + verbatim tanglish, NOT pending", async () => {
    client.post.mockResolvedValue({ data: { suggestions: ["మంచిది", "మంచిదీ"] } });
    await commitUnpickedLatin("w_flat_0", "manchidi");
    const s = useAppStore.getState();
    expect(s.transcriptEdits.wordEdits["w_flat_0"]).toEqual({
      text: "మంచిది",         // top-1 applied as the Telugu source of truth
      text_tanglish: "manchidi", // the user's typed spelling, verbatim
    });
    expect(s.transcriptEdits.wordEdits["w_flat_0"].pendingTelugu).toBeUndefined();
    expect(client.post).toHaveBeenCalledWith("/transliterate", {
      text: "manchidi",
      lang: "te",
    });
    // Both resolvers now agree without a reload — the reported symptom is gone.
    expect(s.effectiveWord("w_flat_0")).toBe("మంచిది"); // Telugu view
    expect(s.displayWord("w_flat_0")).toBe("manchidi"); // Tanglish view
  });

  test("mocked service failure → pending true (degrade preserved)", async () => {
    client.post.mockRejectedValue(new Error("service down"));
    await commitUnpickedLatin("w_flat_0", "manchidi");
    await flush();
    expect(useAppStore.getState().transcriptEdits.wordEdits["w_flat_0"]).toEqual({
      text: null,
      text_tanglish: "manchidi",
      pendingTelugu: true,
    });
  });

  test("a word stuck pending resolves on the NEXT commit (service recovered)", async () => {
    // w_flat_0 committed while the service was down → stuck pending.
    useAppStore.getState().setWordEditsBatch([
      { id: "w_flat_0", text: null, text_tanglish: "manchidi", pending: true },
    ]);
    // Service is back; the user commits a DIFFERENT word, which also retries
    // the stuck one via resolvePendingTelugu.
    client.post.mockImplementation((_url, body) =>
      Promise.resolve({
        data: { suggestions: { manchidi: ["మంచిది"], sagam: ["సగం"] }[body.text] || [] },
      })
    );
    await commitUnpickedLatin("w_flat_1", "sagam");
    await flush();
    const edits = useAppStore.getState().transcriptEdits.wordEdits;
    expect(edits["w_flat_1"]).toEqual({ text: "సగం", text_tanglish: "sagam" });
    // The previously-stuck word is now resolved — no reload required.
    expect(edits["w_flat_0"]).toEqual({ text: "మంచిది", text_tanglish: "manchidi" });
  });
});

// ── persistence: drafts + wire ──────────────────────────────────────

describe("pending-Telugu persistence", () => {
  test("pendingTelugu survives the draft round-trip (sanitize)", () => {
    const clean = sanitizeTranscriptEdits({
      wordEdits: {
        w_flat_0: { text: null, text_tanglish: "kotta", pendingTelugu: true },
        w_flat_1: { text: "కొత్త", text_tanglish: "kotta" },
      },
    });
    expect(clean.wordEdits.w_flat_0).toEqual({
      text: null,
      text_tanglish: "kotta",
      pendingTelugu: true,
    });
    // resolved edits keep their exact two-key shape — no flag creep
    expect(clean.wordEdits.w_flat_1).toEqual({ text: "కొత్త", text_tanglish: "kotta" });
  });

  test("pending words never reach the export wire (Telugu is the source of truth)", () => {
    const edits = createEmptyTranscriptEdits();
    edits.wordEdits.w_flat_0 = { text: null, text_tanglish: "kotta", pendingTelugu: true };
    edits.wordEdits.w_flat_1 = { text: "కొత్త", text_tanglish: "kotta" };
    const wire = serializeTranscriptEdits(edits);
    // Pending w_flat_0 stays off the wire; the resolved w_flat_1 ships its
    // typed romanization verbatim as word_tanglish.
    expect(wire.wordEdits).toEqual([
      { ref: { type: "flat", index: 1 }, word: "కొత్త", word_tanglish: "kotta" },
    ]);
  });

  test("setLineRealignmentTanglish fills only MISSING slots — typed spellings win", () => {
    const key = "0:1";
    useAppStore.getState().setLineRealignment(key, {
      startIdx: 0,
      endIdx: 1,
      words: [
        { word: "రెండు", start: 0, end: 0.5, word_tanglish: "rendhu" }, // typed verbatim
        { word: "ముక్కలు", start: 0.5, end: 1.0, word_tanglish: null }, // Telugu-typed token
      ],
      approximate: true,
    });
    useAppStore.getState().setLineRealignmentTanglish(key, ["rendu", "mukkalu"]);
    const rec = useAppStore.getState().transcriptEdits.lineRealignments[key];
    expect(rec.words.map((w) => w.word_tanglish)).toEqual(["rendhu", "mukkalu"]);
  });
});
