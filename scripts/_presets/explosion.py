"""An explosion at a place: one blast, a plus, a ring or rings of them, breaking blocks or not."""
import math
import re

import mc
from kit import exclude, world

VALUES = {
    "explosion_power": {"type": "number", "min": 1, "max": 16, "default": 4, "live": True},
    "explosion_layout": {"type": "enum", "of": ["one", "plus", "ring", "rings"], "default": "one", "live": True},
    "explosion_spread": {"type": "number", "min": 1, "max": 32, "default": 4, "live": True},
    "explosion_breaks": {"type": "bool", "default": True, "live": True},
    "explosion_keep": {"type": "number", "min": 0, "max": 32, "default": 0, "live": True},
    "explosion_spare": {"type": "list", "of": "player", "default": [], "live": True},
}


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
