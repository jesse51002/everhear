# actions — what the dispatcher can run on the server

One folder per action. `action.py` holds one pydantic class: its docstring's first line is all the
complexity selector sees of it, the rest of the docstring and each field's `description` are what
the action planner reads to fill it in, and its validators check every value on the PC before
anything is sent. A `handler.py` is there only when vanilla commands cannot do the job (they cannot
loop, remember, or feed one reply into the next command); it runs on EC2 through
`vfg action run`, sent with each call, so nothing is deployed. `command` has none. How an action
runs and what it prints: [docs/CONTRACTS.md](../docs/CONTRACTS.md) §14.

## Adding one

A folder, say `actions/whereis/`, with an empty `__init__.py` and:

```python
# actions/whereis/action.py
from __future__ import annotations

from typing import Literal

from pydantic import Field

from actions._types import Action, Player


class WhereIs(Action):
    """Say where a player stands and which saved place is nearest.

    The report gives the player's x y z and the nearest saved place with its distance.
    """

    action: Literal["whereis"]
    player: Player = Field(description="the player's exact name")
```

```python
# actions/whereis/handler.py: Python 3.9, standard library only; never places or removes a block
import math

from mc import rcon, pos
import places


def run(args):
    p = pos(args["player"])
    if p is None:
        return {"ok": False, "summary": "%s is not online" % args["player"]}
    near = sorted((math.dist(p, v["pos"]), name) for name, v in places.all(rcon).items())
    where = "%s is at %d %d %d" % ((args["player"],) + tuple(int(math.floor(c)) for c in p))
    if near:
        where += ", %d blocks from %s" % (near[0][0], near[0][1])
    return {"ok": True, "summary": where, "pos": [int(math.floor(c)) for c in p]}
```

- **The class:** `action: Literal["<folder name>"]`; `model_config` is `extra="forbid"` from `Action`;
  the shared types in [`_types.py`](_types.py) (`Player`, `PlayerOrAll`, `MobId`, `BiomeId`,
  `StructureId`, `ScriptName`, `PlaceName`); `timeout_s: ClassVar[int]` when it needs more than
  `VFG_DISPATCH_ACTION_TIMEOUT_S`; `vfg_argv()` only when it runs as another `vfg` command
  (`save_place`, `run_script`).
- **The handler:** `run(args)` gets the fields plus `"task"` and returns at least
  `{"ok": bool, "summary": str}`, the summary carrying what the conversation needs: who, where
  (x y z and the place), counts. `from mc import rcon, pos, log` and `import places` are there; an
  exception becomes `{"ok": false, "summary": "handler error: …"}`. Read Minecraft's replies as
  26.3 prints them: [`scripts/_test/mc_26_3.py`](../scripts/_test/mc_26_3.py) has the recorded ones.
- **Nothing else to register:** the dispatcher loads every folder with an `action.py` at start.
  Add its menu line to `MENU` in [`tests/test_classes.py`](tests/test_classes.py), and test the
  handler against [`scripts/_test/fake_world.py`](../scripts/_test/fake_world.py) as
  [`tests/test_handlers.py`](tests/test_handlers.py) does.

## Test

```sh
uv run --no-project --with pytest --with pydantic pytest actions/tests
```

No network and no server: the handlers and `toolkit/remote/action.py` run as EC2 runs them, on
`python3.9` when it is installed, against a fake RCON server answering with 26.3's recorded replies.
