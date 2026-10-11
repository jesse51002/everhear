# The tree: one Jev call that routes every line

Jev today sorts each line into four classes. The tree asks Jev **class questions instead, all in one call**, and code walks the answers:

- **Level 1:** who is the line said to? The AI goes to level 3; a player, the speaker themselves or no one goes to level 2.
- **Level 2**, a line not said to the AI: is anything in it worth keeping? Keep it as **context** (no model call), or **drop** it.
- **Level 3**, a line said to the AI: what does the speaker want? Something changed in the game: the **brain**. A reply: **Respond**. Nothing to answer: **context, no reply**.

Two variants came from the same calls, on the 843 lines of the 2026-10-08 playtest (`tree_bench.py`, scored by `tree_score.py`):

- **No council:** one question per level (member A).
- **Council:** three differently framed questions per level (A, B, C), combined by a vote weighted by confidence.

Both are compared with **Jev today** on the same lines: `action_oriented` goes to the brain, `conversational` to Respond, `context` to context, and `not_relevant` is dropped. Model and Jev calls only; nothing ran in a game.

## Verdict: the tree with no council, asked as A's three questions in one call

- **It is the cheapest of the three and keeps nearly every ask.**
  - On the held-out second half: **$1.59 against Jev today's $1.89 (−16%)**. 82 of 87 asks reach the brain, against Jev today's 84.
  - On all 843 lines, a 2.5 h session: **$3.25 against $3.80**, or **$3.21** asked as a 3-question call. 178 of 187 asks reach the brain, against 183.
  - Nothing it does drops an ask. The 7 asks it does not send up land in context, where the brain sees them on its next call. Every one was said to another player or to no one.
- **Nearly all of the saving is at Respond.**
  - Respond calls go from 382 to 72, because level 1 stops sending talk between players to Respond. Jev today classes 260 of the 401 lines it dropped then as `conversational`.
  - Brain calls barely move: 347 for Jev today, 333 for the tree. The brain is 78% of Jev today's cost and 87% of the tree's.
  - Most of the "talk" that reaches the brain is labelled `chat` or `nothing`, but reads as real requests. The old conversation declined them, merged them or did them already ("Claude, I would like to kill Eclips", "give me one shot protection").
  - Cutting cost further means cutting brain calls on real requests, which no wording of these questions can do.
- **The council costs more and is not more accurate.**
  - It gets 2 more asks to the brain than no council (180 against 178 of 187; 84 against 82 on the held-out half). It costs $0.17 more a session, with 17 more brain calls and 17 more Respond calls.
  - Its members share one state, so they mostly agree. At level 1, all three agree on 86% of the first half's lines.
  - When they disagree, the clash rule sends the line to the AI branch, where level 3 often sends it to the brain. On the first half, 7 lines went to the brain with the council and not without it. For each, A's level 1 had said a player, at 0.20–0.49 confidence. Most were talk between players, or complaints said to another player.
  - At level 2, B and C outvote A's keeps. The council drops complaints about the AI's work and plans that no council keeps: "Wow. I realized how Claude, Gallant Gun kills no matter where I'm aiming" is dropped. In a 20-line sample of its drops, 3 look wrong. In the same sample size of no council's drops, none do.
- **One call is fair to the no-council tree.**
  - A's three answers in the 9-question call matched the 3-question call on 96–98 of 100 lines. The 9-question call matched itself, asked twice, on 97–100.
  - The walked outcome was the same on 98 of 100 lines.
- **Latency is flat.**
  - Medians: 218 ms for today's single question, 226 ms for three questions, 232 ms for nine. The p90s are 241, 258 and 259 ms.
- **Beyond the cost formula, context costs batch calls.**
  - The brain makes a batch call on context that no live read took in for 60 s. With fewer live reads, the tree leaves more of these: 32 a session against Jev today's 14 (the council 25). That is $0.15 more.
  - Counting batch calls, the tree is $3.53 ($3.48 as a 3-question call) against Jev today's $3.92: −11% instead of −15%.

## Heat maps

Rows are what the conversation did with the line: **task** (it became a task: an ask), **chat** (answered in chat, no task), **nothing**, **dropped** (Jev dropped it then, so the conversation never saw it). Columns are where the line goes. Each cell shows the count and the row percentage; the darker the cell, the larger its share of the row.

`chat` is a loose label: an AI chat line within 60 s with no task. It holds many requests the old conversation declined, so `chat → brain` is often right.

### The held-out second half: 422 lines, 87 asks

#### Jev today

<table>
<caption>Jev today: label (rows) by outcome (columns), second</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context, no reply</th><th>context kept</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.87);color:#fff;text-align:right">81<br><small>93%</small></td><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">6<br><small>7%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">87</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.47);color:#fff;text-align:right">44<br><small>46%</small></td><td style="background:rgba(37, 99, 235, 0.51);color:#fff;text-align:right">48<br><small>50%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">4<br><small>4%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">96</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.55);color:#fff;text-align:right">20<br><small>56%</small></td><td style="background:rgba(37, 99, 235, 0.41);color:inherit;text-align:right">14<br><small>39%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">2<br><small>6%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">36</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">13<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.61);color:#fff;text-align:right">126<br><small>62%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.08);color:inherit;text-align:right">1<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.34);color:inherit;text-align:right">63<br><small>31%</small></td><td style="text-align:right">203</td></tr>
</table>

| label | brain | Respond | context, no reply | context kept | dropped | all |
|---|---|---|---|---|---|---|
| **task** | 81 (93%) | 6 (7%) | 0 (0%) | 0 (0%) | 0 (0%) | 87 |
| **chat** | 44 (46%) | 48 (50%) | 0 (0%) | 4 (4%) | 0 (0%) | 96 |
| **nothing** | 20 (56%) | 14 (39%) | 0 (0%) | 2 (6%) | 0 (0%) | 36 |
| **dropped** | 13 (6%) | 126 (62%) | 0 (0%) | 1 (0%) | 63 (31%) | 203 |

- **Asks:** 81 straight to the brain; 6 to Respond, of which 3 handed up by B, 3 answered in chat; 0 context with no reply, 0 context kept, 0 dropped. **84 of 87 reach the brain (96.6%).**

#### The tree, no council

<table>
<caption>Tree, no council (v2): label (rows) by outcome (columns), second</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context, no reply</th><th>context kept</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.87);color:#fff;text-align:right">81<br><small>93%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">2<br><small>2%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">4<br><small>5%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">87</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.44);color:inherit;text-align:right">41<br><small>43%</small></td><td style="background:rgba(37, 99, 235, 0.25);color:inherit;text-align:right">19<br><small>20%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.24);color:inherit;text-align:right">18<br><small>19%</small></td><td style="background:rgba(37, 99, 235, 0.24);color:inherit;text-align:right">18<br><small>19%</small></td><td style="text-align:right">96</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.58);color:#fff;text-align:right">21<br><small>58%</small></td><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">4<br><small>11%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.27);color:inherit;text-align:right">8<br><small>22%</small></td><td style="background:rgba(37, 99, 235, 0.15);color:inherit;text-align:right">3<br><small>8%</small></td><td style="text-align:right">36</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">13<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">12<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">5<br><small>2%</small></td><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">15<br><small>7%</small></td><td style="background:rgba(37, 99, 235, 0.74);color:#fff;text-align:right">158<br><small>78%</small></td><td style="text-align:right">203</td></tr>
</table>

| label | brain | Respond | context, no reply | context kept | dropped | all |
|---|---|---|---|---|---|---|
| **task** | 81 (93%) | 2 (2%) | 0 (0%) | 4 (5%) | 0 (0%) | 87 |
| **chat** | 41 (43%) | 19 (20%) | 0 (0%) | 18 (19%) | 18 (19%) | 96 |
| **nothing** | 21 (58%) | 4 (11%) | 0 (0%) | 8 (22%) | 3 (8%) | 36 |
| **dropped** | 13 (6%) | 12 (6%) | 5 (2%) | 15 (7%) | 158 (78%) | 203 |

- **Asks:** 81 straight to the brain; 2 to Respond, of which 1 handed up by B, 1 answered in chat; 0 context with no reply, 4 context kept, 0 dropped. **82 of 87 reach the brain (94.3%).**

#### The tree with council

<table>
<caption>Tree with council (v2, margin 0.25): label (rows) by outcome (columns), second</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context, no reply</th><th>context kept</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.88);color:#fff;text-align:right">82<br><small>94%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">3<br><small>3%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">2<br><small>2%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">87</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.48);color:#fff;text-align:right">45<br><small>47%</small></td><td style="background:rgba(37, 99, 235, 0.27);color:inherit;text-align:right">21<br><small>22%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">13<br><small>14%</small></td><td style="background:rgba(37, 99, 235, 0.23);color:inherit;text-align:right">17<br><small>18%</small></td><td style="text-align:right">96</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.58);color:#fff;text-align:right">21<br><small>58%</small></td><td style="background:rgba(37, 99, 235, 0.25);color:inherit;text-align:right">7<br><small>19%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.22);color:inherit;text-align:right">6<br><small>17%</small></td><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">2<br><small>6%</small></td><td style="text-align:right">36</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">14<br><small>7%</small></td><td style="background:rgba(37, 99, 235, 0.15);color:inherit;text-align:right">17<br><small>8%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">2<br><small>1%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">4<br><small>2%</small></td><td style="background:rgba(37, 99, 235, 0.78);color:#fff;text-align:right">166<br><small>82%</small></td><td style="text-align:right">203</td></tr>
</table>

| label | brain | Respond | context, no reply | context kept | dropped | all |
|---|---|---|---|---|---|---|
| **task** | 82 (94%) | 3 (3%) | 0 (0%) | 2 (2%) | 0 (0%) | 87 |
| **chat** | 45 (47%) | 21 (22%) | 0 (0%) | 13 (14%) | 17 (18%) | 96 |
| **nothing** | 21 (58%) | 7 (19%) | 0 (0%) | 6 (17%) | 2 (6%) | 36 |
| **dropped** | 14 (7%) | 17 (8%) | 2 (1%) | 4 (2%) | 166 (82%) | 203 |

- **Asks:** 82 straight to the brain; 3 to Respond, of which 2 handed up by B, 1 answered in chat; 0 context with no reply, 2 context kept, 0 dropped. **84 of 87 reach the brain (96.6%).**

### All 843 lines: 187 asks

#### Jev today

<table>
<caption>Jev today: label (rows) by outcome (columns), all</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context, no reply</th><th>context kept</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.89);color:#fff;text-align:right">179<br><small>96%</small></td><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">8<br><small>4%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">187</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.51);color:#fff;text-align:right">93<br><small>51%</small></td><td style="background:rgba(37, 99, 235, 0.48);color:#fff;text-align:right">86<br><small>47%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">4<br><small>2%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">183</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.51);color:#fff;text-align:right">36<br><small>50%</small></td><td style="background:rgba(37, 99, 235, 0.41);color:inherit;text-align:right">28<br><small>39%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">4<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">4<br><small>6%</small></td><td style="text-align:right">72</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">16<br><small>4%</small></td><td style="background:rgba(37, 99, 235, 0.63);color:#fff;text-align:right">260<br><small>65%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">6<br><small>1%</small></td><td style="background:rgba(37, 99, 235, 0.33);color:inherit;text-align:right">119<br><small>30%</small></td><td style="text-align:right">401</td></tr>
</table>

| label | brain | Respond | context, no reply | context kept | dropped | all |
|---|---|---|---|---|---|---|
| **task** | 179 (96%) | 8 (4%) | 0 (0%) | 0 (0%) | 0 (0%) | 187 |
| **chat** | 93 (51%) | 86 (47%) | 0 (0%) | 4 (2%) | 0 (0%) | 183 |
| **nothing** | 36 (50%) | 28 (39%) | 0 (0%) | 4 (6%) | 4 (6%) | 72 |
| **dropped** | 16 (4%) | 260 (65%) | 0 (0%) | 6 (1%) | 119 (30%) | 401 |

- **Asks:** 179 straight to the brain; 8 to Respond, of which 4 handed up by B, 4 answered in chat; 0 context with no reply, 0 context kept, 0 dropped. **183 of 187 reach the brain (97.9%).**

#### The tree, no council

<table>
<caption>Tree, no council (v2): label (rows) by outcome (columns), all</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context, no reply</th><th>context kept</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.88);color:#fff;text-align:right">177<br><small>95%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">3<br><small>2%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">7<br><small>4%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">187</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.52);color:#fff;text-align:right">95<br><small>52%</small></td><td style="background:rgba(37, 99, 235, 0.24);color:inherit;text-align:right">34<br><small>19%</small></td><td style="background:rgba(37, 99, 235, 0.08);color:inherit;text-align:right">1<br><small>1%</small></td><td style="background:rgba(37, 99, 235, 0.21);color:inherit;text-align:right">29<br><small>16%</small></td><td style="background:rgba(37, 99, 235, 0.19);color:inherit;text-align:right">24<br><small>13%</small></td><td style="text-align:right">183</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.52);color:#fff;text-align:right">37<br><small>51%</small></td><td style="background:rgba(37, 99, 235, 0.15);color:inherit;text-align:right">6<br><small>8%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.27);color:inherit;text-align:right">16<br><small>22%</small></td><td style="background:rgba(37, 99, 235, 0.23);color:inherit;text-align:right">13<br><small>18%</small></td><td style="text-align:right">72</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">18<br><small>4%</small></td><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">29<br><small>7%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">8<br><small>2%</small></td><td style="background:rgba(37, 99, 235, 0.16);color:inherit;text-align:right">40<br><small>10%</small></td><td style="background:rgba(37, 99, 235, 0.73);color:#fff;text-align:right">306<br><small>76%</small></td><td style="text-align:right">401</td></tr>
</table>

| label | brain | Respond | context, no reply | context kept | dropped | all |
|---|---|---|---|---|---|---|
| **task** | 177 (95%) | 3 (2%) | 0 (0%) | 7 (4%) | 0 (0%) | 187 |
| **chat** | 95 (52%) | 34 (19%) | 1 (1%) | 29 (16%) | 24 (13%) | 183 |
| **nothing** | 37 (51%) | 6 (8%) | 0 (0%) | 16 (22%) | 13 (18%) | 72 |
| **dropped** | 18 (4%) | 29 (7%) | 8 (2%) | 40 (10%) | 306 (76%) | 401 |

- **Asks:** 177 straight to the brain; 3 to Respond, of which 1 handed up by B, 2 answered in chat; 0 context with no reply, 7 context kept, 0 dropped. **178 of 187 reach the brain (95.2%).**

#### The tree with council

<table>
<caption>Tree with council (v2, margin 0.25): label (rows) by outcome (columns), all</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context, no reply</th><th>context kept</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.89);color:#fff;text-align:right">178<br><small>95%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">4<br><small>2%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">5<br><small>3%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">187</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.55);color:#fff;text-align:right">101<br><small>55%</small></td><td style="background:rgba(37, 99, 235, 0.26);color:inherit;text-align:right">38<br><small>21%</small></td><td style="background:rgba(37, 99, 235, 0.08);color:inherit;text-align:right">1<br><small>1%</small></td><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">20<br><small>11%</small></td><td style="background:rgba(37, 99, 235, 0.19);color:inherit;text-align:right">23<br><small>13%</small></td><td style="text-align:right">183</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.52);color:#fff;text-align:right">37<br><small>51%</small></td><td style="background:rgba(37, 99, 235, 0.22);color:inherit;text-align:right">12<br><small>17%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.21);color:inherit;text-align:right">11<br><small>15%</small></td><td style="background:rgba(37, 99, 235, 0.22);color:inherit;text-align:right">12<br><small>17%</small></td><td style="text-align:right">72</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">23<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.15);color:inherit;text-align:right">35<br><small>9%</small></td><td style="background:rgba(37, 99, 235, 0.08);color:inherit;text-align:right">2<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">15<br><small>4%</small></td><td style="background:rgba(37, 99, 235, 0.77);color:#fff;text-align:right">326<br><small>81%</small></td><td style="text-align:right">401</td></tr>
</table>

| label | brain | Respond | context, no reply | context kept | dropped | all |
|---|---|---|---|---|---|---|
| **task** | 178 (95%) | 4 (2%) | 0 (0%) | 5 (3%) | 0 (0%) | 187 |
| **chat** | 101 (55%) | 38 (21%) | 1 (1%) | 20 (11%) | 23 (13%) | 183 |
| **nothing** | 37 (51%) | 12 (17%) | 0 (0%) | 11 (15%) | 12 (17%) | 72 |
| **dropped** | 23 (6%) | 35 (9%) | 2 (0%) | 15 (4%) | 326 (81%) | 401 |

- **Asks:** 178 straight to the brain; 4 to Respond, of which 2 handed up by B, 2 answered in chat; 0 context with no reply, 5 context kept, 0 dropped. **180 of 187 reach the brain (96.3%).**

## Asks, cost and calls

An ask sent to Respond reaches the brain when Respond B handed that line up. Respond B is the gate bench's option B, now Respond's own. Its recorded decision is known for every ask (all 187 are in its 592-line set), so no ask was left unknown.

For cost, a Respond line outside B's set (only `dropped` lines) is counted at B's hand-up rate on the dropped lines it ran (4 of 150). Those lines: Jev today 159, no council 20, council 25.

- **Cost** = brain calls × $0.0085 + Respond calls × $0.0015 + Jev's input tokens as measured × $42 per billion.
- **Brain calls** = lines sent straight to the brain + Respond lines B handed up.
- **Batch calls** are the brain's batch timer on the context each variant leaves it (`tree_bench.py batches`). They are outside the formula, and shown beside it.

| | View | Asks straight to the brain | + via Respond B | = asks reaching the brain | Asks in context | Asks dropped | Asks answered in chat | Brain calls | Respond calls | Jev | **Cost** | Batch calls | Cost with batches |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Jev today | second | 81 | 3 | **84 of 87** | 0 | 0 | 3 | 171 | 194 | $0.139 | **$1.89** | 0 | $1.89 |
| Tree, no council (v2) | second | 81 | 1 | **82 of 87** | 4 | 0 | 1 | 161 | 37 | $0.161 | **$1.59** | 11 | $1.68 |
| Tree with council (v2, margin 0.25) | second | 82 | 2 | **84 of 87** | 2 | 0 | 1 | 170 | 48 | $0.161 | **$1.68** | 7 | $1.74 |
| Jev today | all | 179 | 4 | **183 of 187** | 0 | 0 | 4 | 347 | 382 | $0.272 | **$3.80** | 14 | $3.92 |
| Tree, no council (v2) | all | 177 | 1 | **178 of 187** | 7 | 0 | 2 | 333 | 72 | $0.319 | **$3.25** | 32 | $3.53 |
| Tree with council (v2, margin 0.25) | all | 178 | 2 | **180 of 187** | 5 | 0 | 2 | 350 | 89 | $0.319 | **$3.42** | 25 | $3.64 |

**Asked as a 3-question call:** the no-council tree needs only A's three questions. On the sample, a 3-question call took 86% of a 9-question call's input tokens. That puts Jev at about $0.276 a session and the no-council tree at **$3.21** ($3.48 with batch calls).

**Jev for the tree:** $0.319 a session for the 9-question call, against Jev today's $0.272. Jev today asks one question per line, but it also asks again about up to five recent `not_relevant` lines in the same call.

### Every ask that does not reach the brain

**Jev today**: 4

- `06:13:44` Styrkaeopteryx [first] → **Respond (B answered in chat)**: Oh, it's still working on the castle. away from you. I found an open planes area so it might be easier. It's not made. Like I don't see any…
- `07:19:36` KrownJM [second] → **Respond (B answered in chat)**: When does we realize is it can we just like Oh no. Bedrock is protected.
- `07:32:54` Eclipszr [second] → **Respond (B answered in chat)**: Like I said, I don't have a spawn.
- `07:38:23` Styrkaeopteryx [second] → **Respond (B answered in chat)**: I'm sorry. That claw made Gallic gun destroy everything in the map.

**Tree, no council (v2)**: 9

- `06:14:04` KrownJM [first] → **Respond (B answered in chat)**: Can you teach me about both of us to Stirka? Also Matthew Matt Matthew are you recording? Can you record Bo? 'Cause I wanna post it on shit…
- `06:16:25` Eclipszr [first] → **context kept**: Say yes to turn on spooky mode. Both of you guys turn on spooky mode
- `06:28:11` Styrkaeopteryx [first] → **context kept**: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:38:14` KrownJM [first] → **context kept**: Okay, can we like stay together and actually like clear out some like one place to live?
- `07:14:10` KrownJM [second] → **context kept**: I have to make sure the ice steam also goes through blocks. Holy sit, what the fuck happened here? I think so cool. This is the hitchhik.
- `07:15:38` KrownJM [second] → **context kept**: Wait, I can't I just I just froze that IC pierced out we got to stop. I feel the eyes don't what are you what are you break it, break it? D…
- `07:19:36` KrownJM [second] → **Respond (B answered in chat)**: When does we realize is it can we just like Oh no. Bedrock is protected.
- `07:24:33` Styrkaeopteryx [second] → **context kept**: I don't want to get put in a fucking pit again, please.
- `07:38:23` Styrkaeopteryx [second] → **context kept**: I'm sorry. That claw made Gallic gun destroy everything in the map.

**Tree with council (v2, margin 0.25)**: 7

- `06:14:04` KrownJM [first] → **Respond (B answered in chat)**: Can you teach me about both of us to Stirka? Also Matthew Matt Matthew are you recording? Can you record Bo? 'Cause I wanna post it on shit…
- `06:16:25` Eclipszr [first] → **context kept**: Say yes to turn on spooky mode. Both of you guys turn on spooky mode
- `06:28:11` Styrkaeopteryx [first] → **context kept**: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:38:14` KrownJM [first] → **context kept**: Okay, can we like stay together and actually like clear out some like one place to live?
- `07:14:10` KrownJM [second] → **context kept**: I have to make sure the ice steam also goes through blocks. Holy sit, what the fuck happened here? I think so cool. This is the hitchhik.
- `07:19:36` KrownJM [second] → **Respond (B answered in chat)**: When does we realize is it can we just like Oh no. Bedrock is protected.
- `07:38:23` Styrkaeopteryx [second] → **context kept**: I'm sorry. That claw made Gallic gun destroy everything in the map.

**Tasks citing those lines.** Most of the tree's context asks are the line a task came from, said to another player or to no one, which the old conversation still acted on:

- 06:28:11: T49, cut the lightning staff's delay.
- 06:38:14: T61, gather the players and clear the mobs.
- 07:14:10: T122, Ice Beam goes through blocks.
- 07:15:38: T128, freeze liquids, fix Ice Beam damage.
- 07:24:33: T138, get Styrkaeopteryx out of the pit.

06:16:25 and 07:38:23 are later lines a task also cites.

With the tree, these lines reach the brain as context, not as calls of their own. 07:38:23 ("That claw made Gallic gun destroy everything", with "claw" for Claude) is missed by every option of the gate bench too.

## Latency per Jev call

| Call | Where measured | Calls | Median | p90 |
|---|---|---|---|---|
| today's single question | the 100-line sample, the three calls in turn | 100 | 218 ms | 241 ms |
| A's three questions | the 100-line sample, the three calls in turn | 100 | 226 ms | 258 ms |
| all nine questions | the 100-line sample, the three calls in turn | 100 | 232 ms | 259 ms |
| all nine questions | the v2 run | 843 | 233 ms | 263 ms |
| today's question, with the lines asked again | classes.py's run | 843 | 226 ms | 254 ms |

Input tokens a call, median on the sample: today's question 7,262, three questions 7,781, nine questions 8,930.

## Is one 9-question call fair to the no-council tree?

The test: A's three questions asked alone, against the same three inside the 9-question call. It ran on 100 lines of the first half, picked with seed 20261010, with each line's three calls made one after another and their order rotating. The 9-question call was asked twice: in that loop, and in the main v1 run.

| A's question | 3 vs 9, same loop | 3 vs 9, main run | 9 vs 9 |
|---|---|---|---|
| `l1_a` | 96 of 100 | 97 of 100 | 97 of 100 |
| `l2_a` | 98 of 100 | 97 of 100 | 97 of 100 |
| `l3_a` | 97 of 100 | 97 of 100 | 100 of 100 |

Walking A's answers, the outcome was the same from both calls on 98 of 100 lines. The other questions in the call change A's answers no more than asking twice does. The gate bench found the same: 98% between separate calls and one shared call.

## Level 1 and level 2 mistakes

### Level 1 says the AI for lines labelled chat or nothing

| | `chat` | `nothing` | `dropped` |
|---|---|---|---|
| Jev today (`action_oriented` or `conversational`) | 179 of 183 (98%) | 64 of 72 (89%) | 276 of 401 (69%) |
| The tree, no council | 130 of 183 (71%) | 43 of 72 (60%) | 55 of 401 (14%) |
| The tree with council | 140 of 183 (77%) | 49 of 72 (68%) | 60 of 401 (15%) |

Many `chat` and `nothing` lines are said to the AI: requests the old conversation declined, follow-ups, questions about an item it gave.

I read all 85 held-out lines labelled chat or nothing that the no-council tree's level 1 sends to the AI, each with the lines around it:

- 51 are said to the AI.
- 25 are clearly not. 15 of those go on to the brain and 10 to Respond.
- 9 cannot be told.

That makes about 30% of these sends mistakes, about 15 wasted brain calls in the half. In a 20-line sample of the council's, 8 look wrong.

**Ten that look wrong** (no council):

- `06:58:20` Eclipszr [chat]: “Fine.” — answers Styrkaeopteryx, who just spoke; sent to the brain
- `06:58:51` Eclipszr [chat]: “Can't be damaged and I can't mortgage. I can make them fall but I can't do damage to” — answers Krown's question about the withers; sent to the brain
- `06:59:16` KrownJM [chat]: “Well, you can do that with them, but you barely do demoers of them.” — said to Styrkaeopteryx about the withers; sent to the brain
- `07:03:37` KrownJM [chat]: “I didn't do anything. I'm just like moving. I keep getting killed from. Yeah, tomorrow, what are you?” — to the other players, about being killed; sent to the brain
- `07:19:01` Eclipszr [nothing]: “Is it per player? Yeah, I speak per player. Yeah, I have to hold it.” — to Krown, about how to hold the item; sent to Respond
- `07:23:54` Eclipszr [chat]: “Click or Crouch Free Times on Trade.” — reading the AI's message aloud; sent to the brain
- `07:29:07` Eclipszr [nothing]: “Yes.” — a yes after Krown's line, to no one in particular; sent to the brain
- `07:33:47` Styrkaeopteryx [chat]: “Rome, my gallegun do any damage. Actually doesn't work at all now. Oh, there we go. I told Claude to fix it.” — to another player ("Rome"), about the AI; sent to the brain
- `07:35:29` KrownJM [nothing]: “Six minutes.” — to the players, about the time; sent to the brain
- `07:39:34` KrownJM [chat]: “Fix it, sometimes fix it. I just try to try differently.” — answers Eclipszr about his broken weapon; sent to the brain

### Level 2: keeps and drops that look wrong

Level 2 saw 435 lines with no council. It kept 92 and dropped 343. The council's level 2 saw 412 lines, kept 51 and dropped 361.

| | Kept: task / chat / nothing / dropped | Dropped: task / chat / nothing / dropped |
|---|---|---|
| The tree, no council | 7 / 29 / 16 / 40 | 0 / 24 / 13 / 306 |
| The tree with council | 5 / 20 / 11 / 15 | 0 / 23 / 12 / 326 |

- **Keeps** (no council): I read all 45 held-out keeps, and 19 look wrong (42%).
  - They are chatter about the moment kept as a fact about a player or the world, or the developers' own talk about cost kept as a plan.
  - A wrong keep costs nothing in the formula, but it lengthens the brain's context and can feed a batch call.
  - The council: 7 of 20 sampled keeps look wrong.
- **Drops** (no council): none of a random 20 of the 179 held-out drops looks wrong.
  - A search of all 343 drops found 9 that look wrong, about 3%. Several are borderline.
  - They are complaints that something the AI made misbehaves, and facts that stay true.
  - The council: 3 of 20 sampled drops look wrong.

**Ten keeps that look wrong** (no council, held-out half):

- `06:58:50` KrownJM [chat]: “You guys still haven't killed the withers? What do we can't do damn it? Why? How?” — a question to the players about this fight
- `07:18:50` Eclipszr [chat]: “Right, right click.” — telling Krown which button to press, this second
- `07:18:58` Styrkaeopteryx [chat]: “What the? I did not click.” — a reaction
- `07:21:59` Eclipszr [dropped]: “I can't control the power. Oh wait.” — a reaction
- `07:24:28` Eclipszr [dropped]: “can bury Maybe I raven in front of you. Wait, maybe I could box you in.” — banter in a fight
- `07:29:11` Styrkaeopteryx [nothing]: “No one Oh my god it's doing like both at the same time.” — a reaction to an effect, this second
- `07:34:55` KrownJM [dropped]: “Okay, do that again. We'll do that again one more time tomorrow so that we can get the clip bro because maybe you fucked it up. He killed me for no reason.” — about filming a clip tomorrow
- `07:38:33` KrownJM [chat]: “There's the ice castle now let's see it. We sit this in a war zone.” — the moment
- `07:44:52` KrownJM [dropped]: “Can't see anything? Oh my god, was my desktop audio corrupted the whole time? It might have been. God feels okay. Hey, what is happening? Oh the ice cream. I l…” — the developer's audio, then the moment
- `07:49:38` KrownJM [dropped]: “Yeah, I have to make it a lot cheaper. I can do it. This was like very cheap, a very quick version of purpose. Yeah. I'm just kind of shocked it even work this…” — the developer on the product's cost, kept as a plan

**Drops that look wrong** (no council, all 843 lines): these 9 are every one I found.

- `05:57:26` KrownJM [nothing]: “He's trapped.” — a player in trouble, which the lookout names
- `06:26:00` Styrkaeopteryx [dropped]: “How did you die of fall damage? Oh you got blown up and then fell, I see. Yeah because I think it's just spawns like thirty DNT to do the explosion.” — how an AI weapon works (borderline)
- `06:33:43` Styrkaeopteryx [chat]: “Oh, I see what's happening. It's like spawning stuff on top of me. It's giving me like speed boosts.” — something the AI set up is misbehaving
- `06:42:36` Eclipszr [dropped]: “I don't I didn't do anything. Oh They reflected off of me because I've been fantastic. Tree headed dragon.” — the AI's deflect power working (borderline)
- `07:04:49` Eclipszr [dropped]: “Boy is I don't even have my dadwatch anymore at the back.” — an item gone (borderline)
- `07:36:17` Styrkaeopteryx [dropped]: “Wow. I realized how Claude, Gallant Gun kills no matter where I'm aiming. I was looking the other way when I killed you both.” — a complaint that an AI weapon kills regardless of aim; a task to fix it followed
- `07:41:29` Styrkaeopteryx [chat]: “I actually didn't I'm not clicking. just repeatedly shooting in front of me.” — an AI weapon firing on its own
- `07:42:27` KrownJM [chat]: “Well, I think since the bedrock is ice now it just goes straight to the void.” — a lasting fact: frozen bedrock leads to the void
- `07:47:07` Styrkaeopteryx [dropped]: “Yeah. I don't know. Claude doesn't like water.” — a complaint about the AI's work (borderline)

## The wording versions scored

Two wordings were scored, each on the whole first half (421 lines, 100 asks). Only the second half's numbers above are held out.

- **v1:** the drafts, as given. Every option is described in the same balanced style; no option is a default.
- **v2:** v1 with three general changes, written from patterns in first-half answers. No line is named or fitted.
  1. **Level 3.** A request counts however casually or half-heard it is said. A yes, a no or a choice that answers what the AI asked before doing something is part of the ask (Jev's own rule). A complaint to the AI about its work is a fix. An answer is one with nothing to change in the game.
  2. **Level 2, member A.** A kept fact must be a lasting one. What someone is doing or seeing this second is the moment, so the two options split on time alone.
  3. **Level 2, members B and C.** A plan, a decision or a rule, and a problem with something the AI made, count as lasting information, as they already do for A.

**v2 was chosen for both variants.**

- With no council, it gets one more ask to the brain (96 against 95 of 100) and sends follow-up answers and complaints to the brain. It also drops moment chatter v1 kept.
- It costs $0.04 more on the half: 6 more brain calls, 9 fewer Respond calls.
- v1 was not run on the second half.

**The margin.** It is the council's clash margin, in summed confidence between the top two outcomes. **0.25** is the cheapest margin on the first half that gets as many asks to the brain as no council does there (96).

- Margins from 0.2 to 0.5 all give 96. 0.25 is cheaper by one brain call.
- A clash at level 1 goes to the AI, at level 2 to keep, and at level 3 to Respond. Larger margins gain an ask or two and add brain and Respond calls.

| Version | Variant | Margin | Asks to brain | via B | Asks lost: context / dropped | Asks answered by Respond | Brain calls | Respond calls | Clashes l1 / l2 / l3 | Cost |
|---|---|---|---|---|---|---|---|---|---|---|
| v1 | tree | - | 95 | 0 | 3 / 0 | 2 | 165 | 44 | 0 / 0 / 0 | $1.63 |
| v1 | council | 0.0 | 94 | 0 | 2 / 2 | 2 | 168 | 49 | 0 / 0 / 0 | $1.66 |
| v1 | council | 0.05 | 94 | 0 | 2 / 2 | 2 | 168 | 49 | 1 / 0 / 1 | $1.66 |
| v1 | council | 0.1 | 94 | 0 | 2 / 2 | 2 | 168 | 50 | 4 / 1 / 2 | $1.66 |
| v1 | council | 0.15 | 94 | 0 | 2 / 2 | 2 | 167 | 51 | 4 / 1 / 3 | $1.65 |
| v1 | council | 0.2 | 94 | 0 | 3 / 1 | 2 | 168 | 52 | 6 / 2 / 5 | $1.66 |
| v1 | council | 0.25 | 95 | 0 | 2 / 1 | 2 | 169 | 54 | 9 / 1 / 8 | $1.68 |
| v1 | council | 0.3 | 95 | 0 | 2 / 1 | 2 | 170 | 54 | 10 / 1 / 11 | $1.68 |
| v1 | council | 0.4 | 95 | 0 | 2 / 1 | 2 | 171 | 54 | 13 / 1 / 13 | $1.69 |
| v1 | council | 0.5 | 95 | 0 | 3 / 0 | 2 | 171 | 60 | 21 / 3 / 17 | $1.70 |
| v1 | council | 0.6 | 95 | 0 | 3 / 0 | 2 | 171 | 64 | 31 / 5 / 19 | $1.71 |
| v1 | council | 0.8 | 95 | 0 | 3 / 0 | 2 | 172 | 69 | 45 / 10 / 24 | $1.72 |
| v1 | council | 1.0 | 96 | 0 | 2 / 0 | 2 | 171 | 74 | 55 / 14 / 28 | $1.72 |
| v2 | tree | - | 96 | 0 | 3 / 0 | 1 | 171 | 35 | 0 / 0 / 0 | $1.67 |
| v2 | council | 0.0 | 95 | 0 | 4 / 0 | 1 | 179 | 37 | 1 / 0 / 0 | $1.74 |
| v2 | council | 0.05 | 95 | 0 | 4 / 0 | 1 | 178 | 38 | 1 / 0 / 2 | $1.73 |
| v2 | council | 0.1 | 95 | 0 | 4 / 0 | 1 | 178 | 38 | 1 / 1 / 2 | $1.73 |
| v2 | council | 0.15 | 95 | 0 | 4 / 0 | 1 | 178 | 39 | 4 / 1 / 2 | $1.73 |
| v2 | council | 0.2 | 96 | 0 | 3 / 0 | 1 | 180 | 40 | 8 / 0 / 4 | $1.75 |
| v2 | council | 0.25 | 96 | 0 | 3 / 0 | 1 | 179 | 41 | 10 / 0 / 5 | $1.74 |
| v2 | council | 0.3 | 96 | 0 | 3 / 0 | 1 | 179 | 43 | 15 / 1 / 6 | $1.75 |
| v2 | council | 0.4 | 96 | 0 | 3 / 0 | 1 | 179 | 45 | 18 / 3 / 10 | $1.75 |
| v2 | council | 0.5 | 96 | 0 | 3 / 0 | 1 | 179 | 46 | 21 / 5 / 10 | $1.75 |
| v2 | council | 0.6 | 96 | 0 | 3 / 0 | 1 | 181 | 52 | 31 / 7 / 13 | $1.78 |
| v2 | council | 0.8 | 97 | 0 | 2 / 0 | 1 | 181 | 56 | 43 / 10 / 20 | $1.78 |
| v2 | council | 1.0 | 98 | 0 | 1 / 0 | 1 | 180 | 59 | 54 / 12 / 25 | $1.78 |
| Jev today | | | 98 | 1 | 0 / 0 | 1 | 176 | 188 | | $1.91 |

## The questions (v2)

Each question is a `choice` about line [1], with the instructions `Line [1], <player> at <time>: “<the line>”. <question>`. All nine are asked in one call, with the filter's state as Jev has it today:

- Jev's rules;
- the brain's lookout;
- the brain's 70-action menu (Jev today's run had 67; the three incantation actions came since);
- three hours of history with the brain's AI rows, in the 8,000-token state.

**Level 1, who the line is said to**

| Member | Question | Option | Goes to | Description |
|---|---|---|---|---|
| A | Who is the speaker talking to? | `the_ai` | level 3 | Said to the AI. It names the AI ("Claude", "AI", or a name speech-to-text misheard), asks or tells the AI something, or answers or follows up something the AI said or did. |
|  |  | `a_player` | level 2 | Said to another player. Talk between players, even when it is about the AI or the game. |
|  |  | `themselves` | level 2 | Said to themselves: thinking aloud, or narrating what they are doing, to no one in particular. |
|  |  | `no_one` | level 2 | Not really said to anyone: a reaction, an exclamation, filler, or speech too garbled to tell. |
| B | Who does the speaker expect to answer? | `the_ai` | level 3 | The AI. The speaker expects the AI to answer or to act: they named it, asked or told it something, or are replying to what it said or did. |
|  |  | `another_player` | level 2 | Another player. The speaker expects a player to answer, react or go along, even when the AI or the game is the topic. |
|  |  | `nobody` | level 2 | Nobody. No answer is expected: thinking aloud, narrating, a reaction, filler or garbled speech. |
| C | What kind of line is it? | `request_or_question_to_the_ai` | level 3 | A request or a question to the AI, or a reply to it: it asks the AI to do or answer something, or answers or follows up what the AI said or did. |
|  |  | `talk_between_players` | level 2 | Talk between players: said to another player, including talk about the AI or the game. |
|  |  | `thinking_aloud` | level 2 | Thinking aloud: the speaker narrates what they are doing, or muses to no one in particular. |
|  |  | `only_a_reaction` | level 2 | Only a reaction: an exclamation, a laugh, swearing, filler, or speech too garbled to tell. |

**Level 2, worth keeping? (a line not said to the AI)**

| Member | Question | Option | Goes to | Description |
|---|---|---|---|---|
| A | What in this line is worth keeping for the AI? | `ai_work_bad` | context (alone) | Something the AI made or did isn't working, is too weak or too strong, is in the wrong place, or is gone. |
|  |  | `plan_or_rule` | context | A plan, a decision or a standing rule for later: what the players will do, what is allowed or banned. |
|  |  | `player_or_world_context` | context | A lasting fact about a player or the world, still true and useful in five minutes: where a base is, what someone likes or hates, who is on whose team, what someone is trying to do, what is where in the world. |
|  |  | `the_moment` | dropped | Talk about this moment only: what is happening right now, what someone is doing or seeing this second, chatter, jokes, banter that won't matter in five minutes. |
|  |  | `reaction` | dropped | Nothing but a reaction: an exclamation, swearing, "nice", "oh my god", "wow". |
|  |  | `garbled` | dropped | Speech-to-text garbage: words that don't make sense together. |
| B | Will this line still matter in five minutes? | `yes_lasting` | context | Yes, it says something lasting: a plan, a decision or a rule for what the players will do, a fact about a player or the world, or a problem with something the AI made, that will still be true and useful in five minutes. |
|  |  | `no_this_moment` | dropped | No, it is about this moment only: what is happening right now, chatter, a joke, a reaction, or words that make no sense. |
| C | What kind of line is it? | `information` | context | Information: a plan, a decision or a rule, a fact about a player or the world, or a problem with something the AI made: something the AI could use later. |
|  |  | `chatter` | dropped | Chatter: banter, jokes, reactions, or running commentary on what is happening right now. |
|  |  | `unclear_speech` | dropped | Unclear speech: speech-to-text garbage, or fragments whose meaning can't be made out. |

**Level 3, what the speaker wants (a line said to the AI)**

| Member | Question | Option | Goes to | Description |
|---|---|---|---|---|
| A | What does the speaker want from the AI? | `a_change` | brain | Something to happen in the game, however casually or half-heard it is said: make, give, build, spawn, teleport, change or remove something, or a yes, a no or a choice answering what the AI asked before doing it. |
|  |  | `a_fix` | brain | A change to something the AI already made or did: fix it, undo it, stop it, or make it stronger, weaker or bigger, or a complaint to the AI that it isn't working, is too weak or too strong, or is gone. |
|  |  | `an_answer` | Respond | An answer: a question about the game, Minecraft, the players or the AI itself, which a chat reply settles, with nothing to change in the game. |
|  |  | `just_chatting` | Respond | Conversation with the AI: a greeting, a joke, an opinion, a comment on what's going on, with nothing to do in the game. |
|  |  | `only_thanks` | context, no reply | Nothing to answer: only thanks, "ok", "cool", "wow" or another acknowledgement. |
| B | If the AI only replied in chat and changed nothing in the game, would the speaker be satisfied? | `no_something_must_change` | brain | No, something must change in the game: the speaker wants something made, given, moved, fixed, undone or stopped, or is answering what the AI asked before doing it. |
|  |  | `yes_a_reply_is_enough` | Respond | Yes, a reply is enough: the speaker wants an answer, an opinion or a bit of conversation. |
|  |  | `nothing_needs_answering` | context, no reply | Nothing needs answering: the line is only thanks, an acknowledgement or a reaction. |
| C | What kind of line is it? | `a_request` | brain | A request, however casually or half-heard it is said: it asks the AI to do, make, give, change or remove something in the game, or answers what the AI asked before doing it. |
|  |  | `a_complaint_about_the_ais_work` | brain | A complaint about the AI's work: something the AI made or did is wrong, broken, too weak or too strong, or gone. |
|  |  | `a_question` | Respond | A question: it asks the AI about the game, Minecraft, the players or the AI itself. |
|  |  | `small_talk` | Respond | Small talk: a greeting, a joke, an opinion or a comment, with nothing to do in the game. |
|  |  | `only_thanks` | context, no reply | Only thanks: thanks, "ok", "cool" or another acknowledgement, with nothing to answer. |

**How the council decides.**

- Each member votes for the outcome its answer maps to, weighted by its confidence. The most weight wins.
- At level 2, A's `ai_work_bad` keeps the line whatever B and C say.
- A clash (a tie, or the top two within 0.25) goes to the cheaper mistake: the AI at level 1, keep at level 2, Respond at level 3.
- No council reads A alone.

## How it was run

- **The lines:** all 843 lines said on 2026-10-08, in time order, each with the state Jev had when it judged the line then, as `classes.py` builds it.
  - Only the line itself is asked about. Unlike Jev today, no `not_relevant` line is asked again.
  - No call failed.
- **The halves:** the first 421 lines (05:26–06:57, 100 asks) for the wording and the margin, and the last 422 (06:57–07:59, 87 asks) held out.
- **The runs:**
  - A 20-line pilot: 6,100 tokens a call, $0.005.
  - v1 on the first half.
  - The 100-line sample: today's question, A's three questions and all nine, per line.
  - v2 on the first half; then, with the wording and margin fixed, v2 on the second half.
- **Jev today:** `classes.py`'s `jev` run of 2026-10-10, flips applied.
- **Respond B:** the gate bench's B rows. B ran with the brain's context under Jev today's classes, not the tree's, so its decisions are an approximation for the tree.
- **TypeSafe spend: 13.6M input tokens, $0.57,** about two sessions' worth.

| Run | Lines | Tokens | Cost |
|---|---|---|---|
| v1, first half (with the pilot) | 421 | 3.70M | $0.155 |
| The sample, 3 calls a line | 100 | 2.35M | $0.099 |
| v2, all 843 | 843 | 7.59M | $0.319 |

## Run it

```sh
(cd brain && uv sync --frozen --inexact)
PY=brain/.venv/bin/python; DB=DIR/vfg/vfg.db                 # the playtest's database, as README.md says
JEV=CLASSES_OUT/jev.jsonl; B=GATE_OUT/B/rows.jsonl          # classes.py jev's run; the gate bench's option B
$PY integration/replay/tree_bench.py run v1 --db $DB --out OUT --half first --limit 20   # the pilot
$PY integration/replay/tree_bench.py run v1 --db $DB --out OUT --half first              # goes on
$PY integration/replay/tree_bench.py sample v1 --db $DB --out OUT --n 100               # 1, 3 and 9 questions
$PY integration/replay/tree_bench.py run v2 --db $DB --out OUT --half first
python3 integration/replay/tree_score.py OUT --jev $JEV --gate-b $B --tune              # OUT/tune.md
$PY integration/replay/tree_bench.py run v2 --db $DB --out OUT --half second             # held out
$PY integration/replay/tree_bench.py batches v2 --db $DB --out OUT --jev $JEV --margin 0.25
python3 integration/replay/tree_score.py OUT --jev $JEV --gate-b $B --final v2 --margin 0.25   # OUT/score.md
python3 integration/replay/tree_score.py OUT --jev $JEV --gate-b $B --final v2 --margin 0.25 \
    --judge 20 --db $DB                                                                  # OUT/judge.md, to read
```
