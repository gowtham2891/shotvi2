# Round-2 web-series GT review (telugu-content-strategist), 2026-10-05

Scope: Couple Goals Ep1 `j14Om1SaFYE` (8 clips) and Ep2 `QWIvgoTQGKk` (12 clips), `data/gold/<id>.json` as of
commit 3f5618d. Free work: no LLM calls. I read each clip's `time_segments` in play order against the Scribe words,
plus the existing keyframes and new ffmpeg sheets for the openings (Ep1 w01/w02/w06, Ep2 w03/w10, Ep1 stool beat).

## 1. Blind calibration on round 1 (21 clips)

My ratings went into a scratch file before I opened `data/human/ratings_gt_webseries_r1.csv`.
**Not fully blind:** the brief and the agent file already gave away some founder scores: Ep1 w05 = 1 on every
dimension, Ep1 w09 hook = 4, Ep2 w02 hook = 3, Ep2 w10-w12 overall = 5, the Ep1 w02 ending note, and the averages
(hook 3.3/3.4, payoff 4.0-4.4). For those cells I wrote down the score I would have given anyway (lower than the
founder's for w10-w12), and I report agreement with those cells and without them.

| Clip | Me H/S/P/O/post | Founder H/S/P/O/post | Leaked |
|---|---|---|---|
| Ep1 w01 sweets | 4/4/5/4 yes | 3/5/5/4 yes | |
| Ep1 w02 full meals | 4/5/3/4 yes | 3/5/4/3 maybe | ending note |
| Ep1 w03 short/stool | 4/3/3/3 maybe | 4/4/4/3 yes | |
| Ep1 w04 gongura | 3/4/4/4 yes | 4/4/4/4 yes | |
| Ep1 w05 far-stitch promo | 3/2/2/2 no | 1/1/1/1 no | all |
| Ep1 w06 confession promo | 4/3/4/3 maybe | 4/4/4/4 yes | |
| Ep1 w07 chapati/cooker | 4/4/4/4 yes | 3/4/5/4 yes | |
| Ep1 w08 lies cliffhanger | 3/3/4/3 maybe | 4/4/4/4 yes | |
| Ep1 w09 hostel food | 4/4/4/4 yes | 4/4/5/4 yes | hook |
| Ep2 w01 short girls hug | 2/3/5/4 yes | 4/5/5/4 yes | |
| Ep2 w02 bajji cliffhanger | 3/4/5/4 yes | 3/5/5/4 yes | hook |
| Ep2 w03 amma chethi vanta | 2/4/5/4 yes | 3/4/4/4 yes | |
| Ep2 w04 father's lie story | 3/4/4/4 maybe | 3/4/4/4 yes | |
| Ep2 w05 chai monologue | 4/4/4/4 yes | 4/4/4/4 yes | |
| Ep2 w06 first-night hug ask | 3/4/5/4 yes | 3/4/4/4 yes | |
| Ep2 w07 morning chai | 3/4/4/3 maybe | 3/3/3/3 maybe | |
| Ep2 w08 egg dosa boxes | 3/4/5/4 yes | 4/4/5/4 yes | |
| Ep2 w09 first night over-hyped | 3/5/4/4 yes | 4/5/5/4 yes | |
| Ep2 w10 mom's promises | 3/4/4/4 maybe | 4/4/5/5 yes | overall |
| Ep2 w11 popcorn | 2/4/4/4 maybe | 3/4/4/5 yes | overall |
| Ep2 w12 nail polish | 2/3/4/3 maybe | 3/4/5/5 yes | overall |

**Agreement**

| Dimension | All cells: exact / within 1 | Leaked cells excluded: exact / within 1 | Mean bias (me minus founder, excl.) |
|---|---|---|---|
| Hook | 8/21 = 38% / 90% | 6/18 = 33% / 94% | -0.39 |
| Standalone | 12/21 = 57% / 95% | 12/20 = 60% / 95% | -0.35 |
| Payoff | 10/21 = 48% / 100% | 10/20 = 50% / 100% | -0.20 |
| Overall | 14/21 = 67% / 95% | 14/17 = 82% / 100% | -0.06 |
| would_post | 13/21 = 62% | 12/20 = 60% | I said "maybe" 8 times, the founder 2 |

### Where I was most wrong, and what I take from it
1. **Ep2 hooks: I penalised music lead-ins too hard.** Ep2 w01: I gave 2, the founder 4. The 2.3 s lead-in shows
   him waiting at the door, and the opening exchange ("దేనికో waiting." / "అబ్బచ్చా! అప్పుడే మర్చిపోయావా?")
   is a romantic tease. A lead-in costs about 1 point (w03, w11, w12 got 3), not 2, and a character on screen is
   less costly than pure music.
2. **Ep1 hooks: I over-rated plain openers.** I gave 4 to "మీకు వంట వచ్చా?" (a 0.7 s question that cuts away
   immediately), "ఈ రోజుల్లో marriage అనేది full meals లాంటిది" (start of a monologue) and "నూనెయ్యవే!"
   (a shout, then 5 s of music). The founder gave all three a 3. **His 4 is a line that opens a loop between two
   characters:** a tease ("ఏరా ఆ అమ్మాయి అంత నచ్చిందా?", "night బొమ్మ blockbuster ఆ?"), a confrontation
   ("ఓసేయ్! నీకేమన్నా పిచ్చా?"), an ironic assumption ("మీరు బయట food తినరు కదా"), or a request that puts the
   heroine on the spot ("నాకోసం ఏం వండావు?", "tiffin ఏం చెయ్యాలి?" → "చెయ్యించాలి కదా"). Small talk,
   exposition, a thesis line or a shout gets 3. He gave no 5 for a hook in round 1.
3. **Overall is "will this Reel do well", not an average.** Soft romance and mother-daughter comedy with strong
   visual beats (Ep2 w10, w11, w12) got overall 5 with hooks of 3-4. I scored them 3-4 and "maybe".
4. **Standalone is generous.** He doesn't mark down series context (Ep1 w06/w08 got 4 where I gave 3; Ep2 w01 got
   5 where I gave 3). For him, standalone means "the scene makes sense", not "a cold viewer knows the backstory".
5. **A boundary glitch hits overall and would_post, not payoff.** Ep1 w02 kept payoff 4 but got overall 3 and
   "maybe" for the stray last line. **A far stitch is fatal:** Ep1 w05 got 1 on every dimension, no partial credit
   (I gave 2s). Ep1 w03 got overall 3 with every dimension at 4. That was probably the length or the missing stool
   visual (round 1 played word pieces only, so the silent stool gesture was cut).
6. **He says yes to posting easily:** 18/21 yes. He says "maybe" only when there is a concrete flaw (the w02 ending,
   or Ep2 w07, which was weak everywhere).

## 2. Round 2 (20 clips): predicted founder ratings

Applied: hook 4 needs a character-to-character loop in the first 2-3 s; music or silent lead-ins cost about 1;
standalone is generous; overall follows how strong the clip is as a Reel; "yes" unless there is a concrete flaw.

| Clip | H/S/P/O/post (predicted) | R1 founder hook → my R2 hook | Opening hooks in 2-3 s? Why (one line) |
|---|---|---|---|
| Ep1 w01 sweets | 4/5/5/4 yes | 3 → 4 | Yes. Same question, but now he asks it straight to camera and it holds 2.8 s on Amrutha's wide-eyed panic (checked frames 211-215). Lifting the line won't do more; the reaction is what sells it. Low confidence on a 4: it's the same words he gave 3. |
| Ep1 w02 full meals | 4/5/4/4 yes | 3 → 4 | Yes. "వాడు marriage గురించి ఎలా explain చేసాడో తెలుసా?" is a curiosity question, and "వాడు" is on screen 3 s later. The ending fix removes the founder's complaint. |
| Ep1 w03 short/stool (cold open) | 4/4/4/4 yes | 4 → 4 | Yes. Bitter list line, already rated 4. The lifted line is the same day and the same bride-viewing (mother in the green saree), so it works out of context and isn't repeated. **Risks:** 99.9 s played and 4 internal cuts (the rule is ~3). |
| Ep1 w04 gongura | 4/4/5/4 yes | 4 → 4 | Yes. The opener is unchanged (ironic "మీరు బయట food తినరు కదా"). The new end hold on her lip-bite helps the payoff. |
| Ep1 w06 confession promo | 4/4/4/4 yes | 4 → 4 | Yes. "నేను ఒక విషయం చెప్పాలి మీకు" / "అంతకన్నా ముందు నేను మీకు ఒక విషయం చెప్పాలి" is a tension hook. The opening frames are clean (checked 708.9-712.9; the brand banner sat on the old opening). **Cost:** the line telling a new viewer she can't cook ("వండరాదని…") is gone, so the promo's irony now rests on the title. |
| Ep1 w07 chapati/cooker (cold open) | 4/4/5/4 yes | 3 → 4 | Yes. "పర్లేదమ్మా చపాతీ కాస్త పూరీ అయిపోద్ది" + mom's "ఏం తెలివే" is a deadpan two-shot with a reaction cut, and it reads without context (chapati turning into puri = drowned in oil). It repeats about 15 s later; that's short, but the repeat lands as the joke paying off once the viewer has seen the oil pour. |
| Ep1 w08 lies cliffhanger | 4/4/5/4 yes | 4 → 4 | Yes, unchanged. The 7.5 s end hold on the frozen bride and mother makes it a stronger cliffhanger. Still `spoiler: true` (shows the marriage); founder decides. |
| Ep1 w09 hostel food | 4/4/5/4 yes | 4 → 4 | Yes. Starts on "ఏరా…" instead of a song tail. Restoring the dropped line costs nothing. |
| Ep2 w01 short-girls hug | 4/5/5/4 yes (overall 5 possible) | 4 → 4 | Yes. Lead-in removed; opens on the tease. |
| Ep2 w02 bajji cliffhanger (cold open) | 4/5/5/4 yes | 3 → 4 | Yes. Opens on her worried close-up for 1.3 s, then his brag "నా పెళ్ళాం నాకోసం మిర్చి బజ్జీలు వేస్తుందే". Her face carries the irony out of context, and the in-place repeat sits inside a longer brag, so it feels natural. **Risk:** 96.7 s. |
| Ep2 w03 amma chethi vanta | 4/4/4/4 yes | 3 → 4 | Yes. The 3.9 s of chewing is gone, and the frantic denial comes within 2 s of the compliment. An 18 s clip doesn't need a cold open. |
| Ep2 w04 lies promo (cold open) | 4/4/4/4 yes | 3 → 4 (could be 5) | Yes, strongly. "అబద్ధాలు చెప్పి పెళ్లి చేసుకోవడం correct ఏనా?" is a debate-bait question. It is cut exactly on the flashback shot (404.38-408.12) and gives nothing away. The repeat is framed as memory ("పెళ్ళికి ముందు ఏమన్నావే?"), so it's the most natural repeat of the 8 cold opens. |
| Ep2 w05 chai monologue | 4/4/4/4 yes | 4 → 4 | Unchanged. The punchline is hidden in the transcript by the ASR (see table). |
| Ep2 w06 first-night hug ask (cold open) | 4/4/4/4 yes | 3 → 4 | Mostly. The odd demand "రోజూ నువ్వు నా heart తో communicate అవ్వాలి" + her baffled "ఆ!" close-up creates a mystery. **But** the same 4 s, including the slap SFX and the laugh, replay only ~16 s later. Watch whether the repeat reads as a glitch. |
| Ep2 w07 morning chai (cold open) | 4/3/3/3 maybe | 3 → 4 | The hook is fixed: "…మొగుడికి చాయ్ ఇచ్చి చంపేసిన భార్య అని paper లో వస్తే" is the funniest line and states the premise on its own. The payoff is still the soft "బ్రతికించారు" relief, so overall stays 3. |
| Ep2 w08 egg dosa boxes | 4/4/5/4 yes | 4 → 4 | Yes. Lead-in removed. |
| Ep2 w09 first night over-hyped | 4/5/5/4 yes | 4 → 4 | Yes. Lead-in removed. |
| Ep2 w10 mom's promises | 4/4/5/5 yes | 4 → 4 | Yes. 1 s of music removed; opens on the trio at the stove, no banner (checked frames 289.5-293.5). |
| Ep2 w11 popcorn (cold open) | 4/4/4/5 yes | 3 → 4 | Yes. Her wail "అమ్మే!", then her crying face with an ice pack, then mom's "నా కూతురు kitchen లోకి వెళితే ఏమవుతుందో నాకు తెలియదా": a great "what happened?" hook. Note that the first 1.1 s is the pot shot (the shot change is at 1096.33). The whole 4.9 s exchange then repeats verbatim after the popcorn. |
| Ep2 w12 nail polish (cold open) | 3/4/5/4 yes | 3 → 3 | Weak. "nail polish వేస్తున్నావా? నేను వెయ్యనా?" is sweet but soft, and the identical 5 s exchange replays only ~25 s later. That deja vu may cost the overall 5 it got in round 1. There is also a 0.22 s flash of the next wide shot at the end of the cold open (shot change at 551.88, segment ends 552.10). |

**Predicted average hook, round 2:** Ep1 **4.0** (8 clips) vs founder round 1 3.33 (9 clips; 3.63 without the dropped
w05). Ep2 **3.9** vs 3.42. Honest error bar: about ±0.3. My round-1 hook bias was -0.39, and every 4 I predict
for Ep1 w01/w02 is a "same or similar words now with a reaction" call he may still score 3. Predicted payoff stays
4.3-4.6, overall about 4.1, would_post yes 19/20 (Ep2 w07 maybe).

## 3. Suspected ASR errors in round-2 clips

Audio plays correctly, so this mainly matters for captions and for the LLM pipeline. A misheard punchline makes the
transcript-only selector under-rate the moment (Ep2 w05 and w07 are clear cases).

| Ep | Time | Heard | Probable | p (conf) | Impact |
|---|---|---|---|---|---|
| 2 | 187.9 | ప్రతికించారు | బ్రతికించారు ("you saved me") | 0.50 | **High.** It is w07's only payoff line. |
| 2 | 254.7 | blood గుర పడితే | blood group అడిగితే | 0.49 | **High.** It hides the B+/T+ punchline of w05. |
| 1 | 902.6, 916.3 | కూరంటా | గోరింటాకు / గోరింట (henna) | 0.60 / 0.94 | **High** for captions: the whole gongura/gorintaku joke (w04). |
| 2 | 371.8 | కుక్కను పెట్టుకుందాం | cook ను పెట్టుకుందాం | 0.84 | **Medium-high.** A caption saying "dog" makes a joke nobody intended (w04). |
| 2 | 1224.9 | బిస్మిల్లా బాత్ | బిసిబేళ బాత్ (biryani to bisi bele bath) | 0.95 | **Medium + sensitivity.** The ASR is confident, so check by ear. If it really says బిస్మిల్లా, it reads as a religious reference in captions (w02). |
| 1 | 1051.7-1052.2 | ఇల్లు ఈ week చివరనే తీసుకున్నాం | ఇల్లు ఈ వీధి చివరనే తీసుకున్నాం ("at the end of this street") | 0.49 / 0.43 | **Medium.** It explains "ఎప్పుడు కావంటే అప్పుడు రావచ్చు" (w08). |
| 1 | 1094.4 | మటన్ గోలా కొట్టినట్టు | మటన్ కీమా కొట్టినట్టు | 0.23 | **Medium.** It's the last line of the w08 cliffhanger rant. |
| 1 | 571.2 | బల్బులు విగించాలా? | బల్బులు బిగించాలా? | 0.86 | **Medium.** The w03 romantic punchline, in captions. |
| 2 | 290.3 | నాకోసం ఏం వండవు | నాకోసం ఏం వండావు? | 0.80 | **Medium.** It's the opening line of w10, and "you won't cook for me" vs "what did you cook for me" changes the hook. |
| 2 | 775.8 | ఇరుకుదా | ఇవ్వొచ్చు కదా | 0.60 | **Medium.** It sets up the w06 face-plant. |
| 2 | 972.6 | hop off చేస్తే | half చేస్తే (half hug) | 1.00 (wrong anyway) | **Medium** for captions (w01). |
| 2 | 420.6, 422.5 | parent ఏనే / ఎరికిన్చేసావెంటమ్మా | పదానికే / ఇరికించేసావేంటమ్మా | 0.87 / 0.53 | Low-medium (w04). |
| 1 | 1071.9 | అజ్జన్ గా | అర్జెంట్ గా | 0.37 | Low-medium (w08). |
| 1 | 1090.1 | appalam | అప్పడం | 0.39 | Low (w08). |
| 1 | 287.5 | తుమ్ము మొహం దానా! | Can't tell from text; a stock scold ("…మొహం దానా!"). Check by ear. | 0.38 | Low. The meaning ("you idiot") is clear from tone, and the joke is the cover-up after it (w01). |
| 1 | 222.2 | చచ్చింది గొర్ర | చచ్చింది గొర్రె (idiom: "we're done for") | 0.85 | Low (w01). |
| 1 | 561.6 | అదే మీ గంభీరం | Uncertain (గాంభీర్యం? another word?). Check by ear. | 0.70 | Low-medium: it's his compliment line (w03). |
| 2 | 788.4 | బుర్ర లేని మదక్ | బుర్ర లేని మొద్దు (blockhead) | 0.47 | Low (w06). |
| 2 | 386.7 / 347.0 / 471.5 / 508.5 / 1054.1 | మండే పడిపోతాడు / ఏలేసి / ఓడకి / మే ఇద్దరికీ / First easy with | మండిపడిపోతాడు / లేచి / వాడికి / మీ ఇద్దరికీ / First easy ది | 0.47 / 0.91 / 0.65 / 0.43 / 0.39 | Low. |
| 2 | 1228.6 | Kumari Anty | Kumari Aunty | 0.37 / 0.28 | Low (real person, mentioned as praise). |

## 4. Look at these first, and what I'd fix

Ordered by risk. Items marked (taste) are subjective; product-lead or the founder decides.

1. **Ep2 w12, nail polish.** The cold open replays the identical 5 s exchange 25 s later, plus a 0.22 s flash
   frame. **Fix:** drop the cold open and start the clip in place at 547.0 s ("nail polish వేస్తున్నావా?"). Cutting
   at the opening causes no visible jump, so this skips the small talk with no repeat, at about 42 s. The cost is
   losing "husband అంటే respect" (retitle e.g. "Nail polish నేను వేయనా? | Husband goals"). If the cold open
   stays, end it at 551.88. (Mild taste call.)
2. **Ep1 w03, short/stool.** 99.9 s played and 4 internal cuts (the cap is ~3). The opener is good and from the same
   day, so it is not a real "far stitch". **Fix:** cut the 8.8 s silent pre-roll before "అందరు అబ్బాయిలకి…"
   (517.83 → ~523.5, keeping his eye-level look) and shorten the end hold from 7 s to ~3 s. That gives ~86 s and
   keeps the stool push (489-498, checked frames).
3. **Ep2 w06 and w11, verbatim replays.** The cold open repeats within 16 s (w06, with the same slap SFX and
   laugh) and 35 s (w11, the whole 4.9 s exchange). The lifts are good; the question is whether the replay feels
   like a glitch. Founder taste: watch both once. If it bothers, w06's fallback is the in-order start (hook 3).
   For w11, keep the cold open and in the main stretch jump from the popcorn straight to the shot change at
   1100.08 ("ముందు నేను అనాలే…"), dropping the replay. That join is a little loose, since she answers a line
   heard 35 s earlier. (Taste.)
4. **Ep2 w07, morning chai.** The cold open fixes the hook, but the clip still ends on a relief beat with a
   misheard payoff. **Fix:** caption "బ్రతికించారు" and hold on her fake-sweet face after "నేను చేసేదాన్ని
   కదా". Keep it at the bottom of the list; it is the only clip I'd still predict as "maybe".
5. **Ep1 w06, confession promo.** The new opener avoids the brand banner and hooks well, but nothing in the clip
   now tells a new viewer she can't cook. **Fix:** keep the cut and make sure the title/caption carries the secret
   ("వంట రాదని చెప్పేలోపే…" already does). No re-cut needed.
6. **Ep2 w02, bajji cliffhanger.** 96.7 s. The founder rated round 1 (85 s) 4/5/5/4, so length didn't hurt, but
   dropping the Prakasam call (~70 s) also removes the "Kumari Aunty", "పులిహోర కలిపినట్టు" (double meaning)
   and "బిస్మిల్లా బాత్" risks in one cut. Optional alternative lift (taste): "నీకు వచ్చిన పెళ్ళాం కనీసం gas
   stove on/off చేయడం కూడా రాదన్నావ్ కదా": the aunt's taunt is true, which is sharper irony than the brag.
7. **Caption fixes before any publish:** the High/Medium rows in section 3, especially కూరంటా → గోరింటాకు
   (Ep1 w04), blood group అడిగితే (Ep2 w05) and బ్రతికించారు (Ep2 w07).

No change suggested for Ep1 w01, w02, w04, w07, w08, w09 or Ep2 w01, w03, w04, w05, w08, w09, w10. Ep2 w04's cold
open is the model for the others: a lifted question that is repeated inside a "remember what I asked?" frame.
