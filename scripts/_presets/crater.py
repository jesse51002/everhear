"""Blow a hole in the world at a place, and a tunnel along a beam: a crater of air, a lava pool or a burning floor, any size, or none."""
import math
import threading

import mc
from kit import area, world
from actions._types import Field, Literal, Live, Values  # the values class: read on the PC only

VALUES = {
    "crater_radius": {"type": "int", "min": 0, "max": 300, "default": 8, "live": True},
    "crater_tunnel": {"type": "int", "min": 0, "max": 8, "default": 0, "live": True},
    "crater_leave": {"type": "enum", "of": ["air", "lava", "fire"], "default": "air", "live": True},
    "crater_spare_protected": {"type": "bool", "default": True, "live": True},
    "crater_load": {"type": "bool", "default": True, "live": True},
}


class Crater(Values):
    crater_radius: Live[int] = Field(8, ge=0, le=300)
    """how big the hole is, a ball this many blocks round the place (0: no hole and no tunnel)"""
    crater_tunnel: Live[int] = Field(0, ge=0, le=8)
    """a tunnel along a beam's path, carved this many blocks each way round it (0: none)"""
    crater_leave: Live[Literal["air", "lava", "fire"]] = "air"
    """what is left in the hole: air; lava, a pool in its lowest third; fire, burning on its floor"""
    crater_spare_protected: Live[bool] = True
    """leave the protected areas whole; the bedrock floor is never carved"""
    crater_load: Live[bool] = True
    """load the chunks it reaches first, so the whole hole is carved (up to a radius of about 120); off: only what is loaded"""


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
