# The library: the server's base content

Every script kept from the old library is one or more engine classes here, one class per file. On
the box they load from `/opt/vfg/engine/library/`, before `made/<server>/content/`. They are what
the brain sets up through the engine's ops, and the examples implementers copy. The API is
[WRITING.md](../WRITING.md); the shared helpers are [KIT.md](../KIT.md). This file adds the
library's own rules, and how to test a class.

The reference classes, by what each shows:

| File | Kind | Shows |
|---|---|---|
| `railgun.py` | Item | an item that reads presses and casts its ability; `# NEEDS:` |
| `railgun_shot.py` | Ability | declared damage dealt exactly, through the kit (`hitscan`, `hit`, `beam_line`) |
| `gatling_gun.py`, `gatling_shot.py` | Item, Ability | a variant as its own classes with its own Config; a held button (`held_use`) |
| `horde.py` | Scenario | two scripts merged in as Args; spawns; heavy work spread over calls; an early result |
| `castle.py` | Scenario | a build through the block queue; a one-shot ending in `done`; a plan merged in; a saved place |

## One class, one file

- **`engine/library/<id>.py` holds exactly one class.** Its id is its class name in snake_case
  (`RailgunShot` → `railgun_shot`), and the file is named for the id. Ids are one namespace for
  every kind, so an item and the ability it fires take two ids: `railgun` fires `railgun_shot`.
- **The module docstring** says what it does, in a few lines, and which old scripts or actions
  it replaces.
- **Import only `vfg_engine`, `vfg_engine.kit` and the standard library.** Never import or
  subclass another content class. Name another class by its id instead:
  `ctx.user.cast("railgun_shot")`, `self.spawn("ice_queen", at)`, `self.apply(uuid, "frozen")`,
  `self.start_scenario("horde", ...)`.
- **A merged script** becomes Args or Config of the class it joins. `horde` takes the zoo's and
  summon_around's as args; `castle` takes ice_castle's blocks and the mansion as `plan`. It can
  also be a small class of its own that uses the same kit helper.
- **A variant** is its own class with its own Config: `gatling_gun` and `gatling_shot` stand
  beside `railgun` and `railgun_shot`, with the same few lines of code.

## Config, Args, State and the kit

- **Config holds every number, one for everyone.** Keep the old action's values as defaults.
  - Your own fields take an annotation, `Field` bounds and a comment saying the unit or what 0
    means.
  - `summary` says what it does, without numbers: `meta` shows the numbers, and `engine_config`
    changes them, which would leave a number in the summary wrong.
  - A Literal is the type for a choice: `what: Literal["everything", "mobs", "hostile", "players"]`.
- **Items and abilities take no args.** An item is the thing held: it reads presses and casts
  its ability. The ability does the work and **declares its damage**: ItemConfig has no
  `damage`, so damage belongs in an ability.
  - `damage` is what one target takes from one cast. Deal exactly that, through `self.damage`,
    `self.explode`, or the kit's `hit` and `blast`.
  - An instant ability has `duration_s = 0`: the run ends when `on_cast` returns.
- **Scenarios and marks take Args.** That is how the brain sets them up: every field is typed
  (`Players`, `Place`, `BlockMix`, `EntityType`, `Literal`, a bounded `int`), has a default (the
  old action's) and a `description` the brain reads. The description says what the field is, its
  unit, and what empty or 0 means. It can name a merged script's recipe: "(ice castle:
  packed_ice)". The engine refuses bad args in words before `start` runs.
- **State is what an instance remembers:** plain data with defaults, never `self.x`. Reassign a
  list, `self.state.out = self.state.out + [row]`, so the change is validated.
- **The kit.** Use its helpers; never re-implement one. When a big helper does not fit, compose
  its smaller ones and say why at that line. `horde` calls `safe_spots`, then `self.spawn` for
  each spot, because one wave mixes kinds under one cap, and one `summon_near` per kind would
  read the world once per kind.
- **The 50 ms cut.** A call over 50 ms is cut, and the instance ends. Spread heavy work over
  calls with `self.after(k * self.config.gap_s, "method", plain_args)`:
  - `horde` gives each player's spawns their own call;
  - `castle` fixes every site at start, then queues each castle, and its stairs, in a call of
    its own.

  Timer args are plain data, so pass an index into State, never a Spot or a snapshot. Blocks
  always go through `self.blocks` or the kit's `build_shape`, `sign` and `chest`, never through
  `self.command`.
- **Never** use `while True`, sleep, `/attribute`, `effect clear`, or a raw `self.command` where
  a `self.*` verb or the kit does the job.
- **What the engine lacks.** Do the closest honest thing, mark it `# NEEDS: <what>` on that line,
  and report it (`lightning_strike.py` marks how far its raycast reaches).

## A one-shot scenario ends with a result

```python
def done(self, ctx):                     # wherever it finishes: here, the block queue is empty
    self.result("built a castle, 23 wide, of stone_bricks: Steve's at 10 64 -20",
                at=["10 64 -20"], built=1)
    self.end()
```

- The summary is one line the brain hears, saying what happened and where. The facts are plain
  data.
- `self.result(...)` then `self.end()`, in the same call. A refusal it finds at start is also a
  result, then `end()`: "no castle: nobody is online".
- A scenario that runs on may call `result` early. `horde` reports its first wave. A `start` with
  `wait_s` then answers "still running" with that result.
- `duration_s` is the most a scenario may take (`castle`: 300), or `UNTIL_STOPPED` for one that
  ends itself on a condition (`horde`: its last wave is dead).
- Block-queue builds end in `done(ctx)`. If calls still to come will queue more, `done` returns
  without ending: see `castle.done` and `castle.step_done`.

## Unit tests: `engine/tests/library/test_<group>.py`

There is one file per group of classes ported together, under the group's name (`test_horde.py`,
`test_railgun.py`, `test_castle.py`). Each runs the engine on FakeIo with the fixture `rig`, from
`engine/tests/library/conftest.py`:

```python
def test_a_click_fires(rig):
    r = rig("railgun", "railgun_shot")      # only these library files, copied to a fresh content folder
    r.start()                               # loads them; any load error fails the test
    r.io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)        # FakeIo: vfg_engine/testing.py
    z = r.io.add_entity("minecraft:zombie", (0.0, 64.0, -10.0), health=100)
    r.op("give", player="Steve", item="railgun")
    r.tick()
    r.io.use("Steve")
    r.tick(2)
    assert r.io.by_uuid[z].health == 70 and r.problems() == []
```

| `r.` | |
|---|---|
| `start()` | start the engine; every named class must load |
| `tick(n=1)`, `run_for(seconds)` | move the clock and run the engine's ticks (0.1 s each) |
| `op(name, **args)` | a control op, as the brain sends it, and one tick; its reply |
| `start_scenario(id, **args)` | start one (refused args fail the test); its instance id |
| `cast(player, ability)` | grant it, bind a phrase and cast it as the bridge does; the reply |
| `running(iid)`, `result(iid)` | whether it still runs; its `{"summary", "facts"}` (and `"reason"` once ended) |
| `sent(OpType)` | the ops sent to Minecraft (`Damage`, `Summon`, `Fill`, `SetBlock`, `Message`...) |
| `records(kind, cid)`, `rt`, `io`, `lines()` | the instances, the runtime, FakeIo, the log |
| `problems()` | log lines saying a call failed, was slow or cut, or that damage drifted from what was declared |

`rig(..., extra={"name": source})` adds content written in the test, such as an actor your
scenario spawns.

Each test file checks that:
- every class loads;
- the main behaviour happens on FakeIo;
- what must not happen does not (off the line, past range, over the cap);
- each merged script's args do what that script did;
- bad args are refused in words;
- `r.problems() == []`.

Run your file, then the whole folder, to be sure no one else broke:

```sh
cd engine && uv run --group dev python -m pytest tests/library/test_<group>.py -q
cd engine && uv run --group dev python -m pytest tests/library -q
```

## Live tests: `engine/tests/library/live/<id>.py`

There is one file per class, named for its id, defining `test(t)`. Its full API is the docstring
of `vfg_engine/livetest.py`. The stage is a barrier floor 81 × 81 at x 160, y 295, z 160, at
night; take positions from `t.xyz`, never hard-coded ones. Steve is the caster, facing north (forward is -z); Alex stands 4 to his left. Mind that:
- a live test proves the class works in Minecraft: what the player sees and what happens to
  mobs and blocks, with `t.hurt`, `t.near`, `t.block`, `t.said`. Leave edge cases to the unit
  test;
- `from vfg_engine.livetest import verdict` makes a check of your own:
  `verdict(not running[0], running[1])`;
- after a scenario that ended within its `wait` (a build), `t.no_errors()` and `t.unhurt()` say
  CAN'T TELL: livetest does not count that as something that fired. Use `t.said(...)` there;
- the stage is wiped from y 280 up, 48 blocks round its middle: build inside that;
- `t.region(r, u, f)` with `f < 0` needs `behind=True`.

## Running a live test on realmc

This was run as written on `ge-lib1`, 2026-10-13. Run it from the worktree's root, in one shell,
with `N` set to the server your brief names. Steps 1 to 3 are done once per server; step 4 is
run once per test.

```sh
cd /var/home/jm/Documents/EverHear/.worktrees/game-engine
export REALMC_ROOT=/var/home/jm/Documents/EverHear/.worktrees/script-library/integration/realmc/.server
N=ge-lib1

# 1. up, on a fresh world (about 7 s)
VFG_ACCEPT_EULA=true REALMC_HEAP=1536M integration/realmc/up $N --fresh
eval "$(integration/realmc/env $N 2>/dev/null)"          # REALMC_DIR is now that server's folder

# 2. the marker: livetest refuses a server without one, or one naming another RCON port
printf '{"rcon_port": %s}\n' "$(sed -n 's/^rcon\.port=//p' $REALMC_DIR/mc/server.properties)" \
  > $REALMC_DIR/mc/.vfg-test-server

# 3. the datapack into its world, as deploy.sh does on the box ("datapack: reloaded, file/everhear enabled")
E=$REALMC_DIR/vfg/engine; mkdir -p $E/datapack $E/deploy
rsync -r --delete engine/datapack/everhear $E/datapack/
cp engine/deploy/install.sh engine/deploy/requirements.txt $E/deploy/; cp bridge/deploy/rcon.py $REALMC_DIR/mc/
VFG_DIR=$REALMC_DIR/vfg MC_DIR=$REALMC_DIR/mc UV=$(command -v uv) bash $E/deploy/install.sh

# 4. one live test, about 10 to 30 s (the engine/.venv `uv run` made; any writable --state-dir)
PYTHONPATH=engine engine/.venv/bin/python -m vfg_engine.livetest \
  --properties $REALMC_DIR/mc/server.properties --state-dir <your scratchpad>/livetest-$N \
  --content engine/library --test engine/tests/library/live/<id>.py

# 5. at the end: the marker out, the server down
rm $REALMC_DIR/mc/.vfg-test-server; integration/realmc/down $N
```

- Step 4 prints `ENGINE` (the classes loaded), `AT` and `START` lines, then `PASS`, `FAIL` or
  `CAN'T TELL` for each check, then `RESULT`. Its last line is the JSON, whose `"code"` is the
  exit code: 0 all passed, 1 a check failed, 2 the test could not run as written.
- `--content engine/library` loads the whole library, as on the box. A neighbour's file that
  does not load shows as `ENGINE not loaded: ...` and does not fail your test.
- After an edit to your class, run step 4 again: each run starts its own engine from the files.
- **The owner's rules:** never `down --all`, `--purge-key`, `--regen`, `key` or `carpet`. Never
  print a password or a key: the marker holds only the port.
