# integration — the parts together on one PC

The real bridge, Jev filter and `vfg` toolkit, plus the client mod's HTTP client and the brain's
own reader of the feed (`brain/brain/context.py`), run together in a temporary folder that stands
in for the box's `/opt/vfg` and `/opt/mc`: `run.sh`. No EC2, no Minecraft, no game engine, no Jev
account, no model. The rest of this folder runs the parts against a real Minecraft 26.3 server on
this PC ([realmc/](realmc/README.md)), with the game engine installed and run there as on the box.

| Script | What it proves | Needs | Spends |
|---|---|---|---|
| `run.sh` (`harness.py`) | the bridge, Jev filter and toolkit together, driven as the brain drives them | Python 3.12; a JDK and the mod's classes for path 8 | nothing |
| `engine_e2e.py` | the game engine run as on the box, every op checked in the world | realmc | nothing |
| `incantations_e2e.py` | incantations as engine bindings, cast through the bridge in every way it casts | realmc | nothing |
| `brain_live.py` | the brain live: the conversation agent, the router and its loops, acting through the engine and its library | realmc with its test server, the OpenRouter key | OpenRouter: the brain's three agents; the implementers on a stand-in model |
| `jev_live.py` | Jev's gather call and its name rule live, then the brain's turn for the group | realmc, the stand-in's TypeSafe key, the OpenRouter key | TypeSafe; OpenRouter unless `--no-brain` |
| `loop_replay.py` | the 2026-10-11 session's 110 brain calls through today's brain | `--dry`: nothing; else realmc and the OpenRouter key | `--dry`, `--fake-model`: nothing; else about $1 of OpenRouter |
| `router_replay.py` | the router alone, on the 2026-10-08 playtest's follow-ups and the 2026-10-12/13 night's lines | its data (`--unpack`) | `--dry`: nothing; else about $0.2 of OpenRouter |
| `testserver_e2e.py` | the box's test server laid out on realmc, `vfg test` run as an implementer runs it: one engine per test, on a fresh world, in a sandbox (below) | realmc | nothing |
| `jev_addressing_bench.py` | Jev's name rule on the night of 2026-10-12/13's labelled lines (`jev/README.md`) | the night's `vfg.db` and labels | TypeSafe |
| [replay/](replay/README.md) | the playtests' lines through Jev's routing, the conversation agent and the router, offline | the playtest's `vfg.db` | TypeSafe or OpenRouter per bench |

`mod/ModBridgeContract.java` is path 8's Java side; `shims/crash_after_feed/` is path 7's fault
injection. None of this is part of a unit test run.

## Run it

```sh
integration/run.sh            # PASS or FAIL per check and per path; non-zero on any failure
integration/run.sh --keep     # keep the temp folder and its logs
```

About 70 s. The box's side (bridge, Jev filter, the toolkit's remote programs) runs on Python 3.12,
the box's `/opt/vfg/python`: `run.sh` finds one with `uv python find 3.12`, else uses `python3` and
says so. Path 8 needs a JDK and the mod's compiled classes: `cd client-mod && ./gradlew
compileJava` first.

Stand-ins: Minecraft (console lines appended by the harness, in the shapes of
`bridge/tests/fixtures/console-26.3.log`, and `toolkit/tests/fake_rcon.py`, in the harness's
process), SSH (`toolkit/tests/fake_ssh`, which runs the remote command locally), systemd
(`toolkit/tests/bin/`, its four units active), Jev's API (`jev/tests/fake_jev.py`) and the brain
(the harness makes the brain's `vfg` calls itself, as the brain makes them). No engine runs: the
bridge's and Jev's engine socket and export point into the temp folder, so no player has an
incantation and a cast answers 404. `HOME` and `VFG_HOME` point into the temp folder, the fake repo
links this repo's `actions/` and `engine/`, and every port is on 127.0.0.1, so nothing touches
`~/.vfg`, `~/.ssh` or any remote host.

| # | Checks |
|---|---|
| 0 | setup as the brain does it (§12): `vfg up` (minecraft, vfg-bridge, vfg-jev and vfg-engine active, RCON answering), the brain's watchers, its lookout, Jev's menu (`vfg jev menu set` with `vfg action list`'s lines: one per `actions/engine_*.py`), `vfg context --json`, then `vfg watch --after` its last seq, which keeps no cursor |
| 1 | `POST /v1/lines` → a `lines` row → Jev → one `LINE#seq` in `vfg watch`, unprocessed |
| 2 | typed chat in Jev's three classes and watched game events reach `vfg watch`; `not_relevant` is stored, not fed; a `to_ai` line flushes the waiting context at once |
| 3 | `vfg say` writes an `ai` line Jev never judges and an `AI say` feed row in the same call, `AI say castle_steve` for `--task castle_steve`; Jev's next state holds the AI's lines, the brain's `action` (a loop's one record), `task` and `result` rows, a task's named in front of its text, and not an older brain's notes, the lookout and `menu.md` |
| 4 | `vfg history` by date, source, player, each of the three classes (the old names refused), events and feed |
| 5 | `vfg feed post AI` for each kind, its field a task's name or `-`, each row written processed; `GAME` of any kind, `SYS`; `ORCH`, a task number and bad `AI` fields refused; `vfg context`, `--json` (the rows, then `{tasks}`, then `{last_seq}`) and `vfg context mark` on the rows the brain reads |
| 6 | `usage` rows: one per Jev call; `vfg usage add` writes them, cached tokens included: a step of a brain loop (`#seq s1`, `#seq s2`, a report's `<task> s1`), the conversation agent's (`#seq`, component `respond`), the router's (`#seq`, component `router`), an implementer's (its task's name), and an older brain's `orchestrator` row, which the table still takes; `structured()`'s queued rows (the brain's steps, a router's, an older brain's `planner` row, an alias call's), a failed step's among them (its cost NULL when an attempt was cut off in flight), sent by `vfg usage flush` with `ok`, `error` and `ms` |
| 7 | killing and restarting Jev, the brain's watch (`--after` its last seq) and the bridge loses and repeats nothing, and keeps the processed marks; an ask killed while its group waits for the feed is fed once, alone, and one killed after its group's feed write is not fed again |
| 8 | the client mod's real `BridgeClient` and `LinePayload` against the real bridge |
| 9 | a real group (§5): Steve's earlier line goes with his ask as one `LINE`, under the ask's fields and time, and Alex's line said between them is never asked about; every line the real feed writer produced, the group included, read by the brain's `context.parse`, and `vfg context --json` built into the brain's `Context` |
| 10 | the `tasks` table the bridge makes in `vfg.db`: `vfg task set` as the brain writes a task (the whole row, `made` with its three kinds `content`, `bindings` and `grants`, an update, a write older than the stored one kept out, a write of some keys, from stdin) and `vfg task list` (plain, `--open`, `--name`, `--json`); the tasks in `vfg context --json` and `vfg brain status`; an action's name and a library class's (`engine/library/`), `cast`, a name that is not a task's, a key that is not a column, a bad status or `made`, a new task with no description and bad JSON refused, nothing written |

| Key | Used for |
|---|---|
| `VFG_EC2_PYTHON` | the Python for the box's side, instead of the 3.12 `uv` finds |
| `VFG_JAVA` | the `java` for path 8; else `~/.local/share/jdk/*/bin/java`, else `java` on the PATH |

## On a real server: realmc

Every script below runs a realmc server of its own, named by `--name`, its world fresh from the
template (`up NAME --fresh`), with Carpet's Steve and Alex; `REALMC_ROOT` is a realmc `.server`
holding the jar, and `VFG_ACCEPT_EULA=true` is the owner's acceptance of Minecraft's EULA. Never
`down --all`, `--purge-key`, `--regen`, `key` or `carpet`: other builders' servers share
`REALMC_ROOT`, and its env file holds the real TypeSafe key, which only the Jev filter reads.

The engine runs there as on the box (`engine_e2e.py`'s `install`, `Engine` and `Bridge`, which the
others import): `engine/vfg_engine/`, the examples and the datapack sent to the stand-in's
`<vfg>/engine/`, `engine/deploy/install.sh` run there (the venv on the box's Python 3.12, the
datapack loaded), and `python -m vfg_engine` started with the stand-in's paths and no saved state.
Its socket is in `$XDG_RUNTIME_DIR`, linked from `<vfg>/engine/engine.sock` (a socket's address
holds 107 bytes), where `vfg engine` finds it. `brain_live.py`, `jev_live.py` and `loop_replay.py`
also send the library (`engine/library/`) to `<vfg>/engine/library/` and load it first, then
`made/<NAME>/content/`, as the unit's `VFG_ENGINE_CONTENT` does on the box.

## Incantations: `incantations_e2e.py`

```sh
REALMC_ROOT=… VFG_ACCEPT_EULA=true integration/incantations_e2e.py [--name vt] [--keep] [--speech]
```

The voice-triggers spec's live test, every incantation an engine binding. Two classes are written
fresh into `made/<NAME>/content/` and loaded by `vfg engine reload`: `vt_point`, an ability adding a
point to its caster's score `vt_fire` on each cast (cooldown 2 s), and `vt_move`, a one-shot
scenario moving its `who` to its `to`. Steve is granted `vt_point` and bound "fire ball" to it, and
"ice wall" to `vt_move` with `who=caster` and a spot 12 blocks across the arena (`--cooldown 2`);
no OpenRouter key reaches `vfg`, so a binding's aliases come from code. The real bridge runs
against the engine's export and socket; casts, lines and the Jev filter's cast check are posted as
the mod and the filter post them, each window on the wall clock. Checked in the world, in `vfg.db`
and in the feed:

- the list: `vfg engine incantations` (the ability's cooldown for an ability binding, the binding's
  own for a scenario's), `GET /v1/incantations` for Steve and for Alex, `304` after an unchanged
  wait, and a bind during a long poll answering it at once with the next version;
- a spotter's cast fired in the engine, its point in the world, recorded as
  `engine:steve.fire_ball` with its ability; the same window again `duplicate`; a new window at once
  the ability's `cooldown`, in the engine's words; "ice wall" right after it `fired` (the bridge
  keeps no gap between a player's casts), Steve moved to its spot, recorded with its scenario;
  Alex saying Steve's phrase `404`;
- the drop rule: "fire ball" over the cast dropped before Jev, "fire ball and build me a wall"
  kept with the cast noted;
- Layer 2: "fire bowl now" cast from its transcript, the line dropped, the spotter's late cast of
  it `duplicate`;
- the Jev filter's cast path (`POST /v1/jev/casts`; no filter runs, so lines stay unjudged): a
  9-word line (past Layer 2's 6) cast with `keep`, fired and the line kept with its `cast_id`,
  asked again answering the same; a short mishearing ("tybo") cast, fired and its line dropped;
  one asked 2.3 s after its line came `stale`, never sent, its line dropped;
- the five fired casts as five `cast` events and five `GAME cast Steve` feed lines, the score 4;
- `vfg engine unbind`: Steve's list loses "ice wall", and a cast of it is `404`.

`--speech` also has the mod's `castCheck` say "Fire ball!" (Kokoro) through its real speech
pipeline and spotter against this bridge, and times the end of the phrase to the point; it needs
the mod's test assets (`client-mod/build/vfg-test-assets/`). About 30 s, the server's start
included. A spotter's cast answers in about 80 ms, its point already in the world; a Layer 2 or a
Jev cast's point lands about 100 ms after its post.

## The game engine, on a real server

`integration/engine_e2e.py` is the game engine spec's live test, on realmc, the engine run as on the
box: installed into the stand-in's `/opt/vfg` with `engine/deploy/install.sh` (its venv, its
datapack loaded into the world), started as `vfg-engine` starts it with the stand-in's paths, the
real bridge against it, Carpet's Steve and Alex, and the content (the four examples and six test
classes, written fresh into `made/<NAME>/content/`) deployed by `vfg engine reload`. It drives
`vfg engine …`, `vfg action run engine_*`, the bridge's casts and the fake players, and checks each
step in the world: the Fire Staff's give, wrong base, modifier, a right-click's Fireball on a zombie
(about its declared damage, then its cooldown), a drop and Alex's own pick-up; Giant and Shrunk on
`scale` beside a vanilla Speed II; a Config rewritten, refused four times (a dropped field, code
in a formula, `__import__` in a value: refused before they are sent and never run; a value out of
bounds: put back) and undone; `vfg engine overview --json`; an incantation bound and cast through
`POST /v1/casts`; a blast with `self_damage` off that spares its caster and hits Alex once;
`mob_griefing` held twice, released both ways; a scenario's actor, stopped leaving nothing, one
killed in the world, and two orphans swept (one whose chunk unloaded and loaded again, one taken out
of the saved state); a call cut at 50 ms; a SIGTERM restart with everything back and a stray item
modifier removed; and the engine's own damage never coming back as a hit. The engine's socket is in `$XDG_RUNTIME_DIR`, linked from the
stand-in's `<vfg>/engine/engine.sock`, whose path is longer than a socket's address holds.

```sh
REALMC_ROOT=<a realmc .server with the jar> VFG_ACCEPT_EULA=true integration/engine_e2e.py [--name ge-e2e] [--keep]
```

About 90 s on a quiet PC. It prints the tick times `status` reported along the way and the latencies it measured.

## The test server, end to end: `testserver_e2e.py`

```sh
REALMC_ROOT=… python3 integration/testserver_e2e.py [--name iq-d1] [--only night,runaway,fresh,parallel,heal,refuse,guard]
python3 integration/testserver_e2e.py --box [--server main]     # on the real box: box-safe, strict
```

Prototype_Implementer_Quality's test server proven on this PC, with the real `vfg test` run as an
implementer runs it (realmc's `vfg`, `VFG_TEST_BOX` unset) against a realmc server with its test
server laid out beside it as the box's (`up NAME --test-server`): the night of 2026-10-12 replayed
safely (**night**), a test that never ends (**runaway**), a fresh world for every test
(**fresh**), parallel streams (**parallel**), the test server killed, stopped or stalled
(**heal**), reaches for the live server (**refuse**, with `testserver_escape/`'s escapes) and the
implementers' guard (**guard**). Each check prints PASS, FAIL or INFO with its numbers; the
script's docstring says what each does. Outputs in `$VFG_IMPL_REPLAY/testserver-e2e-NAME/`. No
model is called. Server names start with `iq-d`.

Every test runs as `vfg test` runs one now: `vfg_engine.livetest` on an engine of its own, on the
test server, in the sandbox. The content and the tests the suite writes are the engine's — an
ability (`iqd_blizzard`, an engine Scenario deployed with `vfg engine reload` and bound with `vfg
engine bind`) and livetest test files (`def test(t)`); the runaway is a livetest test that never
ends and detaches a copy, to prove `vfg test --timeout` stops it (exit 2) and the sandbox takes
everything in it; the reach test checks that an engine and `livetest` pointed at the live
`server.properties` refuse it (`VFG_TEST=1`, no marker) and that the sandbox hides the live files.
`testserver_escape/` is a livetest test file too.

## The router replay: `router_replay.py`

```sh
brain/.venv/bin/python integration/router_replay.py --unpack [--data D]          # the data, into ~/.vfg/router-replay
brain/.venv/bin/python integration/router_replay.py --dry [--data D]             # no model: the plumbing and the scorer
brain/.venv/bin/python integration/router_replay.py [--reasoning low] [--data D] # both sets, real calls, about $0.2
python3 integration/replay/router/score.py OUT [OUT …]                           # OUT/report.md
```

Prototype_Conversation_And_Router's first test: the real router (`brain/brain/router.py`, its
prompt, the actions, each one of the engine's ops, and the command references) on the 2026-10-08
playtest's follow-ups and new things (rebuilt from the playtest's `vfg.db` and `replay/tasks.jsonl`)
and on the 64 lines Jev sent to the AI on the night of 2026-10-12/13, with WORK UNDER WAY from
`brain/brain/loops.py` fed by the replay's own choices. The night's tasks show what they made as
classes (`made.content`, their scripts' names). Model calls only, one at a time; their usage rows
queue in the run's folder. The data and the runs stay out of the repo (`--data`,
`$VFG_ROUTER_REPLAY_DATA`); how each set is built and scored is in the two scripts' docstrings, and
the measured runs, on the brain's prompt from before the game engine, in
[`replay/router/report.md`](replay/router/report.md). Not part of any test suite.

## The brain live: `brain_live.py`

```sh
REALMC_ROOT=… VFG_ACCEPT_EULA=true python3 integration/brain_live.py [--name live] [--keep-up] [--only refused,read,…] [--brain-reasoning off]
```

Prototype_Conversation_And_Router's "Live, on realmc" list, with Prototype_Brain_Loop's checks
that still mean something, the brain acting only through the engine's ops, on a real 26.3 server
(realmc, its world fresh from the template, with Carpet's fake players Steve and Alex, and its test
server beside it as the box's, `up --fresh --test-server`, where the implementers' `vfg test`
plays) with the engine and its library. Every ask is checked in the world over RCON, in the engine
(`vfg engine instances`), in the feed's `AI` rows, in `vfg.db`'s tasks and usage tables and in
`brain.log`: each line's `respond`, `router` and `front` lines (the conversation agent's answer,
where the router put the work, the lines said), each step's line and each loop run's (`loop #5
Steve: …, ended reply; L1 saved, 1 of 10 steps, lines #5`). The OpenRouter key is
`OPEN_ROUTER_API` from the environment, else the first `.env` above this repo, or its
`Internal/.env`, that holds it; it goes to the brain's process only, never printed or written.

It starts from a clean slate: the stand-in's `vfg.db` (with its `-wal` and `-shm`), `feed.log` and
deployed `made/<name>/` set aside in `vfg/set-aside-<time>/`, never deleted, and `vfg.db` made again
by `vfg_bridge --init-db`; `made/<name>/` saved with `vfg made save` (to `$VFG_MADE_ARCHIVE`, only
when its `content/` or `tests/` hold files), then moved into `made/.replaced/`; the engine installed
with no saved state, the library loaded with no error. Steve holds a diamond axe, carries an iron
sword and iron boots and wears diamond boots; Alex holds a trident; the place `home` is in the
engine's places (its `places_import` op), listed by `vfg engine places`.

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
   Through the front turn, each: a one-step ask whose actions all land (the agent's held line, the
   loop's reply, one `AI action -` record, no task, its actions the engine's, nothing of them
   running on); actions together (64 cobblestone and twenty chickens) and in turn (any two of the
   engine's actions that land in order); a refused command the next step fixes; Alex's items read
   with the tool; item asks by slot; an effect the engine keeps on Steve (the instance in `vfg
   engine instances`), changed and stopped by the engine's actions; three of Steve's lines waiting
   for his turn and a fourth turned away by a private message naming them; a line the agent answers
   itself, no router, no loop. New: Steve's second line waiting only for his first line's front
   turn, his two loops side by side (the first's step held 12 s) and Alex's line answered beside
   them; a step held 65 s (47 s with `--brain-reasoning off`) landing inside its timeout, 120 s at
   low, while Steve's "is it done yet?" is answered at once, never as done; a line the router adds
   to a thinking loop (its step held 15 s), read in an extra step (the loop's limit 20), a line
   added to it once ended, inside its 2 minutes, taken up with its step read from the cache, and
   after the 2 minutes a line on that thing starting a new loop; the router starting an
   implementer (the boomerang axe) with its brief in `vfg.db` under its five headings and a name
   no action's or library class's, following it up while it runs (the follow-up in its model's
   next request), and sending the same ask again nowhere with its own line in place of the
   agent's; the report reaching the conversation agent and told while Steve's turn is busy (his
   agent's call held 17 s, under its 20 s timeout, across the report); and, over every line of
   this brain, one line per ask from the front turn.
2. **`VFG_ROUTER_REASONING=low`**: nothing held: a new thing (a frost bow) and the same ask again,
   the agent's line out before the router answers, and the router's own after it.
3. **`VFG_LOOP_STEPS=1`, `VFG_LOOP_CAP=2`**: a line's loop gets one step (its first is its last),
   the line added to it once ended one more, at the cap: its last step replies or hands off and it
   is deleted, and the next line on that thing starts a new loop. 10 and 20 are the same code with
   other numbers.

An everyday ask (a give, a teleport, the time, a summon) is one of the library's scenarios started
with `engine_start`: one the library lacks fails its check in the world, with the brain's own
account in the check's evidence. At the end: every line read, no task open, the brain and the
engine stopped cleanly, no traceback or error in `brain.log`, no report waiting for a turn, the key
in no file, every step a usage row `<ref> s<n>` and a line of `brain.log`, every agent's and
router's call a row `#<seq>` (or a report's task), no orchestrator or planner call, and the cost
under $1. It prints each line's front turn (the agent's ending and ms, where the router put it, the
lines said), its work (the loop, its steps, cost, cache rate, ending, state and budget), its time
to the reply, every call's cost, and the medians of each agent's calls. `--only` runs the checks it
names (the brain each needs is started for it). It copies a complete `refs/26.3/` from another
server's stand-in when one has it, and leaves the server down unless `--keep-up`.

Measured on the brain's prompt from before the game engine, on 2026-10-13, after the router's code
review's last fixes (the brain at low by default, 120 s a step; brain.md 2538001ce692, respond.md
f3034d3e74ab, router.md 7c327c4770d4; realmc cr-live): a full run passed **89 of 89** checks in
about 15 minutes, for $0.082 of OpenRouter; the agent's call took a median 561 ms, the router's
733 ms, a step 1.5 s; 91% of 2.0M prompt tokens came from the cache. On the engine its setup is
checked to the brain's start (the engine and the library's classes loaded, `home` in the engine,
a clean stop); no full run with model calls yet.

## Jev's gather call and its rule live: `jev_live.py`

```sh
REALMC_ROOT=… VFG_ACCEPT_EULA=true python3 integration/jev_live.py [--name jr] [--keep-up] [--no-brain] [--rule-only]
```

The Jev routing spec's live test, and Prototype_Addressing_The_AI's rule: an ask said in two pieces
arrives at the brain as one group, another player's lines never join it, and a line goes to the AI
only when it names it or answers a question it asked. The engine and its library run as for `brain_live.py`, the
real bridge against them, and the real Jev filter calling the TypeSafe API with the key in the
stand-in's `.env` (`REALMC_ROOT`'s `shared/env`, which only the filter reads), reading the engine's
export and reaching the bridge as on the box; the brain is `vfg brain start`, OpenRouter. Each line
is posted to the bridge as the mod posts it, a second or two after the one before. Three
scenarios, each an ask in two pieces with the other player saying something about the same thing
between them: "the cave over there is pitch black" … "AI, give me something so I can see in
there"; a broken pickaxe and "can you give me a new one"; an ocean monument and "I need to be able
to breathe down there". Per scenario it checks the feed has one `LINE` for the ask, under the
ask's fields and time, its text both pieces in order; the gather call never asked about the other
player's line, and no group holds it; the brain's turn is for that line (its usage rows,
the conversation agent's and the router's `#seq` and its loop's steps' `#seq s<n>`; `brain.log`'s
agent line with the group's words and its `loop` line for the asker; the task it opened, whose
line is the group), and none is for the first piece alone; and, a bonus not counted, what the brain did is in the world (night vision or torches, a
diamond pickaxe, water breathing). Then the rule (`RULE_CASES`): lines said one after another, and
a question from the AI (`vfg say`), each checked for its class: a line naming the AI goes to it
(`to_ai`), an answer to the question the AI asked another player goes, and an ask to a friend, an
ask that names no AI and a line about the AI do not; with the brain on, it answers what goes.
`--rule-only` runs the rule's cases alone. Jev is not deterministic: it prints every line's verdict with
D2's two answers, each gather answer with its confidence, the time from the end of the ask to
Jev's verdict, to the feed and to the brain's reply, the gather call's ms and each model call's
(the agent's, the router's, each step's), and the money. A scenario whose first piece Jev sent to the AI on its own, or whose ask it did
not send, is Jev's routing, not the gather's: it is reported and skipped. The run fails on any
failed check, or when every scenario was skipped. Each run starts a new transcript, as a new box:
the last run's `vfg.db`, feed, lookout, menu and logs move to `vfg/runs/<time>/`. `--no-brain` checks Jev alone (the groups on
the feed, no OpenRouter), setting the lookout and menu the brain would.

About 1.5 minutes. Measured on the brain from before the game engine: on 2026-10-11, three runs with the brain passed every check and the three
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

## The loop replay: `loop_replay.py`

The 2026-10-11 session's 110 brain calls through today's brain, on a realmc server of its own with
the engine and its library, the implementers recording each brief and starting nothing:
[replay/README.md](replay/README.md#the-loop-replay-2026-10-11-through-the-brains-loop). Each of
the night's lines runs as a new loop, as if the router had chosen `new_loop`; each report goes to
the conversation agent, and what it hands off to the real router (`--no-route` only counts it).
What that night's implementers made (script actions, and incantations in the store before the
engine's) is not put back, and each such change is recorded as not done at its time. `--dry` runs
the dataset, every context and the report with no model and no server; `--fake-model` runs it all
on the server with canned answers, at no cost.

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): `run.sh` checks §2–§9 end to end, and §12's start steps.
