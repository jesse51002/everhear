"""Nuke: a countdown in chat and titles, then a flash, everything alive in the radius dies and a sphere of the world round a player is carved to air, where loaded."""
ASKS = ["drop a nuke in ten minutes, with the countdown in the chat automatically, and then it will destroy everything in it",
        "what is the block radius", "I said one thousand block radius, not two hundred",
        "I want a nuke", "just nuke the world", "it doesn't nuke the world, it doesn't create a bigger crater"]
from kit import values, result, fx, loop, area, world
import json
import math
import re
import time
import mc
import threading


# --- preset: countdown (copied from _presets/countdown.py) ---
def countdown(v):
    """Count countdown_seconds down for countdown_to ("@a", or names): chat says
    "<countdown_text> in 5m 00s." at the start and at 10 and 5 minutes, 2, 1, 30 and
    10 seconds left; the last ten seconds each show a title and tick. Returns True at zero,
    with nothing shown for it (what comes next is the script's), or False if the script
    stops first, and chat then says it was called off."""
    seconds = v["countdown_seconds"]
    end = time.time() + seconds
    going = [True]
    mc.on_stop(lambda: going[0] and _countdown_say(v, "%s called off." % v["countdown_text"], "green"))
    _countdown_say(v, "%s in %s." % (v["countdown_text"], _countdown_span(seconds)))
    said = set(m for m in (600, 300, 120, 60, 30, 10) if m >= seconds)    # chat already said
    shown = set()                                                         # titles already shown
    while True:
        left = end - time.time()
        if left <= 0:
            going[0] = False
            return True
        for m in (600, 300, 120, 60, 30, 10):
            if m not in said and left <= m:
                said.add(m)
                _countdown_say(v, "%s in %s." % (v["countdown_text"], _countdown_span(m)))
        n = int(math.ceil(left))
        if n <= 10 and n not in shown:
            shown.add(n)
            for to in _countdown_who(v):
                fx.title(to, str(n), sub=v["countdown_text"], color="red", fade=(0, 25, 5))
                fx.sound("block.note_block.bass", None, to=to, volume=2, pitch=0.5)
                fx.sound("block.note_block.bit", None, to=to, volume=2, pitch=2.0 if n > 3 else 1.0)
        if not loop.wait(min(0.1, left)):
            return False


def _countdown_who(v):
    """Who sees it: "@a" as it is, else each of the named players online."""
    return ["@a"] if v["countdown_to"] == "@a" else loop.who(v["countdown_to"])


def _countdown_say(v, text, color="red"):
    for to in _countdown_who(v):
        mc.rcon("tellraw %s %s" % (to, json.dumps({"text": text, "color": color, "bold": True})))


def _countdown_span(s):
    """Seconds as "5m 00s" or "30s"."""
    return "%dm %02ds" % (s // 60, s % 60) if s >= 60 else "%ds" % s


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


def fire_phase(at, seconds, r):
    """Set fire to the air within r blocks of `at` for `seconds`, keeping a 5-block square round
    the middle (and 3 up and down) clear of it, so nobody stands in it; the one who cast it gets
    fire resistance, so the fire does them no harm."""
    dim = world.dim(at)
    x, y, z = (int(math.floor(c)) for c in at[:3])
    ground = area.box(at, r, 1)
    spot = area.box(at, 5, 3)
    for p in loop.who(v["player"]):
        mc.rcon("effect give %s fire_resistance %d 0 true" % (p, int(seconds) + 5))
    end = time.time() + seconds
    while time.time() < end and not mc.stopping():
        for b in area.minus(ground, [spot]):
            mc.rcon(world.run(dim, "fill %d %d %d %d %d %d fire replace air" % tuple(b)))
        fx.particle("flame", at, spread=(r / 2.0, 1, r / 2.0), count=80, speed=0.02)      # the fire, seen
        fx.particle("large_smoke", at, spread=(r / 2.0, 2, r / 2.0), count=30, speed=0.01, up=2)
        fx.particle("soul_fire_flame", at, spread=(r * 0.8, 2, r * 0.8), count=60, speed=0.03)   # rolling blue fire
        fx.particle("lava", at, spread=(r * 0.9, 1, r * 0.9), count=12)                            # embers spitting out
        fx.particle("flame", at, spread=(r, 3, r), count=40, speed=0.05, up=2)                     # tall flames at the edge
        time.sleep(0.25)


v = values()
# The middle is where the first player the player value names stands when it starts (or
# nuke_at: a saved place, x y z, or a player, found then), fixed then. countdown_seconds of
# countdown (600: ten minutes); a stop before zero calls it off. At zero: a white flash and
# blindness, the blast heard by everyone, and with nuke_kill every mob and player in survival
# within crater_radius killed. Then the crater: a ball of crater_radius carved to air from the
# middle out, in the chunks that are loaded (crater_load would forceload all the others first:
# for small ones only), never the bedrock floor nor a protected box. A stop while it carves
# leaves the rest.
who = loop.who(v["player"])
if v["nuke_at"]:
    middle = world.place(v["nuke_at"])
    where = v["nuke_at"]
else:
    feet = mc.pos(who[0]) if who else None
    middle = world.Pos(feet[0], feet[1], feet[2], world.dim_of(who[0])) if feet else None
    where = who[0] if who else v["player"]
if middle is None:
    mc.log("nowhere to drop it: %s" % where)
    result("no nuke: %s is %s" % (where, "not a saved place nor a player online" if v["nuke_at"] else "not online"),
           at=v["nuke_at"] or None, players=who)
    raise SystemExit(0)
spot = "%d %d %d" % middle.block() + (" in " + middle.dim.split(":")[-1] if middle.dim else "")
r = v["crater_radius"]
mc.log("nuke at %s, radius %d, in %d s" % (spot, r, v["countdown_seconds"]))
result("destroy everything at %s: radius %d" % (spot, r), at=spot, radius=r)
fire_phase(middle, v["fire_seconds"], v["crater_radius"])          # the fire buildup, then the blast
if True:                                                            # no countdown text
    r = v["crater_radius"]                                          # as it is now: it is live
    px, py, pz = (int(math.floor(c)) for c in middle[:3])
    platform = world.Box(px - 2, py - 1, pz - 2, px + 2, py - 1, pz + 2, dim=world.dim(middle))
    area.fill(platform, "obsidian", dim=world.dim(middle), keep_bedrock=True)   # 5 by 5 under them
    for p in loop.who(v["player"]):
        mc.rcon("effect give %s slow_falling 30 0 true" % p)     # a fall into the crater is slow and harmless
        mc.rcon("effect give %s resistance 30 4 true" % p)
    here = world.prefix(middle) + "positioned %.1f %.1f %.1f " % tuple(middle)
    for sound in ("entity.generic.explode", "entity.warden.sonic_boom", "entity.lightning_bolt.thunder"):
        mc.rcon("execute as @a at @s run playsound minecraft:%s master @s ~ ~ ~ 1000 0.6" % sound)
    fx.particle("explosion_emitter", middle, spread=min(30, r), count=60)
    killed = 0
    if v["nuke_kill"]:
        spared = "".join(",name=!%s" % mc.bare(p) for p in loop.who(v["player"]))   # the caster is never hurt
        for c in ("kill @e[type=!player,type=!item,distance=..%d]" % r,
                  "kill @a[distance=..%d,gamemode=!creative,gamemode=!spectator%s]" % (r, spared)):
            said = mc.rcon(here + "run " + c)                      # "Killed 5 entities", "Killed Bob"
            m = re.match(r"Killed (\d+) entities", said)
            killed += int(m.group(1)) if m else 1 if said.startswith("Killed ") else 0
    n = crater(middle, v)
    area.fill(platform, "obsidian", dim=world.dim(middle), keep_bedrock=True)   # put the platform back
    for p in loop.who(v["player"]):
        mc.rcon("tp %s %d.5 %d %d.5" % (p, px, py, pz))                       # and them back onto it
    result("nuke went off at %s: radius %d, %d killed, %d blocks gone" % (spot, r, killed, n),
           at=spot, radius=r, killed=killed, blocks=n)
