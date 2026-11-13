/**
 * A4 + B1 — Inspector cleanup gate.
 *
 * B1: the Music tab was entirely cosmetic (selectedTrackId/musicVolume never
 * reached startExport or buildRerenderRequest) — it is fully deleted: UI,
 * mock track library, and store state.
 * A4: the Export tab's fake "Format" row wrote container strings ("mp4") into
 * exportSettings.format — the key that stores the ASPECT — and the fake
 * "Resolution" row wrote a dead key. Both rows are deleted; the wire contract
 * additionally degrades a corrupted aspect to the default.
 */
import React from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { useAppStore } from "@/store/useAppStore";
import { createEmptyTranscriptEdits } from "@/lib/transcriptEdits";
import { buildRerenderRequest } from "@/api/renders";
import { EDITOR } from "@/constants/testIds";
import * as mockData from "@/data/mockData";
import Inspector from "@/components/editor/Inspector";

const PRISTINE_ELEMENTS = JSON.parse(JSON.stringify(useAppStore.getState().elements));
const PRISTINE_EXPORT = { ...useAppStore.getState().exportSettings };

const renderInspector = () =>
  renderToString(
    <MemoryRouter>
      <Inspector />
    </MemoryRouter>
  );

beforeEach(() => {
  useAppStore.getState().resetHistory();
  useAppStore.setState({
    transcript: [],
    transcriptEdits: createEmptyTranscriptEdits(),
    elements: JSON.parse(JSON.stringify(PRISTINE_ELEMENTS)),
    exportSettings: { ...PRISTINE_EXPORT },
    selectedElementId: "el_caption_1",
    currentClipId: "clipA",
    captionTemplate: null,
    captionTemplateSaving: false,
  });
});

describe("B1 — Music removal", () => {
  test("the Music tab and its test ids are gone", () => {
    const html = renderInspector();
    expect(html).not.toContain("editor-tab-music");
    expect(html).not.toContain("Music");
    expect(EDITOR.tabMusic).toBeUndefined();
    expect(EDITOR.musicItem).toBeUndefined();
  });

  test("the mock track library is deleted", () => {
    expect(mockData.MUSIC_LIBRARY).toBeUndefined();
  });

  test("the dead store state is deleted", () => {
    const s = useAppStore.getState();
    expect(s.selectedTrackId).toBeUndefined();
    expect(s.setSelectedTrack).toBeUndefined();
    expect(s.musicVolume).toBeUndefined();
    expect(s.setMusicVolume).toBeUndefined();
  });

  test("the export payload is unchanged otherwise (no music fields ever)", () => {
    const req = buildRerenderRequest({});
    expect(Object.keys(req).sort()).toEqual(
      ["background", "bg_color", "crop_mode", "format", "style", "trim_end", "trim_start", "use_autocrop"].sort()
    );
  });
});

describe("A4 — fake Export-tab rows are gone; the aspect survives", () => {
  test("the Style tab renders; container/resolution affordances do not exist anywhere", () => {
    const html = renderInspector();
    expect(html).toContain(EDITOR.tabStyle);
    expect(html).toContain(EDITOR.tabExport);
    expect(html).not.toContain("Resolution");
    expect(html).not.toContain("webm");
    expect(html).not.toContain("720p");
  });

  test("interacting with the remaining export controls never touches the aspect", () => {
    const s = useAppStore.getState();
    expect(s.exportSettings.format).toBe("9:16");
    s.setExportSetting("burnInCaptions", false); // the surviving Export-tab toggle
    expect(useAppStore.getState().exportSettings.format).toBe("9:16");
  });

  test("wire contract: a container string corrupting `format` (old drafts) degrades to the default aspect", () => {
    const req = buildRerenderRequest({ format: "mp4" });
    expect(req.format).toBe("9:16");
  });

  test("the My Style section replaced it on the Style tab", () => {
    const html = renderInspector();
    expect(html).toContain(EDITOR.myStyleSave);
  });
});
