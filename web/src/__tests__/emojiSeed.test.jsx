/**
 * Feature #30 — seedEmojiOverlays (store). A clean clip gets one timed emoji
 * overlay per Gemini suggestion; a clip that already has emoji (restored draft,
 * or a second call) is never double-seeded.
 */
import { useAppStore } from "@/store/useAppStore";

const TRANSCRIPT = [
  { id: "w0", start: 0.0, end: 0.4, text: "ee" },
  { id: "w1", start: 0.4, end: 0.8, text: "okka" },
  { id: "w2", start: 0.8, end: 1.2, text: "AI" },
  { id: "w3", start: 1.2, end: 1.6, text: "tool" },
  { id: "w4", start: 1.8, end: 2.2, text: "mee" },
  { id: "w5", start: 2.2, end: 2.6, text: "life" },
];

const captionEl = () => ({
  // y is an arbitrary sample — this suite is about emoji seeding/timing, not
  // caption placement. Kept off the default anchor on purpose.
  id: "el_caption_1", type: "caption", x: 0.5, y: 0.61, scale: 1, rotation: 0,
  visible: true, locked: false,
  props: { presetId: "bold-yellow", font: "Noto Sans Telugu", fontSize: 0.055, animation: "karaoke", pill: {} },
});

const reset = (extra = {}) => {
  useAppStore.setState({
    transcript: TRANSCRIPT.map((w) => ({ ...w })),
    transcriptEdits: { wordEdits: {}, mergedGroups: [], lineSplits: [], lineRealignments: {} },
    elements: [captionEl()],
    currentClip: { emoji_suggestions: [{ emoji: "💡", word_index: 2 }, { emoji: "🔥", word_index: 5 }] },
    ...extra,
  });
};

const emojiEls = () => useAppStore.getState().elements.filter((el) => el.type === "emoji");

describe("seedEmojiOverlays", () => {
  test("seeds one timed emoji element per suggestion (from currentClip)", () => {
    reset();
    useAppStore.getState().seedEmojiOverlays();
    const em = emojiEls();
    expect(em).toHaveLength(2);
    expect(em.map((e) => e.props.emoji)).toEqual(["💡", "🔥"]);
    // each carries a display window
    expect(em[0].props.start).toBeCloseTo(0.0, 3);
    expect(em[0].props.end).toBeCloseTo(1.6, 3);
    // the caption element is untouched
    expect(useAppStore.getState().elements.some((e) => e.type === "caption")).toBe(true);
  });

  test("is idempotent — a second call does not double-seed", () => {
    reset();
    useAppStore.getState().seedEmojiOverlays();
    useAppStore.getState().seedEmojiOverlays();
    expect(emojiEls()).toHaveLength(2);
  });

  test("skips when an emoji element already exists (restored draft)", () => {
    reset({
      elements: [
        captionEl(),
        { id: "el_emoji_kept", type: "emoji", x: 0.4, y: 0.5, scale: 1, rotation: 0,
          visible: true, locked: false, props: { emoji: "🎉", start: 0, end: 1 } },
      ],
    });
    useAppStore.getState().seedEmojiOverlays();
    const em = emojiEls();
    expect(em).toHaveLength(1);
    expect(em[0].props.emoji).toBe("🎉"); // the draft's emoji, not a re-seed
  });

  test("no suggestions / no transcript → no-op", () => {
    reset({ currentClip: { emoji_suggestions: [] } });
    useAppStore.getState().seedEmojiOverlays();
    expect(emojiEls()).toHaveLength(0);

    reset({ transcript: [] });
    useAppStore.getState().seedEmojiOverlays();
    expect(emojiEls()).toHaveLength(0);
  });

  test("override suggestions (mock mode) are used over currentClip", () => {
    reset({ currentClip: null });
    useAppStore.getState().seedEmojiOverlays([{ emoji: "🚀", word_index: 0 }]);
    const em = emojiEls();
    expect(em).toHaveLength(1);
    expect(em[0].props.emoji).toBe("🚀");
  });

  test("seeding does NOT push a history frame (part of the initial document)", () => {
    reset({ history: { past: [], future: [] } });
    useAppStore.getState().seedEmojiOverlays();
    expect(useAppStore.getState().history.past).toHaveLength(0);
  });
});
