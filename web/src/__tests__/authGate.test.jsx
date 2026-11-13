/**
 * Route-guard decision matrix (lib/authGate.js — the logic behind
 * components/RequireAuth.jsx). Pure-logic suite, repo style.
 */
import { authGate } from "@/lib/authGate";

const USER = { id: "u1", email: "a@b.c", name: "a", plan: "Creator" };

describe("authGate", () => {
  test("dev mode (auth disabled) always allows — local workflows keep working", () => {
    expect(authGate({ user: null, authEnabled: false, authLoading: false })).toBe("allow");
    expect(authGate({ user: null, authEnabled: false, authLoading: true })).toBe("allow");
    expect(authGate({ user: USER, authEnabled: false, authLoading: false })).toBe("allow");
  });

  test("session restore in flight → loading (never a flash of the auth screen)", () => {
    expect(authGate({ user: null, authEnabled: true, authLoading: true })).toBe("loading");
  });

  test("no session → redirect to /auth", () => {
    expect(authGate({ user: null, authEnabled: true, authLoading: false })).toBe("redirect");
  });

  test("signed in → allow", () => {
    expect(authGate({ user: USER, authEnabled: true, authLoading: false })).toBe("allow");
  });

  test("logout transition: allow → redirect when the user disappears", () => {
    const base = { authEnabled: true, authLoading: false };
    expect(authGate({ ...base, user: USER })).toBe("allow");
    expect(authGate({ ...base, user: null })).toBe("redirect");
  });
});
