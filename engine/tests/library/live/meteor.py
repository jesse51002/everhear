"""meteor, live: one flaming meteor falls on open ground near Steve and blasts a crater there;
no blast lands within 7.5 of him, so he is unhurt."""


def test(t):
    t.ground(0, 0, size=25, depth=10)
    t.start("meteor", wait=10, players="Steve", radius=6, size=8, power=4, breaks=True)
    t.check("it says a meteor fell on Steve", t.said("meteor fell on Steve"))
    t.check("it blasted a crater", t.gone(t.region(0, 0, 0, size=20), share=0.01))
    t.check("no blast lands close enough to hurt him", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
