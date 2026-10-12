# jev — the Jev filter

On the EC2 box. It asks Jev (TypeSafe) what should become of each new line a player says or types,
writes the verdict into the transcript history, and feeds the lines it keeps to the brain in
batches. Before that, a voice line from a player with an incantation gets the cast check: if it
was said to cast one, the bridge casts it. A short line surely cast never reaches the brain; a long
one (a cast inside other talk), or a short one Jev is not sure of, is routed as ever too: "I'd
rather it do both if it's not sure."

**The cast check** (`casting.py`, 2026-10-11). Speech-to-text mishears incantations ("hot fireball"
as "Hot variable." or "Affordable.") and cuts them into pieces ("Ice.", then "Beam"); the bridge's
deterministic layers (the spotter, Layer 2, the drop rule) let some through, and the brain answered
them. So every voice line the bridge kept, from a player with an incantation (their own or an `@a`
one, in `VFG_INCANTATIONS`), gets one Jev call before its routing call, one after the other: one `choice` question, `cast`, its options one `cast_<phrase>` per incantation (the phrase,
its aliases and what it does: a `command` one's commands, else its action's line on the menu) and
`not_a_cast` (an option holds up to 24 aliases and the whole of what it does: past 1,000
characters the last aliases go, never the description). The state is the fixed rules (a cast is the phrase, a mishearing, a piece or it said
twice; talk about an incantation, a request or a reply is not), invented examples of both, and
RECENT: the speaker's lines (dropped ones too) and casts of the last minute and the AI's lines, the
newest 8, the line asked about marked `[1]` last.

A cast whose option Jev gave a probability of `VFG_JEV_CAST_MIN` (0.5) or more goes to the
bridge's cast path. The threshold is on the chosen option's probability (the answer's
`probabilities`), not on its `confidence`, which runs lower ("Call fireball.": 0.63 and 0.45); an
answer with no probability for its option is not a cast. The path is
`POST http://127.0.0.1:$VFG_BRIDGE_PORT/v1/jev/casts` with `VFG_CLIENT_TOKEN`: the same checks as
every cast (the 1 s gap, the incantation's cooldown, the duplicate window, a `casts` row, layer
`jev`), plus an age: a line the bridge received more than `VFG_JEV_CAST_MAX_AGE_S` (2 s, the
bridge's setting) before is cast `stale`, never fired. A short line (6 words or fewer) whose cast
is sure, `VFG_JEV_CAST_SURE` (0.7) or over, the bridge drops whatever came of the cast, fired,
cooldown, duplicate or stale, so it is never routed nor fed. One under it is cast all the same, with
`"keep": true`, and the line goes on to routing, which still classes noise like "Ice." as
`not_relevant`. On the two sessions benched, 178 of the 187 short cast lines Jev caught were sure
and dropped, and 9 kept (0.50 to 0.66: "steak.", "Hester.", "High step." three times …), while the
one short false cast on the 2026-10-08 talk ("Uh, that Sorry beam struggle.", 0.61) is kept for
routing.
Not a cast, under the threshold, no probability, a call that fails or is refused, or a bridge that
does not answer or keeps the line: the line goes on to the routing call as before.

**A long line** (over 6 words) is asked in its own wording (`casting.LONG_*`): does it have the
player casting an incantation right now, as a command or a shout, the rest being other talk ("ok
watch this, frost nova", "Frost nova. Can you also give me some food?"), or does it only talk,
about an incantation or anything else ("can you make thunder strike hit more", "that frost nova
was sick")? The bridge's Layer 2 no longer casts in long lines, so this is the one judgement there.
Its threshold is `VFG_JEV_CAST_MIN_LONG` (0.7; over 1, no long line is checked): on the
2026-10-08 playtest's 604 long lines, every player given tonight's incantations, 0.7 made one false
cast and 0.5 the same one; on 2026-10-11's 59 long talk lines, 0.5 made one ("how much damage is
the ice cream due right now") and 0.6 or more none; it caught the one real long cast ("I still have
the floaty effect. Can you remove it? Ice Beam.", 0.97) and 26 of 40 made-up ones (30 at 0.5). A
long line's cast goes to the bridge with `"keep": true`: the same cast path and rules, the line kept
(it takes the cast's id as its `cast_id` when the cast fired and it has none), the line routed
first, as ever, so a request in the same sentence still reaches the AI; the brain sees the cast as its GAME line.
A phrase said more than 3 s before its long line ended is `stale` at the bridge, never fired late
(`bridge/README.md`). A
failed cast check does not count towards failing open (the routing call right after it does); each
line is checked once; nothing is checked while Jev fails open. A line that cannot be read for
RECENT is routed without a call.

**A cast never waits behind routing.** Each step checks every short line due a cast check before it
routes any line, and checks again after each routing call, so a cast said meanwhile goes next. A
long line is kept and routed whatever its check says, so it is routed first and checked right after
(after any short line waiting): its answer never waits on a Jev round trip, and a short cast never
waits behind long lines' checks. And no call
is made for a line already too old for the bridge to fire it (it goes on to routing): more than
`VFG_JEV_CAST_MAX_AGE_S` (2 s, read from the same `.env` as the bridge) since the filter first read
it, on the box's clock (never more than its age at the bridge, so never wrong), or more than that
and 10 s since it was said, on the mod's clock after the clock guard (the 10 s take speech-to-text's
delay and a player's clock a few seconds off; a clock further behind than that loses its player the
check, and the lines go to routing as before). The journal says
`cast-check <id> Steve · too old to cast (2.5 s since it was read here) → routing, no call`.

The store is read by a copy of the kit's reading (`scripts/_lib/incantations.py`), not the kit
itself: the kit reaches the box with `vfg action run`, and this folder is deployed alone.
`tests/test_kit_pin.py` holds the copy to the kit's answers. `VFG_JEV_CAST_MIN` over 1 turns the
check off, and so does a missing `VFG_CLIENT_TOKEN` (said once, at the start).

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

**The gather call.** Speech-to-text cuts a player at every pause, so one ask can arrive in pieces. A
line classed `action_oriented` or `conversational` gets one more call before it is fed, over its
speaker's lines that Jev classed `context` or `not_relevant` after it judged their last line to the
AI, said before this one and 3 minutes before it at most (the newest 10): is each part of this ask?
That is judge order, not the order said, because a voice line is stored only once its speech is
text, after a line typed later may have been judged: each line is offered to the speaker's next line
to the AI judged after it, and to no other. A line passed as `context` with no confidence, while Jev
failed open or after it refused the line twice, is never gathered. The yes lines go with the ask as
one feed line, in the order said, the ask last, joined with ` … ` —
`the castle by the river … the big one … make it bigger` — with the ask's fields and time, the
oldest dropped first when it would be over the feed's 500 characters. A gathered line keeps its own
class and is fed, or not, as before. No such lines, no call. A gather call that fails leaves the ask
to go alone, and does not count towards failing open. The group is held in memory until the ask is
fed, so a crash between the two feeds the ask alone; a restart finds a group already fed by the ask
at its end.

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

The journal has one line per call, the routing call's with both answers, the cast check's with
its answer and what became of the line:

```text
judge <id> Steve voice action_oriented 0.91 · act 0.91, let down 0.88 · 230ms 7012tok
gather <ask id> Steve · 3 asked, 1 yes: <id> 0.91 · no: <id> 0.80, <id> 0.77 · 210ms 7400tok
cast-check <id> Steve · hot fireball p 0.92 → fired · 210ms 1700tok
cast-check <id> Steve · ice beam p 0.88 → cooldown (0.4 s left of its 3 s cooldown) · 190ms 1650tok
cast-check <id> Steve · fire ball p 0.97 → stale (the line is 2.3 s old, past the 2 s a Jev cast may be) · 200ms 1700tok
cast-check <id> Steve · no p 0.95 → routing · 190ms 1600tok
cast-check <id> Steve · long · ice beam p 0.97 → fired, the line kept (routed first) · 180ms 1850tok
cast-check <id> Steve · ice beam p 0.63 → fired, the line kept, not sure (under 0.70), routing · 170ms 1800tok
```

`p` is the probability of the option Jev chose, the number held to `VFG_JEV_CAST_MIN` (`p -` when
the answer gave none).

```text
```

A routing call Jev answers writes one `usage` row, `ref` the line's id, one row for both calls when
it was refused once first; a call that fails, or is refused twice, writes none. An answered gather
call writes one with `ref` `gather:<ask id>`, an answered cast check one with `ref` `cast:<line id>`,
and the probe that finds Jev back one with `ref` `probe`.

## Run it

On the box it is the systemd unit `vfg-jev`: `/opt/vfg/python/bin/python3 -u /opt/vfg/jev/jev_filter.py`.
To judge one text and write nothing: `python3 jev_filter.py --judge "<text>"`, which prints the class
with both answers, with `--raw` to see Jev's answer as sent. The cast check alone, on the store and
menu as they are, with no RECENT: `python3 jev_filter.py --cast "<text>" --player Steve`; it writes
nothing and casts nothing.

Deploy from the repo's root on the owner's PC: `jev/deploy.sh`. It streams this folder (without
`tests/`) to `/opt/vfg/jev/` over SSH, swaps it in and restarts the unit. Set the key once with
`bridge/deploy/set-secret.sh TYPESAFE_API_KEY`.

## Test it

```sh
cd jev && python3 -m unittest discover -s tests
```

No network, about two seconds: `tests/fake_jev.py` stands in for Jev's API (it answers by keyword; a
line with `big one` in it is part of the ask; a line holding a phrase is its cast) and
`tests/fake_lib/` for the bridge's feed writer. Every rule runs on a fake clock in `test_filter.py`,
and the cast check's in `test_cast_check.py`, with a fake bridge; `test_e2e.py` runs `jev_filter.py`
as a process against the stand-ins, once with a stand-in for the bridge's cast path, and stops it
with SIGTERM. `test_drift.py` checks Jev's wording against
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
| `VFG_JEV_CAST_MIN` | `0.5`: the cast check's threshold, on the chosen option's probability; over 1, no cast check |
| `VFG_JEV_CAST_MIN_LONG` | `0.7`: a long line's (over 6 words); over 1, no long line is checked |
| `VFG_JEV_CAST_SURE` | `0.7`: a short cast this sure drops its line; under it, cast and routed too; over 1, no line dropped |
| `VFG_JEV_CAST_MAX_AGE_S` | `2.0`, the bridge's: no cast check call for a line older (above) |
| `VFG_INCANTATIONS`, `VFG_BRIDGE_PORT` | `/opt/vfg/incantations.json`, `8787`: the store, and the bridge on 127.0.0.1 |
| `VFG_CLIENT_TOKEN` | the bridge's, from the environment or else `VFG_ENV_FILE`; never logged; none, no cast check |

`deploy.sh` reads `VFG_SSH_HOST`, `VFG_SSH_USER` (default `ec2-user`) and `VFG_SSH_KEY` from
`~/.vfg/config` (`VFG_CONFIG` names another file).

## Contracts

[docs/CONTRACTS.md](../docs/CONTRACTS.md): §2 (the bridge's cast path, `POST /v1/jev/casts`), §4 (it
writes only the `jev_*` and `fed_at` columns and `usage` rows; it reads `casts`), §5 Jev and its cast
check, §7 the feed (through `vfg_feed`, exactly once per kept line; it reads the
brain's `AI` rows from the `feed` table), §11 secrets.
