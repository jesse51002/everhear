# The scripts folder — the action store

Every script that has run on the Minecraft server lives here, so the next job can find one that already does it, or copy the closest and change the copy. Implementers search it before writing anything; the orchestrator reads it to brief them.

## Layout

```text
scripts/
  README.md            this file
  _lib/mc.py           the shared helpers every script imports
  _test/fake_rcon.py   a fake RCON server that records commands
  _test/run_tests.py   the tests: mc.py, and every script against the fake
  <name>/main.py       one script
  <name>/README.md     what it does: line 1 is the search index
```

A folder whose name starts with `_` is not a script.

## Finding a script

Search the READMEs, case-insensitive, with synonyms. An implementer uses its Grep tool (pattern `tnt|explo|bomb`, glob `scripts/*/README.md`, case-insensitive) and its Read tool; Bash runs only `vfg`, `py_compile` and `sleep`. At a terminal:

```bash
grep -i -E 'tnt|explo|bomb' scripts/*/README.md
head -n 3 scripts/*/README.md        # every script: what it does, usage, how it ends
```

If one does the job, run it. If one is close, copy its folder to a new name and edit the copy: never change what an existing script does (another task may be running it); fixing a plain bug is fine. Never edit `_lib/`.

## A script's README

```text
line 1   One sentence saying what it does. It is the search index.
line 2   Usage: <name> <required> [optional=default]
line 3   Ends: by itself   |   Ends: when stopped
then     each argument, and what the script leaves in the world
```

## A script's main.py

- Python 3.9 (Amazon Linux 2023), standard library only.
- Folder name: lower_snake_case naming what it does (`castle_builder`, `tnt_rain`), never a task id.
- It runs as `python3 -u /opt/vfg/scripts/<name>/main.py`, in its own folder, with `PYTHONPATH=/opt/vfg/scripts/_lib`, so it begins with

  ```python
  from mc import rcon, say, players, pos, spawn, on_stop, log, TAG
  ```

- Arguments come from `sys.argv`, with defaults. `log()` each notable action: it is what `vfg script logs` shows.
- Tag everything it summons with `TAG` (`spawn` does), and register `on_stop(cleanup)` to remove what it added: scoreboard objectives, gamerules, world border, bossbars. On stop, `mc` also kills `@e[tag=<TAG>]` itself. Tag things the world already had (a player's arrows, natural zombies) with `TAG + "_something"`, never `TAG`, so stopping does not kill them.
- A loop sleeps at least 0.1 s per pass and does nothing while nobody is online.
- One `fill` is at most 32768 blocks: build in slices. One command is at most about 1400 characters, and a reply over about 4 KB is cut.
- Game characters (the Demon Lord, Herobrine, the gods) speak with their own `tellraw`; plain announcements use `say()`. Never use `say()` to answer players: that is the conversation's job.

## `_lib/mc.py`

| Name | Is |
|---|---|
| `NAME`, `TAG` | the script's folder name, and `"vfg_" + NAME` |
| `rcon(cmd) -> str` | one server command (no leading `/`); its output, or `""` on failure or refusal, logged. One connection kept open, reconnects once on error. Refuses `stop`, `op`, `deop`, `ban`, `ban-ip`, `pardon`, `pardon-ip`, `whitelist`, `kick`, `save-off`, as the first word or after any `run` |
| `say(text, to="@a", who="AI", color="aqua")` | `tellraw` "[who] text", for a minigame's own announcements |
| `players(alive=False) -> list` | who is online; `alive=True` keeps those with Health > 0 |
| `OWNER` | the server owner's name, from `VFG_OWNER` (`vfg script run` sets it from `~/.vfg/config`), or `""` |
| `default_player() -> str` | the player to act for when the arguments name none: `OWNER`, else the first player online; with neither it logs why and exits 2. Never hard-code a player's name as a default |
| `owner_near(d)`, `not_owner()` | a selector for the owner within `d` blocks, and `",name=!<owner>"` for an `@e` selector, to spare the owner; both match nobody when `OWNER` is empty |
| `pos(player) -> (x, y, z) or None` | from `data get entity <p> Pos` |
| `spawn(player, mob, nbt="", spread=6, y=1) -> bool` | summons at a safe spot near the player, tagged `["vfg", TAG]` and persistent; `y > 3` summons in the air |
| `count(selector) -> int` | how many entities match, from `execute if entity` |
| `score(player, objective) -> int` | a scoreboard value, 0 when unset |
| `on_stop(fn)` | on SIGTERM or SIGINT every fn runs, newest first; then `kill @e[tag=<TAG>]`, and the script exits 0 |
| `log(*parts)` | prints `HH:MM:SS NAME parts` (UTC), flushed |

`rcon` reads `rcon.password` and `rcon.port` from `/opt/mc/server.properties` (`VFG_MC_PROPERTIES` overrides the path) and connects to `127.0.0.1` (`VFG_RCON_HOST`). The password stays in memory: never print, log or copy it.

## Deploy and run

```bash
vfg script deploy                      # the whole folder, _lib included: once, and after any change to _lib
vfg script run <name> [args...]        # deploys that script, then starts it detached; refuses if it is running
vfg script run --wait 30 <name> ...    # waits up to 30 s for one that ends by itself
vfg script logs <name> [-n N]          # its last lines
vfg script stop <name>                 # SIGTERM: its cleanup runs
vfg script list
python3 -m py_compile scripts/<name>/main.py   # syntax check before deploying
```

## Tests

```bash
uv run --python 3.9 python scripts/_test/run_tests.py
```

They start `_test/fake_rcon.py` on a free port and check `mc.py`, then run every script as on EC2: it compiles under 3.9, its README's first three lines have the right shape, it sends commands without a traceback, and it ends by itself within 20 s or stops on SIGTERM within 2 s after sending its cleanup.
