#!/usr/bin/env python3
# Lucky blocks: sponges named "Lucky Block". Breaking one gives that player a random
# reward or trap. Every online player gets some when it starts.
import json
import random
import sys
import time

from mc import rcon, players, score, on_stop, log, TAG

try:
    GIVE = max(0, min(int(sys.argv[1]), 64)) if len(sys.argv) > 1 else 3
except ValueError:
    GIVE = 3
MINED = "vfg_lucky"
TRAP = '{Tags:["vfg","%s"]}' % TAG  # traps are removed on stop; gifts stay with the player
OUTCOMES = [
    ("8 diamonds", ["give {p} diamond 8"]),
    ("4 golden apples", ["give {p} golden_apple 4"]),
    ("an enchanted golden apple", ["give {p} enchanted_golden_apple 1"]),
    ("a totem", ["give {p} totem_of_undying 1"]),
    ("2 netherite ingots", ["give {p} netherite_ingot 2"]),
    ("a stack of XP bottles", ["give {p} experience_bottle 64"]),
    ("a cake", ["execute at {p} run setblock ~1 ~ ~ cake"]),
    ("a pet wolf", ['execute at {p} run summon wolf ~1 ~ ~ {{Owner:"{p}"}}']),
    ("a horse with a saddle",
     ['execute at {p} run summon horse ~1 ~ ~ {{Tame:1b,equipment:{{saddle:{{id:"saddle",count:1}}}}}}']),
    ("speed and haste for a minute", ["effect give {p} speed 60 2", "effect give {p} haste 60 2"]),
    ("a lucky sword", ['give {p} diamond_sword[item_name="Lucky Sword",enchantments={{sharpness:6,looting:4}}] 1']),
    ("zombies!", ["execute at {p} run summon zombie ~3 ~ ~ " + TRAP.replace("{", "{{").replace("}", "}}"),
                  "execute at {p} run summon zombie ~-3 ~ ~ " + TRAP.replace("{", "{{").replace("}", "}}"),
                  "execute at {p} run summon zombie ~ ~ ~3 " + TRAP.replace("{", "{{").replace("}", "}}")]),
    ("a lightning strike", ["execute at {p} run summon lightning_bolt ~2 ~ ~2"]),
    ("a TNT surprise", ['execute at {p} run summon tnt ~ ~3 ~ {{fuse:60,Tags:["vfg","' + TAG + '"]}}']),
    ("a ride into the sky", ["effect give {p} levitation 4 1"]),
    ("a cobweb trap", ["execute at {p} run fill ~-1 ~ ~-1 ~1 ~1 ~1 cobweb replace air"]),
    ("a rain of chickens", ["execute at {p} run summon chicken ~ ~6 ~"] * 8),
    ("more lucky blocks", ['give {p} sponge[item_name="Lucky Block"] 3']),
]


def cleanup():
    rcon("scoreboard objectives remove " + MINED)


on_stop(cleanup)
rcon("scoreboard objectives add %s minecraft.mined:minecraft.sponge" % MINED)
for p in players():
    if GIVE:
        rcon('give %s sponge[item_name="Lucky Block"] %d' % (p, GIVE))
log("lucky blocks on; gave", GIVE, "each")
counts = {}
while True:
    ps = players()
    if not ps:
        time.sleep(2)
        continue
    for p in ps:
        n = score(p, MINED)
        if p in counts and n > counts[p]:
            for _ in range(n - counts[p]):
                name, cmds = random.choice(OUTCOMES)
                for c in cmds:
                    rcon(c.format(p=p))
                rcon("tellraw %s %s" % (p, json.dumps([
                    {"text": "[Lucky Block] ", "color": "yellow", "bold": True},
                    {"text": "You got %s!" % name, "color": "gold", "bold": False}])))
                rcon("execute at %s run particle totem_of_undying ~ ~1 ~ 0.5 0.8 0.5 0.3 40" % p)
                log(p, "got", name)
        counts[p] = n
    time.sleep(0.5)
