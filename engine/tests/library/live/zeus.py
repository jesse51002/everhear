"""zeus, live: granted (no staff), Steve's own thrown trident calls lightning down where it
lands; Steve is not hurt (it never lands within 4 blocks of him)."""


def test(t):
    t.grant("zeus")
    t.cmd("give Steve trident")
    t.check("Steve holds a trident", t.holds("trident"))
    t.wait(1)
    t.use(draw=1.2)                                   # thrown ahead
    t.watch(4)
    t.check("lightning struck where it landed", t.near("lightning_bolt", 0, 0, 15, radius=30, count=1))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
