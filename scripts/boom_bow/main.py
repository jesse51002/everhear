"""Boom Bow: a player's arrows explode where they land or hit, from one blast to a ring of them with a mushroom cloud."""
ASKS = ["arrows that explode where they hit", "an explosive bow", "make it explode on the target it hits"]
from kit import values, items, loop, world, exclude
import re
import time
import mc
import math

VALUES = {
    "player":              {"type": "player", "default": "@a"},
    "projectile_kind":     {"type": "enum", "of": ["both", "arrow", "trident"], "default": "arrow", "live": True},
    "projectile_named":    {"type": "str", "default": "", "live": True},
    "projectile_cooldown": {"type": "number", "min": 0, "max": 60, "default": 0, "live": True},
    "projectile_remove":   {"type": "bool", "default": True, "live": True},
    "explosion_power":     {"type": "number", "min": 1, "max": 16, "default": 8, "live": True},
    "explosion_layout":    {"type": "enum", "of": ["one", "plus", "ring", "rings"], "default": "one", "live": True},
    "explosion_spread":    {"type": "number", "min": 1, "max": 32, "default": 8, "live": True},
    "explosion_breaks":    {"type": "bool", "default": True, "live": True},
    "explosion_keep":      {"type": "number", "min": 0, "max": 32, "default": 0, "live": True},
    "explosion_spare":     {"type": "list", "of": "player", "default": [], "live": True},
    "cloud":               {"type": "bool", "default": False, "live": True},
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


# --- preset: explosion (copied from _presets/explosion.py) ---
def explosion(at, by, v):
    """Blow up a place (or a player's name: where they stand) with blasts of explosion_power
    (TNT is 4), laid out by explosion_layout: "one" at the place, "plus" with four more
    explosion_spread blocks out, "ring" with eight round it, "rings" with six at
    explosion_spread and ten at twice that. explosion_breaks: real TNT, which breaks blocks;
    else a blast that only hurts and throws back. by (the player who set it off, or "") and
    the players in explosion_spare are never hurt, nor are pets and villagers; no blast lands
    within explosion_keep blocks of any player, who is caught at its edge instead. Returns
    how many blasts went off."""
    if isinstance(at, str):
        feet = mc.pos(at)
        if feet is None:
            return 0
        at = world.Pos(feet[0], feet[1], feet[2], world.dim_of(at))
    power = v["explosion_power"]
    spare = [mc.bare(p) for p in [by] + list(v["explosion_spare"]) if p and not p.startswith("@")]
    pre = world.prefix(world.dim(at))
    here = pre + "positioned %.2f %.2f %.2f " % tuple(at[:3])
    points = _explosion_points(at, v["explosion_layout"], v["explosion_spread"])
    if v["explosion_keep"]:
        far = v["explosion_keep"] + 2 * v["explosion_spread"]
        players = [tuple(float(c) for c in m) for m in re.findall(r"\[(-?[\d.eE-]+)d, (-?[\d.eE-]+)d, (-?[\d.eE-]+)d\]",
                   mc.rcon(here + "as @a[distance=..%.1f] run data get entity @s Pos" % far))]
        points = [p for p in points if all(math.hypot(p[0] - q[0], p[2] - q[2]) >= v["explosion_keep"] for q in players)]
    if not points:
        return 0
    if v["explosion_breaks"]:
        # TNT hurts everything in reach: two seconds of Resistance V shield what is spared.
        reach = 2 * power + max(math.hypot(p[0] - at[0], p[2] - at[2]) for p in points)
        shield = "effect give @s resistance 2 4 true"
        for name in spare:
            mc.rcon(here + "as @a[name=%s,distance=..%.1f] run %s" % (name, reach, shield))
        mc.rcon(here + "as @e[type=!player,distance=..%.1f] if data entity @s Owner run %s" % (reach, shield))
        for kind in exclude.SPARED_TYPES:
            mc.rcon(here + "as @e[type=%s,distance=..%.1f] run %s" % (kind, reach, shield))
    for x, y, z in points:
        if v["explosion_breaks"]:
            mc.rcon(pre + "run summon tnt %.2f %.2f %.2f {fuse:0s,explosion_power:%.1ff,Tags:[\"vfg\",\"%s\"]}"
                    % (x, y, z, power, mc.TAG))
        else:
            _explosion_blast(pre, (x, y, z), power, spare)
    return len(points)


def _explosion_points(at, layout, s):
    """Where the blasts go: the place, then the layout's points round it, level with it."""
    rings = {"one": [], "plus": [(s, 4, 0.0)], "ring": [(s, 8, 0.0)], "rings": [(s, 6, 0.3), (2 * s, 10, 0.1)]}
    points = [tuple(at[:3])]
    for d, n, turn in rings[layout]:
        for k in range(n):
            a = 2 * math.pi * k / n + turn
            points.append((at[0] + d * math.cos(a), at[1], at[2] + d * math.sin(a)))
    return points


def _explosion_blast(pre, p, power, spare):
    """A blast that breaks nothing: its flash and sound, and damage from the place, so it
    throws back what it hurts, in three bands out to twice the power, about what TNT does
    there (44 near the middle of a power 4 blast, 6 at its edge)."""
    x, y, z = p
    here = pre + "positioned %.2f %.2f %.2f " % (x, y, z)
    mc.rcon(here + "run particle explosion_emitter ~ ~ ~ %.1f %.1f %.1f 0 %d force"
            % (power / 2, power / 3, power / 2, max(1, int(power / 2))))
    mc.rcon(here + "run playsound minecraft:entity.generic.explode master @a ~ ~ ~ %.1f 0.8" % max(1.0, power))
    r = 2.0 * power
    for k in range(3):
        lo, hi = r * k / 3.0, r * (k + 1) / 3.0
        near = 1.0 - (lo + hi) / 2.0 / r
        amount = (near * near + near) / 2.0 * 7.0 * r + 1.0
        for frag in exclude.targets("", "distance=%.2f..%.2f" % (lo, hi), spare):
            mc.rcon(here + "%s run damage @s %.1f minecraft:explosion at %.2f %.2f %.2f" % (frag, amount, x, y, z))


v = values(VALUES)
mc.log("arrows of", v["player"], "explode, power", v["explosion_power"])
for at, p in loop(projectile_lands(v["player"], v)):
    n = explosion(at, p, v)
    if n and v["cloud"]:
        # The run's "nuke": a mushroom cloud over the blast.
        mc.rcon(world.prefix(at) + "positioned %.1f %.1f %.1f run particle explosion_emitter ~ ~8 ~ 3 6 3 0 20 force"
                % tuple(at[:3]))
    mc.log("%d blast(s) where %s's arrow came down: %.0f %.0f %.0f" % (n, p, at[0], at[1], at[2]))
