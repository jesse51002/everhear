"""Mobs: a number of them at safe spots round a player, and a boss with its bar.

    mobs.around(p, "zombie", 5, spread=8)                  # 5 zombies near p, tagged TAG
    boss = mobs.Boss("Horde King", "zombie", health=800, scale=3)
    boss.spawn(p)                                          # near p, with a red bar for everyone
    while boss.update():                                   # the bar follows the bodies' summed health
        time.sleep(1)
    # it is dead (or gone); its bar is removed. boss.remove() takes it away early.

Everything summoned is tagged "vfg" and TAG and persistent, so a stop kills it.
"""
import json
import re
import threading

import mc
from . import world

_lock = threading.Lock()


def around(player, mob, n=1, nbt="", spread=8, y=1, tags=()):
    """Summon n mobs at safe spots round a player (room to stand on solid ground, never on
    top of them; y > 3 in the air that high), tagged TAG and `tags`, with extra NBT.
    Returns how many were summoned."""
    got = 0
    for _ in range(max(0, int(n))):
        if mc.stopping():
            break
        if mc.spawn(player, mob, nbt, spread=spread, y=y, tags=tags):
            got += 1
    return got


def count(tag=None):
    """How many entities carry a tag (default TAG: everything this script made)."""
    return mc.count("@e[tag=%s]" % (tag or mc.TAG))


_bars = []
_n = [0]


def _remove_all():
    for b in list(_bars):
        b.remove()


class Boss(object):
    """A boss of one body or several, with a bossbar showing their summed health.
    name: what the bar and the bodies are called; mob: its type; health: each body's;
    bodies: how many; scale: its size (1 is normal); nbt: more NBT for each body
    ('equipment:{mainhand:{id:"netherite_sword",count:1}}', never Tags: the kit sets them);
    color: the bar's."""

    def __init__(self, name, mob, health=200, bodies=1, scale=1.0, nbt="", color="red"):
        with _lock:
            _n[0] += 1
            k = _n[0]
            if not _bars:
                mc.on_stop(_remove_all)
            _bars.append(self)
        self.name, self.mob, self.health_each, self.bodies = name, mob, float(health), max(1, int(bodies))
        self.scale, self.nbt, self.color = float(scale), nbt, color
        self.tag = "%s_boss%d" % (mc.TAG, k)
        self.bar = "vfg:%s_boss%d" % (mc.INSTANCE, k)
        self.alive = 0
        self._misses = 0
        self._bar_made = False

    def _body_nbt(self, i):
        name = self.name if self.bodies == 1 else "%s (%d)" % (self.name, i + 1)
        attrs = '{id:"max_health",base:%g}' % self.health_each
        if self.scale != 1.0:
            attrs += ',{id:"scale",base:%g}' % self.scale
        parts = ["CustomName:%s" % json.dumps(name), "CustomNameVisible:1b", "attributes:[%s]" % attrs,
                 "Health:%gf" % self.health_each]
        if self.nbt:
            parts.append(self.nbt)
        return ",".join(parts)

    def spawn(self, near=None, at=None, spread=8):
        """Summon the bodies near a player (at safe spots) or at a place, and show the bar to
        everyone. Returns how many bodies were summoned."""
        got = 0
        for i in range(self.bodies):
            tags = (self.tag, "%s_%d" % (self.tag, i))
            if near:
                ok = mc.spawn(near, self.mob, self._body_nbt(i), spread=spread, tags=tags)
            else:
                x, y, z = at[:3]
                data = "{Tags:[%s],PersistenceRequired:1b,%s}" % (
                    ",".join('"%s"' % t for t in ("vfg", mc.TAG) + tags), self._body_nbt(i))
                ok = "summoned" in mc.rcon(world.run(world.dim(at), "summon %s %.2f %.2f %.2f %s" % (
                    self.mob, x + (i - (self.bodies - 1) / 2.0) * 3 * self.scale, y, z, data))).lower()
            got += 1 if ok else 0
        top = self.health_each * self.bodies
        mc.rcon("bossbar add %s %s" % (self.bar, json.dumps({"text": self.name, "color": self.color})))
        mc.rcon("bossbar set %s color %s" % (self.bar, self.color if self.color in (
            "blue", "green", "pink", "purple", "red", "white", "yellow") else "red"))
        mc.rcon("bossbar set %s max %d" % (self.bar, max(1, int(top))))
        mc.rcon("bossbar set %s value %d" % (self.bar, int(top)))
        mc.rcon("bossbar set %s players @a" % self.bar)
        self._bar_made = True
        self.alive = got
        return got

    def health(self):
        """(summed health, bodies alive) in one command; None when the server did not answer."""
        reply = mc.rcon("execute as @e[tag=%s] run data get entity @s Health" % self.tag)
        hs = [float(v) for v in re.findall(r"data: (-?[\d.]+)f", reply)]
        if hs:
            alive = [h for h in hs if h > 0]
            return sum(alive), len(alive)
        if "no entity" in reply.lower() or mc.count("@e[tag=%s]" % self.tag) == 0:
            return 0.0, 0
        return None

    def update(self, players="@a"):
        """Move the bar to the bodies' summed health and show it to `players`. Returns how
        many bodies are alive; at 0 (seen twice, so a dropped reply is not a death) the bar
        is removed."""
        h = self.health()
        if h is None:
            return self.alive
        total, alive = h
        if alive == 0:
            self._misses += 1
            if self._misses >= 2:
                self.alive = 0
                self._remove_bar()
                return 0
            return self.alive or 1
        self._misses = 0
        self.alive = alive
        mc.rcon("bossbar set %s value %d" % (self.bar, int(round(total))))
        mc.rcon("bossbar set %s players %s" % (self.bar, players))
        return alive

    def dead(self):
        """True once every body is dead or gone."""
        return self.update() == 0

    def _remove_bar(self):
        if self._bar_made:
            self._bar_made = False
            mc.rcon("bossbar remove %s" % self.bar)

    def remove(self):
        """Take the bodies and the bar away."""
        mc.rcon("kill @e[tag=%s]" % self.tag)
        self._remove_bar()
