# integration — the parts together on one PC

The real bridge, Jev filter and `vfg` toolkit, plus the client mod's HTTP client and the brain's
own reader of the feed (`brain/brain/context.py`), run together in a temporary folder that stands
in for the box's `/opt/vfg` and `/opt/mc`. No EC2, no Minecraft, no Jev account, no model.

Stand-ins: Minecraft (console lines appended by the harness, in the shapes of
`bridge/tests/fixtures/console-26.3.log`, and `toolkit/tests/fake_rcon.py`, in the harness's
process), SSH (`toolkit/tests/fake_ssh`, which runs the remote command locally), systemd
(`toolkit/tests/bin/`), Jev's API (`jev/tests/fake_jev.py`) and the brain (the harness makes the
brain's `vfg` calls itself, as the brain makes them). `HOME` and `VFG_HOME` point into the temp
folder and every port is on 127.0.0.1, so nothing touches `~/.vfg`, `~/.ssh` or any remote host.

For the brain itself, end to end on this PC with real model calls: [`brain_e2e.py`](#the-brain-end-to-end).
For a real Minecraft 26.3 server instead of the fake one, with the same kind of stand-in box
around it, several side by side and Carpet's fake players: [realmc/](realmc/README.md), the
brain live on it, [`brain_live.py`](#the-brain-live-on-realmc), and Jev's gather call live on it
with the real TypeSafe API, [`jev_live.py`](#jevs-gather-call-live-on-realmc).

## Run it

```sh
integration/run.sh            # PASS or FAIL per check and per path; non-zero on any failure
integration/run.sh --keep     # keep the temp folder and its logs
```

About a minute. The box's side (bridge, Jev filter, the toolkit's remote programs) runs on Python
3.12, the box's `/opt/vfg/python`: `run.sh` finds one with `uv python find 3.12`, else uses
`python3` and says so. Path 8 needs a JDK and the mod's compiled classes (`cd client-mod &&
./gradlew compileJava` first).

## The paths

| # | Checks |
|---|---|
| 0 | setup as the brain does it (§12): `vfg up`, the brain's watchers, its lookout, Jev's menu (`vfg jev menu set` with `vfg action list`'s lines), `vfg context --json`, then `vfg watch --after` its last seq, which keeps no cursor |
| 1 | `POST /v1/lines` → a `lines` row → Jev → one `LINE#seq` in `vfg watch`, unprocessed |
| 2 | typed chat in Jev's three classes and watched game events reach `vfg watch`; `not_relevant` is stored, not fed; a `to_ai` line flushes the waiting context at once |
| 3 | `vfg say` writes an `ai` line Jev never judges and an `AI say` feed row in the same call, `AI say castle_steve` for `--task castle_steve`; Jev's next state holds the AI's lines, the brain's `action` (a loop's one record), `task` and `result` rows, a task's named in front of its text, and not an older brain's notes, the lookout and `menu.md` |
| 4 | `vfg history` by date, source, player, each of the three classes (the old names refused), events and feed |
| 5 | `vfg feed post AI` for each kind, its field a task's name or `-`, each row written processed; `GAME` of any kind, `SYS`; `ORCH`, a task number and bad `AI` fields refused; `vfg context`, `--json` (the rows, then `{tasks}`, then `{last_seq}`) and `vfg context mark` on the rows the brain reads |
| 6 | `usage` rows: one per Jev call; `vfg usage add` writes them, cached tokens included: a step of the brain's loop (`#seq s1`, `#seq s2`, a report's `<task> s1`), Respond's (`#seq`), an implementer's (its task's name), and an older brain's `orchestrator` row, which the table still takes; `structured()`'s queued rows (the brain's steps, an older brain's `planner` row, an alias call's), a failed step's among them (its cost NULL when an attempt was cut off in flight), sent by `vfg usage flush` with `ok`, `error` and `ms` |
| 7 | killing and restarting Jev, the brain's watch (`--after` its last seq) and the bridge loses and repeats nothing, and keeps the processed marks; an ask killed while its group waits for the feed is fed once, alone, and one killed after its group's feed write is not fed again |
| 8 | the client mod's real `BridgeClient` and `LinePayload` against the real bridge |
| 9 | a real group (§5): Steve's earlier line goes with his ask as one `LINE`, under the ask's fields and time, and Alex's line said between them is never asked about; every line the real feed writer produced, the group included, read by the brain's `context.parse`, and `vfg context --json` built into the brain's `Context` |
| 10 | the `tasks` table the bridge makes in `vfg.db`: `vfg task set` as the brain writes a task (the whole row, an update, a write older than the stored one kept out, a write of some keys, from stdin) and `vfg task list` (plain, `--open`, `--name`, `--json`); the tasks in `vfg context --json` and `vfg brain status`; a library action's name, `cast`, a name that is not a task's, a key that is not a column, a bad status or `made`, a new task with no description and bad JSON refused, nothing written |

## Incantations, on a real server

`integration/incantations_e2e.py` is the voice-triggers spec's live test, on realmc: a server
with a fake player Steve, the real bridge against its stand-in box, and two incantations made with
`vfg action run incantation_create` ("fire ball", a scoreboard point; "ice wall", a teleport whose
player is whoever casts it). Casts and lines are posted as the mod posts them, and each check is
checked in the world or in `vfg.db`: the list and its long poll, a cast to its effect, the
duplicate window, the incantation's cooldown and the 1 s between a player's casts, another
player's phrase, the drop rule, a caster field, Layer 2 from a transcript, and the events and
feed lines. `--speech` also has the mod's `castCheck` say "Fire ball!" (Kokoro) through its real
speech pipeline and spotter against that bridge, and times the end of the phrase to the effect.

```sh
REALMC_ROOT=<a realmc .server with the jar> integration/incantations_e2e.py [--name vt] [--keep] [--speech]
```

About 25 s, most of it the server's start and stop; `--speech` adds about 15 s, the mod's
models loading. On 2026-10-10: a posted cast's effect landed 31–46 ms after the post, a
transcript cast's 39–57 ms after its line, and "Fire ball!" spoken 0.53 s after the phrase ended.

## Implementers on DeepSeek, on realmc

Prototype_Implementer_Harness's tests, each script's docstring saying how to run it. All but
`impl_briefs.py`, `impl_data.py`, `impl_cache.py` and `impl_game_tests.py` call models, through OpenRouter, with
`OPEN_ROUTER_API` read from `Internal/.env` into the process alone; none is part of any test suite.
**Their data and outputs are outside the repository**, in `$VFG_IMPL_REPLAY` (default
`~/.vfg/impl-replay/`), since they hold answers an implementer must not read: what is there is in
[replay/impl/README.md](replay/impl/README.md).

- `impl_data.py` rebuilds the replay's data, `$VFG_IMPL_REPLAY/tasks.json`: the six implementer
  tasks of 2026-10-11 as they happened, 20 of 2026-10-08, the ten wild asks and one that needs `op`.
- `impl_briefs_write.py` writes each ask's brief once, in Brain_Loop's shapes, a stand-in for the
  brain's own, and keeps it in `tasks.json`.
- `impl_briefs.py`, Test 1: every system prompt and brief rebuilt offline with no implementer
  model, each part's size logged; passes at most 3 router scripts and 7 actions in full a brief,
  and T212's first request under 110K tokens.
- `impl_check.py`: Hot fireball through the real runner and the Claude Code adapter, its first ask
  and a follow-up that resumes its session, and an `op` the guard refuses.
- `impl_check_opencode.py`: the same through the OpenCode adapter, with what OpenRouter billed for
  each request, recorded by a pass-through on 127.0.0.1.
- `impl_messages.py`, Tests 3 and 4: two messages into one run, one sent as it reports, the three
  parts of one line making one run, and a cancel of a follow-up and of a new task, each put back.
  `impl_messages_check.py` is the earlier check of the same, with code's put-back alone and a run
  warned at its limit.
- `impl_replay.py`, Test 2: tasks replayed as they happened, each follow-up at its recorded moment,
  each task on a fresh server (`up --fresh`, what the last one made set aside), on either adapter
  (`--harness opencode`). Each server is paired with a test server, `<name>-test` (`--test`), a
  realmc server of its own made fresh with it, where the implementers' `vfg test` runs
  (`VFG_TEST_BOX` its config), checked first with one library test (`--preflight railgun`); `--judge` scores each task with `impl_judge.py`, which casts its action twice,
  each from fresh state on a stage of the test templates' geometry, and has a judge model mark its
  DONE WHEN items: done only when both casts show it (flaky when one does), the judge's own probes
  winning over the implementer's words; a research task, or a scenario not buildable, is scored
  apart, outside the total (`--night`: what Haiku made on 2026-10-11).
- `impl_scenarios.py` derives one scenario per replayed task, once, into
  `$VFG_IMPL_REPLAY/scenarios.json` for a person to review: the player, his opening line, his goal,
  his hidden wants in order, the setup, the DONE WHEN list and how he tries it.
- `impl_game.py`, the game eval: each scenario played from nothing on a fresh live server and its
  test server: each turn the stand-in brain writes the brief, the real runner builds it, the player
  tries it on the live server and a simulated player says his next line (a complaint, his next
  want, or done); then `impl_judge.py` marks the whole checklist. `--dry` spends nothing.
- `impl_game_tests.py` reads a game eval's runs of `vfg test` against the judge: what each fix
  changed (the test, the script, both), false passes, and lowered bars.
- `impl_cache.py`, Test 6: the cache hit rate over each task's session, read from a replay's
  `request` lines.
- `impl_wild.py`, Test 5: the ten wild asks, none refused by the model or the harness, and the
  guard still refusing `op`.

## Settings

| Key | Used for |
|---|---|
| `VFG_EC2_PYTHON` | the Python for the box's side, instead of the 3.12 `uv` finds |
| `VFG_JAVA` | the `java` for path 8; else `~/.local/share/jdk/*/bin/java`, else `java` on the PATH |

## The brain end to end

```sh
python3 integration/brain_e2e.py [--keep]
```

The real brain, started by `vfg brain start`, against a stand-in box on this PC: the fake SSH,
the real feed writer and `vfg.db`, and a fake 26.3 world (`scripts/_test/fake_places_world.py`
with players' items by slot) on the box's RCON port. Its model calls are real, through
OpenRouter, the loop's steps on Gemini 3.8 Flash and Respond's on DeepSeek V4 Flash:
`OPEN_ROUTER_API` from the environment, else from the first `.env` above this repo, or its
`Internal/.env`, that holds it; given to the brain's process only, never printed or written.
Each player's line goes into the feed as a `LINE` row through the box's `vfg_feed`, as Jev would
put a line said to the AI there (`to_ai`), and goes to Respond first (Prototype_Addressing_The_AI).
It checks an action ask Respond hands up and the brain's loop does in the world in one step, its
loop ended with its reply (`AI say -`) and one record (`AI action -`), no task, the brain's own
copy `command_steve`; an item ask on the right slot, handed up the same way; talk answered by
Respond, with no step of the brain; a casual ask handed up and acted on by a loop that replies or
ends silent (the night says it); two players' lines in the same second both answered, their loops at once, each
reply told apart by the words its loop logged, each player's copy named for them; the rows a
call read marked processed; a context line marked read at once with no call, and a game event
read by the next call; and `vfg brain stop`, with no row in `vfg.db`'s tasks table: nothing was
handed off. Then each line's time to its reply with its loop's steps, cost, cache rate and
ending (brain.log's `loop` line), and what the run cost. About a minute and twelve model calls,
about $0.035: each step about $0.005-0.008, its 30K-token prompt 95% read from the cache, and each
Respond call about $0.0006. On 2026-10-12, two runs: 39 of 39 checks passed each time, every loop
one step, replies 6.3-13.9 s after the line (the step's call 4.9-11.0 s). On 2026-10-13, with every
line to the AI going to Respond first: 41 of 41, each ask handed up by Respond in 0.96-1.25 s and
then one step, replies 8.3-13.3 s after the line, talk answered by Respond in 1.5-2.0 s. Not part
of any test suite.

## The brain live, on realmc

```sh
REALMC_ROOT=… python3 integration/brain_live.py [--name live] [--keep-up] [--no-implementer] [--only refused,read,…]
```

Prototype_Brain_Loop's "Live, on realmc" list, on a real 26.3 server (realmc, its world fresh
from the template, with Carpet's fake players Steve and Alex), every ask checked in the world over
RCON, in the feed's `AI` rows, in `vfg.db`'s tasks and usage tables and in `brain.log` (each step's
line, each loop's line, the turns'). It starts from a clean slate: the stand-in's `vfg.db` (with
its `-wal` and `-shm`) and `feed.log` set aside in `vfg/set-aside-<time>/`, never deleted, and made
again by `vfg_bridge --init-db`; `made/<name>/` saved with `vfg made save`, then moved into
`made/.replaced/`. Steve holds a diamond axe, carries an iron sword and iron boots and wears
diamond boots; Alex holds a trident; the place `home` is saved on the arena. Three brains run one
after another on the one feed:

1. **The defaults.** A one-step ask whose actions all land (its reply, one `AI action -` record,
   no task, the copy `command_steve`); a step with two actions together (64 cobblestone and
   twenty chickens: a `command` and a `summon_around`) and a step with two in turn (home, then
   day); a refused command the next step fixes (asks whose first command 26.3 may refuse, in
   turn, until one is: `/enchant` over Fire Aspect's maximum, the scale attribute by its old
   name, a sword's enchantments in the component's old `{levels:…}` form), checked in the world;
   Alex's items read with the tool, the next step's reply naming his trident; item asks by slot
   (boots worn and boots carried; a helmet he does not have); the brain's own copy
   `keep_effects_steve` started, edited (the `edit` action, Speed V) and stopped (the `stop`
   action); Steve's second line waiting for his loop (`turn #… Steve waits for their turn`)
   while Alex's runs beside it; three of Steve's lines waiting and a fourth turned away, never
   answered, by a private message naming the three (`AI say - | (to Steve) Still on: …`), the
   three taking his turn one after another; talk Respond answers alone (every line goes to
   Respond first, which hands an ask up to the loop); and one hand-off
   on the implementers' default, DeepSeek V4.1 Flash through OpenRouter (`docs/CONTRACTS.md` §10;
   the 2026-10-12 run below was on Haiku 5.5, the subscription): its task's name, its `AI task` row, its brief in
   `vfg.db` under its five headings with its done-when items, the turn ended as the implementer
   started, its report starting a new loop (one chat line, one `AI result` row, nothing the
   implementer wrote in the feed), what it made; then a follow-up by its name that resumes the
   session, and a stop of its copy by its name, the task still `done`.
2. **`VFG_LOOP_STEPS=2`, `VFG_IMPLEMENTER_MINUTES=1`.** A loop that reaches its step limit (read
   Alex's items, then a last step that may run nothing) and ends with a reply or a hand-off; and
   a report going ahead of Steve's waiting lines: a hand-off whose implementer reports within the
   1-minute limit, Steve's lines fed from 15 s after it started to keep his turn busy with lines
   waiting until the report comes, and the report's loop run before the lines still waiting when
   it came (`turn report … waits for Steve's turn, first in line`).
3. **`VFG_LOOP_SECONDS=2`, `VFG_IMPLEMENTER_MINUTES=1`.** A loop that reaches its time limit the
   same way.

The limits are lowered so a real ask reaches them in a step or two; 10 steps and 2 minutes are
the same code with other numbers. At the end: every line read, no task open, a clean stop, no
traceback or error in `brain.log`, the key in no file, every step a usage row `<ref> s<n>` and a
line of `brain.log`, no orchestrator or planner call, and the cost under $1. It prints each line's
time to its reply with its loop's steps, cost, cache rate and ending, the implementer tasks'
times, every call's cost, the cache rate of the steps after a loop's first, and how many loops
took one step and how many more. `--no-implementer` leaves out the two checks that start an
implementer; `--only` runs the checks it names (the brain each needs is started for it). It
copies a complete `refs/26.3/` from another server's stand-in when one has it, and leaves the
server down unless `--keep-up`. See [realmc/README.md](realmc/README.md) for the server and its
owner's rules.

On 2026-10-12 a full run passed 77 of 77 checks in about 8 minutes, and the report check, run on its
own again with the busy turn above, 23 of 23. Of 29 loops, 24 took one step and 5 two (Alex's
items read, then the reply; a refused command fixed; the two limits); 25 ended with a reply, 1
silent, 3 in a hand-off. A one-step ask's reply came 6.0-8.9 s after its line, a two-step one's
10.3-21.9 s, a hand-off's line 6.8-10.7 s; a step cost a median $0.0056, its 32-35K-token prompt
95% read from the cache, the steps after a loop's first too. The brain and Respond came to $0.21,
the implementers (the torch ring, its follow-up, and a staff the minute stopped) $0.35 of the
subscription. The brain went straight to an item modifier for `/enchant` over the maximum and
wrote the scale attribute by its new name; the sword's enchantments in the old `{levels:…}` form
were refused, and the next step gave the sword with the component's 26.3 form.

## Jev's gather call live, on realmc

```sh
REALMC_ROOT=… python3 integration/jev_live.py [--name jr] [--keep-up] [--no-brain]
```

The Jev routing spec's live test: an ask said in two pieces arrives at the brain as one group, and
another player's lines never join it. A real 26.3 server with Steve and Alex, the real bridge, the
real Jev filter calling the TypeSafe API with the key in the stand-in's `.env` (REALMC_ROOT's
`shared/env`, which only the filter reads), and the brain (`vfg brain start`, OpenRouter). Each
line is posted to the bridge as the mod posts it, a second or two after the one before. Three
scenarios, each an ask in two pieces with the other player saying something about the same thing
between them: "the cave over there is pitch black" … "AI, give me something so I can see in
there"; a broken pickaxe and "can you give me a new one"; an ocean monument and "I need to be able
to breathe down there". Per scenario it checks the feed has one `LINE` for the ask, under the
ask's fields and time, its text both pieces in order; the gather call never asked about the other
player's line, and no group holds it; the brain's turn is for that line (its usage rows,
Respond's `#seq` and its loop's steps' `#seq s<n>`; `brain.log`'s Respond line with the group's
words and its `loop` line for the asker; the task it opened, whose line is the group), and none is
for the first piece alone; and, a bonus not counted, what the brain did is in the world (night vision or torches, a
diamond pickaxe, water breathing). Jev is not deterministic: it prints every line's verdict with
D2's two answers, each gather answer with its confidence, the time from the end of the ask to
Jev's verdict, to the feed and to the brain's reply, the gather call's ms and each model call's
(Respond's, each step's), and the money. A scenario whose first piece Jev sent to the AI on its own, or whose ask it did
not send, is Jev's routing, not the gather's: it is reported and skipped. The run fails on any
failed check, or when every scenario was skipped. Each run starts a new transcript, as a new box:
the last run's `vfg.db`, feed, lookout, menu and logs move to `vfg/runs/<time>/`. `--no-brain` checks Jev alone (the groups on
the feed, no OpenRouter), setting the lookout and menu the brain would.

About 1.5 minutes. On 2026-10-11, three runs with the brain passed every check and the three
bonus: Jev's verdict 0.2–0.4 s after the end of the ask, the gather call 180–250 ms, the line fed
0.4–0.6 s after, the brain's reply 5.7–8.7 s after (its call 2.3–3.3 s). A run costs about $0.002
of TypeSafe (9 routing calls, 3 gather calls) and $0.014 of OpenRouter, or $0.075 when the brain's
opening is not cached yet. In eight runs, five of them `--no-brain`, the gather call said yes to
every first piece and no to every other line it was asked about; in the seven that printed its
confidence, the yes was 0.42–0.58 for the cave and 0.54–0.76 for the monument, against 0.90–0.96
for the pickaxe, and every no 1.00. On 2026-10-12, with the brain's loop: 16 of 16 checks and the
three bonus passed, each ask one step ended with a reply 10.3–10.9 s after the end of the ask (its
step 5.2–6.8 s), $0.002 of TypeSafe and $0.017 of OpenRouter; the gather's yes was 0.90 for the
cave, 0.96 for the pickaxe and 0.72 for the monument.

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): it checks §2–§9 end to end, and §12's start steps.
