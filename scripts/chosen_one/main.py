#!/usr/bin/env python3
# The chosen one: a private cinematic for one player. A radiant giant appears, names them the
# chosen one, gives them a holy zombie-slaying blade, and vanishes.
import json
import sys
import time

from mc import rcon, on_stop, log, TAG

if len(sys.argv) < 2:
    log("usage: chosen_one <player>")
    sys.exit(2)
P = sys.argv[1]


def whisper(t, c="gold"):
    rcon("tellraw %s %s" % (P, json.dumps([{"text": "<The Radiant One> ", "color": "yellow", "bold": True},
                                           {"text": t, "color": c, "italic": True, "bold": False}])))


on_stop(lambda: rcon("kill @e[tag=%s]" % TAG))
log("cinematic for", P)
rcon("effect give %s slowness 14 4 true" % P)
rcon("effect give %s darkness 3 0 true" % P)
rcon("execute as %s at @s run playsound block.beacon.activate master @s ~ ~ ~ 1 0.5" % P)
time.sleep(3)
rcon("execute at %s rotated ~ 0 positioned ^ ^4 ^9 run summon mannequin ~ ~ ~ "
     '{Tags:["vfg","%s"],profile:{name:"Notch"},Invulnerable:1b,NoGravity:1b,immovable:1b,Glowing:1b,'
     'attributes:[{id:"scale",base:5d}],CustomName:{"text":"The Radiant One","color":"gold","bold":true},'
     "CustomNameVisible:1b}" % (P, TAG))
rcon("execute as @e[tag=%s] at @s run tp @s ~ ~ ~ facing entity %s" % (TAG, P))
rcon("execute at @e[tag=%s,limit=1] run particle end_rod ~ ~4 ~ 3 5 3 0.05 400 force %s" % (TAG, P))
rcon("execute as %s at @s run playsound ui.toast.challenge_complete master @s ~ ~ ~ 1 0.6" % P)
rcon("title %s times 10 60 20" % P)
rcon('title %s subtitle {"text":"a voice only you can hear...","color":"gray"}' % P)
rcon('title %s title {"text":"THE CHOSEN ONE","color":"gold","bold":true}' % P)
for line in ("%s... do not be afraid." % P, "I am the one who waits at the end of all things.",
             "Of all who walk this world, only YOU were chosen.", "The others must not know. Not yet.",
             "Take this blade. The dead will fall before it."):
    time.sleep(2.5)
    whisper(line)
rcon('give %s netherite_sword[item_name="Holy Zombie Slayer",enchantments={smite:10,fire_aspect:2,looting:3,'
     'unbreaking:3,mending:1},unbreakable={},lore=["Gifted by The Radiant One","to the Chosen"]] 1' % P)
rcon("execute as %s at @s run playsound entity.player.levelup master @s ~ ~ ~ 1 0.6" % P)
time.sleep(2.5)
whisper("We will meet again... at the end.", "yellow")
rcon("execute at @e[tag=%s,limit=1] run particle flash ~ ~4 ~ 0 0 0 0 1 force %s" % (TAG, P))
rcon("kill @e[tag=%s]" % TAG)
log("done")
