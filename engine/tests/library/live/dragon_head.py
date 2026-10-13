"""dragon_head, live: spawned directly, it is an ender dragon, and its granted enrage buffs it
once its health is cut below half."""


def test(t):
    head = t.spawn("dragon_head", 0, 10, 10)
    t.check("it is an ender dragon", t.near("ender_dragon", 0, 10, 10, radius=3, count=1))
    t.cmd("damage @e[type=minecraft:ender_dragon,distance=..40] 350 minecraft:generic")
    t.wait(2)
    t.check("it enrages (speed) once under half health", t.effect("minecraft:speed", who=head))
    t.check("no error in the engine's log", t.no_errors())
