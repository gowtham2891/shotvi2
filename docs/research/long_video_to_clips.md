# Research: long video → Shorts pipelines (2023–2026), survey (2026-10-14)

## 1. LLM pipelines over transcripts
- Single-pass "transcript → one LLM call → timestamps" is what popular repos do (FunClip, MIT, ~6.4k stars;
  AI-Youtube-Shorts-Generator). No benchmark evidence. https://github.com/modelscope/FunClip
- **PodReels** (Adobe/Columbia 2023 → Adobe Express Clip Maker): transcript + diarization + audio events; GPT-4
  returns SENTENCE IDS; user "Refine" adds the leading question. N=10 study: time 16.7→9.9 min, quality 4.1→5.6;
  "funny" was its weakest criterion (41.7%). https://arxiv.org/html/2311.05867v2
- **HIVE** (2025; short drama → clips; closest to our problem): multi-stage: ASR corrected by OCR subtitles → face
  clustering + speaker↔character matching → shots → Qwen2-VL merges shots into scenes, Gemini Flash writes scene
  descriptions → DeepSeek-R1 scores scenes vs audience highlight rules → adjacent scenes merged → GPT-4o picks
  OPENING/ENDING pairs → GPT-4o prunes. DramaAD (800+ episodes, 500 pro-edited clips): VEI 4.01 vs 0.54 for
  end-to-end ASR prompting (human 6.35); **ablating the opening/ending stage drops hook rate 0.71 → 0.28.**
  Scene-level only; no code. https://arxiv.org/html/2507.02790
- **Repurpose-10K** (AAAI-25, MIT code): 120k clips re-edited by Opus users; CLIP + PANNs audio + caption
  embeddings with start/end regression; avg tIoU only 11.6 (task far from solved). Good clips are self-contained
  sub-topics that "start with engaging content, end with fitting remarks". Pro clips 7.68, Opus-user 7.29,
  random 3.13. https://arxiv.org/html/2412.08879v1
- **Rhapsody** (podcast highlights from replay graphs, 13k episodes): zero-shot GPT-4o hit-rate 0.265 ≈ frequency
  baseline 0.224; fine-tuned Llama-3.2-1B + HuBERT audio reaches 0.49. One-shot LLM scanning of a 30-min
  transcript is weak; in-domain supervision wins. https://arxiv.org/html/2505.19429
- Opus virality score: trained on "hundreds of thousands of (clip, performance) pairs", ~75% above-median hit at
  score ≥ 70 on talking heads; vendor-reported. https://www.opus.pro/blog/viral-moment-detection-api

## 2. Boundary refinement / standalone-ness
Thin literature. HIVE's separate opening/ending stage is the only ablated evidence. Repurpose-10K's re-edited
boundaries are the only public GT for where editors move start/end. No deep-learning reaction-shot detector found.

## 3. Classic moment retrieval / highlight detection
Lighthouse (Apache-2.0) reproduces Moment-DETR/QD-DETR/CG-DETR (QVHighlights MR R1@0.5 54–65). Trained on vlogs/news
with visual queries; nothing models dialogue, setup→punchline or Telugu. Useful at most as a visual-energy side
signal. https://arxiv.org/html/2408.02901

## 4. Audio cues
- **Laughter as label/feature works for sitcoms:** FunnyNet-W (Friends): audio-only F1 73.7, text-only 77.8,
  audio+text 84.5, all three 88.2. https://arxiv.org/html/2401.04210v1  Funny-scene extraction (AAAI-26, Amazon):
  laughter detector + humor tagger + logistic regression; 87% of extracted scenes intended-funny, 74% judged
  funny; English only. https://arxiv.org/html/2602.15381
- Caveats: needs a laugh track or live audience; open detectors are fine at clip level but poor at exact
  timestamps (arXiv 2609.12642); StandUp4AI text-only humor F1 ≈ 42 (2505.18903).
- Arousal/prosody: audio features help WHEN FINE-TUNED (Rhapsody) but did not help zero-shot Gemini
  (0.327 text vs 0.293 text+audio).

## 5. Multimodal LLMs watching video
Cost is fine (~263 video tokens/s; a 60-min episode ≈ $0.28 on Flash). But humor judgment is weak everywhere:
51% punchline identification in stand-up (CMCL 2025); v-HUB: video LLMs drop sharply on humor given video instead
of text (2509.25773); FunQA/MVBench "far from satisfactory" on humor timing; Gemini+audio did not beat Gemini text
(Rhapsody). No Telugu-LLM humor evaluation exists.

## Ranked shortlist for Telugu web series / comedy
1. **HIVE-style staged pipeline**: scene segmentation → scene descriptions (transcript + keyframes) → payoff-first
   scene scoring → SEPARATE opening/ending selection pass → pruning pass. Only design with ablated evidence that a
   dedicated boundary stage drives hook quality (0.28 → 0.71).
2. **Laughter/reaction channel as a feature, not a label** (FunnyNet-W recipe) for shows with live audience; use
   it to rank, snap boundaries with shot changes.
3. **Learned reranker on founder ratings** (Rhapsody lesson): with a few hundred rated clips, a small fine-tuned
   scorer over transcript + audio features likely beats zero-shot LLM ranking.
4. Repurpose-10K-style boundary regression as an auxiliary start/end prior (weak: tIoU ≈ 12).
5. Direct Gemini video input only as a verifier of visual beats/reaction holds, not as the humor judge.

Gaps: no paper evaluates standalone-ness or reaction-shot holds; no open benchmark for Indian-language scripted
comedy; all "viral" claims vendor-reported.
