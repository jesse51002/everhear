"""Death Gaze: while a player sneaks, every mob they look at dies, up to the first wall, with a beam from their eyes."""
ASKS = ["give me the power to kill everything I stare at", "also make my death gaze work on players",
        "my death stare should one shot everything at infinite range",
        "make the death beam faster, this is way too slow"]
from kit import values, items, aim, fx, loop, exclude, world
import itertools
import math
import mc
import time

VALUES = {
    "player":        {"type": "player", "default": "@a"},
    "range":         {"type": "int", "min": 4, "max": 300, "default": 60, "live": True},
    "damage_amount": {"type": "number", "min": 0, "max": 100000, "default": 20, "live": True},
    "damage_type":   {"type": "enum", "of": ["player_attack", "magic", "lightning_bolt", "explosion", "freeze", "on_fire", "sonic_boom", "arrow", "wither", "generic"], "default": "magic", "live": True},
    "damage_kill":   {"type": "bool", "default": True, "live": True},
    "damage_what":   {"type": "enum", "of": ["all", "mobs", "hostile", "players"], "default": "mobs", "live": True},
    "damage_most":   {"type": "int", "min": 0, "max": 1000, "default": 0, "live": True},
    "damage_head":   {"type": "number", "min": 1, "max": 10, "default": 1, "live": True},
    "damage_spare":  {"type": "list", "of": "player", "default": [], "live": True},
    "sneak_mode":    {"type": "enum", "of": ["while", "tap", "three_taps"], "default": "while", "live": True},
    "sneak_seconds": {"type": "number", "min": 0.3, "max": 5, "default": 1.5, "live": True},
    "sneak_item":    {"type": "str", "default": "", "live": True},
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


# --- preset: sneak (copied from _presets/sneak.py) ---
def sneak(player, v):
    """Yield a player's name (of those `player` names: "@a", "@r" or names) when they sneak:
    "while" on every pass, about ten a second, as long as they crouch; "tap" once each time
    they crouch; "three_taps" when they crouch three times within sneak_seconds. With
    sneak_item set (an item's name, as items.clicks gives it) only while they hold it.
    Only crouching counts, never a click. Each player is followed on their own.

        for p in loop(sneak(v["player"], v)):
            ...
    """
    stat = items.Stat(*items.SNEAK)     # this generator's own look at the shared sneak_time objective
    down, taps = {}, {}                 # per player: crouched at the last look; when their taps came
    while not mc.stopping():
        people = loop.who(player)
        if not people:
            stat.forget()
            down.clear()
            taps.clear()
            time.sleep(1.0)
            continue
        name = v["sneak_item"]
        hold = "if items entity @s weapon.* %s" % items.match(name) if name else ""
        got, now, mode, due = stat.changes(hold), time.time(), v["sneak_mode"], []
        for p in people:
            crouched, was = got.get(p, 0) > 0, down.get(p, False)
            down[p] = crouched
            if mode == "while":
                if crouched:
                    due.append(p)
            elif crouched and not was:                          # a tap: they went down since the last look
                if mode == "tap":
                    due.append(p)
                    continue
                recent = [t for t in taps.get(p, []) if now - t <= v["sneak_seconds"]] + [now]
                taps[p] = [] if len(recent) >= 3 else recent
                if len(recent) >= 3:
                    due.append(p)
        for p in due:
            yield p
        if due:
            stat.changes(hold)          # crouching while the loop's body ran does not count
        time.sleep(0.1)


v = values(VALUES)
for p in loop(sneak(v["player"], v)):
    lk = aim.look(p, aim.SNEAK_EYES)
    if lk is None:
        continue
    r = aim.ray(lk, v["range"])
    damage_hits(aim.near_line(r, 1.3, shooter=p, with_players=v["damage_what"] in ("all", "players")), p, v)
    fx.line(r.start.moved(0, -0.2, 0), r.end, "end_rod", every=8, spread=0.15, count=6)
