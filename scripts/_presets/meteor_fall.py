"""A meteor or asteroid falls out of the sky onto a place or a player, slantwise, burning and smoking, any size and blocks; the script goes on where it lands."""
import math
import random
import re
import time

import mc
from kit import aim, fx, world
from actions._types import Field, Live, Values  # the values class: read on the PC only

VALUES = {
    "meteor_size": {"type": "number", "min": 1, "max": 24, "default": 4, "live": True},
    "meteor_blocks": {"type": "str", "default": "magma_block blackstone obsidian", "live": True},
    "meteor_height": {"type": "int", "min": 10, "max": 300, "default": 60, "live": True},
    "meteor_slant": {"type": "int", "min": 0, "max": 100, "default": 20, "live": True},
    "meteor_seconds": {"type": "number", "min": 0.5, "max": 20, "default": 3, "live": True},
}


class MeteorFall(Values):
    meteor_size: Live[float] = Field(4, ge=1, le=24)
    """how big the meteor is, in blocks across"""
    meteor_blocks: Live[str] = "magma_block blackstone obsidian"
    """what it is made of: a block id or a mix, "70% magma_block 30% obsidian" (no %: counts 1)"""
    meteor_height: Live[int] = Field(60, ge=10, le=300)
    """how high over the ground it starts falling, in blocks"""
    meteor_slant: Live[int] = Field(20, ge=0, le=100)
    """how far off to a random side it starts, in blocks, so it falls slantwise (0: straight down)"""
    meteor_seconds: Live[float] = Field(3, ge=0.5, le=20)
    """how long it takes to fall, in seconds"""


_meteor_fall_n = [0]


def meteor_fall(at, v):
    """Drop a meteor meteor_size blocks across, made of meteor_blocks (a block or a mix, "70%
    magma_block 30% obsidian"), from meteor_height above the ground under `at` (a place, or a
    player's name) and meteor_slant blocks off to a random side, in meteor_seconds. It is a
    show and breaks nothing: block displays, flame, smoke and sound, gone when it lands.
    Returns the world.Pos it landed on, on top of the first block under `at`, where an
    explosion or a crater goes next; None when a player cannot be read."""
    land = _meteor_fall_ground(at)
    if land is None:
        return None
    _meteor_fall_n[0] += 1
    tag = "%s_meteor%d" % (mc.TAG, _meteor_fall_n[0])
    a = random.uniform(0, 2 * math.pi)
    start = land.moved(-v["meteor_slant"] * math.cos(a), v["meteor_height"], -v["meteor_slant"] * math.sin(a))
    gap = v["meteor_size"] / 3.0            # 19 displays, a 3 x 3 x 3 cluster without its corners
    s, mix = gap * 1.35, _meteor_fall_mix(v["meteor_blocks"])
    # 26.3 takes a display's block as its id alone, block_state:"minecraft:magma_block": a
    # state in it, or {Name:...}, shows air, so [...] is cut off.
    for ox, oy, oz in [(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1) if abs(x * y * z) == 0]:
        block = random.choices([m[0] for m in mix], [m[1] for m in mix])[0].split("[")[0]
        mc.rcon(world.run(land, 'summon block_display %.2f %.2f %.2f {block_state:"%s",teleport_duration:2,'
                          'transformation:{translation:[%gf,%gf,%gf],left_rotation:[0f,0f,0f,1f],'
                          'right_rotation:[0f,0f,0f,1f],scale:[%gf,%gf,%gf]},Tags:["vfg","%s","%s"]}'
                          % (start[0] + ox * gap, start[1] + oy * gap, start[2] + oz * gap,
                             block if ":" in block else "minecraft:" + block, -s / 2, -s / 2, -s / 2, s, s, s,
                             mc.TAG, tag)))
    fx.sound("entity.ghast.scream", start, volume=6, pitch=0.6)
    steps = max(5, int(v["meteor_seconds"] / 0.1))
    d = [(land[i] - start[i]) / steps for i in range(3)]
    for i in range(1, steps + 1):
        if mc.stopping():
            break
        mc.rcon("execute as @e[type=block_display,tag=%s] at @s run tp @s ~%.3f ~%.3f ~%.3f" % (tag, d[0], d[1], d[2]))
        here = start.moved(d[0] * i, d[1] * i, d[2] * i)
        fx.particle("flame", here, spread=gap, count=int(10 + 10 * gap), speed=0.05)
        fx.particle("large_smoke", here.moved(0, gap * 1.5, 0), spread=gap, count=int(4 + 4 * gap), speed=0.02)
        if i % 10 == 5:
            fx.sound("entity.blaze.shoot", here, volume=4, pitch=0.6)
        time.sleep(0.1)
    mc.rcon("kill @e[type=block_display,tag=%s]" % tag)
    return land


def _meteor_fall_ground(at):
    """On top of the first block at or under a place or a player, as a world.Pos; the place
    itself when there is none (over the void, or a chunk that is not loaded)."""
    if isinstance(at, str):
        p = mc.pos(at)
        if not p:
            return None
        at = world.Pos(p[0], p[1], p[2], world.dim_of(at))
    at = at if isinstance(at, world.Pos) else world.Pos(*at[:3])
    r = aim.cast(at.moved(0, 0.5, 0), (0.0, -1.0, 0.0), at[1] + 0.5 - world.limits(at.dim)[0])
    if r.why != "block":
        return at
    return world.Pos(r.hit_block[0] + 0.5, r.hit_block[1] + 1.0, r.hit_block[2] + 0.5, at.dim)


def _meteor_fall_mix(text):
    """[(block, weight)] from "70% magma_block, 30% obsidian"; a block with no % weighs 1."""
    mix, weight = [], None
    for w, block in re.findall(r"(\d+(?:\.\d+)?)%|([^\s,%\[{]+(?:\[[^\]]*\])?(?:\{[^}]*\})?)", text):
        if w:
            weight = float(w)
        else:
            mix.append((block, 1.0 if weight is None else weight))
            weight = None
    return mix or [("magma_block", 1.0)]
