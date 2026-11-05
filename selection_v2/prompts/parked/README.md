# Parked (founder, 2026-11-04): not run, test later

- `stage3_clips_pick_beat.md` + `stage3_scene_clips_pick_beat.schema.json`: Stage 3's end.hold names WHAT the clip holds
  on (a (sound) line id, "next_line" or "none") and code looks up its time; the transcript then also shows each sound
  line's end time (`stage3_find.line_text`). Code branch kept in `stage3_find.validate_clip` (used only when an answer
  has `end.hold`). Founder doubt: sound tags miss nods and looks; such beats come only from watching the video.
- Idea not built: an "ending check" per clip that watches a short window around the last word at 4 fps / better
  quality and gives the hold in seconds from the window start (≈ ₹0.8 per clip).

The live prompt is `../stage3_clips.md` (v7: end.hold_until in m:ss, beats-without-words line, PART 2 / (sound) times
pointer), as run on Couple Goals scenes 2, 7, 8, 10 (founder ratings 3 yes / 1 maybe).
