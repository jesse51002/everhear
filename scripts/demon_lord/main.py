"""Demon Lord: a game master that runs round after round of mob waves and minigames, with a boss every fourth round, harder or easier as players fare."""
ASKS = ["make sure to add a boss fight at the end", "don't just do zombies, do skeletons, wither skeletons, creepers, silverfish, do everything",
        "a game master that sends waves at us"]
# Demon Lord game master: event -> wait until its mobs are dead or 30 s pass with no
# kills -> a break -> next round, escalating. Every 4th round is a boss.
import json
import math
import random
import re
import threading
import time

from mc import rcon, players, pos, spawn, score, count, on_stop, log, TAG
from kit import values

VALUES = {
    "round":   {"type": "int", "min": 1, "max": 1000, "default": 1},
    "level":   {"type": "number", "min": 0, "max": 1000, "default": 0},
    "break_s": {"type": "number", "min": 0, "max": 600, "default": 60, "live": True},
}

PROP = TAG + "_prop"     # markers, labels, Gregor: tagged TAG and PROP, never counted as mobs
LABEL = TAG + "_label"
MOBS = "@e[tag=%s,tag=!%s]" % (TAG, PROP)
DEATHS = "vfg_dl_deaths"
BOSSBAR = "vfg:demon_lord"
PROP_TAGS = '"vfg","%s","%s"' % (TAG, PROP)
MOB_TAGS = '"vfg","%s"' % TAG

state = {"border": False, "rules": False, "blocks": [], "bossbar": False}


def speak(msg, title=None, sound="entity.wither.spawn"):
    """The Demon Lord's own voice: a dark red tellraw, with an optional title and sound."""
    if title:
        rcon("title @a times 10 50 15")
        rcon("title @a subtitle " + json.dumps({"text": title[1], "color": "gray", "italic": True}))
        rcon("title @a title " + json.dumps({"text": title[0], "color": "dark_red", "bold": True}))
    if sound:
        rcon("execute as @a at @s run playsound %s hostile @s ~ ~ ~ 0.6 0.7" % sound)
    rcon("tellraw @a " + json.dumps([{"text": "[Demon Lord] ", "color": "dark_red", "bold": True},
                                      {"text": msg, "color": "red", "bold": False}]))


def tell(p, msg, color):
    rcon("tellraw %s %s" % (p, json.dumps({"text": msg, "color": color})))


def r(a):
    return random.randint(-a, a)


def passed(cmd):
    return "passed" in rcon(cmd).lower()


def border_off():
    rcon("worldborder set 59999968")
    rcon("worldborder center 0 0")
    state["border"] = False


def sanctuary(rnd):
    ps = players(alive=True)
    c = pos(ps[0]) if ps else None
    if not c:
        return
    ang = random.uniform(0, 6.28)
    d = 25 + rnd
    x, z = int(c[0] + d * math.cos(ang)), int(c[2] + d * math.sin(ang))
    sanct = "@e[tag=%s_sanct]" % TAG
    rcon("kill " + sanct)
    rcon('summon armor_stand %d 300 %d {Tags:[%s,"%s_sanct"],Invisible:1b,Glowing:1b,Invulnerable:1b,'
         'NoGravity:1b,Marker:1b}' % (x, z, PROP_TAGS, TAG))
    rcon("spreadplayers %d %d 0 1 false %s" % (x, z, sanct))
    rcon("execute at %s run fill ~-2 ~-1 ~-2 ~2 ~-1 ~2 gold_block" % sanct)
    rcon("execute at %s run setblock ~ ~ ~ beacon" % sanct)
    rcon("execute at %s run fill ~-1 ~-2 ~-1 ~1 ~-2 ~1 iron_block" % sanct)
    t = max(25, 45 - rnd)
    rcon('title @a title {"text":"FIND THE SANCTUARY","color":"gold","bold":true}')
    speak("MINIGAME: Reach my golden sanctuary at X %d, Z %d within %d seconds. Follow the glow. "
          "Those outside... burn." % (x, z, t))
    rcon("weather thunder")
    time.sleep(t - 10)
    speak("10 seconds!")
    time.sleep(10)
    for p in players(alive=True):
        if passed("execute at %s if entity @a[name=%s,distance=..4]" % (sanct, p)):
            rcon("give %s golden_apple 1" % p)
            tell(p, "You are spared. Take this.", "gold")
        else:
            rcon("execute at %s run summon lightning_bolt" % p)
            rcon("effect give %s slowness 15 1" % p)
            rcon("effect give %s weakness 30 0" % p)
            for _ in range(2 + rnd // 3):
                spawn(p, "zombie", spread=4)
    rcon("kill " + sanct)


def defend_house(rnd):
    ps = players(alive=True)
    c = pos(ps[0]) if ps else None
    if not c:
        return
    state["border"] = True
    rcon("worldborder center %d %d" % (int(c[0]) + 6, int(c[2]) + 6))
    rcon("worldborder damage amount 2")
    rcon("worldborder set 44")
    x, z = int(c[0]) + 6, int(c[2]) + 6
    mark = "@e[tag=%s_hmark,limit=1]" % TAG
    vip = "@e[tag=%s_vip]" % TAG
    rcon("kill @e[tag=%s_hmark]" % TAG)
    rcon('summon marker %d 300 %d {Tags:[%s,"%s_hmark"]}' % (x, z, PROP_TAGS, TAG))
    rcon("spreadplayers %d %d 0 1 false %s" % (x, z, mark))
    at = "execute at %s run " % mark
    rcon(at + "fill ~-3 ~ ~-3 ~3 ~4 ~3 oak_planks")
    rcon(at + "fill ~-2 ~ ~-2 ~2 ~3 ~2 air")
    rcon(at + "fill ~-3 ~-1 ~-3 ~3 ~-1 ~3 cobblestone")
    rcon(at + "fill ~-3 ~ ~ ~3 ~1 ~ air")
    rcon(at + "fill ~ ~ ~-3 ~ ~1 ~3 air")
    rcon(at + "fill ~-3 ~2 ~-3 ~3 ~2 ~3 glass replace oak_planks")
    rcon(at + "setblock ~ ~3 ~ lantern")
    rcon(at + 'summon villager ~ ~ ~ {Tags:[%s,"%s_vip"],CustomName:"Old Gregor",PersistenceRequired:1b,'
         'Glowing:1b,attributes:[{id:"max_health",base:30d}],Health:30f}' % (PROP_TAGS, TAG))
    rcon(at + 'setblock ~1 ~ ~1 chest{Items:[{Slot:0b,id:"oak_planks",count:64},{Slot:1b,id:"oak_door",count:4},'
         '{Slot:2b,id:"arrow",count:32},{Slot:3b,id:"golden_apple",count:1}]}')
    t = 60 + 5 * rnd
    rcon('title @a title {"text":"DEFEND GREGOR","color":"green","bold":true}')
    speak("MINIGAME: Old Gregor shelters in that hut. My horde wants him dead. Keep him alive for %d seconds. "
          "Patch the doors fast!" % t)
    time.sleep(12)
    rcon("time set midnight")
    rcon("weather rain")
    end = time.time() + t
    while time.time() < end:
        if not passed("execute if entity " + vip):
            break
        for _ in range(2 + rnd // 3):
            mob = random.choice(["zombie", "zombie", "vindicator", "husk"])
            rcon(at + "summon %s ~%d ~1 ~%d {Tags:[%s],PersistenceRequired:1b}"
                 % (mob, random.choice([-14, 14]), r(14), MOB_TAGS))
        time.sleep(12)
    if passed("execute if entity " + vip):
        speak("Gregor lives?! Curse you. He rewards his saviors.")
        for p in players():
            rcon("give %s emerald 8" % p)
            rcon("give %s golden_apple 1" % p)
        rcon("kill " + vip)
    else:
        speak("Old Gregor is DEAD. His screams were delightful. Your punishment: my children stay.")
    rcon("kill @e[tag=%s_hmark]" % TAG)


def parkour(rnd):
    ps = players(alive=True)
    c = pos(ps[0]) if ps else None
    if not c:
        return
    home = {p: pos(p) for p in ps}
    x, y, z = int(c[0]), 230, int(c[2])
    blocks = state["blocks"]

    def put(bx, by, bz, b):
        rcon("setblock %d %d %d %s" % (bx, by, bz, b))
        blocks.append((bx, by, bz))

    for dx in range(-2, 3):
        for dz in range(-2, 3):
            put(x + dx, y, z + dz, "crying_obsidian")
            if (abs(dx) == 2 or abs(dz) == 2) and not (dx == 2 and dz == 0):
                put(x + dx, y + 1, z + dz, "red_stained_glass")
    cx, cy, cz = x, y, z
    mats = ["red_nether_bricks", "magma_block", "blackstone", "nether_wart_block", "soul_sand", "shroomlight"]
    for _ in range(10 + rnd // 2):
        cx += random.choice([2, 3, 3, 4] if rnd > 6 else [2, 3, 3])
        cz += random.choice([-1, 0, 1])
        cy += random.choice([0, 0, 1])
        put(cx, cy, cz, random.choice(mats))
        if rnd > 5 and random.random() < 0.3:
            put(cx, cy + 3, cz, "cobweb")
    cx += 3
    gx, gy, gz = cx, cy, cz
    for dx in range(-2, 3):
        for dz in range(-2, 3):
            put(gx + dx, gy, gz + dz, "gold_block")
            if (abs(dx) == 2 or abs(dz) == 2) and not (dx == -2 and dz == 0):
                put(gx + dx, gy + 1, gz + dz, "yellow_stained_glass")
    put(gx, gy + 1, gz, "light_weighted_pressure_plate")
    t = 90 + 5 * rnd
    rcon('title @a title {"text":"PARKOUR OF THE DAMNED","color":"dark_red","bold":true}')
    speak("MINIGAME: PARKOUR OF THE DAMNED. Reach the gold in %d seconds. Fall, and you begin again. "
          "First one there wins my favor." % t)
    state["rules"] = True
    rcon("gamerule fall_damage false")
    rcon("gamerule pvp false")
    for p in ps:
        rcon("tp %s %d %d %d -90 0" % (p, x, y + 1, z))
    end, winners = time.time() + t, []
    while time.time() < end and len(winners) < len(ps):
        for p in ps:
            if p in winners:
                continue
            if passed("execute positioned %d %d %d if entity @a[name=%s,distance=..2]" % (gx, gy + 1, gz, p)):
                winners.append(p)
                prize = ["diamond 3", "golden_apple 2", "iron_ingot 8"][min(len(winners) - 1, 2)]
                speak("%s conquers the parkour! Place #%d." % (p, len(winners)))
                rcon("give %s %s" % (p, prize))
            elif passed("execute if entity @a[name=%s,y=-64,dy=%d]" % (p, y - 10 + 64)):
                rcon("tp %s %d %d %d -90 0" % (p, x, y + 1, z))
        time.sleep(1)
    if not winners:
        speak("Not one of you reached the gold. Pathetic.")
    for p, h in home.items():
        if h:
            rcon("tp %s %s %s %s" % (p, h[0], h[1], h[2]))
    parkour_off()


def parkour_off():
    for b in state["blocks"]:
        rcon("setblock %d %d %d air" % b)
    state["blocks"] = []
    if state["rules"]:
        rcon("gamerule fall_damage true")
        rcon("gamerule pvp true")
        state["rules"] = False


def closing_ring(rnd):
    ps = players(alive=True)
    c = pos(ps[0]) if ps else None
    if not c:
        return
    size, final, t = 80, max(6, 16 - rnd // 2), 60
    speak("THE RING CLOSES. Stay inside the border or wither away. Round %d." % rnd,
          ("THE RING CLOSES", "there is nowhere to run"), "block.end_portal.spawn")
    state["border"] = True
    rcon("worldborder center %.0f %.0f" % (c[0], c[2]))
    rcon("spawnpoint @a %.0f %.0f %.0f" % (c[0], c[1], c[2]))
    rcon("worldborder damage amount 1")
    rcon("worldborder damage buffer 0")
    rcon("worldborder warning distance 10")
    rcon("worldborder set %d" % size)
    time.sleep(5)
    rcon("worldborder set %d %d" % (final, t))
    for i in range(t // 10):
        for p in players(alive=True):
            for _ in range(1 + rnd // 4):
                spawn(p, random.choice(["zombie", "skeleton", "spider", "creeper"]), spread=5)
            rcon("execute as %s at @s run playsound entity.warden.heartbeat hostile @s ~ ~ ~ 1 %.1f" % (p, 0.6 + i * 0.1))
        time.sleep(10)
    speak("The ring holds you... survive 20 more seconds.", sound="entity.warden.roar")
    time.sleep(20)
    border_off()
    speak("The border breaks. You live... for now.", ("RELEASED", "for now"), "block.beacon.deactivate")


def blood_moon(rnd):
    speak("BLOOD MOON. You cannot see me, but I see you. Round %d." % rnd,
          ("BLOOD MOON", "the dark is hungry"), "entity.warden.emerge")
    rcon("time set midnight")
    rcon("weather thunder")
    for p in players(alive=True):
        rcon("effect give %s darkness 45 0" % p)
        rcon("effect give %s hunger 30 1" % p)
        for _ in range(3 + rnd // 2):
            spawn(p, random.choice(["zombie", "skeleton", "spider", "husk", "witch"]),
                  "Glowing:1b" if rnd < 6 else "", spread=12)
    for _ in range(6):
        time.sleep(7)
        rcon("execute as @a at @s run playsound entity.warden.heartbeat hostile @s ~ ~ ~ 1 0.5")


def lightning_storm(rnd):
    speak("The sky answers to ME. Keep moving or be struck. Round %d." % rnd)
    rcon("weather thunder")
    for _ in range(10 + rnd):
        for p in players(alive=True):
            rcon("execute at %s run summon lightning_bolt ~%d ~ ~%d" % (p, r(6), r(6)))
        time.sleep(2)
    rcon("weather clear")


def arena(size):
    cs = [c for c in (pos(p) for p in players(alive=True)) if c]
    if not cs:
        return
    state["border"] = True
    rcon("spawnpoint @a %.0f %.0f %.0f" % cs[0])  # respawn inside the border
    cx = sum(c[0] for c in cs) / len(cs)
    cz = sum(c[2] for c in cs) / len(cs)
    span = max(max(abs(c[0] - cx), abs(c[2] - cz)) for c in cs)
    rcon("worldborder center %.0f %.0f" % (cx, cz))
    rcon("worldborder damage amount 2")
    rcon("worldborder damage buffer 0")
    rcon("worldborder warning distance 5")
    rcon("worldborder set %d" % int(max(size, span * 2 + 10)))
    rcon('title @a actionbar {"text":"The arena seals around you...","color":"dark_red"}')


def supply(p, rnd):
    eq = rcon("data get entity %s equipment" % p)
    mat = "diamond" if rnd >= 6 else "iron"
    items = ["%s_%s" % (mat, piece) for slot, piece in
             (("head", "helmet"), ("chest", "chestplate"), ("legs", "leggings"), ("feet", "boots"))
             if "%s:" % slot not in eq]
    inv = rcon("data get entity %s Inventory" % p)
    if "sword" not in inv:
        items.append(mat + "_sword")
    if "shield" not in eq and "shield" not in inv:
        items.append("shield")
    if "bow" not in inv:
        items.append("bow")
    if "_axe" not in inv:
        items.append(mat + "_axe")
    items += [("arrow", 24), ("cooked_beef", 12), ("golden_apple", 1 + rnd // 4), ("torch", 16)]
    if rnd >= 8:
        items.append("totem_of_undying")
    st = ",".join('{Slot:%db,id:"%s",count:%d}' % (i, it if isinstance(it, str) else it[0],
                                                  1 if isinstance(it, str) else it[1])
                  for i, it in enumerate(items))
    rcon("execute at %s run setblock ~1 ~ ~ chest{Items:[%s]} replace" % (p, st))
    # Easy to find: a glowing floating label above it for 40 s, and a sound.
    rcon('execute at %s run summon armor_stand ~1.5 ~1 ~0.5 {Tags:[%s,"%s"],Invisible:1b,Marker:1b,Glowing:1b,'
         'NoGravity:1b,CustomNameVisible:1b,CustomName:{"text":"SUPPLIES","color":"gold","bold":true}}'
         % (p, PROP_TAGS, LABEL))
    rcon("execute at %s run particle totem_of_undying ~1 ~1 ~ 0.3 0.6 0.3 0.2 60" % p)
    rcon("execute as %s at @s run playsound block.chest.open master @s ~ ~ ~ 1 1" % p)
    rcon('title %s actionbar {"text":"Supply chest right next to you (east, +X)!","color":"gold"}' % p)
    timer = threading.Timer(40, lambda: rcon("kill @e[tag=%s]" % LABEL))
    timer.daemon = True
    timer.start()


BOSSES = [
    ("The Gravekeeper", "wither_skeleton",
     'attributes:[{id:"scale",base:2.0d},{id:"max_health",base:%d}],Health:%df,'
     'equipment:{mainhand:{id:"netherite_sword",count:1}},drop_chances:{mainhand:0f}'),
    ("Spider Queen", "spider",
     'attributes:[{id:"scale",base:3.0d},{id:"max_health",base:%d}],Health:%df,Glowing:1b'),
    ("Ravager Warlord", "ravager",
     'attributes:[{id:"max_health",base:%d}],Health:%df,Passengers:[{id:"evoker",Tags:[' + MOB_TAGS + ']}]'),
    ("The Hollow King", "zombie",
     'attributes:[{id:"scale",base:1.5d},{id:"max_health",base:%d},{id:"movement_speed",base:0.32d}],Health:%df,'
     'equipment:{head:{id:"netherite_helmet",count:1},chest:{id:"netherite_chestplate",count:1},'
     'mainhand:{id:"netherite_axe",count:1}},drop_chances:{head:0f,chest:0f,mainhand:0f}'),
]


def gather():
    """Bring stragglers to the group, and everyone to survival."""
    ps = players(alive=True)
    rcon("gamemode survival @a")
    if len(ps) < 2:
        return
    cs = {p: pos(p) for p in ps}

    def near(p):
        return sum(1 for q in ps if cs[p] and cs[q] and abs(cs[p][0] - cs[q][0]) + abs(cs[p][2] - cs[q][2]) < 40)

    anchor = max(ps, key=near)
    a = cs[anchor]
    if not a:
        return
    for p in ps:
        c = cs[p]
        if c and abs(c[0] - a[0]) + abs(c[2] - a[2]) > 40:
            rcon("tp %s %s" % (p, anchor))
            tell(p, "The Demon Lord drags you back to your allies.", "dark_purple")


RESPAWN_HERE = ("execute as @a at @s unless block ~ ~-1 ~ air unless block ~ ~-2 ~ air unless block ~ ~-3 ~ air "
                "if entity @s[y=-64,dy=250] run spawnpoint @s ~ ~ ~")


def event(rnd):
    ps = players(alive=True)
    if not ps:
        return None
    gather()
    ps = players(alive=True)
    rcon(RESPAWN_HERE)  # respawn near the event
    n = min(1 + rnd // 2, 6)
    for p in ps:
        supply(p, rnd)
    rcon('tellraw @a {"text":"A supply chest has dropped beside you. Arm up - 20 seconds.","color":"gold"}')
    time.sleep(20)
    kind = [0, 1, 7, 11, 10, 12, 2, 9, 4, 11, 8, 5, 10, 12, 3, 6][(ROUND - 1) % 16]
    if kind == 11:
        closing_ring(rnd)
        return None
    if kind == 12:
        blood_moon(rnd)
        return None
    if kind == 10:
        parkour(rnd)
        return None
    if kind == 9:
        defend_house(rnd)
        return None
    if kind == 7:
        sanctuary(rnd)
        return None
    if kind == 8:
        lightning_storm(rnd)
        return None
    rcon("weather rain" if kind in (0, 4, 6) else "weather clear")
    arena(30 + 2 * rnd)
    if ROUND % 4 == 0:  # boss round
        name, mob, nbt = BOSSES[(ROUND // 4 - 1) % len(BOSSES)]
        hp = 40 + 12 * rnd * len(ps)
        speak("Prepare yourselves. Supplies have fallen... you will need them. %s awakens in 15 seconds." % name,
              (name.upper(), "awakens in 15 seconds"), "entity.ender_dragon.growl")
        for p in ps:
            supply(p, rnd)
        arena(40)
        time.sleep(15)
        target = random.choice(ps)
        spawn(target, mob, 'CustomName:"%s",' % name + nbt % (hp, hp), spread=4)
        rcon("weather thunder")
        rcon("time set midnight")
        state["bossbar"] = True
        rcon('bossbar add %s "%s"' % (BOSSBAR, name))
        rcon("bossbar set %s max %d" % (BOSSBAR, hp))
        rcon("bossbar set %s color red" % BOSSBAR)
        rcon("bossbar set %s players @a" % BOSSBAR)
        for p in ps:
            for _ in range(rnd // 4 + 1):
                spawn(p, random.choice(["zombie", "skeleton", "spider"]))
        return name
    lines = ["My children of the web hunger.", "The dead rise to greet you.", "Fire from the heavens! Look up.",
             "Feel the warmth of my domain...", "My archers have you surrounded.", "Sssssomething is behind you.",
             "The phantoms of your sleepless nights return."]
    subs = ["webs", "the dead rise", "look up", "heat", "archers", "ssss", "phantoms"]
    speak(lines[kind] + " Round %d." % rnd, ("ROUND %d" % rnd, subs[kind]))
    time.sleep(5)
    for p in players(alive=True):
        if kind == 0:
            for _ in range(12):
                rcon("execute at %s run setblock ~%d ~ ~%d cobweb keep"
                     % (p, random.choice([-1, 1]) * random.randint(3, 8), random.choice([-1, 1]) * random.randint(3, 8)))
            rcon("give %s shears 1" % p)
            for _ in range(n):
                spawn(p, random.choice(["spider", "cave_spider"]), spread=4)
        elif kind == 1:
            for _ in range(n * 2):
                spawn(p, random.choice(["zombie", "zombie", "husk"]),
                      "IsBaby:1b" if random.random() < 0.15 else "", spread=8)
        elif kind == 2:
            for _ in range(n):
                rcon("execute at %s run summon tnt ~%d ~20 ~%d {fuse:70,Tags:[%s]}"
                     % (p, random.choice([-1, 1]) * random.randint(4, 9), random.choice([-1, 1]) * random.randint(4, 9),
                        MOB_TAGS))
        elif kind == 3:
            for _ in range(n):
                rcon("execute at %s run setblock ~%d ~6 ~%d lava keep"
                     % (p, random.choice([-1, 1]) * random.randint(3, 7), random.choice([-1, 1]) * random.randint(3, 7)))
            rcon("give %s water_bucket 1" % p)
            for _ in range(max(1, n // 4)):
                spawn(p, "blaze", spread=5, y=3)
        elif kind == 4:
            for _ in range(n):
                spawn(p, random.choice(["skeleton", "skeleton", "stray"]), spread=9)
            spawn(p, "witch")
        elif kind == 5:
            for _ in range(max(1, n // 2)):
                spawn(p, "creeper", "powered:1b" if rnd > 7 and random.random() < 0.3 else "", spread=6)
            rcon("execute at %s run summon tnt ~6 ~20 ~6 {fuse:70,Tags:[%s]}" % (p, MOB_TAGS))
        elif kind == 6:
            for _ in range(max(2, n // 2)):
                spawn(p, "phantom", spread=6, y=12)
    return None


def wait_until_over(boss):
    time.sleep(15)
    last, last_change = count(MOBS), time.time()
    while True:
        c = count(MOBS)
        if boss:
            sel = '@e[tag=%s,name="%s",limit=1]' % (TAG, boss)
            out = rcon("data get entity %s Health" % sel)
            m = re.search(r"([\d.]+)f", out)
            if m:
                rcon("bossbar set %s value %d" % (BOSSBAR, int(float(m.group(1)))))
            elif "No entity" in out:
                speak("%s has fallen... impressive, mortals. Enjoy your break." % boss)
                boss = None
                rcon("bossbar remove " + BOSSBAR)
                state["bossbar"] = False
        if c == 0:
            break
        if c < last:
            last, last_change = c, time.time()
        if time.time() - last_change > 30:
            speak("Cowards hide and my servants wander lost. I recall them... for now.")
            rcon("kill " + MOBS)
            break
        time.sleep(3)
    rcon("bossbar remove " + BOSSBAR)
    state["bossbar"] = False


def cleanup():
    rcon("kill " + MOBS)
    if state["bossbar"]:
        rcon("bossbar remove " + BOSSBAR)
    if state["border"]:
        border_off()
    parkour_off()
    rcon("scoreboard objectives remove " + DEATHS)


v = values(VALUES)
ROUND = v["round"]
lvl = float(v["level"] or ROUND)     # level 0: start at the round's own difficulty
on_stop(cleanup)
rcon("scoreboard objectives add %s deathCount" % DEATHS)
log("starting at round", ROUND, "level", lvl)
while True:
    while not players():
        time.sleep(30)
    while not players(alive=True):
        time.sleep(3)
    log("round", ROUND, "level", round(lvl, 1))
    before = {p: score(p, DEATHS) for p in players()}
    n_players = max(1, len(before))
    boss = event(max(1, int(lvl)))
    wait_until_over(boss)
    if state["border"]:
        border_off()
    rcon(RESPAWN_HERE)  # respawn where you ended the event
    d = sum(max(0, score(p, DEATHS) - before[p]) for p in players() if p in before)
    ratio = d / n_players
    if ratio >= 1.5:
        lvl = max(1, lvl - 2)
        speak("%d deaths... you are too fragile. I will hold back. A little." % d, sound="entity.wither.ambient")
        for p in players(alive=True):
            supply(p, int(lvl))
    elif ratio >= 0.7:
        lvl = max(1, lvl - 0.5)
    elif d == 0:
        lvl += 1.5
        speak("Not a single death? Then I stop holding back.", sound="entity.ender_dragon.growl")
    else:
        lvl += 1
    log("round", ROUND, "over:", d, "deaths -> level", round(lvl, 1))
    ROUND += 1
    time.sleep(v["break_s"])
