"""Fireball: cast it and a fireball flies from the caster's eyes where he looks and explodes on the first mob or block it hits, sparing him; it fires once, at once, as a spoken incantation needs."""
ASKS = ["fireball", "shoot a fireball when I say fireball", "cast a fireball", "a fireball that explodes when it hits",
        "hot fireball", "throw a fireball where I look"]
from kit import result, values, aim, fx, loop, world, exclude
import math
import random
import time
import mc
import re


# --- preset: projectile (copied from _presets/projectile.py) ---
_projectile_n = [0]


def projectile(start, end, v):
    """Fly from `start` (a player's name: from a block in front of their eyes; or a place) to
    `end` (a place, such as aim.ray(p).end) in a straight line at projectile_speed blocks a
    second: an orb projectile_size across of projectile_particle ("dust{color:[0.6,0.8,1.0],
    scale:2}" for a coloured one), or a block_display of projectile_block that size. It is a
    show and touches nothing on the way. Returns `end`, where an explosion or a crater goes
    next; None when the player cannot be read."""
    if isinstance(start, str):
        look = aim.look(start)
        if look is None:
            return None
        start = look.ahead(1.0)
    start = start if isinstance(start, world.Pos) else world.Pos(*start[:3])
    end = end if isinstance(end, world.Pos) else world.Pos(end[0], end[1], end[2], start.dim)
    size, gap = v["projectile_size"], 0.1
    steps = max(1, min(400, int(math.ceil(math.dist(start, end) / (v["projectile_speed"] * gap)))))
    tag = None
    if v["projectile_kind"] == "block":
        _projectile_n[0] += 1
        tag = "%s_projectile%d" % (mc.TAG, _projectile_n[0])
        block = v["projectile_block"].split("[")[0]        # 26.3: the id alone, never a state
        mc.rcon(world.run(start, 'summon block_display %.2f %.2f %.2f {block_state:"%s",teleport_duration:2,'
                          'transformation:{translation:[%gf,%gf,%gf],left_rotation:[0f,0f,0f,1f],'
                          'right_rotation:[0f,0f,0f,1f],scale:[%gf,%gf,%gf]},Tags:["vfg","%s","%s"]}'
                          % (start[0], start[1], start[2], block if ":" in block else "minecraft:" + block,
                             -size / 2, -size / 2, -size / 2, size, size, size, mc.TAG, tag)))
    fx.sound("entity.wind_charge.wind_burst", start, volume=3, pitch=0.6)
    for i in range(1, steps + 1):
        if mc.stopping():
            break
        k = i / float(steps)
        here = world.Pos(*[start[j] + (end[j] - start[j]) * k for j in range(3)], dim=start.dim)
        if tag:
            mc.rcon(world.run(here, "tp @e[type=block_display,tag=%s] %.2f %.2f %.2f" % (tag, here[0], here[1], here[2])))
            fx.particle(v["projectile_particle"], here, spread=size * 0.2, count=3)
        else:
            fx.particle(v["projectile_particle"], here, spread=size * 0.25, count=int(4 + 6 * size), speed=0.01)
            for _ in range(4):
                a, b = random.uniform(0, 2 * math.pi), random.uniform(-1, 1)
                q = math.sqrt(1 - b * b) * size / 2
                fx.particle(v["projectile_particle"], here.moved(q * math.cos(a), b * size / 2, q * math.sin(a)), count=2)
        time.sleep(gap)
    if tag:
        mc.rcon("kill @e[type=block_display,tag=%s]" % tag)
    return end


# --- preset: explosion (copied from _presets/explosion.py) ---
def explosion(at, by, v):
    """Blow up a place (or a player's name: where they stand) with blasts of explosion_power
    (TNT is 4; 0: no blast at all), laid out by explosion_layout: "one" at the place, "plus" with four more
    explosion_spread blocks out, "ring" with eight round it, "rings" with six at
    explosion_spread and ten at twice that. explosion_breaks: real TNT, which breaks blocks;
    else a blast that only hurts and throws back. by (the player who set it off, or "") and
    the players in explosion_spare are never hurt, nor are pets and villagers; no blast lands
    within explosion_keep blocks of any player, who is caught at its edge instead. Returns
    how many blasts went off."""
    power = v["explosion_power"]
    if power <= 0:
        return 0
    if isinstance(at, str):
        feet = mc.pos(at)
        if feet is None:
            return 0
        at = world.Pos(feet[0], feet[1], feet[2], world.dim_of(at))
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


v = values()


def fire(p):
    """One fireball from p's eyes along where he looks: to the first mob or player it meets on the
    way (within fireball_width of its line), else to the first block, else reach blocks out."""
    look = aim.look(p)
    if look is None:
        return None
    r = aim.ray(look, v["reach"])
    hits = aim.near_line(r, v["fireball_width"], shooter=p)
    end = world.Pos(*hits[0].pos[:3], dim=r.dim).moved(0, 1, 0) if hits else r.end
    projectile(look.ahead(1.0), end, v)
    explosion(end, p, v)
    return end, hits[0].name if hits else (r.hit_block and "a block" or "nothing")


# A cast (an incantation said) starts this copy to fire now: it waits for no click or crouch.
fired = []
for p in loop.who(v["player"]):
    got = fire(p)
    if got:
        fired.append("%s at %d %d %d (%s)" % (p, got[0][0], got[0][1], got[0][2], got[1]))
result("fireball: %s" % ("; ".join(fired) if fired else "nobody to fire it"), fired=len(fired),
       cast=mc.cast(), player=v["player"])

