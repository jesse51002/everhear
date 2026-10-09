"""Sniper Rifle: right-click a Super Sniper to fire a long hitscan shot, double damage on the head; sneak while holding it to zoom."""
ASKS = ["give me a sniper rifle with zoom scope", "give me subclass super sniper",
        "give them a sniper rifle to make them fight"]
from kit import values, items, aim, fx, loop, exclude, world
import itertools
import math
import mc
import time

VALUES = {
    "player":        {"type": "player", "default": "@a"},
    "cooldown":      {"type": "number", "min": 0, "max": 60, "default": 0.5, "live": True},
    "range":         {"type": "int", "min": 8, "max": 500, "default": 400, "live": True},
    "hits":          {"type": "enum", "of": ["mobs", "everything"], "default": "mobs", "live": True},
    "zoom":          {"type": "int", "min": 0, "max": 7, "default": 4, "live": True},
    "damage_amount": {"type": "number", "min": 0, "max": 100000, "default": 150, "live": True},
    "damage_type":   {"type": "enum", "of": ["player_attack", "magic", "lightning_bolt", "explosion", "freeze", "on_fire", "sonic_boom", "arrow", "wither", "generic"], "default": "player_attack", "live": True},
    "damage_kill":   {"type": "bool", "default": False, "live": True},
    "damage_most":   {"type": "int", "min": 0, "max": 1000, "default": 0, "live": True},
    "damage_head":   {"type": "number", "min": 1, "max": 10, "default": 2, "live": True},
    "damage_spare":  {"type": "list", "of": "player", "default": [], "live": True},
    "sneak_mode":    {"type": "enum", "of": ["while", "tap", "three_taps"], "default": "while", "live": True},
    "sneak_seconds": {"type": "number", "min": 0.3, "max": 5, "default": 1.5, "live": True},
    "sneak_item":    {"type": "str", "default": "Super Sniper", "live": True},
}


# --- preset: damage_hits (copied from _presets/damage_hits.py) ---
def damage_hits(hits, shooter, v):
    """Hurt every hit once (hits: a list of aim.Hit, from aim.near_line, around or lock_on,
    or several of those added together), by damage_amount of damage_type, as the shooter, so
    a kill is theirs; damage_kill kills instead. damage_most: only the nearest that many (1:
    the first only; 0: all). shooter: a player's name, or their aim.look(), with which a hit
    whose head the look passes takes damage_head times the damage. Never the shooter, a
    name in damage_spare, a pet, a villager or anything else exclude spares. Returns how many."""
    by = getattr(shooter, "player", shooter) or ""
    spare = [by] + list(v["damage_spare"])
    names = set(mc.bare(s) for s in spare if s and not s.startswith("@"))
    hits = [h for h in sorted(hits, key=lambda h: h.t) if mc.bare(h.name) not in names]
    if v["damage_most"]:
        hits = hits[:v["damage_most"]]
    body = "%s_hit%d" % (mc.TAG, next(_DAMAGE_N))      # each call tags with its own name
    count = {}
    for h in hits:
        tag = body + "h" if v["damage_head"] > 1 and _damage_on_head(h, shooter) else body
        count[tag] = count.get(tag, 0) + _damage_mark(h, tag, spare)
    for tag, n in count.items():
        if not n:
            continue
        if v["damage_kill"]:
            _damage_all(tag, 1000000, "generic_kill", by)
        else:
            _damage_all(tag, v["damage_amount"] * (1 if tag == body else v["damage_head"]), v["damage_type"], by)
        mc.rcon("tag @e[tag=%s] remove %s" % (tag, tag))
    return sum(count.values())


_DAMAGE_N = itertools.count(1)


def _damage_mark(h, tag, spare):
    """Tag one hit where it was: a player by name, a mob as the nearest within 1.5 blocks that
    a hit may touch, so a hit listed twice is tagged once. 1 if it was tagged now, else 0."""
    if h.player:
        reply = mc.rcon("tag %s add %s" % (h.name, tag))
    else:
        x, y, z = h.pos[:3]
        reply = mc.rcon(world.prefix(world.dim(h.pos)) + "positioned %.3f %.3f %.3f %s run tag @s add %s" % (
            x, y, z, exclude.mobs("distance=..1.5,sort=nearest,limit=1", spare), tag))
    return 1 if reply.startswith("Added tag") else 0


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
    down, taps, scored = {}, {}, set()  # per player: crouched at the last look; when their taps came
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
        if not scored.issuperset(people):
            # Everyone gets a score (adding 0 changes none): a player with none yet makes
            # items.Stat misread the name after theirs in the same reply.
            mc.rcon("scoreboard players add @a %s 0" % stat.objective)
            scored.update(people)
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
rifle = v["sneak_item"]         # the rifle, and what sneak() zooms with
zoomed = {}                     # player: when they last crouched with it


def zoom():
    """Slowness narrows the view: a scope, while they crouch with the rifle in hand."""
    for p in sneak(v["player"], v):
        zoomed[p] = time.time()
        if v["zoom"]:
            mc.rcon("effect give %s slowness 1 %d true" % (p, v["zoom"] - 1))


def unzoom():
    """The scope comes off as soon as they stand up or put the rifle away."""
    for _ in loop(every=0.1):
        for p in [p for p, t in list(zoomed.items()) if time.time() - t > 0.3]:
            zoomed.pop(p, None)
            mc.rcon("effect clear %s slowness" % p)


mc.on_stop(lambda: [mc.rcon("effect clear %s slowness" % p) for p in list(zoomed)])
loop.background(zoom)
loop.background(unzoom)
for p in loop(items.clicks(v["player"], rifle, cooldown=v["cooldown"])):
    lk = aim.look(p, aim.SNEAK_EYES if p in zoomed else aim.EYES)      # a zoomed shot is fired crouching
    if lk is None:
        continue
    r = aim.ray(lk, v["range"])
    hits = aim.near_line(r, 0.8, shooter=p, with_players=v["hits"] == "everything")
    fx.sound("entity.firework_rocket.blast_far", p, volume=4, pitch=0.7)
    fx.sound("item.trident.thunder", p, volume=3, pitch=1.6)
    heads = [h for h in hits if _damage_on_head(h, lk)] if v["damage_head"] > 1 else []
    damage_hits(hits, lk, v)
    for h in heads:
        fx.particle("crit", h.pos, spread=0.3, count=40, speed=0.5, up=1.7)
    if heads:
        fx.actionbar(p, "HEADSHOT", "red")
    start = r.start.moved(0, -0.15, 0)
    fx.line(start, r.end, "end_rod", every=8, spread=0.1, count=6, speed=0.02)
    fx.line(start, r.end, "electric_spark", every=16, spread=0.1, count=4, speed=0.1)
