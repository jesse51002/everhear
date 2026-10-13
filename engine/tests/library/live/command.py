"""command, live: two harmless commands run in order, their replies in the result; a setblock is
refused before it ever reaches Minecraft."""


def test(t):
    t.start("command", wait=5, commands=["time set day", "weather clear"])
    t.check("it ran time set day", t.said("set the time"))
    t.check("it ran weather clear", t.said("changed the weather"))

    t.start("command", wait=5, commands=["setblock %s minecraft:stone" % t.at(0, 0, 5)])
    t.check("the setblock is refused, not sent", t.said("refused"))
    t.check("nothing was placed", t.block(0, 0, 5, "air"))
    t.check("no error in the engine's log", t.no_errors())
