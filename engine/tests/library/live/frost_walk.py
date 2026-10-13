"""frost_walk, live: applied to Steve, a slab round his feet turns to ice at once (no stepping
needed, unlike walk_replace); it keeps following as he walks."""


def test(t):
    t.apply("frost_walk", who="Steve", radius=2, below=1, above=0, rules="barrier > ice")
    t.check("Steve has it", t.running("frost_walk", who="Steve"))
    t.watch(0.5)
    t.check("ice appears right where he stands, with no stepping", t.block(0, -1, 0, "ice"))
    t.cmd("player Steve move forward")
    t.watch(3.5)
    t.cmd("player Steve move")
    t.check("he walked", t.moved(6, way="forward"))
    t.watch(0.5)
    t.check("the slab followed him: ice where he passed partway through the walk", t.block(0, -1, 5, "ice"))
    t.check("no error in the engine's log", t.no_errors())
