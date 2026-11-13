/**
 * Feature #30 — emoji overlay builder (lib/emojiOverlays.js).
 *
 * Pins the codepoint↔asset contract (must match services/emoji.py exactly) and
 * the suggestion→timed-element mapping: each {emoji, word_index} becomes an
 * emoji element timed to the caption LINE holding that word, one emoji per line.
 */
import {
  emojiCodepoint,
  emojiAssetUrl,
  buildEmojiOverlayElements,
  EMOJI_DEFAULT_Y,
} from "@/lib/emojiOverlays";

describe("emojiCodepoint / emojiAssetUrl (mirror services/emoji.py)", () => {
  test("single-scalar emoji → hex codepoint", () => {
    expect(emojiCodepoint("🔥")).toBe("1f525");
    expect(emojiCodepoint("🚀")).toBe("1f680");
  });

  test("variation selector U+FE0F is stripped (Twemoji naming)", () => {
    expect(emojiCodepoint("❤️")).toBe("2764");
    expect(emojiCodepoint("⚡")).toBe("26a1");
  });

  test("asset URL points at the shared public/emoji PNG", () => {
    expect(emojiAssetUrl("🔥")).toBe("/emoji/1f525.png");
    expect(emojiAssetUrl("❤️")).toBe("/emoji/2764.png");
  });
});

// 8-word transcript → two 4-word caption lines (default grouping).
const TRANSCRIPT = [
  { id: "w0", start: 0.0, end: 0.4, text: "ee" },
  { id: "w1", start: 0.4, end: 0.8, text: "okka" },
  { id: "w2", start: 0.8, end: 1.2, text: "AI" },     // line 0
  { id: "w3", start: 1.2, end: 1.6, text: "tool" },
  { id: "w4", start: 1.8, end: 2.2, text: "mee" },
  { id: "w5", start: 2.2, end: 2.6, text: "life" },   // line 1
  { id: "w6", start: 2.6, end: 3.0, text: "ni" },
  { id: "w7", start: 3.0, end: 3.6, text: "marchestundi" },
];

describe("buildEmojiOverlayElements", () => {
  test("maps each suggestion to an emoji element timed to its caption line", () => {
    const els = buildEmojiOverlayElements(
      [{ emoji: "💡", word_index: 2 }, { emoji: "🔥", word_index: 5 }],
      TRANSCRIPT
    );
    expect(els).toHaveLength(2);

    const [a, b] = els;
    expect(a.type).toBe("emoji");
    expect(a.props.emoji).toBe("💡");
    expect(a.props.codepoint).toBe("1f4a1");
    // line 0 spans words 0-3 → [0.0, 1.6]
    expect(a.props.start).toBeCloseTo(0.0, 3);
    expect(a.props.end).toBeCloseTo(1.6, 3);
    expect(a.y).toBe(EMOJI_DEFAULT_Y);

    // line 1 spans words 4-7 → starts at 1.8
    expect(b.props.emoji).toBe("🔥");
    expect(b.props.start).toBeCloseTo(1.8, 3);
  });

  test("one emoji per line — a second suggestion on the same line is dropped", () => {
    const els = buildEmojiOverlayElements(
      [{ emoji: "💡", word_index: 2 }, { emoji: "🔥", word_index: 3 }], // both line 0
      TRANSCRIPT
    );
    expect(els).toHaveLength(1);
    expect(els[0].props.emoji).toBe("💡"); // first suggestion wins
  });

  test("an out-of-range / unmatched anchor is skipped", () => {
    const els = buildEmojiOverlayElements(
      [{ emoji: "💡", word_index: 99 }, { emoji: "🔥", word_index: 5 }],
      TRANSCRIPT
    );
    expect(els).toHaveLength(1);
    expect(els[0].props.emoji).toBe("🔥");
  });

  test("empty suggestions / empty transcript → no elements", () => {
    expect(buildEmojiOverlayElements([], TRANSCRIPT)).toEqual([]);
    expect(buildEmojiOverlayElements([{ emoji: "🔥", word_index: 0 }], [])).toEqual([]);
    expect(buildEmojiOverlayElements(null, TRANSCRIPT)).toEqual([]);
  });

  test("elements carry a full store-element shape (draggable/exportable)", () => {
    const [el] = buildEmojiOverlayElements([{ emoji: "🚀", word_index: 0 }], TRANSCRIPT);
    expect(el).toMatchObject({
      type: "emoji",
      x: 0.5,
      scale: 1,
      rotation: 0,
      visible: true,
      locked: false,
      props: { emoji: "🚀", height: 0.12, opacity: 1, wordIndex: 0 },
    });
    expect(el.id).toMatch(/^el_emoji_/);
  });
});
