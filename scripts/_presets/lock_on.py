"""Lock onto the mob a player points at: the nearest in a narrow cone where they look, up to the first wall, so a staff hits what they aim at."""
import math

from kit import aim

VALUES = {
    "lock_range": {"type": "int", "min": 4, "max": 500, "default": 100, "live": True},
    "lock_cone": {"type": "number", "min": 1, "max": 45, "default": 6, "live": True},
    "lock_players": {"type": "bool", "default": False, "live": True},
}


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
