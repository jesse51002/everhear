"""Boom Trident: every trident a player throws explodes like a charged creeper where it lands or hits, sparing the thrower; with crater on it also carves a crater there, a spear bomb."""
ASKS = ["give me a trident that explodes", "it exploded on me, make sure it blows on the target it hits, not when it comes back to me",
        "my trident is not consistent, it doesn't explode every single time, make it work consistent",
        "give me a spear bomb", "spear bomb and make it like the biggest explosion humanly possible"]
from kit import values, result, items, loop, world, exclude, area
import json
import re
import time
import mc
import math
import threading


def trident(name):
    """The trident it gives: it comes back when thrown (loyalty), never breaks, and is known by
    its id (items.item_id of its name: "boom_trident", "spear_bomb")."""
    return ('trident[custom_data={vfg_item:"%s"},item_name=%s,enchantments={"minecraft:loyalty":3},unbreakable={},'
            'enchantment_glint_override=true]' % (items.item_id(name), json.dumps({"text": name, "color": "red"})))


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


# --- preset: crater (copied from _presets/crater.py) ---
def crater(at, v, *, path=()):
    """Carve a ball of crater_radius round a place, the middle first, never the bedrock floor
    nor (with crater_spare_protected) a protected box; then crater_leave: "air", "lava" (a
    pool in its lowest third) or "fire" (on its floor, where fire can stand). path: places
    along a beam (r.every(2)), each with a cube crater_tunnel blocks round it carved first:
    the beam's tunnel (0: none). crater_radius 0 carves nothing at all, the tunnel neither.
    crater_load loads the chunks it reaches first, when they are few (up to 256 chunks, a
    radius of about 120), so a big crater is whole, and lets them go after; else only the
    loaded part is carved. It takes a while when big: call it in loop.background. One carves
    at a time and one more waits; any more are skipped. Returns blocks changed."""
    if v["crater_radius"] <= 0:
        return 0
    with _CRATER_LOCK:
        if _CRATER_QUEUE[0] >= 2:
            mc.log("crater skipped: one is carving and one waits already")
            return 0
        _CRATER_QUEUE[0] += 1
    try:
        with _CRATER_CARVING:
            return _crater_carve(at, v, path)
    finally:
        with _CRATER_LOCK:
            _CRATER_QUEUE[0] -= 1


_CRATER_LOCK = threading.Lock()
_CRATER_CARVING = threading.Lock()
_CRATER_QUEUE = [0]         # craters carving or waiting


def _crater_carve(at, v, path):
    r, leave, spare = v["crater_radius"], v["crater_leave"], v["crater_spare_protected"]
    dim = world.dim(at)
    box = area.box(at, r, 0)
    load = v["crater_load"] and len(area.chunks(box)) <= world.MAX_FORCELOAD
    if v["crater_load"] and not load:
        mc.log("crater r=%d: too big to load first, only the loaded part is carved" % r)
    if load:
        area.forceload(box, dim=dim, wait=10.0)
    try:
        n, loaded = 0, {}
        for p in path if v["crater_tunnel"] else ():    # the tunnel: a cube round each place
            n += area.fill(area.box(p, v["crater_tunnel"]), "air", dim=dim, spare_protected=spare,
                           keep_bedrock=True, loaded=loaded)
        n += area.sphere(at, r, "air", spare_protected=spare, keep_bedrock=True, dim=dim)
        if leave != "air":
            # Lava fills the lowest third; fire is put everywhere below the middle and stays
            # only where it can stand: on the floor, and against what burns.
            top = int(math.floor(at[1])) - (r - max(1, r // 3) if leave == "lava" else 0)
            for b in area.sphere_boxes(at, r):
                if b[1] <= top:
                    n += area.fill((b[0], b[1], b[2], b[3], min(b[4], top), b[5]), leave, "replace air", dim=dim,
                                   spare_protected=spare, keep_bedrock=True, loaded=loaded)
        mc.log("crater r=%d at %d %d %d: %d blocks" % ((r,) + tuple(int(math.floor(c)) for c in at[:3]) + (n,)))
        return n
    finally:
        if load:
            area.release(box)


v = values()
given = set()


def arm():
    """Give each player the trident once, when they are first seen."""
    while loop.wait(1):
        for p in loop.who(v["player"]):
            if p not in given:
                given.add(p)
                if not items.has(p, v["item"]) and "Gave" in mc.rcon("give %s %s" % (p, trident(v["item"]))):
                    mc.log("gave a %s to" % v["item"], p)


if v["give"]:
    mc.on_stop(lambda: [items.take(p, v["item"]) for p in sorted(given)])
    loop.background(arm)
    result("%s gets the %s: throw it and it %s where it lands" % (
        v["player"], v["item"], "blows a crater" if v["crater"] else "explodes"), player=v["player"], item=v["item"])
else:
    result("%s's thrown tridents %s where they land" % (v["player"], "blow a crater" if v["crater"] else "explode"),
           player=v["player"])
for at, p, weapon in loop(projectile_lands(v["player"], v)):
    n = explosion(at, p, v)
    if v["crater"]:
        loop.background(crater, at, v)
    mc.log("%s's trident went off at %d %d %d: %d blasts" % ((p,) + at.block() + (n,)))
