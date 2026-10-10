"""When a player's arrow or trident lands or hits something (an exploding arrow, a lightning trident, a bow with a power): where it came down, who threw it, and from which named weapon."""
import re
import time

import mc
from kit import items, loop, world
from actions._types import Field, ItemName, Literal, Live, Values  # the values class: read on the PC only


class ProjectileLands(Values):
    projectile_lands_kind: Live[Literal["both", "arrow", "trident"]] = "both"
    """what counts: a player's arrows, their tridents, or both"""
    projectile_lands_named: Live[list[ItemName]] = []
    """only those from weapons of these names a script gave ("Spear Bomb"); []: from any bow or trident"""
    projectile_lands_cooldown: Live[float] = Field(0, ge=0, le=60)
    """seconds after one of a player's counts during which their next ones land as plain ones (0: all count)"""
    projectile_lands_remove: Live[bool] = False
    """take it away where it lands, so it cannot be picked up and used again"""


def projectile_lands(player, v):
    """Yield (at, p, weapon) each time an arrow or trident that a player (of those `player`
    names) shot or threw lands in a block or hits something: at is where (a Pos), p who threw
    it, weapon the id of the named weapon it came from ("spear_bomb"), or "".

        for at, p, weapon in loop(projectile_lands(v["player"], v)):
            explosion(at, p, v)

    projectile_lands_named: only those from the weapons of those names, each given with its
    custom_data ("Spear Bomb" is trident[custom_data={vfg_item:"spear_bomb"}], items.item_id's
    id); [] for any. projectile_lands_cooldown: a player's next ones within that many seconds
    land as they are. projectile_lands_remove: take it away where it lands, so it is not
    picked up and used again. An arrow that hits a mob is gone at once, so for it at is a
    little ahead of where it was last seen. One already lying in the ground when first seen
    (from before the script started) is left alone, and one that hangs still in the air for
    10 s (out past the simulation distance, it stops) is given up on. Each player is
    followed on their own."""
    seen = mc.TAG + "_seen"             # on every projectile this has looked at; taken off on a stop
    mc.on_stop(lambda: mc.rcon("tag @e[tag=%s] remove %s" % (seen, seen)))
    flying, last = {}, {}               # uuid -> what is known of it in flight; thrower -> when it last acted
    while not mc.stopping():
        people = loop.who(player)
        if not people:
            flying.clear()
            time.sleep(1.0)
            continue
        kind = v["projectile_lands_kind"]
        named = [items.item_id(n) for n in v["projectile_lands_named"] if str(n).strip()]
        for k in (("arrow", "trident") if kind == "both" else (kind,)):
            for uuid in _projectile_lands_new(k, seen):
                p = _projectile_lands_thrower(uuid)
                if p not in people:
                    continue
                weapon = _projectile_lands_weapon(uuid, k)
                if named and weapon not in named:
                    continue
                if _projectile_lands_down(uuid, k) and _projectile_lands_life(uuid) > 40:
                    continue                                    # lying there already (2 s or more)
                flying[uuid] = {"p": p, "kind": k, "weapon": weapon, "dim": world.dim_of(p), "at": None,
                                "step": (0, 0, 0), "moved": time.time()}
        for uuid, f in list(flying.items()):
            at = _projectile_lands_pos(uuid, f["dim"])
            if at is False:                                     # no answer this time: ask again next pass
                continue
            if at is None:                                      # gone: an arrow that hit a mob, or out of the world
                del flying[uuid]
                if f["at"] is None:
                    continue
                at = f["at"].moved(*[s / 2.0 for s in f["step"]])
                lo, hi = world.limits(at.dim)
                if not lo <= at[1] <= hi or not world.loaded(at[0], at[2], at.dim):
                    continue                                    # it left the loaded world: nothing to act on
            elif not _projectile_lands_down(uuid, f["kind"]):   # still flying
                if f["at"] is not None:
                    f["step"] = tuple(min(8.0, max(-8.0, at[j] - f["at"][j])) for j in range(3))
                    if max(abs(s) for s in f["step"]) > 0.01:
                        f["moved"] = time.time()
                    elif time.time() - f["moved"] > 10:
                        del flying[uuid]                        # hung still in the air: it will not land
                        continue
                f["at"] = at
                continue
            else:                                               # down
                del flying[uuid]
            if time.time() - last.get(f["p"], -1e9) < v["projectile_lands_cooldown"]:
                continue                                        # too soon after their last: it lands as it is
            if v["projectile_lands_remove"]:
                mc.rcon("kill %s" % uuid)
            yield at, f["p"], f["weapon"]
            last[f["p"]] = time.time()
        time.sleep(0.05 if flying else 0.1)


_PROJECTILE_LANDS_TYPES = {"arrow": "#minecraft:arrows", "trident": "trident"}
# A projectile keeps the weapon it came from: an arrow its bow ("weapon"), a trident itself ("item").
_PROJECTILE_LANDS_FROM = {"arrow": "weapon", "trident": "item"}
_PROJECTILE_LANDS_UUID = re.compile(r"\[I; (-?\d+), (-?\d+), (-?\d+), (-?\d+)\]")
_PROJECTILE_LANDS_POS = re.compile(r"\[(-?[\d.]+(?:E-?\d+)?)d, (-?[\d.]+(?:E-?\d+)?)d, (-?[\d.]+(?:E-?\d+)?)d\]")


def _projectile_lands_new(kind, seen):
    """The uuids of projectiles of a kind near anyone online that were not looked at yet,
    each marked as looked at."""
    reply = mc.rcon("execute as @a at @s as @e[type=%s,distance=..32,tag=!%s] run data get entity @s UUID"
                    % (_PROJECTILE_LANDS_TYPES[kind], seen))
    out = []
    for m in _PROJECTILE_LANDS_UUID.finditer(reply):
        h = "".join("%08x" % (int(n) & 0xffffffff) for n in m.groups())
        uuid = "%s-%s-%s-%s-%s" % (h[:8], h[8:12], h[12:16], h[16:20], h[20:])
        if uuid not in out:                                     # near two players, it is listed twice
            out.append(uuid)
            mc.rcon("tag %s add %s" % (uuid, seen))
    return out


def _projectile_lands_thrower(uuid):
    """Who shot or threw it, as a bare name ("" for nobody, or a mob's name)."""
    reply = mc.rcon("execute as %s on origin run data get entity @s UUID" % uuid)
    return mc.bare(reply.split(" has the following entity data", 1)[0]) if " has the following" in reply else ""


def _projectile_lands_weapon(uuid, kind):
    """The vfg_item id of the weapon it came from (its custom_data), or ""."""
    reply = mc.rcon('data get entity %s %s.components."minecraft:custom_data".vfg_item'
                    % (uuid, _PROJECTILE_LANDS_FROM[kind]))
    m = re.search(r'entity data: "([^"]*)"', reply)
    return m.group(1) if m else ""


def _projectile_lands_life(uuid):
    """How many ticks it has lain in the ground, 0 when the server does not say."""
    m = re.search(r"entity data: (\d+)s", mc.rcon("data get entity %s life" % uuid))
    return int(m.group(1)) if m else 0


def _projectile_lands_pos(uuid, dim):
    """Where it is (a Pos); None if it is gone; False if the server did not answer."""
    reply = mc.rcon("data get entity %s Pos" % uuid)
    m = _PROJECTILE_LANDS_POS.search(reply)
    if m:
        return world.Pos(float(m.group(1)), float(m.group(2)), float(m.group(3)), dim)
    return None if "no entity" in reply.lower() else False


def _projectile_lands_down(uuid, kind):
    """True once it is in a block, or (a trident) has hit something and bounced off."""
    if "1b" in mc.rcon("data get entity %s inGround" % uuid).rsplit(":", 1)[-1]:
        return True
    return kind == "trident" and "1b" in mc.rcon("data get entity %s DealtDamage" % uuid).rsplit(":", 1)[-1]
