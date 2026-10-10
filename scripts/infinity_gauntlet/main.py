"""Infinity Gauntlet: right-click it and the first thing you point at is gone, in a beam of flame, with no cooldown."""
ASKS = ["give me the power of infinity", "an infinity gauntlet that kills whatever I point at",
        "it does not do enough damage", "no cooldown"]
from kit import values, items, aim, fx, loop, exclude, world
import itertools
import math
import mc

VALUES = {
    "player":        {"type": "player", "default": "@a"},
    "item":          {"type": "str", "default": "Infinity Gauntlet"},
    "cooldown":      {"type": "number", "min": 0, "max": 60, "default": 0, "live": True},
    "range":         {"type": "int", "min": 4, "max": 300, "default": 80, "live": True},
    "damage_amount": {"type": "number", "min": 0, "max": 100000, "default": 100, "live": True},
    "damage_type":   {"type": "enum", "of": ["player_attack", "magic", "lightning_bolt", "explosion", "freeze", "on_fire", "sonic_boom", "arrow", "wither", "generic"], "default": "lightning_bolt", "live": True},
    "damage_kill":   {"type": "bool", "default": True, "live": True},
    "damage_most":   {"type": "int", "min": 0, "max": 1000, "default": 1, "live": True},
    "damage_head":   {"type": "number", "min": 1, "max": 10, "default": 1, "live": True},
    "damage_spare":  {"type": "list", "of": "player", "default": [], "live": True},
}


# --- preset: damage_hits (copied from _presets/damage_hits.py) ---
def damage_hits(hits, shooter, v):
    """Hurt every hit once (hits: a list of aim.Hit, from aim.near_line, around or lock_on,
    or several of those added together), by damage_amount of damage_type, as the shooter, so
    a kill is theirs; damage_kill kills instead. damage_most: only the nearest that many (1:
    the first only; 0: all). shooter: a player's name, or their aim.look(), with which a hit
    whose head the look passes takes damage_head times the damage. Never the shooter, a
    name in damage_spare, a pet, a villager or anything else exclude spares. Returns how many."""
    by = getattr(shooter, "player", shooter) or ""
    spare = [by] + list(v["damage_spare"])
    names = set(mc.bare(s) for s in spare if s and not s.startswith("@"))
    hits = [h for h in sorted(hits, key=lambda h: h.t) if mc.bare(h.name) not in names]
    if v["damage_most"]:
        hits = hits[:v["damage_most"]]
    body = "%s_hit%d" % (mc.TAG, next(_DAMAGE_N))      # each call tags with its own name
    count = {}
    for h in hits:
        tag = body + "h" if v["damage_head"] > 1 and _damage_on_head(h, shooter) else body
        count[tag] = count.get(tag, 0) + _damage_mark(h, tag, spare)
    for tag, n in count.items():
        if not n:
            continue
        if v["damage_kill"]:
            _damage_all(tag, 1000000, "generic_kill", by)
        else:
            _damage_all(tag, v["damage_amount"] * (1 if tag == body else v["damage_head"]), v["damage_type"], by)
        mc.rcon("tag @e[tag=%s] remove %s" % (tag, tag))
    return sum(count.values())


_DAMAGE_N = itertools.count(1)


def _damage_mark(h, tag, spare):
    """Tag one hit where it was: a player by name, a mob as the nearest within 1.5 blocks that
    a hit may touch, so a hit listed twice is tagged once. 1 if it was tagged now, else 0."""
    if h.player:
        reply = mc.rcon("tag %s add %s" % (h.name, tag))
    else:
        x, y, z = h.pos[:3]
        reply = mc.rcon(world.prefix(world.dim(h.pos)) + "positioned %.3f %.3f %.3f %s run tag @s add %s" % (
            x, y, z, exclude.mobs("distance=..1.5,sort=nearest,limit=1", spare), tag))
    return 1 if reply.startswith("Added tag") else 0


def _damage_all(tag, amount, kind, by):
    """Damage everything tagged, once, as `by`; as nobody if `by` cannot be found (gone offline)."""
    cmd = "execute as @e[tag=%s] run damage @s %.1f minecraft:%s" % (tag, amount, kind)
    if by and "Applied" in mc.rcon(cmd + " by " + by):
        return
    mc.rcon(cmd)


def _damage_on_head(h, look):
    """True if a look (aim.look) passes within half a block of the head of a body standing at
    the hit: 1.7 above its feet, as tall as a player or a zombie."""
    if not hasattr(look, "origin"):
        return False
    o, d = look.origin, look.dir
    w = [h.pos[0] - o[0], h.pos[1] + 1.7 - o[1], h.pos[2] - o[2]]
    t = w[0] * d[0] + w[1] * d[1] + w[2] * d[2]
    return t > 0 and math.sqrt(sum((w[i] - t * d[i]) ** 2 for i in range(3))) <= 0.5


v = values(VALUES)
for p in loop(items.clicks(v["player"], v["item"], cooldown=v["cooldown"], color="gold")):
    r = aim.ray(p, v["range"])
    if r is None:
        continue
    hits = aim.near_line(r, 1.5, shooter=p)
    fx.sound("entity.blaze.shoot", p, volume=3, pitch=0.6)
    end = hits[0].pos.moved(0, 1, 0) if hits else r.end
    fx.line(r.start.moved(0, -0.2, 0), end, "flame", every=3, spread=0.5, count=20, speed=0.02)
    if damage_hits(hits, p, v):
        fx.particle("explosion_emitter", end)
        fx.particle("flame", end, spread=1.5, count=150, speed=0.1)
        fx.sound("entity.lightning_bolt.thunder", end, volume=2)
