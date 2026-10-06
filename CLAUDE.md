# Shotvi — Project Brief

Shotvi turns long-form Telugu podcasts and interviews into short-form clips (Instagram Reels, YouTube Shorts).
This is a real product, not a demo. The two videos in `scribe_dataset/` are only development samples; every
decision must generalize to any Telugu podcast a creator uploads.

## Content types (founder, 2026-10-03)

- **Priority now: Telugu comedy shows** (Jabardasth-style skits) **and Telugu web series** (Infinitum-style).
  Podcasts stay supported. **Reality shows are out of scope** for now.
- Clip purpose differs by type: podcast/comedy clips must stand alone (payoff inside the clip); web-series clips are
  either `standalone` beats or `promo` clips that end on a cliffhanger to pull viewers to the full episode (no
  spoilers).
- **Web series: mostly continuous, with a few chunk-level trims (founder, "option 2", 2026-10-05).** A clip is
  one stretch of the episode with at most ~3 internal cuts, each removing a WHOLE unnecessary chunk (side
  conversation, dead air, song bridge, repeated line). Cuts land between lines, ideally on a shot change; never
  trim words inside an actor's line (acting timing, picture and music would jump). Boundaries are TIMES (padded,
  snapped to shot changes), so a clip can include non-dialogue visual beats (looks, gestures, music).
  Podcasts keep flow-based word-level tightening.
- **Web-series openings: cold open when needed (founder, 2026-10-05).** First GT round: endings rated well (payoff
  4.0–4.4) but hooks weak (3.3–3.4). Clips must start on the first spoken word (no music/silence lead-in, ≤0.3 s
  pad); silent beats belong inside or at the end, never as the opener. If the opening line is setup or small talk,
  lift ONE 2–4 s strong moment from inside the clip (punchline line or reaction) to the front, then play the scene
  from its setup; don't drop setup the payoff needs. Don't stitch scenes far apart (a 15-min stitch rated 1/5).
  Founder, 2026-10-06: cold opens "sometimes feel off". Never replay the lifted line word for word later in the
  clip (skip it in place, or lift a reaction instead of a line); the lift is a complete thought or one reaction,
  1–3 s, ending on a shot change and not on a quiet tail or mid-laugh; it must make sense with no context.
- **Web-series GT approved (founder, 2026-10-06):** `data/gold/j14Om1SaFYE.json` (Couple Goals Ep1, 8 clips) and
  `data/gold/QWIvgoTQGKk.json` (Ep2, 12 clips), round 3 (commit 8eca87c): "all clips are good". This is the
  target the automatic web-series pipeline is measured against (moments, boundaries, openings, reaction holds).
- **The podcast pipeline is preserved** at git tag `podcast-pipeline-v1`; web-series work must not break it.
- **Ground truth for these types comes from the `clip-gt-editor` agent** reading the whole episode (plus keyframes
  for web series), reviewed by the founder. The channel's own published Shorts/Reels are NOT used as ground truth.

## Current phase: clip selection

Input: a Scribe transcript JSON. Output: a ranked list of clip proposals a creator (or their team) reviews.
Rendering, reframing, captions, and backend integration come later. Do not build them now.

## Users

Creators upload their own podcasts. The creator or their team always reviews proposals before publishing, so:
- Precision of the top of the list matters more than exhaustive recall.
- Every proposal must explain itself (why it works, what the hook is) so review is fast.
- Proposals must be editable (boundaries are suggestions, not final cuts).

## What a good clip is — think like a Telugu video editor

A real regional editor does not work with fixed rules. They watch, find moments that land, and cut them to be
as long as the moment needs and no longer. Shotvi must behave the same way:

- **Length is content-driven.** No fixed target. Creator Shorts here run 41–114s *after* tightening; a strong
  story may need up to ~2 min. Anything near 3+ min is too long. A clip that works at 25s must not be padded
  to 45s. (Initial guardrails on final edited length: min ~15s, soft max ~120s, hard max ~150s. Source region
  may be longer. `product-lead` owns these numbers.)
- **Count is content-driven.** No fixed N per video. A dense 50-min interview may yield 15 clips; a rambling
  2-hour one may yield 8 great ones. Rank by quality and cut off at a quality threshold, not a count.
- **Standalone.** A viewer who never saw the podcast must understand it. No dangling "అది", "ఆయన", "that
  thing" without the referent.
- **Hook in the first ~3 seconds.** A strong opening line, question, claim, or emotional beat.
- **Payoff.** Ends on a punchline, conclusion, reveal, or emotional peak, never mid-thought.
- **Clean boundaries.** Start and end on word boundaries at natural pauses; never cut mid-word or mid-sentence.
- **Tightened, not just trimmed.** Evidence from creator Shorts (see "What creator Shorts show"): editors pick a
  source *region* (often 1.5–4.5 min) and jump-cut it down to roughly half, keeping 8–26 pieces. They drop
  listener backchannels, fillers, repetitions, false starts, host tangents, side explanations, and crude lines.
  So a proposal = a region + a word-level keep list, not a single start/end. Every cut must land on a word
  boundary and keep the sentence grammatical.
- **Edit by flow, not by sentences (founder, 2026-09-30).** A real editor ignores sentence boundaries: one cut may
  drop the end of sentence 1, all of sentence 2 and the first half of sentence 3. Cuts may start and end on any
  word. Sentence/line structure is only a reading aid, never a constraint on where cuts go.
- **Trim at word level, and every join must sound natural (founder feedback, 2026-09-29).** Creator editors cut
  *inside* sentences in 52% of their cuts and remove 1–3 content words in 26% (e.g. "సుయంవరం అనే ఒక movie
  [✂ అది] one day లో…", "very [✂ common] very close friends"); the pipeline did 18% / 16%. Trim lead-ins and
  low-value words mid-sentence the same way. But the founder's #1 complaint was joins that feel "off": an answer
  played without its question, a question followed by an unrelated line, a cut before the thought finishes, a
  topic shift inside the clip, a replaced weak opener left in. Check every join by reading across it.
  Founder ratings with cut-level notes: `data/human/ratings_*.csv`.
- **Hook first, even if it's out of order.** Editors often open with the host's question or lift a punchline from
  later in the region to the front (cold open), then play the story. Reordering is allowed within a region.
- **Avoid:** censored regions (`[సెన్సార్డ్]`), low-confidence transcript stretches, sponsor reads, intros/outros,
  inside references that need prior context.

## Ground truth philosophy

Creators' own published shorts exist for some videos. They are **positive anchors, not the full answer key**.
Optimizing for overlap with them alone made earlier attempts drop other genuinely good clips. So:
- Measure **recall against creator clips** (did we find the ones they chose?), and
- Measure **precision by judging each proposal on its own merits** (human labels and a calibrated LLM judge),
  never by "not in the creator set = wrong".

## Data

`scribe_dataset/<video_id>/`:
- `<id>.mp4`, `<id>_audio.wav`: source media (large, never load into context).
- `<id>_scribe_transcript.json`: produced by `transcribe_scribe.py` (ElevenLabs `scribe_v2`, diarized):
  - `word_timestamps[]`: `{word, start, end, speaker, logprob}`
  - `sentences[]`: `{id, text, start, end, speaker, words:[first_idx, last_idx]}`, split on end punctuation,
    speaker change, 0.7s gaps, 30 words, or 20s
  - `audio_events[]`: `{text, start, end}`, e.g. `[నవ్వు]` laughter, `[సెన్సార్డ్]` censored, `[పాట]` song
  - `speakers`, `n_speakers`, `language` (`tel`), `audio_duration_secs`, `text`
- `creator_shorts/`: the creator's published Shorts: `<short_id>.mp4`, `<short_id>_audio.wav` (16 kHz mono) and
  `<short_id>_scribe_transcript.json` (same Scribe format as the episode, made by `tools/transcribe_shorts.py`).
  **The Short transcripts are the ground truth.** Mapping a Short back onto episode word indices (region +
  kept words) is still to be built from these transcripts (owner: `evaluation-engineer`). Earlier index/alignment
  JSONs were deliberately deleted; don't recreate them.

## What creator Shorts show (19 Shorts, 2 podcasts)

(From an earlier audio-matching pass; re-confirm against the Short transcripts.)

- Final length 41–114s; source region 44–267s; kept ~32–93% of the region (typically ~50%).
- 8–26 pieces per Short. Removed: backchannels ("ఉమ్", "Right"), host tangents (e.g. host bringing up another
  guest), side explanations, repetition, one crude line. Some backchannels are kept when they add rhythm.
- Openers: the host's question ("If I ask you what is money according to you?", "తినేటప్పుడు water తాగొచ్చా?")
  or a funny/punchy line lifted from later (3/19 reordered). Endings: a crisp conclusion or a quotable line.
- Setup and payoff can be far apart: one Short joins a setup with its payoff 80s later, skipping a tangent.
- This gives two kinds of supervision: **which regions** (recall anchors) and **which words to keep inside a
  region** (a word-level keep/cut label set for the tightening stage).
- 19 Shorts from 2 podcasts, one editor each. Treat these as strong hints about editing style, not universal law.

Known data traits:
- Heavy Telugu–English code-mixing (Tenglish). Treat it as normal, not noise.
- Short listener replies ("ఉమ్", "అవును", "makes sense") are split into their own sentences and fragment a speaker's
  thought. Segment by thought, not by sentence.
- Files are large (up to ~4 MB JSON, ~22k words). Never dump a whole transcript into context; slice it.
- On Windows, set `PYTHONIOENCODING=utf-8` when printing Telugu from Python.

## Code layout

```
shotvi/
  segment/    thought units, Q→A pairs, signals, boundary snapping   (transcript-signal-engineer)
  select/     candidate generation, LLM scoring, ranking, stitching  (clip-selection-engineer)
  llm/        provider-agnostic LLM client (Gemini / Claude / etc.)   (clip-selection-engineer)
eval/         gold sets, metrics, judge, regression runner            (evaluation-engineer)
docs/spec/    clip spec and output schema                             (product-lead)
docs/content/ Telugu short-form playbook, labeling guide              (telugu-content-strategist)
data/gold/    <video_id>.json human-labeled clips                     (telugu-content-strategist)
runs/<run_id>/ pipeline outputs + eval reports (gitignored)
```

Python 3.11, `venv/`. Keep dependencies minimal. Secrets via `.env` (`ELEVENLABS_API_KEY`, LLM keys). Never hardcode keys.

## Team

| Agent | Owns |
|---|---|
| `product-lead` | Clip spec, output schema, guardrail numbers, trade-off decisions |
| `telugu-content-strategist` | What makes Telugu shorts work, gold labels, qualitative critique of proposals |
| `transcript-signal-engineer` | Turning Scribe JSON into clean thought units, signals, and cut points |
| `clip-selection-engineer` | Candidate generation, LLM scoring and ranking, stitching, titles and hooks |
| `evaluation-engineer` | Metrics, LLM judge calibration, regression runs, model bake-offs |
| `code-reviewer` | Correctness, LLM cost and token blowups, generalization to unseen podcasts |
| `clip-gt-editor` | Ground-truth clip picks for comedy shows and web series (whole transcript + keyframes), for founder review |

Working rules for every agent:
- Stay in your lane; hand off with a concrete artifact (file path + what changed + what's next).
- Any change to selection logic or prompts must be followed by an `evaluation-engineer` run before it's called better.
- Never tune for the two sample videos specifically. If a rule only makes sense for one podcast, it's wrong.
- Report honestly: if a metric went down or something is untested, say so.
- A/B tests change ONE thing. A test prompt must be the baseline prompt word-for-word plus the smallest addition;
  show the diff before any paid run. (Round 10's "with notes" prompt also shortened the baseline's guidance, which
  confounded the result.) Prefer one shared baseline run over re-running it, since Gemini runs vary by 2–3 moments.
- Paid runs need the founder's approval in ₹ first; the cap is enforced in `shotvi/llm/spend.py`.
- Editor's notes / guest context are NOT used in clip selection (rounds 7–10: no gain, pulled picks toward named
  people and money/trivia hooks). Clip selection = discover v1 prompt as is; no top-N moment cap in production.
