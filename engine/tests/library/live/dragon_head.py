"""dragon_head, live: spawned directly, it is an ender dragon, and its granted enrage buffs it
once its health is cut below half.

(It is hurt by its uuid, t.mobs[head]: a selector's distance is measured from where RCON runs,
not from the stage, so a type selector there finds nothing. enrage takes its
undamaged health as a baseline on the first tick it can read it -- a freshly spawned body has no
snapshot yet in the very tick its abilities are granted, see enrage.py's own NEEDS -- so this
waits for that tick before hurting it, or the baseline would be the already-hurt health and it
would never look "under half" of it.)"""

from vfg_engine.livetest import verdict


def speed(t, who):
    said = t.cmd("attribute %s minecraft:movement_speed get" % t.mobs[who])
    return float(said.rsplit(" ", 1)[-1])


def faster(t, who, base):
    now = speed(t, who)
    return verdict(now > base * 1.2, "its speed %g -> %g" % (base, now))


def test(t):
    head = t.spawn("dragon_head", 0, 10, 10)
    t.check("it is an ender dragon", t.near("ender_dragon", 0, 10, 10, radius=3, count=1))
    base = speed(t, head)
    t.wait(2)
    # a dragon ignores generic damage and quarters other damage to its body (vanilla: 600 -> 511.5 for
    # 350 explosion): 1300 explosion leaves about 274 of its 600, under half
    t.cmd("damage %s 1300 minecraft:explosion" % t.mobs[head])
    t.wait(2)
    t.check("it enrages (faster: a dragon takes no potion) once under half health", faster(t, head, base))
    t.check("no error in the engine's log", t.no_errors())
