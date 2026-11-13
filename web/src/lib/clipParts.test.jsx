import { describe, expect, it } from "vitest";
import { bringBack, bringBackAround, cutLine, cutsOf, edgeLimits, moveEdge, partsLength, setClipEnd, setClipStart, tidy } from "./clipParts";

const L = (id, start, end) => ({ id, start, end });
const LINES = [L("a", 100, 104), L("b", 105, 108), L("c", 109, 112), L("d", 113, 118), L("e", 150, 155), L("f", 156, 160)];
const TWO = [{ start: 100, end: 118 }, { start: 150, end: 160 }];

describe("clip parts", () => {
  it("lists the cuts", () => {
    expect(cutsOf(TWO)).toEqual([{ i: 0, start: 118, end: 150 }]);
    expect(partsLength(TWO)).toBe(28);
  });

  it("brings a cut back", () => {
    expect(bringBack(TWO, 0)).toEqual([{ start: 100, end: 160 }]);
    expect(bringBackAround(TWO, 130, 135)).toEqual([{ start: 100, end: 160 }]);
    expect(bringBackAround(TWO, 101, 103)).toBe(TWO);
  });

  it("cuts a line in the middle of a part, with the pause around it", () => {
    expect(cutLine(TWO, LINES[1], LINES)).toEqual([{ start: 100, end: 104 }, { start: 109, end: 118 }, { start: 150, end: 160 }]);
  });

  it("cuts the first or last line of a part by moving its edge", () => {
    expect(cutLine(TWO, LINES[0], LINES)[0]).toEqual({ start: 105, end: 118 });
    expect(cutLine(TWO, LINES[3], LINES)[0]).toEqual({ start: 100, end: 112 });
  });

  it("cutting a part's only line removes the part, never the whole clip", () => {
    const one = [{ start: 100, end: 118 }, { start: 150, end: 155 }];
    expect(cutLine(one, LINES[4], LINES)).toEqual([{ start: 100, end: 118 }]);
    expect(cutLine([{ start: 150, end: 155 }], LINES[4], LINES)).toEqual([{ start: 150, end: 155 }]);
  });

  it("start or end past a part drops it", () => {
    expect(setClipStart(TWO, 152)).toEqual([{ start: 152, end: 160 }]);
    expect(setClipEnd(TWO, 110)).toEqual([{ start: 100, end: 110 }]);
    expect(setClipStart(TWO, 90)[0].start).toBe(90);
  });

  it("joins parts that touch", () => {
    expect(tidy([{ start: 150, end: 160 }, { start: 100, end: 150.03 }])).toEqual([{ start: 100, end: 160 }]);
  });

  it("keeps a moved edge away from its neighbour and under the length cap", () => {
    const lim = edgeLimits(TWO, 0, "end", 40, 220, 180);
    expect(lim).toEqual([100.2, 150]);
    expect(moveEdge(TWO, 0, "end", 170, lim)[0].end).toBe(150);
    const capped = edgeLimits(TWO, 1, "end", 40, 400, 40); // 28 s now: the end can add 12 s at most
    expect(capped[1]).toBe(172);
  });
});
