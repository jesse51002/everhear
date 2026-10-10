"""Spirit Bomb: hold right-click and a huge sphere of light gathers over your head to a swelling choir; then it is thrown where you look, a blast hurts everything for a hundred blocks round and carves a crater as big."""
ASKS = ["spirit bomb like in the show, like the giant explosion", "I said spirit bomb, not spear bomb",
        "spirit bomb one hundred percent every time, make it broken and just nuke the world",
        "make it like world ending, like you spirit bomb and you know the world's ending, I want the explosion to be that big",
        "it doesn't create a bigger crater, therefore it isn't good enough",
        "can I have a cool charge animation like in the show, with music",
        "lower it to one hundred by one hundred blocks", "make sure there's no cooldowns",
        "also give me the spirit bomb"]
from kit import values, aim, fx, loop, world, exclude, area, items
import math
import random
import time
import mc
import re
import threading

VALUES = {
    "player":                 {"type": "player", "default": "@a"},
    "item":                   {"type": "str", "default": "Spirit Bomb"},
    "charge":                 {"type": "number", "min": 0, "max": 10, "default": 1, "live": True},
    "cooldown":               {"type": "number", "min": 0, "max": 120, "default": 0, "live": True},
    "reach":                  {"type": "int", "min": 4, "max": 300, "default": 100, "live": True},
    "charge_style":           {"type": "enum", "of": ["sphere", "orbs", "aura"], "default": "sphere", "live": True},
    "charge_seconds":         {"type": "number", "min": 0.2, "max": 15, "default": 5, "live": True},
    "charge_particle":        {"type": "particle", "default": "dust{color:[0.3,0.7,1.0],scale:2.5}", "live": True},
    "charge_size":            {"type": "number", "min": 1, "max": 30, "default": 10, "live": True},
    "theme_tune":             {"type": "enum", "of": ["galick_gun", "hollow_purple", "ice_beam", "spirit_bomb", "spirit_bomb_throw", "super_saiyan", "boss"], "default": "spirit_bomb", "live": True},
    "theme_to":               {"type": "enum", "of": ["near", "everyone", "player"], "default": "near", "live": True},
    "theme_reach":            {"type": "int", "min": 8, "max": 256, "default": 48, "live": True},
    "projectile_kind":        {"type": "enum", "of": ["orb", "block"], "default": "orb", "live": True},
    "projectile_size":        {"type": "number", "min": 0.2, "max": 16, "default": 8, "live": True},
    "projectile_speed":       {"type": "number", "min": 2, "max": 200, "default": 40, "live": True},
    "projectile_particle":    {"type": "particle", "default": "dust{color:[0.9,0.97,1.0],scale:2.0}", "live": True},
    "projectile_block":       {"type": "block", "default": "magma_block", "live": True},
    "explosion_power":        {"type": "number", "min": 1, "max": 16, "default": 16, "live": True},
    "explosion_layout":       {"type": "enum", "of": ["one", "plus", "ring", "rings"], "default": "rings", "live": True},
    "explosion_spread":       {"type": "number", "min": 1, "max": 32, "default": 32, "live": True},
    "explosion_breaks":       {"type": "bool", "default": False, "live": True},
    "explosion_keep":         {"type": "number", "min": 0, "max": 32, "default": 0, "live": True},
    "explosion_spare":        {"type": "list", "of": "player", "default": [], "live": True},
    "crater_radius":          {"type": "int", "min": 2, "max": 300, "default": 100, "live": True},
    "crater_leave":           {"type": "enum", "of": ["air", "lava", "fire"], "default": "air", "live": True},
    "crater_spare_protected": {"type": "bool", "default": True, "live": True},
    "crater_load":            {"type": "bool", "default": True, "live": True},
}


# --- preset: charge_up (copied from _presets/charge_up.py) ---
def charge_up(player, v):
    """Show a charge on a player for charge_seconds, the sound rising with it, then return
    where it ended: a Pos at the sphere's middle, where the orbs met, or the player's chest;
    None if the player could not be read (gone offline). charge_size is how big it gets:
    the sphere's radius, how far apart the orbs start, how far the aura reaches. It follows
    the player as they move, and ends early if the script stops."""
    style, size, dust = v["charge_style"], v["charge_size"], v["charge_particle"]
    seconds = v["charge_seconds"]
    lk = aim.look(player)
    if lk is None:
        return None
    fx.sound("block.beacon.activate", player, volume=3, pitch=1.5 if style == "sphere" else 0.6)
    if style != "sphere":
        fx.sound("entity.warden.sonic_charge", player, volume=2)
    start, i, at = time.time(), 0, lk.feet.moved(0, 1, 0)
    while True:
        k = min(1.0, (time.time() - start) / seconds)          # 0 to 1: how far charged
        lk = aim.look(player) or lk
        if style == "sphere":
            at = _charge_up_sphere(lk, size, k, dust, i)
        elif style == "orbs":
            at = _charge_up_orbs(lk, size, k, dust, i)
        else:
            at = _charge_up_aura(player, lk, size, dust, i)
        _charge_up_sound(at, style, k, i)
        left = start + seconds - time.time()
        if left <= 0 or not loop.wait(min(0.15, left)):
            return at
        i += 1


def _charge_up_sphere(lk, size, k, dust, i):
    """One frame of a sphere growing over the player's head (the Spirit Bomb's): its shell,
    a glow inside, a beam from the sky into it, and light streaming in off the ground."""
    r = size * (0.2 + 0.8 * k)
    mid = lk.feet.moved(0, size + 2.5, 0)
    for _ in range(4 + int(r // 2)):                           # points on the shell
        a, b = random.uniform(0, 2 * math.pi), random.uniform(-1, 1)
        s = math.sqrt(1 - b * b)
        fx.particle(dust, mid.moved(r * s * math.cos(a), r * b, r * s * math.sin(a)), 0.15, 4)
    fx.particle("end_rod", mid, r * 0.4, 6, 0.02)
    fx.particle("glow", mid, r * 0.5, 3, 0.02)
    fx.particle("end_rod", mid.moved(0, 26, 0), (0.3, 26, 0.3), 12)
    phase = (i * 0.2) % 1.0
    for j in range(3):                                          # light streaming in from the ground round it
        a, far = random.uniform(0, 2 * math.pi), random.uniform(8, 40)
        f = (phase + j / 3.0) % 1.0                             # 0 on the ground, 1 in the sphere
        fx.particle(random.choice(("end_rod", "glow", "electric_spark")),
                    mid.moved(far * math.cos(a) * (1 - f), (lk.feet[1] - mid[1]) * (1 - f * f),
                              far * math.sin(a) * (1 - f)), 0.05)
    return mid


def _charge_up_orbs(lk, size, k, dust, i):
    """One frame of two orbs at the player's hands closing in to meet three blocks ahead
    (Hollow Purple's; with a small charge_size, the Galick Gun's one orb)."""
    mid = lk.ahead(3, up=-0.3)
    off = size / 2.0 * (1 - k)
    for side, flame in ((1, "soul_fire_flame"), (-1, "flame")):
        orb = mid.moved(lk.right[0] * off * side, 0, lk.right[2] * off * side)
        fx.particle(dust, orb, 0.25, 12)
        fx.particle(flame, orb, 0.2, 3)
    if k > 0.6:                                                 # met: one orb, flaring
        fx.particle(dust, mid, 0.3, 20)
        fx.particle("witch", mid, 0.5, 10)
    return mid


def _charge_up_aura(player, lk, size, dust, i):
    """One frame of an aura round the player's body (Super Saiyan's), drawn where they stand."""
    w, h = size / 10.0, size / 7.5
    fx.particle(dust, player, (w, h, w), 14, up=1)
    fx.particle("soul_fire_flame", player, (w, h, w), 6, 0.02, up=1)
    fx.particle("end_rod", player, (w * 0.7, 0.1, w * 0.7), 4, 0.08, up=0.2)
    if i % 6 == 0:
        fx.particle("electric_spark", player, (w * 1.5, h * 1.1, w * 1.5), 10, 0.3, up=1)
    return lk.feet.moved(0, 1, 0)


def _charge_up_sound(at, style, k, i):
    """The charge's sound, rising in pitch as it fills."""
    if style == "orbs":
        fx.sound("block.note_block.pling" if i % 2 == 0 else "block.note_block.bell", at, volume=5, pitch=0.6 + 1.4 * k)
        fx.sound("block.amethyst_block.chime", at, volume=5, pitch=0.6 + 1.4 * k)
    elif style == "sphere":
        if i % 4 == 0:
            fx.sound("block.beacon.ambient", at, volume=3, pitch=0.8 + 1.2 * k)
        if i % 6 == 0:
            fx.sound("entity.breeze.idle_air", at, volume=2, pitch=0.6 + k)
    elif i % 6 == 0:
        fx.sound("entity.firework_rocket.twinkle", at, volume=1, pitch=1.8)


# --- preset: theme_music (copied from _presets/theme_music.py) ---
def theme_music(at, v, tune=None):
    """Play the tune theme_tune names, in the background, at a player (or a place), and
    return how many seconds it lasts. theme_to: "near" is heard within theme_reach blocks
    of it, "everyone" by every player where they stand, "player" by that player alone. A
    newer theme at the same player (or for everyone) cuts this one off, a record too.
    Bent: tune plays another of the tunes instead (the throw after the charge's choir)."""
    notes = _THEME_MUSIC_TUNES[tune or v["theme_tune"]]
    to = v["theme_to"]
    if to == "player" and not isinstance(at, str):
        to = "near"                     # a place has nobody to play it to alone
    where = at if isinstance(at, str) else "%d_%d_%d" % tuple(at[:3])
    key = "theme" if to == "everyone" else "theme_" + where      # one theme at a time per player or place
    _theme_music_hush(key)
    hear = {"everyone": "@a", "player": at}.get(to)
    fx.melody(notes, at, to=hear, key=key, reach=v["theme_reach"])
    records = [n[1] for n in notes if n[1] in _THEME_MUSIC_RECORDS]
    if records:
        if not _THEME_MUSIC_PLAYING:    # a stop cuts records off too (said again after all were cut: harmless)
            mc.on_stop(lambda: [_theme_music_hush(k) for k in list(_THEME_MUSIC_PLAYING)])
        _THEME_MUSIC_PLAYING[key] = (hear or "@a", records)
    return max([n[0] + 0.5 for n in notes] + [_THEME_MUSIC_RECORDS[r] for r in records])


# Each tune is a list of notes: (seconds from the start, a note block instrument or a sound
# id, a note from 0 to 24 where 12 is the sound's own pitch, volume). The run's tunes, as
# its scripts played them; a volume of 3 is heard 48 blocks away.
_THEME_MUSIC_TUNES = {
    "galick_gun": [         # a rising purple fanfare, 1.5 s
        (0, "guitar", 0, 3), (0, "block.beacon.power_select", 4, 2), (0.2, "pling", 4, 3), (0.4, "guitar", 7, 3),
        (0.6, "pling", 12, 3), (0.8, "guitar", 16, 3), (0.8, "bell", 16, 3), (1.0, "pling", 19, 3),
        (1.2, "bell", 24, 3),
        (1.5, "bell", 24, 3), (1.5, "pling", 24, 3), (1.5, "guitar", 19, 3), (1.5, "guitar", 16, 3),
        (1.5, "block.beacon.activate", 12, 3), (1.5, "block.amethyst_block.chime", 24, 3),
    ],
    "hollow_purple": [      # a slow fall from bell to bass, then a boom, 3 s
        (0, "bass", 7, 3), (0, "didgeridoo", 7, 3), (0, "block.beacon.power_select", 5, 2),
        (0.4, "bell", 19, 3), (0.4, "block.amethyst_block.chime", 3, 2),
        (0.8, "bell", 15, 3), (0.8, "bass", 3, 3),
        (1.2, "bell", 14, 3),
        (1.6, "bell", 12, 3), (1.6, "didgeridoo", 2, 3),
        (2.0, "bell", 10, 3),
        (2.4, "bell", 7, 3), (2.4, "bass", 0, 3), (2.4, "didgeridoo", 0, 3),
        (3.0, "bell", 0, 3), (3.0, "block.amethyst_block.resonate", 0, 3), (3.0, "entity.generic.explode", 0, 2),
    ],
    "ice_beam": [           # high chimes and xylophone, 2.5 s
        (0, "chime", 24, 3), (0.2, "iron_xylophone", 22, 3), (0.4, "bell", 19, 3), (0.6, "chime", 17, 3),
        (0.8, "iron_xylophone", 19, 3), (1.0, "bell", 22, 3), (1.2, "chime", 24, 3), (1.4, "iron_xylophone", 22, 3),
        (1.6, "bell", 24, 3), (1.6, "block.amethyst_block.chime", 24, 2), (1.9, "chime", 17, 3),
        (2.2, "iron_xylophone", 24, 3), (2.5, "bell", 24, 3), (2.5, "chime", 19, 3),
    ],
    "spirit_bomb": [        # a choir swelling while the bomb charges: a chord every 1.1 s, louder each time, 6 s
        (0.0, "flute", 2, 1), (0.05, "flute", 9, 1), (0.0, "chime", 6, 1), (0.1, "bell", 14, 1),
        (0.0, "block.beacon.ambient", 2, 1),
        (1.1, "flute", 4, 1.5), (1.15, "flute", 11, 1.5), (1.1, "chime", 8, 1.5), (1.2, "bell", 16, 1.5),
        (1.1, "block.beacon.ambient", 4, 1.5), (1.6, "chime", 13, 1.5),
        (2.2, "flute", 5, 2), (2.25, "flute", 12, 2), (2.2, "chime", 9, 2), (2.3, "bell", 17, 2),
        (2.2, "block.beacon.ambient", 5, 2),
        (3.3, "flute", 7, 2.5), (3.35, "flute", 14, 2.5), (3.3, "chime", 11, 2.5), (3.4, "bell", 19, 2.5),
        (3.3, "block.beacon.ambient", 7, 2.5), (3.8, "chime", 16, 2.5),
        (4.4, "flute", 9, 3), (4.45, "flute", 16, 3), (4.4, "chime", 13, 3), (4.5, "bell", 21, 3),
        (4.4, "block.beacon.ambient", 9, 3),
        (5.5, "flute", 11, 3), (5.55, "flute", 18, 3), (5.5, "chime", 15, 3), (5.6, "bell", 23, 3),
        (5.5, "block.beacon.ambient", 11, 3), (6.0, "chime", 20, 3),
    ],
    "spirit_bomb_throw": [  # the release: a bright chord climbing, 1 s
        (0, "flute", 12, 3), (0, "chime", 16, 3), (0, "bell", 19, 3), (0, "block.beacon.activate", 12, 3),
        (0.25, "flute", 16, 3), (0.25, "bell", 24, 3),
        (0.5, "flute", 19, 3), (0.5, "chime", 24, 3), (0.5, "block.amethyst_block.resonate", 24, 3),
        (0.9, "bell", 24, 3), (0.9, "chime", 19, 3),
    ],
    "super_saiyan": [       # a fast climbing riff with a bass under every third note, then a chord, 2 s
        (0, "block.beacon.activate", 18, 0.8),
        (0.0, "bit", 6, 1), (0.0, "bass", 0, 1), (0.14, "pling", 9, 1), (0.28, "guitar", 11, 1),
        (0.42, "bell", 12, 1), (0.42, "bass", 0, 1), (0.56, "bit", 14, 1), (0.7, "pling", 16, 1),
        (0.84, "guitar", 17, 1), (0.84, "bass", 5, 1), (0.98, "bell", 19, 1), (1.12, "bit", 21, 1),
        (1.26, "pling", 22, 1), (1.26, "bass", 10, 1), (1.4, "guitar", 24, 1), (1.54, "bell", 21, 1),
        (1.68, "bit", 24, 1), (1.68, "bass", 12, 1), (1.82, "pling", 24, 1),
        (1.96, "pling", 12, 1), (1.96, "bell", 12, 1), (1.96, "pling", 16, 1), (1.96, "bell", 16, 1),
        (1.96, "pling", 19, 1), (1.96, "bell", 19, 1), (1.96, "pling", 24, 1), (1.96, "bell", 24, 1),
    ],
    "boss": [               # Pigstep, with a growl, a wither and thunder over it, 148 s
        (0, "music_disc.pigstep", 12, 2),
        (20, "entity.ender_dragon.growl", 12, 0.4), (55, "entity.wither.ambient", 12, 0.4),
        (90, "entity.lightning_bolt.thunder", 12, 0.4), (125, "entity.ender_dragon.growl", 12, 0.4),
    ],
}

# Records play on in the players' game once started, so a newer theme stops them by name;
# each with how long it lasts, in seconds.
_THEME_MUSIC_RECORDS = {"music_disc.pigstep": 148.0}
_THEME_MUSIC_PLAYING = {}       # key -> (who hears it, [records]) while a record may still play


def _theme_music_hush(key):
    """Cut off what plays under a key: its notes, and any record it started."""
    fx.stop_melody(key)
    hear, records = _THEME_MUSIC_PLAYING.pop(key, (None, []))
    for r in records:
        mc.rcon("stopsound %s * minecraft:%s" % (hear, r))


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
        block = v["projectile_block"].split("[")[0]
        # Bent: 26.3 takes a display's block as its id; {Name:"..."} makes a display of air.
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


# --- preset: crater (copied from _presets/crater.py) ---
def crater(at, v):
    """Carve a ball of crater_radius round a place, the middle first, never the bedrock floor
    nor (with crater_spare_protected) a protected box; then crater_leave: "air", "lava" (a
    pool in its lowest third) or "fire" (on its floor, where fire can stand). crater_load
    loads the chunks it reaches first, so a big crater is whole, and lets them go after;
    without it only the loaded part is carved. It takes a while when big: call it in
    loop.background. One carves at a time and one more waits; any more are skipped. Returns
    blocks changed."""
    with _CRATER_LOCK:
        if _CRATER_QUEUE[0] >= 2:
            mc.log("crater skipped: one is carving and one waits already")
            return 0
        _CRATER_QUEUE[0] += 1
    try:
        with _CRATER_CARVING:
            return _crater_carve(at, v)
    finally:
        with _CRATER_LOCK:
            _CRATER_QUEUE[0] -= 1


_CRATER_LOCK = threading.Lock()
_CRATER_CARVING = threading.Lock()
_CRATER_QUEUE = [0]         # craters carving or waiting


def _crater_carve(at, v):
    r, leave, spare = v["crater_radius"], v["crater_leave"], v["crater_spare_protected"]
    dim = world.dim(at)
    if v["crater_load"]:
        area.forceload(area.box(at, r, 0), dim=dim, wait=10.0)
    try:
        n = area.sphere(at, r, "air", spare_protected=spare, keep_bedrock=True, dim=dim)
        if leave != "air":
            # Lava fills the lowest third; fire is put everywhere below the middle and stays
            # only where it can stand: on the floor, and against what burns.
            top = int(math.floor(at[1])) - (r - max(1, r // 3) if leave == "lava" else 0)
            loaded = {}
            for b in area.sphere_boxes(at, r):
                if b[1] <= top:
                    n += area.fill((b[0], b[1], b[2], b[3], min(b[4], top), b[5]), leave, "replace air", dim=dim,
                                   spare_protected=spare, keep_bedrock=True, loaded=loaded)
        mc.log("crater r=%d at %d %d %d: %d blocks" % ((r,) + tuple(int(math.floor(c)) for c in at[:3]) + (n,)))
        return n
    finally:
        if v["crater_load"]:
            area.release(dim)


v = values(VALUES)
busy = set()                    # players whose bomb is under way


def throw(p):
    """One bomb: the sphere gathers overhead with its choir, then it flies where they look and
    blows up; the crater is carved after, in the background."""
    try:
        theme_music(p, v)
        ball = charge_up(p, v)
        r = aim.ray(p, v["reach"])
        if ball is None or r is None:
            return
        theme_music(p, v, "spirit_bomb_throw")
        end = projectile(ball, r.end, v)
        fx.particle("flash{color:[1.0,1.0,1.0,1.0]}", end, count=2)
        fx.particle("end_rod", end, spread=(15, 10, 15), count=600, speed=0.8)
        fx.particle(v["charge_particle"], end, spread=(15, 10, 15), count=600)
        fx.particle("firework", end, spread=(15, 10, 15), count=300, speed=0.6)
        for sound in ("entity.lightning_bolt.thunder", "entity.warden.sonic_boom", "entity.firework_rocket.large_blast"):
            fx.sound(sound, end, volume=8, pitch=0.6)
        n = explosion(end, p, v)
        loop.background(crater, end, v)
        mc.log("%s threw it: %d blasts at %d %d %d" % ((p, n) + end.block()))
    finally:
        busy.discard(p)


for p in loop(items.charged(v["player"], v["item"], v["charge"], v["cooldown"])):
    if p not in busy:
        busy.add(p)
        loop.background(throw, p)       # each bomb on its own, so another player's never waits
