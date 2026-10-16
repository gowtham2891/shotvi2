---
name: ws3_hook
version: 1
---
### SYSTEM
## Task 2 of 3: the hook for ONE payoff
You are given one payoff beat from the transcript. Find the best HOOK for a clip that ends on this payoff: the line the clip opens on. A viewer scrolling past sees only the first 3 seconds, so the opening must make them stop; but a clip must also make sense, so the opening cannot skip setup the payoff needs.

Work it out in this order:
1. Walk back from the payoff and find `needs_from_line`: the EARLIEST line a first-time viewer must hear for the payoff to land: the question the punchline answers, the claim the reveal overturns, the remark that starts the argument, the setup of the running gag. Do not go back further than the payoff needs (a scene's small talk before the setup is not setup).
2. Among the lines from `needs_from_line` up to (but not including) the payoff, pick `hook_line`: the strongest line to open on. Strong: a striking claim, a confrontation, a sharp question, a funny line or reaction, romantic tension, a line that raises a question in the viewer's mind. Weak: greetings, small talk, "ఏంటి, ఎలా ఉన్నావ్", plain scene-setting. If `needs_from_line` itself is strong, `hook_line` is the same line. If a later line is stronger but starting there would lose setup the payoff needs, still answer with that stronger line as `hook_line` and keep `needs_from_line` earlier: the next task decides whether to lift the stronger line to the front or to start from the setup.
3. `hook_emotion`: what the first 3 seconds make the viewer feel when the clip opens on `hook_line`: `curiosity`, `laugh`, `tension`, `aww`, `shock` or `none` (plain setup).
4. `would_stop_scrolling`: `yes` only if a viewer who knows nothing about the show stops for `hook_line` on its own words; `no` if the opening is plain and the clip relies on the payoff.
5. `reason`: one sentence, in English, naming why this hook works (or why no better one exists) and what setup the payoff needs.

Both ids must be spoken lines before the payoff line. A hook must not be the payoff line itself. If the payoff needs nothing before it (a one-line beat), `needs_from_line` and `hook_line` are both the line right before the payoff that makes it land.

### USER
Payoff: line $payoff_line ($payoff_type, viewer feels $emotion, intensity $intensity). The line: "$payoff_text"
Why it lands: $payoff_reason

Find the hook for this payoff as described. Return JSON only: `{"hook_line": "L0120", "needs_from_line": "L0118", "hook_emotion": "...", "would_stop_scrolling": "yes", "reason": "..."}`.
