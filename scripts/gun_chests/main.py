"""Gun Chests: chests of named guns and ammo scattered 40 to 160 blocks round each player, each marked with a glowing tag."""
ASKS = ["make cool weapons and put them in a chest for all of us to access", "the chest of mysterious weapons",
        "chests full of guns around the map"]
import math
import random

import mc
from kit import values, area, loop

VALUES = {
    "player":     {"type": "player", "default": "@a"},
    "per_player": {"type": "int", "min": 1, "max": 10, "default": 4},
    "nearest":    {"type": "int", "min": 0, "max": 1000, "default": 40},
    "farthest":   {"type": "int", "min": 1, "max": 1000, "default": 160},
}

FW = 'firework_rocket[fireworks={flight_duration:1,explosions:[{shape:"burst",colors:[I;16711680]}]}]'
GUNS = [
    ('crossbow[item_name="AK-47",enchantments={quick_charge:5,unbreaking:3}]', "arrow 64"),
    ('crossbow[item_name="Shotgun",enchantments={multishot:1,quick_charge:3}]', "arrow 64"),
    ('bow[item_name="Sniper Rifle",enchantments={power:7,punch:1,infinity:1}]', "arrow 1"),
    ('crossbow[item_name="Rocket Launcher",enchantments={quick_charge:2}]', FW + " 16"),
    ('crossbow[item_name="Railgun",enchantments={piercing:10,quick_charge:3}]', "arrow 64"),
    ('trident[item_name="Harpoon Gun",enchantments={loyalty:3,impaling:5}]', "golden_apple 2"),
]
# The glowing tags stay when the script is done, so they carry a tag of their own, not the
# instance's: vfg cmd 'kill @e[tag=vfg_gun_chest]' takes them away.
LABEL = "vfg_gun_chest"

v = values(VALUES)
placed = 0
for p in loop.who(v["player"]):
    c = mc.pos(p)
    if not c:
        continue
    for _ in range(v["per_player"]):
        a, r = random.uniform(0, 6.283), random.uniform(v["nearest"], max(v["nearest"], v["farthest"]))
        x, z = int(c[0] + r * math.cos(a)), int(c[2] + r * math.sin(a))
        top = "execute positioned %d 0 %d positioned over world_surface run " % (x, z)
        spot = (x, 0, z, x, 0, z)
        area.forceload(spot)        # only if it was not loaded; waits for it to load
        mc.rcon(top + "setblock ~ ~ ~ chest replace")
        for i, (gun, ammo) in enumerate(random.sample(GUNS, 2)):
            mc.rcon(top + "item replace block ~ ~-1 ~ container.%d with %s" % (i * 2, gun))
            mc.rcon(top + "item replace block ~ ~-1 ~ container.%d with %s" % (i * 2 + 1, ammo))
        # "positioned x 0 z" already puts it in the middle of the block: the tag floats over the chest.
        mc.rcon(top + 'summon armor_stand ~ ~ ~ {Invisible:1b,Marker:1b,NoGravity:1b,Glowing:1b,'
                'CustomNameVisible:1b,Tags:["vfg","%s"],CustomName:{"text":"GUN CHEST","color":"gold","bold":true}}'
                % LABEL)
        area.release(spot)
        placed += 1
        mc.log("chest at", x, z, "near", p)
if placed:
    mc.say("%d GUN CHESTS dropped %d-%d blocks around you, marked with glowing gold tags. Inside: AK-47, Shotgun, "
           "Sniper Rifle, Rocket Launcher, Railgun, Harpoon Gun + ammo." % (placed, v["nearest"], v["farthest"]),
           color="gold")
mc.log("placed", placed)
