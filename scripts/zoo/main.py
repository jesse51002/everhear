#!/usr/bin/env python3
# Zoo: keeps summoning random animals around one player, never more than the maximum.
import random
import sys
import time

from mc import rcon, players, count, on_stop, log, TAG

if len(sys.argv) < 2:
    log("usage: zoo <player> [max=200]")
    sys.exit(2)
P = sys.argv[1]
try:
    MAX = max(1, min(int(sys.argv[2]), 400)) if len(sys.argv) > 2 else 200
except ValueError:
    MAX = 200
ANIMALS = ("pig cow sheep chicken rabbit fox goat llama cat wolf parrot frog turtle axolotl panda horse "
           "donkey bee armadillo camel").split()

on_stop(lambda: rcon("kill @e[tag=%s]" % TAG))
log("zoo around", P, "max", MAX)
while True:
    if P not in players():
        time.sleep(2)
        continue
    room = min(20, MAX - count("@e[tag=%s]" % TAG))
    for _ in range(max(0, room)):
        rcon('execute at %s run summon %s ~%d ~1 ~%d {Tags:["vfg","%s"]}'
             % (P, random.choice(ANIMALS), random.randint(-8, 8), random.randint(-8, 8), TAG))
    time.sleep(1)
