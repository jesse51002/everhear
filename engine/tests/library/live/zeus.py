"""zeus, live: granted (no staff), Steve's own thrown trident calls lightning down where it lands,
hurting a husk waiting about there; Steve is not hurt (it never lands within 4 blocks of him).

A directly-summoned bolt lives only a tick or two (not weather's own, longer-lived one), too short
for watch-then-poll to catch with t.near: its damage is the evidence, as lightning_staff and
lightning_strike look for theirs, instead of the bolt itself."""


def test(t):
    husk = t.mob("husk", 0, 0, 21, health=200)        # about where the thrown trident comes down
    t.grant("zeus")
    t.cmd("give Steve trident")
    t.check("Steve holds a trident", t.holds("trident"))
    t.wait(1)
    t.use(draw=1.2)                                   # thrown ahead
    t.watch(3)
    t.check("the husk near where it landed is hurt", t.hurt(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
