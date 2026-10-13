"""Launch a player or a mob through the air (a launch pad, a jump pad, a dash that flings you forward, a high jump, a blast that throws them back): forward where they face, up, away from a place or toward it, about so many blocks, and land them safely."""
import math
import random
import re
import threading
import time

import instances
import mc
from kit import aim, loop, world
from actions._types import Field, Literal, Live, Values  # the values class: read on the PC only


class Launch(Values):
    launch_way: Live[Literal["forward", "up", "away", "toward"]] = "up"
    """which way: forward where they face, straight up, away from where it comes from, or toward it (never past it)"""
    launch_strength: Live[float] = Field(10, ge=1, le=64)
    """how far, in blocks: how high they go for up, how far along the ground they land for the others"""
    launch_landing: Live[Literal["safe", "float", "hurt"]] = "safe"
    """how they come down: safe (no fall damage until they land), float (drifting down slowly, no fall damage), or hurt (a long fall hurts and can kill)"""
    launch_method: Live[Literal["wind", "ride"]] = "wind"
    """how a player is thrown: wind (wind charges burst at their feet and they fly free, as from a wind charge jump) or ride (carried along the arc on an unseen stand, then set down)"""


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
