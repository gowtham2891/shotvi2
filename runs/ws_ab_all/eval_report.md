# Web-series eval: `D:\shotvi\runs\ws_ab_all`

Matching: a proposal matches a GT clip if it covers >= 50% of the GT's spoken core and the source-span IoU is >= 0.2; proposals are taken in rank order, each matching at most one GT clip. Unmatched proposals are not errors: `new` and `partial` ones need the founder's rating.

## Episode summary

| video | GT | props | recall (any) | recall@k=GT | recall@5 | missed | mean abs dstart / dend (s) | payoff+hold kept | drops setup | len ratio | word F1 | same purpose | props pass rules | GT pass rules | new / partial / dup |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| epZ0lbXkgWA | 12 | 7 | 7/12 (58%) | 7/12 (58%) | 5/12 (42%) | w05, w09, w10, w11, w12 | 2.75 / 26.146 | 4/7 (57%) | 1/7 | 1.212 | 0.869 | 5/7 (71%) | 7/7 (100%) | 10/12 (83%) | 0 / 0 / 0 |

Overall: recall 7/12 (58%), recall@k=GT 7/12 (58%), recall@5 per episode summed 5/12 (42%). Small sample: every number above rests on 12 GT clips from 1 episode(s).

## epZ0lbXkgWA

Shots: `runs\video\epZ0lbXkgWA\shots.json`. Opening zone: lead 0:00.0-0:07.3, cards 0:07.3-0:17.3; story starts 0:17.8. Sponsor: 0:57.3-1:53.3 (amazon, app and website, available గా, check చేయండి, #ad brand). Credits: heuristic (last word 1487.3s + 10s hold allowance; speechless tail 121s).

### GT clips

| GT | purpose | src | len | match rank | cov / IoU | dstart | dend | payoff / hold | drops setup | len ratio | word P/R | cuts P/GT | same purpose | cold P/GT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w01 | promo | 23:06.1-24:09.5 | 63s | 1 | 0.943 / 0.575 | +3.8 | +40.4 | yes / yes | yes: 3 words | 1.577 | 0.709/0.974 | 0/0 | no | no/no |
| w02 | standalone | 16:42.0-17:52.0 | 70s | 4 | 1.0 / 0.972 | -0.1 | -1.9 | yes / no | no | 0.974 | 1.0/1.0 | 0/0 | yes | no/no |
| w03 | standalone | 20:05.9-21:55.0 | 59s | 5 | 0.966 / 0.515 | -0.0 | +1.9 | yes / yes | no | 1.853 | 0.542/1.0 | 0/1 | yes | yes/no |
| w04 | promo | 5:18.3-6:39.4 | 72s | 6 | 0.898 / 0.628 | -15.3 | -11.4 | no / no | no | 1.181 | 0.748/0.916 | 0/1 | no | no/no |
| w05 | promo | 7:29.1-8:19.4 | 43s | MISSED | no overlap | | | | | | | | | |
| w06 | standalone | 14:37.9-15:58.8 | 81s | 3 | 0.926 / 0.729 | -0.1 | +22.0 | yes / yes | no | 1.2 | 0.719/1.0 | 0/0 | yes | yes/no |
| w07 | standalone | 11:09.9-13:51.3 | 67s | 2 | 0.853 / 0.828 | -0.1 | -104.0 | no / no | no | 0.83 | 1.0/0.82 | 0/1 | yes | yes/no |
| w08 | promo | 0:17.6-0:55.8 | 38s | 7 | 0.812 / 0.798 | -0.0 | +1.5 | yes / yes | no | 0.868 | 1.0/1.0 | 1/0 | yes | no/no |
| w09 | standalone | 12:22.9-13:37.5 | 75s | MISSED | no overlap | | | | | | | | | |
| w10 | standalone | 18:00.9-19:21.6 | 70s | MISSED | no overlap | | | | | | | | | |
| w11 | promo | 9:09.2-10:00.9 | 52s | MISSED | no overlap | | | | | | | | | |
| w12 | standalone | 24:03.5-24:49.9 | 46s | MISSED | nearest: rank 1 cov 1.0 IoU 0.464 (matched to another GT) | | | | | | | | | |

Missed GT clips:
- w05 (promo, 7:29.1-8:19.4): Friend ని కలవడానికి వెళ్తున్నా అన్నాడు… కానీ wife, పాపతో కనిపించాడు | Hello Hello సోహం ఆ చెప్పు అన్సుని ఎక్కడున్నావ్ నేను ఇప్పుడే ఇంటి నుంచి బయలుదేరాను ...
- w09 (standalone, 12:22.9-13:37.5): ఇంట్లో bottle ఖాళీ అవుతుంటే ఎవరా అనుకున్నా… ఆ దొంగ అమ్మే! | ఏదో బాధలన్నీ దేవుడు నీకే పెట్టినట్టు పెద్ద buildup ఇవ్వకు. మాకూ బాధలుంటాయి. ఆలోచించకుండా పడుకోవాలని ...
- w10 (standalone, 18:00.9-19:21.6): మొన్న చేసిన పనికి police కి call చేస్తుందేమో అనుకున్నా… ఆంటీ ఇంత sweet ఆ? | మొన్న చేసిన పనికి, ఇప్పుడు ఆంటీకి కనిపిస్తే ఏం చేస్తదో ఏంటో. police కి call ...
- w11 (promo, 9:09.2-10:00.9): Balance story ఏముంటది? Lady Arjun Reddy అయ్యుంటుంది అంతే | ఎన్ని నాటకాలు ఆడాడు. ప్రపంచం చిన్నదీ పెద్దదీ అంటూ. పాపం అన్సుని చాలా hopes పెట్టుకున్నట్టుంది. ...
- w12 (standalone, 24:03.5-24:49.9): నాకు కోపం రాకూడదా?… 'సోహం చనిపోయాడురా!' | నువ్వు ఆపుతావా ఆపవా? నీకు సోహం గురించి ఏం తెలుసు అని మాట్లాడుతున్నావ్ రా. ఆ ...

Setup dropped (proposal starts later and loses GT-kept lines):
- w01 vs rank 1: starts +3.8s, loses 3 words: నాకు తెలుసు ఏంటి

### Proposals

| rank | status | src | len | purpose | cold | lead-in | cuts | hold | fails | warns | excerpt |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | matched (w01) | 23:09.9-24:49.9 | 100s | standalone | no | 0.3 | 0 | 2.612 | - | - | మీరు ఎందుకు వేరే వాడిని మీ life లోకి రానివ్వట్లేదో నాకు తెలుసు ఏం తెలుసు ... |
| 2 | matched (w07) | 11:09.8-12:07.3 | 56s | standalone | yes | 0.12 | 0 | 3.439 | - | - | ఏయ్ ఇప్పుడు నేను నీకు అమ్మనని ఎవరు చెప్పారు ఏంటమ్మా అలా చూస్తున్నావ్ ఏం లేదు ... |
| 3 | matched (w06) | 14:37.9-16:20.7 | 97s | standalone | yes | 0.12 | 0 | 2.0 | - | - | నేను పోతే ఒంటరిగా ఉండగలవా ఒకటి చెప్పు, చచ్చేంత వరకు నేను ఇలానే ఉంటాను అంటున్నావు ... |
| 4 | matched (w02) | 16:41.9-17:50.1 | 68s | standalone | no | 0.3 | 0 | 5.558 | - | - | తోడు లేని జీవితం waste అన్నట్టు అన్నావు కదా, మరి నీక తోడు అవసరం లేదా ... |
| 5 | matched (w03) | 20:05.9-21:56.9 | 109s | standalone | yes | 0.12 | 0 | 2.366 | - | - | ఒక్కసారి హన్స్నితో పెళ్లయితే అందరం కలిసి తాగొచ్చు. ఆ రోజు నైట్ జరిగిన దానికి I ... |
| 6 | matched (w04) | 5:03.0-6:28.0 | 85s | standalone | no | 0.3 | 0 | 0.944 | - | - | ఏమైంది అంశ? ఎందుకు ఒక వారం నుంచి నరకం చూపిస్తున్నావ్? phone లో block చేశావ్. ... |
| 7 | matched (w08) | 0:17.5-0:57.3 | 33s | promo | no | 0.3 | 1 | 4.541 | - | - | నా మాట విని వదిలేయ్ bro తనని. అసలు తన life లో ఏం జరిగిందని ... |

GT sanity (rule checks on the approved GT clips): 10/12 (83%) pass.
- w03 reaction_hold [fail]: ends 0.47s after the last word, not on a shot change
- w11 reaction_hold [fail]: ends 0.56s after the last word, not on a shot change
