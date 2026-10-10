"""Gravity Staff: right-click to crush every mob round where you look to the ground, flyers too, or to lift, pull or push them."""
ASKS = ["give me the power to control gravity and stop their flight", "gravity crush", "gravity lift",
        "lift, arise"]
from kit import values, items, aim, fx, exclude, world, area, loop
import math
import re
import mc
import random
import itertools
import threading
import time

VALUES = {
    "player":         {"type": "player", "default": "@a"},
    "item":           {"type": "str", "default": "Gravity Staff"},
    "mode":           {"type": "enum", "of": ["crush", "lift", "pull", "push"], "default": "crush", "live": True},
    "range":          {"type": "int", "min": 4, "max": 300, "default": 30, "live": True},
    "cooldown":       {"type": "number", "min": 0, "max": 60, "default": 0, "live": True},
    "around_radius":  {"type": "number", "min": 1, "max": 128, "default": 40, "live": True},
    "around_what":    {"type": "enum", "of": ["mobs", "players", "everything"], "default": "mobs", "live": True},
    "around_most":    {"type": "int", "min": 0, "max": 1000, "default": 0, "live": True},
    "push_strength":  {"type": "number", "min": 0.5, "max": 40, "default": 30, "live": True},
    "freeze_seconds": {"type": "number", "min": 1, "max": 120, "default": 5, "live": True},
    "freeze_shell":   {"type": "bool", "default": False, "live": True},
    "freeze_block":   {"type": "enum", "of": ["ice", "packed_ice", "blue_ice", "snow_block"], "default": "ice", "live": True},
}


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


# --- preset: push (copied from _presets/push.py) ---
def push(hits, frm, v, way="away"):
    """Move every hit (a list of aim.Hit, from aim.near_line, around or lock_on) about
    push_strength blocks `way` (bent here from the preset's push_way value, so the staff's
    mode picks it): "away" from frm, "toward" it (never past it), or "up" into the air.
    frm: a place, or a player's name for where they stand. A mob is thrown (its
    Motion), so it flies, lands and slides; a player is moved there at once, and only into
    room to stand, since a player's Motion cannot be set; "up" floats a player up instead.
    Pets and villagers are never moved. Returns how many were moved."""
    s = v["push_strength"]
    if isinstance(frm, str):
        frm = mc.pos(frm)
        if frm is None:
            return 0
    seen, n = set(), 0
    for h in hits:
        key = h.name if h.player else (h.name, round(h.pos[0], 1), round(h.pos[1], 1), round(h.pos[2], 1))
        if key in seen:
            continue                    # listed twice: moved once
        seen.add(key)
        dx, dz = h.pos[0] - frm[0], h.pos[2] - frm[2]
        far = math.hypot(dx, dz)
        if far < 0.01:
            a = random.uniform(0, 2 * math.pi)
            dx, dz, far = math.cos(a), math.sin(a), 1.0
        ux, uz = dx / far, dz / far
        if way == "toward":
            ux, uz, s_here = -ux, -uz, min(s, far - 1.5)
            if s_here <= 0:
                continue
        else:
            s_here = s
        n += _push_player(h, ux, uz, s_here, way) if h.player else _push_mob(h, ux, uz, s_here, way)
    return n


def _push_mob(h, ux, uz, s, way):
    """Throw a mob: about 5 blocks of flight and slide for each 1 of horizontal Motion, with a
    hop of 0.4 so it leaves the ground; straight up, a Motion of sqrt(height / 5.4)."""
    if way == "up":
        motion = (0.0, math.sqrt(s / 5.4), 0.0)
    else:
        motion = (ux * s / 5.0, 0.4, uz * s / 5.0)
    x, y, z = h.pos[:3]
    mob = exclude.mobs("distance=..1.5,sort=nearest,limit=1")        # found again where it was
    reply = mc.rcon(world.prefix(world.dim(h.pos)) + "positioned %.3f %.3f %.3f %s run data modify entity @s "
                    "Motion set value [%.3fd,%.3fd,%.3fd]" % ((x, y, z, mob) + motion))
    return 1 if "Modified" in reply else 0


def _push_player(h, ux, uz, s, way):
    """Move a player: up by levitation (about 0.75 blocks a second for each level), else to
    the spot s blocks along, or half as far, whichever has room to stand."""
    if way == "up":
        level = max(0, int(round(s / 0.75)) - 1)
        return 1 if "Applied" in mc.rcon("effect give %s levitation 1 %d true" % (h.name, min(level, 127))) else 0
    x, y, z = h.pos[:3]
    for k in (s, s / 2.0):
        tx, ty, tz = x + ux * k, y + 0.1, z + uz * k
        bx, by, bz = int(math.floor(tx)), int(math.floor(ty)), int(math.floor(tz))
        reply = mc.rcon(world.prefix(world.dim(h.pos)) + "if block %d %d %d #minecraft:replaceable if block %d %d %d "
                        "#minecraft:replaceable run tp %s %.3f %.3f %.3f" % (bx, by, bz, bx, by + 1, bz, h.name, tx, ty, tz))
        if "Teleported" in reply:
            return 1
    return 0


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


v = values(VALUES)
PINNED = mc.TAG + "_pinned"
mc.on_stop(lambda: mc.rcon("tag @e[tag=%s] remove %s" % (PINNED, PINNED)))


def pin(hits):
    """Hold what was crushed on the ground below it while it is frozen, flyers and dragons
    too: anything above the surface is put back on it every 0.1 s (never up onto a roof)."""
    for h in hits:
        aim.run_on(h, "tag @s add " + PINNED)
    end = time.time() + v["freeze_seconds"]
    while time.time() < end and loop.wait(0.1):
        mc.rcon("execute as @e[tag=%s] at @s positioned over world_surface if entity @s[dy=400] run tp @s ~ ~ ~"
                % PINNED)
    mc.rcon("tag @e[tag=%s] remove %s" % (PINNED, PINNED))


for p in loop(items.clicks(v["player"], v["item"], cooldown=v["cooldown"], color="dark_purple")):
    r = aim.ray(p, v["range"])
    if r is None:
        continue
    hits = around(r.end, v)
    if v["mode"] == "crush":
        fx.sound("entity.warden.sonic_boom", r.end, volume=4, pitch=0.5)
        freeze(hits, v)
        loop.background(pin, hits)
    else:
        fx.sound("entity.generic.explode", r.end, volume=3, pitch=1.5)
        push(hits, r.end, v, {"lift": "up", "pull": "toward", "push": "away"}[v["mode"]])
