"""Ice Staff: right-click to lock onto the mob you point at, or the block, and freeze and hurt every mob round it."""
ASKS = ["I want some ice ability", "make the range of the ice staff infinite",
        "my staff should do a lot more damage, there is like no damage",
        "make my ice staff work whenever I point at a mob as well and not just the ground"]
from kit import values, items, aim, fx, loop, exclude, world, area
import itertools
import math
import mc
import re
import threading

VALUES = {
    "player":           {"type": "player", "default": "@a"},
    "item":             {"type": "str", "default": "Ice Staff"},
    "cooldown":         {"type": "number", "min": 0, "max": 60, "default": 1, "live": True},
    "damage_amount":    {"type": "number", "min": 0, "max": 100000, "default": 200, "live": True},
    "damage_type":      {"type": "enum", "of": ["player_attack", "magic", "lightning_bolt", "explosion", "freeze", "on_fire", "sonic_boom", "arrow", "wither", "generic"], "default": "magic", "live": True},
    "damage_kill":      {"type": "bool", "default": False, "live": True},
    "damage_what":      {"type": "enum", "of": ["all", "mobs", "hostile", "players"], "default": "all", "live": True},
    "damage_most":      {"type": "int", "min": 0, "max": 1000, "default": 0, "live": True},
    "damage_head":      {"type": "number", "min": 1, "max": 10, "default": 1, "live": True},
    "damage_spare":     {"type": "list", "of": "player", "default": [], "live": True},
    "lock_range":       {"type": "int", "min": 4, "max": 500, "default": 300, "live": True},
    "lock_cone":        {"type": "number", "min": 1, "max": 45, "default": 6, "live": True},
    "lock_players":     {"type": "bool", "default": False, "live": True},
    "around_radius":    {"type": "number", "min": 1, "max": 128, "default": 15, "live": True},
    "around_what":      {"type": "enum", "of": ["mobs", "hostile", "players", "everything", "projectiles"], "default": "mobs", "live": True},
    "around_most":      {"type": "int", "min": 0, "max": 1000, "default": 0, "live": True},
    "freeze_seconds":   {"type": "number", "min": 1, "max": 120, "default": 5, "live": True},
    "freeze_shell":     {"type": "bool", "default": False, "live": True},
    "freeze_block":     {"type": "enum", "of": ["ice", "packed_ice", "blue_ice", "snow_block"], "default": "ice", "live": True},
    "freeze_blindness": {"type": "bool", "default": False, "live": True},
}


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


# --- preset: lock_on (copied from _presets/lock_on.py) ---
def lock_on(player, v):
    """The nearest mob (and player, with lock_players) within lock_cone degrees of where a
    player looks, or within a block of the look up close, up to lock_range blocks and never
    behind the first solid block, as a list of one aim.Hit, or [] when there is none: the
    shape damage_hits, freeze and push take. Never the player, a pet, a villager or anything
    else exclude spares. It looks out in 16-block legs, nearest first, and stops at the first
    leg with something in the cone."""
    lk = aim.look(player)
    if lk is None:
        return []
    r = aim.ray(lk, v["lock_range"])
    tan = math.tan(math.radians(v["lock_cone"]))
    a = 0.0
    while a < r.length:
        b = min(r.length, a + 16.0)
        leg = aim.Ray(r.point(a), r.dir, b - a)
        best = None
        for h in aim.near_line(leg, max(1.0, b * tan) + 0.5, shooter=player, with_players=v["lock_players"]):
            t = _lock_on_in_cone(lk, h.pos, tan)
            if t is not None and t <= r.length + 1 and (best is None or t < best[0]):
                best = (t, h)
        if best:
            t, h = best
            return [aim.Hit(t, h.pos, h.name, h.player)]
        a = b
    return []


def _lock_on_in_cone(lk, feet, tan):
    """How far along the look a body standing at `feet` is, if its feet, middle or head is
    in the cone (or within a block of the look up close); None if it is not."""
    o, d = lk.origin, lk.dir
    for up in (0.9, 0.2, 1.6):
        w = (feet[0] - o[0], feet[1] + up - o[1], feet[2] - o[2])
        t = w[0] * d[0] + w[1] * d[1] + w[2] * d[2]
        if t <= 0.5:
            continue
        off = math.sqrt(max(0.0, w[0] ** 2 + w[1] ** 2 + w[2] ** 2 - t * t))
        if off <= max(1.0, t * tan):
            return t
    return None


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


v = values(VALUES)
for p in loop(items.clicks(v["player"], v["item"], cooldown=v["cooldown"])):
    locked = lock_on(p, v)
    if locked:
        at = locked[0].pos
    else:
        r = aim.ray(p, v["lock_range"])
        if r is None:
            continue
        at = r.end
    fx.sound("block.glass.break", at, volume=3, pitch=0.5)
    fx.particle("snowflake", at, spread=4, count=400, speed=0.05, up=1)
    hits = around(at, v)
    freeze(hits, v)
    damage_hits(hits, p, v)
