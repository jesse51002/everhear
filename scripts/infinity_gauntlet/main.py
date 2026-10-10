"""Infinity Gauntlet: right-click it and the first thing you point at is gone, in a beam of flame, with no cooldown."""
ASKS = ["give me the power of infinity", "an infinity gauntlet that kills whatever I point at",
        "it does not do enough damage", "no cooldown"]
from kit import values, result, items, aim, fx, loop, exclude, world
import itertools
import math
import mc

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


v = values()
result("%s has the %s: right-click to %s the first thing they point at" % (
    v["player"], v["item"], "kill" if v["damage_kill"] else "hurt"), player=v["player"], item=v["item"])
for p in loop(items.clicks(v["player"], v["item"], cooldown=v["cooldown"], color="gold")):
    r = aim.ray(p, v["range"])
    if r is None:
        continue
    hits = aim.near_line(r, 1.5, shooter=p, with_players=v["damage_what"] in ("all", "players"))
    fx.sound("entity.blaze.shoot", p, volume=3, pitch=0.6)
    end = hits[0].pos.moved(0, 1, 0) if hits else r.end
    fx.line(r.start.moved(0, -0.2, 0), end, "flame", every=3, spread=0.5, count=20, speed=0.02)
    if damage_hits(hits, p, v):
        fx.particle("explosion_emitter", end)
        fx.particle("flame", end, spread=1.5, count=150, speed=0.1)
        fx.sound("entity.lightning_bolt.thunder", end, volume=2)
