"""sniper_nest, live: a tower in front of Steve, its door facing him, a sign over the door naming
him, his spawn set on the lookout."""


def test(t):
    t.start("sniper_nest", wait=20, players="Steve", sign=["{player}'s Nest"])
    t.check("it says it built one, spawn set", t.said("built 1 sniper nest: Steve's at"))
    t.check("spawn set there", t.said("spawn set there"))
    t.check("its right wall stands", t.block(2, 1, 7, "stone_bricks"))
    t.check("its far wall stands", t.block(0, 1, 9, "stone_bricks"))
    t.check("the door is open, facing him", t.block(0, 1, 5, "air"))
    t.check("a ladder up the back wall", t.block(0, 0, 8, "ladder"))
    t.check("the footing, so it never floats", t.block(0, -9, 7, "stone_bricks"))
    t.check("the lookout's walls", t.block(2, 16, 7, "stone_bricks"))
    t.check("the roof", t.block(3, 17, 7, "stone_bricks"))
    t.check("a sign over the door", t.block(0, 2, 4, "oak_wall_sign"))
