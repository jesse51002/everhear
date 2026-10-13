"""Galick Gun: hold right-click to charge a purple orb, then fire a beam through walls that hurts everything along it, carves a tunnel and blows a crater at its end; two beams fired at each other clash."""
ASKS = ["give me a galick gun, it's Vegeta's", "give me a gallic gun", "remove the cooldown on galick gun, still have it charge but just the cooldown",
        "make galick gun do more damage", "galick gun pierce blocks like the hollow purple does",
        "galick gun still does not destroy blocks, make it destroy blocks", "increase the range of galick gun and make it deal thirty hearts of damage",
        "make the beam of galick gun do damage as well as the explosion at the end",
        "make it interact with the galick gun, like in the anime they have a clash of beams",
        "when he uses galick gun there should be music"]
from kit import values, result, aim, fx, loop, exclude, world, area, items
import math
import random
import time
import mc
import itertools
import threading
import re
import fcntl
import json
import os

# --- preset: charge_up (copied from _presets/charge_up.py) ---
def charge_up(player, v):
    """Show a charge on a player for charge_seconds, the sound rising with it, then return
    where it ended: a Pos at the sphere's middle, where the orbs met, or the player's chest;
    None if the player could not be read (gone offline). charge_size is how big it gets:
    the sphere's radius, how far apart the orbs start, how far the aura reaches. charge_orbs:
    two particles, the right orb's and the left's (Hollow Purple's blue and red), which meet
    in charge_particle; [] for charge_particle both. It follows the player as they move, and
    ends early if the script stops."""
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
            at = _charge_up_orbs(lk, size, k, dust, i, list(v["charge_orbs"]))
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


def _charge_up_orbs(lk, size, k, dust, i, colors=()):
    """One frame of two orbs at the player's hands closing in to meet three blocks ahead
    (Hollow Purple's; with a small charge_size, the Galick Gun's one orb), each in its own
    colour when two are given."""
    mid = lk.ahead(3, up=-0.3)
    off = size / 2.0 * (1 - k)
    colors = colors[:2] if len(colors) >= 2 else [dust, dust]
    for side, flame, color in ((1, "soul_fire_flame", colors[0]), (-1, "flame", colors[1])):
        orb = mid.moved(lk.right[0] * off * side, 0, lk.right[2] * off * side)
        fx.particle(color, orb, 0.25, 12)
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
def theme_music(at, v, *, tune=None):
    """Play the tune theme_tune names (or `tune`, for this call: the throw after a charge's
    choir), in the background, at a player (or a place), and return how many seconds it
    lasts. theme_to: "near" is heard within theme_reach blocks of it, "everyone" by every
    player where they stand, "player" by that player alone. theme_loop: again each time it
    ends (boss music while the boss lives), until a newer theme, "none" or a stop. A newer
    theme at the same player (or for everyone) cuts this one off, a record too; "none" plays
    nothing and only cuts off what plays there."""
    to = v["theme_to"]
    if to == "player" and not isinstance(at, str):
        to = "near"                     # a place has nobody to play it to alone
    where = at if isinstance(at, str) else "%d_%d_%d" % tuple(at[:3])
    key = "theme" if to == "everyone" else "theme_" + where      # one theme at a time per player or place
    hear = {"everyone": "@a", "player": at}.get(to)
    _THEME_MUSIC_GEN[key] = gen = _THEME_MUSIC_GEN.get(key, 0) + 1   # a loop under this key ends
    fx.stop_melody(key)
    seconds = _theme_music_play(at, v, tune or v["theme_tune"], hear, key)
    if seconds and v["theme_loop"]:
        loop.background(_theme_music_again, at, v, tune, hear, key, gen, seconds)
    return seconds


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

# A record plays on in the players' game once started (fx.melody stops it when a newer
# melody starts, and on a stop); each with how long it lasts, in seconds.
_THEME_MUSIC_RECORDS = {"music_disc.pigstep": 148.0}
_THEME_MUSIC_GEN = {}           # key -> how many themes have started under it: a loop plays on while it is the last


def _theme_music_play(at, v, name, hear, key):
    """Start one tune under a key; how long it lasts (0 for "none")."""
    notes = _THEME_MUSIC_TUNES.get(name)
    if not notes:
        return 0
    fx.melody(notes, at, to=hear, key=key, reach=v["theme_reach"])
    return max([n[0] + 0.5 for n in notes] + [_THEME_MUSIC_RECORDS.get(n[1], 0) for n in notes])


def _theme_music_again(at, v, tune, hear, key, gen, seconds):
    """Play it again each time it ends, while theme_loop is on and nothing newer started there."""
    while loop.wait(seconds) and v["theme_loop"] and _THEME_MUSIC_GEN.get(key) == gen:
        seconds = _theme_music_play(at, v, tune or v["theme_tune"], hear, key)
        if not seconds:
            return


# --- preset: damage_hits (copied from _presets/damage_hits.py) ---
def damage_hits(hits, shooter, v):
    """Hurt every hit once (hits: a list of aim.Hit, from aim.near_line, around or lock_on,
    or several of those added together), by damage_amount of damage_type, as the shooter, so
    a kill is theirs; damage_kill kills instead. damage_what: "all", "mobs" (no players),
    "hostile" (monsters only: never an animal, and no players) or "players". damage_most:
    only the nearest that many (1: the first only; 0: all). shooter: a player's name, or
    their aim.look(), with which a hit whose head the look passes takes damage_head times the
    damage. Never the shooter, a name in damage_spare, a pet, a villager or anything else
    exclude spares. Returns how many."""
    by = getattr(shooter, "player", shooter) or ""
    spare = [by] + list(v["damage_spare"])
    names = set(mc.bare(s) for s in spare if s and not s.startswith("@"))
    what = v["damage_what"]
    hits = [h for h in sorted(hits, key=lambda h: h.t) if mc.bare(h.name) not in names
            and (h.player if what == "players" else (what == "all" or not h.player))]
    if v["damage_most"]:
        hits = hits[:v["damage_most"]]
    body = "%s_hit%d" % (mc.TAG, next(_DAMAGE_N))      # each call tags with its own name
    head = [h for h in hits if v["damage_head"] > 1 and _damage_on_head(h, shooter)]
    count = {}
    for tag, some in ((body, [h for h in hits if h not in head]), (body + "h", head)):
        if some:
            count[tag] = aim.mark(some, tag, spare, hostile=what == "hostile")
    for tag, n in count.items():
        if n:
            if v["damage_kill"]:
                _damage_all(tag, 1000000, "generic_kill", by)
            else:
                _damage_all(tag, v["damage_amount"] * (1 if tag == body else v["damage_head"]), v["damage_type"], by)
        aim.untag(tag)
    return sum(count.values())


_DAMAGE_N = itertools.cycle(range(1, 17))    # 16 names in turn, so aim.mark clears each only once


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


# --- preset: crater (copied from _presets/crater.py) ---
def crater(at, v, *, path=()):
    """Carve a ball of crater_radius round a place, the middle first, never the bedrock floor
    nor (with crater_spare_protected) a protected box; then crater_leave: "air", "lava" (a
    pool in its lowest third) or "fire" (on its floor, where fire can stand). path: places
    along a beam (r.every(2)), each with a cube crater_tunnel blocks round it carved first:
    the beam's tunnel (0: none). crater_radius 0 carves nothing at all, the tunnel neither.
    crater_load loads the chunks it reaches first, when they are few (up to 256 chunks, a
    radius of about 120), so a big crater is whole, and lets them go after; else only the
    loaded part is carved. It takes a while when big: call it in loop.background. One carves
    at a time and one more waits; any more are skipped. Returns blocks changed."""
    if v["crater_radius"] <= 0:
        return 0
    with _CRATER_LOCK:
        if _CRATER_QUEUE[0] >= 2:
            mc.log("crater skipped: one is carving and one waits already")
            return 0
        _CRATER_QUEUE[0] += 1
    try:
        with _CRATER_CARVING:
            return _crater_carve(at, v, path)
    finally:
        with _CRATER_LOCK:
            _CRATER_QUEUE[0] -= 1


_CRATER_LOCK = threading.Lock()
_CRATER_CARVING = threading.Lock()
_CRATER_QUEUE = [0]         # craters carving or waiting


def _crater_carve(at, v, path):
    r, leave, spare = v["crater_radius"], v["crater_leave"], v["crater_spare_protected"]
    dim = world.dim(at)
    box = area.box(at, r, 0)
    load = v["crater_load"] and len(area.chunks(box)) <= world.MAX_FORCELOAD
    if v["crater_load"] and not load:
        mc.log("crater r=%d: too big to load first, only the loaded part is carved" % r)
    if load:
        area.forceload(box, dim=dim, wait=10.0)
    try:
        n, loaded = 0, {}
        for p in path if v["crater_tunnel"] else ():    # the tunnel: a cube round each place
            n += area.fill(area.box(p, v["crater_tunnel"]), "air", dim=dim, spare_protected=spare,
                           keep_bedrock=True, loaded=loaded)
        n += area.sphere(at, r, "air", spare_protected=spare, keep_bedrock=True, dim=dim)
        if leave != "air":
            # Lava fills the lowest third; fire is put everywhere below the middle and stays
            # only where it can stand: on the floor, and against what burns.
            top = int(math.floor(at[1])) - (r - max(1, r // 3) if leave == "lava" else 0)
            for b in area.sphere_boxes(at, r):
                if b[1] <= top:
                    n += area.fill((b[0], b[1], b[2], b[3], min(b[4], top), b[5]), leave, "replace air", dim=dim,
                                   spare_protected=spare, keep_bedrock=True, loaded=loaded)
        mc.log("crater r=%d at %d %d %d: %d blocks" % ((r,) + tuple(int(math.floor(c)) for c in at[:3]) + (n,)))
        return n
    finally:
        if load:
            area.release(box)


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


# --- preset: beam_clash (copied from _presets/beam_clash.py) ---
def beam_clash(shooter, ray, v):
    """Call it as a beam fires (ray: the kit's aim.Ray), before the beam hurts anything.
    None: it met no other beam, so fire it. (won, at): it clashed, the clash is over and has
    exploded at `at` (a Pos); won is True for the shooter who pushed it there.

    Every beam on the box is a record in one shared file in the run directory, so two
    instances of one script (galick-steve, galick-alex) or two scripts clash alike. A beam
    aimed at another player waits up to beam_clash_window s for an answer (every beam does
    with beam_clash_aimed off); a beam that passes within beam_clash_gap blocks of a waiting
    one clashes with it. For up to beam_clash_seconds both beams then run from their
    shooters' eyes to the point where they met, which moves toward the weaker: its
    beam_clash_power, doubled by holding right-click and more by mashing it. When the point
    reaches a shooter, or time is up, it explodes: beam_clash_blast, 0 for a show only."""
    me = "%s/%s" % (mc.INSTANCE, shooter)
    aimed = not v["beam_clash_aimed"] or _beam_clash_aimed(shooter, ray, v)
    wait = v["beam_clash_window"] if aimed else 0.0
    rec = {"shooter": shooter, "eyes": list(ray.start[:3]), "end": list(ray.end[:3]), "dim": ray.dim,
           "particle": str(v["beam_clash_particle"]), "push": float(v["beam_clash_power"]),
           "t": time.time(), "until": time.time() + wait}
    while True:
        last = time.time() >= rec["until"] or mc.stopping()
        role = _beam_clash_db(lambda db: _beam_clash_meet(db, me, rec, v["beam_clash_gap"], last))
        if role or last:
            break
        time.sleep(0.1)
    if not role:
        return None
    if role[0] == "lead":
        return _beam_clash_lead(me, role[1], v)
    return _beam_clash_follow(me, role[1], rec, v)


def _beam_clash_db(fn):
    """fn(beams) while holding the lock on the file every beam script on the box shares,
    which is written back after it; returns what fn returns. Beams a minute old are dropped."""
    path = os.path.join(mc.run_dir(), "beam_clash.json")
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)              # let go when the file closes
        try:
            with open(path, encoding="utf-8") as f:
                db = json.load(f)
        except (OSError, ValueError):
            db = {}
        db = {k: r for k, r in db.items() if time.time() - r.get("t", 0) < 60}
        out = fn(db)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(db, f)
        os.replace(path + ".tmp", path)
        return out


def _beam_clash_meet(db, me, rec, gap, last):
    """This beam into the shared file; ("lead", theirs) when it claims a waiting beam it
    meets, ("follow", theirs) when one has claimed it, else None (and on the last look it
    is taken out again, so nothing can claim it once it has fired)."""
    mine = db.setdefault(me, dict(rec))
    if mine.get("lead"):
        return "follow", mine["lead"]
    for k, o in db.items():
        if (k == me or o["shooter"] == rec["shooter"] or o["dim"] != rec["dim"] or o.get("lead")
                or o.get("clash") or o["until"] < time.time()):
            continue
        d, c1, c2 = _beam_clash_closest(rec["eyes"], rec["end"], o["eyes"], o["end"])
        if d <= gap:
            o["lead"] = me
            mine.update(until=0, clash={"with": k, "at": [(c1[i] + c2[i]) / 2 for i in range(3)], "over": False})
            return "lead", k
    if last:
        db.pop(me, None)
    return None


def _beam_clash_lead(me, other, v):
    """The struggle, run by the beam that claimed the other: both beams drawn, the point
    moved, and the explosion. (won, at), or None if the other beam is gone."""
    a, b = _beam_clash_db(lambda db: (dict(db.get(me, {})), dict(db.get(other, {}))))
    if "clash" not in a or "eyes" not in b:
        return None
    ea, eb, dim = a["eyes"], b["eyes"], a["dim"]
    line = [eb[i] - ea[i] for i in range(3)]
    far = math.sqrt(sum(x * x for x in line)) or 1.0
    met = a["clash"]["at"]
    # How far along from my eyes (0) to theirs (1) the beams met: halfway when head on.
    u = start = min(0.8, max(0.2, sum((met[i] - ea[i]) * line[i] for i in range(3)) / (far * far)))
    if _beam_clash_cos(a, b) < -0.8:
        u = start = 0.5
    at = world.Pos(ea[0] + line[0] * u, ea[1] + line[1] * u, ea[2] + line[2] * u, dim)
    mc.log("beam clash:", a["shooter"], "vs", b["shooter"], "at %.0f %.0f %.0f" % at[:3])
    fx.sound("entity.warden.sonic_boom", at, volume=6, pitch=0.6)
    clicks, recent, loser = items.Stat(*items.CLICK), [], None
    t0 = tick = time.time()
    while time.time() - t0 < v["beam_clash_seconds"] and not mc.stopping():
        push = _beam_clash_push(clicks, a["shooter"], recent, v)

        def swap(db):
            if me in db:
                db[me]["push"] = push
            return db.get(other, {}).get("push", b["push"])
        theirs = _beam_clash_db(swap)
        dt, tick = time.time() - tick, time.time()
        u += dt * 2.0 / v["beam_clash_seconds"] * (push - theirs) / (push + theirs) + random.uniform(-0.01, 0.01)
        at = world.Pos(ea[0] + line[0] * u, ea[1] + line[1] * u, ea[2] + line[2] * u, dim)
        _beam_clash_draw(a, b, at, u)
        if u * far >= far - 2:
            loser = other
            break
        if u * far <= 2:
            loser = me
            break
        time.sleep(0.15)
    if mc.stopping():
        return None
    if loser is None:
        loser = other if u > start else me if u < start else random.choice([me, other])

    def over(db):
        if me in db:
            db[me]["clash"].update(over=True, loser=loser, at=list(at))
    _beam_clash_db(over)
    _beam_clash_boom(at, v)
    mc.log("beam clash won by", b["shooter"] if loser == me else a["shooter"])
    return loser != me, at


def _beam_clash_follow(me, lead, rec, v):
    """The claimed beam's side: its shooter's push into the file until the clash is over."""
    clicks, recent = items.Stat(*items.CLICK), []
    end = time.time() + v["beam_clash_seconds"] + 5
    while time.time() < end and not mc.stopping():
        push = _beam_clash_push(clicks, rec["shooter"], recent, v)

        def look(db):
            if me in db:
                db[me]["push"] = push
            return dict(db.get(lead, {}).get("clash") or {})
        clash = _beam_clash_db(look)
        if clash.get("over"):
            return clash["loser"] != me, world.Pos(clash["at"][0], clash["at"][1], clash["at"][2], rec["dim"])
        time.sleep(0.15)
    return None


def _beam_clash_push(clicks, shooter, recent, v):
    """beam_clash_power, doubled by holding right-click (about 5 uses a second), more by mashing."""
    now = time.time()
    recent.append((now, clicks.changes("if entity @s[name=%s]" % shooter).get(shooter, 0)))
    recent[:] = [(t, n) for t, n in recent if now - t <= 1.0]
    return v["beam_clash_power"] * (1 + sum(n for _, n in recent) / 5.0)


def _beam_clash_draw(a, b, at, u):
    """Both beams to the point, the point itself, and how it stands for each shooter."""
    for r in (a, b):
        fx.line(world.Pos(r["eyes"][0], r["eyes"][1] - 0.3, r["eyes"][2], at.dim), at, r["particle"],
                every=1.5, spread=0.25, count=3)
    fx.particle("end_rod", at, spread=0.6, count=12, speed=0.15)
    fx.particle("electric_spark", at, spread=0.8, count=10, speed=0.4)
    if random.random() < 0.2:
        fx.sound("entity.warden.sonic_boom", at, volume=3, pitch=random.uniform(0.5, 0.9))
    for r, k in ((a, u), (b, 1 - u)):
        n = max(0, min(10, int(k * 10)))
        fx.actionbar(r["shooter"], "CLASH! right-click to push  " + "■" * n + "□" * (10 - n), "light_purple")


def _beam_clash_boom(at, v):
    fx.particle("flash{color:[1.0,1.0,1.0,1.0]}", at, count=3)
    fx.particle("explosion_emitter", at, spread=2, count=3)
    fx.particle("end_rod", at, spread=4, count=150, speed=0.4)
    fx.sound("entity.generic.explode", at, volume=8, pitch=0.6)
    fx.sound("entity.warden.sonic_boom", at, volume=8, pitch=0.7)
    if v["beam_clash_blast"]:
        mc.rcon(world.run(at.dim, 'summon creeper %.2f %.2f %.2f {Fuse:0s,ignited:1b,ExplosionRadius:%db,'
                                  'Tags:["vfg","%s"]}' % (at[0], at[1], at[2], v["beam_clash_blast"], mc.TAG)))


def _beam_clash_aimed(shooter, ray, v):
    """True if another player stands within beam_clash_gap of the beam."""
    for p in loop.online():
        at = mc.pos(p) if p != shooter and world.dim_of(p) == ray.dim else None
        body = at and (at[0], at[1] + 1, at[2])
        if body and _beam_clash_closest(ray.start, ray.end, body, body)[0] <= v["beam_clash_gap"] + 1:
            return True
    return False


def _beam_clash_cos(a, b):
    """The cosine of the angle between two beams: -1 when they point straight at each other."""
    da, db = ([r["end"][i] - r["eyes"][i] for i in range(3)] for r in (a, b))
    n = math.sqrt(sum(x * x for x in da) * sum(x * x for x in db)) or 1.0
    return sum(x * y for x, y in zip(da, db)) / n


def _beam_clash_closest(p1, q1, p2, q2):
    """How close two segments p1-q1 and p2-q2 come (either may be a point): (distance, the
    point on the first, the point on the second)."""
    def dot(x, y):
        return sum(x[i] * y[i] for i in range(3))

    def clamp(x):
        return min(1.0, max(0.0, x))
    d1, d2, r = [q1[i] - p1[i] for i in range(3)], [q2[i] - p2[i] for i in range(3)], [p1[i] - p2[i] for i in range(3)]
    a, e, f, c, b = dot(d1, d1), dot(d2, d2), dot(d2, r), dot(d1, r), dot(d1, d2)
    if a < 1e-9 and e < 1e-9:
        s = t = 0.0
    elif a < 1e-9:
        s, t = 0.0, clamp(f / e)
    elif e < 1e-9:
        s, t = clamp(-c / a), 0.0
    else:
        den = a * e - b * b
        s = clamp((b * f - c * e) / den) if den > 1e-9 else 0.0
        t = (b * s + f) / e
        if t < 0:
            s, t = clamp(-c / a), 0.0
        elif t > 1:
            s, t = clamp((b - c) / a), 1.0
    c1, c2 = [p1[i] + d1[i] * s for i in range(3)], [p2[i] + d2[i] * t for i in range(3)]
    return math.sqrt(sum((c1[i] - c2[i]) ** 2 for i in range(3))), c1, c2


v = values()
busy, ready, quiet = set(), {}, {}      # shots under way; when each player may fire again; when their tune ends


def fire(p):
    """One shot: the tune and the charge, then the beam, or a clash if another beam meets it."""
    try:
        if time.time() >= quiet.get(p, 0):
            quiet[p] = time.time() + theme_music(p, v)
        if charge_up(p, v) is None:
            return
        r = aim.ray(p, v["length"], blocks=not v["through_walls"])
        if r is None or beam_clash(p, r, v) is not None:
            return                                      # the clash was the shot
        fx.sound("entity.warden.sonic_boom", p, volume=6)
        fx.sound("entity.lightning_bolt.thunder", p, volume=6, pitch=1.5)
        fx.line(r.start.moved(0, -0.3, 0), r.end, v["beam_particle"], every=1.5, spread=0.45, count=10)
        fx.line(r.start.moved(0, -0.3, 0), r.end, "witch", every=3, spread=0.4, count=3)
        hits = aim.near_line(r, v["width"] / 2.0, shooter=p, with_players=v["damage_what"] in ("all", "players"))
        n = damage_hits(hits, p, v)
        loop.background(crater, r.end, v, path=r.every(2)[1:])     # crater_radius 0: no blocks broken
        explosion(r.end, p, v)                                  # explosion_power 0: no blast
        mc.log("%s fired: %d hit, %.0f blocks (%s)" % (p, n, r.length, r.why))
    finally:
        ready[p] = time.time() + v["cooldown"]
        busy.discard(p)


result("%s has the %s: hold right-click to charge and fire" % (v["player"], v["item"]), player=v["player"],
       item=v["item"])
for p in loop(items.holds(v["player"], v["item"], color="dark_purple")):
    if p in busy:
        continue
    if time.time() < ready.get(p, 0):
        fx.actionbar(p, "%s: %.1f s" % (v["item"], ready[p] - time.time()), "gray")
        continue
    busy.add(p)
    loop.background(fire, p)        # each shot on its own, so a clash or another player never waits
