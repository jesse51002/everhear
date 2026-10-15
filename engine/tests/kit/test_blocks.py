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


LONG_ITEM = '''
class {cls}(Item):
    class Config(ItemConfig):
        summary = "An item with a long look."
        base = "carrot_on_a_stick"
        name = "{name}"
        active_in = "mainhand"
        color = "gold"
        glint = True
        lore = {lore}
'''

BIG_CHEST = '''
class BigChest(Scenario):
    class Config(ScenarioConfig):
        summary = "A chest of six long EverHear items and arrows."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        huge: bool = Field(False, description="one item too long for any command")

    class State(State):
        later: int = -1

    def start(self, ctx):
        items = ["everhear:huge"] if self.args.huge else ["everhear:power%d" % i for i in range(6)] + ["arrow 64"]
        self.state.later = chest(self, ctx, (3, 64, 3), items)
'''


def _big_chest_rig(rig):
    files = {"big_chest": BIG_CHEST}
    for i in range(6):
        files["power_%d" % i] = LONG_ITEM.format(cls="Power%d" % i, name="Power of the %dth Kind" % i,
                                                 lore=repr(["A line of lore, long enough to weigh: %d" % k
                                                            for k in range(5)]))
    files["huge"] = LONG_ITEM.format(cls="Huge", name="Huge", lore=repr(["x" * 300] * 6))
    r = rig(files)
    r.start()
    r.io.join("Steve")
    r.tick()
    return r


def test_a_big_chest_is_queued_then_filled_under_the_command_cap(rig):
    from vfg_engine.io import BlockData
    from vfg_engine.mc.rcon import MAX_COMMAND
    r = _big_chest_rig(rig)
    iid = r.start_scenario("big_chest")
    waiting = r.rt.records[iid].state.later
    r.run_for(0.5)
    (placed,) = [op for op in r.sent(SetBlock) if op.block.startswith("minecraft:chest")]
    fills = r.sent(BlockData)
    assert waiting == len(fills) > 0                                  # more than one command's worth
    assert len(("setblock 3 64 3 %s strict" % placed.block).encode()) <= MAX_COMMAND
    assert all(f.pos == (3, 64, 3) and f.path == "Items" and f.how == "append" for f in fills)
    assert all(len(("data modify block 3 64 3 Items append value %s" % f.snbt).encode()) <= MAX_COMMAND
               for f in fills)
    sent = r.io.sent
    assert sent.index(placed) < min(sent.index(f) for f in fills)    # the chest first, then its stacks
    every = placed.block + "".join(f.snbt for f in fills)
    assert all('item:"power%d"' % i in every for i in range(6)) and 'id:"minecraft:arrow",count:64' in every
    assert r.problems() == []


def test_a_one_off_that_ends_at_done_still_gets_every_stack(rig):
    from vfg_engine.io import BlockData
    files = {"power%d" % i: LONG_ITEM.format(cls="Power%d" % i, name="Power of the %dth Kind" % i,
                                             lore=repr(["A line of lore, long enough to weigh: %d" % k
                                                        for k in range(5)])) for i in range(6)}
    files["chest_once"] = BIG_CHEST.replace("class BigChest", "class ChestOnce") + """
    def done(self, ctx):
        self.result("filled")
        self.end()
"""
    r = rig(files)
    r.start()
    r.io.join("Steve")
    r.tick()
    iid = r.start_scenario("chest_once")
    r.run_for(1)
    assert iid not in r.rt.records                                    # it ended at done(ctx)
    stacks = r.io.block_data.get(("minecraft:overworld", 3, 64, 3), [])
    placed = [op for op in r.sent(SetBlock) if op.block.startswith("minecraft:chest")][0].block
    assert len(stacks) == len(r.sent(BlockData)) > 0
    every = placed + "".join(snbt for _, _, snbt in stacks)
    assert all('item:"power%d"' % i in every for i in range(6))      # none lost to the end at done


def test_a_stack_too_long_for_any_command_is_refused_in_words(rig):
    r = _big_chest_rig(rig)
    r.op("start", scenario="big_chest", args={"huge": True})
    assert any("everhear:huge in a chest is a stack of" in m and "one command holds 1446" in m for m in r.lines())
    assert [op for op in r.sent(SetBlock) if op.block.startswith("minecraft:chest")] == []
