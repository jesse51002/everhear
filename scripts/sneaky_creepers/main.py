"""Sneaky Creepers: creepers turn invisible near players, but get a longer fuse and a loud hiss so players can react."""
ASKS = ["invisible creepers", "sneaky creepers you can't see coming", "creepers that hiss before they blow"]
import mc
from kit import values, loop

VALUES = {
    "player":           {"type": "player", "default": "@a"},
    "invisible_within": {"type": "number", "min": 1, "max": 64, "default": 10, "live": True},
    "hiss_within":      {"type": "number", "min": 0, "max": 32, "default": 4, "live": True},
    "fuse":             {"type": "number", "min": 1.5, "max": 10, "default": 3, "live": True},
}

FUSE = mc.TAG + "_fuse"  # on the world's own creepers, so never TAG itself


def cleanup():
    mc.rcon("execute as @e[type=creeper,tag=%s] run data merge entity @s {Fuse:30s}" % FUSE)
    mc.rcon("tag @e[type=creeper,tag=%s] remove %s" % (FUSE, FUSE))


v = values(VALUES)
mc.on_stop(cleanup)
mc.log("creepers go sneaky")
for _ in loop(every=1):
    # Every creeper not yet seen gets the longer fuse (a new fuse reaches only creepers seen after it).
    mc.rcon("execute as @e[type=creeper,tag=!%s] run data merge entity @s {Fuse:%ds}" % (FUSE, int(v["fuse"] * 20)))
    mc.rcon("tag @e[type=creeper,tag=!%s] add %s" % (FUSE, FUSE))
    for p in loop.who(v["player"]):
        mc.rcon("execute at %s run effect give @e[type=creeper,distance=..%g] invisibility 3 0 true"
                % (p, v["invisible_within"]))
        if v["hiss_within"]:
            mc.rcon("execute at %s as @e[type=creeper,distance=..%g] at @s run playsound entity.creeper.primed "
                    "hostile @a ~ ~ ~ 4 0.8" % (p, v["hiss_within"]))
