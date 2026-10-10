"""Two beams fired at each other clash, as in the anime: they meet, the shooters mash right-click to push, the point moves toward the weaker one and explodes there."""
import fcntl
import json
import math
import os
import random
import time

import mc
from kit import fx, items, loop, world
from actions._types import Field, Live, ParticleId, Values  # the values class: read on the PC only


class BeamClash(Values):
    beam_clash_power: Live[float] = Field(10, ge=1, le=1000)
    """how hard this beam pushes in a clash, the point moving toward the weaker; holding or mashing right-click adds to it"""
    beam_clash_gap: Live[float] = Field(4, ge=0.5, le=16)
    """how close two beams must pass to clash, in blocks; a beam passing this near a player is aimed at them"""
    beam_clash_window: Live[float] = Field(1.5, ge=0, le=10)
    """seconds a beam waits for another to meet it before it fires (0: none, though one already waiting still meets it)"""
    beam_clash_aimed: Live[bool] = True
    """only a beam aimed at another player waits beam_clash_window; off: every beam waits"""
    beam_clash_seconds: Live[float] = Field(6, ge=1, le=30)
    """how long the struggle lasts at most, in seconds, before it explodes where the point is"""
    beam_clash_blast: Live[int] = Field(4, ge=0, le=8)
    """the explosion where it ends, as a creeper's blast (a creeper is 3); 0: a show only, nothing hurt or broken"""
    beam_clash_particle: Live[ParticleId] = "dust{color:[0.6,0.1,0.9],scale:2.0}"
    """the particle this shooter's beam is drawn with while the beams clash"""


def beam_clash(shooter, ray, v):
    """Call it as a beam fires (ray: the kit's aim.Ray), before the beam hurts anything.
    None: it met no other beam, so fire it. (won, at): it clashed, the clash is over and has
    exploded at `at` (a Pos); won is True for the shooter who pushed it there.

    Every beam on the box is a record in one shared file in the run directory, so two
    instances of one script (galick-steve, galick-alex) or two scripts clash alike. A beam
    aimed at another player waits up to beam_clash_window s for an answer (every beam does
    with beam_clash_aimed off); a beam that passes within beam_clash_gap blocks of a waiting
    one clashes with it. For up to beam_clash_seconds both beams then run from their
    shooters' eyes to the point where they met, which moves toward the weaker: its
    beam_clash_power, doubled by holding right-click and more by mashing it. When the point
    reaches a shooter, or time is up, it explodes: beam_clash_blast, 0 for a show only."""
    me = "%s/%s" % (mc.INSTANCE, shooter)
    aimed = not v["beam_clash_aimed"] or _beam_clash_aimed(shooter, ray, v)
    wait = v["beam_clash_window"] if aimed else 0.0
    rec = {"shooter": shooter, "eyes": list(ray.start[:3]), "end": list(ray.end[:3]), "dim": ray.dim,
           "particle": str(v["beam_clash_particle"]), "push": float(v["beam_clash_power"]),
           "t": time.time(), "until": time.time() + wait}
    while True:
        last = time.time() >= rec["until"] or mc.stopping()
        role = _beam_clash_db(lambda db: _beam_clash_meet(db, me, rec, v["beam_clash_gap"], last))
        if role or last:
            break
        time.sleep(0.1)
    if not role:
        return None
    if role[0] == "lead":
        return _beam_clash_lead(me, role[1], v)
    return _beam_clash_follow(me, role[1], rec, v)


def _beam_clash_db(fn):
    """fn(beams) while holding the lock on the file every beam script on the box shares,
    which is written back after it; returns what fn returns. Beams a minute old are dropped."""
    path = os.path.join(os.environ.get("VFG_RUN_DIR", "/opt/vfg/run"), "beam_clash.json")
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)              # let go when the file closes
        try:
            with open(path, encoding="utf-8") as f:
                db = json.load(f)
        except (OSError, ValueError):
            db = {}
        db = {k: r for k, r in db.items() if time.time() - r.get("t", 0) < 60}
        out = fn(db)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(db, f)
        os.replace(path + ".tmp", path)
        return out


def _beam_clash_meet(db, me, rec, gap, last):
    """This beam into the shared file; ("lead", theirs) when it claims a waiting beam it
    meets, ("follow", theirs) when one has claimed it, else None (and on the last look it
    is taken out again, so nothing can claim it once it has fired)."""
    mine = db.setdefault(me, dict(rec))
    if mine.get("lead"):
        return "follow", mine["lead"]
    for k, o in db.items():
        if (k == me or o["shooter"] == rec["shooter"] or o["dim"] != rec["dim"] or o.get("lead")
                or o.get("clash") or o["until"] < time.time()):
            continue
        d, c1, c2 = _beam_clash_closest(rec["eyes"], rec["end"], o["eyes"], o["end"])
        if d <= gap:
            o["lead"] = me
            mine.update(until=0, clash={"with": k, "at": [(c1[i] + c2[i]) / 2 for i in range(3)], "over": False})
            return "lead", k
    if last:
        db.pop(me, None)
    return None


def _beam_clash_lead(me, other, v):
    """The struggle, run by the beam that claimed the other: both beams drawn, the point
    moved, and the explosion. (won, at), or None if the other beam is gone."""
    a, b = _beam_clash_db(lambda db: (dict(db.get(me, {})), dict(db.get(other, {}))))
    if "clash" not in a or "eyes" not in b:
        return None
    ea, eb, dim = a["eyes"], b["eyes"], a["dim"]
    line = [eb[i] - ea[i] for i in range(3)]
    far = math.sqrt(sum(x * x for x in line)) or 1.0
    met = a["clash"]["at"]
    # How far along from my eyes (0) to theirs (1) the beams met: halfway when head on.
    u = start = min(0.8, max(0.2, sum((met[i] - ea[i]) * line[i] for i in range(3)) / (far * far)))
    if _beam_clash_cos(a, b) < -0.8:
        u = start = 0.5
    at = world.Pos(ea[0] + line[0] * u, ea[1] + line[1] * u, ea[2] + line[2] * u, dim)
    mc.log("beam clash:", a["shooter"], "vs", b["shooter"], "at %.0f %.0f %.0f" % at[:3])
    fx.sound("entity.warden.sonic_boom", at, volume=6, pitch=0.6)
    clicks, recent, loser = items.Stat(*items.CLICK), [], None
    t0 = tick = time.time()
    while time.time() - t0 < v["beam_clash_seconds"] and not mc.stopping():
        push = _beam_clash_push(clicks, a["shooter"], recent, v)

        def swap(db):
            if me in db:
                db[me]["push"] = push
            return db.get(other, {}).get("push", b["push"])
        theirs = _beam_clash_db(swap)
        dt, tick = time.time() - tick, time.time()
        u += dt * 2.0 / v["beam_clash_seconds"] * (push - theirs) / (push + theirs) + random.uniform(-0.01, 0.01)
        at = world.Pos(ea[0] + line[0] * u, ea[1] + line[1] * u, ea[2] + line[2] * u, dim)
        _beam_clash_draw(a, b, at, u)
        if u * far >= far - 2:
            loser = other
            break
        if u * far <= 2:
            loser = me
            break
        time.sleep(0.15)
    if mc.stopping():
        return None
    if loser is None:
        loser = other if u > start else me if u < start else random.choice([me, other])

    def over(db):
        if me in db:
            db[me]["clash"].update(over=True, loser=loser, at=list(at))
    _beam_clash_db(over)
    _beam_clash_boom(at, v)
    mc.log("beam clash won by", b["shooter"] if loser == me else a["shooter"])
    return loser != me, at


def _beam_clash_follow(me, lead, rec, v):
    """The claimed beam's side: its shooter's push into the file until the clash is over."""
    clicks, recent = items.Stat(*items.CLICK), []
    end = time.time() + v["beam_clash_seconds"] + 5
    while time.time() < end and not mc.stopping():
        push = _beam_clash_push(clicks, rec["shooter"], recent, v)

        def look(db):
            if me in db:
                db[me]["push"] = push
            return dict(db.get(lead, {}).get("clash") or {})
        clash = _beam_clash_db(look)
        if clash.get("over"):
            return clash["loser"] != me, world.Pos(clash["at"][0], clash["at"][1], clash["at"][2], rec["dim"])
        time.sleep(0.15)
    return None


def _beam_clash_push(clicks, shooter, recent, v):
    """beam_clash_power, doubled by holding right-click (about 5 uses a second), more by mashing."""
    now = time.time()
    recent.append((now, clicks.changes("if entity @s[name=%s]" % shooter).get(shooter, 0)))
    recent[:] = [(t, n) for t, n in recent if now - t <= 1.0]
    return v["beam_clash_power"] * (1 + sum(n for _, n in recent) / 5.0)


def _beam_clash_draw(a, b, at, u):
    """Both beams to the point, the point itself, and how it stands for each shooter."""
    for r in (a, b):
        fx.line(world.Pos(r["eyes"][0], r["eyes"][1] - 0.3, r["eyes"][2], at.dim), at, r["particle"],
                every=1.5, spread=0.25, count=3)
    fx.particle("end_rod", at, spread=0.6, count=12, speed=0.15)
    fx.particle("electric_spark", at, spread=0.8, count=10, speed=0.4)
    if random.random() < 0.2:
        fx.sound("entity.warden.sonic_boom", at, volume=3, pitch=random.uniform(0.5, 0.9))
    for r, k in ((a, u), (b, 1 - u)):
        n = max(0, min(10, int(k * 10)))
        fx.actionbar(r["shooter"], "CLASH! right-click to push  " + "■" * n + "□" * (10 - n), "light_purple")


def _beam_clash_boom(at, v):
    fx.particle("flash{color:[1.0,1.0,1.0,1.0]}", at, count=3)
    fx.particle("explosion_emitter", at, spread=2, count=3)
    fx.particle("end_rod", at, spread=4, count=150, speed=0.4)
    fx.sound("entity.generic.explode", at, volume=8, pitch=0.6)
    fx.sound("entity.warden.sonic_boom", at, volume=8, pitch=0.7)
    if v["beam_clash_blast"]:
        mc.rcon(world.run(at.dim, 'summon creeper %.2f %.2f %.2f {Fuse:0s,ignited:1b,ExplosionRadius:%db,'
                                  'Tags:["vfg","%s"]}' % (at[0], at[1], at[2], v["beam_clash_blast"], mc.TAG)))


def _beam_clash_aimed(shooter, ray, v):
    """True if another player stands within beam_clash_gap of the beam."""
    for p in loop.online():
        at = mc.pos(p) if p != shooter and world.dim_of(p) == ray.dim else None
        body = at and (at[0], at[1] + 1, at[2])
        if body and _beam_clash_closest(ray.start, ray.end, body, body)[0] <= v["beam_clash_gap"] + 1:
            return True
    return False


def _beam_clash_cos(a, b):
    """The cosine of the angle between two beams: -1 when they point straight at each other."""
    da, db = ([r["end"][i] - r["eyes"][i] for i in range(3)] for r in (a, b))
    n = math.sqrt(sum(x * x for x in da) * sum(x * x for x in db)) or 1.0
    return sum(x * y for x, y in zip(da, db)) / n


def _beam_clash_closest(p1, q1, p2, q2):
    """How close two segments p1-q1 and p2-q2 come (either may be a point): (distance, the
    point on the first, the point on the second)."""
    def dot(x, y):
        return sum(x[i] * y[i] for i in range(3))

    def clamp(x):
        return min(1.0, max(0.0, x))
    d1, d2, r = [q1[i] - p1[i] for i in range(3)], [q2[i] - p2[i] for i in range(3)], [p1[i] - p2[i] for i in range(3)]
    a, e, f, c, b = dot(d1, d1), dot(d2, d2), dot(d2, r), dot(d1, r), dot(d1, d2)
    if a < 1e-9 and e < 1e-9:
        s = t = 0.0
    elif a < 1e-9:
        s, t = 0.0, clamp(f / e)
    elif e < 1e-9:
        s, t = clamp(-c / a), 0.0
    else:
        den = a * e - b * b
        s = clamp((b * f - c * e) / den) if den > 1e-9 else 0.0
        t = (b * s + f) / e
        if t < 0:
            s, t = clamp(-c / a), 0.0
        elif t > 1:
            s, t = clamp((b - c) / a), 1.0
    c1, c2 = [p1[i] + d1[i] * s for i in range(3)], [p2[i] + d2[i] * t for i in range(3)]
    return math.sqrt(sum((c1[i] - c2[i]) ** 2 for i in range(3))), c1, c2
