import { client, toApiError } from "@/api/client";

// Billing API (PHASE 2 BUILD 2 — Razorpay). Mirrors the backend routes in
// api/main.py. Every call carries the Supabase token via the shared axios
// client, exactly like the job routes.
//
// There is exactly ONE paid plan: Creator. `plan` is 'free' | 'creator', with
// 'studio' surviving as a legacy alias in Redis hashes written before the
// rename — never compare against a plan literal here, use lib/plan.js.

// GET /billing/status → { plan, subscription_status, subscription_id,
// configured, plan_info, watermark, render_minutes_used/budget,
// source_minutes_used/budget, expiry_hours }. `configured=false` means
// Razorpay env is absent on the server and the UI shows a "not set up" state.
export async function getBillingStatus() {
  try {
    const { data } = await client.get("/billing/status");
    return data;
  } catch (err) {
    throw toApiError(err, "Could not load billing status");
  }
}

// POST /billing/subscription → { subscription_id, key_id, plan } — everything
// Razorpay Checkout.js needs to open the payment modal.
export async function createSubscription() {
  try {
    const { data } = await client.post("/billing/subscription");
    return data;
  } catch (err) {
    throw toApiError(err, "Could not start checkout");
  }
}

// POST /billing/cancel → requests cancellation; the webhook confirms the flip
// back to free.
export async function cancelSubscription() {
  try {
    const { data } = await client.post("/billing/cancel");
    return data;
  } catch (err) {
    throw toApiError(err, "Could not cancel your subscription");
  }
}
