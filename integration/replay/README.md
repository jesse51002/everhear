# replay — the playtest of 2026-10-08, again

The playtest of 2026-10-08 (2.5 h, three players: KrownJM, Eclipszr, Styrkaeopteryx) replayed
against the parts that replace what ran then: its lines through Jev's routing and Respond, offline,
model calls only, each bench scored against what the conversation agent did with each line or
against hand-checked labels; and the 2026-10-11 session through the brain's loop on realmc
(`../loop_replay.py`). The brain's menu in every bench is today's, the game engine's ops
(`actions/engine_*.py`), so Jev's menu and Respond's prompt are what they are live.

The replay of every direct ask through Prototype_Brain's one call (`lines.py`, `lines_score.py`) is
gone with that call: the brain's loop replaced it, and `../loop_replay.py` is the loop's replay. The
live replay of the playtest's 172 tasks is not here: `tasks.jsonl` is its data, for a replay
written for the brain.

| File | Is |
|---|---|
| `playtest.py` | the playtest's data as the brain would have had it: the context row by row in the order it would have arrived, the world, the open tasks, the batch timer, and what the conversation did with each line |
| `classes.py`, `classes_score.py` | Jev's four classes on every line, against TypeSafe, then Respond on every line Jev classes `conversational` |
| `gate_bench.py`, `gate_score.py`, `gate_set.jsonl`, `gate_prompts/` | the gate bench: six ways to decide whether a line needs the brain or is talk (Respond as it was, Respond deciding first on DeepSeek and on Gemini, the Jev council in one call and in four), every one on the same fixed set of 592 lines; results and the commands in `gate_bench.md` |
| `tree_bench.py`, `tree_score.py`, `tree_questions.py` | the tree bench: Jev routing each line by a decision tree of class questions (who it is said to; worth keeping; what the speaker wants), all asked in one Jev call, with and without a council of three questions a level, against Jev today on all 843 lines; results and the commands in `tree_bench.md` |
| `trees_bench.py`, `trees_score.py`, `trees_questions.py` | the trees bench: five genuinely different ways to build that tree (who then what; kind first; a cheapest-first ladder; consequence first with two keys; observable signals with the tree in code), each one Jev call a line, against Jev today on all 843 lines; results and the commands in `trees_bench.md` |
| `labels_build.py`, `labels_score.py`, `labels_2026-10-08.jsonl`, `labels_check.jsonl`, `labels_review.md`, `labels_score.md` | the hindsight labels: Jev labels each of the 843 lines seeing what the AI said and did in the 90 s after it (five labels, three questions in one call, combined by written rules, checked by hand), and Jev today and the five trees rescored against them from their recorded answers; results and the commands in `labels.md` |
| `whole_asks_build.py`, `whole_asks_2026-10-08.jsonl`, `whole_asks_check.jsonl`, `whole_asks_review.md`, `state_v2.py`, `whole_asks_bench.py`, `whole_asks_score.py`, `whole_asks_score.md` | whole asks: the lines of one ask a player split across utterances merged into one input (Jev with hindsight judges each ask's same-speaker neighbours, checked by hand), then Jev today and the five trees routed on them with Jev's old state and its new one (neutral rules, one set of definitions, a lookout, examples); results and the commands in `whole_asks.md` |
| `state_v1.py`, `lookout_v1.md` | Jev's state before the Jev routing (its rules, four classes, the re-ask's questions, the brain's old lookout), verbatim, so the benches above still build the state they measured |
| `gather_bench.py`, `gather_score.py` | the Jev routing as it ships: Jev's production filter line by line on all 843 lines, D's two questions and the gather call, with the gather call's windows from production's own query; D with and without the gather call on the clean labels, and the gather call's wordings; results and the commands in `gather_bench.md` |
| `respond_bench.py`, `respond_score.py`, `respond_prompts/` | Respond's prompt before and after its rewrite, and its code guard against promises and offers, on the whole-ask inputs D sends Respond and an invented set (its world the game engine's: what it runs, as `vfg engine overview` gives it, and named tasks); results and the commands in `respond_bench.md` |
| `tasks.jsonl` | the playtest's 172 implementer tasks as the conversation wrote them, each with what it was and the run's figures for it: below, _tasks.jsonl_ |
| `loop/`, `../loop_replay.py` | the 2026-10-11 session through the brain's loop, live on realmc (Prototype_Brain_Loop's _Test_): below, _The loop replay_ |

## The data

Read, never written:

- **The playtest's database**, the box's `vfg.db` taken after it: `tar xzf
  ~/Documents/MinecraftServers/aws-backup/vfg-backup-2026-10-08-playtest.tgz -C DIR vfg/vfg.db`
  (extract that file alone: the archive holds the box's `.env` too). Its classes are the old
  names: `direct_ask` is `action_oriented` now, `not_useful` is `not_relevant`.
- **The tasks the conversation wrote**: `~/.vfg/orch/inbox.jsonl`, those before 2026-10-09; each
  task's context names the LINE numbers it came from (`#812`).
- **The route labels**: `~/.vfg/dispatch/eval/selector-actions-2026-10-10.jsonl`, on the brain's
  action menu, and `selector.jsonl`, on the menu of 2026-10-09, by task id.
- **The command references**: `~/.vfg/brain/refs/26.3/`, else `~/.vfg/dispatch/refs/26.3/`.

The context (`playtest.py`'s docstring has it all): every kept LINE when Jev fed it, GAME and SYS
rows, the AI's chat as `AI say` rows, each task the conversation wrote as an `AI task` row (`T7:
…`) and each ORCH done or failed as an `AI result` row (`T7 done: …`), so the brain sees task ids
as it would live. It only grows, in arrival order, so the prompt cache sees what it sees live. The
world says who is online and that positions, items, the time and the weather are unknown; nothing
runs (RUNNING NOW: none), and no place is saved. A call's NEW rows are what no read
took in: each line's call is a live read, and the batch timer (60 s with no read and something
unread) takes in the rest.

## Run it

The brain's own environment, synced once: `(cd brain && uv sync --frozen --inexact)`. The brain's
settings are its own (`brain/brain/config.py`: the environment, then `~/.vfg/config`), so the
model, the reasoning and `VFG_OWNER` are what a live brain on this PC would use. The OpenRouter
key as `structured()` reads it; the TypeSafe key as `TYPESAFE_API_KEY`, else from `--env FILE`
(default: realmc's stand-in box's `integration/realmc/.server/shared/env`, here or in the
`script-library` worktree), never printed. Every model call is a row of the one usage log, as a
live call is (`docs/CONTRACTS.md` §4): the component of what it replays (`brain` or `respond`)
and a call_id that starts `replay `, queued in the run's own
OUT (`gate_bench.py`: OUT/X), never in `$VFG_HOME`, whose queue the brain's next `vfg usage
flush` sends into the live usage table. Each replay sets `VFG_USAGE_QUEUE`, the folder of the
queue (`playtest.queue_usage()`), to OUT unless it is set already; `VFG_HOME` stays, since it
finds the OpenRouter key. A replay's rows reach the box only when sent on purpose:
`VFG_USAGE_QUEUE=OUT vfg usage flush`.

```sh
PY=brain/.venv/bin/python; DB=DIR/vfg/vfg.db
$PY integration/replay/classes.py jev --db $DB --out OUT2                 # 843 lines, one TypeSafe call each
$PY integration/replay/classes.py respond --db $DB --out OUT2             # every line now conversational
python3 integration/replay/classes_score.py OUT2                          # OUT2/classes.md, classes.json
```

A run into an OUT that has rows goes on after the last one. `classes.py respond --budget` (1
dollar) stops a run that spends more.

On 2026-10-10 the Jev step took 3.5 minutes and $0.27, and the Respond step 9 minutes and $1.01 for
the first 322 of the 382 lines Jev classed `conversational`, where its $1 budget stopped it: all of
them would be about $1.2.

### What it leaves in OUT

| Path | Is |
|---|---|
| `usage.pending.jsonl`, `usage.pending.lock` | every model call's usage row, queued and not sent (`VFG_USAGE_QUEUE=OUT vfg usage flush` sends them); `classes.py respond` and `gate_bench.py` leave them |
| `jev.jsonl`, `jev-meta.json`, `menu.md` | per line: Jev's class then and now, its confidence, what the conversation did, and what fit in the state (the menu, the history, the AI records); a `flip` row when a `not_relevant` line asked about again flipped |
| `respond.jsonl`, `respond-meta.json` | per conversational line: hand-up or reply as the model gave them and after Respond's code, the reason, ms, cost, tokens |

## How it is scored

The scorers' docstrings have every definition. In short: a line **became a task** when a task's
context names it (playtest.py). For Jev, a line was **answered in chat** when an AI chat
line followed within 60 s with no task and no result between: a heuristic, so `chat` holds
declined asks as well as talk. `labels_2026-10-08.jsonl` replaces these labels for routing: it finds 298
asks where they find 187 (`labels.md`).

## The loop replay: 2026-10-11 through the brain's loop

Prototype_Brain_Loop's first Test. The 110 brain calls of the 2026-10-11 session (one player,
KrownJM, 05:40–06:59 UTC, the brain before Named Tasks, $1.18 on GPT-6.1 Sol) — 21 talk, 16 action
asks, 32 hand-offs and 41 implementer reports — each run again as one loop of today's brain, in
order, in one process, on a realmc server of its own.

- **The data**, in `loop/`, made once by `loop/build.py` (its docstring has every field): the
  session's feed, each call with the feed it was answered in and what it cost then
  (`events.jsonl`), what that night's implementers did to the world and when (`world.jsonl`, and
  the script actions they made, in `loop/made/`, as a record), and `session.json` (the counts
  against the spec's, the six
  tasks and their 35 follow-ups, the five misread asks, KrownJM's kit). It reads only `vfg/vfg.db`
  of `~/Documents/MinecraftServers/aws-backup/vfg-backup-2026-10-11.tgz` (never its `.env`), that
  night's `~/.vfg/brain/brain.log` and `tasks.json`, and the implementers' transcripts.
- **The run**, `integration/loop_replay.py` (its docstring has the rest): the brain's classes as
  `brain/__main__.py` wires them, the real `vfg` to the server and real model calls, the game
  engine on the server's stand-in with its library (integration/brain_live.py's `start_engine`),
  every action one of the engine's ops; the implementers record each brief and follow-up and start
  nothing. Each loop reads the night's own feed up to its call, so the prompt cache sees what it
  saw then; the night's T<n> task numbers read as today's names. What the night's implementers did
  to the world (`world.jsonl`: four script actions saved, incantations stored in the store before
  the engine's) is the old system's, which the engine does not run: each change is recorded as not
  done at its time, so the reports and follow-ups about those things meet a world without them. Its
  usage rows stay in OUT.

```sh
R=…/integration/realmc/.server     # a REALMC_ROOT with the jar, the template and Carpet
brain/.venv/bin/python integration/loop_replay.py --dry                       # no model, no server
REALMC_ROOT=$R brain/.venv/bin/python integration/loop_replay.py --only 1-12 --budget 0.3   # a pilot
REALMC_ROOT=$R brain/.venv/bin/python integration/loop_replay.py              # all 110, budget $3
python3 integration/loop_replay.py --report OUT                               # the report again
```

OUT (default the temp folder, `vfg-loop-replay-<time>`) gets `results.jsonl` (per event: each
step's cost, tokens, cached share and ms, the time to the reply, how it ended, what ran, each brief
and follow-up, any answer the Step's checks sent back, with its errors), `usage.pending.jsonl`,
`brain.log`, `meta.json` and `report.md`: the cost beside the $1.18, the p90 time to the reply of
the asks that do not hand off (bar 30 s), the cached share of the steps after the first (bar 85%),
one step against two or more, each brief's parts, and the five misread asks' done-when lists. A
whole run also writes `loop/report.md`.

## tasks.jsonl

The 172 implementer tasks the conversation wrote on 2026-10-08, the data for a replay of them
through the brain, which is not written yet. One line per task of the run, in order: `id`, `at`,
`task` and `context` as the conversation wrote them, with every player's name, in any spelling,
replaced by Steve (the owner), Alex or Sam, the same player always by the same name; `class`, from
the analysis behind the spec's figures:

| Class | Tasks | Is |
|---|---|---|
| `value` | 69 | a variation of an earlier ask that changed only a value or a target (the spec's 68, and T33, which a restart lost) |
| `new` | 32 | needed something no script did, in the run |
| `plain` | 43 | a plain command or a stop |
| `fix` | 19 | something reported done did not work |
| `repeat` | 9 | the same thing asked again |

and `run`: the run's figures for it — `send_to_done_s`, `words_to_done_s` (from the player's
words), `kind` (what the run did: a new script written, a script edited, commands), `result`, and
each implementer job: model, time, calls, calls before the first change, seconds per kind of call,
turns, whether it hit the turn cap, cost; and the task's implementer and orchestrator cost.
Two rows also carry `either`, a hand ruling with why: T59 and T115 are right as new tasks or as
follow-ups.
