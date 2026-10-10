"""Throw something you can see from a player to where they look: an orb of light or a block, flying at a speed, for a spirit bomb or a thrown power; the script goes on where it lands."""
import math
import random
import time

import mc
from kit import aim, fx, world

VALUES = {
    "projectile_kind": {"type": "enum", "of": ["orb", "block"], "default": "orb", "live": True},
    "projectile_size": {"type": "number", "min": 0.2, "max": 16, "default": 2, "live": True},
    "projectile_speed": {"type": "number", "min": 2, "max": 200, "default": 30, "live": True},
    "projectile_particle": {"type": "particle", "default": "end_rod", "live": True},
    "projectile_block": {"type": "block", "default": "magma_block", "live": True},
}

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
