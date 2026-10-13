"""build_shape / sign / chest: shapes in their own frame, turned to face a way and built by the
block queue; a sign's text and a chest's items as the block's SNBT."""

from vfg_engine.io import SetBlock
from vfg_engine.kit import compass
from vfg_engine.kit.blocks import shape_parts

BUILD = '''
class Build(Scenario):
    class Config(ScenarioConfig):
        summary = "Builds a shape in front of Steve."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        kind: str = Field("box", description="the shape")
        hollow: bool = Field(False, description="hollow")
        blocks: str = Field("stone_bricks", description="what of")
        facing: str = Field("look", description="which way")

    class State(State):
        box: list = []
        facing: str = ""

    def start(self, ctx):
        a = self.args
        built = build_shape(self, ctx, ctx.world.player("Steve"), a.kind, 5, 4, 5, a.blocks, a.facing,
                            offset=(0, 0, 4), hollow=a.hollow)
        self.state.box = [list(built.a), list(built.b)]
        self.state.facing = built.facing
'''

SIGNS = '''
class Signs(Scenario):
    class Config(ScenarioConfig):
        summary = "A sign and a chest."
        duration_s = UNTIL_STOPPED

    def start(self, ctx):
        sign(self, ctx, (0, 64, 3), ["Steve", "Sniper Nest"], facing="north")
        chest(self, ctx, (1, 64, 3), ["diamond 4", "minecraft:arrow 64", "bread"])
'''


def test_shape_parts_cover_the_shape_once():
    for kind in ("box", "cylinder", "dome", "pyramid", "staircase", "wall"):
        cells = set()
        for (u1, y1, w1, u2, y2, w2), solid in shape_parts(kind, 7, 5, 7, hollow=False):
            assert solid
            for u in range(u1, u2 + 1):
                for y in range(y1, y2 + 1):
                    for w in range(w1, w2 + 1):
                        assert (u, y, w) not in cells, kind
                        cells.add((u, y, w))
        assert cells, kind
    hollow = shape_parts("box", 5, 5, 5, hollow=True)
    assert any(not solid for _, solid in hollow)
    assert compass(0) == "south" and compass(90) == "west" and compass(-90) == "east" and compass(180) == "north"


def test_build_shape_faces_the_player_and_is_built_by_the_queue(rig):
    r = rig({"build": BUILD})
    r.start()
    r.io.join("Steve", pos=(0.5, 64.0, 0.5), yaw=-90.0)            # facing east (+x)
    r.tick()
    iid = r.start_scenario("build", hollow=True)
    r.run_for(1)
    state = r.rt.records[iid].state
    assert state.facing == "east"
    (x1, y1, z1), (x2, y2, z2) = state.box
    assert (x1, x2, z1, z2, y1, y2) == (2, 6, -2, 2, 64, 67)       # 4 ahead, 5 across, 4 up
    ow = "minecraft:overworld"
    assert r.io.block_at(ow, 2, 65, 0) == "minecraft:stone_bricks"  # its wall
    assert r.io.block_at(ow, 4, 65, 0) == "minecraft:air"           # hollow inside
    assert r.problems() == []


def test_a_mix_is_picked_per_cell(rig):
    r = rig({"build": BUILD})
    r.start()
    r.io.join("Steve", pos=(0.5, 64.0, 0.5))
    r.tick()
    r.start_scenario("build", blocks="50% stone_bricks 50% cobblestone", facing="south")
    r.run_for(1)
    got = {r.io.block_at("minecraft:overworld", x, y, z) for x in range(-2, 3) for y in range(64, 68)
           for z in range(2, 7)}
    assert got == {"minecraft:stone_bricks", "minecraft:cobblestone"}


def test_sign_and_chest_are_one_block_each_with_their_snbt(rig):
    r = rig({"signs": SIGNS})
    r.start()
    r.io.join("Steve")
    r.tick()
    r.start_scenario("signs")
    r.run_for(0.5)
    blocks = [op.block for op in r.sent(SetBlock)] + [
        f.block for f in r.io.sent if type(f).__name__ == "Fill" and f.a == f.b]
    assert any(b.startswith("minecraft:oak_wall_sign[facing=north]{front_text:{messages:[\"Steve\",\"Sniper Nest\"")
               for b in blocks), blocks
    assert any(b.startswith("minecraft:chest[facing=north]{Items:[{Slot:0b,id:\"minecraft:diamond\",count:4}")
               for b in blocks), blocks
