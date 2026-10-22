---
name: ws_emotion_find
version: 3
---
### SYSTEM
## Task 1 of 2: list the moments a stranger would feel
Read the whole episode once and list every moment a stranger would FEEL: a payoff beat where someone who never saw this show, scrolling Reels with zero context, laughs, gasps, says "aww", tenses up, tears up, smiles at relatable banter or cringes at an awkward moment once they watch the 30-75s exchange leading up to it.

## How to find the right payoff line
1. Judge the beat with its immediate scene setup: A punchline or emotional line ("నువ్వే నా first crush", "మా ఆవిడకి చెప్పకు ప్లీజ్", "ఈ రోజు నుంచి నేనే boss") may look short on its own, but hits hard when preceded by its 30s setup. List it whenever the self-contained scene around it makes a stranger feel it without needing earlier episodes or earlier scenes.
2. Follow escalations to the FINAL peak: In comedy lists, rapid-fire trolling, or escalating arguments, a scene often has an initial reveal/joke followed by 2 or 3 bigger comebacks or a surprise entrance a few lines later (e.g. a character admits "నేనే ఆ cake తిన్నాను", the other threatens to call the police over a cake, and then the birthday girl walks in). Do NOT stop at the first setup joke—mark the strongest final punchline or climax of that exchange, or mark both so the builder can include the full escalation.
3. Include strong cliffhangers (`tension` / `shock`): When a confrontation or secret escalates to a sharp dramatic turn that makes a stranger desperate to know what happens next (e.g. "ఆ letter రాసింది నేనే"), list that landing line.

## What to NEVER list (strictly ban these)
- Plot recaps & post-conflict wrap-ups: Never list scenes where characters sit down (over drinks, coffee, or a ride home) to summarize what happened earlier in the episode, thank/apologize to each other after a conflict is already resolved, or discuss a third character's off-screen feelings.
- Generic moralizing / preachy advice: Never list flat philosophical statements ("జీవితం అంటే compromise", "friendship లో ego ఉండకూడదు") unless they snap shut an active, heated argument happening right on screen.
- Inside references: Leave out beats that only make sense if the viewer watched the first 10 minutes of the episode.

For each moment give:
- `line`: the id of the spoken line where the beat lands (the punchline, the comic comeback, the reveal, the quotable verdict, or the emotional peak). Always a spoken line, never an audio-event line.
- `type`: `punchline`, `reveal`, `quotable_line`, `emotional_peak`, `cliffhanger`, or `other`.
- `emotion`: what the stranger feels at that moment: `laugh`, `aww`, `shock`, `tension`, `tears`, `relatable_banter` or `awkward_cringe`.
- `intensity`: 1-5, as a stranger watching that 30-75s scene feels it. 5 = a real laugh, a gasp, or lump in the throat, with the reaction on screen; 4 = clearly lands and is postable; 3 = lands mildly. List only 3 and above.
- `reason`: one sentence, in English, naming the setup in the scene and why this payoff lands for a stranger. Quote the key words.
- `lands_on_screen`: true when the transcript shows the landing: a laugh event, a reaction line ("ఏంటి?!", "అయిపోయాం రా", a stunned reply), a music sting, or a pause right after it.

Also give `episode_notes`: two or three sentences for yourself: genre, the main characters (and any fake names/aliases they use in the story), the plot in one line, and which beats would spoil the episode's main twist if clipped.

### USER
Episode length: $episode_len, $n_lines lines. List the moments as described, in transcript order (an empty list when there are none). Return JSON only: `{"episode_notes": "...", "payoffs": [{"line": "L0123", "type": "...", "emotion": "...", "intensity": 4, "reason": "...", "lands_on_screen": true}, ...]}`.
