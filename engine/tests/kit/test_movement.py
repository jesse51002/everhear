"""push / land_safely: the velocity a throw needs, each way, a safe landing's cushion, a
projectile turned without a hop; a landing on safe ground only, on the spot or in a ring round it."""

import math

import pytest

from vfg_engine.io import GiveEffect, Motion, Summon, Teleport
from vfg_engine.kit.movement import flight, speed_for, wind_plan

THROW = '''
class Throw(Ability):
    class Config(AbilityConfig):
        summary = "Throws what is round you."
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = 1
        damage = 0
        way: str = "away"
        landing: str = "safe"

    def on_cast(self, ctx, ev):
        found = targets(self, ctx, ctx.user, 10, "everything")
        push(self, ctx, found, self.config.way, 8, landing=self.config.landing)
'''

LAND = '''
class Land(Scenario):
    class Config(ScenarioConfig):
        summary = "Lands Steve somewhere safe."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        exact: bool = Field(True, description="on the spot")

    class State(State):
        spot: list = []

    def start(self, ctx):
        spot = land_safely(self, ctx, "Steve", (100, 64, 100), exact=self.args.exact)
        self.state.spot = list(spot) if spot else []
'''


@pytest.mark.parametrize("way, blocks", [("up", 10), ("up", 30), ("away", 8), ("away", 20)])
def test_the_speed_throws_that_far(way, blocks):
    along, up, ticks = speed_for(way, blocks)
    x, t, top = flight(along, up)
    assert t == ticks
    assert (top if way == "up" else x) == pytest.approx(blocks, abs=0.05)


def ability(rig, way="away", landing="safe"):
    r = rig({"throw": THROW.replace('way: str = "away"', 'way: str = "%s"' % way)
                            .replace('landing: str = "safe"', 'landing: str = "%s"' % landing)})
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.io.join("Alex", pos=(0.0, 64.0, 5.0))
    zombie = r.io.add_entity("minecraft:zombie", (4.0, 64.0, 0.0))
    arrow = r.io.add_entity("minecraft:arrow", (-3.0, 65.0, 0.0), health=None)
    r.tick()
    return r, zombie, arrow


def test_push_away_throws_mobs_and_players_and_cushions_the_landing(rig):
    r, zombie, _ = ability(rig)
    r.cast("Steve", "throw")
    vecs = {m.target: m.vec for m in r.sent(Motion)}
    assert set(vecs) == {zombie}                     # never the caster; a player has no Motion
    assert vecs[zombie][0] > 0 and abs(vecs[zombie][2]) < 1e-6 and vecs[zombie][1] > 0
    n, o, _ = wind_plan("along", 8.0)
    charges = [s for s in r.sent(Summon) if s.entity_type == "minecraft:wind_charge"]
    assert len(charges) == n and all(c.pos == pytest.approx((0.0, 64.6, 5.0 - o)) for c in charges)
    assert all(c.nbt == "{Motion:[0.0d,-1.0d,0.0d]}" for c in charges)     # behind Alex, away from Steve
    cushions = {g.target for g in r.sent(GiveEffect) if g.effect == "minecraft:resistance" and g.amplifier == 4}
    assert cushions == {zombie, "Alex"} and r.problems() == []


def test_a_player_in_the_air_or_walled_in_is_not_thrown(rig):
    r, zombie, _ = ability(rig, way="up")
    r.io.players["Alex"].on_ground = False
    r.tick()
    r.cast("Steve", "throw")
    assert [s for s in r.sent(Summon) if s.entity_type == "minecraft:wind_charge"] == []
    r2, _, _ = ability(rig, way="up")
    for x in range(-2, 3):
        for z in range(3, 8):
            if (x, z) != (0, 5):
                r2.io.set_block("minecraft:overworld", (x, 64, z), "minecraft:stone")
    r2.tick()
    r2.cast("Steve", "throw")
    assert [s for s in r2.sent(Summon) if s.entity_type == "minecraft:wind_charge"] == []


def test_a_player_thrown_up_gets_a_ring_of_charges(rig):
    r, _, _ = ability(rig, way="up")
    r.cast("Steve", "throw")
    n, o, _ = wind_plan("up", 8.0)
    charges = [s.pos for s in r.sent(Summon) if s.entity_type == "minecraft:wind_charge"]
    assert len(charges) == n
    assert all(math.hypot(x - 0.0, z - 5.0) == pytest.approx(o) and y == pytest.approx(64.6) for x, y, z in charges)


def test_push_toward_never_past_and_hurt_has_no_cushion(rig):
    r, zombie, _ = ability(rig, way="toward", landing="hurt")
    r.cast("Steve", "throw")
    v = {m.target: m.vec for m in r.sent(Motion)}[zombie]
    assert v[0] < 0                                  # toward Steve
    along, _, _ = speed_for("toward", 3.0)           # 4 away: at most 3 blocks
    assert v[0] == pytest.approx(-along, abs=1e-3)
    assert r.sent(GiveEffect) == []


def test_a_projectile_is_turned_without_a_hop(rig):
    r = rig({"throw": THROW.replace('"everything"', '"projectiles"')})
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    arrow = r.io.add_entity("minecraft:arrow", (-3.0, 65.0, 0.0), health=None)
    r.tick()
    r.cast("Steve", "throw")
    (m,) = r.sent(Motion)
    assert m.target == arrow and m.vec[0] == pytest.approx(-8 / 5) and m.vec[1] == pytest.approx(0.1)


def test_up_is_straight_up(rig):
    r, zombie, _ = ability(rig, way="up")
    r.cast("Steve", "throw")
    v = {m.target: m.vec for m in r.sent(Motion)}[zombie]
    assert v[0] == 0 and v[2] == 0 and v[1] == pytest.approx(speed_for("up", 8)[1], abs=1e-3)


def test_land_safely_on_the_spot_when_it_is_safe(rig):
    r = rig({"land": LAND})
    r.start()
    r.io.join("Steve")
    r.tick()
    iid = r.start_scenario("land")
    (tp,) = r.sent(Teleport)
    assert tp.target == "Steve" and tp.pos == (100.5, 64.0, 100.5)
    assert {g.effect for g in r.sent(GiveEffect)} == {"minecraft:resistance", "minecraft:slow_falling"}
    assert r.rt.records[iid].state.spot == [100.5, 64.0, 100.5]


def test_land_safely_goes_round_magma(rig):
    r = rig({"land": LAND})
    r.start()
    r.io.join("Steve")
    r.io.set_block("minecraft:overworld", (100, 63, 100), "minecraft:magma_block")
    r.tick()
    r.start_scenario("land")
    (tp,) = r.sent(Teleport)
    assert tp.pos != (100.5, 64.0, 100.5) and tp.pos[1] == 64.0
    assert 7 <= math.hypot(tp.pos[0] - 100.5, tp.pos[2] - 100.5) <= 10
