#!/usr/bin/env python3
# Grave marks: where a player dies, a skeleton skull and a floating glowing name tag mark the
# spot for a while, and chat says where. The owner walking back to it clears it early.
import json
import re
import sys
import threading
import time

from mc import rcon, players, score, on_stop, log, TAG

try:
    SECONDS = max(5.0, float(sys.argv[1])) if len(sys.argv) > 1 else 30.0
except ValueError:
    SECONDS = 30.0
DEATHS = "vfg_gm_deaths"
graves = {}  # (dim, x, y, z) -> player
lock = threading.Lock()


def clear(key):
    d, x, y, z = key
    rcon("execute in minecraft:%s run fill %s %s %s %s %s %s air replace skeleton_skull" % (d, x, y, z, x, y, z))
    rcon("execute in minecraft:%s run kill @e[type=armor_stand,tag=%s,x=%s,y=%d,z=%s,distance=..2]"
         % (d, TAG, x, int(y) + 1, z))
    with lock:
        graves.pop(key, None)


def mark(p, n):
    out = rcon("data get entity %s LastDeathLocation" % p)
    at = re.search(r"pos: \[I; (-?\d+), (-?\d+), (-?\d+)\]", out)
    dim = re.search(r'dimension: "minecraft:(\w+)"', out)
    if not at:
        return
    x, y, z = at.groups()
    d = dim.group(1) if dim else "overworld"
    key = (d, x, y, z)
    rcon("execute in minecraft:%s run setblock %s %s %s skeleton_skull replace" % (d, x, y, z))
    name = json.dumps({"text": "%s died here (#%d)" % (p, n), "color": "red"})
    rcon("execute in minecraft:%s run summon armor_stand %s.5 %d %s.5 {Invisible:1b,Marker:1b,NoGravity:1b,"
         'Glowing:1b,CustomNameVisible:1b,Tags:["vfg","%s","%s_%s"],CustomName:%s}'
         % (d, x, int(y) + 1, z, TAG, TAG, p, name))
    with lock:
        graves[key] = p
    timer = threading.Timer(SECONDS, clear, args=(key,))
    timer.daemon = True
    timer.start()
    rcon("tellraw @a " + json.dumps([
        {"text": "[Grave] ", "color": "dark_red", "bold": True},
        {"text": "%s died at %s %s %s (%s). A skull marks the spot." % (p, x, y, z, d), "color": "gray", "bold": False}]))
    log(p, "died at", x, y, z, d)


def cleanup():
    with lock:
        keys = list(graves)
    for key in keys:
        clear(key)
    rcon("scoreboard objectives remove " + DEATHS)


on_stop(cleanup)
rcon("scoreboard objectives add %s deathCount" % DEATHS)
log("marking graves for", SECONDS, "s")
seen = {}
while True:
    for p in players():
        n = score(p, DEATHS)
        if p in seen and n > seen[p]:
            mark(p, n)
        seen[p] = n
        own = "@e[type=armor_stand,tag=%s_%s,distance=..3]" % (TAG, p)
        if "passed" in rcon("execute at %s if entity %s" % (p, own)).lower():
            # The owner reached their grave: the mark fades.
            rcon("execute at %s as %s at @s run fill ~ ~-1 ~ ~ ~-1 ~ air replace skeleton_skull" % (p, own))
            rcon("execute at %s run kill %s" % (p, own))
            rcon("tellraw %s %s" % (p, json.dumps({"text": "You found your grave. The mark fades.",
                                                   "color": "gray", "italic": True})))
            with lock:
                for key in [k for k, owner in graves.items() if owner == p]:
                    graves.pop(key, None)
    time.sleep(1)
