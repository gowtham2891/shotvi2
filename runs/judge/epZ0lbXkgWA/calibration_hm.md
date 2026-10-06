# Calibration: clip-judge (blind) vs founder — Hello Madam Ep6 gold clips (12)

Blind ratings: `runs/judge/epZ0lbXkgWA/gold_hm_blind.csv` / `.md` (written before opening the founder's file).
Founder: `data/human/ratings_gt_hm_r1.csv` (GT round 1; founder: 4 yes / 6 maybe / 2 no).
Scores below were not changed after seeing the answers.

## 1. Agreement

| dimension | exact | within 1 | mean bias (judge − founder) | MAE |
|---|---|---|---|---|
| hook | 6/12 | 12/12 | +0.17 | 0.50 |
| standalone | 7/12 | 11/12 | +0.33 | 0.50 |
| payoff | 5/12 | 11/12 | +0.33 | 0.67 |
| overall | 7/12 | 10/12 | +0.25 | 0.58 |

**would_post agreement: 7/12.** The judge is slightly generous on every dimension, most on payoff.

Confusion (rows = judge, columns = founder):

| judge \ founder | yes | maybe | no |
|---|---|---|---|
| **yes** | 3 | 2 | 0 |
| **maybe** | 1 | 3 | 1 |
| **no** | 0 | 1 | 1 |

No yes↔no swaps. The founder's yes set was {w02, w03, w06, w12}; the judge's was {w01, w02, w06, w09, w12}.

Per clip (judge/founder):

| clip | hook | standalone | payoff | overall | would_post |
|---|---|---|---|---|---|
| w01 | 4/3 | 4/3 | 4/3 | 4/4 | yes/maybe ← |
| w02 | 4/4 | 4/4 | 4/4 | 4/4 | yes/yes |
| w03 | 3/4 | 3/4 | **3/5** | **3/5** | maybe/yes ← |
| w04 | 4/3 | 3/3 | 3/3 | 3/3 | maybe/maybe |
| w05 | 3/2 | **4/2** | 4/3 | **4/2** | maybe/no ← |
| w06 | 4/4 | 4/4 | 4/4 | 4/4 | yes/yes |
| w07 | 4/3 | 4/4 | 3/3 | 3/3 | maybe/maybe |
| w08 | 3/3 | 3/3 | 3/2 | 2/2 | no/no |
| w09 | 4/4 | 5/4 | 4/3 | 4/3 | yes/maybe ← |
| w10 | 3/4 | 3/3 | 3/2 | 3/2 | maybe/maybe |
| w11 | 2/2 | 3/3 | 3/3 | 2/2 | no/maybe ← |
| w12 | 4/4 | 4/3 | 5/4 | 5/4 | yes/yes (founder: "kind of yes and maybe") |

## 2. What the founder's numbers say about their scale (this round)

- **would_post is almost a pure function of payoff and standalone:** payoff ≥ 4 → yes (4/4: w02, w03, w06, w12);
  payoff 3 → maybe (5/6: w01, w04, w07, w09, w11; the exception w05 → no has standalone 2); payoff 2 → no
  (w08) unless the hook is 4 (w10 → maybe). The agent file's rule already says this; my misses are payoff misses
  plus one case where I ignored the rule.
- **Payoff is scored on the LAST beat, not the best beat.** w09's punchline ("ఆ దొంగవి నువ్వే") is at 43 s of 75;
  the clip then runs 30 s of the mother's excuse and ends on "సరే సరే" → founder payoff 3. w07 (toast at 53 s,
  then a tail) → 3. w10 (laughs mid-clip, ends on a reply "చాలా బాగుందండి") → 2. w01 (ends on her question) → 3.
- **A comic aside that voices the inversion is a punchline, even as an exit line.** w03 ends on Aravind's "ఏదో తేడా
  తేడాగా ఉంది, urgent గా వెళ్ళిపోవాలి" after expecting to be beaten and getting "All the best" instead → founder
  payoff 5, overall 5. I read it as a fade.
- **Hook 4 = the first finished line poses a mystery or inversion**, not merely a confrontation: "sorry for that
  night, I'm basically a good guy" (w03) and "will aunty call the police for what I did?" (w10) got 4; the breakup
  statement (w04), "నాకు తెలుసు / ఏంటి" with the claim arriving at 4–8 s (w01), and the wine proposal arriving at
  3–8 s after "ఏంటమ్మా అలా చూస్తున్నావ్" (w07) got 3; phone greetings (w05) got 2.
- **Standalone:** 4 for a self-contained domestic scene (w03, w09, w02, w06, w07); 3 whenever an absent named
  character (Soham) carries the meaning (w01, w04, w08, w12); 2 when the relationship between the on-screen people
  is never stated (w05: nothing in the clip says Soham is her boyfriend, so "his wife and daughter" cannot hurt).
  No 5 given.
- Silent stretches count: w05 (9 s of her walking + 5 s empty lobby in a 43 s clip) → overall 2, "no".

## 3. Every clip that differed by 2+ on a dimension or on would_post

### w03 — payoff 3 vs 5, overall 3 vs 5, maybe vs yes (biggest miss)
What the founder saw: a complete comedy arc — he comes to apologise expecting a slap, the mother says "once you
marry Hamsini we can all drink together… we drink wine too", then "All the best, నా support నీకే", and his
bewildered exit "something's off, I must leave urgently" is the button of the whole inversion. The exit aside is
the punchline, not a fade; the laugh ("All the best ఆ ఏమవుతుందిరా" + [నవ్వు]) 12 s before the end warms it up.
What I did: applied "payoff 3 = a line that closes the exchange without landing" to an aside that IS the landing,
and docked the join (position jump, 2.6 s music lead-in) that the founder did not notice or mind. Also hook: I
scored the apology as a plain statement; the founder scored "ఆ రోజు నైట్ జరిగిన దానికి sorry" as a tease (what
happened that night?) → 4. Standalone: the unexplained "that night" did not hurt → 4.

### w05 — standalone 4 vs 2, overall 4 vs 2, hook 3 vs 2, maybe vs no
What the founder saw: the clip never establishes that Soham is her boyfriend ("Hello సోహం / ఎక్కడున్నావ్ / Normal గా
చేశాను, bye" is neutral), so the reveal "సోహం సర్ వాళ్ళ wife and పాప" has no one to hurt; plus 14 s of silence
(walking, empty lobby) in 43 s and a phone-greeting opener. What I did: filled in the relationship from the other
clips/the episode and scored the reveal as if it landed. I did flag the dead lobby shot but only as a "maybe" flaw.

### w01 — payoff 4 vs 3, hook 4 vs 3, standalone 4 vs 3, yes vs maybe
What the founder saw: 45 s of his lecture, then her question "నీకు సోహం గురించి ఏం తెలుసు" — an open question
is payoff 3 no matter how angry she is; "నాకు తెలుసు / ఏంటి" is a two-word opener whose actual claim arrives at
4–8 s → hook 3; Soham is a name the viewer can't place → standalone 3. What I did: scored the emotional peak and
her on-screen fury as a payoff, and let a claim that completes at 8 s count as a 3-second hook.

### w09 — payoff 4 vs 3, overall 4 vs 3, yes vs maybe (standalone 5 vs 4)
What the founder saw: the punchline is at 43 s; the last 30 s are the mother's "doctors said wine is good"
excuse and "ఆరాలు తీయడం ఆపవే / సరే సరే" — a soft close, so payoff 3 and maybe. What I did: credited the best beat
and the laughter hold at the end. (Fix for the clip: end on "ఆ దొంగవి నువ్వే అనమాట" + the mother's reaction, or
right after "ఆహా! ఉమ్".) Also, I gave a standalone 5 the founder never gives.

### w11 — no vs maybe (all four dimensions identical: 2/3/3/2)
Pure threshold error on my side: the agent file's rule (no = payoff ≤ 2 or standalone ≤ 2 or far stitch) gives
"maybe" for payoff 3 / standalone 3, and the founder followed it. I downgraded to "no" for the weak hook and
overall 2. The rule worked; I didn't apply it.

### Other within-1 differences worth noting (not misses)
- w10: founder hook 4 for the self-talk worry "police కి call చేయదు కదా" (a mystery tease), payoff 2 for ending on
  the reply "చాలా బాగుందండి"; still "maybe" because hook 4 rescues a payoff 2.
- w12: founder standalone 3 (Soham/Keerthi backstory), payoff 4 (reveal, not 5), and "kind of yes and maybe" —
  my 5/5 was a touch high but the verdict matched.
- w07, w04, w08, w02, w06: matched on would_post and within 1 everywhere — the "stray tail after the payoff",
  "plea with no answer", "promo withholds the payoff" and "payoff ≥ 4 → yes" rules all held.

## 4. Proposed smallest rule changes to `.claude/agents/clip-judge.md` (not applied)

Each is an addition or a one-clause tightening; none touches the payoff ≥ 4 → yes / stray-tail / far-stitch rules,
which matched.

1. **Payoff = the last beat.** Add under "Payoff is what decides a post": *"Score the final spoken line/beat, not
   the best one. If the strongest line is more than ~15 s before the end and what follows is weaker (an excuse, a
   reply, 'సరే సరే', a laugh hold), payoff ≤ 3 — and the fix is to end on the strong line."* (Fixes w09; keeps w07,
   w10 right.)
2. **Open-question endings are 3, acting does not upgrade them.** Add to Payoff 3: *"…or an ending that is a
   question or a demand ('నీకు X గురించి ఏం తెలుసు?'), even with anger or tears on screen. Reactions upgrade a line
   that answers or reveals, not one that asks."* (Fixes w01; w12's reveal stays 4–5.)
3. **A comic aside that voices the inversion is a punchline.** Add to Payoff 4–5: *"A character's aside / self-talk
   that states the inversion of what he expected ('I thought she'd hit me, she's chill — something's off, I must
   leave') is a punchline, including as an exit line; 5 when it caps two or more laugh beats in the clip."*
   (Fixes w03.)
4. **Standalone needs the relationship inside the clip.** Replace the "2:" clause with: *"2: the point depends on
   something the viewer cannot know — including a reveal whose sting depends on a relationship the clip never
   states (nothing in the clip says X is her boyfriend → 'his wife' cannot hurt). A name the payoff hinges on but
   the clip never explains (Soham) caps standalone at 3. Do not give 5; the founder has not."* (Fixes w05, w01/w12
   standalone, w09's 5.)
5. **Hook 4 = a mystery or inversion finished by ~3 s.** Rewrite Hook 4 as: *"the first finished line (by ~3 s)
   poses a mystery or inversion ('sorry for that night, I'm basically a good guy', 'will she call the police for
   what I did?', 'ఏరా ఆ అమ్మాయి అంత నచ్చిందా?'). A confrontation or breakup statement with no mystery is 3; a
   two-word opener whose claim only completes at 4–8 s is 3; a greeting / phone small-talk opener is 2."*
   (Fixes w01, w03, w05, w07, w10, w04 — six hook diffs, all within 1 but all in one direction.)
6. **Apply the would_post thresholds mechanically.** Add: *"Do not drop to 'no' for a weak hook or a low overall
   when payoff is 3 and standalone ≥ 3 — that is a maybe. 'no' needs payoff ≤ 2 (and hook ≤ 3) or standalone ≤ 2
   or a far stitch; payoff 2 with hook 4 is still a maybe."* (Fixes w11; matches w10 and w08.)
7. **Silence is a concrete flaw.** Add to the named flaws: *"silent stretches ≥ 4 s (walking, an establishing
   shot, fidgeting); two of them in a clip under 60 s pull overall to 2."* (Supports w05's overall 2.)

Expected effect if re-scored with these rules: would_post agreement 11/12 (w03 yes, w01 maybe, w09 maybe, w11
maybe, w05 no — w05 depends on reading the missing relationship, rule 4), payoff within-1 12/12, no 2-point gaps.
Untested claim: this is a re-reading of the same 12 clips, so it is fitted; it needs the next founder round to
confirm.
