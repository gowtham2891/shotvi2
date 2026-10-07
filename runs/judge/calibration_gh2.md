# clip-judge calibration: Girls Hostel 2.0 gated set vs founder (2026-10-07)

Blind predictions: `runs/judge/{H41fJJJYTqo,gQZBXu5FAbs,xmx9n4oLs-M}/gold_gh2.csv` + `.md`, gate summary
`runs/judge/gh2_gate.md` (commit 0e25d3e). Founder: `data/human/ratings_gt_gh2_r1.csv` (16 clips shown).
Scores below are the sealed ones; nothing was adjusted after opening the founder's file.

Two ways to read my would_post on the 16, both reported:
- **as filed in the CSVs**: 11 yes / 5 maybe (the five "maybe" were re-cuts I said would become yes once cut);
- **as the gate intended** (every clip shown was a "yes" or a "yes after re-cut"): 16 yes / 0 maybe / 0 no.
The second is the honest one for an absolute-bar gate: I showed 16 clips believing all would be posted; 3 were.

## 1. Agreement on the 16

| clip | mine h/s/p/o wp | founder h/s/p/o wp | |
|---|---|---|---|
| Ep2 w03 Zomato bags fight / చెత్త brain | 4/4/4/4 yes | 5/4/5/5 yes | ok |
| Ep2 w07 ముప్పై వేలు jab (phone) | 4/4/4/4 yes | 4/4/4/3 maybe | miss |
| Ep2 w08 sorry by text | 4/4/4/4 yes | 3/3/2/2 no | yes→no |
| Ep2 w01 child marriage (re-cut) | 4/4/4/4 yes [csv maybe] | 3/3/3/2 maybe | miss |
| Ep2 w06 చదువుకి సంస్కారానికి (re-cut) | 4/3/4/3 yes [csv maybe] | 3/3/3/3 maybe | miss |
| Ep1 w01 light లే | 4/4/4/4 yes | 4/3/4/4 yes | ok |
| Ep1 w04 long distance romance | 4/4/4/4 yes | 2/3/3/3 no | yes→no |
| Ep1 w05 Rapido aside | 3/4/4/3 yes | 3/3/3/3 maybe | miss |
| Ep1 w06 petrol plan (re-cut) | 3/4/4/4 yes | 2/3/3/2 no | yes→no |
| Ep1 w08 Get out first | 4/4/3/3 yes | 4/4/4/4 maybe | miss |
| Ep1 w09 already పెళ్లి అయింది | 4/4/4/4 yes | 2/2/2/2 no | yes→no |
| Ep1 w03 consoling on the roof (re-cut) | 4/3/4/4 yes [csv maybe] | 3/3/3/4 maybe | miss |
| Ep3 w07 Jai's confession / impossible | 4/4/5/5 yes | 4/4/5/4 yes | ok |
| Ep3 w06 video-call exposure | 4/4/4/4 yes | 3/4/3/3 maybe | miss |
| Ep3 w01 one rupee (re-cut) | 4/3/4/3 yes [csv maybe] | 2/2/2/2 no | yes→no |
| Ep3 w02 discussion అయిపోయింది (re-cut) | 4/3/4/3 yes [csv maybe] | 2/2/2/2 no | yes→no |

| dimension | exact | within 1 | bias (mine − founder) | my mean | founder mean |
|---|---|---|---|---|---|
| hook | 5/16 | 12/16 | +0.81 | 3.88 | 3.06 |
| standalone | 7/16 | 15/16 | +0.62 | 3.75 | 3.12 |
| payoff | 3/16 | 12/16 | +0.81 | 4.00 | 3.19 |
| overall | 4/16 | 12/16 | +0.75 | 3.75 | 3.00 |

would_post: as intended 3/16 (yes→yes 3, yes→maybe 7, yes→no 6); as filed 6/16 (yes→yes 3, yes→maybe 4,
yes→no 4, maybe→maybe 3, maybe→no 2). Founder: 3 yes / 7 maybe / 6 no.

Confusion (gate intent → founder):

| | founder yes | founder maybe | founder no |
|---|---|---|---|
| judge yes | 3 | 7 | 6 |
| judge maybe | 0 | 0 | 0 |
| judge no | 0 | 0 | 0 |

Read: a uniform +0.8 inflation on every dimension, not one bad rule. I scored Girls Hostel on the Couple Goals /
Hello Madam scale by matching rule text ("direct address = 4", "reveal = 4", "reaction on screen = 4") instead of
asking "will this Reel do well". The three clips the founder posted are the three I had at conf 4; every conf-2/3
yes failed. All five re-cuts I specified came back maybe or no (two of them 2 across).

## 2. Every miss, from the frames and text

**Hooks the founder gave 2 (I gave 3–4).** All five openers either have no object, address nobody in frame, or
open on a wide shot. The founder's first 5 (Ep2 w03 "నీకు ఈ Zomato bags ఇక్కడ పెట్టొద్దని ఎన్నిసార్లు చెప్పాలి?") has
the subject of the fight inside the line, heat, and the scolder's face in close-up from frame 1.
- Ep1 w04 "ఈ week అయినా వస్తున్నావా ఇక్కడికి": a phone call to an off-screen boyfriend; frame 0.5 is Meenakshi on the
  phone. The agent file already says phone small talk = 2; I gave 4 because it "addresses someone". Phone openers
  cap at 3 (also Ep3 w06 "తాగుతున్నావా?" on the phone → founder 3, Ep2 w07 phone → 4 only because the line carries
  its subject "phone lift చేయవేంటి").
- Ep1 w06 "ఇప్పుడే petrol అయిపోవాలా": an aside muttered on a scooter to nobody. I gave 3; founder 2. An aside is a
  purely visual opening with words.
- Ep1 w09 "మీనాక్షి / ఏంట్రా నీ బాధ": frame 0.5 is a top-down wide of two figures (faces unreadable); first word is a
  name call; the complaint "ఎన్ని సార్లు propose చేసినా" is a whining stalker. I noted the wide shot as "minor"; it
  is not.
- Ep3 w01 "Madam six months advance ఇచ్చిన తర్వాత చెప్తున్నారు. అది ముందే చెప్పాలి కదా": "told me WHAT?" The object is
  missing; the "…కదా" fooled me. Founder 2.
- Ep3 w02 "అక్షర రా పోదాం discussion అయిపోయింది": which discussion? Frame 210 is a static wide of four people around a
  desk. An exit line as an opener, on a wide. Founder 2 (and my re-cut created this opener).

**Payoff 2 on the twist reveal (Ep1 w09, founder 2/2/2/2).** Frames 17–21 do hold his stunned face, which is what I
scored. What the founder saw: in a comedy series, a drama twist out of context is only a line. The clip never
establishes that she is single, that the marriage is a secret, or who she is; a stranger has no stake in "I'm
already married", there is no laugh, and it ends on "ఆ" + a song hold (a cliffhanger, which the founder has rated
2–3 every time: HM1 w08, HM1 w05, Ep2 w10). Standalone 2 confirms it: the sting depends on what the clip does not
say. Also the stalker thread as a whole did not post (Ep1 w06 no, w09 no, Ep2 w01 maybe with overall 2): a pest
character who gets told "go away" is not a Reel.

**Payoff 2 on the text-message reversal (Ep2 w08, founder 3/3/2/2).** Frames 25.5–44: after the join the clip is
19 s of near-silence — Jai typing at a fence (4.4 s), a voice-over of a typed "I am really sorry", Akshara reading,
"ఊ it's ok", and the last 4 s on a phone screen. Nothing is performed on camera; the "reversal" is my reading of the
plot. The last frame is a screen, not a face. Plus the join changes place and medium (food stall phone call → fence
texting), and "sorry for what" is never stated (hook 3, standalone 3). A payoff delivered as text/voice-over/
screen is not a payoff. My rule "silent stretches only cost overall" was wrong when the silence IS the ending.

**Ep1 w04 long distance romance (founder 2/3/3/3 no).** The on-screen laughs (frames 29–32) are the characters'
own; the line is a tease between roommates, not a joke a stranger laughs at; 17 of 32 s are one side of a phone
call. Note the founder gives "no" to hook 2 + payoff 3 (also Ep1 w06, GH2 w04 held set): hook 2 is itself a "no"
condition; my rule only had payoff ≤ 2 / standalone ≤ 2.

**Ep1 w06 petrol plan (founder 2/3/3/2 no).** A callback the viewer must remember from 60 s earlier, paid off by a
subtle pillion smirk (frame 80.5, eyes closed) with no line and no laugh; a stalker fight in the middle is a
topic jump; opener is an aside. "Plan worked" is not a punchline.

**Ep1 w05 Rapido aside (founder 3/3/3/3 maybe).** Frames 34–38: muttered, eyes down, no one else in frame, no laugh
event. My conf was 2 and the inversion-aside rule was the over-credit I had already flagged. A comic aside needs a
laugh beat or another character's reaction in frame to be 4.

**Ep1 w03 roof consoling (re-cut; founder 3/3/3/4 maybe).** Frame 62 shows Akshara's hand resting on her cheek,
sad; it reads as "face in hand", not "wipes tears and smiles" (that was the editor's note, which I repeated). The
smile at 64 is Meenakshi's. The last line is soft advice ("దానికే ఏదో ఒకరోజు తెలిసి వస్తుంది లేవే"). Founder overall 4
(likes the warmth) but payoff 3: an ambiguous gesture is not a visible reaction.

**Ep1 w08 Get out first (founder 4/4/4/4 maybe).** The only 4/4/4/4 maybe in 59 clips. Beyond the numbers: it is a
downer — the lead is humiliated and walks off alone through a glass door (frames 27–31); no comeback, no laugh,
nobody to root for, a one-off antagonist. The founder scored the craft 4 and still would not post a loss. (I had
payoff 3 and said "my gut says maybe"; the rule forced yes.)

**Ep2 w07 ముప్పై వేలు (founder 4/4/4/3 maybe).** A phone-call argument: he on a call (frames 0.5, 24), she at a food
stall; the jab is a petty friend-tease and the first 10 s are "why didn't you pick up". Overall 3 = "won't do
well". Every phone-framed clip in GH2 was maybe/no (Ep1 w04, Ep2 w07, Ep2 w08, Ep3 w06); the three yes clips are
in-person confrontations.

**Ep2 w01 child marriage (re-cut; founder 3/3/3/2 maybe).** No laugh event; the "joke" is the stalker's jab and the
closing line is "మూసుకుని పో" (a stance line); his stunned look is at 25.7, before the retort, and the clip ends on
her close-up. Overall 2 = the stalker thread again. The re-cut fixed the stray scooter shot but the beat itself was
3, not 4.

**Ep2 w06 (re-cut; founder 3/3/3/3 maybe).** My extension to the phone pickup (frames 432–434.5) is 3 s of her
looking down, flat, then lifting the phone — no smile, no change; the viewer must infer who is calling. I broke my
own last-beat rule: I extended past the strong line into a weaker, silent beat. The honest fix was to end on the
quote (inside her turn) or accept maybe.

**Ep3 w06 video call (founder 3/4/3/3 maybe).** The ending is 4 s of Tarun standing up and walking out of frame
(45–48) with Jai alone; no line, no laugh event; the "sheepish grin" at 43–44 is under a mumble. A silent exit under
song is 3. Hook 3: phone opener.

**Ep3 w01 one rupee (re-cut; founder 2/2/2/2 no).** The clip contains a 4 s flashback of Dhana's lines from another
scene (reads as a confusing join), names "ధన" who never appears, and never says what "issues" means → standalone 2.
The warden's refund refusal is a setback that just closes the exchange; the "stunned glance" (frames 102–103) is a
sideways eye-flick that reads as thinking. A downer with no win, and I upgraded a hard line to 4 on a 1.3 s flick.

**Ep3 w02 discussion అయిపోయింది (re-cut; founder 2/2/2/2 no).** My re-cut removed the phone call, which was the only
setup for Meenakshi's boast ("వాళ్లే two minutes లో call చేసి చెప్తారు"); the 14 s that remain drop the viewer at the
end of an unseen conversation among four unnamed people → standalone 2. The "payoff" is the warden's phone ringing
and an 8 s silent hold: a cliffhanger, nobody learns who called. I traded a missing referent for a missing setup.

Common threads in the six "no"s: (1) openers with no object / to nobody / on a wide (5 of 6); (2) endings that are
screens, rings, walk-offs or holds rather than a line performed to camera (3 of 6); (3) drama reveals or setbacks
in a comedy series with no laugh and no stake for a stranger (3 of 6); (4) the stalker thread (2 of 6); (5) re-cuts
scored on expectation, never re-watched as new clips (2 of 6).

## 3. Numeric rule search over all 59 founder-rated web-series clips

Sets: Couple Goals 21 (18 yes / 2 maybe / 1 no), Hello Madam r1 12 (4/6/2), r2 10 (6/4/0), Girls Hostel 16
(3/7/6). Mean overall: CG 3.86, HM1 3.17, HM2 3.60, GH2 3.00.

One-dimensional facts (no exceptions unless stated):
- hook 2 → never yes (0/7: 6 no, 1 maybe). hook 3 → yes 8 / maybe 12 / no 2.
- standalone ≤ 2 → always no (5/5). standalone 3 → yes only 2/19.
- payoff ≤ 2 → never yes (0/7; 6 no, 1 maybe HM1 w10). payoff 3 → yes 1/19 (HM2 w06 4/4/3/4). payoff 4 → yes 17/20
  (maybes: CG w02 flaw, GH2 w07, GH2 w08). payoff 5 → yes 13/13.
- overall ≤ 2 → never yes (7 no, 3 maybe). overall 3 → yes 1/15 (CG w03 4/4/4/3). overall ≥ 4 → yes 30/33.

**No rule on the four numbers is exception-free**: the tuple 4/4/4/4 holds 10 yes + 1 maybe (GH2 w08) and 4/4/4/3
holds 1 yes (CG w03) + 1 maybe (GH2 w07). Best rules:

| rule | fit | exceptions |
|---|---|---|
| yes ⇔ three of (h,s,p,o) ≥ 4 | 57/59 | GH2 w07 4/4/4/3 maybe; GH2 w08 4/4/4/4 maybe |
| yes ⇔ payoff ≥ 4 and overall ≥ 4 | 56/59 | CG w03 4/4/4/3 yes; HM2 w06 4/4/3/4 yes; GH2 w08 maybe |
| yes ⇔ two of (h,s,p) ≥ 4 (current) | 55/59 | CG w02 (named flaw), HM1 w09 4/4/3/3, GH2 w07, GH2 w08 |
| no ⇔ h+s+p+o ≤ 10 | 57/59 | HM1 w11 2/3/3/2 maybe; GH2 w04 2/3/3/3 no |
| no ⇔ payoff ≤ 2 or standalone ≤ 2 (current) | 56/59 | HM1 w10 maybe; GH2 w04, GH2 w06 (hook-2 "no"s) |

Full 3-way rules (yes/maybe/no):

| rule | fit | errors that would SHOW a non-yes |
|---|---|---|
| current: yes = two of h/s/p ≥ 4; no = p ≤ 2 or s ≤ 2 | 52/59 | 4 (CG w02, HM1 w09, GH2 w07, GH2 w08) |
| **proposed: yes = p ≥ 4 & o ≥ 4 & h ≥ 3 & s ≥ 3; no = p ≤ 2 or s ≤ 2 or (h ≤ 2 & p ≤ 3)** | 54/59 | **1 (GH2 w08)** |
| alt: yes = three of four ≥ 4; same no | 55/59 | 2 (GH2 w07, w08) |

Under the new cost function (showing a "no" is worse than hiding a "yes"), the proposed rule is the one to use:
its other four errors are all on the safe side (CG w03 and HM2 w06 yes→maybe hidden; HM1 w10, w11 maybe→no hidden).
The two GH2 4-cluster maybes cannot be separated numerically; they need the content rules below (downer beat,
phone-call framing).

Caveat: these fits use the founder's numbers. My own numbers on GH2 ran +0.8 high on every dimension, so the
scoring rules (section 4, B–D) matter more than the would_post formula.

## 4. Proposed changes to the agent file (smallest set; not applied)

**A. Strictness / absolute bar (replace the would_post block)**
- yes = payoff ≥ 4 AND overall ≥ 4 AND hook ≥ 3 AND standalone ≥ 3 AND no concrete flaw AND confidence ≥ 3.
  "Two of three" is retired (it let hook+standalone carry a payoff-3 clip: Ep1 w08, Ep1 w07, Ep3 w08).
- no = payoff ≤ 2, or standalone ≤ 2, or hook ≤ 2 with payoff ≤ 3, or a far stitch. (The founder has never posted a
  hook-2 clip, 0/7.)
- maybe = everything else. When unsure between yes and maybe, say maybe; when unsure between 3 and 4 on any
  dimension, give 3. A "yes" with confidence 2 is a maybe. Expect many episodes to yield 0–3 yes; never pad.
- The gate shows ONLY clips scored yes on the cut as it plays. A re-cut is a new clip: extract its frames, print
  its text, score it again; never show a re-cut on the expectation that it becomes a yes (all five GH2 re-cuts
  came back maybe/no; two were 2 across). If the fix cannot be verified from frames, hold.
- Calibrate per series, not per rule: before scoring a new series, note its mean energy. If the first three clips
  all look like 4s, re-read them — the founder's series means were 3.9 / 3.4 / 3.0.

**B. Genre rule (new block under "Payoff")**
- Decide the genre from the clip and the show: comedy / youth series (laugh events in the transcript, skit
  rhythm: Girls Hostel, Couple Goals) vs drama (Hello Madam).
- Comedy: only a laugh beat is a payoff ≥ 4 — a punchline with a [నవ్వు] event or a visible laugh/grin from ANOTHER
  character in the frame, or a comic reversal spoken on camera. Reveals, twists, hard lines, setbacks, "plan
  worked" callbacks, stance lines, exits under song: 3 at most. (Ep1 w09 twist → 2; Ep3 w01 refund → 2; Ep1 w06 → 3.)
- Drama: an emotional peak or a reveal is 4 only if (a) the clip itself stated what the reveal overturns, and
  (b) a face reacts in close-up for ≥ 1.5 s after the line. A reveal the viewer has no stake in is 3; a
  cliffhanger (phone rings, "why" withheld, question) is 2.

**C. Payoff narrowing (edit existing bullets)**
- "Performed to camera": the last beat must be a line or a laugh by a person on screen. A payoff delivered as a
  text on a phone screen, a voice-over of a message, a ringing phone, a character walking out of frame, or a
  silent hold longer than ~3 s at the very end is payoff ≤ 2 (Ep2 w08, Ep3 w02, Ep3 w06). Keep: silent stretches
  INSIDE a clip only cost overall; a silent ENDING caps payoff.
- Comic aside: 4 only with a laugh event or another character's reaction in frame; a mutter with eyes down to
  nobody is 3 (Ep1 w05). Replace the "inversion aside = punchline" bullet with this.
- Visible reaction must be unambiguous (a laugh, a gasp, tears, a smile that changes the face, a shove). An
  eye-flick, a hand resting on the cheek, a glance down does not upgrade (Ep3 w01, Ep1 w03). If the reading of
  the gesture comes from the editor's note rather than the frame, do not use it.
- Downer beats: a beat where the lead loses with no comeback (humiliated, evicted, refused a refund, told off) is
  payoff ≤ 3 and never yes, whatever the craft score (Ep1 w08 4/4/4/4 maybe; Ep3 w01, w02 no).
- Pest/antagonist threads: a clip whose exchange is with a character the viewer is meant to dislike and that ends
  on "go away" / a threat is maybe at most (the stalker clips: no, no, maybe).

**D. Hook narrowing (edit existing bullets)**
- 4 needs all three: direct address, the subject of the conflict inside the line, and the speaker's face on screen
  in the first frame. 5: a scolding with the subject in it and heat ("ఎన్నిసార్లు చెప్పాలి Zomato bags పెట్టొద్దని") in
  close-up.
- 2: the line's object is missing ("చెప్తున్నారు" what? "discussion అయిపోయింది" which?), an aside to nobody, a name
  call, a phone call to someone off-screen, or a first frame that is a wide/establishing shot. "…కదా" alone is not
  a 4.
- Phone-call openers cap at 3 even when pointed ("తాగుతున్నావా?").

**E. Phone-call framing (new, tentative, n = 4)**
- A clip whose exchange is a phone call (one face talking to a phone, the other cross-cut or absent) caps overall
  at 3 and is not a yes unless the ending is a laugh beat with both faces in one frame. All four phone-framed GH2
  clips were maybe/no; the three yes clips are in-person confrontations. Re-check on the next series.

**F. Calibration note to append**
- Girls Hostel 2.0 gated set, sealed: would_post 3/16 as intended (6/16 as filed); every dimension inflated +0.8;
  payoff exact 3/16; 6 yes→no swaps (Ep1 w04, w06, w09; Ep2 w08; Ep3 w01, w02). Causes: direct-address = 4
  applied to openers with no object; reveals/setbacks credited as payoffs in a comedy series; endings on
  screens/rings/walk-offs credited via plot reading; ambiguous gestures counted as reactions; re-cuts shown without
  re-scoring. Fixed by A–E.

Effect check of A on the earlier sets (with the founder's numbers): Couple Goals 20/21 (CG w03 4/4/4/3 would be
maybe, a hidden yes), Hello Madam r1 10/12 (two maybes would be no, hidden), r2 9/10 (HM2 w06 4/4/3/4 would be
maybe, hidden). GH2 15/16 (w08 remains a yes by numbers; rule C "downer beat" catches it). Nothing earlier flips
to "shown a non-yes".
