/**
 * Feature #10 — align & distribute (pure math over normalized centers).
 */
import { alignPatches, distributePatches } from "@/lib/alignDistribute";

const els = (specs) =>
  specs.map(([id, x, y, locked = false]) => ({ id, x, y, locked }));

const THREE = els([
  ["a", 0.2, 0.1],
  ["b", 0.5, 0.4],
  ["c", 0.8, 0.9],
]);

describe("alignPatches", () => {
  test("align left/right/center on x", () => {
    expect(alignPatches(THREE, ["a", "b", "c"], "x", "min")).toEqual({
      a: { x: 0.2, y: 0.1 },
      b: { x: 0.2, y: 0.4 },
      c: { x: 0.2, y: 0.9 },
    });
    expect(alignPatches(THREE, ["a", "b", "c"], "x", "max").b.x).toBe(0.8);
    expect(alignPatches(THREE, ["a", "b", "c"], "x", "center").a.x).toBeCloseTo(0.5, 10);
  });

  test("align top/bottom/center on y leaves x untouched", () => {
    const p = alignPatches(THREE, ["a", "b", "c"], "y", "min");
    expect(p.c).toEqual({ x: 0.8, y: 0.1 });
  });

  test("fewer than 2 unlocked targets → empty patch set", () => {
    expect(alignPatches(THREE, ["a"], "x", "min")).toEqual({});
    const locked = els([["a", 0.2, 0.1, true], ["b", 0.5, 0.4, true], ["c", 0.8, 0.9]]);
    expect(alignPatches(locked, ["a", "b", "c"], "x", "min")).toEqual({});
  });

  test("locked elements are excluded from both the span and the patches", () => {
    const mixed = els([["a", 0.2, 0.1], ["b", 0.5, 0.4], ["c", 0.9, 0.9, true]]);
    const p = alignPatches(mixed, ["a", "b", "c"], "x", "max");
    expect(p.c).toBeUndefined();
    expect(p.a.x).toBe(0.5); // max over unlocked only, not 0.9
  });
});

describe("distributePatches", () => {
  test("even center spacing, outermost pinned", () => {
    const uneven = els([["a", 0.1, 0.5], ["b", 0.15, 0.5], ["c", 0.9, 0.5]]);
    const p = distributePatches(uneven, ["a", "b", "c"], "x");
    expect(p.a.x).toBeCloseTo(0.1, 10);
    expect(p.b.x).toBeCloseTo(0.5, 10); // midpoint
    expect(p.c.x).toBeCloseTo(0.9, 10);
  });

  test("needs at least 3 targets", () => {
    expect(distributePatches(THREE, ["a", "b"], "x")).toEqual({});
  });

  test("y-axis distribution keeps x", () => {
    const p = distributePatches(THREE, ["a", "b", "c"], "y");
    expect(p.b).toEqual({ x: 0.5, y: 0.5 });
  });
});
