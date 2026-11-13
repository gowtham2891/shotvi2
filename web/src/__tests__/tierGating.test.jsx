/**
 * Features #17/#19/#21 — frontend tier surfacing: premium-preset marking
 * (mirror of api/tiers.PREMIUM_PRESETS) and billing-status entitlement fields.
 */
import { isPremiumPreset, PREMIUM_PRESET_IDS, CAPTION_STYLES } from "@/api/renders";

describe("premium preset marking (mirror of backend)", () => {
  test("feature #16 premium presets are gated; free presets are not", () => {
    // Mirror of api/tiers.PREMIUM_PRESETS — the Replix paid-tier styles.
    for (const id of ["punch", "cove", "spotlight", "reel", "noir"]) {
      expect(isPremiumPreset(id)).toBe(true);
    }
    // Free: the 5 free feature-#16 styles + the originals.
    for (const id of ["classic", "yellow", "minimal", "dark", "hormozi-caps",
                      "bold-yellow", "hormozi", "typewriter", "split-color"]) {
      expect(isPremiumPreset(id)).toBe(false);
    }
  });

  test("every premium id is a real preset in the dropdown", () => {
    for (const id of PREMIUM_PRESET_IDS) {
      expect(CAPTION_STYLES.some((s) => s.id === id)).toBe(true);
    }
  });
});
