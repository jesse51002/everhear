"""Nuke: a countdown in chat and titles, then a flash, everything alive in the radius dies and a sphere of the world round a player is carved to air, where loaded."""
ASKS = ["drop a nuke in ten minutes, with the countdown in the chat automatically, and then it will destroy everything in it",
        "what is the block radius", "I said one thousand block radius, not two hundred",
        "I want a nuke", "just nuke the world", "it doesn't nuke the world, it doesn't create a bigger crater"]
from kit import values, fx, loop, area, world
import json
import math
import time
import mc
import threading

VALUES = {
    "player":                 {"type": "player", "default": "@a"},
    "nuke_at":                {"type": "list", "of": "number", "default": []},
    "nuke_kill":              {"type": "bool", "default": True, "live": True},
    "countdown_seconds":      {"type": "int", "min": 1, "max": 3600, "default": 600},
    "countdown_text":         {"type": "str", "default": "\u2622 Nuke", "live": True},
    "countdown_to":           {"type": "player", "default": "@a", "live": True},
    "crater_radius":          {"type": "int", "min": 0, "max": 1000, "default": 1000, "live": True},
    "crater_tunnel":          {"type": "int", "min": 0, "max": 8, "default": 0, "live": True},
    "crater_leave":           {"type": "enum", "of": ["air", "lava", "fire"], "default": "air", "live": True},
    "crater_spare_protected": {"type": "bool", "default": True, "live": True},
    "crater_load":            {"type": "bool", "default": False, "live": True},
}


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


v = values(VALUES)
# The middle is where the first player the player value names stands when it starts (or
# nuke_at, x y z, in their dimension), fixed then. countdown_seconds of countdown (600: ten
# minutes); a stop before zero calls it off. At zero: a white flash and blindness, the blast
# heard by everyone, and with nuke_kill every mob and player in survival within crater_radius
# killed. Then the crater: a ball of crater_radius carved to air from the middle out, in the
# chunks that are loaded (crater_load would forceload all the others first: for small ones
# only), never the bedrock floor nor a protected box. A stop while it carves leaves the rest.
who = loop.who(v["player"])
feet = mc.pos(who[0]) if who else None
if feet is None:
    mc.log("nobody online to drop it on")
    raise SystemExit(0)
at = list(v["nuke_at"])[:3] if len(v["nuke_at"]) >= 3 else feet
middle = world.Pos(at[0], at[1], at[2], world.dim_of(who[0]))
r = v["crater_radius"]
mc.log("nuke at %d %d %d%s, radius %d, in %d s" % (middle.block() + (" in " + middle.dim if middle.dim else "", r,
                                                                      v["countdown_seconds"])))
_countdown_say(v, "%s: middle %.0f %.0f %.0f, radius %d. Everything in it goes." % (
    v["countdown_text"], middle[0], middle[1], middle[2], r))
if countdown(v):
    r = v["crater_radius"]                                          # as it is now: it is live
    here = world.prefix(middle) + "positioned %.1f %.1f %.1f " % tuple(middle)
    fx.title("@a", "\u2588" * 30, sub="\u2588" * 30, color="white", fade=(0, 40, 40))
    mc.rcon(here + "run effect give @a[distance=..%d] blindness 2 0 true" % (r + 100))
    for sound in ("entity.generic.explode", "entity.warden.sonic_boom", "entity.lightning_bolt.thunder"):
        mc.rcon("execute as @a at @s run playsound minecraft:%s master @s ~ ~ ~ 1000 0.6" % sound)
    fx.particle("explosion_emitter", middle, spread=min(30, r), count=60)
    if v["nuke_kill"]:
        mc.rcon(here + "run kill @e[type=!player,type=!item,distance=..%d]" % r)
        mc.rcon(here + "run kill @a[distance=..%d,gamemode=!creative,gamemode=!spectator]" % r)
    _countdown_say(v, "%s: detonated." % v["countdown_text"])
    n = crater(middle, v)
    _countdown_say(v, "%s: crater complete, %d blocks gone." % (v["countdown_text"], n), "gray")
