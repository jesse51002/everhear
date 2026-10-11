# Everhear contracts

_The contract the parts were built to (draft, 2026-10-07; the dispatcher, §14, 2026-10-09; actions, §1, §8, §10 and §14, 2026-10-10; incantations, §2, §4, §7, §8 and §10, 2026-10-10; the brain, which replaces the conversation, the orchestrator and the dispatcher, §1, §4, §5, §7, §8, §9, §12 and §14, 2026-10-10). Code comments cite it as "Contracts §N". Where the code has moved on — model choices — the code and the part's README are current._

**What every part of the prototype takes in and puts out, so each part can be built from its own spec in one prompt and still fit the others.** A part's spec may decide anything inside the part; it may not change anything here. Where a part needs this page to change, the change is made here first.

**The pattern is the one that already worked**: an SSH `tail -F` of a file on the EC2 box, RCON over SSH to act, `tellraw` to answer. One feed file carries everything the brain should hear — kept speech, watched game events — and what the brain itself did, so there is one stream, and it is the shared context.

## 1. Where things run

- **EC2** (one Amazon Linux 2023 instance): the Minecraft Java server, vanilla 26.3, in `/opt/mc`, systemd unit `minecraft`, its console written to `/opt/mc/console.log`; the **bridge** (`vfg-bridge`) and the **Jev filter** (`vfg-jev`) in `/opt/vfg`; the database `/opt/vfg/vfg.db`; the feed `/opt/vfg/feed.log`; deployed scripts in `/opt/vfg/scripts/`, with `_lib/` (the kit, `mc.py`, `instances.py`, `places.py`, `commands.py`); each running copy's files in `/opt/vfg/run/` (§10). Open to the internet: Minecraft `25565` and the bridge's speech port `8787`. SSH only from the owner's IP. RCON `25575` is never reachable from outside — vanilla binds every interface, so the security group and a host firewall rule keep it in. Every unit runs as `ec2-user`, which owns `/opt/mc` and `/opt/vfg` (the database and its `-wal` and `-shm` files included) and has passwordless `sudo` for `systemctl` and `journalctl`, as Amazon Linux 2023 sets it up.
- **Players' PCs**: Minecraft Java 26.3 with Fabric and the **client mod**.
- **The owner's PC**: this repo; the **`vfg` toolkit**; the **brain** (§9), and the **implementers** it starts; the **actions** `actions/`, and `made/<server>/actions/` for what implementers made; the **scripts folder** `scripts/` (and `made/<server>/scripts/`). It reaches EC2 only over SSH; the brain also calls OpenRouter.

Every timestamp is ISO-8601 UTC with milliseconds (`2026-10-07T18:22:31.412Z`). Every id is a ULID unless stated.

**The units** start the server jar in `/opt/mc` (`minecraft`), `/opt/vfg/python/bin/python3 -m vfg_bridge` (`vfg-bridge`) and `/opt/vfg/python/bin/python3 -u /opt/vfg/jev/jev_filter.py` (`vfg-jev`).

**Python on the box** is 3.12, the PC's version: uv, installed into `/opt/vfg/uv/` as a step of the box's setup (`deploy.sh provision` runs `bridge/deploy/python.sh`), installs CPython 3.12 there, and `/opt/vfg/python` links to it. `/opt/vfg/python/bin/python3` runs the units, the scripts and every `vfg` remote program; Amazon Linux's own `python3` (3.9) is left to the host's tools.

**The code** is one repo, a folder per part: `client-mod/`, `bridge/`, `jev/`, `toolkit/`, `brain/` (the brain, Respond and the implementers), `actions/`, `lib/py/` (`structured()`, every model call that wants a structure back; `vfg_actions`, the one loader of the actions), `scripts/`. What runs on EC2 is deployed to `/opt/vfg/`; `vfg action run` deploys a copy's script and `scripts/_lib/` when the box does not have them as the PC does: each deployed folder keeps the stamp, a hash of its files, it was sent with (`.vfg-stamp`), and a run sends the stamps first. An action never leaves the PC: it is a class that names a script and checks its values (§10).

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
- **Incantations** — a phrase of two words or more, bound for a player to an action with its values, fires that action the moment that player says it, with no model on its path. The mod's keyword spotter hears it and posts a **cast**, its own request, beside the line pipeline: neither waits on the other.
  - `POST {VFG_BRIDGE_URL}/v1/casts`, the same token, body `{"client_cast_id": "uuid-v4", "player_uuid", "player_name", "phrase": "fire ball", "heard"?: "fireball", "started_at", "ended_at"}`: the incantation's own phrase (the mod maps a matched alias back to it), and the phrase's window on the mod's audio clock, the clock a line's times use. `202 {"id", "status": "fired"|"cooldown"|"duplicate"|"failed"|"stale", "copy"?, "error"?}` as soon as the copy is spawned; `404` when the player has no such incantation (their own or `@a`); `400`, `401`; `503` when the kit is not on the box. Repeating a `client_cast_id` answers what the first did.
  - The bridge starts the bound action's copy on the box itself (`scripts/_lib/instances.py`, from the entry in `/opt/vfg/incantations.json`, §10), with every caster field set to whoever cast. A cast of an incantation whose window, widened by `VFG_CAST_SLACK_S` (0.3 s) each side, overlaps one of that player's that fired is `duplicate`; within its own `cooldown_s` of its last fired cast, or within `VFG_CAST_GAP_S` (1 s) of any of that player's, it is `cooldown`. Cooldowns are on the bridge's clock.
  - The mod's settings: `incantations` (on), `incantation_threshold` (0.25) and `incantation_boost` (1.5), the spotter's; `heard` is the keyword it matched, normalised (`fireball`). It spots only while it listens, and only that player's own incantations, `@a` ones included; no audio leaves the PC, only the phrase.
  - `GET {VFG_BRIDGE_URL}/v1/incantations?player=Steve&after=<version>&wait=<s>`: `200 {"version", "player", "incantations": [{"phrase", "aliases", "cooldown_s"}]}`, the incantations that apply to that player, `@a` ones included; with `after` the current version it is held until the version changes or `wait` (default 25, at most 30) seconds pass, then `304`. The mod long-polls it while connected.
  - **Layer 2 and the drop rule, on `/v1/lines`.** For a voice line from a player with incantations, a phoneme match of the transcript against their phrases and aliases (`VFG_INCANT_ALLOWANCE`, 0.25) fires a cast through the same path, layer `transcript`, with the line's window. Then a line that overlaps one of that player's casts and has at most one word left once the phrase is taken out (fillers not counted) is stored with `dropped = 'cast'` and never judged or fed (§5); a kept one gets `cast_id`.
- The mod captures only while connected to the server whose address matches its configured `server_address`; a line captured then is still delivered after the player leaves, within its 60 s. A line is speech ended when the mod's turn model hears it finished, or by **`silence_seconds` of silence** (default 3). `text` is 1–2000 characters after trimming. On failure it retries with backoff for up to 60 s, then drops the line.

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
  jev_class TEXT CHECK (jev_class IN ('action_oriented','conversational','context','not_relevant')),
  jev_confidence REAL, jev_judged_at TEXT, fed_at TEXT);
CREATE INDEX lines_at ON lines(at);
CREATE INDEX lines_player_at ON lines(player_name, at);
CREATE TABLE events (id TEXT PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL,
  player_name TEXT, text TEXT NOT NULL);
CREATE INDEX events_at ON events(at);
CREATE TABLE feed (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, line TEXT NOT NULL);  -- line: the whole line, exactly as in feed.log
CREATE TABLE usage (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
  component TEXT NOT NULL CHECK (component IN ('jev','conversation','orchestrator','implementer','brain','respond')),
  model TEXT, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, ref TEXT);
```

- **Incantations** (§2): `lines.dropped` (`'cast'`: only an incantation, never judged or fed) and `lines.cast_id` (the cast the line's speech overlapped), and the bridge's own table `casts (id, at, started_at, ended_at, player_name, phrase, heard, layer 'spotter'|'transcript', incantation_id, action, copy, status 'fired'|'cooldown'|'duplicate'|'failed'|'stale', error, line_id, client_cast_id UNIQUE)`. A fired or failed cast is also an `events` row of kind `cast`.
- It is queried by date with `vfg history` (§8).
- **The AI's own messages are lines too**: every `vfg say` is stored with `source = 'ai'`, `player_name = 'AI'`, no uuid, and the text without the prefix. A whisper is stored as `(to <player>) <text>`. They give Jev the other half of the conversation, and are never judged; `vfg say` feeds each as an `AI say` line itself (§7).
- **The shared context is the `feed` table** (§9): every kept line, every fed game event and everything the brain recorded, in `seq` order, each with its `processed_at` mark: a column added after this DDL, by `ALTER TABLE` on the bridge's start, as are `usage`'s `cache_read_tokens` and `cache_write_tokens`. A database built before the four classes is rebuilt on the bridge's start: `direct_ask` becomes `action_oriented`, `not_useful` becomes `not_relevant`. `conversation` and `orchestrator` stay valid components only for old rows.

## 5. Jev — the Jev filter

- Takes lines with `jev_class IS NULL` and `source` other than `ai`, in `at` order, polling every 250 ms or less.
- Calls Jev — `POST https://api.typesafe.ai/v1/systemone`, model `jev-latest`, key `TYPESAFE_API_KEY` — once per new line. The state is the fixed rules, then `/opt/vfg/lookout.md` (what to look out for), then `/opt/vfg/menu.md` (the brain's actions, one `name — what it does` line each, which `vfg jev menu set` writes), then history: recent lines, the AI's included, and the brain's records of the actions it ran and the tasks it opened (`AI` feed lines), so a follow-up to an earlier ask reads as one — as much as fits in `VFG_JEV_STATE_TOKENS` (default 8,000). Each line asked about gets one `choice` question with its confidence, of four classes:
  - `action_oriented` — asks the AI for something to happen, or follows up an earlier ask;
  - `conversational` — talks to the AI without asking for an action;
  - `context` — not said to the AI, but needed to understand or carry out an ask;
  - `not_relevant` — neither.
- Writes the verdict to the line, and a `usage` row with `component = jev`.
- **Judges again in the same call**: up to 5 lines judged `not_relevant` in the last 2 minutes are asked again, as extra `choice` questions in the new line's call, so the state is paid for once. One that flips is fed with `was=not_relevant`. The filter keeps that marker in memory, so a flip caught by a crash is fed without it.
- **Feeds what it keeps** (`action_oriented`, `conversational`, `context`) in batches. An `action_oriented` or `conversational` line flushes the batch at once, with whatever context is waiting: both get a live answer. Otherwise a batch flushes `VFG_BATCH_SECONDS` (default 5) after its first line.
- If Jev fails for 10 s it **fails open**: every line passes as `context` with no confidence, and one `SYS` line says so; the brain's batch call (§9) is what reads them then. Another `SYS` line says when Jev is back.

## 6. Game watchers — `/opt/vfg/watch.json`

```json
{ "events": ["join","leave","death","advancement","server"], "players": [], "contains": [] }
```

- `events` are the kinds to feed. `players` limits them to those players; empty means everyone. `contains` holds extra regexes on raw console lines, fed as `GAME raw`.
- Every kind is on by default. The bridge rereads the file when it changes. The brain sets it with `vfg watchers set` as it starts.

## 7. The feed — `/opt/vfg/feed.log`

**The one stream the brain follows, and the shared context.** It is append-only, one event per line, and mirrored in the `feed` table. The bridge and the Jev filter write it through one locked writer that assigns `seq`. The owner's PC writes it only through `vfg say` and `vfg feed post`, and only the brain does.

```text
LINE#812 18:22:31 Steve voice action_oriented 0.93 -8dB | build me a castle
LINE#813 18:22:33 Alex chat context 0.71 - was=not_relevant | that cave by spawn
AI#814 18:22:35 task T7 | T7: a castle at spawn, about 40x40, stone and oak
AI#815 18:22:35 say T7 | on it, castle coming up
GAME#816 18:22:40 death Steve | Steve was slain by Zombie
AI#817 18:24:02 result T7 | T7 done: castle built at spawn, 40x40
SYS#818 18:25:00 | jev unavailable — passing every line as context
```

- The grammar is `TYPE#seq HH:MM:SS fields | text`. `TYPE` is `LINE`, `GAME`, `AI` or `SYS`. The time is UTC. The fields, separated by single spaces, with `-` for anything missing:
  - `LINE`: `<player> <voice|chat> <action_oriented|conversational|context> <confidence|-> <NdB|-> [was=not_relevant]`. The confidence has two decimals and is `-` when Jev fails open; dB is the line's peak, rounded, and `-` for typed chat.
  - `GAME`: `<kind> <player|->`, any kind the bridge feeds.
  - `AI`: `<say|action|task|result|note> <task id|->` — what the brain did, which only it writes: `say`, a chat line it sent (as the `lines` table keeps it); `action`, an action it ran and how it went; `task`, an implementer task it opened or a follow-up it sent; `result`, a task's result, one short line; `note`, anything else it records. Nothing an implementer writes is ever fed.
  - `SYS`: no fields.
- The `ORCH` type of the orchestrator is retired.
- Text has newlines replaced by spaces and is cut at 500 characters with `…`.
- A batch is written in one go, under 200 ms.
- **Every kept line is fed exactly once**, across restarts and crashes: before feeding a line not yet marked `fed_at`, the Jev filter looks for it in the `feed` table and only marks it if it is already there.
- **A cast** that fired or failed (§2) is always fed, whatever `watch.json` says: `GAME#820 18:30:02 cast Steve | Steve cast "fire ball": command, running as command_2`, or `… | Steve cast "fire ball": failed — <error>`. Cooldown and duplicate casts are not fed.
- `SYS#0` is never assigned by the feed: `vfg watch` uses it for its own notices — EC2 unreachable, reconnected, lines skipped, feed restarted.
- **The writer is one Python module, `vfg_feed`**, owned by the bridge and deployed to `/opt/vfg/lib/`. Its API is `append(type, fields, text, at=None) -> seq` and `append_batch(entries) -> [seq]`, where each entry is a `(type, fields, text, at)` tuple; `at` is the moment it describes (when a line was said), defaulting to now, and is what the `HH:MM:SS` shows. It cleans the text (newlines to spaces, 500 characters), takes a file lock, appends to `feed.log` and inserts the `feed` row. Its command line is `python3 /opt/vfg/lib/vfg_feed.py post <TYPE> "<fields>" "<text>"`, which prints the seq. The Jev filter imports it, and `vfg feed post` calls it over SSH.

## 8. The `vfg` toolkit — the owner's PC

**Every agent acts through these commands, and nothing else touches EC2.** Output is plain text. Exit 0 means success. A failure exits non-zero with one line on stderr. Configuration lives in `~/.vfg/config`, as `KEY=value` lines: `VFG_SSH_HOST`, `VFG_SSH_USER`, `VFG_SSH_KEY`, and the brain's (§9); an environment variable of the same name wins.

| Command | Does |
|---|---|
| `vfg up` | Checks SSH and the `minecraft`, `vfg-bridge` and `vfg-jev` units, starts any that are down, prints their status |
| `vfg watch [--after <seq>]` | Prints the feed from the line after the last `seq` it printed (cursor in `~/.vfg/cursor`), then follows; reconnects on SSH drops; never exits on its own. `--after <seq>` starts after that seq, prints every line after it, on its first connection and on every reconnect, with no catch-up limit, and reads and writes no cursor: the brain keeps its own place |
| `vfg say "<text>" [--to <player>] [--task T7]` | `tellraw` to everyone or one player, with the prefix `[AI] ` in aqua (`VFG_CHAT_PREFIX`), and stores the text as an `ai` line (§4), and feeds it as an `AI say <task\|->` line (§7), in the same SSH call |
| `vfg cmd "<command>"` | One server command, through the `command` action; prints Minecraft's reply |
| `vfg history [--since <iso\|-10m>] [--until <iso>] [--player <name>] [--class <c>] [--source voice\|chat\|ai] [--events] [--limit N]` | Queries the transcript history, or `events` with `--events` |
| `vfg watchers set <kind,…> [--players a,b] [--contains <regex>]` / `vfg watchers show` | Writes or prints `watch.json` |
| `vfg lookout set "<text>"` / `vfg lookout show` | Writes or prints `lookout.md` |
| `vfg brain start\|stop\|status` | Starts, stops or checks the brain (§9), a background process on the owner's PC: `start` runs `brain/run` detached, its log in `~/.vfg/brain/brain.log` and its pid in `~/.vfg/brain/brain.pid`, and when one is already running prints its pid and succeeds; `stop` sends SIGTERM to the brain alone, so in-flight calls can finish, it can stop its implementers itself (their CLIs are in its process group) and close or fail their tasks, and SIGKILL to the group after 15 s; `status` also lists the open tasks |
| `vfg context [--since <iso\|-6h>] [--json]` / `mark <seq> …` | The shared context (§9): the feed's rows since then, oldest first, `--json` one `{"seq", "at", "line", "processed_at"}` per line, then `{"last_seq": N}`, the feed's highest seq (0 for an empty feed), with rows or none; or marks rows processed, setting `processed_at` where it is empty, any number in one SSH call |
| `vfg jev menu set "<text>"` / `vfg jev menu show` | Writes or prints `/opt/vfg/menu.md`, the brain's actions in Jev's state (§5) |
| `vfg feed post <TYPE> "<fields>" "<text>"` | Low-level append to the feed: the brain's `AI` records (§7) |
| `vfg action run <action> key=value … [as=<copy>] [--task T7]` | Checks the values against the action's class (§10) — an unknown key or one the action fixes refused, a number out of range clamped with a note, a saved place against the places cache — then deploys its script with `_lib/` (unless EC2's stamps say it has them already, §1) and starts a copy on EC2, named `as=<copy>`, else the action's name, else `<action>_2`, `_3` …; prints one line, `{"ok", "copy", "action", "running", "summary", "facts", "notes", "error"?}`, exit 0 when ok; a copy that exits non-zero is not ok, its `error` the error it crashed on (`<copy>.error`), else the `result()` it refused with, else the last line it printed. A once action is waited for, up to its `timeout_s` (else `VFG_DISPATCH_ACTION_TIMEOUT_S`, 20 s) counted on EC2 from the run's start, its deploy included, and answers with its `result()`; past it the answer is `running: true`, what its last `result()` said and `still running as <copy>`, and it is never run again. An until-stopped one answers with its first `result()`, or `running as <copy>` after 2 s. `edit` and `stop` name a copy (`copy=<copy>`) |
| `vfg action list [--json]` / `check <name>` | Every action, the library's and this server's, one `name — what it does` line each (`--json`: `actions.json`); what is wrong with one action, or `ok: <file>` |
| `vfg action refs [--version]` | The server's version, from `server.jar`; or, without `--version`, `{"version", "commands"}`, the data generator's `commands.json` (300 s at most) |
| `vfg script set <copy> key=value …` / `stop <copy>` | The `edit` and `stop` actions: a live value lands within `VFG_LIVE_POLL_S` (0.5 s), any other restarts the copy with the rest kept; a stop ends the copy and takes away everything tagged with it |
| `vfg script list [--json] [--fresh]` / `logs <copy> [-n N] [--follow]` | The running copies, one `galick_gun_2 — galick_gun, T7, player=Alex cooldown=0` line each, from `running.json` (`--fresh` fetches them from EC2 first); a copy's log |
| `vfg script new <name> [--from <script>] [--preset <p> …]` / `check [<name\|path> …]` / `save <copy> <new_name>` / `deploy [<name>]` | A script in `made/<server>/scripts/<name>/`, copied with each preset's function, and its action in `made/<server>/actions/<name>.py`; what would break in a script, a preset or an action; a running copy's values kept as an action of their own; copies the scripts folder, or one script, to `/opt/vfg/scripts/` with `_lib/` |
| `vfg preset save <script> <function> <name>` | Lifts a function, its private helpers and a values class for what it reads (from the script's action) into `made/<server>/presets/<name>.py` |
| `vfg incantation list [--player <p>] [--json] [--fresh]` | The incantations (§10), one `Steve: "fire ball" → command {…} cooldown 3s; aliases fireball, fire bowl` line each, or `none`; `--player` those that apply to that player, `@a` ones included; `--json` a plain list of `{player, phrase, action, values, cooldown_s, aliases}`. It answers from `$VFG_HOME/incantations.json` while it is younger than `VFG_INCANT_TTL_S` (30 s), else fetches the box's store; the `incantation_*` actions write through |
| `vfg made save` / `restore <save\|date>` | Copies `made/<server>/` into a new `$VFG_MADE_ARCHIVE/<YYYY-MM-DD-HHMMSS>/`, never deleting a save; or puts one back, named in full or by its date (that day's latest) |
| `vfg usage add --component <c> --model <m> --in N --out N [--cost X] [--ref T7]` | Writes a `usage` row; queues it locally when EC2 is down. `--ref` is at most 64 characters |
| `vfg place save <name> (--player <p> \| --at <x> <y> <z> [--dim <d>]) [--by <T7>]` / `list [--json] [--fresh]` / `forget <name>` | Saves (the `save_place` action), lists or forgets a named place (§14): `saved castle at 197 68 251 in overworld`; one `castle 197 68 251 overworld (T23)` per place, or `none`; `forgot castle`, exit 1 when there was none. `list` answers from `$VFG_HOME/places.json` while it is younger than `VFG_PLACES_TTL_S` (30 s), else fetches; `--fresh` always fetches; save and forget write through; a failed fetch with any cache prints it, warns, exits 0 |

**What the PC keeps** in `$VFG_HOME` (`~/.vfg`), each file written atomically under its own lock and read by everyone who asks: `places.json`, the saved places; `running.json`, `{"fetched_at", "copies": {copy: {"action", "script", "values", "live", "task", "started", "runs"}}}`, written through on every run, edit and stop through `vfg` and fetched again by `vfg script list --fresh`; `actions.json`, every action as `lib/py/vfg_actions.py` reads it (`{"built_at", "actions": {name: {"line", "script", "runs", "takes", "fixed", "defaults", "descriptions", "source", "file", "fields": {field: {"type", "default", "ge", "le", "choices", "live", "description"}}}}}`), built again when an action file is newer.

`vfg` runs under `uv` on Python 3.12, with pydantic and `lib/py/` for the actions. Small additions a part may rely on: `vfg watch --tail N` (for a person at a terminal), `vfg state` (`--json --items`: every online player's position and items by slot, worn and held marked, for the brain), and `--feed` and `--source ai` on `vfg history`.

## 9. The brain — the owner's PC

**The brain replaces the conversation, the orchestrator and the dispatcher.** Its entry point is the executable `brain/run`; `vfg brain start` runs it detached. It is one async loop: it follows the feed with `vfg watch --after <seq>`, and every event is its own task, so no line waits for another.

- **Events.** An `action_oriented` LINE is one brain call. A `conversational` LINE is one Respond call. An implementer's report is one brain call. A `context` LINE, a GAME line, a SYS line and the brain's own `AI` lines are context only. A LINE already in the feed when the brain reads its context (at or below the `last_seq` it gives) and more than 2 minutes old then is history and is never acted on; a LINE that arrives on `vfg watch` after that is live, whatever its time.
- **A brain call** is one `structured()` call (`lib/py/vfg_llm.py`) on `VFG_BRAIN_MODEL` (`openai/gpt-6.1-sol`, reasoning `low`). It routes each part of the line — `action`, the action written in full, which runs at once (§14); `implementer`, a brief, from which code starts an implementer (§10), or a follow-up sent to the implementer of a task it names; `talk` — and writes the one chat line, the reply, in the same answer. Every line it acts on gets a reply. On a report it writes the player's chat line and one `result` line, and may send that implementer a follow-up.
- **Respond** is the brain's cheap mode, on `VFG_RESPOND_MODEL` (DeepSeek V4 Flash, reasoning off), reading the same context. It replies, or hands the line up to the brain and says nothing itself — never both — and never acts on the game.
- **The shared context** is the feed (§7), `feed` in `vfg.db` (§4): the whole conversation, with the world — every online player's position and items by slot (`vfg state --json --items`), the running copies, the saved places, the time and the weather — and the open tasks. Every brain and Respond call reads it whole. On the AI side only the brain writes to it, through `vfg say` and `vfg feed post AI`; nothing an implementer writes is in it.
- **Processed marks.** Every live call takes in every row not yet processed and marks it, `vfg context mark`. When `VFG_BRAIN_BATCH_S` (60) pass with no live call and something is still unprocessed, the brain makes one batch call over it; every live call pushes that back.
- **Caching.** The call's fixed opening, then the conversation in blocks marked at stable checkpoints (every `VFG_BRAIN_CHECKPOINT`, 5, blocks; at most 4 marks), then what changes.
- **Files**, in `$VFG_BRAIN_DIR` (default `~/.vfg/brain`): `tasks.json` (`{"next", "tasks": {"T7": {"id", "at", "player", "line", "task", "status": "open|done|failed", "result", "session", "closed_at"}}}`, written atomically by the brain alone; `vfg route --task` and `vfg brain status` read it); `lock`; `brain.pid`; `brain.log`, one `<ISO time> <area> <message>` per line; and `refs/<version>/`, the server's command tree and the wiki's command pages, fetched once per server version.
- **Usage**: a `usage` row per call, `component` `brain` or `respond`, and `implementer` per implementer run.

## 10. Implementers, actions and the scripts folder

- **An implementer is started by code from the brain's brief, one per task** (§9): a Claude Agent SDK query on Haiku 5.5, logged in with the owner's Claude subscription. Its brief carries `SAVED PLACES: …` (§14), read fresh, and the example scripts and presets `vfg route` picks. A follow-up to its task resumes its session, or waits behind it while it runs, so two implementers never edit one script. Its last message is its report, to the brain alone; it never talks to a player.
- **Implementers act on the server only through actions**: `vfg action run|list|check`, its shortcuts `vfg cmd`, `vfg script set|stop` and `vfg place save`, and `vfg script new|check|list|logs`, `vfg preset save` and `vfg place list` (§8). The ladder: run an action; change a running copy; a new script and its action; a new preset.
- **The last step of any build players may want to go back to**, once it is checked, is `vfg place save <name> --at <x> <y> <z> --by <task>`, at the spot a player should arrive: the name the brief gives, else a short lowercase one (`castle`, `steve_house`).
- **Four layers, code in one**: the kit (`scripts/_lib/`, imported), presets (`scripts/_presets/<name>.py`, copied into a script), scripts (`scripts/<name>/main.py`, the code) and actions (`actions/<name>.py`, called). What implementers make goes in `made/<server>/scripts/`, `presets/` and `actions/`, under names the library does not use.
- **An action** is one pydantic class in its own file, named as the file (`gatling_gun.py`: `class GatlingGun`), with no code: its docstring's first line is its line on the menu; `script`, `runs` (`once` or `until_stopped`), `takes` (the fields the planner fills), `defaults`, `fixed`, `descriptions` (its own words for a field it inherits, whose type and limits stay) and `timeout_s`; and a field for every value the script reads, its type from `actions/_types.py`, its default, its limits (`Field(ge=, le=)`) and its description under it. `Live[T]` marks a value the script reads again while it runs. A variant subclasses an action. A preset's values are a class beside its function, `class <CamelName>(Values)`, which the action over every script that copied it inherits (`preset("<name>")`). Every value is checked on the PC; the box gets plain JSON.
- **A script** is Python 3.12, standard library only, with `kit`, `mc` and the rest of `scripts/_lib/`; line 1 its docstring, `"""<Title>: what it does."""`; line 2 `ASKS`, what players said. `v = values()` gives its copy's checked values (a `Live` one read again at most every `VFG_LIVE_POLL_S`, 0.5 s); `result(summary, **facts)` is its answer.
- **A copy** is one run of an action. On EC2 it runs as `/opt/vfg/python/bin/python3 -u <folder>/main.py`, its folder the working directory, with `PYTHONPATH=/opt/vfg/scripts/_lib`, `VFG_SCRIPT` and `VFG_INSTANCE` its name, `VFG_TASK`, `VFG_RUN_DIR`; everything it makes carries the tag `vfg_<copy>`, taken away by a stop; a copy that ends by itself keeps what it made, handed over to the tag `vfg_left_<copy>`, which no stop takes (`kill @e[tag=vfg_left_<copy>]` clears it). Its files in `/opt/vfg/run/`: `<copy>.pid`, `<copy>.log`, `<copy>.json` (`{"copy", "action", "script", "source", "server", "values", "live", "runs", "task", "started"}`), `<copy>.values.json` (an edit's live values), `<copy>.result.json` (`{"summary", "facts", "at"}`, the last `result()`) and `<copy>.error` (the last line of an error nothing caught, which `mc.py` writes as it cleans up). `scripts/_lib/instances.py` starts, stops and edits copies, for `vfg` and for the `stop` and `edit` scripts.
- **Incantations** are actions too: `incantation_create` (`player`, required, a name, several or `@a`; `phrase`, two words or more; `action`, from the menu; `values`, its defaults for this phrase; `cooldown_s`, default 3), `incantation_edit` (by `player` and `phrase`: `values`, `cooldown_s`, `aliases`) and `incantation_remove` (by `player` and `phrase`), over one script, `incantations`, which writes the store `/opt/vfg/incantations.json` (`{"version", "incantations": [{"id", "player", "phrase", "aliases", "action", "script", "source", "server", "values", "live", "runs", "caster_fields", "cooldown_s", "created", "by"}]}`, one entry per named player, `@a` one; `scripts/_lib/incantations.py`). vfg checks the bound action's values as a run would, makes every player field left out a caster field, makes the aliases (one `structured()` call, `VFG_INCANT_ALIAS_MODEL`, and the phrase joined and split, at most 12), adds a note when the phrase is short or sounds like, or starts like, another of that player's, and deploys the bound script before the store is written.
- **The six core actions**, each over a script of its name: `command` (up to five server commands in order, the refused list of `scripts/_lib/commands.py` applied), `teleport`, `summon_around`, `save_place`, `stop` and `edit`. Every library script has at least one action, and every script an implementer makes gets one (`vfg script new` writes both; `vfg action check` passes).

## 11. Secrets

- On EC2, `TYPESAFE_API_KEY` and `VFG_CLIENT_TOKEN` live in `/opt/vfg/.env` with mode 600. On the owner's PC they may live in the `.env` file `VFG_BOX_ENV` names (default `~/.vfg/box.env`, mode 600, outside any repo). `deploy.sh provision` and `deploy.sh env` send only the names `env.example` lists, over SSH on stdin, and make the client token on the box when none is given.
- On the owner's PC, settings live in `~/.vfg/config`. The client token also lives in each player's mod config. The OpenRouter key is `OPEN_ROUTER_API` in the environment, or alone in the file `VFG_OPENROUTER_KEY` names (default `~/.vfg/openrouter.key`, mode 600); the config holds the path, never the key, and only `structured()` reads it.
- The RCON password never leaves `server.properties` on EC2.
- No secret goes in the repo.

## 12. Startup — the brain sets everything up

1. The owner runs `vfg brain start` (`brain/run`).
2. The brain runs `vfg up`, then `vfg watchers set …`, then `vfg lookout set …` and `vfg jev menu set …`, its actions for Jev's state (§5); it sets the menu again whenever an action is added.
3. It reads the context with `vfg context --json` and follows the feed with `vfg watch --after` the `last_seq` that gives, so nothing is lost across a restart and nothing older is read as new. A read that fails is tried again; the feed is never followed from 0.
4. It greets the players with `vfg say`, skipped when nobody is online.

## 13. The parts

- **Client mod**, on the players' PCs — [`client-mod/`](../client-mod/README.md)
- **Server and bridge**, on EC2 — [`bridge/`](../bridge/README.md)
- **Jev filter**, on EC2 — [`jev/`](../jev/README.md)
- **`vfg` toolkit**, on the owner's PC — [`toolkit/`](../toolkit/README.md)
- **The brain, Respond and the implementers**, on the owner's PC — [`brain/`](../brain/README.md), [`lib/py/`](../lib/py/vfg_llm.py)
- **Actions and the scripts folder** — [`actions/`](../actions/README.md), [`scripts/`](../scripts/README.md)

## 14. Actions from the brain — the owner's PC

**The brain runs an action part at once**, written in full in its one answer: `vfg action run <action> key=value … --task <T7>` (§8), reading its one line. A command Minecraft refuses is planned again once, with the command's reference, on `VFG_PLAN_MODEL` (DeepSeek V4 Flash, reasoning off). Each action and how it went becomes one `AI action` line.

- **The menu** is every action (§10), the library's and `made/<VFG_SERVER>/actions/`, each as its name and docstring's first line, reloaded when a file changes, so a new action is on it at the next ask; then every running copy, one line each, from `running.json`, which a background `vfg script list --json --fresh` fetches every `VFG_RUNNING_TTL_S` (30 s); `edit` changes a running copy and `stop` ends one.
- **An item a player owns** is changed by its slot, which the context names (`hotbar.4 iron_sword (held)`): the held one, then the worn one, then the first listed; an ask for an item the player does not have is dropped, with why.
- **Saved places** live in Minecraft's command storage `everhear:places`, one entry per name (`^[a-z0-9_]{1,32}$`): `{pos: [x, y, z], dim: "minecraft:overworld", by: "T23"}`, whole blocks. Only `vfg place` and `scripts/_lib/places.py` (which the `save_place` and `teleport` scripts, and a `Place` value, use) read or write them. Every prompt — the brain's, every implementer's brief — carries them as one line: `SAVED PLACES: castle 197 68 251 overworld (T23); …`, or `SAVED PLACES: none`.
- **`command` places or removes no block**: it refuses `setblock`, `fill`, `clone`, `place` and `fillbiome`, alone or after `execute … run`, and the owner's commands, one list in `scripts/_lib/commands.py`, before anything is sent. Building is a script's, through the kit, which spares protected places.
- **Settings**, from the environment, then `~/.vfg/config`: `VFG_BRAIN_DIR`, `VFG_BRAIN_MODEL`, `VFG_BRAIN_REASONING`, `VFG_RESPOND_MODEL`, `VFG_RESPOND_REASONING`, `VFG_PLAN_MODEL`, `VFG_PLAN_REASONING`, `VFG_BRAIN_BATCH_S` (60), `VFG_BRAIN_CHECKPOINT` (5), `VFG_BRAIN_HISTORY_HOURS` (6), `VFG_DISPATCH_ACTION_TIMEOUT_S` (20; an action's `timeout_s` wins), `VFG_RUNNING_TTL_S` (30), `VFG_SERVER` (`main`), `VFG_OWNER`, and the OpenRouter key (§11).
