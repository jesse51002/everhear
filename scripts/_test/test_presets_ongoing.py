"""Tests for the "ongoing" presets and the presets that watch players, against the fake RCON
server and fake_world, under the Python EC2 has:

    uv run --python 3.9 python scripts/_test/test_presets_ongoing.py

boss, horde_wave, director, leaves_area, stands_still and beam_clash. Each sends the commands
it should; its VALUES refuse what is outside their limits; and nothing it made is left after
a stop: the ones that make things are run as scripts the way EC2 does (the preset's file with
a line of wiring under it), then sent SIGTERM.
"""
import importlib.util
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
PRESETS = os.path.join(os.path.dirname(HERE), "_presets")
sys.path.append(os.path.dirname(os.path.dirname(HERE)))  # the repo: a preset's values class imports actions._types
sys.path.insert(0, HERE)

import test_kit  # noqa: E402  (points mc at the fakes, as for the kit's own tests)
from test_kit import TMP  # noqa: E402
from fake_rcon import FakeRcon  # noqa: E402
from fake_world import World  # noqa: E402
import mc  # noqa: E402
from kit import aim, values, world  # noqa: E402

def load(name):
    """A preset's file as a module, as a script holds it once copied in."""
    spec = importlib.util.spec_from_file_location("preset_" + name, os.path.join(PRESETS, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


BOSS, HORDE, DIRECTOR = load("boss"), load("horde_wave"), load("director")
LEAVES, STANDS, CLASH = load("leaves_area"), load("stands_still"), load("beam_clash")
ALL = [BOSS, HORDE, DIRECTOR, LEAVES, STANDS, CLASH]


def vals(preset, *argv):
    return values(preset.VALUES, argv=list(argv), run_dir=tempfile.mkdtemp(dir=TMP), instance="t", poll=0.05)


def until(test, seconds=6.0):
    """Wait for test() to be true; its last answer."""
    end = time.time() + seconds
    got = test()
    while not got and time.time() < end:
        time.sleep(0.05)
        got = test()
    return got


def background(fn, *args, **kwargs):
    """fn in a thread the test's tearDown stops and joins: [its result]."""
    out = []
    th = threading.Thread(target=lambda: out.append(fn(*args, **kwargs)), daemon=True)
    th.start()
    test_kit.RUNNING.append(th)
    return out, th


class OngoingCase(test_kit.KitCase):
    def setUp(self):
        super().setUp()
        self.world.add_player("Alex", (30.5, 64.0, -3.5), rot=(90.0, 0.0))

    def tagged(self, prefix=None):
        prefix = prefix or mc.TAG
        return [e for e in self.world.entities if any(t == prefix or t.startswith(prefix + "_") for t in e["tags"])]


# ---- VALUES ---------------------------------------------------------------------------

class OngoingValuesTests(unittest.TestCase):
    def test_defaults_pass_and_values_outside_their_limits_are_refused(self):
        for p in ALL:
            values(p.VALUES, argv=[], run_dir=tempfile.mkdtemp(dir=TMP), instance="t")     # defaults are good
            for key, e in p.VALUES.items():
                kind = e["type"]
                if kind in ("int", "number"):
                    if "max" in e:
                        self.assertIn("clamped", values.check(key, e, e["max"] + 1)[1], key)
                    if "min" in e:
                        self.assertIn("clamped", values.check(key, e, e["min"] - 1)[1], key)
                    self.assertRaises(ValueError, values.check, key, e, "lots")
                elif kind in ("enum", "bool"):
                    self.assertRaises(ValueError, values.check, key, e, "nonsense")
                elif kind == "mob":
                    self.assertRaises(ValueError, values.check, key, e, "potion")
                elif kind == "list" and e.get("of") in ("number", "mob"):
                    self.assertRaises(ValueError, values.check, key, e, ["potion" if e["of"] == "mob" else "x"])
                elif kind == "player":
                    self.assertRaises(ValueError, values.check, key, e, "not a name!")

    def test_every_value_carries_its_presets_name(self):
        for p in ALL:
            name = p.__name__[len("preset_"):]
            for key in p.VALUES:
                self.assertTrue(key.startswith(name.split("_")[0] + "_"), key)


# ---- boss --------------------------------------------------------------------------------

class OngoingBossTests(OngoingCase):
    def bodies(self, b_tag=None):
        return [e for e in self.world.entities if any(re.match(r"^%s_boss\d+$" % mc.TAG, t) for t in e["tags"])]

    def test_two_bodies_one_bar_phases_abilities_and_the_loot_once(self):
        v = vals(BOSS, "boss_bodies=2", "boss_health=50", "boss_every=1", "boss_scale=1")
        seen = []
        out, th = background(BOSS.boss, world.Pos(0.5, 64, 0.5), v,
                             abilities=[lambda b, v: seen.append(("ability", b.phase))],
                             phases=[lambda b, v: seen.append(("phase", b.phase))])
        self.assertTrue(until(lambda: len(self.bodies()) == 2))
        bar = until(lambda: next((b for k, b in self.world.bossbars.items() if k.startswith("vfg:")), None))
        self.assertEqual(bar["max"], 100)
        body = self.bodies()
        self.assertIn('equipment:{head:{id:"netherite_helmet"', [c for c in self.world.commands if "summon" in c][0])
        self.assertTrue(self.sent("attribute @s attack_damage base set 12"))
        body[0]["health"] = 20.0
        self.assertTrue(until(lambda: bar["value"] == 70))
        self.assertNotIn(("phase", 1), seen)                     # 70 of 100 is not below a half
        body[1]["health"] = 20.0
        self.assertTrue(until(lambda: ("phase", 1) in seen))      # 40 of 100 is
        self.assertTrue(until(lambda: ("ability", 1) in seen))
        body[0]["health"] = body[1]["health"] = 0.0
        th.join(8)
        self.assertEqual(out, [True])
        self.assertEqual(len(self.sent("summon item")), 1)        # the death check fired once
        self.assertIn('{Item:{id:"diamond",count:8}}', self.sent("summon item")[0])
        self.assertEqual([k for k in self.world.bossbars if k.startswith("vfg:")], [])
        self.assertEqual(len([c for c in self.sent("title @a title") if "VICTORY" in c]), 1)
        self.assertEqual(seen.count(("phase", 1)), 1)
        self.assertNotIn(("phase", 2), seen)                      # 0 of 100 is a death, not a phase

    def test_the_default_ability_summons_minions_and_they_die_with_it(self):
        v = vals(BOSS, "boss_every=1", "boss_minions=2", "boss_phases=0.9", "boss_health=100")
        out, th = background(BOSS.boss, "Steve", v)
        self.assertTrue(until(lambda: len(self.bodies()) == 1))
        minions = lambda: [e for e in self.world.entities if any(t.endswith("_minion") for t in e["tags"])]
        self.assertTrue(until(lambda: len(minions()) == 2, 5))
        self.bodies()[0]["health"] = 50.0                        # below 0.9: it enrages
        self.assertTrue(until(lambda: self.sent("effect give @e[tag=%s_boss" % mc.TAG)))
        self.assertTrue(any("speed infinite 0 true" in c for c in self.sent("effect give")))
        self.bodies()[0]["health"] = 0.0
        th.join(8)
        self.assertEqual(out, [True])
        self.assertEqual(minions(), [])

    def test_no_room_for_it(self):
        self.world.solid.update(("minecraft:overworld", x, y, z) for x in range(-10, 31) for y in range(64, 70)
                                for z in range(-25, 18))
        self.assertFalse(BOSS.boss("Steve", vals(BOSS)))
        self.assertEqual(self.bodies(), [])
        self.assertEqual([k for k in self.world.bossbars if k.startswith("vfg:")], [])

    def test_a_player_value_waits_for_one_of_them_and_takes_one(self):
        out, th = background(BOSS.boss, "Steve,Nobody", vals(BOSS, "boss_bodies=1"))
        self.assertTrue(until(lambda: len(self.bodies()) == 1))
        time.sleep(1.5)
        self.assertEqual(len(self.bodies()), 1)
        self.assertTrue(th.is_alive())
        self.assertTrue(self.sent("kill @e[tag=%s_boss" % mc.TAG))      # leftovers first


# ---- horde_wave -------------------------------------------------------------------------------

class OngoingHordeTests(OngoingCase):
    def test_waves_round_each_player_up_to_the_cap_then_it_waits_for_them_to_die(self):
        v = vals(HORDE, "horde_per_player=3", "horde_every=1", "horde_cap=5", "horde_waves=2",
                 "horde_mobs=creeper,zombie")
        out, th = background(HORDE.horde_wave, "@a", v)
        horde = lambda: [e for e in self.world.entities if mc.TAG + "_horde" in e["tags"]]
        self.assertTrue(until(lambda: len(horde()) == 5))
        time.sleep(1.5)
        self.assertEqual(len(horde()), 5)                        # the cap holds through the second wave
        near = {p: [e for e in horde() if abs(e["pos"][0] - self.world.players[p]["pos"][0]) < 20] for p in ("Steve", "Alex")}
        self.assertTrue(near["Steve"] and near["Alex"])
        for c in self.sent("summon creeper"):
            self.assertIn("ExplosionRadius:0b", c)
        self.assertTrue(self.sent("Wave 1"))
        self.assertTrue(th.is_alive())                           # its mobs are not dead yet
        self.world.entities = [e for e in self.world.entities if e not in horde()]
        th.join(5)
        self.assertEqual(out, [2])


    def test_no_wave_announced_and_a_log_line_only_when_the_count_changed(self):
        v = vals(HORDE, "horde_per_player=1", "horde_every=1", "horde_cap=2", "horde_announce=false")
        background(HORDE.horde_wave, "@a", v)
        horde = lambda: [e for e in self.world.entities if mc.TAG + "_horde" in e["tags"]]
        self.assertTrue(until(lambda: len(horde()) == 2))
        time.sleep(1.2)
        self.assertFalse(self.sent("Wave"))


# ---- director -----------------------------------------------------------------------------------

class OngoingDirectorTests(OngoingCase):
    def test_each_player_gets_events_of_their_own_on_the_timer(self):
        v = vals(DIRECTOR, "director_events=ping,never", "director_weights=1,0", "director_min_s=1", "director_max_s=1")
        got = []
        background(DIRECTOR.director, "@a", v, events={"ping": lambda p, v: got.append(p)})
        self.assertTrue(until(lambda: "Steve" in got and "Alex" in got, 5))
        self.assertTrue(until(lambda: got.count("Steve") >= 2 and got.count("Alex") >= 2, 4))

    def test_one_clock_for_everyone_and_one_of_them_at_random(self):
        v = vals(DIRECTOR, "director_events=ping", "director_min_s=1", "director_max_s=1", "director_each=false")
        got = []
        background(DIRECTOR.director, "@a", v, events={"ping": lambda p, v: got.append((p, time.time()))})
        self.assertTrue(until(lambda: len(got) >= 3, 6))
        times = [t for _, t in got[:3]]
        self.assertTrue(all(b - a > 0.6 for a, b in zip(times, times[1:])), got)    # one event a second, not two
        self.assertTrue(set(p for p, _ in got) <= {"Steve", "Alex"})

    def test_the_built_in_events(self):
        v = vals(DIRECTOR)
        DIRECTOR._director_message("Steve", v)
        DIRECTOR._director_dark("Steve", v)
        DIRECTOR._director_sound("Steve", v)
        self.assertTrue(self.sent("tellraw Steve"))
        self.assertTrue(self.sent("effect give Steve darkness"))
        sounds = self.sent("playsound minecraft:")
        self.assertTrue(sounds and all(" ambient Steve " in c for c in sounds))
        # Steve looks along +x, so "behind him" is -x.
        self.assertTrue(all(float(c.split()[4]) < 10.5 for c in sounds), sounds)
        th = threading.Thread(target=DIRECTOR._director_mob, args=("Steve", v))
        th.start()
        self.assertTrue(until(lambda: [e for e in self.tagged() if e["type"] == "creeper"]))
        creeper = self.sent("summon creeper")[0]
        self.assertIn("NoAI:1b", creeper)
        self.assertIn("ExplosionRadius:0b", creeper)
        th.join(4)
        self.assertEqual(self.tagged(), [])

    def test_weights(self):
        v = vals(DIRECTOR, "director_events=a,b,c", "director_weights=0,1")
        self.assertEqual({DIRECTOR._director_pick(v) for _ in range(60)}, {"b", "c"})   # c has no weight: 1
        self.assertIsNone(DIRECTOR._director_pick(vals(DIRECTOR, "director_events=a", "director_weights=0")))


# ---- leaves_area and stands_still --------------------------------------------------------------

class OngoingLeavesTests(OngoingCase):
    def move(self, p, x, z, dim=None):
        with self.world.lock:
            self.world.players[p]["pos"][0], self.world.players[p]["pos"][2] = x, z
            if dim:
                self.world.players[p]["dim"] = dim

    def test_a_radius_round_where_they_started(self):
        v = vals(LEAVES, "leaves_radius=10")
        got, _ = test_kit.collect(LEAVES.leaves_area("Steve", v), 8)
        time.sleep(0.8)
        self.assertEqual(got, [])
        self.move("Steve", 25.5, -3.5)
        self.assertTrue(until(lambda: got))
        p, back = got[0][0]
        self.assertEqual((p, back[:3]), ("Steve", (10.5, 64.0, -3.5)))
        time.sleep(1.0)
        self.assertEqual(len(got), 1)                            # still out: once only
        self.move("Steve", 12.5, -3.5)
        time.sleep(0.8)
        self.move("Steve", 10.5, 20.5)
        self.assertTrue(until(lambda: len(got) == 2))
        self.move("Steve", 10.5, -3.5)
        time.sleep(0.8)
        self.assertEqual(len(got), 2)
        self.move("Steve", 10.5, -3.5, "minecraft:the_nether")   # the same x and z in another dimension is out
        self.assertTrue(until(lambda: len(got) == 3, 4))

    def test_a_box_sends_them_back_where_they_last_stood_inside(self):
        v = vals(LEAVES, "leaves_area=box", "leaves_box=0,60,-10,20,70,10")
        got, _ = test_kit.collect(LEAVES.leaves_area("Steve", v), 8)
        self.move("Steve", 18.5, -3.5)
        time.sleep(0.8)
        self.move("Steve", 22.5, -3.5)
        self.assertTrue(until(lambda: got))
        self.assertEqual(got[0][0][1][:3], (18.5, 64.0, -3.5))

    def test_another_players_reach(self):
        v = vals(LEAVES, "leaves_area=player", "leaves_of_player=Alex", "leaves_radius=25")
        got, _ = test_kit.collect(LEAVES.leaves_area("@a", v), 8)
        time.sleep(0.8)
        self.assertEqual(got, [])                                # 20 apart; Alex is never out of his own reach
        self.move("Steve", -20.5, -3.5)
        self.assertTrue(until(lambda: got))
        self.assertEqual((got[0][0][0], got[0][0][1][:3]), ("Steve", (30.5, 64.0, -3.5)))
        self.assertEqual(self.sent("summon"), [])


class OngoingStandsTests(OngoingCase):
    def test_staying_put_fires_with_a_warning_and_moving_resets_it(self):
        v = vals(STANDS, "stands_seconds=1.5", "stands_radius=3")
        got, _ = test_kit.collect(STANDS.stands_still("Steve", v), 8)
        self.assertTrue(until(lambda: got, 4))
        self.assertTrue(self.sent('"text": "Move! 1"'))
        first = got[0][1]
        with self.world.lock:
            self.world.players["Steve"]["pos"][0] += 2.0          # within 3: still standing still
        self.assertTrue(until(lambda: len(got) >= 2, 4))
        self.assertGreater(got[1][1] - first, 1.3)
        n = len(got)
        for _ in range(16):                                      # walking: never
            with self.world.lock:
                self.world.players["Steve"]["pos"][0] += 4.0
            time.sleep(0.25)
        self.assertEqual(len(got), n)
        self.assertEqual(self.sent("summon"), [])


# ---- beam_clash ---------------------------------------------------------------------------------

EYES = 64.0 + aim.EYES


class OngoingClashTests(OngoingCase):
    def setUp(self):
        super().setUp()
        self.saved_run_dir = os.environ.get("VFG_RUN_DIR")
        os.environ["VFG_RUN_DIR"] = tempfile.mkdtemp(dir=TMP)       # where the shared file goes

    def tearDown(self):
        super().tearDown()
        if self.saved_run_dir is None:
            os.environ.pop("VFG_RUN_DIR", None)
        else:
            os.environ["VFG_RUN_DIR"] = self.saved_run_dir

    def ray(self, x, dx, length=20):
        return aim.Ray(world.Pos(x, EYES, -3.5), (dx, 0.0, 0.0), length)

    def test_how_close_two_beams_come(self):
        d, c1, c2 = CLASH._beam_clash_closest((0, 0, 0), (10, 0, 0), (5, 3, -5), (5, 3, 5))
        self.assertAlmostEqual(d, 3.0)
        self.assertEqual([round(x, 6) for x in c1], [5, 0, 0])
        self.assertAlmostEqual(CLASH._beam_clash_closest((0, 0, 0), (10, 0, 0), (0, 20, 0), (10, 20, 0))[0], 20.0)

    def test_a_beam_at_no_one_goes_straight_on(self):
        v = vals(CLASH)
        t = time.time()
        self.assertIsNone(CLASH.beam_clash("Steve", aim.Ray(world.Pos(10.5, EYES, -3.5), (0.0, 0.0, 1.0), 20), v))
        self.assertLess(time.time() - t, 0.5)

    def test_a_beam_at_a_player_who_does_not_answer_waits_then_goes_on(self):
        v = vals(CLASH, "beam_clash_window=1")
        t = time.time()
        self.assertIsNone(CLASH.beam_clash("Steve", self.ray(10.5, 1.0), v))
        self.assertGreater(time.time() - t, 0.9)

    def test_two_beams_meet_and_the_one_who_pushes_wins(self):
        v = vals(CLASH, "beam_clash_seconds=4", "beam_clash_window=2")
        self.world.hold("Steve", main="galick_gun")
        steve, a = background(CLASH.beam_clash, "Steve", self.ray(10.5, 1.0), v)
        time.sleep(0.5)
        alex, b = background(CLASH.beam_clash, "Alex", self.ray(30.5, -1.0), v)
        while a.is_alive() and b.is_alive():                    # Steve holds right-click down
            self.world.click("Steve")
            time.sleep(0.2)
        a.join(6)
        b.join(6)
        self.assertEqual(steve[0][0], True, steve)
        self.assertEqual(alex[0][0], False, alex)
        self.assertEqual(steve[0][1], alex[0][1])
        self.assertGreater(steve[0][1][0], 26.0)                 # it was pushed up to Alex
        self.assertEqual(len(self.sent("summon creeper")), 1)
        self.assertIn("ExplosionRadius:4b", self.sent("summon creeper")[0])
        self.assertTrue(self.sent("CLASH! right-click to push"))
        self.assertGreater(len(self.sent("particle minecraft:dust") or self.sent("particle dust")), 10)


# ---- as scripts: a stop leaves nothing -------------------------------------------------------------

WIRING = {
    "boss": 'v = values(VALUES)\nboss("Steve", v)\n',
    "horde_wave": 'v = values(VALUES)\nhorde_wave("@a", v)\n',
    "director": 'v = values(VALUES)\ndirector("@a", v)\n',
    "beam_clash": ('v = values(VALUES)\nfrom kit import aim, world\n'
                   'import sys\nx, dx = (10.5, 1.0) if sys.argv[-1] == "Steve" else (30.5, -1.0)\n'
                   'mc.log("result", beam_clash(sys.argv[-1], aim.Ray(world.Pos(x, 65.62, -3.5), (dx, 0.0, 0.0), 20), v))\n'),
}


def script(name):
    with open(os.path.join(PRESETS, name + ".py"), encoding="utf-8") as f:
        text = f.read()
    return text.replace("import mc\n", "import mc\nfrom kit import values\n", 1) + "\n\n" + WIRING[name]


class OngoingStopTests(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.world.add_player("Steve", (10.5, 64.0, -3.5), rot=(-90.0, 0.0), prefix="[Owner] ")
        self.world.add_player("Alex", (30.5, 64.0, -3.5), rot=(90.0, 0.0))
        self.password = secrets.token_hex(8)
        self.fake = FakeRcon(self.password, responder=self.world.respond).start()
        self.procs = []
        self.run_dir = tempfile.mkdtemp(dir=TMP)

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait()
        self.fake.stop()

    def start(self, preset, instance, *argv):
        """The preset as a script, run as EC2 runs one: python3 -u main.py key=value ..."""
        folder = os.path.join(TMP, instance)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "main.py"), "w") as f:
            f.write(script(preset))
        env = dict(os.environ, PYTHONPATH=test_kit.LIB, VFG_RCON_HOST="127.0.0.1", PYTHONDONTWRITEBYTECODE="1",
                   VFG_MC_PROPERTIES=test_kit._properties(self.password, self.fake.port), VFG_INSTANCE=instance,
                   VFG_RUN_DIR=self.run_dir, VFG_PROTECTED=os.path.join(TMP, "none.json"))
        proc = subprocess.Popen([sys.executable, "-u", "main.py"] + list(argv), cwd=folder, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.procs.append(proc)
        return proc

    def stop(self, proc):
        t = time.time()
        proc.send_signal(signal.SIGTERM)
        out = proc.communicate(timeout=5)[0].decode()
        self.assertLess(time.time() - t, 2.0, out)
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("Traceback", out)
        return out

    def left(self, instance):
        tag = "vfg_" + instance
        with self.world.lock:
            return ([e["type"] for e in self.world.entities if any(t == tag or t.startswith(tag + "_") for t in e["tags"])],
                    [b for b in self.world.bossbars if b.startswith("vfg:" + instance)])

    def test_a_boss_stopped_mid_fight(self):
        proc = self.start("boss", "pboss", "boss_every=1", "boss_griefing=false", "boss_music=pigstep")
        self.assertTrue(until(lambda: any(t.endswith("_minion") for e in self.world.entities for t in e["tags"]), 8))
        self.assertEqual(self.world.gamerules["mob_griefing"], "false")
        self.assertTrue(self.left("pboss")[1])
        out = self.stop(proc)
        self.assertEqual(self.left("pboss"), ([], []))
        self.assertEqual(self.world.gamerules["mob_griefing"], "true")
        self.assertIn("stopsound @a record minecraft:music_disc.pigstep", self.world.commands)
        self.assertNotIn("summon item", " ".join(self.world.commands), out)

    def test_a_horde_stopped(self):
        proc = self.start("horde_wave", "phorde", "horde_every=1", "horde_per_player=2")
        self.assertTrue(until(lambda: len(self.left("phorde")[0]) >= 4, 8))
        self.stop(proc)
        self.assertEqual(self.left("phorde"), ([], []))

    def test_a_director_stopped_while_a_shadow_watches(self):
        proc = self.start("director", "pdirector", "director_events=shadow,dark", "director_weights=1,1",
                          "director_min_s=1", "director_max_s=1")
        self.assertTrue(until(lambda: "armor_stand" in self.left("pdirector")[0], 8))
        self.assertTrue(until(lambda: any(c.startswith("effect give") and "darkness" in c for c in self.world.commands), 8))
        self.stop(proc)
        self.assertEqual(self.left("pdirector"), ([], []))
        self.assertTrue(any(c.startswith("effect clear") and "darkness" in c for c in self.world.commands))

    def test_two_scripts_clash_through_the_shared_file(self):
        self.world.hold("Alex", main="hollow_purple")
        a = self.start("beam_clash", "pclash-a", "beam_clash_seconds=4", "beam_clash_window=5", "Steve")
        time.sleep(1.0)
        b = self.start("beam_clash", "pclash-b", "beam_clash_seconds=4", "beam_clash_window=5", "Alex")
        end = time.time() + 12
        while time.time() < end and (a.poll() is None or b.poll() is None):
            self.world.click("Alex")                              # Alex mashes: Alex wins
            time.sleep(0.15)
        out_a, out_b = a.communicate(timeout=5)[0].decode(), b.communicate(timeout=5)[0].decode()
        self.assertIn("result (False, Pos(", out_a, out_a + out_b)
        self.assertIn("result (True, Pos(", out_b, out_a + out_b)
        self.assertIn("beam clash won by Alex", out_a + out_b)
        self.assertTrue(os.path.exists(os.path.join(self.run_dir, "beam_clash.json")))

    def test_a_clash_stopped_mid_struggle(self):
        a = self.start("beam_clash", "pclash-c", "beam_clash_seconds=20", "beam_clash_window=5", "Steve")
        time.sleep(1.0)
        b = self.start("beam_clash", "pclash-d", "beam_clash_seconds=20", "beam_clash_window=5", "Alex")
        self.assertTrue(until(lambda: any("CLASH!" in c for c in self.world.commands), 8))
        for p in (a, b):
            self.stop(p)
        self.assertEqual(self.left("pclash-c"), ([], []))
        self.assertEqual(self.left("pclash-d"), ([], []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
