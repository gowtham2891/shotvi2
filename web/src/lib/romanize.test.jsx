// The browser copy of the Telugu -> English-letters rules must spell every shared case like the renderer
// (tests/v2/romanize_cases.json, checked in Python by tests/v2/test_romanize.py).
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { romanize } from "./romanize";

const cases = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../../tests/v2/romanize_cases.json"), "utf8"));

describe("romanize (same as the renderer)", () => {
  it.each(cases)("$te -> $en", ({ te, en }) => {
    expect(romanize(te)).toBe(en);
  });
});
