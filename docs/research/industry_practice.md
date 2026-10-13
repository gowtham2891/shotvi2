# Research: how commercial auto-clippers pick clips (public evidence, 2026-10-14)

No vendor publishes its pipeline. Real engineering detail exists only in OpusClip's API blog + job post, two papers
(Spotify podcast previews; HIVE short-drama editor), and third-party failure reports.

| Product | Pipeline (as known) | Signals claimed | Validation | Known failures |
|---|---|---|---|---|
| OpusClip | Transcript-first LLM scoring; ClipAnything adds per-frame multimodal; models trained on user feedback + YouTube performance; job post: "preference optimization, data flywheels" | Hook, Flow, Value, Trend; hook strength, narrative completeness, emotional intensity, audio dynamics, quotability | Vendor-only: "70+ predicts above-median ~75% on talking-head"; admits training is talking-head/podcast; "multi-speaker content can produce single-speaker reaction clips" | "Listens, doesn't watch"; cuts before the thought completes; captures setup, misses punchline; "doesn't get comedic timing or sarcasm"; ~30% need trims |
| Klap | transcript → topics + hooks → 15–90 s | hook strength, emotional peak, pacing (marketing) | none | fails on visual/music-heavy/non-dialogue; group shots crop badly |
| Vizard, Munch, quso.ai (Bengaluru), Descript, Kapwing, Submagic | prompt/transcript driven; quso scores every clip 75–99 (uninformative) | unstated | none | marketing only |
| Eklipse (gaming) | kill-feed OCR, audio spikes, chat velocity | visual events, audio energy | "80–90% usable" (user feedback) | weak on talk streams |
| YouTube "Create a video highlight" | uses real engagement/view-time data | retention | real data, not a predictor | "won't be 100% standalone" |

Sources: https://www.opus.pro/blog/viral-moment-detection-api , https://fastcompanyme.com/?p=32331 ,
https://www.eesel.ai/blog/opusclip-reviews , https://www.twoaveragegamers.com/opusclip-review-2026-great-tool-wrong-hype-for-gamers/ ,
https://blog.stateshift.com/?p=3969 , https://klap.app/tools/viral-clip-generator , https://dupple.com/reviews/klap-ai

## Non-product sources with real engineering
- **Spotify podcast previews** (arXiv 2505.23908): Gemini 1.5 Pro over a sentencized, timestamped transcript +
  few-shot, returns start/end + explanation; trimmed to the last complete sentence. Human eval LLM >= expert system
  81%; 6-week A/B: +4.6% evaluation time per user. The only published validation on real engagement.
- **HIVE / DramaAD** (arXiv 2507.02790): staged; opening/ending chosen by a separate LLM pass; pruning never cuts
  inside a highlight; ASR-only editing smoothness 0.65 vs HIVE 4.48 vs human 6.84; random openings/endings drop
  hook rate 0.71 -> 0.28.

## Indian-language / scripted / comedy
No product targets Telugu/Tamil/Hindi serials or comedy timing; only language lists (marketing).

## What editors say
First 1–2 s decide; opinion beats information; 25–45 s outperformed longer; hook -> minimal context -> payoff;
"listen with screen off" standalone test; reject clips that stop just before the answer. AI "leads with setup
instead of payoff… starts or ends a beat early".

## Lessons for Telugu web series / comedy
1. Payoff-first is right; industry's #1 complaint is setup-without-punchline.
2. Transcript-only fails on scripted drama; scene-level structure fixes most of it (HIVE).
3. Choose openings and endings as a SEPARATE scored step (0.28 -> 0.71).
4. Correct ASR with on-screen subtitles/OCR and face-to-speaker matching where subtitles are burned in.
5. Validate on the founder's ratings, not a virality number (Spotify's path: offline human pairwise -> online A/B).
6. Multi-speaker scenes produce single-speaker reaction fragments; filter with diarization-aware checks.
7. LLM for span selection with explanation, then snap to complete sentences/shots (Spotify).
8. Keep the human in the loop as ranking, not approval (~30% of outputs need trims even at vendors).
