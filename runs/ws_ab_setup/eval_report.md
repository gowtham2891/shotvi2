# Web-series eval: `D:\shotvi\runs\ws_ab_setup`

Matching: a proposal matches a GT clip if it covers >= 50% of the GT's spoken core and the source-span IoU is >= 0.2; proposals are taken in rank order, each matching at most one GT clip. Unmatched proposals are not errors: `new` and `partial` ones need the founder's rating.

## Episode summary

| video | GT | props | recall (any) | recall@k=GT | recall@5 | missed | mean abs dstart / dend (s) | payoff+hold kept | drops setup | len ratio | word F1 | same purpose | props pass rules | GT pass rules | new / partial / dup |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| j14Om1SaFYE | 8 | 6 | 6/8 (75%) | 6/8 (75%) | 5/8 (62%) | w06, w09 | 7.657 / 2.142 | 4/6 (67%) | 2/6 | 1.029 | 0.952 | 6/6 (100%) | 5/6 (83%) | 8/8 (100%) | 0 / 0 / 0 |
| QWIvgoTQGKk | 12 | 7 | 6/12 (50%) | 6/12 (50%) | 5/12 (42%) | w03, w04, w07, w10, w11, w12 | 8.255 / 9.325 | 4/6 (67%) | 3/6 | 1.06 | 0.873 | 6/6 (100%) | 6/7 (86%) | 10/12 (83%) | 0 / 1 / 0 |

Overall: recall 12/20 (60%), recall@k=GT 12/20 (60%), recall@5 per episode summed 10/20 (50%). Small sample: every number above rests on 20 GT clips from 2 episode(s).

## j14Om1SaFYE

Shots: `runs\video\j14Om1SaFYE\shots.json`. Opening zone: lead 0:00.0-0:23.6, isolated 0:23.6-0:33.4 (uncertain); story starts 0:33.9. Recap check vs QWIvgoTQGKk. Sponsor: none detected. Credits: heuristic (last word 1096.4s + 10s hold allowance; speechless tail 71s).

### GT clips

| GT | purpose | src | len | match rank | cov / IoU | dstart | dend | payoff / hold | drops setup | len ratio | word P/R | cuts P/GT | same purpose | cold P/GT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w01 | standalone | 3:31.1-5:14.1 | 62s | 2 | 0.956 / 0.835 | -0.1 | +2.6 | yes / yes | no | 1.108 | 1.0/1.0 | 1/2 | yes | yes/no |
| w02 | standalone | 12:45.6-13:47.5 | 62s | 3 | 0.946 / 0.943 | +3.5 | +0.0 | yes / yes | yes: 8 words | 0.944 | 1.0/0.941 | 0/0 | yes | no/no |
| w03 | standalone | 7:18.2-9:47.6 | 84s | 1 | 0.749 / 0.557 | +33.9 | -1.5 | yes / no | yes: 19 words | 0.696 | 0.91/0.762 | 2/3 | yes | no/no |
| w04 | standalone | 14:37.0-15:54.6 | 73s | 5 | 0.905 / 0.837 | -2.3 | +0.0 | yes / yes | no | 1.003 | 0.961/1.0 | 1/1 | yes | no/no |
| w06 | promo | 11:48.9-12:38.2 | 49s | MISSED | no overlap | | | | | | | | | |
| w07 | standalone | 5:28.0-5:58.0 | 30s | 4 | 1.0 / 0.743 | -6.1 | +4.2 | yes / yes | no | 1.345 | 0.98/1.0 | 0/0 | yes | no/no |
| w08 | promo | 17:06.8-18:24.0 | 67s | 6 | 1.0 / 0.814 | -0.1 | -4.5 | yes / no | no | 1.081 | 0.892/1.0 | 0/2 | yes | no/no |
| w09 | standalone | 9:57.9-11:17.6 | 72s | MISSED | no overlap | | | | | | | | | |

Missed GT clips:
- w06 (promo, 11:48.9-12:38.2): వంట రాదని చెప్పేలోపే... 'Let's make our marriage life tasty' | నేను ఒక విషయం చెప్పాలి మీకు అంతకన్నా ముందు నేను మీకు ఒక విషయం చెప్పాలి. ...
- w09 (standalone, 9:57.9-11:17.6): Hostel food బాధ ప్రతి అబ్బాయికీ తెలుసు | food made with love | ఏరా ఆ అమ్మాయి అంత నచ్చిందా? నీలా నువ్వే మురిసిపోతున్నావ్. అమ్మాయి చాలా బాగుందిరా. కానీ ...

Setup dropped (proposal starts later and loses GT-kept lines):
- w02 vs rank 3: starts +3.5s, loses 8 words: వాడు marriage గురించి ఎలా explain చేసాడో తెలుసా? ఆ
- w03 vs rank 1: starts +33.9s, loses 19 words: మీకు ఇది ఎన్నో పెళ్లిచూపులు మొదటి పెళ్లిచూపులు కానీ నాకిది ఐదో పెళ్లిచూపులు ...

### Proposals

| rank | status | src | len | purpose | cold | lead-in | cuts | hold | fails | warns | excerpt |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | matched (w03) | 7:52.1-9:46.1 | 59s | standalone | no | 0.3 | 2 | 5.008 | - | - | నేను పొట్టిగా ఉంటాను కదా ఎవ్వరూ ఇష్టపడరు. అందుకే అందరూ reject చేశారు. మీకు ఇలాంటివి ... |
| 2 | matched (w01) | 3:31.0-5:16.7 | 69s | standalone | yes | 0.02 | 1 | 3.182 | - | - | తుమ్ము మొహం దానా! మీకు వంట వచ్చా? చచ్చింది గొర్ర. ఆహా! ఆ వచ్చు బాబు. ... |
| 3 | matched (w02) | 12:49.1-13:47.5 | 58s | standalone | no | 0.3 | 0 | 0.749 | - | - | ఈ రోజుల్లో marriage అనేది full meals లాంటిది. starting లో hot and exciting ... |
| 4 | matched (w07) | 5:21.9-6:02.2 | 40s | standalone | no | 0.06 | 0 | 5.003 | flash | - | నూనెయ్యవే! పోసేయ్, పోసేయ్, పోసేయ్. అది నూనె అనుకుంటున్నావా, బండిలో పోసే petrol అనుకుంటున్నావా, అంత ... |
| 5 | matched (w04) | 14:34.7-15:54.6 | 74s | standalone | no | 0.298 | 1 | 3.614 | - | - | అయ్యో అయినా మిమ్మల్ని అడుగుతున్నా ఏంటి? మీరు బయట food తినరు కదా. మీకు అన్ని ... |
| 6 | matched (w08) | 17:06.7-18:19.5 | 73s | promo | no | 0.3 | 0 | 3.091 | - | - | అత్తమ్మ మీరు మీ కూతుర్ని miss అవుతున్నారంటే చెప్పండి. ఇక్కడే వదిలేసి వెళ్తాను. అలా ఏం ... |

Rule failures (proposals):
- rank 4 flash: seg0 starts 0.20s before the shot change at 322.12s

GT sanity (rule checks on the approved GT clips): 8/8 (100%) pass.
- w03 mid_line [warn]: seg0 trims line at 'మీకు' | 'ఆ'

## QWIvgoTQGKk

Shots: `runs\video\QWIvgoTQGKk\shots.json`. Opening zone: lead 0:00.0-2:13.6; story starts 2:14.1. Recap check vs j14Om1SaFYE. Sponsor: none detected. Credits: heuristic (evenly timed shot cards from 1257.5s; speechless tail 59s).

### GT clips

| GT | purpose | src | len | match rank | cov / IoU | dstart | dend | payoff / hold | drops setup | len ratio | word P/R | cuts P/GT | same purpose | cold P/GT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| w01 | standalone | 15:48.6-17:01.0 | 72s | 4 | 0.884 / 0.887 | +8.1 | -0.0 | yes / yes | yes: 8 words | 0.887 | 1.0/0.855 | 0/0 | yes | no/no |
| w02 | promo | 18:50.5-20:57.5 | 89s | 6 | 1.0 / 0.865 | +0.0 | -2.8 | yes / no | no | 1.063 | 0.981/1.0 | 1/1 | yes | no/yes |
| w03 | standalone | 10:51.1-11:09.5 | 18s | MISSED | no overlap | | | | | | | | | |
| w04 | promo | 6:05.9-7:21.1 | 74s | MISSED | nearest: rank 7 cov 0.398 IoU 0.384 | | | | | | | | | |
| w05 | standalone | 3:41.4-4:20.8 | 39s | 2 | 0.972 / 0.912 | +0.0 | +2.6 | yes / yes | no | 1.038 | 0.875/1.0 | 0/0 | yes | yes/no |
| w06 | standalone | 11:55.8-13:22.5 | 73s | 1 | 1.0 / 0.752 | -5.6 | -3.4 | yes / no | no | 1.22 | 0.771/1.0 | 0/1 | yes | no/no |
| w07 | standalone | 2:23.2-3:16.5 | 53s | MISSED | no overlap | | | | | | | | | |
| w08 | standalone | 13:28.0-14:42.5 | 74s | 5 | 0.506 / 0.333 | +34.6 | +45.1 | yes / yes | yes: 38 words | 1.141 | 0.486/0.573 | 0/0 | yes | no/no |
| w09 | standalone | 7:25.7-8:32.2 | 66s | 3 | 0.985 / 0.953 | +1.2 | +2.0 | yes / yes | yes: 1 words | 1.012 | 1.0/0.993 | 0/0 | yes | no/no |
| w10 | standalone | 4:49.5-5:57.5 | 68s | MISSED | no overlap | | | | | | | | | |
| w11 | standalone | 17:27.3-18:43.2 | 61s | MISSED | no overlap | | | | | | | | | |
| w12 | standalone | 9:07.0-9:49.5 | 42s | MISSED | no overlap | | | | | | | | | |

Missed GT clips:
- w03 (standalone, 10:51.1-11:09.5): అమ్మ చేతి వంటలా ఉంది… 'మా అమ్మ చెయ్యలేదు ఒట్టు!' | అమ్మ చేతి వంటలా ఉంది. హా మా అమ్మ చెయ్యలేదు నిజంగా మా అమ్మ చెయ్యలేదు ...
- w04 (promo, 6:05.9-7:21.1): అబద్ధం అంటేనే fire అయిపోతాడు… అమృత పరిస్థితి ఏంటి? | అబద్ధాలు చెప్పి పెళ్లి చేసుకోవడం correct ఏనా? ఆ మావోడు food విషయంలో చాలా particular ...
- w07 (standalone, 2:23.2-3:16.5): నాకు చాయ్ పెట్టడానికే కదా లేచావ్? First day నే దొరికిపోయిందా! | first day నే దొరికిపోయేలా ఉన్నావు కదా. నేనెందుకింత తొందరగా లేచానో నాకు తెలియదు గానీ, ...
- w10 (standalone, 4:49.5-5:57.5): Dinner ఏ కాదు carriage కూడా తనే పెడుతుంది! అమ్మ హామీలు | నాకోసం ఏం వండవు. ఆ ఆ తోటకూర పప్పు ఆవకాయ ఊ ఆ ఇది కూడా ...
- w11 (standalone, 17:27.3-18:43.2): First easy వంట try చేద్దాం… popcorn I am coming! | అమ్మే! ప్రతిసారి వంటల కోసం అమ్మనేమడుగుతాం. మనం కూడా try చేద్దాం. First easy with ...
- w12 (standalone, 9:07.0-9:49.5): Nail polish నేను వెయ్యనా? | Husband goals | nail polish వేస్తున్నావా? ఉమ్ సరే నేను వెయ్యనా? వద్దు వద్దు పర్లేదు వద్దు. అంత ...

Setup dropped (proposal starts later and loses GT-kept lines):
- w01 vs rank 4: starts +8.1s, loses 8 words: దేనికో waiting. అబ్బచ్చా! అప్పుడే మర్చిపోయావా? ఆ, ఏం మర్చిపోవడం?
- w08 vs rank 5: starts +34.6s, loses 38 words: ఆహ్, రేపు morning tiffin and lunch ఏం చెయ్యాలి? ఇప్పుడెందుకు అడుగుతున్నావ్? ...
- w09 vs rank 3: starts +1.2s, loses 1 words: ఏయ్

### Proposals

| rank | status | src | len | purpose | cold | lead-in | cuts | hold | fails | warns | excerpt |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | matched (w06) | 11:50.2-13:19.1 | 89s | standalone | no | 0.3 | 0 | 0.254 | - | - | నేను కొన్ని most important topic మాట్లాడాలనుకుంటున్నాను అదే మన శోభనం గురించి మన first ... |
| 2 | matched (w05) | 3:41.4-4:23.3 | 41s | standalone | yes | 0.12 | 0 | 0.108 | flash | - | అసలు చాయ్ అంటే ఏమనుకుంటున్నారు. అయినా నీకు చాయ్ గోలేంట్రా? ఏంటి చాయ్ గోలా It's ... |
| 3 | matched (w09) | 7:26.9-8:34.2 | 67s | standalone | no | 0.123 | 0 | 2.262 | - | - | ఏంటి matter? Matter ఏంటి? అదే night బొమ్మ blockbuster ఆ? అంత ఊహించేసుకోకు first ... |
| 4 | matched (w01) | 15:56.8-17:01.0 | 64s | standalone | no | 0.3 | 0 | 3.729 | - | - | Night escape అయినట్టు ఇప్పుడు escape అవ్వలేవులే. అర్థమైంది. ఆ అమ్ములు. ఉ. ఏంటమ్మా ఇది? ... |
| 5 | matched (w08) | 14:02.6-15:27.6 | 85s | standalone | no | 0.02 | 0 | 1.994 | - | - | Sorry నీకు చెప్పడం మర్చిపోయాను. నాకు egg దోశ అంటే ఇష్టం. నాకోసం ఒక్క egg ... |
| 6 | matched (w02) | 18:50.5-20:54.6 | 95s | promo | no | 0.166 | 1 | 2.0 | - | - | బయట climate అదిరిపోయింది తెలుసా అందుకే snacks గా మిర్చి బజ్జి తినడానికి వచ్చాను. ఆ ... |
| 7 | partial (w04) | 6:45.6-7:14.1 | 28s | standalone | no | 0.3 | 0 | 2.098 | - | - | అబద్ధాలు చెప్పి పెళ్లి చేసుకోవడం correct ఏనా? ఊ ఊ వెయ్యి అబద్ధాలు ఆడి అయినా ... |

New moments (outside the GT, NOT wrong; need the founder's rating):
- rank 7 [partial w04] 6:45.6-7:14.1 28s standalone: అబద్ధాలు చెప్పి పెళ్లి చేసుకుంటే ఇదే గోల | అబద్ధాలు చెప్పి పెళ్లి చేసుకోవడం correct ఏనా? ఊ ఊ వెయ్యి అబద్ధాలు ఆడి అయినా ...

Rule failures (proposals):
- rank 2 flash: seg1 ends 0.28s after the shot change at 226.17s

GT sanity (rule checks on the approved GT clips): 10/12 (83%) pass.
- w04 lead_in [fail]: 0.44s before the first word (silence)
- w05 reaction_hold [fail]: ends 0.39s after the last word, not on a shot change
- w06 mid_line [warn]: seg0 trims line at 'మన' | 'గురించి'
- w07 lead_in [warn]: 0.34s before the first word (0.3s of it music)
- w11 mid_word [warn]: seg1 start 1047.30s inside 'చెప్తాను.' (1041.76-1047.50, stretched Scribe timing)
- w12 mid_word [warn]: seg0 start 547.00s inside 'ఉమ్' (544.56-547.16, stretched Scribe timing)
- w12 mid_line [warn]: seg0 trims line at 'nail' | 'ఉమ్'
