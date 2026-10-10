"""The kit against a real Minecraft 26.3 server: the live counterparts of test_kit.py.

    integration/realmc/up kit && eval "$(integration/realmc/env kit)"
    uv run --no-project --python 3.12 python scripts/_test/live_kit.py

It changes blocks, so it runs only against a realmc test server (REALMC_NAME set, and
VFG_MC_PROPERTIES inside that server's folder), never against the box. Everything it builds
is at least 40 blocks above the arena's floor and is taken away again (leaving air where
terrain reached that high; `up kit --fresh` puts the world back); the mobs it summons are
killed. Nobody needs to be online.
"""
import json
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
        mc.rcon("fill %d %d %d %d %d %d air" % (X - 8, SKY, Z, X + 21, SKY, Z))
        conds = ["if block %d %d %d #minecraft:replaceable" % (X - 8 + i, SKY, Z) for i in range(30)]   # all loaded
        self.assertEqual(mc.rcon("execute " + " ".join(conds)), "Test passed")
        self.assertEqual(world.first_fail(conds), (None, ""))
        # One that fails, among several: the chain answers nothing; alone, "Test failed".
        mc.rcon("setblock %d %d %d glass" % (X + 5, SKY, Z))
        try:
            self.assertEqual(mc.rcon("execute " + " ".join(conds)), "")
            self.assertEqual(world.first_fail(conds), (13, "Test failed"))
            self.assertEqual([i for i, good in enumerate(world.passes(conds)) if not good], [13])
        finally:
            mc.rcon("setblock %d %d %d air" % (X + 5, SKY, Z))

    def test_an_unloaded_chunk_and_the_edge_of_the_world(self):
        self.assertIn("not loaded", mc.rcon("execute if block 900000 64 900000 #minecraft:replaceable"))
        self.assertIn("Test failed", mc.rcon("execute if loaded 900000 64 900000"))
        self.assertIn("Test passed", mc.rcon("execute if loaded %d 64 %d" % (X, Z)))
        self.assertIn("out of this world", mc.rcon("execute if block %d 400 %d #minecraft:replaceable" % (X, Z)))
        # In a chain of several, an unloaded block answers nothing; alone, it says why.
        two = "execute if block %d %d %d #minecraft:replaceable if block 900000 64 900000 #minecraft:replaceable"
        self.assertEqual(mc.rcon(two % (X, SKY, Z)), "")
        bad, reply = world.first_fail(["if block %d %d %d #minecraft:replaceable" % (X, SKY, Z),
                                       "if block 900000 64 900000 #minecraft:replaceable"])
        self.assertEqual(bad, 1)
        self.assertIn("not loaded", reply)
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
        self.assertIn("isn't applicable", mc.rcon("execute if entity @e[type=zombie,type=!villager]"))
        self.assertTrue(ok(mc.rcon("execute if entity @a[type=!villager]")))
        frag = exclude.mobs("type=zombie,distance=..2")
        self.assertTrue(ok(mc.rcon("execute positioned %d %d %d %s run say x" % (X, SKY, Z, frag))))
        for t in exclude.SPARED_TYPES:
            self.assertTrue(ok(mc.rcon("execute if entity @e[type=%s]" % t)), t)

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
        self.assertIn("Can't find element 'minecraft:daytime'", mc.rcon("time query daytime"))
        self.assertRegex(mc.rcon("time query day"), r"Timeline minecraft:day is at \d+ tick")
        self.assertRegex(mc.rcon("time query gametime"), r"The game time is \d+ tick")


class LiveKitMore(unittest.TestCase):
    def tearDown(self):
        mc.rcon("kill @e[tag=vfg_kit_live]")
        mc.rcon("kill @e[tag=%s]" % mc.TAG)
        mc.rcon("fill %d %d %d %d %d %d air" % (X - 12, SKY - 2, Z - 12, X + 12, SKY + 6, Z + 12))
        area.release()

    def test_near_a_place_and_mark(self):
        mc.rcon("fill %d %d %d %d %d %d glass" % (X - 8, SKY, Z - 8, X + 8, SKY, Z + 8))
        nbt = 'NoAI:1b,Tags:["vfg_kit_live"]'
        for dx in (0, 2, 9):
            mc.rcon("summon zombie %d.5 %d %d.5 {%s}" % (X + dx, SKY + 1, Z, nbt))
        mc.rcon("summon villager %d.5 %d %d.5 {%s}" % (X + 1, SKY + 1, Z, nbt))
        hits = aim.near(world.Pos(X + 0.5, SKY + 1, Z + 0.5), 4)
        self.assertEqual([h.name for h in hits], ["Zombie", "Zombie"])
        self.assertEqual(aim.mark(hits + hits, "vfg_kit_hit"), 2)
        self.assertRegex(mc.rcon("execute if entity @e[tag=vfg_kit_hit]"), r"[Cc]ount: 2")
        mc.rcon("tag @e[tag=vfg_kit_hit] remove vfg_kit_hit")

    def test_mobs_round_a_place_and_a_boss_with_attributes(self):
        mc.rcon("fill %d %d %d %d %d %d glass" % (X - 8, SKY, Z - 8, X + 8, SKY, Z + 8))
        got = mobs.around(world.Pos(X + 0.5, SKY + 1, Z + 0.5), "zombie", 3, nbt="NoAI:1b", spread=3)
        self.assertEqual(got, 3)
        boss = mobs.Boss("Attr Boss", "zombie", health=30, nbt="NoAI:1b", attributes={"attack_damage": 12})
        self.assertEqual(boss.spawn(at=(X + 0.5, SKY + 1, Z + 0.5)), 1)
        self.assertIn("12", mc.rcon("attribute @e[tag=%s,limit=1] minecraft:attack_damage base get" % boss.tag))
        self.assertEqual(boss.update(), 1)
        self.assertEqual(boss.total, 30.0)
        boss.remove()

    def test_sounds_records_and_release_by_box(self):
        self.assertTrue(ok(mc.rcon("execute positioned %d %d %d run playsound minecraft:entity.zombie.step master "
                                   "NobodyAtAll ~ ~ ~ 1 1" % (X, SKY, Z))))
        self.assertTrue(ok(mc.rcon("stopsound @a * minecraft:music_disc.pigstep")))
        added = area.forceload((X + 300, 0, Z + 300, X + 340, 0, Z + 340), wait=0)
        added += area.forceload((X + 500, 0, Z + 500, X + 510, 0, Z + 510), wait=0)
        area.release((X + 300, 0, Z + 300, X + 340, 0, Z + 340))
        self.assertIn("not marked", mc.rcon("forceload query %d %d" % (X + 320, Z + 320)))
        self.assertIn("is marked", mc.rcon("forceload query %d %d" % (X + 505, Z + 505)))


class LiveKitPolish(unittest.TestCase):
    """What the polish pass added, asked of the real server."""

    def tearDown(self):
        mc.rcon("kill @e[tag=vfg_kit_live]")
        mc.rcon("fill %d %d %d %d %d %d air" % (X - 12, SKY - 2, Z - 12, X + 12, SKY + 6, Z + 12))

    def test_the_ground_height(self):
        self.assertEqual(world.ground(X, Z), FLOOR + 1)
        mc.rcon("fill %d %d %d %d %d %d glass" % (X - 1, SKY, Z - 1, X + 1, SKY, Z + 1))
        self.assertEqual(world.ground(X, Z), SKY + 1)
        self.assertEqual(world.ground(X + 5, Z + 5, surface="ocean_floor"), FLOOR + 1)    # in the forceloaded arena
        self.assertIsNone(world.ground(X + 30000, Z + 30000))                    # not loaded
        self.assertIn("Test failed", mc.rcon("execute if entity @e[type=marker,x=%d,y=%d,z=%d,distance=..2]"
                                             % (X, SKY + 1, Z)))

    def test_hostile_only_and_marking_takes_a_left_tag_off_first(self):
        mc.rcon("fill %d %d %d %d %d %d glass" % (X - 8, SKY, Z - 8, X + 8, SKY, Z + 8))
        nbt = 'NoAI:1b,Tags:["vfg_kit_live"]'
        for kind, dx in (("zombie", 0), ("cow", 1), ("skeleton", 2), ("sheep", 3), ("enderman", -1)):
            mc.rcon("summon %s %d.5 %d %d.5 {%s}" % (kind, X + dx, SKY + 1, Z, nbt))
        here = world.Pos(X + 0.5, SKY + 1, Z + 0.5)
        hits = aim.near(here, 5, hostile=True)
        self.assertEqual(sorted(h.name for h in hits), ["Enderman", "Skeleton", "Zombie"])
        self.assertEqual(len(aim.near(here, 5)), 5)
        mc.rcon("tag @e[tag=vfg_kit_live,type=zombie] add vfg_kit_hit")       # left from before
        aim._cleared.discard("vfg_kit_hit")                                     # by an earlier process
        self.assertEqual(aim.mark(hits, "vfg_kit_hit", hostile=True), 3)        # the zombie counted again
        aim.untag("vfg_kit_hit")
        self.assertIn("Test failed", mc.rcon("execute if entity @e[tag=vfg_kit_hit]"))

    def test_a_hit_found_by_its_uuid(self):
        mc.rcon('summon armor_stand %d.5 %d %d.5 {Tags:["vfg_kit_live","vfg_kit_uuid"],NoGravity:1b}' % (X, SKY + 3, Z))
        m = re.search(r"\[I; (-?\d+), (-?\d+), (-?\d+), (-?\d+)\]", mc.rcon("data get entity @e[tag=vfg_kit_uuid,limit=1] UUID"))
        h = "".join("%08x" % (int(n) & 0xffffffff) for n in m.groups())
        uuid = "%s-%s-%s-%s-%s" % (h[:8], h[8:12], h[12:16], h[16:20], h[20:])
        hit = aim.Hit(0.0, world.Pos(X + 50, SKY, Z), "Armor Stand", False, uuid)     # far from where it is
        self.assertEqual(aim.mark([hit], "vfg_kit_hit"), 1)
        self.assertIn("Modified", aim.run_on(hit, "data merge entity @s {CustomName:\"found\"}"))
        aim.untag("vfg_kit_hit")


def run_dir(name):
    """A run directory with the meta file a copy named `name` gets: its one value, player=@a."""
    d = tempfile.mkdtemp()
    with open(os.path.join(d, name + ".json"), "w") as f:
        json.dump({"values": {"player": "@a"}, "live": []}, f)
    return d


class LiveKit(unittest.TestCase):
    def tearDown(self):
        mc.rcon("kill @e[tag=vfg_kit_live]")
        for b in mobs._bars:
            b.remove()
        mc.rcon("fill %d %d %d %d %d %d air" % (X - 12, SKY - 2, Z - 12, X + 12, SKY + 22, Z + 12))

    def test_a_fill_split_under_32768_and_only_where_loaded(self):
        box = (X - 70, SKY, Z - 70, X + 69, SKY + 11, Z + 69)       # 140 x 12 x 140 = 235,200
        before = mc.rcon("forceload query")
        area.fill(box, "air")                                       # the loaded part, from a known start
        n = area.fill(box, "glass")
        loaded = area.loaded_chunks(area.chunks(box))
        want = sum(area.volume(b) for c in loaded for b in [(max(box[0], c[0] * 16), box[1], max(box[2], c[1] * 16),
                                                             min(box[3], c[0] * 16 + 15), box[4], min(box[5], c[1] * 16 + 15))])
        print("\n  fill: %d of %d chunks loaded, %d blocks filled" % (len(loaded), len(area.chunks(box)), n))
        self.assertEqual(n, want)
        self.assertGreater(n, 32768)
        # Forceloaded, all of it: then the whole box, and back to air before letting go.
        added = area.forceload(box, wait=20)
        self.assertEqual(added, len(area.chunks(box)) - len(loaded))
        self.assertEqual(len(area.loaded_chunks(area.chunks(box))), len(area.chunks(box)))
        area.fill(box, "air")                                       # all of it now (terrain up here too)
        self.assertEqual(area.fill(box, "glass"), area.volume(box))
        self.assertEqual(area.fill(box, "air"), area.volume(box))
        area.release()
        self.assertEqual(mc.rcon("forceload query"), before)

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
        mc.rcon('summon item %d.5 %d %d.5 {%s,Item:{id:"rotten_flesh",count:1}}' % (X + 13, y, Z, nbt))
        mc.rcon("summon experience_orb %d.5 %d %d.5 {%s,Value:3}" % (X + 14, y, Z, nbt))
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
        self.assertIn("maximum of 100", mc.rcon("bossbar get %s max" % boss.bar))
        self.assertEqual(boss.health(), (100.0, 2))
        mc.rcon("damage @e[tag=%s_0,limit=1] 20 minecraft:magic" % boss.tag)
        self.assertEqual(boss.update(), 2)
        self.assertIn("80", mc.rcon("bossbar get %s value" % boss.bar))
        mc.rcon("kill @e[tag=%s]" % boss.tag)
        boss.update()
        self.assertEqual(boss.update(), 0)
        self.assertIn("No bossbar exists", mc.rcon("bossbar get %s max" % boss.bar))

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
v = values()
world.gamerule("mob_griefing", %s)
world.objective("dummy", "kills")
area.forceload((%d, 0, %d, %d, 0, %d), wait=5)
mobs.Boss("Probe Boss", "zombie", health=40, nbt="NoAI:1b").spawn(at=(%d.5, %d, %d.5))
mc.log("ready")
for p in loop(items.clicks(v["player"], "Probe Wand", every=0.05)):
    pass
''' % ("False" if old == "true" else "True", X + 300, Z + 300, X + 340, Z + 340, X, SKY + 1, Z))
        env = dict(os.environ, PYTHONPATH=LIB, VFG_INSTANCE="kitprobe", VFG_RUN_DIR=run_dir("kitprobe"))
        proc = subprocess.Popen([sys.executable, "-u", "main.py"], cwd=folder, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.addCleanup(lambda: proc.poll() is None and (proc.send_signal(signal.SIGTERM), proc.communicate(timeout=5)))
        bar = "bossbar get vfg:kitprobe_boss1 max"
        deadline = time.time() + 20
        while "No bossbar" in mc.rcon(bar) and time.time() < deadline:
            time.sleep(0.2)
        time.sleep(0.5)
        self.assertIn("maximum of 40", mc.rcon(bar))
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
        self.assertIn("No bossbar exists", mc.rcon(bar))
        self.assertEqual(world.gamerule("mob_griefing"), old)
        self.assertNotIn("vfg_kitprobe_kills", mc.rcon("scoreboard objectives list"))
        self.assertIn("not marked", mc.rcon("forceload query %d %d" % (X + 320, Z + 320)))
        self.assertIn("Test failed", mc.rcon("execute if entity @e[tag=vfg_kitprobe]"))
        self.assertIn("vfg_click", mc.rcon("scoreboard objectives list"))


    def test_a_script_that_ends_by_itself_keeps_its_mobs_and_puts_the_rest_back(self):
        old = world.gamerule("mob_griefing")
        folder = os.path.join(tempfile.mkdtemp(), "kitender")
        os.makedirs(folder)
        with open(os.path.join(folder, "main.py"), "w") as f:
            f.write('''"""Probe: makes what the kit cleans up, then ends by itself."""
ASKS = []
from kit import values, area, mobs, world
import mc
v = values()
world.gamerule("mob_griefing", %s)
world.objective("dummy", "kills")
area.forceload((%d, 0, %d, %d, 0, %d), wait=5)
mobs.Boss("Ender Boss", "zombie", health=40, nbt="NoAI:1b").spawn(at=(%d.5, %d, %d.5))
mc.log("made, ending")
''' % ("False" if old == "true" else "True", X + 300, Z + 300, X + 340, Z + 340, X, SKY + 1, Z))
        env = dict(os.environ, PYTHONPATH=LIB, VFG_INSTANCE="kitender", VFG_RUN_DIR=run_dir("kitender"))
        out = subprocess.run([sys.executable, "-u", "main.py"], cwd=folder, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=30).stdout.decode()
        self.assertIn("ended by itself", out)
        self.assertNotIn("Traceback", out)
        self.assertIn("No bossbar exists", mc.rcon("bossbar get vfg:kitender_boss1 max"))
        self.assertEqual(world.gamerule("mob_griefing"), old)
        self.assertNotIn("vfg_kitender_kills", mc.rcon("scoreboard objectives list"))
        self.assertIn("not marked", mc.rcon("forceload query %d %d" % (X + 320, Z + 320)))
        # What it made stays: the boss's body, its bar gone, handed over to vfg_left_kitender.
        self.assertIn("Count: 1", mc.rcon("execute if entity @e[tag=vfg_left_kitender]"))
        self.assertNotIn("passed", mc.rcon("execute if entity @e[tag=vfg_kitender]").lower())
        mc.rcon("kill @e[tag=vfg_left_kitender]")

REALMC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "integration", "realmc")
sys.path.insert(0, REALMC)
try:
    import players as fake_players  # noqa: E402
    # players.py talks RCON on a connection of its own; realmc's client holds the same lock as
    # mc.rcon for each command (26.3 answers every client from one buffer), so nothing glues.
    PLAYERS = fake_players.on()
    CARPET = "Unknown or incomplete" not in PLAYERS.cmd("carpet")
except Exception:   # no Carpet on this server, or no realmc beside the repo
    CARPET = False

WAND = '''"""%s: logs each click."""
ASKS = []
from kit import values, items, loop
import mc
v = values()
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
        env = dict(os.environ, PYTHONPATH=LIB, VFG_INSTANCE=name, VFG_RUN_DIR=run_dir(name))
        proc = subprocess.Popen([sys.executable, "-u", "main.py"], cwd=folder, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.procs.append(proc)
        return proc

    def stop(self, proc):
        proc.send_signal(signal.SIGTERM)
        return proc.communicate(timeout=5)[0].decode()

    def hold(self, name):
        """Wait until Steve has the item, then pick the hotbar slot it is in; that slot."""
        deadline = time.time() + 10
        while not items.has("Steve", name) and time.time() < deadline:
            time.sleep(0.2)
        for slot in range(1, 10):
            PLAYERS.hotbar("Steve", slot)
            time.sleep(0.3)
            if items.held("Steve", name):
                return slot
        return None

    def click(self):
        time.sleep(0.4)
        PLAYERS.use("Steve")
        time.sleep(0.6)

    def test_two_wands_on_one_player(self):
        PLAYERS.hotbar("Steve", 1)
        items.give("Steve", "Beta Wand")                 # hotbar slot 1
        items.give("Steve", "Alpha Wand")                # hotbar slot 2
        a = self.start("kitalpha", "Alpha", 'clicks(v["player"], "Alpha Wand", give="never", every=0.05)')
        b = self.start("kitbeta", "Beta", 'clicks(v["player"], "Beta Wand", give="never", every=0.05)')
        time.sleep(1.5)
        PLAYERS.hotbar("Steve", 2)
        self.click()                                     # Alpha in the main hand: Alpha
        PLAYERS.player("Steve", "swapHands")             # Alpha to the off hand
        time.sleep(0.3)
        PLAYERS.hotbar("Steve", 1)
        time.sleep(0.3)
        self.assertTrue(items.held("Steve", "Alpha Wand") and items.held("Steve", "Beta Wand"))
        self.click()                                     # Beta in the main hand takes it: Beta only
        PLAYERS.hotbar("Steve", 2)                       # that slot is empty now
        self.click()                                     # Alpha alone, in the off hand: Alpha
        out_a, out_b = self.stop(a), self.stop(b)
        self.assertEqual(out_a.count("fired Steve"), 2, out_a)
        self.assertEqual(out_b.count("fired Steve"), 1, out_b)

    def test_a_charge_held_down_and_the_item_given_and_taken_back(self):
        proc = self.start("kitbomb", "Bomb", 'charged(v["player"], "Kit Bomb", 1.0, every=0.05)')
        self.assertTrue(self.hold("Kit Bomb"))
        time.sleep(0.3)
        PLAYERS.use_continuous("Steve")
        time.sleep(1.8)
        PLAYERS.stop("Steve")
        out = self.stop(proc)
        self.assertEqual(out.count("fired Steve"), 1, out)
        self.assertFalse(items.has("Steve", "Kit Bomb"))        # taken back on stop

    def test_scores_of_three_players_one_with_none(self):
        """26.3 answers "execute as @a run scoreboard players get @s x" with the players who
        have a score, glued ("Steve has 5 [x]Alex has 7 [x]"), and nothing for one who has none."""
        PLAYERS.spawn("Alex")
        PLAYERS.spawn("Sam")
        obj = "vfg_kit_live_s"
        mc.rcon("scoreboard objectives add %s dummy" % obj)
        try:
            mc.rcon("scoreboard players set Steve %s 5" % obj)
            mc.rcon("scoreboard players set Sam %s 9" % obj)
            reply = mc.rcon("execute as @a run scoreboard players get @s %s" % obj)
            self.assertNotIn("Alex", reply)
            self.assertNotIn("none is set", reply)
            stat = items.Stat(obj, "dummy")
            stat.seeded = time.time()                          # read it as it is first
            self.assertEqual(stat.scores(), {"Steve": 5, "Sam": 9})
            stat.seeded = 0.0                                  # then every player gets a score
            self.assertEqual(stat.scores(), {"Steve": 5, "Alex": 0, "Sam": 9})
        finally:
            mc.rcon("scoreboard objectives remove %s" % obj)

    def test_a_first_click_ever_counts(self):
        PLAYERS.spawn("Alex")
        mc.rcon("scoreboard players reset Alex vfg_click")
        PLAYERS.hotbar("Alex", 1)
        PLAYERS.clear("Alex")
        items.give("Alex", "Kit Wand")
        a = self.start("kitwand", "Wand", 'clicks("Alex", "Kit Wand", give="never", every=0.05)')
        time.sleep(3.0)
        PLAYERS.use("Alex")
        time.sleep(0.8)
        out = self.stop(a)
        self.assertEqual(out.count("fired Alex"), 1, out)

    def test_a_look_from_crouching_eyes(self):
        standing = aim.look("Steve", eyes="auto")
        PLAYERS.sneak("Steve")
        time.sleep(0.4)
        crouching = aim.look("Steve", eyes="auto")
        PLAYERS.sneak("Steve", False)
        self.assertAlmostEqual(standing.origin[1] - standing.feet[1], aim.EYES)
        self.assertAlmostEqual(crouching.origin[1] - crouching.feet[1], aim.SNEAK_EYES)

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
