"""When a player leaves an area (an arena nobody can leave, a player kept near the others): a radius round a place, a box, or the reach of other players."""
import math

import mc
from kit import loop, world
from actions._types import Field, Literal, Live, Players, Values  # the values class: read on the PC only


class LeavesArea(Values):
    leaves_area: Literal["radius", "box", "player"] = "radius"
    """where players must stay: radius, within leaves_radius of leaves_center; box, in leaves_box; player, near leaves_of_player"""
    leaves_radius: Live[float] = Field(40, ge=1, le=10000)
    """how far a player may go, in blocks across the ground: from leaves_center, or for player from the nearest of leaves_of_player"""
    leaves_center: Live[list[float]] = []
    """for radius: the middle, as x, y, z; [] for where the first player stood when it started"""
    leaves_box: Live[list[float]] = []
    """for box: two opposite corners, as x1, y1, z1, x2, y2, z2; fewer than six numbers keep nobody in"""
    leaves_of_player: Live[Players] = "@a"
    """for player: who the others must stay near, as names separated by commas or @a"""


def leaves_area(player, v):
    """A generator: (p, back) each time a player the player value names goes out, where back
    is a Pos to send them to. leaves_area "radius": out past leaves_radius blocks (flat) from
    leaves_center (x, y, z; [] for where the first of them stood when it started), back is
    the centre. "box": out of leaves_box (x1, y1, z1, x2, y2, z2), back is where they last
    stood inside. "player": out when none of leaves_of_player (others than p) is within
    leaves_radius, back is the nearest of them. The area is in the dimension the first of
    them was in when it started (not for "player"). Out at the first look counts as leaving;
    it fires again only once they have been back inside."""
    out, last, home = set(), {}, None
    for _ in loop(every=0.5):
        watched = loop.who(player)
        anchors = loop.who(v["leaves_of_player"]) if v["leaves_area"] == "player" else []
        where = {p: _leaves_at(p) for p in set(watched) | set(anchors)}
        if home is None and watched and where[watched[0]]:
            home = where[watched[0]]
            mc.log("area round %.0f %.0f %.0f" % home[:3], home.dim or "")
        for p in watched:
            at = where[p]
            if at is None or home is None:
                continue
            back = _leaves_back(p, at, home, last.get(p), [where[q] for q in anchors if q != p], v)
            if back is None:
                out.discard(p)
                last[p] = at
            elif p not in out:
                out.add(p)
                yield p, back


def _leaves_at(p):
    """Where a player stands, as a Pos that knows its dimension; None if they cannot be read."""
    at = mc.pos(p)
    return world.Pos(at[0], at[1], at[2], world.dim_of(p)) if at else None


def _leaves_flat(a, b):
    return math.hypot(a[0] - b[0], a[2] - b[2])


def _leaves_back(p, at, home, last, anchors, v):
    """None while p is inside; else the Pos to send them back to."""
    r = v["leaves_radius"]
    if v["leaves_area"] == "player":
        near = [q for q in anchors if q and q.dim == at.dim]
        best = min(near, key=lambda q: _leaves_flat(q, at)) if near else None
        return best if best and _leaves_flat(best, at) > r else None
    if v["leaves_area"] == "box":
        b = list(v["leaves_box"])
        if len(b) < 6:
            return None
        lo = [min(b[i], b[i + 3]) for i in range(3)]
        hi = [max(b[i], b[i + 3]) + 1 for i in range(3)]
        if at.dim == home.dim and all(lo[i] <= at[i] < hi[i] for i in range(3)):
            return None
        return last or world.Pos((lo[0] + hi[0]) / 2.0, lo[1], (lo[2] + hi[2]) / 2.0, home.dim)
    c = list(v["leaves_center"])
    centre = world.Pos(c[0], c[1], c[2], home.dim) if len(c) >= 3 else home
    return None if at.dim == centre.dim and _leaves_flat(at, centre) <= r else centre
