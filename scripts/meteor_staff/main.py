"""Meteor Staff: right-click a staff and a flaming meteor falls from the sky onto the block you look at and explodes; the one who called it is spared."""
ASKS = ["I want the power to toss meteors, give it", "giving the ability to toss meteors", "the meteor staff",
        "meteor powers"]
from kit import values, aim, fx, world, exclude, items, loop
import math
import random
import re
import time
import mc

VALUES = {
    "player":           {"type": "player", "default": "@a"},
    "item":             {"type": "str", "default": "Meteor Staff"},
    "cooldown":         {"type": "number", "min": 0, "max": 60, "default": 5, "live": True},
    "reach":            {"type": "int", "min": 4, "max": 300, "default": 120, "live": True},
    "meteor_size":      {"type": "number", "min": 1, "max": 24, "default": 4, "live": True},
    "meteor_blocks":    {"type": "str", "default": "magma_block", "live": True},
    "meteor_height":    {"type": "int", "min": 10, "max": 300, "default": 60, "live": True},
    "meteor_slant":     {"type": "int", "min": 0, "max": 100, "default": 15, "live": True},
    "meteor_seconds":   {"type": "number", "min": 0.5, "max": 20, "default": 2.5, "live": True},
    "explosion_power":  {"type": "number", "min": 1, "max": 16, "default": 4, "live": True},
    "explosion_layout": {"type": "enum", "of": ["one", "plus", "ring", "rings"], "default": "plus", "live": True},
    "explosion_spread": {"type": "number", "min": 1, "max": 32, "default": 3, "live": True},
    "explosion_breaks": {"type": "bool", "default": True, "live": True},
    "explosion_keep":   {"type": "number", "min": 0, "max": 32, "default": 0, "live": True},
    "explosion_spare":  {"type": "list", "of": "player", "default": [], "live": True},
}


# --- preset: meteor_fall (copied from _presets/meteor_fall.py) ---
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
    # Bent: 26.3 takes a display's block as its id, block_state:"minecraft:magma_block"; the
    # {Name:"..."} the preset writes makes a display of air, so the meteor was not seen.
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


def drop(p, at):
    """One meteor onto a place, then its blast; the one who called it is spared."""
    land = meteor_fall(at, v)
    if land is not None:
        n = explosion(land, p, v)
        fx.particle("flame", land, spread=(3, 1, 3), count=150, speed=0.1, up=1)
        mc.log("%s's meteor landed at %d %d %d: %d blasts" % ((p,) + land.block() + (n,)))


for p in loop(items.clicks(v["player"], v["item"], cooldown=v["cooldown"], color="gold", model="blaze_rod")):
    r = aim.ray(p, v["reach"])
    if r is None or r.why != "block":
        fx.actionbar(p, "No target in range", "gray")
        continue
    loop.background(drop, p, r.end)     # it falls for a few seconds: other players' clicks go on
