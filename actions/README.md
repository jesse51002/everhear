# actions — the brain's menu: the game engine's operations

**Every action is one of the game engine's operations, and the actions are the whole menu: one way to do each thing.** Everything that happens in the game is an engine class (`engine/WRITING.md`), started, granted, given, applied, bound or configured through these eleven; there is no script and no copy. One file per action, `actions/<name>.py`, holding one pydantic class named as the file in CamelCase: `engine_grant.py` holds `class EngineGrant`. The class is the action's whole definition and holds no code: its docstring's first line is its line on the menu, its ClassVars name the engine's control op its values go to, and its fields are what that op takes.

[`_types.py`](_types.py) has the `Action` and `Values` bases and the value types; [`lib/py/vfg_actions.py`](../lib/py/vfg_actions.py) loads, checks and renders the actions and keeps `~/.vfg/actions.json`. The engine's control ops are in `engine/README.md` and `engine/vfg_engine/ops.py`.

## One action

```python
# actions/engine_apply.py
from actions._types import Action, Args, ClassId, Field, Player


class EngineApply(Action):
    """Put one of the engine's marks on a player, with its args: shrunk, giant, frozen, burning; it ends by itself when its time is up, or with engine_clear.
    ...
    """

    engine = "apply"
    takes = ["player", "mark", "args", "seconds"]

    player: Player = ""
    """who bears it: one player's exact name"""
    mark: ClassId = ""
    """the mark, by its name under ENGINE, such as giant"""
    args: Args = {}
    """its args, each by its name as ENGINE lists them"""
    seconds: float = Field(0, ge=0, le=86400)
    """how long it lasts, in seconds; 0 for the mark's own duration"""
```

- **The docstring's first line** is the action's line on the menu, the brain's and Jev's: what it does, in players' words. The brain also reads the whole docstring and each field in `takes`.
- **`engine`**: the control op its values go to (`vfg engine <op>` sends the same). **`takes`**: the fields the brain fills, the target first; every other keeps its default. **`timeout_s`**: for an op slower than `VFG_DISPATCH_ACTION_TIMEOUT_S` (a start that waits for a one-off's result, a bind's alias call, a Config's reload).
- **A field per value the op takes**: a type from `_types.py`, the default, limits as `Field(ge=, le=)` or a pattern, and the string under it as its description, in players' words.

## The eleven

| Action | Op | Takes |
| --- | --- | --- |
| `engine_start` | `start` | `scenario`, `args`; waits up to `VFG_ENGINE_START_WAIT_S` (15 s) for a one-off's result, else answers "<scenario> is running as <instance>" |
| `engine_stop` | `stop` | `scenario` (every run of it) or `instance` (one) |
| `engine_give`, `engine_take` | `give`, `take` | `player`, `item` |
| `engine_grant`, `engine_revoke` | `grant`, `revoke` | `player`, `ability` |
| `engine_apply`, `engine_clear` | `apply`, `clear` | `player`, `mark` (and `args`, `seconds` to apply) |
| `engine_bind` | `bind` | `player`, `phrase`, and `ability`, or `scenario` with `args` set once (`caster` for a Player or Place arg: whoever says it) and `cooldown_s`; its aliases are made as it is bound (`toolkit/README.md`) |
| `engine_unbind` | `unbind` | `player`, `phrase` |
| `engine_config` | `config` | `class_id`, `config`: the class's whole new Config in Python, after the brain read it (`read_configs`) |

The library's everyday scenarios (`command` for raw server commands, `teleport`, `save_place`, `summon_around`; TODO(library): their ids and args once `engine/library/` has them) are started with `engine_start`, as any scenario.

## The value types

| Type | Takes | Checked |
| --- | --- | --- |
| `Player` | one player's exact name; empty for none given | `^(|[A-Za-z0-9_]{1,16})$`: never `@a` (bindings, grants and marks are per player) |
| `ClassId` | a class's id as the engine names it, such as `fire_staff`; empty for none given | `^(|[a-z][a-z0-9_]{0,63})$` |
| `Instance` | one running instance, as RUNNING NOW names it, such as `scenario:horde#3` | `^(|[A-Za-z0-9_.:@#-]{1,96})$` |
| `Args` | a scenario's or a mark's args, by name: an object, or a word holding one as JSON | an object; each arg is the engine's to check against the class's own Args (it changes with any reload) |
| `Python` | Python source, such as a whole Config class (`engine_config`'s `config`) | taken whole, every line, its indentation and comments kept: line ends made `\n`, no other control character but a tab, at most 20,000 characters; whether it parses is the engine's check |
| `int`, `float` with `Field(ge=, le=)` | a number | out of range is clamped to the limit and a note says so |
| `bool`, `Literal[...]`, `str`, `list[...]` | true or false, one of a few words, text, several of a type | a word from the command line is read as the type: `yes`/`off`/`1`, an option in any case, a list as `a,b,c` or JSON; text is one line |

## How they run

`vfg action run <action> key=value … [--task <name>]` checks the values (`vfg_actions.check_values`), then sends them to the engine as `vfg engine <op>` does, and prints one JSON line: `{"ok", "action", "running", "summary", "facts", "notes", "error"?}`; `running` is true for a scenario that goes on, `facts` the engine's reply (a one-off's result facts, `changed` for a Config), a refusal `"ok": false` with the engine's words. A scenario's or a mark's args come as `args.<name>=<value>` words (a value starting with `[` or `{` is JSON, anything else text the engine reads as its arg's type) or one `args=<JSON object>`. Exit 2 for a value refused here, 6 when the engine is not running, 124 when it took the request and did not answer in time (it may still have happened).

## Check and test

`vfg action check <name>` (`check_action`) refuses a class not named as its file, an empty docstring, an `engine` that is no op's name (`^[a-z][a-z0-9_]{0,31}$`), a field whose type is not one of `_types.py`'s, a field with no description, a `takes` naming a field the class lacks, a default its field refuses, and code: an action has no method or validator.

```sh
uv run --no-project --with pytest --with pydantic pytest actions/tests
cd lib/py && uv run --group dev python -m pytest
```

[`tests/test_engine_actions.py`](tests/test_engine_actions.py) holds the eleven to what the menu says and the engine would refuse. [`tests/test_places.py`](tests/test_places.py) tests the old kit's `scripts/_lib/places.py`, and goes with `scripts/` (the engine keeps the places now).
