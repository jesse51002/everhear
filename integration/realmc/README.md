# realmc — real 26.3 servers on this PC, for testing everything end to end

A real Minecraft Java 26.3 server (the box's own `server.jar`), with a stand-in for the box's
`/opt/vfg` and `/opt/mc` around it, so `vfg cmd`, `vfg script …`, `vfg state`, every remote
program and every script run here, the remote side on the box's Python 3.12 at the box's path
(`<vfg>/python/bin/python3`, as `/opt/vfg/python/bin/python3` there), against the real server's RCON. No EC2. Several servers run side by side, one per name, each with its own
world, ports and stand-in, so builders testing at the same time never share one.

## Start one, point vfg at it, stop it

```sh
integration/realmc/up                    # the server "main"; prepares everything on first use
eval "$(integration/realmc/env)"         # this shell's vfg (and `import mc`) now reach it
vfg cmd 'time query day'                 # → Timeline minecraft:day is at 6000 tick(s)
vfg action run tnt_rain                  # its script deployed to the stand-in, run there on Python 3.12
integration/realmc/down                  # stops its scripts (their cleanup runs), then the server
```

`up` does nothing if the server already runs; `up --fresh` stops it and gives it a fresh copy of
the template world (a reflink on this btrfs disk, so instant), and `up --regen` makes the
template again from the seed first. `--vanilla` starts it without Fabric and Carpet.

`env` prints the exports: `PATH` gets `integration/realmc/bin` first, whose `vfg` runs this
repo's `toolkit/vfg` with `HOME` and `VFG_HOME` in the stand-in and the keys that choose the box
(`VFG_SSH*`, `VFG_REMOTE_*`, `VFG_REPO`) pinned, so nothing reads `~/.vfg` or `~/.ssh`. `HOME`
itself is not exported, so `uv`, `git` and the rest of the shell keep yours. It also exports
`VFG_MC_PROPERTIES`, so `PYTHONPATH=scripts/_lib python3 …` scripts reach the same server, and
`REALMC_NAME`, the default for every command below.

`vfg brain …` keeps your `HOME`: `vfg brain start` starts the brain, whose implementers' Claude
CLI finds its login under `~/.claude` (in the stand-in's `HOME` it says "Not logged in") and whose
`uv` its Python and cache under `~/.cache`. vfg's own state stays in the stand-in (the brain's log
and command references in `$VFG_HOME/brain`, or `VFG_BRAIN_DIR`; its tasks in the `tasks` table of
the stand-in's `vfg.db`, which `vfg task` reaches through `bin/ssh` as every box program does),
`bin/` goes first on the brain's `PATH`, and every `vfg` the brain and its implementers run comes
back through `bin/vfg`, to the stand-in. The brain's OpenRouter key goes in its environment
(`OPEN_ROUTER_API`), never in the stand-in.

```sh
eval "$(integration/realmc/env p1)"
OPEN_ROUTER_API=… VFG_SERVER=p1 vfg brain start   # "start pid …" in $VFG_HOME/brain/brain.log
vfg brain status
vfg brain stop
```

`VFG_SERVER` keeps what its implementers make in `made/p1/`. The brain's first start on a server
fetches the command references (about 95 s); a complete `refs/26.3/` from another server's
stand-in can be copied into `$VFG_HOME/brain/refs/` first, as `integration/brain_live.py` does.

## Several servers at once

```sh
integration/realmc/up p1 && integration/realmc/up p2     # or --name p1
eval "$(integration/realmc/env p1)"                       # this shell talks to p1
integration/realmc/realmc.py list                         # every server: running or not, ports
integration/realmc/realmc.py status p1 --json             # one server: ports, arena, mode
integration/realmc/down p1        # one;   down --all: every one
```

A name is `a-z 0-9 _ -`, at most 24. Take one per builder or per test run (`p1`, `p2`, …,
`smoke` is the smoke test's) and keep to it in that shell: after `eval "$(…/env p1)"`, `up`,
`down`, `status`, `cmd` and `players.py` without a name all mean `p1`. Each server has its own
game and RCON ports (free ones, on 127.0.0.1, picked when it is first made and checked at each
start), its own RCON password, its own world, `run/`, `vfg.db` and scripts. They share the jar,
its libraries, Carpet and the template world. Each takes up to 2 GB of heap (`REALMC_HEAP`) and
about 0.8 GB when idle, with view distance 10 and simulation distance 6; six ran side by side on
this PC (12 cores, 38 GB) with room to spare.

Measured on 2026-10-09, with five other servers and builders' tests loading the PC (load ~16):
a server whose world exists is ready (RCON answering) in 15–18 s; a new one, copied from the
template, in 15 s; `up --fresh` on a running one in 23 s, its stop included. The first `up` ever
makes the template: 50 s, 28 of them generating 784 chunks. On an idle PC the same start is about
5 s shorter.

## The world

One normal world, made once from the fixed seed `20261009` and kept as `.server/template/world`
(17 MB); every server's world starts as a copy of it. Making it took 50 s, once.

- **Normal, not flat.** Scripts look for ground, trees, water, caves and spawn things 40 to 160
  blocks away; a flat world would test them somewhere no player plays. A fixed seed makes the
  terrain the same every time (same version, same seed), and the template makes it the same
  without generating it again.
- **An arena for exact checks.** At the first dry, treeless spot near the origin the template
  has a flat grass floor 32 x 32, cleared 32 blocks up, with the world spawn on it: with this
  seed, the floor is x -16..15, z -16..15 at y 84, and a player stands at 0 85 0. Its centre is
  in `status --json` under `arena`; a fake player spawns there unless told otherwise.
- **Loaded with nobody on.** 26.3 has no spawn chunks, so the template forceloads the area 32
  blocks around the arena, and has every chunk within 224 blocks of the origin already
  generated, so view distance 10 around the arena only reads from disk.
- **Held still.** Noon and clear weather, held (`advance_time` and `advance_weather` false), and
  `respawn_radius 0`. A test that needs night sets it: `vfg cmd 'time set midnight'`.

`up --fresh` puts a server's world back to the template, and is how to undo what a test did.

## Fake players: Carpet

Vanilla has none. [Carpet](https://github.com/gnembon/fabric-carpet)'s `/player` makes real
server players: in `@a` and `list`, with a position and a rotation, holding items, sneaking,
walking, and a right-click with a carrot_on_a_stick counts in
`minecraft.used:minecraft.carrot_on_a_stick` as a person's does.

```sh
integration/realmc/realmc.py carpet     # once: fabric-carpet-26.3+v260915.jar from the v26.3
                                        # GitHub release, checked against its sha256 (done 2026-10-09)
integration/realmc/up p1 --fresh        # a server loads it only when it starts: restart one
                                        # that was running before (down then up keeps its world)
```

`list` and `status` say `carpet` for a server running it.

The server then runs on Fabric Loader 0.19.5, the loader Fabric lists for 26.3, from jars
already on this PC (a Minecraft launcher, another server, the Gradle cache), each checked
against Maven's sha1; nothing else is fetched. Carpet with its default rules plays as vanilla.

`players.py` drives them, from Python or the shell, Steve and Alex only:

```python
import sys; sys.path.insert(0, "integration/realmc")
import players
p = players.on("p1")                  # or players.on(): the server env named
p.spawn("Steve")                      # on the arena, in survival; returns (x, y, z)
p.give("Steve", "carrot_on_a_stick[custom_name='Spirit Bomb']")
p.look("Steve", 90, -10)              # yaw, pitch
p.use("Steve")                        # one right-click; hold() holds it down as a person does; stop
p.sneak("Steve"); p.move("Steve", "forward"); p.move("Steve", None)
p.pos("Steve"); p.rotation("Steve")
p.kill("Steve")                       # it leaves; kill_all() for every one
```

```sh
python3 integration/realmc/players.py --name p1 spawn Steve
python3 integration/realmc/players.py --name p1 use Steve continuous
```

Without Carpet every call raises `NoCarpet`, saying how to get it.

What Carpet 26.3 does that a test must allow for, all seen on this server:

- **`/player X spawn` from RCON makes a creative player** unless it ends `in survival`; it
  answers nothing, and the player appears a moment later. `players.spawn` adds `in survival`
  (the box's players are) and waits until `list` has him.
- **`/player X look <yaw> <pitch>` swaps the two** (`look 0 45` gives yaw 45, pitch 0).
  `players.look` uses vanilla `rotate X <yaw> <pitch>` instead; `look_at` (Carpet's
  `look at x y z`) is right. Walking follows whichever was set.
- **`use continuous` fires every tick**, 20 uses a second; a person holding right-click makes
  5. `players.hold` is `use interval 4`, the person's rate (1 s → +5 on `vfg_click`); a single
  `use` or `stop` ends either.
- A use with a carrot_on_a_stick adds exactly 1 to `minecraft.used:minecraft.carrot_on_a_stick`,
  so the kit's `vfg_click` path works as for a person: one use → 1, a 1 s hold → 5, and a click
  with a different carrot_on_a_stick in hand is not counted for the item.
- `sneak`/`unsneak`, `move forward` (about 4.7 blocks a second) and `move` to stop, `jump`,
  `drop` (the selected slot's item, onto the ground), `hotbar N`, `attack`, `sprint` all work;
  `kill` takes him off the server (he leaves; he does not die).
- `vfg state` shows a fake player as `online    Steve 0 85 0 overworld`.

## The test server beside it, as the box's

The box runs a second, small server beside the live one for implementers' tests (`vfg test`):
`/opt/mc-test` and `/opt/vfg-test`, unit `minecraft-test`, a fresh world at every start
(`bridge/deploy/testserver.sh`, which `deploy.sh testserver` and `provision.sh` run). realmc lays
out the same thing beside a realmc server, with the same code, so the PC runs the production path:

```sh
integration/realmc/up iq1 --test-server        # the server iq1, then its test server
integration/realmc/realmc.py testserver iq1    # the same for one already up; --fresh: a new template
integration/realmc/realmc.py testserver iq1 --stop
integration/realmc/down iq1                    # stops the test server too
```

- **The same setup.** `bridge/deploy/testserver.py` runs on the stand-in's Python 3.12
  (`<NAME>/vfg/python/bin/python3`, as `/opt/vfg/python/bin/python3` on the box) with the live
  folders `<NAME>/mc` and `<NAME>/vfg` and the test ones `<NAME>/mc-test` and `<NAME>/vfg-test`
  (the contract's "-test" rule), its game and RCON ports free ones on 127.0.0.1 (kept while they
  stay free). It reads the live folders and never writes them. Fabric and Carpet come from
  `shared/`, checked against the pins in `testserver.py`, the one place they are kept (realmc.py
  imports them); on the box the same jars are fetched from their official URLs and checked the same.
- **What it makes:** the marker `mc-test/.vfg-test-server` (JSON; the live folder never has it), a
  copy of the live `server.jar` (sha256-equal), `launch.args` (the unit's java line), its own
  `server.properties` (the live settings without secrets or ports, then the test overrides, its own
  RCON password), `eula.txt` only when the live one says `eula=true`, `reset.sh`, and the template
  world `world.template/`: seed `20261009`, the 26 x 26 chunks around the kit's sky stage (x 3000,
  z 3000) generated, the stage's 97 x 97 blocks forceloaded, the world spawn on it. Made once (27 s
  here, 14 of them generating 676 chunks), again with `--fresh` or when the live jar changes.
  `vfg-test/` gets `python` (a link to the live one's), `scripts/`, `run/`, `tests/`, `state/`,
  `made/`, an empty store `state/incantations.json` with its lock, `protected.json` (`[]`) and no
  `.env`.
- **One user here.** On the box a sandboxed test runs as the system user `vfgtest`, not as the live
  services' `ec2-user` (`testserver.sh` makes it and passes `--test-user vfgtest`; the marker says
  `"test_user"`): it can write only `vfg-test/run` and `vfg-test/state`, reads its RCON password
  from `server.properties` (`640`, its group), and its traffic is refused everywhere but the test
  server's RCON on loopback. realmc has no root to make a user or a firewall rule, so its test
  server is laid out without `--test-user` and a test runs as you, the weaker user-mode sandbox.
- **The unit.** `<NAME>/box/units/minecraft-test.exec` makes the fake systemctl's `minecraft-test`
  a real one: `start`, `stop`, `restart [--no-block]`, `is-active`, `show -p LoadState`, `reset-failed`
  and `journalctl -u minecraft-test` do on this PC what the box's unit does. A supervisor
  (`realmc.py unit NAME minecraft-test run`) runs `reset.sh` (a fresh copy of the template, the
  live world's datapacks copied in, the token `world/vfg-fresh`), then `java -Xms256M
  -Xmx<MC_TEST_HEAP> @launch.args` at nice 10, the console appended to `mc-test/console.log` and
  the unit's `Server stopped` line after it, and starts it again 5 s after it ends unless it was
  stopped (`Restart=always`). It reads the heap and those settings from
  `bridge/deploy/systemd/minecraft-test.service`, and runs java in a `systemd-run --user --scope`
  with the unit's `CPUQuota`, `CPUWeight` and `MemoryMax` where your user manager gives one (the
  journal line says so). `restart` returns once `reset.sh` ran and java started, as systemd's does;
  `--no-block` at once.
- **Measured** (2026-10-13, this PC at load 8–15 from other builders; java as the unit runs it, at
  nice 10, with `-XX:ActiveProcessorCount=4` as the box's 4 vCPU): the template 27 s once; a restart
  to RCON answering 10–21 s; `reset.sh` 12–35 ms (a reflink here; a plain copy of the 17 MB template
  took 33 ms with its sync); a `/stop` from inside back by itself, fresh, in 15–27 s.

  | `-Xmx` | start → RCON | RSS idle | idle CPU | RSS peak, test load | heap used, heavy load | RSS peak, heavy load |
  |---|---|---|---|---|---|---|
  | 1G | 11.5 s | 598 MB | 3.0% of a core | 923 MB | 807 of 929 MB committed | 1322 MB |
  | 1.5G (the unit's) | 13.8 s | 607 MB | 3.4% | 935 MB | 751 of 863 MB | 1294 MB |
  | 2G | 13.9 s | 680 MB | 3.6% | 885 MB | — | — |

  Those were with `-XX:ActiveProcessorCount` left to the JVM; since then `launch.args` gives it two
  CPUs' threads, the C1 compiler only and an AOT cache (`test-server.aot`, recorded after the
  template), and the unit `CPUQuota=200%`. A restart is most of a test server's CPU: one after
  every test. Measured 2026-10-13 with this test server and a live realmc server (one Carpet player,
  60 mobs, unpaused) both pinned to 4 CPUs, as the box's 4 vCPU, the PC at load 9–17 from other
  builders; three back-to-back restarts each, interleaved:

  | | start → RCON | CPU-seconds by RCON | 10 s later | live server, avg / worst P99 mspt during |
  |---|---|---|---|---|
  | before (no quota, JVM defaults) | 9.5–15.9 s | 28–37 | +1.3 | 5.0–6.4 / 30–51 ms |
  | after (200%, 2 CPUs, C1, AOT) | 8.5–18.6 s | 10.6–17.9 | +0.5 | 5.0–5.7 / 21–36 ms |
  | live server alone | | | | 3.7–7.7 / 12–22 ms |

  One at a time: `CPUQuota=200%` alone changes no CPU-seconds (it bounds them: two of four
  CPUs stay the live server's); two CPUs' threads take a third off, the AOT cache a little more, C1
  half again. C1's code ran the stage under a test's load (150 zombies, fills, fireballs, TNT) at
  9.7–10.5 ms a tick against C2's 6.1–6.7, far from 50, and `fireball_cast`'s test passed the same.
  The AOT cache took 18 s to record (in a 2560M, 200% scope, as on the box) and is 111 MB.

  The test load: the stage's barrier floor, 6 fake players on it, 200 pigs and 100 zombies, then
  RCON as fast as it answers for 60 s (fills, particles, fireballs, teleports, `data get`): 800–1200
  commands a second, about 3 cores. The heavy load adds the 6 players thrown 700–4200 blocks apart
  (each generating its own 21 x 21 chunks), 1000 armor stands and 64 x 64 x 16 fills.

`list` and `status` show it under its server (`+ test`, and `"test"` in `--json`). After
`eval "$(integration/realmc/env NAME)"`, `vfg test` finds it as on the box: the live folders + "-test".

## The box's .env, and the TypeSafe key

Every server's `vfg/.env` (what `toolkit/remote/jev.py` reads as `/opt/vfg/.env`) is a link to
one file, **`integration/realmc/.server/shared/env`**, mode 600. It starts with the names and no
values. To give the stand-in the real key:

```sh
integration/realmc/realmc.py key          # TYPESAFE_API_KEY out of vfg-backup.tgz (vfg/.env), unprinted
integration/realmc/down --all --purge-key # empties shared/env again
```

`key` reads only that one line, in memory, and writes it there, nothing else from the backup.
**Once it has run, `shared/env` holds a real key**: it never leaves this folder, which git
ignores. To check it is there without showing it: `grep -c '^TYPESAFE_API_KEY=.' integration/realmc/.server/shared/env`.

## Minecraft's EULA

`up` writes `eula=true` into a server's folder only while the owner's `~/.vfg/config` has
`VFG_ACCEPT_EULA=true`, the same line `deploy.sh provision` needs for the box (their acceptance
of https://aka.ms/MinecraftEULA). Without it, `up` prepares everything and stops with exit 3.

## 26.3 answers worth knowing

Seen on this server; a test that matches output should match these.

| Command | 26.3 answers |
|---|---|
| `time query daytime` | refused: `Can't find element 'minecraft:daytime' of type 'minecraft:timeline'` |
| `time query day` | `Timeline minecraft:day is at 6000 tick(s)` (also `time query time`, `gametime`) |
| `time set noon` | `Set minecraft:overworld to time marker minecraft:noon` |
| `execute if blocks … all` | `Test passed. Count: 27`, or `Test failed` |
| `execute if block …` | `Test passed` or `Test failed` |
| `fill …` | `Successfully filled 27 block(s)` |
| an unknown command | `Unknown or incomplete command. See below for error…<--[HERE]` |
| `give`/`tp` to nobody | `No player was found` / `No entity was found` |
| `execute if predicate {condition:…}` | refused: `No key type`; 26.3's inline predicates take `type:` |
| `execute as Steve if predicate {type:…}` | `Test passed`, or nothing at all when it fails |
| `execute if items entity Steve weapon.mainhand *[custom_data~{…}]` | `Test passed. Count: 1` or `Test failed` |

## What differs from the box

| Setting | Box | Here | Why |
|---|---|---|---|
| `server-ip` | every interface | `127.0.0.1` | nothing outside this PC reaches it |
| `online-mode` | `true` | `false` | test names and fake players; loopback only |
| `rcon.port`, `server-port` | 25575, 25565 | free ports per server | several side by side |
| `pause-when-empty-seconds` | 60 | 0 | tests run with nobody on; it must keep ticking |
| `simulation-distance` | 10 | 6 | less ticking, several side by side |
| `sync-chunk-writes` | `true` | `false` | faster saves; `--fresh` mends a broken world |
| time, weather | running | noon, clear, held | the same start for every test |
| server | vanilla | Fabric + Carpet (once fetched) | fake players |

Everything else is the box's `server.properties` as `mc-backup.tgz` holds it (difficulty hard,
survival, allow-flight), with its password and secrets left out. The console goes to
`mc/console.log` as the box's unit sends it there, and `down` writes the unit's
`[vfg/INFO]: Server stopped` line.

## The smoke test

```sh
integration/realmc/smoke.sh [NAME] [--keep]    # NAME defaults to smoke
```

Up with a fresh world, `vfg cmd 'time query day'` (26.3 has no `time query daytime`), a gold box
filled and compared with its twin by `execute if blocks` (then one block changed, and the test
fails as it should). With Carpet, Steve: spawned in survival, seen by `vfg state`, turned and
aimed at a point, given a carrot_on_a_stick with custom_data, one use counted exactly once on
`vfg_click`, `use continuous` stopped by a `use`, a hold at a person's rate, sneak and unsneak,
walking and stopping, a jump, a drop. Then `tnt_rain` run on him, checked and stopped (its
cleanup takes its TNT away), Steve taken off, down. PASS or FAIL per check; 35 pass on Carpet.

## Settings

| Variable | Default | For |
|---|---|---|
| `REALMC_NAME` | `main` | the server, when a command names none (`env` exports it) |
| `REALMC_ROOT` | `integration/realmc/.server` | where everything is kept |
| `REALMC_HEAP` | `2G` | each server's `-Xmx` |
| `REALMC_TEST_HEAP` | the unit's `MC_TEST_HEAP` | a test server's `-Xmx` |
| `VFG_JAVA` | newest `~/.local/share/jdk/*/bin/java`, else `java` | a Java 25 or newer |
| `VFG_EC2_PYTHON` | `uv python find 3.12` | the box's Python, linked as `<NAME>/vfg/python/bin/python3`; the stand-in's own `python3` is the 3.9 `uv` finds, as Amazon Linux's, else this one |
| `REALMC_BACKUP` | `$VFG_BACKUP_DIR/mc-backup.tgz` | where `server.jar` comes from |
| `REALMC_SERVER_JAR` | — | a `server.jar` to use instead of the backup's |
| `REALMC_LIB_ROOTS` | — | more `libraries/` folders to look for Fabric Loader in |

## Where things are

```text
integration/realmc/
  up, down, env          the three commands most people need (they run realmc.py)
  realmc.py              up, down, env, list, status, cmd, testserver, carpet, key (and unit, the
                         fake systemctl's minecraft-test)
  players.py             Carpet's fake players over RCON
  smoke.sh               the smoke test
  bin/vfg, bin/ssh       vfg pointed at a server's stand-in; toolkit/tests/fake_ssh on its PATH
  .server/               gitignored
    shared/              server.jar, libraries/, versions/, mods/ (Carpet), env (the .env)
    template/world/      the world every server's starts from; template.json says where the arena is
    <NAME>/mc/           the server: server.properties (600), world/, console.log, logs/
    <NAME>/vfg/          /opt/vfg: lib/vfg_feed.py, vfg.db (the lines, the feed, the brain's tasks),
                         scripts/, made/, run/, .env, python/bin/python3 (the box's Python 3.12),
                         set-aside-<time>/ (an earlier vfg.db and feed.log, brain_live's clean slate)
    <NAME>/home/.vfg/    the VFG_HOME vfg uses for it
    <NAME>/box/          the box's PATH (python3 is 3.9, Amazon Linux's own) and the fake systemctl's units:
                         minecraft (a state), minecraft-test (.exec, .journal, .pid: the real test server)
    <NAME>/mc-test/      /opt/mc-test, its test server: .vfg-test-server, server.jar (a copy), libraries/,
                         mods/, launch.args, server.properties (600), reset.sh, world.template/, world/
    <NAME>/vfg-test/     /opt/vfg-test: python (a link), scripts/, run/, tests/, made/,
                         state/ (incantations.json, the store, and its lock), protected.json; no .env
```
