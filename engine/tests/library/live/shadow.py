"""shadow, live: the dark armor-stand figure horror sends, spawned directly to check its look."""


def test(t):
    t.spawn("shadow", 0, 0, -10)
    t.check("it stands watching behind Steve", t.near("armor_stand", 0, 0, -10, radius=3, count=1))
    t.check("no error in the engine's log", t.no_errors())
