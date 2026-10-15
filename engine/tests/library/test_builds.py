"""The builds group on FakeIo: sniper_nest, sky_platform, mountains (with mountain_trees merged in
as its `trees` arg), world_replace (with hellscape merged in as rules="hellscape"). Each a build
through the block queue, ending with a result once the last block is down; args refused in words;
nobody online."""

from vfg_engine.io import Forceload, SetBlock, Spawnpoint, Teleport

OW = "minecraft:overworld"


def block(r, x, y, z):
    return r.io.block_at(OW, x, y, z)


def run_to_end(r, iid, ticks=600):
    """Tick until the instance ends (a build spread over the block queue); a final tick flushes
    whatever its last call (done/finish) queued, such as a teleport or a spawnpoint."""
    for _ in range(ticks):
        r.tick()
        if not r.running(iid):
            break
    assert not r.running(iid), "still running after %d ticks" % ticks
    return iid


def steve(r, pos=(0.5, 64.0, 0.5), yaw=180.0):
    r.io.join("Steve", pos=pos, yaw=yaw)     # on FakeIo's stone, facing north (-z) by default
    r.tick()


def test_it_loads(rig):
    r = rig("sniper_nest", "sky_platform", "mountains", "world_replace")
    assert sorted(r.start()["loaded"]) == ["mountains", "sky_platform", "sniper_nest", "world_replace"]


# ---- sniper_nest ---------------------------------------------------------------------------

def test_a_nest_in_front_of_him_its_door_facing_him(rig):
    r = rig("sniper_nest")
    r.start()
    steve(r)
    iid = run_to_end(r, r.start_scenario("sniper_nest", players="Steve", sign=["{player}'s Nest"]))
    got = r.result(iid)
    assert got["summary"].startswith("built 1 sniper nest: Steve's at 0 78 -7")
    assert got["summary"].endswith("spawn set there")
    assert got["reason"] == "ended"
    assert block(r, 2, 65, -7) == "minecraft:stone_bricks"            # the tower's right wall
    assert block(r, 0, 65, -9) == "minecraft:stone_bricks"            # its far wall
    assert block(r, 0, 65, -5) == "minecraft:air"                     # the door, facing Steve
    assert block(r, 0, 64, -8) == "minecraft:ladder[facing=south]"    # up the back wall
    assert block(r, 0, 55, -7) == "minecraft:stone_bricks"            # the footing, so it never floats
    assert block(r, 2, 80, -7) == "minecraft:stone_bricks"            # the lookout's walls
    (sign,) = [op for op in r.sent(SetBlock) if "sign" in op.block]
    assert sign.pos == (0, 66, -4) and sign.block.startswith("minecraft:oak_wall_sign[facing=south]")
    assert "Steve's Nest" in sign.block
    assert r.io.spawnpoints["Steve"] == (OW, (0, 78, -7))
    assert r.problems() == []


def test_a_nest_at_a_place_for_everyone(rig):
    r = rig("sniper_nest")
    r.start()
    steve(r)
    r.io.join("Alex", pos=(50.5, 64.0, 0.5), yaw=0.0)
    r.tick()
    iid = run_to_end(r, r.start_scenario("sniper_nest", at="200 64 300", height=10, spawn=False))
    got = r.result(iid)
    assert got["summary"].startswith("built 1 sniper nest: Steve's at")
    assert "spawn set" not in got["summary"]
    assert "Steve" not in r.io.spawnpoints and "Alex" not in r.io.spawnpoints


def test_nobody_online_nest(rig):
    r = rig("sniper_nest")
    r.start()
    iid = run_to_end(r, r.start_scenario("sniper_nest"))
    assert r.result(iid)["summary"] == "no sniper nest: nobody is online"
    assert r.result(iid)["facts"]["nests"] == 0


def test_args_refused_in_words_nest(rig):
    r = rig("sniper_nest")
    r.start()
    reply = r.op("start", scenario="sniper_nest", args={"height": 500})
    assert not reply["ok"] and "height" in reply["error"]
    reply = r.op("start", scenario="sniper_nest", args={"sign": ["a", "b", "c", "d", "e"]})
    assert not reply["ok"] and "sign" in reply["error"]


# ---- sky_platform ---------------------------------------------------------------------------

def test_a_platform_over_him_put_on_it_spawn_set(rig):
    r = rig("sky_platform")
    r.start()
    steve(r, yaw=0.0)
    iid = run_to_end(r, r.start_scenario("sky_platform", players="Steve", size=5, y=100))
    got = r.result(iid)
    assert got["summary"] == "Steve put on a sky platform at 0 101 0, spawn set there"
    assert block(r, 0, 100, 0) == "minecraft:obsidian"                 # the floor
    assert block(r, -2, 101, 0) == "minecraft:obsidian"                # the rim
    assert block(r, -1, 101, 0) == "minecraft:air"                     # carved back out, inside the rim
    assert block(r, -2, 101, -2) == "minecraft:glowstone"              # a corner
    (tp,) = r.sent(Teleport)
    assert tp.target == "Steve" and tp.pos == (0.5, 101.0, 0.5) and tp.yaw is None   # facing as he was
    (sp,) = r.sent(Spawnpoint)
    assert sp.player == "Steve" and sp.pos == (0, 101, 0)
    assert r.problems() == []


def test_a_mix_and_no_tp(rig):
    r = rig("sky_platform")
    r.start()
    steve(r)
    iid = run_to_end(r, r.start_scenario("sky_platform", players="Steve", size=5, y=90,
                                         blocks="50% obsidian 50% crying_obsidian"))
    mix = {block(r, x, 90, 0) for x in range(-2, 3)}
    assert mix <= {"minecraft:obsidian", "minecraft:crying_obsidian"}


def test_one_platform_at_a_place_for_two(rig):
    r = rig("sky_platform")
    r.start()
    steve(r)
    r.io.join("Alex", pos=(50.5, 64.0, 0.5))
    r.tick()
    iid = run_to_end(r, r.start_scenario("sky_platform", at="500 64 500", size=5, y=120))
    got = r.result(iid)
    assert got["facts"]["players"] == ["Steve", "Alex"]
    assert len(r.sent(Teleport)) == 2
    assert len(r.sent(Spawnpoint)) == 2
    assert {tp.pos for tp in r.sent(Teleport)} == {(500.5, 121.0, 500.5)}     # the same platform for both
    assert {sp.pos for sp in r.sent(Spawnpoint)} == {(500, 121, 500)}


def test_nobody_online_platform(rig):
    r = rig("sky_platform")
    r.start()
    iid = run_to_end(r, r.start_scenario("sky_platform"))
    assert r.result(iid)["summary"] == "no platform: nobody is online"


# ---- world_replace (hellscape merged in) -----------------------------------------------------

def test_a_simple_rule_round_a_player(rig):
    r = rig("world_replace")
    r.start()
    steve(r)
    r.io.set_block(OW, (5, 63, 5), "minecraft:obsidian")
    r.io.set_block(OW, (40, 63, 40), "minecraft:obsidian")   # outside the radius: untouched
    iid = run_to_end(r, r.start_scenario("world_replace", players="Steve", rules="obsidian > air", radius=10,
                                         height=2, dims=["overworld"]))
    got = r.result(iid)
    assert got["summary"] == "round Steve, 10 each way: obsidian > air"
    assert block(r, 5, 63, 5) == "minecraft:air"
    assert block(r, 40, 63, 40) == "minecraft:obsidian"
    assert r.problems() == []


def test_hellscape_turns_the_land(rig):
    r = rig("world_replace")
    r.start()
    steve(r)
    r.io.block_tags["minecraft:base_stone_overworld"] = {"minecraft:stone"}
    r.io.block_tags["minecraft:dirt"] = {"minecraft:dirt"}
    r.io.block_tags["minecraft:snow"] = set()
    r.io.set_block(OW, (3, 63, 3), "minecraft:water")
    r.io.set_block(OW, (4, 63, 3), "minecraft:dirt")
    iid = run_to_end(r, r.start_scenario("world_replace", players="Steve", rules="hellscape", radius=10, height=3))
    got = r.result(iid)
    assert got["summary"] == "the land round Steve turned to the Nether, 10 each way"
    assert got["facts"]["rules"] == "hellscape"
    assert block(r, 3, 63, 3) == "minecraft:lava"                       # water > lava
    # dirt > netherrack, then the last rule may re-roll a cell's netherrack into a magma/blackstone patch
    assert block(r, 4, 63, 3) in ("minecraft:netherrack", "minecraft:magma_block", "minecraft:blackstone")
    assert block(r, 0, 63, 0) in ("minecraft:netherrack", "minecraft:magma_block", "minecraft:blackstone")


def test_a_rate_under_100_keeps_some_cells(rig):
    r = rig("world_replace")
    r.start()
    steve(r)
    for x in range(0, 16):
        r.io.set_block(OW, (x, 63, 0), "minecraft:obsidian")
    iid = run_to_end(r, r.start_scenario("world_replace", players="Steve", rules="obsidian > air", radius=16,
                                         height=1, rate=50))
    kept = sum(1 for x in range(0, 16) if block(r, x, 63, 0) == "minecraft:obsidian")
    assert 0 < kept < 16          # some cells kept their obsidian, some did not: a rate, not all-or-nothing


def test_nobody_online_replace(rig):
    r = rig("world_replace")
    r.start()
    iid = run_to_end(r, r.start_scenario("world_replace", players="Steve"))
    assert r.result(iid)["summary"] == "nothing changed: Steve is not online"


def test_args_refused_in_words_replace(rig):
    r = rig("world_replace")
    r.start()
    reply = r.op("start", scenario="world_replace", args={"radius": 10000})
    assert not reply["ok"] and "radius" in reply["error"]
    reply = r.op("start", scenario="world_replace", args={"rules": "not a rule at all"})
    assert not reply["ok"]


# ---- mountains (mountain_trees merged in as its trees arg) -------------------------------------

def _query_reply(c):
    if "place feature" in c:
        return "Placed feature minecraft:spruce"
    if "fillbiome" in c:
        return "Set biome"
    return ""


def test_a_range_beside_him_terrain_caves_trees_biomes(rig):
    r = rig("mountains")
    r.start()
    steve(r)
    r.io.query_reply = _query_reply
    iid = r.start_scenario("mountains", players="Steve", away=0, seed=1, trees=5)
    run_to_end(r, iid, ticks=3000)
    got = r.result(iid)
    assert got["summary"].startswith("mountain range built over x 0..200, z -50..50: its highest peak at y ")
    assert got["facts"]["trees"] == 5
    assert got["facts"]["seed"] == 1
    assert got["facts"]["size"] == [200, 100]
    place_queries = [q for q in r.io.queries if "place feature" in q]
    biome_queries = [q for q in r.io.queries if "fillbiome" in q]
    assert len(place_queries) == 5
    assert biome_queries                                              # at least one biome band was asked for
    assert any(("minecraft:jagged_peaks" in q or "minecraft:snowy_slopes" in q or "minecraft:grove" in q)
              for q in biome_queries)
    assert r.sent(Forceload)
    assert r.problems() == []


def test_a_range_at_a_corner(rig):
    r = rig("mountains")
    r.start()
    r.io.query_reply = _query_reply
    iid = r.start_scenario("mountains", corner="1000 64 2000", seed=2, trees=0)
    run_to_end(r, iid, ticks=3000)
    got = r.result(iid)
    assert got["facts"]["corner"] == "1000 2000"
    assert got["facts"]["trees"] == 0
    assert not [q for q in r.io.queries if "place feature" in q]       # trees 0: none asked for


def test_nobody_online_mountains(rig):
    r = rig("mountains")
    r.start()
    iid = run_to_end(r, r.start_scenario("mountains"))
    assert r.result(iid)["summary"] == "no mountains: nobody is online"


def test_args_refused_in_words_mountains(rig):
    r = rig("mountains")
    r.start()
    reply = r.op("start", scenario="mountains", args={"trees": 5000})
    assert not reply["ok"] and "trees" in reply["error"]
    reply = r.op("start", scenario="mountains", args={"seed": -1})
    assert not reply["ok"] and "seed" in reply["error"]
