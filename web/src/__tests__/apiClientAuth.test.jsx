/**
 * API wrapper auth behaviour (api/client.js):
 *  - every request through `client` carries the Supabase access token,
 *    preferring the SYNCHRONOUS session cache (post-login race fix) over
 *    the async getSession()
 *  - no session → request goes out bare (Supabase not signed in yet)
 *  - 401 classification: a 401 while a usable session exists is retried
 *    once with the fresh token (request raced the new session — NOT a dead
 *    session); only no-session / locally-expired / retry-still-401 cases
 *    invoke the unauthorized handler
 */

// CRA jest resetMocks:true wipes factory implementations before every test —
// implementations are (re)set in beforeEach.
jest.mock("@/lib/supabaseClient", () => ({
  AUTH_ENABLED: true,
  supabase: { auth: { getSession: jest.fn() } },
  getCachedSession: jest.fn(),
  DEV_USER: {},
  mapSupabaseUser: () => null,
}));

import { client, setUnauthorizedHandler, isSessionUsable } from "@/api/client";
import { supabase, getCachedSession } from "@/lib/supabaseClient";

const requestInterceptor = client.interceptors.request.handlers[0].fulfilled;
const responseRejected = client.interceptors.response.handlers[0].rejected;

const nowSec = () => Math.floor(Date.now() / 1000);
const FRESH_SESSION = { access_token: "tok-fresh", expires_at: nowSec() + 3600 };
const EXPIRED_SESSION = { access_token: "tok-stale", expires_at: nowSec() - 60 };

beforeEach(() => {
  getCachedSession.mockReturnValue(null);
  supabase.auth.getSession.mockResolvedValue({ data: { session: null } });
  setUnauthorizedHandler(null);
});

describe("isSessionUsable", () => {
  test("live token → usable; missing/expired/near-expiry → not usable", () => {
    expect(isSessionUsable(FRESH_SESSION)).toBe(true);
    expect(isSessionUsable({ access_token: "tok" })).toBe(true); // no expiry claim
    expect(isSessionUsable(null)).toBe(false);
    expect(isSessionUsable({})).toBe(false);
    expect(isSessionUsable(EXPIRED_SESSION)).toBe(false);
    // inside the 5s skew margin counts as expired
    expect(isSessionUsable({ access_token: "t", expires_at: nowSec() + 2 })).toBe(false);
  });
});

describe("request interceptor", () => {
  test("prefers the synchronous session cache — no async getSession needed", async () => {
    getCachedSession.mockReturnValue(FRESH_SESSION);
    const cfg = await requestInterceptor({ headers: {} });
    expect(cfg.headers.Authorization).toBe("Bearer tok-fresh");
    expect(supabase.auth.getSession).not.toHaveBeenCalled();
  });

  test("falls back to getSession() when the cache is cold", async () => {
    supabase.auth.getSession.mockResolvedValueOnce({
      data: { session: { access_token: "tok-xyz" } },
    });
    const cfg = await requestInterceptor({ headers: {} });
    expect(cfg.headers.Authorization).toBe("Bearer tok-xyz");
  });

  test("no session anywhere → no Authorization header", async () => {
    const cfg = await requestInterceptor({ headers: {} });
    expect(cfg.headers.Authorization).toBeUndefined();
  });
});

describe("response interceptor — 401 classification", () => {
  test("post-login race: 401 with a usable session retries once with the token and does NOT invoke the handler", async () => {
    const onUnauthorized = jest.fn();
    setUnauthorizedHandler(onUnauthorized);
    getCachedSession.mockReturnValue(FRESH_SESSION);
    const retrySpy = jest
      .spyOn(client, "request")
      .mockResolvedValue({ data: ["job-1"] });

    const err = { response: { status: 401 }, config: { url: "/jobs", headers: {} } };
    const res = await responseRejected(err); // resolves — jobs load after all

    expect(res).toEqual({ data: ["job-1"] });
    expect(retrySpy).toHaveBeenCalledTimes(1);
    const retriedCfg = retrySpy.mock.calls[0][0];
    expect(retriedCfg._authRetried).toBe(true);
    expect(retriedCfg.headers.Authorization).toBe("Bearer tok-fresh");
    expect(onUnauthorized).not.toHaveBeenCalled();
    retrySpy.mockRestore();
  });

  test("retry that STILL 401s (token live locally, rejected by backend) → handler fires", async () => {
    const onUnauthorized = jest.fn();
    setUnauthorizedHandler(onUnauthorized);
    getCachedSession.mockReturnValue(FRESH_SESSION);

    const err = {
      response: { status: 401 },
      config: { url: "/jobs", headers: {}, _authRetried: true },
    };
    await expect(responseRejected(err)).rejects.toBe(err);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  test("401 with a locally-EXPIRED session → no retry, handler fires", async () => {
    const onUnauthorized = jest.fn();
    setUnauthorizedHandler(onUnauthorized);
    getCachedSession.mockReturnValue(EXPIRED_SESSION);
    const retrySpy = jest.spyOn(client, "request");

    const err = { response: { status: 401 }, config: { url: "/jobs", headers: {} } };
    await expect(responseRejected(err)).rejects.toBe(err);
    expect(retrySpy).not.toHaveBeenCalled();
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    retrySpy.mockRestore();
  });

  test("401 with no session at all → handler fires (dead session)", async () => {
    const onUnauthorized = jest.fn();
    setUnauthorizedHandler(onUnauthorized);
    const err = { response: { status: 401 }, config: { url: "/jobs", headers: {} } };
    await expect(responseRejected(err)).rejects.toBe(err);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  test("non-401 errors pass through without touching the handler", async () => {
    const onUnauthorized = jest.fn();
    setUnauthorizedHandler(onUnauthorized);
    const err = { response: { status: 500 } };
    await expect(responseRejected(err)).rejects.toBe(err);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  test("network errors (no response) do not trigger re-auth", async () => {
    const onUnauthorized = jest.fn();
    setUnauthorizedHandler(onUnauthorized);
    const err = new Error("Network Error");
    await expect(responseRejected(err)).rejects.toBe(err);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});
