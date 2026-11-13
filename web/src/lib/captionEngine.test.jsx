// The live preview must lay out captions exactly as the renderer burns them: same cues (start, end) and the same
// lines, for real clips under several settings (tests/v2/caption_parity.json is written by the Python renderer).
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { cues, displayTok, withDefaults, wrap } from "./captionEngine";

const cases = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../../tests/v2/caption_parity.json"), "utf8"));

describe("caption layout matches the renderer", () => {
  it.each(cases)("$name", ({ settings, breaks, words, expect: want }) => {
    const s = withDefaults(settings);
    const got = cues(words, s, breaks).map((c) => ({
      start: c.start,
      end: c.end,
      lines: wrap(c.words.map((w) => displayTok(w.tok, s)), s),
    }));
    expect(got).toEqual(want);
  });
});
