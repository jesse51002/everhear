# Writing content for the engine

Content is Python classes the engine runs: items, abilities, marks, actors and scenarios (the game
engine spec, `Specs/2026-10-12-Prototype_Game_Engine`). You say what happens; the engine decides
when it runs, who has it, how long it lasts, and removes what it made when it ends.

- **One class per file**, `made/<server>/content/<id>.py` (on the box,
  `/opt/vfg/made/<server>/content/`). The id is the class name in snake_case (`FireStaff` →
  `fire_staff`), or `id = "..."` on the class: a letter, then up to 63 of a-z, 0-9 and `_`.
- **Import only from `vfg_engine`** (and its kit, `vfg_engine.kit`, the shared helpers: [KIT.md](KIT.md)):
  `from vfg_engine import Ability, AbilityConfig, Field, Hit`. A class never imports another content
  class.
- **A server's version shadows the library's.** The content folders are ordered: the library
  (`/opt/vfg/engine/library/`) first, then `made/<server>/content/`. A class there with the same id
  as a library class replaces it for this server, and running instances switch to it; the library's
  comes back when that file is removed. The same id twice in one folder is refused. This is how a
  library class gets this server's own numbers: its file copied into `made/<server>/content/`, then
  changed.
- **Nothing runs until `engine reload`.** It checks the class first; if a check fails, the old
  version keeps running and you get the error. If it passes, every running instance switches to
  the new code at once. A reload of named files takes only files directly in a content folder
  (a path elsewhere is refused, in words).

Working examples: [examples/](examples/) (`fire_staff.py`, `fireball.py`, `giant.py`, `shrunk.py`).

## The five kinds

| Kind | One instance per | It exists while | `ctx.user` |
|---|---|---|---|
| `Item` | holder | someone holds it where `active_in` says | the holder |
| `Ability` | grant (a player or an actor) | it is granted; a triggered one runs once per cast, as a **run** | the caster |
| `Mark` | application on a bearer | until its duration ends, it is cleared, or (by its settings) death or milk | the bearer |
| `Actor` | spawned entity | the entity exists | its own body |
| `Scenario` | start | until stopped or its duration ends | `None` |

You never make an instance; the engine does.

## Config: every number in one place

Each class has a `Config`, the same for every player. Write values without annotations when the
base declares them; give your own fields an annotation and bounds:

```python
class Config(AbilityConfig):
    summary = "Burns, then explodes."           # required everywhere
    radius: float = Field(3, ge=1, le=8)        # your own field, with bounds
    scale_per_point: float = 0.05
    charge_s = 0                                # zero is a value: it must be written
    cooldown_s = 5

    def damage(self, ctx):                      # a formula: a method taking ctx
        return self.radius * (1 + self.scale_per_point * ctx.user.power())

    def duration_s(self, ctx):
        return 4
```

| Base | Required | Optional (default) |
|---|---|---|
| `AbilityConfig` | `summary`, `damage`, `duration_s`, `charge_s`, `cooldown_s`, `scale_per_point` | `cooldown_from` (`"cast"`, or `"end"`), `self_damage` (`True`: its explosions hurt its user) |
| `MarkConfig` | `summary`, `duration_s`, `damage` | `on_death` (`"end"`, or `"keep"`), `on_milk` (`"keep"`, or `"end"`), `again` (`"refresh"`, or `"stack"`) |
| `ItemConfig` | `summary`, `base`, `name`, `active_in` | its look: `model` (`"minecraft:bow"`: what it looks like), `color` (its name's: `"gold"`, `"#ff8800"`), `lore` (lines), `glint`, `enchantments` (`{"power": 5}`), `unbreakable` |
| `ActorConfig` | `summary`, `entity_type`, `health`, `size`, `damage` | `name`, `name_visible`, `glowing`, `abilities` (granted at spawn), `equipment` (slot → item; it never drops), `nbt` (extra SNBT for its summon) |
| `ScenarioConfig` | `summary`, `duration_s` | — |

- `damage` and `duration_s` may be a value or a method taking `ctx`. Everything else is a value.
- A duration that never ends by itself is `duration_s = UNTIL_STOPPED`.
- **`damage` is what one target takes from one cast**, scale included. For an ability that runs
  until stopped (and a passive one), and a mark that lasts until stopped, it is per second. **Deal
  the same parts the formula adds up**: the engine counts what each cast dealt to each target and
  logs one well over `damage(ctx)`.
- `ctx.user.power()` is 1 for everyone for now. With `scale_per_point` above 0, `damage(ctx)` must
  change with power (it is checked at power 0, 10 and 20).
- `active_in`: `"mainhand"`, `"offhand"`, `"hands"` (either), `"worn"` (an armour slot),
  `"inventory"` (anywhere). `base`: a vanilla item the datapack can report your item's triggers
  on (`carrot_on_a_stick` or `warped_fungus_on_a_stick` for right-clicks).
- Read values as `self.config.radius`, formulas as `self.config.damage(ctx)`.

## Args: what a Scenario or a Mark is started with

Items and abilities take no args (their numbers are their Config). A Scenario's start, or a
Mark's application, may: declare `class Args(Args)`, every field with a default and a description.

```python
class Meteor(Scenario):
    class Args(Args):
        at: Place = Field("", description="where it falls: a saved place, x y z, or a player")
        count: int = Field(5, ge=1, le=50, description="how many rocks")
        who: Players = Field("@a", description="who sees it")

    def start(self, ctx):
        x, y, z = self.args.at.pos              # a Place is a Spot: dim, pos, yaw, name
        for name in players(ctx, self.args.who):   # read fresh each time: it follows joins
            ...
```

`start` and `apply` check what they are given and refuse in words (an arg not declared too).
`self.args` is the model. The types: `Player` (an online player's name), `Players` (`"@a"`, `"@r"`
or names), `Place` (a saved place's name, `"x y z [dim]"`, or a player's name → a `Spot`),
`EntityType`, `BlockId`, `BlockMix` (`"70% stone 30% andesite"`), `ItemId`, `ReplaceRules`
(`"snow > air; solid > 70% lava 30% keep"`), `Command` (never the owner's), `Seconds`; or a plain
`int`, `float`, `str`, `bool` with bounds. The `classes` and `meta` ops show each class's args to the
brain.

A scenario's result: `self.result("landed", count=3)`; a one-shot calls it, then `self.end()`. A
`start` with `wait_s` (at most 30) answers with that result once it ends. A scenario may start
another it owns: `self.start_scenario("meteor", at="Steve")` (not `self.start`: that is your
`start(ctx)`). An incantation may start a scenario with fixed args; an arg given as `"caster"`
(a Player or a Place) is filled with whoever spoke.

## State: what an instance remembers

An instance keeps **nothing on `self`** (`self.x = 1` is an error). Declare a `State` and use it:

```python
class State(State):
    hits: int = 0
    at: list[float] = []
```

Every field needs a default: the engine makes a fresh State from the defaults alone.
`self.state.hits += 1`. Plain data only (numbers, text, lists, dicts). Every State also has
`kit`, a dict the kit's helpers keep their memory in. It is saved about once a
second and survives a reload and a restart; a field you add later takes its default. In an
ability, during a cast, `self.state` is **that run's own** State (a fresh one each cast).

## What the engine calls

All optional. Each gets `ctx` first.

| Method | When |
|---|---|
| `start(ctx)` | the instance begins (an item held, an ability granted, a mark applied, an actor spawned, a scenario started) |
| `stop(ctx, reason)` | it ends: `"unheld"`, `"left"`, `"died"`, `"expired"`, `"duration"`, `"stopped"`, `"revoked"`, `"reloaded"`, `"error: ..."`, `"cut: ..."` ... What it made is removed for you |
| `tick(ctx)` | every `every` seconds (`every = 0.5` on the class); in a triggered ability, during each run |
| `on_cast(ctx, ev)` | an ability is cast, once its charge is done. Without it, the ability is passive |
| `on_charge(ctx, ev)` | each tick while an ability's `charge_s` counts down (the charge show); `ev.progress` 0 to 1 |
| `done(ctx)` | the block queue has placed the last of this instance's blocks |
| `on_config(ctx, old)` | the Config's values changed on reload; without it the instance restarts (stop, start) with the new values, keeping its state and its end |
| any name you listen or time with | `self.listen(Hit, "on_hit")` calls `on_hit(ctx, ev)`; `self.after(2, "boom")` calls `boom(ctx)` |

**No loops and no waiting.** A `while True`, or `sleep`, is refused at reload. Repeat with
`every`/`self.every`, wait with `self.after`. A call over 5 ms is logged; over 50 ms it is cut,
that instance ends and its cleanup runs. Those are your code's own milliseconds: time spent waiting on
Minecraft (`ctx.query`, the live reads of `ctx.world`) is not counted, but one call may wait at most
250 ms in all: each read gets what is left of that, a read that does not answer in time is given up,
and one asked for once it is spent is refused, both with an error that says so (catch it, or read
less in one call, or use `self.ask`). A call that waits over 50 ms on Minecraft is logged slow too.
An error ends only that instance (in an ability, only that cast's run).

**A cast's run ends** at its duration, at `self.end()`, when its grant goes, or when its caster dies
or leaves; what the run spawned goes with it, except what it spawned with `keep=True` (a summon meant
to outlive its summoner).

## `ctx`: what you read

| | |
|---|---|
| `ctx.user` | who is using it (a player or an actor's body): `.name`, `.pos`, `.eye`, `.facing`, `.dim`, `.health`, `.look` (what the raycast hit), `.looking_at()` (that point), `.power()`, `.has("fireball")`, `.cast("fireball")`; an actor's body also `.aim(player_or_uuid_or_pos)` |
| `ctx.source` | who caused it (who applied a mark); else the user |
| `ctx.world` | `.players`, `.player(name)`, `.players_near(pos, r)`, `.entities_near(pos, r)`, `.cone(origin, direction, angle, reach)`, `.get(uuid)`: this tick's snapshot and the mirror, from memory |
| `ctx.now` | the engine's time, seconds |
| `ctx.running` | in an ability's cast: its other runs still going; each has `.end()` |
| `ctx.places` | the saved places of this world: `.get(name)` → `{dim, pos, yaw, by, at}` or None, `.all()` |
| `ctx.query(command)` | one command now, and Minecraft's reply; its wait counts against the call's 250 ms of reads, not its 50 ms (past them: an error in words) |

`ctx.world` also reads Minecraft live, each read counted against your 50 ms:
`probe(points, test)` (many blocks in one read: a block id, `"#tag"`, `"solid"`, or `"open"`: it
and the block above are air), `ground_y(x, z)` (the y where feet stand: one above the highest
solid block, fluids counted and leaves not; None in a chunk not loaded), `ray(origin, direction, length,
through_blocks=False)` (a `Look`; it never stops at entities), `entities(center, radius,
types=None)` (living things at any distance in loaded chunks, not players), `loaded(x, z)`, and
`mspt` (the server's ms per tick, 50 is lag). `ctx.world.mirror_radius` is how far round each
player `entities_near` sees; past it, read live with `entities`. A player also has `.on_ground` and `.sprinting`; an
entity snapshot `.owner` (a pet's or a projectile's) and `.in_ground`.

`ctx.user.cast(name)` returns `"fired"`, `"charging"`, `"cooldown"`, `"not_granted"`, `"unknown"`
or `"failed"`, which also has `.remaining_s` (a cooldown's seconds left) and `.error` (why it
failed): `got = ctx.user.cast("railgun")`, then `if got == "cooldown": ... got.remaining_s`. From an
item, an ability the holder lacks is lent to them while the item is held.

## `self`: what you do

| Method | Does | Removed when it ends |
|---|---|---|
| `self.listen(Event, "method", about="user")` | call `method(ctx, ev)` on each such event (see scopes below). A `Mine` listen names the blocks it watches, `blocks=["minecraft:diamond_ore"]`: Minecraft reports breaking only the kinds some instance watches | yes |
| `self.unlisten(Event, "method")` | stop listening | |
| `self.every(seconds, "method")` / `self.after(seconds, "method", *args)` | a repeating / one-off timer; returns its id; `self.cancel(id)` | yes |
| `self.end()` | end this instance (in an ability's cast: end that run) | |
| `self.modifier(attribute, amount, operation, on=None)` | your modifier on your user, or `on` another entity (a uuid) (`"scale"`, `"movement_speed"`...; `"add_value"`, `"add_multiplied_base"`, `"add_multiplied_total"`); `None` takes it off | yes |
| `self.potion(effect, seconds, amplifier=0, who=None)` | give a potion and forget it (the engine never clears one) | no: it runs out |
| `self.keep_potion(effect, amplifier=0, seconds=3)` | a `seconds` potion given again every 2 s (every 1 s from 10 s up: night vision wants `seconds=12`, or it flickers) while you last | yes (stops giving) |
| `self.damage(target, amount, damage_type)` | deal damage through the engine; a tick's damage to one target from one user goes as one hit (Minecraft ignores most of a second hit within half a second of the last), so space hits on one target 0.5 s apart | |
| `self.explode(at, radius, damage)` | an explosion's damage, sound and particles; no blocks break; your user too unless `self_damage = False`; returns whom it hit. A player takes it scaled by difficulty, as a vanilla explosion (1.5 times on hard) | |
| `self.spawn(what, at, nbt=None, keep=False)` | an Actor class by id, or `"minecraft:zombie"` with extra SNBT; returns its uuid. `self.spawned` lists what is still there, `self.remove(uuid)` takes one away | yes (removed); `keep=True`: left in the world unless you are stopped |
| `self.apply(who, mark, seconds=None, **args)` | put a mark, with its args, on a player, an actor or any mob (by uuid; the mark ends when the mob is gone) | only for a scenario |
| `self.start_scenario(scenario, **args)` | start a scenario you own | yes |
| `self.result(summary, **facts)` | a scenario's result (what `start` answers once it ends) | |
| `self.save_place(name, at, by=None)` / `self.forget_place(name)` | name a place in this world (the engine's own) | no |
| `self.teleport(who, at, dim=None, yaw=None, pitch=None)`, `self.motion(who, vec)`, `self.entity_data(who, snbt)`, `self.ignite(who, seconds)` | move, push, change and set on fire a player or an entity | |
| `self.blocks.fill(a, b, block_or_mix, replace=None, mode="replace", undo=False)`, `.setblock(pos, block)`, `.replace((a, b), rules)`, `.sphere(center, r, block, hollow=False)`, `.data(pos, path, value, how="append")` (a block entity's data: a chest's Items), `.forceload(a, b)`; `.done` | the block queue, placed in the order you asked: queuing a fill costs a few microseconds, so thousands fit in one call; a sphere may have a radius up to 1024, planned ring by ring from its middle; placed a little each tick, in loaded chunks only, never in a protected box or the bedrock floor, slower when the server is; `undo=True` puts air back where your blocks still stand when you end; your `done(ctx)` is called when it empties | jobs not placed yet are dropped; forceloads released |
| `self.ask(command, "method")` | a slow command (locate, spreadplayers, place feature) after the tick, outside the 50 ms; `method(ctx, reply)` next tick | |
| `self.bossbar(id, name=, color=, value=, max=, players=)` | a boss bar | yes |
| `self.stop_sound(name=None, to=None)`, `self.spawnpoint(player, at)` | | |
| `self.item_stack(item, count=1)` | an EverHear item's stack as SNBT (for a chest, a mob's hand) | |
| `self.grant(who, ability)` | grant an ability while you last | yes |
| `self.hold(setting, value, priority=0)` / `self.release(setting)` | hold a gamerule, `"weather"` (`"clear"`, `"rain"`, `"thunder"`) or `"time"`; when the last hold goes, the setting is put back as it was (the time cannot be read, so it carries on) | yes |
| `self.watch(area, center=, radius=)`, with `height=` a flat cylinder (`math.inf`: every height), or `box=(a, b)` | `Enter` / `Exit` events for that area; those inside when it is made get their `Enter` right after the call | yes |
| `self.command(text)`, `self.particle(name, at, force=False, to=None)`, `self.sound(name, at, to=None)`, `self.message(text, to=None)` | output (`to`: only these players; `force`: seen from far); a raw command is the escape hatch | |

Also `self.config`, `self.state`, `self.instance` (its id), a mark's `self.stack`, a scenario's
or mark's `self.args`. Anywhere a place is taken (`at`), give a `Spot`, a player, a position, or
`"x y z [dim]"`.

Modifier ids are the engine's: `everhear:item/<id>`, `everhear:mark/<id>` (or `/1`, `/2` for a
stacking mark), `everhear:ability/<id>` (or `/<run>` for a cast's), `everhear:actor/<id>`.
Minecraft adds them up with every other modifier, so two things changing size at once both count,
and either can end without disturbing the other. Never write `/attribute` or `effect clear`
yourself.

## Events

`Use(item, slot)`, `Hit(target, target_type, item)`, `Hurt(amount, attacker, attacker_type)`,
`Kill(target, target_type)`, `Death`, `Respawn`, `Milk`, `Join`, `Leave`, `Held(item, slot)`,
`Unheld(item, slot)`, `Sneak`, `Unsneak`, `Enter(area, instance)`, `Exit(area, instance)`,
`Gone(type, actor)`, `Cast(ability, phrase, cast_id, by, progress)`, `Land(item, projectile,
projectile_type, pos, dim, hit, block, target, target_type)` (a player's arrow or trident landed:
`hit` is `"block"` or `"entity"`; `block` the block it stuck in), `Mine(block, pos, count)` (a
block of a watched kind broken; `pos` is the block the player was looking at, when it was that
kind, else None).
`Hit` has `ranged` (an arrow's hit); `Death` has `pos` and `dim`. Each has `ev.who`: the player's
name (or an actor's uuid) it is about.

Players are never among `ctx.world.entities_near` (they are `players_near`).

Damage you deal comes back from Minecraft as your user's own hit: the engine leaves those hits out,
so answering a `Hit` with damage cannot loop. A `Hurt` or `Kill` your damage caused arrives with
`ev.engine = True`. A use, hit, hurt or kill more than 5 s old (queued while the engine was down)
is not handed over.

`Held` and `Unheld` are a change between two moments the engine saw: none comes when a player
joins already holding something, or after the engine restarts. That the item's instance starts is
how you know it is held.

**Who hears what:**
- by default, events about your user (an item: its holder; for `Use`, `Held`, `Unheld`, `Land`,
  only this item's). An item that listens for `Land` keeps hearing it for 15 s after it is let go
  (what it shot is still flying); held again, it carries on;
- `about="anyone"`: every one;
- a scenario hears everything;
- the instance that watches an area hears its `Enter`/`Exit`, and whatever spawned something hears
  its `Gone`.

## The checks at reload

It imports; it subclasses one kind and its Config the matching base; its id starts with a letter;
no `while True`, no sleep, no `__init__`; its `State()` can be made from its defaults; every
required field has a value; every value in its bounds; `duration_s` a number or
`UNTIL_STOPPED`; `damage(ctx)` a number at power 0, 10 and 20 that changes with power when
`scale_per_point > 0`; an item's base can report the triggers it listens for; an actor's
`entity_type` exists in 26.3; a Scenario's or Mark's Args (only theirs) have a default and a
description for every field, and the defaults fit. A running instance whose state or args no
longer fit stops; a listen or a timer naming a method that is gone is dropped.
