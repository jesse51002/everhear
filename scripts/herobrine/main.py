#!/usr/bin/env python3
# Herobrine horror pranks on the named players (or everyone), one at random every 60-150 s
# of them being online.
import json
import random
import sys
import time

from mc import rcon, players, on_stop, log, TAG

ONLY = sys.argv[1:]


def snd(p, s, v=1, pitch=1):
    rcon("execute as %s at @s run playsound %s ambient @s ^ ^ ^-3 %s %s" % (p, s, v, pitch))


def statue(p):  # Herobrine appears behind you, then vanishes
    head = '{id:"player_head",count:1,components:{"minecraft:profile":{name:"Herobrine"}}}'
    chest = '{id:"leather_chestplate",count:1,components:{"minecraft:dyed_color":3381708}}'
    legs = '{id:"leather_leggings",count:1,components:{"minecraft:dyed_color":3355545}}'
    rcon("execute at %s rotated ~ 0 positioned ^ ^ ^-14 run summon armor_stand ~ ~ ~ "
         '{Tags:["vfg","%s"],Invulnerable:1b,ShowArms:1b,NoBasePlate:1b,equipment:{head:%s,chest:%s,legs:%s}}'
         % (p, TAG, head, chest, legs))
    rcon("execute as @e[tag=%s] at @s run tp @s ~ ~ ~ facing entity %s" % (TAG, p))
    time.sleep(random.uniform(4, 8))
    rcon("kill @e[tag=%s]" % TAG)


def fake_join(p):
    rcon('tellraw @a {"text":"Herobrine joined the game","color":"yellow"}')
    time.sleep(6)
    rcon('tellraw @a {"text":"Herobrine left the game","color":"yellow"}')


def torches(p):
    for t in ("torch", "wall_torch"):
        rcon("execute at %s run fill ~-8 ~-3 ~-8 ~8 ~4 ~8 air replace %s" % (p, t))


def door(p):
    snd(p, "block.wooden_door.open", 1, 0.7)
    time.sleep(2)
    snd(p, "block.wooden_door.close", 1, 0.7)


def whisper(p):
    rcon("tellraw %s %s" % (p, json.dumps({"text": "<Herobrine> i see you, %s" % p, "color": "white"})))


def darkness(p):
    rcon("effect give %s darkness 4 0 true" % p)
    snd(p, "entity.warden.heartbeat", 1, 0.7)


def sign(p):
    rcon("execute at %s rotated ~ 0 positioned ^ ^ ^-2 if block ~ ~ ~ air run setblock ~ ~ ~ "
         'oak_sign[rotation=0]{front_text:{messages:["","behind you","",""]}}' % p)


PRANKS = [
    lambda p: snd(p, "entity.zombie.step", 0.6, 0.8),
    door,
    lambda p: snd(p, "ambient.cave", 1, 0.6),
    fake_join,
    whisper,
    statue,
    torches,
    darkness,
    sign,
    lambda p: snd(p, "entity.enderman.stare", 0.5, 0.5),
    lambda p: rcon("execute at %s rotated ~ 0 positioned ^ ^ ^-18 run summon lightning_bolt ~ ~ ~" % p),
]

on_stop(lambda: rcon("kill @e[tag=%s]" % TAG))
log("haunting", ", ".join(ONLY) or "everyone")
wait = random.uniform(60, 150)
while True:
    targets = [p for p in players() if not ONLY or p in ONLY]
    if not targets:
        time.sleep(5)
        continue
    if wait > 0:
        step = min(5.0, wait)
        time.sleep(step)
        wait -= step
        continue
    p, i = random.choice(targets), random.randrange(len(PRANKS))
    try:
        PRANKS[i](p)
        log("prank", i, "on", p)
    except Exception as e:
        log("prank error:", e)
    wait = random.uniform(60, 150)
