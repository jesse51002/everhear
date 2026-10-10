"""Damage or kill what a beam, a blast or a radius hit: more damage, one-shot kills, hostile mobs only, players or not, the first only; once each, as the player, never pets or villagers."""
import itertools
import math

import mc
from kit import aim
from actions._types import Field, Literal, Live, Player, Values  # the values class: read on the PC only

VALUES = {
    "damage_amount": {"type": "number", "min": 0, "max": 100000, "default": 20, "live": True},
    "damage_type": {"type": "enum", "of": ["player_attack", "magic", "lightning_bolt", "explosion", "freeze",
                                           "on_fire", "sonic_boom", "arrow", "wither", "generic"],
                    "default": "player_attack", "live": True},
    "damage_kill": {"type": "bool", "default": False, "live": True},
    "damage_what": {"type": "enum", "of": ["all", "mobs", "hostile", "players"], "default": "all", "live": True},
    "damage_most": {"type": "int", "min": 0, "max": 1000, "default": 0, "live": True},
    "damage_head": {"type": "number", "min": 1, "max": 10, "default": 1, "live": True},
    "damage_spare": {"type": "list", "of": "player", "default": [], "live": True},
}


class DamageHits(Values):
    damage_amount: Live[float] = Field(20, ge=0, le=100000)
    """damage to each thing hit, in half-hearts (a player has 20); does nothing while damage_kill is on"""
    damage_type: Live[Literal["player_attack", "magic", "lightning_bolt", "explosion", "freeze", "on_fire",
                              "sonic_boom", "arrow", "wither", "generic"]] = "player_attack"
    """the kind of damage, as Minecraft names it: it sets the death message and how much armour stops"""
    damage_kill: Live[bool] = False
    """kill everything hit outright instead of hurting it"""
    damage_what: Live[Literal["all", "mobs", "hostile", "players"]] = "all"
    """what it hurts: all; mobs (no players); hostile (monsters only, never animals or players); players"""
    damage_most: Live[int] = Field(0, ge=0, le=1000)
    """hurt only the nearest this many (1: the first only; 0: all)"""
    damage_head: Live[float] = Field(1, ge=1, le=10)
    """how many times the damage a hit takes when the shooter aims within half a block of its head (1: no extra)"""
    damage_spare: Live[list[Player]] = []
    """players it never hurts; the shooter, pets and villagers are always spared"""


def damage_hits(hits, shooter, v):
    """Hurt every hit once (hits: a list of aim.Hit, from aim.near_line, around or lock_on,
    or several of those added together), by damage_amount of damage_type, as the shooter, so
    a kill is theirs; damage_kill kills instead. damage_what: "all", "mobs" (no players),
    "hostile" (monsters only: never an animal, and no players) or "players". damage_most:
    only the nearest that many (1: the first only; 0: all). shooter: a player's name, or
    their aim.look(), with which a hit whose head the look passes takes damage_head times the
    damage. Never the shooter, a name in damage_spare, a pet, a villager or anything else
    exclude spares. Returns how many."""
    by = getattr(shooter, "player", shooter) or ""
    spare = [by] + list(v["damage_spare"])
    names = set(mc.bare(s) for s in spare if s and not s.startswith("@"))
    what = v["damage_what"]
    hits = [h for h in sorted(hits, key=lambda h: h.t) if mc.bare(h.name) not in names
            and (h.player if what == "players" else (what == "all" or not h.player))]
    if v["damage_most"]:
        hits = hits[:v["damage_most"]]
    body = "%s_hit%d" % (mc.TAG, next(_DAMAGE_N))      # each call tags with its own name
    head = [h for h in hits if v["damage_head"] > 1 and _damage_on_head(h, shooter)]
    count = {}
    for tag, some in ((body, [h for h in hits if h not in head]), (body + "h", head)):
        if some:
            count[tag] = aim.mark(some, tag, spare, hostile=what == "hostile")
    for tag, n in count.items():
        if n:
            if v["damage_kill"]:
                _damage_all(tag, 1000000, "generic_kill", by)
            else:
                _damage_all(tag, v["damage_amount"] * (1 if tag == body else v["damage_head"]), v["damage_type"], by)
        aim.untag(tag)
    return sum(count.values())


_DAMAGE_N = itertools.cycle(range(1, 17))    # 16 names in turn, so aim.mark clears each only once


def _damage_all(tag, amount, kind, by):
    """Damage everything tagged, once, as `by`; as nobody if `by` cannot be found (gone offline)."""
    cmd = "execute as @e[tag=%s] run damage @s %.1f minecraft:%s" % (tag, amount, kind)
    if by and "Applied" in mc.rcon(cmd + " by " + by):
        return
    mc.rcon(cmd)


def _damage_on_head(h, look):
    """True if a look (aim.look) passes within half a block of the head of a body standing at
    the hit: 1.7 above its feet, as tall as a player or a zombie."""
    if not hasattr(look, "origin"):
        return False
    o, d = look.origin, look.dir
    w = [h.pos[0] - o[0], h.pos[1] + 1.7 - o[1], h.pos[2] - o[2]]
    t = w[0] * d[0] + w[1] * d[1] + w[2] * d[2]
    return t > 0 and math.sqrt(sum((w[i] - t * d[i]) ** 2 for i in range(3))) <= 0.5
