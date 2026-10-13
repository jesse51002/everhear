# vfg toolkit

The one command every agent on the owner's PC uses to reach the EC2 box. It runs under `uv` (its
first lines say so, PEP 723) on Python 3.12 with pydantic and httpx; only `vfg action`, `vfg cmd`
and `vfg engine` load them, and run as `python3 vfg` where pydantic is missing, those start
themselves again under `uv`. Every call to the box is one SSH call whose remote command is the
box's Python 3.12, `/opt/vfg/python/bin/python3 -u -`, reading `remote/common.py`, the call's
`ARGS` and one program from `remote/` on its stdin, so nothing an agent typed meets a shell on
either machine. The one exception is `vfg feed post`, which runs `/opt/vfg/lib/vfg_feed.py post`
as the contract fixes it, built with `shlex.join`.

## Install

```sh
toolkit/install.sh        # from the repo's root
```

It links `~/.local/bin/vfg` to `toolkit/vfg`, makes `$VFG_HOME` (`~/.vfg`, mode 700) with `brain/`
and `ssh/` in it, and copies `config.example` to `$VFG_HOME/config` (mode 600) if there is none.
Fill `VFG_OWNER` (the brain's), `VFG_SSH_HOST` and `VFG_SSH_KEY` there, then run `vfg up`. Nothing
is deployed to EC2 for vfg itself: the programs in `remote/` travel with each call, and
`remote/places.py` with `vfg engine migrate`'s, as a module of its own. On the PC, vfg loads by path
what lies beside the toolkit: `lib/py/vfg_actions.py` and `vfg_llm.py`, the bridge's phrase rules
(`bridge/vfg_bridge/sounds.py`) and the engine's Config rewrite (`engine/vfg_engine/rewrite.py`).

## One example per command

```sh
vfg up                                         # minecraft, vfg-bridge, vfg-jev, vfg-engine started if down; RCON answering
vfg watch                                      # the feed from the cursor, then follow; --tail N for a person, --after SEQ for code
vfg say 'on it — castle at spawn'              # tellraw @a with the aqua [AI] prefix, stored as an ai line, fed as AI say; --to P --task NAME
vfg cmd 'time set day'                         # the library's command scenario, through engine_start; prints Minecraft's reply
vfg history --since -10m --player Steve        # lines; also --events, --feed, --class, --source, --until, --limit
vfg context --json                             # the brain's context: the feed rows of the last 6 h, oldest first, then the tasks
vfg context mark 812 813 815                   # their processed_at set to now where it was not; prints 3
vfg watchers set join,leave,death,server       # replaces watch.json; --players a,b --contains REGEX; vfg watchers show
vfg lookout set 'requests to build or fight'   # or: vfg lookout set - < lookout.txt; vfg lookout show
vfg jev menu set - < menu.md                   # the brain's action menu, which Jev reads; vfg jev menu show
vfg brain start                                # brain/run, detached; "running pid N" when it is up already; stop, status
vfg task set '{"name": "fire_staff_alex", "description": "A fire staff for Alex", "player": "Alex"}'   # added fire_staff_alex
vfg task list                                  # fire_staff_alex open Alex: A fire staff for Alex; --open, --name, --json
vfg feed post AI 'action -' 'engine_give: gave Alex fire_staff'   # the brain's own records: say, action, task, result, note
vfg action run engine_start scenario=horde args.waves=3 args.mobs='["zombie","husk"]' --task horde_steve   # one JSON line
vfg action list                                # every action: name — its line; --json
vfg action check engine_bind                   # what is wrong with it, or ok
vfg action show engine_start                   # one action in full, as a brief gives it; exit 1 with the names near it
vfg action refs                                # {"version", "commands"}: the data generator's command tree; --version: 26.3
vfg state --json --items                       # who is online and where, time, weather; and each player's items and state
vfg route --task fire_staff_alex < brief.txt   # the example classes for that brief, at most 3, picked by Jev; JSON
vfg prefix                                     # the implementer's static system prompt, the same bytes each time
vfg test fire_staff_alex                       # its test on the test server's own engine: a line per check; -n N, --timeout S
vfg made save                                  # made/<VFG_SERVER>/content/ and tests/ into a new $VFG_MADE_ARCHIVE/2026-10-13-142301/
vfg made restore 2026-10-13                    # and back: that day's latest, or one save by its full name
vfg usage add --component implementer --model deepseek-flash --in 4000 --out 900 --cache-read 310000 --largest 104322
vfg usage flush                                # the queued rows, every structured() call's among them; none, nothing sent
vfg engine status                              # the game engine: its tick, players, instances, classes; exit 6 when it is not running
vfg engine overview --json                     # its classes, instances, places and incantations in one call: what the brain shows
vfg engine places                              # home -368 70 912 overworld (castle_steve), one per line, or none
vfg engine reload fire_staff                   # made/<VFG_SERVER>/content/ deployed, fire_staff.py reloaded: what loaded, what failed
vfg engine grant Steve fireball                # also revoke, give/take, apply/clear, start/stop, spawn, bind/unbind
vfg engine bind Steve 'fire ball' fireball     # an incantation, its aliases made by the alias call; --scenario ID key=value …
vfg engine config set fireball < config.py     # the whole new Config class: spliced in, checked, deployed, reloaded; get, undo
vfg engine migrate --dry-run                   # what the one-time move of the old places and incantations would do
```

Exit codes: 0 done, 1 refused or failed, 2 bad arguments, 3 not configured, 4 EC2 unreachable or
no answer in time, 5 Minecraft not answering RCON (`vfg say`, `vfg state`), 6 the game engine not
running, 124 `vfg action run` or `vfg cmd` when the engine took the request and did not answer in
time (it may still have happened). A failure prints one line on stderr. `vfg --help` and `vfg
<command> --help` list every option. `--timeout S`, before the command, bounds its SSH call: 30 s
by default; `vfg up` 120, `vfg usage` 15, `vfg action refs` 300, `vfg brain status`'s read 10, an
engine call 30 more than the engine's own wait.

Every call goes through one shared SSH master (`ControlPersist` 10 minutes, its socket in
`$VFG_HOME/ssh/`, the host keys in `$VFG_HOME/known_hosts`, accepted the first time); a call the
master cannot carry goes direct once, and a changed host key is exit 4 with the `ssh-keygen -R`
line that clears it.

## The brain's commands

- **`vfg brain start|stop|status`** runs `brain/run` detached, in a process group of its own, with
  `VFG_BRAIN=1` and `VFG_HOME` in its environment; its pid in `$VFG_BRAIN_DIR/brain.pid` and its
  output appended to `brain.log` there. `start` when it runs already prints its pid and succeeds;
  one that exits within 3 s did not start (exit 1). `stop` sends the brain SIGTERM, so it stops its
  implementers itself, waits for its process group, and sends the group SIGKILL after 15 s.
  `status` says running or stopped and lists the open tasks, oldest first, read from the box's
  `tasks` table (`remote/tasks.py`, 10 s at most, or `--timeout`), one `open   fire_staff_alex Alex:
  <its description, cut to 120>` line each (`-` for no player), or `open   none`; when the read
  fails it warns and prints `open   ? (<why>)`. It exits 1 when the brain is not running, whatever
  the read did.
- **`vfg task set '<json>'|-`** writes one of the brain's tasks, the `tasks` table in the box's
  `vfg.db`, which only the brain writes (the bridge creates it). The JSON is one object of the
  table's columns, `name`, `description`, `player`, `status`, `line`, `brief`, `made`,
  `variables`, `session`, `result`, `created_at`, `updated_at` and `closed_at` (`-` reads it from
  stdin), checked here first: an unknown key, a name that is not a task's, a status other than
  `open`, `done` or `failed`, an empty description, a bad player, `made` other than null or an
  object of `content`, `bindings` and `grants`, each a list of names (the classes the task wrote,
  the engine's ids of its bindings such as `steve.fire_ball`, its grants as `player:ability`),
  `variables` other than null or a list of objects (the brain writes null: a class's numbers are
  its Config), or a time `parse_when` cannot read, exits 2 with no SSH call; times go on as ISO UTC
  with milliseconds. One SSH call then adds the task (`added <name>`), or changes only the keys
  given (`updated <name>`), or keeps the row when the given `updated_at` is older than the stored
  one (`kept <name>: a newer write is there`, exit 0). A new task needs a description and never
  takes the name of a library class or an action (a file `engine/library/<name>.py` or
  `actions/<name>.py` in `VFG_REPO`); the box refuses either with exit 1, as it does when `vfg.db`
  has no `tasks` table yet (`restart vfg-bridge, which adds it`). `name` and `created_at` never
  change; `closed_at`, when not given, is set as the status leaves `open` and cleared as it opens
  again.
- **`vfg task list [--open] [--name NAME] [--json]`** prints one `<name> <status> <player|->:
  <description, cut to 120>` line per task, oldest created first, or `none`; `--json` one JSON
  list of the rows, `made` and `variables` decoded.
- **A task's name** is `^[a-z][a-z0-9_]{1,31}$` (`galick_gun_styrk`). `cast`, the bridge's task for
  a cast, is refused (exit 2) everywhere a task is given: `vfg task set` and `list --name`, `--task`
  on `vfg action run`, `vfg say` and `vfg route`, and `--by` on `vfg engine bind`.
- **`vfg say "<text>" [--to P] [--task NAME]`** sends the tellraw (several, split at spaces, when
  one would pass RCON's 1400 bytes), stores the text as an `ai` line (`(to Steve) …` for a
  whisper) and appends the feed's `AI say <name|->` row through the box's `vfg_feed.py`, all in one
  SSH call; every word is the text, wherever `--to` and `--task` sit. What the brain records
  otherwise goes through `vfg feed post AI "<kind> <name|->" "<text>"`, the kind one of `say`,
  `action`, `task`, `result` and `note`, the second field a task's name or `-`; `LINE`, `GAME` and
  `SYS` (no fields) are the other types, and it prints the seq.
- **`vfg context [--since <iso|-6h>] [--json]`** prints the feed rows since then, the last
  `VFG_BRAIN_HISTORY_HOURS` (6) by default, oldest first; `--json` one `{"seq", "at", "line",
  "processed_at"}` object per line, then `{"tasks": [row, …]}`, every row of the box's `tasks`
  table as `vfg task list --json` gives them, then `{"last_seq": N}`, the feed's highest seq, in
  one SSH call. **`vfg context mark <seq> …`** sets `processed_at` to now on those rows where it is
  not set, in one call and one transaction, and prints how many. The bridge adds the column, and
  the `tasks` table, on start; until it has, both exit 1 saying so, a `--json` read for the table
  too.
- **`vfg watch`** is a person's: it prints the feed after the cursor (`$VFG_HOME/cursor`), at most
  `VFG_WATCH_CATCHUP` (100) lines it missed and at most `VFG_WATCH_MAX_PER_MIN` (120) a minute,
  saying what it held back, then follows, reconnecting after 1, 2, 4, 8, 15 and 30 s; one at a
  time, the newest wins (`watch.lock`, `watch.pid`). `--tail N` prints the last N lines, then
  follows, with no cursor and no lock. **`vfg watch --after <seq>`** is code's: the feed from
  seq+1, then follows; it reads and writes no cursor, takes no lock (a person's `vfg watch` runs
  beside it), has no valve and skips no line however far behind it starts.
- **`vfg state [--json] [--items]`** is one SSH call (`remote/state.py`, read-only RCON): `list`,
  each player's `Pos` and `Dimension`, `time query day` (26.3's day timeline) and `time query time`
  (the clock, for the day number), and the weather from an inline predicate,
  `{"type":"minecraft:weather_check","raining":true}`, which follows `weather …` within a few
  seconds, as the rain players see fades in. Without `--json`, an `online`, a `time` and a
  `weather` row; `--json` is `{"online": [{"name", "pos", "dimension"}], "time": {"daytime",
  "day", "phase"}, "weather", "rcon", "ms"}`. Minecraft not answering is a `minecraft` row
  (`"rcon"`: why) and exit 5. What the engine runs is `vfg engine instances`.
- **`vfg state --items`** adds to each online player `"items"`: Minecraft's replies to `data get
  entity <p> Inventory`, its `equipment` (asked only when it has any) and `SelectedItemSlot`, which
  the brain's `listing.py` turns into slot names. And the player's state, in the same SSH call,
  read from `data get entity <p>` `Health`, `foodLevel`, `XpLevel`, `playerGameType` and
  `active_effects`, and `attribute <p> minecraft:armor get` and `minecraft:max_health get`:
  `"health"` (14.5), `"max_health"` (20.0), `"food"` (18), `"xp_level"` (5), `"armor"` (12.0, the
  armor points worn), `"game_mode"` (`survival`, `creative`, `adventure` or `spectator`) and
  `"effects"` (`[{"id": "minecraft:speed", "amplifier": 1, "duration": 600}]`, the amplifier 0
  for level I, the duration in ticks, -1 for infinite; `[]` when Minecraft says there is none).
  Each is `null` when Minecraft's reply could not be read: never a guess. Without `--json`, an
  `items` and a `player` row each: `Steve: 14.5/20 health, 18/20 food, armor 12, level 5,
  survival; effects: speed 2 0:30, night_vision ∞`.
- **`vfg jev menu set|show`** writes or prints `/opt/vfg/menu.md`, the brain's action menu
  (32,000 characters at most), which Jev reads after `lookout.md`; it may be empty.

## Actions

Every action is one of the game engine's control ops (`actions/engine_*.py`, `actions/README.md`):
the brain's whole menu.

- **`vfg action run <action> key=value … [args.<name>=<value> …] [--task <name>]`** loads the
  actions (`lib/py/vfg_actions.py`, the one loader) and checks the values against the action's
  class (an unknown key, a value its field refuses, or a blank one its op needs, such as
  `engine_grant`'s `ability`, exits 2; a number out of range is clamped and a note says so); an
  action that names none of the engine's ops is exit 1. Then it sends the op to the engine as `vfg
  engine <op>` does. A scenario's or a mark's args come as `args.<name>=<value>` words, one each,
  gathered over one `args=<a JSON object>`: a value starting with `[` or `{` is read as JSON, any
  other stays text, which the engine reads as its arg's type (`args.waves=3` as 3). `--task`,
  before or after the action, names the task that ran it: a binding's `by`, a Config change's `by`
  in the content's history. It prints ONE JSON line, `{"ok", "action", "running", "summary",
  "facts", "notes", "error"?}`, whatever happens, and exits 0 when ok: `facts` the engine's reply,
  `running` true only for a scenario that goes on; a refusal is `"ok": false` with the engine's
  error, exit 1. Exit 6 when the engine is not running, 124 when it took the request and did not
  answer in time. `engine_start` waits for a one-off's result, up to `VFG_ENGINE_START_WAIT_S`
  (15 s; the engine's most is 30), and answers with its summary and facts, `instance` among them;
  one that goes on answers `horde is running as scenario:horde#1`, the instance `engine_stop`
  takes. `engine_bind` is a bind (Incantations, below) and `engine_config` a Config set (The game
  engine, below): its `config` is Python source, taken whole, its lines and comments kept, where
  any other text value is one line.
- **`vfg cmd <command words…>`** is one server command, the words joined with spaces and a leading
  `/` dropped, through the library's `command` scenario: `engine_start` with `scenario=command` and
  `args={"commands": [<the command>]}`, waited for. It prints Minecraft's replies, the result's
  `facts.replies`, one a line, else its summary; exit 1 with `minecraft rejected the command` when
  the result says `failed`, or with the engine's refusal (a command that is the owner's among
  them). A command over 1400 bytes is exit 1 (`put it in a class`), and a word your shell expanded
  from `~` is exit 2: single-quote `~` and JSON. TODO(library): `engine/library/` has no `command`
  scenario yet (`CMD_SCENARIO` in `vfg`); until it has, the engine refuses `vfg cmd`.
- **`vfg action list [--json]`** prints `name — its line` per action, or `none`, and warns for each
  file that did not load; `--json` prints the actions cache, `$VFG_HOME/actions.json`: `{"built_at",
  "files", "actions": {name: {"line", "engine", "takes", "timeout_s", "file", "fields": {field:
  {"type", "default", "ge", "le", "choices", "description"}}, "describe"}}, "errors"}`, built again
  when a file in `actions/` is newer or one was added or removed. **`vfg action check <name>`**
  prints `actions/<name>.py: <problem>` lines and exits 1, or `ok: actions/<name>.py`; one that
  does not load prints why and exits 1. **`vfg action show <name>`** prints one action in full, the
  text a brief gives each action (`vfg_actions.describe()`, kept in the cache and read through
  `lib/menu.py`, which the brain loads by path too): its line, `engine <op> · timeout <s> s`, and
  every value with its type, limits, default and description, those the brain fills marked `*`. A
  name that is not an action exits 1 with the names near it; one that did not load, with why.
- **`vfg action refs [--version]`** runs the server jar's data generator in a temp folder under
  `/tmp` on EC2 (about 10 s; 300 s at most) and prints its `commands.json`, about 190 KB, as
  `{"version", "commands"}`; `--version` prints only the version, from `server.jar`'s
  `version.json`.

## The game engine

The game engine (Specs/2026-10-12-Prototype_Game_Engine, `engine/README.md`) is one process on the
box, `vfg-engine`, that runs abilities, items, marks, actors and scenarios written as Python classes:
the library's (`/opt/vfg/engine/library/`, which `bridge/deploy/deploy.sh` sends) and this server's
(`/opt/vfg/made/<server>/content/`, which vfg sends). `vfg engine <op>` sends one of its control ops
through `remote/engine.py`, which speaks its control socket, `<VFG_REMOTE_VFG>/engine/engine.sock`,
through its folder when that path is longer than a socket's address holds, as a realmc stand-in's
is (one connection a request, one JSON line in with `timeout_s`, one out), in one SSH call; several
ops go in one call where a command needs them. Each prints the reply in words, or with `--json` as
it came; exit 0 when the engine did it, 1 when it refused (its error on stderr), 2 for bad
arguments (refused here, with no SSH call), 4 when it took the request and did not answer in time
(it may still do it), 6 when it is not running. An op waits 10 s for the engine, a reload 120 s, a
Config's reload 60 s, a start 10 s more than its wait.

```sh
vfg engine status                              # engine running since …; tick 4821 at 10 a second; p50 1.2 ms, p95 3.4 ms; players; instances by kind; classes; slow; orphans
vfg engine classes                             # scenario  horde  Waves of mobs …  (engine/library/horde.py), then its args: waves (whole number 0..1000; default 1): …
vfg engine overview --json                     # {"ok", "classes", "instances", "places", "incantations"}: one SSH call
vfg engine places                              # home -368 70 912 overworld (castle_steve); --json: {"ok", "version", "world", "places": [{"name", "dim", "pos", "yaw", "by", "at"}]}
vfg engine meta [fireball]                     # one table: kind, id, damage@0, damage@10, damage@20, the required fields, own: name=value …
vfg engine world                               # mob_griefing = false (it was true); held by scenario:horde#1: false at priority 10 since …
vfg engine instances [--kind ability] [--user Steve]   # instance kind id user started ends
vfg engine log [-n 40]                         # <time> <level> <message> {its fields}
vfg engine reload [fireball …]                 # deployed made/main/content: …; loaded: …; refused <file> (<Class>): <check> failed: …; switched: N; stopped …
vfg engine grant Steve fireball                # granted fireball to Steve; revoke: took fireball away from Steve
vfg engine give Alex railgun [--base B]        # gave Alex railgun (a base that is not the class's is refused); take: took every railgun from Alex
vfg engine apply Steve giant [key=value …] [--seconds 30]   # put giant on Steve for 30 s; clear: took giant off Steve
vfg engine start castle at=home size=31 [--wait S]   # the one-off's summary, or "castle is running as scenario:castle#1"
vfg engine stop horde | --instance scenario:horde#1    # stopped horde (scenario:horde#1)
vfg engine spawn ice_queen --at 10 64 -20 | --near Steve   # spawned ice_queen as <uuid>
vfg engine bind Steve 'fire ball' fireball [--by TASK]     # "fire ball" for Steve now casts fireball; aliases fireball; notes on stderr
vfg engine bind Alex fortress --scenario castle at=caster plan=mansion [--cooldown 30]   # "fortress" for Alex now starts castle {…}; …
vfg engine unbind Steve 'fire ball'            # removed Steve's incantation "fire ball"
vfg engine incantations [--player Steve]       # Steve: "fire ball" → casts fireball cooldown 5s; aliases fireball
vfg engine config get|set|undo fireball        # below
vfg engine migrate [--players a,b] [--dry-run] [--force]   # below
```

- **Content** is written on this PC in `made/<VFG_SERVER>/content/<file>.py` (gitignored, as
  `made/` is) and deployed whole to `/opt/vfg/made/<server>/content/`: the folder packed (no
  caches, no dotfiles, 20 MB at most), unpacked beside it on the box and swapped in under
  `run/deploy.lock`, left as it is when every file is the same. `reload` deploys first, then sends
  `reload` with the named files' paths there (none named: every content folder, the library's
  too), and prints what loaded, each check that failed and why (the old version keeps running), how
  many running instances switched and which stopped; exit 1 when a check failed. `--json` is the
  reload's reply with `"deployed": {"folder", "files", "changed", "removed", "swapped"}`.
- **`overview`** asks the engine for its `classes`, `instances`, `places` and `incantations` in one
  SSH call and prints the four, or with `--json` `{"ok": true, "classes": [...], "instances": [...],
  "places": [...], "incantations": [...]}`, each as its op gives it, the places as rows by name.
  One the engine refused is left out, `ok` false, `error` naming it (`instances: …`), the others
  kept, exit 1. The brain's RUNNING NOW, SAVED PLACES, INCANTATIONS and ENGINE are one `vfg engine
  overview --json` (`brain/brain/world.py`). A class's row has `id`, `kind`, `class`, `file` and
  `summary`, and a scenario's or a mark's `args`, `[{"name", "type", "default", "bounds",
  "description"}]`, which `classes` prints under it.
- **Places** are the engine's own, kept per world in its state (a fresh world starts with none):
  `{name: {dim, pos, yaw, by, at}}`, saved by content (`self.save_place`) and read by every `Place`
  arg. `vfg engine places` lists them, `name x y z dimension (by)` a line, or `none`.
- **`start`** sends `wait_s`, `--wait` or `VFG_ENGINE_START_WAIT_S` (15 s), 0 to 30: the engine
  answers with a one-off's result when it ends within it, else with the instance that goes on.
  **`stop`** takes a scenario's name, stopping every run of it, or one `--instance`.
- **`config get <class>`** prints the class's Config exactly as written on this PC, `made/main/
  content/fireball.py, lines 10 to 33: the Config of Fireball, as written`, then its lines; the
  engine says which file and class (its `config_get`), a class whose file is not in
  `made/<VFG_SERVER>/content/` is refused (exit 1: only this server's content is rewritten here),
  and a box that runs another version than this PC holds is a warning. `--json` is `{"ok", "class",
  "class_name", "file", "start", "end", "source"}`. **`config set <class>`** takes the whole new
  Config class on stdin, at any indentation: the engine's `engine/vfg_engine/rewrite.py` (standard
  library, loaded by its path) splices it into exactly the old one's lines and runs the static
  checks (it parses, the same fields and formulas, nothing outside the Config changed, and only
  values: every formula's code and the `class Config` line as they were, each value a literal or
  `Field(<literals>)`); a failed one is exit 1 with the checks' own words, several numbered `(1) …;
  (2) …`, and nothing is sent. Then the old file is kept, the new one written, deployed and reloaded
  under `$VFG_HOME/engine.lock`; when the engine refuses it (a reload check: in bounds, damage at
  three powers, imports), or is down, or does not answer, this PC's file is put back and deployed
  again, and the error is the answer, saying so. A Config already as written changes nothing
  (`"changed": []`). **`config undo <class>`** splices the Config of that class's last kept version
  back into the file as it is now, checked, deployed and reloaded the same way, and moves the
  version to `undone/`, so the next undo goes one further back. The kept versions are
  `made/<server>/content/.history/<file>/<YYYYmmdd-HHMMSS-ffffff>-<Class>.py`, the whole file before
  each change, and `.history/log.jsonl` one line a change or a refusal (`at`, `op`, `class`,
  `class_name`, `file`, `ok`, `kept` or `error`, `by`); a deploy skips dot folders, so none reaches
  the box.
- **`migrate`** moves what the old system kept into the engine, once: one SSH call reads the
  places in Minecraft's command storage `everhear:places` over RCON and the old incantation store,
  `/opt/vfg/incantations.json` (`remote/migrate.py`, with `remote/places.py`; it writes nothing),
  then one `places_import`, which the engine takes once per world (`--force`: again; a name already
  saved is kept and reported), and one `bind` a binding, with its old aliases, everyday aliases and
  `by`, and no alias call. A binding goes onto the class that took its action over, by `MIGRATE_MAP`
  in `vfg`: an ability it casts (its old values stay behind, a note: the Config is one version for
  everyone), or a scenario it starts with its old values as args (a caster field as `caster`) and
  its cooldown. An `@a` binding is split over `--players`, else over every player another binding
  names; an action with no row is left behind and said, never guessed. It prints `places: 2 found,
  imported`, a `moved KrownJM "freeze" (blizzard_freeze): KrownJM "freeze" casts blizzard` line a
  binding and a `left …: why` line for the rest; `--json` is `{"ok", "dry_run", "places": {"found",
  "imported", "skipped", "refused", "version", "world", "ok"}, "bindings": [{"from", "player",
  "phrase", "kind", "id", "args", "notes", "ok"?, "error"?}], "left": [{"from", "why"}]}`. Exit 1
  when anything was left behind or refused; the rest still moves. `--dry-run` reads and says, and
  sends nothing to the engine. TODO(library): `MIGRATE_MAP` holds made/main's three rows
  (`blizzard_freeze` → `blizzard`, `fire_aura_krown` → `flame`, `fireball_cast` → `fireball`); a
  row for each library action an incantation may have bound comes as its class is ported.

## Incantations

An incantation is a phrase that, said by its player, casts one of that player's engine abilities,
or starts a scenario with args set once, with no model on the path (Specs/2026-10-10-
Prototype_Voice_Triggers, 2026-10-11-Prototype_Fast_Casts). The engine keeps them, per player,
never `@a`, and writes them to its export, `/opt/vfg/engine/incantations.json`, which the bridge
reads as players cast (`bridge/README.md`, *Incantations*). vfg makes a binding's aliases first,
with the bridge's own phrase rules (`bridge/vfg_bridge/sounds.py`, the one copy, loaded by its
path).

- **`vfg engine bind <player> <phrase> <ability>`**, or **`--scenario <id> key=value …`** with
  **`--cooldown S`** (a scenario binding's own, the engine's 3 s by default; an ability's cooldown
  is its Config), and `engine_bind` the same way: the phrase is normalised and checked
  (`check_phrase`), the player one player's name; an ability or a scenario, one of them, and args
  only with a scenario, else exit 2. An arg left as `caster` is filled with whoever casts it, for
  a `Player` or `Place` arg only (the engine checks the rest as a start would). The alias call runs
  in a thread while one SSH call reads the engine's classes, the player's bindings and the
  abilities granted to them; a class the engine does not have is refused (exit 1, naming those it
  has) before the bind. Then the aliases, and as notes (on stderr once it is bound, or in the
  line's `notes`): the bind warnings against the player's other phrases (a short phrase, with a
  second word suggested for one word; one that sounds like, or starts like, another of theirs, a
  one-word phrase found as Layer 2 finds it: `bind_warnings`), the everyday note (below), `Steve
  does not have fireball yet: saying it casts nothing until it is granted (engine_grant)`, and
  `replaces Steve's "fire ball", which cast fireball`. The engine's `bind` follows in a second SSH
  call, with the aliases, the everyday aliases and `--by`, the task that bound it: `"fire ball"
  for Steve now casts fireball; aliases fireball`, its `facts` the binding's `id`
  (`steve.fire_ball`), its `entry` as the engine stored it (its aliases as kept and its cooldown)
  and `alias_call`.
- **The aliases** are the code's (the phrase joined, compound words split: `code_aliases`) and one
  `structured()` call's (`lib/py/vfg_llm.py`, its OpenRouter key), 24 at most:
  `VFG_INCANT_ALIAS_MODEL`, by default Gemini 3.8 Flash at light thinking (`low`), OpenRouter
  picking the host, 20 s at most; `off` skips it, and a failure is a note (`aliases from code only:
  the alias call failed (…)`). Its prompt is `INCANT_ALIAS_PROMPT` in `vfg`: the incantation, each
  word's pronunciation (CMUdict's, in ARPAbet; for a word it lacks, "not in the dictionary (a
  made-up word)" and a guess from its letters: `pronunciation`), five rules (the same sounds in the
  same order with one or two near ones changed, the same length, written as a recognizer writes,
  never the phrase or a synonym, the likeliest first) and exactly 20 variants
  (`INCANT_ALIAS_COUNT`), each with its ARPAbet sounds. The code then drops (`checked_variants`)
  the phrase itself, repeats, one 3 speech sounds longer than the phrase or more, each sound
  weighed as Layer 2 weighs one put in (an H 0.25, an "uh" 0.5, any other 1), so a made-up word
  keeps respellings with H and "uh" sounds ("come may hum may uh"), and one more than 0.35 from it
  in sound (the weighted edit distance of their phonemes, Layer 2's costs, over the phrase's
  count); `merge_aliases` drops those under 4 speech sounds and past the 24. What was dropped goes
  in `facts.alias_call.dropped` (`"call me hammer ha: 3 speech sounds longer"`), never in a note,
  beside the call's `model`, `cost` and `ms`. On 2026-10-12 (ten phrases, three runs) it made 20
  every time, at a median 6.7 s and at most 12.3 s, $0.0036 a call. Its usage row (component
  `alias`, the phrase its ref) is sent by a `vfg usage flush` in its own session, nobody waiting.
- **Everyday talk**: the call also says whether the phrase is everyday talk, a note (`"heal" is
  everyday talk, so it will fire whenever it is said in conversation.`), none when the call is off
  or fails. And it flags each variant that is a common phrase of its own, said meaning something
  else (`common_phrase`: "ice cream", "i scream", "nice beam" and "eyes beam" for ice beam, never
  "ice been", "juicy stake" or "destroyed everything"): those kept among the aliases, never the
  code's own, are the binding's `everyday_aliases`, which the bridge leaves out of what the mod's
  keyword spotter listens for (`GET /v1/incantations`), so ordinary talk does not cast; Layer 2 and
  the Jev cast check still use them, as speech to text writes "Ice cream." for "ice beam". A stray
  flag on a mishearing ("hi step") only takes it from the spotter.
- **`vfg engine unbind <player> <phrase>`** removes the player's binding, the phrase checked as a
  bind checks it; one the player has not is refused (exit 1). **`vfg engine incantations [--player
  P]`** lists the engine's bindings, one a line: `Steve: "fire ball" → casts fireball cooldown 5s;
  aliases fireball, fire bowl`, or `Alex: "fortress" → starts castle {"at":"caster","plan":"mansion"}
  cooldown 30s; …`, the everyday aliases after the aliases (`; everyday, not spotted: ice cream, i
  scream`); `--json` is the engine's `{"ok", "version", "incantations": [{"id", "player", "phrase",
  "aliases", "everyday_aliases", "ability", "scenario", "args", "by", "cooldown_s"}]}`.

## What made/ keeps

`made/<VFG_SERVER>/` holds what this server's implementers made: `content/`, the engine's classes,
and `tests/`, their tests. **`vfg made save`** copies both (each that has files; no caches, no
dotfiles) into a new folder `$VFG_MADE_ARCHIVE/<YYYY-MM-DD-HHMMSS>/`, named to the second, and
never deletes one; exit 1 when there is nothing to save. **`vfg made restore <save|date>`** puts a
save's `content/` and `tests/` back, by its full name or a date (that day's latest), keeps what they
replace in `made/.replaced/<server>-<YYYYmmdd-HHMMSS>/`, and says `vfg engine reload loads it`; a
save with neither (scripts and actions only) restores nothing, exit 1.

## The router and the implementer's prompt

- **`vfg route [--task NAME] [--ref REF] < brief`** reads the implementer's brief on stdin, and
  with `--task` the task's description (else its brief) and `<player> said: <line>` from the box's
  `tasks` table, in one SSH call of at most 1.5 s (`VFG_ROUTE_TIMEOUT_S` if that is less), which
  comes out of the router's budget. A task the box does not have warns `<name> is not a task on
  the box — routing on the brief alone`, a read that fails `cannot read <name> from the box
  (<why>) — routing on the brief alone`, and both route on. It makes one card per content class,
  the library's (`engine/library/`) then this server's (`made/<VFG_SERVER>/content/`, one whose id
  the library has left out), read on this PC by `lib/content.py`, which parses each file and never
  runs it: one class a file, its id, kind, source, path and first line. It asks Jev about all of
  them in one call through `remote/jev.py` on the box, one `choice` question per class,
  `class_<id>`: `runs_it`, `close` or `no`. It prints JSON on stdout, `{"task", "classes":
  [{"name", "kind", "source", "verdict", "p_runs_it", "p_close", "path"}], "fallback", "ms",
  "tokens"}`, with `"cut"` (the picks past the cap), `"why"` (what went wrong) and `"unanswered"`
  (the questions Jev gave no usable answer for) when there are any, and a log line on stderr.
- **Picks:** at most 3 classes of those whose `runs_it` and `close` together reach
  `VFG_ROUTE_LOAD` (0.3), those that run the job as they are first, then by how sure Jev is; what
  the cap leaves out is listed under `"cut"`. Past `VFG_ROUTE_TIMEOUT_S` (3), or on any error, it
  picks by words instead (`lib/menu.py`'s words, `"verdict": "words"`): every class whose name
  shares a word with the job, or that shares three with its name and first line, leaving out words
  on more than a quarter of the cards, most shared first (`"fallback": true`); a class Jev did not
  answer for is judged by words the same way. It writes one usage row, `--component jev --ref
  <REF>` (by default the task's name), in the background.
- **`vfg prefix`** prints the part of an implementer's system prompt that never changes between
  spawns, so the provider reads it from the cache (`lib/prefix.py`): `engine/WRITING.md` whole,
  `engine/KIT.md` whole when it is there, then every library class (`engine/library/<id>.py`) by
  id, `=== fireball (ability) — <its first line> ===` and its `class Config` and `class Args` as
  written (a file that does not read says why). Nothing from `made/`, no date, no count. With no
  library class, one line names `engine/examples/`.
- **`--cache-read N` and `--cache-write N`** on `vfg usage add` are the input tokens read from and
  written to the prompt cache, in two columns of `usage` that the bridge adds on start; until it
  has, the row goes in without them and one line on stderr says so. **`--largest N`** is a run's
  largest request in tokens (an implementer's), in the column `largest_tokens`, the same way.
  `--component` is one of `jev`, `brain`, `respond`, `implementer`, `planner`, `alias`,
  `conversation` and `orchestrator`; `--ref` is at most 64 characters.
- **`vfg usage flush`** sends `usage.pending.jsonl` and writes no row of its own. Every
  `structured()` call appends its row there under `usage.pending.lock`, in the format `vfg usage
  add` queues its own, plus `ok`, `error` and `ms`, the bridge's added columns (a box without them
  gets the rest, and one line on stderr says so); `cost_usd` is null for a call cut off with an
  attempt in flight. A call claims the queue by moving it to `usage.sending.<pid>.jsonl`, so two
  never send one row, and what does not get there is queued again. The brain runs it after its
  calls, never two at once, and vfg after an alias call, each in its own session, nobody waiting.
- **The queue's folder** is `VFG_USAGE_QUEUE` from the environment, else `$VFG_HOME`; `vfg usage`
  and `structured()` read it alike. The replays (`integration/`) point it at their own output, so
  their rows never reach the box unless sent on purpose: `VFG_USAGE_QUEUE=OUT vfg usage flush`.

## A content class's test, on the test server

- **`vfg test <name> [-n N] [--timeout S]`** runs a task's test, `made/<VFG_SERVER>/tests/<name>.py`
  (it defines `test(t)`), on the **test server**: a second Minecraft server with Carpet's fake
  players, never the live one, on an engine of its own (`python -m vfg_engine.livetest`,
  `engine/README.md`, "Tests of content: livetest"; its docstring has every verb of `t`). A name
  with no test gets the skeleton, written there, one `t.check` per DONE WHEN item to fill in, exit
  1. It reads the task's bindings from the live engine (those whose `by` is the task, or whose id
  its row's `made.bindings` holds; none, with a warning, when the live engine cannot be read), then
  in one SSH call to the test server (`remote/engine.py`'s `test` op) deploys
  `made/<VFG_SERVER>/content/` and `tests/` there, as a reload deploys content, and runs livetest
  with the test server's `server.properties`, its state fresh in `<vfg>/state/engine`, the content
  folders the library (`<vfg>/engine/library/`, as deployed there) and this server's, the bindings
  bound first, a 60 s boot and the timeout. Its engine is the test server's own
  `<vfg>/engine/.venv/bin/python`, else the live server's on the same host; with neither, exit 3.
  It prints livetest's report (`ENGINE`, `AT`, `CAST`, `START` lines, one line per check, `PASS`,
  `FAIL` or `CAN'T TELL` with what decided it, and `RESULT: …`), and after a failure the test
  engine's last `-n` (15) log lines as `LOG` lines. Exit 0 when no check failed; 1 when one did
  (`the test failed: 1 of 2 checks: it zaps`), when the test could not run as written, when its
  engine did not start or load the content (each load error said), and when it ran past
  `--timeout` (`VFG_TEST_TIMEOUT_S`, 60; 5 to 600) and was stopped; 3 when the server is not a test
  server (no `.vfg-test-server` marker beside its `server.properties`).
- **What changed since its last run** prints first, as `CHANGED` lines: a check removed, added or
  renamed, a bar lowered or raised (a number in a check's verdict smaller or larger), a check
  changed otherwise, a target moved, changed or removed, the trigger changed; comments and layout
  are no change. The last run's file is kept in `$VFG_HOME/tests/<server>/<name>.py`.
- **The test server is a second box**: `VFG_TEST_BOX` names a file of its settings, `KEY=value`
  lines as `~/.vfg/config` has them, of which `VFG_SSH`, `VFG_SSH_HOST`, `VFG_SSH_USER`,
  `VFG_SSH_KEY`, `VFG_REMOTE_VFG` and `VFG_REMOTE_MC` are read over the live box's. On realmc it is
  a server's own config, `$REALMC_ROOT/<name>/home/.vfg/config` (`integration/realmc/up <name>`),
  its `mc/` folder holding the marker. A second instance on the EC2 box needs only
  `VFG_REMOTE_VFG` and `VFG_REMOTE_MC`. None set, the file unreadable, or the live server itself
  (the same host and the same vfg or Minecraft folder; on the same host the box also checks the
  folders after links, and the RCON port): exit 3, nothing run.

## Files on this PC

In `$VFG_HOME` (`~/.vfg`):

| File | What |
|---|---|
| `config` | `KEY=value` lines (`export` allowed, quotes taken off, `#` comments), mode 600 |
| `vfg.log` | one line a call: `<ISO time> ["argv", …] exit=N <ms>ms`, each word cut to 200 characters; past 5 MB it becomes `vfg.log.1` |
| `ssh/`, `known_hosts` | the shared SSH master's socket; the box's host keys |
| `cursor`, `watch.lock`, `watch.pid` | a person's `vfg watch`: the last seq it printed; one watch at a time |
| `brain/brain.pid`, `brain/brain.log` | the brain's (`VFG_BRAIN_DIR`) |
| `actions.json`, `actions.lock` | the actions cache (`lib/py/vfg_actions.py`), above |
| `engine.lock` | held while a Config is set or undone |
| `usage.pending.jsonl`, `usage.pending.lock`, `usage.sending.<pid>.jsonl` | the usage queue (in `VFG_USAGE_QUEUE` when set): one row a line, as `vfg usage add` writes it |
| `tests/<server>/<name>.py` | each test as it last ran |

In the repo, gitignored: `made/<server>/content/` and its `.history/`, `made/<server>/tests/`, and
`made/.replaced/`.

## Tests

```sh
python3 -m unittest discover -s toolkit/tests      # from the repo's root; about 10 s
```

No EC2 and no network, and where `python3` has no pydantic or httpx the run starts itself again
under `uv`, as vfg does. `tests/harness.py` makes each test a box in a temp folder: the PC (`HOME`,
`VFG_HOME`, a repo) and the EC2 box (`vfg.db` with its `tasks` table, `feed.log`,
`tests/fake_vfg_feed.py` as the feed's writer). vfg's `main()` runs in the test's process, and so
does every remote program it sends: the harness replaces `Ssh`'s one subprocess call and nothing
else, and gives the program the box's environment, none of the PC's. A test class shares one
`fake_rcon.py`, Minecraft's RCON in-process on localhost; `tests/standins.py` puts the repo's own
`actions/_types.py` and engine actions in the test's repo. vfg's own waits are short in the tests
(`vfg brain start`'s 3 s check 0.1 s, `stop`'s 15 s 1 s). No test reaches a model: the box's config
turns the alias model off unless a test names one, with `vfg_llm` mocked, and no OpenRouter key
passes in.

- `test_vfg.py`: every other command at its edge, a fake `java` for the data generator, and
  `tests/bin/`'s fake `systemctl`, `sudo` and `journalctl` for `vfg up`; the owner's commands kept
  in one list (`engine/vfg_engine/mc/commands.py`); `TestRealFeedWriter` runs the bridge's
  `vfg_feed.py` in place of the fake. The player-state test of `vfg state --items` reads 26.3's
  recorded replies from `scripts/_test/mc_26_3.py`, and two RCON lock tests run
  `scripts/_lib/mc.py` beside vfg's client: each skips, saying why, where its file is missing.
- `test_engine.py`: every `vfg engine` op against `fake_engine.py`, the engine's control socket
  served in this process, its replies shaped as `engine/vfg_engine/ops.py`'s: the reads, the ops,
  the content's deploy and reload, a Config set, refused and put back, undone, binds and the alias
  call (`TestAliases`: its prompt, the code's checks, the everyday aliases), the menu's engine
  actions through `vfg action run`, and `migrate`; a Config is held to the engine's own
  `engine/vfg_engine/rewrite.py`. `TestRealEngine` runs the same against the engine itself,
  `python -m vfg_engine --fake-io` (no Minecraft), and skips, saying why, when it cannot start.
- `test_task.py`: `vfg task set` and `list` on the box's `tasks` table, `vfg context --json`'s
  tasks, and a box without the table.
- `test_test.py`: `vfg test` against a second box whose engine's Python is `fake_livetest.py`
  (livetest's command line, noted, and the report the test file asks for; the real runner is
  tested in `engine/tests/box/`): the content, the test and the task's bindings sent there, a
  changed test shown first, a failure, one that cannot run and one past its time, its skeleton,
  and a test server that is the live one.
- `test_route.py`: the router over the engine's classes, `remote/jev.py` against
  `fake_jev_route.py`, `vfg prefix`, `vfg action show`, `vfg usage add`'s added columns and
  `structured()`'s queued rows.
- `test_e2e.py`: a handful with vfg as a process, through `tests/fake_ssh`, which runs the remote
  command here with bash: a command and its SSH options, closed descriptors, and watches
  following the feed.

`tests/route_eval/` is an eval, not a test: it calls Jev for real.

## Settings

From the environment, else `$VFG_HOME/config`; an empty value is the default, and a number outside
its range is exit 3. `config.example` lists the PC's settings.

| Key | Default | What |
|---|---|---|
| `VFG_HOME` | `~/.vfg` | this PC's folder; from the environment only |
| `VFG_SSH_HOST`, `VFG_SSH_KEY` | none: exit 3 | the box, and the path of its SSH key |
| `VFG_SSH_USER`, `VFG_SSH` | `ec2-user`, `ssh` | |
| `VFG_REPO` | the repo `toolkit/` is in | where `actions/`, `engine/library/`, `made/` and `brain/run` are read |
| `VFG_REMOTE_VFG`, `VFG_REMOTE_MC` | `/opt/vfg`, `/opt/mc` | the bridge's and Minecraft's folders on the box |
| `VFG_SERVER` | `main` | this server: `made/<VFG_SERVER>/` here, `/opt/vfg/made/<VFG_SERVER>/content/` there |
| `VFG_CHAT_PREFIX` | `[AI] ` | what `vfg say` puts first, in aqua |
| `VFG_WATCH_CATCHUP`, `VFG_WATCH_MAX_PER_MIN` | `100`, `120` | a person's `vfg watch` |
| `VFG_BRAIN_DIR` | `$VFG_HOME/brain` | the brain's pid and log |
| `VFG_BRAIN_HISTORY_HOURS` | `6` | `vfg context`'s default |
| `VFG_ENGINE_START_WAIT_S` | `15`, 0 to 30 | how long a start waits for a one-off's result |
| `VFG_INCANT_ALIAS_MODEL` | `google/gemini-3.8-flash` | the alias call's model; `off` for none |
| `VFG_ROUTE_TIMEOUT_S` | `3`, 0.1 to 60 | `vfg route`'s budget |
| `VFG_ROUTE_LOAD` | `0.3`, 0 to 1 | what loads a class for an implementer: `runs_it` and `close` together |
| `VFG_TEST_BOX` | none: `vfg test` exits 3 | the file of the test server's settings |
| `VFG_TEST_TIMEOUT_S` | `60`, 5 to 600 | how long a test may run |
| `VFG_MADE_ARCHIVE` | `/var/home/jm/Documents/EverHear/Internal/made` | where `vfg made` keeps its saves |
| `VFG_USAGE_QUEUE` | `$VFG_HOME` | the usage queue's folder; from the environment only |
| `VFG_WATCH_SILENCE_S`, `VFG_WATCH_UNREACHABLE_S` | `35`, `60` | for the tests: the silence after which a watch reconnects, and when it says EC2 is unreachable; from the environment only |
