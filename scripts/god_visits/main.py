#!/usr/bin/env python3
# God visits: every few minutes of play a giant god appears to a random player, speaks to
# them alone, and gives them a gift or takes something.
import json
import random
import sys
import time

from mc import rcon, players, on_stop, log, TAG


def num(i, default):
    try:
        return max(10, int(sys.argv[i]))
    except (IndexError, ValueError):
        return default


MIN_S = num(1, 180)
MAX_S = max(MIN_S, num(2, 360))
GODS = [("The Radiant One", "Notch", "gold"), ("The Storm God", "jeb_", "aqua"), ("Thanatos, God of Death", "Grim", "dark_gray")]
GIFTS = ["diamond 4", "golden_apple 3", "enchanted_golden_apple 1", "netherite_ingot 1", "totem_of_undying 1",
         "ender_pearl 8", "experience_bottle 32", "cooked_beef 32"]
TAKES = ["diamond", "golden_apple", "ender_pearl", "cooked_beef", "iron_ingot", "arrow", "torch", "bread"]


def visit(p):
    name, skin, col = random.choice(GODS)
    rcon("execute as %s at @s run playsound block.beacon.activate master @s ~ ~ ~ 1 0.6" % p)
    label = json.dumps({"text": name, "color": col, "bold": True})
    rcon("execute at %s rotated ~ 0 positioned ^ ^1 ^6 run summon mannequin ~ ~ ~ "
         '{Tags:["vfg","%s"],profile:{name:"%s"},Invulnerable:1b,NoGravity:1b,immovable:1b,Glowing:1b,'
         'attributes:[{id:"scale",base:3d}],CustomName:%s,CustomNameVisible:1b}' % (p, TAG, skin, label))
    rcon("execute as @e[tag=%s] at @s run tp @s ~ ~ ~ facing entity %s" % (TAG, p))
    if random.random() < 0.6:
        g = random.choice(GIFTS)
        rcon("give %s %s" % (p, g))
        item, n = g.split()
        msg = "You amuse me, %s. Take this gift: %sx %s." % (p, n, item.replace("_", " "))
    else:
        t = random.choice(TAKES)
        rcon("clear %s %s 8" % (p, t))
        msg = "You have grown too comfortable, %s. I take your %s." % (p, t.replace("_", " "))
    rcon("tellraw %s %s" % (p, json.dumps({"text": "%s whispers to you: %s" % (name, msg),
                                           "color": "gray", "italic": True})))
    log(name, "visited", p)
    time.sleep(8)
    rcon("kill @e[tag=%s]" % TAG)


on_stop(lambda: rcon("kill @e[tag=%s]" % TAG))
log("a god visits every", MIN_S, "to", MAX_S, "s of play")
wait = random.randint(MIN_S, MAX_S)
while True:
    ps = players()
    if not ps:
        time.sleep(5)
        continue
    if wait > 0:
        step = min(5, wait)
        time.sleep(step)
        wait -= step
        continue
    visit(random.choice(ps))
    wait = random.randint(MIN_S, MAX_S)
