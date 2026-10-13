"""teleport, live: Steve to given coordinates, landing on safe ground; then back to a saved place."""


def test(t):
    t.start("save_place", wait=5, name="test_spot", player="Steve")
    t.check("the place is saved", t.said("saved test_spot"))

    t.start("teleport", wait=5, players="Steve", to="coords", target=t.at(20, 0, 20))
    t.watch(1)
    t.check("Steve moved there", t.moved(15))
    t.check("he landed safely", t.unhurt())

    t.start("teleport", wait=5, players="Steve", to="place", target="test_spot")
    t.watch(1)
    t.check("Steve is back at the saved place", t.moved(15))
    t.check("no error in the engine's log", t.no_errors())
