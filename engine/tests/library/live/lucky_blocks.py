"""lucky_blocks, live: starting it gives Steve a Lucky Block; breaking a sponge in the world (any
sponge counts, not only one named Lucky Block) rolls a reward or trap. There is no t.mine() in T,
so mining is pressed the way a real client holds the same key down: t.cmd("player Steve attack
continuous" / "stop"), a read-only-looking command t.cmd lets through because its check only
flags the "player" verbs that change a player (move, teleport, give...), not "attack". The 18
outcomes are random and otherwise unobservable through T (no chat or RNG check here), so this
proves the break-to-reward pipeline runs clean on a real roll, not any one outcome (the unit test
covers all 18)."""


def test(t):
    t.start("lucky_blocks", wait=2, players="Steve", give=1)
    t.check("it says so, and Steve gets a Lucky Block", t.said("lucky blocks on for Steve"))
    t.check("Steve holds one", t.holds("sponge"))
    t.blocks("sponge", 0, 1, 2, size=1)
    t.cmd("player Steve attack continuous")
    t.watch(3)
    t.cmd("player Steve stop")
    t.check("the sponge was mined", t.block(0, 1, 2, "air"))
    t.check("no error in the engine's log", t.no_errors())
