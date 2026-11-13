/**
 * Auth store slice (Zustand) against a mocked Supabase client:
 * logged out → in → out transitions, session restore, PASSWORD_RECOVERY,
 * error propagation, and the 401 → re-auth handler registered on initAuth.
 *
 * The @/lib/supabaseClient module is mocked with AUTH_ENABLED=true so the
 * REAL slice logic runs (the unmocked module would put the store in dev
 * mode — that path is covered by authGate.test.js and the dev-mode test at
 * the bottom of apiClientAuth.test.js).
 */

// NOTE: CRA's jest config has resetMocks:true — implementations set inside
// this factory are wiped before every test, so they are (re)established in
// beforeEach below. The factory only defines shapes and the listener plumbing.
jest.mock("@/lib/supabaseClient", () => {
  const listeners = [];
  const auth = {
    getSession: jest.fn(),
    onAuthStateChange: jest.fn(),
    signInWithPassword: jest.fn(),
    signUp: jest.fn(),
    signInWithOAuth: jest.fn(),
    resetPasswordForEmail: jest.fn(),
    updateUser: jest.fn(),
    signOut: jest.fn(),
  };
  return {
    AUTH_ENABLED: true,
    supabase: { auth },
    getCachedSession: jest.fn(),
    DEV_USER: { id: "dev-user", email: "dev@localhost", name: "Dev Mode", plan: "Creator" },
    mapSupabaseUser: (u) =>
      u
        ? {
            id: u.id,
            email: u.email || "",
            name: u.user_metadata?.full_name || (u.email || "x").split("@")[0],
            plan: "Creator",
          }
        : null,
    __listeners: listeners,
    // test hook: fire an auth event at every subscriber
    __emit: (event, session) => listeners.forEach((cb) => cb(event, session)),
  };
});

import { useAppStore } from "@/store/useAppStore";
import { supabase, __emit, __listeners, getCachedSession } from "@/lib/supabaseClient";
import { client } from "@/api/client";

const SB_USER = { id: "uuid-1", email: "priya@example.com", user_metadata: {} };
const SESSION = { access_token: "tok-abc", user: SB_USER };

const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  useAppStore.setState({
    user: null,
    session: null,
    authLoading: true,
    passwordRecovery: false,
  });
  __listeners.length = 0;
  getCachedSession.mockReturnValue(null);
  supabase.auth.getSession.mockResolvedValue({ data: { session: null } });
  supabase.auth.onAuthStateChange.mockImplementation((cb) => {
    __listeners.push(cb);
    return { data: { subscription: { unsubscribe: jest.fn() } } };
  });
  supabase.auth.signInWithPassword.mockResolvedValue({ error: null });
  supabase.auth.signUp.mockResolvedValue({ data: { session: null }, error: null });
  supabase.auth.signInWithOAuth.mockResolvedValue({ error: null });
  supabase.auth.resetPasswordForEmail.mockResolvedValue({ error: null });
  supabase.auth.updateUser.mockResolvedValue({ error: null });
  supabase.auth.signOut.mockResolvedValue({ error: null });
});

describe("auth slice", () => {
  test("initial state: logged out, loading until session restore", () => {
    const s = useAppStore.getState();
    expect(s.user).toBeNull();
    expect(s.authEnabled).toBe(true);
    expect(s.authLoading).toBe(true);
  });

  test("initAuth restores a persisted session → logged in", async () => {
    supabase.auth.getSession.mockResolvedValueOnce({ data: { session: SESSION } });
    useAppStore.getState().initAuth();
    await flush();
    const s = useAppStore.getState();
    expect(s.authLoading).toBe(false);
    expect(s.session).toBe(SESSION);
    expect(s.user).toEqual(
      expect.objectContaining({ id: "uuid-1", email: "priya@example.com", name: "priya" })
    );
  });

  test("initAuth with no persisted session → logged out, not loading", async () => {
    useAppStore.getState().initAuth();
    await flush();
    const s = useAppStore.getState();
    expect(s.authLoading).toBe(false);
    expect(s.user).toBeNull();
  });

  test("SIGNED_IN then SIGNED_OUT auth events drive user in and out", async () => {
    useAppStore.getState().initAuth();
    await flush();

    __emit("SIGNED_IN", SESSION);
    expect(useAppStore.getState().user?.id).toBe("uuid-1");

    __emit("SIGNED_OUT", null);
    expect(useAppStore.getState().user).toBeNull();
    expect(useAppStore.getState().session).toBeNull();
  });

  test("PASSWORD_RECOVERY event flags the recovery flow", async () => {
    useAppStore.getState().initAuth();
    await flush();
    __emit("PASSWORD_RECOVERY", SESSION);
    expect(useAppStore.getState().passwordRecovery).toBe(true);
  });

  test("signInWithPassword surfaces Supabase errors as thrown Errors", async () => {
    supabase.auth.signInWithPassword.mockResolvedValueOnce({
      error: { message: "Invalid login credentials" },
    });
    await expect(
      useAppStore.getState().signInWithPassword("a@b.c", "nope")
    ).rejects.toThrow("Invalid login credentials");
  });

  test("signUp reports needsConfirmation when Supabase returns no session", async () => {
    supabase.auth.signUp.mockResolvedValueOnce({ data: { session: null }, error: null });
    const res = await useAppStore.getState().signUpWithPassword("a@b.c", "pw123456", "A");
    expect(res.needsConfirmation).toBe(true);
    expect(supabase.auth.signUp).toHaveBeenCalledWith(
      expect.objectContaining({
        email: "a@b.c",
        options: { data: { full_name: "A" } },
      })
    );
  });

  test("signOut clears user + session and tells Supabase", async () => {
    useAppStore.setState({ user: { id: "uuid-1" }, session: SESSION });
    await useAppStore.getState().signOut();
    expect(supabase.auth.signOut).toHaveBeenCalled();
    expect(useAppStore.getState().user).toBeNull();
    expect(useAppStore.getState().session).toBeNull();
  });

  test("a 401 with a GENUINELY dead session clears auth state (re-auth, not dead app)", async () => {
    supabase.auth.getSession.mockResolvedValueOnce({ data: { session: SESSION } });
    useAppStore.getState().initAuth(); // registers the unauthorized handler
    await flush();
    expect(useAppStore.getState().user).not.toBeNull();

    // Later in the session the auth client no longer has a session (refresh
    // exhausted / expired): cache is null, getSession resolves null (the
    // beforeEach default). A 401 now means the session is really dead.
    const rejected = client.interceptors.response.handlers[0].rejected;
    await expect(
      rejected({ response: { status: 401 }, config: { url: "/jobs", headers: {} } })
    ).rejects.toBeTruthy();

    expect(useAppStore.getState().user).toBeNull();
    expect(useAppStore.getState().session).toBeNull();
    expect(supabase.auth.signOut).toHaveBeenCalled(); // designed global logout
  });

  test("post-login race: a spurious 401 right after sign-in does NOT log the user out, and the request retries to success", async () => {
    // Fresh, valid session just created by sign-in — visible via the cache.
    supabase.auth.getSession.mockResolvedValueOnce({ data: { session: SESSION } });
    useAppStore.getState().initAuth();
    await flush();
    __emit("SIGNED_IN", SESSION);
    getCachedSession.mockReturnValue({
      ...SESSION,
      expires_at: Math.floor(Date.now() / 1000) + 3600,
    });
    expect(useAppStore.getState().user?.id).toBe("uuid-1");

    // The dashboard's jobs fetch raced the new session and came back 401.
    const retrySpy = jest
      .spyOn(client, "request")
      .mockResolvedValue({ data: [{ job_id: "j1" }] });
    const rejected = client.interceptors.response.handlers[0].rejected;
    const res = await rejected({
      response: { status: 401 },
      config: { url: "/jobs", headers: {} },
    });

    // Jobs load once the token is attached; NO logout of any kind.
    expect(res).toEqual({ data: [{ job_id: "j1" }] });
    expect(retrySpy).toHaveBeenCalledTimes(1);
    expect(retrySpy.mock.calls[0][0].headers.Authorization).toBe("Bearer tok-abc");
    expect(supabase.auth.signOut).not.toHaveBeenCalled();
    expect(useAppStore.getState().user?.id).toBe("uuid-1");
    expect(useAppStore.getState().session).toBeTruthy();
    retrySpy.mockRestore();
  });
});
