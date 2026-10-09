"""Showing things: a particle, a line of them, a sound, a title, an actionbar, and a melody
played in the background. Each is for everyone, or for one player only with to="Steve".

    fx.line(r.start, r.end, "dust{color:[0.6,0.1,0.9],scale:2.0}", every=1.5, spread=0.3, count=4)
    fx.sound("entity.generic.explode", r.end, volume=4, pitch=0.6)
    fx.title(p, "SUPER SAIYAN", sub="blue", color="aqua")
    fx.melody([(0, "pling", 12, 3), (0.2, "bell", 16, 3), (0.4, "block.beacon.activate", 12, 2)], p)

`at` is a place (a Pos or (x, y, z)) or a player's name, which means where they stand.
A melody note is (seconds from the start, a note block instrument or a sound id, a note from
0 to 24 where 12 is the sound's own pitch, volume). One melody plays per key: a newer one
with the same key cuts the older one off.
"""
import json
import threading
import time

import mc
from . import world


def _at(at, up=0.0):
    """"execute ... run " that runs a command at a place or a player's feet, up blocks higher."""
    if isinstance(at, str):
        return "execute at %s positioned ~ ~%g ~ run " % (at, up) if up else "execute at %s run " % at
    return world.prefix(world.dim(at)) + "positioned %.2f %.2f %.2f run " % (at[0], at[1] + up, at[2])


def particle(name, at, spread=0.0, count=1, speed=0.0, to=None, up=0.0):
    """Particles at a place or a player (up blocks higher), spread that far each way, seen
    from far off (force); to="Steve" shows them to him only."""
    s = spread if isinstance(spread, (tuple, list)) else (spread, spread, spread)
    mc.rcon("%sparticle %s ~ ~ ~ %g %g %g %g %d force %s" % (_at(at, up), name, s[0], s[1], s[2], speed, count, to or "@a"))


def line(a, b, name, every=1.0, spread=0.0, count=1, speed=0.0, to=None):
    """Particles along a straight line from a to b, one command every `every` blocks."""
    d = [b[i] - a[i] for i in range(3)]
    length = sum(v * v for v in d) ** 0.5
    n = max(1, int(length / max(0.1, every)))
    dim = world.dim(a)
    for i in range(n + 1):
        if mc.stopping():
            return
        k = i / float(n)
        particle(name, world.Pos(a[0] + d[0] * k, a[1] + d[1] * k, a[2] + d[2] * k, dim), spread, count, speed, to)


def sound(name, at, to=None, volume=1.0, pitch=1.0, source="master"):
    """A sound at a place or a player, heard by everyone in range (16 blocks times the
    volume, when it is over 1); to="Steve" plays it to him only, at him."""
    name = name if ":" in name else "minecraft:" + name
    if to:
        mc.rcon("execute as %s at @s run playsound %s %s @s ~ ~ ~ %g %g" % (to, name, source, volume, pitch))
        return
    mc.rcon("%splaysound %s %s @a ~ ~ ~ %g %g" % (_at(at), name, source, volume, pitch))


def title(to, text, sub=None, color="white", fade=(10, 40, 10)):
    """A title across the screen of `to` (a player, or "@a"), with a subtitle under it."""
    mc.rcon("title %s times %d %d %d" % ((to,) + tuple(fade)))
    if sub:
        mc.rcon("title %s subtitle %s" % (to, json.dumps({"text": sub, "color": "gray"})))
    mc.rcon("title %s title %s" % (to, json.dumps({"text": text, "color": color, "bold": True})))


def actionbar(to, text, color="white"):
    """A line above the hotbar of `to` (a player, or "@a")."""
    mc.rcon("title %s actionbar %s" % (to, json.dumps({"text": text, "color": color})))


def pitch(note):
    """The playsound pitch of a note from 0 to 24 (12 is the sound's own pitch)."""
    return max(0.5, min(2.0, 2 ** ((note - 12) / 12.0)))


_tunes = {}
_lock = threading.Lock()


def melody(notes, at, to=None, key="theme", reach=48):
    """Play notes [(seconds, instrument or sound id, note 0-24, volume)] in the background
    at a player or a place, heard within `reach` blocks, or by `to` only. A newer melody with
    the same key cuts this one off. Returns at once."""
    with _lock:
        gen = _tunes.get(key, 0) + 1
        _tunes[key] = gen

    def play():
        t0 = time.time()
        for at_s, inst, note, vol in sorted(notes, key=lambda n: n[0]):
            wait = t0 + at_s - time.time()
            if wait > 0:
                time.sleep(wait)
            if _tunes.get(key) != gen or mc.stopping():
                return
            sid = inst if "." in inst else "block.note_block." + inst
            sid = sid if ":" in sid else "minecraft:" + sid
            if to:
                mc.rcon("execute as %s at @s run playsound %s master @s ~ ~ ~ %g %.3f" % (to, sid, vol, pitch(note)))
            else:
                mc.rcon("%splaysound %s master @a[distance=..%d] ~ ~ ~ %g %.3f" % (_at(at), sid, reach, vol, pitch(note)))

    th = threading.Thread(target=play, daemon=True)
    th.start()
    return th


def stop_melody(key="theme"):
    """Cut off the melody playing under a key."""
    with _lock:
        _tunes[key] = _tunes.get(key, 0) + 1
