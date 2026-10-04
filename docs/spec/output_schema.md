# Shotvi clip output schema

Version 1.1 (2026-09-27; 1.0 was 2026-09-26). Owner: `product-lead`. Machine-readable: `clip_proposal.schema.json` (JSON Schema
2020-12). Rules the fields express: `clip_spec.md`.

One format is used for three things so they can be compared word by word:

| File | `kind` | Written by | Clip type |
|---|---|---|---|
| `runs/<run_id>/<video_id>.json` | `proposals` | pipeline (`shotvi/select`) | `proposal` |
| `data/gold/<video_id>.json` | `gold` | `telugu-content-strategist` | `goldClip` |
| `data/anchors/<video_id>.json` | `anchors` | `evaluation-engineer` (Short → episode mapping) | `anchorClip` |

## Conventions

- **Word index**: 0-based index into the episode transcript's `word_timestamps`. Ranges are **inclusive**.
- **Indices are authoritative. Seconds are derived**: `start = words[start_word].start`,
  `end = words[end_word].end`, rounded to 3 decimals, and they must match within 0.01s. Seconds are included so
  people and the UI can read files without the transcript.
- **Transcript pinning**: every file header carries the transcript `sha256`. Re-transcribing changes the
  indices, so a file whose hash does not match the transcript on disk is invalid and must be remapped.
- Times are in seconds (float). Text is UTF-8, written with `ensure_ascii=False`.

## File header (all kinds)

`schema_version` (`"1.1"`), `kind`, `video_id`, `transcript: {path, sha256, n_words, audio_duration_secs}`, and optional
`derived_params: {skip_s}`: the skip threshold used to derive `n_blocks`. If it is absent, validators use 30s for 1.1 files and
20s for 1.0 files, so existing 1.0 anchor and gold files stay valid.

**Changes in 1.1** (all additive): `derived_params`; edit-note type `reorder`; flags `rough_start, rough_end,
many_jumps, long_range_stitch, crude_language, host_led, similar_theme`; optional `related[]` and `tags[]` on proposals and
gold. The default skip threshold is 30s (spec S4).

## Clip core (shared by all three)

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Unique within the file. |
| `pieces` | `[{start_word, end_word, start, end}]` | Kept ranges in **playback order**. A contiguous clip has 1 piece; a tightened clip has many. No overlaps, except a replayed cold open (spec O4). |
| `region` | span | Derived: `[min start_word, max end_word]` over pieces. |
| `edited_duration` | seconds | Derived: sum of `(end - start)` over pieces. Every length guardrail applies to this value. |
| `reordered` | bool | Derived: true if any piece starts at or before the previous piece's `end_word` (a backward jump: cold open or reorder). |
| `n_blocks` | int | Derived: 1 + number of jumps (backward, or forward skip > `skip_s` of source; see header). |
| `text` | string, optional | Kept words joined in playback order. |

Derived fields are stored for convenience. Validators recompute them and fail the file on mismatch.

## Proposal (`kind: proposals`) = core +

| Field | Type | Notes |
|---|---|---|
| `rank` | int ≥ 1 | 1..n with no gaps, ordered per spec Q5. |
| `tier` | `recommended` / `worth_a_look` / `below_threshold` | From `overall` vs `cutoff.t_rec` / `t_show` (spec Q2, Q6). |
| `scores` | `{hook, standalone, payoff, value_emotion, overall}` | 1–5 (floats allowed). Same scale as `docs/content/labeling_guide.md`. |
| `title` | string ≤ 100 chars | Must contain Telugu script; Tenglish is fine. Aim for ≤ 60 chars. |
| `hook_line` | `{text, start_word, end_word}` | Verbatim opening words. `start_word == pieces[0].start_word`, and the span lies inside `pieces[0]`. |
| `why_it_works` | one line ≤ 200 chars | Names the specific hook and payoff. "Engaging content" is not an explanation. |
| `category` | enum | `story, advice, humour, controversy, emotional, insight, reveal, motivation, other`. |
| `standalone_risks` | `[{type, note, word_index?}]` | Types: `unresolved_referent, inside_reference, missing_setup, abrupt_end, other`. May be empty. |
| `edit_notes` | `[{type, after_piece?, reason}]` | One `cold_open` or `reorder` note per backward jump, one `skip` note per skip (`after_piece` = index of the piece before the jump), and one `length_over_soft_max` note if edited duration > 120s. For a long-range skip the reason says what sits in between. |
| `flags` | enum[] | `low_confidence, song_overlap, sensitive_content, over_chopped, under_tightened, dead_air, low_keep_ratio, over_soft_max, starts_with_demonstrative`, and in 1.1 `rough_start, rough_end, many_jumps, long_range_stitch, crude_language, host_led, similar_theme`. Shown to the reviewer as warnings. |
| `related` | `[{id, relation}]`, optional | `relation`: `similar_theme` (spec Q8) or `sequel` (part 2 of a story). Lets the UI group clips. |
| `tags` | enum[], optional | `relatable` only for now (spec §11, pending founder). Not used in ranking. |

## Gold clip (`kind: gold`) = core +

`labeler`, `tightening_labeled` (false = `pieces` is the whole region as one piece, and eval skips keep-list
metrics for it), `scores`, `title`, `why_it_works`, `category`, optional `hook_line`, `notes`, `related`, `tags`.

## Anchor clip (`kind: anchors`) = core +

`short_id`, optional `url`, `title`, `short_duration_secs`, and
`mapping: {method, confidence 0–1, short_words, matched_words, notes?}`. Anchors record what the creator actually
did, so spec rules (block limits, overlap) are **not** enforced on them. They are measured instead.

## Run wrapper (`kind: proposals` only)

| Field | Content |
|---|---|
| `run_id`, `created_at` | Run identity (ISO 8601 UTC). |
| `pipeline` | `{version, git_commit, prompt_versions: {name: version}, config}`, where `config` holds the guardrails and thresholds actually used. |
| `models` | `[{role, provider, model, params?}]`, one per role (`candidate_gen`, `tightener`, `scorer`, `titler`, ...). |
| `cost` | `{input_tokens, output_tokens, usd, wall_time_secs, n_llm_calls, by_role?}`. |
| `cutoff` | `{t_show, t_rec, n_candidates, n_passed_hard_rules, n_shown, warnings[]}`. |
| `proposals` | Shown clips, ranked. |
| `rejected` | Optional: core + `reject_reasons` (spec rule id + detail, e.g. `"A1: overlaps censored event at 69.4s"`) + optional `scores`. Kept for eval and debugging. The creator never sees these. |

## Checks beyond JSON Schema (to implement in the eval structural checks)

1. `start_word <= end_word < n_words` for every span, and seconds match the transcript within 0.01s.
2. Derived fields (`region`, `edited_duration`, `reordered`, `n_blocks`) match a recomputation.
3. The transcript sha256 matches the file on disk.
4. `hook_line` sits inside `pieces[0]` and starts at its first word.
5. `edit_notes` match the jumps and length as described above: the count of `cold_open` + `reorder` equals the backward jumps, and the count of `skip` equals the skips.
6. Ranks are contiguous, tiers match scores and thresholds, and `n_shown == len(proposals)`.
7. Proposals pass every code-checkable `reject` rule in `clip_spec.md`: length, `n_blocks ≤ 4`, ≤ 2 backward jumps, moved
   duration ≤ 30s, overlaps only per O4, ≤ 1 long-range jump, R2 span, and censored overlap. T4/T5 code failures are flags, not rejects.
8. Flags match their code checks where one exists (`rough_start`, `rough_end`, `many_jumps`, `long_range_stitch`, and
   `host_led` when a host is labelled), and a clip with `long_range_stitch` is never `recommended`.

## Example (format illustration only)

This example uses real words from `TBlm6wcij94`, but they come from the episode's opening teaser montage, which
spec R3 would reject. It shows the shape and nothing more. It is a 4-piece clip with a cold open, so
`reordered: true` and `n_blocks: 2`. It validates against the schema.

```json
{
  "id": "p001",
  "pieces": [
    {"start_word": 42, "end_word": 54, "start": 15.02, "end": 19.0},
    {"start_word": 0,  "end_word": 4,  "start": 0.22,  "end": 1.62},
    {"start_word": 7,  "end_word": 19, "start": 2.42,  "end": 7.3},
    {"start_word": 55, "end_word": 71, "start": 21.16, "end": 25.94}
  ],
  "region": {"start_word": 0, "end_word": 71, "start": 0.22, "end": 25.94},
  "edited_duration": 15.04,
  "reordered": true,
  "n_blocks": 2,
  "rank": 1,
  "tier": "recommended",
  "scores": {"hook": 4, "standalone": 3, "payoff": 3, "value_emotion": 4, "overall": 4.0},
  "title": "బిర్యానీ తిని 7 kgs తగ్గొచ్చా?",
  "hook_line": {"text": "మధ్యాహ్నం బిర్యానీ సాయంత్రం బర్గర్ చేసి two months లో almost six, seven kgs తగ్గిపోతుంది.",
                "start_word": 42, "end_word": 54},
  "why_it_works": "Counter-intuitive claim (lose weight eating biryani and burgers) opens cold, then the speaker explains.",
  "category": "advice",
  "standalone_risks": [{"type": "missing_setup", "note": "Claim is not explained within the clip; viewer may feel baited.", "word_index": 54}],
  "edit_notes": [{"type": "cold_open", "reason": "Weight-loss claim lifted to the front as the hook; it is the strongest line in the region."}],
  "flags": ["sensitive_content"]
}
```

## Versioning

Adding an optional field or an enum value is a minor bump (`1.1`). Renaming or removing a field, or changing
index semantics, is a major bump (`2.0`) and needs a `docs/decisions.md` entry. The category list is co-owned
with `telugu-content-strategist`: propose changes to `product-lead`.
