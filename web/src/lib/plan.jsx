/**
 * Plan-key semantics for the frontend — ONE place that decides "is this user
 * paid?".
 *
 * Why this exists: the paid plan key changed from `studio` to `creator` when
 * the ladder collapsed to a single paid plan, but `studio` is still the live
 * value in existing Redis `user:{id}` hashes (api/tiers.py aliases it rather
 * than migrating). Every hardcoded `plan === "studio"` in the UI would have
 * silently shown a paying subscriber the free-tier state after the rename —
 * and the same comparison rewritten to `=== "creator"` would break the legacy
 * subscribers instead.
 *
 * So the test is inverted: a plan is paid unless it is a KNOWN free key. That
 * is correct for `creator` today, correct for the legacy `studio` alias, and
 * correct for any future paid key without another frontend edit. The only
 * values that must be enumerated are the free ones, and those are stable.
 */
const FREE_PLAN_KEYS = new Set(["", "free", "starter", "none"]);

export const isPaidPlanKey = (plan) =>
  plan != null && !FREE_PLAN_KEYS.has(String(plan).trim().toLowerCase());

/**
 * The check components should actually use: is THIS ACCOUNT paid?
 *
 * Prefers resolved entitlements over plan identity, because /billing/status
 * already resolves legacy plan keys server-side — `watermark: false` is true
 * for a legacy `studio` subscriber regardless of what the key string says, and
 * it stays true if the key set changes again. The plan-key test is kept as the
 * first branch only because it is the field that exists on every response
 * shape, including ones that predate the entitlement fields.
 *
 * The failure this guards against is asymmetric and worth spelling out: read a
 * paying user as free and they get watermark warnings and an Upgrade button
 * that 409s against their own live subscription. So every signal that means
 * "paid" is ORed, and only the absence of all of them means free.
 */
export const isPaidAccount = (billingStatus) => {
  if (!billingStatus) return false;
  if (isPaidPlanKey(billingStatus.plan)) return true;
  // Entitlement signal: only a paid tier exports without a watermark.
  if (billingStatus.watermark === false) return true;
  // Subscription signal: a live (or cancelling-but-still-active) subscription.
  const sub = String(billingStatus.subscription_status || "").toLowerCase();
  return sub === "active" || sub === "cancelling";
};

// Display name for the current plan. The server owns the copy (plan_info.name
// comes straight from api/tiers.py), so prefer it; the literals are only the
// pre-fetch / billing-not-configured fallback.
export const PAID_PLAN_FALLBACK_NAME = "Creator Plan";

export const planDisplayName = (billingStatus) =>
  billingStatus?.plan_info?.name ||
  (isPaidAccount(billingStatus) ? PAID_PLAN_FALLBACK_NAME : "Free");
