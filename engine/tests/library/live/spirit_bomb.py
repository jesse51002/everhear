"""spirit_bomb, live: holding right-click for just over a second arms it; it then charges for 5 s
on its own and an orb flies where Steve looks -- up to its 100-block reach, stopped sooner only by
a block in the way -- bursting on a wall 20 ahead and hurting a husk beside it. Letting go right
away (under a second) never arms it at all."""


def test(t):
    t.give("spirit_bomb")
    t.check("Steve holds spirit bomb", t.holds("spirit_bomb"))
    t.use(hold=0.3)                                  # let go well before arm_s: nothing should start
    t.wait(1)
    t.check("the bomb never armed", not t.instances(kind="ability", who="Steve"))

    t.blocks("stone", 0, 0, 20)                      # a wall 20 ahead: where the orb lands (reach is 100)
    husk = t.mob("husk", 2, 0, 20, health=1000)       # by the landing point, beside the wall
    t.use(hold=1.5)                                  # armed; it charges and fires on its own from here
    t.watch(8)                                       # 5 s charge, under a second to fly, then the burst
    t.check("the burst hurts the husk", t.hurt(husk, at_least=100))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
