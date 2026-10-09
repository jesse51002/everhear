"""Super Saiyan: crouch three times quickly (or right-click the star) to power up in an aura with its own riff, then for a minute run fast, hit hard, jump high and fly; do it again to power down."""
ASKS = ["can I make it a super saiyan", "super saiyan blue", "super saiyan blue aura",
        "click or crouch three times", "make super saiyan blue make you fly, because I feel like super saiyan should make you fly",
        "make sure there's no cooldowns", "can you make music for each of the attacks"]
from kit import values, items, loop, aim, fx
import threading
import time
import mc
import math
import random

VALUES = {
    "player":          {"type": "player", "default": "@a"},
    "item":            {"type": "str", "default": "Super Saiyan"},
    "level":           {"type": "enum", "of": ["1", "2", "3", "blue"], "default": "blue", "live": True},
    "seconds":         {"type": "number", "min": 5, "max": 3600, "default": 60, "live": True},
    "cooldown":        {"type": "number", "min": 0, "max": 600, "default": 0, "live": True},
    "flight":          {"type": "bool", "default": True, "live": True},
    "sneak_mode":      {"type": "enum", "of": ["while", "tap", "three_taps"], "default": "three_taps", "live": True},
    "sneak_seconds":   {"type": "number", "min": 0.3, "max": 5, "default": 1.5, "live": True},
    "sneak_item":      {"type": "str", "default": "", "live": True},
    "charge_style":    {"type": "enum", "of": ["sphere", "orbs", "aura"], "default": "aura", "live": True},
    "charge_seconds":  {"type": "number", "min": 0.2, "max": 15, "default": 1.5, "live": True},
    "charge_particle": {"type": "particle", "default": "dust{color:[0.2,0.9,1.0],scale:1.4}", "live": True},
    "charge_size":     {"type": "number", "min": 1, "max": 30, "default": 5, "live": True},
    "theme_tune":      {"type": "enum", "of": ["galick_gun", "hollow_purple", "ice_beam", "spirit_bomb", "spirit_bomb_throw", "super_saiyan", "boss"], "default": "super_saiyan", "live": True},
    "theme_to":        {"type": "enum", "of": ["near", "everyone", "player"], "default": "near", "live": True},
    "theme_reach":     {"type": "int", "min": 8, "max": 256, "default": 48, "live": True},
}

# What each level gives, as effect amplifiers (0 is level I): speed, strength, jump boost.
LEVELS = {"1": (0, 0, 0), "2": (1, 1, 0), "3": (2, 2, 1), "blue": (2, 1, 1)}
FLIGHT = "vfg:super_saiyan_flight"      # the gravity modifier that lets them hover


# --- preset: sneak (copied from _presets/sneak.py) ---
def sneak(player, v):
    """Yield a player's name (of those `player` names: "@a", "@r" or names) when they sneak:
    "while" on every pass, about ten a second, as long as they crouch; "tap" once each time
    they crouch; "three_taps" when they crouch three times within sneak_seconds. With
    sneak_item set (an item's name, as items.clicks gives it) only while they hold it.
    Only crouching counts, never a click. Each player is followed on their own.

        for p in loop(sneak(v["player"], v)):
            ...
    """
    stat = items.Stat(*items.SNEAK)     # this generator's own look at the shared sneak_time objective
    down, taps, scored = {}, {}, set()  # per player: crouched at the last look; when their taps came
    while not mc.stopping():
        people = loop.who(player)
        if not people:
            stat.forget()
            down.clear()
            taps.clear()
            time.sleep(1.0)
            continue
        name = v["sneak_item"]
        hold = "if items entity @s weapon.* %s" % items.match(name) if name else ""
        got, now, mode, due = stat.changes(hold), time.time(), v["sneak_mode"], []
        if not scored.issuperset(people):
            # Everyone gets a score (adding 0 changes none): a player with none yet makes
            # items.Stat misread the name after theirs in the same reply.
            mc.rcon("scoreboard players add @a %s 0" % stat.objective)
            scored.update(people)
        for p in people:
            crouched, was = got.get(p, 0) > 0, down.get(p, False)
            down[p] = crouched
            if mode == "while":
                if crouched:
                    due.append(p)
            elif crouched and not was:                          # a tap: they went down since the last look
                if mode == "tap":
                    due.append(p)
                    continue
                recent = [t for t in taps.get(p, []) if now - t <= v["sneak_seconds"]] + [now]
                taps[p] = [] if len(recent) >= 3 else recent
                if len(recent) >= 3:
                    due.append(p)
        for p in due:
            yield p
        if due:
            stat.changes(hold)          # crouching while the loop's body ran does not count
        time.sleep(0.1)


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
def theme_music(at, v):
    """Play the tune theme_tune names, in the background, at a player (or a place), and
    return how many seconds it lasts. theme_to: "near" is heard within theme_reach blocks
    of it, "everyone" by every player where they stand, "player" by that player alone. A
    newer theme at the same player (or for everyone) cuts this one off, a record too."""
    notes = _THEME_MUSIC_TUNES[v["theme_tune"]]
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


v = values(VALUES)
on, rest, lock = {}, {}, threading.Lock()   # players in the form (each its own thread); when each may again


def form(p):
    """The form on one player until it ends or they turn it off: the riff and the charge, then
    the aura, the effects and, with flight, hovering (look up to rise, crouch to sink)."""
    theme_music(p, v)
    charge_up(p, v)
    fx.sound("entity.lightning_bolt.thunder", p, volume=0.6, pitch=1.5)
    fx.particle("end_rod", p, spread=(0.5, 1, 0.5), count=80, speed=0.3, up=1)
    crouch, end, i = items.Stat(*items.SNEAK), time.time() + v["seconds"], 0
    hovering = rising = False
    while on.get(p) and time.time() < end and loop.wait(0.2):
        lk = aim.look(p)
        if lk is None:
            break                                   # gone offline
        _charge_up_aura(p, lk, v["charge_size"], v["charge_particle"], i)
        if i % 10 == 0:
            for effect, level in zip(("speed", "strength", "jump_boost"), LEVELS[v["level"]]):
                mc.rcon("effect give %s %s 4 %d true" % (p, effect, level))
        hover = v["flight"] and not crouch.changes("if entity @s[name=%s]" % p).get(p, 0)
        if hover != hovering:
            hovering = hover
            mc.rcon("attribute %s gravity modifier %s" % (p, ("add %s -1 add_multiplied_total" if hover else "remove %s")
                                                          % FLIGHT))
        if hover and lk.pitch < -30:
            mc.rcon("effect give %s levitation 1 4 true" % p)
            rising = True
        elif rising:
            mc.rcon("effect clear %s levitation" % p)
            rising = False
        fx.actionbar(p, "Super Saiyan %s  %d s" % (v["level"].title(), end - time.time()), "aqua")
        i += 1
    power_down(p)


def power_down(p):
    """Back to normal, landing softly."""
    with lock:
        on.pop(p, None)
        rest[p] = time.time() + v["cooldown"]
    for effect in ("speed", "strength", "jump_boost", "levitation"):
        mc.rcon("effect clear %s %s" % (p, effect))
    mc.rcon("attribute %s gravity modifier remove %s" % (p, FLIGHT))
    mc.rcon("effect give %s slow_falling 5 0 true" % p)
    fx.sound("block.beacon.deactivate", p, volume=0.8)


def toggle(p):
    """Power up, or down if they are up already."""
    with lock:
        if on.get(p):
            on[p] = False                           # the form's thread sees it and powers down
            return
        if time.time() < rest.get(p, 0):
            fx.actionbar(p, "Super Saiyan: %.0f s" % (rest[p] - time.time()), "gray")
            return
        on[p] = True
    loop.background(form, p)


def star():
    for p in items.clicks(v["player"], v["item"], cooldown=1, color="aqua", model="nether_star"):
        toggle(p)


mc.on_stop(lambda: [power_down(p) for p in list(on)])
if v["item"]:
    loop.background(star)       # the star's clicks, beside the crouches
for p in loop(sneak(v["player"], v)):
    toggle(p)
