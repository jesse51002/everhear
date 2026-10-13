"""Spawning (the kit, DESIGN2 §5 "Spawning"): where something can stand (`safe_spots`) or fall
from (`sky_spots`), spawning mobs there (`summon_near`, with `mob_nbt` for a name, health, size,
gear...), a boss bar over several bodies (`boss_bar`), and a rule on the world's own mobs of a type
(`world_mob_rule`, undone by `undo_world_mob_rule`).

Block reads are batched: every spot a call tries is tested in two `ctx.world.probe` reads (room
to stand, "open"; solid ground, "solid"), each one command however many points.
"""

from __future__ import annotations

import json
import math
import random

from ._base import floor3, limits, memory, where

TRIES = 4                           # spots tried for each one wanted
NUDGES = (0, 1, -1, 2, -2, 3)       # heights tried round a spot's own, nearest first
NOT_ON_TOP = 1.5                    # a spot round a player is at least this far from them
GEAR = ("leather", "chainmail", "iron", "golden", "diamond", "netherite")
SPAWNED_TAGS = ("everhear.spawn", "everhear.actor")
MOB_RULE_KEEP = 2000                # world mobs a rule remembers changing, at most


def _is_player(ctx, x) -> bool:
    if isinstance(x, str):
        return ctx.world.player(x) is not None
    return bool(getattr(x, "is_player", False)) or hasattr(x, "gamemode")


def safe_spots(ctx, near, count: int = 1, spread: float = 8.0, dim: str | None = None) -> list:
    """Up to `count` spots where a mob or a player can stand, within `spread` blocks of `near` (a
    position, a player, a Spot; round a player never on top of them): solid ground under it and
    two blocks of air. With spread 0, `near` itself (or a block above or below it) or nothing.
    Each is (x + 0.5, y, z + 0.5) on top of its ground. Fewer come back where there is no room."""
    centre, d = where(ctx, near, dim)
    lo, hi = limits(d)
    away = NOT_ON_TOP if _is_player(ctx, near) and spread > NOT_ON_TOP else 0.0
    columns = []
    for _ in range(max(1, int(count)) * (TRIES if spread > 0 else 1)):
        if spread > 0:
            a, r = random.uniform(0, 2 * math.pi), away + (float(spread) - away) * math.sqrt(random.random())
            x, z = math.floor(centre[0] + r * math.cos(a)), math.floor(centre[2] + r * math.sin(a))
        else:
            x, z = math.floor(centre[0]), math.floor(centre[2])
        if (x, z) not in columns:
            columns.append((x, z))
    y0 = math.floor(centre[1])
    feet = [(x, y0 + n, z) for x, z in columns for n in NUDGES if lo < y0 + n < hi]
    if not feet:
        return []
    room = ctx.world.probe(feet, "open", dim=d)
    ground = ctx.world.probe([(x, y - 1, z) for x, y, z in feet], "solid", dim=d)
    out, used = [], set()
    for (x, y, z), open_, solid in zip(feet, room, ground):
        if open_ and solid and (x, z) not in used:
            used.add((x, z))
            out.append((x + 0.5, float(y), z + 0.5))
            if len(out) >= count:
                break
    return out


def sky_spots(ctx, near, count: int = 6, radius: float = 14.0, height: float = 20.0, band: float = 0.0,
              open_only: bool = True, dim: str | None = None) -> list:
    """`count` random points up in the sky round `near` (a position, a player, a Spot, or a list
    of them: that many round each), up to `radius` out, `height` above them give or take `band`,
    below the top of the world. `open_only`: only points in open air (none in a mountain or under
    a roof), so fewer may come back. Where rain, falling lava, meteors or TNT start."""
    many = near if isinstance(near, (list, tuple)) and near and not isinstance(near[0], (int, float)) else [near]
    out = []
    for c in many:
        centre, d = where(ctx, c, dim)
        top = limits(d)[1] - 1
        here = []
        for _ in range(max(0, int(count))):
            a, r = random.uniform(0, 2 * math.pi), float(radius) * math.sqrt(random.random())
            y = min(top, centre[1] + float(height) + random.uniform(-band, band))
            here.append((centre[0] + r * math.cos(a), y, centre[2] + r * math.sin(a)))
        if open_only and here:
            ok = ctx.world.probe([floor3(p) for p in here], "open", dim=d)
            here = [p for p, good in zip(here, ok) if good]
        out += here
    return out


def mob_nbt(name: str | None = None, health: float | None = None, scale: float | None = None,
            gear: str | None = None, weapon: str | None = None, glowing: bool = False, baby: bool = False,
            no_ai: bool = False, silent: bool = False, invulnerable: bool = False, extra: str | None = None) -> str:
    """The SNBT for a spawn ("{...}"): a name shown over its head, its health (and max health), its
    size (1 is its own), a full suit of `gear` armour ("iron", "diamond"...) and a `weapon` in hand
    (neither dropped), glowing, a baby, no AI, silent, invulnerable, and `extra` SNBT entries
    ("Fuse:60s"). It always persists (never despawns)."""
    parts, attrs, kit = ["PersistenceRequired:1b"], [], {}
    if name:
        parts += ["CustomName:%s" % json.dumps(str(name), ensure_ascii=False), "CustomNameVisible:1b"]
    if health:
        attrs.append('{id:"minecraft:max_health",base:%g}' % float(health))
        parts.append("Health:%gf" % float(health))
    if scale and float(scale) != 1.0:
        attrs.append('{id:"minecraft:scale",base:%g}' % float(scale))
    if attrs:
        parts.append("attributes:[%s]" % ",".join(attrs))
    if gear:
        if gear not in GEAR:
            raise ValueError("gear is one of %s, not %r" % (", ".join(GEAR), gear))
        for slot, piece in (("head", "helmet"), ("chest", "chestplate"), ("legs", "leggings"), ("feet", "boots")):
            kit[slot] = "minecraft:%s_%s" % (gear, piece)
    if weapon:
        kit["mainhand"] = weapon if ":" in weapon else "minecraft:" + weapon
    if kit:
        parts.append("equipment:{%s}" % ",".join('%s:{id:"%s",count:1}' % sk for sk in sorted(kit.items())))
        parts.append("drop_chances:{%s}" % ",".join("%s:0f" % s for s in sorted(kit)))
    for flag, key in ((glowing, "Glowing"), (baby, "IsBaby"), (no_ai, "NoAI"), (silent, "Silent"),
                      (invulnerable, "Invulnerable")):
        if flag:
            parts.append("%s:1b" % key)
    if extra:
        parts.append(str(extra).strip().strip("{}"))
    return "{%s}" % ",".join(parts)


def summon_near(self, ctx, near, mob: str, count: int = 1, spread: float = 8.0, nbt: str | None = None,
                keep: bool = False, cap: int = 0, dim: str | None = None) -> list[str]:
    """Spawn `count` of `mob` (an Actor's id, or a vanilla entity "minecraft:zombie") at safe
    spots round `near`, with `nbt` (mob_nbt(...)). `keep`: left in the world when this ends by
    itself. `cap`: never more than that many of this instance's `mob` alive at once (0: no cap;
    it remembers them in the kit's memory). Returns the uuids spawned (fewer where there is no
    room)."""
    centre, d = where(ctx, near, dim)
    alive = []
    if cap:
        mem = memory(self, "summoned")
        alive = [u for u in mem.get(mob, []) if ctx.world.get(u) is not None]
        count = min(int(count), int(cap) - len(alive))
    if count <= 0:
        return []
    out = [self.spawn(mob, spot, dim=d, nbt=nbt, keep=keep) for spot in safe_spots(ctx, near, count, spread, d)]
    if cap:
        mem[mob] = alive + out
    return out


def boss_bar(self, ctx, bodies, name: str, color: str = "red", players="@a", bar: str = "boss",
             most: float | None = None) -> tuple[float, int]:
    """Keep a boss bar on the summed health of `bodies` (their uuids): call it from tick() (once a
    second is plenty). The bar is full at `most`, else at the bodies' summed health the first time
    it was called (remembered in the kit's memory). `bar` names it, for an instance with two. The
    engine removes it when this ends. Returns (summed health, bodies alive)."""
    total, alive = 0.0, 0
    for u in bodies:
        e = ctx.world.get(u)
        if e is not None and e.health is not None and e.health > 0:
            total += e.health
            alive += 1
    top = most
    if top is None:
        mem = memory(self, "boss_bar")
        top = max(float(mem.get(bar, 0.0)), total)
        mem[bar] = top
    self.bossbar(bar, name=name, color=color, value=int(round(total)), max=max(1, int(round(top))),
                 players=players)
    return total, alive


# ---- a rule on the world's mobs of a type ----------------------------------------------------

def world_mob_rule(self, ctx, mob: str, nbt: str | None = None, undo_nbt: str | None = None,
                   modifiers=(), radius: float = 48.0) -> list[str]:
    """Change each of the world's own `mob`s ("minecraft:creeper") within `radius` of a player,
    once: `nbt` merged onto it, and `modifiers` [(attribute, amount, operation)] put on it as this
    instance's (removed when this ends). Call it from tick(). What the engine spawned is left
    alone. Returns the uuids changed this call. Call undo_world_mob_rule(self, ctx) in stop() to
    put `undo_nbt` back on them."""
    mob = mob if ":" in mob else "minecraft:" + mob
    mem = memory(self, "mob_rule").setdefault(mob, {"changed": [], "undo": undo_nbt,
                                                    "mods": [[a, op] for a, _, op in modifiers]})
    done = set(mem["changed"])
    new = []
    for p in ctx.world.players.values():
        if not p.alive:
            continue
        if radius <= ctx.world.mirror_radius:
            found = [e for e in ctx.world.entities_near(p.pos, radius, dim=p.dim) if e.type == mob]
        else:
            found = ctx.world.entities(p.pos, radius, types=[mob], dim=p.dim)
        for e in found:
            if e.uuid in done or e.type != mob or any(t in e.tags for t in SPAWNED_TAGS):
                continue
            done.add(e.uuid)
            new.append(e.uuid)
            if nbt:
                self.entity_data(e.uuid, nbt)
            for attribute, amount, operation in modifiers:
                self.modifier(attribute, amount, operation, on=e.uuid)
    if new:
        mem["changed"] = (mem["changed"] + new)[-MOB_RULE_KEEP:]
    return new


def undo_world_mob_rule(self, ctx, mob: str | None = None) -> int:
    """Put back what world_mob_rule changed (all its rules, or one `mob`'s): its `undo_nbt` on each
    mob it changed and its modifiers off. Returns how many mobs."""
    rules = memory(self, "mob_rule")
    n = 0
    for name in [m for m in list(rules) if mob is None or m == (mob if ":" in mob else "minecraft:" + mob)]:
        rule = rules.pop(name)
        for u in rule["changed"]:
            if rule.get("undo"):
                self.entity_data(u, rule["undo"])
            for attribute, _operation in rule.get("mods", []):
                self.modifier(attribute, None, on=u)
            n += 1
    return n
