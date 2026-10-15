"""god, live: the giant mannequin god_visits sends, spawned directly to check its look stands up
in Minecraft."""


def test(t):
    t.spawn("god", 0, 1, 6)
    t.check("the god stands glowing in front of Steve", t.near("mannequin", 0, 1, 6, radius=3, count=1))
    t.check("no error in the engine's log", t.no_errors())
