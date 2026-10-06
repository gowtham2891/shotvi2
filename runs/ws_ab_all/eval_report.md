# Web-series eval: `D:\shotvi\runs\ws_ab_all`

Matching: a proposal matches a GT clip if it covers >= 50% of the GT's spoken core and the source-span IoU is >= 0.2; proposals are taken in rank order, each matching at most one GT clip. Unmatched proposals are not errors: `new` and `partial` ones need the founder's rating.

## Episode summary

| video | GT | props | recall (any) | recall@k=GT | recall@5 | missed | mean abs dstart / dend (s) | payoff+hold kept | drops setup | len ratio | word F1 | same purpose | props pass rules | GT pass rules | new / partial / dup |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| epZ0lbXkgWA | 12 | 7 | 7/12 (58%) | 7/12 (58%) | 5/12 (42%) | w05, w09, w10, w11, w12 | 2.75 / 26.146 | 4/7 (57%) | 1/7 | 1.212 | 0.869 | 5/7 (71%) | 7/7 (100%) | 10/12 (83%) | 0 / 0 / 0 |
| j14Om1SaFYE | 8 | 8 | 7/8 (88%) | 7/8 (88%) | 4/8 (50%) | w09 | 7.82 / 2.669 | 4/7 (57%) | 3/7 | 0.933 | 0.926 | 6/7 (86%) | 8/8 (100%) | 8/8 (100%) | 1 / 0 / 0 |

Overall: recall 14/20 (70%), recall@k=GT 14/20 (70%), recall@5 per episode summed 9/20 (45%). Small sample: every number above rests on 20 GT clips from 2 episode(s).

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

## j14Om1SaFYE

Shots: `runs\video\j14Om1SaFYE\shots.json`. Opening zone: lead 0:00.0-0:23.6, isolated 0:23.6-0:33.4 (uncertain); story starts 0:33.9. Recap check vs QWIvgoTQGKk. Sponsor: none detected. Credits: heuristic (last word 1096.4s + 10s hold allowance; speechless tail 71s).

### GT clips

| GT | purpose | src | len | match rank | cov / IoU | dstart | dend | payoff / hold | drops setup | len ratio | word P/R | cuts P/GT | same purpose | cold P/GT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w01 | standalone | 3:31.1-5:14.1 | 62s | 2 | 0.943 / 0.825 | -0.1 | +2.6 | yes / yes | no | 1.096 | 1.0/0.948 | 1/2 | yes | no/no |
| w02 | standalone | 12:45.6-13:47.5 | 62s | 3 | 0.946 / 0.943 | +3.5 | +0.0 | yes / yes | yes: 8 words | 0.944 | 1.0/0.941 | 0/0 | yes | no/no |
| w03 | standalone | 7:18.2-9:47.6 | 84s | 1 | 0.749 / 0.494 | +33.9 | -1.5 | yes / no | yes: 19 words | 0.834 | 0.91/0.762 | 1/3 | yes | no/no |
| w04 | standalone | 14:37.0-15:54.6 | 73s | 8 | 0.905 / 0.837 | -2.3 | +0.0 | yes / yes | no | 1.003 | 0.961/1.0 | 1/1 | yes | no/no |
| w06 | promo | 11:48.9-12:38.2 | 49s | 7 | 0.639 / 0.58 | +14.9 | -5.8 | yes / no | yes: 33 words | 0.58 | 1.0/0.593 | 0/0 | no | no/no |
| w07 | standalone | 5:28.0-5:58.0 | 30s | 6 | 1.0 / 0.876 | +0.0 | +4.2 | yes / yes | no | 1.141 | 1.0/1.0 | 0/0 | yes | no/no |
| w08 | promo | 17:06.8-18:24.0 | 67s | 5 | 0.955 / 0.805 | -0.1 | -4.5 | yes / no | no | 0.931 | 0.973/1.0 | 1/2 | yes | yes/no |
| w09 | standalone | 9:57.9-11:17.6 | 72s | MISSED | no overlap | | | | | | | | | |

Missed GT clips:
- w09 (standalone, 9:57.9-11:17.6): Hostel food బాధ ప్రతి అబ్బాయికీ తెలుసు | food made with love | ఏరా ఆ అమ్మాయి అంత నచ్చిందా? నీలా నువ్వే మురిసిపోతున్నావ్. అమ్మాయి చాలా బాగుందిరా. కానీ ...

Setup dropped (proposal starts later and loses GT-kept lines):
- w02 vs rank 3: starts +3.5s, loses 8 words: వాడు marriage గురించి ఎలా explain చేసాడో తెలుసా? ఆ
- w03 vs rank 1: starts +33.9s, loses 19 words: మీకు ఇది ఎన్నో పెళ్లిచూపులు మొదటి పెళ్లిచూపులు కానీ నాకిది ఐదో పెళ్లిచూపులు ...
- w06 vs rank 7: starts +14.9s, loses 33 words: నేను ఒక విషయం చెప్పాలి మీకు అంతకన్నా ముందు నేను మీకు ఒక ...

### Proposals

| rank | status | src | len | purpose | cold | lead-in | cuts | hold | fails | warns | excerpt |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | matched (w03) | 7:52.1-9:46.1 | 70s | standalone | no | 0.3 | 1 | 5.008 | - | - | నేను పొట్టిగా ఉంటాను కదా ఎవ్వరూ ఇష్టపడరు. అందుకే అందరూ reject చేశారు. మీకు ఇలాంటివి ... |
| 2 | matched (w01) | 3:31.0-5:16.7 | 68s | standalone | no | 0.3 | 1 | 3.182 | - | - | మీకు వంట వచ్చా? చచ్చింది గొర్ర. ఆహా! ఆ వచ్చు బాబు. ఎందుకు రాదు. వచ్చే ... |
| 3 | matched (w02) | 12:49.1-13:47.5 | 58s | standalone | no | 0.3 | 0 | 0.749 | - | - | ఈ రోజుల్లో marriage అనేది full meals లాంటిది. starting లో hot and exciting ... |
| 4 | new | 1:24.7-2:24.2 | 56s | standalone | no | 0.3 | 1 | 0.403 | - | - | అమృత గారు, ఒకసారి నించోగలుగుతారా please. please ఒక్కసారి నించోండి. అమృత గారు I am ... |
| 5 | matched (w08) | 17:06.7-18:19.5 | 63s | promo | yes | 0.12 | 1 | 3.091 | - | - | ఎవరైనా అబద్ధాలాడినా, మోసం చేసినా అస్సలు తీసుకోలేను. అత్తమ్మ మీరు మీ కూతుర్ని miss అవుతున్నారంటే ... |
| 6 | matched (w07) | 5:28.0-6:02.2 | 34s | standalone | no | 0.098 | 0 | 5.003 | - | - | పోసేయ్, పోసేయ్, పోసేయ్. అది నూనె అనుకుంటున్నావా, బండిలో పోసే petrol అనుకుంటున్నావా, అంత పోసావు. ... |
| 7 | matched (w06) | 12:03.8-12:32.4 | 29s | standalone | no | 0.3 | 0 | 2.664 | - | - | నాకు వంట వచ్చే wife మాత్రమే కావాలనుకుంటున్నానంటే నా wife వంట గదికి మాత్రమే పరిమితమైపోవాలని ... |
| 8 | matched (w04) | 14:34.7-15:54.6 | 74s | standalone | no | 0.298 | 1 | 3.614 | - | - | అయ్యో అయినా మిమ్మల్ని అడుగుతున్నా ఏంటి? మీరు బయట food తినరు కదా. మీకు అన్ని ... |

New moments (outside the GT, NOT wrong; need the founder's rating):
- rank 4 [new] 1:24.7-2:24.2 56s standalone: పెళ్లిచూపుల్లో అమ్మాయిల బాధ | అమృత గారు, ఒకసారి నించోగలుగుతారా please. please ఒక్కసారి నించోండి. అమృత గారు I am ...

GT sanity (rule checks on the approved GT clips): 8/8 (100%) pass.
- w03 mid_line [warn]: seg0 trims line at 'మీకు' | 'ఆ'
