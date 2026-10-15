"""dragon_head, live: spawned directly, it is an ender dragon, and its granted enrage buffs it
once its health is cut below half.

(It is hurt by its uuid, t.mobs[head]: a selector's distance is measured from where RCON runs,
not from the stage, so a type selector there finds nothing. enrage takes its
undamaged health as a baseline on the first tick it can read it -- a freshly spawned body has no
snapshot yet in the very tick its abilities are granted, see enrage.py's own NEEDS -- so this
waits for that tick before hurting it, or the baseline would be the already-hurt health and it
would never look "under half" of it.)"""


def test(t):
    head = t.spawn("dragon_head", 0, 10, 10)
    t.check("it is an ender dragon", t.near("ender_dragon", 0, 10, 10, radius=3, count=1))
    t.wait(2)
    t.cmd("damage %s 350 minecraft:generic" % t.mobs[head])
    t.wait(2)
    t.check("it enrages (speed) once under half health", t.effect("minecraft:speed", who=head))
    t.check("no error in the engine's log", t.no_errors())
