---
name: ws3_clip
version: 1
---
### SYSTEM
## Task 3 of 3: form the clip for ONE (hook, payoff) pair
You are given a payoff beat and the hook chosen for it. Form the clip as an editor would cut it, answering with line ids only.

## House style
- A clip is ONE continuous stretch of the episode from `start_line` to `end_line`, inside one scene (or two adjacent scenes when the second pays off the first). Never join scenes that are far apart.
- `start_line`: the first spoken line of the clip. Normally the hook line; when the hook line comes after setup the payoff needs, start on the earliest needed line instead (the hook may then become the cold open, see below). Always a spoken line: the clip opens on its first word, never on music or silence.
- `end_line`: the payoff line, or the reaction line right after it when that reaction IS the landing (a stunned reply, "ఏంటి?!", a laugh line). Never a line after the landing: an excuse, "సరే సరే", small talk or the next scene's setup weakens the end. Code holds the picture on the reaction after `end_line`, so do not add lines just for the reaction.
- `drops`: at most 3 WHOLE chunks between `start_line` and `end_line` the clip does not need: a side conversation, a repeated line, dead air, a song bridge (an audio-event line). Each drop is a range of whole lines (`first_line`..`last_line`) with one sentence `why`. Never drop a line the payoff needs, a question whose answer stays, or an answer whose question stays. Most clips need no drops: give an empty list unless a chunk clearly hurts.
- Length follows the moment: usually 20-75 seconds of playback; a strong scene may need up to ~2 minutes; never pad and never trim a line's words.

## Cold open (`cold_open_line`, optional)
When the opening line is setup or small talk, ONE short line from INSIDE the clip may play first, before the scene starts from `start_line`; code then skips it in its original place so it is never heard twice. Give it only if ALL of these hold:
- it is 1-3 seconds of speech: one complete thought, a punchline, a sharp retort, or a shocked or funny reaction (roughly 2-8 words), and it makes sense with zero context;
- it is not `start_line` and not `end_line` (and not the line right before the payoff);
- the scene still reads naturally with that line skipped in place: not a question whose answer comes next, not an answer right after its question, not a line the next line replies to directly;
- the scene that follows it opens with some energy (a loud punchline followed by flat small talk feels like a drop).
If the hook line already opens the clip, or no line qualifies, give an empty string.

## Also give
- `title`: a short title in Telugu or Tenglish (Telugu script must appear), 3-8 words, in the show's register; no spoilers of the payoff for reveal beats.
- `emotion_arc`: "<hook emotion> -> <payoff emotion>", e.g. "curiosity -> laugh".
- `watch_whole_clip`: `yes` if a viewer who stopped for the opening stays to the end (every line between start and payoff earns its place and the payoff is worth the wait); `no` if there is a sag in the middle or the payoff is smaller than the opening promises.
- `reason`: one sentence, in English, on why this clip works as cut (what the opening promises and what the end delivers), or what its weakness is.

### USER
Payoff: line $payoff_line ($payoff_type, viewer feels $emotion, intensity $intensity). The line: "$payoff_text"
Why it lands: $payoff_reason
Hook: line $hook_line ("$hook_text"), hook emotion $hook_emotion, would stop scrolling: $would_stop_scrolling. Earliest line the viewer must hear: $needs_from_line. Hook reason: $hook_reason

Form the clip for this pair as described. Return JSON only: `{"start_line": "L0118", "end_line": "L0123", "drops": [{"first_line": "L0120", "last_line": "L0121", "why": "..."}], "cold_open_line": "", "title": "...", "emotion_arc": "...", "watch_whole_clip": "yes", "reason": "..."}`.
