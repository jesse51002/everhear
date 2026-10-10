# bridge — speech in, game events in, the feed out

On the EC2 box. It takes the players' speech lines over HTTP, tails the Minecraft console for chat
and game events, keeps both in the transcript history, and writes the feed the brain watches.
`deploy/` is everything that makes, provisions, updates and retires the box, run from the owner's PC.

## Run it

On the box it is the systemd unit `vfg-bridge`:
`PYTHONPATH=/opt/vfg/lib:/opt/vfg/bridge /opt/vfg/python/bin/python3 -m vfg_bridge`, on the box's
Python 3.12 (below). Two one-shot modes:
`--init-db` applies the schema and exits; `--parse FILE` prints each console line it recognises as
JSON. `vfg_feed.py` is the one feed writer, deployed to `/opt/vfg/lib/`; the Jev filter imports it.

**An older history is brought up to date** whenever the schema is applied: the columns added since
§4's DDL (`db.COLUMNS`), and `lines` and `usage` rebuilt once, in one transaction, when their CHECK
lists are older (`db.CHECKS`: Jev's four classes, `direct_ask` becoming `action_oriented` and
`not_useful` becoming `not_relevant`; the brain's `brain` and `respond` usage). Every other column
on those tables is kept. Deploy the bridge and `jev/` together: once the history has the four
classes, an older Jev filter's verdicts fail the CHECK until it is replaced.

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

No network and no EC2, under a second: a temporary database, feed and console log, an injected
clock and poll, and `tests/fixtures/console-26.3.log`, real 26.3 console shapes with placeholder
names. One test runs the bridge as a process and stops it with SIGTERM.

## Settings

On the box, from `/opt/vfg/.env` (its names are in `deploy/env.example`):

| Key | Default |
|---|---|
| `VFG_CLIENT_TOKEN` | required; the bridge exits 78 without it, and systemd does not restart it |
| `VFG_DB`, `VFG_FEED`, `VFG_WATCH` | `/opt/vfg/vfg.db`, `/opt/vfg/feed.log`, `/opt/vfg/watch.json` |
| `VFG_STATE`, `VFG_CONSOLE_LOG` | `/opt/vfg/state`, `/opt/mc/console.log` |
| `VFG_BRIDGE_BIND`, `VFG_BRIDGE_PORT` | `0.0.0.0`, `8787` |

On the owner's PC, from `~/.vfg/config`: `VFG_SSH_HOST`, `VFG_SSH_USER`, `VFG_SSH_KEY`,
`VFG_AWS_REGION`, `VFG_INSTANCE_TYPE`, `VFG_KEY_PAIR`, `VFG_BACKUP_DIR` and `VFG_ACCEPT_EULA`
(see `toolkit/config.example`).

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): §1 where things run, §2 speech in, §3 chat and game
events, §4 the transcript history (it owns the schema), §6 game watchers, §7 the feed and
`vfg_feed`, §11 secrets.
