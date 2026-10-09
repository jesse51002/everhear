"""The kit against a real Minecraft 26.3 server: the live counterparts of test_kit.py.

    integration/realmc/up kit && eval "$(integration/realmc/env kit)"
    uv run --python 3.9 python scripts/_test/live_kit.py

It changes blocks, so it runs only against a realmc test server (REALMC_NAME set, and
VFG_MC_PROPERTIES inside that server's folder), never against the box. Everything it builds
is at least 40 blocks above the arena's floor and is taken away again; the mobs it summons
are killed. Nobody needs to be online.
"""
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), "_lib")
sys.path.insert(0, LIB)

PROPS = os.environ.get("VFG_MC_PROPERTIES", "")
if not os.environ.get("REALMC_NAME") or "realmc" not in os.path.realpath(PROPS):
    sys.exit("live_kit: point it at a realmc server first: eval \"$(integration/realmc/env kit)\"")

import mc  # noqa: E402
from kit import aim, area, exclude, items, mobs, world  # noqa: E402

signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)

BAD = re.compile(r"<--\[HERE\]|Incorrect argument|Unknown or incomplete|Expected |Invalid |Unknown |Malformed|"
                 r"Can't find|isn't applicable")


def ok(reply):
    """The reply is not a parse error."""
    return not BAD.search(reply or "")


def find_floor():
    """The arena: the middle of the chunks forceloaded for good, and the floor under it."""
    pairs = [(int(x), int(z)) for x, z in re.findall(r"\[(-?\d+), (-?\d+)\]", mc.rcon("forceload query"))]
    if pairs:
        cx = sorted(p[0] for p in pairs)[len(pairs) // 2]
        cz = sorted(p[1] for p in pairs)[len(pairs) // 2]
        x, z = cx * 16 + 8, cz * 16 + 8
    else:
        x, z = 8, 8
    r = aim.cast((x + 0.5, 300.0, z + 0.5), (0.0, -1.0, 0.0), 400)
    assert r.why == "block", "no floor under %d %d: %s" % (x, z, r.why)
    return int(x), int(r.hit_block[1]), int(z)


X, FLOOR, Z = find_floor()
SKY = FLOOR + 40      # everything this test builds is up here, in air


class LiveFacts(unittest.TestCase):
    """The 26.3 facts the kit is built on, asked of the server itself."""

    def test_many_block_conditions_in_one_command(self):
        conds = ["if block %d %d %d #minecraft:replaceable" % (X + i, SKY, Z) for i in range(30)]
        self.assertIn("Test passed", mc.rcon("execute " + " ".join(conds)))
        self.assertEqual(world.first_fail(conds), (None, ""))

    def test_an_unloaded_chunk_and_the_edge_of_the_world(self):
        self.assertIn("not loaded", mc.rcon("execute if block 900000 64 900000 #minecraft:replaceable"))
        self.assertIn("Test failed", mc.rcon("execute if loaded 900000 64 900000"))
        self.assertIn("Test passed", mc.rcon("execute if loaded %d 64 %d" % (X, Z)))
        self.assertIn("out of the world", mc.rcon("execute if block %d 400 %d #minecraft:replaceable" % (X, Z)))
        self.assertIn("not loaded", mc.rcon("fill 900000 64 900000 900001 64 900001 stone"))

    def test_the_fill_limit(self):
        reply = mc.rcon("fill %d %d %d %d %d %d air" % (X, SKY, Z, X + 32, SKY + 32, Z + 32))
        self.assertIn("maximum 32768", reply)

    def test_the_click_poll_and_the_item_parse(self):
        _objective = items.Stat(*items.CLICK)
        w = items._Watch("@a", "Kit Wand", "never", "aqua", None)
        reply = mc.rcon("execute as @a %s run scoreboard players get @s vfg_click" % w.cond)
        self.assertTrue(ok(reply), reply)
        self.assertEqual(_objective.scores(w.cond), {})
        reply = mc.rcon("give NobodyAtAll %s" % items.item("Kit Wand", model="nether_star"))
        self.assertIn("No player was found", reply)
        self.assertTrue(ok(mc.rcon("clear NobodyAtAll %s" % items.match("Kit Wand"))))
        self.assertTrue(ok(mc.rcon("execute as @a run scoreboard players get @s vfg_sneak")))

    def test_what_a_hit_never_touches_parses(self):
        for frag in exclude.targets("Steve", "distance=..2", ["Alex"]):
            reply = mc.rcon("execute positioned %d %d %d %s run say x" % (X, SKY, Z, frag))
            self.assertTrue(ok(reply), reply)
        self.assertIn("Invalid or unknown entity type", mc.rcon("execute if entity @e[type=potion]"))
        self.assertIn("Invalid or unknown entity type", mc.rcon("execute if entity @e[type=boat]"))
        self.assertTrue(ok(mc.rcon("execute if entity @e[type=!#minecraft:boat]")))
        self.assertIn("isn't applicable", mc.rcon("execute if entity @a[type=player]"))

    def test_gone_block_tags(self):
        self.assertFalse(ok(mc.rcon("execute if block %d %d %d #minecraft:tall_flowers" % (X, SKY, Z))))
        self.assertTrue(ok(mc.rcon("execute if block %d %d %d #minecraft:replaceable" % (X, SKY, Z))))

    def test_gamerules_are_snake_case(self):
        for name in sorted(set(world.GAMERULES.values()) | {"pvp"}):
            self.assertIn("currently set to", mc.rcon("gamerule %s" % name), name)
        self.assertFalse(ok(mc.rcon("gamerule keepInventory")))

    def test_formats_the_kit_reads(self):
        self.assertGreater(world.mspt(), 0.0)
        self.assertTrue(ok(mc.rcon("forceload query")))
        self.assertRegex(mc.rcon("forceload query %d %d" % (X, Z)), r"is (not )?marked for force loading")
        self.assertRegex(mc.rcon("list"), r"players online:")
        self.assertTrue(ok(mc.rcon("particle dust{color:[0.6,0.1,0.9],scale:2.0} %d %d %d 0.1 0.1 0.1 0 1 force @a"
                                   % (X, SKY, Z))))
        self.assertTrue(ok(mc.rcon("playsound minecraft:block.note_block.pling master @a %d %d %d 1 1" % (X, SKY, Z))))
        self.assertTrue(ok(mc.rcon('title @a actionbar {"text":"kit","color":"aqua"}')))
        print("\n  time query daytime ->", mc.rcon("time query daytime")[:90])
        print("  time query gametime ->", mc.rcon("time query gametime")[:90])


class LiveKit(unittest.TestCase):
    def tearDown(self):
        mc.rcon("kill @e[tag=vfg_kit_live]")
        mc.rcon("fill %d %d %d %d %d %d air" % (X - 12, SKY - 2, Z - 12, X + 12, SKY + 22, Z + 12))

    def test_a_fill_split_under_32768_and_only_where_loaded(self):
        box = (X - 70, SKY, Z - 70, X + 69, SKY + 3, Z + 69)        # 140 x 4 x 140 = 78,400
        before = len(re.findall(r"\[", mc.rcon("forceload query")))
        n = area.fill(box, "glass")
        loaded = area.loaded_chunks(area.chunks(box))
        want = sum(area.volume(b) for c in loaded for b in [(max(box[0], c[0] * 16), box[1], max(box[2], c[1] * 16),
                                                             min(box[3], c[0] * 16 + 15), box[4], min(box[5], c[1] * 16 + 15))])
        print("\n  fill: %d of %d chunks loaded, %d blocks filled" % (len(loaded), len(area.chunks(box)), n))
        self.assertEqual(n, want)
        self.assertGreater(n, 32768)
        self.assertEqual(area.fill(box, "air"), n)
        self.assertEqual(len(re.findall(r"\[", mc.rcon("forceload query"))), before)

    def test_a_sphere_carved_out_of_a_block(self):
        cube = (X - 10, SKY, Z - 10, X + 10, SKY + 20, Z + 10)
        area.fill(cube, "stone")
        n = area.sphere((X, SKY + 10, Z), 8, "air", spare_protected=False)
        self.assertGreater(n, 2000)

        def air(dx, dy, dz):
            return "passed" in mc.rcon("execute if block %d %d %d air" % (X + dx, SKY + 10 + dy, Z + dz)).lower()
        self.assertTrue(all(air(*d) for d in [(0, 0, 0), (7, 0, 0), (0, -7, 0), (0, 0, 7), (4, 4, 4)]))
        self.assertFalse(any(air(*d) for d in [(10, 0, 0), (0, 10, 0), (7, 7, 7)]))

    def test_a_ray_against_real_terrain(self):
        down = aim.cast((X + 0.5, SKY + 0.5, Z + 0.5), (0.0, -1.0, 0.0), 100)
        self.assertEqual(down.why, "block")
        self.assertEqual(int(down.hit_block[1]), FLOOR)
        far = aim.cast((X + 0.5, FLOOR + 2.5, Z + 0.5), (1.0, 0.0, 0.0), 2000, blocks=False)
        self.assertEqual(far.why, "unloaded")
        self.assertIn("Test failed", mc.rcon("execute if loaded %d 64 %d" % (int(far.end[0]) + 1, Z)))
        # Across the terrain round the arena, slanting down: whatever it says, the server agrees.
        mc.rcon('summon armor_stand %d %d %d {Tags:["vfg_kit_live","kit_eye"],NoGravity:1b,Invisible:1b,'
                'Rotation:[-60f,12f]}' % (X, FLOOR + 1, Z))
        r = aim.ray("@e[tag=kit_eye,limit=1]", 200)
        self.assertIsNotNone(r)
        print("\n  ray from the arena: %s after %.1f blocks, end %s" % (r.why, r.length, r.end))
        if r.why == "block":
            hx, hy, hz = (int(v) for v in r.hit_block)
            self.assertIn("Test passed", mc.rcon("execute unless block %d %d %d #minecraft:replaceable" % (hx, hy, hz)))
        for p in r.path[1:-1:3]:
            self.assertIn("Test passed", mc.rcon("execute if block %d %d %d #minecraft:replaceable" % p.block()))

    def test_who_is_near_a_line(self):
        y = SKY + 1
        mc.rcon("fill %d %d %d %d %d %d glass" % (X - 2, SKY, Z - 4, X + 30, SKY, Z + 4))
        nbt = 'NoAI:1b,Tags:["vfg_kit_live"]'
        mc.rcon("summon zombie %d.5 %d %d.5 {%s,Health:20f}" % (X + 10, y, Z, nbt))
        mc.rcon("summon zombie %d.5 %d %d.5 {%s}" % (X + 15, y, Z + 3, nbt))
        mc.rcon("summon villager %d.5 %d %d.5 {%s}" % (X + 5, y, Z, nbt))
        mc.rcon("summon wolf %d.5 %d %d.5 {%s,Owner:[I;1,2,3,4]}" % (X + 7, y, Z, nbt))
        mc.rcon("summon armor_stand %d.5 %d %d.5 {%s}" % (X + 12, y, Z, nbt))
        r = aim.cast((X + 0.5, y + 1.62, Z + 0.5), (1.0, 0.0, 0.0), 25)
        hits = aim.near_line(r, 1.5, shooter="Steve")
        self.assertEqual([h.name for h in hits], ["Zombie"], hits)
        reply = aim.run_on(hits[0], "damage @s 5 minecraft:magic")
        self.assertIn("Applied", reply)
        self.assertRegex(mc.rcon("data get entity @e[type=zombie,tag=vfg_kit_live,limit=1,sort=nearest,"
                                 "x=%d,y=%d,z=%d] Health" % (X + 10, y, Z)), r"15\.0f")

    def test_a_boss_and_its_bar(self):
        mc.rcon("fill %d %d %d %d %d %d glass" % (X - 6, SKY, Z - 6, X + 6, SKY, Z + 6))
        boss = mobs.Boss("Kit Boss", "zombie", health=50, bodies=2, scale=1.5, nbt="NoAI:1b")
        self.assertEqual(boss.spawn(at=(X + 0.5, SKY + 1, Z + 0.5)), 2)
        self.assertIn(boss.bar, mc.rcon("bossbar list"))
        self.assertEqual(boss.health(), (100.0, 2))
        mc.rcon("damage @e[tag=%s_0,limit=1] 20 minecraft:magic" % boss.tag)
        self.assertEqual(boss.update(), 2)
        self.assertIn("80", mc.rcon("bossbar get %s value" % boss.bar))
        mc.rcon("kill @e[tag=%s]" % boss.tag)
        boss.update()
        self.assertEqual(boss.update(), 0)
        self.assertNotIn(boss.bar, mc.rcon("bossbar list"))

    def test_a_stop_puts_everything_back(self):
        old = world.gamerule("mob_griefing")
        mc.rcon("fill %d %d %d %d %d %d glass" % (X - 6, SKY, Z - 6, X + 6, SKY, Z + 6))
        folder = os.path.join(tempfile.mkdtemp(), "kitprobe")
        os.makedirs(folder)
        with open(os.path.join(folder, "main.py"), "w") as f:
            f.write('''"""Probe: one of everything the kit cleans up."""
ASKS = []
from kit import values, items, area, mobs, world, loop
import mc
v = values({"player": {"type": "player", "default": "@a"}})
world.gamerule("mob_griefing", %s)
world.objective("dummy", "kills")
area.forceload((%d, 0, %d, %d, 0, %d), wait=5)
mobs.Boss("Probe Boss", "zombie", health=40, nbt="NoAI:1b").spawn(at=(%d.5, %d, %d.5))
mc.log("ready")
for p in loop(items.clicks(v["player"], "Probe Wand", every=0.05)):
    pass
''' % ("False" if old == "true" else "True", X + 300, Z + 300, X + 340, Z + 340, X, SKY + 1, Z))
        env = dict(os.environ, PYTHONPATH=LIB, VFG_INSTANCE="kitprobe", VFG_RUN_DIR=tempfile.mkdtemp())
        proc = subprocess.Popen([sys.executable, "-u", "main.py"], cwd=folder, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.time() + 20
        while "vfg:kitprobe" not in mc.rcon("bossbar list") and time.time() < deadline:
            time.sleep(0.2)
        time.sleep(0.5)
        self.assertIn("vfg:kitprobe", mc.rcon("bossbar list"))
        self.assertNotEqual(world.gamerule("mob_griefing"), old)
        self.assertIn("vfg_kitprobe_kills", mc.rcon("scoreboard objectives list"))
        self.assertIn("is marked", mc.rcon("forceload query %d %d" % (X + 320, Z + 320)))
        t = time.time()
        proc.send_signal(signal.SIGTERM)
        out = proc.communicate(timeout=5)[0].decode()
        took = time.time() - t
        print("\n  stop took %.2f s" % took)
        self.assertLess(took, 2.0)
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("Traceback", out)
        self.assertNotIn("vfg:kitprobe", mc.rcon("bossbar list"))
        self.assertEqual(world.gamerule("mob_griefing"), old)
        self.assertNotIn("vfg_kitprobe_kills", mc.rcon("scoreboard objectives list"))
        self.assertIn("not marked", mc.rcon("forceload query %d %d" % (X + 320, Z + 320)))
        self.assertIn("Test failed", mc.rcon("execute if entity @e[tag=vfg_kitprobe]"))
        self.assertIn("vfg_click", mc.rcon("scoreboard objectives list"))


REALMC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "integration", "realmc")
sys.path.insert(0, REALMC)
try:
    import players as fake_players  # noqa: E402
    PLAYERS = fake_players.on()
    CARPET = "Unknown or incomplete" not in PLAYERS.cmd("carpet")
except Exception:   # no Carpet on this server, or no realmc beside the repo
    CARPET = False

WAND = '''"""%s: logs each click."""
ASKS = []
from kit import values, items, loop
import mc
v = values({"player": {"type": "player", "default": "@a"}})
for p in loop(items.%s):
    mc.log("fired", p)
'''


@unittest.skipUnless(CARPET, "the server has no Carpet: integration/realmc/realmc.py carpet, then up")
class LiveClicks(unittest.TestCase):
    """A Carpet fake player right-clicks and sneaks, as a person does."""

    def setUp(self):
        self.procs = []
        PLAYERS.spawn("Steve")
        PLAYERS.clear("Steve")

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
                p.communicate(timeout=5)
        PLAYERS.kill_all()

    def start(self, name, title, gen):
        folder = os.path.join(tempfile.mkdtemp(), name)
        os.makedirs(folder)
        with open(os.path.join(folder, "main.py"), "w") as f:
            f.write(WAND % (title, gen))
        env = dict(os.environ, PYTHONPATH=LIB, VFG_INSTANCE=name, VFG_RUN_DIR=tempfile.mkdtemp())
        proc = subprocess.Popen([sys.executable, "-u", "main.py"], cwd=folder, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.procs.append(proc)
        return proc

    def stop(self, proc):
        proc.send_signal(signal.SIGTERM)
        return proc.communicate(timeout=5)[0].decode()

    def test_two_wands_on_one_player(self):
        a = self.start("kitalpha", "Alpha", 'clicks(v["player"], "Alpha Wand", every=0.05)')
        b = self.start("kitbeta", "Beta", 'clicks(v["player"], "Beta Wand", every=0.05)')
        deadline = time.time() + 10
        while not (items.has("Steve", "Alpha Wand") and items.has("Steve", "Beta Wand")) and time.time() < deadline:
            time.sleep(0.2)
        self.assertTrue(items.has("Steve", "Alpha Wand") and items.has("Steve", "Beta Wand"))
        for slot in (1, 2, 3, 4, 5, 6, 7, 8, 9):
            PLAYERS.hotbar("Steve", slot)
            time.sleep(0.3)
            if items.held("Steve", "Alpha Wand"):
                break
        PLAYERS.use("Steve")
        time.sleep(0.6)
        out_a, out_b = self.stop(a), self.stop(b)
        self.assertEqual(out_a.count("fired Steve"), 1, out_a)
        self.assertEqual(out_b.count("fired Steve"), 0, out_b)
        self.assertFalse(items.has("Steve", "Alpha Wand"))      # taken back on stop

    def test_a_charge_held_down(self):
        proc = self.start("kitbomb", "Bomb", 'charged(v["player"], "Kit Bomb", 1.0, every=0.05)')
        deadline = time.time() + 10
        while not items.held("Steve", "Kit Bomb") and time.time() < deadline:
            time.sleep(0.2)
        PLAYERS.use_continuous("Steve")
        time.sleep(1.8)
        PLAYERS.stop("Steve")
        out = self.stop(proc)
        self.assertEqual(out.count("fired Steve"), 1, out)

    def test_sneaking(self):
        items.sneaking()
        PLAYERS.sneak("Steve")
        time.sleep(0.5)
        self.assertIn("Steve", items.sneaking())
        PLAYERS.sneak("Steve", False)
        time.sleep(0.3)
        items.sneaking()
        time.sleep(0.3)
        self.assertNotIn("Steve", items.sneaking())


if __name__ == "__main__":
    print("realmc %s: arena floor at %d %d %d" % (os.environ.get("REALMC_NAME"), X, FLOOR, Z))
    unittest.main(verbosity=2)
