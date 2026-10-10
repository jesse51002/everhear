# bridge — speech in, game events in, the feed out

On the EC2 box. It takes the players' speech lines over HTTP, tails the Minecraft console for chat
and game events, keeps both in the transcript history, and writes the feed the conversation watches.
It also fires incantations: a player's bound phrase, heard by their mod, starts its action on the box.
`deploy/` is everything that makes, provisions, updates and retires the box, run from the owner's PC.

## Run it

On the box it is the systemd unit `vfg-bridge`:
`PYTHONPATH=/opt/vfg/lib:/opt/vfg/bridge /opt/vfg/python/bin/python3 -m vfg_bridge`, on the box's
Python 3.12 (below). Two one-shot modes:
`--init-db` applies the schema and exits; `--parse FILE` prints each console line it recognises as
JSON. `vfg_feed.py` is the one feed writer, deployed to `/opt/vfg/lib/`; the Jev filter imports it.

From the repo's root on the owner's PC:

```sh
bridge/deploy/ec2.sh launch|start|stop|status|ssh-ip|retire   # the box itself (AWS CLI v2)
bridge/deploy/deploy.sh provision [--restore]                 # Java, Python 3.12, Minecraft, the firewall, /opt/vfg/.env
bridge/deploy/deploy.sh env                                   # the secrets in VFG_BOX_ENV into /opt/vfg/.env
bridge/deploy/deploy.sh                                       # the bridge and the units; restarts them
bridge/deploy/set-secret.sh VFG_CLIENT_TOKEN                  # a value into /opt/vfg/.env, typed hidden
bridge/deploy/client-token.sh                                 # prints the client token, to a terminal only
```

`ec2.sh retire` backs up `/opt/mc` and the history into `VFG_BACKUP_DIR`, checks both copies by
sha256, then terminates the instance. `provision --restore` puts them back on a new box.

**Python on the box.** `provision` runs `deploy/python.sh`, which puts uv (a pinned release, checked
by sha256) in `/opt/vfg/uv/` and CPython 3.12, installed by uv, behind the link `/opt/vfg/python`.
`/opt/vfg/python/bin/python3` is what the scripts, `vfg`'s remote programs, `vfg-bridge` and
`vfg-jev` run on, the PC's version; Amazon Linux's own `python3` (3.9) is left to the host. A box
provisioned before this has no `/opt/vfg/python`: `deploy.sh provision` adds it, and `deploy.sh`
refuses until it is there.

## Test it

```sh
cd bridge && python3 -m unittest discover -s tests
```

No network and no EC2: a temporary database, feed and console log, a stub Jev filter, and
`tests/fixtures/console-26.3.log`, real 26.3 console shapes with placeholder names.

## Incantations

The store is `/opt/vfg/incantations.json`, written by the incantation actions' box script; the
bridge only reads it, through the kit's `incantations` module (`scripts/_lib/incantations.py`), and
starts copies through its `instances`. Both are imported from `VFG_KIT`, and imported again when
the kit's `.vfg-stamp` changes, so a deploy of the kit needs no restart. Without them, casts and the
list answer `503` and lines are stored as before. Every endpoint takes the client token.

- **`POST /v1/casts`** `{"client_cast_id", "player_uuid", "player_name", "phrase", "heard"?,
  "started_at", "ended_at"}`: the mod's keyword spotter heard `phrase`. `202 {"id", "status",
  "copy"?, "error"?}`, the status `fired`, `cooldown`, `duplicate` or `failed`; `404` when the player
  has no such incantation (their own or `@a`). Under one lock with Layer 2's casts: a repeated
  `client_cast_id` answers what the first did; a cast of an incantation whose window, widened by
  `VFG_CAST_SLACK_S` on each side, overlaps an earlier cast of it by that player, whatever its
  status, is `duplicate`; its `cooldown_s` since it last fired for the player, then
  `VFG_CAST_GAP_S` since any of theirs fired, are `cooldown`. Otherwise its copy starts (`instances.Box.start`, task `cast`, every
  `caster_fields` value the caster's name) and the answer goes as soon as it is spawned; the copy
  is reaped on a thread. A fired or failed cast also writes an `events` row (kind `cast`) and a feed
  line, whatever `watch.json` says: `GAME#n HH:MM:SS cast Steve | Steve cast "fire ball": command,
  running as command_2`, or `... failed — <error>`. Also after the answer, a cast that came after
  its line drops that line, by the drop rule, if Jev has not judged it yet.
- **`GET /v1/incantations?player=Steve&after=<version>&wait=<s>`**: `200 {"version", "player",
  "incantations": [{"phrase", "aliases", "cooldown_s"}]}`, the player's own then the `@a` ones. With
  `after` the current version it is held until the store changes or `wait` s pass (default 25, at
  most 30), then `304`.
- **`POST /v1/lines`**, for a player with incantations: Layer 2 (`match_line` at
  `VFG_INCANT_ALLOWANCE`) may fire `transcript` casts, with the line's window: its matches best
  first, one per run of words, one per phrase (the player's own before `@a`). Then the drop rule:
  a line that overlaps one of the player's casts and has at most one word besides its phrase is
  stored with `dropped = 'cast'`, and Jev never sees it; a kept line gets `cast_id`, the cast that
  fired. The casts it overlaps get its `line_id`. A repeated `client_line_id` casts nothing.

`casts` in `vfg.db`: `(id, at, started_at, ended_at, player_name, phrase, heard, layer,
incantation_id, action, copy, status, error, line_id, client_cast_id UNIQUE)`, indexed on
`(player_name, started_at)`. `at` is the bridge's clock, which the cooldowns use; the windows are the
mod's audio clock, after the same clock guard as lines. The guard keeps the shift it found for a
player's clock and applies it to their later casts and lines while it brings them within bounds,
so a cast and its line stay on one clock. `lines` has `dropped` and `cast_id`.

## Settings

On the box, from `/opt/vfg/.env` (its names are in `deploy/env.example`):

| Key | Default |
|---|---|
| `VFG_CLIENT_TOKEN` | required; the bridge exits 78 without it, and systemd does not restart it |
| `VFG_DB`, `VFG_FEED`, `VFG_WATCH` | `/opt/vfg/vfg.db`, `/opt/vfg/feed.log`, `/opt/vfg/watch.json` |
| `VFG_STATE`, `VFG_CONSOLE_LOG` | `/opt/vfg/state`, `/opt/mc/console.log` |
| `VFG_BRIDGE_BIND`, `VFG_BRIDGE_PORT` | `0.0.0.0`, `8787` |
| `VFG_BASE`, `VFG_MC` | `/opt/vfg`, `/opt/mc`: the box a cast starts its copy on |
| `VFG_KIT` | `$VFG_BASE/scripts/_lib` |
| `VFG_INCANTATIONS` | `$VFG_BASE/incantations.json`; for tests only: the box script and vfg use that path |
| `VFG_OWNER`, `VFG_LIVE_POLL_S` | none, `0.5`: passed to a cast's copy |
| `VFG_CAST_GAP_S`, `VFG_CAST_SLACK_S` | `1.0` s between a player's casts, `0.3` s of overlap slack |
| `VFG_INCANT_ALLOWANCE`, `VFG_INCANT_LAYER2` | `0.25`, `on` (`off` turns Layer 2 off) |

On the owner's PC, from `~/.vfg/config`: `VFG_SSH_HOST`, `VFG_SSH_USER`, `VFG_SSH_KEY`,
`VFG_AWS_REGION`, `VFG_INSTANCE_TYPE`, `VFG_KEY_PAIR`, `VFG_BACKUP_DIR` and `VFG_ACCEPT_EULA`
(see `toolkit/config.example`).

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): §1 where things run, §2 speech in, §3 chat and game
events, §4 the transcript history (it owns the schema), §6 game watchers, §7 the feed and
`vfg_feed`, §11 secrets.
