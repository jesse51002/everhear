"""The castle group on FakeIo: castle, with ice_castle (its walls and towers args) and made/main's
krownjm_mansion (plan="mansion") merged in. Built in front of a player facing them, through the
block queue, ending when the last block is down with a result; at a place, named and saved; a
sign; nobody online."""

import time

from vfg_engine.io import Forceload, SetBlock

OW = "minecraft:overworld"


def block(r, x, y, z):
    return r.io.block_at(OW, x, y, z)


def build(r, **args):
    """Start it and run until it ends (the block queue spread over ticks); its instance."""
    iid = r.start_scenario("castle", **args)
    for _ in range(600):
        if not r.running(iid):
            break
        r.tick()
    assert not r.running(iid), "still building after 60 s"
    return iid


def steve(r):
    r.io.join("Steve", pos=(0.5, 64.0, 0.5), yaw=180.0)     # on FakeIo's stone, facing north (-z)
    r.tick()


def test_it_loads(rig):
    r = rig("castle")
    assert r.start()["loaded"] == ["castle"]


def test_a_castle_in_front_of_him_its_gate_facing_him(rig):
    r = rig("castle")
    r.start()
    steve(r)
    iid = build(r, players="Steve", sign=["{player}'s Castle", "keep out"])
    got = r.result(iid)
    assert got["summary"] == "built a castle, 23 wide, of stone_bricks: Steve's at 0 64 -17"
    assert got["reason"] == "ended"
    # the front wall is 6 ahead (z -6), the back wall at z -28, the side walls at x -11 and 11
    assert block(r, 8, 64, -6) == "minecraft:stone_bricks"
    assert block(r, 0, 64, -6) == "minecraft:air"                          # the gate
    assert block(r, 0, 67, -6) == "minecraft:iron_bars"                    # bars across its top
    assert block(r, -11, 66, -17) == "minecraft:stone_bricks" and block(r, 11, 66, -17) == "minecraft:stone_bricks"
    assert block(r, 0, 64, -28) == "minecraft:stone_bricks"
    assert block(r, 0, 63, -17) == "minecraft:stone_bricks"                # the yard's floor
    assert block(r, 0, 64, -17) == "minecraft:air"                         # the yard
    assert block(r, 13, 64, -6) == "minecraft:cobblestone"                 # a tower's wall
    assert block(r, 1, 71, -6) == "minecraft:stone_bricks" and block(r, 2, 71, -6) == "minecraft:air"   # battlements
    (sign,) = [op for op in r.sent(SetBlock) if "sign" in op.block]
    assert sign.pos == (0, 68, -5) and sign.block.startswith("minecraft:oak_wall_sign[facing=south]")
    assert "Steve's Castle" in sign.block and "keep out" in sign.block
    assert r.sent(Forceload)
    assert r.problems() == []


def test_an_ice_castle_is_its_blocks(rig):
    r = rig("castle")
    r.start()
    steve(r)
    iid = build(r, players="Steve", walls="packed_ice", towers="blue_ice", size=31)
    assert r.result(iid)["summary"].startswith("built a castle, 31 wide, of packed_ice")
    assert block(r, 8, 64, -6) == "minecraft:packed_ice" and block(r, 18, 64, -6) == "minecraft:blue_ice"


def test_a_mix_and_stairs_down(rig):
    r = rig("castle")
    r.start()
    steve(r)
    build(r, players="Steve", walls="50% stone_bricks 50% mossy_stone_bricks", stairs=20)
    wall = {block(r, x, y, -6) for x in range(3, 8) for y in range(64, 70)}      # between the gate and a tower
    assert wall == {"minecraft:stone_bricks", "minecraft:mossy_stone_bricks"}
    # the staircase goes down from just inside the gate, a block a step, carved through the stone:
    # 12 steps in (z -20) the step is at y 51, with three blocks of air over it
    assert block(r, 0, 51, -20) in wall and block(r, 0, 50, -20) == "minecraft:stone"
    assert {block(r, 0, y, -20) for y in (52, 53, 54)} == {"minecraft:air"}


def test_at_a_place_named_and_saved(rig):
    r = rig("castle")
    r.start()
    steve(r)
    iid = build(r, players="Steve", at="200 64 300", size=11, name="Fort Knox")
    got = r.result(iid)
    assert got["summary"] == ("built a castle, 11 wide, of stone_bricks: Steve's at 200 64 305; "
                              "saved as fort_knox")
    assert block(r, 205, 66, 305) == "minecraft:stone_bricks"              # its side wall: it faces south from the spot
    assert block(r, 200, 64, 300) == "minecraft:air"                       # the gate
    place = r.rt.places.get("fort_knox")
    assert place["pos"] == [200.0, 64.0, 305.0] and place["yaw"] == 0.0


def test_the_mansion(rig):
    r = rig("castle")
    r.start()
    steve(r)
    t0 = time.perf_counter()
    iid = build(r, players="Steve", plan="mansion", name="home")
    assert time.perf_counter() - t0 < 30
    got = r.result(iid)
    assert got["summary"] == "built a mansion, 31 by 23, two floors: Steve's at 0 65 -17; saved as home"
    # its front is 6 ahead (z -6), 31 across (x -15..15), 23 deep (to z -28)
    doors = [op for op in r.sent(SetBlock) if "oak_door" in op.block]
    assert [d.pos for d in doors] == [(0, 65, -6), (0, 66, -6)]
    assert doors[0].block == "minecraft:oak_door[facing=north,half=lower,hinge=left,open=false]"
    assert block(r, 0, 65, -6).startswith("minecraft:oak_door") and block(r, 0, 66, -6).startswith("minecraft:oak_door")
    assert block(r, -9, 66, -6) == "minecraft:glass_pane" and block(r, 9, 73, -6) == "minecraft:glass_pane"
    assert block(r, 0, 63, -17) == "minecraft:stone_bricks"                # the foundation
    assert block(r, 0, 64, -17) in ("minecraft:stone_bricks", "minecraft:cobblestone")   # the ground floor's floor
    assert block(r, 0, 70, -17) in ("minecraft:white_concrete", "minecraft:quartz_block")
    assert block(r, 0, 76, -17) == "minecraft:dark_oak_planks"             # the roof
    assert block(r, 5, 66, -17) == "minecraft:spruce_planks"               # a partition
    assert block(r, 0, 69, -21) == "minecraft:glowstone"
    assert block(r, 0, 65, -17) == "minecraft:air"                         # the hall


def test_nobody_online(rig):
    r = rig("castle")
    r.start()
    iid = build(r)
    assert r.result(iid)["summary"] == "no castle: nobody is online"


def test_two_players_two_castles(rig):
    r = rig("castle")
    r.start()
    steve(r)
    r.io.join("Alex", pos=(200.5, 64.0, 0.5), yaw=0.0)                  # facing south
    r.tick()
    iid = build(r, size=11, name="keep")
    got = r.result(iid)
    assert got["facts"]["built"] == 2 and sorted(got["facts"]["places"]) == ["keep_alex", "keep_steve"]
    assert block(r, 205, 66, 11) == "minecraft:stone_bricks"              # Alex's side wall: his faces south
