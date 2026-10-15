"""nuke, live: it goes off at once (no countdown), kills a husk and carves the ground, Steve 10
ahead of it unhurt; then made/main's destroy_everything (fire_s above 0), at Steve himself, spares
and protects him with a pad while a second husk beside him dies.

nuke reports once at once ("going off now", the countdown-less case here) and again, later, once
the crater (and, with fire_s, the pad) is placed: a start with wait_s answers with the first and
"running" (the engine's documented early-result rule: op_start / Engine.wait_for in vfg_engine,
same as horde's "its first wave"). t.wait(...) then t.running(...) proves each one ran to its end;
what it did is checked on the world itself, not read back from a second result()."""

from vfg_engine.livetest import verdict


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=40)
    t.ground(0, 10, size=20, depth=10)
    t.start("nuke", wait=15, players="Steve", countdown_s=0, radius=8, kill=True, at=t.at(0, 0, 10))
    t.check("it says it is going off", t.said("going off now"))
    t.wait(2)
    running = t.running("nuke")
    t.check("it ends once the crater is carved", verdict(not running[0], running[1]))
    t.check("the husk died", t.killed(husk))
    t.check("it carved a crater there", t.gone(t.region(0, 0, 10, size=16), share=0.01))
    t.check("Steve, 10 away, is unhurt", t.unhurt())

    husk2 = t.mob("husk", 4, 0, 4, health=40)
    t.ground(0, 0, size=20, depth=10)
    t.start("nuke", wait=15, players="Steve", countdown_s=0, radius=8, fire_s=2, leave="fire")
    t.check("it says it is going off", t.said("going off now"))
    t.wait(6)
    running = t.running("nuke")
    t.check("it ends once the pad is back", verdict(not running[0], running[1]))
    t.check("the husk beside him died", t.killed(husk2))
    t.check("Steve, protected, was spared", t.unhurt())
    t.check("a pad is put back under him", t.block(0, -1, 0, "obsidian"))
    t.check("no error in the engine's log", t.no_errors())
