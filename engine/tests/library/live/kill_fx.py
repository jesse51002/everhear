"""kill_fx, live: every sound here is heard only by the player who caused it, and there is no
t.heard() in T to check one, so this proves the scenario runs clean through a real kill and a real
death (which is what it listens for), not that a sound played; the unit test checks the sounds
themselves on FakeIo."""


def test(t):
    weak = t.mob("zombie", 0, 0, 2, health=2)
    t.start("kill_fx", wait=2, players="Steve")
    for _ in range(4):
        t.attack()
        t.wait(0.6)
    t.check("the zombie is killed", t.killed(weak))
    t.cmd("kill Steve")
    t.wait(1)
    t.check("no error in the engine's log", t.no_errors())
