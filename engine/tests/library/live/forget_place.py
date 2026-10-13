"""forget_place, live: forgets a place save_place just saved; forgetting it again says there is
none."""


def test(t):
    t.start("save_place", wait=5, name="to_forget", player="Steve")
    t.start("forget_place", wait=5, name="to_forget")
    t.check("it says it forgot it", t.said("forgot to_forget"))

    t.start("forget_place", wait=5, name="to_forget")
    t.check("it says there is none now", t.said("there is no saved place called to_forget"))
    t.check("no error in the engine's log", t.no_errors())
