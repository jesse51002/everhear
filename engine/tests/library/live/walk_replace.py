"""walk_replace, live: chunk_changer's shape first (the whole chunk under Steve changes at once,
no walking needed, so it has to run before he leaves it); then ice_walk's (ice_path is the same
with a narrower radius, already covered by the unit test): he walks north and the ground along
his path turns to ice, both right where he started and further along once he is moving."""


def test(t):
    t.start("walk_replace", wait=2, players="Steve", per="chunk", rules="barrier > gold_block",
           around_chunks=0, below=1, above=0)
    t.check("it says so", t.said("chunk changer on for Steve"))
    t.watch(1.5)
    t.check("his whole starting chunk's floor changed, with no walking", t.block(0, -1, 0, "gold_block"))
    t.stop("walk_replace")

    t.start("walk_replace", wait=2, players="Steve", rules="gold_block > ice", radius=1, below=1, above=0,
           every_s=2)
    t.check("it says so", t.said("ice walk on for Steve"))
    t.watch(0.5)                                      # its first tick (every=0.1), and the queue, need a beat
    t.check("the gold under him turned to ice the moment it started", t.block(0, -1, 0, "ice"))
    t.cmd("player Steve move forward")
    t.watch(3.5)
    t.cmd("player Steve move")
    t.check("he walked", t.moved(6, way="forward"))
    t.check("ice a few steps further along, where he stepped early in the walk (still gold ground there)",
           t.block(0, -1, 4, "ice"))
    t.check("no error in the engine's log", t.no_errors())
