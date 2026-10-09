# The scripts folder

The script library: **the kit** every script imports, **presets** — small steps an implementer copies into a script, each with the values a player might change — and **example scripts** that show presets wired together. Nothing calls a preset: it is copied and bent. What implementers make during a game goes into `made/<server>/`, never in here.

```text
scripts/
  README.md              this file
  _lib/mc.py             RCON and the basics: rcon, players, pos, spawn, on_stop, log, TAG
  _lib/kit/              the kit: values, items, aim, area, mobs, fx, world, exclude, loop
  _presets/<name>.py     one preset
  _test/                 the fake RCON server and world, and the tests
  <name>/main.py         one example script (the 16 seed scripts are older: a README.md beside, positional arguments)
made/<server>/           what this server's implementers made (VFG_SERVER, default "main")
  scripts/<name>/main.py
  presets/<name>.py
```

## The ladder

Every ask takes the lowest rung that does it. The first three write no file.

1. **A plain command:** `vfg cmd 'effect give Alex speed 60 2'`.
2. **A script run again**, for someone else or as a second copy: `vfg script run galick_gun player=Alex as=galick-alex`.
3. **A value changed:** `vfg script set galick-alex cooldown=0`. A value marked `live` reaches the running script within half a second; any other restarts it with the rest kept. "Bigger", "no cooldown", "for him too" are this rung.
4. **A new script from presets:** `vfg script new galick_ice --from galick_gun --preset replace_blocks` copies the script and appends each preset, then you edit the few lines that wire them. `vfg script check` before you report.
5. **A new preset**, only for a step no preset does: write it in a made script, then `vfg preset save <script> <function> <name>` lifts it into `made/<server>/presets/`.

`vfg script list` shows every instance and its values; `vfg state` adds who is online, where, the time and the weather. `vfg script stop <instance>` stops one: everything it made goes.

## A script

```python
"""Spirit Bomb: charge a sphere overhead, then throw it where you look; it leaves a crater."""
ASKS = ["spirit bomb", "genki dama", "the biggest explosion possible"]
from kit import values, items, aim, area, loop

VALUES = {
    "player":   {"type": "player", "default": "@a"},
    "charge":   {"type": "number", "min": 1, "max": 10, "default": 3},
    "cooldown": {"type": "number", "min": 0, "max": 60, "default": 10, "live": True},
    # ...and the values of every preset copied below
}

# --- preset: crater (copied from _presets/crater.py) ---
def crater(at, v):
    area.sphere(at, v["crater_radius"], block=v["crater_leave"], spare_protected=True)

v = values(VALUES)
for p in loop(items.charged(v["player"], "Spirit Bomb", v["charge"], v["cooldown"])):
    r = aim.ray(p, 120)
    if r:
        crater(r.end, v)
```

- Line 1: `"""<Title>: what it does."""`. Line 2: `ASKS`, what players said, in their words, no names. Then imports (`kit`, `mc`, the standard library), then one `VALUES` dict literal, then the copied presets under `# --- preset: <name> (copied from _presets/<name>.py) ---`, then the wiring.
- Python 3.9, standard library only. Never import from `_presets`; never edit `_lib`.
- Everything summoned is tagged `TAG` (`vfg_<instance>`); a stop removes it and everything the kit made.

## A preset

```python
"""Carve a sphere out of the world at a place, leaving air, lava or fire."""
from kit import area

VALUES = {
    "crater_radius": {"type": "int", "min": 2, "max": 64, "default": 8, "live": True},
    "crater_leave":  {"type": "enum", "of": ["air", "lava", "fire"], "default": "air", "live": True},
}

def crater(at, v):
    area.sphere(at, v["crater_radius"], block=v["crater_leave"], spare_protected=True)
```

One-line docstring; imports; `VALUES`; **one public function named like the file**, taking the values `v` last; private helpers `_<name>_...` below it. Every value name starts with the preset's name or a short form of it and `_`, so two presets in one script never ask for the same value.

## Values

`VALUES` entry keys: `type`, `default` (required), `min` and `max` (numbers), `of` (an enum's options; a list's options, or the type of its items), `live` (default false). Types: `int`, `number`, `bool`, `enum`, `str`, `player` (`@a`, `@r`, or names separated by commas), `block`, `mob`, `item`, `particle`, `sound`, `list`.

`v = values(VALUES)` gives each value as its default, then what the command gave, then for a `live` value what `vfg script set` wrote into `/opt/vfg/run/<instance>.values.json`, read again at most every `VFG_LIVE_POLL_S` (0.5) s. Each is checked: a number out of range is clamped and logged, a bad value logged and the one before kept, an id 26.3 refuses (`potion`, `#minecraft:tall_flowers`) refused. Reading a name `VALUES` does not declare raises. A live value passed to the kit as it is (`cooldown=v["cooldown"]`) stays live; read `v[...]` again on each pass for anything else.

## The kit

`from kit import values, items, aim, area, mobs, fx, world, exclude, loop`. Its source is the reference; each module's docstring shows it in use. A place is a `world.Pos` (x, y, z that knows its dimension) or a tuple; a box is a `world.Box` (`(x1, y1, z1, x2, y2, z2)` that knows its dimension; `area.box` and `area.chunk_box` make one) or a 6-tuple.

| Module | For |
| --- | --- |
| `values` | `values(VALUES)`; `values.check(key, entry, raw)`; `values.now(x)` |
| `loop` | `loop(source)` or `loop(every=s)`: the script's loop, waiting while nobody is online; `loop.who(player)`, `loop.online()`, `loop.background(fn, *args)`, `loop.wait(s)` |
| `items` | a named carrot on a stick, given at start, kept or never: `items.clicks`, `items.holds`, `items.charged`, `items.sneaking`; `give`, `has`, `held`, `take`, `item`, `match`, `item_id`; `items.Stat` for a shared statistic |
| `aim` | `aim.look(p)`, `aim.ray(p, length)` → `.end .path .hit_block .why .length .point(k)`, `aim.cast(start, dir, length)`, `aim.near_line(ray, radius, shooter=p)`, `aim.run_on(hit, cmd)`, `aim.cells` |
| `area` | `area.fill(box, block, mode)` of any size, `area.sphere(at, r, block)`, `area.forceload(box)` / `area.release()`; `box`, `chunks`, `chunk_of`, `chunk_box`, `band`, `loaded_chunks`, `minus`, `split`, `plan`, `volume`, `norm` |
| `mobs` | `mobs.around(p, mob, n)`, `mobs.count(tag)`, `mobs.Boss(name, mob, health, bodies, scale, nbt)` → `.spawn(near=p)` `.update()` `.dead()` `.health()` `.remove()` |
| `fx` | `fx.particle`, `fx.line`, `fx.sound`, `fx.title`, `fx.actionbar`, `fx.melody(notes, at)`, `fx.stop_melody`, `fx.pitch` — each for everyone or `to=` one player |
| `world` | `world.Pos`, `world.Box`, `world.run(dim, cmd)`, `world.prefix(dim)`, `world.dim_of(p)`, `world.limits(dim)`, `world.passes(conds)`, `world.first_fail(conds)`, `world.loaded(x, z)`, `world.mspt()`, `world.throttle()`, `world.gamerule(name, value)` / `world.restore()`, `world.objective(criterion, name)`, `world.protected(dim)`, `world.is_protected(at)` |
| `exclude` | `exclude.mobs(where)`, `exclude.players(shooter, where)`, `exclude.targets(...)`: what a hit never touches, as `execute ... <fragment> run <command on @s>` |

`mc` is under it: `rcon(cmd)`, `say`, `players()` (bare names), `bare(name)`, `pos(p)`, `spawn(p, mob, nbt, spread, y, tags)`, `count(selector)`, `score(p, obj)`, `on_stop(fn)`, `stopping()`, `log(...)`, `NAME`, `INSTANCE`, `TAG`, `OWNER`, `default_player()`, `owner_near(d)`, `not_owner()`.

## Minecraft 26.3

Asked of a real 26.3 server (`scripts/_test/live_kit.py`), or seen in the run:

- **Gamerules are snake_case:** `fall_damage`, `mob_griefing`, `keep_inventory`, `advance_time` (was `doDaylightCycle`), `advance_weather`, `spawn_mobs` (was `doMobSpawning`), `spawn_phantoms` (`doInsomnia`), `natural_health_regeneration`, `block_drops` (`doTileDrops`), `mob_drops` (`doMobLoot`), `show_advancement_messages` (`announceAdvancements`), `respawn_radius` (`spawnRadius`), `raids` (was `disableRaids`, the other way round), `fire_spread_radius_around_player` (a number, in place of `doFireTick`). camelCase is refused; `world.gamerule` translates the old names (`world.GAMERULES`).
- **Not entity types:** `potion` (`splash_potion`, `lingering_potion`), `boat`, `chest_boat` (`oak_boat`..., or `#minecraft:boat`). One wrong type fails the whole selector, so the hit hits nothing.
- **A selector takes one `type=X` alone, or any number of `type=!X`, never both:** `@e[type=zombie,type=!villager]` is refused. `exclude.mobs("type=zombie")` drops its own `type=!` for that reason.
- **Gone block tags:** `#minecraft:tall_flowers`, `#minecraft:mineable/sword` (`#minecraft:sword_efficient` is there).
- **Items and experience orbs have a `Health`**, so `if data entity @s Health` alone does not mean alive; `exclude` leaves them out by type.
- **`execute if A if B ...` answers `Test passed`, or nothing at all** when any fails or errors; only a single condition answers `Test failed` or why (`That position is not loaded`, `That position is out of this world!`). `world.first_fail` halves a chain down to the one that says why.
- **A fill is at most 32,768 blocks**, a chunk that is not loaded answers `That position is not loaded` to anything in it, and `Successfully filled N` counts only blocks that changed. "The whole world" means the loaded part, or each chunk as a player enters it. `area.fill` handles all three.
- **No spawn chunks:** with nobody near, nothing is loaded but what is forceloaded (`area.forceload`).
- **A reply over 4 KB is cut; a command over about 1,400 characters is refused** (mc refuses it before sending). Ask about many entities in slices.
- RCON runs in the overworld: anything at coordinates in another dimension needs `execute in <dim>` (`world.run`). The eyes are 1.62 above the feet (1.27 crouching).
- `damage` takes one target: `execute as <selector> run damage @s ...`.
- Text in NBT and components is a component: `CustomName:"Horde King"`, `item_name={"text":"Ice Staff","color":"aqua"}`. Attributes have no `generic.`: `{id:"max_health",base:800}`, `{id:"scale",base:3}`.
- A tamed animal has an `Owner`; `execute ... unless data entity @s Owner` spares pets.
- `bossbar list` shows names, not ids; `bossbar get <id> max` answers `No bossbar exists with the ID ...` for one that is gone.
- **Time:** `time query daytime` is refused (`Can't find element 'minecraft:daytime' of type 'minecraft:timeline'`); `time query day` answers `Timeline minecraft:day is at 6000 tick(s)`, `time query gametime` `The game time is N tick(s)`.

## Running

The box starts `python3 -u <folder>/main.py key=value ...` with `VFG_INSTANCE` (the instance, default the script's name), `PYTHONPATH=/opt/vfg/scripts/_lib`, `VFG_LIVE_POLL_S`, `VFG_MC_PROPERTIES` and `VFG_OWNER`. `TAG` is `vfg_<instance>`, so stopping one instance cleans up only what it made. A stop is SIGTERM: every `on_stop` cleanup runs, the kit's too (items taken back, bossbars removed, forceloads released, gamerules put back as they were, its objectives removed), then `kill @e[tag=<TAG>]`, all within 2 s. The shared objectives `vfg_click` and `vfg_sneak` stay.

## Tests

```bash
uv run --python 3.9 python scripts/_test/run_tests.py     # mc, the kit, and every script
uv run --python 3.9 python scripts/_test/test_kit.py      # the kit alone
eval "$(integration/realmc/env kit)" && uv run --python 3.9 python scripts/_test/live_kit.py   # the kit on a real 26.3 server
```

`run_tests.py` starts every example as on EC2 (its defaults, `VFG_INSTANCE` its folder name) against a fake world with Steve and Alex online; Steve tries each item it gives him (a click, a 1.5 s hold) and crouches; then it is stopped. It must send commands, raise nothing, exit 0 within 2 s, and leave nothing tagged `vfg_<name>`: no entity, bossbar, forceload or objective.

`_test/fake_rcon.py` is a fake RCON server; `_test/fake_world.py` is a small fake 26.3 world behind it (players, blocks, chunks, scoreboards, fills, bossbars, forceloads). The fake proves the plumbing; a real 26.3 server is the bar for "it works": `live_kit.py` runs only against a realmc test server, never the box.
