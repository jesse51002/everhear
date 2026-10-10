"""Attribute Guard: every few seconds, any player attribute that was changed is put back to its default, and chat says so."""
ASKS = ["reset my attributes to regular", "I'm really small, please fix that", "reset all my attributes to default so I'm normal size again"]
import json
import re

import mc
from kit import values, result, loop

DEFAULTS = {
    "minecraft:max_health": 20.0, "minecraft:movement_speed": 0.10000000149011612, "minecraft:attack_speed": 4.0,
    "minecraft:gravity": 0.08, "minecraft:step_height": 0.6, "minecraft:block_break_speed": 1.0,
    "minecraft:entity_interaction_range": 3.0, "minecraft:block_interaction_range": 4.5, "minecraft:scale": 1.0,
    "minecraft:jump_strength": 0.41999998688697815, "minecraft:safe_fall_distance": 3.0,
    "minecraft:fall_damage_multiplier": 1.0, "minecraft:waypoint_transmit_range": 6.0E7,
    "minecraft:waypoint_receive_range": 6.0E7,
}
ATTR = re.compile(r'id: "(minecraft:[a-z_]+)", base: ([0-9.Ee+-]+)d')


def sweep(player):
    for p in loop.who(player):
        for aid, base in ATTR.findall(mc.rcon("data get entity %s attributes" % p)):
            b, d = float(base), DEFAULTS.get(aid)
            if (d is not None and abs(b - d) > 1e-6) or (d is None and abs(b) > 1000):
                mc.rcon("attribute %s %s base reset" % (p, aid))
                mc.log("reset", p, aid, b)
                mc.rcon("tellraw @a " + json.dumps([{"text": "[Demon Lord] ", "color": "dark_red", "bold": True},
                                                    {"text": "Someone tampered with %s. I undid it." % p,
                                                     "color": "red", "bold": False}]))


v = values()
result("the attributes of %s are put back to normal every %g s" % (v["player"], v["every"]),
       player=v["player"], every=v["every"])
for _ in loop(every=v["every"]):
    sweep(v["player"])
