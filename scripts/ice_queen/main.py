"""Ice Queen: a giant stray in ice gear near a player, with a blue bar; every 8 s she freezes everyone near her in ice or calls frost servants."""
ASKS = ["put a boss in the ice castle and make her super overpowered", "throw ice spikes everywhere and make her scary",
        "a she-boss", "ice queen"]
from kit import values, fx, loop, mobs, world, aim, exclude, area
import time
import mc
import math
import re
import itertools
import threading
import json

VALUES = {
    "player":            {"type": "player", "default": "@a"},
    "at":                {"type": "list", "of": "number", "default": []},
    "cold":              {"type": "number", "min": 0, "max": 100, "default": 6, "live": True},
    "boss_name":         {"type": "str", "default": "Ice Queen"},
    "boss_mob":          {"type": "mob", "default": "stray"},
    "boss_bodies":       {"type": "int", "min": 1, "max": 8, "default": 1},
    "boss_health":       {"type": "int", "min": 1, "max": 1024, "default": 1000},
    "boss_scale":        {"type": "number", "min": 0.25, "max": 16, "default": 2},
    "boss_attack":       {"type": "number", "min": 0, "max": 100, "default": 8},
    "boss_gear":         {"type": "enum", "of": ["none", "leather", "golden", "iron", "diamond", "netherite", "ice"], "default": "ice"},
    "boss_bar":          {"type": "enum", "of": ["red", "blue", "green", "pink", "purple", "white", "yellow"], "default": "blue"},
    "boss_every":        {"type": "number", "min": 1, "max": 120, "default": 8, "live": True},
    "boss_phases":       {"type": "list", "of": "number", "default": [0.5, 0.25]},
    "boss_reward":       {"type": "item", "default": "blue_ice", "live": True},
    "boss_reward_count": {"type": "int", "min": 0, "max": 64, "default": 16, "live": True},
    "boss_music":        {"type": "enum", "of": ["none", "pigstep", "otherside", "relic", "creator", "precipice"], "default": "none"},
    "boss_griefing":     {"type": "bool", "default": True},
    "around_radius":     {"type": "number", "min": 1, "max": 128, "default": 16, "live": True},
    "around_what":       {"type": "enum", "of": ["mobs", "players", "everything"], "default": "players", "live": True},
    "around_most":       {"type": "int", "min": 0, "max": 1000, "default": 0, "live": True},
    "freeze_seconds":    {"type": "number", "min": 1, "max": 120, "default": 4, "live": True},
    "freeze_shell":      {"type": "bool", "default": True, "live": True},
    "freeze_block":      {"type": "enum", "of": ["ice", "packed_ice", "blue_ice", "snow_block"], "default": "packed_ice", "live": True},
    "summon_mob":        {"type": "mob", "default": "stray", "live": True},
    "summon_count":      {"type": "int", "min": 1, "max": 200, "default": 3, "live": True},
    "summon_spread":     {"type": "int", "min": 1, "max": 100, "default": 4, "live": True},
    "summon_cap":        {"type": "int", "min": 1, "max": 1000, "default": 6, "live": True},
    "summon_scale":      {"type": "number", "min": 0.1, "max": 16, "default": 1, "live": True},
    "summon_health":     {"type": "number", "min": 0, "max": 2048, "default": 0, "live": True},
    "summon_gear":       {"type": "enum", "of": ["none", "leather", "chainmail", "iron", "golden", "diamond", "netherite"], "default": "none", "live": True},
    "summon_weapon":     {"type": "str", "default": "", "live": True},
    "summon_name":       {"type": "str", "default": "Frost Servant", "live": True},
    "summon_glow":       {"type": "bool", "default": False, "live": True},
    "summon_nbt":        {"type": "str", "default": "", "live": True},
}


# --- preset: boss (copied from _presets/boss.py) ---
def boss(where, v, *, abilities=None, phases=None):
    """Summon the boss near a player (a player value: it waits until one of them is online)
    or at a place (a Pos), and run the fight until every body is dead: then its minions die,
    its loot drops where it fell, and it returns True. False if there was no room for it, or
    the script stopped first.

    abilities: functions f(b, v), one called in turn every boss_every s (default: minions).
    phases: functions f(b, v), the first called when its health falls below the first share
    in boss_phases, the next below the next (the last repeats; default: it enrages).
    b is the kit's mobs.Boss: b.tag selects the bodies, b.phase counts the phases from 0."""
    b = mobs.Boss(v["boss_name"], v["boss_mob"], v["boss_health"], v["boss_bodies"], v["boss_scale"],
                  _boss_gear(v["boss_gear"]), v["boss_bar"])
    near = None
    if isinstance(where, str):
        for _ in loop(every=1):
            near = (loop.who(where) or [None])[0]
            if near:
                break
        if not near:
            return False
    mc.rcon("kill @e[tag=%s]" % b.tag)                 # what a run killed outright left behind
    mc.rcon("bossbar remove %s" % b.bar)
    if not v["boss_griefing"]:
        world.gamerule("mob_griefing", False)          # put back when it dies, or the script stops
    if not b.spawn(near=near, at=None if near else where):
        mc.log("no room for", v["boss_name"])
        b.remove()
        return False
    b.phase, body = 0, "@e[tag=%s,limit=1]" % b.tag
    dim = world.dim_of(near) if near else world.dim(where)
    b.dim = dim                                        # bent: her abilities act in her dimension
    mc.rcon("execute as @e[tag=%s] run attribute @s attack_damage base set %g" % (b.tag, v["boss_attack"]))
    fx.title("@a", v["boss_name"], sub="has risen", color="dark_red")
    fx.sound("entity.wither.spawn", body, volume=4, pitch=0.8)
    mc.log("boss", v["boss_name"], "is up")

    abilities = list(abilities or [])                  # bent: her abilities are the script's
    phases = [_boss_enrage] if phases is None else list(phases)
    shares = sorted((s for s in v["boss_phases"] if 0 < s < 1), reverse=True)
    full = float(v["boss_health"] * v["boss_bodies"])
    music = "minecraft:music_disc." + v["boss_music"]
    track = _BOSS_TRACKS.get(v["boss_music"])
    if track:
        mc.on_stop(lambda: mc.rcon("stopsound @a record " + music))
    job, turn, due, replay, last = None, 0, time.time() + v["boss_every"], 0.0, None
    while not mc.stopping():
        if not b.update():                             # the bar follows the bodies; 0 when all are dead
            break
        at = mc.pos(body)
        last = world.Pos(at[0], at[1], at[2], dim) if at else last
        health = b.health() if shares else None
        if health and health[1] and health[0] < full * shares[0]:
            shares.pop(0)
            b.phase += 1
            if phases:
                loop.background(phases[min(b.phase, len(phases)) - 1], b, v)
        if abilities and time.time() >= due and not (job and job.is_alive()):
            job = loop.background(abilities[turn % len(abilities)], b, v)
            turn, due = turn + 1, time.time() + v["boss_every"]
        if track and time.time() >= replay:
            mc.rcon("execute as @a at @s run playsound %s record @s ~ ~ ~ 1 1" % music)
            replay = time.time() + track
        loop.wait(1)
    if track:
        mc.rcon("stopsound @a record " + music)
    if mc.stopping():
        return False
    if job:
        job.join(10)                                   # an ability still summoning
    mc.rcon("kill @e[tag=%s_minion]" % b.tag)
    if last and v["boss_reward_count"]:
        mc.rcon(world.run(last.dim, 'summon item %.2f %.2f %.2f {Item:{id:"%s",count:%d}}' % (
            last[0], last[1] + 0.5, last[2], v["boss_reward"].split("[")[0], v["boss_reward_count"])))
    fx.title("@a", "VICTORY", sub="%s has fallen" % v["boss_name"], color="gold")
    fx.sound("ui.toast.challenge_complete", None, to="@a")
    if not v["boss_griefing"]:
        world.restore("mob_griefing")
    mc.log("boss", v["boss_name"], "is dead")
    return True


# Each record's length in seconds, so the boss music comes round again.
_BOSS_TRACKS = {"pigstep": 148, "otherside": 195, "relic": 218, "creator": 176, "precipice": 299}


def _boss_gear(gear):
    """The NBT for a full set of armour and a sword, none of it dropped; "" for none."""
    if gear == "none":
        return ""
    if gear == "ice":                                  # bent: a diamond crown and boots, pale blue robes, an iron sword
        robe = '{id:"%s",count:1,components:{"minecraft:dyed_color":8900331}}'
        return ('equipment:{head:{id:"diamond_helmet",count:1},chest:%s,legs:%s,feet:{id:"diamond_boots",count:1},'
                'mainhand:{id:"iron_sword",count:1}},drop_chances:{head:0f,chest:0f,legs:0f,feet:0f,mainhand:0f}'
                % (robe % "leather_chestplate", robe % "leather_leggings"))
    worn = ['%s:{id:"%s_%s",count:1}' % (slot, gear, piece) for slot, piece in
            (("head", "helmet"), ("chest", "chestplate"), ("legs", "leggings"), ("feet", "boots"))]
    worn.append('mainhand:{id:"%s_sword",count:1}' % ("wooden" if gear == "leather" else gear))
    return "equipment:{%s},drop_chances:{head:0f,chest:0f,legs:0f,feet:0f,mainhand:0f}" % ",".join(worn)


def _boss_enrage(b, v):
    """The default phase: faster and stronger with each phase, and a roar."""
    mc.rcon("effect give @e[tag=%s] speed infinite %d true" % (b.tag, b.phase - 1))
    mc.rcon("effect give @e[tag=%s] strength infinite %d true" % (b.tag, b.phase - 1))
    fx.sound("entity.ravager.roar", "@e[tag=%s,limit=1]" % b.tag, volume=4, pitch=0.7)
    fx.actionbar("@a", "%s is enraged" % v["boss_name"], "red")


# --- preset: around (copied from _presets/around.py) ---
def around(at, v):
    """Every mob, player or both (around_what) within around_radius of a place or a player
    (a name: never that player), as a list of aim.Hit nearest first, the shape aim.near_line
    gives, so damage_hits, freeze and push take either. around_most: the nearest that many
    (0: all). Never a pet, a villager or anything else exclude spares, nor a player in
    creative or spectator. For the blocks round a place, area.box(at, r) and area.sphere."""
    me = ""
    if isinstance(at, str):
        me, feet = at, mc.pos(at)
        if feet is None:
            return []
        at = world.Pos(feet[0], feet[1], feet[2], world.dim_of(me))
    r, what = v["around_radius"], v["around_what"]
    found = {}
    if what in ("mobs", "everything"):
        _around_ask(at, exclude.mobs("distance=..%g" % r), r, False, found)
    if what in ("players", "everything"):
        _around_ask(at, exclude.players(me, "distance=..%g" % r), r, True, found)
    dim = world.dim(at)
    hits = []
    for (x, y, z, player), name in found.items():
        t = math.sqrt((x - at[0]) ** 2 + (y - at[1]) ** 2 + (z - at[2]) ** 2)
        hits.append(aim.Hit(t, world.Pos(x, y, z, dim), mc.bare(name) if player else name, player))
    hits.sort(key=lambda h: h.t)
    return hits[:v["around_most"]] if v["around_most"] else hits


_AROUND_POS = re.compile(r"([^\]\[]*?) has the following entity data: \[(-?[\d.eE-]+)d, (-?[\d.eE-]+)d, (-?[\d.eE-]+)d\]")


def _around_ask(at, frag, r, player, found, box=None, depth=0):
    """Ask where everything a fragment selects is; a reply cut at 4 KB is asked again in
    eight smaller boxes."""
    if box is None:
        box = (at[0] - r, at[1] - r, at[2] - r, 2 * r)
    x, y, z, size = box
    inside = "" if depth == 0 else "positioned %.2f %.2f %.2f if entity @s[dx=%.2f,dy=%.2f,dz=%.2f] " % (
        x, y, z, size, size, size)
    reply = mc.rcon(world.prefix(world.dim(at)) + "positioned %.2f %.2f %.2f %s %srun data get entity @s Pos" % (
        at[0], at[1], at[2], frag, inside))
    if len(reply.encode("utf-8")) >= world.MAX_REPLY - 200 and depth < 3:
        half = size / 2.0
        for i in range(8):
            _around_ask(at, frag, r, player, found,
                        (x + half * (i & 1), y + half * (i >> 1 & 1), z + half * (i >> 2 & 1), half), depth + 1)
        return
    for m in _AROUND_POS.finditer(reply):
        found[(float(m.group(2)), float(m.group(3)), float(m.group(4)), player)] = m.group(1).strip()


# --- preset: freeze (copied from _presets/freeze.py) ---
def freeze(hits, v):
    """Freeze every hit (a list of aim.Hit, from aim.near_line, around or lock_on) for
    freeze_seconds: it cannot walk or hurt anything, and a mob shows the frost (and takes
    the cold's 1 damage every 2 s, as in powder snow). With freeze_shell, a shell of
    freeze_block round each body, one block wide and two high, that melts when the freeze
    ends; it only fills air, so nothing is replaced (never bedrock, never a chest). Pets and
    villagers are never frozen. Returns how many were frozen."""
    secs = int(math.ceil(v["freeze_seconds"]))
    tag = "%s_frz%d" % (mc.TAG, next(_FREEZE_N))      # each call tags with its own name
    mc.rcon("tag @e[tag=%s] remove %s" % (tag, tag))    # bent: one an earlier run left would spare them
    frozen = [h for h in hits if _freeze_mark(h, tag)]
    if not frozen:
        return 0
    # Slowness and weakness X (amplifier 9; above 127 it is saved as a negative byte) leave
    # no speed and no attack, and wear off by themselves even if the script dies.
    mc.rcon("effect give @e[tag=%s] slowness %d 9 true" % (tag, secs))
    mc.rcon("effect give @e[tag=%s] weakness %d 9 true" % (tag, secs))
    mc.rcon("execute as @e[tag=%s,type=!player] run data merge entity @s {TicksFrozen:%d}" % (tag, 140 + 40 * secs))
    mc.rcon("execute at @e[tag=%s] run particle snowflake ~ ~1 ~ 0.4 0.6 0.4 0.02 25 force" % tag)
    mc.rcon("tag @e[tag=%s] remove %s" % (tag, tag))
    if v["freeze_shell"]:
        shells = [_freeze_shell(h.pos, v["freeze_block"]) for h in frozen]
        with _FREEZE_LOCK:
            if not _FREEZE_SHELLS:
                mc.on_stop(lambda: [_freeze_melt(s) for s in list(_FREEZE_SHELLS)])
            _FREEZE_SHELLS.extend(shells)
        loop.background(_freeze_thaw, shells, secs)
    return len(frozen)


_FREEZE_N = itertools.count(1)
_FREEZE_SHELLS = []         # shells still standing; a stop melts them
_FREEZE_LOCK = threading.Lock()


def _freeze_mark(h, tag):
    """Tag one hit where it was: a player by name, a mob as the nearest within 1.5 blocks that
    a hit may touch. True if it was tagged now (a hit listed twice is tagged once)."""
    if h.player:
        reply = mc.rcon("tag %s add %s" % (h.name, tag))
    else:
        x, y, z = h.pos[:3]
        reply = mc.rcon(world.prefix(world.dim(h.pos)) + "positioned %.3f %.3f %.3f %s run tag @s add %s" % (
            x, y, z, exclude.mobs("distance=..1.5,sort=nearest,limit=1"), tag))
    return reply.startswith("Added tag")


def _freeze_shell(at, block):
    """A shell of block round the body standing at a place, put into air only: a floor, a roof
    and four walls, leaving the body's own two blocks free. Returns (block, dim, boxes)."""
    x, y, z = (int(math.floor(c)) for c in at[:3])
    boxes = [(x - 1, y - 1, z - 1, x + 1, y - 1, z + 1), (x - 1, y + 2, z - 1, x + 1, y + 2, z + 1),
             (x - 1, y, z - 1, x + 1, y + 1, z - 1), (x - 1, y, z + 1, x + 1, y + 1, z + 1),
             (x - 1, y, z, x - 1, y + 1, z), (x + 1, y, z, x + 1, y + 1, z)]
    dim, loaded = world.dim(at), {}
    for b in boxes:
        area.fill(b, block, "keep", dim=dim, spare_protected=True, keep_bedrock=True, most_mspt=0, loaded=loaded)
    return (block, dim, boxes)


def _freeze_thaw(shells, secs):
    """Melt shells once their freeze is over."""
    if loop.wait(secs):
        for s in shells:
            _freeze_melt(s)


def _freeze_melt(shell):
    """Turn a shell's block back to air, in the shell's own boxes only. Plain fills (a few
    blocks each), because the kit's fill does nothing once the script is stopping."""
    block, dim, boxes = shell
    with _FREEZE_LOCK:
        if shell not in _FREEZE_SHELLS:
            return
        _FREEZE_SHELLS.remove(shell)
    for b in boxes:
        mc.rcon(world.run(dim, "fill %d %d %d %d %d %d air replace %s" % (b + (block,))))


# --- preset: summon (copied from _presets/summon.py) ---
_summon_n = [0]


def summon(at, v):
    """summon_count of summon_mob round `at` (a player's name, a player value such as "@a" or
    "Steve,Alex" for round each of them, or a place), at safe spots summon_spread blocks out:
    standing room on solid ground, never on top of anyone. summon_scale sizes them;
    summon_health (0: their own) sets their health; summon_gear a full suit of that armour and
    summon_weapon an item in hand, neither dropped; summon_name a name over their heads;
    summon_glow shows them through walls; summon_nbt is more NBT ("IsBaby:1b"). Never more
    than summon_cap of this script's alive at once. They are tagged TAG_summon, and a stop
    kills them. Returns how many it summoned."""
    tag, anchor = mc.TAG + "_summon", None
    if isinstance(at, str):
        spots = loop.who(at)
    else:
        _summon_n[0] += 1
        anchor = "%s_at%d" % (mc.TAG, _summon_n[0])
        reply = mc.rcon(world.run(at, 'summon marker %.2f %.2f %.2f {Tags:["vfg","%s","%s"]}'
                                  % (at[0], at[1], at[2], mc.TAG, anchor)))
        if "summoned" not in reply.lower():
            mc.log("summon: nowhere to stand at %.0f %.0f %.0f: %s" % (at[0], at[1], at[2], reply[:80]))
            return 0
        spots = ["@e[type=marker,tag=%s,limit=1]" % anchor]
    got = 0
    for spot in spots:
        room = v["summon_cap"] - mobs.count(tag)
        if room <= 0:
            mc.log("summon: %d alive, the cap" % v["summon_cap"])
            break
        got += mobs.around(spot, v["summon_mob"], min(v["summon_count"], room), _summon_nbt(v),
                           spread=v["summon_spread"], tags=(tag,))
    if anchor:
        mc.rcon("kill @e[type=marker,tag=%s]" % anchor)
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


def ice_blast(b, v):
    """Her first ability: everyone within around_radius of her is frozen in a shell of ice
    and hurt by the cold."""
    at = mc.pos("@e[tag=%s,limit=1]" % b.tag)
    if not at:
        return
    here = world.Pos(at[0], at[1], at[2], b.dim)
    fx.particle("snowflake", here, (4, 1, 4), 150, 0.05, up=1)
    fx.sound("block.glass.break", here, volume=3, pitch=0.6)
    hits = around(here, v)
    freeze(hits, v)
    for h in hits:
        if v["cold"]:
            aim.run_on(h, "damage @s %g minecraft:freeze by @e[tag=%s,limit=1]" % (v["cold"], b.tag))


def frost_servants(b, v):
    """Her second ability: summon_count frost servants round her, never more than summon_cap."""
    at = mc.pos("@e[tag=%s,limit=1]" % b.tag)
    if at and summon(world.Pos(at[0], at[1], at[2], b.dim), v):
        fx.sound("entity.evoker.prepare_summon", "@e[tag=%s,limit=1]" % b.tag, volume=3, pitch=1.4)


v = values(VALUES)
where = world.Pos(v["at"][0], v["at"][1], v["at"][2]) if len(v["at"]) >= 3 else v["player"]
boss(where, v, abilities=[ice_blast, frost_servants])      # every boss_every s, each in turn
mc.rcon("kill @e[tag=%s_summon]" % mc.TAG)                # her servants fall with her,
for shell in list(_FREEZE_SHELLS):                         # and her ice melts now: the script ends here
    _freeze_melt(shell)
