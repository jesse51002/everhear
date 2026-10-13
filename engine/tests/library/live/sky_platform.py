"""sky_platform, live: an obsidian platform high over Steve, a rim and glowstone corners, he is put
on it facing as he was, his spawn set there."""


def test(t):
    t.start("sky_platform", wait=15, players="Steve", y=310, size=5, blocks="obsidian")
    t.check("it says he's put on it, spawn set", t.said("put on a sky platform at"))
    t.check("the floor is obsidian", t.block(0, 14, 0, "obsidian"))
    t.check("a rim on the edge", t.block(2, 15, 0, "obsidian"))
    t.check("glowstone on a corner", t.block(2, 15, 2, "glowstone"))
    t.check("no error in the engine's log", t.no_errors())
