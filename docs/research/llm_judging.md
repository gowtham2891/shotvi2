# Research: LLM judgment of subjective clip quality (2026-10-14)

Source: evaluation research pass (web only). Question: why do our pointwise 1–5 Gemini scores cluster at 3.5–4.5
and barely track the founder's ratings (Spearman ≈ 0.2), and what works instead.

## Findings
1. **Central-tendency compression is a documented judge failure.** Absolute ratings pile up near the midpoint;
   pairwise comparison is less affected (arXiv 2604.11581; 2605.16386). Pairwise beats direct scoring for NLG
   quality (PairS, arXiv 2403.16950; Liusie et al. 2405.05894; RankJudge 2605.21748).
2. **Biases:** position bias (MT-Bench: only 65% of verdicts survive order swap, arXiv 2306.05685; 2305.17926);
   verbosity and self-preference; judges prefer AI-styled text, near-zero correlation with humans on subjective
   dimensions (arXiv 2608.23705). Rubric judges are also position-biased (2602.02219).
3. **Consistency ≠ validity.** Test-retest > 0.95 yet kappa 33–41 points below raw agreement (arXiv 2606.19544);
   "Rating Roulette" (2510.27106). A bigger model fixes neither compression nor validity (matches our Pro result).
4. **Calibration techniques, ranked:** rubric anchors + reference examples help but are "not always sufficient"
   (LongJudgeBench 2606.01629); few-shot gains modest (+0.04 at 20 shots, 2508.12792); CoT before score: no
   consistent gain (Bavaresco et al., 2406.18403); probability-weighted scores (G-Eval 2303.16634) beat the mode
   but need logprobs; 0–5 scale gave the best ICC (2601.03444); a panel of 3 small models from different families
   beat GPT-4 alone at 1/7 the cost (PoLL, 2404.18796).
5. **Pairwise/tournament ranking:** PRP (2306.17563), RankGPT (2304.09542), setwise (2310.09497). Round-robin +
   Bradley–Terry is more reliable than Elo (ACL 2025 "Recipes for LLM Rankings"; Arena-Lite EMNLP 2025).
   N=10 → 45 pairs (90 with order swap); merge-sort/PairS ≈ 25–35 comparisons; well under ₹5/episode on Flash.
6. **Learning from ~100–300 labels:** a small calibrator on LLM outputs beats stuffing labels into the prompt
   (CORDIAL, 2609.29807; logistic regression over LLM outputs strong at tens of shots, 2408.03414). With 89
   items Spearman SE ≈ 0.11; > 5–10 features or per-series tuning overfits.
7. **LLMs judging humor is weak:** funniness correlation with humans 0.17–0.27 (AAAI 2026 Oogiri); pairwise joke
   judging 58% vs human consensus (HumorGen 2604.09629); punchline detection in stand-up ≤ 51% (CMCL 2025);
   New Yorker captions 30 points behind humans (Hessel et al. ACL 2023). LLMs explain jokes well (HumorBench
   2507.21476) but do not predict whether they land. Our 0.2 matches the field.

## Ranked changes to our review step (validate on the 89 ratings, leave-one-series-out, chance-corrected metrics)
1. Pairwise round-robin per episode → Bradley–Terry score (both orderings; "which clip would the founder post?").
   < ₹5/episode. Target Spearman ≥ 0.45 vs founder overall; top-3 precision of yes.
2. Binary "would post" with 6–8 founder-rated exemplars (3 yes, 3 no with reasons) retrieved per series/type;
   logprob or 5-sample vote. Validate: AUC (SE ≈ 0.06 at n=89; < 0.1 change is noise).
3. Logistic/ordinal calibrator on LLM outputs (BT score, sub-scores, length, laughter count, clip type), ≤ 6
   features, L2. ₹0. Reject if the gain disappears across folds.
4. Panel of 2–3 cheap models from different families on the pairwise decision. 2–3× cost of #1.
5. Active labelling: send the founder the 10–15 clips where #1 and #3 disagree most.

Evidence strength: 1 and the humor findings strong; 2–3 supported at small n; 4–5 extrapolations.
