#!/usr/bin/env python3
# Gun chests: scatters chests of "guns" (named crossbows and bows, with ammo) 40-160 blocks
# around each online player, each marked with a glowing GUN CHEST tag, then ends.
import random
import sys
import math

from mc import rcon, say, players, pos, log, TAG

try:
    PER = max(1, min(int(sys.argv[1]), 10)) if len(sys.argv) > 1 else 4
except ValueError:
    PER = 4
FW = 'firework_rocket[fireworks={flight_duration:1,explosions:[{shape:"burst",colors:[I;16711680]}]}]'
GUNS = [
    ('crossbow[item_name="AK-47",enchantments={quick_charge:5,unbreaking:3}]', "arrow 64"),
    ('crossbow[item_name="Shotgun",enchantments={multishot:1,quick_charge:3}]', "arrow 64"),
    ('bow[item_name="Sniper Rifle",enchantments={power:7,punch:1,infinity:1}]', "arrow 1"),
    ('crossbow[item_name="Rocket Launcher",enchantments={quick_charge:2}]', FW + " 16"),
    ('crossbow[item_name="Railgun",enchantments={piercing:10,quick_charge:3}]', "arrow 64"),
    ('trident[item_name="Harpoon Gun",enchantments={loyalty:3,impaling:5}]', "golden_apple 2"),
]

placed = 0
for p in players():
    c = pos(p)
    if not c:
        continue
    for _ in range(PER):
        a, r = random.uniform(0, 6.283), random.uniform(40, 160)
        x, z = int(c[0] + r * math.cos(a)), int(c[2] + r * math.sin(a))
        top = "execute positioned %d 0 %d positioned over world_surface run " % (x, z)
        rcon("forceload add %d %d" % (x, z))
        rcon(top + "setblock ~ ~ ~ chest replace")
        for i, (gun, ammo) in enumerate(random.sample(GUNS, 2)):
            rcon(top + "item replace block ~ ~-1 ~ container.%d with %s" % (i * 2, gun))
            rcon(top + "item replace block ~ ~-1 ~ container.%d with %s" % (i * 2 + 1, ammo))
        rcon(top + 'summon armor_stand ~0.5 ~ ~0.5 {Invisible:1b,Marker:1b,NoGravity:1b,Glowing:1b,'
             'CustomNameVisible:1b,Tags:["vfg","%s"],CustomName:{"text":"GUN CHEST","color":"gold","bold":true}}' % TAG)
        rcon("forceload remove %d %d" % (x, z))
        placed += 1
        log("chest at", x, z, "near", p)
if placed:
    say("%d GUN CHESTS dropped 40-160 blocks around you, marked with glowing gold tags. Inside: AK-47, Shotgun, "
        "Sniper Rifle, Rocket Launcher, Railgun, Harpoon Gun + ammo." % placed, color="gold")
log("placed", placed)
