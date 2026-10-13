"""Tests: an ability tried for real on the test server, high in the sky, and checked item by item.

A test is a script, made/<server>/tests/<task>.py (a library script's: scripts/<name>/test.py), run
by `vfg test <task>` on the test server (a second Minecraft server with Carpet's fake players, never
the live one) after it gets the task's scripts and incantations. One line per check, PASS, FAIL or
CAN'T TELL with the numbers that decided it; exit 0 when none failed. Start from the template of
its kind (kit/templates/<kind>.py; vfg test <task> --kind <kind> writes it), one check per DONE WHEN
item at its TODO; edit anything else in it as the ability needs:
  cast-projectile  said, it flies where he looks and hits      item-use     a click, hold, charge or bow
  cast-launch      said, it moves him (a dash, a jump, flight)  area-blast   a blast, a crater, fire round a place
  build            something built in front of him              world-event  mobs, weather, a rain of things

    from kit import test

    t = test.Test("Steve")            # Steve faces north; Alex stands 4 to his left
    ground = t.ground(0, 10, size=9)  # real ground: grass on dirt, short grass to burn, 10 ahead
    husk = t.mob("husk", 0, 0, 10)    # 10 ahead, on his eye line: (right, up, forward)
    wall = t.blocks("stone", 0, 0, 12, size=3)    # 3 x 3 x 3, 2 behind the husk
    t.cast("hot fireball")            # his incantation, started as the bridge starts a heard one
    t.watch(4)                        # positions, motion, health, effects and fire, 4 times a second
    t.check("it hits the husk", t.hurt(husk))
    t.check("it blows up the wall", t.gone(wall, 0.05))
    t.check("Steve is not hurt", t.unhurt())
    t.check("no error in its log", t.no_errors())
    t.done()                          # the report; everything cleared; exit 0, or 1 on a FAIL

WHERE: (right, up, forward) from the caster's feet. Forward = where he faces = north = -z, never
+z; right = east = +x, left = -x; up = +y. A target in front has forward > 0; one behind (forward
< 0) or inside a placed cube is an error. Each thing placed prints an AT line, with a cube's extent
("stone cube 3x3x3: 11 to 13 ahead, 1 left to 1 right, 0 to 2 up"): read them.
START: t.cast(phrase, who=caster): the real trigger of an incantation: it fires at once, with nothing
pressed after (the script sees mc.cast()), and prints its values: what the binding asked for, the
rest the action's as deployed now (a heard cast takes the same); t.bind(action, phrase,
**values) binds one on the test server (a library test; a task's come from the live server).
t.run(action, **values): the action as vfg action run starts it, other values its defaults. An item:
t.run, then t.use() a click, t.use(hold=3) held, t.use(draw=1.2) a bow; t.sneak(3) crouches;
t.look(pitch, turn). Each returns at once: t.watch(seconds) after it samples positions, motion,
health, effects and fire 4 times a second, which hurt, moved and burning read. t.wait(s);
t.cmd(command) only sets the stage: it never changes a player, and stops the test when Minecraft
refuses it. t.at(r, u, f) is a spot as "x y z" for a value.
PLACING: Test(player="Steve", others=("Alex",)); t.mob(kind, r, u, f, health=None, still=True: no
AI; False: pushed by knockback; behind=False); t.blocks(block, r, u, f, size=3) a cube, its bottom
at u; t.ground(r=0, f=10, size=9, depth=3); t.region(r, u, f, size) a cube left as it is;
t.player(name, r, u, f); t.stand_in(), a villager beside him that knockback pushes.
CHECKS, verdicts for t.check(item, verdict); moved, said and result count from the last cast, run,
use or crouch; a FAIL says where things ended up:
  t.hurt(who, at_least=1)  t.killed(who)  t.unhurt(who=caster)  t.others_unhurt()  t.burning(who)
  t.moved(at_least, way="any"|"up"|"forward", who=caster)   t.near("zombie", r, u, f, radius=8, count=1)
  t.gone(cube, share)  t.turned(cube, "ice" or ["ice", "packed_ice"], share)  t.block(r, u, f, "ice")
  t.built(cube, share)  t.fire_near(r, u, f, radius=4, count=1)  t.effect("speed", who)
  t.holds("Railgun", who)  t.weather("rain"|"thunder"|"clear")  t.said("went off")  t.no_errors()
  test.verdict(ok, why): ok True, False or None. A bare True/False works too; a FAIL is false, so
  `if v` and `not v` read right. t.result() is the copy's latest {"summary", "facts"} ({} if none);
  t.log(n) prints its last n log lines.
THE STAGE is not the live world: a barrier floor that never breaks (t.ground puts real ground in
it), fake players (Steve and Alex, launched by blasts as people are), chunks forceloaded, night,
clear weather, no mobs spawning, a fresh world. When nothing fired after the cast, the unhurt
checks and no_errors say so (CAN'T TELL). On a FAIL, decide which is wrong, the ability or the
test, and fix that one; the report ends with each copy's last log lines (vfg test -n N for more).
"""
# The helpers themselves are in kit/_stage/helpers.py: this guide is their API.
from ._stage.helpers import (CLEAR, FLOOR, HALF, TEMPLATES, X, Z, Test, TestError, Verdict,  # noqa: F401
                             kinds, verdict)
