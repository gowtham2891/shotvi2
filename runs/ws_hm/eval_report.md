# Web-series eval: `D:\shotvi\runs\ws_hm`

GT = clips the founder would post (`founder_would_post: yes`). Matching: a proposal matches a GT clip if it covers >= 50% of the GT's spoken core and the source-span IoU is >= 0.2; proposals are taken in rank order, each matching at most one GT clip. Leftover proposals are matched the same way against the founder's `maybe` clips (`acceptable`: not a miss, not a false positive) and `no` clips (`founder_no`: a negative hit). Unmatched proposals are not errors: `new` and `partial` ones need the founder's rating. Founder payoff/overall columns are the founder's scores for the GT cut of that moment (informational), not a rating of the proposal.

## Episode summary

| video | GT yes | maybe / no | props | recall (any) | recall@k=GT | recall@5 | missed | acceptable | founder_no | mean abs dstart / dend (s) | payoff+hold kept | drops setup | len ratio | word F1 | same purpose | props pass rules | GT pass rules | new / partial / dup |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| epZ0lbXkgWA | 6 | 4 / 0 | 5 | 3/6 (50%) | 3/6 (50%) | 3/6 (50%) | w06, w01, w09 | 1/4 (25%) | 0/0 | 1.433 / 0.637 | 3/3 (100%) | 1/3 | 1.164 | 0.921 | 3/3 (100%) | 4/5 (80%) | 5/6 (83%) | 0 / 1 / 0 |

Overall: recall 3/6 (50%), recall@k=GT 3/6 (50%), recall@5 per episode summed 3/6 (50%); acceptable hits 1 (of 4 maybe clips), founder_no hits 0 (of 0 no clips). Small sample: every number above rests on 6 founder-yes GT clips from 1 episode(s).

## epZ0lbXkgWA

Shots: `runs\video\epZ0lbXkgWA\shots.json`. Opening zone: lead 0:00.0-0:07.3, cards 0:07.3-0:17.3; story starts 0:17.8. Sponsor: 0:57.3-1:53.3 (amazon, app and website, available గా, check చేయండి, #ad brand). Credits: heuristic (last word 1487.3s + 10s hold allowance; speechless tail 121s).

### GT clips (founder: yes)

| GT | purpose | founder payoff/overall | src | len | match rank | cov / IoU | dstart | dend | payoff / hold | drops setup | len ratio | word P/R | cuts P/GT | same purpose | cold P/GT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w02 | standalone | 4/4 | 16:42.0-17:50.1 | 68s | 5 | 1.0 / 0.999 | -0.1 | +0.0 | yes / yes | no | 1.001 | 1.0/1.0 | 0/0 | yes | no/no |
| w03 | standalone | 5/4 | 20:05.9-21:55.0 | 59s | 4 | 1.0 / 0.64 | -0.0 | +1.9 | yes / yes | no | 1.564 | 0.657/1.0 | 1/1 | yes | no/no |
| w06 | standalone | 3/4 | 14:37.9-15:58.8 | 78s | MISSED | no overlap | | | | | | | | | |
| w01 | standalone | 4/4 | 23:41.7-24:49.9 | 68s | MISSED | nearest: rank 2 cov 0.404 IoU 0.267 | | | | | | | | | |
| w09 | standalone | 4/4 | 12:22.9-13:13.2 | 50s | MISSED | no overlap | | | | | | | | | |
| w07 | standalone | 4/4 | 11:09.9-12:07.2 | 57s | 1 | 0.925 / 0.926 | +4.2 | +0.0 | yes / yes | yes: 5 words | 0.926 | 1.0/0.939 | 0/0 | yes | no/no |

### Founder maybe / no clips (not GT: a hit is `acceptable` / `founder_no`, a miss is not a miss)

| clip | verdict | founder payoff/overall | purpose | src | len | hit by rank | cov / IoU | excerpt |
|---|---|---|---|---|---|---|---|---|
| w10 | maybe | 3/3 | standalone | 18:00.9-18:47.4 | 42s | - | - | మొన్న చేసిన పనికి, ఇప్పుడు ఆంటీకి కనిపిస్తే ఏం చేస్తదో ఏంటో. police కి call ... |
| w15 | maybe | 3/3 | standalone | 13:42.0-14:27.4 | 45s | - | - | ఏంటి ఆ అబ్బాయి matter నాకు తెలుసు, నువ్వు all of a sudden ఎందుకు ... |
| w14 | maybe | 3/3 | standalone | 20:35.0-21:12.8 | 38s | - | - | అప్పుడప్పుడు మా హన్స్ని కొంచెం గరుగ్గా మాట్లాడుతుంది. ఏం పట్టించుకోకు బాబు. ఉమ్ పర్లేదండి నాకు ... |
| w13 | maybe | 3/3 | standalone | 5:18.3-8:19.4 | 67s | 3 | 0.574 / 0.275 | నీకు already చెప్పాను కదా. I am not interested in you and this ... |

Missed GT clips:
- w06 (standalone, 14:37.9-15:58.8): నేను రేపో మాపో పోతే ఒంటరిగా ఉండగలవా? అమ్మ చెప్పిన సృష్టి ధర్మం | ఒకటి చెప్పు, చచ్చేంత వరకు నేను ఇలానే ఉంటాను అంటున్నావు కదా. నేను రేపో మాపో ...
- w01 (standalone, 23:41.7-24:49.9): వాడు అస్సలు happy గా ఉండలేడు అన్నాడు… 'సోహం చనిపోయాడురా!' | అయినా ఎవడికి మనసేలో వచ్చిందండి. మీలాంటి మంచి మనిషిని ఇబ్బంది పెట్టడానికి. మీరు అలా చూస్తూ ...
- w09 (standalone, 12:22.9-13:13.2): ఇంట్లో bottle ఖాళీ అవుతుంటే ఎవరా అనుకున్నా… ఆ దొంగ అమ్మే! | ఏదో బాధలన్నీ దేవుడు నీకే పెట్టినట్టు పెద్ద buildup ఇవ్వకు. మాకూ బాధలుంటాయి. ఆలోచించకుండా పడుకోవాలని ...

Setup dropped (proposal starts later and loses GT-kept lines):
- w07 vs rank 1: starts +4.2s, loses 5 words: ఏంటమ్మా అలా చూస్తున్నావ్ ఏం లేదు

### Proposals

| rank | status | src | len | purpose | cold | lead-in | cuts | hold | fails | warns | excerpt |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | matched (w07) | 11:14.1-12:07.3 | 53s | standalone | no | 0.3 | 0 | 3.439 | - | - | నేను ఇప్పుడు wine తాగుదాము అంటే ఏమంటావా అని ఏం మాట్లాడుతున్నావమ్మా నాకన్నీ తెలుసులేవే నువ్వు ... |
| 2 | partial (w01) | 23:09.9-24:08.4 | 58s | promo | no | 0.3 | 0 | 0.45 | reaction_hold | - | మీరు ఎందుకు వేరే వాడిని మీ life లోకి రానివ్వట్లేదో నాకు తెలుసు ఏం తెలుసు ... |
| 3 | acceptable (w13) founder 3/3 | 5:03.0-6:36.6 | 94s | standalone | no | 0.3 | 0 | 1.937 | - | - | ఏమైంది అంశ? ఎందుకు ఒక వారం నుంచి నరకం చూపిస్తున్నావ్? phone లో block చేశావ్. ... |
| 4 | matched (w03) | 20:05.9-21:56.9 | 92s | standalone | no | 0.255 | 1 | 2.366 | - | - | ఆ రోజు నైట్ జరిగిన దానికి I am really sorry అండి. ఏదో మధ్య ... |
| 5 | matched (w02) | 16:41.9-17:50.1 | 68s | standalone | no | 0.3 | 0 | 5.558 | - | - | తోడు లేని జీవితం waste అన్నట్టు అన్నావు కదా, మరి నీక తోడు అవసరం లేదా ... |

Founder maybe / no hits (acceptable = on a `maybe` moment; founder_no = on a moment the founder would NOT post):
- rank 3 [acceptable w13, founder 3/3] 5:03.0-6:36.6 94s standalone: వారం నుంచి నరకం చూపిస్తున్నావ్ | ఏమైంది అంశ? ఎందుకు ఒక వారం నుంచి నరకం చూపిస్తున్నావ్? phone లో block చేశావ్. ...

New moments (outside the GT, NOT wrong; need the founder's rating):
- rank 2 [partial w01] 23:09.9-24:08.4 58s promo: నీకు సోహం గురించి ఏం తెలుసని మాట్లాడుతున్నావ్ | మీరు ఎందుకు వేరే వాడిని మీ life లోకి రానివ్వట్లేదో నాకు తెలుసు ఏం తెలుసు ...

Rule failures (proposals):
- rank 2 reaction_hold: ends 0.45s after the last word, not on a shot change

GT sanity (rule checks on the approved GT clips): 5/6 (83%) pass.
- w03 reaction_hold [fail]: ends 0.47s after the last word, not on a shot change
