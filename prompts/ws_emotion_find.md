---
name: ws_emotion_find
version: 1
---
### SYSTEM
## Task 1 of 2: list the moments a stranger would feel
Read the whole episode once and list the moments a stranger would FEEL: a line after which someone who never saw this show, scrolling Reels with zero context, laughs, gasps, says "aww", tenses up or tears up. A clip will be built around each one later (the start, the cuts and the opening are chosen in a later task, so do not think about them now). Be strict: list a moment only when a stranger would feel it without any explanation, from the scene alone; leave out beats that need earlier scenes, inside jokes or knowledge of the characters to land. There is no quota: an episode may have many such moments, a few, or none. Do not list the same beat twice; when a joke builds over several lines, the payoff is the line where it lands (the last line that gets the laugh), not the setup.

For each moment give:
- `line`: the id of the spoken line where the beat lands (the punchline itself, the reveal line, the quotable line, the line that hits). A spoken line, never an audio-event line.
- `type`: `punchline` (a joke, comeback, absurd reply or comic reaction that gets a laugh), `reveal` (a twist, confession or fact that reframes what came before), `quotable_line` (a line that closes an argument or a thought and snaps; people would repeat it), `emotional_peak` (a line that hits, with a visible reaction after it), or `other`.
- `emotion`: what the stranger feels at that moment: `laugh`, `aww`, `shock`, `tension` or `tears`.
- `intensity`: 1-5, as the stranger feels it. 5 = a real laugh, a gasp, a lump in the throat, with the reaction visible; 4 = clearly lands; 3 = lands but mildly (a smile, a nod); 2 = a beat only fans of the show would feel; 1 = barely a beat. List only 3 and above.
- `reason`: one sentence, in English, naming what the stranger feels and why (the joke's logic, what the reveal overturns, why the line hits). Quote the key words of the line.
- `lands_on_screen`: true when the transcript shows the landing: a laugh event, a reaction line ("ఏంటి?!", a gasp, a stunned reply) or a long silence right after it. False when nothing in the transcript shows a reaction (the beat may still be good).

Also give `episode_notes`: two or three sentences for yourself: genre, the characters, the plot in one line, and which beats would spoil the episode's main twist if clipped (a later task may still clip them; the creator decides).

### USER
Episode length: $episode_len, $n_lines lines. List the moments as described, in transcript order (an empty list when there are none). Return JSON only: `{"episode_notes": "...", "payoffs": [{"line": "L0123", "type": "...", "emotion": "...", "intensity": 4, "reason": "...", "lands_on_screen": true}, ...]}`.
