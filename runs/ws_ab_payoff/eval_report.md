# Web-series eval: `D:\shotvi\runs\ws_ab_payoff`

GT = clips the founder would post (`founder_would_post: yes`). Matching: a proposal matches a GT clip if it covers >= 50% of the GT's spoken core and the source-span IoU is >= 0.2; proposals are taken in rank order, each matching at most one GT clip. Leftover proposals are matched the same way against the founder's `maybe` clips (`acceptable`: not a miss, not a false positive), `no` clips (`founder_no`: a negative hit) and the unrated `held` (hidden by the judge gate, never shown to the founder) / `pending` (not yet rated) clips (`unrated`: neither a hit nor a miss; such clips are never counted as missed). Unmatched proposals are not errors: `new` and `partial` ones need the founder's rating. Founder payoff/overall columns are the founder's scores for the GT cut of that moment (informational), not a rating of the proposal.

## Headline: precision at the top

Of the proposals shown, how many land on a founder-yes clip (hit), a maybe (acceptable), a no (founder_no), or nothing rated (unrated: new / partial / on a held or pending clip; dup = a second proposal on a moment already matched). Precision = hits / rated shown. `founder_no shown` target is 0. Count: top-tier shown vs founder-yes count; over-proposing = shown > 2x yes, under-proposing = shown < yes.

| video | founder yes / maybe / no / unrated | shown rec / look / below | top tier | hit / acc / no / unrated / dup | precision (top) | founder_no shown (top / all) | @3 | @5 | all | missed yes | recall yes | count |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| epZ0lbXkgWA | 6 / 4 / 0 / 0 | 5 / 2 / 0 | recommended (5) | 5 / 0 / 0 / 0 / 0 | 5/5 (100%) | 0 / 0 | 3/3 (100%) | 5/5 (100%) | 5/6 (83%) | w09 | 5/6 (83%) | under-proposing |
| j14Om1SaFYE | 8 / 0 / 0 / 0 | 2 / 4 / 0 | recommended (2) | 2 / 0 / 0 / 0 / 0 | 2/2 (100%) | 0 / 0 | 3/3 (100%) | 4/4 (100%) | 5/5 (100%) | w04, w06, w08 | 5/8 (62%) | under-proposing |
| QWIvgoTQGKk | 12 / 0 / 0 / 0 | 4 / 4 / 0 | recommended (4) | 3 / 0 / 0 / 1 / 0 | 3/3 (100%) | 0 / 0 | 3/3 (100%) | 4/4 (100%) | 6/6 (100%) | w02, w04, w07, w10, w11, w12 | 6/12 (50%) | under-proposing |

Overall (pooled over 3 episode(s)): top tier 10/10 (100%) hits of rated shown (11 shown, 1 unrated, 0 dup); all shown 16/17 (94%) (21 shown); founder_no shown 0 (0 in the top tier); missed yes 10 of 26; count flags: under-proposing x3. Small sample: 26 founder-yes clips, 10 rated top-tier proposals.

## Secondary: recall and boundary metrics

| video | GT yes | maybe / no / unrated | props | recall (any) | recall@k=GT | recall@5 | missed | acceptable | founder_no | unrated hits | mean abs dstart / dend (s) | payoff+hold kept | drops setup | len ratio | word F1 | same purpose | props pass rules | GT pass rules | new / partial / dup |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| epZ0lbXkgWA | 6 | 4 / 0 / 0 | 7 | 5/6 (83%) | 5/6 (83%) | 5/6 (83%) | w09 | 1/4 (25%) | 0/0 | 0/0 | 18.602 / 15.556 | 3/5 (60%) | 3/5 | 1.182 | 0.79 | 4/5 (80%) | 7/7 (100%) | 5/6 (83%) | 0 / 1 / 0 |
| j14Om1SaFYE | 8 | 0 / 0 / 0 | 6 | 5/8 (62%) | 5/8 (62%) | 4/8 (50%) | w04, w06, w08 | 0/0 | 0/0 | 0/0 | 24.202 / 4.4 | 3/5 (60%) | 5/5 | 0.835 | 0.866 | 5/5 (100%) | 5/6 (83%) | 8/8 (100%) | 0 / 1 / 0 |
| QWIvgoTQGKk | 12 | 0 / 0 / 0 | 8 | 6/12 (50%) | 6/12 (50%) | 4/12 (33%) | w02, w04, w07, w10, w11, w12 | 0/0 | 0/0 | 0/0 | 6.665 / 4.638 | 1/6 (17%) | 2/6 | 0.825 | 0.91 | 6/6 (100%) | 6/8 (75%) | 10/12 (83%) | 0 / 2 / 0 |

Overall: recall 16/26 (62%), recall@k=GT 16/26 (62%), recall@5 per episode summed 13/26 (50%); acceptable hits 1 (of 4 maybe clips), founder_no hits 0 (of 0 no clips). Small sample: every number above rests on 26 founder-yes GT clips from 3 episode(s).

## epZ0lbXkgWA

Shots: `runs\video\epZ0lbXkgWA\shots.json`. Opening zone: lead 0:00.0-0:07.3, cards 0:07.3-0:17.3; story starts 0:17.8. Sponsor: 0:57.3-1:53.3 (amazon, app and website, available గా, check చేయండి, #ad brand). Credits: heuristic (last word 1487.3s + 10s hold allowance; speechless tail 121s).

### GT clips (founder: yes)

| GT | purpose | founder payoff/overall | src | len | match rank | cov / IoU | dstart | dend | payoff / hold | drops setup | len ratio | word P/R | cuts P/GT | same purpose | cold P/GT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w02 | standalone | 4/4 | 16:42.0-17:50.1 | 68s | 3 | 1.0 / 0.999 | -0.1 | +0.0 | yes / yes | no | 1.001 | 1.0/1.0 | 0/0 | yes | no/no |
| w03 | standalone | 5/4 | 20:05.9-21:55.0 | 59s | 4 | 0.552 / 0.402 | +54.5 | +1.9 | yes / yes | yes: 53 words | 0.742 | 0.649/0.411 | 1/1 | yes | no/no |
| w06 | standalone | 3/4 | 14:37.9-15:58.8 | 78s | 5 | 0.898 / 0.855 | +2.5 | -6.8 | no / no | yes: 2 words | 0.915 | 1.0/0.866 | 0/1 | yes | no/no |
| w01 | standalone | 4/4 | 23:41.7-24:49.9 | 68s | 1 | 0.875 / 0.574 | -31.8 | -10.8 | no / no | no | 1.309 | 0.645/0.971 | 0/0 | no | no/no |
| w09 | standalone | 4/4 | 12:22.9-13:13.2 | 50s | MISSED | nearest: rank 2 cov 0.872 IoU 0.359 (matched to another GT) | | | | | | | | | |
| w07 | standalone | 4/4 | 11:09.9-12:07.2 | 57s | 2 | 0.925 / 0.459 | +4.2 | +58.4 | yes / yes | yes: 5 words | 1.944 | 0.616/0.939 | 0/0 | yes | no/no |

### Founder maybe / no / unrated clips (not GT: a hit is `acceptable` / `founder_no` / `unrated`, a miss is not a miss)

| clip | verdict | founder payoff/overall | purpose | src | len | hit by rank | cov / IoU | excerpt |
|---|---|---|---|---|---|---|---|---|
| w10 | maybe | 3/3 | standalone | 18:00.9-18:47.4 | 42s | - | - | మొన్న చేసిన పనికి, ఇప్పుడు ఆంటీకి కనిపిస్తే ఏం చేస్తదో ఏంటో. police కి call ... |
| w15 | maybe | 3/3 | standalone | 13:42.0-14:27.4 | 45s | - | - | ఏంటి ఆ అబ్బాయి matter నాకు తెలుసు, నువ్వు all of a sudden ఎందుకు ... |
| w14 | maybe | 3/3 | standalone | 20:35.0-21:12.8 | 38s | - | - | అప్పుడప్పుడు మా హన్స్ని కొంచెం గరుగ్గా మాట్లాడుతుంది. ఏం పట్టించుకోకు బాబు. ఉమ్ పర్లేదండి నాకు ... |
| w13 | maybe | 3/3 | standalone | 5:18.3-8:19.4 | 67s | 6 | 0.574 / 0.275 | నీకు already చెప్పాను కదా. I am not interested in you and this ... |

Missed yes clips (founder would post, no proposal matched):
- w09 (standalone, 12:22.9-13:13.2, founder 4/4): ఇంట్లో bottle ఖాళీ అవుతుంటే ఎవరా అనుకున్నా… ఆ దొంగ అమ్మే! | ఏదో బాధలన్నీ దేవుడు నీకే పెట్టినట్టు పెద్ద buildup ఇవ్వకు. మాకూ బాధలుంటాయి. ఆలోచించకుండా పడుకోవాలని ... | nearest: rank 2 cov 0.872 IoU 0.359 (matched to another GT)

Setup dropped (proposal starts later and loses GT-kept lines):
- w03 vs rank 4: starts +54.5s, loses 53 words: ఆ రోజు నైట్ జరిగిన దానికి I am really sorry అండి. ...
- w06 vs rank 5: starts +2.5s, loses 2 words: ఒకటి చెప్పు,
- w07 vs rank 2: starts +4.2s, loses 5 words: ఏంటమ్మా అలా చూస్తున్నావ్ ఏం లేదు

### Proposals

| rank | tier | status | src | len | purpose | cold | lead-in | cuts | hold | fails | warns | excerpt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | recommended | matched (w01) | 23:09.9-24:39.1 | 89s | promo | no | 0.3 | 0 | 4.351 | - | - | మీరు ఎందుకు వేరే వాడిని మీ life లోకి రానివ్వట్లేదో నాకు తెలుసు ఏం తెలుసు ... |
| 2 | recommended | matched (w07) | 11:14.1-13:05.6 | 111s | standalone | no | 0.3 | 0 | 2.385 | - | - | నేను ఇప్పుడు wine తాగుదాము అంటే ఏమంటావా అని ఏం మాట్లాడుతున్నావమ్మా నాకన్నీ తెలుసులేవే నువ్వు ... |
| 3 | recommended | matched (w02) | 16:41.9-17:50.1 | 68s | standalone | no | 0.3 | 0 | 5.558 | - | - | తోడు లేని జీవితం waste అన్నట్టు అన్నావు కదా, మరి నీక తోడు అవసరం లేదా ... |
| 4 | recommended | matched (w03) | 21:00.4-21:56.9 | 44s | standalone | no | 0.3 | 1 | 2.366 | - | - | మా హన్స్ని పైకే గరుగ్గా ఉంటుంది కానీ లోపల చాలా సున్నితం బాబు. అందుకేనండి వదులుకోలేకపోతున్నాను. ... |
| 5 | recommended | matched (w06) | 14:40.4-15:52.0 | 72s | standalone | no | 0.3 | 0 | 2.72 | - | - | చచ్చేంత వరకు నేను ఇలానే ఉంటాను అంటున్నావు కదా. నేను రేపో మాపో పోతే అమ్మా ... |
| 6 | worth_a_look | acceptable (w13) founder 3/3 | 5:03.0-6:36.6 | 94s | standalone | no | 0.3 | 0 | 1.937 | - | - | ఏమైంది అంశ? ఎందుకు ఒక వారం నుంచి నరకం చూపిస్తున్నావ్? phone లో block చేశావ్. ... |
| 7 | worth_a_look | partial (w03) | 19:58.0-20:33.0 | 35s | standalone | no | 0.3 | 0 | 3.49 | - | - | మీతో మాట్లాడదామనే వచ్చాను. ఆ రోజు నైట్ జరిగిన దానికి ఆహ్ దాని గురించి ఎప్పుడో ... |

Founder maybe / no hits (acceptable = on a `maybe` moment; founder_no = on a moment the founder would NOT post):
- rank 6 [acceptable w13, founder 3/3] 5:03.0-6:36.6 94s standalone: అసలు ఏం జరిగిందో చెప్పకుండా breakup ఆ? | ఏమైంది అంశ? ఎందుకు ఒక వారం నుంచి నరకం చూపిస్తున్నావ్? phone లో block చేశావ్. ...

New moments (outside the GT, NOT wrong; need the founder's rating):
- rank 7 [partial w03] 19:58.0-20:33.0 35s standalone: పెళ్లయ్యాక అందరం కలిసి తాగుదాం బాబు | మీతో మాట్లాడదామనే వచ్చాను. ఆ రోజు నైట్ జరిగిన దానికి ఆహ్ దాని గురించి ఎప్పుడో ...

GT sanity (rule checks on the approved GT clips): 5/6 (83%) pass.
- w03 reaction_hold [fail]: ends 0.47s after the last word, not on a shot change

## j14Om1SaFYE

Shots: `runs\video\j14Om1SaFYE\shots.json`. Opening zone: lead 0:00.0-0:23.6, isolated 0:23.6-0:33.4 (uncertain); story starts 0:33.9. Recap check vs QWIvgoTQGKk. Sponsor: none detected. Credits: heuristic (last word 1096.4s + 10s hold allowance; speechless tail 71s).

### GT clips (founder: yes)

| GT | purpose | founder payoff/overall | src | len | match rank | cov / IoU | dstart | dend | payoff / hold | drops setup | len ratio | word P/R | cuts P/GT | same purpose | cold P/GT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w01 | standalone | - | 3:31.1-5:14.1 | 62s | 3 | 0.768 / 0.704 | +55.1 | -4.3 | no / no | yes: 16 words | 0.704 | 1.0/0.771 | 0/2 | yes | no/no |
| w02 | standalone | - | 12:45.6-13:47.5 | 62s | 2 | 0.946 / 0.943 | +3.5 | +0.0 | yes / yes | yes: 8 words | 0.944 | 1.0/0.941 | 0/0 | yes | no/no |
| w03 | standalone | - | 7:18.2-9:47.6 | 84s | 1 | 0.633 / 0.379 | +33.9 | -13.4 | no / no | yes: 19 words | 0.693 | 0.898/0.662 | 1/3 | yes | no/no |
| w04 | standalone | - | 14:37.0-15:54.6 | 73s | MISSED | nearest: rank 4 cov 0.419 IoU 0.436 | | | | | | | | | |
| w06 | promo | - | 11:48.9-12:38.2 | 49s | MISSED | no overlap | | | | | | | | | |
| w07 | standalone | - | 5:28.0-5:58.0 | 30s | 5 | 0.966 / 0.845 | +1.1 | +4.2 | yes / yes | yes: 3 words | 1.105 | 1.0/0.94 | 0/0 | yes | no/no |
| w08 | promo | - | 17:06.8-18:24.0 | 67s | MISSED | no overlap | | | | | | | | | |
| w09 | standalone | - | 9:57.9-11:17.6 | 72s | 6 | 0.603 / 0.557 | +27.4 | +0.0 | yes / yes | yes: 42 words | 0.728 | 0.891/0.661 | 0/1 | yes | no/no |

Missed yes clips (founder would post, no proposal matched):
- w04 (standalone, 14:37.0-15:54.6, founder -): గోంగూర అడిగితే గోరింటాకు అంటుంది! | పిండి family | మీరు బయట food తినరు కదా. మీకు అన్ని dishes వచ్చినప్పుడు. ఆ మీ favourite ... | nearest: rank 4 cov 0.419 IoU 0.436
- w06 (promo, 11:48.9-12:38.2, founder -): వంట రాదని చెప్పేలోపే... 'Let's make our marriage life tasty' | నేను ఒక విషయం చెప్పాలి మీకు అంతకన్నా ముందు నేను మీకు ఒక విషయం చెప్పాలి. ... | no overlapping proposal
- w08 (promo, 17:06.8-18:24.0, founder -): అబద్ధాలు చెప్తే అస్సలు తీసుకోలేను! | కొత్త అల్లుడి warning | అత్తమ్మ మీరు మీ కూతుర్ని miss అవుతున్నారంటే చెప్పండి. ఇక్కడే వదిలేసి వెళ్తాను. అలా ఏం ... | no overlapping proposal

Setup dropped (proposal starts later and loses GT-kept lines):
- w01 vs rank 3: starts +55.1s, loses 16 words: మీకు వంట వచ్చా? చచ్చింది గొర్ర. ఆహా! ఆ వచ్చు బాబు. ఎందుకు ...
- w02 vs rank 2: starts +3.5s, loses 8 words: వాడు marriage గురించి ఎలా explain చేసాడో తెలుసా? ఆ
- w03 vs rank 1: starts +33.9s, loses 19 words: మీకు ఇది ఎన్నో పెళ్లిచూపులు మొదటి పెళ్లిచూపులు కానీ నాకిది ఐదో పెళ్లిచూపులు ...
- w07 vs rank 5: starts +1.1s, loses 3 words: పోసేయ్, పోసేయ్, పోసేయ్.
- w09 vs rank 6: starts +27.4s, loses 42 words: ఏరా ఆ అమ్మాయి అంత నచ్చిందా? నీలా నువ్వే మురిసిపోతున్నావ్. అమ్మాయి చాలా ...

### Proposals

| rank | tier | status | src | len | purpose | cold | lead-in | cuts | hold | fails | warns | excerpt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | recommended | matched (w03) | 7:52.1-9:34.2 | 59s | standalone | no | 0.3 | 1 | 2.372 | - | - | నేను పొట్టిగా ఉంటాను కదా ఎవ్వరూ ఇష్టపడరు. అందుకే అందరూ reject చేశారు. మీకు ఇలాంటివి ... |
| 2 | recommended | matched (w02) | 12:49.1-13:47.5 | 58s | standalone | no | 0.3 | 0 | 0.749 | - | - | ఈ రోజుల్లో marriage అనేది full meals లాంటిది. starting లో hot and exciting ... |
| 3 | worth_a_look | matched (w01) | 4:26.2-5:09.8 | 44s | standalone | no | 0.172 | 0 | 0.67 | reaction_hold | - | వచ్చే wife కి వంట వస్తే చాలనుకునే అబ్బాయిల్లో నేను ఒకడిని. అయ్యో! ఈ sweets ... |
| 4 | worth_a_look | partial (w04) | 15:22.7-15:54.6 | 32s | standalone | no | 0.3 | 0 | 3.614 | - | - | మ్ మీకు మిర్చి బజ్జి వేయడం వచ్చా? అయ్యో super. నేను చేసే మిర్చి బజ్జి ... |
| 5 | worth_a_look | matched (w07) | 5:29.1-6:02.2 | 33s | standalone | no | 0.055 | 0 | 5.003 | - | - | అది నూనె అనుకుంటున్నావా, బండిలో పోసే petrol అనుకుంటున్నావా, అంత పోసావు. ఎవరైనా చపాతీ మీద ... |
| 6 | worth_a_look | matched (w09) | 10:25.3-11:17.6 | 52s | standalone | no | 0.16 | 0 | 2.97 | - | - | నువ్వేమన్నా ఆ అమ్మాయితో curry point పెట్టేస్తున్నావా? పెళ్లిచూపులకి వెళ్లి చూడాల్సింది ఇదా? మరి ఏం ... |

New moments (outside the GT, NOT wrong; need the founder's rating):
- rank 4 [partial w04] 15:22.7-15:54.6 32s standalone: పిండి family అంటే ఇదేనేమో! | మ్ మీకు మిర్చి బజ్జి వేయడం వచ్చా? అయ్యో super. నేను చేసే మిర్చి బజ్జి ...

Rule failures (proposals):
- rank 3 reaction_hold: ends 0.67s after the last word, not on a shot change

GT sanity (rule checks on the approved GT clips): 8/8 (100%) pass.
- w03 mid_line [warn]: seg0 trims line at 'మీకు' | 'ఆ'

## QWIvgoTQGKk

Shots: `runs\video\QWIvgoTQGKk\shots.json`. Opening zone: lead 0:00.0-2:13.6; story starts 2:14.1. Recap check vs j14Om1SaFYE. Sponsor: none detected. Credits: heuristic (evenly timed shot cards from 1257.5s; speechless tail 59s).

### GT clips (founder: yes)

| GT | purpose | founder payoff/overall | src | len | match rank | cov / IoU | dstart | dend | payoff / hold | drops setup | len ratio | word P/R | cuts P/GT | same purpose | cold P/GT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w01 | standalone | - | 15:48.6-17:01.0 | 72s | 7 | 0.982 / 0.932 | -0.0 | -4.9 | no / no | no | 0.932 | 1.0/0.964 | 0/0 | yes | no/no |
| w02 | promo | - | 18:50.5-20:57.5 | 89s | MISSED | nearest: rank 4 cov 0.348 IoU 0.315 | | | | | | | | | |
| w03 | standalone | - | 10:51.1-11:09.5 | 18s | 1 | 1.0 / 0.837 | -0.0 | -3.0 | yes / no | no | 0.84 | 1.0/1.0 | 0/0 | yes | no/no |
| w04 | promo | - | 6:05.9-7:21.1 | 74s | MISSED | nearest: rank 8 cov 0.401 IoU 0.381 | | | | | | | | | |
| w05 | standalone | - | 3:41.4-4:20.8 | 39s | 5 | 0.716 / 0.711 | +0.0 | -10.3 | no / no | no | 0.711 | 1.0/0.73 | 0/0 | yes | yes/no |
| w06 | standalone | - | 11:55.8-13:22.5 | 73s | 3 | 1.0 / 0.885 | -5.6 | -3.4 | yes / no | no | 1.029 | 0.91/1.0 | 1/1 | yes | no/no |
| w07 | standalone | - | 2:23.2-3:16.5 | 53s | MISSED | no overlap | | | | | | | | | |
| w08 | standalone | - | 13:28.0-14:42.5 | 74s | 6 | 0.554 / 0.58 | +31.3 | -0.0 | yes / yes | yes: 35 words | 0.58 | 1.0/0.607 | 0/0 | yes | no/no |
| w09 | standalone | - | 7:25.7-8:32.2 | 66s | 2 | 0.866 / 0.859 | +3.1 | -6.2 | no / no | yes: 5 words | 0.859 | 1.0/0.861 | 0/0 | yes | no/no |
| w10 | standalone | - | 4:49.5-5:57.5 | 68s | MISSED | no overlap | | | | | | | | | |
| w11 | standalone | - | 17:27.3-18:43.2 | 61s | MISSED | no overlap | | | | | | | | | |
| w12 | standalone | - | 9:07.0-9:49.5 | 42s | MISSED | no overlap | | | | | | | | | |

Missed yes clips (founder would post, no proposal matched):
- w02 (promo, 18:50.5-20:57.5, founder -): మిర్చి బజ్జీలు నువ్వే వెయ్, నేను చూడాలి! Red handed గా దొరికిపోయిందా? | నీకు వచ్చిన పెళ్ళాం కనీసం gas stove off చేయడం on చేయడం కూడా రాదన్నావ్ ... | nearest: rank 4 cov 0.348 IoU 0.315
- w04 (promo, 6:05.9-7:21.1, founder -): అబద్ధం అంటేనే fire అయిపోతాడు… అమృత పరిస్థితి ఏంటి? | అబద్ధాలు చెప్పి పెళ్లి చేసుకోవడం correct ఏనా? ఆ మావోడు food విషయంలో చాలా particular ... | nearest: rank 8 cov 0.401 IoU 0.381
- w07 (standalone, 2:23.2-3:16.5, founder -): నాకు చాయ్ పెట్టడానికే కదా లేచావ్? First day నే దొరికిపోయిందా! | first day నే దొరికిపోయేలా ఉన్నావు కదా. నేనెందుకింత తొందరగా లేచానో నాకు తెలియదు గానీ, ... | no overlapping proposal
- w10 (standalone, 4:49.5-5:57.5, founder -): Dinner ఏ కాదు carriage కూడా తనే పెడుతుంది! అమ్మ హామీలు | నాకోసం ఏం వండవు. ఆ ఆ తోటకూర పప్పు ఆవకాయ ఊ ఆ ఇది కూడా ... | no overlapping proposal
- w11 (standalone, 17:27.3-18:43.2, founder -): First easy వంట try చేద్దాం… popcorn I am coming! | అమ్మే! ప్రతిసారి వంటల కోసం అమ్మనేమడుగుతాం. మనం కూడా try చేద్దాం. First easy with ... | no overlapping proposal
- w12 (standalone, 9:07.0-9:49.5, founder -): Nail polish నేను వెయ్యనా? | Husband goals | nail polish వేస్తున్నావా? ఉమ్ సరే నేను వెయ్యనా? వద్దు వద్దు పర్లేదు వద్దు. అంత ... | no overlapping proposal

Setup dropped (proposal starts later and loses GT-kept lines):
- w08 vs rank 6: starts +31.3s, loses 35 words: ఆహ్, రేపు morning tiffin and lunch ఏం చెయ్యాలి? ఇప్పుడెందుకు అడుగుతున్నావ్? ...
- w09 vs rank 2: starts +3.1s, loses 5 words: ఏయ్ ఏంటి matter? Matter ఏంటి?

### Proposals

| rank | tier | status | src | len | purpose | cold | lead-in | cuts | hold | fails | warns | excerpt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | recommended | matched (w03) | 10:51.1-11:06.5 | 15s | standalone | no | 0.3 | 0 | 1.137 | - | - | అమ్మ చేతి వంటలా ఉంది. హా మా అమ్మ చెయ్యలేదు నిజంగా మా అమ్మ చెయ్యలేదు ... |
| 2 | recommended | matched (w09) | 7:28.8-8:25.9 | 57s | standalone | no | 0.207 | 0 | 0.933 | - | - | అదే night బొమ్మ blockbuster ఆ? అంత ఊహించేసుకోకు first night అనేదానికన్నా over hypeడ్ ... |
| 3 | recommended | matched (w06) | 11:50.2-13:19.1 | 75s | standalone | no | 0.3 | 1 | 0.254 | - | - | నేను కొన్ని most important topic మాట్లాడాలనుకుంటున్నాను అదే మన శోభనం గురించి మన first ... |
| 4 | recommended | partial (w02) | 20:09.4-20:37.5 | 28s | standalone | no | 0.229 | 0 | 1.971 | - | - | రేయ్ ప్రకాశం ఆరోజు పులిహోర ఇవ్వరా అంటే ఏమన్నావ్ రా నా పెళ్ళాం నాకోసం ప్రేమతో ... |
| 5 | worth_a_look | matched (w05) | 3:41.4-4:10.4 | 28s | standalone | yes | 0.12 | 0 | 3.293 | flash | - | అసలు చాయ్ అంటే ఏమనుకుంటున్నారు. అయినా నీకు చాయ్ గోలేంట్రా? ఏంటి చాయ్ గోలా It's ... |
| 6 | worth_a_look | matched (w08) | 13:59.3-14:42.5 | 43s | standalone | no | 0.02 | 0 | 4.669 | - | - | Plain దోశా. ఓహ్! Sorry నీకు చెప్పడం మర్చిపోయాను. నాకు egg దోశ అంటే ఇష్టం. ... |
| 7 | worth_a_look | matched (w01) | 15:48.6-16:56.0 | 67s | standalone | no | 0.219 | 0 | 0.89 | reaction_hold | - | దేనికో waiting. అబ్బచ్చా! అప్పుడే మర్చిపోయావా? ఆ, ఏం మర్చిపోవడం? Night escape అయినట్టు ఇప్పుడు ... |
| 8 | worth_a_look | partial (w04) | 6:05.9-6:35.5 | 28s | standalone | no | 0.3 | 1 | 6.235 | - | - | ఆ మావోడు food విషయంలో చాలా particular గా ఉంటాడమ్మా. వాళ్ళ అమ్మ చనిపోయిన తర్వాత ... |

New moments (outside the GT, NOT wrong; need the founder's rating):
- rank 4 [partial w02] 20:09.4-20:37.5 28s standalone: నా పెళ్ళాం వంట కుమారి ఆంటీ కన్నా టేస్టీ | రేయ్ ప్రకాశం ఆరోజు పులిహోర ఇవ్వరా అంటే ఏమన్నావ్ రా నా పెళ్ళాం నాకోసం ప్రేమతో ...
- rank 8 [partial w04] 6:05.9-6:35.5 28s standalone: నా కొడుకు నాతో నాలుగేళ్లు మాట్లాడలేదు | ఆ మావోడు food విషయంలో చాలా particular గా ఉంటాడమ్మా. వాళ్ళ అమ్మ చనిపోయిన తర్వాత ...

Rule failures (proposals):
- rank 5 flash: seg1 ends 0.28s after the shot change at 226.17s
- rank 7 reaction_hold: ends 0.89s after the last word, not on a shot change

GT sanity (rule checks on the approved GT clips): 10/12 (83%) pass.
- w04 lead_in [fail]: 0.44s before the first word (silence)
- w05 reaction_hold [fail]: ends 0.39s after the last word, not on a shot change
- w06 mid_line [warn]: seg0 trims line at 'మన' | 'గురించి'
- w07 lead_in [warn]: 0.34s before the first word (0.3s of it music)
- w11 mid_word [warn]: seg1 start 1047.30s inside 'చెప్తాను.' (1041.76-1047.50, stretched Scribe timing)
- w12 mid_word [warn]: seg0 start 547.00s inside 'ఉమ్' (544.56-547.16, stretched Scribe timing)
- w12 mid_line [warn]: seg0 trims line at 'nail' | 'ఉమ్'
