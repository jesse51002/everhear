#!/usr/bin/env python3
# Explosive bow for one player: their arrows explode where they hit.
# radius 0 = one TNT; radius >= 10 = "nuke": a ring of secondary blasts and a mushroom cloud.
import math
import re
import sys
import time

from mc import rcon, players, on_stop, log, TAG

if len(sys.argv) < 2:
    log("usage: boom_bow <player> [radius=4]")
    sys.exit(2)
P = sys.argv[1]
try:
    RAD = max(0, min(int(sys.argv[2]), 30)) if len(sys.argv) > 2 else 4
except ValueError:
    RAD = 4
ARROW, SEEN = TAG + "_a", TAG + "_seen"  # never TAG itself: the arrows are the player's
POS = re.compile(r"\[(-?[\d.]+)d, (-?[\d.]+)d, (-?[\d.]+)d\]")
TAGS = 'Tags:["vfg","%s"]' % TAG


def positions(cmd):
    return [tuple(map(float, m.groups())) for m in POS.finditer(rcon(cmd))]


def blast(x, y, z):
    if RAD == 0:  # TNT mode: one primed TNT where the arrow lands
        rcon("summon tnt %.1f %.1f %.1f {fuse:40,%s}" % (x, y, z, TAGS))
        return
    rcon("summon creeper %.1f %.1f %.1f {powered:1b,Fuse:0s,ExplosionRadius:%db,%s}" % (x, y, z, RAD, TAGS))
    if RAD >= 10:
        for a in range(0, 360, 45):
            t = math.radians(a)
            rcon("summon creeper %.1f %.1f %.1f {powered:1b,Fuse:0s,ExplosionRadius:5b,%s}"
                 % (x + 8 * math.cos(t), y, z + 8 * math.sin(t), TAGS))
        rcon("particle explosion_emitter %.1f %.1f %.1f 3 6 3 0 20 force" % (x, y + 8, z))


def cleanup():
    rcon("tag @e[type=arrow,tag=%s] remove %s" % (ARROW, ARROW))
    rcon("tag @e[type=arrow,tag=%s] remove %s" % (SEEN, SEEN))


on_stop(cleanup)
log("arrows of", P, "explode, radius", RAD)
tracked, online, checked = [], False, 0.0
while True:
    if time.time() - checked >= 1:
        online, checked = P in players(), time.time()
    if not online:
        tracked = []
        time.sleep(1)
        continue
    rcon("execute at %s as @e[type=arrow,distance=..4,tag=!%s] run tag @s add %s" % (P, SEEN, ARROW))
    rcon("execute at %s as @e[type=arrow,distance=..4,tag=!%s] run tag @s add %s" % (P, SEEN, SEEN))
    now = positions("execute as @e[type=arrow,tag=%s] run data get entity @s Pos" % ARROW)
    landed = positions("execute as @e[type=arrow,tag=%s,nbt={inGround:1b}] run data get entity @s Pos" % ARROW)
    rcon("kill @e[type=arrow,tag=%s,nbt={inGround:1b}]" % ARROW)
    hits = landed[:]
    if len(now) < len(tracked):  # an arrow vanished mid-air: it hit an entity
        hits += tracked[len(now):]
    for x, y, z in hits:
        blast(x, y, z)
    tracked = [p for p in now if p not in landed]
    time.sleep(0.1)
