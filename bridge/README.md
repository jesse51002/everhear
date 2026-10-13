# bridge — speech in, game events in, the feed out

On the EC2 box. It takes the players' speech lines over HTTP, tails the Minecraft console for chat
and game events, keeps both in the transcript history, and writes the feed the brain watches.
It also fires incantations: a player's bound phrase, heard by their mod, in a line's transcript, or by
the Jev filter's cast check, is cast in the game engine (`engine/README.md`).
`deploy/` is everything that makes, provisions, updates and retires the box, run from the owner's PC.

## Run it

On the box it is the systemd unit `vfg-bridge`:
`PYTHONPATH=/opt/vfg/lib:/opt/vfg/bridge /opt/vfg/python/bin/python3 -m vfg_bridge`, on the box's
Python 3.12 (below). Two one-shot modes:
`--init-db` applies the schema and exits; `--parse FILE` prints each console line it recognises as
JSON. `vfg_feed.py` is the one feed writer, deployed to `/opt/vfg/lib/`; the Jev filter imports it.
It checks each type's fields as it writes; an `AI` row's are `<say|action|task|result|note> <name|->`,
the name a task's (`galick_gun_styrk`, `[a-z][a-z0-9_]{1,31}`). Older `T<n>` rows still read.

**An older history is brought up to date** whenever the schema is applied: the columns added since
§4's DDL (`db.COLUMNS`), and `lines` and `usage` rebuilt once, in one transaction, when their CHECK
lists are older (`db.CHECKS`: Jev's four classes, `direct_ask` becoming `action_oriented` and
`not_useful` becoming `not_relevant`; the brain's `brain`, `respond` and `planner` usage, and the
incantation alias call's `alias`). `usage`'s added columns are the cache counts and a model call's
`ok`, `error` and `ms`, which `structured()` records for every call. Every other column
on those tables is kept. Deploy the bridge and `jev/` together: once the history has the four
classes, an older Jev filter's verdicts fail the CHECK until it is replaced.

From the repo's root on the owner's PC:

```sh
bridge/deploy/ec2.sh launch|start|stop|status|ssh-ip|retire   # the box itself (AWS CLI v2)
bridge/deploy/deploy.sh provision [--restore]                 # Java, Python 3.12, Minecraft, the firewall, /opt/vfg/.env
bridge/deploy/deploy.sh env                                   # the secrets in VFG_BOX_ENV into /opt/vfg/.env
bridge/deploy/deploy.sh                                       # the bridge, the engine and the units; restarts them
bridge/deploy/deploy.sh engine                                # the game engine alone (engine/README.md)
bridge/deploy/set-secret.sh VFG_CLIENT_TOKEN                  # a value into /opt/vfg/.env, typed hidden
bridge/deploy/client-token.sh                                 # prints the client token, to a terminal only
```

`ec2.sh retire` backs up `/opt/mc` and the history into `VFG_BACKUP_DIR`, checks both copies by
sha256, then terminates the instance. `provision --restore` puts them back on a new box. The last
two retires are kept as `mc-backup.tgz` and `vfg-backup.tgz` with their `.prev.tgz`; each
retire's history is also kept as `vfg-backup-<UTC time>.tgz`, never deleted, mode 600 (it holds
`.env`).

**Python on the box.** `provision` runs `deploy/python.sh`, which puts uv (a pinned release, checked
by sha256) in `/opt/vfg/uv/` and CPython 3.12, installed by uv, behind the link `/opt/vfg/python`.
`/opt/vfg/python/bin/python3` is what `vfg`'s remote programs, `vfg-bridge` and `vfg-jev` run on, and
the game engine's venv is made from, the PC's version; Amazon Linux's own `python3` (3.9) is left to the host. A box
provisioned before this has no `/opt/vfg/python`: `deploy.sh provision` adds it, and `deploy.sh`
refuses until it is there.

## Test it

```sh
cd bridge && python3 -m unittest discover -s tests
```

No network and no EC2, in a few seconds: a temporary database, feed and console log, an injected
clock and poll, and `tests/fixtures/console-26.3.log`, real 26.3 console shapes with placeholder
names. One test runs the bridge as a process and stops it with SIGTERM. Every cast path runs against
a fake engine (`tests/fake_engine.py`: its export, and its socket answering as told): `test_casts.py`
the spotter's, Layer 2's and the Jev filter's casts, the drop rule and the list; `test_engine.py` the
engine's statuses, the engine down or late, and the round trip outside the cast lock;
`test_sounds.py` the phoneme matching.

## Incantations

**Every incantation is the game engine's** (the game engine spec, *Incantations*): a phrase bound, per
player, to one of its abilities (or to a scenario, with its args). The engine owns them and writes them
to its export, `/opt/vfg/engine/incantations.json` (`VFG_ENGINE_EXPORT`), `{"version", "incantations":
[{"id", "player", "phrase", "aliases", "everyday_aliases", "ability" | "scenario" and "args",
"cooldown_s"}]}`, whole, on every change; the bridge only reads it (`vfg_bridge/engine.py`), parsed
again only when the file changes. A missing or unreadable export has none: casts answer `404`, the list
is version 0 and empty, and lines are stored as ever. An entry the bridge cannot use (no phrase, both an
ability and a scenario, aliases that are not a list) is left out alone, with one warning per file. There
are no `@a` bindings: a binding is one player's. Every endpoint takes the client token.

The phoneme matching (Layer 2, the drop rule, the Jev cast check's placing of a phrase) is the bridge's
own, `vfg_bridge/sounds.py`, with CMUdict (`cmudict.gz`, its licence beside it), read once as the bridge
starts. It came from the old kit's incantations module (its store and running copies are gone), and is
the one copy of it: vfg on the PC can import this file by path for its bind checks and the alias call's.

**A cast** goes to the engine, on its control socket `/opt/vfg/engine/engine.sock`
(`VFG_ENGINE_SOCKET`; one JSON line in, `{"op": "cast", "args": {"player", "phrase", "cast_id"},
"timeout_s"}`, one out: `engine/README.md`), with the binding's own phrase (not the alias heard) and
the cast's id. It waits at most `VFG_ENGINE_CAST_WAIT_S` (1 s), and never past `VFG_CAST_WAIT_S`
since the request came; the engine is told that wait (`timeout_s`), and its socket takes a cast it
has not begun off its queue when that passes, so a cast answered `failed` for it never fires late.
**The engine's round trip is outside the bridge's one cast lock**, so no cast waits on another's:
under the lock the cast is checked and its row stored with status `pending`; then the engine is
asked, and the row takes its status. A `pending` row counts as deciding its saying, so the same
saying heard again meanwhile (Vosk, Layer 2, Jev) is `duplicate`; a repeated `client_cast_id` waits
for the first request and answers what it did (`503` only if that first request never finished).
`pending` is never an answer, and lives only while the engine is asked: one a stopped bridge left is
made `failed` when it starts again. **Cooldowns are the ability's, in the engine**: the bridge has none
of its own and no gap between a player's casts; it keeps the duplicate check (one saying heard twice,
by the spotter and Vosk or by Layer 2), the idempotency keys and `stale`. The engine's answer becomes
one the mod knows:

| The engine says | The cast is |
|---|---|
| `fired` | `fired`; announced `Steve cast "fire ball": fireball, fired in the engine` |
| `charging` (the charge-up runs, and it fires when done) | `fired`; announced `…: fireball, charging in the engine` |
| `cooldown` (`remaining_s`) | `cooldown`, error `2.3 s left of its cooldown`; not announced |
| `not_granted`, `unknown`, `failed`, or `ok` false | `failed`, with the engine's words (`Steve has not been granted fireball`); announced |
| nothing in time, `reason` `timeout` | `failed`, `the game engine did not answer the cast within 1.0 s` |
| no socket, nobody listening, `reason` `stopping` | `failed`, `the game engine is not running, so 'fire ball' was not cast (…)` |

The answer is `202 {"id", "status", "error"?}`; the record's `incantation_id` is `engine:<the
binding's id>` (`engine:steve.fire_ball`) and its `action` the ability (or the scenario).

- **`POST /v1/casts`** `{"client_cast_id", "player_uuid", "player_name", "phrase", "heard"?,
  "started_at", "ended_at"}`: the mod's keyword spotter heard `phrase`. `202 {"id", "status",
  "error"?}`, the status `fired`, `cooldown`, `duplicate`, `failed` or `stale`; `404` when the player
  has no such incantation (by its phrase or an alias). A repeated `client_cast_id` answers what the
  first did; a cast of an incantation whose window, widened by `VFG_CAST_SLACK_S` on each side,
  overlaps an earlier cast of it by that player that fired or is `pending`, or one the spotter made
  whatever became of it, is `duplicate`; a cast the bridge has held `VFG_CAST_WAIT_S` since its
  request came is `stale`: stored, never sent or announced. Otherwise it is cast in the engine, as
  above. A fired or failed cast also writes an `events` row (kind `cast`) and a feed line, whatever
  `watch.json` says: `GAME#n HH:MM:SS cast Steve | Steve cast "fire ball": fireball, fired in the
  engine`, or `... failed — <error>`. Also after the answer, a cast that came after its line drops
  that line, by the drop rule, if Jev has not judged it yet.
- **`GET /v1/incantations?player=Steve&after=<version>&wait=<s>`**: `200 {"version", "player",
  "incantations": [{"phrase", "aliases", "cooldown_s"}]}`: the engine's bindings for the player (names
  in any case), one per phrase, at the export's version; `cooldown_s` is the ability's (`0` while it is
  not loaded). With `after` the current version it is held until the export's file changes or `wait` s
  pass (default 25, at most 30), then `304`. It is what the mod's keyword spotter listens for, so each
  incantation's `aliases` leave out its `everyday_aliases` (`Casts.spotted`): those that are everyday
  talk of their own, "ice cream" and "i scream" for "ice beam", which would cast on ordinary
  conversation. Layer 2, the drop rule and the Jev cast check still use every alias.
- **`POST /v1/lines`**, for a player with incantations: Layer 2 (`sounds.match_line` at
  `VFG_INCANT_ALLOWANCE`), on a line of at most `VFG_INCANT_LAYER2_MAX_WORDS` words (6,
  normalised, fillers counted; 0 any length), may fire `transcript` casts, with the line's
  window: its matches best first, one per run of words, one per phrase. A longer line is talk and casts nothing: on 10-08's 843 lines of talk, with each player
  given that night's six incantations, the stored aliases cast falsely on 216 of them, at 6
  words on 7, and 10-11's false casts went from 28 to 2 with every cast found as before
  (`casts.LAYER2_MAX_WORDS`). A one-word phrase
  matches only from the start of a word, so it casts in any sentence and at the start of a longer
  word ("dash" in "dashboard"), and one of fewer than 6 speech sounds only as it is said, its
  mishearings left to its aliases (`incantations._found`). Then the drop rule, on a line of any
  length: a line that
  overlaps one of the player's casts and has at most one word besides its phrase is
  stored with `dropped = 'cast'`, and Jev never sees it; a kept line gets `cast_id`, the cast that
  fired. The casts it overlaps get its `line_id`. A repeated `client_line_id` casts nothing.
- **`POST /v1/jev/casts`** `{"line_id", "phrase", "keep"?}`, the Jev filter's cast check (`jev/README.md`),
  taken only from the box itself (a peer other than loopback is `403`), with the token: Jev judged
  that stored voice line, not yet judged or dropped, a cast of `phrase`. It goes through the same
  lock and checks as every cast (`Casts.jev_cast`), layer `jev`, with the line's window (its `at`
  and `ended_at`, the clock guard's already) and `client_cast_id` `jev:<line id>`, so asking again
  answers what the first did; then the line is stored with `dropped = 'cast'` whatever became of the
  cast, and `cast_id` that cast, unless it did not fire and the line carries a fired one already.
  With `"keep": true` (a long line, the cast said inside other talk, or a short one Jev is not sure
  of) the cast is the same but the
  line is kept, for Jev to route as ever (`"line": "kept"`); it takes the cast's id as its `cast_id`
  when the cast fired and it has none. Jev routes a long line before it asks, so a kept cast is not
  refused for a line Jev judged. A long line comes only when the whole utterance ends, so a phrase
  said at its start would fire seconds late: the phoneme match (`sounds.match_line`, any length,
  allowance 0.4) finds the last place the phrase or an alias is said, its time is placed by word
  between the line's `at` and `ended_at`, and more than 3 s before the line ended the cast is
  `stale` (`the phrase was said about 6.2 s before its line ended, past the 3 s a Jev cast may be`).
  Where the phrase is not found, the cast is allowed.
  A Jev cast never fires late: when `/v1/lines` received its line more than `VFG_JEV_CAST_MAX_AGE_S`
  (2 s) before, on the bridge's monotonic clock, it is `stale` (error `the line is 2.3 s old, past
  the 2 s a Jev cast may be`), recorded and not started, and its line dropped all the same. The
  bridge stores no time it received a line, so `Casts` keeps it in memory, by `client_line_id`, for
  a line of a player with an incantation (no other line can get a Jev cast), noted as Layer 2 runs,
  before the line is stored, for 30 minutes; a line received before the bridge last started has
  none, and its Jev cast is `stale` too.
  `202 {"id", "status", "error"?, "line": "dropped"|"kept"}`; `200 {"line": "dropped",
  "cast_id"}` for a line dropped meanwhile, nothing cast; `404` no such line or incantation; `400` not
  a voice line; `409 {"line": "judged"}` when Jev judged it first. A fired or failed one is announced
  after the answer, as every cast is.

`casts` in `vfg.db`: `(id, at, started_at, ended_at, player_name, phrase, heard, layer
(spotter, transcript or jev), incantation_id, action, copy (the old system's running copy; NULL
since), status (fired, cooldown, duplicate, failed, stale; pending while the engine is asked), error,
line_id, client_cast_id UNIQUE)`, indexed on `(player_name, started_at)`. `at` is the bridge's clock;
the windows are the mod's audio clock, after the same clock guard as lines. The guard keeps the shift it found for a
player's clock and applies it to their later casts and lines while it brings them within bounds,
so a cast and its line stay on one clock. `lines` has `dropped` and `cast_id`.

`tasks` in `vfg.db` is the brain's, one row per implementer task, keyed by its name:
`(name PRIMARY KEY, description, player, status open|done|failed, line, brief, made, variables,
session, result, created_at, updated_at, closed_at)`, `made` and `variables` JSON text. The bridge
creates it, on every start and every `--init-db` (`deploy.sh` runs one), and never writes it: only
`vfg task set` does, for the brain. A history from before it gets the table on the bridge's next start,
and nothing else changes (`PRAGMA user_version` 4).

## Settings

On the box, from `/opt/vfg/.env` (its names are in `deploy/env.example`):

| Key | Default |
|---|---|
| `VFG_CLIENT_TOKEN` | required; the bridge exits 78 without it, and systemd does not restart it |
| `VFG_DB`, `VFG_FEED`, `VFG_WATCH` | `/opt/vfg/vfg.db`, `/opt/vfg/feed.log`, `/opt/vfg/watch.json` |
| `VFG_STATE`, `VFG_CONSOLE_LOG` | `/opt/vfg/state`, `/opt/mc/console.log` |
| `VFG_BRIDGE_BIND`, `VFG_BRIDGE_PORT` | `0.0.0.0`, `8787` |
| `VFG_BASE` | `/opt/vfg`: where the engine's socket and export are, unless set below |
| `VFG_CAST_SLACK_S` | `0.3` s of overlap slack in the duplicate window |
| `VFG_CAST_WAIT_S` | `1.5`: a cast held longer than this since its request is `stale` |
| `VFG_JEV_CAST_MAX_AGE_S` | `2.0`: a Jev cast of a line received longer ago than this is `stale` |
| `VFG_INCANT_ALLOWANCE`, `VFG_INCANT_LAYER2` | `0.25`, `on` (`off` turns Layer 2 off) |
| `VFG_INCANT_LAYER2_MAX_WORDS` | `6`: Layer 2 only on a line of at most this many words; `0` any length |
| `VFG_ENGINE_SOCKET` | `$VFG_BASE/engine/engine.sock`: the game engine's control socket, for its casts |
| `VFG_ENGINE_EXPORT` | `$VFG_BASE/engine/incantations.json`: the engine's bindings, as the engine writes them |
| `VFG_ENGINE_CAST_WAIT_S` | `1.0`: how long an engine binding's cast waits for the engine's answer |

On the owner's PC, from `~/.vfg/config`: `VFG_SSH_HOST`, `VFG_SSH_USER`, `VFG_SSH_KEY`,
`VFG_AWS_REGION`, `VFG_INSTANCE_TYPE`, `VFG_KEY_PAIR`, `VFG_BACKUP_DIR` and `VFG_ACCEPT_EULA`
(see `toolkit/config.example`).

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): §1 where things run, §2 speech in, §3 chat and game
events, §4 the transcript history (it owns the schema), §6 game watchers, §7 the feed and
`vfg_feed`, §11 secrets.
