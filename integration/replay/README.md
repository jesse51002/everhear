# replay — the run's tasks sent again, measured

The spec's last test ("Script library and router", _Test_, the replay): the 172 tasks the
conversation sent the orchestrator on 2026-10-08, sent again with `vfg orch send`, one every 30 s,
to the real runner, against a real 26.3 server on this PC (`integration/realmc`) with the run's
three players online for the whole replay; then, for each task, the time from the send to done,
the rung it took, its cost by component, and from the implementers' transcripts their time, the
calls before their first change and the share of their time on calls that only look, measured as
for the run and set against the run's own figures.

It runs on your Claude subscription: the orchestrator and implementers on Haiku 5.5 at low effort
(the runner's defaults), and the router's Jev call on the stand-in's TypeSafe key
(`integration/realmc/realmc.py key`; without it `vfg route` falls back to words).

## Run it

```sh
python3 integration/replay/run.py --only T18,T104,T117,T123 --out /tmp/replay-pilot   # a pilot
python3 integration/replay/run.py --from T40 --limit 20                                 # a stretch
python3 integration/replay/run.py --out /tmp/replay-full --cap 30                       # all 172
python3 integration/replay/measure.py /tmp/replay-pilot                                 # again, any time
python3 integration/replay/run.py --only T1,T2 --dry-run                                # what would be sent
```

| Option | Default | Is |
|---|---|---|
| `--every` | `30` | seconds between two sends |
| `--only`, `--from`, `--limit` | all | which tasks, by the run's ids |
| `--cap` | `20` | minutes after the last send to wait for every task to close |
| `--out` | `$VFG_REPLAY_DIR` or the temp folder, `vfg-replay-<time>` | where every record goes |
| `--name`, `--server` | `replay`, `replay` | the realmc server; `VFG_SERVER`, so `made/replay/` |
| `--no-fresh`, `--keep-server` | off | keep the world as it is; leave the server up at the end |

`run.py` starts the server fresh (`integration/realmc/up replay --fresh`, with Carpet), empties
`made/replay/`, spawns Steve (the owner, realmc's `VFG_OWNER`), Alex and Sam at spread spots on the
arena and puts back any who leaves or dies; starts the runner as `vfg orch start` does
(`orchestrator/run`, detached, its pid in `runner.pid`, so `vfg orch status|stop` work) but with
your own `HOME`, since through realmc's `bin/vfg` it would get the stand-in's and the CLI would not
be logged in; its inbox, state and log are in `OUT/orch` (`VFG_ORCH_DIR`) and `vfg` points at the
stand-in, so nothing reads or writes `~/.vfg`; sends the tasks; waits until all are closed, at
most `--cap` minutes; stops the runner, every script instance and the players, copies every record
into `OUT`, stops the server and runs `measure.py`. Ctrl-C stops everything the same way and keeps what there is.

A task gets the next free id in the replay's own inbox, so the ids match the run's only when the
replay starts at T1 with nothing left out. A reference in a task's words to an earlier task
becomes that task's replay id, or "an earlier task" when it was not sent; `sent.jsonl` maps each
replay id to the run's.

## What it leaves in OUT

| Path | Is |
|---|---|
| `orch/` | the runner's inbox, state (the router's picks per implementer), replies and log |
| `sent.jsonl` | one line per task sent: replay id, run id, when, class |
| `usage.jsonl` | the stand-in's usage rows written during the replay (orchestrator, implementer, jev) |
| `transcripts/<session>/` | the orchestrator's session (`main.jsonl`) and every implementer (`subagents/`) |
| `made/` | what the implementers made: `scripts/`, `presets/` |
| `box/` | each instance's log, record and live values on the stand-in |
| `scripts.txt`, `state.json` | what was running at the end, and the server's state |
| `players.log`, `harness.log` | players put back; what the harness did |
| `measure.md`, `measure.json` | the measurement |

## tasks.jsonl

One line per task of the run, in order: `id`, `at`, `task` and `context` as the conversation
wrote them, with every player's name, in any spelling, replaced by Steve (the owner), Alex or Sam,
the same player always by the same name; `class`, from the analysis behind the spec's figures:

| Class | Tasks | Is |
|---|---|---|
| `value` | 69 | a variation of an earlier ask that changed only a value or a target (the spec's 68, and T33, which a restart lost) |
| `new` | 32 | needed something no script did, in the run |
| `plain` | 43 | a plain command or a stop |
| `fix` | 19 | something reported done did not work |
| `repeat` | 9 | the same thing asked again |

and `run`: the run's figures for it — `send_to_done_s`, `words_to_done_s` (from the player's
words), `kind` (what the run did: a new script written, a script edited, commands), `result`, and
each implementer job: model, time, calls, calls before the first change, seconds per kind of call,
turns, whether it hit the turn cap, cost; and the task's implementer and orchestrator cost.

## How it is measured

`measure.py`'s docstring has every definition. In short: the run's rules for what looks, acts and
checks, and for the first change, with the commands the run did not have added (`vfg state` looks,
`vfg script check` checks, `vfg script new|set|save|stop` and `vfg preset save` change); the rung
a task took is the highest of its implementers' calls; a job's cost is its tokens at Anthropic's
ratios (fresh 1, cache write 1.25, read 0.1, out 5), scaled so a model's jobs add up to its usage
rows; an orchestrator row is split between the tasks open when it was written. The same code
measured the run's 116 transcripts for `tasks.jsonl`, and gives the spec's figures: a median job
of 56 s, 4 calls before the first change, 40% of call time looking, $12.33 for implementers, and
4 of 25 Haiku jobs at the turn cap.
