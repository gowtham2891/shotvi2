/**
 * Regression: the Export page's "Download MP4" was a bare <a href> to
 * GET /clips/download — a browser navigation carries no Authorization
 * header, so with real Supabase auth the click returned
 * {"detail":"Missing bearer token"} instead of the file. downloadClip must
 * fetch through the AUTHED axios client (whose interceptor attaches the
 * bearer token) and save the blob client-side.
 */
import { downloadClip } from "@/api/clips";
import { client } from "@/api/client";

jest.mock("@/api/client", () => {
  const actual = jest.requireActual("@/api/client");
  return { ...actual, client: { get: jest.fn() } };
});

describe("downloadClip (authenticated blob download)", () => {
  let createdUrls;
  let clicks;

  beforeEach(() => {
    client.get.mockReset();
    createdUrls = [];
    clicks = [];
    global.URL.createObjectURL = jest.fn((blob) => {
      createdUrls.push(blob);
      return "blob:mock-url";
    });
    global.URL.revokeObjectURL = jest.fn();
    jest
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function () {
        clicks.push({ href: this.href, download: this.download });
      });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  test("fetches via the authed client with responseType blob and saves under the file's basename", async () => {
    const blob = new Blob(["mp4-bytes"], { type: "video/mp4" });
    client.get.mockResolvedValue({ data: blob });

    const path = "storage\\outputs\\vidX_clip1_t_9_16_blur_ab12cd34_canvas_bold-yellow_captioned.mp4";
    const name = await downloadClip(path);

    // THE fix: the request goes through the token-attaching client, with the
    // full server path as a param — never a bare <a href> navigation.
    expect(client.get).toHaveBeenCalledWith("/clips/download", {
      params: { path },
      responseType: "blob",
    });
    expect(createdUrls).toEqual([blob]);
    expect(clicks).toHaveLength(1);
    expect(clicks[0].download).toBe(
      "vidX_clip1_t_9_16_blur_ab12cd34_canvas_bold-yellow_captioned.mp4"
    );
    expect(name).toBe(clicks[0].download);
    expect(global.URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
  });

  test("a backend rejection surfaces as a readable ApiError (no navigation, no save)", async () => {
    client.get.mockRejectedValue(new Error("boom"));
    await expect(downloadClip("storage/outputs/x.mp4")).rejects.toThrow();
    expect(clicks).toHaveLength(0);
    expect(createdUrls).toHaveLength(0);
  });
});
