# engine — the game engine, one loop beside Minecraft

One process on the box runs every item, ability, mark, actor and scenario as live Python classes,
in one loop, and is the only thing that writes their effects to Minecraft (the game engine spec:
`Internal/workspace/pages/home/children/Specs/children/2026-10-12-Prototype_Game_Engine/`).
The server stays vanilla 26.3: the engine's senses are a datapack, and it writes over RCON.

- **How to write content** (the classes, their configs, `ctx` and `self`): [WRITING.md](WRITING.md).
- **The datapack** (the event queue, the raycast, what each event carries): [datapack/README.md](datapack/README.md).
- **This file**: the process, its control socket and `ctl`, its files and settings, how it is
  deployed and tested.

```text
engine/
  vfg_engine/            the package: the core, mc/ (Minecraft), and the process:
    __main__.py          python -m vfg_engine: settings, lock, socket, loop, signals, logs, exit codes
    server.py            the control socket
    ctl.py               python -m vfg_engine.ctl: one request from the shell
    livetest.py          python -m vfg_engine.livetest: one test of content, on the test server
  datapack/everhear/     the datapack, copied into the world
  library/               the server's base content, shipped with the engine (the library port's)
  examples/              the spec's Fire Staff, Fireball and two marks, as working content
  deploy/                vfg-engine.service, requirements.txt (hashed), install.sh
  tests/core, mc, box    the core on FakeIo; against a real 26.3 server; the process and socket
```

## Run it

On the box it is the systemd unit `vfg-engine`, as `ec2-user`, after `minecraft`:
`PYTHONPATH=/opt/vfg/engine /opt/vfg/engine/.venv/bin/python -m vfg_engine`, with
`/opt/vfg/.env` in its environment. The engine has **its own venv**, on the box's Python 3.12, with
pydantic pinned by hash; the bridge and Jev stay standard library only.

It takes the engine's lock (`engine.sock.lock`: two engines on one world would both write to it),
makes the control socket, starts the engine (connects to RCON, checks the datapack, rebuilds every
instance from the saved state) and runs the loop on the main thread, which the loop needs for the
timer signal that cuts a slow call. The socket serves on a thread of its own. **SIGTERM or SIGINT
stops it cleanly**: the loop ends on its next tick, the socket answers whatever still waits with
`stopping`, and the engine closes, saving its state.

| Exit | Means | systemd |
|---|---|---|
| `0` | stopped by a signal, state saved | — |
| `1` | crashed: an error escaped the loop, it could not start (Minecraft not up yet), or another engine holds the lock | started again in 5 s (`Restart=on-failure`) |
| `78` | a setting is wrong, or the socket or the log cannot be made where asked | left stopped (`RestartPreventExitStatus=78`): fix `/opt/vfg/.env` |

**Logs** go to `VFG_ENGINE_LOG` as JSON lines, one object a line — `at`, `level`, `logger`, `msg`,
any fields the call gave, and `exc` with the traceback — kept to 4 files of 20 MB; and to stderr as
plain lines, for `journalctl -u vfg-engine`. Every control request is one line with its `op`, `ok`,
`ms` and, when refused, `error` and `reason`. The `log` op answers the engine's own recent lines.

On a PC with no Minecraft, `python -m vfg_engine --fake-io` runs the engine on the testing FakeIo,
for trying content and the socket:

```sh
cd engine && VFG_ENGINE_SOCKET=/tmp/e.sock VFG_ENGINE_LOG=/tmp/e.log VFG_ENGINE_STATE=/tmp/e.json \
  VFG_ENGINE_EXPORT=/tmp/e-inc.json VFG_ENGINE_CONTENT=$PWD/examples uv run python -m vfg_engine --fake-io
echo '{"op": "classes"}' | VFG_ENGINE_SOCKET=/tmp/e.sock uv run python -m vfg_engine.ctl
```

## Files on the box

| Path | What |
|---|---|
| `/opt/vfg/engine/vfg_engine/` | the package (`deploy.sh` rsyncs `engine/vfg_engine/`) |
| `/opt/vfg/engine/library/` | the library: the server's base content, the first content folder |
| `/opt/vfg/engine/examples/` | the example content, deployed but loaded only if `VFG_ENGINE_CONTENT` names it |
| `/opt/vfg/engine/.venv/` | the venv: Python 3.12 (`/opt/vfg/python`), pydantic and its four dependencies |
| `/opt/vfg/engine/state.json` | the saved state: every instance, binding and world-setting hold |
| `/opt/vfg/engine/incantations.json` | the incantation export the bridge reads, written whole on every change |
| `/opt/vfg/engine/engine.sock` | the control socket, mode 600; `engine.sock.lock` beside it holds the pid |
| `/opt/vfg/engine/engine.log` | the log, JSON lines |
| `/opt/vfg/made/<server>/content/` | content classes, one file each; vfg deploys `made/<server>/content/` there |
| `/opt/mc/world/datapacks/everhear/` | the datapack |

## Settings

From the environment, which the unit fills from `/opt/vfg/.env` (names in `bridge/deploy/env.example`):

| Key | Default |
|---|---|
| `VFG_ENGINE_TICK_HZ` | `10` ticks a second |
| `VFG_ENGINE_SLOW_MS`, `VFG_ENGINE_CUT_MS` | `5`: a content call over it is logged slow; `50`: cut |
| `VFG_ENGINE_CONTENT` | content folders, colon-separated; the unit's default `/opt/vfg/engine/library:/opt/vfg/made/main/content`, the library first (an `.env` value wins) |
| `VFG_ENGINE_STATE` | `/opt/vfg/engine/state.json` |
| `VFG_ENGINE_EXPORT` | `/opt/vfg/engine/incantations.json` (the bridge reads the same key) |
| `VFG_ENGINE_SOCKET` | `/opt/vfg/engine/engine.sock` (the bridge and `ctl` read the same key); at most 107 bytes |
| `VFG_ENGINE_LOG` | `/opt/vfg/engine/engine.log` |
| `VFG_ENGINE_STALE_S` | `5`: a use, hit, hurt or kill older than this when it is read is not acted on (`vfg_engine/intake.py`) |
| `VFG_ENGINE_RAY_BLOCKS`, `VFG_ENGINE_MIRROR_RADIUS` | `48`: each player's raycast; `32`: the mirror's reach round each player ([MINECRAFT.md](MINECRAFT.md)) |
| `VFG_ENGINE_MIRROR_S`, `VFG_ENGINE_MIRROR_BUDGET` | `0.1` s between mirror passes; `0.1`: slower when a pass would take more of the server's time |
| `VFG_MC_PROPERTIES` | `/opt/mc/server.properties`: the RCON port and password, never printed |

## The control socket

**One connection carries one request: one line of JSON in, one line of JSON out**, then the engine
closes the connection. Simple enough that any standard-library client speaks it in a few lines; the
bridge has its own (`bridge/vfg_bridge/engine.py`) and so does the toolkit's remote program.

In, at most 1 MiB, ending in a newline:

```json
{"op": "give", "args": {"player": "Steve", "item": "fire_staff"}, "timeout_s": 10}
```

- `op`, required: one of the ops below.
- `args`, an object; left out is `{}`.
- `timeout_s`, how long the socket waits for the engine: by default 10 s, `reload` 120 s; at most
  600. Each request is handled on the engine's next tick, in order with its other inputs, so a
  simple one answers within about one tick (0.1 s).

Out: the engine's reply, `{"ok": true, ...}` or `{"ok": false, "error": "..."}`. **A reply always
comes back.** One the socket made itself, rather than the engine, also says `reason`:

| `reason` | When | Carried out? |
|---|---|---|
| `bad_request` | the line was not a request: not JSON, not an object, no `op`, `args` not an object, over 1 MiB, nothing sent within 10 s | no |
| `timeout` | the engine did not answer within `timeout_s` | no when `cancelled` is true: it still waited in the engine's queue, which skips a cancelled request; maybe when false |
| `stopping` | the engine is stopping | no (`cancelled` true), or maybe if it had begun |
| `failed` | the engine raised instead of answering, or answered with something that is not a reply | the error says |

A value JSON cannot hold goes as its text, so the reply still goes. A Python client, whole:

```python
import json, socket
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
    s.settimeout(12)
    s.connect("/opt/vfg/engine/engine.sock")
    s.sendall(b'{"op": "status"}\n')
    reply = json.loads(s.makefile("rb").readline())
```

The ops are the core's (DESIGN, THE CONTROL OPS); every reply has `ok`, and `error` when it is false:

| op | args | reply |
|---|---|---|
| `status` | — | `tick`, `tick_hz`, `tick_ms` (p50/p95), `players`, `instances`, `classes`, `slow`, `started_at` |
| `reload` | `paths?` (files; none: every content folder) | `loaded`, `errors: [{file, check, error}]`, `switched`, `stopped` |
| `classes` | — | `classes: [{id, kind, class, file, summary, args?}]`: a Scenario's or Mark's `args` each `{name, type, default, bounds, description}` |
| `meta` | `id?` | `rows`: required fields, damage at power 0, 10 and 20, the class's own fields, and its `args` |
| `world` | — | `settings: [{name, value, original, holds}]` |
| `instances` | `kind?`, `user?` | `instances: [{instance, kind, id, user, started, ends?}]` |
| `grant`, `revoke` | `who`, `ability` | — |
| `give`, `take` | `player`, `item`, `base?` | — |
| `apply`, `clear` | `who`, `mark`, `seconds?`, `args?` (apply) | `instance`; `cleared` |
| `start` | `scenario`, `args?`, `wait_s?` (0 to 30) | `instance`; with `wait_s`, `ended` and `result` (`{summary, facts}`) once it ends, `running: true` and its result once it reports one, or `running: true`, `note: "still running"` when the wait runs out |
| `stop` | `instance` or `scenario` (every run of it) | `stopped` |
| `spawn` | `actor`, `at?` or `near?` | `uuid` |
| `bind`, `unbind` | `player`, `phrase`, and `ability`, or `scenario` with `args` (`"caster"` for a Player or Place arg) and `cooldown_s?`; `aliases?`, `everyday_aliases?`, `by?` | `id`, `version`, `entry` |
| `places` | — | `version`, `world`, `places: {name: {dim, pos, yaw, by, at}}` (this world's) |
| `places_import` | `places`: a list of `{name, dim, pos (or x y z), yaw?, by?}`, or a dict by name; `force?` | `imported`, `skipped` (a name already saved), `refused` (with why), `version`, `world`: once per world unless `force` |
| `incantations` | `player?` | `version`, `incantations` |
| `cast` | `player`, `phrase`, `cast_id` | `status`: `fired`, `charging`, `cooldown` (`remaining_s`), `not_granted`, `unknown`, `failed` |
| `config_get` | `class` | `file`, `start`, `end`, `source` |
| `log` | `n?` | `lines` |

## ctl: one request from the shell

```sh
echo '{"op": "status"}' | PYTHONPATH=/opt/vfg/engine /opt/vfg/engine/.venv/bin/python -m vfg_engine.ctl
echo '{"op": "reload"}' | … -m vfg_engine.ctl --timeout 60
```

It reads one request as JSON on stdin (one line or pretty), sends it to `VFG_ENGINE_SOCKET` (or
`--socket`), and prints the reply as one line of JSON. `--timeout` sets `timeout_s`; ctl itself
waits 2 s longer, so the engine's own answer comes back. Standard library only: it imports nothing of
the core, so it answers even when the core would not import.

| Exit | Means | stdout's `reason` |
|---|---|---|
| `0` | `ok` true | — |
| `2` | refused: by the engine, or the request on stdin is not one | the engine's, or `bad_request` |
| `3` | the engine is not running: no socket, nobody listening, it closed without a reply, or it is stopping | `not_running`, `stopping` |
| `4` | timeout | `timeout` |

Whatever happens, stdout is one JSON object with `ok`.

## Deploy

From the repo's root on the owner's PC (`bridge/README.md` has the rest of the box):

```sh
bridge/deploy/deploy.sh provision   # once: Python 3.12 and uv in /opt/vfg, /opt/vfg/engine, made/main/content
bridge/deploy/deploy.sh             # the bridge and the engine; restarts all three units
bridge/deploy/deploy.sh engine      # the engine alone; restarts vfg-engine only
```

`deploy.sh engine` (and `deploy.sh`, which does the same in its course):

1. rsyncs `engine/vfg_engine/`, `engine/library/` and `engine/examples/` to `/opt/vfg/engine/`, `engine/datapack/everhear/`
   to `/opt/vfg/engine/datapack/`, and `install.sh` and `requirements.txt` to `/opt/vfg/engine/deploy/`;
2. runs `install.sh` there as `ec2-user`: the venv is made when it is missing or the box's Python
   changed under it (`uv venv`, the box's uv), then `uv pip sync --require-hashes --no-build` makes it
   exactly `requirements.txt`; the datapack is copied over `/opt/mc/world/datapacks/everhear/` only
   when the two differ, and then loaded with `/reload` over RCON (`/opt/mc/rcon.py`, under the box's
   RCON lock), and enabled if the world had it disabled; with Minecraft down it loads when the
   server starts;
3. installs `vfg-engine.service`, enables it and restarts it.

`requirements.txt` is made from `requirements.in` (pydantic 2.14.0 and its dependencies at
`brain/uv.lock`'s versions), with every wheel's hash:
`cd engine/deploy && uv pip compile requirements.in --generate-hashes --python-version 3.12 --universal -o requirements.txt`.

`ec2.sh stop` and `retire` stop `vfg-engine` first, so it saves its state while Minecraft still
answers; `deploy.sh env` restarts it with the others.

## The bridge's side

The bridge reads the export and casts through the socket; nothing else of the engine. Every
incantation is the engine's; the bridge keeps no cooldown of its own: [bridge/README.md](../bridge/README.md),
*Incantations*.

## Tests of content: livetest

`python -m vfg_engine.livetest` runs **one test of content against one engine started for it**, on
the test server (Prototype_Implementer_Quality's: `/opt/mc-test`, a fresh world each test, in a
sandbox), and prints one JSON result. `vfg test` calls it through the test server's remote program;
a builder runs it on a realmc server the same way. Its module docstring is the test file's guide.

```sh
python -m vfg_engine.livetest --properties /opt/mc-test/server.properties --state-dir /opt/vfg-test/state/engine \
    --content /opt/vfg/engine/library --content /opt/vfg-test/made/main/content \
    --test /opt/vfg-test/tests/fire_staff_steve.py [--bindings FILE] [--boot 60] [--timeout S] \
    [--caster Steve] [--others Alex]
```

1. **Only a test server.** The folder holding `--properties` must have the `.vfg-test-server` marker
   (only the test server's setup writes it), and a marker that names `rcon_port` must name this
   server's: else exit 3, nothing started. The engine it starts runs with `VFG_TEST=1`, under which
   `python -m vfg_engine` refuses the same way (exit 78), so neither talks to the live server.
2. **The test file** must define `test(t)`; one that does not load is exit 2, nothing started.
3. **One engine for the test**: `python -m vfg_engine` (the runner's own Python and package) with
   `VFG_MC_PROPERTIES` the test server's, `VFG_ENGINE_CONTENT` the `--content` folders joined, and its
   state, export, log and output (`engine.out`) in `--state-dir`, which is emptied of an earlier
   test's first; its socket there too, or in a folder of its own under `/tmp` when that path is
   longer than a Unix socket's (107 bytes). It dies with the runner (`PR_SET_PDEATHSIG`). Its socket
   must answer within `--boot` s (60), else exit 4 with its last words; then a `reload` gives the
   classes loaded and the load errors, printed as `ENGINE` lines; `--bindings` (a JSON list of `bind`
   args: `player`, `phrase`, `ability` or `scenario` and `args`, `aliases`) are bound.
4. **The stage**: a barrier floor 81 x 81 high in the sky at 160 296 160 (inside the chunks a new
   world generates round its spawn, so a fresh world's first test does not wait for chunks to be
   made: 0.4 s of setup, 1.1 s on a fresh world, against 3.9 s at 3000 3000), forceloaded, night,
   clear, no mobs spawning; Steve (the caster, facing north) and Alex, 4 to his left, Carpet fake
   players the engine sees before `test(t)` is called.
5. **The test runs**, then the engine is stopped (SIGTERM: its state saved; its exit code reported),
   the stage cleared (its players, its mobs, every block round it, the forceload).

Its stdout is the report (`ENGINE`, `AT`, `CAST`, `START`, then `PASS` / `FAIL` / `CAN'T TELL — item
— why`, `RESULT: n passed, n failed, n can't tell`, `TIME` with the wall time and each phase's), then,
last, one JSON line:

```json
{"code": 0, "passed": 6, "failed": 0, "cant_tell": 0, "checks": [{"item": "...", "ok": true, "why": "..."}],
 "engine": {"loaded": ["test_zap"], "errors": [], "boot_s": 0.6, "exit": 0}, "seconds": 4.6,
 "timing": {"boot": 1.4, "stage": 0.4, "test": 2.1, "clear": 0.7}, "slow": false}
```

with `"error"` in words when it did not run to the end, and `"stopped": true` after SIGTERM (or
`--timeout`). **Every live test is short and self-contained: under 20 s, its setup included.** One
over `VFG_LIVETEST_BUDGET_S` (20) gets a `WARN` line and `"slow": true`; its code is unchanged. The
exit code is `"code"`:

| Code | Means |
|---|---|
| `0` | every check passed |
| `1` | a check failed |
| `2` | the test could not run as written: no `test(t)`, an error in it, an op the engine refused, a call made wrong, no check made |
| `3` | not a test server (no marker, or another server's) |
| `4` | the engine did not start, or did not answer within `--boot` |
| `5` | stopped: SIGTERM (the sandbox's limit), or `--timeout` |

**`t`, the test's API** (the module docstring has it in full, with examples): the engine's ops
(`t.give`, `t.take`, `t.grant`, `t.revoke`, `t.apply`, `t.clear`, `t.start(scenario, wait=0,
**args)`, `t.stop`, `t.spawn`, `t.bind`, `t.cast(phrase)` as the bridge sends a heard cast,
`t.instances`, `t.result(within=0)`, `t.op(name, **args)`); Carpet's presses (`t.use(hold=0, draw=0)`,
`t.sneak(times=3)` or `t.sneak(hold=s)` for one crouch held s seconds, `t.look`,
`t.hotbar`, `t.attack`, `t.jump`); the stage (`t.mob`, `t.blocks`, `t.region`, `t.ground`,
`t.player`, `t.stand_in`, `t.cmd`, `t.wait`, `t.watch`, `t.at`, `t.xyz`); and checks for
`t.check(item, verdict)`: `t.hurt`, `t.killed`, `t.unhurt`, `t.others_unhurt`, `t.burning`,
`t.moved`, `t.near`, `t.gone`, `t.turned`, `t.built`, `t.block`, `t.fire_near`, `t.effect`, `t.holds`,
`t.weather`, `t.running`, `t.said(text, within=5)` (the last start's result: its own answer, else the
engine's `result` op on its instance, asked for up to `within` s), `t.no_errors` (no error in the
engine's log since the test began), and `verdict(ok, why)`. A call made wrong says how it is called,
in one line. `t.unhurt` and `t.no_errors` say CAN'T TELL when nothing fired since the last action;
fired is a `t.start` (a one-shot that ended within its wait ran all the same), a new instance, more
things on the stage, blocks placed or broken there (a census: a masked `/clone` of the stage, a few
ms), or anyone there moved, hurt, set alight or given an effect.

**On a realmc server**: the server's folder needs the marker (a realmc test server has one; for a
plain realmc server, write `{"rcon_port": <its port>}` to `<NAME>/mc/.vfg-test-server` yourself, and
take it out after), the datapack installed (as below), then run it with the engine venv's Python:
`PYTHONPATH=engine engine/.venv/bin/python -m vfg_engine.livetest --properties $REALMC_DIR/mc/server.properties
--state-dir <a scratch folder> --content <folder> --test <file>`.

## Test it

```sh
cd engine && uv run --group dev python -m pytest tests/core -q      # the core, on FakeIo
cd engine && uv run --group dev python -m pytest tests/box -q       # the process, the socket, ctl, livetest
cd engine && uv run --group dev python -m pytest tests/mc -q        # against a realmc 26.3 server; skip without one
```

`tests/box` runs the socket and `ctl` against a fake engine, and the process (`main`) in a subprocess
on one: a reply on every error, the timeouts and the queue, the stop answering what waits, mode 600,
one engine at a time, SIGTERM and SIGINT with the state saved, the JSON log, and exit codes 0, 1 and 78;
then the real core as a process on FakeIo with `examples/`, through `ctl`, across a restart; and the
livetest runner: its refusals (no marker, the engine under `VFG_TEST=1` too), a test file it cannot
run, an engine that does not start, and whole runs (the real engine on FakeIo, a fake RCON) to PASS,
FAIL, a raising test and `--timeout`, the stage cleared after, the wall time against the budget; and `t`
on a fake engine: what counts as fired, the `result` op, a held crouch. With `LIVETEST_PROPERTIES`
naming a marked realmc server, two live tests too (`tests/box/livetest_case/`).

**On a realmc server** (`integration/realmc/README.md`), the engine runs as on the box, from the
stand-in's `vfg/engine/`: send the files there as `deploy.sh` does, run `install.sh` with the
stand-in's folders and this PC's uv (it needs `rcon.py` in the server's folder, as `provision.sh`
puts it on the box), then start the unit's command with the stand-in's paths. A Unix socket's path is
at most 107 bytes, which the stand-in's folders pass, so the socket goes in `$XDG_RUNTIME_DIR`,
linked from `$E/engine.sock`, where `vfg engine` looks (`toolkit/remote/engine.py` connects through
the folder when the path is longer than a socket's address holds). `integration/engine_e2e.py`
does all of it, then proves the engine end to end:

```sh
eval "$(integration/realmc/env NAME)"; BOX=$REALMC_DIR; E=$BOX/vfg/engine; mkdir -p $E/datapack $E/deploy
rsync -r --delete --exclude=__pycache__/ engine/vfg_engine engine/examples $E/
rsync -r --delete engine/datapack/everhear $E/datapack/
cp engine/deploy/install.sh engine/deploy/requirements.txt $E/deploy/; cp bridge/deploy/rcon.py $BOX/mc/
VFG_DIR=$BOX/vfg MC_DIR=$BOX/mc UV=$(command -v uv) bash $E/deploy/install.sh
VFG_MC_PROPERTIES=$BOX/mc/server.properties VFG_ENGINE_CONTENT=$E/examples VFG_ENGINE_STATE=$E/state.json \
  VFG_ENGINE_EXPORT=$E/incantations.json VFG_ENGINE_LOG=$E/engine.log \
  VFG_ENGINE_SOCKET=$XDG_RUNTIME_DIR/NAME.sock PYTHONPATH=$E $E/.venv/bin/python -m vfg_engine
ln -sfn $XDG_RUNTIME_DIR/NAME.sock $E/engine.sock   # then VFG_SERVER=NAME vfg engine status
```
