/**
 * Public pricing — the landing page's price list is REAL data, and the ladder
 * is exactly two tiers.
 *
 * Two separate bugs are pinned here:
 *
 * 1. Landing.jsx imported PRICING_TIERS from data/mockData.js unconditionally
 *    — no USE_MOCKS guard — so the production, pre-login pricing section was
 *    served from the offline dev fixture file. The prices now live in
 *    data/pricing.js and mockData no longer exports them at all.
 *
 * 2. The page advertised a third ₹1,499 "Studio" tier that matched nothing
 *    purchasable on the server (Razorpay drives free ⇄ creator only). Quoting
 *    a price nobody can pay is worse than quoting none.
 *
 * The numbers themselves mirror api/tiers.py :: TIERS. There is no public
 * pricing endpoint (the landing page is pre-login and /billing/status requires
 * auth), so this constant is the contract — hence the assertions on the
 * entitlement figures, which are the part that silently rots.
 */
import fs from "fs";
import path from "path";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { PRICING_TIERS } from "@/data/pricing";
import * as mockData from "@/data/mockData";
import Landing from "@/pages/Landing";
import { LANDING } from "@/constants/testIds";

global.IS_REACT_ACT_ENVIRONMENT = true;

const SRC = path.join(__dirname, "..");

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

describe("pricing data lives outside the mock fixtures", () => {
  test("mockData no longer exports PRICING_TIERS — no dead export left behind", () => {
    expect(mockData.PRICING_TIERS).toBeUndefined();
    expect(Object.keys(mockData)).not.toContain("PRICING_TIERS");
  });

  test("Landing.jsx does not import from data/mockData at all", () => {
    const src = fs.readFileSync(path.join(SRC, "pages", "Landing.jsx"), "utf8");
    expect(src).toContain('from "@/data/pricing"');
    expect(src).not.toMatch(/from\s+["']@\/data\/mockData["']/);
  });
});

describe("the ladder is Starter + Creator", () => {
  test("exactly two tiers, keyed to the backend plan keys", () => {
    expect(PRICING_TIERS).toHaveLength(2);
    expect(PRICING_TIERS.map((t) => t.id)).toEqual(["free", "creator"]);
    expect(PRICING_TIERS.map((t) => t.name)).toEqual(["Starter", "Creator"]);
  });

  test("no Studio tier and no ₹1,499 price anywhere in the list", () => {
    const blob = JSON.stringify(PRICING_TIERS).toLowerCase();
    expect(blob).not.toContain("studio");
    expect(blob).not.toContain("1,499");
    expect(blob).not.toContain("talk to sales");
  });

  test("prices match the one purchasable plan (₹0 free, ₹499/mo Creator)", () => {
    const [starter, creator] = PRICING_TIERS;
    expect(starter.price).toBe("₹0");
    expect(creator.price).toBe("₹499");
    expect(creator.highlighted).toBe(true);
    expect(starter.highlighted).toBe(false);
  });

  // Mirrors api/tiers.py :: TIERS. If the backend budgets move, this fails and
  // points at the constant that has to move with them.
  test("Creator's feature list states what Creator actually grants", () => {
    const feats = PRICING_TIERS[1].features.join(" | ");
    expect(feats).toMatch(/120 source minutes/i);
    expect(feats).toMatch(/300 render minutes/i);
    expect(feats).toMatch(/no watermark/i);
    expect(feats).toMatch(/premium caption presets/i);
    expect(feats).toMatch(/30 days/i);
  });

  test("Starter's feature list states the free-tier limits, watermark included", () => {
    const feats = PRICING_TIERS[0].features.join(" | ");
    expect(feats).toMatch(/10 source minutes/i);
    expect(feats).toMatch(/30 render minutes/i);
    expect(feats).toMatch(/watermark/i);
    expect(feats).toMatch(/24 hours/i);
  });
});

describe("Landing renders the two tiers", () => {
  test("two pricing cards, both with a CTA; no studio card", async () => {
    await mount(
      <MemoryRouter>
        <Landing />
      </MemoryRouter>
    );

    const cards = container.querySelectorAll('[data-testid^="landing-pricing-"]');
    // Each tier contributes a card and a CTA under the same id prefix.
    expect(byTestId(LANDING.pricingCard("free"))).toBeTruthy();
    expect(byTestId(LANDING.pricingCard("creator"))).toBeTruthy();
    expect(byTestId(LANDING.pricingCard("studio"))).toBeNull();
    expect(byTestId(LANDING.pricingCta("free"))).toBeTruthy();
    expect(byTestId(LANDING.pricingCta("creator"))).toBeTruthy();
    expect(cards.length).toBe(4); // 2 cards + 2 CTAs

    // The pricing section must not render a two-of-three grid.
    const grid = byTestId(LANDING.pricingCard("free")).parentElement;
    expect(grid.className).toContain("md:grid-cols-2");
    expect(grid.className).not.toContain("md:grid-cols-3");

    expect(container.textContent).not.toMatch(/studio/i);
    expect(container.textContent).not.toContain("1,499");

    await unmount();
  });
});
