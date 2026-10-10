"""The world presets against a real Minecraft 26.3 server: replace_blocks, shape_fill, summon,
sky_above, meteor_fall, projectile, under_feet, walk_steps and enters_chunk.

    integration/realmc/up pw && eval "$(integration/realmc/env pw)"
    uv run --python 3.9 python scripts/_test/live_presets_world.py

It changes blocks, so it runs only against a realmc test server, never the box. What it builds
is on the arena or at least 40 blocks above it, and is taken away again; the mobs it summons
are killed and its forceloads let go. walk_steps and enters_chunk need a Carpet fake player
(the server started with Carpet: integration/realmc/realmc.py carpet, then up --fresh);
without one those tests are skipped and say so.
"""
import importlib.util
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), "_lib")
PRESETS = os.path.join(os.path.dirname(HERE), "_presets")
sys.path.append(os.path.dirname(os.path.dirname(HERE)))  # the repo: a preset's values class imports actions._types
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, LIB)

PROPS = os.environ.get("VFG_MC_PROPERTIES", "")
if not os.environ.get("REALMC_NAME") or "realmc" not in os.path.realpath(PROPS):
    sys.exit("live_presets_world: point it at a realmc server first: eval \"$(integration/realmc/env pw)\"")

import mc  # noqa: E402
from kit import aim, area, loop, values, world  # noqa: E402

signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)

NETHER = "minecraft:the_nether"
NAMES = ["replace_blocks", "shape_fill", "summon", "sky_above", "meteor_fall", "projectile",
         "under_feet", "walk_steps", "enters_chunk"]


def load(name):
    spec = importlib.util.spec_from_file_location("preset_" + name, os.path.join(PRESETS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = {n: load(n) for n in NAMES}
TMP = tempfile.mkdtemp(prefix="vfg-live-presets-")


def vals(name, **given):
    return values(P[name].VALUES, argv=["%s=%s" % kv for kv in given.items()], run_dir=TMP, instance="t")


def find_floor():
    """The arena: the middle of the chunks forceloaded for good, and the floor under it."""
    pairs = [(int(x), int(z)) for x, z in re.findall(r"\[(-?\d+), (-?\d+)\]", mc.rcon("forceload query"))]
    cx = sorted(p[0] for p in pairs)[len(pairs) // 2] if pairs else 0
    cz = sorted(p[1] for p in pairs)[len(pairs) // 2] if pairs else 0
    x, z = cx * 16, cz * 16
    r = aim.cast((x + 0.5, 300.0, z + 0.5), (0.0, -1.0, 0.0), 400)
    assert r.why == "block", "no floor under %d %d: %s" % (x, z, r.why)
    return int(x), int(r.hit_block[1]), int(z)


X, FLOOR, Z = find_floor()
SKY = FLOOR + 40
FORCED = mc.rcon("forceload query")


def block_is(x, y, z, block, dim=None):
    return "passed" in mc.rcon(world.prefix(dim) + "if block %d %d %d %s" % (x, y, z, block)).lower()


def same(a, b, dim=None):
    """True when box a holds exactly what box b does (execute if blocks ... all)."""
    return "passed" in mc.rcon(world.prefix(dim) + "if blocks %d %d %d %d %d %d %d %d %d all"
                               % (a[0], a[1], a[2], a[3], a[4], a[5], b[0], b[1], b[2])).lower()


def count(selector):
    m = re.search(r"count: (\d+)", mc.rcon("execute if entity %s" % selector), re.I)
    return int(m.group(1)) if m else 0


class Live(unittest.TestCase):
    def tearDown(self):
        mc.rcon("kill @e[tag=%s]" % mc.TAG)
        area.release()
        mc.rcon("fill %d %d %d %d %d %d air" % (X - 15, SKY - 2, Z - 15, X + 15, SKY + 14, Z + 15))


class LiveReplaceBlocks(Live):
    def setUp(self):
        P["replace_blocks"]._replace_blocks_known.clear()

    def test_one_block_and_its_twin(self):
        box, twin = (X, SKY, Z, X + 7, SKY + 2, Z + 7), (X - 10, SKY, Z, X - 3, SKY + 2, Z + 7)
        for b, low in ((box, "snow_block"), (twin, "packed_ice")):
            area.fill(b, low)
            area.fill((b[0], SKY + 1, b[2], b[3], SKY + 2, b[5]), "stone")
            mc.rcon("setblock %d %d %d powder_snow" % (b[0] + 3, SKY + 2, b[2] + 3))
        mc.rcon("setblock %d %d %d packed_ice" % (twin[0] + 3, SKY + 2, twin[2] + 3))
        n = P["replace_blocks"].replace_blocks(world.Box(*box), vals("replace_blocks",
                                                                     replace_rules="#minecraft:snow > packed_ice"))
        self.assertEqual(n, 8 * 8 + 1)
        self.assertTrue(same(box, twin))

    def test_solid_to_a_mix_the_same_way_twice_glass_and_air_left_alone(self):
        box = (X, SKY, Z, X + 5, SKY + 1, Z + 5)
        v = vals("replace_blocks", replace_rules="solid > 50% gold_block 50% diamond_block", replace_cell=1)
        picks = []
        for _ in range(2):
            area.fill(box, "stone")
            area.fill((X, SKY + 1, Z, X + 5, SKY + 1, Z + 5), "oak_planks")
            mc.rcon("setblock %d %d %d glass" % (X + 2, SKY, Z + 2))
            mc.rcon("setblock %d %d %d air" % (X + 3, SKY + 1, Z + 3))
            P["replace_blocks"].replace_blocks(world.Box(*box), v)
            cells = [(x, y, z) for x in range(X, X + 6) for y in (SKY, SKY + 1) for z in range(Z, Z + 6)]
            gold = world.passes(["if block %d %d %d gold_block" % c for c in cells])
            diamond = world.passes(["if block %d %d %d diamond_block" % c for c in cells])
            picks.append(gold)
            left = [c for c, g, d in zip(cells, gold, diamond) if not g and not d]
            self.assertEqual(sorted(left), [(X + 2, SKY, Z + 2), (X + 3, SKY + 1, Z + 3)])
            self.assertTrue(10 < sum(gold) < 60, sum(gold))
        self.assertEqual(picks[0], picks[1])
        self.assertTrue(block_is(X + 2, SKY, Z + 2, "glass"))

    def test_a_tag_26_3_dropped_is_refused_and_nothing_changes(self):
        area.fill((X, SKY, Z, X + 3, SKY, Z + 3), "sunflower")
        n = P["replace_blocks"].replace_blocks((X, SKY, Z, X + 3, SKY, Z + 3), vals(
            "replace_blocks", replace_rules="#minecraft:tall_flowers > air"))
        self.assertEqual(n, 0)
        self.assertFalse(P["replace_blocks"]._replace_blocks_known["#minecraft:tall_flowers"])

    def test_under_feet_on_the_arena_floor_and_back(self):
        at = world.Pos(X + 0.5, FLOOR + 1, Z + 0.5)
        b = P["under_feet"].under_feet(at, vals("under_feet", under_radius=2, under_depth=2))
        self.assertEqual(tuple(b), (X - 2, FLOOR - 1, Z - 2, X + 2, FLOOR, Z + 2))
        self.assertIn("cloned", mc.rcon("clone %d %d %d %d %d %d %d %d %d" % (b + (X - 2, SKY + 10, Z - 2))).lower())
        try:
            area.fill(b, "dirt")
            area.fill((X - 2, FLOOR, Z - 2, X + 2, FLOOR, Z + 2), "grass_block")
            n = P["replace_blocks"].replace_blocks(b, vals("replace_blocks", replace_rules="solid > packed_ice"))
            self.assertEqual(n, 5 * 5 * 2)
            self.assertTrue(all(block_is(X + dx, FLOOR - dy, Z + dz, "packed_ice")
                                for dx in (-2, 0, 2) for dz in (-2, 0, 2) for dy in (0, 1)))
            self.assertFalse(block_is(X, FLOOR - 2, Z, "packed_ice"))
            self.assertFalse(block_is(X + 3, FLOOR, Z, "packed_ice"))
        finally:
            mc.rcon("clone %d %d %d %d %d %d %d %d %d" % (X - 2, SKY + 10, Z - 2, X + 2, SKY + 11, Z + 2,
                                                         X - 2, FLOOR - 1, Z - 2))

    def test_a_hellscape(self):
        box = (X, SKY, Z, X + 7, SKY + 3, Z + 7)
        area.fill(box, "stone")
        area.fill((X, SKY + 1, Z, X + 7, SKY + 1, Z + 7), "dirt")
        area.fill((X, SKY + 2, Z, X + 7, SKY + 2, Z + 7), "grass_block")
        area.fill((X - 1, SKY + 3, Z - 1, X + 8, SKY + 3, Z + 8), "glass")     # a rim, so the water stays put
        area.fill((X, SKY + 3, Z, X + 3, SKY + 3, Z + 7), "oak_leaves")
        area.fill((X + 4, SKY + 3, Z, X + 7, SKY + 3, Z + 7), "water")
        rules = ["grass_block dirt > netherrack", "#minecraft:base_stone_overworld > 70% netherrack 30% magma_block",
                 "#minecraft:leaves > air", "water > lava"]
        v = values(P["replace_blocks"].VALUES, argv=['replace_rules=%s' % __import__("json").dumps(rules)],
                   run_dir=TMP, instance="t")
        n = P["replace_blocks"].replace_blocks(world.Box(*box), v)
        self.assertEqual(n, 8 * 8 * 4)
        self.assertTrue(block_is(X, SKY + 2, Z, "netherrack") and block_is(X + 7, SKY + 1, Z + 7, "netherrack"))
        low = world.passes(["if block %d %d %d netherrack" % (x, SKY, z) for x in range(X, X + 8) for z in range(Z, Z + 8)])
        self.assertTrue(16 < sum(low) < 64, sum(low))
        self.assertTrue(block_is(X + 1, SKY + 3, Z + 1, "air") and block_is(X + 5, SKY + 3, Z + 5, "lava"))
        self.assertTrue(block_is(X - 1, SKY + 3, Z - 1, "glass"))
        area.fill((X - 1, SKY, Z - 1, X + 8, SKY + 3, Z + 8), "air")

    def test_along_a_beam_through_a_wall(self):
        wall = (X + 4, SKY, Z - 4, X + 6, SKY + 8, Z + 4)
        area.fill(wall, "stone")
        r = aim.cast(world.Pos(X + 0.5, SKY + 4.5, Z + 0.5), (1.0, 0.0, 0.0), 12, blocks=False)
        n = P["replace_blocks"].replace_blocks(r.every(1), vals("replace_blocks", replace_rules="solid > packed_ice",
                                                                 replace_radius=1))
        self.assertEqual(n, 3 * 3 * 3)
        self.assertTrue(all(block_is(X + dx, SKY + 4 + dy, Z + dz, "packed_ice")
                            for dx in (4, 5, 6) for dy in (-1, 0, 1) for dz in (-1, 0, 1)))
        self.assertTrue(block_is(X + 5, SKY + 2, Z, "stone") and block_is(X + 5, SKY + 4, Z + 2, "stone"))

    def test_in_the_nether(self):
        b = world.Box(8, 118, 8, 10, 118, 10, dim=NETHER)
        area.forceload(b, wait=20)
        try:
            area.fill(b, "netherrack")
            n = P["replace_blocks"].replace_blocks(b, vals("replace_blocks", replace_rules="netherrack > magma_block"))
            self.assertEqual(n, 9)
            self.assertTrue(block_is(9, 118, 9, "magma_block", NETHER))
            self.assertFalse(block_is(9, 118, 9, "magma_block"))
            area.fill(b, "netherrack")
        finally:
            area.release()


class LiveShapeFill(Live):
    def test_the_ice_castle_wall_hollow_in_one_call(self):
        v = vals("shape_fill", shape_width=61, shape_height=12, shape_depth=61, shape_blocks="packed_ice",
                 shape_hollow="true", shape_facing="north")
        at = world.Pos(X + 0.5, SKY, Z + 0.5)
        try:
            whole = P["shape_fill"].shape_fill(at, v)
            self.assertEqual(tuple(whole), (X - 30, SKY, Z - 30, X + 30, SKY + 11, Z + 30))
            for x, y, z in ((X - 30, SKY, Z - 30), (X + 30, SKY + 11, Z + 30), (X - 30, SKY + 5, Z), (X, SKY, Z),
                            (X, SKY + 11, Z), (X + 7, SKY + 6, Z + 30)):
                self.assertTrue(block_is(x, y, z, "packed_ice"), (x, y, z))
            for x, y, z in ((X, SKY + 5, Z), (X - 29, SKY + 1, Z - 29), (X + 29, SKY + 10, Z + 29)):
                self.assertTrue(block_is(x, y, z, "air"), (x, y, z))
        finally:
            P["shape_fill"].shape_fill(at, v, blocks="air", hollow=False)

    def test_a_cylinder_a_pyramid_and_a_wall_facing_east(self):
        sf = P["shape_fill"].shape_fill
        at = world.Pos(X + 0.5, SKY, Z + 0.5)
        sf(at, vals("shape_fill", shape_kind="cylinder", shape_width=9, shape_depth=9, shape_height=3,
                    shape_blocks="glass"))
        self.assertTrue(block_is(X, SKY + 2, Z, "glass") and block_is(X + 4, SKY, Z, "glass"))
        self.assertTrue(block_is(X + 4, SKY, Z + 4, "air"))        # outside the circle
        sf(at, vals("shape_fill", shape_blocks="air", shape_width=11, shape_depth=11, shape_height=4))
        sf(at, vals("shape_fill", shape_kind="pyramid", shape_width=7, shape_depth=7, shape_height=4,
                    shape_blocks="sandstone"))
        self.assertTrue(block_is(X, SKY + 3, Z, "sandstone") and block_is(X + 3, SKY, Z + 3, "sandstone"))
        self.assertTrue(block_is(X + 3, SKY + 1, Z + 3, "air") and block_is(X + 1, SKY + 3, Z, "air"))
        sf(at, vals("shape_fill", shape_blocks="air", shape_width=11, shape_depth=11, shape_height=5))
        whole = sf(at, vals("shape_fill", shape_kind="wall", shape_width=7, shape_height=3, shape_facing="east",
                            shape_offset="0,0,5", shape_blocks="70% stone_bricks 30% mossy_stone_bricks"))
        self.assertEqual(tuple(whole), (X + 5, SKY, Z - 3, X + 5, SKY + 2, Z + 3))
        stone = world.passes(["if block %d %d %d stone_bricks" % (X + 5, y, z) for y in range(SKY, SKY + 3)
                              for z in range(Z - 3, Z + 4)])
        mossy = world.passes(["if block %d %d %d mossy_stone_bricks" % (X + 5, y, z) for y in range(SKY, SKY + 3)
                              for z in range(Z - 3, Z + 4)])
        self.assertTrue(all(s or m for s, m in zip(stone, mossy)))

    def test_far_off_it_loads_the_chunks_builds_and_lets_them_go(self):
        at = world.Pos(200.5, SKY, 200.5)
        self.assertFalse(world.loaded(200, 200))
        whole = P["shape_fill"].shape_fill(at, vals("shape_fill", shape_width=5, shape_depth=5, shape_height=3,
                                                     shape_blocks="glass"))
        self.assertEqual(mc.rcon("forceload query"), FORCED)
        mc.rcon("forceload add 200 200")
        try:
            deadline = time.time() + 20
            while not world.loaded(200, 200) and time.time() < deadline:
                time.sleep(0.25)
            self.assertTrue(block_is(198, SKY, 198, "glass") and block_is(202, SKY + 2, 202, "glass"))
            area.fill(whole, "air")
        finally:
            mc.rcon("forceload remove 200 200")


class LiveSummon(Live):
    def test_round_a_place_armed_named_scaled_capped(self):
        v = vals("summon", summon_mob="husk", summon_count=3, summon_cap=4, summon_gear="iron",
                 summon_weapon="iron_sword", summon_name="Horde King", summon_scale=2, summon_health=60)
        at = world.Pos(X + 0.5, FLOOR + 1, Z + 0.5)
        self.assertEqual(P["summon"].summon(at, v), 3)
        self.assertEqual(P["summon"].summon(at, v), 1)
        self.assertEqual(P["summon"].summon(at, v), 0)
        sel = "@e[tag=%s_summon]" % mc.TAG
        self.assertEqual(count(sel), 4)
        self.assertEqual(count("@e[type=marker,tag=%s]" % mc.TAG), 0)
        self.assertEqual(count('@e[tag=%s_summon,nbt={equipment:{head:{id:"minecraft:iron_helmet"},'
                               'mainhand:{id:"minecraft:iron_sword"}}}]' % mc.TAG), 4)
        one = "@e[tag=%s_summon,limit=1]" % mc.TAG
        self.assertIn("Horde King", mc.rcon("data get entity %s CustomName" % one))
        self.assertRegex(mc.rcon("attribute %s minecraft:scale get" % one), r" 2(\.0+)?$")
        self.assertRegex(mc.rcon("data get entity %s Health" % one), r"60(\.0+)?f")


class LiveSkyAbove(Live):
    def test_open_air_kept_a_glass_roof_dropped(self):
        at = world.Pos(X + 0.5, FLOOR + 1, Z + 0.5)
        v = vals("sky_above", sky_count=30, sky_radius=12, sky_height=20)
        pts = P["sky_above"].sky_above(at, v)
        self.assertEqual(len(pts), 30)
        self.assertTrue(all(p.block()[1] == FLOOR + 21 for p in pts))
        self.assertTrue(all(world.passes(["if block %d %d %d %s" % (p.block() + (aim.OPEN,)) for p in pts])))
        roof = (X - 15, FLOOR + 21, Z - 15, X + 15, FLOOR + 21, Z + 15)
        area.fill(roof, "glass")
        try:
            self.assertEqual(P["sky_above"].sky_above(at, v), [])
        finally:
            area.fill(roof, "air")


class LiveShows(Live):
    def test_a_meteor_falls_slantwise_onto_the_floor_and_is_gone(self):
        got = {}
        th = threading.Thread(target=lambda: got.update(land=P["meteor_fall"].meteor_fall(
            world.Pos(X + 0.5, FLOOR + 6, Z + 0.5), vals("meteor_fall", meteor_seconds=2, meteor_height=30))))
        th.start()
        time.sleep(0.4)
        tag = "%s_meteor%d" % (mc.TAG, P["meteor_fall"]._meteor_fall_n[0])
        self.assertEqual(count("@e[type=block_display,tag=%s]" % tag), 19)
        shows = mc.rcon("execute as @e[type=block_display,tag=%s] run data get entity @s block_state" % tag)
        self.assertNotIn("minecraft:air", shows)          # 26.3 shows air for block_state:{Name:...}
        self.assertTrue(re.search(r'"minecraft:(magma_block|blackstone|obsidian)"', shows), shows[:200])
        y1 = float(re.findall(r"(-?[\d.]+)d", mc.rcon("data get entity @e[tag=%s,limit=1] Pos" % tag))[1])
        time.sleep(0.8)
        y2 = float(re.findall(r"(-?[\d.]+)d", mc.rcon("data get entity @e[tag=%s,limit=1] Pos" % tag))[1])
        self.assertLess(y2, y1 - 5)                       # it moves, every display together
        th.join(10)
        self.assertEqual(tuple(got["land"]), (X + 0.5, FLOOR + 1.0, Z + 0.5))
        self.assertEqual(count("@e[type=block_display,tag=%s]" % tag), 0)

    def test_a_block_flies_to_where_it_lands(self):
        got = {}
        end = world.Pos(X + 12.5, SKY, Z + 0.5)
        th = threading.Thread(target=lambda: got.update(end=P["projectile"].projectile(
            world.Pos(X - 12.5, SKY, Z + 0.5), end, vals("projectile", projectile_kind="block", projectile_speed=10))))
        th.start()
        time.sleep(1.2)
        tag = "%s_projectile%d" % (mc.TAG, P["projectile"]._projectile_n[0])
        x = float(re.findall(r"(-?[\d.]+)d", mc.rcon("data get entity @e[tag=%s,limit=1] Pos" % tag))[0])
        self.assertTrue(X - 10 < x < X + 2, x)
        self.assertIn('"minecraft:magma_block"', mc.rcon("data get entity @e[tag=%s,limit=1] block_state" % tag))
        th.join(10)
        self.assertEqual(got["end"], end)
        self.assertEqual(count("@e[type=block_display,tag=%s]" % tag), 0)
        P["projectile"].projectile(world.Pos(X - 12.5, SKY, Z + 0.5), end, vals("projectile", projectile_speed=60))


PROBE = '''"""Probe: the world presets mid-way when it is stopped."""
ASKS = []
import os
import mc
from kit import values, loop, world
V = {}
for name in ("shape_fill", "summon", "meteor_fall"):
    g = {"__name__": name}
    exec(open(os.path.join(%r, name + ".py")).read(), g)
    globals()[name] = g[name]
    V.update(g["VALUES"])
v = values(V)
summon(world.Pos(%d.5, %d, %d.5), v)
loop.background(meteor_fall, world.Pos(%d.5, %d, %d.5), v)
loop.background(shape_fill, world.Pos(260.5, %d, 260.5), v, width=64, depth=64, height=30,
                blocks="50%% air 50%% cave_air")
mc.log("ready")
for n in loop(every=0.2):
    pass
'''


class LiveStop(unittest.TestCase):
    def test_nothing_is_left_after_a_stop(self):
        folder = os.path.join(TMP, "probe_world")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "main.py"), "w") as f:
            f.write(PROBE % (PRESETS, X, FLOOR + 1, Z, X, FLOOR + 1, Z, SKY))
        env = dict(os.environ, PYTHONPATH=LIB, PYTHONDONTWRITEBYTECODE="1", VFG_INSTANCE="probe_world",
                   VFG_RUN_DIR=TMP, VFG_LIVE_POLL_S="0.5", VFG_MC_PROPERTIES=PROPS)
        py = os.environ.get("VFG_EC2_PYTHON") or sys.executable
        proc = subprocess.Popen([py, "-u", "main.py", "meteor_seconds=20", "summon_mob=husk"], cwd=folder, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            deadline = time.time() + 30
            while (mc.rcon("forceload query") == FORCED or count("@e[type=block_display,tag=vfg_probe_world]") < 19) \
                    and time.time() < deadline:
                time.sleep(0.05)
            self.assertNotEqual(mc.rcon("forceload query"), FORCED)      # the build is under way
            self.assertEqual(count("@e[tag=vfg_probe_world_summon]"), 5)
            self.assertEqual(count("@e[type=block_display,tag=vfg_probe_world]"), 19)
            t = time.time()
            proc.send_signal(signal.SIGTERM)
            out = proc.communicate(timeout=6)[0].decode()
            self.assertLess(time.time() - t, 2.0)
            self.assertEqual(proc.returncode, 0, out)
            self.assertNotIn("Traceback", out)
            self.assertEqual(count("@e[tag=vfg_probe_world]"), 0)
            self.assertEqual(mc.rcon("forceload query"), FORCED)
        finally:
            if proc.poll() is None:             # a failed check: stop it the proper way first
                proc.send_signal(signal.SIGTERM)
                try:
                    out = proc.communicate(timeout=4)[0].decode()
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out = proc.communicate()[0].decode()
                print("\n" + out[-3000:])
            mc.rcon("kill @e[tag=vfg_probe_world]")


def fake_players():
    """Carpet's players for this server, or None (and why) when it has no Carpet."""
    sys.path.insert(0, os.path.join(REPO, "integration", "realmc"))
    try:
        import players
        p = players.on(os.environ.get("REALMC_NAME"))
        p.online()
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)
    try:
        p.player("Steve", "stop")
    except players.NoCarpet as e:
        return None, str(e)
    except players.Refused:
        pass            # Carpet is there; Steve is not online yet
    return p, ""


class LivePlayers(Live):
    """With Carpet's fake players: walking, being lifted out of a block, mobs round a player.
    Skipped, saying why, on a server without Carpet."""

    @classmethod
    def setUpClass(cls):
        cls.p, why = fake_players()
        if cls.p is None:
            raise unittest.SkipTest("no fake players: " + why)

    def tearDown(self):
        self.p.kill_all()
        super(LivePlayers, self).tearDown()

    def spawn(self, at, facing=(0, 0)):
        """Steve on the server, and seen by the kit at once (it reads who is online once a second)."""
        self.p.spawn("Steve", at, facing=facing)
        loop._online["at"] = 0.0

    def run_loop(self, gen, body, seconds):
        """The script's loop, `for x in gen: body(x)`, in a thread for `seconds` or until
        self.halt is set: what gen yielded."""
        got, stop, self.halt = [], time.time() + seconds, threading.Event()

        def go():
            for x in gen:
                if time.time() > stop or self.halt.is_set():     # a later test's player is not this loop's
                    return
                got.append(x)
                body(x)
        th = threading.Thread(target=go, daemon=True)
        th.start()
        return got, th

    def test_ice_walk_freezes_each_step_and_none_in_the_air(self):
        self.spawn((X - 6.5, FLOOR + 1, Z + 0.5), facing=(-90, 0))      # facing east
        v = values(dict(P["walk_steps"].VALUES, **dict(P["under_feet"].VALUES, **P["replace_blocks"].VALUES)),
                   argv=["replace_rules=solid > packed_ice", "under_radius=1"], run_dir=TMP, instance="t")
        rb, uf = P["replace_blocks"].replace_blocks, P["under_feet"].under_feet
        try:
            steps, th = self.run_loop(P["walk_steps"].walk_steps("Steve", v), lambda s: rb(uf(s[1], v), v), 6)
            time.sleep(0.6)
            self.p.move("Steve", "forward")
            time.sleep(1.6)
            self.p.move("Steve", None)
            time.sleep(0.6)
            walked = len(steps)
            x, y, z = self.p.pos("Steve")
            self.assertGreater(x - (X - 6.5), 4)
            self.assertGreaterEqual(walked, 4, steps)                     # one about every block
            ice = world.passes(["if block %d %d %d packed_ice" % (xx, FLOOR, int(z // 1))
                                for xx in range(X - 6, int(x // 1) + 1)])
            self.assertEqual(sum(ice), len(ice), ice)                     # a trail of ice where Steve walked
            self.assertFalse(block_is(X - 6, FLOOR, int(z // 1) + 2, "packed_ice"))     # radius 1: three wide
            mc.rcon("tp Steve %.1f %d %.1f" % (x, FLOOR + 9, z))         # in the air: falling, no step
            time.sleep(0.25)                # (OnGround is a tick behind a teleport)
            walked = len(steps)
            time.sleep(0.5)
            self.assertEqual(len(steps), walked)
        finally:
            self.halt.set()
            th.join(2)
            mc.rcon("fill %d %d %d %d %d %d grass_block replace packed_ice" % (X - 16, FLOOR, Z - 16, X + 15, FLOOR,
                                                                               Z + 15))

    def test_chunk_changer_once_a_chunk(self):
        self.spawn((X + 3.5, FLOOR + 1, Z + 8.5), facing=(90, 0))        # facing west, over x=0
        v = values(dict(P["enters_chunk"].VALUES, **P["replace_blocks"].VALUES),
                   argv=["replace_rules=grass_block > moss_block", "enters_below=1", "enters_above=0"],
                   run_dir=TMP, instance="t")
        rb = P["replace_blocks"].replace_blocks
        try:
            got, th = self.run_loop(P["enters_chunk"].enters_chunk("Steve", v), lambda g: rb(g[1], v), 5)
            time.sleep(0.8)
            self.p.move("Steve", "forward")
            time.sleep(1.5)
            self.p.move("Steve", "backward")
            time.sleep(1.5)
            self.p.move("Steve", None)
            time.sleep(0.6)
            self.halt.set()
            th.join(2)
            boxes = [tuple(b) for _, b in got]
            cx = (X + 3) // 16
            self.assertEqual(boxes, [(cx * 16, FLOOR, Z, cx * 16 + 15, FLOOR + 1, Z + 15),
                                     (cx * 16 - 16, FLOOR, Z, cx * 16 - 1, FLOOR + 1, Z + 15)])
            for b in boxes:
                for x, z in ((max(b[0], X - 16), b[2]), (min(b[3], X + 15), min(b[5], Z + 15))):
                    self.assertTrue(block_is(x, FLOOR, z, "moss_block"),
                                    (b, x, z, [blk for blk in ("grass_block", "packed_ice", "air", "dirt")
                                               if block_is(x, FLOOR, z, blk)], got))
        finally:
            mc.rcon("fill %d %d %d %d %d %d grass_block replace moss_block" % (X - 16, FLOOR, Z - 16, X + 15, FLOOR,
                                                                               Z + 15))

    def test_a_player_left_inside_a_block_is_lifted_out(self):
        self.spawn((X + 0.5, FLOOR + 1, Z + 0.5))
        box = (X - 1, FLOOR + 1, Z - 1, X + 1, FLOOR + 3, Z + 1)
        try:
            n = P["replace_blocks"].replace_blocks(box, vals("replace_blocks", replace_rules="air > stone"))
            self.assertEqual(n, 27)
            x, y, z = self.p.pos("Steve")
            self.assertGreaterEqual(y, FLOOR + 4)
        finally:
            mc.rcon("fill %d %d %d %d %d %d air" % box)

    def test_mobs_round_players_and_the_sky_over_them(self):
        self.spawn((X + 0.5, FLOOR + 1, Z + 0.5))
        got = P["summon"].summon("@a", vals("summon", summon_mob="husk", summon_count=2, summon_spread=6))
        self.assertEqual(got, 2)
        self.assertEqual(count("@e[tag=%s_summon]" % mc.TAG), 2)
        self.assertIn("passed", mc.rcon("execute at Steve if entity @e[tag=%s_summon,distance=6..14]" % mc.TAG).lower())
        pts = P["sky_above"].sky_above("@a", vals("sky_above", sky_count=10, sky_radius=8, sky_height=20))
        self.assertEqual(len(pts), 10)
        self.assertTrue(all(abs(p[0] - (X + 0.5)) <= 8 and p.block()[1] == FLOOR + 21 for p in pts))
        land = P["meteor_fall"].meteor_fall("Steve", vals("meteor_fall", meteor_seconds=0.5, meteor_height=15))
        self.assertEqual(tuple(land), (X + 0.5, FLOOR + 1.0, Z + 0.5))

    def test_a_wall_in_front_of_a_player_and_a_throw_from_them(self):
        self.spawn((X + 0.5, FLOOR + 1, Z + 0.5), facing=(-90, 0))      # facing east
        try:
            whole = P["shape_fill"].shape_fill("Steve", vals("shape_fill", shape_kind="wall", shape_width=5,
                                                             shape_height=2, shape_offset="0,0,4",
                                                             shape_blocks="glass"))
            self.assertEqual(tuple(whole), (X + 4, FLOOR + 1, Z - 2, X + 4, FLOOR + 2, Z + 2))
            self.assertTrue(block_is(X + 4, FLOOR + 2, Z + 2, "glass"))
            end = world.Pos(X + 3.5, FLOOR + 2, Z + 0.5)
            self.assertEqual(P["projectile"].projectile("Steve", end, vals("projectile", projectile_speed=40)), end)
        finally:
            mc.rcon("fill %d %d %d %d %d %d air" % (X + 4, FLOOR + 1, Z - 2, X + 4, FLOOR + 2, Z + 2))


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        mc.rcon("kill @e[tag=%s]" % mc.TAG)
        area.release()
