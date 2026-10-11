# D with and without the gather call

The Jev routing spec (`2026-10-10-Prototype_Jev_Routing`, _Test_) requires "D with and without the
gather call", on the clean labels, the second half held out. This is that measurement, on what ships:
Jev's production filter on the `jev-routing` branch (215c8fa), run line by line on the 843 lines of
2026-10-08.

- **`gather_bench.py`**: the replay. Each line goes through `jev/filter.py`'s own `Filter._judge_one`.
- **`gather_score.py`**: the scoring, from the rows alone, no model calls.
- **`gather_bench.md`**: this page.

## 1. How it was measured

**The filter is production's.** For each line, in production's order (`Store.unjudged`: by `at`,
then `id`), `Filter._judge_one` runs as it does on the box:

- the routing call: D2's two questions (`prompt.questions`) on the production state
  (`prompt.build_state`: the rules, `brain/prompts/lookout.md`, the examples block with its two edits,
  the brain's menu, the history), walked by `prompt.route`;
- for a line classed `action_oriented` or `conversational`, `Filter._gather`: the window from
  `Store.gather_window`, one call with `prompt.gather_questions` on the gather state (`GATHER_RULES`,
  `GATHER_HEADING`), and the yes lines fitted by `filter.fit_group`.

The window is read from a sqlite database built from §4's schema, holding every line of the playtest
with this replay's verdicts so far. The recorded whole-ask answers are not reused.

**Two things are the bench's.** `Filter._state` is replaced by one that builds the same state from
what Jev had when it judged the line then: the lines and the brain's records visible by the line's
`judged` time, three hours back, as the other replay benches build it. A call that fails is tried
again, a second apart, up to five times, as the other benches do. No call failed and none was
refused, so no try was repeated.

**Checks.** Every window was derived again from the rows by the plan's rule, in a script separate
from `store.py`: 382 windows, 0 differ. Production's gather wording asked a second time on the first
half's 89 windows gave the same answer on all 196 lines, the same input tokens on every call, and
probabilities within 0.10.

**Where a line goes.** Without the gather call, by its class: `action_oriented` to the brain,
`conversational` to Respond, `context` kept, `not_relevant` dropped. Each line to the brain or
Respond is one event. With the gather call, the event is the ask and its group, and a gathered line
counts where its ask went. It is also fed as its own class, which costs nothing.

**Reaching the brain.** An event reaches the brain when it goes there, or when it goes to Respond and
Respond B hands it up. B's hand-ups are the gate bench's recorded run (592 of the 843 lines), counted
as `whole_asks_score.py` counts a merged input: an event is handed up when B handed up any of its
lines that B ran. An event none of whose lines B ran is counted apart ("+1" below), and in the cost at
B's hand-up rate for its label. A line reaches the brain when its event does. An ask input (one row
of `whole_asks_2026-10-08.jsonl`) reaches the brain when any of its lines does. It arrives whole when
one event that reaches the brain holds all of its lines.

**Cost** is a 2.5 h session (the 843 lines): brain calls × $0.0085, Respond calls × $0.0015, and
Jev's input tokens × $42 per billion. **Latency** is Jev's ms per call. On a line to the AI with a
gather call, the routing and gather calls run one after the other, before the line is fed.

**Halves.** A line's half is the trees benches': the first 421 lines in time order are the first
half. An input's half is its last line's, and an event's is its ask's. The gather wordings were tuned
on the first half only. The second half is reported on its own.

## 2. Summary

| | Ask lines reach the brain | direct + via B | Lost: chat / context / dropped | Ask inputs reach (any line) | Ask inputs arrive whole | Talk and noise lines to the brain | Noise dropped | Cost | Jev ms, every routing call | Jev ms, a line to the AI |
|---|---|---|---|---|---|---|---|---|---|---|
| D without gather, all 843 lines | **267 of 298 (90%)** | 250 + 17 | 15 / 6 / 9, +1 | **224 of 243** | 175 of 243 (0 of 50 split) | 18 of 535 (3%) | 372 of 506 (74%) | **$2.97** | 219 / 248 ms | 220 / 248 ms |
| **D with gather, all 843 lines** | **267 of 298 (90%)** | 250 + 17 | 16 / 5 / 9, +1 | **224 of 243** | 175 of 243 (0 of 50 split) | 19 of 535 (4%) | 372 of 506 (74%) | **$3.03** | 219 / 248 ms | 263 / 465 ms |
| D with gather worded g3, all 843 lines | **269 of 298 (90%)** | 252 + 17 | 15 / 6 / 7, +1 | **224 of 243** | 175 of 243 (0 of 50 split) | 21 of 535 (4%) | 370 of 506 (73%) | **$3.03** | 219 / 248 ms | 263 / 471 ms |
| D without gather, second half | **124 of 138 (90%)** | 114 + 10 | 5 / 3 / 6 | **101 of 111** | 78 of 111 (0 of 23 split) | 12 of 280 (4%) | 191 of 266 (72%) | **$1.43** | 220 / 249 ms | 220 / 248 ms |
| **D with gather, second half** | **124 of 138 (90%)** | 114 + 10 | 5 / 3 / 6 | **101 of 111** | 78 of 111 (0 of 23 split) | 13 of 280 (5%) | 191 of 266 (72%) | **$1.46** | 220 / 249 ms | 410 / 468 ms |
| D with gather worded g3, second half | **124 of 138 (90%)** | 114 + 10 | 5 / 3 / 6 | **101 of 111** | 78 of 111 (0 of 23 split) | 15 of 280 (5%) | 189 of 266 (71%) | **$1.46** | 220 / 249 ms | 414 / 478 ms |

*With gather* is production's wording. *g3* is the candidate wording under _7. The wording_. Latency
is median / p90. *Lost* is chat / context / dropped: answered in chat by Respond B, kept as context,
dropped; "+1" is an ask sent to Respond on a line outside B's set. *Arrive whole* counts the
single-line ask inputs that reach the brain and the split ones that arrive in one event. In brackets
are the 50 ask inputs split across lines.

**The gather call changes almost nothing on this session.** It runs on 184 of the 382 lines D sends to
the AI. Production's wording says yes to 2 of the 408 lines it is asked about, and g3 to 8. No split
ask arrives whole, with either wording. Asks reaching the brain go from 267 to 267 with production's
wording, and to 269 with g3. The cost is $0.057 a session in Jev tokens. On a line with a gather call,
Jev's time goes from about 220 ms to about 440 ms.

**D without the gather call, line by line, on the production state:** 267 of 298 ask lines (90%) and
224 of 243 ask inputs reach the brain. 18 of 535 talk and noise lines go to the brain, 74% of the
noise is dropped, and it costs $2.97 a session. The spec's D2 line by line on the old state: 265 of
298, 23 of 535, $3.02.

## 3. The heat maps

Rows are the clean labels, columns where the line went, with the row's share. With the gather call,
a gathered line counts where its ask went. The cell where the label belongs is in bold.

**D without the gather call**

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **242 (85%)** | 28 (10%) | 6 (2%) | 9 (3%) | 285 |
| answer_to_ai | **8 (62%)** | 5 (38%) | 0 (0%) | 0 (0%) | 13 |
| talk_to_ai | 1 (3%) | **23 (79%)** | 0 (0%) | 5 (17%) | 29 |
| context | 0 (0%) | 0 (0%) | **10 (100%)** | 0 (0%) | 10 |
| noise | 17 (3%) | 58 (11%) | 59 (12%) | **372 (74%)** | 506 |

**D with the gather call, production's wording**: two lines move. A request kept as context goes to
Respond inside a noise line's event, and a noise line kept as context goes to the brain inside an
ask.

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **242 (85%)** | 29 (10%) | 5 (2%) | 9 (3%) | 285 |
| answer_to_ai | **8 (62%)** | 5 (38%) | 0 (0%) | 0 (0%) | 13 |
| talk_to_ai | 1 (3%) | **23 (79%)** | 0 (0%) | 5 (17%) | 29 |
| context | 0 (0%) | 0 (0%) | **10 (100%)** | 0 (0%) | 10 |
| noise | 18 (4%) | 58 (11%) | 58 (11%) | **372 (74%)** | 506 |

**D with the gather call, worded g3**: eight lines move, all into events to the brain or Respond.

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **244 (86%)** | 28 (10%) | 6 (2%) | 7 (2%) | 285 |
| answer_to_ai | **8 (62%)** | 5 (38%) | 0 (0%) | 0 (0%) | 13 |
| talk_to_ai | 1 (3%) | **23 (79%)** | 0 (0%) | 5 (17%) | 29 |
| context | 1 (10%) | 0 (0%) | **9 (90%)** | 0 (0%) | 10 |
| noise | 20 (4%) | 60 (12%) | 56 (11%) | **370 (73%)** | 506 |

**The second half, held out.** Without the gather call:

| label | brain | Respond | context | dropped | all |
|---|---|---|---|---|---|
| request | **111 (82%)** | 15 (11%) | 3 (2%) | 6 (4%) | 135 |
| answer_to_ai | **3 (100%)** | 0 (0%) | 0 (0%) | 0 (0%) | 3 |
| talk_to_ai | 0 (0%) | **12 (86%)** | 0 (0%) | 2 (14%) | 14 |
| context | 0 (0%) | 0 (0%) | **4 (100%)** | 0 (0%) | 4 |
| noise | 12 (5%) | 33 (12%) | 30 (11%) | **191 (72%)** | 266 |

With production's wording, one noise line moves from context to the brain (13 / 33 / 29 / 191).
Worded g3, one context line goes to the brain (1 / 0 / 3 / 0), and four noise lines join asks
(15 / 34 / 28 / 189). Every other cell is as above.

## 4. The gather call itself

| | All 843 lines | First half | Second half |
|---|---|---|---|
| Lines to the AI | 382 | 196 | 186 |
| Gather calls (a non-empty window) | **184** | 89 | 95 |
| Lines asked about | 408 | 196 | 212 |
| Yes, production's wording | **2 (0.5%)** | 1 | 1 |
| Yes, g3 | 8 (2.0%) | 3 | 5 |
| Jev ms a call, median / p90 / most | **218 / 245 / 379** | 216 / 248 / 330 | 220 / 244 / 379 |
| Routing and gather call together, on a line with a gather call | **439 / 485** | 439 / 485 | 440 / 485 |
| Input tokens, a call at the median | 7,334 | 7,268 | 7,358 |
| Extra cost | **$0.057 a session** | $0.027 | $0.030 |

- **Window sizes** (lines asked about: calls): 1: 87, 2: 41, 3: 18, 4: 19, 5: 11, 6: 4, 7: 2, 8: 2.
  The cap of 10 never bit; the largest window held 8 lines.
- **Ms against window size**: 1 line, 220 / 243 ms (87 calls); 2–3, 215 / 248 (59); 4–6, 218 / 245
  (34); 7–10, 224 / 377 (4). Tokens at the median grow from 7,223 to 8,544. The time does not grow
  with the window.
- **Jev accepted every call**: 0 refusals, 0 failed tries, and every question got a well-formed
  answer. No call ran over the filter's 3 s timeout; the slowest took 379 ms.
- **10-question calls**: none happened in the replay. Production's gather call was asked 12 times on
  windows made full: the speaker's 10 newest unsent lines before a line to the AI, with neither the
  180 s nor the last-sent bound. All 12 were accepted with all 10 answers well formed: 242 ms median,
  300 p90, 333 at most, 8,928 tokens at the median.
- **fit_group** dropped no line: no group came near the 500 characters.
- The spec's estimate was "about $0.0003 and 0.2 s" a call, "at most about $0.13 a session". The
  measured cost is $0.00031 a call at the median and $0.057 a session.

## 5. Why the gather call finds so little here

**Most split asks are an ask said again or corrected, and D sends each piece on its own.** 37 of the
53 merged whole-ask inputs hold asks only (`whole_asks.md`). The 50 ask inputs split across lines
hold 120 lines. D sends 109 of them to the brain or Respond by themselves, and 42
of the 50 inputs have two or more lines sent. The gather call looks only at unsent lines, back to the
speaker's last sent line, so it cannot join a piece that was sent. That is by design: "Lines already
sent were seen with their own ask."

Why each of the 50 split ask inputs does not arrive whole (the event that holds most of the input,
the pieces left out of it):

| Why | Inputs |
|---|---|
| every piece left out was sent on its own, to the brain or Respond | 38 |
| a piece said after the ask, not sent (the gather call looks back only) | 5 |
| a piece in the window, and Jev said no | 4 |
| a piece outside the window, before the speaker's last line sent to the AI | 2 |
| no line of it reaches the brain | 1 |

**The windows are almost all noise.** Of the 408 lines the gather call asked about, 381 are labelled
noise, 15 request, 7 context and 5 talk_to_ai. Only 5 are in their ask's own whole-ask input.

**What the gather adds, production's wording:** no ask input arrives whole that did not before. No ask
piece D dropped or kept as context reaches the brain inside a group. One request kept as context goes
to Respond inside a noise line's event, and Respond B answers that event in chat. One noise line joins
an ask to the brain. **Worded g3:** two ask pieces D dropped reach the brain inside a group ("totally
give us a bunch of cool stuff", and "Do."). Three noise lines and one context line join asks to the
brain, and two noise lines join events to Respond.

## 6. Every gathered line, and the borderline ones

*P* is Jev's probability for `part_of_the_ask`; a line is gathered when it is over 0.5 (**yes**).
*Grouping* is whether `whole_asks_2026-10-08.jsonl` puts the line in its ask's input. Every line
either wording gathered is listed, and every window line in its ask's input or with P ≥ 0.35 under
either wording. *D* is the line's own class.

| Half | Line | Ask → where | P prod | P g3 | Grouping | My read |
|---|---|---|---|---|---|---|
| first | `06:06:20` Eclipszr “Matthew. Matthew. Would you agree that D two is one of the best games ever?” [noise, D: dropped] | “Yes. Air that cloud, he said yes.” [answer_to_ai] → brain | 0.28 | 0.23 | same | **A piece, missed by both.** The AI asked for the target's consent to a kill; this is the question the "yes" answered, so the consent was a trick. The line was dropped, so without it the brain cannot see that. |
| first | `06:19:23` Styrkaeopteryx “My castle.” [noise, D: context] | “it built yet.” [noise] → Respond | 0.43 | 0.54 **yes** | apart | **A piece**: one sentence cut at a pause, "My castle … [is] it built yet". The grouping does not join them; neither line is an ask. |
| first | `06:22:43` KrownJM “Yes it's Everyone. It's doing a quick test run before he starts this. I just kind of sad to try that it's sli…” [context, D: context] | “Is that good to charge up all the wafers explode? No. I don't get it.” [noise] → Respond | 0.36 | 0.25 | same | Arguable: both lines are about something of his not exploding every time. Leaving it out costs little. |
| first | `06:25:36` Eclipszr “totally give us a bunch of cool stuff.” [request, D: dropped] | “Claude, where's the thing I asked for the chest mysterious weapons? Scary up and up, please and thank you.” [request] → brain | 0.19 | 0.52 **yes** | same | **A piece**: the same ask, said between its first telling (sent at 06:25:01) and this follow-up. |
| first | `06:28:35` Styrkaeopteryx “How” [noise, D: dropped] | “Is it possible to increase the lightning's damage? Mm, I guess. How” [request] → brain | 0.04 | 0.20 | same | No is right: "How" adds nothing. |
| first | `06:31:30` Eclipszr “Do.” [request, D: dropped] | “me” [noise] → brain | 0.44 | 0.57 **yes** | apart | Garble joined to garble: D sends "me" to the brain and g3 adds "Do.". Harmless. The grouping is right that "Do." is the tail of the 06:31:30 affinity ask. |
| first | `06:38:14` KrownJM “Okay, can we like stay together and actually like clear out some like one place to live?” [request, D: context] | “do. Oh, that's what you would do it tomorrow. That's crazy. They brought us all together. To what? Well, I th…” [noise] → Respond | 0.55 **yes** | 0.49 | apart | **The grouping is right.** The later line reacts to what the AI did about the earlier one. Joining them sends a real ask, which D kept as context, to Respond inside a reaction, and B answers it in chat. Related, but not useful. |
| first | `06:56:55` Eclipszr “No, is that? So that's” [noise, D: dropped] | “No, Claude don't You listen, Claude, don't do it. Don't Don't do it.” [request] → brain | 0.13 | 0.40 | apart | A false start of the same refusal. Either answer is harmless. |
| second | `06:58:04` Eclipszr “But those” [noise, D: dropped] | “Fine.” [noise] → brain | 0.18 | 0.51 **yes** | apart | Garble joined to a one-word answer D sends to the brain. Harmless. |
| second | `06:58:37` Eclipszr “Oh, I just realized crush. Oh” [noise, D: dropped] | “Can't be damaged and I can't mortgage. I can make them fall but I can't do damage to” [request] → brain | 0.21 | 0.36 | apart | Arguable: likely the gravity crush the ask is about. Leaving it out costs little. |
| second | `07:03:37` KrownJM “I didn't do anything. I'm just like moving. I keep getting killed from. Yeah, tomorrow, what are you?” [noise, D: context] | “where you have a thing where if Like Also, there is a script with creepers and people are just like randomly …” [request] → brain | 0.47 | 0.56 **yes** | apart | **A piece**: it says what the ask is about (he keeps getting killed). The gather is right. The grouping's wording leaves out remarks and talk to a player about an ask. |
| second | `07:13:15` Styrkaeopteryx “The gallegan is like.” [noise, D: dropped] | “Does a little look charged thing?” [talk_to_ai] → Respond | 0.34 | 0.61 **yes** | apart | **A piece**: one sentence cut at a pause, "The Galick gun is like … does it [have a] little charged thing?" Without it the question has no subject. |
| second | `07:16:31` Eclipszr “Oh wait, wait wait. I think the bean class should be here.” [noise, D: dropped] | “Okay, I'm done. Oh wait wait It works. Wait wait, Mattie. Game class works. Wait wait, make a freezer arena. …” [noise] → brain | 0.22 | 0.38 | apart | Arguable: the beam clash, the subject of the later line. Leaving it out costs little. |
| second | `07:31:25` Styrkaeopteryx “I'm still pretty sure Galligun doesn't do any damage.” [context, D: context] | “But my gallic gun doesn't work anymore.” [request] → brain | 0.47 | 0.53 **yes** | apart | **A piece**: it says what is broken. The gather is right. |
| second | `07:31:32` Styrkaeopteryx “No, it said like made it do thirty hertz of damage, but I think it's like specifically the end.” [noise, D: context] | “But my gallic gun doesn't work anymore.” [request] → brain | 0.55 **yes** | 0.57 **yes** | apart | **A piece**: it says how it fails. The gather is right. |
| second | `07:38:23` Styrkaeopteryx “I'm sorry. That claw made Gallic gun destroy everything in the map.” [request, D: context] | “Did you fix Gallic gun killing everyone no matter which way I look?” [request] → Respond | 0.49 | 0.48 | same | Arguable, missed by both: the ask restates the problem, so leaving it out costs little. |
| second | `07:47:00` Eclipszr “I think it's a three hundred by three hundred block deletion.” [context, D: context] | “It's delaying stuff in slices, I'm pretty sure. Oh my god, I think it's going too far. Also, Claude give us a…” [request] → brain | 0.37 | 0.43 | apart | Arguable: the size that "lower it to one hundred by one hundred" changes. The AI's own record says 300 too. |
| second | `07:47:58` KrownJM “I wonder how much like usage I spent on this thing. How much have we spent only twenty three dollars on the c…” [noise, D: dropped] | “Yeah, this is like a cool idea, but we have to make like a bunch of preset stuff to make it a lot cheaper and…” [request] → brain | 0.35 | 0.29 | apart | No is right: talk about the bill, not an ask to the AI. |

**Wrong joins by the grouping file**: production's wording, 2 of 2 gathered lines; g3, 7 of 8 (2 of 3
on the first half, 5 of 5 on the second). **Noise joined into an ask**: production's wording 1, to the
brain; g3 5, 3 to the brain and 2 to Respond.

**My read** (one reader; the grouping file is the yardstick named in the brief):

- **The grouping file answers a narrower question than the gather call.** It was built to merge one
  ask said in pieces or said again. Its `not_an_ask` option covers "a remark, talk to a player (even
  about [1] or what the AI made)", and it only judged the neighbours of lines labelled `request`,
  `answer_to_ai` or `talk_to_ai`. The gather's `part_of_the_ask` covers "it says what, where, who or
  how for the ask". Three g3 joins it calls wrong are lines a player said about the ask's problem ("I
  keep getting killed", and the two Galick gun damage lines). Two are sentences cut at a pause: "My
  castle." … "it built yet.", neither line an ask, and "The gallegan is like." … "Does a
  little look charged thing?".
- **Clear pieces in the windows, by my reading: 7.** Production's wording takes 1 (0 of 3 on the first
  half, 1 of 4 on the second). g3 takes 6 (2 of 3 on the first, 4 of 4 on the second). Both miss the
  consent trick.
- **Joins I would not make:** production's wording 1, the real ask joined to a reaction to it. g3: 2,
  garble joined to garble, both into events that were already going to the brain.

## 7. The wording

Production's wording said yes to 1 of the first half's 196 lines and left out pieces of a sentence
cut at a pause. Its `not_part_of_it` ends "or the ask makes full sense without it", and a short ask
read alone nearly always does. Two candidates were written from the first half's answers. Both give
the two sides a description of what the line is, and neither asks whether the ask stands alone:

| Wording | `part_of_the_ask` | `not_part_of_it` | The instruction |
|---|---|---|---|
| production | Part of the ask: it says what, where, who or how for the ask, or the ask doesn't make sense without it. | Not part of the ask: it is about something else, or the ask makes full sense without it. | `<Steve>'s line [ask] at <time>, “<ask>”, goes to the AI. Line [k], said by <Steve> before it at <time>: “<line>”. Is line [k] part of that ask?` |
| g2 | Part of the ask: a piece of the same sentence or request, cut off by a pause, or it says what, where, who or how for the ask. | Not part of the ask: something else: a reaction, a remark, talk to a player, or another subject or ask. | production's |
| g3 | as g2 | as g2 | production's, with "Speech-to-text cuts a player at every pause, so one ask can arrive as several lines. " before "Line [k]" |

Each was asked on the recorded windows, the state built as production's at the same moment. The
windows are D's verdicts and do not depend on the gather call's answers. *Repeat* is production's
wording asked again, for run-to-run noise.

| Wording | Half | Calls | Yes of asked | In the ask's input (grouping) | Clear pieces taken, by my read | Joins I would not make | Ask lines reach the brain | Talk and noise lines to the brain | Jev ms median / p90 | Tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| production | first | 89 | 1 of 196 | 0 | 0 of 3 | 1 | 143 of 160 | 6 of 255 | 216 / 248 | 645,974 |
| repeat | first | 89 | 1 of 196 | 0 | 0 of 3 | 1 | 143 of 160 | 6 of 255 | 227 / 258 | 645,974 |
| g2 | first | 89 | 2 of 196 | 0 | 0 of 3 | 2 | 144 of 160 | 6 of 255 | 226 / 257 | 648,130 |
| g3 | first | 89 | 3 of 196 | 1 | 2 of 3 | 1 | 145 of 160 | 6 of 255 | 221 / 247 | 651,854 |
| production | second | 95 | 1 of 212 | 0 | 1 of 4 | 0 | 124 of 138 | 13 of 280 | 220 / 244 | 710,182 |
| **g3, held out** | second | 95 | 5 of 212 | 0 | **4 of 4** | 1 | 124 of 138 | 15 of 280 | 228 / 255 | 716,542 |

g2 moved one line over 0.5, "Do." (0.44 to 0.51), and kept production's join of the ask to the
reaction. g3 was chosen on the first half and asked on the second once. On the 408 window lines,
g3's probability is within 0.33 of production's, and 0.01 apart at the median.

**I would put g3 into production.** On the first half it took 2 of the 3 clear pieces, where
production's wording took none, and it dropped production's one join I would not make; it added one
harmless garble join. On the held-out half it took all 4 pieces I read as clear, where production's wording took 1, and it
added one harmless garble join. It costs 0.9% more tokens, the same money at two decimals, and 8 ms
more at the median. Measured against the grouping file, g3 is worse (7 of its 8 joins are outside the
ask's input, against production's 2 of 2), for the reason given under _6_. Either way, the headline
numbers barely move, because the windows rarely hold a piece. The change is two strings in
`jev/prompt.py`: `GATHER_CRITERIA`, and the instruction head in `gather_questions` (`BALANCED` and
`PIECES` in `gather_bench.py`). The jev builder owns that file.

**g3 went into production on 2026-10-11**, the orchestrator's call: `jev/prompt.py`'s `GATHER_CRITERIA`
and `GATHER_WHY`. Every "production" row on this page is the first wording, recorded before that; in
`gather_bench.py` it is now `FIRST`, and `prod` and `repeat` ask it, so they reproduce. `run` asks
whatever production's wording is, g3 since.

### Round two: the live runs

**The live realmc test showed g3's weakness.** Asked again on the gather states of the live runs, g3
gave the scenario's first piece "I'm going down into that ocean monument" 0.55 to 0.70 for "AI, I need
to be able to breathe down there". The first wording gave it 0.77 to 0.86. The final live run, with
g3 in production, said no to it at 0.50. That line tells where "there" is. Two candidates were written
from the playtest's first half and the live states, and a third from the spec's own words:

| Wording | `part_of_the_ask` | `not_part_of_it` | The instruction |
|---|---|---|---|
| g4 | the first wording's | the first wording's | g3's |
| **g5** | Part of the ask: a piece of the same sentence, cut off by a pause, or it says what, where, who or how for the ask, such as what "it", "there" or "one" in the ask points at. | Not part of the ask: it is about something else, such as another subject, another ask or a reaction, and the ask needs nothing from it. | g3's |
| g6 | Part of the ask: it says what, where, who or how for the ask, such as what "it", "there" or "one" in the ask points at, or it is a piece of the same sentence, cut off by a pause. | Not part of the ask: anything else: another subject, another ask, or a reaction. | g3's |

g4 tests g3's sentence on pauses alone. g5 drops the form-based names in g3's `not_part_of_it` ("a
remark", "talk to a player"), which also describe a line like the monument one, and says what each
side is about instead. Its `part_of_the_ask` names what the ask's own words point at, the role every
live piece plays. g6 is g5 in the spec's own order and words ("anything else", named).

**The live states.** `gather_bench.py live` rebuilds each gather state of the 9 live transcripts (the
earlier runs under `vfg/runs/` and the last): production's `Store` on a copy of the run's database cut
at the ask, and the state the filter builds then. That gives 27 states, each scenario's ask in each
run. 24 windows can be checked against the run's own `jev.log`, and all 24 match; the first run has no
log. Each window holds the scenario's first piece, and for two of the scenarios also one line about
another scenario. P(part), lowest to highest over the 9 runs, and how many said yes:

| Wording | “the cave over there is pitch black” | “my diamond pickaxe just broke” | “I'm going down into that ocean monument” | the other lines (18) |
|---|---|---|---|---|
| first | 0.72–0.79, 9 of 9 | 0.96–0.98, 9 of 9 | 0.77–0.86, 9 of 9 | 0.00, 0 yes |
| g3 (production) | 0.66–0.78, 9 of 9 | 0.85–0.90, 9 of 9 | 0.55–0.70, 9 of 9 | 0.00, 0 yes |
| g4 | 0.68–0.85, 9 of 9 | 0.95–0.98, 9 of 9 | 0.60–0.81, 9 of 9 | 0.00, 0 yes |
| **g5** | **0.91–0.95**, 9 of 9 | **0.97–0.99**, 9 of 9 | **0.82–0.90**, 9 of 9 | 0.00, 0 yes |
| g6 | 0.92–0.96, 9 of 9 | 0.96–0.98, 9 of 9 | 0.80–0.91, 9 of 9 | 0.00, 0 yes |

The orchestrator's own script, asking first and g3 on states built the same way, gave ranges up to
0.10 apart from these (first: cave 0.62–0.77; g3: monument 0.50–0.71, with one no), which puts the
run-to-run noise on these states at about 0.10.

**The playtest.** The same recorded windows as above. *Clear pieces*, *arguable* and *joins I would
not make* are the hand reading of _6_, now a table in `gather_score.py` (`READ`), so any wording is
counted the same way. No wording gathered a line outside it.

| Wording | Half | Yes of asked | Clear pieces taken | Joins I would not make | Arguable joins | Gathered lines outside the ask's input (grouping) | Ask lines reach the brain | Jev ms median / p90 | Tokens |
|---|---|---|---|---|---|---|---|---|---|
| first | first | 1 of 196 | 0 of 3 | 1 | 0 | 1 | 143 of 160 | 216 / 248 | 645,974 |
| g3 | first | 3 of 196 | 2 of 3 | 1 | 0 | 2 | 145 of 160 | 221 / 247 | 651,854 |
| g4 | first | 2 of 196 | 1 of 3 | 1 | 0 | 2 | 144 of 160 | 220 / 252 | 649,698 |
| **g5** | first | 3 of 196 | **2 of 3** | 1 | 0 | 2 | 145 of 160 | 217 / 241 | 656,166 |
| g6 | first | 3 of 196 | 1 of 3 | 2 | 0 | 3 | 144 of 160 | 221 / 247 | 654,206 |
| first | second | 1 of 212 | 1 of 4 | 0 | 0 | 1 | 124 of 138 | 220 / 244 | 710,182 |
| g3 | second | 5 of 212 | 4 of 4 | 1 | 0 | 5 | 124 of 138 | 228 / 255 | 716,542 |
| **g5, held out** | second | 6 of 212 | **4 of 4** | **0** | 2 | 5 | 125 of 138 | 219 / 244 | 721,206 |

- **g4**, g3's sentence on pauses with the first wording's criteria: on the live states it is between
  the first wording and g3 (monument 0.60–0.81), so the sentence on pauses pulls that line down too,
  not only "a remark". On the first half it took 1 of the 3 clear pieces.
- **g5** took the same lines as g3 on the first half: "My castle." … "it built yet." (0.77), "totally
  give us a bunch of cool stuff" (0.50, a coin toss), and the garble "Do." into "me" (0.57). On the
  live states it and g6 are the highest of the five on every piece, within 0.01 of each other, and
  0.00 on every other line.
- **g6**, the spec's order and words, scored as g5 on the live states. On the first half it lost
  "totally give us a bunch of cool stuff" (0.48), and it joined the ask "can we like stay together"
  into a reaction to it (0.53), the first wording's join I would not make.
- **g5 on the held-out half**, asked once, took the 4 clear pieces at 0.56 to 0.72, where g3 had them
  at 0.53 to 0.61. It left out g3's garble join ("But those" into "Fine.", 0.46). It joined two lines I
  read as arguable: "I'm sorry. That claw made Gallic gun destroy everything in the map." into "Did you
  fix Gallic gun killing everyone no matter which way I look?" (0.54), in the ask's own whole-ask
  input, so that split ask arrives whole, and "I think it's a three hundred by three hundred block
  deletion." into the ask to lower it to one hundred by one hundred (0.70).

**On all 843 lines, g5** reaches the brain with 270 of 298 ask lines (the first wording 267, g3 269);
1 of the 50 split ask inputs arrives whole (0 for the others); 20 talk and noise lines go to the brain
(19, 21); $3.03 a session; 9 yes of 408 lines asked; 219 / 243 ms a call. No Respond event's hand-up
changes.

**I would put g5 into production.** It keeps g3's gains on the playtest (the same 2 of 3 clear pieces
on the first half, all 4 held out) and is above the first wording on every live piece (cave 0.91–0.95
against 0.72–0.79, pickaxe 0.97–0.99 against 0.96–0.98, monument 0.82–0.90 against 0.77–0.86), with
the other lines at 0.00. It costs 0.7% more tokens than g3. The change is `GATHER_CRITERIA` in
`jev/prompt.py`; `GATHER_WHY` stays as it is:

```python
GATHER_CRITERIA = {
    "part_of_the_ask": "Part of the ask: a piece of the same sentence, cut off by a pause, or it says what, where, who "
                       "or how for the ask, such as what \"it\", \"there\" or \"one\" in the ask points at.",
    "not_part_of_it": "Not part of the ask: it is about something else, such as another subject, another ask or a "
                      "reaction, and the ask needs nothing from it.",
}
```

## Respond's hand-ups

Counted as `whole_asks_score.py` counts them, from the gate bench's recorded Respond B run on single
lines: an event is handed up when B handed up any of its lines it ran. B was not asked again on the
groups: no OpenRouter calls were made. No gathered line changed an event's hand-up, under either
wording. B hands up 29 of the 114 events D sends to Respond. The events none of whose lines B ran
count for about 1.7 more brain calls, at B's rates on the lines it ran (request 70%, answer_to_ai 15%,
talk_to_ai 17%, context 10%, noise 6%).

## Caveats

- **One run of D.** The routing call was asked once a line. The gather call is near deterministic on
  the same state (the repeat), but D's own run-to-run noise was not measured here. Production D gives
  the same outcome as the benched D2 with the new state (`whole_asks`, recorded) on 686 of the 717
  single-line inputs; 31 differ, 5 of them asks. The state differs between the two (the examples'
  two edits, one rules sentence).
- **The labels and the grouping are Jev's, with hindsight.** "My read" is one reader's, written into
  the table above; every answer behind its 18 rows is in the bench's rows, to read again.
- **The held-out half was not fully blind.** Before writing g2 and g3 I saw the second half's summary
  numbers and production's one second-half join (in an early score of a partial run). I did not read
  the second half's windows, or any answer of g3 there, until g3 was chosen.
- **The brain's use of a group is not measured.** Whether "I'm still pretty sure Galligun doesn't do
  any damage … But my gallic gun doesn't work anymore" leads to a better fix than the ask alone is
  for the live test.
- Respond B's hand-up for an event is inferred from its lines' recorded hand-ups. B saw the lines
  alone, never a group.
- The history is the bench's (the lines and records visible when the line was judged), not
  `store.history`; it is the same rows as `classes.py` and the trees benches use.
- The cost leaves out the few extra tokens a group adds to the brain's or Respond's prompt.
- **The windows are the ones recorded at 215c8fa.** Since then (151041c) the window follows judge
  order. Replayed with the old run's judge times, 11 of the 382 windows would differ, most by one
  line. Every wording here was asked on the same recorded windows, so they compare fairly.
- **Round two is small.** The live states are 9 runs of one script, 3 pieces and 2 other lines; the
  playtest's first half holds 3 clear pieces. g5 was tuned on both and asked once on the held-out
  half; its "totally give us a bunch of cool stuff" is a yes at 0.50.

## Cost and commands

**TypeSafe: $0.4355 in all, 10,368,573 input tokens.** The run on all 843 lines with the gather call:
$0.3186 (the 30-line pilot $0.0077 of it). Candidates on the first half: g2 $0.0272, g3 $0.0274;
g3 on the second half $0.0301. The repeat: $0.0271. The 10-question calls: $0.0045. A check that the
factored `regather` gives the recorded answers (3 calls): $0.0006. A bug in the first `regather` asked
Jev with no questions on 101 empty windows; each was refused (422) with no tokens, and those rows were
removed. **No OpenRouter calls.** About 7,300 input tokens and 220 ms a call.

**Round two: $0.1286, 3,061,866 input tokens.** The live states, 27 calls a wording: first, g3, g4,
g5 and g6, $0.0031 to $0.0032 each ($0.0159). The playtest's first half: g4 $0.0273, g5 $0.0276, g6
$0.0275. g5 on the held-out half: $0.0303.

```sh
(cd brain && uv sync --frozen --inexact)
PY=brain/.venv/bin/python; L=integration/replay; DB=DIR/vfg/vfg.db
ENV=integration/realmc/.server/shared/env   # TYPESAFE_API_KEY, never printed
$PY $L/gather_bench.py run --db $DB --out OUT --limit 30 --env $ENV           # the pilot
$PY $L/gather_bench.py run --db $DB --out OUT --env $ENV                      # all 843 lines
for W in g2 g3; do
  $PY $L/gather_bench.py regather $W --db $DB --out OUT --half first --env $ENV
done
$PY $L/gather_bench.py regather g3 --db $DB --out OUT --half second --env $ENV   # the chosen one, held out
$PY $L/gather_bench.py regather repeat --db $DB --out OUT --half first --env $ENV
$PY $L/gather_bench.py ten --db $DB --out OUT --env $ENV
# round two
RUNS=integration/realmc/.server/jr/vfg   # jev_live.py's transcripts: vfg/runs/* and the last run
$PY $L/gather_bench.py live first g3 g4 g5 g6 --runs $RUNS --out OUT --env $ENV
for W in g4 g5 g6; do
  $PY $L/gather_bench.py regather $W --db $DB --out OUT --half first --env $ENV
done
$PY $L/gather_bench.py regather g5 --db $DB --out OUT --half second --env $ENV   # the chosen one, held out
python3 $L/gather_score.py --rows OUT/prod/rows.jsonl --inputs $L/whole_asks_2026-10-08.jsonl \
    --gate-b GATE_OUT/B/rows.jsonl --alt repeat=OUT/repeat/rows.jsonl --alt g2=OUT/g2/rows.jsonl \
    --alt g3=OUT/g3/rows.jsonl --alt g4=OUT/g4/rows.jsonl --alt g5=OUT/g5/rows.jsonl \
    --alt g6=OUT/g6/rows.jsonl --ten OUT/ten/rows.jsonl --live OUT/live --to OUT/score.md --json OUT/score.json
```
