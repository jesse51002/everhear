"""world_replace, live: a small obsidian block ahead of Steve cleared to air; then rules
"hellscape" (the old hellscape action) turns the ground round him to the Nether."""


def test(t):
    cube = t.blocks("obsidian", 0, 0, 6, size=3)
    t.start("world_replace", wait=10, players="Steve", rules="obsidian > air", radius=10, height=3)
    t.check("it says what changed", t.said("obsidian > air"))
    t.check("the obsidian is gone", t.gone(cube))

    t.start("world_replace", wait=20, players="Steve", rules="hellscape", radius=12, height=4)
    t.check("it says the land turned to the Nether", t.said("turned to the Nether"))
    t.check("no error in the engine's log", t.no_errors())
