"""A charge-up animation before a power fires: a sphere of light growing overhead, two orbs meeting at the hands, or an aura round the body, for a few seconds, any colour and size."""
import math
import random
import time

from kit import aim, fx, loop
from actions._types import Field, Literal, Live, ParticleId, Values  # the values class: read on the PC only

VALUES = {
    "charge_style": {"type": "enum", "of": ["sphere", "orbs", "aura"], "default": "sphere", "live": True},
    "charge_seconds": {"type": "number", "min": 0.2, "max": 15, "default": 3, "live": True},
    "charge_particle": {"type": "particle", "default": "dust{color:[0.3,0.7,1.0],scale:2.5}", "live": True},
    "charge_size": {"type": "number", "min": 1, "max": 30, "default": 6, "live": True},
    "charge_orbs": {"type": "list", "of": "particle", "default": [], "live": True},
}


class ChargeUp(Values):
    charge_style: Live[Literal["sphere", "orbs", "aura"]] = "sphere"
    """how it looks: sphere, light growing overhead (Spirit Bomb); orbs, two meeting ahead (Hollow Purple); aura, a glow round the body"""
    charge_seconds: Live[float] = Field(3, ge=0.2, le=15)
    """how long it charges before the power fires, in seconds"""
    charge_particle: Live[ParticleId] = "dust{color:[0.3,0.7,1.0],scale:2.5}"
    """the particle it is drawn with; dust{color:[r,g,b],scale:2} for a colour, r, g and b from 0 to 1"""
    charge_size: Live[float] = Field(6, ge=1, le=30)
    """how big it gets: the sphere's radius, or how far apart the orbs start, in blocks; an aura of 30 reaches 3 blocks out"""
    charge_orbs: Live[list[ParticleId]] = []
    """for orbs: the right orb's particle and the left's, meeting in charge_particle; []: both charge_particle"""


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
