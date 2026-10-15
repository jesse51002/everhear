"""enrage, live: granted directly to a spawned body (frost_servant, borrowed only as a plain
actor to grant it to), it buffs speed and strength once health falls under half of what it was
when granted, with a roar.

(It is hurt by its uuid, t.mobs[body]: a selector's distance is measured from where RCON runs,
not from the stage, so a type selector there finds nothing.)"""


def test(t):
    body = t.spawn("frost_servant", 0, 0, 8)      # health 20: a plain body to grant enrage to
    t.grant("enrage", who=t.mobs[body])
    t.wait(2.5)      # its own baseline tick (enrage.py: "a freshly spawned actor's body has no
    # snapshot yet in the tick its abilities are granted") needs to land, undamaged, before this
    t.cmd("damage %s 15 minecraft:magic" % t.mobs[body])     # 5 left: under half
    t.wait(2)
    t.check("it has speed once under half health", t.effect("minecraft:speed", who=body))
    t.check("it has strength too", t.effect("minecraft:strength", who=body))
    t.check("no error in the engine's log", t.no_errors())
