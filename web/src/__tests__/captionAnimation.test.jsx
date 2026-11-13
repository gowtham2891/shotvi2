/**
 * Feature #15 — caption animation preset reaches the export payload only when
 * non-default (karaoke), keeping karaoke exports byte-identical.
 */
import { buildRerenderRequest } from "@/api/renders";
import { animationClass } from "@/components/editor/ElementBodies";

describe("buildRerenderRequest caption_animation", () => {
  test("non-default presets are serialized", () => {
    for (const a of ["pop", "fade", "slide-up", "none"]) {
      expect(buildRerenderRequest({ captionAnimation: a }).caption_animation).toBe(a);
    }
  });

  test("karaoke (default) is omitted → byte-identical payload", () => {
    expect("caption_animation" in buildRerenderRequest({ captionAnimation: "karaoke" })).toBe(false);
    expect("caption_animation" in buildRerenderRequest({})).toBe(false);
  });

  test("unknown values are omitted (backend renders its karaoke default)", () => {
    expect("caption_animation" in buildRerenderRequest({ captionAnimation: "disco" })).toBe(false);
    expect("caption_animation" in buildRerenderRequest({ captionAnimation: null })).toBe(false);
  });
});

describe("preview animationClass", () => {
  test("maps each preset to its CSS class; slide-up supported; legacy bounce aliased", () => {
    expect(animationClass("pop")).toBe("anim-pop");
    expect(animationClass("fade")).toBe("anim-fade");
    expect(animationClass("slide-up")).toBe("anim-slide-up");
    expect(animationClass("bounce")).toBe("anim-bounce");
    expect(animationClass("karaoke")).toBe("");
    expect(animationClass("none")).toBe("");
  });
});
