# Shotvi labeling guide: rating a Telugu short

Owner: `telugu-content-strategist`. Version 1.0 (2026-09-27).
Used by human labelers, the LLM judge (`eval/`), and the gold files in `data/gold/`. Field names match
`docs/spec/output_schema.md`: `scores = {hook, standalone, payoff, value_emotion, overall}`, each 1–5
(halves allowed, e.g. 3.5). The rules the scores feed are in `docs/spec/clip_spec.md` §6.

## How to rate

1. **Read (or watch) the kept text only, in playback order.** Rate what the viewer would get, not the episode
   around it. If you know the episode, pretend you don't.
2. **Score the four parts first, then overall.** Overall is a judgement, not an average (see the overall section).
3. **Write one line of why.** Name the hook and the payoff ("hook: 'మన ఇంట్లో పనివాళ్ళు మనకంటే ఎందుకు strong?';
   payoff: '30 నిమిషాలు workout – that is your fountain of youth'"). "Good content" is not a reason.
4. **Flag, don't punish, taste calls.** Crude language, named brands, health claims, politics, religion: score the
   clip on craft and add the flag. The creator decides what they post.
5. **Judge the moment and the cut together.** A great moment cut badly (starts mid-thought, ends before the
   punchline) gets low standalone or payoff. Say in the why whether the fix is a boundary nudge
   ("start 6s earlier at the question") or whether the moment itself is weak.

Genre does not change the scale. A 4 in a comedy podcast and a 4 in a finance podcast are equally likely to be
posted by their creators.

---

## 1. Hook: will a scrolling Telugu viewer stop in the first ~3 seconds?

Rate the **first spoken line** (the `hook_line`), plus the first frame of on-screen text if the clip has one.

| Score | What it looks like | Anchors |
|---|---|---|
| **5** | A line that works on its own as a caption: bold claim, counter-intuitive question, a named celebrity plus a tease, a punchline, a raw emotional line. Understood with zero setup. | "మన ఇళ్ళల్లో పనివాళ్ళు మనకంటే ఎందుకు strong గా ఉంటారు?" · "Mutton తింటే cancer వస్తది." (as a cold open the clip then answers) · "ఈ సమాజం they understand only two languages: success and money." · "Steroid invention was in 1930. Body building was there from 1800s." |
| **4** | Clear, specific, intriguing topic or a strong setup, but it takes a sentence to bite. | "బిర్యాని నాకు చాలా ఇష్టం అండి. నా cheat meal బిర్యానీ." · "Friends అంతా వెళ్లిపోయారు. ఎవడు friend అని అప్పుడు తెలిసింది." · host question that names the stakes: "మీ direction versus మీ production – conflict ఎలా ఉంటది?" |
| **3** | Topic is clear but flat: a plain question, a generic opener, a host wind-up of 5–10s before the guest speaks. | "For example lately I have spoken with one client of mine…" · "ఇప్పుడు మనకి ఈ zero sugar coke…" (the good line arrives at ~15s) |
| **2** | Slow or confusing: filler, a greeting, "so as I said", a reference the viewer can't place in the first seconds. | "Because it's been see దసరాకి పనిచేశా…" · "అంటే ఆ ఇది కూడా…" · "Thank you for calling me." |
| **1** | Nothing to stop for: dead air, mid-word start, crosstalk, an intro or sponsor line. | "Hi sir. Hello. Finally." · "Help అయితే like చేయండి…" |

Notes
- A **cold open** (a later line lifted to the front) is judged as the hook. It must intrigue, not confuse, and
  be resolved by the end of the clip; if it isn't, cap hook at 3 and standalone at 2.
- On-screen text can lift a 3 to a 4 (the title does the stopping), but never rate a 1–2 spoken opening above 3.

## 2. Standalone: does a first-time viewer understand who, what and why?

| Score | What it looks like | Anchors |
|---|---|---|
| **5** | No context needed. Every "ఆయన / అది / అప్పుడు / that" resolves inside the clip. Anyone scrolling gets it. | The diet-coke clip: host states the belief, guest answers it. · Arjun's "I am very very normal… పిల్లల shoes polish చేసి school కి drop చేసేవాణ్ణి." |
| **4** | Clear, with a small gap the viewer fills easily (who the guest is, a film name they may not know, a referent resolved a few seconds late). | "ఆ picture లో climax compose చేస్తున్నాను" resolves as his first directorial within 20s. · A named film (కడలి) the viewer may not know but the story explains. |
| **3** | Understandable but leans on outside knowledge or has one noticeable loose end ("మీ mother అంతా వచ్చారు" referring to the host's mother). | Fan clips that assume you know the guest's films. |
| **2** | A key referent or setup is missing: the answer plays without its question, "ఆయన" never gets a name, the joke depends on something said 10 minutes earlier. | Starting the Puri Jagannadh fan story at "లేదు అదంతా కాదు ఆయన చెప్పినంత కాదు" without the host's quote. |
| **1** | Incomprehensible alone: inside joke, mid-argument, two people's lines cut into nonsense. | A reaction ("అవునా sir!") with no idea what it reacts to. |

Hard cap: an unresolved referent or inside reference in the first 5 kept words caps standalone at **2**
(spec C2).

## 3. Payoff: does it end on something?

| Score | What it looks like | Anchors |
|---|---|---|
| **5** | Ends on the punchline, the reveal, the emotional peak, or a quotable conclusion, and stops right there. | "…మా ఊరుకొచ్చి మమ్ముల్ని కింద వేస్తారా సార్ అన్నాడు. పోరా తేరికి అని train ఎక్కి వెళ్ళిపోయాం." · "…12 మంది చూసి బయట చెప్పారు; next day ఒక bag తో వచ్చారు. రావయ్యా ఇస్తానురా." · "I remember only the pain behind that." |
| **4** | A clear conclusion or satisfying end, slightly soft (a line of wrap-up after the peak, or the peak is good but not great). | "Fitness quality of life పెంచాలి… Body building is a sport. రెండింటిని కలపకూడదు." |
| **3** | Complete thought but anticlimactic: a turning point rather than a resolution, trails into general talk, "that's how it happened". | Arjun's struggle clip ending on "ఎందుకు direction చేయకూడదు" (part 1 of 2). · Allari Naresh meal-timing clip ending on "తనకి help అవుతుంది". |
| **2** | Ends before the point lands, or on filler ("so… అలా"), or the payoff is cut and sits in the next minute. | Ending the diabetes-myth clip before "మీ parents 40 వరకు test చేయించలేదు". |
| **1** | Cut mid-sentence or mid-word; the viewer feels robbed. | "…మీరు రోజూ తింటే ఏమవుతుందంటే" [end] |

## 4. Value / emotion: did the viewer get something?

Any one of these counts: **laugh, feel, learn, or want to argue/share.** Rate the strongest one.

| Score | What it looks like | Anchors |
|---|---|---|
| **5** | Strong on at least one axis and shareable: a real laugh, a lump in the throat, a tip people will save, a claim people will fight about in comments. | Teen asks "అమ్మా ₹2000 కావాలి supplement కి" for a trainer's steroid injection. · Soundarya crying because Arjun didn't win the award. · Mothers eating "పచ్చడి అన్నం, chicken మిగిలితే కొడుకు తింటే చాలు". |
| **4** | Clearly useful, funny, or moving, but a known idea or a smaller moment. | "BCAA waste of money – costly రస్నా." · "Friendship అంటే 'yes I am there' అనే ఒక word." with a quotable line. |
| **3** | Mildly interesting; a fact or anecdote people nod at and scroll on. | Anjaneya statue trivia (32 ft, 180 tons). · Rest 2–3 minutes between sets. |
| **2** | Generic advice anyone could say ("health is important"), or a story with no turn. | "Health చాలా important, అందరూ జాగ్రత్తగా ఉండాలి." |
| **1** | Nothing: logistics, promo, small talk. | "February 14th release. All the best sir." |

## 5. Overall: would the creator post this after at most small boundary nudges?

Overall is the holistic call. Start from the weakest of hook, standalone and payoff, then adjust for value and for
how the parts work together. A clip with a 5 hook and a 2 payoff is not a 3.5; it's a 2 until someone fixes the end.

| Score | Meaning | Typical profile |
|---|---|---|
| **5** | Post it first. Likely one of the channel's best Shorts from this episode. | Hook 4–5, standalone ≥ 4, payoff 5, value 5. |
| **4** | Strong and publishable as is (or with a 1–2 s nudge). Would be in the creator's shortlist. | All parts ≥ 4, or one 3 made up by a 5 elsewhere. |
| **3** | Publishable filler: fine on a slow week or for a niche audience, not a shortlist pick. | Parts 3–4, value 3. |
| **2** | Not publishable without real re-editing (a missing setup, a bad end, a long ramble). The moment may be good; the cut isn't. | Any of hook/standalone/payoff at 2. |
| **1** | Reject. No moment here, or a hard problem (censored audio, sponsor read, intro/outro, meaning changed by the edit). | |

Hard rules (from `clip_spec.md`) that override taste:
- hook, standalone or payoff ≤ 2 → overall ≤ 2 (the clip is never shown; spec C5);
- a kept piece overlaps `[సెన్సార్డ్]`, is a sponsor read or intro/outro, or the edit changes what someone meant
  (drops a "కాదు", pins an answer on the wrong question) → overall 1.

## Publishable

A clip is **publishable** when all of these hold:

1. overall ≥ 3, **and** hook ≥ 3, standalone ≥ 3, payoff ≥ 3;
2. none of the hard problems above (censored overlap, sponsor/intro/outro, meaning change, mid-word cut);
3. the edited length is inside the guardrails (15–150 s; over 120 s needs a reason).

**Strong** = publishable **and** overall ≥ 4. Eval should report both "publishable rate" and "strong rate"; the
creator's review time is spent on the strong ones.

Flags (`sensitive_content` for health claims, named-brand allegations, crude lines, politics, religion) do **not**
make a clip unpublishable. They tell the creator to look before posting.

## Category (one per clip)

I confirm `product-lead`'s list in `docs/spec/output_schema.md` unchanged. Pick the **main reason a viewer would
watch or share**; if two fit, use the tie-break order below.

| Category | Use when the viewer mainly… | Example |
|---|---|---|
| `humour` | laughs. The joke or comic act-out is the point. | "నేను అడ్డం తిరిగితే ఆ అమ్మాయి సేవలు చేయడానికి రాదు" |
| `emotional` | feels (pride, grief, sympathy, nostalgia). | Mother's "నేనున్నాను, ఏదైనా అమ్మిస్తాను" |
| `story` | follows a narrative with a turn (struggle, comeback, incident). | 12 people saw the film; next day buyers came with a bag |
| `reveal` | learns something insider or unknown: behind-the-scenes, money figures, who did what. | Celebrity coaches charge ₹1–8 lakh a month; Nani paid himself |
| `controversy` | wants to argue: a claim against popular belief or naming a practice as harmful. | Chain-marketing liquid diet and a death |
| `advice` | gets a concrete, usable tip or rule. | "Diet Coke: రోజుకి ఒకటి max, water చాలు" |
| `insight` | gets a reframing or opinion that changes how they see something (not a step-by-step tip). | "Child birth is a 3-year project" · "success and money" |
| `motivation` | gets pushed to act (discipline, fitness, career). | Arjun admits he too wants coffee and TV instead of exercise |
| `other` | none of the above; say why in notes. | |

Tie-break when two fit: `humour` > `emotional` > `story` > `reveal` > `controversy` > `advice` > `insight` >
`motivation`. A funny story is `humour`; a sad story is `emotional`; a story whose value is the insider fact is
`reveal`.

Possible future addition (not proposed yet, for `product-lead`): a secondary `relatable` tag. Many of the best Telugu
shorts win on "that's me / that's my amma" (coffee instead of gym, pickle rice, doom-scrolling between sets)
regardless of category. Worth tracking once we have more labels.

## Gold-file conventions (`data/gold/<video_id>.json`)

- Pieces are what an editor would keep: listener backchannels ("ఉమ్", "అవును", "Right"), false starts and
  side tangents dropped; laughter gaps kept only at punchlines. `tightening_labeled: true` on every clip.
- `hook_line` is the verbatim first spoken words (up to the first natural pause within ~6 s). `on_screen_hook`
  (gold-only, optional) is the caption an editor would burn in; it may paraphrase.
- Every skip or cold open has an `edit_notes` entry saying what was dropped or lifted and why.
- `publishable` (gold-only) follows the definition above.
- Gold picks are made **without** looking at the creator's published shorts, so recall against creator clips and
  the "good clips the creator didn't post" set are both measurable.
- Scores are one expert's taste. Where a call is subjective the clip's `notes` says so.
