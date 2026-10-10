"""Projectile Shield: arrows, tridents, fireballs and skulls that fly at a player turn round a few blocks from them and fly back the way they came."""
ASKS = ["make sure all projectiles bounce off me", "give me the ability to deflect",
        "deflect everything", "can you make projectiles deflect off of me"]
from kit import values, aim, exclude, fx, loop, world
import math
import re
import mc
import random

VALUES = {
    "player":        {"type": "player", "default": "@a", "live": True},
    "around_radius": {"type": "number", "min": 1, "max": 128, "default": 8, "live": True},
    "around_what":   {"type": "enum", "of": ["mobs", "hostile", "players", "everything", "projectiles"], "default": "projectiles", "live": True},
    "around_most":   {"type": "int", "min": 0, "max": 1000, "default": 0, "live": True},
    "push_way":      {"type": "enum", "of": ["away", "toward", "up"], "default": "away", "live": True},
    "push_strength": {"type": "number", "min": 0.5, "max": 40, "default": 8, "live": True},
}


# --- preset: around (copied from _presets/around.py) ---
def around(at, v):
    """Every mob, player or both (around_what) within around_radius of a place or a player
    (a name: never that player), as a list of aim.Hit nearest first, the shape aim.near_line
    gives, so damage_hits, freeze and push take either. "hostile": monsters only, never an
    animal; "projectiles": arrows, tridents, fireballs and the like still flying, never the
    player's own nor one this script turned already, each found again by its uuid. around_most:
    the nearest that many (0: all). Never a pet, a villager or anything else exclude spares,
    nor a player in creative or spectator. For the blocks round a place, area.box(at, r) and
    area.sphere."""
    me = ""
    if isinstance(at, str):
        me, feet = at, mc.pos(at)
        if feet is None:
            return []
        at = world.Pos(feet[0], feet[1], feet[2], world.dim_of(me))
    r, what = v["around_radius"], v["around_what"]
    if what == "projectiles":
        hits = _around_projectiles(at, me, r)
    else:
        hits = aim.near(at, r, shooter=me, with_players=what in ("players", "everything"),
                        hostile=what == "hostile", with_mobs=what != "players")
    return hits[:v["around_most"]] if v["around_most"] else hits


_AROUND_UUID = re.compile(r"([^\]\[]*?) has the following entity data: \[I; (-?\d+), (-?\d+), (-?\d+), (-?\d+)\]")
_AROUND_OWN = {}            # player -> ",nbt=!{Owner:[I;...]}": their own projectiles are not turned


def _around_projectiles(at, me, r):
    """Arrows, tridents, fireballs and the like in flight within r of a place, never me's own
    nor one this script turned already (tagged TAG), as hits named by their uuid."""
    if me and me not in _AROUND_OWN:
        m = _AROUND_UUID.search(mc.rcon("data get entity %s UUID" % me))
        if m:
            _AROUND_OWN[me] = ",nbt=!{Owner:[I;%s]}" % ",".join(m.groups()[1:])
    reply = mc.rcon(world.prefix(world.dim(at)) + "positioned %.2f %.2f %.2f as @e[type=#minecraft:impact_projectiles,"
                    "distance=..%g,tag=!%s,nbt=!{inGround:1b}%s] run data get entity @s UUID"
                    % (at[0], at[1], at[2], r, mc.TAG, _AROUND_OWN.get(me, "")))
    hits = []
    for m in _AROUND_UUID.finditer(reply):
        h = "".join("%08x" % (int(n) & 0xffffffff) for n in m.groups()[1:])
        uuid = "%s-%s-%s-%s-%s" % (h[:8], h[8:12], h[12:16], h[16:20], h[20:])
        p = re.findall(r"(-?[\d.]+(?:E-?\d+)?)d", mc.rcon("data get entity %s Pos" % uuid))
        if len(p) >= 3:
            x, y, z = (float(c) for c in p[:3])
            t = math.sqrt((x - at[0]) ** 2 + (y - at[1]) ** 2 + (z - at[2]) ** 2)
            hits.append(aim.Hit(t, world.Pos(x, y, z, world.dim(at)), m.group(1).strip(), False, uuid))
    return sorted(hits, key=lambda h: h.t)


# --- preset: push (copied from _presets/push.py) ---
def push(hits, frm, v, *, way=None):
    """Move every hit (a list of aim.Hit, from aim.near_line, around or lock_on) about
    push_strength blocks: "away" from frm, "toward" it (never past it), or "up" into the
    air; way, when given, is the way for this call (a script whose own value picks it), else
    push_way. frm: a place, or a player's name for where they stand. A mob is thrown (its
    Motion), so it flies, lands and slides; a player is moved there at once, and only into
    room to stand, since a player's Motion cannot be set; "up" floats a player up instead. A
    hit with a uuid (a projectile, from around's "projectiles") is sent off on its new
    heading and becomes this script's (tagged TAG), so it is turned only once. Pets and
    villagers are never moved. Returns how many were moved."""
    way, s = way or v["push_way"], v["push_strength"]
    if isinstance(frm, str):
        frm = mc.pos(frm)
        if frm is None:
            return 0
    seen, n = set(), 0
    for h in hits:
        key = h.uuid or (h.name if h.player else (h.name, round(h.pos[0], 1), round(h.pos[1], 1), round(h.pos[2], 1)))
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
    hop of 0.4 so it leaves the ground; straight up, a Motion of sqrt(height / 5.4). A
    projectile found by its uuid flies straight on its new heading, no hop."""
    if h.uuid:
        k = s / 5.0
        motion = (0.0, k, 0.0) if way == "up" else (ux * k, 0.1, uz * k)
    elif way == "up":
        motion = (0.0, math.sqrt(s / 5.4), 0.0)
    else:
        motion = (ux * s / 5.0, 0.4, uz * s / 5.0)
    reply = aim.run_on(h, "data modify entity @s Motion set value [%.3fd,%.3fd,%.3fd]" % motion)
    if h.uuid:
        mc.rcon("tag %s add %s" % (h.uuid, mc.TAG))
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


v = values(VALUES)
for _ in loop(every=0.1):
    for p in loop.who(v["player"]):
        hits = around(p, v)                         # what flies near them, never their own
        if hits and push(hits, p, v):               # away from them: back the way it came
            for h in hits:
                fx.particle("enchanted_hit", h.pos, 0.3, 10)
            fx.sound("item.shield.block", p, volume=1.5, pitch=1.3)
