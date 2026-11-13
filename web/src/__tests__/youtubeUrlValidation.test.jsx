import { isValidYoutubeUrl } from "@/lib/youtubeUrl";

describe("isValidYoutubeUrl", () => {
  test("accepts a standard watch URL", () => {
    expect(isValidYoutubeUrl("https://www.youtube.com/watch?v=CC8V0PwlQ4o")).toBe(true);
  });

  test("accepts a bare youtube.com watch URL (no www)", () => {
    expect(isValidYoutubeUrl("https://youtube.com/watch?v=CC8V0PwlQ4o")).toBe(true);
  });

  test("accepts a youtu.be short link", () => {
    expect(isValidYoutubeUrl("https://youtu.be/CC8V0PwlQ4o")).toBe(true);
  });

  test("accepts a shorts URL", () => {
    expect(isValidYoutubeUrl("https://youtube.com/shorts/CC8V0PwlQ4o")).toBe(true);
  });

  test("accepts an embed URL", () => {
    expect(isValidYoutubeUrl("https://www.youtube.com/embed/CC8V0PwlQ4o")).toBe(true);
  });

  test("accepts an m.youtube.com mobile URL", () => {
    expect(isValidYoutubeUrl("https://m.youtube.com/watch?v=CC8V0PwlQ4o")).toBe(true);
  });

  test("trims surrounding whitespace", () => {
    expect(isValidYoutubeUrl("  https://youtu.be/CC8V0PwlQ4o  ")).toBe(true);
  });

  test("rejects a non-YouTube URL", () => {
    expect(isValidYoutubeUrl("https://vimeo.com/12345")).toBe(false);
  });

  test("rejects plain text", () => {
    expect(isValidYoutubeUrl("not a url")).toBe(false);
  });

  test("rejects an empty or whitespace-only string", () => {
    expect(isValidYoutubeUrl("")).toBe(false);
    expect(isValidYoutubeUrl("   ")).toBe(false);
  });

  test("rejects a youtube.com URL with no video id", () => {
    expect(isValidYoutubeUrl("https://www.youtube.com/")).toBe(false);
    expect(isValidYoutubeUrl("https://www.youtube.com/results?search_query=x")).toBe(false);
  });

  test("rejects a youtu.be URL with no path", () => {
    expect(isValidYoutubeUrl("https://youtu.be/")).toBe(false);
  });

  test("rejects non-http(s) protocols", () => {
    expect(isValidYoutubeUrl("ftp://youtube.com/watch?v=CC8V0PwlQ4o")).toBe(false);
  });

  test("rejects null/undefined/non-string input", () => {
    expect(isValidYoutubeUrl(null)).toBe(false);
    expect(isValidYoutubeUrl(undefined)).toBe(false);
    expect(isValidYoutubeUrl(123)).toBe(false);
  });
});
