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
| 2 | typed chat in Jev's four classes and watched game events reach `vfg watch`; `not_relevant` is stored, not fed; a `conversational` line flushes the waiting context at once |
| 3 | `vfg say` writes an `ai` line Jev never judges and an `AI say` feed row in the same call, `AI say castle_steve` for `--task castle_steve`; Jev's next state holds the AI's lines, the brain's `action`, `task` and `result` rows (not its notes), the lookout and `menu.md` |
| 4 | `vfg history` by date, source, player, each of the four classes, events and feed |
| 5 | `vfg feed post AI` for each kind, its field a task's name or `-`, each row written processed; `GAME` of any kind, `SYS`; `ORCH`, a task number and bad `AI` fields refused; `vfg context`, `--json` (the rows, then `{tasks}`, then `{last_seq}`) and `vfg context mark` on the rows the brain reads |
| 6 | `usage` rows: one per Jev call; `vfg usage add` as an implementer writes them, cached tokens included, the ref a `#seq` or a task's name; `structured()`'s queued rows (the planner's, the orchestrator's for a line's `#seq` or a report's task, an alias call's), a failed call's among them (its cost NULL when an attempt was cut off in flight), sent by `vfg usage flush` with `ok`, `error` and `ms` |
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
OpenRouter: `OPEN_ROUTER_API` from the environment, else from the first `.env` above this repo,
or its `Internal/.env`, that holds it; given to the brain's process only, never printed or
written. Each player's line goes into the feed as a `LINE` row through the box's `vfg_feed`, as
Jev would put it there. It checks an action ask done in the world with one reply and no task (its
actions `AI action -` rows, its reply `AI say -`, the brain's own copy `command_steve` with no
task), an item ask on the right slot, a conversational line answered by Respond, a conversational
ask handed up and acted on, two lines in the same second both answered (two `AI say -` rows, each
player's copy named for them) with neither waiting, the rows a call read marked processed, a
context line marked read at once with no call and a game event read by the next call, and
`vfg brain stop`, with no row in `vfg.db`'s tasks table: none of its lines goes to the
orchestrator. Then it prints each line's time to its reply, and what the run cost. About a minute and eight model calls, about $0.09: the first call, its 22K-token opening not yet cached, is about $0.06 of it.
Not part of any test suite.

## The brain live, on realmc

```sh
REALMC_ROOT=… python3 integration/brain_live.py [--name brain] [--keep-up] [--no-implementer]
REALMC_ROOT=… python3 integration/brain_live.py --name named --keep-up     # a build's own server
```

The brain on a real 26.3 server (realmc, its world fresh from the template, with Carpet's fake
players Steve and Alex), every ask checked in the world over RCON, in the feed's `AI` rows and in
`vfg.db`'s tasks table. It starts from a clean slate: the stand-in's `vfg.db` (with its `-wal` and
`-shm`) and `feed.log` set aside in `vfg/set-aside-<time>/`, never deleted, and made again by
`vfg_bridge --init-db`; `made/<name>/` saved with `vfg made save`, then moved into
`made/.replaced/`. Then: an action ask, with no task (`AI action -` rows, the brain's own copy
`command_steve`); item asks (boots worn and boots carried, a sword held and one carried, a helmet
he does not have); the brain's own copy `keep_effects_steve` started, then edited (an
`orchestrator` call, Speed V, an `AI note - | edited keep_effects_steve: …` row) and stopped by the
orchestrator; two players asking in the same second (two `AI say -` rows); a conversational line
for Respond; one implementer task end to end, on the Claude subscription (Haiku 5.5): its name
from the orchestrator, its row (player, session, the description its report's `DESCRIPTION:` line
gave, `made` and `variables`), its script, action and copy all under that name, its implementer's
usage rows under it too; a follow-up by its name that resumes the session and leaves a copy
running with `VFG_TASK=<name>`; a stop of that copy by its name, the task still `done`; each
report one chat line and one `AI result` row, with nothing the implementer wrote in the feed. It
prints each line's time to its reply with every model call, the orchestrator's among them, the
implementer's times, every call's cost from the `usage` rows and the cache hit rate. About 6 to 7
minutes: the brain, Respond and the orchestrator about $0.25 of OpenRouter, the implementer about
$0.3 of the subscription's usage; `--no-implementer` leaves the task, its follow-up and its stop
out. It copies a complete `refs/26.3/` from another server's stand-in when one has it, and leaves
the server down unless `--keep-up`. See [realmc/README.md](realmc/README.md) for the server and
its owner's rules.

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
player's line, and no group holds it; the brain's call is for that line (its usage row, its
`brain.log` line, the task it opened, whose line is the group), and none is for the first piece
alone; and, a bonus not counted, what the brain did is in the world (night vision or torches, a
diamond pickaxe, water breathing). Jev is not deterministic: it prints every line's verdict with
D2's two answers, each gather answer with its confidence, the time from the end of the ask to
Jev's verdict, to the feed and to the brain's reply, the gather call's and the brain call's ms,
and the money. A scenario whose first piece Jev sent to the AI on its own, or whose ask it did
not send, is Jev's routing, not the gather's: it is reported and skipped. The run fails on any
failed check, or when every scenario was skipped. Each run starts a new transcript, as a new box: the last run's `vfg.db`, feed,
lookout, menu and logs move to `vfg/runs/<time>/`. `--no-brain` checks Jev alone (the groups on
the feed, no OpenRouter), setting the lookout and menu the brain would.

About 1.5 minutes. On 2026-10-11, three runs with the brain passed every check and the three
bonus: Jev's verdict 0.2–0.4 s after the end of the ask, the gather call 180–250 ms, the line fed
0.4–0.6 s after, the brain's reply 5.7–8.7 s after (its call 2.3–3.3 s). A run costs about $0.002
of TypeSafe (9 routing calls, 3 gather calls) and $0.014 of OpenRouter, or $0.075 when the brain's
opening is not cached yet. In eight runs, five of them `--no-brain`, the gather call said yes to
every first piece and no to every other line it was asked about; in the seven that printed its
confidence, the yes was 0.42–0.58 for the cave and 0.54–0.76 for the monument, against 0.90–0.96
for the pickaxe, and every no 1.00.

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): it checks §2–§9 end to end, and §12's start steps.
