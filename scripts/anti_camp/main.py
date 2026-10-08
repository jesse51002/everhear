#!/usr/bin/env python3
# Anti-camping: a player who stays within 4 blocks of one spot for too long gets a warning,
# then TNT from the sky.
import json
import math
import sys
import time

from mc import rcon, players, pos, on_stop, log, TAG

try:
    SECONDS = max(6.0, float(sys.argv[1])) if len(sys.argv) > 1 else 15.0
except ValueError:
    SECONDS = 15.0

on_stop(lambda: rcon("kill @e[type=tnt,tag=%s]" % TAG))
log("campers get TNT after", SECONDS, "s")
anchor = {}
while True:
    ps = players()
    if not ps:
        time.sleep(2)
        continue
    now = time.time()
    for p in ps:
        c = pos(p)
        if not c:
            continue
        a = anchor.get(p)
        if not a or math.hypot(a[0] - c[0], a[2] - c[2]) > 4:
            anchor[p] = (c[0], c[1], c[2], now)
            continue
        stay = now - a[3]
        if SECONDS - 5 <= stay < SECONDS - 3:
            rcon("title %s actionbar %s" % (p, json.dumps({"text": "MOVE. The Demon Lord sees you hiding...",
                                                           "color": "red", "bold": True})))
        if stay >= SECONDS:
            rcon("tellraw @a " + json.dumps([{"text": "[Demon Lord] ", "color": "dark_red", "bold": True},
                                              {"text": "%s thinks standing still is safe." % p, "color": "red",
                                               "bold": False}]))
            for dx, dz in ((0, 0), (2, 0), (-2, 0), (0, 2), (0, -2), (2, 2), (-2, -2)):
                rcon('execute at %s run summon tnt ~%d ~12 ~%d {fuse:40,Tags:["vfg","%s"]}' % (p, dx, dz, TAG))
            anchor[p] = (c[0], c[1], c[2], now)
            log("TNT on", p)
    time.sleep(1)
