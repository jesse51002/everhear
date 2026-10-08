#!/usr/bin/env python3
# Sneaky creepers: creepers turn invisible near players, but get a longer (3 s) fuse and a
# loud hiss so players can react.
import time

from mc import rcon, players, on_stop, log, TAG

FUSE = TAG + "_fuse"  # on the world's own creepers, so never TAG itself


def cleanup():
    rcon("execute as @e[type=creeper,tag=%s] run data merge entity @s {Fuse:30s}" % FUSE)
    rcon("tag @e[type=creeper,tag=%s] remove %s" % (FUSE, FUSE))


on_stop(cleanup)
log("creepers go sneaky")
while True:
    ps = players()
    if not ps:
        time.sleep(2)
        continue
    rcon("execute as @e[type=creeper,tag=!%s] run data merge entity @s {Fuse:60s}" % FUSE)
    rcon("tag @e[type=creeper,tag=!%s] add %s" % (FUSE, FUSE))
    for p in ps:
        rcon("execute at %s run effect give @e[type=creeper,distance=..10] invisibility 3 0 true" % p)
        rcon("execute at %s as @e[type=creeper,distance=..4] at @s run playsound entity.creeper.primed hostile @a ~ ~ ~ 4 0.8" % p)
    time.sleep(1)
