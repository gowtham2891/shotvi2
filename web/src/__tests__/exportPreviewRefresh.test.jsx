/**
 * Regression test for a real bug report: the export page's preview player
 * (and the editor canvas) kept showing the old render after a rerender
 * completed successfully, even though the downloaded file had the new
 * edits. Root cause: previewUrl/videoUrl were computed once from the
 * ORIGINAL pipeline ClipOut and never refreshed after a rerender — every
 * consumer read that same stale clip object. Secondary risk: the backend
 * derives rerender output filenames deterministically from export settings,
 * so re-exporting with identical settings overwrites the same path/URL,
 * which a browser could serve from cache without a cache-busting token.
 */
import { useAppStore } from "@/store/useAppStore";
import { API_BASE_URL } from "@/api/client";

const baseClip = {
  id: "abc123_c1",
  jobId: "job-1",
  index: 0,
  captionedPath: "storage\\outputs\\abc123_c1_bold-yellow_captioned.mp4",
  verticalPath: "storage\\outputs\\abc123_c1_vertical.mp4",
  rawPath: "storage\\outputs\\abc123_c1_raw.mp4",
  previewUrl: `${API_BASE_URL}/outputs/abc123_c1_bold-yellow_captioned.mp4`,
  // Sprint 4: the editor canvas plays the 16:9 MASTER (raw_path). The
  // immutability contract below is unchanged — videoUrl is set once per clip
  // (mapClipToUi) and never refreshed by rerenders.
  videoUrl: `${API_BASE_URL}/outputs/abc123_c1_raw.mp4`,
};

function seedStore() {
  useAppStore.setState({
    currentClip: { ...baseClip },
    clipsByJob: { "job-1": [{ ...baseClip }] },
    exportTargetClipId: baseClip.id,
  });
}

describe("applyExportUpdate refreshes the rerendered clip's preview URL only", () => {
  beforeEach(seedStore);

  test("on completion, currentClip.previewUrl points at the NEW rerender output", () => {
    const rerenderJob = {
      job_id: "rerender-job-42",
      status: "done",
      captioned_path: "storage\\outputs\\abc123_c1_vertical_9_16_blur_canvas_red-pop_captioned.mp4",
      vertical_path: "storage\\outputs\\abc123_c1_vertical_9_16_blur_canvas.mp4",
    };
    useAppStore.getState().applyExportUpdate(rerenderJob);

    const clip = useAppStore.getState().currentClip;
    expect(clip.previewUrl).toContain("abc123_c1_vertical_9_16_blur_canvas_red-pop_captioned.mp4");
    // Never the stale original-pipeline paths.
    expect(clip.previewUrl).not.toContain("bold-yellow_captioned.mp4");
  });

  test("videoUrl stays pointed at the pipeline's ORIGINAL 16:9 master after rerender", () => {
    // Real-world bug from a user: after export they returned to the editor
    // and saw TWO stickers on the canvas — one that could be moved (the live
    // overlay element), one that could NOT (baked into the rerender's
    // vertical_path, which is the post-overlay pre-caption composite).
    // Fix: applyExportUpdate must NOT overwrite videoUrl; the editor canvas
    // stays pointed at the pipeline's clean source (Sprint 4: the 16:9
    // master, raw_path), which never has overlays burned into pixels.
    // See useAppStore.js applyExportUpdate for the fix.
    const originalVideoUrl = useAppStore.getState().currentClip.videoUrl;
    useAppStore.getState().applyExportUpdate({
      job_id: "rerender-job-42",
      status: "done",
      captioned_path: "storage\\outputs\\abc123_c1_vertical_9_16_blur_canvas_red-pop_captioned.mp4",
      vertical_path: "storage\\outputs\\abc123_c1_vertical_9_16_blur_canvas.mp4", // <- HAS overlays baked in
    });
    const clip = useAppStore.getState().currentClip;
    expect(clip.videoUrl).toBe(originalVideoUrl);
    // Extra belt-and-braces: the rerender's post-overlay filename must NOT
    // appear in videoUrl — that was the exact substring the editor canvas
    // used to render, which produced the double-sticker/double-progress-bar.
    expect(clip.videoUrl).not.toContain("_9_16_blur_canvas");
  });

  test("the new previewUrl is cache-busted with the rerender job id", () => {
    const rerenderJob = {
      job_id: "rerender-job-42",
      status: "done",
      captioned_path: "storage\\outputs\\abc123_c1_captioned.mp4",
      vertical_path: "storage\\outputs\\abc123_c1_vertical.mp4",
    };
    useAppStore.getState().applyExportUpdate(rerenderJob);
    const clip = useAppStore.getState().currentClip;
    expect(clip.previewUrl).toMatch(/\?v=rerender-job-42$/);
    // videoUrl is untouched — no query string added (see above test for why).
    expect(clip.videoUrl).toBe(baseClip.videoUrl);
  });

  test("two rerenders with identical settings (same deterministic filename) still bust the preview differently", () => {
    const sameFilenameBothTimes = {
      captioned_path: "storage\\outputs\\abc123_c1_vertical_9_16_blur_canvas_red-pop_captioned.mp4",
      vertical_path: "storage\\outputs\\abc123_c1_vertical_9_16_blur_canvas.mp4",
      status: "done",
    };
    useAppStore.getState().applyExportUpdate({ ...sameFilenameBothTimes, job_id: "job-A" });
    const first = useAppStore.getState().currentClip.previewUrl;
    useAppStore.getState().applyExportUpdate({ ...sameFilenameBothTimes, job_id: "job-B" });
    const second = useAppStore.getState().currentClip.previewUrl;

    expect(first).not.toBe(second); // different query string -> browser must refetch
    expect(first.split("?")[0]).toBe(second.split("?")[0]); // same underlying file path
  });

  test("also refreshes the clip inside clipsByJob (so re-opening the gallery/editor sees it too)", () => {
    useAppStore.getState().applyExportUpdate({
      job_id: "rerender-job-42",
      status: "done",
      captioned_path: "storage\\outputs\\abc123_c1_new_captioned.mp4",
      vertical_path: "storage\\outputs\\abc123_c1_new_vertical.mp4",
    });
    const cached = useAppStore.getState().clipsByJob["job-1"][0];
    expect(cached.previewUrl).toContain("abc123_c1_new_captioned.mp4");
    // videoUrl stays as the pipeline's original master — editor never sees the burned file.
    expect(cached.videoUrl).toBe(baseClip.videoUrl);
  });

  test("does not touch other clips in the same job", () => {
    useAppStore.setState((s) => ({
      clipsByJob: {
        "job-1": [...s.clipsByJob["job-1"], { ...baseClip, id: "abc123_c2", index: 1 }],
      },
    }));
    useAppStore.getState().applyExportUpdate({
      job_id: "rerender-job-42",
      status: "done",
      captioned_path: "storage\\outputs\\abc123_c1_new_captioned.mp4",
      vertical_path: "storage\\outputs\\abc123_c1_new_vertical.mp4",
    });
    const untouched = useAppStore.getState().clipsByJob["job-1"].find((c) => c.id === "abc123_c2");
    expect(untouched.previewUrl).toBe(baseClip.previewUrl); // unchanged
    expect(untouched.videoUrl).toBe(baseClip.videoUrl);
  });

  test("a failed rerender does not touch the clip's preview or video URLs", () => {
    useAppStore.getState().applyExportUpdate({ job_id: "x", status: "failed", error: "boom" });
    const clip = useAppStore.getState().currentClip;
    expect(clip.previewUrl).toBe(baseClip.previewUrl);
    expect(clip.videoUrl).toBe(baseClip.videoUrl);
  });
});
