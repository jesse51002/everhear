"""Tests for the kit's test helpers (scripts/_lib/kit/test.py, the guide, and kit/_stage/helpers.py) against the fake RCON server and
fake_world, with Carpet's /player answered by a small addition to it:

    uv run --no-project --python 3.12 python scripts/_test/test_kit_test.py

run_tests.py runs these too. The stage, a mob, the cast and the run as the box would start them
(the copy's start itself is caught, not made), each check's verdict from a watch written here,
the report and its exit code. What the stand-in and a launch do on a real server is checked live
by the library's tests (scripts/<name>/test.py, through vfg test on a realmc pair).
"""
import io
import json
import os
import re
import secrets
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(HERE), "_lib")
sys.path.insert(0, HERE)
sys.path.insert(0, LIB)
TMP = tempfile.mkdtemp(prefix="vfg-kit-test-test-")
if "VFG_MC_PROPERTIES" not in os.environ:
    with open(os.path.join(TMP, "unused.properties"), "w") as f:
        f.write("enable-rcon=true\nrcon.port=1\nrcon.password=unused\n")
    os.environ["VFG_MC_PROPERTIES"] = os.path.join(TMP, "unused.properties")

from fake_rcon import FakeRcon  # noqa: E402
from fake_world import World  # noqa: E402
import mc  # noqa: E402
from kit import area, test, world  # noqa: E402
from kit._stage import helpers as stage  # noqa: E402


class CarpetWorld(World):
    """fake_world with Carpet's /player (spawn, kill, use, sneak, stop, hotbar), and what the stage
    sends besides: time, rotate, effect, gamemode."""

    def __init__(self):
        World.__init__(self)
        self.acts = []
        self.gamerules.update(spawn_mobs="true", spawn_phantoms="true")

    def _cmd_player(self, args, ctx):
        name, rest = args.split(" ", 1)
        if rest.startswith("spawn"):
            m = re.search(r"at (\S+) (\S+) (\S+)", rest)
            self.add_player(name, tuple(float(v) for v in m.groups()))
            return ""
        if rest == "kill":
            self.players.pop(name, None)
            return ""
        self.acts.append((name, rest))
        return ""

    def _cmd_time(self, args, ctx):
        return "Set the time to 18000"

    def _cmd_rotate(self, args, ctx):
        return "Rotated"

    def _cmd_effect(self, args, ctx):
        return "Applied effect"

    def _cmd_gamemode(self, args, ctx):
        return "Set own game mode"


class TestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = secrets.token_hex(8)
        cls.fake = FakeRcon(cls.password, responder=lambda c: cls.world.respond(c)).start()
        cls.saved = mc._settings

    @classmethod
    def tearDownClass(cls):
        mc._drop()
        mc._settings = cls.saved
        cls.fake.stop()

    def setUp(self):
        type(self).world = CarpetWorld()
        mc._drop()
        mc._settings = (self.password, self.fake.port)
        world._rules.clear()
        area._forced.clear()
        self.stoppers = list(mc._stoppers)
        self.hook = sys.excepthook
        self.run_dir = os.path.join(TMP, self._testMethodName, "run")   # instances.here(): <box>/run
        os.makedirs(self.run_dir, exist_ok=True)
        env = {"VFG_TEST": "1", "VFG_RUN_DIR": self.run_dir, "VFG_SERVER": "main",
               "VFG_INCANTATIONS": os.path.join(TMP, "incantations-%s.json" % self._testMethodName)}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.started = []
        stage._made.clear()      # one Test per file: each unit test is a file of its own here

    def tearDown(self):
        self.env.stop()
        mc._stoppers[:] = self.stoppers
        sys.excepthook = self.hook

    def stage(self, player="Steve", **kw):
        stage._made.clear()
        t = test.Test(player, **kw)
        t._cleared = True        # nothing to clear: the fake world is new for every test
        t._box.start = lambda meta, base=None: self.started.append((meta, base)) or ("copy_%d" % len(self.started),
                                                                                     1, None)
        t._box.wait = lambda *a, **k: {}
        return t

    def sent(self, pattern):
        return [c for c in self.world.commands if re.search(pattern, c)]


class TestStage(TestCase):
    def test_only_on_the_test_server(self):
        with mock.patch.dict(os.environ, {"VFG_TEST": ""}):
            with self.assertRaises(SystemExit) as e:
                test.Test("Steve")
        self.assertIn("only through vfg test", str(e.exception))

    def test_the_stage_in_the_sky_far_out_and_the_player_on_it(self):
        with mock.patch.object(stage.time, "sleep"):
            t = self.stage()
        x, y, z = test.X, test.FLOOR, test.Z
        self.assertTrue(self.sent(r"^forceload add %d %d %d %d$" % (x - 48, z - 48, x + 47, z + 47)) or
                        self.sent(r"^forceload add "), self.world.commands)
        self.assertEqual(self.sent(r"^fill .* barrier$"), ["fill %d %d %d %d %d %d barrier" % (
            x - test.HALF, y, z - test.HALF, x + test.HALF, y, z + test.HALF)])
        self.assertTrue(self.sent(r"^player Steve spawn at 3000.5 296 3000.5 facing 180 0 in minecraft:overworld"))
        self.assertTrue(self.sent(r"^player Alex spawn at 2996.5 296 3000.5 facing 180 0"))   # 4 to his left
        self.assertEqual(t.players, ["Steve", "Alex"])
        self.assertTrue(self.sent(r"^weather clear$"))
        self.assertTrue(self.sent(r"^gamerule spawn_mobs false$") and self.sent(r"^gamerule spawn_phantoms false$"))
        self.assertTrue(self.sent(r"^time set midnight$"))
        self.assertEqual(t.start["Steve"], {"pos": (3000.5, 296, 3000.5), "health": 20.0})
        self.assertGreaterEqual(y, 290)
        self.assertGreaterEqual(min(abs(x), abs(z)), 1000)
        # (right, up, forward) from the caster's feet, facing north
        self.assertEqual(t.xyz(2, 1, 10), (3002.5, 297, 2990.5))
        self.assertEqual(t.at(0, 0, 20), "3000.5 296 2980.5")

    def test_a_mob_is_tagged_still_and_has_the_health_asked(self):
        with mock.patch.object(stage.time, "sleep"):
            t = self.stage()
            husk = t.mob("husk", 0, 0, 12, health=100)
            pushed = t.mob("zombie", 3, 0, 0, still=False)
        self.assertEqual(husk, "husk_1")
        cmd = self.sent(r"^summon husk ")[0]
        for part in ("NoAI:1b", 'Tags:["vfg_test","vfg_test_husk_1"]', "Health:100f",
                     '{id:"minecraft:max_health",base:100}', "3000.5 296 2988.5"):
            self.assertIn(part, cmd)
        self.assertEqual(t.start["husk_1"]["health"], 100.0)
        cmd = self.sent(r"^summon zombie ")[0]
        self.assertNotIn("NoAI", cmd)
        self.assertIn('{id:"minecraft:movement_speed",base:0.0}', cmd)
        self.assertEqual(t.mobs[pushed], "@e[tag=vfg_test_zombie_2,limit=1]")
        cube = t.blocks("stone", 0, 0, 20, size=3)
        self.assertEqual(tuple(cube), (2999, 296, 2979, 3001, 298, 2981))
        self.assertEqual(tuple(t.region(0, 0, 13, size=15)), (2993, 296, 2980, 3007, 310, 2994))


class TestWhere(TestCase):
    def test_each_thing_says_where_it_is_as_the_caster_faces_once(self):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(stage.time, "sleep"):
            t = self.stage()
            t.mob("husk", 0, 0, 12, health=100)
            t.mob("zombie", -2, 1, 3)
            t.blocks("stone", 0, 0, 20, size=3)
            t.ground(0, 10, size=9)
            t.mob("cow", 0, 0, -3, behind=True)
            t.player("Alex", 5, 0, 0)
            t.stand_in()
        self.assertEqual([ln for ln in out.getvalue().splitlines() if not ln[:2].isdigit()], [   # the kit's log out
            "AT         Steve: the caster, facing north (forward is -z, right is +x), x 3000.5 y 296 z 3000.5",
            "AT         Alex: 4 left, x 2996.5 y 296 z 3000.5",
            "AT         husk_1: 12 ahead, x 3000.5 y 296 z 2988.5",
            "AT         zombie_2: 3 ahead, 2 left, 1 up, x 2998.5 y 297 z 2997.5",
            "AT         stone cube 3x3x3: 19 to 21 ahead, 1 left to 1 right, 0 to 2 up, x 2999..3001 y 296..298 "
            "z 2979..2981",
            "AT         ground 9x9, 4 deep: 6 to 14 ahead, 4 left to 4 right, 1 to 4 below his feet, x 2996..3004 "
            "y 292..295 z 2986..2994",
            "AT         cow_3: 3 behind, x 3000.5 y 296 z 3003.5",
            "AT         Alex: 5 right, x 3005.5 y 296 z 3000.5",
            "AT         stand_in: 1 left, x 2999.5 y 296 z 3000.5"])
        self.assertTrue(self.sent(r'^summon villager .*"vfg_test_stand_in"'))
        self.assertTrue(self.sent(r"^fill 2996 295 2986 3004 295 2994 grass_block$"))
        self.assertTrue(self.sent(r"^fill 2996 296 2986 3004 296 2994 short_grass replace air$"))

    def test_a_target_behind_him_or_inside_a_cube_is_an_error(self):
        with redirect_stdout(io.StringIO()), mock.patch.object(stage.time, "sleep"):
            t = self.stage()
            with self.assertRaisesRegex(test.TestError, r"husk_1 at 10 behind: forward is the way he faces \(north, "
                                                        r"-z\); something in front of him has forward > 0"):
                t.mob("husk", 0, 0, -10)
            with self.assertRaisesRegex(test.TestError, r"forward > 0"):
                t.blocks("stone", 0, 0, -12)
            t.blocks("stone", 0, 0, 12, size=3)
            with self.assertRaisesRegex(test.TestError, r"husk_2 at 12 ahead, 1 up would be inside the stone cube "
                                                        r"3x3x3 \(11 to 13 ahead, 1 left to 1 right, 0 to 2 up\)"):
                t.mob("husk", 0, 1, 12)
            t.mob("husk", 0, 0, 9)
            with self.assertRaisesRegex(test.TestError, r"the stone cube \(8 to 10 ahead.*\) would hold husk_3"):
                t.blocks("stone", 0, 0, 9)


class TestStart(TestCase):
    def test_a_cast_starts_the_incantation_as_the_bridge_starts_a_heard_one(self):
        import incantations
        incantations.Store().put({"player": "Steve", "phrase": "zombie party", "action": "zombie_party_steve",
                                  "script": "zombie_party_steve", "source": "made", "server": "main",
                                  "values": {"count": 3, "players": "@a"}, "caster_fields": ["players"],
                                  "live": ["count"], "runs": "once"})
        out = io.StringIO()
        with mock.patch.object(stage.time, "sleep"), redirect_stdout(out):
            t = self.stage()
            self.assertEqual(t.cast("Zombie party"), "copy_1")
        self.assertIn("CAST       Steve says 'Zombie party': zombie_party_steve with count=3", out.getvalue())
        meta, base = self.started[0]
        self.assertEqual(meta, {"action": "zombie_party_steve", "script": "zombie_party_steve", "source": "made",
                                "server": "main", "values": {"count": 3, "players": "Steve"}, "live": ["count"],
                                "runs": "once", "task": "cast"})
        self.assertIsNone(base)
        self.assertEqual(t.copies, ["copy_1"])
        with self.assertRaisesRegex(test.TestError, r"Steve has no incantation 'fire ball' on the test server: bind it "
                                                    r"first, on the live server \(vfg action run incantation_create "):
            t.cast("fire ball")

    def test_a_run_takes_its_defaults_clamps_and_fixed_values(self):
        actions = {"railgun": {"script": "railgun", "source": "library", "runs": "until_stopped",
                               "fields": {"player": {"default": "@a"}, "cooldown": {"default": 2, "ge": 0, "le": 60,
                                                                                     "live": True},
                                          "colour": {"default": "red"}},
                               "defaults": {"cooldown": 1}, "fixed": {"colour": "blue"}}}
        path = os.path.join(TMP, "actions.json")
        with open(path, "w") as f:
            json.dump(actions, f)
        with mock.patch.dict(os.environ, {"VFG_TEST_ACTIONS": path}), mock.patch.object(stage.time, "sleep"):
            t = self.stage()
            t.run("railgun", player="Steve", cooldown=500)
            with self.assertRaisesRegex(test.TestError, r"railgun has no value range; it has colour, cooldown, player"):
                t.run("railgun", range=5)
            with self.assertRaisesRegex(test.TestError, r"no action nuke here"):
                t.run("nuke")
        meta, base = self.started[0]
        self.assertEqual(meta["values"], {"player": "Steve", "cooldown": 60, "colour": "blue"})
        self.assertEqual((meta["live"], meta["runs"], meta["task"], base), (["cooldown"], "until_stopped", "test",
                                                                             "test_railgun"))

    def test_bind_then_cast_and_a_press_after_a_cast_is_warned(self):
        actions = {"fireball_cast": {"script": "fireball_cast", "source": "library", "runs": "once",
                                     "fields": {"player": {"type": "players", "default": "@a"},
                                                "explosion_power": {"type": "number", "default": 3, "ge": 0, "le": 16,
                                                                    "live": True}}}}
        path = os.path.join(TMP, "actions-bind.json")
        with open(path, "w") as f:
            json.dump(actions, f)
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"VFG_TEST_ACTIONS": path}), mock.patch.object(stage.time, "sleep"), \
                redirect_stdout(out):
            t = self.stage()
            t.bind("fireball_cast", "fire ball", explosion_power=40)
            t.cast("fire ball")
            t.use()
            t.sneak(1)
        meta, _base = self.started[0]
        self.assertEqual(meta["values"], {"player": "Steve", "explosion_power": 16})   # the caster's, clamped
        self.assertEqual((meta["task"], meta["runs"], meta["live"]), ("cast", "once", ["explosion_power"]))
        warns = [ln for ln in out.getvalue().splitlines() if ln.startswith("WARN")]
        self.assertEqual(warns, ["WARN       a cast fires the ability at once: a crouch or click after the cast tests "
                                 "a trigger the real cast will not press"])        # once
        import incantations
        t._cleared = False
        with mock.patch.object(stage.time, "sleep"):
            t._clear()
        self.assertIsNone(incantations.Store().find("Steve", "fire ball"))   # the test's binding goes after

    def test_a_cast_names_its_values_and_what_its_action_fills_in(self):
        import incantations
        folder = os.path.join(TMP, self._testMethodName, "made", "main", "scripts", "zap")   # as vfg deployed it
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "action_defaults.json"), "w") as f:
            json.dump({"zap": {"values": {"player": "@a", "power": 5, "reach": 30, "width": 2}, "live": []}}, f)
        incantations.Store().put({"player": "Steve", "phrase": "big zap", "action": "zap", "script": "zap",
                                  "source": "made", "server": "main", "values": {"player": "", "power": 9, "reach": 20},
                                  "asked": {"power": 9}, "caster_fields": ["player"], "runs": "once"})
        out = io.StringIO()
        with mock.patch.object(stage.time, "sleep"), redirect_stdout(out):
            t = self.stage()
            t.cast("big zap")
        lines = [ln for ln in out.getvalue().splitlines() if ln[:4] in ("CAST", "BIND", "WARN")]
        self.assertEqual(lines, [
            "CAST       Steve says 'big zap': zap with power=9, reach=30, width=2",
            "BINDING    from zap's defaults now, as a heard cast takes them: reach=30 (bound 20), width=2 (not bound)"])
        self.assertEqual(self.started[0][0]["asked"], ["player", "power"])   # Box.start fills the same in

    def test_using_it_is_carpet_and_vanilla(self):
        with mock.patch.object(stage.time, "sleep"):
            t = self.stage()
            t.use()
            t.sneak(2)
            t.look(pitch=-30, turn=90)
            t.use(draw=1.2)
        self.assertEqual(self.world.acts[-7:], [("Steve", "use once"), ("Steve", "sneak"), ("Steve", "unsneak"),
                                                ("Steve", "sneak"), ("Steve", "unsneak"),
                                                ("Steve", "use continuous"), ("Steve", "stop")])
        self.assertTrue(self.sent(r"^rotate Steve 270 -30$"))


def row(pos, health, effects=(), fire=0):
    return {"pos": pos, "health": health, "effects": list(effects), "motion": (0, 0, 0), "fire": fire}


class TestChecks(TestCase):
    def watched(self, rows):
        with mock.patch.object(stage.time, "sleep"):
            t = self.stage()
        t.start["husk_1"] = {"pos": (3000.5, 296, 2988.5), "health": 100.0}
        t.mobs["husk_1"] = "@e[tag=vfg_test_husk_1,limit=1]"
        t.trace = [dict(r, t=i) for i, r in enumerate(rows)]
        for r in t.trace:        # Alex, who is always there, stands still unless a row says otherwise
            r.setdefault("Alex", row((2996.5, 296, 3000.5), 20.0))
        t._acted = 0             # these rows came after it
        return t

    def test_hurt_killed_and_unhurt_from_the_watch(self):
        t = self.watched([{"Steve": row((3000.5, 296, 3000.5), 20.0), "husk_1": row((3000.5, 296, 2988.5), 100.0)},
                          {"Steve": row((3000.5, 296, 3000.5), 20.0), "husk_1": row((3000.5, 296, 2988.5), 62.0)}])
        self.assertEqual(t.hurt("husk_1"), (True, "husk_1 health 100 -> 62 (lost 38, at least 1)"))
        self.assertEqual(t.hurt("husk_1", at_least=50), (False, "husk_1 health 100 -> 62 (lost 38, at least 50); it "
                                                                "first lost health 1 s after the start of the test; it "
                                                                "was 12 ahead"))
        self.assertEqual(t.killed("husk_1"), (False, "husk_1 alive at the end, health 62"))
        self.assertEqual(t.others_unhurt(), (True, "Alex not hurt"))
        self.assertEqual(t.unhurt(), (True, "Steve health 20 -> 20"))
        self.assertEqual(t.unhurt("husk_1")[0], False)
        t.trace.append({"Steve": row((3000.5, 296, 3000.5), 20.0), "husk_1": None})
        self.assertEqual(t.killed("husk_1"), (True, "husk_1 died"))
        self.assertEqual(t.hurt("husk_1"), (True, "husk_1 died"))

    def test_moved_judges_the_caster_alone_and_names_a_stand_in_pushed_instead(self):
        still = row((3000.5, 296, 3000.5), 20.0)
        t = self.watched([{"Steve": still}, {"Steve": row((3000.5, 302.5, 3000.5), 20.0)}])
        self.assertEqual(t.moved(4, "up"), (True, "Steve moved 6.5 up (at least 4, since the start of the test)"))
        self.assertEqual(t.moved(1, "forward"), (False, "Steve moved 0 forward (at least 1, since the start of the "
                                                        "test); Steve ended 6.5 up"))
        # the stand-in thrown, Steve not: a FAIL that says so, never a PASS on the stand-in
        t = self.watched([{"Steve": still}, {"Steve": still}])
        t.mobs["stand_in"] = "@e[tag=x]"
        t.start["stand_in"] = {"pos": (2999.5, 296, 3000.5), "health": 1000.0}
        t.trace[0]["stand_in"] = row((2999.5, 296, 3000.5), 1000.0)
        t.trace[1]["stand_in"] = row((2998.5, 299, 3000.5), 990.0)
        self.assertEqual(t.moved(2, "up"), (False, "Steve moved 0 up (at least 2, since the start of the test); the "
                                                   "stand-in was thrown 3 but Steve did not move: the ability pushes "
                                                   "what is near him, not him; Steve ended where he stands"))
        self.assertEqual(t.moved(4, "up")[0], False)
        self.assertEqual(t.moved(2, "up", who="stand_in")[0], True)

    def test_moved_counts_from_the_last_cast_run_or_use(self):
        t = self.watched([{"Steve": row((3000.5, 306, 3000.5), 20.0)},       # before the cast: thrown by another
                          {"Steve": row((3000.5, 306, 3000.5), 20.0)}])
        t._acted = 1
        t._base = {"Steve": ((3000.5, 306, 3000.5), set(), False)}
        self.assertEqual(t.moved(3)[0], False)
        t._acted = 0
        t._base = {}
        self.assertEqual(t.moved(3)[0], True)

    def test_effects_items_blocks_and_the_logs(self):
        t = self.watched([{"Steve": row((3000.5, 296, 3000.5), 20.0, ["speed"]), "husk_1": row((0, 0, 0), 100.0)}])
        self.assertEqual(t.effect("speed"), (True, "Steve had speed"))
        self.assertEqual(t.effect("minecraft:levitation")[0], False)
        self.assertEqual(t.holds("Railgun", within=0), (False, "Steve has no Railgun"))
        self.world.hold("Steve", main="railgun")
        self.assertEqual(t.holds("Railgun", within=0), (True, "Steve has Railgun"))
        self.assertEqual(t.no_errors(), (None, "nothing was started"))
        t.copies = ["gun"]
        with open(os.path.join(self.run_dir, "gun.log"), "w") as f:
            f.write("--- header ---\n12:00:00 gun fired\n")
        self.assertEqual(t.no_errors(), (True, "no error in the logs of gun"))
        with open(os.path.join(self.run_dir, "gun.log"), "a") as f:
            f.write("Traceback (most recent call last):\nNameError: name 'x' is not defined\n")
        self.assertEqual(t.no_errors(), (False, "gun: Traceback (most recent call last):"))
        with open(os.path.join(self.run_dir, "gun.log"), "w") as f:     # a command Minecraft refused, logged
            f.write("--- header ---\n12:00:00 gun Incorrect argument for command ...ive @s <--[HERE]\n")
        self.assertEqual(t.no_errors()[0], False)
        cube = t.region(0, 0, 0, size=3)
        asked = []
        with mock.patch.object(stage.world, "passes", lambda conds: asked.extend(conds) or [True] * 14 + [False] * 13):
            self.assertEqual(t.gone(cube), (True, "14 of its 27 blocks are air (at least 50%)"))
            self.assertEqual(t.turned(cube, "packed_ice", 0.6), (False, "14 of its 27 blocks are packed_ice "
                                                                         "(at least 60%); it is 1 behind to 1 ahead, 1 "
                                                                         "left to 1 right, 0 to 2 up"))
            self.assertEqual(t.built(cube, 0.4), (True, "13 of its 27 blocks are built (at least 40%)"))
        self.assertEqual(asked[0], "if block 2999 296 2999 air")
        self.assertEqual(len(asked), 81)
        with mock.patch.object(stage.world, "passes", lambda conds: [conds[0].endswith(" ice")] * len(conds)):
            self.assertEqual(t.turned(cube, ["packed_ice", "ice"]), (True, "27 of its 27 blocks are packed_ice or ice "
                                                                            "(at least 10%)"))

    def test_fire_is_read_from_fire_ticks_and_fire_blocks(self):
        t = self.watched([{"Steve": row((3000.5, 296, 3000.5), 20.0), "husk_1": row((3000.5, 296, 2988.5), 100.0)},
                          {"Steve": row((3000.5, 296, 3000.5), 20.0), "husk_1": row((3000.5, 296, 2988.5), 99, fire=80)}])
        self.assertEqual(t.burning("husk_1"), (True, "husk_1 burned (Fire up to 80 ticks)"))
        self.assertEqual(t.burning("Steve"), (False, "Steve never burned"))
        with self.assertRaisesRegex(test.TestError, r"burning is not an effect: t.burning\(who\)"):
            t.effect("fire", "husk_1")
        asked = []
        with mock.patch.object(stage.world, "passes", lambda conds: asked.extend(conds) or [True] * 3 + [False] * (
                len(conds) - 3)):
            self.assertEqual(t.fire_near(0, 0, 10, radius=2, count=2), (True, "3 fire blocks within 2 of 10 ahead "
                                                                              "(at least 2)"))
        self.assertEqual(asked[0], "if block 2998 295 2988 #minecraft:fire")

    def test_nothing_fired_since_the_cast_is_cant_tell_not_a_pass(self):
        still = {"Steve": row((3000.5, 296, 3000.5), 20.0), "Alex": row((2996.5, 296, 3000.5), 20.0),
                 "husk_1": row((3000.5, 296, 2988.5), 100.0)}
        t = self.watched([still, still])
        t.copies = ["gun"]
        t._what = "the cast"
        self.assertEqual(t.unhurt(), (None, "nothing fired since the cast: no result or log line, nothing new on the "
                                            "stage, and nobody moved, was hurt or set alight"))
        self.assertEqual(t.others_unhurt()[0], None)
        self.assertEqual(t.no_errors()[1], "no error, but nothing fired since the cast: " + stage._QUIET)
        with open(os.path.join(self.run_dir, "gun.log"), "w") as f:     # a line the copy logged after it
            f.write("--- header ---\n12:00:00 gun summoned 2 withers\n")
        self.assertEqual(t.unhurt()[0], True)
        os.remove(os.path.join(self.run_dir, "gun.log"))
        with open(os.path.join(self.run_dir, "gun.result.json"), "w") as f:
            json.dump({"summary": "fired", "facts": {}, "at": 5}, f)
        self.assertEqual(t.unhurt(), (True, "Steve health 20 -> 20"))
        self.assertEqual(t.no_errors()[0], True)


class TestResult(TestCase):
    def write_result(self, copy, summary, at, **facts):
        with open(os.path.join(TMP, self._testMethodName, "run", copy + ".result.json"), "w") as f:
            json.dump({"summary": summary, "facts": facts, "at": at}, f)

    def test_the_latest_result_since_the_last_cast_run_or_use(self):
        with mock.patch.object(stage.time, "sleep"):
            t = self.stage()
        with self.assertRaisesRegex(test.TestError, r"nothing was started"):
            t.result()
        t.copies = ["test_wand"]
        t._acted = 1000.0
        self.write_result("test_wand", "Steve has the wand: click to fire", 999.0)   # its first, at its start
        with mock.patch.object(stage.time, "sleep"):
            self.assertEqual(t.result(within=0), {})
            self.assertEqual(t.said("fired", within=0), (False, "test_wand wrote no result() within 0 s of the "
                                                                "start of the test"))
        self.write_result("test_wand", "Steve fired: 2 hit", 1001.5, hits=2, target="husk_1")
        self.assertEqual(t.result("wand"), {"summary": "Steve fired: 2 hit", "facts": {"hits": 2, "target": "husk_1"}})
        self.assertEqual(t.said("FIRED"), (True, "test_wand said 'Steve fired: 2 hit'"))
        self.assertEqual(t.said("husk_1")[0], True)                     # a fact counts
        self.assertEqual(t.said("missed")[0], False)
        with open(os.path.join(TMP, self._testMethodName, "run", "test_wand.log"), "w") as f:
            f.write("--- header ---\n12:00:01 test_wand fired\n12:00:02 test_wand hit husk_1\n")
        out = io.StringIO()
        with redirect_stdout(out):
            t.log(1)
        self.assertEqual(out.getvalue(), "LOG        test_wand: 12:00:02 test_wand hit husk_1\n")

    def test_a_use_or_a_cast_moves_the_mark(self):
        with mock.patch.object(stage.time, "sleep"):
            t = self.stage()
            before = t._acted
            t.use()
        self.assertGreaterEqual(t._acted, before)
        t._acted = 0
        with mock.patch.object(stage.time, "sleep"):
            t.sneak(1)
        self.assertGreater(t._acted, 0)


class TestReport(TestCase):
    def test_one_line_a_check_then_the_result_and_the_exit_code(self):
        with mock.patch.object(stage.time, "sleep"):
            t = self.stage()
        t.check("it hits", test.verdict(True, "husk 100 -> 62"))
        t.check("it launches him", test.verdict(None, "a fake player may not show it"))
        t.check("a bare bool", 3 > 2)
        with self.assertRaisesRegex(test.TestError, r"takes a verdict"):
            t.check("bad", "yes")
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(stage.os, "_exit", side_effect=SystemExit) as ex:
            with self.assertRaises(SystemExit):
                t.done()
        self.assertEqual(ex.call_args[0][0], 0)
        self.assertEqual(out.getvalue().splitlines(), [
            "PASS       it hits — husk 100 -> 62", "CAN'T TELL it launches him — a fake player may not show it",
            "PASS       a bare bool — the test's own True (test.verdict(ok, why) would say why)",
            "RESULT: 2 passed, 0 failed, 1 can't tell"])
        t.check("it breaks blocks", test.verdict(False, "0 of 27 are air"))
        with redirect_stdout(io.StringIO()), mock.patch.object(stage.os, "_exit", side_effect=SystemExit) as ex:
            with self.assertRaises(SystemExit):
                t.done()
        self.assertEqual(ex.call_args[0][0], 1)
        t.checks = []
        with redirect_stdout(io.StringIO()), mock.patch.object(stage.os, "_exit", side_effect=SystemExit) as ex:
            with self.assertRaises(SystemExit):
                t.done()
        self.assertEqual(ex.call_args[0][0], 2)

    def test_a_fail_or_cant_tell_is_false(self):
        self.assertTrue(test.verdict(True, ""))
        self.assertFalse(test.verdict(False, ""))
        self.assertFalse(test.verdict(None, ""))
        self.assertEqual(test.verdict(0, "x"), (False, "x"))


class TestMistakes(TestCase):
    def test_a_wrong_helper_or_argument_names_the_right_ones(self):
        with redirect_stdout(io.StringIO()), mock.patch.object(stage.time, "sleep"):
            t = self.stage()
        with self.assertRaisesRegex(test.TestError, r"t has no box; a copy's result: t.result\(copy\).*its helpers: "
                                                    r"at, bind, block, blocks, built, burning, cast, check, cmd, done, "):
            t.box.result("x")
        with self.assertRaisesRegex(test.TestError, r"t.hurt\(who, at_least=1\): missing a required argument: 'who'"):
            t.hurt()
        with self.assertRaisesRegex(test.TestError, r"t.cast\(phrase, who=None\): got an unexpected keyword argument "
                                                    r"'power'; its values are the binding's"):
            t.cast("fire ball", power=5)
        self.assertFalse(hasattr(t, "nothing_like_it"))

    def test_one_test_per_file(self):
        with redirect_stdout(io.StringIO()), mock.patch.object(stage.time, "sleep"):
            self.stage()
            with self.assertRaisesRegex(test.TestError, r"one test per file: use the same t"):
                test.Test("Steve")

    def test_cmd_sets_the_stage_and_never_changes_a_player(self):
        with redirect_stdout(io.StringIO()), mock.patch.object(stage.time, "sleep"):
            t = self.stage()
            husk = t.mob("husk", 0, 0, 10)
        for c in ("effect give Steve minecraft:levitation 1 0 true", "effect clear @a", "/tp Alex 0 300 0",
                  "attribute Steve minecraft:gravity base set 0", "scoreboard players set @a vfg_sneak 0",
                  "data modify entity Steve Motion set value [0d,2d,0d]", "execute as Steve run effect give @s speed",
                  "player Alex kill", "effect give @e speed"):
            with self.assertRaisesRegex(test.TestError, r"refused: a test only watches"):
                t.cmd(c)
        for c in ("give Steve bow", "effect give %s slowness 5" % t.mobs[husk], "player Steve move forward",
                  "attribute Steve minecraft:gravity get", "execute as @e[type=husk] run effect give @s speed",
                  "data get entity Steve Pos", "scoreboard players get Steve x"):
            t.cmd(c)
        with mock.patch.object(stage.mc, "rcon", return_value="Unknown or incomplete command, see below for error"
                                                             "...<--[HERE]"):
            with self.assertRaisesRegex(test.TestError, r"t.cmd\('setblok 1 2 3 stone'\): Minecraft did not run it: "
                                                        r"Unknown or incomplete command"):
                t.cmd("setblok 1 2 3 stone")
        with mock.patch.object(stage.mc, "rcon", return_value="Expected whitespace to end one argument"):
            with self.assertRaisesRegex(test.TestError, r"did not run it: Expected whitespace"):
                t.cmd("fill 1 2 3 4 5 6 stone[")

    def test_a_fail_says_where_the_copy_said_it_landed(self):
        with redirect_stdout(io.StringIO()), mock.patch.object(stage.time, "sleep"):
            t = self.stage()
        t.copies = ["boom"]
        t.start["husk_1"] = {"pos": (3000.5, 296, 2990.5), "health": 40.0}
        t.mobs["husk_1"] = "@e[tag=vfg_test_husk_1,limit=1]"
        t.trace = [{"t": 1, "Steve": row((3000.5, 296, 3000.5), 20.0), "Alex": row((2996.5, 296, 3000.5), 20.0),
                    "husk_1": row((3000.5, 296, 2990.5), 40.0)}]
        t._acted = 0
        with open(os.path.join(self.run_dir, "boom.log"), "w") as f:
            f.write("--- header ---\n12:00:00 boom fireball from 3000.5 297.6 2999.5 to 3004.5 296.0 2980.5\n")
        self.assertEqual(t.hurt("husk_1"), (False, "husk_1 health 40 -> 40 (lost 0, at least 1); it was 10 ahead; "
                                                   "boom last named 3004.5 296.0 2980.5 (20 ahead, 4 right)"))


class TestTemplates(unittest.TestCase):
    def test_a_template_per_kind_short_whole_and_named_in_the_guide(self):
        import ast
        self.assertEqual(test.kinds(), ["area-blast", "build", "cast-launch", "cast-projectile", "item-use",
                                        "world-event"])
        for kind in test.kinds():
            with open(os.path.join(test.TEMPLATES, kind + ".py"), encoding="utf-8") as f:
                text = f.read()
            self.assertIn(kind, test.__doc__)
            filled = text.replace("{name}", "zap").replace("{player}", "Steve").replace("{phrase}", "big zap") \
                .replace("{item}", "Zapper")
            ast.parse(filled)
            body = [ln for ln in filled.split('"""')[2].splitlines() if ln.strip()]
            self.assertLessEqual(len(body), 18, kind)
            for part in ('t = test.Test("Steve")', "t.unhurt()", "t.no_errors()", "# TODO DONE WHEN", "t.done()"):
                self.assertIn(part, filled, (kind, part))
            self.assertEqual(filled.count("TODO"), 1, kind)
            trigger = "t.run(" if kind == "item-use" else "t.cast("
            self.assertIn(trigger, filled, kind)
            after = filled.split(trigger, 1)[1]
            if kind != "item-use":
                self.assertNotIn("t.use(", after, kind)       # the cast alone: nothing pressed after it
                self.assertNotIn("t.sneak(", after, kind)


if __name__ == "__main__":
    unittest.main()
