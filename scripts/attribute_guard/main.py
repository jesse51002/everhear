#!/usr/bin/env python3
# Attribute guard: every 5 s, puts any player attribute that was changed back to its default,
# and says so in chat.
import json
import re
import time

from mc import rcon, players, on_stop, log

DEFAULTS = {
    "minecraft:max_health": 20.0, "minecraft:movement_speed": 0.10000000149011612, "minecraft:attack_speed": 4.0,
    "minecraft:gravity": 0.08, "minecraft:step_height": 0.6, "minecraft:block_break_speed": 1.0,
    "minecraft:entity_interaction_range": 3.0, "minecraft:block_interaction_range": 4.5, "minecraft:scale": 1.0,
    "minecraft:jump_strength": 0.41999998688697815, "minecraft:safe_fall_distance": 3.0,
    "minecraft:fall_damage_multiplier": 1.0, "minecraft:waypoint_transmit_range": 6.0E7,
    "minecraft:waypoint_receive_range": 6.0E7,
}
ATTR = re.compile(r'id: "(minecraft:[a-z_]+)", base: ([0-9.Ee+-]+)d')


def sweep():
    for p in players():
        for aid, base in ATTR.findall(rcon("data get entity %s attributes" % p)):
            b, d = float(base), DEFAULTS.get(aid)
            if (d is not None and abs(b - d) > 1e-6) or (d is None and abs(b) > 1000):
                rcon("attribute %s %s base reset" % (p, aid))
                log("reset", p, aid, b)
                rcon("tellraw @a " + json.dumps([{"text": "[Demon Lord] ", "color": "dark_red", "bold": True},
                                                  {"text": "Someone tampered with %s. I undid it." % p,
                                                   "color": "red", "bold": False}]))


on_stop(lambda: log("guard off"))
log("guarding player attributes")
while True:
    sweep()
    time.sleep(5)
