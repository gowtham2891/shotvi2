# All prompts, grouped by pipeline (copies, 2026-10-16)

These are COPIES for reading. The code reads the originals in `prompts/`; if a prompt changes there, re-copy it here.

| Folder | Pipeline | Prompts, in the order they run |
|---|---|---|
| 0_transcript_prep | Before any clip pipeline: fix transcript words and speaker names | asr_context, asr_relisten (word fixes), speaker_fix (speaker names) |
| 1_v1 | v1 (the one rated 6-7 of 10 good) | ws_discover_b_payoff (find + cut all clips, one call) -> ws_review (score each clip, title) |
| 2_ws3_three_calls | Founder's 3-call design (judged worse: fragments) | ws3_context (saved brief + transcript) -> ws3_payoffs -> ws3_hook (per payoff) -> ws3_clip (per payoff) |
| 3_merged | v1 + payoff list (built, not run paid) | v1 prompts, then ws3_context + ws3_payoffs -> merged_moment (per missed payoff) -> ws_review (ws_review_ctx = optional version that sees the transcript) |
| 4_emotion_check | Checker on already-made clips (built, not used: founder said the reason must drive selection) | ws_emotion |
| 5_emotion_first | Emotion-first selection (run 2026-10-16, Rs 73.67) | ws3_context (saved brief + transcript) -> ws_emotion_find (one call per episode) -> ws_emotion_build (one call per moment). example_build_call_as_sent.txt = one real build call exactly as Gemini received it |
| 8_webseries_old_tests | Earlier web-series tests, not in use | ws_discover (pre-v1), ws_discover_b_all / _scene / _setup (tested variants), ws_gate + video_* (video checks, not adopted) |
| 9_podcast | Podcast pipeline (preserved at git tag podcast-pipeline-v1) | brief, discover*, opening, ending, hook, tighten, joins, rank, score, ... |
