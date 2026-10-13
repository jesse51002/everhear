"""anti_camp, live: Steve stands still (he is a fake player who never moves unless pressed);
past the camping window TNT falls near him and hurts him, and the Demon Lord mocks him in chat
(unobservable through T, so not checked here - the unit test covers the mock line's wording)."""


def test(t):
    t.start("anti_camp", wait=2, players="Steve", seconds=6, radius=3, tnt=3, height=6, fuse=2, mock=True,
           warning="MOVE")
    t.check("it says what it watches", t.said("3 TNT fall on anyone of Steve who stays within 3 blocks for 6 s"))
    t.watch(7)
    t.check("TNT falls near him before it blows", t.near("tnt", 0, 0, 0, radius=6, count=1, within=2))
    t.watch(4)                                       # the fall (height 6) plus its 2 s fuse
    t.check("the TNT hurts him", t.hurt("Steve", at_least=1))
    t.check("no error in the engine's log", t.no_errors())
