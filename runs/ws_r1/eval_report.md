# Web-series eval: `D:\shotvi\runs\ws_r1`

Matching: a proposal matches a GT clip if it covers >= 50% of the GT's spoken core and the source-span IoU is >= 0.2; proposals are taken in rank order, each matching at most one GT clip. Unmatched proposals are not errors: `new` and `partial` ones need the founder's rating.

## Episode summary

| video | GT | props | recall (any) | recall@k=GT | recall@5 | missed | mean abs dstart / dend (s) | payoff+hold kept | drops setup | len ratio | word F1 | same purpose | props pass rules | GT pass rules | new / partial / dup |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| j14Om1SaFYE | 8 | 8 | 8/8 (100%) | 8/8 (100%) | 5/8 (62%) | - | 5.766 / 3.596 | 5/8 (62%) | 3/8 | 1.045 | 0.928 | 7/8 (88%) | 7/8 (88%) | 8/8 (100%) | 0 / 0 / 0 |
| QWIvgoTQGKk | 12 | 8 | 7/12 (58%) | 7/12 (58%) | 5/12 (42%) | w03, w04, w10, w11, w12 | 8.109 / 8.75 | 4/7 (57%) | 3/7 | 0.967 | 0.883 | 7/7 (100%) | 7/8 (88%) | 10/12 (83%) | 0 / 1 / 0 |

Overall: recall 15/20 (75%), recall@k=GT 15/20 (75%), recall@5 per episode summed 10/20 (50%). Small sample: every number above rests on 20 GT clips from 2 episode(s).

## j14Om1SaFYE

Shots: `runs\video\j14Om1SaFYE\shots.json`. Opening zone: lead 0:00.0-0:23.6, isolated 0:23.6-0:33.4 (uncertain); story starts 0:33.9. Recap check vs QWIvgoTQGKk. Sponsor: none detected. Credits: heuristic (last word 1096.4s + 10s hold allowance; speechless tail 71s).

### GT clips

| GT | purpose | src | len | match rank | cov / IoU | dstart | dend | payoff / hold | drops setup | len ratio | word P/R | cuts P/GT | same purpose | cold P/GT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w01 | standalone | 3:31.1-5:14.1 | 62s | 3 | 0.943 / 0.825 | -0.1 | +2.6 | yes / yes | no | 1.096 | 1.0/0.948 | 1/2 | yes | no/no |
| w02 | standalone | 12:45.6-13:47.5 | 62s | 2 | 0.946 / 0.811 | +3.5 | +10.1 | yes / yes | yes: 8 words | 1.107 | 0.927/0.941 | 0/0 | yes | no/no |
| w03 | standalone | 7:18.2-9:47.6 | 84s | 1 | 1.0 / 0.59 | -0.7 | -1.5 | yes / no | no | 1.124 | 0.808/1.0 | 2/3 | yes | no/no |
| w04 | standalone | 14:37.0-15:54.6 | 73s | 6 | 1.0 / 0.917 | -2.3 | +0.0 | yes / yes | no | 1.09 | 0.961/1.0 | 0/1 | yes | no/no |
| w06 | promo | 11:48.9-12:38.2 | 49s | 7 | 1.0 / 0.882 | -0.0 | -5.8 | yes / no | no | 0.882 | 1.0/1.0 | 0/0 | no | no/no |
| w07 | standalone | 5:28.0-5:58.0 | 30s | 5 | 1.0 / 0.743 | -6.1 | +4.2 | yes / yes | no | 1.345 | 0.98/1.0 | 0/0 | yes | no/no |
| w08 | promo | 17:06.8-18:24.0 | 67s | 8 | 0.872 / 0.723 | +7.1 | -4.5 | yes / no | yes: 14 words | 0.974 | 0.877/0.869 | 0/2 | yes | no/no |
| w09 | standalone | 9:57.9-11:17.6 | 72s | 4 | 0.618 / 0.57 | +26.4 | +0.0 | yes / yes | yes: 39 words | 0.743 | 0.895/0.685 | 0/1 | yes | no/no |

Setup dropped (proposal starts later and loses GT-kept lines):
- w02 vs rank 2: starts +3.5s, loses 8 words: వాడు marriage గురించి ఎలా explain చేసాడో తెలుసా? ఆ
- w08 vs rank 8: starts +7.1s, loses 14 words: అత్తమ్మ మీరు మీ కూతుర్ని miss అవుతున్నారంటే చెప్పండి. ఇక్కడే వదిలేసి వెళ్తాను. ...
- w09 vs rank 4: starts +26.4s, loses 39 words: ఏరా ఆ అమ్మాయి అంత నచ్చిందా? నీలా నువ్వే మురిసిపోతున్నావ్. అమ్మాయి చాలా ...

### Proposals

| rank | status | src | len | purpose | cold | lead-in | cuts | hold | fails | warns | excerpt |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | matched (w03) | 7:17.5-9:46.1 | 95s | standalone | no | 0.3 | 2 | 5.008 | - | - | ఆ మీకు ఇది ఎన్నో పెళ్లిచూపులు మొదటి పెళ్లిచూపులు కానీ నాకిది ఐదో పెళ్లిచూపులు ఓహో! ... |
| 2 | matched (w02) | 12:49.1-13:57.6 | 68s | standalone | no | 0.3 | 0 | 1.15 | - | - | ఈ రోజుల్లో marriage అనేది full meals లాంటిది. starting లో hot and exciting ... |
| 3 | matched (w01) | 3:31.0-5:16.7 | 68s | standalone | no | 0.3 | 1 | 3.182 | - | - | మీకు వంట వచ్చా? చచ్చింది గొర్ర. ఆహా! ఆ వచ్చు బాబు. ఎందుకు రాదు. వచ్చే ... |
| 4 | matched (w09) | 10:24.3-11:17.6 | 53s | standalone | no | 0.3 | 0 | 2.97 | - | - | మరి లేకపోతే ఏంట్రా? నువ్వేమన్నా ఆ అమ్మాయితో curry point పెట్టేస్తున్నావా? పెళ్లిచూపులకి వెళ్లి చూడాల్సింది ... |
| 5 | matched (w07) | 5:21.9-6:02.2 | 40s | standalone | no | 0.06 | 0 | 5.003 | flash | - | నూనెయ్యవే! పోసేయ్, పోసేయ్, పోసేయ్. అది నూనె అనుకుంటున్నావా, బండిలో పోసే petrol అనుకుంటున్నావా, అంత ... |
| 6 | matched (w04) | 14:34.7-15:54.6 | 80s | standalone | no | 0.298 | 0 | 3.614 | - | - | అయ్యో అయినా మిమ్మల్ని అడుగుతున్నా ఏంటి? మీరు బయట food తినరు కదా. మీకు అన్ని ... |
| 7 | matched (w06) | 11:48.8-12:32.4 | 44s | standalone | no | 0.3 | 0 | 2.664 | - | - | నేను ఒక విషయం చెప్పాలి మీకు అంతకన్నా ముందు నేను మీకు ఒక విషయం చెప్పాలి. ... |
| 8 | matched (w08) | 17:13.9-18:19.5 | 66s | promo | no | 0.3 | 0 | 3.091 | - | - | ఆహా లేదు. మీరు మీ కూతుర్ని miss అవుతున్నారో లేదో తెలీదు గానీ, తను చేసే ... |

Rule failures (proposals):
- rank 5 flash: seg0 starts 0.20s before the shot change at 322.12s

GT sanity (rule checks on the approved GT clips): 8/8 (100%) pass.
- w03 mid_line [warn]: seg0 trims line at 'మీకు' | 'ఆ'

## QWIvgoTQGKk

Shots: `runs\video\QWIvgoTQGKk\shots.json`. Opening zone: lead 0:00.0-2:13.6; story starts 2:14.1. Recap check vs j14Om1SaFYE. Sponsor: none detected. Credits: heuristic (evenly timed shot cards from 1257.5s; speechless tail 59s).

### GT clips

| GT | purpose | src | len | match rank | cov / IoU | dstart | dend | payoff / hold | drops setup | len ratio | word P/R | cuts P/GT | same purpose | cold P/GT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w01 | standalone | 15:48.6-17:01.0 | 72s | 6 | 1.0 / 1.0 | -0.0 | -0.0 | yes / yes | no | 1.0 | 1.0/1.0 | 0/0 | yes | no/no |
| w02 | promo | 18:50.5-20:57.5 | 89s | 5 | 1.0 / 0.871 | +0.0 | -2.8 | yes / no | no | 1.068 | 0.881/1.0 | 1/1 | yes | no/yes |
| w03 | standalone | 10:51.1-11:09.5 | 18s | MISSED | no overlap | | | | | | | | | |
| w04 | promo | 6:05.9-7:21.1 | 74s | MISSED | nearest: rank 8 cov 0.496 IoU 0.49 | | | | | | | | | |
| w05 | standalone | 3:41.4-4:20.8 | 39s | 3 | 1.0 / 0.998 | +0.0 | +0.1 | yes / yes | no | 1.002 | 1.0/1.0 | 0/0 | yes | no/no |
| w06 | standalone | 11:55.8-13:22.5 | 73s | 2 | 1.0 / 0.885 | -5.6 | -3.4 | yes / no | no | 1.029 | 0.91/1.0 | 1/1 | yes | no/no |
| w07 | standalone | 2:23.2-3:16.5 | 53s | 4 | 0.528 / 0.503 | +16.8 | -7.8 | no / no | yes: 24 words | 0.503 | 1.0/0.612 | 0/0 | yes | yes/yes |
| w08 | standalone | 13:28.0-14:42.5 | 74s | 7 | 0.554 / 0.361 | +31.3 | +45.1 | yes / yes | yes: 35 words | 1.186 | 0.5/0.607 | 0/0 | yes | no/no |
| w09 | standalone | 7:25.7-8:32.2 | 66s | 1 | 0.956 / 0.925 | +3.1 | +2.0 | yes / yes | yes: 5 words | 0.983 | 1.0/0.964 | 0/0 | yes | no/no |
| w10 | standalone | 4:49.5-5:57.5 | 68s | MISSED | no overlap | | | | | | | | | |
| w11 | standalone | 17:27.3-18:43.2 | 61s | MISSED | no overlap | | | | | | | | | |
| w12 | standalone | 9:07.0-9:49.5 | 42s | MISSED | no overlap | | | | | | | | | |

Missed GT clips:
- w03 (standalone, 10:51.1-11:09.5): అమ్మ చేతి వంటలా ఉంది… 'మా అమ్మ చెయ్యలేదు ఒట్టు!' | అమ్మ చేతి వంటలా ఉంది. హా మా అమ్మ చెయ్యలేదు నిజంగా మా అమ్మ చెయ్యలేదు ...
- w04 (promo, 6:05.9-7:21.1): అబద్ధం అంటేనే fire అయిపోతాడు… అమృత పరిస్థితి ఏంటి? | అబద్ధాలు చెప్పి పెళ్లి చేసుకోవడం correct ఏనా? ఆ మావోడు food విషయంలో చాలా particular ...
- w10 (standalone, 4:49.5-5:57.5): Dinner ఏ కాదు carriage కూడా తనే పెడుతుంది! అమ్మ హామీలు | నాకోసం ఏం వండవు. ఆ ఆ తోటకూర పప్పు ఆవకాయ ఊ ఆ ఇది కూడా ...
- w11 (standalone, 17:27.3-18:43.2): First easy వంట try చేద్దాం… popcorn I am coming! | అమ్మే! ప్రతిసారి వంటల కోసం అమ్మనేమడుగుతాం. మనం కూడా try చేద్దాం. First easy with ...
- w12 (standalone, 9:07.0-9:49.5): Nail polish నేను వెయ్యనా? | Husband goals | nail polish వేస్తున్నావా? ఉమ్ సరే నేను వెయ్యనా? వద్దు వద్దు పర్లేదు వద్దు. అంత ...

Setup dropped (proposal starts later and loses GT-kept lines):
- w07 vs rank 4: starts +16.8s, loses 24 words: నేనెందుకింత తొందరగా లేచానో నాకు తెలియదు గానీ, నువ్వెందుకింత తొందరగా లేచావో నాకు ...
- w08 vs rank 7: starts +31.3s, loses 35 words: ఆహ్, రేపు morning tiffin and lunch ఏం చెయ్యాలి? ఇప్పుడెందుకు అడుగుతున్నావ్? ...
- w09 vs rank 1: starts +3.1s, loses 5 words: ఏయ్ ఏంటి matter? Matter ఏంటి?

### Proposals

| rank | status | src | len | purpose | cold | lead-in | cuts | hold | fails | warns | excerpt |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | matched (w09) | 7:28.8-8:34.2 | 65s | standalone | no | 0.207 | 0 | 2.262 | - | - | అదే night బొమ్మ blockbuster ఆ? అంత ఊహించేసుకోకు first night అనేదానికన్నా over hypeడ్ ... |
| 2 | matched (w06) | 11:50.2-13:19.1 | 75s | standalone | no | 0.3 | 1 | 0.254 | - | - | నేను కొన్ని most important topic మాట్లాడాలనుకుంటున్నాను అదే మన శోభనం గురించి మన first ... |
| 3 | matched (w05) | 3:41.4-4:20.8 | 39s | standalone | no | 0.225 | 0 | 0.47 | reaction_hold | - | అయినా నీకు చాయ్ గోలేంట్రా? ఏంటి చాయ్ గోలా అసలు చాయ్ అంటే ఏమనుకుంటున్నారు. It's ... |
| 4 | matched (w07) | 2:39.9-3:08.7 | 27s | standalone | yes | 0.12 | 0 | 0.043 | - | - | first day నే దొరికిపోయేలా ఉన్నావు కదా. మరి పెట్టావా? ఆ ఆ అదే పనిలో ... |
| 5 | matched (w02) | 18:50.5-20:54.6 | 95s | promo | no | 0.166 | 1 | 2.0 | - | - | బయట climate అదిరిపోయింది తెలుసా అందుకే snacks గా మిర్చి బజ్జి తినడానికి వచ్చాను. ఆ ... |
| 6 | matched (w01) | 15:48.6-17:01.0 | 72s | standalone | no | 0.219 | 0 | 3.729 | - | - | దేనికో waiting. అబ్బచ్చా! అప్పుడే మర్చిపోయావా? ఆ, ఏం మర్చిపోవడం? Night escape అయినట్టు ఇప్పుడు ... |
| 7 | matched (w08) | 13:59.3-15:27.6 | 88s | standalone | no | 0.02 | 0 | 1.994 | - | - | Plain దోశా. ఓహ్! Sorry నీకు చెప్పడం మర్చిపోయాను. నాకు egg దోశ అంటే ఇష్టం. ... |
| 8 | partial (w04) | 6:41.3-7:24.9 | 44s | standalone | no | 0.3 | 0 | 5.27 | - | - | పెళ్ళికి ముందు ఏమన్నావే? అమ్మా! ఉమ్ అబద్ధాలు చెప్పి పెళ్లి చేసుకోవడం correct ఏనా? ఊ ... |

New moments (outside the GT, NOT wrong; need the founder's rating):
- rank 8 [partial w04] 6:41.3-7:24.9 44s standalone: అబద్ధాలు చెప్పి పెళ్లి చేశావా అమ్మా? | పెళ్ళికి ముందు ఏమన్నావే? అమ్మా! ఉమ్ అబద్ధాలు చెప్పి పెళ్లి చేసుకోవడం correct ఏనా? ఊ ...

Rule failures (proposals):
- rank 3 reaction_hold: ends 0.47s after the last word, not on a shot change

GT sanity (rule checks on the approved GT clips): 10/12 (83%) pass.
- w04 lead_in [fail]: 0.44s before the first word (silence)
- w05 reaction_hold [fail]: ends 0.39s after the last word, not on a shot change
- w06 mid_line [warn]: seg0 trims line at 'మన' | 'గురించి'
- w07 lead_in [warn]: 0.34s before the first word (0.3s of it music)
- w11 mid_word [warn]: seg1 start 1047.30s inside 'చెప్తాను.' (1041.76-1047.50, stretched Scribe timing)
- w12 mid_word [warn]: seg0 start 547.00s inside 'ఉమ్' (544.56-547.16, stretched Scribe timing)
- w12 mid_line [warn]: seg0 trims line at 'nail' | 'ఉమ్'
