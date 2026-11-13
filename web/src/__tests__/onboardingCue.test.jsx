import { hasSeenFirstClipCue, markFirstClipCueSeen } from "@/lib/onboarding";

beforeEach(() => {
  localStorage.clear();
});

describe("first-clip completion cue flag", () => {
  test("has not been seen for a fresh user", () => {
    expect(hasSeenFirstClipCue("user-1")).toBe(false);
  });

  test("marking seen persists across reads", () => {
    markFirstClipCueSeen("user-1");
    expect(hasSeenFirstClipCue("user-1")).toBe(true);
  });

  test("is scoped per user id — a shared browser doesn't skip it for a second account", () => {
    markFirstClipCueSeen("user-1");
    expect(hasSeenFirstClipCue("user-1")).toBe(true);
    expect(hasSeenFirstClipCue("user-2")).toBe(false);
  });

  test("falls back to a shared anon flag when no user id is passed (dev mode)", () => {
    markFirstClipCueSeen(undefined);
    expect(hasSeenFirstClipCue(undefined)).toBe(true);
    expect(hasSeenFirstClipCue(null)).toBe(true);
  });

  test("survives corrupted localStorage content without throwing", () => {
    localStorage.setItem("shotvi.onboarding.v1", "{not json");
    expect(() => hasSeenFirstClipCue("user-1")).not.toThrow();
    expect(hasSeenFirstClipCue("user-1")).toBe(false);
    expect(() => markFirstClipCueSeen("user-1")).not.toThrow();
    expect(hasSeenFirstClipCue("user-1")).toBe(true);
  });
});
