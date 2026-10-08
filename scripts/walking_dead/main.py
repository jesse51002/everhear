#!/usr/bin/env python3
# Walking Dead zombies: slow shamblers in bigger groups that hear players. Sneaking: they only
# notice you up close. Walking: normal. Sprinting: the noise draws them from far away.
import random
import time

from mc import rcon, players, score, on_stop, log, TAG

WALKER = TAG + "_w"  # on the world's own zombies, so never TAG itself
ZT = "type=#minecraft:zombies,tag=!custom"
SNEAK, SPRINT = "vfg_wd_sneak", "vfg_wd_sprint"


def cleanup():
    rcon("execute as @e[tag=%s] run attribute @s minecraft:movement_speed base reset" % WALKER)
    rcon("execute as @e[tag=%s] run attribute @s minecraft:follow_range base reset" % WALKER)
    rcon("tag @e[tag=%s] remove %s" % (WALKER, WALKER))
    rcon("scoreboard objectives remove " + SNEAK)
    rcon("scoreboard objectives remove " + SPRINT)


on_stop(cleanup)
rcon("scoreboard objectives add %s minecraft.custom:minecraft.sneak_time" % SNEAK)
rcon("scoreboard objectives add %s minecraft.custom:minecraft.sprint_one_cm" % SPRINT)
log("zombies become walkers")
last = {}
while True:
    ps = players()
    if not ps:
        time.sleep(2)
        continue
    # New zombies become walkers: slow, able to break doors, sometimes with a friend.
    rcon("execute as @e[%s,tag=!%s] run attribute @s minecraft:movement_speed base set 0.17" % (ZT, WALKER))
    rcon("execute as @e[%s,tag=!%s] run data merge entity @s {CanBreakDoors:1b}" % (ZT, WALKER))
    if random.random() < 0.3:
        rcon('execute as @e[%s,tag=!%s,limit=2,sort=random] at @s run summon zombie ~1 ~ ~1 {Tags:["vfg","%s","%s"]}'
             % (ZT, WALKER, TAG, WALKER))
    rcon("tag @e[%s,tag=!%s] add %s" % (ZT, WALKER, WALKER))
    for p in ps:
        sn, sp = score(p, SNEAK), score(p, SPRINT)
        ls = last.get(p, (sn, sp))
        last[p] = (sn, sp)
        sneak, sprint = sn > ls[0], sp > ls[1]
        rng = 5 if sneak else (40 if sprint else 16)
        rcon("execute at %s as @e[%s,distance=..48] run attribute @s minecraft:follow_range base set %d" % (p, ZT, rng))
        # The closer they get, the faster they lunge.
        rcon("execute at %s as @e[%s,distance=12..48] run attribute @s minecraft:movement_speed base set 0.17" % (p, ZT))
        rcon("execute at %s as @e[%s,distance=6..12] run attribute @s minecraft:movement_speed base set 0.23" % (p, ZT))
        rcon("execute at %s as @e[%s,distance=..6] run attribute @s minecraft:movement_speed base set 0.3" % (p, ZT))
    time.sleep(1)
