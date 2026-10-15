"""enrage, live: granted directly to a spawned body (frost_servant, borrowed only as a plain
actor to grant it to), it buffs speed and strength once health falls under half of what it was
when granted, with a roar.

(It is hurt by its uuid, t.mobs[body]: a selector's distance is measured from where RCON runs,
not from the stage, so a type selector there finds nothing.)"""

from vfg_engine.livetest import verdict


def speed(t, who):
    said = t.cmd("attribute %s minecraft:movement_speed get" % t.mobs[who])
    return float(said.rsplit(" ", 1)[-1])


def faster(t, who, base):
    now = speed(t, who)
    return verdict(now > base * 1.2, "its speed %g -> %g" % (base, now))


def test(t):
    body = t.spawn("frost_servant", 0, 0, 8)      # health 20: a plain body to grant enrage to
    t.grant("enrage", who=t.mobs[body])
    base = speed(t, body)
    t.wait(2.5)      # its own baseline tick (enrage.py: "a freshly spawned actor's body has no
    # snapshot yet in the tick its abilities are granted") needs to land, undamaged, before this
    t.cmd("damage %s 15 minecraft:magic" % t.mobs[body])     # 5 left: under half
    t.wait(2)
    t.check("it is faster once under half health", faster(t, body, base))
    t.check("and has strength", t.effect("minecraft:strength", who=body))
    t.check("no error in the engine's log", t.no_errors())
