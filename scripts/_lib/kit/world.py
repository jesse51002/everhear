"""The world as 26.3 has it: limits, a position that knows its dimension, many conditions
tested in one command, the ground's height, the server's mspt, a gamerule set and put back,
an objective the script owns, and the protected boxes.

A command sent over RCON runs in the overworld. Anything at coordinates in another
dimension goes through run(dim, cmd) or prefix(dim), which add "execute in <dim>".
"""
import json
import math
import os
import re
import threading
import time

import mc

MAX_FILL = 32768          # blocks in one fill; more is "Too many blocks in the specified area"
MAX_FORCELOAD = 256       # chunks in one forceload add
MAX_REPLY = 4096          # bytes of a reply; the rest is cut
MAX_COMMAND = mc.MAX_COMMAND
OVERWORLD = "minecraft:overworld"
NETHER = "minecraft:the_nether"
END = "minecraft:the_end"
LIMITS = {OVERWORLD: (-64, 319), NETHER: (0, 255), END: (0, 255)}
# The lowest and highest y that is not bedrock floor or roof: carving stays inside this.
NO_BEDROCK = {OVERWORLD: (-59, 319), NETHER: (5, 122), END: (0, 255)}

# Names that 26.3 refuses, and what to use instead. A selector or fill naming one fails whole.
GONE = {
    "potion": "entity type: splash_potion or lingering_potion",
    "boat": "entity type: oak_boat, spruce_boat... (or the tag #minecraft:boat)",
    "chest_boat": "entity type: oak_chest_boat, spruce_chest_boat...",
    "#minecraft:tall_flowers": "block tag: name the flowers (sunflower, lilac, rose_bush, peony)",
    "#minecraft:mineable/sword": "block tag: #minecraft:sword_efficient",
}
# Gamerules are snake_case on 26.3, and the old camelCase names are refused. Each name here
# was asked of a 26.3 server. disableRaids is now raids, doFireTick fire_spread_radius_around_player
# (a number) and disableElytraMovementCheck elytra_movement_check: each the other way round.
GAMERULES = {
    "doDaylightCycle": "advance_time", "doWeatherCycle": "advance_weather", "doMobSpawning": "spawn_mobs",
    "doInsomnia": "spawn_phantoms", "doPatrolSpawning": "spawn_patrols", "doTraderSpawning": "spawn_wandering_traders",
    "doWardenSpawning": "spawn_wardens", "doImmediateRespawn": "immediate_respawn",
    "naturalRegeneration": "natural_health_regeneration", "randomTickSpeed": "random_tick_speed",
    "showDeathMessages": "show_death_messages", "commandBlockOutput": "command_block_output",
    "sendCommandFeedback": "send_command_feedback", "drowningDamage": "drowning_damage", "fireDamage": "fire_damage",
    "freezeDamage": "freeze_damage", "fallDamage": "fall_damage", "doEntityDrops": "entity_drops",
    "doTileDrops": "block_drops", "doMobLoot": "mob_drops", "announceAdvancements": "show_advancement_messages",
    "playersSleepingPercentage": "players_sleeping_percentage", "spawnRadius": "respawn_radius",
    "universalAnger": "universal_anger", "forgiveDeadPlayers": "forgive_dead_players",
    "maxEntityCramming": "max_entity_cramming", "doLimitedCrafting": "limited_crafting", "keepInventory": "keep_inventory",
    "mobGriefing": "mob_griefing", "doVinesSpread": "spread_vines", "lavaSourceConversion": "lava_source_conversion",
    "waterSourceConversion": "water_source_conversion", "projectilesCanBreakBlocks": "projectiles_can_break_blocks",
    "enderPearlsVanishOnDeath": "ender_pearls_vanish_on_death", "globalSoundEvents": "global_sound_events",
    "maxCommandChainLength": "max_command_sequence_length", "spectatorsGenerateChunks": "spectators_generate_chunks",
    "snowAccumulationHeight": "max_snow_accumulation_height", "tntExplodes": "tnt_explodes",
    "tntExplosionDropDecay": "tnt_explosion_drop_decay", "blockExplosionDropDecay": "block_explosion_drop_decay",
    "mobExplosionDropDecay": "mob_explosion_drop_decay",
}

PROTECTED_FILE = os.environ.get("VFG_PROTECTED", "/opt/vfg/protected.json")

_lock = threading.Lock()


class Pos(tuple):
    """(x, y, z) as floats that also knows its dimension: pos.dim is "minecraft:the_nether",
    or None for the overworld. Everything in the kit that takes a place takes a Pos or a tuple."""

    def __new__(cls, x, y, z, dim=None):
        p = tuple.__new__(cls, (float(x), float(y), float(z)))
        p.dim = None if dim == OVERWORLD else dim
        return p

    def __repr__(self):
        return "Pos(%.2f, %.2f, %.2f%s)" % (self[0], self[1], self[2], ", %r" % self.dim if self.dim else "")

    def moved(self, dx=0.0, dy=0.0, dz=0.0):
        """This place moved by dx, dy, dz, in the same dimension."""
        return Pos(self[0] + dx, self[1] + dy, self[2] + dz, self.dim)

    def block(self):
        """The block this place is in, as ints."""
        return int(self[0] // 1), int(self[1] // 1), int(self[2] // 1)


class Box(tuple):
    """(x1, y1, z1, x2, y2, z2) as ints, low corner first, that also knows its dimension:
    box.dim, None for the overworld. area.box and area.chunk_box make one; a plain 6-tuple
    is a box in the overworld (or in the dim= given with it)."""

    def __new__(cls, x1, y1, z1, x2, y2, z2, dim=None):
        b = [int(math.floor(v)) for v in (x1, y1, z1, x2, y2, z2)]
        t = tuple.__new__(cls, (min(b[0], b[3]), min(b[1], b[4]), min(b[2], b[5]),
                                max(b[0], b[3]), max(b[1], b[4]), max(b[2], b[5])))
        t.dim = None if dim == OVERWORLD else dim
        return t

    def __repr__(self):
        return "Box(%d, %d, %d, %d, %d, %d%s)" % (self + ((", %r" % self.dim,) if self.dim else ("",)))


def dim(at):
    """The dimension of a place or a box (a Pos's or Box's .dim), or None for the overworld."""
    d = getattr(at, "dim", None)
    return None if d == OVERWORLD else d


def prefix(d=None):
    """"execute " or "execute in <dim> ", to start a command at coordinates in that dimension."""
    d = dim(d) if not isinstance(d, str) and d is not None else d
    return "execute " if not d or d == OVERWORLD else "execute in %s " % d


def run(d, cmd):
    """cmd as it is in the overworld, else "execute in <dim> run <cmd>"."""
    d = dim(d) if not isinstance(d, str) and d is not None else d
    return cmd if not d or d == OVERWORLD else "execute in %s run %s" % (d, cmd)


def limits(d=None):
    """(lowest y, highest y) of the build height in a dimension."""
    return LIMITS.get(d or OVERWORLD, LIMITS[OVERWORLD])


_dims = {}


def dim_of(player):
    """The dimension a player is in ("minecraft:the_nether"), None for the overworld or unknown.
    Read at most every 2 s per player."""
    now = time.time()
    hit = _dims.get(player)
    if hit and now - hit[1] < 2.0:
        return hit[0]
    m = re.search(r'"([a-z0-9_.-]+:[a-z0-9_./-]+)"', mc.rcon("data get entity %s Dimension" % player))
    d = m.group(1) if m else None
    d = None if d == OVERWORLD else d
    _dims[player] = (d, now)
    return d


# ---- many conditions in one command ------------------------------------------------------

def _passed(reply):
    return "test passed" in reply.lower()


def _test(conds, d, word="if"):
    """One command: "execute [in d] if <c1> if <c2> ..." (or unless). Its reply."""
    if word == "unless":
        conds = ["unless" + c[2:] if c.startswith("if ") else c for c in conds]
    return mc.rcon(prefix(d) + " ".join(conds))


def _groups(conds, d, budget=1200, most=32):
    """Index ranges of conds that fit in one command."""
    start, size = 0, len(prefix(d))
    for i, c in enumerate(conds):
        if i > start and (size + len(c) + 1 > budget or i - start >= most):
            yield start, i
            start, size = i, len(prefix(d))
        size += len(c) + 1
    if start < len(conds):
        yield start, len(conds)


def first_fail(conds, d=None):
    """The index of the first condition ("if block 1 64 2 #minecraft:replaceable", "if loaded
    16 64 32") that does not pass, and the server's reply to it alone ("Test failed",
    "That position is not loaded", "That position is out of this world!"); (None, "") when
    all pass. Up to 32 are tested in one command, then the failing group is halved. On 26.3 a
    chain of several conditions answers "Test passed" or nothing at all (a failure and an
    error look the same); only a condition alone says why."""
    for lo, hi in _groups(conds, d):
        if _passed(_test(conds[lo:hi], d)):
            continue
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if _passed(_test(conds[lo:mid], d)):
                lo = mid
            else:
                hi = mid
        return lo, _test(conds[lo:lo + 1], d)
    return None, ""


def passes(conds, d=None):
    """Which of many "if ..." conditions pass, as a list of bools, in few commands: a group
    that all pass, or all fail (tested with "unless"), costs one; a mixed one is halved."""
    out = [False] * len(conds)

    def classify(lo, hi):
        if _passed(_test(conds[lo:hi], d)):
            out[lo:hi] = [True] * (hi - lo)
            return
        if hi - lo == 1:
            return
        if all(c.startswith("if ") for c in conds[lo:hi]) and _passed(_test(conds[lo:hi], d, "unless")):
            return
        mid = (lo + hi) // 2
        classify(lo, mid)
        classify(mid, hi)

    for lo, hi in _groups(conds, d):
        classify(lo, hi)
    return out


def loaded(x, z, d=None):
    """True if the chunk holding block column x, z is loaded in that dimension."""
    return _passed(mc.rcon(prefix(d) + "if loaded %d 64 %d" % (int(x // 1), int(z // 1))))


_GROUND = re.compile(r"\[(-?[\d.]+)d, (-?[\d.]+)d, (-?[\d.]+)d\]")
_ground_n = [0]


def ground(x, z, d=None, surface="motion_blocking_no_leaves"):
    """The height a player would stand at on top of block column x, z: the y of the first
    open block over the ground, in three commands (a marker summoned at the surface with a tag
    of its own, read, and killed by that tag whatever the read said, so none is left and no
    other marker is touched). surface: "motion_blocking_no_leaves" (ground, water, trunks;
    leaves are passed through), "motion_blocking" (leaves too), "world_surface" (any block:
    grass, flowers), "ocean_floor" (the bottom under water). None in a chunk that is not loaded."""
    x, z = int(x // 1), int(z // 1)
    with _lock:
        _ground_n[0] += 1
        tag = "%s_ground%d" % (mc.TAG, _ground_n[0])
    mc.rcon(prefix(d) + 'positioned %d 0 %d positioned over %s run summon marker ~ ~ ~ {Tags:["vfg","%s","%s"]}'
            % (x, z, surface, mc.TAG, tag))
    m = _GROUND.search(mc.rcon("data get entity @e[tag=%s,limit=1] Pos" % tag))
    mc.rcon("kill @e[tag=%s]" % tag)
    return int(float(m.group(2)) // 1) if m else None


# ---- the server's load -----------------------------------------------------------------

_ms = {"value": 0.0, "at": 0.0}
_ms_lock = threading.Lock()


def mspt():
    """Milliseconds per tick, from "tick query" (read at most every 0.5 s); 0.0 when unknown."""
    with _ms_lock:
        if time.time() - _ms["at"] >= 0.5:
            m = re.search(r"Average time per tick: ([\d.]+) ?ms", mc.rcon("tick query"))
            if m:
                _ms["value"] = float(m.group(1))
            _ms["at"] = time.time()
        return _ms["value"]


def throttle(most=45.0, longest=10.0):
    """Wait while the server is slower than `most` ms per tick (50 is lag), up to `longest`
    seconds. Call it between big fills. Returns the last mspt read."""
    if mc.cleaning():
        return _ms["value"]
    end = time.time() + longest
    m = mspt()
    while m >= most and time.time() < end and not mc.stopping():
        time.sleep(0.5)
        m = mspt()
    return m


# ---- gamerules, put back as they were ---------------------------------------------------

_rules = {}


def gamerule(name, value=None):
    """Set a gamerule, or read it when value is None. The first time a script sets one its
    old value is kept, and put back when the script stops. Names are snake_case on 26.3
    (fall_damage, mob_griefing, keep_inventory, advance_time); camelCase is translated.
    Returns the value as the server reports it, or None."""
    name = GAMERULES.get(name, name)
    if name != name.lower():
        name = re.sub(r"(?<!^)([A-Z])", r"_\1", name).lower()
    m = re.search(r"currently set to:? ?(\S+)", mc.rcon("gamerule %s" % name))
    now = m.group(1) if m else None
    if value is None:
        return now
    value = str(value).lower() if isinstance(value, bool) else str(value)
    if now is None:
        mc.log("gamerule", name, "unknown on this server; not set")
        return None
    with _lock:
        if name not in _rules:
            if not _rules:
                mc.on_stop(restore)
            _rules[name] = now
    reply = mc.rcon("gamerule %s %s" % (name, value))
    m = re.search(r"now set to:? ?(\S+)", reply)
    return m.group(1) if m else now


def restore(name=None):
    """Put back a gamerule this script changed (or all of them) to what it was before."""
    with _lock:
        names = [name] if name else list(_rules)
        olds = [(n, _rules.pop(n)) for n in names if n in _rules]
    for n, old in olds:
        mc.rcon("gamerule %s %s" % (n, old))


# ---- objectives the script owns ---------------------------------------------------------

_objectives = []


def objective(criterion="dummy", name=""):
    """Make a scoreboard objective named TAG (or TAG_<name>) and return its name; it is
    removed when the script stops. For a statistic every script shares, see items.Stat."""
    obj = mc.TAG + ("_" + name if name else "")
    mc.rcon("scoreboard objectives add %s %s" % (obj, criterion))
    with _lock:
        if not _objectives:
            mc.on_stop(lambda: [mc.rcon("scoreboard objectives remove " + o) for o in list(_objectives)])
        if obj not in _objectives:
            _objectives.append(obj)
    return obj


# ---- protected boxes -----------------------------------------------------------------------

_prot = {"mtime": None, "boxes": []}


def protected(d=None):
    """The protected boxes (x1, y1, z1, x2, y2, z2) in a dimension, from the protected file
    (VFG_PROTECTED, default /opt/vfg/protected.json): a JSON list of
    {"name": "castle", "dim": "minecraft:overworld", "box": [x1, y1, z1, x2, y2, z2]}.
    Re-read when it changes; [] when there is none."""
    try:
        mtime = (PROTECTED_FILE, os.stat(PROTECTED_FILE).st_mtime)
    except OSError:
        _prot.update(mtime=None, boxes=[])
        return []
    if mtime != _prot["mtime"]:
        boxes = []
        try:
            with open(PROTECTED_FILE, encoding="utf-8") as f:
                for e in json.load(f):
                    b = [int(v) for v in e["box"]]
                    box = (min(b[0], b[3]), min(b[1], b[4]), min(b[2], b[5]),
                           max(b[0], b[3]), max(b[1], b[4]), max(b[2], b[5]))
                    bd = e.get("dim") or OVERWORLD
                    boxes.append((None if bd == OVERWORLD else bd, box, str(e.get("name", ""))))
        except (OSError, ValueError, KeyError, TypeError, IndexError) as e:
            mc.log("protected file unreadable, nothing protected:", type(e).__name__, e)
            boxes = []
        _prot.update(mtime=mtime, boxes=boxes)
    d = None if d == OVERWORLD else d
    return [b for bd, b, _ in _prot["boxes"] if bd == d]


def is_protected(at):
    """True if a place (Pos or tuple) is inside a protected box."""
    x, y, z = (int(v // 1) for v in at[:3])
    return any(b[0] <= x <= b[3] and b[1] <= y <= b[4] and b[2] <= z <= b[5] for b in protected(dim(at)))
