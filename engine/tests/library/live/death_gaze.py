"""death_gaze, live: granted, crouching kills a husk 10 blocks ahead. (The wall case — the beam
stops at the first wall, per death_gaze/main.py's own "up to the first wall" and inv_G2's verdict
— is the unit test's test_the_wall_stops_the_beam_but_a_mob_before_it_dies.)"""


def test(t):
    husk = t.mob("husk", 0, 0, 10, health=40)
    t.grant("death_gaze")
    t.sneak(times=3)          # "held": each crouch's 0.15 s window gives on_sneaking's 0.1 s check a chance to land
    t.watch(2)
    t.check("the husk is killed", t.killed(husk))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in the engine's log", t.no_errors())
