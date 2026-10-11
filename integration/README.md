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
around it, several side by side and Carpet's fake players: [realmc/](realmc/README.md), and the
brain live on it, [`brain_live.py`](#the-brain-live-on-realmc).

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
| 5 | `vfg feed post AI` for each kind, its field a task's name or `-`; `GAME` of any kind, `SYS`; `ORCH`, a task number and bad `AI` fields refused; `vfg context`, `--json` (the rows, then `{tasks}`, then `{last_seq}`) and `vfg context mark` |
| 6 | `usage` rows: one per Jev call; `vfg usage add` as the brain, Respond, the orchestrator and an implementer write them, cached tokens included, the ref a `#seq` or a task's name |
| 7 | killing and restarting Jev, the brain's watch (`--after` its last seq) and the bridge loses and repeats nothing, and keeps the processed marks |
| 8 | the client mod's real `BridgeClient` and `LinePayload` against the real bridge |
| 9 | every line the real feed writer produced, read by the brain's `context.parse`, and `vfg context --json` built into the brain's `Context` |
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

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): it checks §2–§9 end to end, and §12's start steps.
