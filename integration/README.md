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
| `brain_live.py` | the brain's loop live, acting through the engine and its library | realmc, the OpenRouter key | OpenRouter: the brain, Respond and one implementer |
| `jev_live.py` | Jev's gather call live, then the brain's turn for the group | realmc, the stand-in's TypeSafe key, the OpenRouter key | TypeSafe; OpenRouter unless `--no-brain` |
| `loop_replay.py` | the 2026-10-11 session's 110 brain calls through today's loop | `--dry`: nothing; else realmc and the OpenRouter key | `--dry`: nothing; else about $1 of OpenRouter |
| [replay/](replay/README.md) | the 2026-10-08 playtest's lines through Jev's routing and Respond, offline | the playtest's `vfg.db` | TypeSafe or OpenRouter per bench |

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
| 2 | typed chat in Jev's four classes and watched game events reach `vfg watch`; `not_relevant` is stored, not fed; a `conversational` line flushes the waiting context at once |
| 3 | `vfg say` writes an `ai` line Jev never judges and an `AI say` feed row in the same call, `AI say castle_steve` for `--task castle_steve`; Jev's next state holds the AI's lines, the brain's `action` (a loop's one record), `task` and `result` rows, a task's named in front of its text, and not an older brain's notes, the lookout and `menu.md` |
| 4 | `vfg history` by date, source, player, each of the four classes, events and feed |
| 5 | `vfg feed post AI` for each kind, its field a task's name or `-`, each row written processed; `GAME` of any kind, `SYS`; `ORCH`, a task number and bad `AI` fields refused; `vfg context`, `--json` (the rows, then `{tasks}`, then `{last_seq}`) and `vfg context mark` on the rows the brain reads |
| 6 | `usage` rows: one per Jev call; `vfg usage add` writes them, cached tokens included: a step of the brain's loop (`#seq s1`, `#seq s2`, a report's `<task> s1`), Respond's (`#seq`), an implementer's (its task's name), and an older brain's `orchestrator` row, which the table still takes; `structured()`'s queued rows (the brain's steps, an older brain's `planner` row, an alias call's), a failed step's among them (its cost NULL when an attempt was cut off in flight), sent by `vfg usage flush` with `ok`, `error` and `ms` |
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

## The brain live: `brain_live.py`

```sh
REALMC_ROOT=… VFG_ACCEPT_EULA=true python3 integration/brain_live.py [--name live] [--keep-up] [--no-implementer] [--only refused,read,…]
```

Prototype_Brain_Loop's "Live, on realmc" list, the brain acting only through the engine's ops, on a
real 26.3 server with the engine and its library. Every ask is checked in the world over RCON, in
the engine (`vfg engine instances`), in the feed's `AI` rows, in `vfg.db`'s tasks and usage tables
and in `brain.log` (each step's line, each action's, each loop's, the turns'). The OpenRouter key is
`OPEN_ROUTER_API` from the environment, else the first `.env` above this repo, or its
`Internal/.env`, that holds it; it goes to the brain's process only, never printed or written.

It starts from a clean slate: the stand-in's `vfg.db` (with its `-wal` and `-shm`), `feed.log` and
deployed `made/<name>/` set aside in `vfg/set-aside-<time>/`, never deleted, and `vfg.db` made again
by `vfg_bridge --init-db`; `made/<name>/` saved with `vfg made save` (to `$VFG_MADE_ARCHIVE`), then
moved into `made/.replaced/`; the engine installed with no saved state, the library loaded with no
error. Steve holds a diamond axe, carries an iron sword and iron boots and wears diamond boots; Alex
holds a trident; the place `home` is in the engine's places (its `places_import` op), listed by
`vfg engine places`. Three brains run one after another on the one feed:

1. **The defaults.** A one-step ask whose actions all land (its reply, one `AI action -` record,
   no task, its actions the engine's, nothing of them running on); a step with two actions
   together (64 cobblestone and twenty chickens) and a step with two in turn (home, then day); a
   refused command the next step fixes (asks whose first command 26.3 may refuse, in turn, until
   one is: `/enchant` over Fire Aspect's maximum, the scale attribute by its old name, a sword's
   enchantments in the component's old `{levels:…}` form), checked in the world; Alex's items read
   with the tool, the next step's reply naming his trident; item asks by slot (boots worn and
   boots carried; a helmet he does not have); an effect the engine keeps on Steve (Speed II, the
   instance in `vfg engine instances`), changed to Speed V and stopped by the engine's actions;
   Steve's second line waiting for his loop (`turn #… Steve waits for their turn`) while Alex's
   runs beside it; three of Steve's lines waiting and a fourth turned away, never answered, by a
   private message naming the three; a conversational line for Respond; and one hand-off on the
   implementers' default, DeepSeek V4.1 Flash through OpenRouter, with no test server
   (`VFG_TEST_BOX` unset): its task's name (no action's or library class's), its `AI task` row,
   its brief in `vfg.db` under its five headings with its done-when items, the turn ended as the
   implementer started, its report starting a new loop (one chat line, one `AI result` row,
   nothing the implementer wrote in the feed), its row's `made`; then a follow-up by its name that
   resumes the session, and a stop by its name of what of its classes the engine runs, the task
   still `done`.
2. **`VFG_LOOP_STEPS=2`, `VFG_IMPLEMENTER_MINUTES=1`.** A loop that reaches its step limit and
   ends with a reply or a hand-off; and a report going ahead of Steve's waiting lines
   (`turn report … waits for Steve's turn, first in line`).
3. **`VFG_LOOP_SECONDS=2`, `VFG_IMPLEMENTER_MINUTES=1`.** A loop that reaches its time limit the
   same way.

An everyday ask (a give, a teleport, the time, a summon) is one of the library's scenarios started
with `engine_start`: one the library lacks fails its check in the world, with the brain's own
account in the check's evidence. At the end: every line read, no task open, the brain and the
engine stopped cleanly, no traceback or error in `brain.log`, the key in no file, every step a
usage row `<ref> s<n>` and a line of `brain.log`, no orchestrator or planner call, and the brain's
and Respond's cost under $1. It prints each line's time to its reply with its loop's steps, cost,
cache rate and ending, the implementer tasks' times and every call's cost. `--no-implementer`
leaves out the two checks that start an implementer; `--only` runs the checks it names
(`one_step`, `together`, `in_turn`, `refused`, `read`, `items`, `kept_effect`, `two_players`,
`waiting`, `talk`, `implementer`, `step_limit`, `report_first`, `time_limit`), each with the brain it
needs. It copies a complete `refs/26.3/` from another server's stand-in when one has it, and leaves
the server down unless `--keep-up`.

## Jev's gather call live: `jev_live.py`

```sh
REALMC_ROOT=… VFG_ACCEPT_EULA=true python3 integration/jev_live.py [--name jr] [--keep-up] [--no-brain]
```

The Jev routing spec's live test: an ask said in two pieces arrives at the brain as one group, and
another player's lines never join it. The engine and its library run as for `brain_live.py`, the
real bridge against them, and the real Jev filter calling the TypeSafe API with the key in the
stand-in's `.env` (`REALMC_ROOT`'s `shared/env`, which only the filter reads), reading the engine's
export and reaching the bridge as on the box; the brain is `vfg brain start`, OpenRouter. Each line
is posted to the bridge as the mod posts it, a second or two after the one before. Three
scenarios, each an ask in two pieces with the other player saying something about the same thing
between them: "the cave over there is pitch black" … "AI, give me something so I can see in
there"; a broken pickaxe and "can you give me a new one"; an ocean monument and "I need to be able
to breathe down there". Per scenario it checks the feed has one `LINE` for the ask, under the ask's
fields and time, its text both pieces in order; the gather call never asked about the other
player's line, and no group holds it; the brain's turn is for that line (its usage rows, Respond's
`#seq` and its loop's steps' `#seq s<n>`; `brain.log`'s Respond line with the group's words and its
`loop` line for the asker; the task it opened, whose line is the group), and none is for the first
piece alone; and, a bonus not counted, what the brain did is in the world (night vision or
torches, a diamond pickaxe, water breathing). Jev is not deterministic: it prints every line's
verdict with D2's two answers, each gather answer with its confidence, the time from the end of the
ask to Jev's verdict, to the feed and to the brain's reply, the gather call's ms and each model
call's, and the money; a scenario whose first piece Jev sent to the AI on its own, or whose ask it
did not send, is Jev's routing, not the gather's: reported and skipped. The run fails on any failed
check, or when every scenario was skipped. Each run starts a new transcript, as a new box: the last
run's `vfg.db`, feed, lookout, menu and logs move to `vfg/runs/<time>/`. `--no-brain` checks Jev
alone (the groups on the feed, no OpenRouter), setting the lookout and menu the brain would. A run
spends about $0.002 of TypeSafe (9 routing calls, 3 gather calls).

## The loop replay: `loop_replay.py`

The 2026-10-11 session's 110 brain calls, each one loop of today's brain, on a realmc server of its
own with the engine and its library, the implementers recording each brief and starting nothing:
[replay/README.md](replay/README.md#the-loop-replay-2026-10-11-through-the-brains-loop). What that
night's implementers made (script actions, and incantations in the store before the engine's) is
not put back, and each such change is recorded as not done at its time. `--dry` runs the dataset,
every context and the report with no model and no server.

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): `run.sh` checks §2–§9 end to end, and §12's start steps.
