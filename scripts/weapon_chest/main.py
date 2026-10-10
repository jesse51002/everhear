"""Weapon Chest: a chest of named weapons beside a player: bows and a trident that blast, freeze or call lightning where they land, and a sword, an axe and a mace whose right-click does it where you look."""
ASKS = ["make cool weapons, like the netherite stuff, randomized with effects and powers",
        "put them in a chest for all of us to access", "where's the chest of mysterious weapons"]
from kit import values, items, aim, loop, exclude, world, area
import itertools
import json
import math
import mc
import random
import re
import time
import threading

VALUES = {
    "player":              {"type": "player", "default": "@a"},
    "per_player":          {"type": "bool", "default": False},
    "at":                  {"type": "list", "of": "number", "default": []},
    "cooldown":            {"type": "number", "min": 0, "max": 60, "default": 1, "live": True},
    "range":               {"type": "int", "min": 4, "max": 300, "default": 120, "live": True},
    "damage_amount":       {"type": "number", "min": 0, "max": 100000, "default": 10, "live": True},
    "damage_type":         {"type": "enum", "of": ["player_attack", "magic", "lightning_bolt", "explosion", "freeze", "on_fire", "sonic_boom", "arrow", "wither", "generic"], "default": "lightning_bolt", "live": True},
    "damage_kill":         {"type": "bool", "default": False, "live": True},
    "damage_most":         {"type": "int", "min": 0, "max": 1000, "default": 0, "live": True},
    "damage_head":         {"type": "number", "min": 1, "max": 10, "default": 1, "live": True},
    "damage_spare":        {"type": "list", "of": "player", "default": [], "live": True},
    "lightning_count":     {"type": "int", "min": 1, "max": 50, "default": 1, "live": True},
    "lightning_spread":    {"type": "number", "min": 0, "max": 32, "default": 3, "live": True},
    "lightning_layout":    {"type": "enum", "of": ["scatter", "ring"], "default": "scatter", "live": True},
    "lightning_every":     {"type": "number", "min": 0, "max": 5, "default": 0, "live": True},
    "lightning_spare":     {"type": "list", "of": "player", "default": [], "live": True},
    "around_radius":       {"type": "number", "min": 1, "max": 128, "default": 4, "live": True},
    "around_what":         {"type": "enum", "of": ["mobs", "players", "everything"], "default": "mobs", "live": True},
    "around_most":         {"type": "int", "min": 0, "max": 1000, "default": 0, "live": True},
    "projectile_kind":     {"type": "enum", "of": ["both", "arrow", "trident"], "default": "both", "live": True},
    "projectile_named":    {"type": "str", "default": "", "live": True},
    "projectile_cooldown": {"type": "number", "min": 0, "max": 60, "default": 0, "live": True},
    "projectile_remove":   {"type": "bool", "default": False, "live": True},
    "explosion_power":     {"type": "number", "min": 1, "max": 16, "default": 3, "live": True},
    "explosion_layout":    {"type": "enum", "of": ["one", "plus", "ring", "rings"], "default": "one", "live": True},
    "explosion_spread":    {"type": "number", "min": 1, "max": 32, "default": 4, "live": True},
    "explosion_breaks":    {"type": "bool", "default": False, "live": True},
    "explosion_keep":      {"type": "number", "min": 0, "max": 32, "default": 0, "live": True},
    "explosion_spare":     {"type": "list", "of": "player", "default": [], "live": True},
    "freeze_seconds":      {"type": "number", "min": 1, "max": 120, "default": 6, "live": True},
    "freeze_shell":        {"type": "bool", "default": False, "live": True},
    "freeze_block":        {"type": "enum", "of": ["ice", "packed_ice", "blue_ice", "snow_block"], "default": "ice", "live": True},
    "push_way":            {"type": "enum", "of": ["away", "toward", "up"], "default": "up", "live": True},
    "push_strength":       {"type": "number", "min": 0.5, "max": 40, "default": 8, "live": True},
}


# --- preset: damage_hits (copied from _presets/damage_hits.py) ---
def damage_hits(hits, shooter, v):
    """Hurt every hit once (hits: a list of aim.Hit, from aim.near_line, around or lock_on,
    or several of those added together), by damage_amount of damage_type, as the shooter, so
    a kill is theirs; damage_kill kills instead. damage_most: only the nearest that many (1:
    the first only; 0: all). shooter: a player's name, or their aim.look(), with which a hit
    whose head the look passes takes damage_head times the damage. Never the shooter, a
    name in damage_spare, a pet, a villager or anything else exclude spares. Returns how many."""
    by = getattr(shooter, "player", shooter) or ""
    spare = [by] + list(v["damage_spare"])
    names = set(mc.bare(s) for s in spare if s and not s.startswith("@"))
    hits = [h for h in sorted(hits, key=lambda h: h.t) if mc.bare(h.name) not in names]
    if v["damage_most"]:
        hits = hits[:v["damage_most"]]
    body = "%s_hit%d" % (mc.TAG, next(_DAMAGE_N))      # each call tags with its own name
    for t in (body, body + "h"):    # bent: one an earlier run left on a player it killed would spare them
        mc.rcon("tag @e[tag=%s] remove %s" % (t, t))
    count = {}
    for h in hits:
        tag = body + "h" if v["damage_head"] > 1 and _damage_on_head(h, shooter) else body
        count[tag] = count.get(tag, 0) + _damage_mark(h, tag, spare)
    for tag, n in count.items():
        if not n:
            continue
        if v["damage_kill"]:
            _damage_all(tag, 1000000, "generic_kill", by)
        else:
            _damage_all(tag, v["damage_amount"] * (1 if tag == body else v["damage_head"]), v["damage_type"], by)
        mc.rcon("tag @e[tag=%s] remove %s" % (tag, tag))
    return sum(count.values())


_DAMAGE_N = itertools.count(1)


def _damage_mark(h, tag, spare):
    """Tag one hit where it was: a player by name, a mob as the nearest within 1.5 blocks that
    a hit may touch, so a hit listed twice is tagged once. 1 if it was tagged now, else 0."""
    if h.player:
        reply = mc.rcon("tag %s add %s" % (h.name, tag))
    else:
        x, y, z = h.pos[:3]
        reply = mc.rcon(world.prefix(world.dim(h.pos)) + "positioned %.3f %.3f %.3f %s run tag @s add %s" % (
            x, y, z, exclude.mobs("distance=..1.5,sort=nearest,limit=1", spare), tag))
    return 1 if reply.startswith("Added tag") else 0


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


# --- preset: lightning (copied from _presets/lightning.py) ---
def lightning(at, by, v):
    """Strike lightning_count bolts: the first at the place (or a player's name: where they
    stand), the rest within lightning_spread blocks of it, scattered at random or evenly in
    a ring ("ring" with 5 is a plus), lightning_every seconds apart (0: all at once). A bolt
    hurts and sets fire to what is within about 3 blocks, so none lands within 4 blocks of
    by (the player who called it, or "") or of a player in lightning_spare. Returns how many
    struck."""
    if isinstance(at, str):
        feet = mc.pos(at)
        if feet is None:
            return 0
        at = world.Pos(feet[0], feet[1], feet[2], world.dim_of(at))
    n, s = v["lightning_count"], v["lightning_spread"]
    points = [tuple(at[:3])]
    for i in range(1, n):
        if v["lightning_layout"] == "ring":
            a, d = 2 * math.pi * (i - 1) / (n - 1), s
        else:
            a, d = random.uniform(0, 2 * math.pi), random.uniform(min(1.0, s), s)
        points.append((at[0] + d * math.cos(a), at[1], at[2] + d * math.sin(a)))
    keep = "".join("unless entity @a[name=%s,distance=..4] " % mc.bare(p) for p in [by] + list(v["lightning_spare"])
                   if p and not p.startswith("@"))
    struck = 0
    for i, (x, y, z) in enumerate(points):
        if i and v["lightning_every"] and not loop.wait(v["lightning_every"]):
            break
        reply = mc.rcon(world.prefix(world.dim(at)) + "positioned %.2f %.2f %.2f %srun summon lightning_bolt ~ ~ ~ "
                        "{Tags:[\"vfg\",\"%s\"]}" % (x, y, z, keep, mc.TAG))
        struck += 1 if "Summoned" in reply else 0
    return struck


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


# --- preset: projectile_lands (copied from _presets/projectile_lands.py) ---
def projectile_lands(player, v, named=None, kind=None):
    """Yield (at, p) each time an arrow or trident that a player (of those `player` names)
    shot or threw lands in a block or hits something: at is where (a Pos), p who threw it.

        for at, p in loop(projectile_lands(v["player"], v)):
            explosion(at, v)

    projectile_named: only those from the weapon of that name, given with its custom_data
    ("Spear Bomb" is trident[custom_data={vfg_item:"spear_bomb"}], items.item_id's id).
    projectile_cooldown: a player's next ones within that many seconds land as they are.
    projectile_remove: take it away where it lands, so it is not picked up and used again.
    An arrow that hits a mob is gone at once, so for it at is a little ahead of where it
    was last seen. Each player is followed on their own. Bent: named and kind, when given,
    stand in for projectile_named and projectile_kind, so each weapon has its own."""
    seen = mc.TAG + "_seen"             # on every projectile this has looked at; taken off on a stop
    mc.on_stop(lambda: mc.rcon("tag @e[tag=%s] remove %s" % (seen, seen)))
    flying, last = {}, {}               # uuid -> what is known of it in flight; thrower -> when it last acted
    while not mc.stopping():
        people = loop.who(player)
        if not people:
            flying.clear()
            time.sleep(1.0)
            continue
        kinds = (kind,) if kind else ("arrow", "trident") if v["projectile_kind"] == "both" else (v["projectile_kind"],)
        name = named or v["projectile_named"]
        for k in kinds:
            for uuid in _projectile_lands_new(k, items.item_id(name) if name else "", seen):
                p = _projectile_lands_thrower(uuid)
                if p in people:
                    flying[uuid] = {"p": p, "kind": k, "dim": world.dim_of(p), "at": None, "step": (0, 0, 0)}
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
                f["at"] = at
                continue
            else:                                               # down
                del flying[uuid]
            if time.time() - last.get(f["p"], -1e9) < v["projectile_cooldown"]:
                continue                                        # too soon after their last: it lands as it is
            if v["projectile_remove"]:
                mc.rcon("kill %s" % uuid)
            yield at, f["p"]
            last[f["p"]] = time.time()
        time.sleep(0.05 if flying else 0.1)


_PROJECTILE_LANDS_TYPES = {"arrow": "#minecraft:arrows", "trident": "trident"}
# A projectile keeps the weapon it came from: an arrow its bow ("weapon"), a trident itself ("item").
_PROJECTILE_LANDS_FROM = {"arrow": "weapon", "trident": "item"}
_PROJECTILE_LANDS_UUID = re.compile(r"\[I; (-?\d+), (-?\d+), (-?\d+), (-?\d+)\]")
_PROJECTILE_LANDS_POS = re.compile(r"\[(-?[\d.]+(?:E-?\d+)?)d, (-?[\d.]+(?:E-?\d+)?)d, (-?[\d.]+(?:E-?\d+)?)d\]")


def _projectile_lands_new(kind, named, seen):
    """The uuids of projectiles of a kind near anyone online that were not looked at yet,
    each marked as looked at."""
    match = ',nbt={%s:{components:{"minecraft:custom_data":{vfg_item:"%s"}}}}' % (
        _PROJECTILE_LANDS_FROM[kind], named) if named else ""
    reply = mc.rcon("execute as @a at @s as @e[type=%s,distance=..32,tag=!%s%s] run data get entity @s UUID"
                    % (_PROJECTILE_LANDS_TYPES[kind], seen, match))
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


# --- preset: explosion (copied from _presets/explosion.py) ---
def explosion(at, by, v):
    """Blow up a place (or a player's name: where they stand) with blasts of explosion_power
    (TNT is 4), laid out by explosion_layout: "one" at the place, "plus" with four more
    explosion_spread blocks out, "ring" with eight round it, "rings" with six at
    explosion_spread and ten at twice that. explosion_breaks: real TNT, which breaks blocks;
    else a blast that only hurts and throws back. by (the player who set it off, or "") and
    the players in explosion_spare are never hurt, nor are pets and villagers; no blast lands
    within explosion_keep blocks of any player, who is caught at its edge instead. Returns
    how many blasts went off."""
    if isinstance(at, str):
        feet = mc.pos(at)
        if feet is None:
            return 0
        at = world.Pos(feet[0], feet[1], feet[2], world.dim_of(at))
    power = v["explosion_power"]
    spare = [mc.bare(p) for p in [by] + list(v["explosion_spare"]) if p and not p.startswith("@")]
    pre = world.prefix(world.dim(at))
    here = pre + "positioned %.2f %.2f %.2f " % tuple(at[:3])
    points = _explosion_points(at, v["explosion_layout"], v["explosion_spread"])
    if v["explosion_keep"]:
        far = v["explosion_keep"] + 2 * v["explosion_spread"]
        players = [tuple(float(c) for c in m) for m in re.findall(r"\[(-?[\d.eE-]+)d, (-?[\d.eE-]+)d, (-?[\d.eE-]+)d\]",
                   mc.rcon(here + "as @a[distance=..%.1f] run data get entity @s Pos" % far))]
        points = [p for p in points if all(math.hypot(p[0] - q[0], p[2] - q[2]) >= v["explosion_keep"] for q in players)]
    if not points:
        return 0
    if v["explosion_breaks"]:
        # TNT hurts everything in reach: two seconds of Resistance V shield what is spared.
        reach = 2 * power + max(math.hypot(p[0] - at[0], p[2] - at[2]) for p in points)
        shield = "effect give @s resistance 2 4 true"
        for name in spare:
            mc.rcon(here + "as @a[name=%s,distance=..%.1f] run %s" % (name, reach, shield))
        mc.rcon(here + "as @e[type=!player,distance=..%.1f] if data entity @s Owner run %s" % (reach, shield))
        for kind in exclude.SPARED_TYPES:
            mc.rcon(here + "as @e[type=%s,distance=..%.1f] run %s" % (kind, reach, shield))
    for x, y, z in points:
        if v["explosion_breaks"]:
            mc.rcon(pre + "run summon tnt %.2f %.2f %.2f {fuse:0s,explosion_power:%.1ff,Tags:[\"vfg\",\"%s\"]}"
                    % (x, y, z, power, mc.TAG))
        else:
            _explosion_blast(pre, (x, y, z), power, spare)
    return len(points)


def _explosion_points(at, layout, s):
    """Where the blasts go: the place, then the layout's points round it, level with it."""
    rings = {"one": [], "plus": [(s, 4, 0.0)], "ring": [(s, 8, 0.0)], "rings": [(s, 6, 0.3), (2 * s, 10, 0.1)]}
    points = [tuple(at[:3])]
    for d, n, turn in rings[layout]:
        for k in range(n):
            a = 2 * math.pi * k / n + turn
            points.append((at[0] + d * math.cos(a), at[1], at[2] + d * math.sin(a)))
    return points


def _explosion_blast(pre, p, power, spare):
    """A blast that breaks nothing: its flash and sound, and damage from the place, so it
    throws back what it hurts, in three bands out to twice the power, about what TNT does
    there (44 near the middle of a power 4 blast, 6 at its edge)."""
    x, y, z = p
    here = pre + "positioned %.2f %.2f %.2f " % (x, y, z)
    mc.rcon(here + "run particle explosion_emitter ~ ~ ~ %.1f %.1f %.1f 0 %d force"
            % (power / 2, power / 3, power / 2, max(1, int(power / 2))))
    mc.rcon(here + "run playsound minecraft:entity.generic.explode master @a ~ ~ ~ %.1f 0.8" % max(1.0, power))
    r = 2.0 * power
    for k in range(3):
        lo, hi = r * k / 3.0, r * (k + 1) / 3.0
        near = 1.0 - (lo + hi) / 2.0 / r
        amount = (near * near + near) / 2.0 * 7.0 * r + 1.0
        for frag in exclude.targets("", "distance=%.2f..%.2f" % (lo, hi), spare):
            mc.rcon(here + "%s run damage @s %.1f minecraft:explosion at %.2f %.2f %.2f" % (frag, amount, x, y, z))


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


# --- preset: push (copied from _presets/push.py) ---
def push(hits, frm, v):
    """Move every hit (a list of aim.Hit, from aim.near_line, around or lock_on) about
    push_strength blocks: "away" from frm, "toward" it (never past it), or "up" into the
    air. frm: a place, or a player's name for where they stand. A mob is thrown (its
    Motion), so it flies, lands and slides; a player is moved there at once, and only into
    room to stand, since a player's Motion cannot be set; "up" floats a player up instead.
    Pets and villagers are never moved. Returns how many were moved."""
    way, s = v["push_way"], v["push_strength"]
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


# Each weapon: its name and colour, what it is, how it fires ("lands": where its arrow or
# trident comes down; "click": a right-click, where you look), what it does there, its
# enchantments, and the line under its name. A click weapon is the kit's carrot on a stick,
# made to look like what it is.
WEAPONS = [
    ("Hellfire Longbow", "red", "bow", "lands", "blast", "power:5,infinity:1,flame:1", "Arrows explode where they land"),
    ("Glacier Bow", "aqua", "bow", "lands", "frost", "power:5,infinity:1", "Arrows freeze everything where they land"),
    ("Stormcaller", "yellow", "trident", "lands", "storm", "loyalty:3,impaling:5", "Thrown: lightning where it lands"),
    ("Thunderbrand", "yellow", "netherite_sword", "click", "storm", "", "Right-click: lightning where you look"),
    ("Doomcleaver", "red", "netherite_axe", "click", "blast", "", "Right-click: a blast where you look"),
    ("Gravebreaker", "light_purple", "mace", "click", "lift", "", "Right-click: hurls everything near you up"),
]


def blast(at, p, v):
    explosion(at, p, v)


def frost(at, p, v):
    freeze(around(at, v), v)


def storm(at, p, v):
    lightning(at, p, v)
    damage_hits(around(at, v), p, v)


def lift(at, p, v):
    push(around(p, v), p, v)


WHAT = {"blast": blast, "frost": frost, "storm": storm, "lift": lift}


def weapon(w):
    """The item, for the chest."""
    name, color, base, how, what, ench, says = w
    lore = json.dumps([{"text": says, "color": "gray", "italic": False}])
    if how == "click":
        return items.item(name, color, base)[:-1] + ",lore=%s]" % lore
    return ('%s[custom_data={vfg_item:"%s"},item_name=%s,lore=%s,enchantments={%s},unbreakable={},'
            'enchantment_glint_override=true]' % (base, items.item_id(name), json.dumps({"text": name, "color": color}),
                                                  lore, ench))


def chest(p, v):
    """A chest of every weapon and 64 arrows at `at`, or two blocks from a player where there is
    room. True once it is there."""
    if len(v["at"]) >= 3:
        spots, dim = [tuple(int(math.floor(c)) for c in v["at"][:3])], None
    else:
        feet, dim = mc.pos(p), world.dim_of(p)
        if feet is None:
            return False
        x, y, z = (int(math.floor(c)) for c in feet)
        spots = [(x + dx, y, z + dz) for dx, dz in ((2, 0), (0, 2), (-2, 0), (0, -2), (2, 2), (-2, -2))]
    for x, y, z in spots:
        here = "%d %d %d" % (x, y, z)
        if "passed" not in mc.rcon(world.prefix(dim) + "if block %s #minecraft:replaceable" % here).lower() and \
                "passed" not in mc.rcon(world.prefix(dim) + "if block %s chest" % here).lower():
            continue
        mc.rcon(world.run(dim, "setblock %s chest" % here))
        for i, w in enumerate(WEAPONS):
            mc.rcon(world.run(dim, "item replace block %s container.%d with %s" % (here, i, weapon(w))))
        mc.rcon(world.run(dim, "item replace block %s container.%d with arrow 64" % (here, len(WEAPONS))))
        mc.log("weapon chest at", here, "for", p)
        return True
    return False


def fires(w, v):
    """Wait for one weapon to fire, and do what it does there, until the script stops."""
    name, color, base, how, what = w[:5]
    if how == "click":
        for p in loop(items.clicks(v["player"], name, cooldown=v["cooldown"], give="never", color=color, model=base)):
            r = aim.ray(p, v["range"])
            if r:
                WHAT[what](r.end, p, v)
    else:
        for at, p in loop(projectile_lands(v["player"], v, named=name, kind="arrow" if base == "bow" else "trident")):
            WHAT[what](at, p, v)


v = values(VALUES)
for w in WEAPONS:
    loop.background(fires, w, v)
done = set()                    # who has a chest beside them: "all" for one chest for everyone
for _ in loop(every=2):
    for p in loop.who(v["player"]):
        key = p if v["per_player"] and not v["at"] else "all"
        if key not in done and chest(p, v):
            done.add(key)
