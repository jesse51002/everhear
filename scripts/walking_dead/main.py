"""Walking Dead: zombies become slow walkers in bigger groups that hear sprinting from far away and barely notice sneaking."""
ASKS = ["walking dead zombies", "zombies that hear you when you sprint", "slow zombies in big groups"]
import random

import mc
from kit import values, result, loop

WALKER = mc.TAG + "_w"  # on the world's own zombies, so never TAG itself
ZT = "type=#minecraft:zombies,tag=!custom"
SNEAK, SPRINT = "vfg_wd_sneak", "vfg_wd_sprint"


def cleanup():
    mc.rcon("execute as @e[tag=%s] run attribute @s minecraft:movement_speed base reset" % WALKER)
    mc.rcon("execute as @e[tag=%s] run attribute @s minecraft:follow_range base reset" % WALKER)
    mc.rcon("tag @e[tag=%s] remove %s" % (WALKER, WALKER))
    mc.rcon("scoreboard objectives remove " + SNEAK)
    mc.rcon("scoreboard objectives remove " + SPRINT)


v = values()
mc.on_stop(cleanup)
mc.rcon("scoreboard objectives add %s minecraft.custom:minecraft.sneak_time" % SNEAK)
mc.rcon("scoreboard objectives add %s minecraft.custom:minecraft.sprint_one_cm" % SPRINT)
result("zombies are walkers now: they hear %s sprint from %d blocks, walk from %d, sneak from %d"
       % (v["player"], v["sprint_range"], v["walk_range"], v["sneak_range"]), player=v["player"],
       walk_speed=v["walk_speed"], sprint_range=v["sprint_range"], sneak_range=v["sneak_range"])
last = {}
for _ in loop(every=1):
    ps = loop.who(v["player"])
    # New zombies become walkers: slow, able to break doors, sometimes with a friend.
    mc.rcon("execute as @e[%s,tag=!%s] run attribute @s minecraft:movement_speed base set %g"
            % (ZT, WALKER, v["walk_speed"]))
    mc.rcon("execute as @e[%s,tag=!%s] run data merge entity @s {CanBreakDoors:1b}" % (ZT, WALKER))
    if random.random() < v["friends"]:
        mc.rcon('execute as @e[%s,tag=!%s,limit=2,sort=random] at @s run summon zombie ~1 ~ ~1 {Tags:["vfg","%s","%s"]}'
                % (ZT, WALKER, mc.TAG, WALKER))
    mc.rcon("tag @e[%s,tag=!%s] add %s" % (ZT, WALKER, WALKER))
    for p in ps:
        sn, sp = mc.score(p, SNEAK), mc.score(p, SPRINT)
        ls = last.get(p, (sn, sp))
        last[p] = (sn, sp)
        sneak, sprint = sn > ls[0], sp > ls[1]
        rng = v["sneak_range"] if sneak else (v["sprint_range"] if sprint else v["walk_range"])
        mc.rcon("execute at %s as @e[%s,distance=..48] run attribute @s minecraft:follow_range base set %d" % (p, ZT, rng))
        # The closer they get, the faster they lunge.
        mc.rcon("execute at %s as @e[%s,distance=12..48] run attribute @s minecraft:movement_speed base set %g"
                % (p, ZT, v["walk_speed"]))
        mc.rcon("execute at %s as @e[%s,distance=6..12] run attribute @s minecraft:movement_speed base set %g"
                % (p, ZT, v["walk_speed"] * 0.23 / 0.17))
        mc.rcon("execute at %s as @e[%s,distance=..6] run attribute @s minecraft:movement_speed base set %g"
                % (p, ZT, v["walk_speed"] * 0.3 / 0.17))
