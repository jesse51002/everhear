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
| 6 | `usage` rows: one per Jev call; `vfg usage add` writes them, cached tokens included: a step of a brain loop (`#seq s1`, `#seq s2`, a report's `<task> s1`), the conversation agent's (`#seq`, component `respond`), the router's (`#seq`, component `router`), an implementer's (its task's name), and an older brain's `orchestrator` row, which the table still takes; `structured()`'s queued rows (the brain's steps, a router's, an older brain's `planner` row, an alias call's), a failed step's among them (its cost NULL when an attempt was cut off in flight), sent by `vfg usage flush` with `ok`, `error` and `ms` |
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
  (`--harness opencode`). Each server has its test server beside it, laid out as the box's
  (`realmc/up <name> --test-server`: `<name>/mc-test` and `<name>/vfg-test`, the live folders +
  "-test", the unit `minecraft-test`, a fresh world at every start, each test in its sandbox) and
  made fresh with it, where the implementers' `vfg test` runs with `VFG_TEST_BOX` unset, exactly
  the box's path (`--test none`: none, `VFG_TEST_BOX=none`), checked first with one library test
  (`--preflight railgun`); at the end, also on a failure or Ctrl-C, both are stopped unless `--keep-up`;
  `--judge` scores each task with `impl_judge.py`, which casts its action twice,
  each from fresh state on a stage of the test templates' geometry, and has a judge model mark its
  DONE WHEN items: done only when both casts show it (flaky when one does), the judge's own probes
  winning over the implementer's words; a research task, or a scenario not buildable, is scored
  apart, outside the total (`--night`: what Haiku made on 2026-10-11).
- `impl_scenarios.py` derives one scenario per replayed task, once, into
  `$VFG_IMPL_REPLAY/scenarios.json` for a person to review: the player, his opening line, his goal,
  his hidden wants in order, the setup, the DONE WHEN list and how he tries it.
- `impl_game.py`, the game eval: each scenario played from nothing on a fresh live server and its
  test server beside it (`up <name> --fresh --test-server`, `VFG_TEST_BOX` unset, as on the box),
  on the runner's own settings (`VFG_IMPL_EFFORT`, high by default; 20 minutes a run): each turn
  the stand-in brain writes the brief, the real runner builds it, the player
  tries it on the live server and a simulated player says his next line (a complaint, his next
  want, or done); then `impl_judge.py` marks the whole checklist. `--dry` spends nothing. At the end,
  also on a failure or Ctrl-C, the server and its test server (2-3 GB each) are stopped unless
  `--keep-up`.
- `impl_game_tests.py` reads a game eval's runs of `vfg test` against the judge: what each fix
  changed (the test, the script, both), false passes, and lowered bars.
- `impl_cache.py`, Test 6: the cache hit rate over each task's session, read from a replay's
  `request` lines.
- `impl_wild.py`, Test 5: the ten wild asks, none refused by the model or the harness, and the
  guard still refusing `op`.

With `VFG_TEST_BOX` unset, its default, an implementer's `vfg test` plays on the test server
beside the live one, as on the box. `impl_game.py`, `impl_replay.py` and `brain_live.py` lay it out
(`realmc/up NAME --test-server`); the others (`impl_check*.py`, `impl_messages*.py`,
`impl_wild.py`) do not, so their implementers' `vfg test` exits 3 (no test server) and they check
with one action run, unless `integration/realmc/up NAME --test-server` ran first. Their waits for
a report follow the runner's limit (`VFG_IMPLEMENTER_MINUTES`, 20) and 2 minutes more.

## The test server, end to end on realmc

```sh
REALMC_ROOT=… python3 integration/testserver_e2e.py [--name iq-d1] [--only night,runaway,fresh,parallel,heal,refuse,guard]
python3 integration/testserver_e2e.py --box [--server main]     # on the real box: box-safe, strict
```

Prototype_Implementer_Quality's test server proven on this PC, with the real `vfg test` run as an
implementer runs it (realmc's `vfg`, `VFG_TEST_BOX` unset) against a realmc server with its test
server laid out beside it as the box's (`up NAME --test-server`). Each check prints PASS, FAIL
or INFO with its numbers, and a table at the end; the script's docstring says what each does.

- **night**, the night of 2026-10-12 replayed safely: the live server holds Carpet's fake KrownJM
  and Styrkaeopteryx 3 blocks apart, and gold blocks on the arena and where the sky stage is. A
  made ice aura (10 s of freeze damage and slowness round the caster, sparing her, then it ends),
  bound to her "freeze" on the live server, and its test from the kit's helpers (cast as her on
  the test server, KrownJM beside her hurt and slowed, she not, the storm ending), run 3 times.
  The live players, world, files and console are compared before and after.
- **runaway**: an aura that never ends, ignores SIGTERM and starts a detached copy of itself, and
  a test that waits for its end: stopped at `--timeout 20`, nothing of it left (`/proc`, its
  sandbox unit), and the next test on a fresh world; then the same without the sandbox (INFO).
- **fresh**: a baseline test records the fresh world (24 probes: objectives, bossbars, teams,
  border, forceloads, gamerules, time, weather, difficulty, players, entities, a block outside the
  stage and one under it, a fake player's saved effects, items and xp, which Carpet reloads in a
  kept world); then 20 tests in a row, each checking every probe against it and then leaving junk
  in all of them.
- **parallel**: 3 streams of 5 such tests at once, as parallel implementers; their windows on the
  server never overlap.
- **heal**: the test server's java SIGKILLed, its unit stopped, and its java SIGSTOPped (the RCON
  port still takes connections): exit 5 in bounded time with a short boot budget, recovery after,
  and with the defaults the restart clears it.
- **refuse**: `VFG_TEST_BOX` naming the live folders, half of them, a folder without the marker, a
  link to the live folder: exit 3. A test reaching for the live server (its `server.properties`,
  folder, `.env`, a write, mc and a child given its properties, mc switched by hand after its
  check): refused in the sandbox; the kit's `NotTestServer` again without one. The escapes round
  the sandbox are `testserver_escape/`'s test: INFO in the PC's user sandbox (the same uid as the
  live server), every one refused on the box (`--box`).
- **guard**: the implementers' guard at unit level: 4 tests a run, unchanged reruns refused, live
  probes capped, and the prompt's exit-4/5 rule.

`--box` runs only the reach and escape tests, on the real box's test server, through this
checkout's `vfg` and `~/.vfg/config`: nothing goes to the live server but `vfg test`'s read of its
incantations. Outputs in `$VFG_IMPL_REPLAY/testserver-e2e-NAME/` (`e2e.log`, every `vfg test`
whole; `summary.json`, `summary.md`). No model is called. Server names start with `iq-d`.

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
OpenRouter, on the brain's defaults: the conversation agent, the router and the loops' steps on
DeepSeek V4.1 Flash, the first two with no reasoning and the steps at low (`brain/brain/config.py`):
`OPEN_ROUTER_API` from the
environment, else from the first `.env` above this repo, or its `Internal/.env`, that holds it;
given to the brain's process only, never printed or written. Each player's line goes into the feed
as a `LINE` row through the box's `vfg_feed`, as Jev would put a line said to the AI there
(`to_ai`, Prototype_Addressing_The_AI), and takes its player's turn for two calls
(Prototype_Conversation_And_Router): the conversation agent answers, and a hand-off goes to the
router, which places the work, here a new loop each time. It checks an action
ask handed off by the agent, a new loop started by the router (brain.log's `front #seq` line),
done in the world in one step, the loop's reply (`AI say -`) after the agent's held
acknowledgement when it wrote one, one record (`AI action -`), no task, the brain's own copy
`command_steve`; an item ask on the right slot; a line the agent answers itself, with no router
and no loop; a casual ask handed off and acted on by a loop that replies or ends silent; two players' lines in the same second both answered, their loops at once; the rows a
call read marked processed; a context line marked read at once with no call, and a game event
read by the next call; and `vfg brain stop`, with no row in `vfg.db`'s tasks table: nothing went
to an implementer. Then each line's time to its reply with its front turn and its loop's steps,
cost, cache rate and ending (brain.log's `front` and `loop` lines), and what the run cost. About
a minute and twenty model calls. Not part of any test suite. Its log grammar (`front_of`,
`loop_runs`, `work_of`, `logged_reply`) is what `brain_live.py` and `jev_live.py` read brain.log
with.

On 2026-10-13, on this spec's build: 36 of 41 and 39 of 41 checks, $0.017–0.022 a run; replies 4.3–6.1 s
after the line, the agent's call 0.4–0.8 s, the router's 0.55–0.95 s, a step 0.75–1.2 s. What
failed was the brain's step (refused twice by its checks, once; an item modifier on the boots carried
rather than worn, once) and, once, the conversation agent leaving Alex's ask said in the same second
as Steve's unanswered and undone. On 2026-10-12, before this spec (the loop's steps on Gemini, Respond
in front of conversational lines only), two runs: 39 of 39 checks passed each time. On
2026-10-13, with every line to the AI going to Respond first (Prototype_Addressing_The_AI, before
this spec): 41 of 41, each ask handed up by Respond in 0.96-1.25 s and then one step, replies
8.3-13.3 s after the line, talk answered by Respond in 1.5-2.0 s.

## The router replay

```sh
brain/.venv/bin/python integration/router_replay.py --unpack          # the data, into ~/.vfg/router-replay
brain/.venv/bin/python integration/router_replay.py --dry             # no model: the plumbing and the scorer
brain/.venv/bin/python integration/router_replay.py [--reasoning low] # both sets, real calls, about $0.2
python3 integration/replay/router/score.py OUT [OUT …]                # OUT/report.md
```

Prototype_Conversation_And_Router's first test: the real router (`brain/brain/router.py`, its
prompt, the library's actions and the command references) on the 2026-10-08 playtest's follow-ups
and new things (rebuilt: taskcall.py's inputs are gone) and on the 64 lines Jev sent to the AI on
the night of 2026-10-12/13, with WORK UNDER WAY from `brain/brain/loops.py` fed by the replay's own
choices. Model calls only, one at a time; their usage rows queue in the run's folder. The data and
the runs stay out of the repo (`--data`, `$VFG_ROUTER_REPLAY_DATA`); how each set is built and
scored is in the two scripts' docstrings, and the measured runs in
[`replay/router/report.md`](replay/router/report.md). Not part of any test suite.

## The brain live, on realmc

```sh
REALMC_ROOT=… python3 integration/brain_live.py [--name cr-live] [--keep-up] [--only refused,read,…]
```

Prototype_Conversation_And_Router's "Live, on realmc" list, with Prototype_Brain_Loop's checks
that still mean something, on a real 26.3 server (realmc, its world fresh from the template, with
Carpet's fake players Steve and Alex, and its test server beside it as the box's, `up --fresh
--test-server`, where the implementers' `vfg test` plays), every ask checked in the world over RCON, in the feed's `AI`
rows, in `vfg.db`'s tasks and usage tables and in `brain.log`: each line's `respond`, `router` and
`front` lines (the conversation agent's answer, where the router put the work, the lines said),
each step's line and each loop run's (`loop #5 Steve: …, ended reply; L1 saved, 1 of 10 steps, lines
#5`). It starts from a clean slate: the stand-in's `vfg.db` (with its `-wal` and `-shm`) and
`feed.log` set aside in `vfg/set-aside-<time>/`, never deleted, and made again by `vfg_bridge
--init-db`; `made/<name>/` saved with `vfg made save`, then moved into `made/.replaced/`. Steve holds
a diamond axe, carries an iron sword and iron boots and wears diamond boots; Alex holds a trident;
the place `home` is saved on the arena.

Two stand-ins, so the checks that need timing get it on cue and spend nothing:
- **the implementers' model**, [`stand_in_model.py`](stand_in_model.py): an Anthropic Messages API
  on 127.0.0.1 that the real runner, relay and Claude Code CLI talk to (`VFG_IMPL_PROVIDER=deepseek`,
  `DEEPSEEK_API_KEY=stand-in`, `VFG_IMPL_BASE_URL`, in the brain's environment only). It records
  every request (`<server>/stand-in-model.jsonl`) and answers with a `Done:` report, an
  implementer's first answer after 32 s and every later one after 3 s: it builds nothing;
- **a slow provider**, [`shims/slow_step/`](shims/slow_step/sitecustomize.py), on the brain's
  `PYTHONPATH` with `VFG_TEST_SLOW`: it holds a chosen brain step (or a conversation agent's call)
  back for so many seconds before vfg_llm sends it, inside the call's own timeout, and logs `test
  slow call: held …`. It does nothing without `VFG_TEST_SLOW`.

Three brains run one after another on the one feed:

1. **The defaults** (the router with no reasoning: the agent's line to a hand-off held for it).
   Through the front turn now, each: a one-step ask whose actions all land (the agent's held line,
   the loop's reply, one `AI action -` record, no task, the copy `command_steve`); actions together
   (64 cobblestone and twenty chickens) and in turn (home, then day); a refused command the next
   step fixes; Alex's items read with the tool; item asks by slot; the brain's own copy
   `keep_effects_steve` started, edited and stopped; three of Steve's lines waiting for his turn
   and a fourth turned away by a private message naming them; a line the agent answers itself, no
   router, no loop. New: Steve's second line waiting only for his first line's front turn, his two
   loops side by side (the first's step held 12 s) and Alex's line answered beside them; a step
   held 65 s (47 s with `--brain-reasoning off`) landing inside its timeout, 120 s at low, while
   Steve's "is it done yet?" is answered at once,
   never as done; a line the router adds to a thinking loop (its step held 15 s), read in an extra
   step (the loop's limit 20), a line added to it once ended, inside its 2 minutes, taken up with
   its step read from the cache, and after the 2 minutes a line on that thing starting a new loop;
   the router starting an implementer (the boomerang axe) with its brief in `vfg.db` under its five
   headings, following it up while it runs (the follow-up in its model's next request), and
   sending the same ask again nowhere with its own line in place of the agent's; the report
   reaching the conversation agent and told while Steve's turn is busy (his agent's call held 17 s,
   under its 20 s timeout, across the report); and, over every line of this brain, one line per ask from the front turn.
2. **`VFG_ROUTER_REASONING=low`**: nothing held: a new thing (a frost bow) and the same ask again,
   the agent's line out before the router answers, and the router's own after it.
3. **`VFG_LOOP_STEPS=1`, `VFG_LOOP_CAP=2`**: a line's loop gets one step (its first is its last),
   the line added to it once ended one more, at the cap: its last step replies or hands off and it
   is deleted, and the next line on that thing starts a new loop. 10 and 20 are the same code with
   other numbers.

At the end: every line read, no task open, a clean stop, no traceback or error in `brain.log`, no
report waiting for a turn, the key in no file, every step a usage row `<ref> s<n>` and a line of
`brain.log`, every agent's and router's call a row `#<seq>` (or a report's task), no orchestrator
or planner call, and the cost under $1. It prints each line's front turn (the agent's ending and
ms, where the router put it, the lines said), its work (the loop, its steps, cost, cache rate,
ending, state and budget), its time to the reply, every call's cost, and the medians of each
agent's calls. `--only` runs the checks it names (the brain each needs is started for it). It
copies a complete `refs/26.3/` from another server's stand-in when one has it, and leaves the
server down unless `--keep-up`. See [realmc/README.md](realmc/README.md) for the server and its
owner's rules.

On 2026-10-13, after the code review's last fixes (the brain at low by default, 120 s a step, the
Step's values slot `fields`; brain.md 2538001ce692, respond.md f3034d3e74ab, router.md 7c327c4770d4;
realmc cr-live), a full run passed **89 of 89** checks in about 15 minutes, for $0.082 of OpenRouter:
22 hand-offs placed, each agent's line said after the router answered; the held step's call 66.6 s,
past 60 s, inside 120 s, with "is it done yet?" answered at once meanwhile; a line added to a
thinking loop read in an extra step, one added once it ended taken up from the cache, one after its
2 minutes a new loop; the boomerang axe started with its brief, followed up, the same ask again sent
nowhere with one line, its report told 9.9–10.7 s into Steve's held 18.1 s turn; at low, the agent's
line out before the router answered and, once, the router's own after it; and the budget's cap. The
agent's call took a median 561 ms, the router's 733 ms, a step 1.5 s; 91% of 2.0M prompt tokens came
from the cache.

The run before those fixes passed 74 of 88: the router sent "give me 64 steak" nowhere with no line
of its own, so the agent's held "On it, chef!" went out and nothing was done, and three steps keyed a
value with the action's own name and were refused twice.

Before the fix round, five runs on the same build: the last passed 83 of 86 ($0.077), the others
74–80 of 84–87, every failure the brain's model, not the plumbing: steps refused twice by their
checks, a plain give handed to an implementer, a silent ending after a refused command.

Before this spec, on 2026-10-12, a full run passed 77 of 77 checks in about 8 minutes (the loop's
steps on Gemini 3.8 Flash low, Respond in front of conversational lines only).

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
the conversation agent's and the router's `#seq` and its loop's steps' `#seq s<n>`; `brain.log`'s
agent line with the group's words and its `loop` line for the asker; the task it opened, whose
line is the group), and none is for the first piece alone; and, a bonus not counted, what the brain did is in the world (night vision or torches, a
diamond pickaxe, water breathing). Jev is not deterministic: it prints every line's verdict with
D2's two answers, each gather answer with its confidence, the time from the end of the ask to
Jev's verdict, to the feed and to the brain's reply, the gather call's ms and each model call's
(the agent's, the router's, each step's), and the money. A scenario whose first piece Jev sent to the AI on its own, or whose ask it did
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

On 2026-10-13, on Prototype_Conversation_And_Router's build (every line through the conversation
agent and the router, the loop's steps on DeepSeek V4.1 Flash): 16 of 16 checks, the three scenarios
checked, the gather's yes 0.88, 0.96 and 0.80; the brain's reply 4.8–5.6 s after the end of the ask
(the agent 0.67–0.96 s, the router 0.60–0.91 s); the world bonus 1 of 3, the other two loops' steps
refused twice by their checks ("tangled up"); $0.002 of TypeSafe and $0.004 of OpenRouter.

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): it checks §2–§9 end to end, and §12's start steps.
