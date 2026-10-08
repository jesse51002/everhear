#!/usr/bin/env python3
# Zeus: every trident the player throws calls lightning where it lands or hits, in any weather.
import re
import sys
import time

from mc import rcon, players, on_stop, log, TAG

if len(sys.argv) < 2:
    log("usage: zeus <player>")
    sys.exit(2)
P = sys.argv[1]
FLYING, DONE = TAG + "_t", TAG + "_done"  # never TAG itself: the tridents are the player's
POS = re.compile(r"\[(-?[\d.]+)d, (-?[\d.]+)d, (-?[\d.]+)d\]")


def positions(cmd):
    return [tuple(map(float, m.groups())) for m in POS.finditer(rcon(cmd))]


def strike(x, y, z):
    rcon("summon lightning_bolt %.1f %.1f %.1f" % (x, y, z))


def cleanup():
    rcon("tag @e[type=trident,tag=%s] remove %s" % (FLYING, FLYING))
    rcon("tag @e[type=trident,tag=%s] remove %s" % (DONE, DONE))


on_stop(cleanup)
log("tridents of", P, "call lightning")
tracked, online, checked = [], False, 0.0
while True:
    if time.time() - checked >= 1:
        online, checked = P in players(), time.time()
    if not online:
        tracked = []
        time.sleep(1)
        continue
    rcon("execute at %s as @e[type=trident,distance=..4,tag=!%s,tag=!%s] run tag @s add %s" % (P, FLYING, DONE, FLYING))
    now = positions("execute as @e[type=trident,tag=%s] run data get entity @s Pos" % FLYING)
    landed = positions("execute as @e[type=trident,tag=%s,nbt={inGround:1b}] run data get entity @s Pos" % FLYING)
    for x, y, z in landed:
        strike(x, y, z)
    rcon("tag @e[type=trident,tag=%s,nbt={inGround:1b}] add %s" % (FLYING, DONE))
    rcon("tag @e[type=trident,tag=%s] remove %s" % (DONE, FLYING))
    if len(now) < len(tracked):  # a trident vanished mid-air: it hit something
        for x, y, z in tracked[len(now):]:
            strike(x, y, z)
    tracked = [p for p in now if p not in landed]
    rcon("execute at %s as @e[type=trident,tag=%s,distance=..2] run tag @s remove %s" % (P, DONE, DONE))
    time.sleep(0.1)
