# Shotvi decision log

Newest first within a date. Owner: `product-lead`. `Founder` = the human founder's call. Numbers marked HYP in
`docs/spec/clip_spec.md` are provisional until `evaluation-engineer` validates them.

## Open founder questions (as of 2026-09-27)

Each has a provisional default already in the spec (status `PENDING`), so engineering is not blocked. The F-numbers
are referenced from `clip_spec.md`.

| F | Question | Recommendation (current default) |
|---|---|---|
| F1 | Publishable-rate targets for tiers: ≥ 80% of `recommended` and ≥ 60% of `worth_a_look` rated publishable? (spec Q3) | Accept. The top tier is what creators trust first. |
| F2 | If nothing clears the bar, show up to 3 best as `below_threshold` (labelled), or show nothing? (Q6) | Show up to 3, labelled. An empty screen reads as a broken product; a clear label keeps trust. |
| F3 | Sensitive content (health/finance/medical claims, allegations, politics, religion): flag only, or hide or disclaim? The strategist also asks whether to add a default "consult a doctor" style disclaimer. (A6) | Flag only in this phase and let the creator decide. Disclaimers are a rendering-phase decision. Later, offer a per-channel "brand-safe" filter that hides flagged clips. |
| F4 | Cost ceiling per audio-hour for choosing the LLM provider (decision #8). | Let `evaluation-engineer` show the quality vs cost table first, then set a ceiling. There is no default number until the table exists. |
| F5 | Crude or explicit lines. (§11) | Keep and score on craft. Drop the line in tightening when the clip works without it; otherwise flag `crude_language`. Never hide. |
| F6 | Should the pipeline propose long-range stitches (setup and payoff > 5 min apart; gold g10 is 45 min)? (S5) | Allow ≤ 1 per clip, flagged, never in `recommended`, and build no dedicated search. No creator Short does this, and the meaning risk is higher. |
| F7 | Host-led clips (the host's line is the best part). (§11) | Allow and score on merit, flagged `host_led`. Later, a per-channel "guest-only" preference. |
| F8 | Different moments with the same theme (e.g. two fan-craze stories). (Q8) | Show both, link them, and flag the lower one `similar_theme`. The creator picks. |
| F9 | 90–120s stories: post whole or split into parts? (§11) | Post whole, up to the 120s soft max. No auto-split. 4/19 creator Shorts are 98–115s. |
| F10 | Add a secondary `relatable` tag? (§11) | Add it as optional and non-ranking, then track it in eval. Revisit once more labels exist. |

## 2026-09-27 (spec 0.2, schema 1.1)

Evidence: 19 creator Shorts mapped to episode word indices (`data/anchors/*`, all high confidence) and 55 gold picks
(`data/gold/*`). The data comes from 2 podcasts and 2 editors, so changes loosen toward flags rather than add hard rejects.

| # | Decision | Why | Who |
|---|---|---|---|
| 18 | **T4 start: ≤ 4-word lead-in allowance (any words); a code fail flags `rough_start` and the judge decides.** Was ≤ 2 filler/conjunction words, code reject. | Only 12/19 creator starts are on a natural boundary. Editors drop spoken lead-ins ("Actually నేను మొన్న ఒక \| పూరి జగన్నాథ్…"). With the allowance 17/19 pass; the 2 misses follow unpunctuated sentence-final verbs, which is a transcript artefact rather than a bad cut. | product-lead (evaluation-engineer evidence) |
| 19 | **T5 end: ≤ 2 trailing words allowed; a code fail flags `rough_end` and the judge's C3 decides.** Was a code reject. | 15/19 → 17/19. Editors drop a trailing "కదా"/"అది". The remaining misses are complete clauses without punctuation. | product-lead |
| 20 | **The T2 judge sees 5 source words on each side of every cut edge.** | Looser boundaries (#18, #19) raise the risk of silently dropping sentence-final negation. The judge must see what was cut. | product-lead |
| 21 | **Skip threshold 20s → 30s (HYP).** | The 162 creator gaps have no natural break (median 2.3s; 20–30s: 8, 30–60s: 6, > 60s: 4). At 30s every creator Short and gold clip is ≤ 3 blocks. The number is a reporting line for "a sub-thought was dropped", not a discovered category. | product-lead |
| 22 | **Block cap: ≤ 3 normal, 4 flagged `many_jumps`, ≥ 5 rejected.** Supersedes #10's hard 3. | Under the old 20s rule, 7spOv2MtDnc had 5 blocks. The evidence is thin, so there is one block of headroom behind a flag. The montage ban (S3, judge) is unchanged. | product-lead |
| 23 | **Reordering: up to 2 backward jumps anywhere, moved duration ≤ 30s.** Supersedes #11 (one cold open at position 0, ≤ 10s). New edit-note type `reorder`. | 2 of 3 creator reorders move a conclusion line to the end (jnNwSIge0LU, 7spOv2MtDnc; the latter has 2 backward jumps). The one creator cold open (dFFP12qq6FI) is about 22–25s. Gold cold opens are 1–8s. | product-lead |
| 24 | **Cold-open replay: remove by default, replay allowed (HYP).** No other overlaps. | Creators removed the moved line in 3/3 reorders. Gold replays in 5/8. Both look publishable, so this needs a human A/B, not a rule. | product-lead |
| 25 | **R3 (take teaser moments from the body) is FIRM.** Confirms #17. | 10/19 Shorts also match the opening teaser, and all resolved to the body. | product-lead |
| 26 | **Long-range stitch (> 300s jump) is provisionally allowed, ≤ 1 per clip, flagged, capped at `worth_a_look`.** PENDING F6. | Gold g10 joins a setup to its payoff 45 min later. No creator Short does this (max forward gap 100s, max region 276s). | product-lead (strategist raised) |
| 27 | **Taste calls get flag-based provisional defaults** (crude, host-led, similar themes, 90s+ stories, `relatable`). PENDING F5, F7–F10. | The flags let the founder's later decision be a filter change rather than a pipeline change. | product-lead (strategist raised) |
| 28 | **Schema 1.0 → 1.1 (additive):** `derived_params.skip_s` in header (1.0 files are read at 20s, so existing anchors and gold stay valid); edit note `reorder`; 7 new flags; optional `related[]` and `tags[]`. | This is a minor bump per the versioning rule. Existing anchor and gold files validate against 1.1 unchanged. | product-lead |
| 29 | **Unchanged after checking:** R2 300s span (creators 44–276s), keep-ratio flag < 0.25 (creators 0.30–0.88), length guardrails (creators 38–115s, median 72s), natural-boundary gap 0.5s (failed starts have 0.01–0.08s gaps, so lowering it would not help). | The evidence does not contradict them. | product-lead |
| 30 | **All numeric rules are named config keys** (`clip_spec.md` §10), recorded per run in `pipeline.config`. | Tuning should not need code changes, and runs must be comparable. | product-lead |

## 2026-09-26

| # | Decision | Why | Who |
|---|---|---|---|
| 1 | **Clip length is content-driven.** There is no target length. Guardrails apply to the *edited* length: hard min 15s, soft max 120s (needs a written justification above it), hard max 150s. | Editors cut a moment as long as it needs. Creator Shorts run 41–114s after tightening, and each editor has their own range (42–68s vs 41–114s), so a fixed target would encode one editor's taste. | Founder (principle, initial numbers); product-lead (numbers confirmed, soft-max justification rule) |
| 2 | **Clip count is content-driven.** Show every clip above a quality threshold, in tiers (`recommended` ≥ 4.0, `worth_a_look` ≥ 3.5, provisional). There is no fixed N. | A dense episode deserves more clips than a rambling one. Thresholds get calibrated against human ratings once the judge is trusted. | Founder (principle); product-lead (tiers, provisional thresholds) |
| 3 | **Creators review every proposal before publishing.** Optimise precision at the top of the list. Every proposal explains itself (hook line, why it works, risks) and is editable at word level. | Review time is the creator's real cost. Being able to trust the top 5 matters more than finding everything. | Founder |
| 4 | **Telugu only for now.** Tenglish code-mixing is normal input. Titles must contain Telugu script. | Focus. Tenglish is how the audience talks. | Founder |
| 5 | **Clip selection first.** Rendering, reframing, captions and backend come later. | The quality of selection decides whether the product is worth anything. | Founder |
| 6 | **Creator Shorts are positive anchors, not the answer key.** Recall is measured against them. Precision is judged on each clip's merits (human labels plus a calibrated LLM judge). | Earlier attempts that optimised overlap alone dropped genuinely good clips. | Founder |
| 7 | **Old index/alignment JSONs deleted.** The Short transcripts (`creator_shorts/*_scribe_transcript.json`) are the ground truth. `evaluation-engineer` builds the Short → episode mapping into `data/anchors/`. | The old alignments were not trustworthy. Re-deriving them from transcripts is auditable. | Founder |
| 8 | **LLM provider chosen by bake-off (Gemini vs Claude)**: same pipeline, quality vs cost table from `evaluation-engineer`. The code stays provider-agnostic (`shotvi/llm/`). | Evidence over preference. Cost per audio-hour matters for a real product. | Founder |
| 9 | **A clip = source region + word-level keep list (ordered pieces), not a single start/end.** | Creators jump-cut regions of 44–267s down to about half, in 8–26 pieces. A start/end format cannot express what they publish. | product-lead (from CLAUDE.md evidence) |
| 10 | (Numbers superseded by #21, #22.) **Stitching limit is counted in blocks, not pieces.** Max 3 blocks (≤ 2 jumps). A jump is a backward move or a skip > 20s of source (HYP). Each jump needs a written reason. | Tightening creates many pieces. The old "≤ 3 pieces" rule would forbid normal creator edits while still needing to stop montages. Supersedes "stitched clips ≤ 3 pieces" in the evaluation-engineer brief. | product-lead |
| 11 | (Superseded by #23.) **Reordering is limited to one cold-open piece at the front, ≤ 10s (HYP)**, from the same region. | Creators did this in 3 of 19 Shorts. Freer reordering risks changing meaning and is hard to review. | product-lead |
| 12 | **Meaning preservation is a hard reject rule.** An edit must not flip negation, drop a claim-changing qualifier, or pair an answer with the wrong question. | Word-level cuts can put words in a guest's mouth, and that is a trust and legal risk for the creator. Telugu negation often sits in the sentence-final verb, so it is easy to cut. | product-lead |
| 13 | **Word indices are authoritative. Transcripts are pinned by sha256.** Seconds are derived. | Indices make gold, anchors and proposals directly comparable. Re-transcription silently shifts them, so the hash detects it. | product-lead |
| 14 | **One shared clip core for proposals, gold and anchors** (`docs/spec/clip_proposal.schema.json`). | Recall, keep-list F1 and overlap need the same units on all three sides. | product-lead |
| 15 | **A clip with hook, standalone, or payoff ≤ 2 is never shown**, whatever its overall score. | A clip that fails any one of these is not publishable, and a high average must not hide that. | product-lead |
| 16 | **Hard min stays 15s** even though no creator Short is under 40s. | 19 Shorts from 2 editors is too narrow to raise the floor. Eval reports judged quality by length bucket, and we revisit then. | product-lead |
| 17 | **Episode teaser montages are not clip sources.** Take the moment from the episode body (HYP rule R3). | `TBlm6wcij94` opens with a montage whose lines also open creator Shorts. For example, "మన MS ధోని గారు … farm" (which opens Short `FDApN1_cA7U`) appears at 43s (word ~111, teaser) and again at 4137s (word ~10971, body). Teasers have no context and may carry a music bed. Anchor mapping must also prefer the body occurrence. | product-lead |
