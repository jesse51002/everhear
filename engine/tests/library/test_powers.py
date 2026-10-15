"""The powers group on FakeIo: fireball (ability) flies to the first mob on the caster's look, or
to the first wall, then blasts; rocket_launcher (item) fires it. death_gaze (ability) kills (or
hurts) along a sneaking beam. barrier_shield (ability) and place_barrier (scenario) throw mobs out
of a bubble, launching a caught player up instead; place_barrier's args refused in words; the
projectile_shield merge (barrier's own `what` already offers "projectiles"). super_saiyan
(ability), super_saiyan_form (mark) and super_saiyan_star (item) power a player up and back down."""

import re

from vfg_engine.io import (
    AddModifier,
    Damage,
    GiveEffect,
    Message,
    Motion,
    Particle,
    RemoveModifier,
    Sound,
    Summon,
)

GROUP = ("fireball", "rocket_launcher", "death_gaze", "barrier_shield", "place_barrier",
         "super_saiyan", "super_saiyan_form", "super_saiyan_star")

PROJECTILE_SHIELD = """
class ShieldTest(Ability):
    every = 0.1

    class Config(AbilityConfig):
        summary = "projectile_shield's own numbers, for the test."
        radius: float = Field(8, ge=1, le=128)
        what = "projectiles"
        strength: float = Field(8, ge=0.5, le=40)
        launch_strength: float = Field(10, ge=0.5, le=40)
        ring: bool = False
        damage = 0
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = UNTIL_STOPPED

    def tick(self, ctx):
        c = self.config
        found = targets(self, ctx, ctx.user, c.radius, c.what)
        if found:
            push(self, ctx, found, "away", c.strength, frm=ctx.user)
"""


def health(r, uuid):
    return r.io.by_uuid[uuid].health


def test_the_group_loads(rig):
    r = rig(*GROUP)
    loaded = r.start()["loaded"]
    assert sorted(loaded) == sorted(GROUP)


# ---- fireball, rocket_launcher ---------------------------------------------------------------

def stage(r):
    """Steve at 0 64 0 facing north (-z); a zombie 10 ahead, a cow well past its reach."""
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    return {
        "near": io.add_entity("minecraft:zombie", (0.0, 64.0, -10.0), health=1000),
        "beyond": io.add_entity("minecraft:cow", (0.0, 64.0, -400.0), health=1000),
    }


def test_the_ball_flies_to_the_first_mob_then_blasts(rig):
    r = rig("fireball")
    r.start()
    mobs = stage(r)
    reply = r.cast("Steve", "fireball")
    assert reply["status"] == "fired", reply
    r.run_for(2)
    assert health(r, mobs["near"]) == 958.0                   # 40 * (1 + 0.05 * power 1) on-fire damage
    assert health(r, mobs["beyond"]) == 1000                  # far past its reach: untouched
    hits = r.sent(Damage)
    assert {d.damage_type for d in hits} == {"minecraft:on_fire"}
    tnt = [s for s in r.sent(Summon) if s.entity_type == "minecraft:tnt"]
    assert tnt and "explosion_power:6" in tnt[0].nbt           # hot_fireball's blast power
    shielded = [e for e in r.sent(GiveEffect) if e.effect == "minecraft:resistance"]
    assert any(e.target == "Steve" for e in shielded)          # the caster is spared the blast
    assert any(s.name == "minecraft:entity.blaze.shoot" for s in r.sent(Sound))
    assert r.problems() == []


def test_with_nothing_on_the_line_it_blasts_at_the_first_wall(rig):
    r = rig("fireball")
    r.start()
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    for x in (-1, 0, 1):
        for y in (64, 65, 66):
            for z in (-19, -20, -21):
                io.set_block("minecraft:overworld", (x, y, z), "minecraft:stone")
    r.cast("Steve", "fireball")
    r.run_for(2)
    assert not r.sent(Damage)                                  # nothing was found to hit
    assert [s for s in r.sent(Summon) if s.entity_type == "minecraft:tnt"]   # it still blasts, at the wall
    assert r.problems() == []


def test_within_the_cooldown_the_rocket_launcher_only_says_so(rig):
    r = rig("fireball", "rocket_launcher")
    r.start()
    mobs = stage(r)
    r.op("give", player="Steve", item="rocket_launcher")
    r.tick()
    r.io.use("Steve")
    r.run_for(2)
    assert health(r, mobs["near"]) == 958.0
    r.io.use("Steve")                                          # straight away: recharging
    r.tick(2)
    assert any(m.where == "actionbar" and re.search(r"Rocket Launcher: \d+\.\d s", m.text) for m in r.sent(Message))
    assert health(r, mobs["near"]) == 958.0                    # no second shot landed
    assert r.problems() == []


# ---- death_gaze --------------------------------------------------------------------------

def test_sneaking_kills_what_the_beam_touches_up_to_the_first_wall(rig):
    """death_gaze/main.py and inv_G2's own verdict: the beam reaches "up to the first wall" (a
    wall stops it sooner); through_walls=False in on_sneaking is that. A mob before the wall dies,
    one behind it is spared, and one off the beam's width is untouched either way."""
    r = rig("death_gaze")
    r.start()
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    for x in (-1, 0, 1):                                       # a wall wide enough to be solid, not a sliver
        for y in (64, 65, 66):
            io.set_block("minecraft:overworld", (x, y, -5), "minecraft:stone")
    near = io.add_entity("minecraft:zombie", (0.0, 64.0, -3.0), health=1000)     # before the wall
    behind = io.add_entity("minecraft:zombie", (0.0, 64.0, -10.0), health=1000)  # behind the wall
    aside = io.add_entity("minecraft:zombie", (6.0, 64.0, -3.0), health=1000)
    assert r.op("grant", who="Steve", ability="death_gaze")["ok"]
    io.sneak("Steve", True)
    r.tick(2)
    assert near not in io.by_uuid                              # killed outright, before the wall
    assert health(r, behind) == 1000                           # the wall stops the beam sooner
    assert health(r, aside) == 1000                            # off the beam's width: untouched
    beam = [p for p in r.sent(Particle) if p.name == "minecraft:end_rod"]
    assert beam and all(p.force for p in beam)
    assert r.problems() == []


def test_letting_go_of_sneak_stops_the_beam(rig):
    r = rig("death_gaze")
    r.start()
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0), yaw=180.0)
    r.op("grant", who="Steve", ability="death_gaze")
    io.sneak("Steve", True)
    r.tick()
    io.sneak("Steve", False)
    r.tick()
    before = len(r.sent(Particle))
    far = io.add_entity("minecraft:zombie", (0.0, 64.0, -10.0), health=1000)
    r.run_for(1)
    assert health(r, far) == 1000                               # the beam is not firing any more
    assert len(r.sent(Particle)) == before


# ---- barrier_shield, place_barrier -------------------------------------------------------

def test_the_bubble_throws_a_mob_out(rig):
    r = rig("barrier_shield")
    r.start()
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0))
    io.join("Alex", pos=(3.0, 64.0, 0.0))                       # inside the default radius (5), but
                                                                 # the default `what` ("mobs") never
                                                                 # picks up a player (see the test below)
    mob = io.add_entity("minecraft:zombie", (2.0, 64.0, 0.0), health=20)
    r.op("grant", who="Steve", ability="barrier_shield")
    r.tick(2)
    assert any(op.target == mob for op in r.sent(Motion))       # the mob was given a push away
    assert not any(op.target == "Alex" for op in r.sent(Motion))
    rings = [p for p in r.sent(Particle) if p.name in ("minecraft:end_rod", "minecraft:soul_fire_flame")]
    assert rings and all(p.force for p in rings)
    assert r.problems() == []


BARRIER_EVERYTHING = """
class BarrierEverything(Ability):
    every = 0.2

    class Config(AbilityConfig):
        summary = "barrier_shield's own numbers, with `what` set to everything, for the test."
        radius: float = Field(5, ge=1, le=128)
        what = "everything"
        strength: float = Field(4, ge=0.5, le=40)
        launch_strength: float = Field(10, ge=0.5, le=40)
        ring: bool = True
        damage = 0
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = UNTIL_STOPPED

    def tick(self, ctx):
        c = self.config
        found = targets(self, ctx, ctx.user, c.radius, c.what)
        mobs = [t for t in found if not t.is_player]
        people = [t for t in found if t.is_player]
        if mobs:
            push(self, ctx, mobs, "away", c.strength, frm=ctx.user)
        if people:
            push(self, ctx, people, "up", c.launch_strength, frm=ctx.user)
"""


def test_a_player_caught_inside_is_launched_up_when_what_includes_players(rig):
    """barrier_shield's own `what` ("mobs" by default) decides who the bubble catches, the same way
    barrier_shield/main.py's around() preset gated players (`with_players=what in ("players",
    "everything")`); a player is only launched once `what` is "players" or "everything" (set by
    engine_config, as the module's docstring says to do for this look)."""
    r = rig(extra={"barrier_everything": BARRIER_EVERYTHING})
    r.start()
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0))
    io.join("Alex", pos=(3.0, 64.0, 0.0))
    for x in range(0, 7):                                         # solid ground under and round Alex's
        for z in range(-3, 4):                                    # feet, for the wind launch's own probe
            io.set_block("minecraft:overworld", (x, 63, z), "minecraft:stone")
    r.op("grant", who="Steve", ability="barrier_everything")
    r.tick(2)
    charges = [s for s in r.sent(Summon) if s.entity_type == "minecraft:wind_charge"]
    assert charges                                                 # Alex was launched by wind charges
    assert r.problems() == []


def test_place_barrier_at_a_saved_spot_with_no_player(rig):
    r = rig("place_barrier")
    r.start()
    mob = r.io.add_entity("minecraft:zombie", (12.0, 64.0, 100.0), health=20)
    iid = r.start_scenario("place_barrier", at="10 64 100", radius=5, strength=6, ring=False)
    r.tick(2)
    assert any(op.target == mob for op in r.sent(Motion))
    assert r.running(iid)                                        # until_stopped: it keeps going
    assert r.op("stop", instance=iid)["ok"]
    r.tick()
    assert not r.running(iid)


def test_place_barrier_needs_a_place(rig):
    r = rig("place_barrier")
    r.start()
    iid = r.start_scenario("place_barrier")
    r.tick()
    assert not r.running(iid)
    assert r.result(iid)["summary"] == "no barrier: no place given"


def test_place_barrier_args_refused_in_words(rig):
    r = rig("place_barrier")
    r.start()
    reply = r.op("start", scenario="place_barrier", args={"radius": 1000})
    assert not reply["ok"] and "radius" in reply["error"]
    reply = r.op("start", scenario="place_barrier", args={"what": "pets"})
    assert not reply["ok"]


def test_deflecting_projectiles_is_the_same_class_configured_for_it(rig):
    """projectile_shield's merge: barrier_shield's own `what` already offers "projectiles" (its
    verdict). A test-only class with projectile_shield's own numbers proves that value reaches
    the same targets()/push() calls barrier_shield's own code does."""
    r = rig("barrier_shield", extra={"shield_test": PROJECTILE_SHIELD})
    r.start()
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0))
    arrow = io.add_entity("minecraft:arrow", (4.0, 64.0, 0.0), health=None)
    r.op("grant", who="Steve", ability="shield_test")
    r.tick(2)
    assert any(op.target == arrow for op in r.sent(Motion))


# ---- super_saiyan, super_saiyan_form, super_saiyan_star --------------------------------------

def test_three_crouches_power_up_the_form(rig):
    r = rig("super_saiyan", "super_saiyan_form")
    r.start()
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.op("grant", who="Steve", ability="super_saiyan")
    for _ in range(3):
        io.sneak("Steve", True)
        r.tick()
        io.sneak("Steve", False)
        r.tick()
    r.run_for(2)                                                 # the 1.5 s charge
    speed = [e for e in r.sent(GiveEffect) if e.effect == "minecraft:speed"]
    assert speed and speed[0].amplifier == 2
    strength = [e for e in r.sent(GiveEffect) if e.effect == "minecraft:strength"]
    assert strength and strength[0].amplifier == 1
    gravity = [op for op in r.sent(AddModifier) if op.attribute == "minecraft:gravity"]
    assert gravity                                               # not sneaking: it is floating
    assert r.problems() == []


def test_sneaking_in_the_form_lets_you_sink_and_looking_up_levitates(rig):
    r = rig("super_saiyan", "super_saiyan_form")
    r.start()
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0))
    iid = r.op("apply", who="Steve", mark="super_saiyan_form")["instance"]
    r.tick(3)
    assert any(op.attribute == "minecraft:gravity" for op in r.sent(AddModifier))
    io.sneak("Steve", True)
    r.run_for(0.3)
    assert any(op.attribute == "minecraft:gravity" for op in r.sent(RemoveModifier))
    io.sneak("Steve", False)
    io.players["Steve"].pitch = -45.0
    r.run_for(0.3)
    assert any(e.effect == "minecraft:levitation" for e in r.sent(GiveEffect))
    assert r.running(iid)


def test_casting_again_powers_down_with_slow_falling(rig):
    r = rig("super_saiyan", "super_saiyan_form")
    r.start()
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.op("grant", who="Steve", ability="super_saiyan")
    r.op("bind", player="Steve", phrase="super saiyan", ability="super_saiyan")
    reply = r.op("cast", player="Steve", phrase="super saiyan", cast_id="c1")
    assert reply["status"] == "charging", reply                 # charge_s 1.5: on_cast runs once it is done
    r.run_for(2)
    on = [m for m in r.records("mark", "super_saiyan_form") if not m.ending]
    assert on
    reply = r.op("cast", player="Steve", phrase="super saiyan", cast_id="c2")
    assert reply["status"] == "charging", reply
    r.run_for(2)
    off = [m for m in r.records("mark", "super_saiyan_form") if not m.ending]
    assert not off
    falls = [e for e in r.sent(GiveEffect) if e.effect == "minecraft:slow_falling"]
    assert falls
    assert r.problems() == []


def test_the_star_item_casts_it(rig):
    r = rig("super_saiyan", "super_saiyan_form", "super_saiyan_star")
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.op("give", player="Steve", item="super_saiyan_star")
    r.tick()
    r.io.use("Steve")
    r.run_for(2)
    assert any(e.effect == "minecraft:speed" for e in r.sent(GiveEffect))
    assert r.problems() == []
