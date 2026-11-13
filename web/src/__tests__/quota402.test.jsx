/**
 * HTTP 402 — quota / entitlement blocks are a paywall, not an error.
 *
 * The backend now returns 402 from POST /jobs and POST /jobs/upload (monthly
 * SOURCE-minute budget) on top of the existing rerender 402s (render minutes,
 * premium presets). api/client.js only special-cased 401, so a 402 fell
 * through to the generic error path — and because the source-minute gate fires
 * on PASTE, a brand-new user's first action could surface as an unexplained
 * failure.
 *
 * What is pinned here:
 *  - toApiError carries status 402 and lifts the server's `detail` into the
 *    message (string OR dict detail), so the UI can render the server's copy
 *  - job creation (URL + file) and export record {message, status} and skip
 *    the red error toast for 402 specifically
 *  - the copy shown to the user is the SERVER's, not a frontend-invented
 *    quota sentence, and it comes with an upgrade affordance
 *  - no component compares a plan against a hardcoded key string
 */
import fs from "fs";
import path from "path";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { toApiError, isQuotaError, QUOTA_EXCEEDED_STATUS, ApiError } from "@/api/client";
import { useAppStore } from "@/store/useAppStore";
import { createJobFromUrl, createJobFromFile } from "@/api/uploads";
import { startRerender } from "@/api/renders";
import { createSubscription } from "@/api/billing";
import { loadRazorpay, openSubscriptionCheckout } from "@/lib/razorpayCheckout";
import { FirstRunHero } from "@/components/shotvi/FirstRunHero";
import Export from "@/pages/Export";
import { toast } from "sonner";
import { QUOTA, DASHBOARD, EXPORT } from "@/constants/testIds";

jest.mock("@/api/uploads", () => ({
  createJobFromUrl: jest.fn(),
  createJobFromFile: jest.fn(),
  getJob: jest.fn(),
  listJobs: jest.fn(),
}));
jest.mock("@/api/billing", () => ({
  getBillingStatus: jest.fn(),
  createSubscription: jest.fn(),
  cancelSubscription: jest.fn(),
}));
jest.mock("@/lib/razorpayCheckout", () => ({
  loadRazorpay: jest.fn(),
  openSubscriptionCheckout: jest.fn(),
}));
jest.mock("@/api/renders", () => {
  const actual = jest.requireActual("@/api/renders");
  return { ...actual, startRerender: jest.fn() };
});
jest.mock("@/api/clips", () => {
  const actual = jest.requireActual("@/api/clips");
  return { ...actual, loadDraft: jest.fn(), saveDraft: jest.fn(), generateClipMetadata: jest.fn() };
});
jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn() },
}));
// Export-page collaborators irrelevant to the tier strip / quota panel.
jest.mock("@/api/templates", () => ({
  getCaptionTemplate: jest.fn(),
  putCaptionTemplate: jest.fn(),
}));
jest.mock("@/components/shotvi/AppShell", () => ({
  __esModule: true,
  AppShell: ({ children }) => <div>{children}</div>,
}));
jest.mock("@/hooks/useJobPolling", () => ({
  __esModule: true,
  useJobPolling: () => ({ error: null }),
}));
jest.mock("@/components/editor/StaticElementLayer", () => ({
  __esModule: true,
  StaticElementLayer: () => null,
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

beforeAll(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  Object.defineProperty(HTMLMediaElement.prototype, "play", {
    configurable: true,
    value: () => Promise.resolve(),
  });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", {
    configurable: true,
    value: () => {},
  });
});

const SRC = path.join(__dirname, "..");
const st = () => useAppStore.getState();

// The real shape the server sends: FastAPI HTTPException(402, detail="…").
const SOURCE_QUOTA_COPY =
  "This video is 42.0 minutes but you have 6.3 of 10 source minutes left this month.";
const RENDER_QUOTA_COPY = "You've used 30.0 of 30 render minutes this month.";

const quotaApiError = (detail) =>
  toApiError(
    Object.assign(new Error("Request failed with status code 402"), {
      isAxiosError: true,
      response: { status: QUOTA_EXCEEDED_STATUS, data: { detail } },
      config: {},
    }),
    "Could not submit the YouTube URL"
  );

let container;
let root;
const mount = async (jsx) => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(jsx));
};
const unmount = async () => {
  await act(async () => root.unmount());
  container.remove();
};
const byTestId = (id) => container.querySelector(`[data-testid="${id}"]`);
const setInputValue = async (el, value) => {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value"
  ).set;
  setter.call(el, value);
  await act(async () => el.dispatchEvent(new Event("input", { bubbles: true })));
};

beforeEach(() => {
  // CRA jest runs with resetMocks:true — implementations are set per test.
  createSubscription.mockResolvedValue({ subscription_id: "s", key_id: "k", plan: {} });
  loadRazorpay.mockResolvedValue({});
  useAppStore.setState({
    user: { id: "u1", email: "a@b.com" },
    billingStatus: { plan: "free", configured: true, watermark: true },
    billingActionPending: false,
    submitError: null,
    submitErrorStatus: null,
    activeJobId: null,
    activeJob: null,
    exportStatus: "idle",
    exportError: null,
    exportErrorStatus: null,
  });
});

describe("toApiError classifies 402", () => {
  test("status is preserved and the server's string detail becomes the message", () => {
    const err = quotaApiError(SOURCE_QUOTA_COPY);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(402);
    expect(isQuotaError(err)).toBe(true);
    expect(err.message).toBe(SOURCE_QUOTA_COPY);
    // A quota block is not a transient failure — never auto-retryable.
    expect(err.retryable).toBe(false);
  });

  test("a dict detail still yields the human sentence, not [object Object]", () => {
    const err = quotaApiError({ message: RENDER_QUOTA_COPY, used: 30, budget: 30 });
    expect(err.message).toBe(RENDER_QUOTA_COPY);
    expect(err.message).not.toContain("object Object");
  });

  test("non-402 statuses are unaffected", () => {
    const err = toApiError(
      Object.assign(new Error("boom"), {
        isAxiosError: true,
        response: { status: 500, data: { detail: "server exploded" } },
        config: {},
      }),
      "fallback"
    );
    expect(isQuotaError(err)).toBe(false);
    expect(err.retryable).toBe(true);
  });
});

describe("job creation surfaces the server's 402 copy, not a generic error", () => {
  test("submitYouTubeUrl records the detail + status 402", async () => {
    createJobFromUrl.mockRejectedValue(quotaApiError(SOURCE_QUOTA_COPY));

    await expect(
      st().submitYouTubeUrl({ url: "https://youtu.be/abcdefghijk", language: "te" })
    ).rejects.toThrow();

    expect(st().submitError).toBe(SOURCE_QUOTA_COPY);
    expect(st().submitErrorStatus).toBe(QUOTA_EXCEEDED_STATUS);
    // NOT the generic fallback copy.
    expect(st().submitError).not.toMatch(/could not submit/i);
    // A red error toast on top of a paywall panel reads as a crash.
    expect(toast.error).not.toHaveBeenCalled();
  });

  test("submitFile records the detail + status 402 the same way", async () => {
    createJobFromFile.mockRejectedValue(quotaApiError(SOURCE_QUOTA_COPY));

    await expect(
      st().submitFile({ file: { name: "a.mp4", size: 10 }, language: "te" })
    ).rejects.toThrow();

    expect(st().submitError).toBe(SOURCE_QUOTA_COPY);
    expect(st().submitErrorStatus).toBe(QUOTA_EXCEEDED_STATUS);
    expect(toast.error).not.toHaveBeenCalled();
  });

  test("a non-quota submit failure still toasts and carries its own status", async () => {
    createJobFromUrl.mockRejectedValue(
      toApiError(
        Object.assign(new Error("nope"), {
          isAxiosError: true,
          response: { status: 500, data: { detail: "download service down" } },
          config: {},
        }),
        "Could not submit the YouTube URL"
      )
    );

    await expect(
      st().submitYouTubeUrl({ url: "https://youtu.be/abcdefghijk", language: "te" })
    ).rejects.toThrow();

    expect(st().submitError).toBe("download service down");
    expect(st().submitErrorStatus).toBe(500);
    expect(toast.error).toHaveBeenCalledWith("download service down");
  });

  test("resetSubmission clears the quota status too", async () => {
    useAppStore.setState({ submitError: SOURCE_QUOTA_COPY, submitErrorStatus: 402 });
    st().resetSubmission();
    expect(st().submitError).toBeNull();
    expect(st().submitErrorStatus).toBeNull();
  });
});

describe("export surfaces the server's 402 copy", () => {
  test("startExport records the detail + status 402 without a generic toast", async () => {
    startRerender.mockRejectedValue(quotaApiError(RENDER_QUOTA_COPY));
    useAppStore.setState({
      currentClip: { id: "clipA", jobId: "job1", index: 0, videoUrl: "http://x/v.mp4", duration: 12 },
      duration: 12,
    });

    const out = await st().startExport();

    expect(out).toBeNull();
    expect(st().exportStatus).toBe("failed");
    expect(st().exportError).toBe(RENDER_QUOTA_COPY);
    expect(st().exportErrorStatus).toBe(QUOTA_EXCEEDED_STATUS);
    expect(st().exportError).not.toMatch(/could not start the export/i);
    expect(toast.error).not.toHaveBeenCalled();
  });

  test("resetExport clears the quota status", () => {
    useAppStore.setState({
      exportStatus: "failed", exportError: RENDER_QUOTA_COPY, exportErrorStatus: 402,
    });
    st().resetExport();
    expect(st().exportError).toBeNull();
    expect(st().exportErrorStatus).toBeNull();
  });
});

describe("the user sees the server's copy plus something to click", () => {
  test("a 402 on the first-run paste renders the quota panel with an upgrade CTA", async () => {
    createJobFromUrl.mockRejectedValue(quotaApiError(SOURCE_QUOTA_COPY));

    await mount(
      <MemoryRouter>
        <FirstRunHero />
      </MemoryRouter>
    );

    await setInputValue(byTestId(DASHBOARD.heroUrlInput), "https://youtu.be/abcdefghijk");
    await act(async () => {
      byTestId(DASHBOARD.heroSubmit)
        .closest("form")
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });

    // The server's sentence, verbatim — no frontend-invented quota numbers.
    expect(byTestId(QUOTA.notice)).toBeTruthy();
    expect(byTestId(QUOTA.message).textContent).toBe(SOURCE_QUOTA_COPY);
    // …and not routed through the plain red validation line.
    expect(byTestId(DASHBOARD.heroError)).toBeNull();

    // An upgrade affordance wired to the same checkout the sidebar opens.
    const upgrade = byTestId(QUOTA.upgrade);
    expect(upgrade).toBeTruthy();
    await act(async () =>
      upgrade.dispatchEvent(new MouseEvent("click", { bubbles: true }))
    );
    expect(createSubscription).toHaveBeenCalled();
    expect(openSubscriptionCheckout).toHaveBeenCalled();

    await unmount();
  });

  test("an already-paid account gets the message without a dead-end Upgrade button", async () => {
    useAppStore.setState({
      billingStatus: { plan: "creator", configured: true, watermark: false },
    });
    createJobFromUrl.mockRejectedValue(quotaApiError(SOURCE_QUOTA_COPY));

    await mount(
      <MemoryRouter>
        <FirstRunHero />
      </MemoryRouter>
    );
    await setInputValue(byTestId(DASHBOARD.heroUrlInput), "https://youtu.be/abcdefghijk");
    await act(async () => {
      byTestId(DASHBOARD.heroSubmit)
        .closest("form")
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });

    expect(byTestId(QUOTA.message).textContent).toBe(SOURCE_QUOTA_COPY);
    expect(byTestId(QUOTA.upgrade)).toBeNull();

    await unmount();
  });
});

describe("the Export page shows the budgets before they block you", () => {
  const CLIP = {
    id: "clipA", jobId: "job1", index: 0, videoUrl: "http://x/v.mp4", duration: 12,
  };
  const mountExport = async () => {
    useAppStore.setState({ currentClip: CLIP, currentClipId: "clipA", duration: 12 });
    await mount(
      <MemoryRouter initialEntries={["/export/clipA"]}>
        <Routes>
          <Route path="/export/:clipId" element={<Export />} />
        </Routes>
      </MemoryRouter>
    );
  };

  test("renders BOTH monthly meters — the source meter is the one that 402s on paste", async () => {
    useAppStore.setState({
      billingStatus: {
        plan: "free", configured: true, watermark: true,
        render_minutes_used: 12.5, render_minutes_budget: 30,
        source_minutes_used: 3.7, source_minutes_budget: 10,
        expiry_hours: 24,
      },
    });
    await mountExport();

    expect(byTestId(EXPORT.renderMeter).textContent).toContain("12.5 / 30");
    const source = byTestId(EXPORT.sourceMeter);
    expect(source).toBeTruthy();
    expect(source.textContent).toContain("3.7 / 10");

    await unmount();
  });

  test("a backend without the source fields omits the meter rather than showing 0/0", async () => {
    useAppStore.setState({
      billingStatus: {
        plan: "free", configured: true, watermark: true,
        render_minutes_used: 0, render_minutes_budget: 30, expiry_hours: 24,
      },
    });
    await mountExport();

    expect(byTestId(EXPORT.renderMeter)).toBeTruthy();
    expect(byTestId(EXPORT.sourceMeter)).toBeNull();

    await unmount();
  });

  test("the free-tier watermark notice has a real CTA, not a dead paragraph", async () => {
    useAppStore.setState({
      billingStatus: {
        plan: "free", configured: true, watermark: true,
        render_minutes_used: 0, render_minutes_budget: 30,
        source_minutes_used: 0, source_minutes_budget: 10, expiry_hours: 24,
      },
    });
    await mountExport();

    const cta = byTestId(EXPORT.upgradeCta);
    expect(cta).toBeTruthy();
    expect(cta.tagName).toBe("BUTTON");
    await act(async () => cta.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(createSubscription).toHaveBeenCalled();

    await unmount();
  });

  test("a 402 export renders the quota panel, not the red 'Export failed' + Retry", async () => {
    useAppStore.setState({
      billingStatus: {
        plan: "free", configured: true, watermark: true,
        render_minutes_used: 30, render_minutes_budget: 30,
        source_minutes_used: 0, source_minutes_budget: 10, expiry_hours: 24,
      },
    });
    await mountExport();
    // After mount — the page's mount effect calls resetExport().
    await act(async () =>
      useAppStore.setState({
        exportStatus: "failed",
        exportError: RENDER_QUOTA_COPY,
        exportErrorStatus: QUOTA_EXCEEDED_STATUS,
      })
    );

    expect(byTestId(QUOTA.message).textContent).toBe(RENDER_QUOTA_COPY);
    expect(container.textContent).not.toContain("Export failed");

    await unmount();
  });

  test("a genuine render failure still gets the error panel, not a paywall", async () => {
    useAppStore.setState({
      billingStatus: {
        plan: "free", configured: true, watermark: true,
        render_minutes_used: 0, render_minutes_budget: 30, expiry_hours: 24,
      },
    });
    await mountExport();
    await act(async () =>
      useAppStore.setState({
        exportStatus: "failed",
        exportError: "ffmpeg exited with code 1",
        exportErrorStatus: 500,
      })
    );

    expect(container.textContent).toContain("Export failed");
    expect(byTestId(QUOTA.notice)).toBeNull();

    await unmount();
  });
});

/**
 * The plan key moved from `studio` to `creator`, with `studio` kept as a
 * LEGACY ALIAS that is still the live value in existing Redis hashes. Both
 * directions of hardcoded compare are bugs: `=== "studio"` misses new
 * subscribers, `=== "creator"` demotes the existing ones to the free-tier UI
 * (watermark warnings and an Upgrade button that 409s on their live
 * subscription). lib/plan.js owns the predicate; nothing else may compare.
 */
describe("no hardcoded plan-key comparison survives in app code", () => {
  const collect = (dir, out = []) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === "__tests__" || entry.name === "node_modules") continue;
        collect(full, out);
      } else if (/\.jsx?$/.test(entry.name)) {
        out.push(full);
      }
    }
    return out;
  };

  test("no `=== \"studio\"` / `=== \"creator\"` anywhere under src/", () => {
    const offenders = [];
    for (const file of collect(SRC)) {
      const src = fs.readFileSync(file, "utf8");
      src.split("\n").forEach((line, i) => {
        // Comments may quote the anti-pattern to explain it (lib/plan.js does).
        const trimmed = line.trim();
        if (trimmed.startsWith("//") || trimmed.startsWith("*") || trimmed.startsWith("/*")) return;
        if (/[!=]==\s*["'`](studio|creator)["'`]/i.test(line)) {
          offenders.push(`${path.relative(SRC, file)}:${i + 1}: ${line.trim()}`);
        }
      });
    }
    expect(offenders).toEqual([]);
  });

  test("the paid predicate accepts both the current key and the legacy alias", () => {
    for (const plan of ["creator", "studio"]) {
      useAppStore.setState({ billingStatus: { plan, configured: true } });
      expect(st().isPaidPlan()).toBe(true);
    }
    useAppStore.setState({ billingStatus: { plan: "free", configured: true } });
    expect(st().isPaidPlan()).toBe(false);
  });

  test("resolved entitlements alone are enough to read as paid", () => {
    // An unknown/legacy plan string the frontend has never heard of, but the
    // server has already resolved its entitlements: still paid.
    useAppStore.setState({
      billingStatus: { plan: "free", configured: true, watermark: false },
    });
    expect(st().isPaidPlan()).toBe(true);
    useAppStore.setState({
      billingStatus: { plan: "free", configured: true, subscription_status: "active" },
    });
    expect(st().isPaidPlan()).toBe(true);
  });
});
