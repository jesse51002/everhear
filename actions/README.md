# actions — what anything can run on the server

**An action makes one script callable, and actions are the only thing anything calls.** One file per action, `actions/<name>.py`, holding one pydantic class named as the file in CamelCase: `gatling_gun.py` holds `class GatlingGun`. The class is the action's whole definition and holds no code: its docstring's first line is its line on the menu, its ClassVars name the script it runs and how, and its fields are the values the script reads. What implementers make goes in `made/<server>/actions/<name>.py`, by the same rules, under a name no library action has.

[`_types.py`](_types.py) has the `Action` and `Values` bases and the value types; [`lib/py/vfg_actions.py`](../lib/py/vfg_actions.py) loads, checks and renders the actions and keeps `~/.vfg/actions.json`. The spec: `Internal/workspace/pages/home/children/Specs/children/2026-10-09-Prototype_Actions/`, and its `Library_Actions` child for which actions to write.

## One action

```python
# actions/railgun.py
from actions._types import Action, Field, Literal, Live, ParticleId, Players, SoundId, preset


class Railgun(Action, preset("damage_hits")):
    """Give a player a railgun: click to fire a beam that hurts everything it passes, through walls."""

    script = "railgun"
    runs = "until_stopped"
    takes = ["player", "damage_amount", "cooldown", "range", "through_walls"]
    defaults = {"damage_amount": 30, "damage_type": "magic"}
    fixed = {"fire": "click"}

    player: Players = "@a"
    """who gets one: a name, several separated by commas, or @a"""
    cooldown: Live[float] = Field(2, ge=0, le=60)
    """seconds between shots"""
    # ...a field for every value the script reads that no preset holds
```

- **The docstring's first line** is all the selector sees: what it does, in players' words.
- **`script`**: the folder under `scripts/` (a made action: `made/<server>/scripts/` or `scripts/`). **`runs`**: `"once"` for a script that ends by itself, whose `result()` is the answer (a castle, a meteor), or `"until_stopped"` (a weapon, a rain, a rule). **`timeout_s`**: for a once action slower than `VFG_DISPATCH_ACTION_TIMEOUT_S`.
- **`takes`**: the fields the planner fills, usually two to six, the target first. Every other field keeps its default, and an edit can still change it.
- **The presets the script copied** (its `# --- preset: <name>` lines) are inherited, `preset("<name>")` each, so a preset's value is written once, in `scripts/_presets/<name>.py`'s `class <CamelName>(Values)`. **`defaults`** gives an inherited field this action's own default (`damage_amount` is 20 in the preset, 30 here); the fields the action declares are the script's own values.
- **A field per value the script reads**, named as the script reads it: a type from `_types.py`, the default, limits as `Field(ge=, le=)`, and the string under it as its description, in players' words. **`Live[T]`** for a value the script reads again while it runs (its VALUES said `"live": True`): an edit to it lands within half a second; an edit to any other restarts the copy.
- **`fixed`**: values this action sets, which nobody fills; a run that gives one is refused, an edit may still change it.

## A variant

One script has as many actions as players have names for what it does. A variant subclasses the action and fixes what makes it different; it inherits `script`, `runs` and the fields, may replace `takes`, and its `defaults` and `fixed` merge over its parent's (a key it puts in `defaults` is no longer fixed):

```python
# actions/gatling_gun.py
from actions.railgun import Railgun


class GatlingGun(Railgun):
    """Give a player a gatling gun: hold right-click to rapid-fire."""

    takes = ["player", "damage_amount", "damage_what"]
    fixed = {"fire": "hold", "item": "Gatling Gun", "cooldown": 0.1, "range": 24,
             "particle": "crit", "sound": "entity.arrow.shoot"}
```

## The value types

| Type | Takes | Checked |
| --- | --- | --- |
| `Player` | one player's exact name | `^[A-Za-z0-9_]{1,16}$` |
| `Players` | names separated by commas, or `@a`, `@r`, `@p` | each name as `Player` (the kit's `player`) |
| `MobId`, `BlockId`, `ItemId`, `ParticleId`, `SoundId` | an id; `minecraft:` is dropped, `[states]` and `{nbt}` kept where the kit takes them | the kit's patterns, and the ids 26.3 refuses (kit/world.py's `GONE`: `potion`, `boat`, `#minecraft:tall_flowers`) |
| `Place` | a saved place's name, `x y z` (then `the_nether` or `the_end`), or a player's name; empty for none | a saved name against the places cache when one is given; coordinates inside the world. EC2 finds where it is as the script runs |
| `Command` | one server command, no slash | the owner's commands and the block commands refused (`scripts/_lib/commands.py`) |
| `CopyName` | a running copy's name, such as `galick_gun_2`; empty for none | `^[a-z0-9][a-z0-9_]{0,47}$` |
| `int`, `float` with `Field(ge=, le=)` | a number | out of range is clamped to the limit and a note says so, as the kit does |
| `bool`, `Literal[...]`, `str`, `list[...]` | true or false, one of a few words, text, several of a type | a word from the command line is read as the type: `yes`/`off`/`1`, an option in any case, a list as `a,b,c` or JSON |
| `Live[T]` | any of these | as `T` |

`Field` and `Literal` are importable from `actions._types` too, so a preset needs that one import.

## A preset's values

Beside the preset's function in `scripts/_presets/<name>.py`, with a description under every field, written from what the function does; it keeps its `VALUES` until the kit's `values()` reads the checked values instead:

```python
from actions._types import Field, Literal, Live, Player, Values  # the values class: read on the PC only


class DamageHits(Values):
    damage_amount: Live[float] = Field(20, ge=0, le=100000)
    """damage to each thing hit, in half-hearts (a player has 20); does nothing while damage_kill is on"""
```

`preset()` runs only that class (and the file's imports from `actions`, `pydantic` and `typing`), never the rest of the file, which imports `mc` and the kit; `vfg script new --preset` copies the function and never the class.

## Check and test

`vfg action check <name>` (`check_action`) refuses a field whose type is not one of `_types.py`'s, a field with no description, a `takes` naming a field the class lacks or one it fixes, a `defaults` or `fixed` value (or a default) its field refuses, an empty docstring, a missing script folder, a made name equal to a library name, and code: an action has no method or validator.

```sh
uv run --no-project --with pytest --with pydantic pytest actions/tests
cd lib/py && uv run pytest
```

[`tests/test_values_parity.py`](tests/test_values_parity.py) holds every action over a library script to the values the script took before it was ported (`tests/fixtures/values_before.json`, from each script's `VALUES`): the same names, defaults (after `defaults`, before `fixed`), limits, kinds and live-ness. A script with no action yet is skipped and named in one log line; so is a preset with no values class (`preset_values_before.json`). A field the spec adds to a script (horde_boss's `at`) goes in its `ADDED`.

## The core actions

`command`, `teleport`, `summon_around` and `save_place` are the dispatcher's first actions, each now a class over a script of its own name; `stop` and `edit` end and change a running copy, by its name (`CopyName`): `edit`'s `values` are the values to change, checked against the copy's own action. `run_script` and `stop_script` are gone: a script runs only through an action. Their tests: [`tests/test_core_actions.py`](tests/test_core_actions.py).
