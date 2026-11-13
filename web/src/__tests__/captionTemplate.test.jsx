/**
 * F2 — saved caption template ("My Style").
 *
 * ONE named style {name, presetId, font, fontSize, pill, x, y} per user,
 * persisted via GET/PUT /users/me/caption-template. Apply order contract:
 * a clip's OWN restored draft ALWAYS wins; the template fills only a clip
 * whose draft load confirmed empty. Unknown/junk template fields degrade to
 * defaults instead of corrupting the document.
 */
import { useAppStore } from "@/store/useAppStore";
import { createEmptyTranscriptEdits } from "@/lib/transcriptEdits";
import { loadDraft, saveDraft } from "@/api/clips";
import {
  getTranscript,
  getSegmentSidecar,
  buildClipTranscript,
  isMultiSegmentClip,
  canRemapMultiSegment,
} from "@/api/transcripts";
import { getCaptionTemplate, putCaptionTemplate } from "@/api/templates";
import { CAPTION_DEFAULT_POSITION } from "@/api/renders";

jest.mock("@/api/clips", () => {
  const actual = jest.requireActual("@/api/clips");
  return {
    ...actual,
    loadDraft: jest.fn(),
    saveDraft: jest.fn(),
    generateClipMetadata: jest.fn(),
  };
});
jest.mock("@/api/transcripts", () => ({
  getTranscript: jest.fn(),
  getSegmentSidecar: jest.fn(),
  buildClipTranscript: jest.fn(),
  isMultiSegmentClip: jest.fn(),
  canRemapMultiSegment: jest.fn(),
}));
jest.mock("@/api/templates", () => ({
  getCaptionTemplate: jest.fn(),
  putCaptionTemplate: jest.fn(),
}));

const CLIP_A = { id: "clipA", jobId: "job1", index: 0, start: 0, end: 12, duration: 12, verticalPath: "", segments: [] };
const CLIP_B = { id: "clipB", jobId: "job1", index: 1, start: 20, end: 30, duration: 10, verticalPath: "", segments: [] };
const WORDS = [
  { id: "w_flat_0", ref: { type: "flat", index: 0 }, text: "ఒకటి", start: 0, end: 0.5 },
];

const TEMPLATE = {
  name: "Punchy",
  presetId: "hormozi",
  font: "Ramabhadra",
  fontSize: 0.07,
  pill: { enabled: true, color: "#112233", opacity: 0.4, padding: 12, radius: 10 },
  x: 0.5,
  y: 0.3,
};

const PRISTINE_ELEMENTS = JSON.parse(JSON.stringify(useAppStore.getState().elements));
const PRISTINE_EXPORT = { ...useAppStore.getState().exportSettings };

const st = () => useAppStore.getState();
const caption = () => st().elements.find((el) => el.type === "caption");

beforeEach(() => {
  st().resetHistory();
  useAppStore.setState({
    currentClipId: null,
    currentClip: null,
    currentJobId: null,
    clipsByJob: { job1: [CLIP_A, CLIP_B] },
    projects: [{ id: "job1", videoId: "vid1" }],
    transcript: [],
    transcriptStatus: "idle",
    transcriptEdits: createEmptyTranscriptEdits(),
    elements: JSON.parse(JSON.stringify(PRISTINE_ELEMENTS)),
    exportSettings: { ...PRISTINE_EXPORT },
    selectedElementId: "el_caption_1",
    draftStatus: "idle",
    draftLoadStatus: "idle",
    draftVersions: {},
    captionTemplate: null,
    captionTemplateLoaded: false,
    captionTemplateSaving: false,
    duration: 0,
    currentTime: 0,
  });
  getTranscript.mockResolvedValue({});
  getSegmentSidecar.mockResolvedValue(null);
  buildClipTranscript.mockReturnValue(WORDS);
  isMultiSegmentClip.mockReturnValue(false);
  canRemapMultiSegment.mockReturnValue(false);
  loadDraft.mockResolvedValue(null);
  saveDraft.mockResolvedValue({ ok: true });
  getCaptionTemplate.mockResolvedValue(null);
  putCaptionTemplate.mockResolvedValue(TEMPLATE);
});

describe("auto-apply on clean clips only", () => {
  test("a clip with NO draft starts from the saved style", async () => {
    getCaptionTemplate.mockResolvedValue(TEMPLATE);
    await st().openClip("clipA");
    const c = caption();
    expect(c.props.presetId).toBe("hormozi");
    expect(c.props.font).toBe("Ramabhadra");
    expect(c.props.fontSize).toBe(0.07);
    expect(c.props.pill).toMatchObject({ enabled: true, color: "#112233" });
    expect(c.y).toBe(0.3);
    // Starting state, not an edit: no undo frame, autosave-armable as usual.
    expect(st().history.past).toHaveLength(0);
    expect(st().draftLoadStatus).toBe("ready");
  });

  test("a clip WITH a draft keeps its draft — the template never overrides it", async () => {
    getCaptionTemplate.mockResolvedValue(TEMPLATE);
    const draftCaption = {
      ...JSON.parse(JSON.stringify(PRISTINE_ELEMENTS.find((el) => el.type === "caption"))),
      y: 0.6,
    };
    draftCaption.props.presetId = "red-pop";
    loadDraft.mockResolvedValue({ elements: [draftCaption] });
    await st().openClip("clipA");
    expect(caption().props.presetId).toBe("red-pop");
    expect(caption().y).toBe(0.6);
    expect(caption().props.font).not.toBe("Ramabhadra");
  });

  test("no template + no draft → plain defaults", async () => {
    await st().openClip("clipA");
    expect(caption().props.presetId).toBe("bold-yellow");
    // The only clip that picks up the default anchor: no draft AND no template.
    expect(caption().y).toBe(CAPTION_DEFAULT_POSITION.y);
  });

  test("a draft sitting at the OLD default is NOT pulled to the new one", async () => {
    // The constraint that matters when the default moves: a stored caption
    // position is authoritative, whatever it happens to equal. A draft saved
    // at the pre-move 0.82 must stay at 0.82 — and, because it now differs
    // from the default, must ship caption_y so the burn honours it too.
    const draftCaption = {
      ...JSON.parse(JSON.stringify(PRISTINE_ELEMENTS.find((el) => el.type === "caption"))),
      y: 0.82,
    };
    loadDraft.mockResolvedValue({ elements: [draftCaption] });
    await st().openClip("clipA");
    expect(caption().y).toBe(0.82);
    expect(st().getEditDocument().captionY).toBe(0.82);
  });

  test("the template is fetched once per session, then served from cache", async () => {
    getCaptionTemplate.mockResolvedValue(TEMPLATE);
    await st().openClip("clipA");
    await st().openClip("clipB");
    expect(getCaptionTemplate).toHaveBeenCalledTimes(1);
    expect(caption().props.presetId).toBe("hormozi"); // clip B also clean → styled
  });

  test("a failed template fetch degrades to defaults and is retried next open", async () => {
    getCaptionTemplate.mockRejectedValue(new Error("boom"));
    await st().openClip("clipA");
    expect(caption().props.presetId).toBe("bold-yellow");
    expect(st().captionTemplateLoaded).toBe(false); // not latched → retry later
    expect(st().draftLoadStatus).toBe("ready"); // clip still fully usable
  });
});

describe("junk-tolerant apply", () => {
  test("unknown preset/font and out-of-range coords are ignored field-by-field", async () => {
    getCaptionTemplate.mockResolvedValue({
      name: "Stale",
      presetId: "retired-style",
      font: "Comic Sans",
      fontSize: 7, // not a 0–1 canvas fraction
      x: 42,
      y: 0.25, // valid
    });
    await st().openClip("clipA");
    const c = caption();
    expect(c.props.presetId).toBe("bold-yellow");
    expect(c.props.font).toBe("Noto Sans Telugu");
    expect(c.props.fontSize).toBe(0.055);
    expect(c.x).toBe(0.5);
    expect(c.y).toBe(0.25); // the one valid field still applies
  });
});

describe("saving and clearing the style", () => {
  test("saveMyStyle snapshots the current caption element and persists it", async () => {
    st().updateElementProps("el_caption_1", { presetId: "hormozi", font: "Mandali", fontSize: 0.065 });
    st().updateElement("el_caption_1", { y: 0.4 });
    await st().saveMyStyle("  Punchy  ");
    expect(putCaptionTemplate).toHaveBeenCalledTimes(1);
    const sent = putCaptionTemplate.mock.calls[0][0];
    expect(sent).toMatchObject({
      name: "Punchy",
      presetId: "hormozi",
      font: "Mandali",
      fontSize: 0.065,
      y: 0.4,
    });
    expect(st().captionTemplate).toEqual(sent);
    expect(st().captionTemplateLoaded).toBe(true);
  });

  test("a failed save rolls the cached template back", async () => {
    putCaptionTemplate.mockRejectedValue(new Error("500"));
    await st().saveMyStyle("Nope");
    expect(st().captionTemplate).toBeNull();
    expect(st().captionTemplateSaving).toBe(false);
  });

  test("clearMyStyle deletes server-side and locally", async () => {
    useAppStore.setState({ captionTemplate: TEMPLATE, captionTemplateLoaded: true });
    putCaptionTemplate.mockResolvedValue(null);
    await st().clearMyStyle();
    expect(putCaptionTemplate).toHaveBeenCalledWith(null);
    expect(st().captionTemplate).toBeNull();
  });

  test("manual re-apply to the current clip is one undoable action", () => {
    useAppStore.setState({ captionTemplate: TEMPLATE, captionTemplateLoaded: true });
    st().applyMyStyleToCurrentClip();
    expect(caption().props.presetId).toBe("hormozi");
    expect(st().history.past).toHaveLength(1);
    st().undo();
    expect(caption().props.presetId).toBe("bold-yellow");
  });
});
