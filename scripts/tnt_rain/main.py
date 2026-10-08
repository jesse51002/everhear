#!/usr/bin/env python3
# TNT rain: primed TNT falls around every online player (or only the named ones),
# a volley every few seconds, until stopped.
import random
import sys
import time

from mc import rcon, players, on_stop, log, TAG


def parse(args):
    """[every_s=20] [count=10] [player...]: leading numbers, then names."""
    nums, names = [], []
    for a in args:
        try:
            if names:
                raise ValueError
            nums.append(float(a))
        except ValueError:
            names.append(a)
    every = nums[0] if len(nums) > 0 else 20.0
    n = int(nums[1]) if len(nums) > 1 else 10
    return max(1.0, every), max(1, min(n, 40)), names


EVERY, COUNT, ONLY = parse(sys.argv[1:])


def cleanup():
    rcon("kill @e[type=tnt,tag=%s]" % TAG)


on_stop(cleanup)
log("every", EVERY, "s,", COUNT, "TNT each, on", ", ".join(ONLY) or "everyone")
while True:
    targets = [p for p in players() if not ONLY or p in ONLY]
    for p in targets:
        for _ in range(COUNT):
            rcon('execute at %s run summon tnt ~%d ~25 ~%d {fuse:80,Tags:["vfg","%s"]}'
                 % (p, random.randint(-7, 7), random.randint(-7, 7), TAG))
    if targets:
        log("dropped", COUNT, "TNT on", ", ".join(targets))
    time.sleep(EVERY)
