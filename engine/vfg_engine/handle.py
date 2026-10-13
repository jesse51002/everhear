"""What `self.*` does in content: the engine side of each method on vfg_engine.content.Thing.

A Handle is made per instance and never handed to anything else. During a call it knows which
run (an ability's cast) the call belongs to, so what the call makes is that run's, and goes when
the run ends. Every method runs as the engine's own code inside the call (Guard.engine_code), so
a cut waits until it is done. Nothing here talks to Minecraft: writes are queued for the tick's
one batch, and work that runs content (starting a mark, a spawned actor, a cast) is done right
after the calling code returns (Runtime.later).
"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING

from . import registries
from .events import kind_of
from .io import (Bossbar, Command, EntityData, GiveEffect, Ignite, Message, Motion, Particle, Sound,
                 Spawnpoint, StopSound, Teleport)
from .modifiers import OPERATIONS, attribute_name
from .records import Sub, Timer

if TYPE_CHECKING:
    from .records import Record
    from .runtime import Runtime

MIN_PERIOD_S = 0.05            # a timer faster than a tick still runs once a tick
KEEP_POTION_S = 3.0            # the beacon-style potion: this long (the default) ...
KEEP_POTION_EVERY_S = 2.0      # ... given again this often (every 1 s for one of 10 s or more)
ASKS_PER_TICK = 8              # self.ask: slow commands one instance may queue a tick


def _effect_name(effect: str) -> str:
    effect = str(effect).strip().lower()
    return effect if ":" in effect else "minecraft:" + effect


class Handle:
    def __init__(self, rt: "Runtime", record: "Record"):
        self.rt = rt
        self.record = record
        self.run = None

    # ---- what the instance reads ---------------------------------------------------------

    @property
    def iid(self):
        return self.record.iid

    @property
    def info(self):
        return self.rt.classes[self.record.cid]

    @property
    def config(self):
        return self.info.config

    @property
    def state(self):
        return self.run.state if self.run is not None else self.record.state

    @property
    def args(self):
        """The instance's Args model (a Scenario's or a Mark's), built from its saved args."""
        return self.rt.args_of(self.record)

    @property
    def blocks(self):
        made = self.__dict__.get("_blocks")
        if made is None:
            from .blocks import Blocks
            made = self._blocks = Blocks(self.rt, self)
        return made

    def _owned(self):
        return self.run.owned if self.run is not None else self.record.owned

    def _holder(self) -> str:
        """The id world-setting holds are made under: the instance, or one run of it."""
        return self.record.iid if self.run is None else "%s/run%d" % (self.record.iid, self.run.n)

    def _method(self, method: str):
        if not isinstance(method, str) or not callable(getattr(self.info.cls, method, None)):
            raise AttributeError("%s has no method %r to call" % (self.info.name, method))

    # ---- subscriptions and timers --------------------------------------------------------

    def listen(self, event, method, about, blocks=None):
        with self.rt.guard.engine_code():
            kind = kind_of(event)
            self._method(method)
            if about not in ("user", "anyone"):
                raise ValueError("about must be \"user\" or \"anyone\", not %r" % (about,))
            if blocks is not None:
                if kind != "mine":
                    raise ValueError("blocks= says which blocks a Mine listen watches; it is not for %s" % kind)
                if isinstance(blocks, str):
                    blocks = [blocks]
                from .args import _id
                blocks = sorted({_id("block", registries.BLOCKS)(str(b)) for b in blocks})
                if not blocks:
                    raise ValueError("blocks= names at least one block")
            elif kind == "mine":
                raise ValueError('a Mine listen says which blocks it watches: '
                                 'self.listen(Mine, "on_mine", blocks=["minecraft:diamond_ore"])')
            run = self.run.n if self.run is not None else None
            subs = self.record.subs
            for s in subs:
                if s.kind == kind and s.method == method and s.run == run:
                    s.about, s.blocks = about, blocks
                    return
            subs.append(Sub(kind, method, about, run, blocks))

    def unlisten(self, event, method):
        with self.rt.guard.engine_code():
            kind = kind_of(event)
            self.record.subs = [s for s in self.record.subs
                                if not (s.kind == kind and (method is None or s.method == method))]

    def timer(self, seconds, method, args, repeat):
        with self.rt.guard.engine_code():
            self._method(method)
            seconds = float(seconds)
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError("a timer needs a number of seconds, 0 or more, not %r" % (seconds,))
            try:
                args = json.loads(json.dumps(list(args)))
            except (TypeError, ValueError):
                raise ValueError("a timer's arguments are saved, so they must be plain data "
                                 "(numbers, text, lists, dicts), not %r" % (args,)) from None
            rec = self.record
            tid = "t%d" % rec.next_timer
            rec.next_timer += 1
            period = max(seconds, MIN_PERIOD_S) if repeat else None
            rec.timers.append(Timer(tid, self.rt.now + (period if repeat else seconds), method, args,
                                    period, self.run.n if self.run is not None else None))
            return tid

    def cancel(self, timer_id):
        with self.rt.guard.engine_code():
            self.record.timers = [t for t in self.record.timers if t.id != timer_id]

    def end(self):
        with self.rt.guard.engine_code():
            rec, run = self.record, self.run
            if run is not None:
                self.rt.later(lambda: self.rt.end_run(rec, run.n, "ended"))
            else:
                self.rt.later(lambda: self.rt.end_record(rec, "ended"))

    # ---- modifiers and potions -----------------------------------------------------------

    def modifier(self, attribute, amount, operation, on=None):
        with self.rt.guard.engine_code():
            attr = attribute_name(attribute)
            if attr not in registries.ATTRIBUTES:
                raise ValueError("%r is not an attribute in Minecraft 26.3" % attribute)
            key = attr
            if on is not None:
                target = self.rt.target_of(on)
                if target is None:
                    raise ValueError("modifier(..., on=...) needs an entity's uuid or a player, not %r" % (on,))
                if target != self.record.user:
                    key = "%s|%s" % (target, attr)
            elif self.record.user is None:
                raise ValueError("a scenario has no user to put a modifier on: give on=<uuid>, or apply a mark")
            mods = self._owned().modifiers
            if amount is None:
                mods.pop(key, None)
                return
            if operation not in OPERATIONS:
                raise ValueError("operation must be one of %s, not %r" % (", ".join(OPERATIONS), operation))
            amount = float(amount)
            if not math.isfinite(amount):
                raise ValueError("a modifier's amount must be a finite number, not %r" % amount)
            mods[key] = [amount, operation]

    def potion(self, effect, seconds, amplifier, who, particles):
        with self.rt.guard.engine_code():
            name = _effect_name(effect)
            if name not in registries.MOB_EFFECTS:
                raise ValueError("%r is not an effect in Minecraft 26.3" % effect)
            target = self.rt.target_of(who) if who is not None else self.record.user
            if target is None:
                raise ValueError("potion(..., who=...) needs a target: a scenario has no user")
            self.rt.queue(GiveEffect(target, name, float(seconds), int(amplifier), bool(particles)),
                          self.rt.potion_reply)

    def keep_potion(self, effect, amplifier, particles, seconds=KEEP_POTION_S):
        with self.rt.guard.engine_code():
            name = _effect_name(effect)
            if name not in registries.MOB_EFFECTS:
                raise ValueError("%r is not an effect in Minecraft 26.3" % effect)
            if self.record.user is None:
                raise ValueError("a scenario has no user to keep a potion on")
            seconds = float(seconds)
            if not 1 <= seconds <= 60:
                raise ValueError("keep_potion's seconds are 1 to 60, not %r" % seconds)
            self._owned().potions[name] = [int(amplifier), bool(particles), self.rt.now, seconds]

    # ---- damage --------------------------------------------------------------------------

    def damage(self, target, amount, damage_type):
        with self.rt.guard.engine_code():
            key = self.rt.target_of(target)
            if key is None:
                raise ValueError("damage needs a target: a player, an entity or a uuid, not %r" % (target,))
            amount = float(amount)
            if not math.isfinite(amount) or amount < 0:
                raise ValueError("damage must be a number, 0 or more, not %r" % amount)
            self.rt.deal(self.record, self.run, key, amount, damage_type)

    def explode(self, at, radius, damage):
        with self.rt.guard.engine_code():
            return self.rt.explode(self.record, self.run, at, float(radius), float(damage))

    # ---- what it makes -------------------------------------------------------------------

    def spawn(self, what, at, dim, nbt=None, keep=False):
        with self.rt.guard.engine_code():
            uuid = self.rt.spawn(what, at, dim, owner=self.record.iid, source=self.record.user,
                                 nbt=nbt, keep=keep)
            (self._owned().kept if keep else self._owned().spawned).append(uuid)
            return uuid

    def spawned(self):
        owned = self._owned()
        live = set(self.rt.actors) | set(self.rt.raw_spawned)
        return [u for u in owned.spawned + owned.kept if u in live]

    def remove(self, uuid):
        with self.rt.guard.engine_code():
            from . import actors
            for owned in [self.record.owned] + [r.owned for r in self.record.runs.values()]:
                for kept in (owned.spawned, owned.kept):
                    if uuid in kept:
                        kept.remove(uuid)
                        actors.unspawn(self.rt, uuid, "removed by %s" % self.record.iid)
                        return
            raise ValueError("%r is not something %s spawned" % (uuid, self.record.iid))

    def item_stack(self, item, count):
        with self.rt.guard.engine_code():
            from .items import stack_snbt
            return stack_snbt(self.rt, item, int(count))

    def apply(self, who, mark, seconds, args=None):
        with self.rt.guard.engine_code():
            key = self.rt.target_of(who)
            if key is None:
                raise ValueError("apply needs someone to put %r on, not %r" % (mark, who))
            owner = self.record.iid if self.record.kind == "scenario" else None
            iid = self.rt.apply_mark(key, mark, seconds, source=self.record.user or self.record.iid,
                                     owner=owner, args=args)
            if owner and iid not in self._owned().marks:
                self._owned().marks.append(iid)
            return iid

    def start_scenario(self, scenario, args):
        with self.rt.guard.engine_code():
            from . import scenarios
            return scenarios.start(self.rt, scenario, args, source=self.record.user or self.record.iid,
                                   owner=self.record.iid)

    def result(self, summary, facts):
        with self.rt.guard.engine_code():
            try:
                facts = json.loads(json.dumps(dict(facts)))
            except (TypeError, ValueError):
                raise ValueError("a result's facts are plain data (numbers, text, lists, dicts)") from None
            self.record.notes["result"] = {"summary": str(summary), "facts": facts}

    def save_place(self, name, at, by):
        with self.rt.guard.engine_code():
            dim, pos, yaw = self.rt.where(at)
            return self.rt.places.save(name, dim, pos, yaw, by or self.record.user, self.rt.now)

    def forget_place(self, name):
        with self.rt.guard.engine_code():
            return self.rt.places.forget(name)

    def teleport(self, who, at, dim, yaw, pitch):
        with self.rt.guard.engine_code():
            from .args import Spot
            target = self._target(who, "teleport")
            where_dim, pos, where_yaw = self.rt.where(at, dim)
            if yaw is None and isinstance(at, (Spot, dict)):     # a saved place faces its own way
                yaw = where_yaw
            self.rt.queue(Teleport(target, where_dim, pos, None if yaw is None else float(yaw),
                                   None if pitch is None else float(pitch)))

    def motion(self, who, vec):
        with self.rt.guard.engine_code():
            self.rt.queue(Motion(self._target(who, "motion"), _pos(vec)))

    def entity_data(self, who, snbt):
        with self.rt.guard.engine_code():
            snbt = str(snbt).strip()
            if not (snbt.startswith("{") and snbt.endswith("}")):
                raise ValueError("entity_data takes SNBT, {...}, not %r" % snbt[:40])
            self.rt.queue(EntityData(self._target(who, "entity_data"), snbt))

    def ignite(self, who, seconds):
        with self.rt.guard.engine_code():
            seconds = float(seconds)
            if not 0 < seconds <= 3600:
                raise ValueError("ignite's seconds are above 0 and at most 3600, not %r" % seconds)
            self.rt.queue(Ignite(self._target(who, "ignite"), seconds))

    def ask(self, command, method):
        with self.rt.guard.engine_code():
            self._method(method)
            mine = sum(1 for a in self.rt.asks if a[0] == self.record.iid)
            if mine >= ASKS_PER_TICK:
                raise ValueError("at most %d slow commands a tick" % ASKS_PER_TICK)
            self.rt.asks.append((self.record.iid, str(command), method))

    def bossbar(self, bar, name, color, value, max_, players):
        with self.rt.guard.engine_code():
            bar_id = self.rt.bossbar_id(self.record, bar)
            if players is not None:
                players = tuple(self.rt.player_names(players))
            self.rt.queue(Bossbar(bar_id, None if name is None else str(name), color,
                                  None if value is None else int(value), None if max_ is None else int(max_),
                                  players))
            if bar_id not in self.record.owned.bossbars:
                self.record.owned.bossbars.append(bar_id)

    def stop_sound(self, name, to):
        with self.rt.guard.engine_code():
            self.rt.queue(StopSound(tuple(self.rt.player_names(to)) if to is not None else (),
                                    None if name is None else str(name)))

    def spawnpoint(self, player, at):
        with self.rt.guard.engine_code():
            name = self.rt.target_of(player)
            if name is None or self.rt.player(name) is None:
                raise ValueError("spawnpoint is for a player, not %r" % (player,))
            dim, pos, _ = self.rt.where(at)
            self.rt.queue(Spawnpoint(name, dim, pos))

    def _target(self, who, what):
        target = self.rt.target_of(who)
        if target is None:
            raise ValueError("%s needs a player or an entity, not %r" % (what, who))
        return target

    def grant(self, who, ability):
        with self.rt.guard.engine_code():
            key = self.rt.target_of(who)
            if key is None:
                raise ValueError("grant needs a player or an actor, not %r" % (who,))
            if self.rt.grant(key, ability, owner=self.record.iid) is not None:
                self._owned().grants.append([key, ability])

    def hold(self, setting, value, priority):
        with self.rt.guard.engine_code():
            setting = str(setting).strip()
            if setting not in ("time", "weather") and setting not in registries.GAME_RULES:
                raise ValueError("%r is not a gamerule in Minecraft 26.3 (or \"time\", \"weather\")" % setting)
            self.rt.world_settings.hold(self._holder(), setting, value, int(priority), self.rt.now)
            if setting not in self._owned().holds:
                self._owned().holds.append(setting)

    def release(self, setting):
        with self.rt.guard.engine_code():
            self.rt.world_settings.release(self._holder(), setting)
            owned = self._owned()
            owned.holds = [h for h in owned.holds if h != setting]

    def watch(self, area, center, radius, box, dim):
        with self.rt.guard.engine_code():
            if (center is None) == (box is None):
                raise ValueError("watch takes center and radius (a sphere) or box=(corner, corner)")
            doc = {"dim": dim, "inside": []}
            if center is not None:
                if radius is None or float(radius) <= 0:
                    raise ValueError("a watched sphere needs a radius above 0")
                doc.update(center=[float(c) for c in _pos(center)], radius=float(radius))
            else:
                a, b = (_pos(c) for c in box)
                doc["box"] = [[min(a[i], b[i]) for i in range(3)], [max(a[i], b[i]) for i in range(3)]]
            self._owned().areas[str(area)] = doc

    # ---- output --------------------------------------------------------------------------

    def _dim(self):
        user = self.rt.being(self.record.user) if self.record.user else None
        try:
            return user.dim if user is not None else "minecraft:overworld"
        except LookupError:
            return "minecraft:overworld"

    def command(self, text):
        with self.rt.guard.engine_code():
            self.rt.queue(Command(str(text)))

    def particle(self, name, at, count, delta, speed, force=False, to=None, dim=None):
        with self.rt.guard.engine_code():
            where_dim, pos, _ = self.rt.where(at, dim or self._dim())
            self.rt.queue(Particle(str(name), where_dim, pos, tuple(float(d) for d in delta),
                                   float(speed), int(count), bool(force),
                                   tuple(self.rt.player_names(to)) if to is not None else ()))

    def sound(self, name, at, volume, pitch, to=None, dim=None):
        with self.rt.guard.engine_code():
            if at is None:
                user = self.rt.being(self.record.user) if self.record.user else None
                if user is None:
                    raise ValueError("sound(..., at=...) needs a place: a scenario has no user")
                at = user
            where_dim, pos, _ = self.rt.where(at, dim or self._dim())
            self.rt.queue(Sound(str(name), where_dim, pos, float(volume), float(pitch),
                                tuple(self.rt.player_names(to)) if to is not None else ()))

    def message(self, text, to, where):
        with self.rt.guard.engine_code():
            if to is None:
                user = self.rt.being(self.record.user) if self.record.user else None
                players = (user.key,) if user is not None and user.is_player else ()
            elif isinstance(to, str):
                players = (to,)
            else:
                players = tuple(self.rt.target_of(p) for p in to)
            self.rt.queue(Message(players, str(text), where))


def _pos(p):
    if hasattr(p, "pos") and not isinstance(p, (tuple, list)):
        p = p.pos
    if callable(p):
        p = p()
    x, y, z = p
    return (float(x), float(y), float(z))
