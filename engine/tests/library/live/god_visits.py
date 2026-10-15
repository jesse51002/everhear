"""god_visits, live: a god visits Steve almost at once, gives him a gift, then leaves again."""


def test(t):
    t.start("god_visits", wait=3, players="Steve", min_s=10, max_s=10, each=False, gift_weight=1, take_weight=0,
            gifts=["diamond 4"], stay_s=3)
    t.wait(11)
    t.check("the god stands in front of Steve", t.near("mannequin", 0, 1, 6, radius=5, count=1))
    t.check("Steve is given the gift", t.holds("diamond"))
    t.check("no error in the engine's log", t.no_errors())
