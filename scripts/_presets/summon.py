"""Spawn mobs round a player or where they look: any mob, how many, bigger, armoured, armed, named, glowing, up to a cap."""
import json

import mc
from kit import loop, mobs

VALUES = {
    "summon_mob": {"type": "mob", "default": "zombie", "live": True},
    "summon_count": {"type": "int", "min": 1, "max": 200, "default": 5, "live": True},
    "summon_spread": {"type": "int", "min": 1, "max": 100, "default": 8, "live": True},
    "summon_cap": {"type": "int", "min": 1, "max": 1000, "default": 60, "live": True},
    "summon_scale": {"type": "number", "min": 0.1, "max": 16, "default": 1, "live": True},
    "summon_health": {"type": "number", "min": 0, "max": 2048, "default": 0, "live": True},
    "summon_gear": {"type": "enum", "of": ["none", "leather", "chainmail", "iron", "golden", "diamond", "netherite"],
                    "default": "none", "live": True},
    "summon_weapon": {"type": "str", "default": "", "live": True},
    "summon_name": {"type": "str", "default": "", "live": True},
    "summon_glow": {"type": "bool", "default": False, "live": True},
    "summon_nbt": {"type": "str", "default": "", "live": True},
}


def summon(at, v):
    """summon_count of summon_mob round `at` (a player's name, a player value such as "@a" or
    "Steve,Alex" for round each of them, or a place, in its dimension), at safe spots:
    standing room on solid ground, round a player summon_spread blocks out and never on top
    of them, round a place anywhere within summon_spread of it. summon_scale sizes them;
    summon_health (0: their own) sets their health; summon_gear a full suit of that armour and
    summon_weapon an item in hand, neither dropped; summon_name a name over their heads;
    summon_glow shows them through walls; summon_nbt is more NBT ("IsBaby:1b"). Never more
    than summon_cap of this script's alive at once. They are tagged TAG_summon, and a stop
    kills them. Returns how many it summoned."""
    tag = mc.TAG + "_summon"
    spots = loop.who(at) if isinstance(at, str) else [at]
    got = 0
    for spot in spots:
        room = v["summon_cap"] - mobs.count(tag)
        if room <= 0:
            mc.log("summon: %d alive, the cap" % v["summon_cap"])
            break
        got += mobs.around(spot, v["summon_mob"], min(v["summon_count"], room), _summon_nbt(v),
                           spread=v["summon_spread"], tags=(tag,))
    return got


def _summon_nbt(v):
    """The NBT the values ask for, without braces or Tags (mc.spawn adds those)."""
    parts, attrs, gear = [], [], {}
    if v["summon_name"]:
        parts += ["CustomName:%s" % json.dumps(v["summon_name"]), "CustomNameVisible:1b"]
    if v["summon_health"] > 0:
        attrs.append('{id:"max_health",base:%g}' % v["summon_health"])
        parts.append("Health:%gf" % v["summon_health"])
    if v["summon_scale"] != 1:
        attrs.append('{id:"scale",base:%g}' % v["summon_scale"])
    if attrs:
        parts.append("attributes:[%s]" % ",".join(attrs))
    if v["summon_gear"] != "none":
        for slot, piece in (("head", "helmet"), ("chest", "chestplate"), ("legs", "leggings"), ("feet", "boots")):
            gear[slot] = "%s_%s" % (v["summon_gear"], piece)
    if v["summon_weapon"]:
        gear["mainhand"] = v["summon_weapon"]
    if gear:
        parts.append("equipment:{%s}" % ",".join('%s:{id:"%s",count:1}' % sg for sg in sorted(gear.items())))
        parts.append("drop_chances:{%s}" % ",".join("%s:0f" % s for s in sorted(gear)))
    if v["summon_glow"]:
        parts.append("Glowing:1b")
    if v["summon_nbt"]:
        parts.append(v["summon_nbt"].strip().strip("{}"))
    return ",".join(parts)
