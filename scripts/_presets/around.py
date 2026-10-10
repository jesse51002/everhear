"""Everything within a radius of a player or a place (a blast's reach, a barrier, an aura), nearest first, as hits to act on: mobs, hostile mobs only, players, everything, or projectiles in flight."""
import math
import re

import mc
from kit import aim, world
from actions._types import Field, Literal, Live, Values  # the values class: read on the PC only

VALUES = {
    "around_radius": {"type": "number", "min": 1, "max": 128, "default": 8, "live": True},
    "around_what": {"type": "enum", "of": ["mobs", "hostile", "players", "everything", "projectiles"],
                    "default": "mobs", "live": True},
    "around_most": {"type": "int", "min": 0, "max": 1000, "default": 0, "live": True},
}


class Around(Values):
    around_radius: Live[float] = Field(8, ge=1, le=128)
    """how far it reaches round the player or the place, in blocks"""
    around_what: Live[Literal["mobs", "hostile", "players", "everything", "projectiles"]] = "mobs"
    """what it finds: mobs; hostile (monsters only); players; everything (mobs and players); projectiles (flying, not the player's own)"""
    around_most: Live[int] = Field(0, ge=0, le=1000)
    """only the nearest this many (0: all)"""


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
