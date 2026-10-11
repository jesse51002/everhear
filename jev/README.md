# jev — the Jev filter

On the EC2 box. It asks Jev (TypeSafe) what should become of each new line a player says or types,
writes the verdict into the transcript history, and feeds the lines it keeps to the brain in
batches.

**One call a line, two questions** (D2, chosen on 2026-10-11): what should happen with the line —
the AI must act, answer in chat, remember it, or ignore it — and, if the AI only replied in chat,
whether the speaker would be fine, let down, or not notice. The answers walk onto four classes:

| Class | Answers | Goes to |
|---|---|---|
| `action_oriented` | act, and the speaker would be let down by a reply alone | the brain |
| `conversational` | answer; or act, with the second answer anything else or missing | Respond |
| `context` | remember | the feed, read by no model |
| `not_relevant` | ignore | nowhere; never fed |

The confidence stored and fed is the first answer's. An answer without the first question counts as
Jev failing.

**The gather call.** Speech-to-text cuts a player at every pause, so one ask can arrive in pieces.
A line classed `action_oriented` or `conversational` gets one more call before it is fed, over its
speaker's lines since their last line to the AI, 3 minutes back at most, that were classed
`context` or `not_relevant` (the newest 10): is each part of this ask? The yes lines go with the ask
as one feed line, in the order said, the ask last, joined with ` … ` —
`the castle by the river … the big one … make it bigger` — with the ask's fields and time, the
oldest dropped first when it would be over the feed's 500 characters. A gathered line keeps its own
class and is fed, or not, as before. No such lines, no call. A gather call that fails leaves the ask to go
alone, and does not count towards failing open. The group is held in memory until the ask is fed,
so a crash between the two feeds the ask alone; a restart finds a group already fed by the ask at
its end.

An `action_oriented` or `conversational` line flushes the batch at once, since both get a live
answer; otherwise a batch goes `VFG_BATCH_SECONDS` after its first line. If Jev fails for 10 s the
filter fails open: every line passes as `context`, with one `SYS` line in the feed when that starts
and another when Jev is back.

Jev's state is the fixed rules (the setting and how to read the state; the classes are defined only
in the questions' options), `lookout.md` (what to watch for), a block of invented examples under
what should become of each, the brain's action menu `menu.md` (one `name — what it does` a line;
missing or empty is fine), then the history: the lines said, the AI's included, and the brain's own
records of what it did — the feed's `AI` rows of kind `action`, `task` and `result` — so a follow-up
to an earlier ask reads as one. The line asked about is marked `[1]` where it was said; in the
gather call the ask is marked `[ask]` and the lines asked about `[1]` … `[n]`. All of it fits in
`VFG_JEV_STATE_TOKENS`: the menu takes at most half of what the rules, the lookout, the examples and
the lines asked about leave, in whole lines, and the history the rest, newest first.

The journal has one line per call, the routing call's with both answers:

```text
judge <id> Steve voice action_oriented 0.91 · act 0.91, let down 0.88 · 230ms 7012tok
gather <ask id> Steve · 3 asked, 1 yes: <id> 0.91 · no: <id> 0.80, <id> 0.77 · 210ms 7400tok
```

Every call writes a `usage` row; the gather call's `ref` is `gather:<ask id>`.

## Run it

On the box it is the systemd unit `vfg-jev`: `/opt/vfg/python/bin/python3 -u /opt/vfg/jev/jev_filter.py`.
To judge one text and write nothing: `python3 jev_filter.py --judge "<text>"`, which prints the class
with both answers, with `--raw` to see Jev's answer as sent.

Deploy from the repo's root on the owner's PC: `jev/deploy.sh`. It streams this folder (without
`tests/`) to `/opt/vfg/jev/` over SSH, swaps it in and restarts the unit. Set the key once with
`bridge/deploy/set-secret.sh TYPESAFE_API_KEY`.

## Test it

```sh
cd jev && python3 -m unittest discover -s tests
```

No network, under a second: `tests/fake_jev.py` stands in for Jev's API (it answers by keyword; a
line with `big one` in it is part of the ask) and `tests/fake_lib/` for the bridge's feed writer.
Every rule runs on a fake clock in `test_filter.py`; `test_e2e.py` runs `jev_filter.py` once as a
process against the stand-ins and stops it with SIGTERM. `test_drift.py` checks Jev's wording against
the wording benched in `integration/replay/` (skipped where that is not there).

## Settings

From the environment (systemd reads `/opt/vfg/.env`):

| Key | Default |
|---|---|
| `TYPESAFE_API_KEY` | required, from the environment or else `VFG_ENV_FILE`; never logged |
| `VFG_ENV_FILE`, `VFG_DB`, `VFG_LIB` | `/opt/vfg/.env`, `/opt/vfg/vfg.db`, `/opt/vfg/lib` |
| `VFG_LOOKOUT` | `/opt/vfg/lookout.md`, what to look out for (`vfg lookout set`) |
| `VFG_MENU` | `/opt/vfg/menu.md`, the brain's action menu (`vfg jev menu set`) |
| `VFG_BATCH_SECONDS`, `VFG_POLL_MS` | `5`, `50` |
| `VFG_JEV_URL`, `VFG_JEV_MODEL`, `VFG_JEV_TIMEOUT` | `https://api.typesafe.ai/v1/systemone`, `jev-latest`, `3` s |
| `VFG_JEV_STATE_TOKENS`, `VFG_JEV_HISTORY_HOURS` | `8000`, `3` |

`deploy.sh` reads `VFG_SSH_HOST`, `VFG_SSH_USER` (default `ec2-user`) and `VFG_SSH_KEY` from
`~/.vfg/config` (`VFG_CONFIG` names another file).

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): §4 (it writes only the `jev_*` and `fed_at` columns and
`usage` rows), §5 Jev, §7 the feed (through `vfg_feed`, exactly once per kept line; it reads the
brain's `AI` rows from the `feed` table), §11 secrets.
