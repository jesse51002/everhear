"""Herobrine: horror pranks on the players, one at random every minute or two: footsteps, a door, a whisper, Herobrine behind them."""
ASKS = ["be like a horror mod", "turn on spooky mode", "a horror atmosphere: eerie sounds, darkness and jump scares"]
from kit import values, aim, loop, world
import itertools
import json
import random
import time
import mc

VALUES = {
    "player":            {"type": "player", "default": "@a"},
    "director_events":   {"type": "list", "of": "str", "live": True,
                          "default": ["sound", "door", "fake_join", "message", "shadow", "torches", "dark", "sign",
                                      "lightning"]},
    "director_weights":  {"type": "list", "of": "number", "default": [3, 1, 1, 1, 1, 1, 1, 1, 1], "live": True},
    "director_min_s":    {"type": "number", "min": 1, "max": 3600, "default": 60, "live": True},
    "director_max_s":    {"type": "number", "min": 1, "max": 3600, "default": 150, "live": True},
    "director_messages": {"type": "list", "of": "str", "live": True,
                          "default": ["i see you", "turn around", "you are not alone", "behind you"]},
}


# --- preset: director (copied from _presets/director.py) ---
def director(player, v, *, events=None):
    """Until the script stops: for each player the player value names, every director_min_s
    to director_max_s s of them being online, one of director_events, picked by director_weights
    (the same order; a missing weight is 1), run in the background so no one waits for it.
    events: {name: f(p, v)}, the script's own, beside the built-in sound, shadow, mob,
    message and dark (a name of its own replaces a built-in one)."""
    table = {"sound": _director_sound, "shadow": _director_shadow, "mob": _director_mob,
             "message": _director_message, "dark": _director_dark}
    table.update(events or {})
    mc.on_stop(lambda: [mc.rcon("effect clear %s darkness" % p) for p in sorted(_DIRECTOR_DARK)])
    due = {}
    for _ in loop(every=1):
        now, names = time.time(), loop.who(player)
        due = {p: t for p, t in due.items() if p in names}     # the clock runs only while they are on
        for p in names:
            if now < due.setdefault(p, now + _director_wait(v)):
                continue
            due[p] = now + _director_wait(v)
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


v = values(VALUES)


def door(p, v):
    """A door opens behind them, and shuts."""
    at, _ = _director_behind(p, 3)
    if at:
        _director_hear(p, "block.wooden_door.open", at, 1.0, 0.7)
        loop.wait(2)
        _director_hear(p, "block.wooden_door.close", at, 1.0, 0.7)


def fake_join(p, v):
    mc.rcon('tellraw @a {"text":"Herobrine joined the game","color":"yellow"}')
    loop.wait(6)
    mc.rcon('tellraw @a {"text":"Herobrine left the game","color":"yellow"}')


def whisper(p, v):
    if v["director_messages"]:
        mc.rcon("tellraw %s %s" % (p, json.dumps({"text": "<Herobrine> %s, %s" % (random.choice(v["director_messages"]), p),
                                                  "color": "white"})))


def torches(p, v):
    """Every torch within 8 blocks goes out."""
    for torch in ("torch", "wall_torch"):
        mc.rcon("execute at %s run fill ~-8 ~-3 ~-8 ~8 ~4 ~8 air replace %s" % (p, torch))


def sign(p, v):
    """A sign that says "behind you", two blocks behind them, where there is room."""
    mc.rcon("execute at %s rotated ~ 0 positioned ^ ^ ^-2 if block ~ ~ ~ air run setblock ~ ~ ~ "
            'oak_sign[rotation=0]{front_text:{messages:["","behind you","",""]}}' % p)


def far_lightning(p, v):
    """Lightning 18 blocks behind them."""
    mc.rcon('execute at %s rotated ~ 0 positioned ^ ^ ^-18 run summon lightning_bolt ~ ~ ~ {Tags:["vfg","%s"]}'
            % (p, mc.TAG))


mc.log("haunting", v["player"])
director(v["player"], v, events={"door": door, "fake_join": fake_join, "message": whisper, "torches": torches,
                                 "sign": sign, "lightning": far_lightning})
