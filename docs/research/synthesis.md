# Clip selection v2: what the research says we should build (2026-10-14)

Four research passes (methods, industry, emotion/engagement, LLM judging) are in docs/research/. This is the
synthesis: evidence first, then a design that follows from it, then how to validate it on our own data.

## What the evidence says about what we have

| Our v1 design choice | Evidence | Verdict |
|---|---|---|
| One LLM call reads the whole episode and picks start, end, cold open and drops | Zero-shot GPT-4o over 30-min podcasts scored at the frequency baseline (0.265 vs 0.224, Rhapsody); end-to-end ASR prompting scored 0.54 vs 4.01 for a staged pipeline (HIVE) | Weakest known design; the founder's instinct is right |
| No separate step for choosing openings and endings | Removing HIVE's opening/ending stage dropped hook rate 0.71 -> 0.28, the only ablated boundary result in the literature | Add a dedicated boundary step |
| Payoff-first moment finding | Industry's #1 user complaint about OpusClip/Klap: "captures setup, misses punchline", "cuts before the joke lands"; editors: hook -> minimal context -> payoff | Keep |
| Cuts snapped to shot changes, whole-chunk trims only, never inside a highlight | HIVE's rules match exactly; Spotify trims to complete sentences | Keep |
| Review = one call scoring all clips 1–5 | Absolute LLM scores compress to the midpoint (documented); pairwise beats direct scoring (PairS, Liusie 2024, RankJudge 2026); position bias 35% | Replace with pairwise ranking |
| Expecting an LLM to tell whether a joke lands | Frontier models correlate 0.17–0.27 with humans on funniness; 51% on stand-up punchlines; video LLMs get WORSE on humor given video (v-HUB) | Stop expecting it; our 0.2 is the field's number |
| Bigger model / video gate / speaker names to fix ranking | All consistent with the above: none of these address compression or the humor gap | Already measured; don't retry |
| No use of our 89 founder ratings in scoring | Small calibrators on LLM outputs beat prompting at 5–100 labels (CORDIAL); fine-tuned small models beat zero-shot LLMs with in-domain labels (Rhapsody 0.49 vs 0.265) | Use the ratings as training signal |
| Laughter only as a transcript tag | audio+text F1 84.5 vs text-only 77.8 on sitcom humor (FunnyNet-W); laughter detection is the most mature "it landed" signal | Add audio laughter as a feature |
| Emotion not modelled | High-arousal emotions drive sharing (Berger & Milkman; Tellis on video); Gemini labels emotion at category level about as well as humans agree with each other (r 0.37–0.55 vs 0.31) | Add emotion as a label + feature; the genre hypothesis is untested anywhere, our data would be the first test |

Limits to keep in mind: content-only engagement prediction tops out near rank correlation 0.7 (SnapUGC); popularity
is partly random (MusicLab); no benchmark exists for Telugu scripted comedy; every vendor "virality score" is
unvalidated; the 26 channel Shorts are a sanity check, not a label.

## The design that follows (v2, staged)

```
A  Scenes            code: shot cuts -> scenes (already built: shotvi/select/scenes.py)                 free
B  Scene cards       per scene, one small Flash call: transcript lines + 2–4 keyframes ->
                     who is there, what happens, beats that land (line ids), emotion + intensity,
                     laughter/reaction evidence                                                        ~₹4
C  Beat -> clip      per beat, one small call with the scene card + ~60 lines before/after:
                     candidate STARTS (earliest line the payoff needs) and ENDS (landing line +
                     reaction); pick the best start by hook/clarity, best end by landing.
                     Code snaps to first word / shot changes / hold rules (already built)             ~₹4
D  Rank              pairwise "which would the founder post?" round-robin over the episode's
                     clips (both orderings) -> Bradley–Terry score; small logistic calibrator on
                     BT score + emotion fit + laughter + length + intensity, trained on founder
                     ratings (leave-one-series-out); genre map learned from the data              ~₹3–5
E  Output            ranked candidate list + verdict; creator picks (human-in-loop as ranking)      free
```
Expected cost ~₹12–16 per 20-min episode, about what v1 costs, because every call is small with low thinking.
Audio laughter (free, YAMNet/Omine) and emotion labels feed D; Gemini video stays off the humor-judging path and
is used, if at all, only to verify a reaction hold.

Why each stage: A/B stop the model skipping scenes (scene-local calls cannot skip); C is the ablated boundary
stage; D is the only ranking method with evidence of beating pointwise scores, and the calibrator is the only way
the founder's ratings reach the ranking.

## How we validate it, on data we already have
1. Emotion labels on the 89 rated clips (free with the judge agent, or ~₹3 with Flash). Test: does
   intensity x genre_match x lands separate yes from no (target Spearman >= 0.4; n=89 has >95% power at 0.4)?
   Also: does the best-rated emotion differ by genre (per-genre fit with CIs)?
2. Pairwise ranking on the 30 rated v1_test clips (~₹5): Spearman of BT score vs founder overall; top-3 precision
   of yes. Target >= 0.45 (a jump of ~0.3 is needed to be significant at n=89).
3. Calibrator on existing features (₹0): leave-one-series-out AUC/Brier; reject if the gain disappears on any fold.
4. Then the staged pipeline on the 4 new episodes (+ the 26 GT candidates once rated): missed-yes and
   precision-at-top vs v1. Spotify's template: offline human pairwise -> later online A/B with real views.

What to stop doing: retesting bigger models or more video for the humor judgment; trusting any 1–5 score;
hiding clips on a score threshold.
