# Prompts for selection_v2

One file per Gemini call, added as each stage is built (Stage 1B episode pass, Stage 2 beat sheets, Stage 4 scoring,
Stage 5 critic, Stage 6 on-screen text). Stage 1A makes no model call, so this directory is empty for now.

Rule (spec section 11.2): every prompt puts the transcript + episode_facts FIRST, byte-identical across calls, so
Gemini's implicit cache hits; the varying part (scene, candidates, question) comes after.
