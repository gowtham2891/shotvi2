# Shotvi clip spec

Version 0.2 (2026-09-27). Owner: `product-lead`. Format: `output_schema.md` (schema 1.1). Log of changes:
`../decisions.md` (#18–#30).

## Changes in v2 (read this first if you implemented v0.1)

Evidence: the 19 creator Shorts mapped to word indices (`data/anchors/*`) and 55 gold picks (`data/gold/*`).
Every changed number is also listed in §10 as a config key.

| # | Rule | v0.1 | v0.2 | Why |
|---|---|---|---|---|
| 1 | T4 start | Natural boundary, or ≤ 2 words after one if only fillers/conjunctions were dropped. Code fail = **reject**. | Natural boundary, or ≤ **4** words after one (any words, T2 still applies). Code fail = **flag `rough_start`** + judge check; judge "starts mid-thought" = reject. | Creators drop spoken lead-ins such as "Actually నేను మొన్న ఒక \| పూరి జగన్నాథ్…". A ≤ 4-word allowance passes 17/19 starts (v0.1: 13/19). The other 2 misses follow an unpunctuated sentence-final verb, which is a transcript artefact. |
| 2 | T5 end | Followed by a natural boundary. Code fail = **reject**. | Natural boundary, or ≤ **2** words before one. Code fail = **flag `rough_end`** + judge C3; judge "ends mid-thought" = reject. | 2 creator ends drop a trailing "కదా" / "అది". 17/19 pass (v0.1: 15/19). The other 2 misses are complete clauses with no punctuation. |
| 3 | T2 judge input | Kept text only. | Kept text **plus 5 source words on each side of every cut edge**. | Looser boundaries must not hide a dropped "కాదు" or "లేదు". The judge has to see what was cut. |
| 4 | Skip threshold (S4) | 20s | **30s** | The 162 creator gaps show no natural break (median 2.3s, long tail up to 100s). At 30s no creator Short or gold clip exceeds 3 blocks. At 20s one Short had 5. |
| 5 | Block cap (S1) | ≤ 3 blocks, else **reject** | ≤ 3 is normal; **4 = flag `many_jumps`**; ≥ 5 = reject | This is thin evidence, so there is one block of headroom behind a flag instead of a hard wall. |
| 6 | Reorder form (O1) | One cold-open piece at position 0 only | Up to **2 backward jumps anywhere** in the clip. Each is a `cold_open` (moved line plays first) or a `reorder` (e.g. a conclusion moved to the end). | 2 of the 3 creator reorders move a conclusion line to the end (jnNwSIge0LU, 7spOv2MtDnc). One Short has 2 backward jumps. |
| 7 | Reorder size (O2) | Cold open ≤ 10s, and T4/T5 at both of its ends | **Moved duration ≤ 30s** (defined in §3). Inner edges of moved pieces are judged by T3, not by code. | The creator cold open in dFFP12qq6FI is about 22–25s. Gold cold opens are 1–8s. |
| 8 | Cold-open replay (O4) | Remove or replay, "check how creators do it" | **Default: remove** (creators 3/3). Replay is allowed for a cold open only (gold does it in 5/8). Pieces never overlap otherwise. | Both are observed. Removing is what published editors did. |
| 9 | Teasers (R3) | HYP | **FIRM** | 10/19 Shorts also match the episode's opening teaser, and all resolved to the body. Teasers lack context and may carry music. |
| 10 | Long-range stitch | Impossible (R2 span ≤ 300s) | **New S5, pending founder**: ≤ 1 jump > 300s of source, flagged `long_range_stitch`, tier capped at `worth_a_look`. The pipeline need not search for them. | Gold g10 joins a setup to a payoff 45 min later. No creator Short does this. |
| 11 | Taste calls | Not specified | **§11 provisional defaults, pending founder**: crude lines, host-led clips, similar themes, 90s+ stories, the `relatable` tag. | The strategist raised them. Defaults are flag-don't-hide. |
| 12 | Schema | 1.0 | **1.1**: header `derived_params.skip_s`; edit-note type `reorder`; flags `rough_start, rough_end, many_jumps, long_range_stitch, crude_language, host_led, similar_theme`; optional `related[]` and `tags[]`. | Additive. 1.0 files stay valid, with blocks derived at 20s. |

Unchanged, and consistent with the new evidence: R2 (300s span; creators 44–276s), T9 (keep ratio; creators 0.30–0.88),
length guardrails (creators 38–115s, median 72s), natural-boundary pause of 0.5s (lowering it would not fix the
misses: gaps at failed starts are 0.01–0.08s), and inner cuts may be mid-sentence (269/370 creator inner edges are natural).

---

A Shotvi clip is **one moment** from the episode (a story, an answer, an argument, a joke) that a Telugu viewer
who never saw the podcast will stop for, understand, and feel was worth it. The clip is a **source region** plus
a **word-level keep list** (ordered pieces), cut as long as the moment needs and no longer.

**Status tags.** `FIRM` = enforce now; changing it needs a decision-log entry. `HYP` = hypothesis; a starting number
that `evaluation-engineer` validates, after which it becomes FIRM, changes, or goes. `PENDING` = provisional default
awaiting the founder (§11). **On fail**: `reject` = never shown; `hide` = not shown, kept in the run file for eval;
`flag` = shown with a warning to the reviewer; `score` = lowers the score, no hard action.

## 0. Definitions (all checkable from the transcript)

| Term | Definition |
|---|---|
| word index | 0-based index into the episode's `word_timestamps`. Ranges are inclusive `[start_word, end_word]`. |
| piece | One contiguous kept range. Duration = `words[end_word].end - words[start_word].start`. |
| pieces | Ordered list in **playback order**. Pieces do not overlap, except a replayed cold open (O4). |
| region | `[min start_word, max end_word]` over all pieces. Derived, never chosen separately. |
| edited duration | Sum of piece durations. Speech only: no handles, end cards, or music. Every length rule uses this. |
| jump | Between consecutive pieces A→B: a **backward jump** if `B.start_word <= A.end_word`; a **skip** if `words[B.start_word].start - words[A.end_word].end > skip_s` (30s, HYP). Smaller forward gaps are tightening cuts. |
| long-range jump | A jump whose source distance (`abs(words[B.start_word].start - words[A.end_word].end)`) > `long_range_s` (300s). See S5. |
| block | A maximal run of pieces with no jump. Contiguous and merely tightened clips are 1 block. `n_blocks = 1 + number of jumps`. |
| natural boundary | Between words i and i+1, if word i ends with `. ? ! ।`, or the speakers differ, or the gap is ≥ `natural_gap_s` (0.5s, HYP). |
| lead-in | For a clip start at word s: the words from the previous natural boundary up to s−1 (0 words if s is on a boundary). |
| tail | For a clip end at word e: the words from e+1 up to the next natural boundary (0 words if e is followed by one). |
| moved duration | `edited duration − D`, where D is the largest total duration of a subsequence of pieces (in playback order) whose `start_word`s strictly increase. 0 for clips in source order. |
| kept text | Words of all pieces joined in playback order. This is what the judge reads. |
| cut context | For every cut edge (clip start, clip end, and both sides of each inner cut), the 5 source words beyond it (`cut_context_words`). |

## 1. Region selection

| ID | Rule | Status | Check | On fail |
|---|---|---|---|---|
| R1 | One moment: the clip's point fits in one sentence ("X explains why Y", "story of how Z"). Two topics = two clips. | FIRM | judge | reject |
| R2 | Region span ≤ `region_span_max_s` (300s). If the clip has a long-range jump (S5), each side of it is measured separately. Creator regions: 44–276s (median 131s). | HYP | code | reject |
| R3 | Episode teasers: if the episode opens with a teaser montage, take the moment from the body (full context, no music bed). Never propose both. | FIRM | code (overlap of kept text) + judge | reject the teaser copy |
| R4 | Signals (Q→A pairs, laughter, speaker turns) generate candidates. They do not show quality. Laughter alone never qualifies a region. | FIRM | review of `why_it_works` | score |

## 2. Tightening (the keep list)

Editors cut roughly half of a region (creator keep ratio 0.30–0.88, median 0.54; 3–15 pieces). Tighten the same way.

**Drop:** listener backchannels inside the main speaker's thought ("ఉమ్", "అవును", "హా", "Right", "makes sense");
fillers and false starts; immediate repetitions; dead air; host tangents; side explanations the payoff does not
need; spoken lead-ins before the real first line ("Actually నేను మొన్న ఒక…", "Okay, ఇప్పుడు…"); crude lines that
add nothing (§11); anything under a censor beep.

**Keep:** the question or setup the payoff answers; every referent the kept text relies on (the name behind
"ఆయన", the thing behind "అది"); the payoff line in full; words that carry meaning in Telugu grammar, especially
sentence-final negation and verb endings ("కాదు", "లేదు", "-కూడదు"). Keep a backchannel or laugh only if it adds
rhythm at a punchline.

| ID | Rule | Status | Check | On fail |
|---|---|---|---|---|
| T1 | Cuts are only between words, expressed as word-index pieces. Nothing finer. | FIRM | schema | reject |
| T2 | **Meaning preservation.** The kept text says what the speaker meant. It does not flip a negation, drop a qualifier that changes the claim, attach an answer to a question it did not answer, or put words in someone's mouth. The judge is given the kept text **and the cut context**. | FIRM | judge ("does the edit change what anyone meant?") | reject |
| T3 | The kept text reads as natural speech: no garbled joins or half-words at inner cuts, including around moved pieces. Inner cuts may fall mid-sentence to drop a filler or repeat. | FIRM | judge | reject |
| T4 | **Clean start.** Code passes if the lead-in is ≤ `lead_in_max_words` (4). Otherwise it flags `rough_start` and the judge answers "does the first played word sound like the start of a thought?". A no means reject. | FIRM principle; 4 words HYP | code + judge | flag / reject |
| T5 | **Clean end.** Code passes if the tail is ≤ `tail_max_words` (2). Otherwise it flags `rough_end` and the judge's C3 "is the last thought complete?" decides. A no means reject. | FIRM principle; 2 words HYP | code + judge | flag / reject |
| T6 | Dead air: no gap > 1.5s between consecutive words inside a piece unless a laughter event overlaps it. | HYP | code | flag |
| T7 | Over-chopping: more than 1 piece per 3s of edited duration. | HYP | code | flag |
| T8 | Under-tightening: the judge says the clip could lose ≥ 20% and keep hook, context, and payoff → `padded`. A 25s moment is not stretched to 45s. | HYP | judge | flag + score |
| T9 | Keep ratio (`edited duration / region span`) is reported, not enforced. Below 0.25 is flagged. | HYP | code | flag |

## 3. Reordering

Creators reordered 3 of 19 Shorts. One is a cold open (a line lifted to the front); two move a conclusion line to
the end so the clip closes on it. The rest of the clip plays in source order.

| ID | Rule | Status | Check | On fail |
|---|---|---|---|---|
| O1 | At most `backward_jumps_max` (2) backward jumps per clip, at any position. | HYP | code | reject |
| O2 | Moved duration ≤ `moved_max_s` (30s). | HYP | code | reject |
| O3 | A moved line intrigues or lands rather than confuses: a cold open's meaning is clear by the end, and a moved conclusion still answers the question it originally answered (T2). | FIRM | judge | reject |
| O4 | A moved line is removed from its original position by default. A cold open (piece 0) may instead be replayed at its original position; that overlap is the only one allowed. | HYP | code: every overlap involves piece 0 (its replay may span several later pieces, e.g. gold g03) | reject other overlaps |
| O5 | One `edit_notes` entry per backward jump, typed `cold_open` (the moved material plays first) or `reorder` (anywhere else), with `after_piece` set and a reason. The type is descriptive; code checks only the count. | FIRM | schema + code | reject |

## 4. Stitching (jumps and blocks)

Contiguous is the default. Tightening is not stitching. Stitching means a jump (skip or backward).

| ID | Rule | Status | Check | On fail |
|---|---|---|---|---|
| S1 | `n_blocks` ≤ 3 is normal. 4 flags `many_jumps`. ≥ 5 is rejected (`blocks_flag_above` 3, `blocks_max` 4). | HYP | code | flag / reject |
| S2 | Every skip has an `edit_notes` entry `{type: "skip", after_piece, reason}` saying what was skipped and why. Backward jumps are covered by O5. | FIRM | schema + code | reject |
| S3 | Allowed purposes: join a question to its answer, join a setup to its payoff, drop a tangent, cold open, move a conclusion to the end. A montage of separate moments is not allowed (R1). | FIRM | judge | reject |
| S4 | Skip threshold `skip_s` is 30s of source. Rationale: gaps up to 30s are the same beat being tightened. Past 30s a whole sub-thought (about 60+ words) is dropped, and the reviewer should be told what was dropped. The creator gap distribution has no natural break, so this is a reporting line, not a discovered category. | HYP | code | n/a |
| S5 | **Long-range stitch** (jump > 300s): at most `long_range_max` (1) per clip. Allowed only for question→answer or setup→payoff/callback. The note says what sits in between. Flag `long_range_stitch`; the tier is capped at `worth_a_look`. The pipeline is not required to search for these; the rules apply if it proposes one. | PENDING (F6) | code + judge | reject if > 1 |

## 5. Length guardrails (on edited duration)

| Quantity | Value | Status | Rationale |
|---|---|---|---|
| Hard min | 15s | FIRM (founder) | Below this there is no room for setup and payoff. The shortest creator Short is 38s, but that reflects two editors' taste, so the floor stays low. Eval reports quality by length bucket. |
| Soft max | 120s | FIRM | The longest creator Short is 115s, and 4/19 are 98–115s. Above 120s the proposal needs `edit_notes` `{type: "length_over_soft_max", reason}` and is flagged. |
| Hard max | 150s | FIRM (founder) | At 3+ min it is long-form, not a Short. Reject. |
| Target | none | FIRM | Length follows the moment. Creator Shorts run 38–115s (median 72s), and each editor has their own range, so length must not be a model prior. |

Rendered clips will be a few seconds longer than edited duration (handles, end cards). Guardrails ignore this.

## 6. Hook, standalone, payoff, value (scored 1–5; anchors in `docs/content/labeling_guide.md`)

| ID | Rule | Status | Check | On fail |
|---|---|---|---|---|
| C1 | **Hook**: the clip opens on its hook. `hook_line` is the verbatim first played words (`hook_line.start_word == pieces[0].start_word`), spoken within ≤ `hook_within_s` (6s). Score: would a scrolling Telugu viewer stop in the first 3s? | FIRM (6s HYP) | code + judge | reject / score |
| C2 | **Standalone**: a first-time viewer understands who and what without the episode. An unresolved referent ("అది", "ఆయన", "అప్పుడు", "that thing") or an inside reference caps standalone at 2. | FIRM | judge; code flags demonstratives in the first 5 kept words | score / flag |
| C3 | **Payoff**: ends on a conclusion, punchline, reveal, or emotional peak. The last thought is complete (this is the judge half of T5). | FIRM | judge | score / reject (T5) |
| C4 | **Value / emotion**: the viewer learns, laughs, feels, or wants to argue. | FIRM | judge | score |
| C5 | **Overall**: "would the creator post this after at most small boundary nudges?". A clip with hook, standalone, or payoff ≤ 2 is never shown. | FIRM | code on scores | hide |

## 7. Avoid

| ID | Rule | Status | Check | On fail |
|---|---|---|---|---|
| A1 | No piece's time range overlaps a `[సెన్సార్డ్]` event. The region may contain one if the tightening drops it. | FIRM | code | reject |
| A2 | Low-confidence transcript: ≥ 4 consecutive kept words with `logprob < -0.7`. Reject if the run is inside the hook line, otherwise flag. | HYP | code | reject / flag |
| A3 | Sponsor reads, ads, channel promo. | FIRM | judge | reject |
| A4 | Episode intros, guest introductions, sign-offs, outros. | FIRM | judge | reject |
| A5 | Song / music events overlapping pieces (copyright risk). | HYP | code | flag |
| A6 | Sensitive content (health, finance or medical claims; allegations about named people; politics; religion). The creator decides. Crude language has its own flag (§11). | PENDING (F3) | judge | flag |
| A7 | Inside references that need earlier parts of the episode are covered by C2. | FIRM | judge | score |

## 8. Ranking, cut-off, count

There is no fixed number of clips. The list length comes from how many clips clear the quality bar.

| ID | Rule | Status | Check |
|---|---|---|---|
| Q1 | A candidate is **shown** only if it passes every reject rule, passes C5, has `overall ≥ T_show`, and is not a near-duplicate. | FIRM | code |
| Q2 | Tiers: `recommended` if `overall ≥ T_rec`, `worth_a_look` if `T_show ≤ overall < T_rec`. Start values `T_show = 3.5`, `T_rec = 4.0`. S5 caps long-range stitches at `worth_a_look`. | HYP | code |
| Q3 | Threshold calibration: set `T_rec`/`T_show` so that ≥ 80% of `recommended` and ≥ 60% of `worth_a_look` are rated publishable. | HYP; targets PENDING (F1) | eval |
| Q4 | Near-duplicate: if two candidates share > 50% of the smaller one's kept words, keep only the higher-scored one. | FIRM (50% HYP) | code |
| Q5 | Order: `overall` desc, then `hook` desc, then episode position. | FIRM | code |
| Q6 | Nothing clears `T_show`: return up to 3 best candidates as `below_threshold`, labelled as such. Never pad silently. | PENDING (F2) | code |
| Q7 | More than 1 shown clip per 2 min of audio logs a warning (the scorer is probably too lenient). The list is not truncated. | HYP | code |
| Q8 | Similar themes (different moments, same point or same kind of story from the same speaker) are **not** hidden. The lower-ranked clip gets flag `similar_theme` and `related: [{id, relation: "similar_theme"}]`. A clip that continues another (part 2) gets `relation: "sequel"`. | PENDING (F8) | judge/ranker |

## 9. Evidence base and what to validate next

Evidence: 19 creator Shorts (2 podcasts, one editor each, 7 + 12) mapped to word indices at high confidence, and
55 gold picks from one strategist. That is two editors' taste, so rules stay HYP and flag-first where evidence is thin.
Validate next, in order:
1. T4/T5 on proposals: how often `rough_start`/`rough_end` fire, and how often the judge agrees with the humans on them.
2. T2 with cut context: seed meaning-flip cases (drop a trailing "కాదు") and confirm that the judge rejects them.
3. S1/O1/O2/S4: re-derive `n_blocks` on anchors and gold at `skip_s = 30` (schema 1.1) and report the distribution.
4. T6/T7/T9 firing rates on creator Shorts (they should rarely fire).
5. Q2/Q3 thresholds after judge calibration; judged quality by length bucket (<30, 30–60, 60–90, 90–120, >120s).
6. Cold-open replay vs remove (O4): a human A/B on a few gold clips.

## 10. Config defaults (keys the pipeline records in `pipeline.config`)

| Key | Default | Rule | Status |
|---|---|---|---|
| `min_s` / `soft_max_s` / `hard_max_s` | 15 / 120 / 150 | §5 | FIRM |
| `natural_gap_s` | 0.5 | §0 | HYP |
| `lead_in_max_words` | 4 | T4 | HYP |
| `tail_max_words` | 2 | T5 | HYP |
| `cut_context_words` | 5 | T2 | HYP |
| `dead_air_s` | 1.5 | T6 | HYP |
| `max_pieces_per_s` | 0.333 | T7 | HYP |
| `under_tighten_share` | 0.20 | T8 | HYP |
| `keep_ratio_flag_below` | 0.25 | T9 | HYP |
| `region_span_max_s` | 300 | R2 | HYP |
| `skip_s` | 30 | S4 | HYP |
| `blocks_flag_above` / `blocks_max` | 3 / 4 | S1 | HYP |
| `backward_jumps_max` | 2 | O1 | HYP |
| `moved_max_s` | 30 | O2 | HYP |
| `long_range_s` / `long_range_max` | 300 / 1 | S5 | PENDING |
| `hook_within_s` | 6 | C1 | HYP |
| `low_conf_logprob` / `low_conf_run` | -0.7 / 4 | A2 | HYP |
| `t_show` / `t_rec` | 3.5 / 4.0 | Q2 | HYP |
| `dup_overlap` | 0.5 | Q4 | HYP |
| `below_threshold_max` | 3 | Q6 | PENDING |
| `warn_clips_per_min` | 0.5 | Q7 | HYP |
| `host_led_share` | 0.5 | §11 | PENDING |

## 11. Taste calls: provisional defaults (PENDING founder decision)

These are **not decided**. They are defaults so engineering is not blocked. Each one is built as a flag or an
optional field, so changing the decision later means changing a filter, not the pipeline. F-numbers match the
founder questions in `../decisions.md`.

| F | Topic | Provisional default | Check |
|---|---|---|---|
| F5 | Crude or explicit lines | Score on craft. Tightening drops a crude line when the clip works without it. If the line is load-bearing, keep it and flag `crude_language`, with a `standalone_risks` `other` note giving its `word_index`. Never hide for crudeness. | judge |
| F6 | Long-range stitches | S5: allowed, ≤ 1 per clip, flagged, capped at `worth_a_look`, no dedicated search. | code + judge |
| F7 | Host-led clips (host's line is the best part, the guest mostly agrees) | Allowed and scored on merit. Flag `host_led` when > 50% of kept words are the host's. The host is the speaker the pipeline labels as interviewer once per episode; with unknown roles, don't set the flag. | code (given host id) |
| F8 | Near-duplicate themes | Q8: show both, flag the lower one, link them. No score penalty beyond what the judge gives. | judge/ranker |
| F9 | 90s+ stories | No change: allowed up to the 120s soft max as one clip (4/19 creator Shorts are 98–115s). The pipeline does not auto-split a story into parts. If it proposes two parts, each must pass C2 alone and they are linked as `sequel`. | code + judge |
| F10 | `relatable` tag | Optional `tags: ["relatable"]` ("that's me / that's my amma" moments). Recorded for eval, **not** used in ranking or thresholds. The pipeline may leave it empty. | judge (optional) |
