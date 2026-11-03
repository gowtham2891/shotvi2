# selection_v2

The six-stage clip-selection pipeline from `docs-1/klipvi_Clip_Selection_Pipeline.docx` (v0.3). Additive: the
existing `shotvi.select` pipeline is untouched.

## Launch the debug app

    venv/bin/streamlit run selection_v2/debug_app.py

## Layout

| path | what |
|---|---|
| `config/pipeline.yaml` | models, token rates, rupee-rate env var, Stage 1A thresholds and weights, tag classes |
| `config/cast_lists.yaml` | cast per series (Telugu spellings) |
| `inputs.py` | loads one episode: Scribe lines (+ ASR corrections), the speaker_fix layer (required), meta (no shot cuts since 2026-11-02) |
| `stage1_facts.py` | Stage 1A (code only): normalize, gap table, tag events, boundary proposals, suspects (name votes removed 2026-11-02) |
| `identity_pass.py` | the video step: per spoken line, who talks (one, several, a voice change inside the line) and who is on screen |
| `naming_check.py` | one text call: the final person per line from the text step + the video step; writes line_speakers / line_voices / on_screen |
| `stage1_episode_pass.py` … `stage6_select.py` | later stages; stubs that raise NotImplementedError until built |
| `runner.py` | runs one stage for one episode, validates `episode_facts.json` against `schemas/`, logs `runs.csv`, runs the speaker fix |
| `schemas/episode_facts.schema.json` | the per-episode document every stage reads and extends |
| `reference/<id>.yaml` + `reference/compare.py` | expected values (the spec's Surya Ep 1 numbers) and the agreement table |
| `tags_review.py` | every Scribe tag in the dataset with its class (the app's Tags tab) |
| `eval/` | `golden_set.yaml` format, importers (channel Shorts, gold clips, heatmap, comments stub), Short matcher, recall |
| `debug_app.py` | the Streamlit app |

Outputs: `runs/selection_v2/<video_id>/episode_facts.json`, `runs/selection_v2/runs.csv`.

## CLI equivalents

    venv/bin/python -m selection_v2.runner <video_id> 1a
    venv/bin/python -m selection_v2.reference.compare p3XTZlZ6-xw
    venv/bin/python -m selection_v2.tags_review
    venv/bin/python -m selection_v2.eval.import_sources --all
    venv/bin/python -m selection_v2.eval.recall <video_id>
    venv/bin/python -m selection_v2.eval.match_short <video_id> <short_id> [--transcribe] [--write]
    venv/bin/python -m pytest tests/v2 -q

## Stage 1B (episode pass)

`stage1_episode_pass.py`: one Gemini call (model `models.stage1b` in `config/pipeline.yaml`) over the cached prefix
(`prefix.py` → `runs/selection_v2/<id>/prefix.txt`, byte-identical across calls) plus `prompts/stage1b_episode_pass.md`.
Answer validated against `schemas/episode_pass.schema.json` and by code (scenes as line ids on gap-table pauses,
every speaker key covered, real line ids); a failing tier is re-asked alone with the errors, at most
`stage1b.max_retries_per_tier` times. Every attempt, the exact instructions and the raw answers go to
`runs/selection_v2/<id>/stage1b_debug.json`. The glossary is merged into `data/series_glossary/<series>.json` and
fed back as a prior for later episodes. Acceptance tests per episode: `reference/acceptance/<id>.yaml`
(`acceptance.py`), shown green / red in the app.

## Stage 2 (scene beat sheets)

`stage2_beats.py`: one Gemini call per 1B scene with the scene media (`media.py`: ffmpeg cut from the 720p mp4,
≤ 480p with audio, or 16 kHz mono mp3 for podcasts; uploaded through `shotvi.llm.gemini.upload_file`) and the cached
prefix `prefix_stage2.txt` (= `prefix.txt` + the 1B results). Prompt `prompts/stage2_beats.md`, schema
`schemas/beat_sheet.schema.json`, positions as line ids. Code validates (one retry with the errors), derives times
and cut quality, and turns `speakers_seen` mismatches into speaker-map corrections (`speaker_map` in the facts, 1B's
map kept, each correction revertible in the app). Scenes with thin diarization (`stage1_facts.diarization_stats`)
get a warning in the prompt. Per-scene cost in runs.csv (`2:scene<id>`). Acceptance: `reference/acceptance/<id>.2.yaml`.

### Reference faces (cast sheet) and the series face bank

Stage 2 identifies people by comparing with REFERENCE FACES. `cast_sheet.py` picks them: one Gemini call per episode
with ~8 candidate frames per speaker key (cut while that key's lines are spoken) and the lines around each frame; the
model says who is really visible and how many people (a reverse shot shows the listener, a phone call the one holding
the phone), plus a LOOK sentence per character. Code keeps single-person frames (never 3+). The founder checks the
"Reference faces" row in the app and presses Save: frames + looks go to `data/series_faces/<series>/` and later
episodes of the series use them first. Looks are also written into `prefix_stage2.txt` next to the speaker map.
Hand pins: `selection_v2/reference/faces/<video_id>.yaml`. Accuracy line: `speaker_accuracy.py` against
`data/speakers/<id>.json` (aliases in `data/speakers/aliases.json`).

### Stage 1B with the whole episode as video (2026-10-26)

`stage1b.video: true` sends the episode (cut to 480p, fps 1, Files API) after the prefix text; `stage1b.prior: raw` makes
the line table show Scribe's raw labels, with the speaker fix's names in a low-trust block. Tier 0 answers come from
watching: cast per raw label, `same_person`, `split_labels`, `reference_times`; code derives every line's final key
(merges, splits). The cast sheet then cuts only the nominated times. Acceptance (decisive) in
`reference/acceptance/p3XTZlZ6-xw.1b.yaml`.


## Stage 3 (candidate generation, code)
`stage3_candidates.py` turns every scene's beat sheet into typed candidates (comedy full arc / tight / cold open, emotional full / peak, hook, loop; several payoffs add an extended arc) with boundaries on gap-table pauses, trims as kept segments, beat protection, non-story handling and caps; every number is in `config/pipeline.yaml → stage3`. Output `facts["candidates"]` (`schemas/candidate.schema.json`); acceptance `reference/acceptance/<id>.3.yaml`; tests `tests/v2/test_stage3.py`.
