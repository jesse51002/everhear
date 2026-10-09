"""Chosen One: a private cinematic in which a radiant giant names one player the chosen one and gives them a holy blade."""
ASKS = ["make me the chosen one", "a god gives me a holy sword", "a sword that slays zombies"]
import json
import time

import mc
from kit import values, loop

VALUES = {
    "player": {"type": "player", "default": "@r"},
    "blade":  {"type": "str", "default": "Holy Zombie Slayer", "live": True},
}

v = values(VALUES)
chosen = loop.who(v["player"])
if not chosen:
    mc.log("nobody to choose: %s is not online" % v["player"])
    raise SystemExit(0)
P = chosen[0]


def whisper(t, c="gold"):
    mc.rcon("tellraw %s %s" % (P, json.dumps([{"text": "<The Radiant One> ", "color": "yellow", "bold": True},
                                              {"text": t, "color": c, "italic": True, "bold": False}])))


mc.on_stop(lambda: mc.rcon("kill @e[tag=%s]" % mc.TAG))
mc.log("cinematic for", P)
mc.rcon("effect give %s slowness 14 4 true" % P)
mc.rcon("effect give %s darkness 3 0 true" % P)
mc.rcon("execute as %s at @s run playsound block.beacon.activate master @s ~ ~ ~ 1 0.5" % P)
time.sleep(3)
mc.rcon("execute at %s rotated ~ 0 positioned ^ ^4 ^9 run summon mannequin ~ ~ ~ "
        '{Tags:["vfg","%s"],profile:{name:"Notch"},Invulnerable:1b,NoGravity:1b,immovable:1b,Glowing:1b,'
        'attributes:[{id:"scale",base:5d}],CustomName:{"text":"The Radiant One","color":"gold","bold":true},'
        "CustomNameVisible:1b}" % (P, mc.TAG))
mc.rcon("execute as @e[tag=%s] at @s run tp @s ~ ~ ~ facing entity %s" % (mc.TAG, P))
mc.rcon("execute at @e[tag=%s,limit=1] run particle end_rod ~ ~4 ~ 3 5 3 0.05 400 force %s" % (mc.TAG, P))
mc.rcon("execute as %s at @s run playsound ui.toast.challenge_complete master @s ~ ~ ~ 1 0.6" % P)
mc.rcon("title %s times 10 60 20" % P)
mc.rcon('title %s subtitle {"text":"a voice only you can hear...","color":"gray"}' % P)
mc.rcon('title %s title {"text":"THE CHOSEN ONE","color":"gold","bold":true}' % P)
for line in ("%s... do not be afraid." % P, "I am the one who waits at the end of all things.",
             "Of all who walk this world, only YOU were chosen.", "The others must not know. Not yet.",
             "Take this blade. The dead will fall before it."):
    time.sleep(2.5)
    whisper(line)
mc.rcon('give %s netherite_sword[item_name=%s,enchantments={smite:10,fire_aspect:2,looting:3,'
        'unbreaking:3,mending:1},unbreakable={},lore=["Gifted by The Radiant One","to the Chosen"]] 1'
        % (P, json.dumps(v["blade"])))
mc.rcon("execute as %s at @s run playsound entity.player.levelup master @s ~ ~ ~ 1 0.6" % P)
time.sleep(2.5)
whisper("We will meet again... at the end.", "yellow")
# 26.3: flash takes a colour.
mc.rcon("execute at @e[tag=%s,limit=1] run particle flash{color:[1.0,1.0,1.0,1.0]} ~ ~4 ~ 0 0 0 0 1 force %s"
        % (mc.TAG, P))
mc.rcon("kill @e[tag=%s]" % mc.TAG)
mc.log("done")
