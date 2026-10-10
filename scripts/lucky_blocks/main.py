"""Lucky Blocks: sponges named Lucky Block that give the player who breaks one a random reward or trap."""
ASKS = ["lucky blocks", "blocks that give a random reward or a trap when you break them", "give us some lucky blocks"]
import json
import random

import mc
from kit import values, result, loop

MINED = "vfg_lucky"
TRAP = '{Tags:["vfg","%s"]}' % mc.TAG  # traps are removed on stop; gifts stay with the player
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
    ("a TNT surprise", ['execute at {p} run summon tnt ~ ~3 ~ {{fuse:60,Tags:["vfg","' + mc.TAG + '"]}}']),
    ("a ride into the sky", ["effect give {p} levitation 4 1"]),
    ("a cobweb trap", ["execute at {p} run fill ~-1 ~ ~-1 ~1 ~1 ~1 cobweb replace air"]),
    ("a rain of chickens", ["execute at {p} run summon chicken ~ ~6 ~"] * 8),
    ("more lucky blocks", ['give {p} sponge[item_name="Lucky Block"] 3']),
]


def cleanup():
    mc.rcon("scoreboard objectives remove " + MINED)


v = values()
mc.on_stop(cleanup)
mc.rcon("scoreboard objectives add %s minecraft.mined:minecraft.sponge" % MINED)
given = loop.who(v["player"]) if v["give"] else []
for p in given:
    mc.rcon('give %s sponge[item_name="Lucky Block"] %d' % (p, v["give"]))
result("lucky blocks on for %s%s" % (v["player"], "; gave %d each to %s" % (v["give"], ", ".join(given)) if given else ""),
       player=v["player"], given=given, each=v["give"])
counts = {}
for _ in loop(every=0.5):
    for p in loop.who(v["player"]):
        n = mc.score(p, MINED)
        if p in counts and n > counts[p]:
            for _ in range(n - counts[p]):
                name, cmds = random.choice(OUTCOMES)
                for c in cmds:
                    mc.rcon(c.format(p=p))
                mc.rcon("tellraw %s %s" % (p, json.dumps([
                    {"text": "[Lucky Block] ", "color": "yellow", "bold": True},
                    {"text": "You got %s!" % name, "color": "gold", "bold": False}])))
                mc.rcon("execute at %s run particle totem_of_undying ~ ~1 ~ 0.5 0.8 0.5 0.3 40" % p)
                mc.log(p, "got", name)
        counts[p] = n
