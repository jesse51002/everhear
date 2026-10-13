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
from .io import Command, GiveEffect, Message, Particle, Sound
from .modifiers import OPERATIONS, attribute_name
from .records import Sub, Timer

if TYPE_CHECKING:
    from .records import Record
    from .runtime import Runtime

MIN_PERIOD_S = 0.05            # a timer faster than a tick still runs once a tick
KEEP_POTION_S = 3.0            # the beacon-style potion: this long ...
KEEP_POTION_EVERY_S = 2.0      # ... given again this often


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

    def _owned(self):
        return self.run.owned if self.run is not None else self.record.owned

    def _holder(self) -> str:
        """The id world-setting holds are made under: the instance, or one run of it."""
        return self.record.iid if self.run is None else "%s/run%d" % (self.record.iid, self.run.n)

    def _method(self, method: str):
        if not isinstance(method, str) or not callable(getattr(self.info.cls, method, None)):
            raise AttributeError("%s has no method %r to call" % (self.info.name, method))

    # ---- subscriptions and timers --------------------------------------------------------

    def listen(self, event, method, about):
        with self.rt.guard.engine_code():
            kind = kind_of(event)
            self._method(method)
            if about not in ("user", "anyone"):
                raise ValueError("about must be \"user\" or \"anyone\", not %r" % (about,))
            run = self.run.n if self.run is not None else None
            subs = self.record.subs
            if not any(s.kind == kind and s.method == method and s.run == run for s in subs):
                subs.append(Sub(kind, method, about, run))

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

    def modifier(self, attribute, amount, operation):
        with self.rt.guard.engine_code():
            if self.record.user is None:
                raise ValueError("a scenario has no user to put a modifier on: apply a mark instead")
            attr = attribute_name(attribute)
            if attr not in registries.ATTRIBUTES:
                raise ValueError("%r is not an attribute in Minecraft 26.3" % attribute)
            mods = self._owned().modifiers
            if amount is None:
                mods.pop(attr, None)
                return
            if operation not in OPERATIONS:
                raise ValueError("operation must be one of %s, not %r" % (", ".join(OPERATIONS), operation))
            amount = float(amount)
            if not math.isfinite(amount):
                raise ValueError("a modifier's amount must be a finite number, not %r" % amount)
            mods[attr] = [amount, operation]

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

    def keep_potion(self, effect, amplifier, particles):
        with self.rt.guard.engine_code():
            name = _effect_name(effect)
            if name not in registries.MOB_EFFECTS:
                raise ValueError("%r is not an effect in Minecraft 26.3" % effect)
            if self.record.user is None:
                raise ValueError("a scenario has no user to keep a potion on")
            self._owned().potions[name] = [int(amplifier), bool(particles), self.rt.now]

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

    def spawn(self, what, at, dim):
        with self.rt.guard.engine_code():
            uuid = self.rt.spawn(what, at, dim, owner=self.record.iid, source=self.record.user)
            self._owned().spawned.append(uuid)
            return uuid

    def apply(self, who, mark, seconds):
        with self.rt.guard.engine_code():
            key = self.rt.target_of(who)
            if key is None:
                raise ValueError("apply needs someone to put %r on, not %r" % (mark, who))
            owner = self.record.iid if self.record.kind == "scenario" else None
            iid = self.rt.apply_mark(key, mark, seconds, source=self.record.user or self.record.iid,
                                     owner=owner)
            if owner and iid not in self._owned().marks:
                self._owned().marks.append(iid)
            return iid

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

    def particle(self, name, at, count, delta, speed):
        with self.rt.guard.engine_code():
            self.rt.queue(Particle(str(name), self._dim(), _pos(at), tuple(float(d) for d in delta),
                                   float(speed), int(count)))

    def sound(self, name, at, volume, pitch):
        with self.rt.guard.engine_code():
            if at is None:
                user = self.rt.being(self.record.user) if self.record.user else None
                if user is None:
                    raise ValueError("sound(..., at=...) needs a place: a scenario has no user")
                at = user.pos
            self.rt.queue(Sound(str(name), self._dim(), _pos(at), float(volume), float(pitch)))

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
