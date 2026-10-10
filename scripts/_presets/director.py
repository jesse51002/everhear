"""Random scares or visits on a timer, for each player or one at random, picked by weight: a sound behind them, a shadow watching, a fake creeper, a whisper, the dark, or the script's own events (horror mode, a god)."""
import itertools
import json
import random
import time

import mc
from kit import aim, loop, world
from actions._types import Field, Live, Values  # the values class: read on the PC only

VALUES = {
    "director_events":   {"type": "list", "of": "str", "default": ["sound", "shadow", "mob", "message", "dark"],
                          "live": True},
    "director_weights":  {"type": "list", "of": "number", "default": [4, 1, 1, 2, 1], "live": True},
    "director_min_s":    {"type": "number", "min": 1, "max": 3600, "default": 25, "live": True},
    "director_max_s":    {"type": "number", "min": 1, "max": 3600, "default": 60, "live": True},
    "director_messages": {"type": "list", "of": "str", "live": True,
                          "default": ["i see you", "turn around", "you are not alone", "why are you still here",
                                      "behind you"]},
    "director_each":     {"type": "bool", "default": True, "live": True},
}


class Director(Values):
    director_events: Live[list[str]] = ["sound", "shadow", "mob", "message", "dark"]
    """what it picks from: sound (a noise behind them), shadow (a figure watching), mob (a fake creeper), message, dark, the script's own"""
    director_weights: Live[list[float]] = [4, 1, 1, 2, 1]
    """how often each event comes, in director_events' order (4 is four times as often as 1, 0 never; missing: 1)"""
    director_min_s: Live[float] = Field(25, ge=1, le=3600)
    """the shortest wait before an event, in seconds the player is online"""
    director_max_s: Live[float] = Field(60, ge=1, le=3600)
    """the longest wait before an event, in seconds (under director_min_s: director_min_s)"""
    director_messages: Live[list[str]] = ["i see you", "turn around", "you are not alone", "why are you still here",
                                          "behind you"]
    """what the message event whispers, one at random"""
    director_each: Live[bool] = True
    """each player gets events on their own clock; off: one clock for all, each event to one player at random"""


def director(player, v, *, events=None):
    """Until the script stops: for each player the player value names, every director_min_s
    to director_max_s s of them being online, one of director_events, picked by director_weights
    (the same order; a missing weight is 1), run in the background so no one waits for it.
    director_each off: one clock for everyone, and its event goes to one of them at random
    (as often with three players on as with one). events: {name: f(p, v)}, the script's own,
    beside the built-in sound, shadow, mob, message and dark (a name of its own replaces a
    built-in one)."""
    table = {"sound": _director_sound, "shadow": _director_shadow, "mob": _director_mob,
             "message": _director_message, "dark": _director_dark}
    table.update(events or {})
    mc.on_stop(lambda: [mc.rcon("effect clear %s darkness" % p) for p in sorted(_DIRECTOR_DARK)])
    due = {}
    for _ in loop(every=1):
        now, names = time.time(), loop.who(player)
        clocks = names if v["director_each"] else (["*"] if names else [])
        due = {k: t for k, t in due.items() if k in clocks}    # a clock runs only while they are on
        for k in clocks:
            if now < due.setdefault(k, now + _director_wait(v)):
                continue
            due[k] = now + _director_wait(v)
            p = random.choice(names) if k == "*" else k
            name = _director_pick(v)
            if name in table:
                mc.log("event", name, "for", p)
                loop.background(table[name], p, v)
            elif name:
                mc.log("no event called", name)


# Sounds played to one player only, from a few blocks behind them.
_DIRECTOR_SOUNDS = ["entity.zombie.step", "block.wooden_door.open", "ambient.cave", "entity.enderman.stare",
                    "entity.warden.heartbeat", "entity.ghast.ambient"]
_DIRECTOR_IDS = itertools.count(1)
_DIRECTOR_DARK = set()       # who the dark has fallen on: it lifts when the script stops


def _director_wait(v):
    lo = v["director_min_s"]
    return random.uniform(lo, max(lo, v["director_max_s"]))


def _director_pick(v):
    """One event name, by weight; None when every weight is 0."""
    names = list(v["director_events"])
    weights = [max(0.0, w) for w in list(v["director_weights"]) + [1] * len(names)][:len(names)]
    return random.choices(names, weights)[0] if sum(weights) > 0 else None


def _director_behind(p, k):
    """(the place k blocks behind a player, level with their feet, their yaw), or (None, 0)."""
    lk = aim.look(p)
    if lk is None:
        return None, 0.0
    d = aim.direction(lk.yaw, 0)
    return lk.feet.moved(-d[0] * k, 0, -d[2] * k), lk.yaw


def _director_room(at):
    """True if there is room to stand at a place: its block and the one above are open."""
    x, y, z = at.block()
    return all(world.passes(["if block %d %d %d %s" % (x, y + i, z, aim.OPEN) for i in (0, 1)], at.dim))


def _director_hear(p, sound, at, volume=1.0, pitch=0.7):
    """A sound that only p hears, coming from a place."""
    mc.rcon(world.run(at.dim, "playsound minecraft:%s ambient %s %.2f %.2f %.2f %g %g" % (
        sound, p, at[0], at[1], at[2], volume, pitch)))


def _director_sound(p, v):
    at, _ = _director_behind(p, 3)
    if at:
        _director_hear(p, random.choice(_DIRECTOR_SOUNDS), at, 1.0, random.uniform(0.5, 0.8))


def _director_shadow(p, v):
    """A dark figure 12 blocks behind them (nearer if that is in a wall), facing them, for a few seconds."""
    for k in (12, 8, 5):
        at, yaw = _director_behind(p, k)
        if at and _director_room(at):
            break
    else:
        return
    tag = "%s_shadow%d" % (mc.TAG, next(_DIRECTOR_IDS))
    head = '{id:"player_head",count:1,components:{"minecraft:profile":{name:"Herobrine"}}}'
    chest = '{id:"leather_chestplate",count:1,components:{"minecraft:dyed_color":3381708}}'
    legs = '{id:"leather_leggings",count:1,components:{"minecraft:dyed_color":3355545}}'
    mc.rcon(world.run(at.dim, 'summon armor_stand %.2f %.2f %.2f {Tags:["vfg","%s","%s"],Invulnerable:1b,'
                              'NoGravity:1b,ShowArms:1b,NoBasePlate:1b,Rotation:[%.1ff,0f],'
                              'equipment:{head:%s,chest:%s,legs:%s}}'
                      % (at[0], at[1], at[2], mc.TAG, tag, yaw, head, chest, legs)))
    _director_hear(p, "entity.warden.heartbeat", at, 1.0, 0.6)
    loop.wait(random.uniform(3, 6))
    mc.rcon("kill @e[tag=%s]" % tag)


def _director_mob(p, v):
    """A creeper right behind them that hisses, does nothing, and is gone."""
    at, yaw = _director_behind(p, 2)
    if not at or not _director_room(at):
        return
    tag = "%s_mob%d" % (mc.TAG, next(_DIRECTOR_IDS))
    mc.rcon(world.run(at.dim, 'summon creeper %.2f %.2f %.2f {Tags:["vfg","%s","%s"],NoAI:1b,Invulnerable:1b,'
                              'Silent:1b,ExplosionRadius:0b,Rotation:[%.1ff,0f]}' % (at[0], at[1], at[2], mc.TAG, tag, yaw)))
    _director_hear(p, "entity.creeper.primed", at, 1.0, 1.0)
    loop.wait(1.5)
    mc.rcon("execute at @e[tag=%s] run particle poof ~ ~1 ~ 0.3 0.6 0.3 0.02 20" % tag)
    mc.rcon("kill @e[tag=%s]" % tag)


def _director_message(p, v):
    if v["director_messages"]:
        text = random.choice(v["director_messages"])
        mc.rcon("tellraw %s %s" % (p, json.dumps({"text": text, "color": "dark_red", "italic": True})))


def _director_dark(p, v):
    _DIRECTOR_DARK.add(p)
    mc.rcon("effect give %s darkness 8 0 true" % p)
    at, _ = _director_behind(p, 2)
    if at:
        _director_hear(p, "entity.warden.heartbeat", at, 1.0, 0.5)
