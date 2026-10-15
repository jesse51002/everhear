"""meteor, live: one flaming meteor falls on open ground near Steve and blasts a crater there;
no blast lands within 7.5 of him (meteor's keep), so he is caught at most at a blast's edge, as the
old meteor's players were (power 4 reaches 8: a few points on hard)."""

from vfg_engine.livetest import verdict


def test(t):
    t.ground(0, 0, size=25, depth=10)
    t.start("meteor", wait=10, players="Steve", radius=6, size=8, power=4, breaks=True)
    t.check("it says a meteor fell on Steve", t.said("meteor fell on Steve"))
    t.check("it blasted a crater", t.gone(t.region(0, 0, 0, size=20), share=0.01))
    hurt = t.hurt("Steve", at_least=6)
    t.check("no blast lands near him: at most its edge", verdict(not hurt[0], hurt[1]))
    t.check("no error in the engine's log", t.no_errors())
