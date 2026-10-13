# Research: emotion triggers and short-video engagement (2026-10-14)

## Why viewers stop and finish
- Hook window is real; thresholds are folklore. TikTok: ~50% of an ad's impact lands in the first 2 s, ~90% by 6 s.
  Vendor "3-second retention" benchmarks contradict each other. (weak evidence)
- Platform ranking = watch time + shares (Meta creator blog; Mosseri). "Would share" is a legitimate proxy.
- Suspense/curiosity: mechanism supported; a 496-person Douyin survey found suspense/surprise did NOT predict
  immersion (beta 0.09) while novelty (0.41) and entertainment value (0.28) did (Frontiers 2023).
- Text-only hooks predict poorly: 3,392 Douyin videos, 24 sentiment/emotion features: R2 ~ 0 held-out
  (Frontiers Comm 2026). Transcript sentiment alone is useless.

## Emotion and virality
- Berger & Milkman 2012: high-arousal emotions (awe, anger, anxiety, amusement) raise sharing; low-arousal sadness
  lowers it; arousal is causal in lab experiments.
- Tellis et al. 2019 (YouTube ads): drama (characters, plot, surprise) -> emotion -> sharing; positive high-arousal
  emotions (inspiration, warmth, amusement, excitement) boost shares; only ~7–10% of ads evoke them; sharing peaks
  at moderate length (~1.2–1.7 min).
- Humor: humorous videos viewed more favourably (2,911 videos); humorous memes shared more; dark humor draws
  toxic comment sentiment (arXiv 2606.00046).
- Ceiling: MusicLab (Salganik 2006): popularity is partly random; expect view counts of 26 Shorts to be noisy.

## Predicting engagement from content
- SnapUGC / VQualA 2025 (120k Snapchat videos, ECR = P(watch > 5 s)): winner SROCC 0.707; VideoLLaMA2
  (frames+text+audio) 0.695 vs Qwen2.5-VL (no audio) 0.664. Content-only ceiling ~0.7 rank correlation.
- Beyond Views (5.3M YouTube videos): engagement must be normalised for duration.
- Emotion labelling: Gemini-2.0-flash reproduces human per-clip emotion ratings at r 0.37–0.55 (human–human
  0.31); good at category level (50–55% vs 17% chance) (arXiv 2505.12746). Laughter detection is the most mature
  signal (Omine 2024, Gillick 2021); FunnyNet-W "funny moment = clip followed by laughter". emotion2vec: no
  Telugu test; Telugu text sentiment XLM-R 79%; no Tenglish emotion dataset.

## Genre dependence
Viewers map genres to distinct target emotions (comedy -> funny, drama -> sad); no "content-type-aware ranking"
paper found. The founder's hypothesis is plausible but UNTESTED; Shotvi's own data would be the first test.

## Validation with ~90 rated clips + 26 Shorts
Spearman 0.5 at alpha 0.05 needs n ~ 33 (80% power); n=90 is ample for rho >= 0.3. The 26 Shorts detect only
rho >~ 0.5 and views are exposure-confounded: use within-channel log-view rank as a sanity check only.

## Proposed emotion-aware score
Labels per clip: primary_emotion in {laugh, aww/warmth, shock/surprise, tension, tears, none}; intensity 1–5;
lands_in_clip; hook_emotion_at_3s. Genre target map learned from founder ratings (comedy -> laugh; romcom ->
aww/laugh; family drama -> tears/aww/tension; promo -> tension with an open gap).
Signals: laughter segments (count, peak, position in last 20%); shot-change rate, close-up ratio, facial-expression
peaks; Gemini category-level emotion/intensity (not lexicon sentiment); length-normalised.
Score: emotion_fit = intensity x genre_match x lands_in_clip, as a gate + tie-breaker until validated.
Validation: label the 90 rated clips; Spearman(emotion_fit, founder overall) target >= 0.4; precision@top-10;
founder re-labels 30 primary emotions (need kappa >= 0.6); per-genre fit; 26 Shorts as sanity; ablate signals.
