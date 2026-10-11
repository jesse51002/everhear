# replay — the playtest of 2026-10-08, again

The playtest of 2026-10-08 (2.5 h, three players: KrownJM, Eclipszr, Styrkaeopteryx) replayed
against the parts that replace what ran then. **The replay now is `lines.py` and `classes.py`**:
the playtest's own lines through the brain's real calls, offline, model calls only, scored against
what the conversation agent did with each line. They are Prototype_Brain's first two required
tests (_Test_). `run.py`, the live task replay, drove the orchestrator, which the brain replaced:
it no longer runs on this branch and **needs rewriting for the brain** (below).

| File | Is |
|---|---|
| `playtest.py` | the playtest's data as the brain would have had it: the context row by row in the order it would have arrived, the world, the open tasks, the batch timer, and what the conversation did with each line |
| `lines.py`, `lines_score.py` | the raw-line replay: every direct ask through the brain's one call, with the whole conversation |
| `classes.py`, `classes_score.py` | Jev's four classes on every line, against TypeSafe, then Respond on every line Jev classes `conversational` |
| `taskcall.py`, `taskcall_score.py`, `taskcall_prompts/` | the named-tasks orchestrator, one `structured()` call per implementer part, before it is built: every part the brain's full `lines.py` run sent to an implementer, through it, two models compared; results in `taskcall.md`, every miss in `taskcall_scores/` |
| `gate_bench.py`, `gate_score.py`, `gate_set.jsonl`, `gate_prompts/` | the gate bench: six ways to decide whether a line needs the brain or is talk (Respond as it was, Respond deciding first on DeepSeek and on Gemini, the Jev council in one call and in four), every one on the same fixed set of 592 lines; results and the commands in `gate_bench.md` |
| `tree_bench.py`, `tree_score.py`, `tree_questions.py` | the tree bench: Jev routing each line by a decision tree of class questions (who it is said to; worth keeping; what the speaker wants), all asked in one Jev call, with and without a council of three questions a level, against Jev today on all 843 lines; results and the commands in `tree_bench.md` |
| `trees_bench.py`, `trees_score.py`, `trees_questions.py` | the trees bench: five genuinely different ways to build that tree (who then what; kind first; a cheapest-first ladder; consequence first with two keys; observable signals with the tree in code), each one Jev call a line, against Jev today on all 843 lines; results and the commands in `trees_bench.md` |
| `labels_build.py`, `labels_score.py`, `labels_2026-10-08.jsonl`, `labels_check.jsonl`, `labels_review.md`, `labels_score.md` | the hindsight labels: Jev labels each of the 843 lines seeing what the AI said and did in the 90 s after it (five labels, three questions in one call, combined by written rules, checked by hand), and Jev today and the five trees rescored against them from their recorded answers; results and the commands in `labels.md` |
| `run.py`, `measure.py`, `tasks.jsonl` | the live task replay against the orchestrator: gone with it |

## The data

Read, never written:

- **The playtest's database**, the box's `vfg.db` taken after it: `tar xzf
  ~/Documents/MinecraftServers/aws-backup/vfg-backup-2026-10-08-playtest.tgz -C DIR vfg/vfg.db`
  (extract that file alone: the archive holds the box's `.env` too). Its classes are the old
  names: `direct_ask` is `action_oriented` now, `not_useful` is `not_relevant`.
- **The tasks the conversation wrote**: `~/.vfg/orch/inbox.jsonl`, those before 2026-10-09; each
  task's context names the LINE numbers it came from (`#812`).
- **The route labels**: `~/.vfg/dispatch/eval/selector-actions-2026-10-10.jsonl`, on the brain's
  action menu, and `selector.jsonl`, on the menu of 2026-10-09, by task id.
- **The command references**: `~/.vfg/brain/refs/26.3/`, else `~/.vfg/dispatch/refs/26.3/`.

The context (`playtest.py`'s docstring has it all): every kept LINE when Jev fed it, GAME and SYS
rows, the AI's chat as `AI say` rows, each task the conversation wrote as an `AI task` row (`T7:
…`) and each ORCH done or failed as an `AI result` row (`T7 done: …`), so the brain sees task ids
as it would live. It only grows, in arrival order, so the prompt cache sees what it sees live. The
world says who is online and that positions, items, the time and the weather are unknown; nothing
was a running copy of an action then, and no place was saved. A call's NEW rows are what no read
took in: each line's call is a live read, and the batch timer (60 s with no read and something
unread) takes in the rest.

## Run it

The brain's own environment, synced once: `(cd brain && uv sync --frozen --inexact)`. The brain's
settings are its own (`brain/brain/config.py`: the environment, then `~/.vfg/config`), so the
model, the reasoning and `VFG_OWNER` are what a live brain on this PC would use. The OpenRouter
key as `structured()` reads it; the TypeSafe key as `TYPESAFE_API_KEY`, else from `--env FILE`
(default: realmc's stand-in box's `integration/realmc/.server/shared/env`, here or in the
`script-library` worktree), never printed.

```sh
PY=brain/.venv/bin/python; DB=DIR/vfg/vfg.db
$PY integration/replay/lines.py --db $DB --out OUT --limit 20             # a pilot: check the cost a call
$PY integration/replay/lines.py --db $DB --out OUT                        # all 357 direct asks
python3 integration/replay/lines_score.py OUT                             # OUT/score.md, score.json

$PY integration/replay/taskcall.py --db $DB --lines OUT/lines.jsonl --out OUT3 \
    --model google/gemini-3.8-flash --reasoning low --limit 15          # the orchestrator: a 15-part pilot
$PY integration/replay/taskcall.py --db $DB --lines OUT/lines.jsonl --out OUT3 \
    --model google/gemini-3.8-flash --reasoning low                     # goes on to all 168 parts
python3 integration/replay/taskcall_score.py OUT3                         # OUT3/score.md, score.json

$PY integration/replay/classes.py jev --db $DB --out OUT2                 # 843 lines, one TypeSafe call each
$PY integration/replay/classes.py respond --db $DB --out OUT2             # every line now conversational
python3 integration/replay/classes_score.py OUT2                          # OUT2/classes.md, classes.json
```

A run into an OUT that has rows goes on after the last one. `lines.py --only 13,15,18` asks those
lines alone, by their LINE numbers, the context still built in full; `--dry` writes the prompts
and calls nothing; `--budget` (4 dollars) stops a run that spends more. `classes.py respond
--budget` is 1 dollar.

On 2026-10-10 a full `lines.py` run took about 24 minutes and $3.02 (a call: median $0.0084 and
3.7 s; the prompt grew from 25K to 68K tokens, 98% of it cached), the Jev step 3.5 minutes and
$0.27, and the Respond step 9 minutes and $1.01 for the first 322 of the 382 lines Jev classed
`conversational`, where its $1 budget stopped it: all of them would be about $1.2. A window of
lines, `--only`, costs a cold first call (about $0.10) and then what the full run's calls do.

`taskcall.py` needs a full `lines.py` run first: its `lines.jsonl` gives the implementer parts.
`--model` is any OpenRouter id, `--reasoning` off or an effort, `--version` the prompt in
`taskcall_prompts/` (v3, the one to ship, is the default); `--budget` (1 dollar) stops it. A run
into an OUT that has rows goes on after the last line, the tasks rebuilt from the answers there.
On 2026-10-10 the full 168 parts took about 9 minutes and $1.18 on Gemini 3.8 Flash at low
reasoning (a call: median 3.0 s, p90 5.0 s), and 11 minutes and $0.16 on DeepSeek V4 Flash with
reasoning off (3.6 s, 5.0 s); at low reasoning DeepSeek took 7.7 s, p90 26 s.

### What it leaves in OUT

| Path | Is |
|---|---|
| `lines.jsonl` | one row per line: its words, when it was fed, what the conversation did (its tasks and their labels), the size of the context, the brain's decision (every part: `to`, `task`, instruction, action, drop) and reply, ms, cost, prompt, cached and completion tokens |
| `meta.json` | the settings, the commit and a hash of `brain/prompts/brain.md` |
| `prompts/<n>.txt` | the whole prompt of every 50th call, `[MARK]` where the cache is marked |
| `jev.jsonl`, `jev-meta.json`, `menu.md` | per line: Jev's class then and now, its confidence, what the conversation did, and what fit in the state (the menu, the history, the AI records); a `flip` row when a `not_relevant` line asked about again flipped |
| `respond.jsonl`, `respond-meta.json` | per conversational line: hand-up or reply as the model gave them and after Respond's code, the reason, ms, cost, tokens |
| `taskcall.jsonl`, `tasks.json`, `meta.json` | the orchestrator's run: per line, each implementer part's answer (task, name, description, brief, reply, any edit or stop), the name it got, ms, cost, tokens, and the conversation's tasks for the line with the names that held their targets; every task at the end with its chain of T ids and every description it had |

## How it is scored

The scorers' docstrings have every definition. In short: a line **became a task** when a task's
context names it, and the tasks that name it first are its own, whose labels are its route.
**Acted** is any part not `talk`. The **route** is the actions named, with `+implementer` for an
implementer part, against the labels as `linescore.py` scored the earlier replay, `orchestrator`
read as `implementer`; **lost** is actions alone where every accepted route needs an implementer.
A **follow-up** is a line the conversation turned into `Change to T<n>` or `Cancel T<n>`, less
those whose T<n> was written after the line reached the brain; it is right when an implementer
part names T<n>, or a task of its chain. For Jev, a line was **answered in chat** when an AI chat
line followed within 60 s with no task and no result between: a heuristic, so `chat` holds
declined asks as well as talk. `labels_2026-10-08.jsonl` replaces these labels for routing: it finds 298
asks where they find 187 (`labels.md`).

For the orchestrator (`taskcall_score.py`), each of the conversation's T ids is given to the name
its line's parts answered, so a follow-up is right when it names the task whose chain holds its
target. Two truths: **by script**, from the run's own record in `tasks.jsonl` (a task that wrote a
new script is new; one that edited an earlier task's script follows that task up), which comes
first; and **by the labels** (`Change to T<x>` is a follow-up, anything else fresh), which counts
many right follow-ups as new tasks taken, since the conversation often wrote a fix as a fresh
task. Since the chains are the replay's own, **one thing, one task** checks each task against the
scripts its T ids touched: lumped tasks and split scripts.

## The live task replay: gone with the orchestrator

`run.py` sent the run's 172 tasks again with `vfg orch send`, one every 30 s, to the orchestrator's
runner, against a real 26.3 server on this PC (`integration/realmc`), and `measure.py` measured
each task as the run was measured. The orchestrator, `vfg orch send` and its runner are gone, so it
**needs rewriting for the brain**: each task said as a LINE the brain answers, against realmc, with
the implementers the brain starts and their reports. What follows is how it worked.

### Run it

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

### What it leaves in OUT

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

### tasks.jsonl

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

### How it is measured

`measure.py`'s docstring has every definition. In short: the run's rules for what looks, acts and
checks, and for the first change, with the commands the run did not have added (`vfg state` looks,
`vfg script check` checks, `vfg script new|set|save|stop` and `vfg preset save` change); the rung
a task took is the highest of its implementers' calls; a job's cost is its tokens at Anthropic's
ratios (fresh 1, cache write 1.25, read 0.1, out 5), scaled so a model's jobs add up to its usage
rows; an orchestrator row is split between the tasks open when it was written. The same code
measured the run's 116 transcripts for `tasks.jsonl`, and gives the spec's figures: a median job
of 56 s, 4 calls before the first change, 40% of call time looking, $12.33 for implementers, and
4 of 25 Haiku jobs at the turn cap.
