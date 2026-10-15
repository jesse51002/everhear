"""blast / lightning / crater: damage that falls off and throws back, TNT that breaks with the
spared shielded, a pattern kept off players, bolts kept off the caster, a crater carved through
the block queue (and only that) with its done(ctx) called."""

import math

import pytest

from vfg_engine.io import Damage, GiveEffect, Motion, Particle, Remove, Sound, Summon
from vfg_engine.kit import blast_damage, blast_points

BOOM = '''
class Boom(Scenario):
    class Config(ScenarioConfig):
        summary = "A blast at x 64 0."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        x: float = Field(0, description="where")
        power: float = Field(4, description="how big")
        breaks: bool = Field(False, description="TNT")
        layout: str = Field("one", description="the pattern")
        keep: float = Field(0, description="off players")
        spare: str = Field("", description="spared")
        once: bool = Field(False, description="end at once, a one-shot")

    class State(State):
        points: int = 0

    def start(self, ctx):
        a = self.args
        spare = [s for s in a.spare.split(",") if s]
        self.state.points = len(blast(self, ctx, (a.x, 64.0, 0.0), a.power, a.layout, 4, a.breaks, a.keep, spare))
        if a.once:
            self.end()
'''

BOLT = '''
class Bolt(Ability):
    class Config(AbilityConfig):
        summary = "Lightning where you look, never on you."
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = 1
        damage = 0
        on_me: bool = False

    def on_cast(self, ctx, ev):
        at = ctx.user.pos if self.config.on_me else ctx.user.looking_at()
        self.message("struck %d" % len(lightning(self, ctx, at, count=5, spread=3, layout="ring")))
'''

HOLE = '''
class Hole(Scenario):
    class Config(ScenarioConfig):
        summary = "A crater."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        y: int = Field(50, description="its middle")
        radius: int = Field(3, description="how big")
        leave: str = Field("air", description="what is left")

    class State(State):
        done: bool = False

    def start(self, ctx):
        crater(self, ctx, (0, self.args.y, 0), self.args.radius, self.args.leave)

    def done(self, ctx):
        self.state.done = True
'''


def test_a_blast_falls_off_throws_back_and_spares(rig):
    r = rig({"boom": BOOM})
    r.start()
    io = r.io
    io.join("Steve", pos=(0.0, 64.0, 40.0))
    near = io.add_entity("minecraft:zombie", (1.0, 64.0, 0.0), health=200)
    mid = io.add_entity("minecraft:zombie", (5.0, 64.0, 0.0), health=200)
    out = io.add_entity("minecraft:zombie", (9.0, 64.0, 0.0), health=200)
    villager = io.add_entity("minecraft:villager", (1.0, 64.0, 1.0))
    r.tick()
    r.start_scenario("boom")
    dealt = {d.target: d.amount for d in r.sent(Damage)}
    assert set(dealt) == {near, mid} and out not in dealt and villager not in dealt
    assert dealt[near] == pytest.approx(blast_damage(4, math.dist((0, 64, 0), (1, 64.2, 0))), rel=1e-3)
    assert dealt[near] > dealt[mid] > 0
    pushed = {m.target: m.vec for m in r.sent(Motion)}
    assert set(pushed) == {near, mid} and pushed[near][0] > pushed[mid][0] > 0     # away, harder nearer
    assert [p.force for p in r.sent(Particle) if p.name == "minecraft:explosion_emitter"] == [True]
    assert len(r.sent(Sound)) == 1 and r.problems() == []


def test_a_breaking_blast_is_tnt_and_shields_the_spared(rig):
    r = rig({"boom": BOOM})
    r.start()
    io = r.io
    io.join("Steve", pos=(2.0, 64.0, 0.0))
    villager = io.add_entity("minecraft:villager", (1.0, 64.0, 1.0))
    wolf = io.add_entity("minecraft:wolf", (0.0, 64.0, 2.0))
    io.by_uuid[wolf].owner = "Steve"
    zombie = io.add_entity("minecraft:zombie", (0.0, 64.0, 3.0))
    r.tick()
    r.start_scenario("boom", breaks=True, spare="Steve")
    tnt = [s for s in r.sent(Summon) if s.entity_type == "minecraft:tnt"]
    assert len(tnt) == 1 and tnt[0].nbt == "{fuse:0s,explosion_power:4f}"
    shielded = {g.target for g in r.sent(GiveEffect) if g.effect == "minecraft:resistance" and g.amplifier == 4}
    assert shielded == {"Steve", villager, wolf} and zombie not in shielded
    assert r.sent(Damage) == []                      # TNT's damage is Minecraft's


def test_a_one_shot_that_ends_at_once_leaves_its_tnt_to_go_off(rig):
    r = rig({"boom": BOOM})
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 30.0))
    r.tick()
    iid = r.start_scenario("boom", breaks=True, once=True)
    r.tick()
    (tnt,) = [s for s in r.sent(Summon) if s.entity_type == "minecraft:tnt"]
    assert iid not in r.rt.records and tnt.uuid in r.io.by_uuid
    assert tnt.uuid not in [op.uuid for op in r.sent(Remove)]


def test_a_pattern_keeps_off_players(rig):
    assert len(blast_points((0, 0, 0), "plus", 4)) == 5
    assert len(blast_points((0, 0, 0), "rings", 4)) == 17
    r = rig({"boom": BOOM})
    r.start()
    r.io.join("Steve", pos=(4.0, 64.0, 0.0))                     # on the plus's east arm
    r.tick()
    iid = r.start_scenario("boom", layout="plus", keep=2, breaks=True)
    assert r.rt.records[iid].state.points == 4


def test_lightning_strikes_round_the_look_and_never_on_the_caster(rig):
    r = rig({"bolt": BOLT})
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.io.look_at("Steve", (0.0, 64.0, 10.0))
    r.tick()
    r.cast("Steve", "bolt")
    bolts = [s for s in r.sent(Summon) if s.entity_type == "minecraft:lightning_bolt"]
    assert len(bolts) == 5 and all(math.dist(b.pos, (0, 64, 0)) >= 4 for b in bolts)

    r2 = rig({"bolt": BOLT.replace("on_me: bool = False", "on_me: bool = True")})
    r2.start()
    r2.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r2.tick()
    r2.cast("Steve", "bolt")
    assert [s for s in r2.sent(Summon) if s.entity_type == "minecraft:lightning_bolt"] == []


def test_a_crater_is_carved_by_the_queue_and_says_done(rig):
    r = rig({"hole": HOLE})
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    iid = r.start_scenario("hole", y=50, radius=3)
    r.run_for(2)
    io, ow = r.io, "minecraft:overworld"
    assert io.block_at(ow, 0, 50, 0) == "minecraft:air" and io.block_at(ow, 3, 50, 0) == "minecraft:air"
    assert io.block_at(ow, 3, 53, 3) == "minecraft:stone"          # outside the ball
    assert io.block_at(ow, 0, 46, 0) == "minecraft:stone"
    assert r.rt.records[iid].state.done is True and r.problems() == []


def test_a_crater_never_carves_the_bedrock_floor_and_can_leave_lava(rig):
    r = rig({"hole": HOLE})
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    r.tick()
    r.start_scenario("hole", y=-62, radius=4)
    r.start_scenario("hole", y=30, radius=6, leave="lava")
    r.run_for(3)
    io, ow = r.io, "minecraft:overworld"
    assert io.block_at(ow, 0, -64, 0) == "minecraft:bedrock"
    assert io.block_at(ow, 0, 25, 0) == "minecraft:lava"            # the lowest third
    assert io.block_at(ow, 0, 33, 0) == "minecraft:air"
