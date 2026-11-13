/**
 * GATE 3 — graceful degrade of the transliteration adapter.
 *
 * fetchTransliterations() is the ONLY seam between the editable transcript
 * and the (future) IndicXlit service. The editor must stay fully usable
 * when the service is stubbed, dead, or returns garbage — so the adapter
 * must NEVER throw and must resolve to [] on every failure path. Typed
 * text commits through setWordEdit regardless (covered by GATE 2); these
 * tests pin the adapter side of that guarantee.
 */
import { fetchTransliterations } from "@/api/transliterate";
import { client } from "@/api/client";

jest.mock("@/api/client", () => ({
  ...jest.requireActual("@/api/client"),
  client: { post: jest.fn() },
}));

beforeEach(() => {
  client.post.mockReset();
});

test("passes suggestions through from POST /transliterate", async () => {
  client.post.mockResolvedValue({ data: { suggestions: ["కల్పవృక్షం", "కల్పవ్రుక్షం"] } });
  await expect(fetchTransliterations("kalpavruksham")).resolves.toEqual([
    "కల్పవృక్షం",
    "కల్పవ్రుక్షం",
  ]);
  expect(client.post).toHaveBeenCalledWith("/transliterate", {
    text: "kalpavruksham",
    lang: "te",
  });
});

test("stubbed service ([]) resolves to an empty list — editing must not depend on suggestions", async () => {
  client.post.mockResolvedValue({ data: { suggestions: [] } });
  await expect(fetchTransliterations("mind")).resolves.toEqual([]);
});

test("dead/unreachable service resolves to [] — NEVER throws", async () => {
  client.post.mockRejectedValue(new Error("ECONNREFUSED"));
  await expect(fetchTransliterations("kalpavruksham")).resolves.toEqual([]);
});

test("malformed payloads resolve to []", async () => {
  client.post.mockResolvedValue({ data: {} });
  await expect(fetchTransliterations("abc")).resolves.toEqual([]);
  client.post.mockResolvedValue({ data: { suggestions: "not-a-list" } });
  await expect(fetchTransliterations("abc")).resolves.toEqual([]);
  client.post.mockResolvedValue({ data: { suggestions: ["ok", 42, "", null] } });
  await expect(fetchTransliterations("abc")).resolves.toEqual(["ok"]);
});

test("non-Latin and empty input short-circuit without any network call", async () => {
  await expect(fetchTransliterations("తెలుగు")).resolves.toEqual([]);
  await expect(fetchTransliterations("")).resolves.toEqual([]);
  await expect(fetchTransliterations("  ")).resolves.toEqual([]);
  await expect(fetchTransliterations("abc123")).resolves.toEqual([]); // mixed token
  expect(client.post).not.toHaveBeenCalled();
});
