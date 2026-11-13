/**
 * PUBLIC PRICING — the pre-login landing page's source of truth.
 *
 * This is deliberately NOT in data/mockData.js. mockData is the offline dev
 * fixture set (REACT_APP_USE_MOCKS), and public prices were being served from
 * it unconditionally — a production page reading a mock file. Prices are real
 * product copy, so they live in a real module.
 *
 * It is also deliberately NOT an API read. The landing page is pre-login and
 * GET /billing/status requires auth, so there is no authenticated call to make
 * here. That makes this a static constant BY NECESSITY — the trade-off is that
 * it must be kept in lockstep with api/tiers.py :: TIERS by hand.
 *
 * Mirrors api/tiers.py (the entitlement table) at:
 *   free    → Starter: 10 source min, 30 render min, watermark, 24h expiry,
 *             no premium presets
 *   creator → Creator: 120 source min, 300 render min, no watermark, 30-day
 *             expiry, premium presets
 * `id` matches the backend plan key so a card can be correlated with the plan
 * a signed-in user actually holds.
 *
 * ONE paid plan. The old ₹1,499 "Studio" tier was removed: it matched nothing
 * purchasable on the server (Razorpay drives free ⇄ creator only), so the page
 * was quoting a price no one could pay.
 */
export const PRICING_TIERS = [
  {
    id: "free",
    name: "Starter",
    price: "₹0",
    period: "forever",
    tag: "Free",
    features: [
      "10 source minutes / month",
      "30 render minutes / month",
      "Telugu + Tenglish captions",
      "Full word-level editor",
      "Watermarked exports",
      "Clips available for 24 hours",
    ],
    highlighted: false,
    cta: "Start free",
  },
  {
    id: "creator",
    name: "Creator",
    price: "₹499",
    period: "per month",
    tag: "Most popular",
    features: [
      "120 source minutes / month",
      "300 render minutes / month",
      "No watermark on exports",
      "All premium caption presets",
      "Clips kept for 30 days",
      "Everything in Starter",
    ],
    highlighted: true,
    cta: "Get Creator",
  },
];

export default PRICING_TIERS;
