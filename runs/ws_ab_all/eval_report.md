# Web-series eval: `D:\shotvi\runs\ws_ab_all`

GT = clips the founder would post (`founder_would_post: yes`). Matching: a proposal matches a GT clip if it covers >= 50% of the GT's spoken core and the source-span IoU is >= 0.2; proposals are taken in rank order, each matching at most one GT clip. Leftover proposals are matched the same way against the founder's `maybe` clips (`acceptable`: not a miss, not a false positive), `no` clips (`founder_no`: a negative hit) and the unrated `held` (hidden by the judge gate, never shown to the founder) / `pending` (not yet rated) clips (`unrated`: neither a hit nor a miss; such clips are never counted as missed). Unmatched proposals are not errors: `new` and `partial` ones need the founder's rating. Founder payoff/overall columns are the founder's scores for the GT cut of that moment (informational), not a rating of the proposal.

## Headline: precision at the top

Of the proposals shown, how many land on a founder-yes clip (hit), a maybe (acceptable), a no (founder_no), or nothing rated (unrated: new / partial / on a held or pending clip; dup = a second proposal on a moment already matched). Precision = hits / rated shown. `founder_no shown` target is 0. Count: top-tier shown vs founder-yes count; over-proposing = shown > 2x yes, under-proposing = shown < yes.

| video | founder yes / maybe / no / unrated | shown rec / look / below | top tier | hit / acc / no / unrated / dup | precision (top) | founder_no shown (top / all) | @3 | @5 | all | missed yes | recall yes | count |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| epZ0lbXkgWA | 6 / 4 / 0 / 0 | 4 / 2 / 1 | recommended (4) | 4 / 0 / 0 / 0 / 0 | 4/4 (100%) | 0 / 0 | 3/3 (100%) | 5/5 (100%) | 5/6 (83%) | w09 | 5/6 (83%) | under-proposing |
| j14Om1SaFYE | 8 / 0 / 0 / 0 | 3 / 5 / 0 | recommended (3) | 3 / 0 / 0 / 0 / 0 | 3/3 (100%) | 0 / 0 | 3/3 (100%) | 4/4 (100%) | 7/7 (100%) | w09 | 7/8 (88%) | under-proposing |

Overall (pooled over 2 episode(s)): top tier 7/7 (100%) hits of rated shown (7 shown, 0 unrated, 0 dup); all shown 12/13 (92%) (15 shown); founder_no shown 0 (0 in the top tier); missed yes 2 of 14; count flags: under-proposing x2. Small sample: 14 founder-yes clips, 7 rated top-tier proposals.

## Secondary: recall and boundary metrics

| video | GT yes | maybe / no / unrated | props | recall (any) | recall@k=GT | recall@5 | missed | acceptable | founder_no | unrated hits | mean abs dstart / dend (s) | payoff+hold kept | drops setup | len ratio | word F1 | same purpose | props pass rules | GT pass rules | new / partial / dup |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| epZ0lbXkgWA | 6 | 4 / 0 / 0 | 7 | 5/6 (83%) | 5/6 (83%) | 5/6 (83%) | w09 | 1/4 (25%) | 0/0 | 0/0 | 6.396 / 4.778 | 5/5 (100%) | 0/5 | 1.306 | 0.866 | 5/5 (100%) | 7/7 (100%) | 5/6 (83%) | 1 / 0 / 0 |
| j14Om1SaFYE | 8 | 0 / 0 / 0 | 8 | 7/8 (88%) | 7/8 (88%) | 4/8 (50%) | w09 | 0/0 | 0/0 | 0/0 | 7.82 / 2.669 | 4/7 (57%) | 3/7 | 0.933 | 0.926 | 6/7 (86%) | 8/8 (100%) | 8/8 (100%) | 1 / 0 / 0 |

Overall: recall 12/14 (86%), recall@k=GT 12/14 (86%), recall@5 per episode summed 9/14 (64%); acceptable hits 1 (of 4 maybe clips), founder_no hits 0 (of 0 no clips). Small sample: every number above rests on 14 founder-yes GT clips from 2 episode(s).

## epZ0lbXkgWA

Shots: `runs\video\epZ0lbXkgWA\shots.json`. Opening zone: lead 0:00.0-0:07.3, cards 0:07.3-0:17.3; story starts 0:17.8. Sponsor: 0:57.3-1:53.3 (amazon, app and website, available గా, check చేయండి, #ad brand). Credits: heuristic (last word 1487.3s + 10s hold allowance; speechless tail 121s).

### GT clips (founder: yes)

| GT | purpose | founder payoff/overall | src | len | match rank | cov / IoU | dstart | dend | payoff / hold | drops setup | len ratio | word P/R | cuts P/GT | same purpose | cold P/GT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w02 | standalone | 4/4 | 16:42.0-17:50.1 | 68s | 4 | 1.0 / 0.999 | -0.1 | +0.0 | yes / yes | no | 1.001 | 1.0/1.0 | 0/0 | yes | no/no |
| w03 | standalone | 5/4 | 20:05.9-21:55.0 | 59s | 5 | 0.966 / 0.515 | -0.0 | +1.9 | yes / yes | no | 1.853 | 0.542/1.0 | 0/1 | yes | yes/no |
| w06 | standalone | 3/4 | 14:37.9-15:58.8 | 78s | 3 | 0.992 / 0.748 | -0.1 | +22.0 | yes / yes | no | 1.239 | 0.719/1.0 | 0/1 | yes | yes/no |
| w01 | standalone | 4/4 | 23:41.7-24:49.9 | 68s | 1 | 1.0 / 0.682 | -31.8 | +0.0 | yes / yes | no | 1.466 | 0.652/1.0 | 0/0 | yes | no/no |
| w09 | standalone | 4/4 | 12:22.9-13:13.2 | 50s | MISSED | no overlap | | | | | | | | | |
| w07 | standalone | 4/4 | 11:09.9-12:07.2 | 57s | 2 | 0.967 / 0.968 | -0.1 | +0.0 | yes / yes | no | 0.971 | 1.0/1.0 | 0/0 | yes | yes/no |

### Founder maybe / no / unrated clips (not GT: a hit is `acceptable` / `founder_no` / `unrated`, a miss is not a miss)

| clip | verdict | founder payoff/overall | purpose | src | len | hit by rank | cov / IoU | excerpt |
|---|---|---|---|---|---|---|---|---|
| w10 | maybe | 3/3 | standalone | 18:00.9-18:47.4 | 42s | - | - | మొన్న చేసిన పనికి, ఇప్పుడు ఆంటీకి కనిపిస్తే ఏం చేస్తదో ఏంటో. police కి call ... |
| w15 | maybe | 3/3 | standalone | 13:42.0-14:27.4 | 45s | - | - | ఏంటి ఆ అబ్బాయి matter నాకు తెలుసు, నువ్వు all of a sudden ఎందుకు ... |
| w14 | maybe | 3/3 | standalone | 20:35.0-21:12.8 | 38s | - | - | అప్పుడప్పుడు మా హన్స్ని కొంచెం గరుగ్గా మాట్లాడుతుంది. ఏం పట్టించుకోకు బాబు. ఉమ్ పర్లేదండి నాకు ... |
| w13 | maybe | 3/3 | standalone | 5:18.3-8:19.4 | 67s | 6 | 0.574 / 0.295 | నీకు already చెప్పాను కదా. I am not interested in you and this ... |

Missed yes clips (founder would post, no proposal matched):
- w09 (standalone, 12:22.9-13:13.2, founder 4/4): ఇంట్లో bottle ఖాళీ అవుతుంటే ఎవరా అనుకున్నా… ఆ దొంగ అమ్మే! | ఏదో బాధలన్నీ దేవుడు నీకే పెట్టినట్టు పెద్ద buildup ఇవ్వకు. మాకూ బాధలుంటాయి. ఆలోచించకుండా పడుకోవాలని ... | no overlapping proposal

### Proposals

| rank | tier | status | src | len | purpose | cold | lead-in | cuts | hold | fails | warns | excerpt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | recommended | matched (w01) | 23:09.9-24:49.9 | 100s | standalone | no | 0.3 | 0 | 2.612 | - | - | మీరు ఎందుకు వేరే వాడిని మీ life లోకి రానివ్వట్లేదో నాకు తెలుసు ఏం తెలుసు ... |
| 2 | recommended | matched (w07) | 11:09.8-12:07.3 | 56s | standalone | yes | 0.12 | 0 | 3.439 | - | - | ఏయ్ ఇప్పుడు నేను నీకు అమ్మనని ఎవరు చెప్పారు ఏంటమ్మా అలా చూస్తున్నావ్ ఏం లేదు ... |
| 3 | recommended | matched (w06) | 14:37.9-16:20.7 | 97s | standalone | yes | 0.12 | 0 | 2.0 | - | - | నేను పోతే ఒంటరిగా ఉండగలవా ఒకటి చెప్పు, చచ్చేంత వరకు నేను ఇలానే ఉంటాను అంటున్నావు ... |
| 4 | recommended | matched (w02) | 16:41.9-17:50.1 | 68s | standalone | no | 0.3 | 0 | 5.558 | - | - | తోడు లేని జీవితం waste అన్నట్టు అన్నావు కదా, మరి నీక తోడు అవసరం లేదా ... |
| 5 | worth_a_look | matched (w03) | 20:05.9-21:56.9 | 109s | standalone | yes | 0.12 | 0 | 2.366 | - | - | ఒక్కసారి హన్స్నితో పెళ్లయితే అందరం కలిసి తాగొచ్చు. ఆ రోజు నైట్ జరిగిన దానికి I ... |
| 6 | worth_a_look | acceptable (w13) founder 3/3 | 5:03.0-6:28.0 | 85s | standalone | no | 0.3 | 0 | 0.944 | - | - | ఏమైంది అంశ? ఎందుకు ఒక వారం నుంచి నరకం చూపిస్తున్నావ్? phone లో block చేశావ్. ... |
| 7 | below_threshold | new | 0:17.5-0:57.3 | 33s | promo | no | 0.3 | 1 | 4.541 | - | - | నా మాట విని వదిలేయ్ bro తనని. అసలు తన life లో ఏం జరిగిందని ... |

Founder maybe / no hits (acceptable = on a `maybe` moment; founder_no = on a moment the founder would NOT post):
- rank 6 [acceptable w13, founder 3/3] 5:03.0-6:28.0 85s standalone: Sudden గా breakup చెప్తే ఎలా? | ఏమైంది అంశ? ఎందుకు ఒక వారం నుంచి నరకం చూపిస్తున్నావ్? phone లో block చేశావ్. ...

New moments (outside the GT, NOT wrong; need the founder's rating):
- rank 7 [new] 0:17.5-0:57.3 33s promo: తన past లో ఏం జరిగింది? | నా మాట విని వదిలేయ్ bro తనని. అసలు తన life లో ఏం జరిగిందని ...

GT sanity (rule checks on the approved GT clips): 5/6 (83%) pass.
- w03 reaction_hold [fail]: ends 0.47s after the last word, not on a shot change

## j14Om1SaFYE

Shots: `runs\video\j14Om1SaFYE\shots.json`. Opening zone: lead 0:00.0-0:23.6, isolated 0:23.6-0:33.4 (uncertain); story starts 0:33.9. Recap check vs QWIvgoTQGKk. Sponsor: none detected. Credits: heuristic (last word 1096.4s + 10s hold allowance; speechless tail 71s).

### GT clips (founder: yes)

| GT | purpose | founder payoff/overall | src | len | match rank | cov / IoU | dstart | dend | payoff / hold | drops setup | len ratio | word P/R | cuts P/GT | same purpose | cold P/GT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w01 | standalone | - | 3:31.1-5:14.1 | 62s | 2 | 0.943 / 0.825 | -0.1 | +2.6 | yes / yes | no | 1.096 | 1.0/0.948 | 1/2 | yes | no/no |
| w02 | standalone | - | 12:45.6-13:47.5 | 62s | 3 | 0.946 / 0.943 | +3.5 | +0.0 | yes / yes | yes: 8 words | 0.944 | 1.0/0.941 | 0/0 | yes | no/no |
| w03 | standalone | - | 7:18.2-9:47.6 | 84s | 1 | 0.749 / 0.494 | +33.9 | -1.5 | yes / no | yes: 19 words | 0.834 | 0.91/0.762 | 1/3 | yes | no/no |
| w04 | standalone | - | 14:37.0-15:54.6 | 73s | 8 | 0.905 / 0.837 | -2.3 | +0.0 | yes / yes | no | 1.003 | 0.961/1.0 | 1/1 | yes | no/no |
| w06 | promo | - | 11:48.9-12:38.2 | 49s | 7 | 0.639 / 0.58 | +14.9 | -5.8 | yes / no | yes: 33 words | 0.58 | 1.0/0.593 | 0/0 | no | no/no |
| w07 | standalone | - | 5:28.0-5:58.0 | 30s | 6 | 1.0 / 0.876 | +0.0 | +4.2 | yes / yes | no | 1.141 | 1.0/1.0 | 0/0 | yes | no/no |
| w08 | promo | - | 17:06.8-18:24.0 | 67s | 5 | 0.955 / 0.805 | -0.1 | -4.5 | yes / no | no | 0.931 | 0.973/1.0 | 1/2 | yes | yes/no |
| w09 | standalone | - | 9:57.9-11:17.6 | 72s | MISSED | no overlap | | | | | | | | | |

Missed yes clips (founder would post, no proposal matched):
- w09 (standalone, 9:57.9-11:17.6, founder -): Hostel food బాధ ప్రతి అబ్బాయికీ తెలుసు | food made with love | ఏరా ఆ అమ్మాయి అంత నచ్చిందా? నీలా నువ్వే మురిసిపోతున్నావ్. అమ్మాయి చాలా బాగుందిరా. కానీ ... | no overlapping proposal

Setup dropped (proposal starts later and loses GT-kept lines):
- w02 vs rank 3: starts +3.5s, loses 8 words: వాడు marriage గురించి ఎలా explain చేసాడో తెలుసా? ఆ
- w03 vs rank 1: starts +33.9s, loses 19 words: మీకు ఇది ఎన్నో పెళ్లిచూపులు మొదటి పెళ్లిచూపులు కానీ నాకిది ఐదో పెళ్లిచూపులు ...
- w06 vs rank 7: starts +14.9s, loses 33 words: నేను ఒక విషయం చెప్పాలి మీకు అంతకన్నా ముందు నేను మీకు ఒక ...

### Proposals

| rank | tier | status | src | len | purpose | cold | lead-in | cuts | hold | fails | warns | excerpt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | recommended | matched (w03) | 7:52.1-9:46.1 | 70s | standalone | no | 0.3 | 1 | 5.008 | - | - | నేను పొట్టిగా ఉంటాను కదా ఎవ్వరూ ఇష్టపడరు. అందుకే అందరూ reject చేశారు. మీకు ఇలాంటివి ... |
| 2 | recommended | matched (w01) | 3:31.0-5:16.7 | 68s | standalone | no | 0.3 | 1 | 3.182 | - | - | మీకు వంట వచ్చా? చచ్చింది గొర్ర. ఆహా! ఆ వచ్చు బాబు. ఎందుకు రాదు. వచ్చే ... |
| 3 | recommended | matched (w02) | 12:49.1-13:47.5 | 58s | standalone | no | 0.3 | 0 | 0.749 | - | - | ఈ రోజుల్లో marriage అనేది full meals లాంటిది. starting లో hot and exciting ... |
| 4 | worth_a_look | new | 1:24.7-2:24.2 | 56s | standalone | no | 0.3 | 1 | 0.403 | - | - | అమృత గారు, ఒకసారి నించోగలుగుతారా please. please ఒక్కసారి నించోండి. అమృత గారు I am ... |
| 5 | worth_a_look | matched (w08) | 17:06.7-18:19.5 | 63s | promo | yes | 0.12 | 1 | 3.091 | - | - | ఎవరైనా అబద్ధాలాడినా, మోసం చేసినా అస్సలు తీసుకోలేను. అత్తమ్మ మీరు మీ కూతుర్ని miss అవుతున్నారంటే ... |
| 6 | worth_a_look | matched (w07) | 5:28.0-6:02.2 | 34s | standalone | no | 0.098 | 0 | 5.003 | - | - | పోసేయ్, పోసేయ్, పోసేయ్. అది నూనె అనుకుంటున్నావా, బండిలో పోసే petrol అనుకుంటున్నావా, అంత పోసావు. ... |
| 7 | worth_a_look | matched (w06) | 12:03.8-12:32.4 | 29s | standalone | no | 0.3 | 0 | 2.664 | - | - | నాకు వంట వచ్చే wife మాత్రమే కావాలనుకుంటున్నానంటే నా wife వంట గదికి మాత్రమే పరిమితమైపోవాలని ... |
| 8 | worth_a_look | matched (w04) | 14:34.7-15:54.6 | 74s | standalone | no | 0.298 | 1 | 3.614 | - | - | అయ్యో అయినా మిమ్మల్ని అడుగుతున్నా ఏంటి? మీరు బయట food తినరు కదా. మీకు అన్ని ... |

New moments (outside the GT, NOT wrong; need the founder's rating):
- rank 4 [new] 1:24.7-2:24.2 56s standalone: పెళ్లిచూపుల్లో అమ్మాయిల బాధ | అమృత గారు, ఒకసారి నించోగలుగుతారా please. please ఒక్కసారి నించోండి. అమృత గారు I am ...

GT sanity (rule checks on the approved GT clips): 8/8 (100%) pass.
- w03 mid_line [warn]: seg0 trims line at 'మీకు' | 'ఆ'
