"""castle, live: an 11-wide castle in front of Steve, its gate facing him, a sign over it naming
him, saved as a place; then KrownJM's mansion (plan mansion) at a spot behind him, facing south,
its door in its front wall.

No t.no_errors() or t.unhurt() here: after a build that ended within its wait they say CAN'T TELL
(livetest counts neither a scenario that ran and ended, nor blocks placed, as something that fired).
The said() checks prove it ran to its end: a build that failed has no "built" result."""


def test(t):
    t.start("castle", wait=30, players="Steve", size=11, sign=["{player}'s Castle"], name="test castle")
    t.check("it says it built one and saved it", t.said("built a castle, 11 wide, of stone_bricks: Steve's at"))
    t.check("its place is saved", t.said("saved as test_castle"))
    t.check("its right wall stands", t.block(5, 2, 11, "stone_bricks"))
    t.check("the gate is open", t.block(0, 1, 6, "air"))
    t.check("bars across the gate's top", t.block(0, 3, 6, "iron_bars"))
    t.check("a sign over the gate", t.block(0, 4, 5, "oak_wall_sign"))
    t.check("a tower on the front right corner", t.block(7, 2, 6, "cobblestone"))

    t.start("castle", wait=30, plan="mansion", at=t.at(0, 0, -8))
    t.check("it says it built the mansion", t.said("built a mansion, 31 by 23, two floors"))
    t.check("its door, in the front wall", t.block(0, 1, -8, "oak_door"))
    t.check("a front window", t.block(9, 2, -8, "glass_pane"))
    t.check("its roof", t.built(t.region(0, 13, -20, size=3, behind=True)))
