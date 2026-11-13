/**
 * Regression: cold deep-link to /editor/:clipId (or /export/:clipId) in a
 * browser whose localStorage project registry doesn't know the job —
 * another device, cleared storage, or a job created outside the upload flow
 * (e.g. POST /jobs/recover). resolveClip used to search only the in-memory
 * clip cache and the registry, so a perfectly valid owned clip resolved to
 * "Clip not found". Fix: fall back to the caller's backend job list
 * (GET /jobs — the same ownership-enforced population the dashboard shows)
 * and adopt the hit into the registry for future cold loads.
 */
import { useAppStore } from "@/store/useAppStore";
import { listJobs, getJob } from "@/api/uploads";
import { listProjects, upsertProject, getProjectEntry } from "@/lib/projectRegistry";

jest.mock("@/api/uploads", () => {
  const actual = jest.requireActual("@/api/uploads");
  return { ...actual, listJobs: jest.fn(), getJob: jest.fn() };
});
jest.mock("@/lib/projectRegistry", () => {
  const actual = jest.requireActual("@/lib/projectRegistry");
  return {
    ...actual,
    listProjects: jest.fn(),
    upsertProject: jest.fn(),
    getProjectEntry: jest.fn(),
  };
});

const OWNED_JOB = {
  job_id: "job-owned-1",
  status: "done",
  progress: 100,
  current_stage: "Complete",
  video_id: "vidABC123",
  clips: [
    {
      clip_id: "vidABC123_c1",
      rank: 1,
      why: "hook",
      hook_text: "",
      virality_score: 9,
      engagement_type: "",
      start: 0,
      end: 30,
      duration: 30,
      raw_path: "storage/outputs/vidABC123_clip1_t.mp4",
      vertical_path: "storage/outputs/vidABC123_clip1_t_vertical.mp4",
      captioned_path: "",
      default_crop_box: { x: 0.25, y: 0, w: 0.28125, h: 1 },
    },
  ],
};

// CRA resetMocks wipes factory implementations between tests — install the
// in-memory registry double (EMPTY: the cold-browser premise) per test.
let entries;
beforeEach(() => {
  useAppStore.setState({ clipsByJob: {} });
  entries = [];
  listProjects.mockImplementation(() => entries);
  upsertProject.mockImplementation((e) => {
    const i = entries.findIndex((x) => x.jobId === e.jobId);
    if (i >= 0) entries[i] = { ...entries[i], ...e };
    else entries.push({ ...e });
  });
  getProjectEntry.mockImplementation(
    (jobId) => entries.find((e) => e.jobId === jobId) || null
  );
});

describe("resolveClip cold-deep-link fallback", () => {
  test("registry empty → falls back to GET /jobs, finds the owned clip, adopts registry entry", async () => {
    listJobs.mockResolvedValue([OWNED_JOB]);

    const found = await useAppStore.getState().resolveClip("vidABC123_c1");

    expect(found).toBeTruthy();
    expect(found.jobId).toBe("job-owned-1");
    expect(found.clip.id).toBe("vidABC123_c1");
    // Sprint 4 fields survive the fallback mapping.
    expect(found.clip.defaultCropBox).toEqual({ x: 0.25, y: 0, w: 0.28125, h: 1 });
    expect(found.clip.videoUrl).toContain("vidABC123_clip1_t.mp4"); // the master

    // The clip cache warmed so the gallery/editor share the same object…
    expect(useAppStore.getState().clipsByJob["job-owned-1"]).toHaveLength(1);
    // …and the registry adopted the job (videoId included) so openClip's
    // transcript lookup and FUTURE cold loads resolve without the fallback.
    expect(getProjectEntry("job-owned-1")).toMatchObject({ videoId: "vidABC123" });
    // The old paths were tried first: registry was consulted and empty.
    expect(listProjects).toHaveBeenCalled();
  });

  test("clip genuinely absent from the backend list → still resolves to null", async () => {
    listJobs.mockResolvedValue([OWNED_JOB]);
    const found = await useAppStore.getState().resolveClip("someoneElses_c9");
    expect(found).toBeNull();
  });

  test("backend unreachable during fallback → null, no throw", async () => {
    listJobs.mockRejectedValue(new Error("network down"));
    await expect(useAppStore.getState().resolveClip("vidABC123_c1")).resolves.toBeNull();
  });

  test("in-memory cache still wins without any network call", async () => {
    const cached = { id: "vidABC123_c1", jobId: "job-owned-1" };
    useAppStore.setState({ clipsByJob: { "job-owned-1": [cached] } });
    const found = await useAppStore.getState().resolveClip("vidABC123_c1");
    expect(found.clip).toBe(cached);
    expect(listJobs).not.toHaveBeenCalled();
    expect(getJob).not.toHaveBeenCalled();
  });
});
