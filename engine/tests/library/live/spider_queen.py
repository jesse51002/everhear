"""spider_queen, live: spawned directly, she is a giant glowing spider (seen through walls)."""


def test(t):
    t.spawn("spider_queen", 0, 0, 10)
    t.check("she is a giant spider", t.near("spider", 0, 0, 10, radius=3, count=1))
    t.check("no error in the engine's log", t.no_errors())
