# replay/impl — Prototype_Implementer_Harness's eval data

The scripts are `integration/impl_*.py`; each has its usage at its top. **The data they need is
here; their outputs are not.** An implementer cannot read this folder: the guard lets it read only
`scripts/`, `actions/` and `made/<server>/` (`readable()` in `brain/brain/implementers/guard.py`),
and these files hold answers (the night's reports, the follow-ups still to come, the checklists).
Every output goes to `$VFG_IMPL_REPLAY`, by default `~/.vfg/impl-replay/`, outside the repository.

| Here | Is |
|---|---|
| `scenarios.json` | the game eval's 26 scenarios (`impl_scenarios.py`), read by `impl_game.py` |
| `tasks.json` | the replay's 37 tasks (`impl_data.py`), with the briefs the stand-in brain wrote (`impl_briefs_write.py`), read by `impl_replay.py`, `impl_briefs.py`, `impl_messages.py`, `impl_wild.py` and the judge |
| `night/` | what Haiku made on 2026-10-11 (four actions and their scripts) and the box's incantations after it, for `impl_judge.py --night` |

| In ~/.vfg/impl-replay/ (never here) | Is |
|---|---|
| `game-<server>/`, `replay-<server>/`, `test1/`, `wild/`, `messages-<server>/`, any `--out` | each run's `brain.log`, its results, `tests.jsonl` (each `vfg test` run whole), its usage rows |
| `judge-<label>.json`, `.md` | `impl_judge.py`'s marks |
| `made-aside/<server>-<time>/` | a server's `made/` as a run found it, moved out before it starts |
| `usage/` | the brief writer's and the judge's usage rows, never sent to a box |

## scenarios.json

One scenario per task of 2026-10-11 and 2026-10-08 (26): a player (Steve, or Alex), a persona
line, his opening line, his goal, how he uses it (`use`), his hidden wants in order (each a want
and how he says it), a `setup` (a running copy to change, for an edit of 2026-10-08), the DONE
WHEN `checklist` for the whole goal, `max_turns`, and `try`, how the player tries it (cast, item,
build, event or research).

They were derived by `impl_scenarios.py`: one google/gemini-3.8-flash call per task, reading the
night's record (the player's lines, the briefs, the reports). A new want ("make it bigger") became
a hidden want. A complaint about that night's build ("it teleports me") did not: what it asks for
is in the goal when the opening implied it, or dropped.

Reviewed:
- **`reviewed`** marks the six of 2026-10-11, read by a person against the night.
- **`FIXES`** in `impl_scenarios.py` holds a person's corrections, applied each time the file is
  written (`impl_scenarios.py --fixes` applies them with no model call). T211's third item forbade
  the levitation its opening asks for. It now reads "floats and comes down slowly for about five
  seconds (levitation, slow falling or both)".
- **`buildable: false`** marks research an in-game implementer cannot do (no web; it reads only
  `scripts/`, `actions/` and `made/`): T215, with the reason in `not_buildable`. The judge scores
  it apart, outside the total.

## tasks.json

37 tasks, the night's player made Steve throughout:

- **The six of 2026-10-11** (T207, T211, T212, T215, T216, T217): 41 asks, each with the
  orchestrator's brief then, the player's own lines, the conversation before it, when it arrived,
  and the night's runs (`night.runs`: start, end, status, report).
- **20 of 2026-10-08**, across kinds: builds, weapons and abilities, mob and world events, edits of
  running things (each with the copy `setup` starts first), and two that cannot be built as asked
  (T16, T62).
- **Test 5's wild asks** (W1–W10) and one that needs `op` (OP).

Each ask's `brief` is the stand-in brain's: a hand-off in Brain_Loop's shape (`text`, `done_when`)
or a follow-up (`text`, and `done_when_now`, the whole checklist after the change, for the judge).
`impl_data.py` rebuilds the file from the records below and keeps the briefs;
`impl_briefs_write.py --again` writes them anew.

Built from, read only: the brain's log of that night (`~/.vfg/brain/brain.log`), the implementers'
transcripts, the box's `vfg.db` at the session's end (only `vfg/vfg.db` taken out of the
aws-backup archive, never the `.env` beside it), and `integration/replay/tasks.jsonl` with
`labels_2026-10-08.jsonl` for 2026-10-08.

## Running a game eval

Each scenario is played as a game on a realmc server with its test server beside it, laid out as
the box's (`integration/realmc/up NAME --test-server`: `NAME/mc-test` and `NAME/vfg-test`, the live
folders + "-test", the unit `minecraft-test`, a fresh world at every start, each test in its
sandbox); both are made fresh for every scenario, and `VFG_TEST_BOX` is unset, so the implementers'
`vfg test` takes exactly the box's path:
1. The stand-in brain writes the brief.
2. The implementer (DeepSeek V4.1 Flash through OpenRouter, on the Claude Code adapter) builds it,
   on the runner's settings: `VFG_IMPL_EFFORT` (high by default; `off` is the old thinking-disabled),
   `VFG_IMPLEMENTER_MINUTES` (20; a turn waits 25 minutes for its report), Together first in the
   host order. Its tests play on the test server, never on the live one.
3. The player tries it on the live server.
4. A simulated player answers, until he is done or out of turns.
5. The judge marks the checklist.

At the end, also on a failure or Ctrl-C, the server and its test server are stopped (`--keep-up` to
leave them running).

OPEN_ROUTER_API is read from `Internal/.env` (or `$VFG_IMPL_ENV`) into the process's environment
only.

    export REALMC_ROOT=…/integration/realmc/.server
    cd brain
    uv run python ../integration/impl_game.py --name ih-d1 --only T207,T215 --dry      # no model: a check of the loop
    uv run python ../integration/impl_game.py --name ih-d1 --only T207,T215 --stop-at 1.20 \
        --budget-file ~/.vfg/impl-replay/game-budget --out ~/.vfg/impl-replay/game-ih-d1
    python3 ../integration/impl_game_tests.py ~/.vfg/impl-replay/game-ih-d1   # the table, false passes, lowered bars

Run three servers at once (each with its test server) with one `--budget-file`: `--stop-at` is then
the whole run's budget. A server's first `up --test-server` makes the test server's template world
(27 s on an idle PC, 85 s at a load of 15); after that each scenario's fresh start is a restart.

The same scenarios with the implementers' thinking at high and off (Prototype_Implementer_Quality's
Test): `VFG_IMPL_EFFORT` set for the process, each arm on servers of its own with a budget file of
its own (one file for both would make `--stop-at` the two arms' total), and one `--out` per server:

    VFG_IMPL_EFFORT=high uv run python ../integration/impl_game.py --name iq-e-h1 --only T207,T212,T216 \
        --stop-at 2.00 --budget-file ~/.vfg/impl-replay/effort-high-budget --out ~/.vfg/impl-replay/game-effort-high-1
    VFG_IMPL_EFFORT=off uv run python ../integration/impl_game.py --name iq-e-o1 --only T207,T212,T216 \
        --stop-at 2.00 --budget-file ~/.vfg/impl-replay/effort-off-budget --out ~/.vfg/impl-replay/game-effort-off-1
    python3 ../integration/impl_game_tests.py ~/.vfg/impl-replay/game-effort-high-*     # then the off arm's

Each request's line in the out folder's `brain.log` has its `reasoning=` tokens; each `vfg test` an
implementer ran is a `testcall`/`testresult` pair there, and whole in `tests.jsonl`, its SERVER line
naming the test server and its sandbox.

## Running the judge alone

`impl_judge.py` casts twice from fresh state on builder B's test templates' stage:
- a husk 10 ahead, a stone wall 2 behind it, real ground under them, and Alex 4 blocks to Steve's
  left;
- each cast watched until it settles, effects kept at their peak;
- its own probes beat the implementer's words;
- an item is done only when both casts show it.

A game runs it by itself. To judge a saved build again, put its `made/` back into
`made/<server>/` and its incantations into the stand-in, then:

    uv run python ../integration/impl_judge.py --name ih-d3 --only T212 \
        --data ~/.vfg/impl-replay/game-ih-d3/T212.record.json --reports ~/.vfg/impl-replay/game-ih-d3/T212.runs.json \
        [--scenario] [--plans an-earlier-judge.json] --label rejudge-T212
    uv run python ../integration/impl_judge.py --name ih-d1 --night      # what Haiku made on 2026-10-11

`--scenario` takes each checklist from `scenarios.json` as it is now. `--plans` uses an earlier
judging's plans again, with no plan call.
