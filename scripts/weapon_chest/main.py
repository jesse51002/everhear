"""Weapon Chest: a chest of named weapons with powers beside a player: an explosive bow, a freeze bow and a lightning trident that act where they land, and a lightning sword, a blast axe and a mace that lifts everything near, on a right-click where you look."""
ASKS = ["make cool weapons, like the netherite stuff, randomized with effects and powers",
        "put them in a chest for all of us to access", "where's the chest of mysterious weapons",
        "weapon powers", "a bow whose arrows explode", "a trident that calls lightning where it lands",
        "a bow that freezes what it hits", "weapons with powers for all of us"]
from kit import values, result, items, aim, loop, exclude, world, area
import itertools
import json
import math
import mc
import random
import re
import time
import threading
import instances


# --- preset: damage_hits (copied from _presets/damage_hits.py) ---
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


# --- preset: projectile_lands (copied from _presets/projectile_lands.py) ---
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


# --- preset: explosion (copied from _presets/explosion.py) ---
def explosion(at, by, v):
    """Blow up a place (or a player's name: where they stand) with blasts of explosion_power
    (TNT is 4; 0: no blast at all), laid out by explosion_layout: "one" at the place, "plus" with four more
    explosion_spread blocks out, "ring" with eight round it, "rings" with six at
    explosion_spread and ten at twice that. explosion_breaks: real TNT, which breaks blocks;
    else a blast that only hurts and throws back. by (the player who set it off, or "") and
    the players in explosion_spare are never hurt, nor are pets and villagers; no blast lands
    within explosion_keep blocks of any player, who is caught at its edge instead. Returns
    how many blasts went off."""
    power = v["explosion_power"]
    if power <= 0:
        return 0
    if isinstance(at, str):
        feet = mc.pos(at)
        if feet is None:
            return 0
        at = world.Pos(feet[0], feet[1], feet[2], world.dim_of(at))
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
    ends; it only fills air, so nothing is replaced (never bedrock, never a chest).
    freeze_blindness blinds it too (blindness IV, as the run's ice beam did). Pets and
    villagers are never frozen. Returns how many were frozen."""
    secs = int(math.ceil(v["freeze_seconds"]))
    tag = "%s_frz%d" % (mc.TAG, next(_FREEZE_N))      # each call tags with its own name
    if not aim.mark(hits, tag):                         # each once, where it was
        aim.untag(tag)
        return 0
    # Slowness and weakness X (amplifier 9; above 127 it is saved as a negative byte) leave
    # no speed and no attack, and wear off by themselves even if the script dies.
    mc.rcon("effect give @e[tag=%s] slowness %d 9 true" % (tag, secs))
    mc.rcon("effect give @e[tag=%s] weakness %d 9 true" % (tag, secs))
    if v["freeze_blindness"]:
        mc.rcon("effect give @e[tag=%s] blindness %d 3 true" % (tag, secs))
    mc.rcon("execute as @e[tag=%s,type=!player] run data merge entity @s {TicksFrozen:%d}" % (tag, 140 + 40 * secs))
    mc.rcon("execute at @e[tag=%s] run particle snowflake ~ ~1 ~ 0.4 0.6 0.4 0.02 25 force" % tag)
    frozen = [world.Pos(float(x), float(y), float(z), world.dim(hits[0].pos)) for x, y, z in _FREEZE_POS.findall(
        mc.rcon("execute as @e[tag=%s] run data get entity @s Pos" % tag))]
    aim.untag(tag)
    if v["freeze_shell"]:
        shells = [_freeze_shell(at, v["freeze_block"]) for at in frozen]
        with _FREEZE_LOCK:
            if not _FREEZE_SHELLS:
                mc.on_stop(lambda: [_freeze_melt(s) for s in list(_FREEZE_SHELLS)])
            _FREEZE_SHELLS.extend(shells)
        loop.background(_freeze_thaw, shells, secs)
    return len(frozen)


_FREEZE_N = itertools.cycle(range(1, 17))    # 16 names in turn, so aim.mark clears each only once
_FREEZE_POS = re.compile(r"\[(-?[\d.]+(?:E-?\d+)?)d, (-?[\d.]+(?:E-?\d+)?)d, (-?[\d.]+(?:E-?\d+)?)d\]")
_FREEZE_SHELLS = []         # shells still standing; a stop melts them
_FREEZE_LOCK = threading.Lock()


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
def push(hits, frm, v, *, way=None):
    """Move every hit (a list of aim.Hit, from aim.near_line, around or lock_on) about
    push_strength blocks: "away" from frm, "toward" it (never past it), or "up" into the
    air; way, when given, is the way for this call (a script whose own value picks it), else
    push_way. frm: a place, or a player's name for where they stand. A mob is thrown (its
    Motion), so it flies, lands and slides; a player is launched through the air by the launch
    preset (wind charges at their feet, landing safely). A hit with a uuid (a projectile,
    from around's "projectiles") is sent off on its new heading and becomes this script's
    (tagged TAG), so it is turned only once. Pets and villagers are never moved. Returns how
    many were moved."""
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
        n += launch(h, v, way=way, strength=s_here, frm=frm) if h.player else _push_mob(h, ux, uz, s_here, way)
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


def chest(p, at, beside=False):
    """A chest of every weapon and 64 arrows at `at` (a Pos; beside: two blocks from it), or,
    with None, two blocks from player p, where there is room. Where it is, or None until it is
    there."""
    if at is not None and not beside:
        spots, dim = [at.block()], at.dim
    else:
        feet, dim = (at, at.dim) if at is not None else (mc.pos(p), world.dim_of(p))
        if feet is None:
            return None
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
        return world.Pos(x, y, z, dim)
    return None


def clicks(w, v):
    """Wait for one click weapon's right-clicks, and do what it does where they look."""
    name, color, base, how, what = w[:5]
    for p in loop(items.clicks(v["player"], name, cooldown=v["cooldown"], give="never", color=color, model=base)):
        r = aim.ray(p, v["range"])
        if r:
            WHAT[what](r.end, p, v)


def lands(v):
    """Every arrow and trident from the chest's weapons (projectile_lands_named), each doing
    what its weapon does where it lands."""
    what = dict((items.item_id(w[0]), w[4]) for w in WEAPONS if w[3] == "lands")
    for at, p, weapon in loop(projectile_lands(v["player"], v)):
        if weapon in what:
            WHAT[what[weapon]](at, p, v)


# --- preset: launch (copied from _presets/launch.py) ---
def launch(who, v, *, way=None, strength=None, frm=None, wait=False):
    """Throw `who` through the air: a player's name, an aim.Hit (a player, or a mob, as around,
    aim.near and lock_on give) or a list of them. `strength` blocks (else launch_strength),
    `way` (else launch_way): "forward" where each faces, "up", "away" from frm, or "toward"
    frm, never past it; frm is a place, or a player's name for where they stand.

    A player is thrown by launch_method. "wind" stages wind charges round their feet (behind
    them for a way along the ground) and lets them all burst on the ground in one tick, as a
    wind charge jump does: the push reaches their own client, so they fly free and can look
    round, and it pushes whoever stands within 2 blocks of a burst too and flips a door or a
    button there. A player in the air, or with no open ground to burst on, is carried instead.
    "ride" carries them on an invisible armor stand along the arc and sets them down where it
    lands (at once if their head goes into a block). A mob is thrown by its Motion. An arc
    along the ground leaves at about 40 to 50 degrees, so a long throw goes high too.

    launch_landing "safe": nothing thrown takes fall damage until it lands (the fall_damage_
    multiplier modifier vfg:launch, taken off as it lands); "float": Slow Falling for the way
    down too; "hurt": the fall as it comes (a wind charge jump still spares a player the part of
    the fall above where they left from). Anyone already in the air from an earlier throw is left
    to fly. What it summons is gone within a tick or as they land; a stop, or the script's end,
    sets down whoever is still riding and gives Slow Falling to whoever is still in the air.
    The modifier is saved with a player, so each throw is stamped with the game time (objective
    vfg_launch), and instances.launch_sweep, run at every call (any copy's), at this copy's
    cleanup and after a stop or a kill of it (instances.Box.stop), sets down what this copy
    threw and takes the modifier off whoever has stood on the ground 2 s past their last throw.

    Returns how many were thrown. wait=True returns only once all of them have landed (or 30 s
    passed): a script that ends right after a throw waits, so its end does not cut it short."""
    way = way or v["launch_way"]
    s = float(v["launch_strength"] if strength is None else strength)
    landing, method = v["launch_landing"], v["launch_method"]
    if isinstance(frm, str):
        c = mc.pos(frm)
        if c is None:
            return 0
        frm = world.Pos(c[0], c[1], c[2], world.dim_of(frm))
    targets = [who] if isinstance(who, str) or hasattr(who, "player") else list(who)
    n, seen = 0, set()
    with _LAUNCH["lock"]:
        _LAUNCH["busy"] += 1
    try:
        if not _launch_ready():
            instances.launch_sweep(mc.rcon)
        for t in targets:
            key = t if isinstance(t, str) else (t.uuid or (t.name if t.player else
                                                          (t.name,) + tuple(round(c, 1) for c in t.pos[:3])))
            if key in seen:
                continue
            seen.add(key)
            if isinstance(t, str) or t.player:
                n += _launch_player(t if isinstance(t, str) else t.name, way, s, frm, landing, method)
            else:
                n += _launch_mob(t, way, s, frm, landing)
    finally:
        with _LAUNCH["lock"]:
            _LAUNCH["busy"] -= 1
        _launch_start()
    while wait and n and not mc.stopping() and not _LAUNCH["idle"].wait(0.1):
        pass
    return n


# ---- how things fly (measured on 26.3: a player, a husk and an armor stand fly alike) ----------

_LAUNCH_GRAVITY, _LAUNCH_DRAG = 0.08, 0.98      # taken off the y speed each tick, then what is kept of it
_LAUNCH_AIR = 0.91                              # what is kept of the speed along the ground each tick in the air
_LAUNCH_GROUND = 0.546                          # and on the ground (0.6 x 0.91); slime and ice keep more:
_LAUNCH_SLIDY = (("minecraft:slime_block", 0.728), ("#minecraft:ice", 0.89))
_LAUNCH_REST = 0.0784                           # the y speed of anything standing on the ground
_LAUNCH_EYES = 1.62
# A wind charge pushes along the line from its burst to the eyes, by 1.22 x (1 - d / 2.4) for d
# from the burst to the feet; one let fall on the ground bursts 0.25 above it.
_LAUNCH_PUSH, _LAUNCH_REACH, _LAUNCH_BURST = 1.22, 2.4, 0.25
_LAUNCH_MOST = 16                               # wind charges in one throw, at most
_LAUNCH_SLOPE = 0.84                            # tan 40 degrees: how steeply a mob or a ride leaves along the ground
_LAUNCH_LONGEST = 30.0                          # seconds anything is watched for its landing


def _launch_flight(along, up, first=_LAUNCH_GROUND):
    """(blocks along the ground it lands and slides to a stop at, ticks in the air, blocks up
    at the top) for a throw from level ground at `along` and `up` blocks a tick; `first`: what
    is kept of `along` over the first tick, which the ground it leaves from takes."""
    x = y = top = 0.0
    t = 0
    while t < 1200:
        x, y, t = x + along, y + up, t + 1
        top = max(top, y)
        up = (up - _LAUNCH_GRAVITY) * _LAUNCH_DRAG
        along *= first if t == 1 else _LAUNCH_AIR
        if y <= 0 and up < 0:
            break
    while along > 0.003:
        x, along = x + along, along * _LAUNCH_GROUND
    return x, t, top


def _launch_solve(f, want, lo, hi, rising=True):
    """The x in [lo, hi] where f(x) is `want`, f rising (or falling) in x; the nearer end when
    it is out of reach."""
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if (f(mid) < want) == rising:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _launch_speed(way, s, first):
    """(along, up) in blocks a tick that throws something s blocks: up that high, or along the
    ground that far, leaving at 40 degrees."""
    if way == "up":
        return 0.0, _launch_solve(lambda u: _launch_flight(0.0, u)[2], s, 0.0, 6.0)
    c = _launch_solve(lambda c: _launch_flight(c, c * _LAUNCH_SLOPE, first)[0], s, 0.0, 6.0)
    return c, c * _LAUNCH_SLOPE


def _launch_charge(o):
    """(along, up) push of one wind charge that bursts on the ground o blocks from the feet."""
    k = max(0.0, 1.0 - math.hypot(o, _LAUNCH_BURST) / _LAUNCH_REACH) * _LAUNCH_PUSH
    d = math.hypot(o, _LAUNCH_EYES - _LAUNCH_BURST)
    return k * o / d, k * (_LAUNCH_EYES - _LAUNCH_BURST) / d


def _launch_plan(way, s, first):
    """(how many charges, how far from the feet they burst, ticks in the air): for up a ring
    round the feet whose pushes along the ground cancel; for the others all at one spot
    behind, as close to 1.6 blocks back (a 40-degree arc) as the count lets."""
    if way == "up":
        need = _launch_speed("up", s, first)[1] + _LAUNCH_REST
        n = max(2, int(math.ceil(need / 1.0)))
        o = _launch_solve(lambda o: _launch_charge(o)[1], need / n, 0.0, 2.3, rising=False)
        return n, o, _launch_flight(0.0, need - _LAUNCH_REST)[1]

    def far(n, o):
        a, u = _launch_charge(o)
        return _launch_flight(n * a, n * u - _LAUNCH_REST, first)[0]
    best = None
    for n in range(1, _LAUNCH_MOST + 1):
        if far(n, 2.0) <= s <= far(n, 1.0):
            o = _launch_solve(lambda o: far(n, o), s, 1.0, 2.0, rising=False)
            if best is None or abs(o - 1.6) < abs(best[1] - 1.6):
                best = (n, o)
    n, o = best or ((1, 2.0) if s < far(1, 2.0) else (_LAUNCH_MOST, 1.0))
    a, u = _launch_charge(o)
    return n, o, _launch_flight(n * a, n * u - _LAUNCH_REST, first)[1]


# ---- throwing ------------------------------------------------------------------------------------

_LAUNCH_NUM = re.compile(r"(-?\d+(?:\.\d+)?(?:[eE]-?\d+)?)[fd]")


def _launch_way(way, at, frm, yaw, s):
    """(unit vector along the ground, blocks) for a throw, or None when there is nothing to do
    ("toward" a place they stand at)."""
    if way == "up":
        return (0.0, 0.0), s
    if way == "forward":
        r = math.radians(yaw or 0.0)
        return (-math.sin(r), math.cos(r)), s
    dx, dz = (at[0] - frm[0], at[2] - frm[2]) if frm is not None else (0.0, 0.0)
    far = math.hypot(dx, dz)
    if way == "toward":
        if far < 1.3:
            return None
        return (-dx / far, -dz / far), min(s, far - 1.0)
    if far < 0.01:
        a = random.uniform(0, 2 * math.pi)
        return (math.cos(a), math.sin(a)), s
    return (dx / far, dz / far), s


def _launch_player(name, way, s, frm, landing, method):
    feet = mc.pos(name)
    if feet is None:
        return 0
    dim = world.dim_of(name)
    at = world.Pos(feet[0], feet[1], feet[2], dim)
    yaw = None
    if way == "forward":
        rot = _LAUNCH_NUM.findall(mc.rcon("data get entity %s Rotation" % name))
        yaw = float(rot[0]) if rot else 0.0
    aim_ = _launch_way(way, at, frm, yaw, s)
    if aim_ is None:
        return 0
    u, s = aim_
    ground = mc.rcon("data get entity %s OnGround" % name).endswith(" 1b")
    if not ground and _launch_flying(name):
        return 0                    # still in the air from an earlier throw: left to fly
    first = _launch_friction(at) if way != "up" else _LAUNCH_GROUND
    if method == "wind" and ground:
        n, o, ticks = _launch_plan(way, s, first)
        spots = _launch_spots(at, way, u, n, o)
        if spots and _launch_burst(at, spots):
            _launch_guard(name, landing, ticks)
            return 1
        mc.log("launch: no open ground round %s to burst on: carried instead" % name)
    along, up = _launch_speed(way, s, _LAUNCH_AIR)
    return _launch_ride(name, at, (u[0] * along, up, u[1] * along), landing)


def _launch_mob(h, way, s, frm, landing):
    yaw = None
    if way == "forward":
        rot = _LAUNCH_NUM.findall(aim.run_on(h, "data get entity @s Rotation"))
        yaw = float(rot[0]) if rot else 0.0
    aim_ = _launch_way(way, h.pos, frm, yaw, s)
    if aim_ is None:
        return 0
    u, s = aim_
    first = _launch_friction(h.pos) if way != "up" else _LAUNCH_GROUND
    along, up = _launch_speed(way, s, first)
    _launch_guard(h, landing, _launch_flight(along, up, first)[1])
    reply = aim.run_on(h, "data modify entity @s Motion set value [%.4fd,%.4fd,%.4fd]" % (u[0] * along, up, u[1] * along))
    return 1 if "Modified" in reply else 0


def _launch_friction(at):
    """What is kept of the speed along the ground over the first tick, from the block under at."""
    x, y, z = int(math.floor(at[0])), int(math.floor(at[1] - 0.5)), int(math.floor(at[2]))
    for block, kept in _LAUNCH_SLIDY:
        if mc.rcon(world.prefix(at) + "if block %d %d %d %s" % (x, y, z, block)).startswith("Test passed"):
            return kept
    return _LAUNCH_GROUND


def _launch_spots(at, way, u, n, o):
    """Where the n charges are staged, (x, z) each: a ring for up (turned half a step when the
    first has no open ground), else one spot behind; None when there is none."""
    if way != "up":
        rings = [[(at[0] - u[0] * o, at[2] - u[1] * o)] * n]
    else:
        rings = [[(at[0] + o * math.cos(turn + 2 * math.pi * i / n), at[2] + o * math.sin(turn + 2 * math.pi * i / n))
                  for i in range(n)] for turn in (0.0, math.pi / n)]
    room, floor = int(math.floor(at[1] + 0.6)), int(math.floor(at[1] - 0.5))
    for ring in rings:
        conds = ""
        for x, z in sorted(set((int(math.floor(x)), int(math.floor(z))) for x, z in ring)):
            conds += " if block %d %d %d #minecraft:replaceable unless block %d %d %d #minecraft:replaceable" % (
                x, room, z, x, floor, z)
        if mc.rcon(world.prefix(at) + conds.strip()).startswith("Test passed"):
            return ring
    return None


def _launch_burst(at, spots):
    """Stage a wind charge, still, at each spot just above the feet, then send them all down in
    one command, so they burst on the ground in one tick. True when they went."""
    with _LAUNCH["lock"]:
        _LAUNCH["n"] += 1
        tag = "%s_lw%d" % (mc.TAG, _LAUNCH["n"])
    for x, z in spots:
        mc.rcon(world.run(at, 'summon wind_charge %.3f %.3f %.3f {Tags:["%s","%s","%s"]}'
                          % (x, at[1] + 0.6, z, mc.TAG, tag, instances.LAUNCH_CHARGE)))
    _launch_stamp("@e[type=wind_charge,tag=%s]" % tag)
    out = mc.rcon("execute as @e[type=wind_charge,tag=%s] run data modify entity @s Motion set value [0.0d,-1.0d,0.0d]"
                  % tag)
    return out.count("Modified") == len(spots)


def _launch_ride(name, at, motion, landing):
    """Carry a player on an invisible armor stand given `motion`; the watcher sets them down."""
    with _LAUNCH["lock"]:
        _LAUNCH["n"] += 1
        tag = "%s_lr%d" % (mc.TAG, _LAUNCH["n"])
    mc.rcon(world.run(at, 'summon armor_stand %.3f %.3f %.3f {Invisible:1b,Small:1b,NoGravity:1b,Invulnerable:1b,'
                          'Silent:1b,NoBasePlate:1b,Tags:["%s","%s_lr","%s","%s"]}'
                          % (at[0], at[1], at[2], mc.TAG, mc.TAG, tag, instances.LAUNCH_STAND)))
    _launch_stamp("@e[tag=%s,limit=1]" % tag)
    if "started riding" not in mc.rcon("ride %s mount @e[tag=%s,limit=1]" % (name, tag)):
        mc.rcon("kill @e[tag=%s]" % tag)
        return 0
    mc.rcon("data merge entity @e[tag=%s,limit=1] {NoGravity:0b,Motion:[%.4fd,%.4fd,%.4fd]}" % ((tag,) + tuple(motion)))
    ticks = _launch_flight(math.hypot(motion[0], motion[2]), motion[1], _LAUNCH_AIR)[1]
    with _LAUNCH["lock"]:
        _LAUNCH["rides"][tag] = {"name": name, "since": time.time(), "until": time.time() + ticks / 20.0 * 1.5 + 3,
                                 "air": False}
    _launch_guard(name, landing, ticks)
    return 1


# ---- landing -------------------------------------------------------------------------------------

_LAUNCH = {"lock": threading.Lock(), "n": 0, "busy": 0, "rides": {}, "until": 0.0, "thread": None,
           "cleanup": False, "idle": threading.Event()}


def _launch_tags():
    """(the tag on whatever this copy threw that is in flight, the tag once it has left the
    ground, the tag once it has landed, the modifier's id): instances.launch_tags'."""
    return instances.launch_tags(mc.INSTANCE) + (instances.LAUNCH_MOD,)


def _launch_stamp(sel):
    """Stamp what `sel` (as an execute selector) names with the game time, in vfg_launch."""
    mc.rcon("execute as %s run execute store result score @s %s run time query gametime"
            % (sel, instances.LAUNCH_SCORE))


def _launch_ready():
    """Once a process: the objective, the cleanup at a stop or the end, and a sweep of what an
    earlier run of this copy left. True when it ran (the sweep heals too)."""
    with _LAUNCH["lock"]:
        if _LAUNCH["cleanup"]:
            return False
        _LAUNCH["cleanup"] = True
    mc.rcon("scoreboard objectives add %s dummy" % instances.LAUNCH_SCORE)
    instances.launch_sweep(mc.rcon, mc.INSTANCE)
    mc.on_stop(_launch_cleanup)
    return True


def _launch_guard(who, landing, ticks):
    """Tag what was thrown so the watcher sees it land; spare it fall damage until then unless
    landing is "hurt"; Slow Falling for "float"."""
    g, _, _, mod = _launch_tags()
    cmds = ["tag @s add %s" % g, "execute store result score @s %s run time query gametime" % instances.LAUNCH_SCORE]
    if landing != "hurt":
        cmds.append("attribute @s minecraft:fall_damage_multiplier modifier add %s -1 add_multiplied_total" % mod)
    if landing == "float":
        cmds.append("effect give @s minecraft:slow_falling %d 0 true" % (int(ticks / 20.0) + 10))
    for cmd in cmds:
        if isinstance(who, str):
            mc.rcon("execute as %s run %s" % (who, cmd))
        else:
            aim.run_on(who, cmd)
    with _LAUNCH["lock"]:
        _LAUNCH["until"] = max(_LAUNCH["until"], time.time() + min(_LAUNCH_LONGEST, ticks / 20.0 * 2 + 5))


def _launch_flying(name):
    """True when a player still carries the tag of a throw that has not landed."""
    return mc.rcon("execute if entity @a[name=%s,tag=%s]" % (name, _launch_tags()[0])).startswith("Test passed")


def _launch_start():
    """Start the watcher when it is not running."""
    with _LAUNCH["lock"]:
        if _LAUNCH["thread"] is None:
            _LAUNCH["idle"].clear()
            _LAUNCH["thread"] = loop.background(_launch_watch)


def _launch_watch():
    """Every 0.1 s: set down a rider whose stand has landed (or whose head is in a block), and
    take the modifier and tags off whatever has landed (on the ground, or in water); once the
    last landing is overdue, off everything. Ends when nothing is left to watch."""
    g, air, down, mod = _launch_tags()
    try:
        while not mc.stopping():
            time.sleep(0.1)
            with _LAUNCH["lock"]:
                rides = dict(_LAUNCH["rides"])
                late = time.time() > _LAUNCH["until"]
            for tag, r in rides.items():
                _launch_watch_ride(tag, r)
            mc.rcon("execute as @e[tag=%s,tag=!%s,nbt={OnGround:0b}] run tag @s add %s" % (g, air, air))
            mc.rcon("execute as @e[tag=%s,nbt={OnGround:1b}] run tag @s add %s" % (air, down))
            mc.rcon("execute as @e[tag=%s] at @s if block ~ ~ ~ minecraft:water run tag @s add %s" % (air, down))
            if late and not rides:
                for sel in ("@e", "@a"):
                    mc.rcon("tag %s[tag=%s] add %s" % (sel, g, down))
            _launch_landed(g, air, down, mod)
            with _LAUNCH["lock"]:
                if not _LAUNCH["busy"] and not _LAUNCH["rides"] and not any(
                        mc.rcon("execute if entity %s[tag=%s]" % (sel, g)).startswith("Test passed") for sel in ("@a", "@e")):
                    break
    finally:
        with _LAUNCH["lock"]:
            _LAUNCH["thread"] = None
            _LAUNCH["idle"].set()


def _launch_watch_ride(tag, r):
    """Set a rider down once their stand has left the ground and landed, or is gone, or has
    carried their head into a block, or has flown too long."""
    stand = mc.rcon("data get entity @e[tag=%s,limit=1] OnGround" % tag)
    on = stand.endswith(" 1b")
    if "entity data" in stand and not on:
        r["air"] = True
    head = mc.rcon("execute as %s at @s anchored eyes positioned ^ ^ ^ unless block ~ ~ ~ #minecraft:replaceable"
                   % r["name"]).startswith("Test passed")
    if (on and r["air"]) or head or "entity data" not in stand or time.time() > r["until"]:
        mc.rcon("ride %s dismount" % r["name"])
        mc.rcon("kill @e[tag=%s]" % tag)
        with _LAUNCH["lock"]:
            _LAUNCH["rides"].pop(tag, None)


def _launch_landed(g, air, down, mod):
    """Take the modifier and the three tags off whatever is tagged as landed."""
    for sel in ("@e", "@a"):
        if not mc.rcon("execute if entity %s[tag=%s]" % (sel, down)).startswith("Test passed"):
            continue
        mc.rcon("execute as %s[tag=%s] run attribute @s minecraft:fall_damage_multiplier modifier remove %s"
                % (sel, down, mod))
        mc.rcon("tag %s[tag=%s] remove %s" % (sel, down, g))
        mc.rcon("tag %s[tag=%s] remove %s" % (sel, down, air))
        mc.rcon("tag %s[tag=%s] remove %s" % (sel, down, down))


def _launch_cleanup():
    """At a stop or the script's end: set every rider down, then instances.launch_sweep for this
    copy: Slow Falling for whoever is still in the air, the modifier and the tags off them all."""
    with _LAUNCH["lock"]:
        rides = dict(_LAUNCH["rides"])
        _LAUNCH["rides"].clear()
    for r in rides.values():
        mc.rcon("effect give %s minecraft:slow_falling 30 0 true" % r["name"])
        mc.rcon("ride %s dismount" % r["name"])
    instances.launch_sweep(mc.rcon, mc.INSTANCE)


v = values()
chest_at = world.place(v["at"])     # where the one chest goes; None: beside a player
beside = chest_at is not None and v["at"] in loop.online()     # at a player: beside him, as by default
if v["at"] and chest_at is None:
    mc.log("the chest goes beside a player instead")
for w in WEAPONS:
    if w[3] == "click":
        loop.background(clicks, w, v)
loop.background(lands, v)
done = {}                       # who has a chest beside them ("all" for one chest for everyone): where
for _ in loop(every=2):
    for p in loop.who(v["player"]):
        key = p if v["per_player"] and chest_at is None else "all"
        if key not in done:
            where = chest(p, chest_at, beside)
            if where is not None:
                done[key] = where
                result("%s at %s for %s: %s" % ("Weapon chests" if len(done) > 1 else "A weapon chest",
                                                "; ".join("%d %d %d" % c.block() for c in done.values()),
                                                v["player"], ", ".join(w[0] for w in WEAPONS)),
                       player=v["player"], at=[list(c.block()) for c in done.values()], chests=len(done))
