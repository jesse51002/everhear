# Everhear contracts

_The contract the parts were built to (draft, 2026-10-07). Code comments cite it as "Contracts §N". Where the code has moved on — model choices, the `start` script — the code and the part's README are current._

**What every part of the prototype takes in and puts out, so each part can be built from its own spec in one prompt and still fit the others.** A part's spec may decide anything inside the part; it may not change anything here. Where a part needs this page to change, the change is made here first.

**The pattern is the one that already worked**: Claude Code's Monitor on an SSH `tail -F` of a file on the EC2 box, RCON over SSH to act, `tellraw` to answer. What is new is one feed file that carries everything the conversation should hear — kept speech, watched game events, the orchestrator's results — so there is one mechanism, not three.

## 1. Where things run

- **EC2** (one Amazon Linux 2023 instance): the Minecraft Java server, vanilla 26.3, in `/opt/mc`, systemd unit `minecraft`, its console written to `/opt/mc/console.log`; the **bridge** (`vfg-bridge`) and the **Jev filter** (`vfg-jev`) in `/opt/vfg`; the database `/opt/vfg/vfg.db`; the feed `/opt/vfg/feed.log`; deployed scripts in `/opt/vfg/scripts/`. Open to the internet: Minecraft `25565` and the bridge's speech port `8787`. SSH only from the owner's IP. RCON `25575` is never reachable from outside — vanilla binds every interface, so the security group and a host firewall rule keep it in. Every unit runs as `ec2-user`, which owns `/opt/mc` and `/opt/vfg` (the database and its `-wal` and `-shm` files included) and has passwordless `sudo` for `systemctl` and `journalctl`, as Amazon Linux 2023 sets it up.
- **Players' PCs**: Minecraft Java 26.3 with Fabric and the **client mod**.
- **The owner's PC**: this repo; the **`vfg` toolkit**; the **conversation** (a Claude Code session the owner starts); the **orchestrator** runner and its **implementers**; the **scripts folder** `scripts/`. It reaches EC2 only over SSH.

Every timestamp is ISO-8601 UTC with milliseconds (`2026-10-07T18:22:31.412Z`). Every id is a ULID unless stated.

**The units** start the server jar in `/opt/mc` (`minecraft`), `/usr/bin/python3 -m vfg_bridge` (`vfg-bridge`) and `/usr/bin/python3 -u /opt/vfg/jev/jev_filter.py` (`vfg-jev`).

**The code** is one repo, a folder per part: `client-mod/`, `bridge/`, `jev/`, `toolkit/`, `orchestrator/`, `conversation/`, `scripts/`. What runs on EC2 is deployed to `/opt/vfg/`.

## 2. Speech in — client mod → bridge

`POST {VFG_BRIDGE_URL}/v1/lines`, header `Authorization: Bearer {VFG_CLIENT_TOKEN}`, body:

```json
{ "client_line_id": "uuid-v4", "player_uuid": "…", "player_name": "Steve",
  "text": "build me a castle",
  "started_at": "2026-10-07T18:22:29.100Z", "ended_at": "2026-10-07T18:22:31.400Z",
  "peak_dbfs": -8.2, "mean_dbfs": -23.5 }
```

- `202 {"id": "<line id>"}` stored; `400` bad body; `401` bad token. The token is checked before the body, so an empty `{}` with a good token gets `400` — the mod's connection test relies on it. Repeating a `client_line_id` returns the first id and stores nothing. An empty `player_uuid` is stored as NULL, the same as an AI line.
- Plain HTTP is accepted for the prototype: the token only lets someone post lines.
- The mod captures only while connected to the server whose address matches its configured `server_address`; a line captured then is still delivered after the player leaves, within its 60 s. A line is speech ended by **5 s of silence** (`silence_seconds`, default 5). `text` is 1–2000 characters after trimming. On failure it retries with backoff for up to 60 s, then drops the line.

## 3. Chat and game events — Minecraft server → bridge

The bridge tails `/opt/mc/console.log` and parses it.

- **Chat** `<Name> message` becomes a line with `source = chat`, in the same table as speech, and goes through Jev the same way.
- **Events**, kinds `join`, `leave`, `death`, `advancement`, `server` (start or stop), always go into `events`, and into the feed only if watched (§6).
- RCON echoes, `Played sound`, `Set spawn point` and other noise are ignored.

## 4. Transcript history — `/opt/vfg/vfg.db` (SQLite, WAL)

The bridge creates the schema. The Jev filter writes only the `jev_*` and `fed_at` columns and `usage` rows.

```sql
CREATE TABLE lines (id TEXT PRIMARY KEY, at TEXT NOT NULL, ended_at TEXT,
  player_uuid TEXT, player_name TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('voice','chat','ai')), text TEXT NOT NULL,
  peak_dbfs REAL, mean_dbfs REAL, client_line_id TEXT UNIQUE,
  jev_class TEXT CHECK (jev_class IN ('direct_ask','context','not_useful')),
  jev_confidence REAL, jev_judged_at TEXT, fed_at TEXT);
CREATE INDEX lines_at ON lines(at);
CREATE INDEX lines_player_at ON lines(player_name, at);
CREATE TABLE events (id TEXT PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
  player_name TEXT, text TEXT NOT NULL);
CREATE INDEX events_at ON events(at);
CREATE TABLE feed (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, line TEXT NOT NULL);  -- line: the whole line, exactly as in feed.log
CREATE TABLE usage (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
  component TEXT NOT NULL CHECK (component IN ('jev','conversation','orchestrator','implementer')),
  model TEXT, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, ref TEXT);
```

- It is queried by date with `vfg history` (§8).
- **The AI's own messages are lines too**: every `vfg say` is stored with `source = 'ai'`, `player_name = 'AI'`, no uuid, and the text without the prefix. A whisper is stored as `(to <player>) <text>`. They give Jev the other half of the conversation, and are never judged or fed.

## 5. Jev — the Jev filter

- Takes lines with `jev_class IS NULL` and `source` other than `ai`, in `at` order, polling every 250 ms or less.
- Calls Jev — `POST https://api.typesafe.ai/v1/systemone`, model `jev-latest`, key `TYPESAFE_API_KEY` — once per new line. The state is the fixed rules, then `/opt/vfg/lookout.md` (what to look out for), then recent lines, the AI's included, as history — as much as fits in `VFG_JEV_STATE_TOKENS` (default 8,000). Each line asked about gets one `choice` question: `direct_ask`, `context` or `not_useful`, with its confidence.
- Writes the verdict to the line, and a `usage` row with `component = jev`.
- **Judges again in the same call**: up to 5 lines judged `not_useful` in the last 2 minutes are asked again, as extra `choice` questions in the new line's call, so the state is paid for once. One that flips is fed with `was=not_useful`. The filter keeps that marker in memory, so a flip caught by a crash is fed without it.
- **Feeds what it keeps** (`direct_ask`, `context`) in batches. A `direct_ask` flushes the batch at once, with whatever context is waiting. Otherwise a batch flushes `VFG_BATCH_SECONDS` (default 5) after its first line.
- If Jev fails for 10 s it **fails open**: every line passes as `context` with no confidence, and one `SYS` line says so. Another `SYS` line says when Jev is back.

## 6. Game watchers — `/opt/vfg/watch.json`

```json
{ "events": ["join","leave","death","advancement","server"], "players": [], "contains": [] }
```

- `events` are the kinds to feed. `players` limits them to those players; empty means everyone. `contains` holds extra regexes on raw console lines, fed as `GAME raw`.
- Every kind is on by default. The bridge rereads the file when it changes. The conversation sets it with `vfg watchers set`.

## 7. The feed — `/opt/vfg/feed.log`

**The one stream the conversation watches.** It is append-only, one event per line, and mirrored in the `feed` table. The bridge and the Jev filter write it through one locked writer that assigns `seq`. The owner's PC writes it only through `vfg feed post`.

```text
LINE#812 18:22:31 Steve voice direct_ask 0.93 -8dB | build me a castle
LINE#813 18:22:33 Alex chat context 0.71 - was=not_useful | that cave by spawn
GAME#814 18:22:40 death Steve | Steve was slain by Zombie
ORCH#815 18:24:02 T7 done | castle built at spawn, 40x40
SYS#816 18:25:00 | jev unavailable — passing every line as context
```

- The grammar is `TYPE#seq HH:MM:SS fields | text`. `TYPE` is `LINE`, `GAME`, `ORCH` or `SYS`. The time is UTC. The fields, separated by single spaces, with `-` for anything missing:
  - `LINE`: `<player> <voice|chat> <direct_ask|context> <confidence|-> <NdB|-> [was=not_useful]`. The confidence has two decimals and is `-` when Jev fails open; dB is the line's peak, rounded, and `-` for typed chat.
  - `GAME`: `<kind> <player|->`.
  - `ORCH`: `<task id> <progress|done|failed>`.
  - `SYS`: no fields.
- Text has newlines replaced by spaces and is cut at 500 characters with `…`.
- A batch is written in one go, under 200 ms, so it reaches the conversation as one notification.
- **Every kept line is fed exactly once**, across restarts and crashes: before feeding a line not yet marked `fed_at`, the Jev filter looks for it in the `feed` table and only marks it if it is already there.
- `SYS#0` is never assigned by the feed: `vfg watch` uses it for its own notices — EC2 unreachable, reconnected, lines skipped, feed restarted.
- **The writer is one Python module, `vfg_feed`**, owned by the bridge and deployed to `/opt/vfg/lib/`. Its API is `append(type, fields, text, at=None) -> seq` and `append_batch(entries) -> [seq]`, where each entry is a `(type, fields, text, at)` tuple; `at` is the moment it describes (when a line was said), defaulting to now, and is what the `HH:MM:SS` shows. It cleans the text (newlines to spaces, 500 characters), takes a file lock, appends to `feed.log` and inserts the `feed` row. Its command line is `python3 /opt/vfg/lib/vfg_feed.py post <TYPE> "<fields>" "<text>"`, which prints the seq. The Jev filter imports it, and `vfg feed post` calls it over SSH.

## 8. The `vfg` toolkit — the owner's PC

**Every agent acts through these commands, and nothing else touches EC2.** Output is plain text. Exit 0 means success. A failure exits non-zero with one line on stderr. Configuration lives in `~/.vfg/config`, as `KEY=value` lines: `VFG_SSH_HOST`, `VFG_SSH_USER`, `VFG_SSH_KEY`; an environment variable of the same name wins.

| Command | Does |
|---|---|
| `vfg up` | Checks SSH and the `minecraft`, `vfg-bridge` and `vfg-jev` units, starts any that are down, prints their status |
| `vfg watch` | Prints the feed from the line after the last `seq` it printed (cursor in `~/.vfg/cursor`), then follows; reconnects on SSH drops; never exits on its own |
| `vfg say "<text>" [--to <player>]` | `tellraw` to everyone or one player, with the prefix `[AI] ` in aqua (`VFG_CHAT_PREFIX`), and stores the text as an `ai` line (§4) |
| `vfg cmd "<command>"` | One RCON command, run over SSH; prints its output |
| `vfg history [--since <iso\|-10m>] [--until <iso>] [--player <name>] [--class <c>] [--source voice\|chat\|ai] [--events] [--limit N]` | Queries the transcript history, or `events` with `--events` |
| `vfg watchers set <kind,…> [--players a,b] [--contains <regex>]` / `vfg watchers show` | Writes or prints `watch.json` |
| `vfg lookout set "<text>"` / `vfg lookout show` | Writes or prints `lookout.md` |
| `vfg orch start\|stop\|status` | Starts, stops or checks the orchestrator runner, as a background process on the owner's PC; `start` when it is already running prints its pid and succeeds; `stop` sends SIGTERM and waits 15 s before SIGKILL, so open tasks can be failed and queued replies sent |
| `vfg orch send "<task>" [--context "<text>"]` | Appends to the orchestrator inbox; prints the task id `T<n>` |
| `vfg orch reply <task> progress\|done\|failed "<text>"` | Writes an `ORCH` line to the feed |
| `vfg feed post <TYPE> "<fields>" "<text>"` | Low-level append to the feed, used by the commands above |
| `vfg script deploy [<name>]` | Copies the scripts folder, or one script, to `/opt/vfg/scripts/`; `_lib/` goes with every deploy |
| `vfg script run <name> [args…]` / `stop <name>` / `logs <name> [-n N] [--follow]` | Runs a script detached on EC2 (`setsid nohup python3 …/main.py`), refusing if it is already running — stop it first; stops it; prints the last N lines of its log and exits, or follows with `--follow` |
| `vfg usage add --component <c> --model <m> --in N --out N [--cost X] [--ref T7]` | Writes a `usage` row; queues it locally when EC2 is down. `--ref` is at most 64 characters |

Small additions a part may rely on: `vfg watch --tail N` (for a person at a terminal), `vfg script list`, `--wait [S]` (default 30 s) and `--no-deploy` on `vfg script run`, and `--feed` and `--source ai` on `vfg history`.

## 9. The orchestrator — the owner's PC

- **Inbox** `~/.vfg/orch/inbox.jsonl`, one task per line: `{"id": "T7", "at": "…", "task": "…", "context": "…"}`. Only `vfg orch send` appends to it.
- **The runner**'s entry point is the executable `orchestrator/run`. `vfg orch start` runs it detached, with its log in `~/.vfg/orch/runner.log` and its pid in `~/.vfg/orch/runner.pid`. It is one Claude Agent SDK process on Opus, logged in with the owner's Claude subscription, always running. It reads the inbox; every task becomes a message in its one long-running conversation. It starts implementer subagents (Haiku) in parallel.
- It answers every task with exactly one `done` or `failed` through `vfg orch reply`, plus any number of `progress` replies.
- It never talks to a player. It writes `usage` rows with `component` set to `orchestrator` or `implementer`.

## 10. Implementers and the scripts folder

- Implementers act on the server only through `vfg cmd` and `vfg script …`.
- A script is `scripts/<name>/main.py` plus `README.md`. The README's first line is one sentence saying what it does; usage and arguments follow.
- Scripts are Python 3, standard library only, and use the shared helpers in `scripts/_lib/mc.py`: `rcon`, `say`, `players`, `pos`, `spawn`. They start from the old `rcon.py` and `demon.py`.
- On EC2 a script runs as `python3 -u /opt/vfg/scripts/<name>/main.py`, with its own folder as the working directory and `PYTHONPATH=/opt/vfg/scripts/_lib`, so it does `import mc`. One instance per script; `vfg script run` deploys it first.
- **Before writing a script, search the READMEs for something similar, then copy and edit it.** After writing: deploy, then run.

## 11. Secrets

- On EC2, `TYPESAFE_API_KEY` and `VFG_CLIENT_TOKEN` live in `/opt/vfg/.env` with mode 600.
- On the owner's PC, settings live in `~/.vfg/config`. The client token also lives in each player's mod config.
- The RCON password never leaves `server.properties` on EC2.
- No secret goes in the repo.

## 12. Startup — the conversation sets everything up

1. The owner runs `claude` on Sonnet at low effort in `conversation/` (`conversation/start` does it). It is a normal session, and the owner can type to it at any time.
2. The conversation runs `vfg up`, then `vfg watchers set …`, then `vfg lookout set …`, then `vfg orch start`.
3. It arms Monitor on `vfg watch` with the longest timeout, and re-arms it each time it expires. Resuming from the cursor means nothing is lost in between.
4. From then on `LINE`, `GAME` and `ORCH` events arrive as notifications. It answers with `vfg say`, stays quiet, or hands work on with `vfg orch send`, and it never runs anything slow itself.
5. It records its own usage — `component = conversation` — from a Claude Code hook through `vfg usage add`, so every part's usage is in one table.

## 13. The parts

- **Client mod**, on the players' PCs — [`client-mod/`](../client-mod/README.md)
- **Server and bridge**, on EC2 — [`bridge/`](../bridge/README.md)
- **Jev filter**, on EC2 — [`jev/`](../jev/README.md)
- **`vfg` toolkit**, on the owner's PC — [`toolkit/`](../toolkit/README.md)
- **Orchestrator and implementers**, on the owner's PC — [`orchestrator/`](../orchestrator/README.md), [`scripts/`](../scripts/README.md)
- **Conversation**, on the owner's PC — [`conversation/`](../conversation/README.md)
