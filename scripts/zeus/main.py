"""Zeus: every trident a player throws calls lightning down where it lands or hits, in any weather."""
ASKS = ["a trident that calls lightning where it lands", "call a lightning strike on impact", "Zeus's trident"]
from kit import values, items, loop, world
import re
import time
import mc
import math
import random

VALUES = {
    "player":              {"type": "player", "default": "@a"},
    "projectile_kind":     {"type": "enum", "of": ["both", "arrow", "trident"], "default": "trident", "live": True},
    "projectile_named":    {"type": "str", "default": "", "live": True},
    "projectile_cooldown": {"type": "number", "min": 0, "max": 60, "default": 0, "live": True},
    "projectile_remove":   {"type": "bool", "default": False, "live": True},
    "lightning_count":     {"type": "int", "min": 1, "max": 50, "default": 1, "live": True},
    "lightning_spread":    {"type": "number", "min": 0, "max": 32, "default": 3, "live": True},
    "lightning_layout":    {"type": "enum", "of": ["scatter", "ring"], "default": "scatter", "live": True},
    "lightning_every":     {"type": "number", "min": 0, "max": 5, "default": 0, "live": True},
    "lightning_spare":     {"type": "list", "of": "player", "default": [], "live": True},
}


# --- preset: projectile_lands (copied from _presets/projectile_lands.py) ---
def projectile_lands(player, v):
    """Yield (at, p) each time an arrow or trident that a player (of those `player` names)
    shot or threw lands in a block or hits something: at is where (a Pos), p who threw it.

        for at, p in loop(projectile_lands(v["player"], v)):
            explosion(at, v)

    projectile_named: only those from the weapon of that name, given with its custom_data
    ("Spear Bomb" is trident[custom_data={vfg_item:"spear_bomb"}], items.item_id's id).
    projectile_cooldown: a player's next ones within that many seconds land as they are.
    projectile_remove: take it away where it lands, so it is not picked up and used again.
    An arrow that hits a mob is gone at once, so for it at is a little ahead of where it
    was last seen. Each player is followed on their own."""
    seen = mc.TAG + "_seen"             # on every projectile this has looked at; taken off on a stop
    mc.on_stop(lambda: mc.rcon("tag @e[tag=%s] remove %s" % (seen, seen)))
    flying, last = {}, {}               # uuid -> what is known of it in flight; thrower -> when it last acted
    while not mc.stopping():
        people = loop.who(player)
        if not people:
            flying.clear()
            time.sleep(1.0)
            continue
        kinds = ("arrow", "trident") if v["projectile_kind"] == "both" else (v["projectile_kind"],)
        named = items.item_id(v["projectile_named"]) if v["projectile_named"] else ""
        for kind in kinds:
            for uuid in _projectile_lands_new(kind, named, seen):
                p = _projectile_lands_thrower(uuid)
                if p in people:
                    flying[uuid] = {"p": p, "kind": kind, "dim": world.dim_of(p), "at": None, "step": (0, 0, 0)}
        for uuid, f in list(flying.items()):
            at = _projectile_lands_pos(uuid, f["dim"])
            if at is False:                                     # no answer this time: ask again next pass
                continue
            if at is None:                                      # gone: an arrow that hit a mob, or out of the world
                del flying[uuid]
                if f["at"] is None:
                    continue
                at = f["at"].moved(*[s / 2.0 for s in f["step"]])
                lo, hi = world.limits(at.dim)
                if not lo <= at[1] <= hi or not world.loaded(at[0], at[2], at.dim):
                    continue                                    # it left the loaded world: nothing to act on
            elif not _projectile_lands_down(uuid, f["kind"]):   # still flying
                if f["at"] is not None:
                    f["step"] = tuple(min(8.0, max(-8.0, at[j] - f["at"][j])) for j in range(3))
                f["at"] = at
                continue
            else:                                               # down
                del flying[uuid]
            if time.time() - last.get(f["p"], -1e9) < v["projectile_cooldown"]:
                continue                                        # too soon after their last: it lands as it is
            if v["projectile_remove"]:
                mc.rcon("kill %s" % uuid)
            yield at, f["p"]
            last[f["p"]] = time.time()
        time.sleep(0.05 if flying else 0.1)


_PROJECTILE_LANDS_TYPES = {"arrow": "#minecraft:arrows", "trident": "trident"}
# A projectile keeps the weapon it came from: an arrow its bow ("weapon"), a trident itself ("item").
_PROJECTILE_LANDS_FROM = {"arrow": "weapon", "trident": "item"}
_PROJECTILE_LANDS_UUID = re.compile(r"\[I; (-?\d+), (-?\d+), (-?\d+), (-?\d+)\]")
_PROJECTILE_LANDS_POS = re.compile(r"\[(-?[\d.]+(?:E-?\d+)?)d, (-?[\d.]+(?:E-?\d+)?)d, (-?[\d.]+(?:E-?\d+)?)d\]")


def _projectile_lands_new(kind, named, seen):
    """The uuids of projectiles of a kind near anyone online that were not looked at yet,
    each marked as looked at."""
    match = ',nbt={%s:{components:{"minecraft:custom_data":{vfg_item:"%s"}}}}' % (
        _PROJECTILE_LANDS_FROM[kind], named) if named else ""
    reply = mc.rcon("execute as @a at @s as @e[type=%s,distance=..32,tag=!%s%s] run data get entity @s UUID"
                    % (_PROJECTILE_LANDS_TYPES[kind], seen, match))
    out = []
    for m in _PROJECTILE_LANDS_UUID.finditer(reply):
        h = "".join("%08x" % (int(n) & 0xffffffff) for n in m.groups())
        uuid = "%s-%s-%s-%s-%s" % (h[:8], h[8:12], h[12:16], h[16:20], h[20:])
        if uuid not in out:                                     # near two players, it is listed twice
            out.append(uuid)
            mc.rcon("tag %s add %s" % (uuid, seen))
    return out


def _projectile_lands_thrower(uuid):
    """Who shot or threw it, as a bare name ("" for nobody, or a mob's name)."""
    reply = mc.rcon("execute as %s on origin run data get entity @s UUID" % uuid)
    return mc.bare(reply.split(" has the following entity data", 1)[0]) if " has the following" in reply else ""


def _projectile_lands_pos(uuid, dim):
    """Where it is (a Pos); None if it is gone; False if the server did not answer."""
    reply = mc.rcon("data get entity %s Pos" % uuid)
    m = _PROJECTILE_LANDS_POS.search(reply)
    if m:
        return world.Pos(float(m.group(1)), float(m.group(2)), float(m.group(3)), dim)
    return None if "no entity" in reply.lower() else False


def _projectile_lands_down(uuid, kind):
    """True once it is in a block, or (a trident) has hit something and bounced off."""
    if "1b" in mc.rcon("data get entity %s inGround" % uuid).rsplit(":", 1)[-1]:
        return True
    return kind == "trident" and "1b" in mc.rcon("data get entity %s DealtDamage" % uuid).rsplit(":", 1)[-1]


# --- preset: lightning (copied from _presets/lightning.py) ---
def lightning(at, by, v):
    """Strike lightning_count bolts: the first at the place (or a player's name: where they
    stand), the rest within lightning_spread blocks of it, scattered at random or evenly in
    a ring ("ring" with 5 is a plus), lightning_every seconds apart (0: all at once). A bolt
    hurts and sets fire to what is within about 3 blocks, so none lands within 4 blocks of
    by (the player who called it, or "") or of a player in lightning_spare. Returns how many
    struck."""
    if isinstance(at, str):
        feet = mc.pos(at)
        if feet is None:
            return 0
        at = world.Pos(feet[0], feet[1], feet[2], world.dim_of(at))
    n, s = v["lightning_count"], v["lightning_spread"]
    points = [tuple(at[:3])]
    for i in range(1, n):
        if v["lightning_layout"] == "ring":
            a, d = 2 * math.pi * (i - 1) / (n - 1), s
        else:
            a, d = random.uniform(0, 2 * math.pi), random.uniform(min(1.0, s), s)
        points.append((at[0] + d * math.cos(a), at[1], at[2] + d * math.sin(a)))
    keep = "".join("unless entity @a[name=%s,distance=..4] " % mc.bare(p) for p in [by] + list(v["lightning_spare"])
                   if p and not p.startswith("@"))
    struck = 0
    for i, (x, y, z) in enumerate(points):
        if i and v["lightning_every"] and not loop.wait(v["lightning_every"]):
            break
        reply = mc.rcon(world.prefix(world.dim(at)) + "positioned %.2f %.2f %.2f %srun summon lightning_bolt ~ ~ ~ "
                        "{Tags:[\"vfg\",\"%s\"]}" % (x, y, z, keep, mc.TAG))
        struck += 1 if "Summoned" in reply else 0
    return struck


v = values(VALUES)
mc.log("tridents of", v["player"], "call lightning")
for at, p in loop(projectile_lands(v["player"], v)):
    struck = lightning(at, p, v)
    mc.log("lightning x%d where %s's trident came down: %.0f %.0f %.0f" % (struck, p, at[0], at[1], at[2]))
