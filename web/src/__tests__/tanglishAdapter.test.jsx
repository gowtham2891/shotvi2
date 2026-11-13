/**
 * Graceful degrade of the Tanglish derivation adapter (api/tanglish.js).
 *
 * fetchTanglish() is the only seam between the word-edit commit path and
 * POST /tanglish. Editing must never break on it: the adapter NEVER throws
 * and resolves to null on every failure path (dead service, malformed or
 * length-mismatched payload) — the store then leaves text_tanglish null and
 * the Tanglish view falls back to the word's stored romanization.
 */
import { fetchTanglish } from "@/api/tanglish";
import { client } from "@/api/client";

jest.mock("@/api/client", () => ({
  ...jest.requireActual("@/api/client"),
  client: { post: jest.fn() },
}));

beforeEach(() => {
  client.post.mockReset();
});

test("passes derivations through from POST /tanglish, order-preserving", async () => {
  client.post.mockResolvedValue({ data: { tanglish: ["deenni", "control"] } });
  await expect(fetchTanglish(["దీన్ని", "control"])).resolves.toEqual(["deenni", "control"]);
  expect(client.post).toHaveBeenCalledWith("/tanglish", { words: ["దీన్ని", "control"] });
});

test("dead/unreachable service resolves to null — NEVER throws", async () => {
  client.post.mockRejectedValue(new Error("ECONNREFUSED"));
  await expect(fetchTanglish(["దీన్ని"])).resolves.toBeNull();
});

test("malformed payloads resolve to null", async () => {
  client.post.mockResolvedValue({ data: {} });
  await expect(fetchTanglish(["అ"])).resolves.toBeNull();
  client.post.mockResolvedValue({ data: { tanglish: "not-a-list" } });
  await expect(fetchTanglish(["అ"])).resolves.toBeNull();
  client.post.mockResolvedValue({ data: { tanglish: [42] } });
  await expect(fetchTanglish(["అ"])).resolves.toBeNull();
});

test("length mismatch resolves to null — a wrong-length list could romanize the wrong word", async () => {
  client.post.mockResolvedValue({ data: { tanglish: ["a", "b"] } });
  await expect(fetchTanglish(["అ"])).resolves.toBeNull();
});

test("empty input short-circuits without a network call", async () => {
  await expect(fetchTanglish([])).resolves.toBeNull();
  expect(client.post).not.toHaveBeenCalled();
});
