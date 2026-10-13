"""enrage, live: granted directly to a spawned body (frost_servant, borrowed only as a plain
actor to grant it to), it buffs speed and strength once health falls under half of what it was
when granted, with a roar."""


def test(t):
    body = t.spawn("frost_servant", 0, 0, 8)      # health 20: a plain body to grant enrage to
    t.grant("enrage", who=t.mobs[body])
    t.wait(1.5)
    t.cmd("damage @e[type=minecraft:stray,distance=..40] 15 minecraft:magic")   # 5 left: under half
    t.wait(2)
    t.check("it has speed once under half health", t.effect("minecraft:speed", who=body))
    t.check("it has strength too", t.effect("minecraft:strength", who=body))
    t.check("no error in the engine's log", t.no_errors())
