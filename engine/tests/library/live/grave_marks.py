"""grave_marks, live: Steve's death marks the spot with a skull and a named floating marker; it
fades once its seconds are up. Killed with t.cmd("kill Steve"): not one of t.cmd's flagged "player"
verbs, so it is let through, and a command death is still a death."""


def test(t):
    t.start("grave_marks", wait=2, players="Steve", seconds=3)
    t.cmd("kill Steve")
    t.wait(1)
    t.check("a skull marks where he died", t.block(0, 0, 0, "skeleton_skull"))
    t.check("a named marker stands over it", t.near("armor_stand", 0, 1, 0, radius=3, count=1))
    t.wait(3)
    t.check("the mark fades once its time is up", t.block(0, 0, 0, "air"))
    t.check("no error in the engine's log", t.no_errors())
