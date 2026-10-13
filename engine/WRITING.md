# Writing content for the engine

Content is Python classes the engine runs: items, abilities, marks, actors and scenarios (the game
engine spec, `Specs/2026-10-12-Prototype_Game_Engine`). You say what happens; the engine decides
when it runs, who has it, how long it lasts, and removes what it made when it ends.

- **One class per file**, `made/<server>/content/<id>.py` (on the box,
  `/opt/vfg/made/<server>/content/`). The id is the class name in snake_case (`FireStaff` →
  `fire_staff`), or `id = "..."` on the class: a letter, then up to 63 of a-z, 0-9 and `_`.
- **Import only from `vfg_engine`**: `from vfg_engine import Ability, AbilityConfig, Field, Hit`.
- **Nothing runs until `engine reload`.** It checks the class first; if a check fails, the old
  version keeps running and you get the error. If it passes, every running instance switches to
  the new code at once.

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
| `ItemConfig` | `summary`, `base`, `name`, `active_in` | — |
| `ActorConfig` | `summary`, `entity_type`, `health`, `size`, `damage` | `name`, `abilities` (granted at spawn), `equipment` (slot → item) |
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

## State: what an instance remembers

An instance keeps **nothing on `self`** (`self.x = 1` is an error). Declare a `State` and use it:

```python
class State(State):
    hits: int = 0
    at: list[float] = []
```

Every field needs a default: the engine makes a fresh State from the defaults alone.
`self.state.hits += 1`. Plain data only (numbers, text, lists, dicts). It is saved about once a
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
| `on_config(ctx, old)` | the Config's values changed on reload; without it the instance restarts (stop, start) with the new values, keeping its state and its end |
| any name you listen or time with | `self.listen(Hit, "on_hit")` calls `on_hit(ctx, ev)`; `self.after(2, "boom")` calls `boom(ctx)` |

**No loops and no waiting.** A `while True`, or `sleep`, is refused at reload. Repeat with
`every`/`self.every`, wait with `self.after`. A call over 5 ms is logged; over 50 ms it is cut,
that instance ends and its cleanup runs. An error ends only that instance (in an ability, only that
cast's run).

## `ctx`: what you read

| | |
|---|---|
| `ctx.user` | who is using it (a player or an actor's body): `.name`, `.pos`, `.eye`, `.facing`, `.dim`, `.health`, `.look` (what the raycast hit), `.looking_at()` (that point), `.power()`, `.has("fireball")`, `.cast("fireball")`; an actor's body also `.aim(player_or_uuid_or_pos)` |
| `ctx.source` | who caused it (who applied a mark); else the user |
| `ctx.world` | `.players`, `.player(name)`, `.players_near(pos, r)`, `.entities_near(pos, r)`, `.cone(origin, direction, angle, reach)`, `.get(uuid)`: this tick's snapshot and the mirror, from memory |
| `ctx.now` | the engine's time, seconds |
| `ctx.running` | in an ability's cast: its other runs still going; each has `.end()` |
| `ctx.query(command)` | one command now, and Minecraft's reply; its time counts against your 50 ms |

`ctx.user.cast(name)` returns `"fired"`, `"charging"`, `"cooldown"`, `"not_granted"`, `"unknown"`
or `"failed"`. From an item, an ability the holder lacks is lent to them while the item is held.

## `self`: what you do

| Method | Does | Removed when it ends |
|---|---|---|
| `self.listen(Event, "method", about="user")` | call `method(ctx, ev)` on each such event (see scopes below) | yes |
| `self.unlisten(Event, "method")` | stop listening | |
| `self.every(seconds, "method")` / `self.after(seconds, "method", *args)` | a repeating / one-off timer; returns its id; `self.cancel(id)` | yes |
| `self.end()` | end this instance (in an ability's cast: end that run) | |
| `self.modifier(attribute, amount, operation)` | your modifier on your user (`"scale"`, `"movement_speed"`...; `"add_value"`, `"add_multiplied_base"`, `"add_multiplied_total"`); `None` takes it off | yes |
| `self.potion(effect, seconds, amplifier=0, who=None)` | give a potion and forget it (the engine never clears one) | no: it runs out |
| `self.keep_potion(effect, amplifier=0)` | a 3 s potion given again every 2 s while you last | yes (stops giving) |
| `self.damage(target, amount, damage_type)` | deal damage through the engine; a tick's damage to one target from one user goes as one hit (Minecraft ignores most of a second hit within half a second of the last), so space hits on one target 0.5 s apart | |
| `self.explode(at, radius, damage)` | an explosion's damage, sound and particles; no blocks break; your user too unless `self_damage = False`; returns whom it hit. A player takes it scaled by difficulty, as a vanilla explosion (1.5 times on hard) | |
| `self.spawn(actor_or_entity_type, at)` | an Actor class by id, or `"minecraft:zombie"`; returns its uuid | yes (removed) |
| `self.apply(who, mark, seconds=None)` | put a mark on someone | only for a scenario |
| `self.grant(who, ability)` | grant an ability while you last | yes |
| `self.hold(setting, value, priority=0)` / `self.release(setting)` | hold a gamerule, `"time"` or `"weather"` | yes |
| `self.watch(area, center=, radius=)` or `box=(a, b)` | `Enter` / `Exit` events for that area | yes |
| `self.command(text)`, `self.particle(name, at)`, `self.sound(name, at)`, `self.message(text, to=None)` | output; a raw command is the escape hatch | |

Also `self.config`, `self.state`, `self.instance` (its id), a mark's `self.stack`, a scenario's
`self.args`.

Modifier ids are the engine's: `everhear:item/<id>`, `everhear:mark/<id>` (or `/1`, `/2` for a
stacking mark), `everhear:ability/<id>` (or `/<run>` for a cast's), `everhear:actor/<id>`.
Minecraft adds them up with every other modifier, so two things changing size at once both count,
and either can end without disturbing the other. Never write `/attribute` or `effect clear`
yourself.

## Events

`Use(item, slot)`, `Hit(target, target_type, item)`, `Hurt(amount, attacker, attacker_type)`,
`Kill(target, target_type)`, `Death`, `Respawn`, `Milk`, `Join`, `Leave`, `Held(item, slot)`,
`Unheld(item, slot)`, `Sneak`, `Unsneak`, `Enter(area, instance)`, `Exit(area, instance)`,
`Gone(type, actor)`, `Cast(ability, phrase, cast_id, by)`. Each has `ev.who`: the player's name (or
an actor's uuid) it is about.

Players are never among `ctx.world.entities_near` (they are `players_near`).

Damage you deal comes back from Minecraft as your user's own hit: the engine leaves those hits out,
so answering a `Hit` with damage cannot loop. A `Hurt` or `Kill` your damage caused arrives with
`ev.engine = True`. A use, hit, hurt or kill more than 5 s old (queued while the engine was down)
is not handed over.

`Held` and `Unheld` are a change between two moments the engine saw: none comes when a player
joins already holding something, or after the engine restarts. That the item's instance starts is
how you know it is held.

**Who hears what:**
- by default, events about your user (an item: its holder; for `Use`, `Held`, `Unheld`, only this
  item's);
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
`entity_type` exists in 26.3. A running instance whose state no longer fits the new `State` stops;
a listen or a timer naming a method that is gone is dropped.
