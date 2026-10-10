"""Tests for the world presets (replace_blocks, shape_fill, summon, sky_above, meteor_fall,
projectile, under_feet, walk_steps, enters_chunk) against the fake RCON server and fake_world,
under the Python EC2 has, with pydantic for a preset's values class:

    uv run --no-project --python 3.12 --with pydantic python scripts/_test/test_presets_world.py

Each preset is loaded from scripts/_presets/ as it is, its function run in this process
against a fresh fake world (test_kit's KitCase): it sends the commands it should, its values
class clamps or refuses values outside its limits, and a script that copies them leaves
nothing behind after a stop.
"""
import importlib.util
import os
import signal
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
PRESETS = os.path.join(os.path.dirname(HERE), "_presets")
sys.path.append(os.path.dirname(os.path.dirname(HERE)))  # the repo: a preset's values class imports actions._types
sys.path.insert(0, HERE)

import test_kit  # noqa: E402
from test_kit import KitCase, collect, copied, start_script, TMP  # noqa: E402
from fake_rcon import FakeRcon  # noqa: E402
from fake_world import World  # noqa: E402
import secrets  # noqa: E402
import mc  # noqa: E402
from kit import values, world, area  # noqa: E402

NAMES = ["replace_blocks", "shape_fill", "summon", "sky_above", "meteor_fall", "projectile",
         "under_feet", "walk_steps", "enters_chunk"]
NETHER = "minecraft:the_nether"


def load(name):
    """The preset as a module, from its file."""
    spec = importlib.util.spec_from_file_location("preset_" + name, os.path.join(PRESETS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = {n: load(n) for n in NAMES}


def vals(name, **given):
    """A preset's values: its defaults, then these, checked as vfg action run checks them."""
    return values(meta=test_kit.values_meta(P[name], given=given), run_dir=tempfile.mkdtemp(dir=TMP), instance="t")


def cells(fills, block=None):
    """Every block the fills covered (with that block), as {(x, y, z): block}."""
    out = {}
    for _, b, blk, _ in fills:
        if block is None or blk == block:
            for x in range(b[0], b[3] + 1):
                for y in range(b[1], b[4] + 1):
                    for z in range(b[2], b[5] + 1):
                        out[(x, y, z)] = blk
    return out


class WorldValuesTests(unittest.TestCase):
    def test_every_preset_clamps_or_refuses_values_outside_its_limits(self):
        for name in NAMES:
            for key in test_kit.values_class(P[name]).model_fields:
                self.assertTrue(key.startswith(name.split("_")[0][:3]), key)
            test_kit.assert_checked_on_the_pc(self, P[name])
        for name, key, bad in (("summon", "summon_mob", "potion"), ("projectile", "projectile_block",
                                                                     "#minecraft:tall_flowers"),
                               ("shape_fill", "shape_offset", "1,up,2"), ("shape_fill", "shape_blocks", "#logs"),
                               ("meteor_fall", "meteor_blocks", "0% magma_block"),
                               ("replace_blocks", "replace_rules", "stone"), ("summon", "summon_weapon", "#swords")):
            with self.assertRaises(ValueError, msg=key):
                test_kit.values_meta(P[name], given={key: bad})


class WorldReplaceBlocksTests(KitCase):
    def setUp(self):
        super(WorldReplaceBlocksTests, self).setUp()
        P["replace_blocks"]._replace_blocks_known.clear()
        P["replace_blocks"]._replace_blocks_said.clear()
        self.rb = P["replace_blocks"].replace_blocks

    def test_one_block_fills_in_big_pieces_and_solid_is_five_tags(self):
        v = vals("replace_blocks", replace_rules="#minecraft:snow > packed_ice,solid > blue_ice")
        n = self.rb(world.Box(0, 60, 0, 40, 70, 40), v)
        modes = sorted(set(m for _, _, _, m in self.world.fills))
        self.assertEqual(modes, sorted(["replace #minecraft:snow strict"] + ["replace %s strict" % t for t in
                                                                            P["replace_blocks"]._REPLACE_BLOCKS_SOLID]))
        self.assertEqual(len([f for f in self.world.fills if f[3] == "replace #minecraft:snow strict"]), 1)  # 18,491
        self.assertEqual(n, 6 * 41 * 11 * 41)
        self.assertFalse([c for c in self.world.commands if "bedrock" in c or "barrier" in c])

    def test_a_mix_picks_per_cell_the_same_way_every_time(self):
        v = vals("replace_blocks", replace_rules="stone > 70% netherrack 30% magma_block", replace_cell=2)
        self.rb(world.Box(0, 60, 0, 7, 63, 7), v)
        first = [(b, blk) for _, b, blk, _ in self.world.fills]
        self.assertEqual(len(first), 4 * 2 * 4)
        self.assertEqual(set(blk for _, blk in first), {"netherrack", "magma_block"})
        self.assertTrue(all(area.volume(b) == 8 for b, _ in first))
        del self.world.fills[:]
        self.rb(world.Box(0, 60, 0, 7, 63, 7), v)
        self.assertEqual([(b, blk) for _, b, blk, _ in self.world.fills], first)

    def test_rules_written_with_commas(self):
        rb = P["replace_blocks"]
        self.assertEqual(rb._replace_blocks_texts(["stone > 70% netherrack", "30% magma_block", "dirt",
                                                   "grass_block > soul_sand"]),
                         ["stone > 70% netherrack, 30% magma_block, dirt", "grass_block > soul_sand"])
        self.assertEqual(rb._replace_blocks_texts(["stone", "deepslate > netherrack"]), ["stone, deepslate > netherrack"])
        text, froms, mix = rb._replace_blocks_rule("stone, deepslate > 70% netherrack, 30%oak_stairs[facing=north,half=top]")
        self.assertEqual(froms, ["stone", "deepslate"])
        self.assertEqual(mix, [("netherrack", 70.0), ("oak_stairs[facing=north,half=top]", 30.0)])
        v = vals("replace_blocks", replace_rules="stone > 70% netherrack, 30% magma_block", replace_cell=4)
        self.rb(world.Box(0, 60, 0, 7, 63, 7), v)
        self.assertEqual(set(blk for _, _, blk, _ in self.world.fills), {"netherrack", "magma_block"})
        self.assertEqual(P["shape_fill"]._shape_fill_mix("70% stone_bricks, 30%mossy_stone_bricks"),
                         [("stone_bricks", 70.0), ("mossy_stone_bricks", 30.0)])

    def test_rate_and_keep_leave_some_cells_alone(self):
        v = vals("replace_blocks", replace_rules="stone > netherrack", replace_cell=1, replace_rate=30)
        self.rb(world.Box(0, 60, 0, 9, 69, 9), v)
        self.assertTrue(200 < len(self.world.fills) < 400, len(self.world.fills))
        del self.world.fills[:]
        v = vals("replace_blocks", replace_rules="stone > 50% keep 50% gold_block", replace_cell=1)
        self.rb(world.Box(0, 60, 0, 9, 69, 9), v)
        self.assertTrue(350 < len(self.world.fills) < 650, len(self.world.fills))
        self.assertEqual(set(blk for _, _, blk, _ in self.world.fills), {"gold_block"})

    def test_a_place_and_a_path_in_another_dimension(self):
        v = vals("replace_blocks", replace_rules="netherrack > packed_ice", replace_radius=2)
        self.rb(world.Pos(0.5, 64, 0.5, NETHER), v)
        self.assertEqual([(d, b) for d, b, _, _ in self.world.fills], [(NETHER, (-2, 62, -2, 2, 66, 2))])
        self.assertTrue(self.sent("execute in minecraft:the_nether run fill"))
        del self.world.fills[:]
        self.rb([world.Pos(x, 64, 0.5, NETHER) for x in (0.5, 3.5, 6.5)], v)
        self.assertEqual(len(self.world.fills), 3)

    def test_a_rule_the_server_refuses_is_logged_and_left_out(self):
        real = self.world.respond

        def respond(cmd):
            if "#minecraft:no_such_tag" in cmd:
                return "Unknown block tag 'minecraft:no_such_tag'...<--[HERE]"
            return real(cmd)
        self.world.respond = respond
        # A tag the PC takes (it cannot know every tag): the server refuses it.
        v = vals("replace_blocks", replace_rules="#minecraft:no_such_tag > air,dirt > coarse_dirt", replace_radius=1)
        self.assertIsNone(P["replace_blocks"]._replace_blocks_rule("stone"))       # no >: not a rule
        self.rb((0, 64, 0), v)
        self.assertEqual(set(m for _, _, _, m in self.world.fills), {"replace dirt strict"})
        self.assertEqual(len(self.sent("execute if block 0 0 0 ")), 3)    # asked once each
        self.rb((0, 64, 0), v)
        self.assertEqual(len(self.sent("execute if block 0 0 0 ")), 3)

    def test_a_player_left_inside_a_block_is_lifted(self):
        v = vals("replace_blocks", replace_rules="air > stone")
        self.rb(world.Box(8, 64, -6, 12, 66, -2), v)
        lifts = self.sent("#minecraft:blocks_motion run tp @s ~ ~1 ~")
        self.assertEqual(len(lifts), 2)
        self.assertIn("as @a[x=8,y=62,z=-6,dx=4,dy=4,dz=4] at @s if block ~ ~0.5 ~", lifts[0])


class WorldUnderFeetTests(KitCase):
    def test_the_box_under_a_place_and_a_player(self):
        uf = P["under_feet"].under_feet
        b = uf(world.Pos(10.5, 64.0, -3.5), vals("under_feet"))
        self.assertEqual((tuple(b), b.dim), ((8, 63, -6, 12, 63, -2), None))
        b = uf(world.Pos(10.5, 63.5, -3.5, NETHER), vals("under_feet", under_radius=0, under_depth=3, under_above=2))
        self.assertEqual((tuple(b), b.dim), ((10, 61, -4, 10, 65, -4), NETHER))
        self.assertEqual(tuple(uf("Steve", vals("under_feet", under_radius=1))), (9, 63, -5, 11, 63, -3))
        self.assertIsNone(uf("Nobody", vals("under_feet")))


class WorldWalkStepsTests(KitCase):
    def test_a_step_each_block_walked_and_once_on_arrival(self):
        got, _ = collect(P["walk_steps"].walk_steps("@a", vals("walk_steps")), 5)
        time.sleep(0.4)
        steve = self.world.players["Steve"]
        steve["pos"][0] += 0.5
        time.sleep(0.4)
        steve["pos"][0] += 0.7
        time.sleep(0.4)
        steve["pos"][1] += 1.0
        time.sleep(0.4)
        steve["dim"] = NETHER
        time.sleep(2.3)                 # the kit reads a player's dimension at most every 2 s
        at = [x for (p, x), _ in got]
        self.assertEqual([p for (p, _), _ in got], ["Steve"] * 4)
        self.assertEqual([round(a[0], 1) for a in at], [10.5, 11.7, 11.7, 11.7])
        self.assertEqual([a.dim for a in at], [None, None, None, NETHER])
        self.assertTrue(self.sent("execute as Steve if entity @s[nbt={OnGround:1b}] run data get entity @s Pos"))


class WorldEntersChunkTests(KitCase):
    def test_each_chunk_once_per_dimension(self):
        got, _ = collect(P["enters_chunk"].enters_chunk("@a", vals("enters_chunk", enters_dims="overworld")), 4)
        steve = self.world.players["Steve"]
        time.sleep(0.7)
        steve["pos"][0] = 14.5          # the same chunk
        time.sleep(0.7)
        steve["pos"][0] = 17.5          # the next one
        time.sleep(0.7)
        steve["pos"][0] = 3.5           # back
        time.sleep(0.7)
        steve["dim"] = NETHER           # not in enters_dims
        time.sleep(0.7)
        boxes = [b for (_, b), _ in got]
        self.assertEqual([tuple(b) for b in boxes], [(0, 32, -16, 15, 104, -1), (16, 32, -16, 31, 104, -1)])
        self.assertEqual([b.dim for b in boxes], [None, None])


class WorldShapeFillTests(KitCase):
    def setUp(self):
        super(WorldShapeFillTests, self).setUp()
        self.sf = P["shape_fill"].shape_fill

    def test_a_box_in_front_facing_south_and_east(self):
        v = vals("shape_fill", shape_width=5, shape_height=3, shape_depth=7, shape_offset="0,0,10",
                 shape_facing="south")
        whole = self.sf(world.Pos(0.5, 64, 0.5), v)
        self.assertEqual(tuple(whole), (-2, 64, 7, 2, 66, 13))
        self.assertEqual(len(cells(self.world.fills, "stone_bricks")), 5 * 3 * 7)
        del self.world.fills[:]
        whole = self.sf(world.Pos(0.5, 64, 0.5), v, facing="east", offset=[2, 1, 10])
        self.assertEqual(tuple(whole), (7, 65, 0, 13, 67, 4))

    def test_a_player_and_look(self):
        whole = self.sf("Steve", vals("shape_fill", shape_kind="wall", shape_width=9, shape_height=4,
                                      shape_offset="0,0,3"))
        self.assertEqual(tuple(whole), (13, 64, -8, 13, 67, 0))       # Steve looks east (yaw -90)
        self.assertEqual(whole.facing, "east")

    def test_a_value_no_one_gives_is_the_presets_default(self):
        v = values(meta={"values": {"shape_blocks": "stone_bricks"}, "live": []}, run_dir=tempfile.mkdtemp(dir=TMP),
                   instance="t")
        whole = self.sf(world.Pos(0.5, 70, 0.5), v, kind="box", width=3)
        self.assertEqual(tuple(whole), (-1, 70, -4, 1, 74, 4))         # depth 9, height 5: the defaults
        self.assertEqual(whole.facing, "south")

    def test_hollow_leaves_air_inside_and_the_ice_castle_wall_fits(self):
        whole = self.sf(world.Pos(0.5, 59, 0.5), vals("shape_fill", shape_width=61, shape_height=12,
                                                       shape_depth=61, shape_blocks="packed_ice",
                                                       shape_hollow="true", shape_facing="north"))
        self.assertEqual(area.volume(whole), 44652)
        ice, air = cells(self.world.fills, "packed_ice"), cells(self.world.fills, "air")
        self.assertEqual(len(air), 59 * 10 * 59)
        self.assertEqual(len(ice), 44652 - 59 * 10 * 59)
        self.assertTrue(all(area.volume(b) <= 32768 for _, b, _, _ in self.world.fills))

    def test_round_shapes_and_stairs(self):
        for kind, n in (("cylinder", 69 * 4), ("dome", None), ("pyramid", 9 * 9 + 7 * 7 + 5 * 5 + 3 * 3),
                        ("staircase", 3 * (1 + 2 + 3 + 4))):
            del self.world.fills[:]
            self.sf(world.Pos(0.5, 70, 0.5), vals("shape_fill", shape_kind=kind, shape_width=9 if kind != "staircase"
                                                   else 3, shape_depth=9 if kind != "staircase" else 4,
                                                   shape_height=4))
            got = cells(self.world.fills)
            if n:
                self.assertEqual(len(got), n, kind)
            else:
                self.assertTrue(150 < len(got) < 9 * 9 * 4, (kind, len(got)))

    def test_a_mix_and_air_carving(self):
        self.sf(world.Pos(0.5, 70, 0.5), vals("shape_fill", shape_blocks="70% stone_bricks 30% mossy_stone_bricks",
                                              shape_width=8, shape_depth=8, shape_height=4))
        self.assertEqual(set(b for _, _, b, _ in self.world.fills), {"stone_bricks", "mossy_stone_bricks"})
        self.assertEqual(len(cells(self.world.fills)), 8 * 8 * 4)
        self.assertEqual(len(self.world.fills), 4 * 4 * 2)
        del self.world.fills[:]
        self.sf(world.Pos(0.5, 70, 0.5), vals("shape_fill", shape_blocks="air"))
        self.assertEqual(set(b for _, _, b, _ in self.world.fills), {"air"})

    def test_chunks_not_loaded_are_loaded_for_the_build_and_let_go(self):
        self.world.unloaded.update({("minecraft:overworld", cx, cz) for cx in range(4, 7) for cz in range(4, 7)})
        self.sf(world.Pos(80.5, 64, 80.5), vals("shape_fill", shape_width=21, shape_depth=21))
        self.assertEqual(len(cells(self.world.fills)), 21 * 21 * 5)
        self.assertTrue(self.sent("forceload add"))
        self.assertEqual(self.world.forced, set())


class WorldSummonTests(KitCase):
    def test_round_a_player_armed_named_and_capped(self):
        v = vals("summon", summon_mob="zombie", summon_count=3, summon_cap=4, summon_gear="iron",
                 summon_weapon="iron_sword", summon_name="Horde King", summon_health=800, summon_scale=3)
        self.assertEqual(P["summon"].summon("Steve", v), 3)
        self.assertEqual(P["summon"].summon("@a", v), 1)
        self.assertEqual(P["summon"].summon("Steve", v), 0)
        made = [e for e in self.world.entities if mc.TAG + "_summon" in e["tags"]]
        self.assertEqual(len(made), 4)
        self.assertTrue(all({"vfg", mc.TAG} <= e["tags"] for e in made))
        cmd = self.sent(" summon zombie ")[0]
        for part in ('CustomName:"Horde King"', '{id:"max_health",base:800}', '{id:"scale",base:3}',
                     'head:{id:"iron_helmet",count:1}', 'mainhand:{id:"iron_sword",count:1}', "mainhand:0f",
                     "Health:800f"):
            self.assertIn(part, cmd)

    def test_round_a_place_in_another_dimension(self):
        got = P["summon"].summon(world.Pos(0.5, 64, 0.5, NETHER), vals("summon", summon_count=2, summon_mob="blaze"))
        self.assertEqual(got, 2)
        self.assertTrue(self.sent("execute in minecraft:the_nether positioned 0.50 64.00 0.50 "))
        self.assertFalse(self.sent("summon marker"))            # the place goes to mobs.around as it is
        self.assertEqual([e["type"] for e in self.world.entities], ["blaze", "blaze"])
        self.assertTrue(all(e["dim"] == NETHER for e in self.world.entities))


class WorldSkyAboveTests(KitCase):
    def test_points_in_open_air_round_players_and_a_place(self):
        v = vals("sky_above", sky_count=20, sky_radius=10, sky_height=20, sky_band=2)
        pts = P["sky_above"].sky_above("@a", v)
        self.assertEqual(len(pts), 20)
        for p in pts:
            self.assertLessEqual(((p[0] - 10.5) ** 2 + (p[2] + 3.5) ** 2) ** 0.5, 10.0001)
            self.assertTrue(82 <= p[1] <= 86)
        self.world.unloaded.add(("minecraft:overworld", 0, 0))
        self.world.solid.update(("minecraft:overworld", x, 318, z) for x in range(-48, 0) for z in range(-48, 48))
        pts = P["sky_above"].sky_above(world.Pos(8, 300, 8), vals("sky_above", sky_count=60, sky_radius=40,
                                                                    sky_height=40))
        self.assertTrue(0 < len(pts) < 60, len(pts))
        for p in pts:
            self.assertEqual(p.block()[1], 318)                       # under the top of the world
            self.assertGreaterEqual(p[0], 0)                          # not in the solid half
            self.assertFalse(0 <= p[0] < 16 and 0 <= p[2] < 16)       # nor the chunk that is not loaded


class WorldShowTests(KitCase):
    def test_a_meteor_falls_onto_the_ground_and_leaves_nothing(self):
        land = P["meteor_fall"].meteor_fall(world.Pos(0.5, 75, 0.5),
                                            vals("meteor_fall", meteor_seconds=0.5, meteor_blocks="obsidian"))
        self.assertEqual((tuple(land), land.dim), ((0.5, 64.0, 0.5), None))
        self.assertEqual(len(self.sent("summon block_display")), 19)
        self.assertEqual(len(self.sent("run tp @s ~")), 5)
        self.assertTrue(all('block_state:"minecraft:obsidian"' in c for c in self.sent("summon block_display")))
        self.assertEqual([e for e in self.world.entities if e["type"] == "block_display"], [])

    def test_a_projectile_flies_from_a_player_and_lands(self):
        end = world.Pos(40.5, 65, -3.5)
        got = P["projectile"].projectile("Steve", end, vals("projectile", projectile_speed=100))
        self.assertEqual(got, end)
        self.assertTrue(len(self.sent("particle end_rod")) >= 5 * 3)
        got = P["projectile"].projectile(world.Pos(0, 70, 0, NETHER), (20, 70, 0),
                                         vals("projectile", projectile_kind="block", projectile_speed=100))
        self.assertEqual(got.dim, NETHER)
        self.assertEqual(len(self.sent("summon block_display")), 1)
        self.assertEqual(len(self.sent("execute in minecraft:the_nether run tp @e[type=block_display")), 2)
        self.assertEqual([e for e in self.world.entities if e["type"] == "block_display"], [])


PROBE = '''"""Probe: the world presets mid-way when it is stopped."""
ASKS = []
import mc
from kit import values, loop
COPIED = %r
for name in ("shape_fill", "summon", "meteor_fall"):
    g = {"__name__": name}
    exec(COPIED[name], g)
    globals()[name] = g[name]
v = values()
summon("Steve", v)
loop.background(meteor_fall, (0.5, 70, 0.5), v)
loop.background(shape_fill, (100.5, 64, 100.5), v)
mc.log("ready")
for n in loop(every=0.2):
    pass
'''


class WorldStopTests(unittest.TestCase):
    def test_nothing_is_left_after_a_stop(self):
        w = World()
        w.add_player("Steve", (10.5, 64.0, -3.5), rot=(-90.0, 0.0))
        w.unloaded.update({("minecraft:overworld", cx, cz) for cx in range(5, 8) for cz in range(5, 8)})
        w.mspt = 80.0       # so the build waits, with its chunks forceloaded, when the stop comes
        password = secrets.token_hex(8)
        fake = FakeRcon(password, responder=w.respond).start()
        try:
            copies = {n: copied(os.path.join(PRESETS, n + ".py")) for n in ("shape_fill", "summon", "meteor_fall")}
            proc = start_script("probe_world", PROBE % (copies,), fake, password,
                                vals=test_kit.values_meta("shape_fill", "summon", "meteor_fall"))
            deadline = time.time() + 8
            while not (w.forced and [e for e in w.entities if e["type"] == "block_display"]) \
                    and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(w.forced)
            self.assertEqual(len([e for e in w.entities if "vfg_probe_world_summon" in e["tags"]]), 5)
            t = time.time()
            proc.send_signal(signal.SIGTERM)
            out = proc.communicate(timeout=4)[0].decode()
            self.assertLess(time.time() - t, 2.0)
            self.assertEqual(proc.returncode, 0, out)
            self.assertNotIn("Traceback", out)
            self.assertEqual([e for e in w.entities if any(x.startswith("vfg_probe_world") for x in e["tags"])], [])
            self.assertEqual(w.forced, set())
        finally:
            fake.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
