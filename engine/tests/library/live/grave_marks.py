"""grave_marks, live: a kill marks the spot with a skull and a named floating marker; it fades
once its seconds are up. Tested through `on="kill"`, not the default "death": a Carpet fake
player removed by a death is already gone from @a by the very next command (confirmed: `list`
right after `kill Steve`, no wait at all, already shows Steve gone), before the datapack's
per-tick death check (`@a[scores={everhear.deaths=1..}]`) ever gets to run for him - a real
client would stay on its death screen, still in @a, so this is the test stage's fake player, not
grave_marks.py. `on_kill` shares the same mark()/fade() code and needs the player alive at the
moment, which Steve is throughout."""


def test(t):
    weak = t.mob("zombie", 0, 0, 2, health=2)
    t.start("grave_marks", wait=2, players="Steve", on="kill", seconds=5)
    for _ in range(4):
        t.attack()
        t.wait(0.6)
    t.check("the zombie is killed", t.killed(weak))
    t.wait(0.5)
    t.check("a skull marks where he stood", t.block(0, 0, 0, "skeleton_skull"))
    t.check("a named marker stands over it", t.near("armor_stand", 0, 1, 0, radius=3, count=1))
    t.wait(5)
    t.check("the mark fades once its time is up", t.block(0, 0, 0, "air"))
    t.check("no error in the engine's log", t.no_errors())
