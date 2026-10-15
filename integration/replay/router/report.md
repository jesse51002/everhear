# The router replay: the report

**Status, 2026-10-13, the last round (router fix 3, router.md sha 3e85e3d292b4), reasoning off:**
- **Met:** "different asks start their own", 10 of 10 in both passes.
- **Not met:** the 2026-10-08 follow-up bar and the night's 7 repeats under the strict reading.
- **Met only under a soft reading:**
  - The 7 repeats: 7 of 7 in pass 1 and 6 of 7 in pass 2, counting `nowhere` (“already done”) for
    a pure repeat. Strict: 5 and 3.
  - #114: `nowhere` in both passes, so there is no second task, but the line never goes to the task.

The runs: the playtest set once and the night set twice, each in full, DeepSeek V4.1 Flash, hosts
together, coreweave, parasail, one call at a time. The first night pass hit its budget two lines
short and was finished with `--resume`. The sections after the diagnosis are fix 3's runs, written
by `score.py --repo`. Everything above them is written by hand, with every earlier round's numbers
from its runs, judged on today's truth and labels. How each set is built and scored: the
docstrings of `integration/router_replay.py` and `score.py`. The data and the runs stay out of
the repo.

## Against the bars, round by round

The rounds, by router.md sha:
- Before: 08647b9c4cd5.
- Fix 1: e81c00c8513d.
- Fix 2: f7c05de3c976.
- Fix 3: 3e85e3d292b4.

Reasoning is off unless the column says low. A night cell with two figures is two passes of the
same prompt. A playtest cell is one run.

| Bar | Before | Fix 1 | Fix 1, low | Fix 2 | **Fix 3** |
|---|---|---|---|---|---|
| **10-08 follow-ups by script**, strict: each task the line's scripts belong to followed up. Bar 87.8% (36 of 41) | 32 of 43 | 30 | 31 | 32 | **31 of 43, 72.1% (+3 partly): not met** |
| … with my hand rulings (mine, not Jesse's: below) | 35 | 32 | 34 | 35 | **33 of 43, 76.7%** |
| … taskcall's any-of rule, no rulings | 34 | 33 | 33 | 34 | **34** |
| … any-of rule with my rulings (bar = 38 of 43) | 37 | 35 | 36 | 38: met | **36 of 43, 83.7%: not met** |
| **10-08 new things** not taken for a follow-up (at most 5%) | 35 of 37 | 36 | 37 | 33 | **34 of 37**: none taken for a follow-up; 2 sent nowhere, 1 call failed |
| **Night: the 7 repeated asks**, strict | 6 of 7 | 5, 5 | **7: met** | 5, 5 | **5, 3 of 7: not met** |
| … counting `nowhere` (“already done”) for a pure repeat | 6 | 5, 6 | 7 | 6, 6 | **7: met, 6** |
| **Night: #114**, KrownJM echoing the blizzard: #113's work, no second task | task | task; nowhere | new_loop | nowhere, nowhere | **nowhere, nowhere** |
| **Night: different asks start their own** | 10 of 10 | 9, 10 | 10 | 10, 10 | **10, 10: met** |
| Night: F/U lines that need nothing glued onto work (no bar) | 10 of 24 | 3, 5 | 3 | 3, 2 | 4, 5 |

**Cost, latency and cache, fix 3:**

| Run | Calls | Cost | Median | p90 | Cached |
|---|---|---|---|---|---|
| Playtest | 79 (1 failed its checks twice) | $0.141 | 1.32 s | 2.64 s | 92.8% |
| Night, pass 1 | 64 | $0.036 | 0.90 s | 1.78 s | 98.1% |
| Night, pass 2 | 64 | $0.033 | 0.92 s | 1.51 s | 98.7% |

- Fix 3 cost $0.222 with its pilot and the 2-call resume, against a budget of $0.25.
- Every model call of this build: about $1.31.
- Off, the router answers in about 0.9 s on the night's 28-35K-token prompts and 1.3-1.6 s on the
  playtest's prompts, which grow to 76K. Fix 1 at low took 3.3-6.4 s at the median, with a p90 of
  about 14 s.

**What fix 3 changed:**
- **Fixed:**
  - New asks are no longer taken for follow-ups of a busy task (#338, #577, #591, #729 in fix 2).
  - #266 goes to the blizzard task in pass 1.
  - #737 is now a fix ask, though it went to `new_loop`.
- **Still wrong:**
  - #577 and #591 now go `nowhere` as “still on your spear bomb”: read as repeats, not new things.
  - **#338's call failed:** the router followed up a task, `sniper_nest_eclipszr`, that is not
    under TASKS, twice.
  - #128 (fire aura repeat) became a second task, `flame_move_krownjm`, in pass 1.
  - #267 went to a new loop in pass 2.
  - #711, “did you fix the Galick Gun…?”, still got a false “Galick Gun is fixed”.

## Verdict across the rounds

**What holds, in every off round and pass:**
- **Different asks start their own.** 10 of 10 in 7 of the 8 night passes, low included; #52 went
  nowhere once (fix 1, pass 1).
- **No duplicate blizzard task off.** #114 goes to #113's task or `nowhere` every time. Only low
  sent it to a new loop.
- **The router is fast and cheap off:** median 0.8-0.9 s on the night and 1.3-1.6 s on the
  playtest, p90 under 4 s. About $0.0005 a night call and $0.002 a playtest call, 92-99% cached.
- **The strict playtest bar is never met.**
  - 30-32 of 43 strict, 33-35 by taskcall's any-of rule, whatever the prompt.
  - Three truth faults (my rulings) and four two-task lines account for most of the gap.

**What is noise.** Two passes of one prompt differ by 1 to 2 lines on every night bar:
- fix 3's strict repeats: 5 and 3;
- glued F/U lines: 2 to 5;
- fix 1's "different asks": 9 and 10.

So no prompt round is measurably better than another on the repeats, on the playtest follow-ups
(30-32 strict across all four rounds) or on the F/U glue after fix 1. The real changes are only
those that moved every pass at once:
- **Fix 1:** lines that ask nothing went `nowhere`. Glued F/U lines fell from 10 to 2-5, and #5
  was fixed.
- **Fix 2 broke, and fix 3 repaired:** gluing new asks onto busy tasks.
- **Fix 3:** pure repeats are answered “already done” `nowhere`. That is why the soft repeats
  reached 7 and 6.

**The best round per bar:**

| Bar | Best |
|---|---|
| Playtest follow-ups, strict | before and fix 2 (32), within noise of the rest |
| Playtest follow-ups, any-of with my rulings | fix 2 (38, the only run at the bar) |
| Playtest new things | fix 1 low (37 of 37); off: fix 1 (36) |
| The 7 repeats, strict | fix 1 low (7 of 7, the only strict pass) |
| The 7 repeats, soft | fix 3 pass 1 (7 of 7) |
| #114 to the task itself | before and fix 1 pass 1 |
| Different asks | every round |
| F/U glue | fix 2 pass 2 (2 of 24) |

**Taken together:**
- Reasoning off, with fix 3's prompt, meets one of the spec's bars as written: different asks.
- It meets the repeats bar and #114 only if `nowhere` (“already done” / “that's Styrka's, coming”)
  counts. That reading meets the spec's aim, one piece of work per ask and no second task, but
  not its letter.
- The playtest bar is not met. On this rebuilt truth it needs Jesse's rulings on the 43, and a
  choice between the strict and the any-of rule, before any prompt can be judged against it.
- Low met the strict repeats once, at 4 to 8 times the latency, and missed #114. It is not a clear
  win for the hold that reasoning off allows.

## My hand rulings on the playtest bar (D1's, not Jesse's)

Three follow-ups were missed in every run. Each, by my reading, is a fault in the script truth,
not in the router. The rulings are mine, made in this build, and need Jesse's check before they
count. `score.py` holds them (`RULINGS`) and shows the score with and without them.

- **#403, KrownJM: “Can you also remove all damage?”** (T92). Truth: `keep_effects_krown`.
  **Ruling: new is right** (`new_loop` or `new_implementer`); the follow-up is accepted too.
  - keep_effects_krown was then "KrownJM's speed effect, lowered to level two". The player asked
    for a new standing rule, not a change to his speed effect.
  - T92's implementer chose to build it inside keep_effects (Resistance V and more): how it was
    built, not what was asked.
  - FOLLOW-UP OR NEW's own test: did the player ask for the existing thing to change? No.
  - The router answered `new_loop` or `new_implementer no_damage_krown` in every run, and Gemini
    missed it the same way (taskcall.md: "a new rule is defensible").
- **#583, KrownJM: “my ice beam is not working anymore. My stash stopped working, fix it.”**
  (T135). Truth: `ice_walk_krown` only. **Ruling: `ice_beam_krown` is right**; ice_walk_krown is
  accepted too, and both would be fuller.
  - T135's result in tasks.jsonl is "rerun ice_walk/ice_beam". The scorer's parser
    (taskcall_score's EDITED) splits scripts on `+` only, so it dropped ice_beam. The truth says the
    line touched ice_walk alone.
  - The line names the ice beam first and plainly. “My stash” is garbled ("steps", by the
    conversation's reading).
  - The router answered `ice_beam_krown` in every run.
- **#619, Eclipszr: “make sure there's no cooldowns.”** (T144). Truth: `spirit_bomb_eclipszr`.
  **Ruling: either `spirit_bomb_eclipszr` or `hollow_purple_eclipszr` is right**; both would be
  fuller. `nowhere` stays a miss.
  - The line names no power.
  - The AI's last line to Eclipszr, 33 s before, was “hollow purple gets a shout title and big
    sounds too”, from an open hollow_purple_eclipszr follow-up.
  - The conversation's own task covered the Spirit Bomb, the Super Saiyan form (a library
    action's, no task) and "confirm hollow_purple has none".
  - The router answered hollow_purple_eclipszr in three of five runs; fix 1 off and fix 3 answered
    `nowhere`.

## The night's 7 repeated asks, and the labels

- From the spec's queue table: #60 and #63 (#58's teleport), #90 (#86's fireball), and #266 and
  #267 (the frost aura).
- From session-2026-10-13.md, section 3, where the spec's "7 lines" figure comes from: #115 onto
  #113, and #147 (U) onto #144.
- #128 and #214 are scored apart, with no bar.
- A line is right when it goes to whatever the replay made of the earlier line: its loop, or the
  task the router gave it.
- Labels set in round 1, and every night run judged on them:
  - **#266 and #267 are one ask with #260 and #263.** #266 answers the AI's “Did you want it
    unbound?”, which came from #260's loop.
  - **#200 and #201** answer “your flame aura or Styrka's ice aura?”, so they are about the
    blizzard.
  - **#217** is about the cooldowns.

## Notes on the data

- **The 2026-10-08 set is rebuilt**, because taskcall.py's inputs are gone.
  - Implementers' conversation tasks come from the route labels.
  - Chains follow `Change to T<x>`, else the earliest implementer's task on the same script.
  - Names are the script plus the player.
  - That gives 43 follow-ups by script (taskcall's: 41) and 37 new things.
- **A line about two tasks** is right when each is followed up, partly when one is. The bar counts
  only the full ones.
- **The night's context is the night's own feed,** so its AI rows show what the night's loops did.

## What is left (fix 3, off), and what would fix it

router.md was not changed: it is the router builder's file. Fix 3 was the last round. These are
for later.
- **Pure repeats go `nowhere`, not to the loop.** That is fine for the player: “already done”, and
  nothing done twice. It needs the bar read softly, or a line in router.md: "a repeat of an ask a
  loop has done goes to that loop", so the loop can answer from its record.
- **#267 and #214 still drift to a new loop or the wrong task in one pass of two.** Both are noise
  at this sample size.
- **#577 and #591** are new asks taken for repeats of a task being built (“still on your spear
  bomb”). The new-ask rule needs its example to show a different power named while another is
  built.
- **#338, a validator loop:** the router named `sniper_nest_eclipszr`, a task that does not exist,
  twice. The core then places the line as a new loop (DESIGN: router LlmError → new_loop), which
  is harmless.
- **#711:** a "did you fix X?" answered “it's fixed”, a claim the router cannot know. The reply
  rule should forbid saying a thing works.
- **Two-task lines: 4 of 5 partly right in every round.** The router names one task. This is the
  biggest single part of the playtest gap.

## Run: the playtest, reasoning off, fix 3

_playtest, deepseek/deepseek-v4.1-flash off_

79 lines; router brain/brain/router.py, prompt 3e85e3d292b4, head 94e517e (brain/ changed); hosts together,coreweave,parasail; started 2026-10-13T07:03:25+00:00.

**78 calls, 1 failed. Cost** $0.1410, a call median $0.00204. **Latency** median 1.32 s, p90 2.64 s, slowest 4.22 s. **Cache** 92.8% of 4,345,677 prompt tokens cached (92.7% after the first call); a prompt grew from 28,049 to 76,295 tokens; completions 237 tokens median. **Choices:** new_loop 29, loop 0, new_implementer 10, implementer 35, nowhere 4.

| | Right | Of | Share | Bar |
|---|---|---|---|---|
| **Follow-ups by script**: `implementer`, following up the task holding each script it edited | 31 | 43 | 72.1% | at least 36 of 41 (87.8%): **NOT met** |
| … partly right: a follow-up on two tasks' scripts, one of them followed up | 3 | 43 | 7.0% | |
| … with D1's hand rulings on #403, #583 and #619 (not Jesse's: report.md) | 33 | 43 | 76.7% | at least 87.8%: **NOT met** |
| **New things by script**: `new_loop` or `new_implementer` (nothing glued onto a task) | 34 | 37 | 91.9% | taskcall's: at most 5% taken for a follow-up: 8.1%, **NOT met** |
| … of them, the ones the labels give an implementer | 2 | 4 | 50.0% | |

Left out of the follow-ups: 16 whose script no task held (the brain's actions made it), 0 whose holder was written after the line was fed, 6 the labels give to the brain's actions.

Lines about two tasks or more (5): right when each is followed up, partly when some are: #255 partly (1 of 2: death_gaze_eclipszr + gatling_gun_eclipszr; got implementer death_gaze_eclipszr); #513 partly (1 of 2: ice_walk_krown + hollow_purple_eclipszr; got implementer ice_walk_krown); #554 partly (1 of 2: ice_beam_krown + ice_walk_krown; got implementer ice_beam_krown); #574 right (2 of 2: ice_beam_krown/ice_walk_krown + ice_walk_krown; got implementer ice_walk_krown); #635 partly (1 of 2: ice_beam_krown/ice_walk_krown + ice_walk_krown; got implementer ice_beam_krown).
New things went to: FAILED 1, new_implementer 9, new_loop 25, nowhere 2.

### Follow-ups missed: 12 (3 of them partly right)

| # | Who | The line | Expected | Got | The router's reason |
|---|---|---|---|---|---|
| 155 | Styrkaeopteryx | did that to me once a while ago. Yeah, Claude, can you remove the dar… | implementer horror_mode_krown (T35: Remove the darkness effects from Styrkaeopteryx) | new_loop | Styrkaeopteryx asks to remove the darkness effects — the horror_mode_krown task's copy is not running (RUNNING NOW lists none), so a stop needs a loop; the 'No' at the end is unclear, so: new_loop |
| 255 | Eclipszr | Sneak and lock into our enemies. Oh no anyways. But it doesn't do eno… | implementer death_gaze_eclipszr + gatling_gun_eclipszr (T63: Change to T60: also raise the damage of Eclipszr’…) | implementer death_gaze_eclipszr (partly) | Eclipszr says his death gaze doesn't do enough damage and wants more; the death_gaze_eclipszr task is open with an implementer on it, so this is a change to that thing; the conversation agent already promised work. so: … |
| 403 | KrownJM | Can you also remove all damage? | implementer keep_effects_krown (T92: Turn off all damage for all online players) | new_implementer new:no_damage_all | Krown asks to remove all damage — a rule the game must keep from now on (a sword hits, a creeper blows, and nobody is hurt), bound to no existing task or running copy; nothing on the menu makes a player untouchable fore… |
| 513 | KrownJM | Okay, this ice thing is still breaking. It's going to piss me off. Al… | implementer hollow_purple_eclipszr (T114: Upgrade Eclipszr’s Hollow Purple as he asked) | implementer ice_walk_krown | Krown says the ice (ground ice, ice_walk_krown's thing) is still breaking and is angry about it: a fix for a task's thing, and no copy of ice_walk is running to edit, so: implementer ice_walk_krown. The second half is p… |
| 554 | KrownJM | Wait, I can't I just I just froze that IC pierced out we got to stop.… | implementer ice_beam_krown + ice_walk_krown (T128: Change to T115 and T124: freeze lava and all liqu…) | implementer ice_beam_krown (partly) | Krown asks his Ice Beam to also freeze lava and any liquid; ice_beam_krown is the task that made it and its implementer is not on it, so: implementer ice_beam_krown |
| 583 | KrownJM | Also, my ice beam is not working anymore. My stash stopped working cl… | implementer ice_walk_krown (T135: Fix ice_beam and ice_walk, both stopped working f…) | implementer ice_beam_krown | KrownJM says his Ice Beam is not working and 'my stash stopped working' — the Ice Beam is ice_beam_krown, an open task with an implementer on it. Fixes to a task's thing go to that task, so: implementer ice_beam_krown. |
| 600 | KrownJM | Look at me! Oh. one shot. Can you make my IFLE do no damage and just … | implementer ice_beam_krown/ice_walk_krown (T137: Change to T129: Ice Beam does no damage, only Slo…) | new_loop | Krown asks to change his rifle — no damage, only slowness and blindness — which is a task's thing (sniper_rifle_eclipszr is Eclipszr's, not his); no task made a rifle for Krown, so: new_loop. |
| 619 | Eclipszr | Awesome called, make sure there's no cooldowns. | implementer spirit_bomb_eclipszr (T144: Remove the cooldowns on Eclipszr’s Spirit Bomb an…) | nowhere  “Already on it — your cooldowns are getting cut.” | Eclipszr asks to remove cooldowns, which points at his Hollow Purple task (cooldown removal was already its follow-up) and his Spirit Bomb, both already under an implementer; the Hollow Purple task is open with an imple… |
| 635 | KrownJM | What the fuck just happened? Oh this is not what I wanted. I didn't w… | implementer ice_beam_krown/ice_walk_krown + ice_walk_krown (T150: Undo the T146 ice floor and fix the Ice Beam not …) | implementer ice_beam_krown (partly) | Krown asks to change the ice behaviour he just had built: no ice everywhere as he walks, and instead an ice path only under what his beam shoots; the ice-beam task ice_beam_krown is open with an implementer already on i… |
| 666 | KrownJM | Can you remove the glow on everyone's wallet? Wait, did you throw up … | implementer keep_effects_krown (T159: Remove the Glowing effect from everyone) | new_loop | Krown asks to remove the glow effect from players; his own keep_effects_krown and the earlier Visibility ask set the permanent Glowing, and no task's implementer is on that thing, so it's new_loop. The rest of the line … |
| 711 | Styrkaeopteryx | Did you fix Gallic gun killing everyone no matter which way I look? | implementer galick_gun_styrka (T161: Fix the Galick Gun hitting everyone regardless of…) | nowhere  “Galick Gun is fixed — it only hits what it passes through.” | Styrkaeopteryx asks about the Galick Gun, a thing the galick_gun_styrka task made and keeps; the line presses on whether its fix landed rather than asking for a change, and the conversation agent promised work nobody wi… |
| 737 | Eclipszr | Claude, it doesn't nuke the world. It doesn't create a bigger crater,… | implementer spirit_bomb_eclipszr (T164: Change to T151: Spirit Bomb crater and damage rad…) | new_loop | Eclipszr complains the nuke's crater is too small and not good enough; the nuke is the ready-made nuke action Krown ran, not a task, and no copy is running now, so a bigger nuke blast is a new value on that action for t… |

### New things glued onto a task (or sent nowhere): 3

| # | Who | The line | Expected | Got | The router's reason |
|---|---|---|---|---|---|
| 338 | Eclipszr | Give my snipy nest a barrier, a barrier that protects it from all har… | new (T84: Protect Eclipszr’s sniper nest tower from damage) | FAILED: deepseek/deepseek-v4.1-flash twice failed to match Route: - the answer: Value e… |  |
| 577 | Eclipszr | You know where a super sent is? Can I make it a super sent? | new (T134: Give Eclipszr a Super Saiyan Blue transformation) | nowhere  “Still working on your spear bomb!” | Eclipszr's follow-up is unclear speech-to-text about the spear bomb already being built (spear_bomb_eclipszr); it adds nothing clear to that ask, so: nowhere |
| 591 | Eclipszr | I said spirit bon, not spirit bon. | new (T136: Replace the Spear Bomb with a Spirit Bomb for Ecl…) | nowhere  “Still on your spear bomb, hang tight!” | Eclipszr says "I said spirit bon, not spirit bon" — a garbled repeat of the spear bomb ask (he said "spear bomb" moments earlier at 07:19:55). A spear bomb task spear_bomb_eclipszr is open with an implementer on it, and… |

## Run: the night, reasoning off, fix 3, pass 1

_night, deepseek/deepseek-v4.1-flash off_

64 lines; router brain/brain/router.py, prompt 3e85e3d292b4, head 94e517e (brain/ changed); hosts together,coreweave,parasail; started 2026-10-13T07:08:15+00:00.

**64 calls, 0 failed. Cost** $0.0363, a call median $0.00053. **Latency** median 0.90 s, p90 1.78 s, slowest 3.67 s. **Cache** 98.1% of 2,024,240 prompt tokens cached (98.1% after the first call); a prompt grew from 28,005 to 34,977 tokens; completions 106 tokens median. **Choices:** new_loop 12, loop 7, new_implementer 3, implementer 18, nowhere 24.

| | Right | Of | Bar |
|---|---|---|---|
| **The 7 repeated asks** (#60, #63, #90, #115, #147, #266, #267) to the loop or task already on them | 5 | 7 | all: **NOT met** |
| … counting `nowhere` (“still on it”) as fine for a pure repeat | 7 | 7 | met |
| **#114**, KrownJM repeating the blizzard, to #113's work | 0 | 1 | **NOT met** (nowhere: the soft reading meets it) |
| **Different asks start their own** (T lines that are asks of their own): `new_loop` or `new_implementer` | 10 | 10 | all: **met** |
| T lines about work already under way (follow-ups, answers to the AI, #128 and #214) | 11 (+1 nowhere) | 15 | no bar |
| T lines that are talk (#5): nowhere | 1 (+0 new loop) | 1 | no bar |
| **F and U lines**, apart: as labelled | 20 | 30 | no bar |

F and U lines that need nothing (24): 17 nowhere, 3 a new loop (harmless: a loop that answers), 4 glued onto a loop or a task. The other 6 F/U lines are about work under way: 3 right.

### Misses on the T lines and the bars: 6

| # | T/F/U | Who | The line | Expected | Got | The router's reason |
|---|---|---|---|---|---|---|
| 60 | T | KrownJM | But Can you tell for me to Stirka, teleport me to Stirkosau… | the ask's work: L8 (or nowhere) | nowhere  “Already sent you to Styrkaeopteryx!” (nowhere) | KrownJM repeats the teleport-to-Styrkaeopteryx ask that L8 just carried out; the line adds nothing to it, so: nowhere. The conversation agent's 'On it!' promises work nobody will now do, so a reply goes out. |
| 87 | T | KrownJM | That's not what I said can you | the ask's work: L9 | loop L10 | Krown's line is a cut-off correction of the fireball-ask loop L10 is already on, and it says nothing new to act on, so: loop L10. |
| 114 | F | KrownJM | blizzard sets of freeze. | the ask's work: blizzard_styrkaeopteryx (or nowhere) | nowhere  “Ha, that one's Styrkae's blizzard — it's coming!” (nowhere) | KrownJM says “blizzard sets of freeze,” a garbled echo of Styrkaeopteryx's blizzard ask; it adds nothing new, and a task (blizzard_styrkaeopteryx) with an implementer on it holds that thing, so: nowhere. |
| 128 | T | KrownJM | Can you give me a fire or a move called Flame? It can that … | the ask's work: fire_aura_krownjm (or nowhere) | new_implementer new:flame_move_krownjm | KrownJM asks for a new fire/flame move that burns anyone who walks into it — no task made that; fire_aura_krownjm is a different thing (an always-on aura bound to 'burn'), so this is a new thing. So: new_implementer fla… |
| 214 | T | KrownJM | I do freeze. Can you remove the cooldown on all the buildin… | the work on it (L11) or new | loop L12 | Krown asks two things: freeze (a question about his own freeze aura's behavior) and removing cooldown on "all the buildings"; freeze is under blizzard_styrkaeopteryx's task and Krown's own freeze copies exist while L12 … |
| 267 | T | Styrkaeopteryx | I should not have freeze. | the ask's work: blizzard_styrkaeopteryx (or nowhere) | nowhere  “Freeze is off your book, Styrka, nothing to cast.” (nowhere) | Styrkaeopteryx restates that freeze should not be hers, which adds nothing to the blizzard task already unbound/checked; it only presses the ask, so: nowhere. |

### F and U lines placed against their label: 6

| # | T/F/U | Who | The line | Expected | Got | The router's reason |
|---|---|---|---|---|---|---|
| 38 | F | Styrkaeopteryx | me food. | nowhere (talk) | loop L4 | Styrkaeopteryx asks again for food, the same thing loop L4 just did and can still take a line; RUNNING NOW lists none, so: loop L4 |
| 139 | F | KrownJM | Oh, that happens on me too. That's not supposed to never bu… | nowhere (talk) | implementer blizzard_styrkaeopteryx | KrownJM wonders whether the ice aura hits him too and whether it damages — a question about a task's thing; only the thing's own task can change or explain it, and no copy runs to… |
| 156 | F | KrownJM | This just last forever. … You gotta tell it to remove it. | nowhere (talk) | implementer blizzard_styrkaeopteryx | KrownJM is telling someone (Styrkaeopteryx) to ask the AI to remove the freeze blizzard that lasts forever — the blizzard_styrkaeopteryx task's thing, whose implementer is on it; … |
| 177 | F | KrownJM | I think it might have not cooled down in terms of | nowhere (talk) | loop L11 | KrownJM's line is an unfinished thought about a cooldown, most likely the fireball cooldown he just asked L11 to remove (fireball is his, a fireball_cast incantation, not a task's… |
| 197 | U | KrownJM | I think it's only doing damage to Moms. … And it doesn't. …… | the work on it (blizzard_styrkaeopteryx) or new | implementer flame_move_krownjm | Krown is reporting that the flame aura does no damage to players and that 'flame' still won't turn off — both are changes to the thing flame_move_krownjm has; no copy of it runs, … |
| 260 | U | Styrkaeopteryx | Guild yourself Frost Or is unbound to me | the work on it (blizzard_styrkaeopteryx, flame_move_krownjm) or new | nowhere  “Yeah, freeze is unbound for you, Styrka.” | Styrkaeopteryx is remarking to the others that their freeze incantation is unbound to them, not asking for anything; RUNNING NOW lists none, so: nowhere |

## Run: the night, reasoning off, fix 3, pass 2

_night, deepseek/deepseek-v4.1-flash off_

64 lines; router brain/brain/router.py, prompt 3e85e3d292b4, head 94e517e (brain/ changed); hosts together,coreweave,parasail; started 2026-10-13T07:06:38+00:00.

**64 calls, 0 failed. Cost** $0.0332, a call median $0.00050. **Latency** median 0.92 s, p90 1.51 s, slowest 3.12 s. **Cache** 98.7% of 2,020,355 prompt tokens cached (98.7% after the first call); a prompt grew from 28,005 to 34,888 tokens; completions 112 tokens median. **Choices:** new_loop 12, loop 3, new_implementer 2, implementer 20, nowhere 27.

| | Right | Of | Bar |
|---|---|---|---|
| **The 7 repeated asks** (#60, #63, #90, #115, #147, #266, #267) to the loop or task already on them | 3 | 7 | all: **NOT met** |
| … counting `nowhere` (“still on it”) as fine for a pure repeat | 6 | 7 | not met |
| **#114**, KrownJM repeating the blizzard, to #113's work | 0 | 1 | **NOT met** (nowhere: the soft reading meets it) |
| **Different asks start their own** (T lines that are asks of their own): `new_loop` or `new_implementer` | 10 | 10 | all: **met** |
| T lines about work already under way (follow-ups, answers to the AI, #128 and #214) | 11 (+1 nowhere) | 15 | no bar |
| T lines that are talk (#5): nowhere | 1 (+0 new loop) | 1 | no bar |
| **F and U lines**, apart: as labelled | 22 | 30 | no bar |

F and U lines that need nothing (24): 19 nowhere, 0 a new loop (harmless: a loop that answers), 5 glued onto a loop or a task. The other 6 F/U lines are about work under way: 3 right.

### Misses on the T lines and the bars: 8

| # | T/F/U | Who | The line | Expected | Got | The router's reason |
|---|---|---|---|---|---|---|
| 60 | T | KrownJM | But Can you tell for me to Stirka, teleport me to Stirkosau… | the ask's work: L7 (or nowhere) | nowhere  “Already sent you to Styrkaeopteryx!” (nowhere) | KrownJM is asking again to be teleported to Styrkaeopteryx; L7 already did exactly that a moment ago. The line adds nothing new, so: nowhere. |
| 63 | T | KrownJM | Can you teleport me to Stirka? | the ask's work: L7 (or nowhere) | nowhere  “Already sent you to Styrka — you're right there!” (nowhere) | KrownJM asks the same teleport to Styrkaeopteryx that L7 just carried out and already reported done; the ask repeats what is already complete, so: nowhere. |
| 87 | T | KrownJM | That's not what I said can you | the ask's work: L8 | loop L9 | Line #87 is KrownJM's correction to his just-prior ask about a fireball incantation, which loop L9 is still thinking on; the line adds to that ask, so: loop L9 |
| 90 | T | KrownJM | Can you turn fireball? Can you make a fireball spell that i… | the ask's work: L9 (or nowhere) | nowhere  “Already bound! Just say 'fireball' and it flies.” (nowhere) | KrownJM repeats the same ask L9 just fulfilled — a fireball spell bound to 'fireball' — and L9 is still open to lines; the conversation agent's 'On it!' promises work nobody will now do, so: nowhere. |
| 114 | F | KrownJM | blizzard sets of freeze. | the ask's work: blizzard_styrkaeopteryx (or nowhere) | nowhere  “That blizzard's for Styrkaeopteryx, Krown!” (nowhere) | KrownJM's line is a misheard fragment of Styrkaeopteryx's blizzard ask, said at the same moment while that task's implementer is already building it; it adds nothing, so: nowhere. |
| 203 | T | Styrkaeopteryx | Just unbinded freeze. Actually, that's probably better. | the work on it (blizzard_styrkaeopteryx, fire_aura_krown) or new | nowhere  “Ha, you got it then! Just say the word if you want it back.” | Styrkaeopteryx says they just unbound freeze, a remark, and the conversation agent's "On it!" promises an unbind nobody will do; the freeze thing is a task's incantation with an implementer on it, so the line asks for n… |
| 214 | T | KrownJM | I do freeze. Can you remove the cooldown on all the buildin… | the work on it (L10) or new | implementer blizzard_styrkaeopteryx | KrownJM asks to remove the cooldown on his freeze incantation; the thing is his blizzard spell, which is already being worked on by an implementer (blizzard_styrkaeopteryx), so: implementer blizzard_styrkaeopteryx |
| 267 | T | Styrkaeopteryx | I should not have freeze. | the ask's work: blizzard_styrkaeopteryx (or nowhere) | new_loop | The line asks to no longer have the freeze incantation, and a task (blizzard_styrkaeopteryx) and a running copy exist; the player has that phrase bound, so a loop should unfollow the thing the line asks about. RUNNING N… |

### F and U lines placed against their label: 8

| # | T/F/U | Who | The line | Expected | Got | The router's reason |
|---|---|---|---|---|---|---|
| 8 | F | KrownJM | Bug for some reason. Not bugged, what are the model calls o… | the ask's work: L1 (or nowhere) | new_loop | KrownJM's line is a rambling question about how many incantations he has (and about last time's incantations), which a look at the game answers; L1 already handled the incantation… |
| 38 | F | Styrkaeopteryx | me food. | nowhere (talk) | loop L5 | Styrkaeopteryx is asking for food again, the same ask L5 already handled with 64 steak; it repeats the ask, so: loop L5 |
| 56 | F | Styrkaeopteryx | Just dry everything. | nowhere (talk) | loop L6 | Styrkaeopteryx's line is garbled speech-to-text of the same 'destroy everything' incantation remark KrownJM just made; L6 is on that same thing and it can take a line. So: loop L6 |
| 141 | F | KrownJM | Oh, it is not average. … Hello, it doesn't damage. | nowhere (talk) | implementer blizzard_styrkaeopteryx | KrownJM reports the ice aura doesn't damage — a fix to the blizzard_styrkaeopteryx thing, which is done with no implementer on it and no copy RUNNING NOW. So: implementer blizzard… |
| 156 | F | KrownJM | This just last forever. … You gotta tell it to remove it. | nowhere (talk) | implementer blizzard_styrkaeopteryx | KrownJM's line was about the blizzard freezing aura lasting forever, and he already rephrased it as a request to have it removed; the blizzard_styrkaeopteryx task holds that thing… |
| 197 | U | KrownJM | I think it's only doing damage to Moms. … And it doesn't. …… | the work on it (blizzard_styrkaeopteryx) or new | implementer fire_aura_krown | Krown reports the flame aura (fire_aura_krown) does no damage and won't turn off — a change to a task's thing, and no copy is listed under RUNNING NOW, so: implementer fire_aura_k… |
| 213 | F | KrownJM | Okay, does our freeze? | nowhere (talk) | implementer blizzard_styrkaeopteryx | KrownJM's line is a fragment asking whether the ice aura damages players, a change to the blizzard_styrkaeopteryx task's thing (which an implementer is already on), so: implemente… |
| 260 | U | Styrkaeopteryx | Guild yourself Frost Or is unbound to me | the work on it (blizzard_styrkaeopteryx, fire_aura_krown) or new | nowhere  “Yep, freeze is unbound for you — say the word if you want i…” | Styrka is remarking/confirming that freeze is unbound to him, which asks for nothing; no loop is running and no change is requested, but the agent's "On it!" promises work nobody … |