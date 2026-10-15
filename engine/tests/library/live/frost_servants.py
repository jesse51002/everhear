"""frost_servants, live: granted to Steve directly, it calls a frost servant to his side within
its cooldown."""


def test(t):
    t.grant("frost_servants")
    t.wait(9)
    t.check("a frost servant stands near Steve", t.near("stray", 0, 0, 0, radius=6, count=1))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
