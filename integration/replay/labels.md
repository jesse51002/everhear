# The 2026-10-08 labels, with hindsight

The benches scored routing against labels that were wrong in one direction. A line counted as an
ask only when a task the conversation wrote named its number, and as chat when the AI said
anything in the 60 s after it. A hand reading of 103 of tree D's "chat" and "nothing" lines found
86 asks and answers among them. These labels replace them: Jev labels every one of the 843 lines,
**seeing what the AI said and did after it**, and the routings are scored again from the answers
they already gave.

- **`labels_build.py`**: the hindsight record, the questions, the combining rules, the labels file, the review.
- **`labels_score.py`**: the rescoring.
- **`labels_2026-10-08.jsonl`**: the labels, one line a row, with each member's answer.
- **`labels_check.jsonl`**: the hand reading, 230 lines.
- **`labels_review.md`**: the 60 random lines and every disputed one, for skimming.
- **`labels_score.md`**: the full rescoring, every heat map and every lost ask.

## How a line is labelled

**The record.** Jev's state holds:

- the line: who, when, voice or chat, and whether the conversation saw it then;
- BEFORE: what was said in the two minutes before it;
- HINDSIGHT, marked as what happened after the line:
  - the AI's chat in the 90 s after it, each with its seconds since;
  - every task written in those 90 s, and any later task whose context names the line;
  - for each task, whether its context names this line, the context itself, and its done or failed result.

**Five labels**, each with a balanced description:

| Label | Is | Goes to |
|---|---|---|
| `request` | asks the AI to do, make, give, change, fix, undo or stop something in the game, whether it was done, declined, asked about or never seen | brain |
| `answer_to_ai` | answers a question the AI asked: a yes, a no, a choice, a value | brain |
| `talk_to_ai` | said to the AI, needs nothing done: a question, a greeting, a joke, thanks | Respond |
| `context` | not to the AI, but lasting: a plan, a rule, a fact still true in five minutes, a problem with what the AI made | context |
| `noise` | talk of the moment between players or to no one, a reaction, filler, garble | dropped |

**A council of three questions in one Jev call:**

- `what`: given what the AI said and did after it, what was this line?
- `change`: did the speaker want something to change in the game?
- `to`: who was the line said to?

Written rules combine them, and a line is **disputed** when they disagree (`RULES_DOC` in
`labels_build.py`). Rules r2 are r1 plus one change. When `what` and `change` agree but `to`
disagrees and is at least as sure, `to` decides whether the line was for the AI.

## Versions and how right they are

The **pilot** was 20 seeded lines on v1, and 18 were right by hand. The two misses were a
complaint about the Galick Gun and "Six minutes", both called requests.

Each version was then read by hand on two sets:

- 60 lines picked at random, seeded;
- every disputed line, read by hand for that version.

"Accepted" counts a label the reader also allows on a line that reads two ways.

| Wording | Rules | request / answer / talk / context / noise | Disputed | Random 60 right | Disputed right | Disputed right or accepted |
|---|---|---|---|---|---|---|
| v1 | r1 | 301 / 17 / 54 / 13 / 458 | 156 | 52 (87%) | 76 of 145 (52%) | 106 (73%) |
| v2 | r1 | 290 / 13 / 31 / 9 / 500 | 87 | 57 (95%) | 42 of 79 (53%) | 60 (76%) |
| **v2** | **r2** | **285 / 13 / 29 / 10 / 506** | **87** | **58 (97%)** | **45 of 79 (57%)** | **63 (80%)** |

**v1's systematic mistakes:**

- Player-to-player questions and remarks called `talk_to_ai` (12 of them) or `request` (15).
- Asks at the end of a long ramble called `noise`.
- Asks in lines the AI never saw called `noise`.

**What v2 changed**, all general:

- Most lines are between players. A question, remark or complaint said to a player is not talk to the AI, even when it is about the AI's work.
- Reading the AI's message aloud is noise.
- A request anywhere in a line makes it a request, and so does one in a line the AI never saw.
- `change` says outright that wanting something from a player is a no.

**Why r2.** On v1's hand-read lines, `what` and `change` sometimes both called a line an ask or
talk while `to` said it was said to a player, to themselves or to no one. In 30 of those 41 lines,
`to` was right.

**Run-to-run noise.** v2 was asked again on all 843 lines. 831 lines (98.6%) got the same label.

**Cross-check against the earlier hand reading.** Of its 103 lines, v2/r2 calls 83 asks (77
requests, 6 answers) and 20 other. The earlier hand reading found 86 asks and 17 others.

## The labels against the old ones

| old | request | answer_to_ai | talk_to_ai | context | noise | all |
|---|---|---|---|---|---|---|
| task | 184 | 2 | 0 | 0 | 1 | 187 |
| chat | 71 | 9 | 19 | 8 | 76 | 183 |
| nothing | 27 | 2 | 3 | 2 | 38 | 72 |
| dropped | 3 | 0 | 7 | 0 | 391 | 401 |

There are **298 asks, not 187**. 109 of the old `chat` and `nothing` lines are asks:

- asks the AI acted on, with the task credited to another line;
- asks it declined (consent, owner rules, "one thing at a time");
- asks it asked to clarify;
- answers to its own questions.

## Rescored

Every routing is scored from its recorded answers, with no new routing calls.

- **Asks** are `request` and `answer_to_ai`. An ask reaches the brain straight, or through Respond B's recorded hand-up.
- **Undisputed asks** leaves out the asks the council disagreed on.
- **Noise answered in chat** is noise sent to Respond that B answered in chat instead of handing up. B answers in chat almost every noise line it does not hand up.
- **Cost** is a 2.5 h session, as `trees_score.py` counts it.

| Routing | Asks reach the brain | direct + via B | Asks lost: chat / context / dropped | Undisputed asks reach | Talk and noise to the brain | Noise answered in chat | Cost | *Old: asks reach* | *Old: talk to the brain* |
|---|---|---|---|---|---|---|---|---|---|
| Jev today | **289 of 298 (97%)** | 284 + 5 | 7 / 1 / 0, +1 | 271 of 273 (99%) | 39 of 535 (7%) | 310 | $3.85 | *183 of 187 (98%)* | *129 of 255 (51%)* |
| A (v2) | **282 of 298 (95%)** | 281 + 1 | 5 / 7 / 4 | 267 of 273 (98%) | 45 of 535 (8%) | 43 | $3.22 | *178 of 187 (95%)* | *132 of 255 (52%)* |
| B2 | **279 of 298 (94%)** | 275 + 4 | 5 / 12 / 2 | 264 of 273 (97%) | 34 of 535 (6%) | 56 | $3.18 | *178 of 187 (95%)* | *117 of 255 (46%)* |
| C1 | **274 of 298 (92%)** | 259 + 15 | 14 / 2 / 7, +1 | 260 of 273 (95%) | 18 of 535 (3%) | 88 | $3.13 | *176 of 187 (94%)* | *103 of 255 (40%)* |
| D2 | **265 of 298 (89%)** | 253 + 12 | 15 / 2 / 15, +1 | 258 of 273 (95%) | 23 of 535 (4%) | 58 | $3.02 | *176 of 187 (94%)* | *103 of 255 (40%)* |
| E1 rules r2 | **257 of 298 (86%)** | 239 + 18 | 9 / 27 / 4, +1 | 247 of 273 (90%) | 22 of 535 (4%) | 47 | $2.89 | *167 of 187 (89%)* | *85 of 255 (33%)* |
| D2 + B's net | **266 of 298 (89%)** | 253 + 13 | 22 / 1 / 6, +3 | 258 of 273 (95%) | 23 of 535 (4%) | 74 | $3.11 | *176 of 187 (94%)* | *103 of 255 (40%)* |

"+1" and "+3" are asks sent to Respond on lines outside B's set. The held-out second half ranks
the same way (`labels_score.md`).

**Jev today** (rows: new label; columns: where it went; the right cell in bold):

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **272 (95%)** | 12 (4%) | 1 (0%) | 0 (0%) | 285 |
| answer_to_ai | **12 (92%)** | 1 (8%) | 0 (0%) | 0 (0%) | 13 |
| talk_to_ai | 1 (3%) | **28 (97%)** | 0 (0%) | 0 (0%) | 29 |
| context | 1 (10%) | 8 (80%) | **1 (10%)** | 0 (0%) | 10 |
| noise | 38 (8%) | 333 (66%) | 12 (2%) | **123 (24%)** | 506 |

**D2:**

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **246 (86%)** | 23 (8%) | 2 (1%) | 14 (5%) | 285 |
| answer_to_ai | **7 (54%)** | 5 (38%) | 0 (0%) | 1 (8%) | 13 |
| talk_to_ai | 0 (0%) | **24 (83%)** | 0 (0%) | 5 (17%) | 29 |
| context | 1 (10%) | 2 (20%) | **4 (40%)** | 3 (30%) | 10 |
| noise | 23 (5%) | 67 (13%) | 9 (2%) | **407 (80%)** | 506 |

## What moved most, and why

1. **The asks: 187 to 298.**
   - The old labels missed the asks the conversation answered in chat: the task named another line, the ask was declined, or the AI asked what was meant. They also missed the answers to the AI's own questions.
2. **Talk sent to the brain fell, for every routing: from 33–52% of the old talk to 3–8% of talk and noise.**
   - Most of the "talk" the routings sent to the brain was asks.
   - On the old labels this read as waste. It was the routings being right.
3. **Asks lost grew, and much more for the trees than for Jev today.**

   | Routing | Asks lost, old labels | Asks lost, new labels |
   |---|---|---|
   | Jev today | 4 | 9 |
   | A | 9 | 16 |
   | B2 | 9 | 19 |
   | C1 | 11 | 24 |
   | D2 | 11 | 33 |
   | E | 20 | 41 |

   - The 111 new asks are the hard ones: declined asks, answers ("Yes.", "No.", "Fine."), asks at the end of a ramble, and garbled ones.
   - D2 sends asks put as questions ("Where is my death gaze, please?", "Can make music for each of the attacks?") to Respond as `the_ai_should_answer`, and answers ("Yes.", "No.", "Don't stop anything.") there through its second key, which reads them as fine with a reply.
   - At Respond, B answers it in chat. B handed up 15% of the answers to the AI's questions it ran, and 70% of the requests.
   - D2's `ignore_it` drops 15 more.
4. **Cost barely moved.** It depends on the routing, not on the labels. Only the estimate for Respond lines outside B's set changed.

## Recommendation

- **Use A (who, then what: the tree bench's v2, member A) for Jev's routing call, not D2.**
  - The trees bench picked D2 on the old labels.
  - On these labels D2 loses 33 of 298 asks (11%), twice A's 16. That is 17 more asks lost to save $0.20 a session.
  - A reaches 282 asks (95%, and 98% of the undisputed ones).
  - A sends 8% of talk and noise to the brain, and Respond answers about 43 noise lines in chat.
  - A costs $3.22 a session, about $1.29 an hour.
- **Two runners-up:**
  - **B2** is close behind A: 279 asks, $3.18.
  - **Jev today** reaches 7 more asks than A (97%). But it sends 333 noise lines to Respond, which answers about 310 of them in chat: the AI talking over the players' banter every 30 seconds or so. It also costs $0.63 more.
- **Drop B's net on D2.** It moves 1 ask.
- **Caveats:**
  - The labels are Jev's own, made with hindsight, and so are all the routings.
  - The hand reading checks 230 lines: 97% right on the random 60, but only about half right on disputed lines. That is why the undisputed column is there.
  - A's Jev cost is scaled from its 9-question run (`trees_score.py`).
  - B's hand-ups are one recorded run.

## Cost and commands

- **TypeSafe: $0.31 in all.** Pilot $0.002, v1 $0.098, v2 $0.104, v2 asked again $0.104. Each full run is about 2.4 M input tokens (2,900 a call) and 3 minutes, a call 190 ms at the median.
- **No OpenRouter calls.**

```sh
PY=brain/.venv/bin/python; DB=DIR/vfg/vfg.db; S=SCRATCH   # the playtest's vfg.db; the benches' outputs
$PY integration/replay/labels_build.py ask v1 --db $DB --out $S/labels-pilot --pilot 20
$PY integration/replay/labels_build.py ask v1 --db $DB --out $S/labels
$PY integration/replay/labels_build.py ask v2 --db $DB --out $S/labels
$PY integration/replay/labels_build.py ask v2 --db $DB --out $S/labels-repeat              # run-to-run noise
python3 integration/replay/labels_build.py label $S/labels/v2 --rules r2 --to integration/replay/labels_2026-10-08.jsonl
python3 integration/replay/labels_build.py label $S/labels-repeat/v2 --rules r2 --to $S/labels/labels-repeat.jsonl
python3 integration/replay/labels_build.py review $S/labels/v2 --rules r2 --check integration/replay/labels_check.jsonl \
    --to integration/replay/labels_review.md --history $S/labels/v1:r1 $S/labels/v2:r1 $S/labels/v2:r2
python3 integration/replay/labels_score.py --labels integration/replay/labels_2026-10-08.jsonl --trees $S/trees \
    --a $S/tree --jev $S/replay/classes/jev.jsonl --gate-b $S/gate/B/rows.jsonl \
    --repeat $S/labels/labels-repeat.jsonl --to integration/replay/labels_score.md
```
