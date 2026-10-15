"""Showing things (the kit, DESIGN2 §5 "Show"): a line of particles (`beam_line`), a charge-up
frame (`charge_frames`), a tune played note by note (`melody`, with the run's `TUNES`), a
countdown in chat and titles (`countdown`), a narrator's line (`speak`) and something seen flying
from one point to another (`fly`: an orb, a block, or a meteor of blocks).

`melody`, `countdown` and `fly` take time: they schedule their steps on the kit's timers
(`kit_step`). A tune also remembers which one plays under its key, in the kit's memory, so a
newer one cuts it off.
"""

from __future__ import annotations

import math
import random

from ._base import (add, call_back, dim_of, flat_facing, later, lerp, memory, mul, plain, pos_of, step, sub,
                    unit, where)
from ._base import length as vlen

MOST_POINTS = 200                   # particles a beam draws, at most (a long one spaces them out)
STEP_S = 0.1                        # a flight moves one step a tick (10 a second)
NEXT_S = 0.05                       # the next step's timer: under a tick, so it comes at the next one
MOST_STEPS = 400                    # a flight takes at most this many steps (40 s)
COLORS = {"black": "0", "dark_blue": "1", "dark_green": "2", "dark_aqua": "3", "dark_red": "4",
          "dark_purple": "5", "gold": "6", "gray": "7", "dark_gray": "8", "blue": "9", "green": "a",
          "aqua": "b", "red": "c", "light_purple": "d", "yellow": "e", "white": "f"}


# ---- a beam -------------------------------------------------------------------------------

def beam_line(self, ctx, a, b, particle: str = "minecraft:end_rod", every: float = 1.0, spread: float = 0.0,
              count: int = 1, speed: float = 0.0, force: bool = True, to=None, dim: str | None = None) -> int:
    """Particles along the straight line from `a` to `b`, one every `every` blocks (at most 200
    along it: a longer line spaces them out), `spread` each way round the line. `force`: seen
    from far off (a beam is long). `to`: only these players see it. Returns how many points."""
    start, d = where(ctx, a, dim)
    end = where(ctx, b, d)[0]
    span = vlen(sub(end, start))
    n = max(1, min(MOST_POINTS, int(span / max(0.1, float(every)))))
    for i in range(n + 1):
        self.particle(particle, lerp(start, end, i / n), count=count, delta=(spread, spread, spread),
                      speed=speed, force=force, to=to, dim=d)
    return n + 1


# ---- a charge -----------------------------------------------------------------------------

def charge_frames(self, ctx, k: float, style: str = "sphere", size: float = 6.0,
                  particle: str = "minecraft:dust{color:[0.3,0.7,1.0],scale:2.5}", orbs=(), who=None) -> tuple:
    """One frame of a charge-up on the user (or `who`), `k` of the way charged (0 to 1): call it
    from on_charge each tick of an ability's charge_s. "sphere": light growing over their head
    (Spirit Bomb); "orbs": two orbs at their hands meeting three blocks ahead (Hollow Purple;
    `orbs` the right one's particle and the left's); "aura": a glow round the body (Super
    Saiyan). `size`: the sphere's radius, how far apart the orbs start, how far the aura reaches.
    The sound rises with it. Returns where the charge is (where the power then comes from)."""
    who = who if who is not None else ctx.user
    feet, d = where(ctx, who)
    k = max(0.0, min(1.0, float(k)))
    i = int(ctx.now / STEP_S)
    if style == "sphere":
        at = _sphere(self, d, feet, size, k, particle, i)
    elif style == "orbs":
        at = _orbs(self, d, who, feet, size, k, particle, list(orbs))
    elif style == "aura":
        at = _aura(self, d, feet, size, particle, i)
    else:
        raise ValueError("style is sphere, orbs or aura, not %r" % (style,))
    _charge_sound(self, d, at, style, k, i)
    return at


def _sphere(self, d, feet, size, k, dust, i):
    r = size * (0.2 + 0.8 * k)
    mid = add(feet, (0.0, size + 2.5, 0.0))
    for _ in range(4 + int(r // 2)):
        a, b = random.uniform(0, 2 * math.pi), random.uniform(-1, 1)
        s = math.sqrt(1 - b * b)
        self.particle(dust, add(mid, (r * s * math.cos(a), r * b, r * s * math.sin(a))), count=4,
                      delta=(0.15, 0.15, 0.15), force=True, dim=d)
    self.particle("minecraft:end_rod", mid, count=6, delta=(r * 0.4,) * 3, speed=0.02, force=True, dim=d)
    self.particle("minecraft:end_rod", add(mid, (0.0, 26.0, 0.0)), count=12, delta=(0.3, 26.0, 0.3), force=True, dim=d)
    phase = (i * 0.2) % 1.0
    for j in range(3):                                   # light streaming in from the ground round it
        a, far = random.uniform(0, 2 * math.pi), random.uniform(8, 40)
        f = (phase + j / 3.0) % 1.0
        spot = add(mid, (far * math.cos(a) * (1 - f), (feet[1] - mid[1]) * (1 - f * f), far * math.sin(a) * (1 - f)))
        self.particle(random.choice(("minecraft:end_rod", "minecraft:glow", "minecraft:electric_spark")), spot,
                      force=True, dim=d)
    return mid


def _orbs(self, d, who, feet, size, k, dust, colors):
    try:
        eye, look = tuple(who.eye), unit(tuple(who.facing))
    except (AttributeError, LookupError, TypeError):
        eye, look = add(feet, (0.0, 1.62, 0.0)), flat_facing(who)
    f = flat_facing(who)
    right = (-f[2], 0.0, f[0])
    mid = add(add(eye, mul(look, 3.0)), (0.0, -0.3, 0.0))
    off = size / 2.0 * (1 - k)
    colors = colors[:2] if len(colors) >= 2 else [dust, dust]
    for side, flame, color in ((1, "minecraft:soul_fire_flame", colors[0]), (-1, "minecraft:flame", colors[1])):
        orb = add(mid, mul(right, off * side))
        self.particle(color, orb, count=12, delta=(0.25, 0.25, 0.25), force=True, dim=d)
        self.particle(flame, orb, count=3, delta=(0.2, 0.2, 0.2), force=True, dim=d)
    if k > 0.6:                                          # met: one orb, flaring
        self.particle(dust, mid, count=20, delta=(0.3, 0.3, 0.3), force=True, dim=d)
        self.particle("minecraft:witch", mid, count=10, delta=(0.5, 0.5, 0.5), force=True, dim=d)
    return mid


def _aura(self, d, feet, size, dust, i):
    w, h = size / 10.0, size / 7.5
    body = add(feet, (0.0, 1.0, 0.0))
    self.particle(dust, body, count=14, delta=(w, h, w), dim=d)
    self.particle("minecraft:soul_fire_flame", body, count=6, delta=(w, h, w), speed=0.02, dim=d)
    self.particle("minecraft:end_rod", add(feet, (0.0, 0.2, 0.0)), count=4, delta=(w * 0.7, 0.1, w * 0.7),
                  speed=0.08, dim=d)
    if i % 6 == 0:
        self.particle("minecraft:electric_spark", body, count=10, delta=(w * 1.5, h * 1.1, w * 1.5), speed=0.3, dim=d)
    return body


def _charge_sound(self, d, at, style, k, i):
    if style == "orbs":
        self.sound("minecraft:block.note_block.pling" if i % 2 == 0 else "minecraft:block.note_block.bell",
                   at, volume=5, pitch=0.6 + 1.4 * k, dim=d)
        self.sound("minecraft:block.amethyst_block.chime", at, volume=5, pitch=0.6 + 1.4 * k, dim=d)
    elif style == "sphere":
        if i % 4 == 0:
            self.sound("minecraft:block.beacon.ambient", at, volume=3, pitch=0.8 + 1.2 * k, dim=d)
        if i % 6 == 0:
            self.sound("minecraft:entity.breeze.idle_air", at, volume=2, pitch=0.6 + k, dim=d)
    elif i % 6 == 0:
        self.sound("minecraft:entity.firework_rocket.twinkle", at, volume=1, pitch=1.8, dim=d)


# ---- tunes --------------------------------------------------------------------------------

# Each tune is a list of notes: (seconds from the start, a note block instrument or a sound id, a
# note from 0 to 24 where 12 is the sound's own pitch, volume). The run's tunes, as its scripts
# played them (the old theme_music preset); a volume of 3 is heard 48 blocks away.
TUNES = {
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
# A record plays on in the players' game once started, so the tune lasts as long as it does.
RECORDS = {"minecraft:music_disc.pigstep": 148.0}


def note_sound(instrument: str) -> str:
    """"bell" -> minecraft:block.note_block.bell; "entity.generic.explode" -> minecraft:entity.generic.explode."""
    sid = instrument if "." in instrument else "block.note_block." + instrument
    return sid if ":" in sid else "minecraft:" + sid


def note_pitch(note: float) -> float:
    """The playsound pitch of a note from 0 to 24 (12 is the sound's own pitch)."""
    return round(max(0.5, min(2.0, 2 ** ((float(note) - 12) / 12.0))), 4)


def _where_heard(ctx, at, to):
    """What a note's step keeps of where it plays: a player's name (followed as they move) or a
    position; and who hears it: None (everyone near it), or a Players arg read at each note
    ("@a" for "everyone"; "Steve,Alex"), each hearing it where they stand."""
    if isinstance(at, str):
        place = at
    else:
        p = pos_of(ctx, at)
        place = list(p) if p is not None else None
    if to is None:
        return place, None
    if to == "everyone":
        return place, "@a"
    if isinstance(to, (list, tuple)):
        return place, ",".join(str(getattr(t, "name", t)) for t in to)
    return place, str(getattr(to, "name", to))


def melody(self, ctx, tune, at=None, to=None, key: str = "theme", loop: bool = False) -> float:
    """Play a tune note by note on the kit's timers: a name from TUNES or a list of notes
    [(seconds, instrument or sound id, note 0-24, volume)]. `at`: where (the user when None; a
    player's name follows them). `to`: None, heard round `at` (16 blocks times the volume);
    "everyone" (or "@a"), by every player where they stand; or a Players arg or names, by those
    alone, each where they stand. One tune plays per
    `key`: a newer one (or a call with tune None) cuts it off, a record too. `loop`: again each
    time it ends, until cut off. Returns how long it lasts, in seconds."""
    mem = memory(self, "melody")
    gen = int(mem.get(key, 0)) + 1
    mem[key] = gen
    for sid, hear in mem.pop("disc:" + key, []):          # a record the older tune started plays on
        self.stop_sound(sid, to=hear)
    if tune is None:
        return 0.0
    notes = TUNES.get(tune) if isinstance(tune, str) else list(tune)
    if not notes:
        raise ValueError("%r is not a tune (the tunes: %s)" % (tune, ", ".join(sorted(TUNES))))
    if at is None and ctx.user is not None:
        at = ctx.user.key if getattr(ctx.user, "is_player", False) else ctx.user
    place, hear = _where_heard(ctx, at, to)
    if place is None:
        raise ValueError("melody needs a place to play at: a scenario has no user (give at=...)")
    d = dim_of(ctx, at)
    chords: dict[float, list] = {}
    discs = []
    for t, inst, note, vol in notes:
        sid = note_sound(inst)
        chords.setdefault(round(float(t), 2), []).append([sid, note_pitch(note), float(vol)])
        if sid in RECORDS:
            discs.append(sid)
    for t, sounds in sorted(chords.items()):
        if t <= 0:                                       # the first notes now, not a tick late
            _melody_note(self, ctx, key, gen, sounds, place, hear, d)
        else:
            later(self, t, "melody_note", key, gen, sounds, place, hear, d)
    if discs:
        who = None if hear in (None, "@a") else _people(ctx, hear)
        mem["disc:" + key] = [[sid, who] for sid in discs]
    seconds = max([t + 0.5 for t in chords] + [RECORDS[s] for s in discs])
    if loop:
        later(self, seconds, "melody_again", key, gen, plain(tune), plain(place), plain(hear), d)
    return seconds


@step("melody_note")
def _melody_note(self, ctx, key, gen, sounds, place, hear, dim):
    if memory(self, "melody").get(key) != gen:
        return                                           # cut off by a newer tune
    if hear is None:
        at = pos_of(ctx, place)
        if at is None:
            return
        for sid, pitch, vol in sounds:
            self.sound(sid, at, volume=vol, pitch=pitch, dim=dim)
        return
    for name in _people(ctx, hear):
        p = ctx.world.player(name)
        if p is None:
            continue
        for sid, pitch, vol in sounds:
            self.sound(sid, p.pos, volume=vol, pitch=pitch, to=[p.name], dim=p.dim)


@step("melody_again")
def _melody_again(self, ctx, key, gen, tune, place, hear, dim):
    if memory(self, "melody").get(key) == gen:
        memory(self, "melody")[key] = gen - 1            # melody() counts it up again: the same tune goes on
        melody(self, ctx, tune, place if isinstance(place, str) else tuple(place), hear, key, loop=True)


# ---- a countdown ---------------------------------------------------------------------------

MARKS = (600, 300, 120, 60, 30, 10)


def span(seconds: float) -> str:
    """Seconds as "5m 00s" or "30s"."""
    s = int(round(seconds))
    return "%dm %02ds" % (s // 60, s % 60) if s >= 60 else "%ds" % s


def countdown(self, ctx, seconds: float, text: str = "Detonation", to="@a", then: str | None = None) -> None:
    """Count `seconds` down for `to` (a Players arg: "@a", "@r" or names): chat says "<text> in
    5m 00s." at the start and at 10 and 5 minutes, 2, 1, 30 and 10 seconds left; each of the last
    ten seconds shows a title and ticks. At zero it calls the class's own `then(ctx)`. Stopping the
    instance first calls it off (its timers go with it)."""
    seconds = float(seconds)
    if seconds <= 0:
        raise ValueError("a countdown needs seconds above 0, not %r" % seconds)
    _say(self, ctx, "%s in %s." % (text, span(seconds)), to)
    for m in MARKS:
        if m < seconds:
            later(self, seconds - m, "count_say", "%s in %s." % (text, span(m)), to)
    for n in range(min(10, int(seconds)), 0, -1):
        later(self, seconds - n, "count_tick", text, n, to)
    later(self, seconds, "count_zero", then)


def _people(ctx, to) -> list[str]:
    from ..args import players
    if isinstance(to, (list, tuple)):
        to = ",".join(str(t) for t in to)
    return players(ctx, to or "@a")


def _say(self, ctx, text, to):
    names = _people(ctx, to)
    if names:
        self.message("§c§l" + text, to=names)


@step("count_say")
def _count_say(self, ctx, text, to):
    _say(self, ctx, text, to)


@step("count_tick")
def _count_tick(self, ctx, text, n, to):
    for name in _people(ctx, to):
        p = ctx.world.player(name)
        self.message("§c§l%d" % n, to=[name], where="title")
        self.message("§7" + text, to=[name], where="subtitle")
        if p is not None:
            self.sound("minecraft:block.note_block.bass", p.pos, volume=2, pitch=0.5, to=[name], dim=p.dim)
            self.sound("minecraft:block.note_block.bit", p.pos, volume=2, pitch=2.0 if n > 3 else 1.0,
                       to=[name], dim=p.dim)


@step("count_zero")
def _count_zero(self, ctx, then):
    call_back(self, ctx, then)


# ---- a voice ------------------------------------------------------------------------------

def speak(self, ctx, text: str, voice: str = "Demon Lord", color: str = "dark_red", to=None,
          where: str = "chat") -> None:
    """A line said by a voice, in its colour: "[Demon Lord] You will not leave." `voice` as
    written when it is already in brackets ("<The Radiant One>"); "" for none. `to`: a Players arg
    or names (everyone when None). `where`: chat, actionbar, title or subtitle."""
    if color not in COLORS:
        raise ValueError("color is one of %s, not %r" % (", ".join(COLORS), color))
    name = voice if not voice or voice[0] in "[<" else "[%s]" % voice
    line = "§%s%s%s" % (COLORS[color], name + " " if name else "", text)
    names = _people(ctx, to) if to is not None else ()
    if to is not None and not names:
        return
    self.message(line, to=list(names), where=where)


# ---- something flying ------------------------------------------------------------------------

LOOKS = ("orb", "block", "meteor")
# The meteor: a 3 x 3 x 3 cluster of block displays without its corners (19).
_CLUSTER = [(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1) if x * y * z == 0]


def _display_nbt(block: str, s: float) -> str:
    # 26.3 shows a display's block from its id alone (block_state:"minecraft:magma_block"): a
    # state in it, or {Name:...}, shows air, so [...] is cut off (measured by the old kit).
    block = block.split("[")[0].split("{")[0]
    block = block if ":" in block else "minecraft:" + block
    return ('{block_state:"%s",teleport_duration:2,transformation:{translation:[%gf,%gf,%gf],'
            'left_rotation:[0f,0f,0f,1f],right_rotation:[0f,0f,0f,1f],scale:[%gf,%gf,%gf]}}'
            % (block, -s / 2, -s / 2, -s / 2, s, s, s))


def fly(self, ctx, start, end, speed: float = 30.0, look: str = "orb", size: float = 1.0,
        particle: str = "minecraft:end_rod", blocks: str = "minecraft:magma_block", then: str | None = None,
        dim: str | None = None) -> float:
    """Something seen flying in a straight line from `start` to `end` at `speed` blocks a second,
    one step a tick: "orb", a ball of `particle` `size` across; "block", a block display of
    `blocks` trailing `particle`; "meteor", a burning cluster of `blocks` (a block or a mix)
    `size` across with flame and smoke. It is a show and touches nothing; on arrival it calls the
    class's own `then(ctx, end)`, where the blast goes. Returns how long it flies, in seconds."""
    if look not in LOOKS:
        raise ValueError("look is one of %s, not %r" % (", ".join(LOOKS), look))
    a, d = where(ctx, start, dim)
    b = where(ctx, end, d)[0]
    steps = max(1, min(MOST_STEPS, int(math.ceil(vlen(sub(b, a)) / max(0.1, float(speed) * STEP_S)))))
    parts = []
    from ..args import mix, pick
    choices = mix(blocks)                       # a block, or a mix picked from per display
    if look == "block":
        block = pick(choices, "%s block" % self.instance)
        parts.append([self.spawn("minecraft:block_display", a, dim=d, nbt=_display_nbt(block, size)), [0.0, 0.0, 0.0]])
    elif look == "meteor":
        gap = float(size) / 3.0
        for ox, oy, oz in _CLUSTER:
            off = [ox * gap, oy * gap, oz * gap]
            block = pick(choices, "%s %d %d %d" % (self.instance, ox, oy, oz))
            parts.append([self.spawn("minecraft:block_display", add(a, off), dim=d,
                                     nbt=_display_nbt(block, gap * 1.35)), off])
        self.sound("minecraft:entity.ghast.scream", a, volume=6, pitch=0.6, dim=d)
    if look != "meteor":
        self.sound("minecraft:entity.wind_charge.wind_burst", a, volume=3, pitch=0.6, dim=d)
    seconds = steps * STEP_S
    later(self, NEXT_S, "fly_step", list(a), list(b), ctx.now, seconds, look, float(size), particle, parts, then, d)
    return seconds


@step("fly_step")
def _fly_step(self, ctx, a, b, t0, seconds, look, size, particle, parts, then, d):
    # where it is by the clock, not by counting steps: a timer may come a tick late, a flight may not
    k = min(1.0, (ctx.now - t0) / seconds + 1e-9) if seconds > 0 else 1.0     # 1e-9: a clock's float sums
    here = lerp(a, b, k)
    for uuid, off in parts:
        self.teleport(uuid, add(here, off), dim=d)
    if look == "orb":
        self.particle(particle, here, count=int(4 + 6 * size), delta=(size * 0.25,) * 3, speed=0.01, force=True, dim=d)
        for _ in range(4):
            u, v = random.uniform(0, 2 * math.pi), random.uniform(-1, 1)
            q = math.sqrt(1 - v * v) * size / 2
            self.particle(particle, add(here, (q * math.cos(u), v * size / 2, q * math.sin(u))), count=2,
                          force=True, dim=d)
    elif look == "block":
        self.particle(particle, here, count=3, delta=(size * 0.2,) * 3, force=True, dim=d)
    else:
        gap = size / 3.0
        self.particle("minecraft:flame", here, count=int(10 + 10 * gap), delta=(gap, gap, gap), speed=0.05,
                      force=True, dim=d)
        self.particle("minecraft:large_smoke", add(here, (0.0, gap * 1.5, 0.0)), count=int(4 + 4 * gap),
                      delta=(gap, gap, gap), speed=0.02, force=True, dim=d)
        if int((ctx.now - t0) / STEP_S) % 10 == 5:
            self.sound("minecraft:entity.blaze.shoot", here, volume=4, pitch=0.6, dim=d)
    if k < 1.0:
        later(self, NEXT_S, "fly_step", a, b, t0, seconds, look, size, particle, parts, then, d)
        return
    for uuid, _off in parts:
        self.remove(uuid)
    call_back(self, ctx, then, list(b))
