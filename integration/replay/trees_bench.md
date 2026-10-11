# Five trees: five ways to route a line in one Jev call

A tree asks Jev **class questions about each line, all in one call**, and code walks the answers to one of four outcomes:

- the **brain** (GPT-6.1 Sol, about $0.0085 a call);
- **Respond** ($0.0015), which can hand the line up to the brain;
- **context**, stored for a memory system later; no model reads it, $0;
- **dropped**, $0.

The goal is cost: the brain only for lines that explicitly ask the AI to do something, Respond only for talk aimed at the AI, everything else context or dropped.

Five genuinely different ways to build the tree ran on the 843 lines of the 2026-10-08 playtest, each with the state Jev builds today, in time order. They are compared with **Jev today** on the same lines (`trees_bench.py`, scored by `trees_score.py`):

- **A. Who, then what** (3 questions). The tree bench's v2, member A, reused and not run again. Who is the line said to? Then, said to the AI, what does the speaker want; said to anyone else, is it worth keeping?
- **B. Kind first** (2 questions). One six-way question, "what kind of line is it?", each kind with one home. Talk to a player or to themselves goes on to "lasting, or this moment?".
- **C. Cheapest first, an elimination ladder** (4 questions). Four rungs asked at once; code takes the first that fires: noise (dropped), an explicit request to the AI (brain), said to the AI (Respond), something lasting (context), otherwise dropped.
- **D. Consequence first, two keys** (2 questions). "What should happen with this line?" (act / answer / remember / ignore) and "if the AI only replied, how would the speaker feel?". The brain only when both say something must change.
- **E. Observable signals, the tree in code** (4 questions). Who the line addresses, its form, its topic, how long it stays true. Five rules in code combine them.

Each question's options are balanced classes, each with its own description: no default option, no yes/no question. Model and Jev calls only; nothing ran in a game.

## Verdict: D, consequence first

- **D is the cheapest approach that keeps the asks.**
  - All 843 lines, a 2.5 h session: **$3.01, against Jev today's $3.80 (−21%) and A's $3.21 (−6%)**. 176 of 187 asks reach the brain, against 178 for A and B and 183 for Jev today.
  - The held-out second half: **$1.46**, against $1.89 for Jev today and $1.57 for A. 80 of 87 asks reach the brain, against 82 (A), 83 (B) and 84 (Jev today).
  - D asked a second time on the held-out half gave the same outcome on 419 of 422 lines: 80 then 81 asks, $1.46 then $1.48. The 1–3 ask gaps between A, B, C and D are about that noise. D's 31 fewer brain calls than A a session are not.
- **Its saving comes from the first question, not the second key.**
  - "The AI should answer in chat" takes the questions and complaints said to the AI that A's level 3 calls a change or a fix. "Ignore it" takes the chatter.
  - The second key overruled the first on only 7 of 843 lines.
- **It drops the most of what the old filter dropped: 90%.** But it also drops 4 asks outright:
  - one the speaker took back ("oh wait, never mind");
  - three implied asks said to a player or to no one.
- **E is the cheapest, but loses too many asks.** $2.87 a session, but only 167 of 187 asks reach the brain. 16 of its 20 lost asks land in context. Its rules can't see intent: "But my gallic gun doesn't work anymore" is a statement about the AI's work, so it is stored, not acted on.
- **B matches A in one question.** 178 of 187 asks, $3.17.
  - It never drops an ask; all 9 of its lost asks are in context.
  - Its "comment on the AI's work" kind takes 178 lines, so it drops only 53% of the old filter's dropped lines. That is free now that models don't read context.
- **C sends implied asks to Respond, which hands most of them up.** 176 of 187 asks at $3.11, with the most Respond calls of any tree (158).
- **The trees have converged.**
  - A to D are within $0.20 a session and 2 asks.
  - What's left of the brain bill is mostly real requests. D sends 55 held-out talk lines (`chat` or `nothing`) to the brain. Reading them, about 35 are requests or follow-ups to the AI, about 8 are implied fixes or unclear, and about 12 look wrong: about $0.10 of the half.
- **No combination pays.** Scored offline, none beats D alone on the first half:
  - a net from B or C catches no ask there;
  - a third key (B, C, or A's "who") saves 0–4 cents and loses 1–2 asks.
- **Latency is flat:** 219–226 ms median, 243–258 ms p90.

## Side by side

Cost per 2.5 h session = brain calls × $0.0085 + Respond calls × $0.0015 + Jev's input tokens as measured × $42 per billion.

- **Brain calls** are lines sent straight to the brain plus the Respond lines that Respond B handed up.
- **Lost** asks are asks that never reach the brain. Each is one of three:
  - answered in chat by Respond;
  - left in context;
  - dropped.
- **Talk** is the lines labelled `chat` or `nothing`.

| | View | Cost | Brain calls | Respond calls | Jev | Asks to brain direct | + via B | = reach | Lost: chat / context / dropped | Talk sent to the brain | Old filter's dropped, dropped | Latency median / p90 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Jev today | second | **$1.89** | 171 | 194 | $0.139 | 81 | 3 | **84 of 87** | 3 / 0 / 0 | 64 of 132 (48%) | 63 of 203 (31%) | 226 / 254 ms |
| A (v2) | second | **$1.57** | 161 | 37 | $0.140 | 81 | 1 | **82 of 87** | 1 / 4 / 0 | 62 of 132 (47%) | 158 of 203 (78%) | 226 / 258 ms |
| B (B2) | second | **$1.55** | 157 | 50 | $0.136 | 80 | 3 | **83 of 87** | 0 / 4 / 0 | 55 of 132 (42%) | 105 of 203 (52%) | 221 / 246 ms |
| C (C1) | second | **$1.51** | 148 | 80 | $0.141 | 74 | 7 | **81 of 87** | 5 / 0 / 1 | 51 of 132 (39%) | 164 of 203 (81%) | 222 / 250 ms |
| D (D2) | second | **$1.46** | 145 | 62 | $0.133 | 75 | 5 | **80 of 87** | 2 / 2 / 3 | 55 of 132 (42%) | 184 of 203 (91%) | 219 / 243 ms |
| E (E1/r2) | second | **$1.38** | 136 | 53 | $0.138 | 70 | 6 | **76 of 87** | 0 / 10 / 1 | 41 of 132 (31%) | 126 of 203 (62%) | 221 / 246 ms |
| Jev today | all | **$3.80** | 347 | 382 | $0.272 | 179 | 4 | **183 of 187** | 4 / 0 / 0 | 129 of 255 (51%) | 119 of 401 (30%) | 226 / 254 ms |
| A (v2) | all | **$3.21** | 333 | 72 | $0.276 | 177 | 1 | **178 of 187** | 2 / 7 / 0 | 132 of 255 (52%) | 306 of 401 (76%) | 226 / 258 ms |
| B (B2) | all | **$3.17** | 325 | 95 | $0.267 | 175 | 3 | **178 of 187** | 0 / 9 / 0 | 117 of 255 (46%) | 212 of 401 (53%) | 221 / 246 ms |
| C (C1) | all | **$3.11** | 305 | 158 | $0.278 | 165 | 11 | **176 of 187** | 6 / 2 / 3 | 103 of 255 (40%) | 310 of 401 (77%) | 222 / 250 ms |
| D (D2) | all | **$3.01** | 302 | 121 | $0.261 | 170 | 6 | **176 of 187** | 4 / 3 / 4 | 103 of 255 (40%) | 362 of 401 (90%) | 219 / 243 ms |
| E (E1/r2) | all | **$2.87** | 288 | 102 | $0.272 | 157 | 10 | **167 of 187** | 2 / 16 / 2 | 85 of 255 (33%) | 235 of 401 (59%) | 221 / 246 ms |

**A's Jev** is the 9-question call's tokens × 0.86, the share a 3-question call took on the tree bench's sample. A's latency is from that sample's 3-question calls.

## Recommendation

- **Use D (wording D2) as Jev's routing call.** It is two questions in one call, with no rules in code beyond the two-key check.
- **Where two approaches each win on something:**
  - **E wins on cost, D on cost for the asks kept.**
    - E saves $0.34 a session over A, and loses 11 more asks.
    - D saves $0.20 of that, and loses 2.
    - Nothing in E's rules would add to D, because the asks E loses are ones D keeps.
  - **A and B never drop an ask; D drops the most noise.**
    - The way to combine them is a net. Ask B's kind question in D's call. A line D drops or keeps as context, that B calls a request to the AI, goes to Respond.
    - On the first half it caught nothing.
    - On the held-out half it moved 3 asks to Respond, 2 that D dropped and 1 it kept as context. Respond B answered all three in chat, so the brain still never sees them.
    - It costs about 5 cents a session. It does not pay as measured, but it is the cheap form if no ask should ever be silently dropped.
  - **D, plus A's "who" as a third key, is the one to watch.**
    - On the held-out half it kept the same 80 asks for 5 cents less.
    - On the first half, where a choice may be made, it lost 2 asks for 4 cents.
    - Within noise. Not adopted.

## Why each approach did what it did

- **A: who, then what.**
  - Level 1 is generous: a line that mentions or follows up the AI is "said to the AI".
  - Level 3 then has no outlet for a complaint or a question with something in it to change. It calls them `a_change` or `a_fix`, so A sends the most talk to the brain of any tree: 52%, 333 brain calls.
  - Asks said to a player go to level 2, which keeps or drops lines but never sends one to a model. Seven asks end in context there, none dropped.
- **B: kind first.**
  - Its request kind is A's "said to the AI and wants a change", in one class. Brain calls are close to A's (325).
  - Its "comment on the AI's work" kind is a sink: 178 lines, 107 of them dropped by the old filter, and 8 asks, which are complaints said to a player or to no one.
  - B1 put every complaint there and lost 8 asks on the first half. B2 moved complaints said *to the AI* into the request kind.
- **C: cheapest first.**
  - Rung 2 asks only for an explicit request, and gives "only implied" a class of its own, so implied asks fall to rung 3. When said to the AI, they go to Respond: 17 asks went there, and Respond B handed 11 up.
  - The noise rung drops 167 lines cleanly; one was an ask, too garbled to read ("Super Sane. Cyan, super cyan?").
  - It sends the fewest asks straight to the brain (165), and the most lines to Respond.
- **D: consequence first.**
  - The first question names what happens, not who is spoken to. "Answer in chat" takes questions and complaints to the AI, which A sends to the brain. "Ignore it" takes 430 lines, 362 of them dropped by the old filter.
  - D1's "remember it" said "a rule", and took standing instructions to the AI ("from now on, don't ask for permission") as things to remember. D2 restricted it to rules the players make among themselves.
  - The second key barely fires. When the first question says act, the second almost always agrees: it overruled 7 lines, one of them an ask ("No.", answering the AI), which then went to Respond.
- **E: observable signals.**
  - Jev answers the features well: an instruction to "you" with no name, or to the AI by name, catches 157 asks.
  - But a complaint is a statement, and the code can only file it under its topic. "Something the AI made" is stored: 16 asks end in context.
  - r1 also asked for the topic of a "you" instruction, and lost "can you teleport me" when Jev tagged its topic `a_player`; r2 dropped that test.

### Five lines where they disagree most

| Line | Label | Jev today | A | B | C | D | E |
|---|---|---|---|---|---|---|---|
| `05:35:12` KrownJM: I wonder if it might be better to just remove the implement agents for now because we don't have the speed for it Like, I don't have the time to do the cash-ix… | chat | brain | brain | brain | respond | dropped | context |
| `06:35:42` KrownJM: Maked Night again is turning into day. | task | brain | brain | context | respond | brain | dropped |
| `07:01:12` Styrkaeopteryx: Oh, that's still I'm still being blown up randomly. | chat | brain | brain | context | respond | brain | dropped |
| `06:16:25` Eclipszr: Say yes to turn on spooky mode. Both of you guys turn on spooky mode | task | brain | context | context | dropped | brain | respond |
| `07:29:07` Eclipszr: Yes. | nothing | respond | brain | dropped | dropped | brain | context |

The paths behind them:

- `05:35:12`: Jev today `action_oriented`; A `the_ai > a_change`; B `request_to_the_ai`; C `c3_to_ai=talk_with_the_ai`; D `ignore_it`; E `no_one, something_the_ai_made_or_did, still_true_in_five_minutes`
- `06:35:42`: Jev today `action_oriented`; A `the_ai > a_fix`; B `comment_on_the_ais_work`; C `c3_to_ai=talk_with_the_ai`; D `the_ai_must_act + let_down_wanted_something_done`; E `no_one, the_world_or_the_game, only_this_moment`
- `07:01:12`: Jev today `action_oriented`; A `the_ai > a_fix`; B `comment_on_the_ais_work`; C `c3_to_ai=talk_with_the_ai`; D `the_ai_must_act + let_down_wanted_something_done`; E `no_one, the_world_or_the_game, only_this_moment`
- `06:16:25`: Jev today `action_oriented`; A `a_player > plan_or_rule`; B `talk_to_a_player > a_plan_or_rule`; C `otherwise`; D `the_ai_must_act + let_down_wanted_something_done`; E `names_a_player, instruction`
- `07:29:07`: Jev today `conversational`; A `the_ai > a_change`; B `noise_or_reaction`; C `c1_noise=only_filler_or_a_reaction`; D `the_ai_must_act + let_down_wanted_something_done`; E `you_with_no_name, something_the_ai_made_or_did, only_this_moment`

A–E give the same outcome on 422 of 843 lines; two outcomes on 295, three on 118, four on 8.

How to read them:

- **05:35:12**, the developer musing about removing agents (labelled `chat`). It is not for the AI. A and B take "I wonder if it might be better to just remove…" as a request. D ignores it, rightly; E stores it, and C asks Respond.
- **06:35:42**, "Maked Night again is turning into day" (an ask). Spooky mode was supposed to keep it night, so this is an implied fix.
  - A and D send it to the brain.
  - C sends it to Respond, which handed it up.
  - B calls it a comment and stores it; E reads "no one, the world, this moment" and drops it.
- **07:01:12**, "I'm still being blown up randomly" (`chat`). The same complaint shape, about an effect the AI set up, with the same split.
- **06:16:25**, "Say yes to turn on spooky mode. Both of you guys turn on spooky mode" (an ask). It is said to the players, right after the AI asked them to say yes.
  - Only D sends it to the brain: its first question reads what the line should cause, not who it is said to.
  - A and B keep it as a plan; C drops it.
  - E sends it to Respond, which answered in chat.
- **07:29:07**, "Yes." (`nothing`). Said after Krown's line, with the AI's last message a status update, nothing to say yes to.
  - A and D take it as an answer to the AI and send it to the brain.
  - B and C drop it as filler; E stores it.

## Heat maps

Rows are the labels, what the old conversation did with the line:

- **task:** it became a task (an ask);
- **chat:** answered in chat, no task;
- **nothing;**
- **dropped:** Jev dropped it then, so the conversation never saw it.

Columns are where the line goes. Each cell shows the count and the row percentage; the darker the cell, the larger its share of the row.

`chat` is a loose label: an AI chat line within 60 s with no task. It holds many requests the old conversation declined or merged, so `chat → brain` is often right.

### The held-out second half: 422 lines, 87 asks

#### Jev today

<table>
<caption>Jev today: label (rows) by outcome (columns), second</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.87);color:#fff;text-align:right">81<br><small>93%</small></td><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">6<br><small>7%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">87</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.47);color:#fff;text-align:right">44<br><small>46%</small></td><td style="background:rgba(37, 99, 235, 0.51);color:#fff;text-align:right">48<br><small>50%</small></td><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">4<br><small>4%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">96</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.55);color:#fff;text-align:right">20<br><small>56%</small></td><td style="background:rgba(37, 99, 235, 0.41);color:inherit;text-align:right">14<br><small>39%</small></td><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">2<br><small>6%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">36</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">13<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.61);color:#fff;text-align:right">126<br><small>62%</small></td><td style="background:rgba(37, 99, 235, 0.08);color:inherit;text-align:right">1<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.34);color:inherit;text-align:right">63<br><small>31%</small></td><td style="text-align:right">203</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| **task** | 81 (93%) | 6 (7%) | 0 (0%) | 0 (0%) | 87 |
| **chat** | 44 (46%) | 48 (50%) | 4 (4%) | 0 (0%) | 96 |
| **nothing** | 20 (56%) | 14 (39%) | 2 (6%) | 0 (0%) | 36 |
| **dropped** | 13 (6%) | 126 (62%) | 1 (0%) | 63 (31%) | 203 |

- **Asks:** 81 straight to the brain; 6 to Respond, of which 3 handed up by B and 3 answered in chat; 0 in context; 0 dropped. **84 of 87 reach the brain (96.6%).**
- **Cost:** 158 brain calls straight, 194 Respond calls of which 11 handed up by B and about 2.3 more of the 88 outside B's set: 171 brain calls $1.46, Respond $0.29, Jev 422 calls 3,320,794 tokens $0.139. **$1.89** this half.

#### A. Who, then what (v2)

<table>
<caption>A. Who, then what (v2): label (rows) by outcome (columns), second</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.87);color:#fff;text-align:right">81<br><small>93%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">2<br><small>2%</small></td><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">4<br><small>5%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">87</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.44);color:inherit;text-align:right">41<br><small>43%</small></td><td style="background:rgba(37, 99, 235, 0.25);color:inherit;text-align:right">19<br><small>20%</small></td><td style="background:rgba(37, 99, 235, 0.24);color:inherit;text-align:right">18<br><small>19%</small></td><td style="background:rgba(37, 99, 235, 0.24);color:inherit;text-align:right">18<br><small>19%</small></td><td style="text-align:right">96</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.58);color:#fff;text-align:right">21<br><small>58%</small></td><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">4<br><small>11%</small></td><td style="background:rgba(37, 99, 235, 0.27);color:inherit;text-align:right">8<br><small>22%</small></td><td style="background:rgba(37, 99, 235, 0.15);color:inherit;text-align:right">3<br><small>8%</small></td><td style="text-align:right">36</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">13<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">12<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.16);color:inherit;text-align:right">20<br><small>10%</small></td><td style="background:rgba(37, 99, 235, 0.74);color:#fff;text-align:right">158<br><small>78%</small></td><td style="text-align:right">203</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| **task** | 81 (93%) | 2 (2%) | 4 (5%) | 0 (0%) | 87 |
| **chat** | 41 (43%) | 19 (20%) | 18 (19%) | 18 (19%) | 96 |
| **nothing** | 21 (58%) | 4 (11%) | 8 (22%) | 3 (8%) | 36 |
| **dropped** | 13 (6%) | 12 (6%) | 20 (10%) | 158 (78%) | 203 |

- **Asks:** 81 straight to the brain; 2 to Respond, of which 1 handed up by B and 1 answered in chat; 4 in context; 0 dropped. **82 of 87 reach the brain (94.3%).**
- **Cost:** 156 brain calls straight, 37 Respond calls of which 5 handed up by B and about 0.3 more of the 10 outside B's set: 161 brain calls $1.37, Respond $0.06, Jev 422 calls 3,324,378 tokens $0.140. **$1.57** this half.
- A's Jev is the 9-question call's tokens x 0.86, the share a 3-question call took on the tree bench's sample.

#### B. Kind first (B2)

<table>
<caption>B. Kind first (B2): label (rows) by outcome (columns), second</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.86);color:#fff;text-align:right">80<br><small>92%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">3<br><small>3%</small></td><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">4<br><small>5%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">87</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.41);color:inherit;text-align:right">37<br><small>39%</small></td><td style="background:rgba(37, 99, 235, 0.27);color:inherit;text-align:right">21<br><small>22%</small></td><td style="background:rgba(37, 99, 235, 0.33);color:inherit;text-align:right">28<br><small>29%</small></td><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">10<br><small>10%</small></td><td style="text-align:right">96</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.51);color:#fff;text-align:right">18<br><small>50%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">5<br><small>14%</small></td><td style="background:rgba(37, 99, 235, 0.36);color:inherit;text-align:right">12<br><small>33%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">1<br><small>3%</small></td><td style="text-align:right">36</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">13<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">21<br><small>10%</small></td><td style="background:rgba(37, 99, 235, 0.35);color:inherit;text-align:right">64<br><small>32%</small></td><td style="background:rgba(37, 99, 235, 0.52);color:#fff;text-align:right">105<br><small>52%</small></td><td style="text-align:right">203</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| **task** | 80 (92%) | 3 (3%) | 4 (5%) | 0 (0%) | 87 |
| **chat** | 37 (39%) | 21 (22%) | 28 (29%) | 10 (10%) | 96 |
| **nothing** | 18 (50%) | 5 (14%) | 12 (33%) | 1 (3%) | 36 |
| **dropped** | 13 (6%) | 21 (10%) | 64 (32%) | 105 (52%) | 203 |

- **Asks:** 80 straight to the brain; 3 to Respond, of which 3 handed up by B and 0 answered in chat; 4 in context; 0 dropped. **83 of 87 reach the brain (95.4%).**
- **Cost:** 148 brain calls straight, 50 Respond calls of which 9 handed up by B and about 0.4 more of the 16 outside B's set: 157 brain calls $1.34, Respond $0.07, Jev 422 calls 3,230,128 tokens $0.136. **$1.55** this half.

#### C. Cheapest first, a ladder (C1)

<table>
<caption>C. Cheapest first, a ladder (C1): label (rows) by outcome (columns), second</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.80);color:#fff;text-align:right">74<br><small>85%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">12<br><small>14%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">1<br><small>1%</small></td><td style="text-align:right">87</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.37);color:inherit;text-align:right">33<br><small>34%</small></td><td style="background:rgba(37, 99, 235, 0.40);color:inherit;text-align:right">36<br><small>38%</small></td><td style="background:rgba(37, 99, 235, 0.18);color:inherit;text-align:right">11<br><small>11%</small></td><td style="background:rgba(37, 99, 235, 0.22);color:inherit;text-align:right">16<br><small>17%</small></td><td style="text-align:right">96</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.51);color:#fff;text-align:right">18<br><small>50%</small></td><td style="background:rgba(37, 99, 235, 0.29);color:inherit;text-align:right">9<br><small>25%</small></td><td style="background:rgba(37, 99, 235, 0.22);color:inherit;text-align:right">6<br><small>17%</small></td><td style="background:rgba(37, 99, 235, 0.15);color:inherit;text-align:right">3<br><small>8%</small></td><td style="text-align:right">36</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">6<br><small>3%</small></td><td style="background:rgba(37, 99, 235, 0.18);color:inherit;text-align:right">23<br><small>11%</small></td><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">10<br><small>5%</small></td><td style="background:rgba(37, 99, 235, 0.77);color:#fff;text-align:right">164<br><small>81%</small></td><td style="text-align:right">203</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| **task** | 74 (85%) | 12 (14%) | 0 (0%) | 1 (1%) | 87 |
| **chat** | 33 (34%) | 36 (38%) | 11 (11%) | 16 (17%) | 96 |
| **nothing** | 18 (50%) | 9 (25%) | 6 (17%) | 3 (8%) | 36 |
| **dropped** | 6 (3%) | 23 (11%) | 10 (5%) | 164 (81%) | 203 |

- **Asks:** 74 straight to the brain; 12 to Respond, of which 7 handed up by B and 5 answered in chat; 0 in context; 1 dropped. **81 of 87 reach the brain (93.1%).**
- **Cost:** 131 brain calls straight, 80 Respond calls of which 16 handed up by B and about 0.5 more of the 19 outside B's set: 148 brain calls $1.25, Respond $0.12, Jev 422 calls 3,356,738 tokens $0.141. **$1.51** this half.

#### D. Consequence first, two keys (D2)

<table>
<caption>D. Consequence first, two keys (D2): label (rows) by outcome (columns), second</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.81);color:#fff;text-align:right">75<br><small>86%</small></td><td style="background:rgba(37, 99, 235, 0.15);color:inherit;text-align:right">7<br><small>8%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">2<br><small>2%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">3<br><small>3%</small></td><td style="text-align:right">87</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.43);color:inherit;text-align:right">39<br><small>41%</small></td><td style="background:rgba(37, 99, 235, 0.35);color:inherit;text-align:right">31<br><small>32%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">3<br><small>3%</small></td><td style="background:rgba(37, 99, 235, 0.28);color:inherit;text-align:right">23<br><small>24%</small></td><td style="text-align:right">96</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.46);color:inherit;text-align:right">16<br><small>44%</small></td><td style="background:rgba(37, 99, 235, 0.27);color:inherit;text-align:right">8<br><small>22%</small></td><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">4<br><small>11%</small></td><td style="background:rgba(37, 99, 235, 0.27);color:inherit;text-align:right">8<br><small>22%</small></td><td style="text-align:right">36</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">3<br><small>1%</small></td><td style="background:rgba(37, 99, 235, 0.15);color:inherit;text-align:right">16<br><small>8%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.85);color:#fff;text-align:right">184<br><small>91%</small></td><td style="text-align:right">203</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| **task** | 75 (86%) | 7 (8%) | 2 (2%) | 3 (3%) | 87 |
| **chat** | 39 (41%) | 31 (32%) | 3 (3%) | 23 (24%) | 96 |
| **nothing** | 16 (44%) | 8 (22%) | 4 (11%) | 8 (22%) | 36 |
| **dropped** | 3 (1%) | 16 (8%) | 0 (0%) | 184 (91%) | 203 |

- **Asks:** 75 straight to the brain; 7 to Respond, of which 5 handed up by B and 2 answered in chat; 2 in context; 3 dropped. **80 of 87 reach the brain (92.0%).**
- **Cost:** 133 brain calls straight, 62 Respond calls of which 12 handed up by B and about 0.3 more of the 11 outside B's set: 145 brain calls $1.23, Respond $0.09, Jev 422 calls 3,161,764 tokens $0.133. **$1.46** this half.

#### E. Observable signals, the tree in code (E1/r2)

<table>
<caption>E. Observable signals, the tree in code (E1/r2): label (rows) by outcome (columns), second</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.76);color:#fff;text-align:right">70<br><small>80%</small></td><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">6<br><small>7%</small></td><td style="background:rgba(37, 99, 235, 0.18);color:inherit;text-align:right">10<br><small>11%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">1<br><small>1%</small></td><td style="text-align:right">87</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.34);color:inherit;text-align:right">29<br><small>30%</small></td><td style="background:rgba(37, 99, 235, 0.25);color:inherit;text-align:right">19<br><small>20%</small></td><td style="background:rgba(37, 99, 235, 0.30);color:inherit;text-align:right">25<br><small>26%</small></td><td style="background:rgba(37, 99, 235, 0.28);color:inherit;text-align:right">23<br><small>24%</small></td><td style="text-align:right">96</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.36);color:inherit;text-align:right">12<br><small>33%</small></td><td style="background:rgba(37, 99, 235, 0.29);color:inherit;text-align:right">9<br><small>25%</small></td><td style="background:rgba(37, 99, 235, 0.41);color:inherit;text-align:right">14<br><small>39%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">1<br><small>3%</small></td><td style="text-align:right">36</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">11<br><small>5%</small></td><td style="background:rgba(37, 99, 235, 0.16);color:inherit;text-align:right">19<br><small>9%</small></td><td style="background:rgba(37, 99, 235, 0.28);color:inherit;text-align:right">47<br><small>23%</small></td><td style="background:rgba(37, 99, 235, 0.61);color:#fff;text-align:right">126<br><small>62%</small></td><td style="text-align:right">203</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| **task** | 70 (80%) | 6 (7%) | 10 (11%) | 1 (1%) | 87 |
| **chat** | 29 (30%) | 19 (20%) | 25 (26%) | 23 (24%) | 96 |
| **nothing** | 12 (33%) | 9 (25%) | 14 (39%) | 1 (3%) | 36 |
| **dropped** | 11 (5%) | 19 (9%) | 47 (23%) | 126 (62%) | 203 |

- **Asks:** 70 straight to the brain; 6 to Respond, of which 6 handed up by B and 0 answered in chat; 10 in context; 1 dropped. **76 of 87 reach the brain (87.4%).**
- **Cost:** 122 brain calls straight, 53 Respond calls of which 14 handed up by B and about 0.5 more of the 18 outside B's set: 136 brain calls $1.16, Respond $0.08, Jev 422 calls 3,285,842 tokens $0.138. **$1.38** this half.

### All 843 lines: 187 asks

#### Jev today

<table>
<caption>Jev today: label (rows) by outcome (columns), all</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.89);color:#fff;text-align:right">179<br><small>96%</small></td><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">8<br><small>4%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">187</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.51);color:#fff;text-align:right">93<br><small>51%</small></td><td style="background:rgba(37, 99, 235, 0.48);color:#fff;text-align:right">86<br><small>47%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">4<br><small>2%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">183</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.51);color:#fff;text-align:right">36<br><small>50%</small></td><td style="background:rgba(37, 99, 235, 0.41);color:inherit;text-align:right">28<br><small>39%</small></td><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">4<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">4<br><small>6%</small></td><td style="text-align:right">72</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">16<br><small>4%</small></td><td style="background:rgba(37, 99, 235, 0.63);color:#fff;text-align:right">260<br><small>65%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">6<br><small>1%</small></td><td style="background:rgba(37, 99, 235, 0.33);color:inherit;text-align:right">119<br><small>30%</small></td><td style="text-align:right">401</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| **task** | 179 (96%) | 8 (4%) | 0 (0%) | 0 (0%) | 187 |
| **chat** | 93 (51%) | 86 (47%) | 4 (2%) | 0 (0%) | 183 |
| **nothing** | 36 (50%) | 28 (39%) | 4 (6%) | 4 (6%) | 72 |
| **dropped** | 16 (4%) | 260 (65%) | 6 (1%) | 119 (30%) | 401 |

- **Asks:** 179 straight to the brain; 8 to Respond, of which 4 handed up by B and 4 answered in chat; 0 in context; 0 dropped. **183 of 187 reach the brain (97.9%).**
- **Cost:** 324 brain calls straight, 382 Respond calls of which 19 handed up by B and about 4.2 more of the 159 outside B's set: 347 brain calls $2.95, Respond $0.57, Jev 843 calls 6,478,294 tokens $0.272. **$3.80** a session.

#### A. Who, then what (v2)

<table>
<caption>A. Who, then what (v2): label (rows) by outcome (columns), all</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.88);color:#fff;text-align:right">177<br><small>95%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">3<br><small>2%</small></td><td style="background:rgba(37, 99, 235, 0.11);color:inherit;text-align:right">7<br><small>4%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">187</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.52);color:#fff;text-align:right">95<br><small>52%</small></td><td style="background:rgba(37, 99, 235, 0.24);color:inherit;text-align:right">34<br><small>19%</small></td><td style="background:rgba(37, 99, 235, 0.22);color:inherit;text-align:right">30<br><small>16%</small></td><td style="background:rgba(37, 99, 235, 0.19);color:inherit;text-align:right">24<br><small>13%</small></td><td style="text-align:right">183</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.52);color:#fff;text-align:right">37<br><small>51%</small></td><td style="background:rgba(37, 99, 235, 0.15);color:inherit;text-align:right">6<br><small>8%</small></td><td style="background:rgba(37, 99, 235, 0.27);color:inherit;text-align:right">16<br><small>22%</small></td><td style="background:rgba(37, 99, 235, 0.23);color:inherit;text-align:right">13<br><small>18%</small></td><td style="text-align:right">72</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">18<br><small>4%</small></td><td style="background:rgba(37, 99, 235, 0.14);color:inherit;text-align:right">29<br><small>7%</small></td><td style="background:rgba(37, 99, 235, 0.18);color:inherit;text-align:right">48<br><small>12%</small></td><td style="background:rgba(37, 99, 235, 0.73);color:#fff;text-align:right">306<br><small>76%</small></td><td style="text-align:right">401</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| **task** | 177 (95%) | 3 (2%) | 7 (4%) | 0 (0%) | 187 |
| **chat** | 95 (52%) | 34 (19%) | 30 (16%) | 24 (13%) | 183 |
| **nothing** | 37 (51%) | 6 (8%) | 16 (22%) | 13 (18%) | 72 |
| **dropped** | 18 (4%) | 29 (7%) | 48 (12%) | 306 (76%) | 401 |

- **Asks:** 177 straight to the brain; 3 to Respond, of which 1 handed up by B and 2 answered in chat; 7 in context; 0 dropped. **178 of 187 reach the brain (95.2%).**
- **Cost:** 327 brain calls straight, 72 Respond calls of which 5 handed up by B and about 0.5 more of the 20 outside B's set: 333 brain calls $2.83, Respond $0.11, Jev 843 calls 6,561,345 tokens $0.276. **$3.21** a session.
- A's Jev is the 9-question call's tokens x 0.86, the share a 3-question call took on the tree bench's sample.

#### B. Kind first (B2)

<table>
<caption>B. Kind first (B2): label (rows) by outcome (columns), all</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.88);color:#fff;text-align:right">175<br><small>94%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">3<br><small>2%</small></td><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">9<br><small>5%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="text-align:right">187</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.47);color:#fff;text-align:right">84<br><small>46%</small></td><td style="background:rgba(37, 99, 235, 0.25);color:inherit;text-align:right">37<br><small>20%</small></td><td style="background:rgba(37, 99, 235, 0.30);color:inherit;text-align:right">47<br><small>26%</small></td><td style="background:rgba(37, 99, 235, 0.15);color:inherit;text-align:right">15<br><small>8%</small></td><td style="text-align:right">183</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.47);color:#fff;text-align:right">33<br><small>46%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">10<br><small>14%</small></td><td style="background:rgba(37, 99, 235, 0.35);color:inherit;text-align:right">23<br><small>32%</small></td><td style="background:rgba(37, 99, 235, 0.15);color:inherit;text-align:right">6<br><small>8%</small></td><td style="text-align:right">72</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">18<br><small>4%</small></td><td style="background:rgba(37, 99, 235, 0.18);color:inherit;text-align:right">45<br><small>11%</small></td><td style="background:rgba(37, 99, 235, 0.35);color:inherit;text-align:right">126<br><small>31%</small></td><td style="background:rgba(37, 99, 235, 0.53);color:#fff;text-align:right">212<br><small>53%</small></td><td style="text-align:right">401</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| **task** | 175 (94%) | 3 (2%) | 9 (5%) | 0 (0%) | 187 |
| **chat** | 84 (46%) | 37 (20%) | 47 (26%) | 15 (8%) | 183 |
| **nothing** | 33 (46%) | 10 (14%) | 23 (32%) | 6 (8%) | 72 |
| **dropped** | 18 (4%) | 45 (11%) | 126 (31%) | 212 (53%) | 401 |

- **Asks:** 175 straight to the brain; 3 to Respond, of which 3 handed up by B and 0 answered in chat; 9 in context; 0 dropped. **178 of 187 reach the brain (95.2%).**
- **Cost:** 310 brain calls straight, 95 Respond calls of which 14 handed up by B and about 0.8 more of the 29 outside B's set: 325 brain calls $2.76, Respond $0.14, Jev 843 calls 6,358,733 tokens $0.267. **$3.17** a session.

#### C. Cheapest first, a ladder (C1)

<table>
<caption>C. Cheapest first, a ladder (C1): label (rows) by outcome (columns), all</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.83);color:#fff;text-align:right">165<br><small>88%</small></td><td style="background:rgba(37, 99, 235, 0.16);color:inherit;text-align:right">17<br><small>9%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">2<br><small>1%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">3<br><small>2%</small></td><td style="text-align:right">187</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.41);color:inherit;text-align:right">71<br><small>39%</small></td><td style="background:rgba(37, 99, 235, 0.40);color:inherit;text-align:right">68<br><small>37%</small></td><td style="background:rgba(37, 99, 235, 0.15);color:inherit;text-align:right">16<br><small>9%</small></td><td style="background:rgba(37, 99, 235, 0.21);color:inherit;text-align:right">28<br><small>15%</small></td><td style="text-align:right">183</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.46);color:inherit;text-align:right">32<br><small>44%</small></td><td style="background:rgba(37, 99, 235, 0.28);color:inherit;text-align:right">17<br><small>24%</small></td><td style="background:rgba(37, 99, 235, 0.23);color:inherit;text-align:right">13<br><small>18%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">10<br><small>14%</small></td><td style="text-align:right">72</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">10<br><small>2%</small></td><td style="background:rgba(37, 99, 235, 0.20);color:inherit;text-align:right">56<br><small>14%</small></td><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">25<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.74);color:#fff;text-align:right">310<br><small>77%</small></td><td style="text-align:right">401</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| **task** | 165 (88%) | 17 (9%) | 2 (1%) | 3 (2%) | 187 |
| **chat** | 71 (39%) | 68 (37%) | 16 (9%) | 28 (15%) | 183 |
| **nothing** | 32 (44%) | 17 (24%) | 13 (18%) | 10 (14%) | 72 |
| **dropped** | 10 (2%) | 56 (14%) | 25 (6%) | 310 (77%) | 401 |

- **Asks:** 165 straight to the brain; 17 to Respond, of which 11 handed up by B and 6 answered in chat; 2 in context; 3 dropped. **176 of 187 reach the brain (94.1%).**
- **Cost:** 278 brain calls straight, 158 Respond calls of which 26 handed up by B and about 1.0 more of the 36 outside B's set: 305 brain calls $2.59, Respond $0.24, Jev 843 calls 6,612,193 tokens $0.278. **$3.11** a session.

#### D. Consequence first, two keys (D2)

<table>
<caption>D. Consequence first, two keys (D2): label (rows) by outcome (columns), all</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.85);color:#fff;text-align:right">170<br><small>91%</small></td><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">10<br><small>5%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">3<br><small>2%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">4<br><small>2%</small></td><td style="text-align:right">187</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.42);color:inherit;text-align:right">74<br><small>40%</small></td><td style="background:rgba(37, 99, 235, 0.36);color:inherit;text-align:right">61<br><small>33%</small></td><td style="background:rgba(37, 99, 235, 0.10);color:inherit;text-align:right">5<br><small>3%</small></td><td style="background:rgba(37, 99, 235, 0.28);color:inherit;text-align:right">43<br><small>23%</small></td><td style="text-align:right">183</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.42);color:inherit;text-align:right">29<br><small>40%</small></td><td style="background:rgba(37, 99, 235, 0.26);color:inherit;text-align:right">15<br><small>21%</small></td><td style="background:rgba(37, 99, 235, 0.16);color:inherit;text-align:right">7<br><small>10%</small></td><td style="background:rgba(37, 99, 235, 0.33);color:inherit;text-align:right">21<br><small>29%</small></td><td style="text-align:right">72</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">4<br><small>1%</small></td><td style="background:rgba(37, 99, 235, 0.15);color:inherit;text-align:right">35<br><small>9%</small></td><td style="background:transparent;color:inherit;text-align:right">0<br><small>0%</small></td><td style="background:rgba(37, 99, 235, 0.85);color:#fff;text-align:right">362<br><small>90%</small></td><td style="text-align:right">401</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| **task** | 170 (91%) | 10 (5%) | 3 (2%) | 4 (2%) | 187 |
| **chat** | 74 (40%) | 61 (33%) | 5 (3%) | 43 (23%) | 183 |
| **nothing** | 29 (40%) | 15 (21%) | 7 (10%) | 21 (29%) | 72 |
| **dropped** | 4 (1%) | 35 (9%) | 0 (0%) | 362 (90%) | 401 |

- **Asks:** 170 straight to the brain; 10 to Respond, of which 6 handed up by B and 4 answered in chat; 3 in context; 4 dropped. **176 of 187 reach the brain (94.1%).**
- **Cost:** 277 brain calls straight, 121 Respond calls of which 24 handed up by B and about 0.6 more of the 23 outside B's set: 302 brain calls $2.56, Respond $0.18, Jev 843 calls 6,222,167 tokens $0.261. **$3.01** a session.

#### E. Observable signals, the tree in code (E1/r2)

<table>
<caption>E. Observable signals, the tree in code (E1/r2): label (rows) by outcome (columns), all</caption>
<tr><th></th><th>brain</th><th>Respond</th><th>context</th><th>dropped</th><th>all</th></tr>
<tr><th>task</th><td style="background:rgba(37, 99, 235, 0.79);color:#fff;text-align:right">157<br><small>84%</small></td><td style="background:rgba(37, 99, 235, 0.13);color:inherit;text-align:right">12<br><small>6%</small></td><td style="background:rgba(37, 99, 235, 0.15);color:inherit;text-align:right">16<br><small>9%</small></td><td style="background:rgba(37, 99, 235, 0.09);color:inherit;text-align:right">2<br><small>1%</small></td><td style="text-align:right">187</td></tr>
<tr><th>chat</th><td style="background:rgba(37, 99, 235, 0.36);color:inherit;text-align:right">61<br><small>33%</small></td><td style="background:rgba(37, 99, 235, 0.26);color:inherit;text-align:right">39<br><small>21%</small></td><td style="background:rgba(37, 99, 235, 0.31);color:inherit;text-align:right">50<br><small>27%</small></td><td style="background:rgba(37, 99, 235, 0.23);color:inherit;text-align:right">33<br><small>18%</small></td><td style="text-align:right">183</td></tr>
<tr><th>nothing</th><td style="background:rgba(37, 99, 235, 0.36);color:inherit;text-align:right">24<br><small>33%</small></td><td style="background:rgba(37, 99, 235, 0.23);color:inherit;text-align:right">13<br><small>18%</small></td><td style="background:rgba(37, 99, 235, 0.40);color:inherit;text-align:right">27<br><small>38%</small></td><td style="background:rgba(37, 99, 235, 0.17);color:inherit;text-align:right">8<br><small>11%</small></td><td style="text-align:right">72</td></tr>
<tr><th>dropped</th><td style="background:rgba(37, 99, 235, 0.12);color:inherit;text-align:right">19<br><small>5%</small></td><td style="background:rgba(37, 99, 235, 0.16);color:inherit;text-align:right">38<br><small>9%</small></td><td style="background:rgba(37, 99, 235, 0.31);color:inherit;text-align:right">109<br><small>27%</small></td><td style="background:rgba(37, 99, 235, 0.58);color:#fff;text-align:right">235<br><small>59%</small></td><td style="text-align:right">401</td></tr>
</table>

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| **task** | 157 (84%) | 12 (6%) | 16 (9%) | 2 (1%) | 187 |
| **chat** | 61 (33%) | 39 (21%) | 50 (27%) | 33 (18%) | 183 |
| **nothing** | 24 (33%) | 13 (18%) | 27 (38%) | 8 (11%) | 72 |
| **dropped** | 19 (5%) | 38 (9%) | 109 (27%) | 235 (59%) | 401 |

- **Asks:** 157 straight to the brain; 12 to Respond, of which 10 handed up by B and 2 answered in chat; 16 in context; 2 dropped. **167 of 187 reach the brain (89.3%).**
- **Cost:** 261 brain calls straight, 102 Respond calls of which 26 handed up by B and about 0.8 more of the 30 outside B's set: 288 brain calls $2.45, Respond $0.15, Jev 843 calls 6,470,569 tokens $0.272. **$2.87** a session.

## Every ask that does not reach the brain (all 843 lines)

An ask sent to Respond reaches the brain when Respond B handed that line up. B is the gate bench's option B, now Respond's own. Its recorded decision is known for every ask. A Respond line outside B's set (only `dropped` lines) is counted at B's hand-up rate on the dropped lines it ran (4 of 150).

**Jev today**: 4

- `06:13:44` Styrkaeopteryx [first] → **Respond (B answered in chat)** `conversational`: Oh, it's still working on the castle. away from you. I found an open planes area so it might be easier. It's not made. Like I don't see any…
- `07:19:36` KrownJM [second] → **Respond (B answered in chat)** `conversational`: When does we realize is it can we just like Oh no. Bedrock is protected.
- `07:32:54` Eclipszr [second] → **Respond (B answered in chat)** `conversational`: Like I said, I don't have a spawn.
- `07:38:23` Styrkaeopteryx [second] → **Respond (B answered in chat)** `conversational`: I'm sorry. That claw made Gallic gun destroy everything in the map.

**A. Who, then what (v2)**: 9

- `06:14:04` KrownJM [first] → **Respond (B answered in chat)** `the_ai > an_answer`: Can you teach me about both of us to Stirka? Also Matthew Matt Matthew are you recording? Can you record Bo? 'Cause I wanna post it on shit…
- `06:16:25` Eclipszr [first] → **context** `a_player > plan_or_rule`: Say yes to turn on spooky mode. Both of you guys turn on spooky mode
- `06:28:11` Styrkaeopteryx [first] → **context** `a_player > ai_work_bad`: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:38:14` KrownJM [first] → **context** `a_player > plan_or_rule`: Okay, can we like stay together and actually like clear out some like one place to live?
- `07:14:10` KrownJM [second] → **context** `themselves > ai_work_bad`: I have to make sure the ice steam also goes through blocks. Holy sit, what the fuck happened here? I think so cool. This is the hitchhik.
- `07:15:38` KrownJM [second] → **context** `a_player > ai_work_bad`: Wait, I can't I just I just froze that IC pierced out we got to stop. I feel the eyes don't what are you what are you break it, break it? D…
- `07:19:36` KrownJM [second] → **Respond (B answered in chat)** `the_ai > an_answer`: When does we realize is it can we just like Oh no. Bedrock is protected.
- `07:24:33` Styrkaeopteryx [second] → **context** `a_player > player_or_world_context`: I don't want to get put in a fucking pit again, please.
- `07:38:23` Styrkaeopteryx [second] → **context** `a_player > ai_work_bad`: I'm sorry. That claw made Gallic gun destroy everything in the map.

**B. Kind first (B2)**: 9

- `06:13:44` Styrkaeopteryx [first] → **context** `comment_on_the_ais_work`: Oh, it's still working on the castle. away from you. I found an open planes area so it might be easier. It's not made. Like I don't see any…
- `06:16:25` Eclipszr [first] → **context** `talk_to_a_player > a_plan_or_rule`: Say yes to turn on spooky mode. Both of you guys turn on spooky mode
- `06:23:27` Styrkaeopteryx [first] → **context** `comment_on_the_ais_work`: Oh, not out of spooky mode, Claude. Darkness got Ow.
- `06:28:11` Styrkaeopteryx [first] → **context** `comment_on_the_ais_work`: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:35:42` KrownJM [first] → **context** `comment_on_the_ais_work`: Maked Night again is turning into day.
- `07:19:36` KrownJM [second] → **context** `comment_on_the_ais_work`: When does we realize is it can we just like Oh no. Bedrock is protected.
- `07:32:54` Eclipszr [second] → **context** `comment_on_the_ais_work`: Like I said, I don't have a spawn.
- `07:38:23` Styrkaeopteryx [second] → **context** `comment_on_the_ais_work`: I'm sorry. That claw made Gallic gun destroy everything in the map.
- `07:44:28` Eclipszr [second] → **context** `comment_on_the_ais_work`: Claude, it doesn't nuke the world. It doesn't create a bigger crater, therefore isn't good enough.

**C. Cheapest first, a ladder (C1)**: 11

- `06:13:44` Styrkaeopteryx [first] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: Oh, it's still working on the castle. away from you. I found an open planes area so it might be easier. It's not made. Like I don't see any…
- `06:14:04` KrownJM [first] → **dropped** `otherwise`: Can you teach me about both of us to Stirka? Also Matthew Matt Matthew are you recording? Can you record Bo? 'Cause I wanna post it on shit…
- `06:16:25` Eclipszr [first] → **dropped** `otherwise`: Say yes to turn on spooky mode. Both of you guys turn on spooky mode
- `06:28:11` Styrkaeopteryx [first] → **context** `c4_lasting=a_problem_with_the_ais_work`: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:38:14` KrownJM [first] → **context** `c4_lasting=a_plan_or_rule`: Okay, can we like stay together and actually like clear out some like one place to live?
- `07:19:36` KrownJM [second] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: When does we realize is it can we just like Oh no. Bedrock is protected.
- `07:20:46` Eclipszr [second] → **dropped** `c1_noise=garbled_speech`: Super Sane. Cyan, super cyan?
- `07:24:33` Styrkaeopteryx [second] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: I don't want to get put in a fucking pit again, please.
- `07:27:05` KrownJM [second] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: Oh my god, it's so hard to like I want the path because I can't walk anywhere anymore. It's all fucked up.
- `07:32:54` Eclipszr [second] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: Like I said, I don't have a spawn.
- `07:38:23` Styrkaeopteryx [second] → **Respond (B answered in chat)** `c3_to_ai=talk_with_the_ai`: I'm sorry. That claw made Gallic gun destroy everything in the map.

**D. Consequence first, two keys (D2)**: 11

- `06:13:44` Styrkaeopteryx [first] → **context** `remember_it`: Oh, it's still working on the castle. away from you. I found an open planes area so it might be easier. It's not made. Like I don't see any…
- `06:14:04` KrownJM [first] → **Respond (B answered in chat)** `the_ai_should_answer`: Can you teach me about both of us to Stirka? Also Matthew Matt Matthew are you recording? Can you record Bo? 'Cause I wanna post it on shit…
- `06:28:11` Styrkaeopteryx [first] → **dropped** `ignore_it`: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:55:50` Eclipszr [first] → **Respond (B answered in chat)** `the_ai_must_act + fine_a_reply_is_all`: No.
- `07:14:10` KrownJM [second] → **dropped** `ignore_it`: I have to make sure the ice steam also goes through blocks. Holy sit, what the fuck happened here? I think so cool. This is the hitchhik.
- `07:19:36` KrownJM [second] → **Respond (B answered in chat)** `the_ai_should_answer`: When does we realize is it can we just like Oh no. Bedrock is protected.
- `07:24:33` Styrkaeopteryx [second] → **context** `remember_it`: I don't want to get put in a fucking pit again, please.
- `07:25:09` KrownJM [second] → **dropped** `ignore_it`: Could you also remove the glow effect on? Oh wait, never mind. Keep the effect never mind. You can keep it good. Wait, did it just turn ice…
- `07:27:56` Eclipszr [second] → **Respond (B answered in chat)** `the_ai_should_answer`: Can make music for each of the attacks? Is that possible?
- `07:32:54` Eclipszr [second] → **context** `remember_it`: Like I said, I don't have a spawn.
- `07:38:23` Styrkaeopteryx [second] → **dropped** `ignore_it`: I'm sorry. That claw made Gallic gun destroy everything in the map.

**E. Observable signals, the tree in code (E1/r2)**: 20

- `06:12:01` Styrkaeopteryx [first] → **context** `you_with_no_name, something_the_ai_made_or_did, still_true_in_five_minutes`: No. I'm going to do the castle over here.
- `06:13:44` Styrkaeopteryx [first] → **context** `no_one, something_the_ai_made_or_did, only_this_moment`: Oh, it's still working on the castle. away from you. I found an open planes area so it might be easier. It's not made. Like I don't see any…
- `06:16:25` Eclipszr [first] → **Respond (B answered in chat)** `names_a_player, instruction`: Say yes to turn on spooky mode. Both of you guys turn on spooky mode
- `06:28:11` Styrkaeopteryx [first] → **context** `names_a_player, something_the_ai_made_or_did, still_true_in_five_minutes`: Jessie. God, the lightning is so hard to aim. It's like hyper delayed
- `06:32:48` Eclipszr [first] → **context** `no_one, something_the_ai_made_or_did, only_this_moment`: My gaming gun doesn't work.
- `06:35:42` KrownJM [first] → **dropped** `no_one, the_world_or_the_game, only_this_moment`: Maked Night again is turning into day.
- `06:38:14` KrownJM [first] → **Respond (B answered in chat)** `names_a_player, instruction`: Okay, can we like stay together and actually like clear out some like one place to live?
- `06:54:06` KrownJM [first] → **context** `no_one, something_the_ai_made_or_did, still_true_in_five_minutes`: It does look like my staff should do a lot more damage. There's like no damage. My staff should do a lot of damage.
- `06:55:50` Eclipszr [first] → **context** `you_with_no_name, something_the_ai_made_or_did, only_this_moment`: No.
- `07:00:34` KrownJM [second] → **dropped** `you_with_no_name, a_player, only_this_moment`: Also, where is my ice bag? I wore my eyes dog bag.
- `07:14:10` KrownJM [second] → **context** `no_one, something_the_ai_made_or_did, only_this_moment`: I have to make sure the ice steam also goes through blocks. Holy sit, what the fuck happened here? I think so cool. This is the hitchhik.
- `07:19:36` KrownJM [second] → **context** `no_one, something_the_ai_made_or_did, still_true_in_five_minutes`: When does we realize is it can we just like Oh no. Bedrock is protected.
- `07:22:28` Eclipszr [second] → **context** `you_with_no_name, something_the_ai_made_or_did, only_this_moment`: I said spirit bon, not spirit bon.
- `07:27:05` KrownJM [second] → **context** `no_one, something_the_ai_made_or_did, still_true_in_five_minutes`: Oh my god, it's so hard to like I want the path because I can't walk anywhere anymore. It's all fucked up.
- `07:28:11` KrownJM [second] → **context** `you_with_no_name, something_the_ai_made_or_did, still_true_in_five_minutes`: What the fuck just happened? Oh this is not what I wanted. I didn't want to create Just ice everywhere I walk. I bet like I bet like whenev…
- `07:32:22` Styrkaeopteryx [second] → **context** `you_with_no_name, something_the_ai_made_or_did, still_true_in_five_minutes`: But my gallic gun doesn't work anymore.
- `07:32:45` KrownJM [second] → **context** `you_with_no_name, something_the_ai_made_or_did, still_true_in_five_minutes`: I sent one thousand block radios, not two hundred.
- `07:32:54` Eclipszr [second] → **context** `you_with_no_name, a_player, still_true_in_five_minutes`: Like I said, I don't have a spawn.
- `07:38:23` Styrkaeopteryx [second] → **context** `no_one, something_the_ai_made_or_did, still_true_in_five_minutes`: I'm sorry. That claw made Gallic gun destroy everything in the map.
- `07:46:47` KrownJM [second] → **context** `no_one, the_world_or_the_game, still_true_in_five_minutes`: What the fuck? Okay, let's add a mountain biome. Let's do the mountain biome. Holy shit fro this is so cool. This is so fucking cool. alway…

## Latency per Jev call

| | Where measured | Calls | Median | p90 |
|---|---|---|---|---|
| Jev today | classes.py's run (one question, with not_relevant lines asked again) | 843 | 226 ms | 254 ms |
| A | the tree bench's 100-line sample, A's three questions alone | 100 | 226 ms | 258 ms |
| B | the B2 run | 843 | 221 ms | 246 ms |
| C | the C1 run | 843 | 222 ms | 250 ms |
| D | the D2 run | 843 | 219 ms | 243 ms |
| E | the E1/r2 run | 843 | 221 ms | 246 ms |
| A, 9-question call | the tree bench's sample | 100 | 232 ms | 259 ms |

## Run-to-run noise

D2 asked again on the same 422 lines: the same outcome on **419 of 422**. Asks reaching the brain 80 then 81 of 87; brain calls 145 then 147; Respond calls 62 then 63; cost $1.46 then $1.48. Differences of a couple of asks or a few cents between approaches are inside this noise.

## Combinations, scored offline

Each combination walks answers already collected, from each approach's own call. The choice among them would be made on the first half. Cost leaves Jev out (one call with both question sets would add a few cents a session).

| Combination | Half | Asks reach | Lost: chat / context / dropped | Brain calls | Respond calls | Talk to brain | Old-dropped dropped | Cost without Jev |
|---|---|---|---|---|---|---|---|---|
| D alone | first | 96 of 100 | 2 / 1 / 1 | 156 | 59 | 48 | 90% | $1.42 |
| D + B's net | first | 96 of 100 | 2 / 1 / 1 | 156 | 70 | 48 | 88% | $1.43 |
| D + C's net | first | 96 of 100 | 2 / 1 / 1 | 157 | 65 | 48 | 88% | $1.44 |
| D and B, three keys | first | 95 of 100 | 3 / 1 / 1 | 153 | 75 | 46 | 88% | $1.42 |
| D and C, three keys | first | 94 of 100 | 4 / 1 / 1 | 149 | 77 | 42 | 88% | $1.39 |
| D and A's who | first | 94 of 100 | 4 / 1 / 1 | 151 | 64 | 46 | 90% | $1.38 |
| D alone | second | 80 of 87 | 2 / 2 / 3 | 145 | 62 | 55 | 91% | $1.33 |
| D + B's net | second | 80 of 87 | 5 / 1 / 1 | 147 | 78 | 55 | 86% | $1.36 |
| D + C's net | second | 80 of 87 | 4 / 2 / 1 | 146 | 71 | 55 | 89% | $1.35 |
| D and B, three keys | second | 80 of 87 | 5 / 1 / 1 | 143 | 85 | 48 | 86% | $1.34 |
| D and C, three keys | second | 79 of 87 | 5 / 2 / 1 | 139 | 86 | 46 | 89% | $1.31 |
| D and A's who | second | 80 of 87 | 2 / 2 / 3 | 138 | 70 | 49 | 91% | $1.28 |
| D alone | all | 176 of 187 | 4 / 3 / 4 | 302 | 121 | 103 | 90% | $2.75 |
| D + B's net | all | 176 of 187 | 7 / 2 / 2 | 303 | 148 | 103 | 87% | $2.80 |
| D + C's net | all | 176 of 187 | 6 / 3 / 2 | 304 | 136 | 103 | 89% | $2.79 |
| D and B, three keys | all | 175 of 187 | 8 / 2 / 2 | 296 | 160 | 94 | 87% | $2.76 |
| D and C, three keys | all | 173 of 187 | 9 / 3 / 2 | 289 | 163 | 88 | 89% | $2.70 |
| D and A's who | all | 174 of 187 | 6 / 3 / 4 | 290 | 134 | 95 | 90% | $2.66 |

- **D + B's net**: D, but a line D drops or keeps as context that B calls a request to the AI goes to Respond.
- **D + C's net**: D, but a line D drops or keeps as context that C's rung 2 calls an explicit request goes to Respond.
- **D and B, three keys**: the brain only when D's two keys and B's request agree; D's act without B to Respond; then B's net.
- **D and C, three keys**: the brain only when D's two keys and C's rung 2 agree; D's act without C to Respond; then C's net.
- **D and A's who**: the brain only when D's two keys agree and A's level 1 says the AI; else Respond.

## Every version scored

Every wording version, and each of E's rule sets, was scored on the whole first half (421 lines, 100 asks). Only versions chosen there were run on the second half. Changes were written from patterns in first-half answers; no line is named or fitted.

- **B1 → B2:**
  1. A yes, a no or a choice answering what the AI asked before doing something is part of the request (Jev's own rule). B1 read such answers as comments.
  2. A complaint said *to the AI* about its work is a request to fix it. A comment on the AI's work is one made to a player or to no one, or praise. B1 put every complaint in context.
- **C1:** as first written. Nothing in its first-half losses called for a general change.
- **D1 → D2:**
  1. Acting includes a standing instruction for how the AI should act from now on, and a yes, a no or a choice answering what the AI asked before doing something.
  2. What to remember is a plan or a rule the players make among themselves. D1's "a rule" took standing instructions to the AI.
- **E's rules, r1 → r2 (wording E1 throughout):**
  1. An instruction to "you" with no name goes to the brain whatever its topic. r1 required its topic to be the AI's work or the world.
  2. An instruction that names a player goes to Respond, which hands it up if it was meant for the AI.
- **r3:** r2 without its second change.

**Chosen on the first half:**

- B2: 3 more asks for $0.08.
- D2: 2 more asks for $0.06.
- E r2: 8 more asks than r1, 2 more than r3, for $0.04 more than r3.

421 lines, 100 asks, 123 talk (chat and nothing), 198 dropped by the old filter. Cost is the first half's.

| Approach | Version | Asks to brain | + via B | = reach | Lost: chat / context / dropped | Talk to brain | Old-dropped dropped | Brain calls | Respond calls | Jev | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Jev today | today | 98 | 1 | **99** | 1 / 0 / 0 | 65 (53%) | 56 (28%) | 176 | 188 | $0.133 | **$1.91** |
| A | v2 | 96 | 0 | **96** | 1 / 3 / 0 | 70 (57%) | 148 (75%) | 171 | 35 | $0.136 | **$1.64** |
| B | B1 | 92 | 0 | **92** | 0 / 8 / 0 | 53 (43%) | 110 (56%) | 156 | 54 | $0.131 | **$1.54** |
| B | B2 | 95 | 0 | **95** | 0 / 5 / 0 | 62 (50%) | 107 (54%) | 167 | 45 | $0.131 | **$1.62** |
| C | C1 | 91 | 4 | **95** | 1 / 2 / 2 | 52 (42%) | 146 (74%) | 157 | 78 | $0.137 | **$1.59** |
| D | D1 | 93 | 1 | **94** | 2 / 4 / 0 | 40 (33%) | 174 (88%) | 148 | 70 | $0.128 | **$1.49** |
| D | D2 | 95 | 1 | **96** | 2 / 1 / 1 | 48 (39%) | 178 (90%) | 156 | 59 | $0.129 | **$1.55** |
| E | E1/r1 | 81 | 2 | **83** | 0 / 13 / 4 | 41 (33%) | 119 (60%) | 133 | 38 | $0.134 | **$1.32** |
| E | E1/r2 | 87 | 4 | **91** | 2 / 6 / 1 | 44 (36%) | 109 (55%) | 151 | 49 | $0.134 | **$1.49** |
| E | E1/r3 | 87 | 2 | **89** | 0 / 10 / 1 | 44 (36%) | 113 (57%) | 148 | 38 | $0.134 | **$1.45** |

## The questions

Each question is a `choice` about line [1], its instructions `Line [1], <player> at <time>: “<the line>”. <question>`, all of an approach's questions in one call on the filter's state.


**B. Kind first (B2)**

| Question | Option | Goes to | Description |
|---|---|---|---|
| `b_kind`: What kind of line is it? | `request_to_the_ai` | brain | An explicit request to the AI to do something in the game: it tells or asks the AI to make, give, build, spawn, teleport, change, fix, undo or stop something, however casually it is said; follows up an earlier request with what to change; tells the AI that something it made is broken, too weak or too strong; or answers yes, no or a choice to what the AI asked before doing something. |
|  | `question_or_talk_to_the_ai` | Respond | A question to the AI, or conversation with it: it asks the AI something a chat reply settles, greets it, jokes with it or tells it an opinion, with nothing to do in the game. |
|  | `comment_on_the_ais_work` | context | A comment on something the AI made or did, said to a player or to no one, or praise for it: it works, it's cool, it doesn't work, it's too strong, too weak or gone, without asking the AI for anything. |
|  | `talk_to_a_player` | level 2 | Talk to another player: said to someone else in the game, even when it is about the AI or the game. |
|  | `talk_to_themselves` | level 2 | Talk to themselves: thinking aloud, or narrating what they are doing, to no one in particular. |
|  | `noise_or_reaction` | dropped | Noise, filler or a reaction: an exclamation, a laugh, swearing, "ok", "yeah", "wow", thanks, or speech too garbled to make out. |
| `b_keep`: What does the line tell that could still be used later? | `a_plan_or_rule` | context | A plan, a decision or a rule: what the players will do, build, fight or find next, or what is allowed or banned. |
|  | `lasting_info` | context | A lasting fact about a player or the world, still true in five minutes: where a base or a build is, who is on whose team, what someone likes, hates or is trying to do. |
|  | `only_this_moment` | dropped | Only this moment: what is happening or being seen right now, a reaction, a joke, banter or running commentary that won't matter in five minutes. |

**C. Cheapest first, a ladder (C1)**

| Question | Option | Goes to | Description |
|---|---|---|---|
| `c1_noise`: How much does the line say? | `only_filler_or_a_reaction` | rung 1 fires: dropped | Only filler or a reaction: "so", "um", "yeah", "okay", a laugh, swearing, or an exclamation like "wow", "nice" or "oh my god", and nothing more. |
|  | `garbled_speech` | rung 1 fires: dropped | Garbled speech: speech-to-text words that don't make sense together, whose meaning can't be made out. |
|  | `something_with_meaning` | on to rung 2 | Something with meaning: a request, a question, an idea, a fact, a plan or a comment, even short, mixed with filler or partly garbled. |
| `c2_request`: What, if anything, does the line ask to be done? | `an_explicit_request_to_the_ai` | rung 2 fires: brain | An explicit request to the AI: it tells or asks the AI to make, give, build, spawn, teleport, change, fix, undo or stop something in the game, however casually, or answers what the AI asked before doing it. |
|  | `only_implied` | on to rung 3 | Something wanted but only implied: a wish, a complaint or a remark that something should be different, without telling anyone to do it. |
|  | `a_request_to_a_player` | on to rung 3 | A request to another player: it asks or tells a player, not the AI, to do something. |
|  | `no_request` | on to rung 3 | No request at all: a question, a comment, a story, a joke or a reaction, asking nothing to be done. |
| `c3_to_ai`: Who is the line said to? | `a_question_to_the_ai` | rung 3 fires: Respond | A question to the AI: it asks the AI about the game, Minecraft, the players or the AI itself. |
|  | `talk_with_the_ai` | rung 3 fires: Respond | Talk with the AI: a greeting, a joke, an opinion or a reply to what the AI said, said to the AI. |
|  | `said_to_a_player` | on to rung 4 | Said to another player: talk between players, even when it is about the AI or the game. |
|  | `said_to_no_one` | on to rung 4 | Said to no one: thinking aloud, narrating, or a remark to the room. |
| `c4_lasting`: What could the AI still use from this line in five minutes? | `a_plan_or_rule` | rung 4 fires: context | A plan, a decision or a rule: what the players will do, build, fight or find next, or what is allowed or banned. |
|  | `lasting_player_or_world_info` | rung 4 fires: context | A lasting fact about a player or the world: where a base or a build is, who is on whose team, what someone likes, hates or is trying to do. |
|  | `a_problem_with_the_ais_work` | rung 4 fires: context | A problem with something the AI made or did: it doesn't work, is too strong or too weak, is in the wrong place, or is gone. |
|  | `nothing_beyond_this_moment` | on to rung 5 | Nothing beyond this moment: what is happening right now, chatter, a joke or a reaction that won't matter in five minutes. |

**D. Consequence first, two keys (D2)**

| Question | Option | Goes to | Description |
|---|---|---|---|
| `d_what`: What should happen with this line? | `the_ai_must_act` | brain if Q2 agrees, else Respond | The AI must act in the game: the speaker asked it to make, give, build, move, change, fix, undo or stop something, told it how to act from now on, or answered yes, no or a choice to what it asked before doing something. |
|  | `the_ai_should_answer` | Respond | The AI should answer in chat: the speaker asked it a question or talked to it, and a chat reply is all that's needed. |
|  | `remember_it` | context | Remember it for later: no one needs an answer now, but it tells something lasting: a plan or a rule the players make among themselves, a fact about a player or the world, or a problem with what the AI made. |
|  | `ignore_it` | dropped | Ignore it: talk that matters only this moment, said between players or to no one, a reaction, filler or garbled speech. |
| `d_feel`: If the AI only replied in chat and nothing changed in the game, how would the speaker feel? | `fine_a_reply_is_all` | disagrees | Fine: a reply is all they wanted from the AI, an answer, an opinion or a bit of conversation. |
|  | `let_down_wanted_something_done` | agrees: act | Let down: they wanted the AI to get something done in the game, made, given, moved, fixed or stopped. |
|  | `not_talking_to_the_ai` | disagrees | They wouldn't notice: they weren't talking to the AI, but to a player or to no one. |

**E. Observable signals, the tree in code (E1/r2)**

| Question | Option | Goes to | Description |
|---|---|---|---|
| `e_addressed`: Who does the line address? | `names_the_ai` | a feature, to the rules | It names the AI: "Claude", "AI", "bot", or a name speech-to-text misheard, like "Cloud" or "Clod". |
|  | `you_with_no_name` | a feature, to the rules | It speaks to "you", or gives an order or asks a question, without naming anyone. |
|  | `names_a_player` | a feature, to the rules | It names another player, or speaks to the group: "you guys", "bro", a player's name. |
|  | `no_one` | a feature, to the rules | It addresses no one: a remark about things, thinking aloud, narrating, or a reaction. |
| `e_form`: What form does the line take? | `an_instruction_or_request` | a feature, to the rules | An instruction or request: somewhere in it, someone is told or asked to do something ("give me", "can you make", "teleport me", "stop"). |
|  | `a_question` | a feature, to the rules | A question: it asks something, and asks nothing to be done. |
|  | `a_statement` | a feature, to the rules | A statement: it tells or describes something, and asks nothing. |
|  | `only_an_exclamation` | a feature, to the rules | Only an exclamation: a reaction, a laugh, swearing, filler, or words too garbled to read. |
| `e_topic`: What is the line about? | `something_the_ai_made_or_did` | a feature, to the rules | Something the AI made, gave, changed or said: an item, a power, a build, an effect, or the AI itself. |
|  | `a_player` | a feature, to the rules | A player: what someone is doing, has, feels, or where they are. |
|  | `the_world_or_the_game` | a feature, to the rules | The world or the game: places, mobs, blocks, the server, or how Minecraft works. |
|  | `nothing_in_particular` | a feature, to the rules | Nothing in particular: filler, a reaction, or chatter with no clear subject. |
| `e_time`: How long will what the line says stay true or useful? | `still_true_in_five_minutes` | a feature, to the rules | Still true in five minutes: a plan, a rule, a fact about a player or the world, or a problem with something that stays. |
|  | `only_this_moment` | a feature, to the rules | Only this moment: what is happening or being seen right now, or a reaction to it. |

**E's rules (r2)**, in order; the first that applies decides:

1. Only an exclamation: dropped.
2. An instruction or request that names the AI, or speaks to "you" with no name: the brain, whatever its topic.
3. An instruction or request that names a player: Respond, which hands it up if it was meant for the AI.
4. Any other line that names the AI: Respond. A question to "you" about something the AI made, or the world: Respond.
5. The rest: still true in five minutes, or about something the AI made, is context; else dropped.

**A's questions** are the tree bench's v2, member A: `l1_a` "Who is the speaker talking to?", `l2_a` "What in this line is worth keeping for the AI?", `l3_a` "What does the speaker want from the AI?". See `tree_bench.md`. A's "nothing to answer" (`only_thanks`) counts as context here.

## How each approach reached its outcomes (all 843 lines)

### A. Who, then what (v2)

| Outcome | Path | task | chat | nothing | dropped |
|---|---|---|---|---|---|
| brain | `the_ai > a_change` | 134 | 69 | 27 | 14 |
| brain | `the_ai > a_fix` | 43 | 26 | 10 | 4 |
| respond | `the_ai > an_answer` | 3 | 24 | 4 | 9 |
| respond | `the_ai > just_chatting` | 0 | 10 | 2 | 20 |
| context | `a_player > ai_work_bad` | 3 | 13 | 9 | 13 |
| context | `a_player > player_or_world_context` | 1 | 10 | 2 | 9 |
| context | `a_player > plan_or_rule` | 2 | 1 | 0 | 9 |
| context | `the_ai > only_thanks` | 0 | 1 | 0 | 8 |
| context | `themselves > ai_work_bad` | 1 | 1 | 2 | 2 |
| context | `themselves > plan_or_rule` | 0 | 2 | 0 | 3 |
| context | `no_one > ai_work_bad` | 0 | 2 | 2 | 1 |
| context | `themselves > player_or_world_context` | 0 | 0 | 1 | 2 |
| context | `no_one > player_or_world_context` | 0 | 0 | 0 | 1 |
| dropped | `a_player > the_moment` | 0 | 14 | 5 | 108 |
| dropped | `no_one > reaction` | 0 | 0 | 1 | 64 |
| dropped | `no_one > garbled` | 0 | 1 | 2 | 54 |
| dropped | `no_one > the_moment` | 0 | 4 | 3 | 24 |
| dropped | `a_player > garbled` | 0 | 3 | 1 | 24 |
| dropped | `themselves > the_moment` | 0 | 2 | 1 | 18 |
| dropped | `a_player > reaction` | 0 | 0 | 0 | 8 |
| dropped | `themselves > garbled` | 0 | 0 | 0 | 6 |

### B. Kind first (B2)

| Outcome | Path | task | chat | nothing | dropped |
|---|---|---|---|---|---|
| brain | `request_to_the_ai` | 175 | 84 | 33 | 18 |
| respond | `question_or_talk_to_the_ai` | 3 | 37 | 10 | 45 |
| context | `comment_on_the_ais_work` | 8 | 42 | 21 | 107 |
| context | `talk_to_a_player > lasting_info` | 0 | 2 | 1 | 7 |
| context | `talk_to_a_player > a_plan_or_rule` | 1 | 1 | 1 | 5 |
| context | `talk_to_themselves > lasting_info` | 0 | 0 | 0 | 5 |
| context | `talk_to_themselves > a_plan_or_rule` | 0 | 2 | 0 | 2 |
| dropped | `noise_or_reaction` | 0 | 6 | 5 | 147 |
| dropped | `talk_to_a_player > only_this_moment` | 0 | 7 | 1 | 53 |
| dropped | `talk_to_themselves > only_this_moment` | 0 | 2 | 0 | 12 |

### C. Cheapest first, a ladder (C1)

| Outcome | Path | task | chat | nothing | dropped |
|---|---|---|---|---|---|
| brain | `c2_request=an_explicit_request_to_the_ai` | 165 | 71 | 32 | 10 |
| respond | `c3_to_ai=talk_with_the_ai` | 14 | 32 | 8 | 40 |
| respond | `c3_to_ai=a_question_to_the_ai` | 3 | 36 | 9 | 16 |
| context | `c4_lasting=a_problem_with_the_ais_work` | 1 | 11 | 12 | 15 |
| context | `c4_lasting=lasting_player_or_world_info` | 0 | 3 | 1 | 6 |
| context | `c4_lasting=a_plan_or_rule` | 1 | 2 | 0 | 4 |
| dropped | `otherwise` | 2 | 21 | 6 | 155 |
| dropped | `c1_noise=only_filler_or_a_reaction` | 0 | 2 | 3 | 89 |
| dropped | `c1_noise=garbled_speech` | 1 | 5 | 1 | 66 |

### D. Consequence first, two keys (D2)

| Outcome | Path | task | chat | nothing | dropped |
|---|---|---|---|---|---|
| brain | `the_ai_must_act + let_down_wanted_something_done` | 170 | 74 | 29 | 4 |
| respond | `the_ai_should_answer` | 9 | 57 | 14 | 34 |
| respond | `the_ai_must_act + fine_a_reply_is_all` | 1 | 4 | 1 | 1 |
| context | `remember_it` | 3 | 5 | 7 | 0 |
| dropped | `ignore_it` | 4 | 43 | 21 | 362 |

### E. Observable signals, the tree in code (E1/r2)

| Outcome | Path | task | chat | nothing | dropped |
|---|---|---|---|---|---|
| brain | `you_with_no_name, instruction` | 109 | 35 | 20 | 18 |
| brain | `names_the_ai, instruction` | 48 | 26 | 4 | 1 |
| respond | `you, a_question, something_the_ai_made_or_did` | 4 | 28 | 6 | 14 |
| respond | `names_a_player, instruction` | 5 | 1 | 4 | 11 |
| respond | `ai, a_statement` | 2 | 7 | 1 | 8 |
| respond | `you, a_question, the_world_or_the_game` | 1 | 2 | 1 | 3 |
| respond | `ai, a_question` | 0 | 1 | 1 | 2 |
| context | `no_one, something_the_ai_made_or_did, only_this_moment` | 3 | 24 | 15 | 52 |
| context | `no_one, something_the_ai_made_or_did, still_true_in_five_minutes` | 4 | 11 | 6 | 12 |
| context | `you_with_no_name, something_the_ai_made_or_did, only_this_moment` | 2 | 4 | 3 | 14 |
| context | `names_a_player, something_the_ai_made_or_did, only_this_moment` | 0 | 1 | 1 | 14 |
| context | `no_one, a_player, still_true_in_five_minutes` | 0 | 1 | 0 | 7 |
| context | `no_one, the_world_or_the_game, still_true_in_five_minutes` | 1 | 4 | 1 | 1 |
| context | `you_with_no_name, something_the_ai_made_or_did, still_true_in_five_minutes` | 4 | 2 | 0 | 1 |
| context | `names_a_player, something_the_ai_made_or_did, still_true_in_five_minutes` | 1 | 0 | 0 | 3 |
| context | `you_with_no_name, a_player, still_true_in_five_minutes` | 1 | 1 | 0 | 1 |
| context | `no_one, nothing_in_particular, still_true_in_five_minutes` | 0 | 0 | 0 | 2 |
| context | `names_a_player, a_player, still_true_in_five_minutes` | 0 | 0 | 1 | 1 |
| context | `you_with_no_name, the_world_or_the_game, still_true_in_five_minutes` | 0 | 2 | 0 | 0 |
| context | `names_a_player, the_world_or_the_game, still_true_in_five_minutes` | 0 | 0 | 0 | 1 |
| dropped | `no_one, nothing_in_particular, only_this_moment` | 0 | 1 | 1 | 77 |
| dropped | `exclamation` | 0 | 1 | 1 | 62 |
| dropped | `no_one, the_world_or_the_game, only_this_moment` | 1 | 13 | 2 | 34 |
| dropped | `no_one, a_player, only_this_moment` | 0 | 10 | 1 | 13 |
| dropped | `you_with_no_name, a_player, only_this_moment` | 1 | 2 | 0 | 16 |
| dropped | `you_with_no_name, nothing_in_particular, only_this_moment` | 0 | 2 | 1 | 11 |
| dropped | `names_a_player, nothing_in_particular, only_this_moment` | 0 | 0 | 1 | 13 |
| dropped | `names_a_player, a_player, only_this_moment` | 0 | 0 | 1 | 8 |
| dropped | `you_with_no_name, the_world_or_the_game, only_this_moment` | 0 | 2 | 0 | 1 |
| dropped | `names_a_player, the_world_or_the_game, only_this_moment` | 0 | 2 | 0 | 0 |

## How it was run

- **The lines:** all 843 lines said on 2026-10-08, in time order. Each was asked with the state Jev had when it judged the line then, as `tree_bench.Bench` builds it:
  - Jev's rules;
  - the brain's lookout;
  - the brain's 70-action menu;
  - three hours of history with the brain's AI rows, in the 8,000-token state.
  - Only the line itself is asked about. Unlike Jev today, no `not_relevant` line is asked again.
- **The halves:**
  - The first 421 lines (05:26–06:57, 100 asks) set the wording and E's rules.
  - The last 422 (06:57–07:59, 87 asks) are held out.
- **The runs:**
  1. A 20-line pilot of each of B, C, D and E: 4,500–5,000 tokens a call, $0.004 each, every answer well formed.
  2. B1, C1, D1 and E1 on the first half.
  3. B2 and D2 on the first half.
  4. With the wording and rules fixed, B2, C1, D2 and E1 on the second half.
  5. D2 again on the second half, for run-to-run noise.
  - No call failed: 4,636 calls.
- **A** is the tree bench's v2 run, walked on member A alone.
- **Jev today** is `classes.py`'s `jev` run of 2026-10-10, flips applied.
- **Respond B** is the gate bench's B rows. B ran with the brain's context under Jev today's classes, not each tree's, so its hand-ups are an approximation for the trees.
- **No batch calls are counted.** The brain no longer has a batch timer.
- **TypeSafe spend: 35.0M input tokens, $1.47.** No OpenRouter spend.

| Run | Lines | Tokens | Cost |
|---|---|---|---|
| B1, first half (with the pilot) | 421 | 3.11M | $0.131 |
| B2, all 843 | 843 | 6.36M | $0.267 |
| C1, all 843 (with the pilot) | 843 | 6.61M | $0.278 |
| D1, first half (with the pilot) | 421 | 3.05M | $0.128 |
| D2, all 843 | 843 | 6.22M | $0.261 |
| E1, all 843 (with the pilot) | 843 | 6.47M | $0.272 |
| D2 again, second half | 422 | 3.16M | $0.133 |
| **All** | **4,636** | **34.98M** | **$1.47** |

## Run it

```sh
(cd brain && uv sync --frozen --inexact)
PY=brain/.venv/bin/python; DB=DIR/vfg/vfg.db; OUT=TREES_OUT     # the playtest's database, as README.md says
JEV=CLASSES_OUT/jev.jsonl; B=GATE_OUT/B/rows.jsonl; TREE=TREE_OUT # classes.py jev; the gate bench's B; the tree bench's OUT (A)
ENV=--env=FILE                                                    # the file that holds TYPESAFE_API_KEY, if not in the environment
for v in B1 C1 D1 E1; do $PY integration/replay/trees_bench.py $v --db $DB --out $OUT --half first --limit 20 $ENV; done  # pilots
for v in B1 C1 D1 E1; do $PY integration/replay/trees_bench.py $v --db $DB --out $OUT --half first $ENV; done
for v in B2 D2; do $PY integration/replay/trees_bench.py $v --db $DB --out $OUT --half first $ENV; done
python3 integration/replay/trees_score.py $OUT --jev $JEV --gate-b $B --a $TREE --tune               # OUT/tune.md
for v in B2 C1 D2 E1; do $PY integration/replay/trees_bench.py $v --db $DB --out $OUT --half second $ENV; done   # held out
$PY integration/replay/trees_bench.py D2 --db $DB --out $OUT-repeat --half second $ENV               # run-to-run noise
python3 integration/replay/trees_score.py $OUT --jev $JEV --gate-b $B --a $TREE --final B2 C1 D2 E1/r2 \
    --repeat $OUT-repeat --combos                                                                     # OUT/score.md
```
