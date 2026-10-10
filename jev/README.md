# jev — the Jev filter

On the EC2 box. It asks Jev (TypeSafe) which of four classes each new line a player says or types
is, writes the verdict into the transcript history, and feeds the lines it keeps to the brain in
batches:

| Class | A line that |
|---|---|
| `action_oriented` | asks the AI for something to happen, or follows up an earlier ask |
| `conversational` | talks to the AI without asking for an action |
| `context` | is not said to the AI, but the AI needs it to understand or carry out an ask |
| `not_relevant` | is neither; never fed |

An `action_oriented` or `conversational` line flushes the batch at once, since both get a live
answer; otherwise a batch goes `VFG_BATCH_SECONDS` after its first line. Lines dropped in the last
two minutes are asked about again inside the next call, and one that flips is fed with
`was=not_relevant`. If Jev fails for 10 s the filter fails open: every line passes as `context`,
with one `SYS` line in the feed when that starts and another when Jev is back.

## Run it

On the box it is the systemd unit `vfg-jev`: `/opt/vfg/python/bin/python3 -u /opt/vfg/jev/jev_filter.py`.
To judge one text and write nothing: `python3 jev_filter.py --judge "<text>"`, with `--raw` to see
Jev's answer as sent.

Deploy from the repo's root on the owner's PC: `jev/deploy.sh`. It streams this folder (without
`tests/`) to `/opt/vfg/jev/` over SSH, swaps it in and restarts the unit. Set the key once with
`bridge/deploy/set-secret.sh TYPESAFE_API_KEY`.

## Test it

```sh
cd jev && python3 -m unittest discover -s tests
```

No network: `tests/fake_jev.py` stands in for Jev's API and `tests/fake_lib/` for the bridge's feed
writer. `test_e2e.py` runs `jev_filter.py` as a process against them.

## Settings

From the environment (systemd reads `/opt/vfg/.env`):

| Key | Default |
|---|---|
| `TYPESAFE_API_KEY` | required, from the environment or else `VFG_ENV_FILE`; never logged |
| `VFG_ENV_FILE`, `VFG_DB`, `VFG_LIB` | `/opt/vfg/.env`, `/opt/vfg/vfg.db`, `/opt/vfg/lib` |
| `VFG_LOOKOUT` | `/opt/vfg/lookout.md`, what to look out for (`vfg lookout set`) |
| `VFG_BATCH_SECONDS`, `VFG_POLL_MS` | `5`, `50` |
| `VFG_JEV_URL`, `VFG_JEV_MODEL`, `VFG_JEV_TIMEOUT` | `https://api.typesafe.ai/v1/systemone`, `jev-latest`, `3` s |
| `VFG_JEV_STATE_TOKENS`, `VFG_JEV_HISTORY_HOURS` | `8000`, `3` |

`deploy.sh` reads `VFG_SSH_HOST`, `VFG_SSH_USER` (default `ec2-user`) and `VFG_SSH_KEY` from
`~/.vfg/config` (`VFG_CONFIG` names another file).

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): §4 (it writes only the `jev_*` and `fed_at` columns and
`usage` rows), §5 Jev, §7 the feed (through `vfg_feed`, exactly once per kept line), §11 secrets.
