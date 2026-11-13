/**
 * Billing / upgrade flow — frontend regression suite (PHASE 2 BUILD 2).
 *
 * Store-level (matching the repo's test style — no component rendering): pins
 * plan-status loading, the upgrade wiring (create subscription → load + open
 * Razorpay Checkout), the not-configured guard, cancellation/dismiss clearing
 * the pending state, post-payment status refetch, and cancel.
 *
 * The AppShell card is a thin read of this state: free+configured → Upgrade
 * button, a paid plan → Active + Cancel, configured=false → "not configured".
 */
import { useAppStore } from "@/store/useAppStore";
import { getBillingStatus, createSubscription, cancelSubscription } from "@/api/billing";
import { loadRazorpay, openSubscriptionCheckout } from "@/lib/razorpayCheckout";
import { toast } from "sonner";

jest.mock("@/api/billing", () => ({
  getBillingStatus: jest.fn(),
  createSubscription: jest.fn(),
  cancelSubscription: jest.fn(),
}));
jest.mock("@/lib/razorpayCheckout", () => ({
  loadRazorpay: jest.fn(),
  openSubscriptionCheckout: jest.fn(),
}));
jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn() },
}));

// CRA jest runs with resetMocks:true — implementations must be (re)set per test.
beforeEach(() => {
  loadRazorpay.mockResolvedValue({});
  useAppStore.setState({
    user: { id: "u1", email: "a@b.com" },
    billingStatus: null,
    billingLoading: false,
    billingActionPending: false,
  });
});

describe("loadBillingStatus", () => {
  test("stores the status returned by the API", async () => {
    getBillingStatus.mockResolvedValue({
      plan: "free", subscription_status: "", configured: true,
      plan_info: { name: "Creator", price_display: "₹499/mo" },
    });
    await useAppStore.getState().loadBillingStatus();
    expect(useAppStore.getState().billingStatus.plan).toBe("free");
    expect(useAppStore.getState().isPaidPlan()).toBe(false);
  });

  test("is non-fatal on error (ambient, no crash, no toast)", async () => {
    getBillingStatus.mockRejectedValue(new Error("boom"));
    await useAppStore.getState().loadBillingStatus();
    expect(useAppStore.getState().billingStatus).toBeNull();
    expect(useAppStore.getState().billingLoading).toBe(false);
    expect(toast.error).not.toHaveBeenCalled();
  });
});

describe("isPaidPlan", () => {
  test("creator is paid; free is not", () => {
    useAppStore.setState({ billingStatus: { plan: "creator", configured: true } });
    expect(useAppStore.getState().isPaidPlan()).toBe(true);
    useAppStore.setState({ billingStatus: { plan: "free", configured: true } });
    expect(useAppStore.getState().isPaidPlan()).toBe(false);
  });

  // The rename from `studio` to `creator` must not demote existing paying
  // subscribers: their Redis hash still says 'studio' and the backend aliases
  // it, so the UI has to read it as paid too.
  test("the legacy `studio` key still reads as paid", () => {
    useAppStore.setState({ billingStatus: { plan: "studio", configured: true } });
    expect(useAppStore.getState().isPaidPlan()).toBe(true);
  });

  test("missing / empty plan is not paid (safe floor)", () => {
    useAppStore.setState({ billingStatus: { plan: "", configured: true } });
    expect(useAppStore.getState().isPaidPlan()).toBe(false);
    useAppStore.setState({ billingStatus: null });
    expect(useAppStore.getState().isPaidPlan()).toBe(false);
  });
});

describe("startUpgrade", () => {
  test("creates a subscription and opens Razorpay Checkout with its details", async () => {
    useAppStore.setState({
      billingStatus: { plan: "free", configured: true, plan_info: { name: "Creator" } },
    });
    createSubscription.mockResolvedValue({
      subscription_id: "sub_1", key_id: "rzp_test_k", plan: { name: "Creator" },
    });

    await useAppStore.getState().startUpgrade();

    expect(createSubscription).toHaveBeenCalled();
    expect(loadRazorpay).toHaveBeenCalled();
    expect(openSubscriptionCheckout).toHaveBeenCalledWith(
      expect.objectContaining({
        keyId: "rzp_test_k",
        subscriptionId: "sub_1",
        email: "a@b.com",
      })
    );
    // Pending stays true across the modal's lifetime.
    expect(useAppStore.getState().billingActionPending).toBe(true);
  });

  test("refuses and warns when billing is not configured", async () => {
    useAppStore.setState({ billingStatus: { plan: "free", configured: false } });
    await useAppStore.getState().startUpgrade();
    expect(createSubscription).not.toHaveBeenCalled();
    expect(openSubscriptionCheckout).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalled();
  });

  test("dismissing the checkout modal clears the pending state (no broken UI)", async () => {
    useAppStore.setState({ billingStatus: { plan: "free", configured: true, plan_info: {} } });
    createSubscription.mockResolvedValue({ subscription_id: "s", key_id: "k", plan: {} });
    let opts;
    openSubscriptionCheckout.mockImplementation((o) => { opts = o; });

    await useAppStore.getState().startUpgrade();
    expect(useAppStore.getState().billingActionPending).toBe(true);

    opts.onDismiss();
    expect(useAppStore.getState().billingActionPending).toBe(false);
  });

  test("a failed subscription create clears pending and surfaces an error", async () => {
    useAppStore.setState({ billingStatus: { plan: "free", configured: true, plan_info: {} } });
    createSubscription.mockRejectedValue(new Error("502"));
    await useAppStore.getState().startUpgrade();
    expect(openSubscriptionCheckout).not.toHaveBeenCalled();
    expect(useAppStore.getState().billingActionPending).toBe(false);
    expect(toast.error).toHaveBeenCalled();
  });

  test("successful payment refetches status and reflects the paid plan", async () => {
    useAppStore.setState({ billingStatus: { plan: "free", configured: true, plan_info: {} } });
    createSubscription.mockResolvedValue({ subscription_id: "s", key_id: "k", plan: {} });
    getBillingStatus.mockResolvedValue({
      plan: "creator", subscription_status: "active", configured: true, plan_info: {},
    });
    let opts;
    openSubscriptionCheckout.mockImplementation((o) => { opts = o; });

    await useAppStore.getState().startUpgrade();
    await opts.onSuccess();

    expect(getBillingStatus).toHaveBeenCalled();
    expect(useAppStore.getState().billingStatus.plan).toBe("creator");
    expect(useAppStore.getState().isPaidPlan()).toBe(true);
    expect(useAppStore.getState().billingActionPending).toBe(false);
  });
});

describe("cancelPlan", () => {
  test("cancels and refetches status", async () => {
    useAppStore.setState({
      billingStatus: { plan: "creator", subscription_status: "active", configured: true },
    });
    cancelSubscription.mockResolvedValue({ ok: true });
    getBillingStatus.mockResolvedValue({
      plan: "creator", subscription_status: "cancelling", configured: true,
    });

    await useAppStore.getState().cancelPlan();

    expect(cancelSubscription).toHaveBeenCalled();
    expect(useAppStore.getState().billingStatus.subscription_status).toBe("cancelling");
    expect(useAppStore.getState().billingActionPending).toBe(false);
  });
});
