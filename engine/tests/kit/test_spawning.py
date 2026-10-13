"""safe_spots / sky_spots / summon_near / mob_nbt / boss_bar / world_mob_rule: spots with ground and
room only, a cap counted over this instance's own living mobs, a bar on summed health, a rule on
the world's own mobs undone at the end; block reads batched into two probes a call."""

import math

from vfg_engine.io import Bossbar, EntityData, Summon
from vfg_engine.kit import mob_nbt

SPOTS = '''
class Spots(Scenario):
    class Config(ScenarioConfig):
        summary = "Finds spots round Steve."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        sky: bool = Field(False, description="in the sky")
        spread: float = Field(8, description="how far")

    class State(State):
        spots: list = []

    def start(self, ctx):
        steve = ctx.world.player("Steve")
        if self.args.sky:
            got = sky_spots(ctx, [steve], count=6, radius=10, height=20)
        else:
            got = safe_spots(ctx, steve, count=5, spread=self.args.spread)
        self.state.spots = [list(s) for s in got]
'''

HORDE = '''
class Horde(Scenario):
    every = 1

    class Config(ScenarioConfig):
        summary = "Zombies round Steve, at most 3 alive, with a bar."
        duration_s = UNTIL_STOPPED

    class State(State):
        mobs: list = []
        health: float = 0
        alive: int = 0

    def start(self, ctx):
        self.state.mobs = summon_near(self, ctx, "Steve", "minecraft:zombie", 5, 6, cap=3,
                                      nbt=mob_nbt(name="Grunt", health=40))

    def tick(self, ctx):
        self.state.mobs = self.state.mobs + summon_near(self, ctx, "Steve", "minecraft:zombie", 5, 6, cap=3)
        self.state.health, self.state.alive = boss_bar(self, ctx, self.state.mobs, "The Horde")
'''

RULE = '''
class Creepy(Scenario):
    every = 0.5

    class Config(ScenarioConfig):
        summary = "Long fuses on the world's creepers, put back at the end."
        duration_s = UNTIL_STOPPED

    def tick(self, ctx):
        world_mob_rule(self, ctx, "creeper", nbt="{Fuse:60s}", undo_nbt="{Fuse:30s}",
                       modifiers=[("movement_speed", -0.5, "add_multiplied_total")])

    def stop(self, ctx, reason):
        undo_world_mob_rule(self, ctx)
'''


def test_safe_spots_stand_on_ground_with_room_never_on_the_player(rig):
    r = rig({"spots": SPOTS})
    r.start()
    r.io.join("Steve", pos=(0.5, 64.0, 0.5))
    for x in range(-8, 9):                       # a step of stone east of him: spots there are on top
        for z in range(-8, 9):
            if x > 2:
                r.io.set_block("minecraft:overworld", (x, 64, z), "minecraft:stone")
    r.tick()
    probes = []
    real = r.io.probe
    r.io.probe = lambda *a: probes.append(a[2]) or real(*a)
    iid = r.start_scenario("spots")
    spots = r.rt.records[iid].state.spots
    assert 1 <= len(spots) <= 5 and probes == ["open", "solid"]
    block = lambda x, y, z: r.io.block_at("minecraft:overworld", math.floor(x), int(y), math.floor(z))  # noqa: E731
    for x, y, z in spots:                         # on the ground or on top of the wall, with room
        assert block(x, y - 1, z) == "minecraft:stone" and block(x, y, z) == block(x, y + 1, z) == "minecraft:air"
        assert math.hypot(x - 0.5, z - 0.5) >= 1.5
    assert r.problems() == []


def test_sky_spots_are_in_open_air_above(rig):
    r = rig({"spots": SPOTS})
    r.start()
    r.io.join("Steve", pos=(0.5, 64.0, 0.5))
    r.tick()
    iid = r.start_scenario("spots", sky=True)
    spots = r.rt.records[iid].state.spots
    assert len(spots) == 6 and all(s[1] == 84.0 and math.hypot(s[0] - 0.5, s[2] - 0.5) <= 10 for s in spots)


def test_summon_near_keeps_to_its_cap_and_the_bar_follows_their_health(rig):
    r = rig({"horde": HORDE})
    r.start()
    r.io.join("Steve", pos=(0.5, 64.0, 0.5))
    r.tick()
    iid = r.start_scenario("horde")
    r.tick()
    first = [s for s in r.sent(Summon) if s.entity_type == "minecraft:zombie"]
    assert len(first) == 3 and 'CustomName:"Grunt"' in first[0].nbt and "Health:40f" in first[0].nbt
    r.run_for(1.1)
    assert len([s for s in r.sent(Summon) if s.entity_type == "minecraft:zombie"]) == 3     # still the cap
    bar = r.sent(Bossbar)[-1]
    assert bar.value == 120 and bar.max == 120 and bar.name == "The Horde"
    r.io.kill_entity(first[0].uuid)
    r.run_for(2.1)                                # one more summoned, then seen alive
    state = r.rt.records[iid].state
    assert len([s for s in r.sent(Summon) if s.entity_type == "minecraft:zombie"]) == 4      # one more, to the cap
    assert r.sent(Bossbar)[-1].max == 120 and state.alive == 3
    assert r.problems() == []
    r.op("stop", instance=iid)
    assert not any(u in r.io.by_uuid for u in state.mobs)          # removed at the end


def test_a_world_mob_rule_changes_each_once_and_is_undone(rig):
    r = rig({"creepy": RULE})
    r.start()
    r.io.join("Steve", pos=(0.0, 64.0, 0.0))
    wild = r.io.add_entity("minecraft:creeper", (5.0, 64.0, 0.0))
    far = r.io.add_entity("minecraft:creeper", (200.0, 64.0, 0.0))
    r.tick()
    iid = r.start_scenario("creepy")
    r.run_for(2)
    merged = [(d.target, d.snbt) for d in r.sent(EntityData)]
    assert merged == [(wild, "{Fuse:60s}")]                          # once, and not the far one
    assert r.io.by_uuid[wild].modifiers
    r.op("stop", instance=iid)
    assert [(d.target, d.snbt) for d in r.sent(EntityData)][-1] == (wild, "{Fuse:30s}")
    assert not r.io.by_uuid[wild].modifiers and far in r.io.by_uuid


def test_mob_nbt():
    nbt = mob_nbt(name="Horde King", health=800, scale=3, gear="netherite", weapon="netherite_sword",
                  glowing=True, extra="Fuse:60s")
    assert nbt.startswith("{PersistenceRequired:1b,") and nbt.endswith("}")
    for part in ('CustomName:"Horde King"', 'Health:800f', '{id:"minecraft:scale",base:3}',
                 'head:{id:"minecraft:netherite_helmet",count:1}', 'mainhand:{id:"minecraft:netherite_sword",count:1}',
                 "mainhand:0f", "Glowing:1b", "Fuse:60s"):
        assert part in nbt, part
