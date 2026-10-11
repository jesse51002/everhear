# Routing on whole asks, scored

The 2026-10-08 lines with each split ask merged into one input (whole_asks_build.py), routed by Jev today and the five trees with Jev's old state and its new one (whole_asks_bench.py, state_v2.py).

770 inputs from 843 lines: 53 merged inputs hold 126 lines. Labels: 236 request, 7 answer_to_ai, 28 talk_to_ai, 9 context, 490 noise; 243 asks. Second half: 387 inputs, 111 asks.

Respond B's hand-up rate by label, on the lines it ran: request 70%, answer_to_ai 15%, talk_to_ai 17%, context 10%, noise 6%.

## Summary

*Asks* are `request` and `answer_to_ai` inputs. *Lost* is chat / context / dropped: answered in chat by Respond B, kept as context, dropped (+ to Respond on lines outside B's set). *Talk and noise to the brain* is `talk_to_ai` and `noise` inputs sent straight to the brain. *Noise to Respond*: noise inputs sent to Respond, and how many of them B answers in chat. Cost is a 2.5 h session. Latency is a Jev call's, median / p90.

| Routing | State | Asks reach the brain | direct + via B | Lost: chat / context / dropped | Talk and noise to the brain | Noise to Respond (answered in chat) | Inputs where they belong | Cost a session | Latency | Second half: asks reach | Second half: talk and noise to the brain |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Jev today | old | **235 of 243 (97%)** | 232 + 3 | 6 / 1 / 0, +1 | 34 of 518 (7%) | 324 (308) | 381 (49%) | **$3.28** | 226 / 255 ms | 108 of 111 (97%) | 24 of 272 (9%) |
| Jev today | new | **236 of 243 (97%)** | 235 + 1 | 3 / 1 / 2, +1 | 46 of 518 (9%) | 253 (241) | 420 (55%) | **$3.21** | 224 / 259 ms | 108 of 111 (97%) | 29 of 272 (11%) |
| A (v2) | old | **229 of 243 (94%)** | 228 + 1 | 4 / 6 / 4 | 40 of 518 (8%) | 41 (39) | 587 (76%) | **$2.68** | 233 / 264 ms | 105 of 111 (95%) | 25 of 272 (9%) |
| A (v2) | new | **230 of 243 (95%)** | 227 + 3 | 3 / 9 / 1 | 58 of 518 (11%) | 49 (45) | 538 (70%) | **$2.88** | 222 / 247 ms | 106 of 111 (95%) | 32 of 272 (12%) |
| B2 | old | **228 of 243 (94%)** | 226 + 2 | 5 / 9 / 1 | 30 of 518 (6%) | 60 (53) | 479 (62%) | **$2.67** | 222 / 248 ms | 104 of 111 (94%) | 18 of 272 (7%) |
| B2 | new | **230 of 243 (95%)** | 228 + 2 | 4 / 7 / 1, +1 | 24 of 518 (5%) | 52 (46) | 492 (64%) | **$2.62** | 227 / 261 ms | 105 of 111 (95%) | 13 of 272 (5%) |
| C1 | old | **227 of 243 (93%)** | 221 + 6 | 9 / 2 / 4, +1 | 16 of 518 (3%) | 89 (82) | 589 (76%) | **$2.63** | 222 / 251 ms | 104 of 111 (94%) | 11 of 272 (4%) |
| C1 | new | **229 of 243 (94%)** | 224 + 5 | 8 / 3 / 2, +1 | 18 of 518 (3%) | 96 (88) | 560 (73%) | **$2.68** | 222 / 253 ms | 104 of 111 (94%) | 12 of 272 (4%) |
| D2 | old | **219 of 243 (90%)** | 208 + 11 | 8 / 2 / 13, +1 | 18 of 518 (3%) | 61 (54) | 637 (83%) | **$2.52** | 220 / 245 ms | 98 of 111 (88%) | 15 of 272 (6%) |
| D2 | new | **221 of 243 (91%)** | 210 + 11 | 5 / 9 / 7, +1 | 18 of 518 (3%) | 52 (46) | 594 (77%) | **$2.50** | 228 / 261 ms | 100 of 111 (90%) | 11 of 272 (4%) |
| E1 rules r2 | old | **217 of 243 (89%)** | 202 + 15 | 6 / 16 / 3, +1 | 21 of 518 (4%) | 48 (43) | 492 (64%) | **$2.49** | 221 / 247 ms | 99 of 111 (89%) | 13 of 272 (5%) |
| E1 rules r2 | new | **217 of 243 (89%)** | 203 + 14 | 4 / 17 / 4, +1 | 23 of 518 (4%) | 58 (51) | 455 (59%) | **$2.54** | 221 / 253 ms | 98 of 111 (88%) | 14 of 272 (5%) |

The held-out second half alone (the wording of the grouping and the trees was set on the first; cost is the half's):

| Routing | State | Asks reach the brain | direct + via B | Lost: chat / context / dropped | Talk and noise to the brain | Noise to Respond (answered in chat) | Inputs where they belong | Cost of the half | Latency |
|---|---|---|---|---|---|---|---|---|---|
| Jev today | old | **108 of 111 (97%)** | 106 + 2 | 2 / 1 / 0 | 24 of 272 (9%) | 167 (159) | 182 (47%) | $1.64 | 226 / 254 ms |
| Jev today | new | **108 of 111 (97%)** | 108 + 0 | 1 / 0 / 2 | 29 of 272 (11%) | 122 (116) | 209 (54%) | $1.58 | 223 / 257 ms |
| A (v2) | old | **105 of 111 (95%)** | 104 + 1 | 0 / 4 / 2 | 25 of 272 (9%) | 22 (20) | 289 (75%) | $1.31 | 233 / 270 ms |
| A (v2) | new | **106 of 111 (95%)** | 104 + 2 | 0 / 5 / 0 | 32 of 272 (12%) | 19 (17) | 264 (68%) | $1.38 | 221 / 245 ms |
| B2 | old | **104 of 111 (94%)** | 102 + 2 | 1 / 5 / 1 | 18 of 272 (7%) | 31 (28) | 228 (59%) | $1.29 | 222 / 245 ms |
| B2 | new | **105 of 111 (95%)** | 103 + 2 | 1 / 5 / 0 | 13 of 272 (5%) | 27 (24) | 234 (60%) | $1.25 | 227 / 262 ms |
| C1 | old | **104 of 111 (94%)** | 100 + 4 | 5 / 0 / 2 | 11 of 272 (4%) | 44 (41) | 291 (75%) | $1.27 | 224 / 252 ms |
| C1 | new | **104 of 111 (94%)** | 101 + 3 | 5 / 1 / 1 | 12 of 272 (4%) | 47 (44) | 276 (71%) | $1.27 | 220 / 247 ms |
| D2 | old | **98 of 111 (88%)** | 93 + 5 | 3 / 2 / 8 | 15 of 272 (6%) | 34 (31) | 311 (80%) | $1.22 | 219 / 242 ms |
| D2 | new | **100 of 111 (90%)** | 95 + 5 | 1 / 5 / 5 | 11 of 272 (4%) | 30 (27) | 292 (75%) | $1.19 | 226 / 262 ms |
| E1 rules r2 | old | **99 of 111 (89%)** | 90 + 9 | 1 / 9 / 2 | 13 of 272 (5%) | 27 (25) | 249 (64%) | $1.19 | 223 / 249 ms |
| E1 rules r2 | new | **98 of 111 (88%)** | 91 + 7 | 1 / 10 / 2 | 14 of 272 (5%) | 31 (29) | 229 (59%) | $1.20 | 220 / 247 ms |

The merged inputs alone (the asks among them, where the old state's answers are new calls too):

| Routing | Old state: merged asks reach | New state: merged asks reach |
|---|---|---|
| Jev today | 49 of 50 | 48 of 50 |
| A (v2) | 47 of 50 | 47 of 50 |
| B2 | 47 of 50 | 47 of 50 |
| C1 | 48 of 50 | 48 of 50 |
| D2 | 46 of 50 | 46 of 50 |
| E1 rules r2 | 44 of 50 | 44 of 50 |

## What the new state moved

For each routing, the inputs whose outcome changed from the old state to the new one. *Asks gained* reach the brain only in the new state, *asks lost* only in the old; the noise columns count noise inputs sent to the brain or to Respond in one state and not the other.

| Routing | Inputs moved | Asks gained | Asks lost | Noise off the brain / onto it | Noise off Respond / onto it | Talk to the AI off Respond / onto it |
|---|---|---|---|---|---|---|
| Jev today | 109 | 3 | 2 | 3 / 12 | 78 / 7 | 4 / 1 |
| A (v2) | 97 | 3 | 2 | 2 / 19 | 10 / 18 | 1 / 1 |
| B2 | 56 | 2 | 0 | 8 / 1 | 12 / 4 | 1 / 1 |
| C1 | 84 | 3 | 1 | 1 / 2 | 13 / 20 | 1 / 2 |
| D2 | 114 | 5 | 3 | 7 / 6 | 18 / 9 | 1 / 1 |
| E1 rules r2 | 67 | 2 | 2 | 0 / 2 | 0 / 10 | 0 / 3 |

## Each routing

Rows are the inputs' labels, columns where the routing sent them, with the row's share; the cell where the label belongs is in bold. All inputs.


### Jev today, old state

<table>
<caption>Jev today, old state: label (rows) by outcome (columns)</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>request</th><td style="background:rgba(37, 99, 235, 0.89);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">226<br><small>96%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">9<br><small>4%</small></td><td style="background:rgba(37, 99, 235, 0.08);color:inherit;text-align:right">1<br><small>0%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">236</td></tr>
<tr><th>answer_to_ai</th><td style="background:rgba(37, 99, 235, 0.81);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">6<br><small>86%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">7</td></tr>
<tr><th>talk_to_ai</th><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">2<br><small>7%</small></td><td style="background:rgba(37, 99, 235, 0.87);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">26<br><small>93%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">28</td></tr>
<tr><th>context</th><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">1<br><small>11%</small></td><td style="background:rgba(37, 99, 235, 0.84);color:#fff;text-align:right">8<br><small>89%</small></td><td style="background:transparent;color:inherit;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">0<br><small>0%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">9</td></tr>
<tr><th>noise</th><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">32<br><small>7%</small></td><td style="background:rgba(37, 99, 235, 0.64);color:#fff;text-align:right">324<br><small>66%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">11<br><small>2%</small></td><td style="background:rgba(37, 99, 235, 0.29);color:inherit;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">123<br><small>25%</small></td><td style="text-align:right">490</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **226 (96%)** | 9 (4%) | 1 (0%) | 0 (0%) | 236 |
| answer_to_ai | **6 (86%)** | 1 (14%) | 0 (0%) | 0 (0%) | 7 |
| talk_to_ai | 2 (7%) | **26 (93%)** | 0 (0%) | 0 (0%) | 28 |
| context | 1 (11%) | 8 (89%) | **0 (0%)** | 0 (0%) | 9 |
| noise | 32 (7%) | 324 (66%) | 11 (2%) | **123 (25%)** | 490 |

- **Asks reaching the brain: 235 of 243 (96.7%)**: 232 straight, 3 through Respond B's hand-up.
- **Asks lost: 8**: 6 answered in chat by Respond, 1 kept as context, 0 dropped, 1 to Respond outside B's set.
- **Talk and noise sent to the brain: 34 of 518 (6.6%)** straight; 350 to Respond, of which B handed up 11.
- **Noise to Respond: 324**, about 308 answered in chat.
- **Cost: $3.28 a session**: 267 brain calls straight, 368 Respond calls, of which 15 handed up by B and about 10.3 more outside B's set (292 brain calls $2.48, Respond $0.55), Jev 5,882,238 tokens $0.247.
- Latency: 226 ms median, 255 ms p90, over 770 Jev calls.

### Jev today, new state

<table>
<caption>Jev today, new state: label (rows) by outcome (columns)</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>request</th><td style="background:rgba(37, 99, 235, 0.91);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">230<br><small>97%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">4<br><small>2%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">2<br><small>1%</small></td><td style="text-align:right">236</td></tr>
<tr><th>answer_to_ai</th><td style="background:rgba(37, 99, 235, 0.69);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">5<br><small>71%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">7</td></tr>
<tr><th>talk_to_ai</th><td style="background:rgba(37, 99, 235, 0.23);color:inherit;text-align:right">5<br><small>18%</small></td><td style="background:rgba(37, 99, 235, 0.78);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">23<br><small>82%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">28</td></tr>
<tr><th>context</th><td style="background:rgba(37, 99, 235, 0.27);color:inherit;text-align:right">2<br><small>22%</small></td><td style="background:rgba(37, 99, 235, 0.46);color:inherit;text-align:right">4<br><small>44%</small></td><td style="background:rgba(37, 99, 235, 0.36);color:inherit;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">3<br><small>33%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">9</td></tr>
<tr><th>noise</th><td style="background:rgba(37, 99, 235, 0.15);color:inherit;text-align:right">41<br><small>8%</small></td><td style="background:rgba(37, 99, 235, 0.52);color:#fff;text-align:right">253<br><small>52%</small></td><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">37<br><small>8%</small></td><td style="background:rgba(37, 99, 235, 0.36);color:inherit;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">159<br><small>32%</small></td><td style="text-align:right">490</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **230 (97%)** | 4 (2%) | 0 (0%) | 2 (1%) | 236 |
| answer_to_ai | **5 (71%)** | 1 (14%) | 1 (14%) | 0 (0%) | 7 |
| talk_to_ai | 5 (18%) | **23 (82%)** | 0 (0%) | 0 (0%) | 28 |
| context | 2 (22%) | 4 (44%) | **3 (33%)** | 0 (0%) | 9 |
| noise | 41 (8%) | 253 (52%) | 37 (8%) | **159 (32%)** | 490 |

- **Asks reaching the brain: 236 of 243 (97.1%)**: 235 straight, 1 through Respond B's hand-up.
- **Asks lost: 7**: 3 answered in chat by Respond, 1 kept as context, 2 dropped, 1 to Respond outside B's set.
- **Talk and noise sent to the brain: 46 of 518 (8.9%)** straight; 276 to Respond, of which B handed up 8.
- **Noise to Respond: 253**, about 241 answered in chat.
- **Cost: $3.21 a session**: 283 brain calls straight, 285 Respond calls, of which 9 handed up by B and about 8.4 more outside B's set (300 brain calls $2.55, Respond $0.43), Jev 5,521,697 tokens $0.232.
- Latency: 224 ms median, 259 ms p90, over 770 Jev calls.

### A (v2), old state

<table>
<caption>A (v2), old state: label (rows) by outcome (columns)</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>request</th><td style="background:rgba(37, 99, 235, 0.88);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">223<br><small>94%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">4<br><small>2%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">6<br><small>3%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">3<br><small>1%</small></td><td style="text-align:right">236</td></tr>
<tr><th>answer_to_ai</th><td style="background:rgba(37, 99, 235, 0.69);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">5<br><small>71%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="text-align:right">7</td></tr>
<tr><th>talk_to_ai</th><td style="background:rgba(37, 99, 235, 0.26);color:inherit;text-align:right">6<br><small>21%</small></td><td style="background:rgba(37, 99, 235, 0.66);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">19<br><small>68%</small></td><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">2<br><small>7%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">1<br><small>4%</small></td><td style="text-align:right">28</td></tr>
<tr><th>context</th><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">1<br><small>11%</small></td><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">1<br><small>11%</small></td><td style="background:rgba(37, 99, 235, 0.74);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">7<br><small>78%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">9</td></tr>
<tr><th>noise</th><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">34<br><small>7%</small></td><td style="background:rgba(37, 99, 235, 0.15);color:inherit;text-align:right">41<br><small>8%</small></td><td style="background:rgba(37, 99, 235, 0.22);color:inherit;text-align:right">82<br><small>17%</small></td><td style="background:rgba(37, 99, 235, 0.66);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">333<br><small>68%</small></td><td style="text-align:right">490</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **223 (94%)** | 4 (2%) | 6 (3%) | 3 (1%) | 236 |
| answer_to_ai | **5 (71%)** | 1 (14%) | 0 (0%) | 1 (14%) | 7 |
| talk_to_ai | 6 (21%) | **19 (68%)** | 2 (7%) | 1 (4%) | 28 |
| context | 1 (11%) | 1 (11%) | **7 (78%)** | 0 (0%) | 9 |
| noise | 34 (7%) | 41 (8%) | 82 (17%) | **333 (68%)** | 490 |

- **Asks reaching the brain: 229 of 243 (94.2%)**: 228 straight, 1 through Respond B's hand-up.
- **Asks lost: 14**: 4 answered in chat by Respond, 6 kept as context, 4 dropped.
- **Talk and noise sent to the brain: 40 of 518 (7.7%)** straight; 60 to Respond, of which B handed up 3.
- **Noise to Respond: 41**, about 39 answered in chat.
- **Cost: $2.68 a session**: 269 brain calls straight, 66 Respond calls, of which 4 handed up by B and about 1.6 more outside B's set (275 brain calls $2.33, Respond $0.10), Jev 5,995,570 tokens $0.252.
- Latency: 233 ms median, 264 ms p90, over 770 Jev calls.

### A (v2), new state

<table>
<caption>A (v2), new state: label (rows) by outcome (columns)</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>request</th><td style="background:rgba(37, 99, 235, 0.88);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">221<br><small>94%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">6<br><small>3%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">9<br><small>4%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">236</td></tr>
<tr><th>answer_to_ai</th><td style="background:rgba(37, 99, 235, 0.81);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">6<br><small>86%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="text-align:right">7</td></tr>
<tr><th>talk_to_ai</th><td style="background:rgba(37, 99, 235, 0.29);color:inherit;text-align:right">7<br><small>25%</small></td><td style="background:rgba(37, 99, 235, 0.66);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">19<br><small>68%</small></td><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">2<br><small>7%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">28</td></tr>
<tr><th>context</th><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">1<br><small>11%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.84);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">8<br><small>89%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">9</td></tr>
<tr><th>noise</th><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">51<br><small>10%</small></td><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">49<br><small>10%</small></td><td style="background:rgba(37, 99, 235, 0.26);color:inherit;text-align:right">106<br><small>22%</small></td><td style="background:rgba(37, 99, 235, 0.57);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">284<br><small>58%</small></td><td style="text-align:right">490</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **221 (94%)** | 6 (3%) | 9 (4%) | 0 (0%) | 236 |
| answer_to_ai | **6 (86%)** | 0 (0%) | 0 (0%) | 1 (14%) | 7 |
| talk_to_ai | 7 (25%) | **19 (68%)** | 2 (7%) | 0 (0%) | 28 |
| context | 1 (11%) | 0 (0%) | **8 (89%)** | 0 (0%) | 9 |
| noise | 51 (10%) | 49 (10%) | 106 (22%) | **284 (58%)** | 490 |

- **Asks reaching the brain: 230 of 243 (94.7%)**: 227 straight, 3 through Respond B's hand-up.
- **Asks lost: 13**: 3 answered in chat by Respond, 9 kept as context, 1 dropped.
- **Talk and noise sent to the brain: 58 of 518 (11.2%)** straight; 68 to Respond, of which B handed up 5.
- **Noise to Respond: 49**, about 45 answered in chat.
- **Cost: $2.88 a session**: 286 brain calls straight, 74 Respond calls, of which 8 handed up by B and about 1.7 more outside B's set (296 brain calls $2.51, Respond $0.11), Jev 5,981,185 tokens $0.251.
- Latency: 222 ms median, 247 ms p90, over 770 Jev calls.

### B2, old state

<table>
<caption>B2, old state: label (rows) by outcome (columns)</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>request</th><td style="background:rgba(37, 99, 235, 0.88);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">221<br><small>94%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">6<br><small>3%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">8<br><small>3%</small></td><td style="background:rgba(37, 99, 235, 0.08);color:inherit;text-align:right">1<br><small>0%</small></td><td style="text-align:right">236</td></tr>
<tr><th>answer_to_ai</th><td style="background:rgba(37, 99, 235, 0.69);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">5<br><small>71%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">7</td></tr>
<tr><th>talk_to_ai</th><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.69);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">20<br><small>71%</small></td><td style="background:rgba(37, 99, 235, 0.29);color:inherit;text-align:right">7<br><small>25%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">1<br><small>4%</small></td><td style="text-align:right">28</td></tr>
<tr><th>context</th><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">1<br><small>11%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.84);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">8<br><small>89%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">9</td></tr>
<tr><th>noise</th><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">30<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.18);color:inherit;text-align:right">60<br><small>12%</small></td><td style="background:rgba(37, 99, 235, 0.38);color:inherit;text-align:right">175<br><small>36%</small></td><td style="background:rgba(37, 99, 235, 0.47);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">225<br><small>46%</small></td><td style="text-align:right">490</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **221 (94%)** | 6 (3%) | 8 (3%) | 1 (0%) | 236 |
| answer_to_ai | **5 (71%)** | 1 (14%) | 1 (14%) | 0 (0%) | 7 |
| talk_to_ai | 0 (0%) | **20 (71%)** | 7 (25%) | 1 (4%) | 28 |
| context | 1 (11%) | 0 (0%) | **8 (89%)** | 0 (0%) | 9 |
| noise | 30 (6%) | 60 (12%) | 175 (36%) | **225 (46%)** | 490 |

- **Asks reaching the brain: 228 of 243 (93.8%)**: 226 straight, 2 through Respond B's hand-up.
- **Asks lost: 15**: 5 answered in chat by Respond, 9 kept as context, 1 dropped.
- **Talk and noise sent to the brain: 30 of 518 (5.8%)** straight; 80 to Respond, of which B handed up 9.
- **Noise to Respond: 60**, about 53 answered in chat.
- **Cost: $2.67 a session**: 257 brain calls straight, 87 Respond calls, of which 11 handed up by B and about 1.7 more outside B's set (270 brain calls $2.29, Respond $0.13), Jev 5,805,293 tokens $0.244.
- Latency: 222 ms median, 248 ms p90, over 770 Jev calls.

### B2, new state

<table>
<caption>B2, new state: label (rows) by outcome (columns)</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>request</th><td style="background:rgba(37, 99, 235, 0.88);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">223<br><small>94%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">6<br><small>3%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">7<br><small>3%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">236</td></tr>
<tr><th>answer_to_ai</th><td style="background:rgba(37, 99, 235, 0.69);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">5<br><small>71%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="text-align:right">7</td></tr>
<tr><th>talk_to_ai</th><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">1<br><small>4%</small></td><td style="background:rgba(37, 99, 235, 0.69);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">20<br><small>71%</small></td><td style="background:rgba(37, 99, 235, 0.26);color:inherit;text-align:right">6<br><small>21%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">1<br><small>4%</small></td><td style="text-align:right">28</td></tr>
<tr><th>context</th><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">1<br><small>11%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.84);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">8<br><small>89%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">9</td></tr>
<tr><th>noise</th><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">23<br><small>5%</small></td><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">52<br><small>11%</small></td><td style="background:rgba(37, 99, 235, 0.39);color:inherit;text-align:right">179<br><small>37%</small></td><td style="background:rgba(37, 99, 235, 0.49);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">236<br><small>48%</small></td><td style="text-align:right">490</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **223 (94%)** | 6 (3%) | 7 (3%) | 0 (0%) | 236 |
| answer_to_ai | **5 (71%)** | 1 (14%) | 0 (0%) | 1 (14%) | 7 |
| talk_to_ai | 1 (4%) | **20 (71%)** | 6 (21%) | 1 (4%) | 28 |
| context | 1 (11%) | 0 (0%) | **8 (89%)** | 0 (0%) | 9 |
| noise | 23 (5%) | 52 (11%) | 179 (37%) | **236 (48%)** | 490 |

- **Asks reaching the brain: 230 of 243 (94.7%)**: 228 straight, 2 through Respond B's hand-up.
- **Asks lost: 13**: 4 answered in chat by Respond, 7 kept as context, 1 dropped, 1 to Respond outside B's set.
- **Talk and noise sent to the brain: 24 of 518 (4.6%)** straight; 72 to Respond, of which B handed up 8.
- **Noise to Respond: 52**, about 46 answered in chat.
- **Cost: $2.62 a session**: 253 brain calls straight, 79 Respond calls, of which 10 handed up by B and about 2.2 more outside B's set (265 brain calls $2.25, Respond $0.12), Jev 5,809,191 tokens $0.244.
- Latency: 227 ms median, 261 ms p90, over 770 Jev calls.

### C1, old state

<table>
<caption>C1, old state: label (rows) by outcome (columns)</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>request</th><td style="background:rgba(37, 99, 235, 0.86);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">217<br><small>92%</small></td><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">14<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">2<br><small>1%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">3<br><small>1%</small></td><td style="text-align:right">236</td></tr>
<tr><th>answer_to_ai</th><td style="background:rgba(37, 99, 235, 0.57);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">4<br><small>57%</small></td><td style="background:rgba(37, 99, 235, 0.32);color:inherit;text-align:right">2<br><small>29%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="text-align:right">7</td></tr>
<tr><th>talk_to_ai</th><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">1<br><small>4%</small></td><td style="background:rgba(37, 99, 235, 0.84);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">25<br><small>89%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">1<br><small>4%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">1<br><small>4%</small></td><td style="text-align:right">28</td></tr>
<tr><th>context</th><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">1<br><small>11%</small></td><td style="background:rgba(37, 99, 235, 0.36);color:inherit;text-align:right">3<br><small>33%</small></td><td style="background:rgba(37, 99, 235, 0.46);color:inherit;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">4<br><small>44%</small></td><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">1<br><small>11%</small></td><td style="text-align:right">9</td></tr>
<tr><th>noise</th><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">15<br><small>3%</small></td><td style="background:rgba(37, 99, 235, 0.23);color:inherit;text-align:right">89<br><small>18%</small></td><td style="background:rgba(37, 99, 235, 0.16);color:inherit;text-align:right">47<br><small>10%</small></td><td style="background:rgba(37, 99, 235, 0.67);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">339<br><small>69%</small></td><td style="text-align:right">490</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **217 (92%)** | 14 (6%) | 2 (1%) | 3 (1%) | 236 |
| answer_to_ai | **4 (57%)** | 2 (29%) | 0 (0%) | 1 (14%) | 7 |
| talk_to_ai | 1 (4%) | **25 (89%)** | 1 (4%) | 1 (4%) | 28 |
| context | 1 (11%) | 3 (33%) | **4 (44%)** | 1 (11%) | 9 |
| noise | 15 (3%) | 89 (18%) | 47 (10%) | **339 (69%)** | 490 |

- **Asks reaching the brain: 227 of 243 (93.4%)**: 221 straight, 6 through Respond B's hand-up.
- **Asks lost: 16**: 9 answered in chat by Respond, 2 kept as context, 4 dropped, 1 to Respond outside B's set.
- **Talk and noise sent to the brain: 16 of 518 (3.1%)** straight; 114 to Respond, of which B handed up 9.
- **Noise to Respond: 89**, about 82 answered in chat.
- **Cost: $2.63 a session**: 238 brain calls straight, 133 Respond calls, of which 15 handed up by B and about 3.1 more outside B's set (256 brain calls $2.18, Respond $0.20), Jev 6,038,401 tokens $0.254.
- Latency: 222 ms median, 251 ms p90, over 770 Jev calls.

### C1, new state

<table>
<caption>C1, new state: label (rows) by outcome (columns)</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>request</th><td style="background:rgba(37, 99, 235, 0.87);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">219<br><small>93%</small></td><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">13<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">3<br><small>1%</small></td><td style="background:rgba(37, 99, 235, 0.08);color:inherit;text-align:right">1<br><small>0%</small></td><td style="text-align:right">236</td></tr>
<tr><th>answer_to_ai</th><td style="background:rgba(37, 99, 235, 0.69);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">5<br><small>71%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="text-align:right">7</td></tr>
<tr><th>talk_to_ai</th><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">2<br><small>7%</small></td><td style="background:rgba(37, 99, 235, 0.87);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">26<br><small>93%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">28</td></tr>
<tr><th>context</th><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.36);color:inherit;text-align:right">3<br><small>33%</small></td><td style="background:rgba(37, 99, 235, 0.65);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">6<br><small>67%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">9</td></tr>
<tr><th>noise</th><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">16<br><small>3%</small></td><td style="background:rgba(37, 99, 235, 0.25);color:inherit;text-align:right">96<br><small>20%</small></td><td style="background:rgba(37, 99, 235, 0.21);color:inherit;text-align:right">74<br><small>15%</small></td><td style="background:rgba(37, 99, 235, 0.61);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">304<br><small>62%</small></td><td style="text-align:right">490</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **219 (93%)** | 13 (6%) | 3 (1%) | 1 (0%) | 236 |
| answer_to_ai | **5 (71%)** | 1 (14%) | 0 (0%) | 1 (14%) | 7 |
| talk_to_ai | 2 (7%) | **26 (93%)** | 0 (0%) | 0 (0%) | 28 |
| context | 0 (0%) | 3 (33%) | **6 (67%)** | 0 (0%) | 9 |
| noise | 16 (3%) | 96 (20%) | 74 (15%) | **304 (62%)** | 490 |

- **Asks reaching the brain: 229 of 243 (94.2%)**: 224 straight, 5 through Respond B's hand-up.
- **Asks lost: 14**: 8 answered in chat by Respond, 3 kept as context, 2 dropped, 1 to Respond outside B's set.
- **Talk and noise sent to the brain: 18 of 518 (3.5%)** straight; 122 to Respond, of which B handed up 10.
- **Noise to Respond: 96**, about 88 answered in chat.
- **Cost: $2.68 a session**: 242 brain calls straight, 139 Respond calls, of which 15 handed up by B and about 3.6 more outside B's set (261 brain calls $2.21, Respond $0.21), Jev 6,042,299 tokens $0.254.
- Latency: 222 ms median, 253 ms p90, over 770 Jev calls.

### D2, old state

<table>
<caption>D2, old state: label (rows) by outcome (columns)</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>request</th><td style="background:rgba(37, 99, 235, 0.82);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">205<br><small>87%</small></td><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">18<br><small>8%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">2<br><small>1%</small></td><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">11<br><small>5%</small></td><td style="text-align:right">236</td></tr>
<tr><th>answer_to_ai</th><td style="background:rgba(37, 99, 235, 0.44);color:inherit;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">3<br><small>43%</small></td><td style="background:rgba(37, 99, 235, 0.32);color:inherit;text-align:right">2<br><small>29%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.32);color:inherit;text-align:right">2<br><small>29%</small></td><td style="text-align:right">7</td></tr>
<tr><th>talk_to_ai</th><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.78);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">23<br><small>82%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.23);color:inherit;text-align:right">5<br><small>18%</small></td><td style="text-align:right">28</td></tr>
<tr><th>context</th><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">1<br><small>11%</small></td><td style="background:rgba(37, 99, 235, 0.27);color:inherit;text-align:right">2<br><small>22%</small></td><td style="background:rgba(37, 99, 235, 0.46);color:inherit;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">4<br><small>44%</small></td><td style="background:rgba(37, 99, 235, 0.27);color:inherit;text-align:right">2<br><small>22%</small></td><td style="text-align:right">9</td></tr>
<tr><th>noise</th><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">18<br><small>4%</small></td><td style="background:rgba(37, 99, 235, 0.19);color:inherit;text-align:right">61<br><small>12%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">9<br><small>2%</small></td><td style="background:rgba(37, 99, 235, 0.78);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">402<br><small>82%</small></td><td style="text-align:right">490</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **205 (87%)** | 18 (8%) | 2 (1%) | 11 (5%) | 236 |
| answer_to_ai | **3 (43%)** | 2 (29%) | 0 (0%) | 2 (29%) | 7 |
| talk_to_ai | 0 (0%) | **23 (82%)** | 0 (0%) | 5 (18%) | 28 |
| context | 1 (11%) | 2 (22%) | **4 (44%)** | 2 (22%) | 9 |
| noise | 18 (4%) | 61 (12%) | 9 (2%) | **402 (82%)** | 490 |

- **Asks reaching the brain: 219 of 243 (90.1%)**: 208 straight, 11 through Respond B's hand-up.
- **Asks lost: 24**: 8 answered in chat by Respond, 2 kept as context, 13 dropped, 1 to Respond outside B's set.
- **Talk and noise sent to the brain: 18 of 518 (3.5%)** straight; 84 to Respond, of which B handed up 10.
- **Noise to Respond: 61**, about 54 answered in chat.
- **Cost: $2.52 a session**: 227 brain calls straight, 106 Respond calls, of which 21 handed up by B and about 2.2 more outside B's set (250 brain calls $2.13, Respond $0.16), Jev 5,680,553 tokens $0.239.
- Latency: 220 ms median, 245 ms p90, over 770 Jev calls.

### D2, new state

<table>
<caption>D2, new state: label (rows) by outcome (columns)</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>request</th><td style="background:rgba(37, 99, 235, 0.82);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">206<br><small>87%</small></td><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">16<br><small>7%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">9<br><small>4%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">5<br><small>2%</small></td><td style="text-align:right">236</td></tr>
<tr><th>answer_to_ai</th><td style="background:rgba(37, 99, 235, 0.57);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">4<br><small>57%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.32);color:inherit;text-align:right">2<br><small>29%</small></td><td style="text-align:right">7</td></tr>
<tr><th>talk_to_ai</th><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">1<br><small>4%</small></td><td style="background:rgba(37, 99, 235, 0.78);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">23<br><small>82%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">1<br><small>4%</small></td><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">3<br><small>11%</small></td><td style="text-align:right">28</td></tr>
<tr><th>context</th><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.93);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">9<br><small>100%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">9</td></tr>
<tr><th>noise</th><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">17<br><small>3%</small></td><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">52<br><small>11%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">69<br><small>14%</small></td><td style="background:rgba(37, 99, 235, 0.69);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">352<br><small>72%</small></td><td style="text-align:right">490</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **206 (87%)** | 16 (7%) | 9 (4%) | 5 (2%) | 236 |
| answer_to_ai | **4 (57%)** | 1 (14%) | 0 (0%) | 2 (29%) | 7 |
| talk_to_ai | 1 (4%) | **23 (82%)** | 1 (4%) | 3 (11%) | 28 |
| context | 0 (0%) | 0 (0%) | **9 (100%)** | 0 (0%) | 9 |
| noise | 17 (3%) | 52 (11%) | 69 (14%) | **352 (72%)** | 490 |

- **Asks reaching the brain: 221 of 243 (90.9%)**: 210 straight, 11 through Respond B's hand-up.
- **Asks lost: 22**: 5 answered in chat by Respond, 9 kept as context, 7 dropped, 1 to Respond outside B's set.
- **Talk and noise sent to the brain: 18 of 518 (3.5%)** straight; 75 to Respond, of which B handed up 9.
- **Noise to Respond: 52**, about 46 answered in chat.
- **Cost: $2.50 a session**: 228 brain calls straight, 92 Respond calls, of which 20 handed up by B and about 1.9 more outside B's set (250 brain calls $2.12, Respond $0.14), Jev 5,684,451 tokens $0.239.
- Latency: 228 ms median, 261 ms p90, over 770 Jev calls.

### E1 rules r2, old state

<table>
<caption>E1 rules r2, old state: label (rows) by outcome (columns)</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>request</th><td style="background:rgba(37, 99, 235, 0.80);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">200<br><small>85%</small></td><td style="background:rgba(37, 99, 235, 0.16);color:inherit;text-align:right">21<br><small>9%</small></td><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">13<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">2<br><small>1%</small></td><td style="text-align:right">236</td></tr>
<tr><th>answer_to_ai</th><td style="background:rgba(37, 99, 235, 0.32);color:inherit;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">2<br><small>29%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="background:rgba(37, 99, 235, 0.44);color:inherit;text-align:right">3<br><small>43%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="text-align:right">7</td></tr>
<tr><th>talk_to_ai</th><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.69);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">20<br><small>71%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">4<br><small>14%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">4<br><small>14%</small></td><td style="text-align:right">28</td></tr>
<tr><th>context</th><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.74);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">7<br><small>78%</small></td><td style="background:rgba(37, 99, 235, 0.27);color:inherit;text-align:right">2<br><small>22%</small></td><td style="text-align:right">9</td></tr>
<tr><th>noise</th><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">21<br><small>4%</small></td><td style="background:rgba(37, 99, 235, 0.16);color:inherit;text-align:right">48<br><small>10%</small></td><td style="background:rgba(37, 99, 235, 0.35);color:inherit;text-align:right">158<br><small>32%</small></td><td style="background:rgba(37, 99, 235, 0.54);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">263<br><small>54%</small></td><td style="text-align:right">490</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **200 (85%)** | 21 (9%) | 13 (6%) | 2 (1%) | 236 |
| answer_to_ai | **2 (29%)** | 1 (14%) | 3 (43%) | 1 (14%) | 7 |
| talk_to_ai | 0 (0%) | **20 (71%)** | 4 (14%) | 4 (14%) | 28 |
| context | 0 (0%) | 0 (0%) | **7 (78%)** | 2 (22%) | 9 |
| noise | 21 (4%) | 48 (10%) | 158 (32%) | **263 (54%)** | 490 |

- **Asks reaching the brain: 217 of 243 (89.3%)**: 202 straight, 15 through Respond B's hand-up.
- **Asks lost: 26**: 6 answered in chat by Respond, 16 kept as context, 3 dropped, 1 to Respond outside B's set.
- **Talk and noise sent to the brain: 21 of 518 (4.1%)** straight; 68 to Respond, of which B handed up 7.
- **Noise to Respond: 48**, about 43 answered in chat.
- **Cost: $2.49 a session**: 223 brain calls straight, 90 Respond calls, of which 22 handed up by B and about 2.9 more outside B's set (248 brain calls $2.11, Respond $0.14), Jev 5,909,041 tokens $0.248.
- Latency: 221 ms median, 247 ms p90, over 770 Jev calls.

### E1 rules r2, new state

<table>
<caption>E1 rules r2, new state: label (rows) by outcome (columns)</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>request</th><td style="background:rgba(37, 99, 235, 0.81);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">202<br><small>86%</small></td><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">18<br><small>8%</small></td><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">14<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">2<br><small>1%</small></td><td style="text-align:right">236</td></tr>
<tr><th>answer_to_ai</th><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">1<br><small>14%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">1<br><small>14%</small></td><td style="background:rgba(37, 99, 235, 0.44);color:inherit;text-align:right">3<br><small>43%</small></td><td style="background:rgba(37, 99, 235, 0.32);color:inherit;text-align:right">2<br><small>29%</small></td><td style="text-align:right">7</td></tr>
<tr><th>talk_to_ai</th><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.78);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">23<br><small>82%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">4<br><small>14%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">1<br><small>4%</small></td><td style="text-align:right">28</td></tr>
<tr><th>context</th><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.84);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">8<br><small>89%</small></td><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">1<br><small>11%</small></td><td style="text-align:right">9</td></tr>
<tr><th>noise</th><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">23<br><small>5%</small></td><td style="background:rgba(37, 99, 235, 0.18);color:inherit;text-align:right">58<br><small>12%</small></td><td style="background:rgba(37, 99, 235, 0.41);color:inherit;text-align:right">188<br><small>38%</small></td><td style="background:rgba(37, 99, 235, 0.46);color:#fff;text-align:right;outline:2px solid #16a34a;outline-offset:-2px">221<br><small>45%</small></td><td style="text-align:right">490</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **202 (86%)** | 18 (8%) | 14 (6%) | 2 (1%) | 236 |
| answer_to_ai | **1 (14%)** | 1 (14%) | 3 (43%) | 2 (29%) | 7 |
| talk_to_ai | 0 (0%) | **23 (82%)** | 4 (14%) | 1 (4%) | 28 |
| context | 0 (0%) | 0 (0%) | **8 (89%)** | 1 (11%) | 9 |
| noise | 23 (5%) | 58 (12%) | 188 (38%) | **221 (45%)** | 490 |

- **Asks reaching the brain: 217 of 243 (89.3%)**: 203 straight, 14 through Respond B's hand-up.
- **Asks lost: 26**: 4 answered in chat by Respond, 17 kept as context, 4 dropped, 1 to Respond outside B's set.
- **Talk and noise sent to the brain: 23 of 518 (4.4%)** straight; 81 to Respond, of which B handed up 9.
- **Noise to Respond: 58**, about 51 answered in chat.
- **Cost: $2.54 a session**: 226 brain calls straight, 100 Respond calls, of which 23 handed up by B and about 3.3 more outside B's set (252 brain calls $2.14, Respond $0.15), Jev 5,912,939 tokens $0.248.
- Latency: 221 ms median, 253 ms p90, over 770 Jev calls.

## Every ask that does not reach the brain


**Jev today, old state**: 8

- `05:27:52` KrownJM [answer_to_ai] → **Respond (B answered in chat)** `conversational`: No my credits are it's zero i'm broke I said.
- `05:47:26` KrownJM [request] → **Respond (B answered in chat)** `conversational`: Yeah. Yeah, it's not sending out just red and conversations to clod either. So don't leave your response to when we say clod or... Which is the point? So you know? Well, I guess now that I said part …
- `06:09:05` Eclipszr [request] → **Respond (B answered in chat)** `conversational`: Hey, Claude, show me the copyright claims that are given to my private identity when I'm being recorded without permission.
- `06:18:09` KrownJM [request] → **Respond (outside B's set)** `conversational`: I think I need to make the orchestrator do things on his own. Oh what the fuck? Is it just talk to me? Is this your spooky shit? What the I heard something. My God exactly crazy to fight that possibl…
- `06:29:26` Eclipszr [request] → **Respond (B answered in chat)** `conversational`: Princess open at a castle gate. Okay, can I go check out?
- `06:59:03` Eclipszr [request, 2 lines, second half] → **Respond (B answered in chat)** `conversational`: Can't be damaged and I can't mortgage. I can make them fall but I can't do damage to … I can make them on the floor
- `07:20:46` Styrkaeopteryx [request, second half] → **context** `context`: I was trying to by giving Gallic gun piercing. But
- `07:32:54` Eclipszr [request, second half] → **Respond (B answered in chat)** `conversational`: Like I said, I don't have a spawn.

**Jev today, new state**: 7

- `05:27:52` KrownJM [answer_to_ai] → **Respond (B answered in chat)** `conversational`: No my credits are it's zero i'm broke I said.
- `06:06:42` Eclipszr [answer_to_ai, 2 lines] → **context** `context`: Matthew. Matthew. Would you agree that D two is one of the best games ever? … Yes. Air that cloud, he said yes.
- `06:09:05` Eclipszr [request] → **Respond (B answered in chat)** `conversational`: Hey, Claude, show me the copyright claims that are given to my private identity when I'm being recorded without permission.
- `06:18:09` KrownJM [request] → **Respond (outside B's set)** `conversational`: I think I need to make the orchestrator do things on his own. Oh what the fuck? Is it just talk to me? Is this your spooky shit? What the I heard something. My God exactly crazy to fight that possibl…
- `06:57:22` Eclipszr [request, second half] → **dropped** `not_relevant`: I want you to worse.
- `06:59:03` Eclipszr [request, 2 lines, second half] → **Respond (B answered in chat)** `conversational`: Can't be damaged and I can't mortgage. I can make them fall but I can't do damage to … I can make them on the floor
- `07:20:46` Styrkaeopteryx [request, second half] → **dropped** `not_relevant`: I was trying to by giving Gallic gun piercing. But

**A (v2), old state**: 14

- `05:27:52` KrownJM [answer_to_ai] → **Respond (B answered in chat)** `the_ai > just_chatting`: No my credits are it's zero i'm broke I said.
- `05:47:26` KrownJM [request] → **Respond (B answered in chat)** `the_ai > just_chatting`: Yeah. Yeah, it's not sending out just red and conversations to clod either. So don't leave your response to when we say clod or... Which is the point? So you know? Well, I guess now that I said part …
- `06:06:42` Eclipszr [answer_to_ai, 2 lines] → **dropped** `a_player > the_moment`: Matthew. Matthew. Would you agree that D two is one of the best games ever? … Yes. Air that cloud, he said yes.
- `06:09:05` Eclipszr [request] → **Respond (B answered in chat)** `the_ai > an_answer`: Hey, Claude, show me the copyright claims that are given to my private identity when I'm being recorded without permission.
- `06:14:04` KrownJM [request] → **Respond (B answered in chat)** `the_ai > an_answer`: Can you teach me about both of us to Stirka? Also Matthew Matt Matthew are you recording? Can you record Bo? 'Cause I wanna post it on shit with different perspectives.
- `06:28:11` Styrkaeopteryx [request] → **context** `a_player > ai_work_bad`: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:38:14` KrownJM [request] → **context** `a_player > plan_or_rule`: Okay, can we like stay together and actually like clear out some like one place to live?
- `06:47:05` Eclipszr [request, 4 lines] → **dropped** `themselves > garbled`: Gravity crush Gravity lift. … Privacy crush. Gravity lift. … Cush street down. … cross. Oh wait, use this. So every year around mobs, you hold it, hold right click on it and all the mobs are just for…
- `06:59:03` Eclipszr [request, 2 lines, second half] → **context** `themselves > ai_work_bad`: Can't be damaged and I can't mortgage. I can make them fall but I can't do damage to … I can make them on the floor
- `07:14:10` KrownJM [request, second half] → **context** `themselves > ai_work_bad`: I have to make sure the ice steam also goes through blocks. Holy sit, what the fuck happened here? I think so cool. This is the hitchhik.
- `07:15:38` KrownJM [request, second half] → **context** `a_player > ai_work_bad`: Wait, I can't I just I just froze that IC pierced out we got to stop. I feel the eyes don't what are you what are you break it, break it? Don't shoot me, do not shoot me. Thank you. Okay. Okay, this …
- `07:20:46` Styrkaeopteryx [request, second half] → **dropped** `a_player > garbled`: I was trying to by giving Gallic gun piercing. But
- `07:24:33` Styrkaeopteryx [request, second half] → **context** `a_player > player_or_world_context`: I don't want to get put in a fucking pit again, please.
- `07:46:06` KrownJM [request, second half] → **dropped** `no_one > the_moment`: Oh back here. Okay, let's let's Let's go farther from here. What? What is shooting? What is shooting it? I'm on the shooting. What the fuck is happening? Whoa. Wait, isn't it just not loading? Wait, …

**A (v2), new state**: 13

- `06:06:42` Eclipszr [answer_to_ai, 2 lines] → **dropped** `a_player > the_moment`: Matthew. Matthew. Would you agree that D two is one of the best games ever? … Yes. Air that cloud, he said yes.
- `06:09:05` Eclipszr [request] → **Respond (B answered in chat)** `the_ai > an_answer`: Hey, Claude, show me the copyright claims that are given to my private identity when I'm being recorded without permission.
- `06:14:04` KrownJM [request] → **Respond (B answered in chat)** `the_ai > an_answer`: Can you teach me about both of us to Stirka? Also Matthew Matt Matthew are you recording? Can you record Bo? 'Cause I wanna post it on shit with different perspectives.
- `06:28:11` Styrkaeopteryx [request] → **context** `a_player > ai_work_bad`: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:29:26` Eclipszr [request] → **Respond (B answered in chat)** `the_ai > an_answer`: Princess open at a castle gate. Okay, can I go check out?
- `06:38:14` KrownJM [request] → **context** `a_player > plan_or_rule`: Okay, can we like stay together and actually like clear out some like one place to live?
- `06:47:05` Eclipszr [request, 4 lines] → **context** `themselves > player_or_world_context`: Gravity crush Gravity lift. … Privacy crush. Gravity lift. … Cush street down. … cross. Oh wait, use this. So every year around mobs, you hold it, hold right click on it and all the mobs are just for…
- `06:56:41` Styrkaeopteryx [request] → **context** `a_player > plan_or_rule`: Okay. Like I'll I'll just say at the bottom of the pit. more stop shooting at things at me through the wall, please.
- `06:59:03` Eclipszr [request, 2 lines, second half] → **context** `themselves > ai_work_bad`: Can't be damaged and I can't mortgage. I can make them fall but I can't do damage to … I can make them on the floor
- `07:14:10` KrownJM [request, second half] → **context** `themselves > ai_work_bad`: I have to make sure the ice steam also goes through blocks. Holy sit, what the fuck happened here? I think so cool. This is the hitchhik.
- `07:15:38` KrownJM [request, second half] → **context** `a_player > ai_work_bad`: Wait, I can't I just I just froze that IC pierced out we got to stop. I feel the eyes don't what are you what are you break it, break it? Don't shoot me, do not shoot me. Thank you. Okay. Okay, this …
- `07:24:33` Styrkaeopteryx [request, second half] → **context** `a_player > player_or_world_context`: I don't want to get put in a fucking pit again, please.
- `07:46:06` KrownJM [request, second half] → **context** `themselves > plan_or_rule`: Oh back here. Okay, let's let's Let's go farther from here. What? What is shooting? What is shooting it? I'm on the shooting. What the fuck is happening? Whoa. Wait, isn't it just not loading? Wait, …

**B2, old state**: 15

- `05:27:52` KrownJM [answer_to_ai] → **Respond (B answered in chat)** `question_or_talk_to_the_ai`: No my credits are it's zero i'm broke I said.
- `05:47:26` KrownJM [request] → **Respond (B answered in chat)** `question_or_talk_to_the_ai`: Yeah. Yeah, it's not sending out just red and conversations to clod either. So don't leave your response to when we say clod or... Which is the point? So you know? Well, I guess now that I said part …
- `06:06:42` Eclipszr [answer_to_ai, 2 lines] → **context** `talk_to_a_player > lasting_info`: Matthew. Matthew. Would you agree that D two is one of the best games ever? … Yes. Air that cloud, he said yes.
- `06:09:05` Eclipszr [request] → **Respond (B answered in chat)** `question_or_talk_to_the_ai`: Hey, Claude, show me the copyright claims that are given to my private identity when I'm being recorded without permission.
- `06:18:09` KrownJM [request] → **context** `comment_on_the_ais_work`: I think I need to make the orchestrator do things on his own. Oh what the fuck? Is it just talk to me? Is this your spooky shit? What the I heard something. My God exactly crazy to fight that possibl…
- `06:28:11` Styrkaeopteryx [request] → **context** `comment_on_the_ais_work`: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:29:26` Eclipszr [request] → **Respond (B answered in chat)** `question_or_talk_to_the_ai`: Princess open at a castle gate. Okay, can I go check out?
- `06:47:05` Eclipszr [request, 4 lines] → **context** `comment_on_the_ais_work`: Gravity crush Gravity lift. … Privacy crush. Gravity lift. … Cush street down. … cross. Oh wait, use this. So every year around mobs, you hold it, hold right click on it and all the mobs are just for…
- `06:59:03` Eclipszr [request, 2 lines, second half] → **context** `comment_on_the_ais_work`: Can't be damaged and I can't mortgage. I can make them fall but I can't do damage to … I can make them on the floor
- `07:03:41` Styrkaeopteryx [request, second half] → **context** `comment_on_the_ais_work`: Stuff is still blowing up on me, like constantly. It's one of your things that you did. I know it.
- `07:13:59` Eclipszr [request, second half] → **Respond (B answered in chat)** `question_or_talk_to_the_ai`: Oh wait, is you beam closer for the Hall of Pro and gallakon? This might be cool.
- `07:20:46` Styrkaeopteryx [request, second half] → **context** `comment_on_the_ais_work`: I was trying to by giving Gallic gun piercing. But
- `07:32:54` Eclipszr [request, second half] → **context** `comment_on_the_ais_work`: Like I said, I don't have a spawn.
- `07:44:28` Eclipszr [request, second half] → **context** `comment_on_the_ais_work`: Claude, it doesn't nuke the world. It doesn't create a bigger crater, therefore isn't good enough.
- `07:46:06` KrownJM [request, second half] → **dropped** `noise_or_reaction`: Oh back here. Okay, let's let's Let's go farther from here. What? What is shooting? What is shooting it? I'm on the shooting. What the fuck is happening? Whoa. Wait, isn't it just not loading? Wait, …

**B2, new state**: 13

- `05:27:52` KrownJM [answer_to_ai] → **Respond (B answered in chat)** `question_or_talk_to_the_ai`: No my credits are it's zero i'm broke I said.
- `05:47:26` KrownJM [request] → **Respond (B answered in chat)** `question_or_talk_to_the_ai`: Yeah. Yeah, it's not sending out just red and conversations to clod either. So don't leave your response to when we say clod or... Which is the point? So you know? Well, I guess now that I said part …
- `06:06:42` Eclipszr [answer_to_ai, 2 lines] → **dropped** `talk_to_a_player > only_this_moment`: Matthew. Matthew. Would you agree that D two is one of the best games ever? … Yes. Air that cloud, he said yes.
- `06:09:05` Eclipszr [request] → **Respond (B answered in chat)** `question_or_talk_to_the_ai`: Hey, Claude, show me the copyright claims that are given to my private identity when I'm being recorded without permission.
- `06:18:09` KrownJM [request] → **Respond (outside B's set)** `question_or_talk_to_the_ai`: I think I need to make the orchestrator do things on his own. Oh what the fuck? Is it just talk to me? Is this your spooky shit? What the I heard something. My God exactly crazy to fight that possibl…
- `06:28:11` Styrkaeopteryx [request] → **context** `comment_on_the_ais_work`: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:47:05` Eclipszr [request, 4 lines] → **context** `comment_on_the_ais_work`: Gravity crush Gravity lift. … Privacy crush. Gravity lift. … Cush street down. … cross. Oh wait, use this. So every year around mobs, you hold it, hold right click on it and all the mobs are just for…
- `06:59:03` Eclipszr [request, 2 lines, second half] → **context** `comment_on_the_ais_work`: Can't be damaged and I can't mortgage. I can make them fall but I can't do damage to … I can make them on the floor
- `07:03:41` Styrkaeopteryx [request, second half] → **context** `comment_on_the_ais_work`: Stuff is still blowing up on me, like constantly. It's one of your things that you did. I know it.
- `07:13:59` Eclipszr [request, second half] → **Respond (B answered in chat)** `question_or_talk_to_the_ai`: Oh wait, is you beam closer for the Hall of Pro and gallakon? This might be cool.
- `07:20:46` Styrkaeopteryx [request, second half] → **context** `comment_on_the_ais_work`: I was trying to by giving Gallic gun piercing. But
- `07:32:54` Eclipszr [request, second half] → **context** `comment_on_the_ais_work`: Like I said, I don't have a spawn.
- `07:44:28` Eclipszr [request, second half] → **context** `comment_on_the_ais_work`: Claude, it doesn't nuke the world. It doesn't create a bigger crater, therefore isn't good enough.

**C1, old state**: 16

- `05:27:52` KrownJM [answer_to_ai] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: No my credits are it's zero i'm broke I said.
- `05:47:26` KrownJM [request] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: Yeah. Yeah, it's not sending out just red and conversations to clod either. So don't leave your response to when we say clod or... Which is the point? So you know? Well, I guess now that I said part …
- `06:06:42` Eclipszr [answer_to_ai, 2 lines] → **dropped** `otherwise`: Matthew. Matthew. Would you agree that D two is one of the best games ever? … Yes. Air that cloud, he said yes.
- `06:09:05` Eclipszr [request] → **Respond (B answered in chat)** `c3_to_ai=a_question_to_the_ai`: Hey, Claude, show me the copyright claims that are given to my private identity when I'm being recorded without permission.
- `06:14:04` KrownJM [request] → **dropped** `otherwise`: Can you teach me about both of us to Stirka? Also Matthew Matt Matthew are you recording? Can you record Bo? 'Cause I wanna post it on shit with different perspectives.
- `06:18:09` KrownJM [request] → **Respond (outside B's set)** `c3_to_ai=a_question_to_the_ai`: I think I need to make the orchestrator do things on his own. Oh what the fuck? Is it just talk to me? Is this your spooky shit? What the I heard something. My God exactly crazy to fight that possibl…
- `06:28:11` Styrkaeopteryx [request] → **context** `c4_lasting=a_problem_with_the_ais_work`: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:29:26` Eclipszr [request] → **Respond (B answered in chat)** `c3_to_ai=a_question_to_the_ai`: Princess open at a castle gate. Okay, can I go check out?
- `06:38:14` KrownJM [request] → **context** `c4_lasting=a_plan_or_rule`: Okay, can we like stay together and actually like clear out some like one place to live?
- `06:57:22` Eclipszr [request, second half] → **dropped** `c1_noise=garbled_speech`: I want you to worse.
- `06:58:09` KrownJM [answer_to_ai, second half] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: Yeah, it does. That's why I turned it off. To more just like trolling us. It just happens twenty four seven.
- `06:59:03` Eclipszr [request, 2 lines, second half] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: Can't be damaged and I can't mortgage. I can make them fall but I can't do damage to … I can make them on the floor
- `07:13:59` Eclipszr [request, second half] → **Respond (B answered in chat)** `c3_to_ai=a_question_to_the_ai`: Oh wait, is you beam closer for the Hall of Pro and gallakon? This might be cool.
- `07:20:46` Styrkaeopteryx [request, second half] → **dropped** `otherwise`: I was trying to by giving Gallic gun piercing. But
- `07:24:33` Styrkaeopteryx [request, second half] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: I don't want to get put in a fucking pit again, please.
- `07:32:54` Eclipszr [request, second half] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: Like I said, I don't have a spawn.

**C1, new state**: 14

- `05:27:52` KrownJM [answer_to_ai] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: No my credits are it's zero i'm broke I said.
- `06:06:42` Eclipszr [answer_to_ai, 2 lines] → **dropped** `otherwise`: Matthew. Matthew. Would you agree that D two is one of the best games ever? … Yes. Air that cloud, he said yes.
- `06:14:04` KrownJM [request] → **Respond (B answered in chat)** `c3_to_ai=a_question_to_the_ai`: Can you teach me about both of us to Stirka? Also Matthew Matt Matthew are you recording? Can you record Bo? 'Cause I wanna post it on shit with different perspectives.
- `06:18:09` KrownJM [request] → **Respond (outside B's set)** `c3_to_ai=a_question_to_the_ai`: I think I need to make the orchestrator do things on his own. Oh what the fuck? Is it just talk to me? Is this your spooky shit? What the I heard something. My God exactly crazy to fight that possibl…
- `06:28:11` Styrkaeopteryx [request] → **context** `c4_lasting=a_problem_with_the_ais_work`: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:29:26` Eclipszr [request] → **Respond (B answered in chat)** `c3_to_ai=a_question_to_the_ai`: Princess open at a castle gate. Okay, can I go check out?
- `06:38:14` KrownJM [request] → **context** `c4_lasting=a_plan_or_rule`: Okay, can we like stay together and actually like clear out some like one place to live?
- `06:57:22` Eclipszr [request, second half] → **dropped** `c1_noise=garbled_speech`: I want you to worse.
- `06:59:03` Eclipszr [request, 2 lines, second half] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: Can't be damaged and I can't mortgage. I can make them fall but I can't do damage to … I can make them on the floor
- `07:08:13` KrownJM [request, second half] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: But you don't have to ask me for permission anywhere. You can just do whatever they say.
- `07:13:59` Eclipszr [request, second half] → **Respond (B answered in chat)** `c3_to_ai=a_question_to_the_ai`: Oh wait, is you beam closer for the Hall of Pro and gallakon? This might be cool.
- `07:20:46` Styrkaeopteryx [request, second half] → **context** `c4_lasting=a_problem_with_the_ais_work`: I was trying to by giving Gallic gun piercing. But
- `07:24:33` Styrkaeopteryx [request, second half] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: I don't want to get put in a fucking pit again, please.
- `07:32:54` Eclipszr [request, second half] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: Like I said, I don't have a spawn.

**D2, old state**: 24

- `05:27:52` KrownJM [answer_to_ai] → **dropped** `ignore_it`: No my credits are it's zero i'm broke I said.
- `05:47:26` KrownJM [request] → **Respond (B answered in chat)** `the_ai_should_answer`: Yeah. Yeah, it's not sending out just red and conversations to clod either. So don't leave your response to when we say clod or... Which is the point? So you know? Well, I guess now that I said part …
- `06:06:42` Eclipszr [answer_to_ai, 2 lines] → **dropped** `ignore_it`: Matthew. Matthew. Would you agree that D two is one of the best games ever? … Yes. Air that cloud, he said yes.
- `06:07:06` Styrkaeopteryx [answer_to_ai, 3 lines] → **Respond (B answered in chat)** `the_ai_must_act + fine_a_reply_is_all`: Yes. … I just say yes to? … No, Claude, I was not saying yes to Eclipse are killing me.
- `06:09:05` Eclipszr [request] → **Respond (B answered in chat)** `the_ai_should_answer`: Hey, Claude, show me the copyright claims that are given to my private identity when I'm being recorded without permission.
- `06:13:57` KrownJM [request] → **dropped** `ignore_it`: Please don't split please on it.
- `06:14:04` KrownJM [request] → **Respond (B answered in chat)** `the_ai_should_answer`: Can you teach me about both of us to Stirka? Also Matthew Matt Matthew are you recording? Can you record Bo? 'Cause I wanna post it on shit with different perspectives.
- `06:18:09` KrownJM [request] → **Respond (outside B's set)** `the_ai_should_answer`: I think I need to make the orchestrator do things on his own. Oh what the fuck? Is it just talk to me? Is this your spooky shit? What the I heard something. My God exactly crazy to fight that possibl…
- `06:28:11` Styrkaeopteryx [request] → **dropped** `ignore_it`: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:29:26` Eclipszr [request] → **Respond (B answered in chat)** `the_ai_should_answer`: Princess open at a castle gate. Okay, can I go check out?
- `06:47:05` Eclipszr [request, 4 lines] → **dropped** `ignore_it`: Gravity crush Gravity lift. … Privacy crush. Gravity lift. … Cush street down. … cross. Oh wait, use this. So every year around mobs, you hold it, hold right click on it and all the mobs are just for…
- `06:57:22` Eclipszr [request, second half] → **dropped** `ignore_it`: I want you to worse.
- `06:58:09` KrownJM [answer_to_ai, second half] → **Respond (B answered in chat)** `the_ai_must_act + fine_a_reply_is_all`: Yeah, it does. That's why I turned it off. To more just like trolling us. It just happens twenty four seven.
- `06:59:03` Eclipszr [request, 2 lines, second half] → **dropped** `ignore_it`: Can't be damaged and I can't mortgage. I can make them fall but I can't do damage to … I can make them on the floor
- `07:13:59` Eclipszr [request, second half] → **Respond (B answered in chat)** `the_ai_should_answer`: Oh wait, is you beam closer for the Hall of Pro and gallakon? This might be cool.
- `07:14:10` KrownJM [request, second half] → **dropped** `ignore_it`: I have to make sure the ice steam also goes through blocks. Holy sit, what the fuck happened here? I think so cool. This is the hitchhik.
- `07:20:46` Styrkaeopteryx [request, second half] → **dropped** `ignore_it`: I was trying to by giving Gallic gun piercing. But
- `07:24:33` Styrkaeopteryx [request, second half] → **context** `remember_it`: I don't want to get put in a fucking pit again, please.
- `07:25:09` KrownJM [request, second half] → **dropped** `ignore_it`: Could you also remove the glow effect on? Oh wait, never mind. Keep the effect never mind. You can keep it good. Wait, did it just turn ice to? Oh it turns ice to
- `07:25:33` Eclipszr [request, second half] → **dropped** `ignore_it`: Yeah, whatever make everything IMIFIE. That's the way I'm going to pronounce it. Any fine.
- `07:27:56` Eclipszr [request, second half] → **Respond (B answered in chat)** `the_ai_should_answer`: Can make music for each of the attacks? Is that possible?
- `07:32:54` Eclipszr [request, second half] → **context** `remember_it`: Like I said, I don't have a spawn.
- `07:34:20` KrownJM [request, second half] → **dropped** `ignore_it`: Oh. Can't you just use your thing to like break? Using your hand. Hm. Holy sit. Honestly, sit.
- `07:46:06` KrownJM [request, second half] → **dropped** `ignore_it`: Oh back here. Okay, let's let's Let's go farther from here. What? What is shooting? What is shooting it? I'm on the shooting. What the fuck is happening? Whoa. Wait, isn't it just not loading? Wait, …

**D2, new state**: 22

- `05:27:52` KrownJM [answer_to_ai] → **dropped** `ignore_it`: No my credits are it's zero i'm broke I said.
- `05:47:26` KrownJM [request] → **context** `remember_it`: Yeah. Yeah, it's not sending out just red and conversations to clod either. So don't leave your response to when we say clod or... Which is the point? So you know? Well, I guess now that I said part …
- `06:06:42` Eclipszr [answer_to_ai, 2 lines] → **dropped** `ignore_it`: Matthew. Matthew. Would you agree that D two is one of the best games ever? … Yes. Air that cloud, he said yes.
- `06:07:06` Styrkaeopteryx [answer_to_ai, 3 lines] → **Respond (B answered in chat)** `the_ai_must_act + fine_a_reply_is_all`: Yes. … I just say yes to? … No, Claude, I was not saying yes to Eclipse are killing me.
- `06:09:05` Eclipszr [request] → **Respond (B answered in chat)** `the_ai_should_answer`: Hey, Claude, show me the copyright claims that are given to my private identity when I'm being recorded without permission.
- `06:14:04` KrownJM [request] → **Respond (B answered in chat)** `the_ai_should_answer`: Can you teach me about both of us to Stirka? Also Matthew Matt Matthew are you recording? Can you record Bo? 'Cause I wanna post it on shit with different perspectives.
- `06:18:09` KrownJM [request] → **Respond (outside B's set)** `the_ai_should_answer`: I think I need to make the orchestrator do things on his own. Oh what the fuck? Is it just talk to me? Is this your spooky shit? What the I heard something. My God exactly crazy to fight that possibl…
- `06:28:11` Styrkaeopteryx [request] → **context** `remember_it`: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:29:26` Eclipszr [request] → **Respond (B answered in chat)** `the_ai_should_answer`: Princess open at a castle gate. Okay, can I go check out?
- `06:38:14` KrownJM [request] → **context** `remember_it`: Okay, can we like stay together and actually like clear out some like one place to live?
- `06:47:05` Eclipszr [request, 4 lines] → **context** `remember_it`: Gravity crush Gravity lift. … Privacy crush. Gravity lift. … Cush street down. … cross. Oh wait, use this. So every year around mobs, you hold it, hold right click on it and all the mobs are just for…
- `06:57:22` Eclipszr [request, second half] → **dropped** `ignore_it`: I want you to worse.
- `06:59:03` Eclipszr [request, 2 lines, second half] → **context** `remember_it`: Can't be damaged and I can't mortgage. I can make them fall but I can't do damage to … I can make them on the floor
- `07:03:41` Styrkaeopteryx [request, second half] → **context** `remember_it`: Stuff is still blowing up on me, like constantly. It's one of your things that you did. I know it.
- `07:20:46` Styrkaeopteryx [request, second half] → **dropped** `ignore_it`: I was trying to by giving Gallic gun piercing. But
- `07:24:33` Styrkaeopteryx [request, second half] → **context** `remember_it`: I don't want to get put in a fucking pit again, please.
- `07:25:09` KrownJM [request, second half] → **dropped** `ignore_it`: Could you also remove the glow effect on? Oh wait, never mind. Keep the effect never mind. You can keep it good. Wait, did it just turn ice to? Oh it turns ice to
- `07:25:33` Eclipszr [request, second half] → **dropped** `ignore_it`: Yeah, whatever make everything IMIFIE. That's the way I'm going to pronounce it. Any fine.
- `07:27:56` Eclipszr [request, second half] → **Respond (B answered in chat)** `the_ai_should_answer`: Can make music for each of the attacks? Is that possible?
- `07:32:54` Eclipszr [request, second half] → **context** `remember_it`: Like I said, I don't have a spawn.
- `07:34:20` KrownJM [request, second half] → **dropped** `ignore_it`: Oh. Can't you just use your thing to like break? Using your hand. Hm. Holy sit. Honestly, sit.
- `07:44:28` Eclipszr [request, second half] → **context** `remember_it`: Claude, it doesn't nuke the world. It doesn't create a bigger crater, therefore isn't good enough.

**E1 rules r2, old state**: 26

- `05:27:52` KrownJM [answer_to_ai] → **context** `you_with_no_name, a_player, still_true_in_five_minutes`: No my credits are it's zero i'm broke I said.
- `05:47:26` KrownJM [request] → **Respond (B answered in chat)** `ai, a_statement`: Yeah. Yeah, it's not sending out just red and conversations to clod either. So don't leave your response to when we say clod or... Which is the point? So you know? Well, I guess now that I said part …
- `06:06:42` Eclipszr [answer_to_ai, 2 lines] → **dropped** `names_a_player, nothing_in_particular, only_this_moment`: Matthew. Matthew. Would you agree that D two is one of the best games ever? … Yes. Air that cloud, he said yes.
- `06:07:06` Styrkaeopteryx [answer_to_ai, 3 lines] → **Respond (B answered in chat)** `ai, a_question`: Yes. … I just say yes to? … No, Claude, I was not saying yes to Eclipse are killing me.
- `06:15:50` Styrkaeopteryx [answer_to_ai] → **context** `you_with_no_name, something_the_ai_made_or_did, only_this_moment`: Also, yes
- `06:18:09` KrownJM [request] → **Respond (outside B's set)** `you, a_question, something_the_ai_made_or_did`: I think I need to make the orchestrator do things on his own. Oh what the fuck? Is it just talk to me? Is this your spooky shit? What the I heard something. My God exactly crazy to fight that possibl…
- `06:20:26` Eclipszr [request] → **context** `no_one, something_the_ai_made_or_did, only_this_moment`: Giving the ability to toss meteor's own command.
- `06:28:11` Styrkaeopteryx [request] → **context** `names_a_player, something_the_ai_made_or_did, still_true_in_five_minutes`: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:29:26` Eclipszr [request] → **Respond (B answered in chat)** `you, a_question, something_the_ai_made_or_did`: Princess open at a castle gate. Okay, can I go check out?
- `06:29:58` Eclipszr [request] → **context** `no_one, something_the_ai_made_or_did, only_this_moment`: Doom weaver. Curls everyone in near error blast targets with explosions. Thunder calls lightning on every head. That target is ablaze, freezes and snows targets. whether
- `06:30:23` Eclipszr [request] → **Respond (B answered in chat)** `names_a_player, instruction`: Freeze anything. Brave Digger. Everything in there.
- `06:38:14` KrownJM [request] → **Respond (B answered in chat)** `names_a_player, instruction`: Okay, can we like stay together and actually like clear out some like one place to live?
- `06:47:05` Eclipszr [request, 4 lines] → **context** `you_with_no_name, something_the_ai_made_or_did, still_true_in_five_minutes`: Gravity crush Gravity lift. … Privacy crush. Gravity lift. … Cush street down. … cross. Oh wait, use this. So every year around mobs, you hold it, hold right click on it and all the mobs are just for…
- `06:54:06` KrownJM [request] → **context** `no_one, something_the_ai_made_or_did, still_true_in_five_minutes`: It does look like my staff should do a lot more damage. There's like no damage. My staff should do a lot of damage.
- `06:58:09` KrownJM [answer_to_ai, second half] → **context** `no_one, the_world_or_the_game, still_true_in_five_minutes`: Yeah, it does. That's why I turned it off. To more just like trolling us. It just happens twenty four seven.
- `06:59:03` Eclipszr [request, 2 lines, second half] → **context** `no_one, something_the_ai_made_or_did, still_true_in_five_minutes`: Can't be damaged and I can't mortgage. I can make them fall but I can't do damage to … I can make them on the floor
- `07:00:34` KrownJM [request, second half] → **dropped** `you_with_no_name, a_player, only_this_moment`: Also, where is my ice bag? I wore my eyes dog bag.
- `07:03:41` Styrkaeopteryx [request, second half] → **context** `you_with_no_name, something_the_ai_made_or_did, only_this_moment`: Stuff is still blowing up on me, like constantly. It's one of your things that you did. I know it.
- `07:13:59` Eclipszr [request, second half] → **Respond (B answered in chat)** `you, a_question, something_the_ai_made_or_did`: Oh wait, is you beam closer for the Hall of Pro and gallakon? This might be cool.
- `07:14:10` KrownJM [request, second half] → **context** `no_one, something_the_ai_made_or_did, only_this_moment`: I have to make sure the ice steam also goes through blocks. Holy sit, what the fuck happened here? I think so cool. This is the hitchhik.
- `07:20:46` Styrkaeopteryx [request, second half] → **context** `no_one, something_the_ai_made_or_did, only_this_moment`: I was trying to by giving Gallic gun piercing. But
- `07:22:40` Eclipszr [request, 2 lines, second half] → **context** `you_with_no_name, something_the_ai_made_or_did, still_true_in_five_minutes`: I said spirit bon, not spirit bon. … No, no, spirit bomb like in the show, like the giant key explosion.
- `07:28:11` KrownJM [request, 2 lines, second half] → **context** `no_one, something_the_ai_made_or_did, only_this_moment`: Oh my god, it's so hard to like I want the path because I can't walk anywhere anymore. It's all fucked up. … What the fuck just happened? Oh this is not what I wanted. I didn't want to create Just ic…
- `07:32:54` Eclipszr [request, second half] → **context** `you_with_no_name, a_player, still_true_in_five_minutes`: Like I said, I don't have a spawn.
- `07:46:06` KrownJM [request, second half] → **dropped** `no_one, the_world_or_the_game, only_this_moment`: Oh back here. Okay, let's let's Let's go farther from here. What? What is shooting? What is shooting it? I'm on the shooting. What the fuck is happening? Whoa. Wait, isn't it just not loading? Wait, …
- `07:46:47` KrownJM [request, second half] → **context** `no_one, the_world_or_the_game, still_true_in_five_minutes`: What the fuck? Okay, let's add a mountain biome. Let's do the mountain biome. Holy shit fro this is so cool. This is so fucking cool. always still going.

**E1 rules r2, new state**: 26

- `05:27:52` KrownJM [answer_to_ai] → **context** `you_with_no_name, a_player, still_true_in_five_minutes`: No my credits are it's zero i'm broke I said.
- `06:06:42` Eclipszr [answer_to_ai, 2 lines] → **dropped** `names_a_player, nothing_in_particular, only_this_moment`: Matthew. Matthew. Would you agree that D two is one of the best games ever? … Yes. Air that cloud, he said yes.
- `06:07:06` Styrkaeopteryx [answer_to_ai, 3 lines] → **Respond (B answered in chat)** `ai, a_question`: Yes. … I just say yes to? … No, Claude, I was not saying yes to Eclipse are killing me.
- `06:15:50` Styrkaeopteryx [answer_to_ai] → **dropped** `you_with_no_name, the_world_or_the_game, only_this_moment`: Also, yes
- `06:18:09` KrownJM [request] → **Respond (outside B's set)** `you, a_question, something_the_ai_made_or_did`: I think I need to make the orchestrator do things on his own. Oh what the fuck? Is it just talk to me? Is this your spooky shit? What the I heard something. My God exactly crazy to fight that possibl…
- `06:20:26` Eclipszr [request] → **context** `no_one, something_the_ai_made_or_did, only_this_moment`: Giving the ability to toss meteor's own command.
- `06:28:11` Styrkaeopteryx [request] → **context** `names_a_player, something_the_ai_made_or_did, still_true_in_five_minutes`: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:29:26` Eclipszr [request] → **Respond (B answered in chat)** `you, a_question, something_the_ai_made_or_did`: Princess open at a castle gate. Okay, can I go check out?
- `06:29:58` Eclipszr [request] → **context** `no_one, something_the_ai_made_or_did, only_this_moment`: Doom weaver. Curls everyone in near error blast targets with explosions. Thunder calls lightning on every head. That target is ablaze, freezes and snows targets. whether
- `06:38:14` KrownJM [request] → **Respond (B answered in chat)** `names_a_player, instruction`: Okay, can we like stay together and actually like clear out some like one place to live?
- `06:47:05` Eclipszr [request, 4 lines] → **context** `you_with_no_name, something_the_ai_made_or_did, still_true_in_five_minutes`: Gravity crush Gravity lift. … Privacy crush. Gravity lift. … Cush street down. … cross. Oh wait, use this. So every year around mobs, you hold it, hold right click on it and all the mobs are just for…
- `06:54:06` KrownJM [request] → **context** `no_one, something_the_ai_made_or_did, still_true_in_five_minutes`: It does look like my staff should do a lot more damage. There's like no damage. My staff should do a lot of damage.
- `06:54:09` Eclipszr [request] → **context** `no_one, something_the_ai_made_or_did, still_true_in_five_minutes`: Like my dead stare one shot everything. at infinite range as well.
- `06:58:09` KrownJM [answer_to_ai, second half] → **context** `you_with_no_name, the_world_or_the_game, still_true_in_five_minutes`: Yeah, it does. That's why I turned it off. To more just like trolling us. It just happens twenty four seven.
- `06:59:03` Eclipszr [request, 2 lines, second half] → **context** `no_one, something_the_ai_made_or_did, still_true_in_five_minutes`: Can't be damaged and I can't mortgage. I can make them fall but I can't do damage to … I can make them on the floor
- `07:00:34` KrownJM [request, second half] → **dropped** `you_with_no_name, a_player, only_this_moment`: Also, where is my ice bag? I wore my eyes dog bag.
- `07:03:41` Styrkaeopteryx [request, second half] → **context** `you_with_no_name, something_the_ai_made_or_did, still_true_in_five_minutes`: Stuff is still blowing up on me, like constantly. It's one of your things that you did. I know it.
- `07:04:06` Eclipszr [answer_to_ai, second half] → **context** `you_with_no_name, something_the_ai_made_or_did, only_this_moment`: Frozen I won't take it.
- `07:13:59` Eclipszr [request, second half] → **Respond (B answered in chat)** `you, a_question, something_the_ai_made_or_did`: Oh wait, is you beam closer for the Hall of Pro and gallakon? This might be cool.
- `07:14:10` KrownJM [request, second half] → **context** `no_one, something_the_ai_made_or_did, only_this_moment`: I have to make sure the ice steam also goes through blocks. Holy sit, what the fuck happened here? I think so cool. This is the hitchhik.
- `07:20:46` Styrkaeopteryx [request, second half] → **context** `no_one, something_the_ai_made_or_did, only_this_moment`: I was trying to by giving Gallic gun piercing. But
- `07:22:40` Eclipszr [request, 2 lines, second half] → **context** `you_with_no_name, something_the_ai_made_or_did, still_true_in_five_minutes`: I said spirit bon, not spirit bon. … No, no, spirit bomb like in the show, like the giant key explosion.
- `07:28:11` KrownJM [request, 2 lines, second half] → **context** `no_one, something_the_ai_made_or_did, only_this_moment`: Oh my god, it's so hard to like I want the path because I can't walk anywhere anymore. It's all fucked up. … What the fuck just happened? Oh this is not what I wanted. I didn't want to create Just ic…
- `07:32:54` Eclipszr [request, second half] → **context** `you_with_no_name, a_player, still_true_in_five_minutes`: Like I said, I don't have a spawn.
- `07:46:06` KrownJM [request, second half] → **dropped** `no_one, the_world_or_the_game, only_this_moment`: Oh back here. Okay, let's let's Let's go farther from here. What? What is shooting? What is shooting it? I'm on the shooting. What the fuck is happening? Whoa. Wait, isn't it just not loading? Wait, …
- `07:46:47` KrownJM [request, second half] → **context** `no_one, something_the_ai_made_or_did, still_true_in_five_minutes`: What the fuck? Okay, let's add a mountain biome. Let's do the mountain biome. Holy shit fro this is so cool. This is so fucking cool. always still going.
