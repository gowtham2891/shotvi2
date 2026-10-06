# Web-series eval: `D:\shotvi\runs\ws_hm`

Matching: a proposal matches a GT clip if it covers >= 50% of the GT's spoken core and the source-span IoU is >= 0.2; proposals are taken in rank order, each matching at most one GT clip. Unmatched proposals are not errors: `new` and `partial` ones need the founder's rating.

## Episode summary

| video | GT | props | recall (any) | recall@k=GT | recall@5 | missed | mean abs dstart / dend (s) | payoff+hold kept | drops setup | len ratio | word F1 | same purpose | props pass rules | GT pass rules | new / partial / dup |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| epZ0lbXkgWA | 12 | 5 | 5/12 (42%) | 5/12 (42%) | 5/12 (42%) | w05, w06, w08, w09, w10, w11, w12 | 4.67 / 22.35 | 1/5 (20%) | 2/5 | 1.111 | 0.901 | 4/5 (80%) | 4/5 (80%) | 10/12 (83%) | 0 / 0 / 0 |

Overall: recall 5/12 (42%), recall@k=GT 5/12 (42%), recall@5 per episode summed 5/12 (42%). Small sample: every number above rests on 12 GT clips from 1 episode(s).

## epZ0lbXkgWA

Shots: `runs\video\epZ0lbXkgWA\shots.json`. Opening zone: lead 0:00.0-0:07.3, cards 0:07.3-0:17.3; story starts 0:17.8. Sponsor: 0:57.3-1:53.3 (amazon, app and website, available గా, check చేయండి, #ad brand). Credits: heuristic (last word 1487.3s + 10s hold allowance; speechless tail 121s).

### GT clips

| GT | purpose | src | len | match rank | cov / IoU | dstart | dend | payoff / hold | drops setup | len ratio | word P/R | cuts P/GT | same purpose | cold P/GT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w01 | promo | 23:06.1-24:09.5 | 63s | 2 | 0.941 / 0.922 | +3.8 | -1.2 | no / no | yes: 3 words | 0.922 | 1.0/0.957 | 0/0 | yes | no/no |
| w02 | standalone | 16:42.0-17:52.0 | 70s | 5 | 1.0 / 0.972 | -0.1 | -1.9 | yes / no | no | 0.974 | 1.0/1.0 | 0/0 | yes | no/no |
| w03 | standalone | 20:05.9-21:55.0 | 59s | 4 | 1.0 / 0.64 | -0.0 | +1.9 | yes / yes | no | 1.564 | 0.657/1.0 | 1/1 | yes | no/no |
| w04 | promo | 5:18.3-6:39.4 | 72s | 3 | 1.0 / 0.718 | -15.3 | -2.8 | yes / no | no | 1.301 | 0.764/1.0 | 0/1 | no | no/no |
| w05 | promo | 7:29.1-8:19.4 | 43s | MISSED | no overlap | | | | | | | | | |
| w06 | standalone | 14:37.9-15:58.8 | 81s | MISSED | no overlap | | | | | | | | | |
| w07 | standalone | 11:09.9-13:51.3 | 67s | 1 | 0.816 / 0.792 | +4.2 | -104.0 | no / no | yes: 5 words | 0.792 | 1.0/0.77 | 0/1 | yes | no/no |
| w08 | promo | 0:17.6-0:55.8 | 38s | MISSED | no overlap | | | | | | | | | |
| w09 | standalone | 12:22.9-13:37.5 | 75s | MISSED | no overlap | | | | | | | | | |
| w10 | standalone | 18:00.9-19:21.6 | 70s | MISSED | no overlap | | | | | | | | | |
| w11 | promo | 9:09.2-10:00.9 | 52s | MISSED | no overlap | | | | | | | | | |
| w12 | standalone | 24:03.5-24:49.9 | 46s | MISSED | nearest: rank 2 cov 0.107 IoU 0.049 (matched to another GT) | | | | | | | | | |

Missed GT clips:
- w05 (promo, 7:29.1-8:19.4): Friend ని కలవడానికి వెళ్తున్నా అన్నాడు… కానీ wife, పాపతో కనిపించాడు | Hello Hello సోహం ఆ చెప్పు అన్సుని ఎక్కడున్నావ్ నేను ఇప్పుడే ఇంటి నుంచి బయలుదేరాను ...
- w06 (standalone, 14:37.9-15:58.8): నేను రేపో మాపో పోతే ఒంటరిగా ఉండగలవా? అమ్మ చెప్పిన సృష్టి ధర్మం | ఒకటి చెప్పు, చచ్చేంత వరకు నేను ఇలానే ఉంటాను అంటున్నావు కదా. నేను రేపో మాపో ...
- w08 (promo, 0:17.6-0:55.8): అసలు తన life లో ఏం జరిగింది bro? ఈ ఒక్క question కి answer చెప్పండి | నా మాట విని వదిలేయ్ bro తనని. అసలు తన life లో ఏం జరిగిందని ...
- w09 (standalone, 12:22.9-13:37.5): ఇంట్లో bottle ఖాళీ అవుతుంటే ఎవరా అనుకున్నా… ఆ దొంగ అమ్మే! | ఏదో బాధలన్నీ దేవుడు నీకే పెట్టినట్టు పెద్ద buildup ఇవ్వకు. మాకూ బాధలుంటాయి. ఆలోచించకుండా పడుకోవాలని ...
- w10 (standalone, 18:00.9-19:21.6): మొన్న చేసిన పనికి police కి call చేస్తుందేమో అనుకున్నా… ఆంటీ ఇంత sweet ఆ? | మొన్న చేసిన పనికి, ఇప్పుడు ఆంటీకి కనిపిస్తే ఏం చేస్తదో ఏంటో. police కి call ...
- w11 (promo, 9:09.2-10:00.9): Balance story ఏముంటది? Lady Arjun Reddy అయ్యుంటుంది అంతే | ఎన్ని నాటకాలు ఆడాడు. ప్రపంచం చిన్నదీ పెద్దదీ అంటూ. పాపం అన్సుని చాలా hopes పెట్టుకున్నట్టుంది. ...
- w12 (standalone, 24:03.5-24:49.9): నాకు కోపం రాకూడదా?… 'సోహం చనిపోయాడురా!' | నువ్వు ఆపుతావా ఆపవా? నీకు సోహం గురించి ఏం తెలుసు అని మాట్లాడుతున్నావ్ రా. ఆ ...

Setup dropped (proposal starts later and loses GT-kept lines):
- w01 vs rank 2: starts +3.8s, loses 3 words: నాకు తెలుసు ఏంటి
- w07 vs rank 1: starts +4.2s, loses 5 words: ఏంటమ్మా అలా చూస్తున్నావ్ ఏం లేదు

### Proposals

| rank | status | src | len | purpose | cold | lead-in | cuts | hold | fails | warns | excerpt |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | matched (w07) | 11:14.1-12:07.3 | 53s | standalone | no | 0.3 | 0 | 3.439 | - | - | నేను ఇప్పుడు wine తాగుదాము అంటే ఏమంటావా అని ఏం మాట్లాడుతున్నావమ్మా నాకన్నీ తెలుసులేవే నువ్వు ... |
| 2 | matched (w01) | 23:09.9-24:08.4 | 58s | promo | no | 0.3 | 0 | 0.45 | reaction_hold | - | మీరు ఎందుకు వేరే వాడిని మీ life లోకి రానివ్వట్లేదో నాకు తెలుసు ఏం తెలుసు ... |
| 3 | matched (w04) | 5:03.0-6:36.6 | 94s | standalone | no | 0.3 | 0 | 1.937 | - | - | ఏమైంది అంశ? ఎందుకు ఒక వారం నుంచి నరకం చూపిస్తున్నావ్? phone లో block చేశావ్. ... |
| 4 | matched (w03) | 20:05.9-21:56.9 | 92s | standalone | no | 0.255 | 1 | 2.366 | - | - | ఆ రోజు నైట్ జరిగిన దానికి I am really sorry అండి. ఏదో మధ్య ... |
| 5 | matched (w02) | 16:41.9-17:50.1 | 68s | standalone | no | 0.3 | 0 | 5.558 | - | - | తోడు లేని జీవితం waste అన్నట్టు అన్నావు కదా, మరి నీక తోడు అవసరం లేదా ... |

Rule failures (proposals):
- rank 2 reaction_hold: ends 0.45s after the last word, not on a shot change

GT sanity (rule checks on the approved GT clips): 10/12 (83%) pass.
- w03 reaction_hold [fail]: ends 0.47s after the last word, not on a shot change
- w11 reaction_hold [fail]: ends 0.56s after the last word, not on a shot change
