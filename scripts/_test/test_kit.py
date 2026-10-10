"""Tests for the kit (scripts/_lib/kit) against the fake RCON server and fake_world, under
the Python EC2 has:

    uv run --python 3.9 python scripts/_test/test_kit.py

run_tests.py runs these too. In this process mc is pointed at a fake whose world each test
makes afresh; the tests that need a script of their own (two scripts on one player, a stop)
start one the way EC2 does, with PYTHONPATH=scripts/_lib and VFG_INSTANCE set.
"""
import ast
import contextlib
import io
import json
import os
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
LIB = os.path.join(os.path.dirname(HERE), "_lib")
sys.path.insert(0, HERE)
sys.path.insert(0, LIB)

from fake_rcon import FakeRcon  # noqa: E402
from fake_world import World  # noqa: E402

TMP = tempfile.mkdtemp(prefix="vfg-kit-test-")


def _properties(password, port):
    path = os.path.join(TMP, "server-%d.properties" % port)
    with open(path, "w") as f:
        f.write("enable-rcon=true\nrcon.port=%d\nrcon.password=%s\n" % (port, password))
    return path


# mc reads its settings from the environment on first use; point it at a throwaway
# properties file now, and at each test's fake in KitCase.setUp.
if "VFG_MC_PROPERTIES" not in os.environ:
    os.environ["VFG_MC_PROPERTIES"] = _properties("unused", 1)
import mc  # noqa: E402
from kit import values, items, aim, area, mobs, fx, world, exclude, loop  # noqa: E402

signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)

REPO = os.path.dirname(os.path.dirname(HERE))


def _vfg_actions():
    """lib/py/vfg_actions.py, with the repo importable for actions._types (and pydantic)."""
    for path in (REPO, os.path.join(REPO, "lib", "py")):
        if path not in sys.path:
            sys.path.append(path)
    import vfg_actions
    return vfg_actions


def values_class(p):
    """A preset's values class, from its module (as a test loads it), its class or its name."""
    T = _vfg_actions()._types()
    if isinstance(p, str):
        return T.preset(p)
    if isinstance(p, type):
        return p
    return getattr(p, T.camel(os.path.splitext(os.path.basename(p.__file__))[0]))


def values_meta(*presets, given=()):
    """The meta file vfg action run writes for a copy of a script holding these presets: every
    value of their values classes (each a preset's module, its class or a preset's name), its
    default, then `given` (key=value words, or a dict), checked on the PC as vfg checks a run's
    values (lib/py/vfg_actions.py, with pydantic). For values(meta=...)."""
    from pydantic import create_model
    classes = [values_class(p) for p in presets]
    model = create_model("Script", __base__=tuple(classes)) if len(classes) > 1 else classes[0]
    if not isinstance(given, dict):
        given = dict(w.split("=", 1) for w in given)
    checked = _vfg_actions().check_values(model, given, allow_fixed=True)
    return {"values": checked.values, "live": checked.live}


def assert_checked_on_the_pc(case, preset):
    """A preset's values as vfg checks a run's: the defaults pass, a number past its limits is
    clamped with a note, and a word that is not of its type is refused."""
    va = _vfg_actions()
    T, cls = va._types(), values_class(preset)
    va.check_values(cls, {}, allow_fixed=True)
    bad = {"int": "lots", "number": "lots", "enum": "nonsense", "bool": "nonsense", "mob": "potion",
           "players": "not a name!", "list of number": "x", "list of int": "x", "list of mob": "potion"}
    for key, f in cls.model_fields.items():
        kind = T.kind(f.annotation, f.metadata)
        lo, hi = T.limits(f.annotation, f.metadata)
        for edge, past in ((lo, -1), (hi, 1)):
            if edge is not None:
                notes = va.check_values(cls, {key: str(edge + past)}, allow_fixed=True).notes
                case.assertIn("clamped", " ".join(notes), key)
        if kind in bad:
            with case.assertRaises(ValueError, msg=key):
                va.check_values(cls, {key: bad[kind]}, allow_fixed=True)


class KitCase(unittest.TestCase):
    """mc talks to a fake whose world is self.world, made new for each test."""

    @classmethod
    def setUpClass(cls):
        cls.password = secrets.token_hex(8)
        cls.fake = FakeRcon(cls.password, responder=lambda c: cls.current.respond(c)).start()
        cls.saved = mc._settings

    @classmethod
    def tearDownClass(cls):
        mc._drop()
        mc._settings = cls.saved
        cls.fake.stop()

    def setUp(self):
        type(self).current = self.world = World()
        self.world.add_player("Steve", (10.5, 64.0, -3.5), rot=(-90.0, 0.0), prefix="[Owner] ")
        mc._drop()
        mc._settings = (self.password, self.fake.port)
        items._made.clear()
        loop._online["at"] = 0.0
        world._dims.clear()
        world._ms["at"] = 0.0
        world._rules.clear()
        area._forced.clear()
        self.stoppers = list(mc._stoppers)

    def tearDown(self):
        # Let the generators collect() started see a stop and end, then carry on.
        mc._stop["thread"] = threading.get_ident()
        mc._stopping.set()
        try:
            for th in RUNNING:
                th.join(3)
        finally:
            del RUNNING[:]
            mc._stopping.clear()
        mc._stoppers[:] = self.stoppers

    def sent(self, word):
        return [c for c in self.world.commands if word in c]


RUNNING = []


def collect(gen, seconds):
    """Run a kit generator in a thread for a while (or until the test ends): a list of
    (what it yielded, time.time())."""
    got = []
    end = time.time() + seconds

    def go():
        for p in gen:
            got.append((p, time.time()))
            if time.time() > end:
                return
    th = threading.Thread(target=go, daemon=True)
    th.start()
    RUNNING.append(th)
    return got, th


# ---- values ------------------------------------------------------------------------------

class ValuesTests(KitCase):
    def test_a_live_value_reaches_a_running_generator(self):
        d = tempfile.mkdtemp(dir=TMP)
        v = values(meta={"values": {"player": "@a", "cooldown": 30.0}, "live": ["cooldown"]}, run_dir=d,
                   instance="rail", poll=0.05)
        self.world.hold("Steve", main="railgun")
        got, th = collect(items.clicks(v["player"], "Railgun", cooldown=v["cooldown"], every=0.05), 3)
        time.sleep(0.3)
        self.world.click("Steve")
        time.sleep(0.3)
        self.world.click("Steve")          # inside the 30 s cooldown: not fired
        time.sleep(0.3)
        with open(os.path.join(d, "rail.values.json"), "w") as f:
            json.dump({"cooldown": 0}, f)
        time.sleep(0.3)
        self.world.click("Steve")          # the cooldown is 0 now
        time.sleep(0.4)
        self.assertEqual([p for p, _ in got], ["Steve", "Steve"])

    def test_a_values_meta_is_every_preset_value_checked_on_the_pc(self):
        meta = values_meta("crater", "sneak", given=["crater_radius=999", "sneak_item=Spirit Bomb"])
        self.assertEqual(meta["values"]["crater_radius"], 300)        # clamped, as vfg action run clamps
        self.assertEqual(meta["values"]["sneak_item"], "Spirit Bomb")
        self.assertIn("crater_radius", meta["live"])
        with self.assertRaises(ValueError):
            values_meta("crater", given={"crater_leave": "water"})


class CopyValuesTests(KitCase):
    """values(): a copy's meta file, checked on the PC, and its live file; and result(), what
    the copy reports."""

    def copy(self, name, vals, live=(), edits=None):
        d = tempfile.mkdtemp(dir=TMP)
        with open(os.path.join(d, name + ".json"), "w") as f:
            json.dump({"copy": name, "action": "gun", "script": "gun", "values": vals, "live": list(live)}, f)
        if edits is not None:
            with open(os.path.join(d, name + ".values.json"), "w") as f:
                json.dump(edits, f)
        return d

    def test_the_meta_values_then_the_live_file_for_live_fields_only(self):
        d = self.copy("gun", {"player": "Steve", "cooldown": 2.0, "range": 100, "spare": ["Alex"], "on": True},
                      live=["cooldown", "on"], edits={"cooldown": 0.5, "range": 5, "nope": 1})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            v = values(run_dir=d, instance="gun", poll=0.05)
            self.assertEqual(v["cooldown"], 0.5)      # live: the edit
            self.assertEqual(v["range"], 100)         # not live: the edit is left out
            v["range"]
        self.assertEqual((v["player"], v["spare"], v["on"]), ("Steve", ["Alex"], True))
        self.assertEqual(sorted(v), ["cooldown", "on", "player", "range", "spare"])
        with self.assertRaises(KeyError):
            v["crater_radius"]
        said = out.getvalue()
        self.assertEqual(said.count("live file: ignored range, not live"), 1)
        self.assertEqual(said.count("live file: ignored nope, not one of its values"), 1)
        cooldown = v["cooldown"]                      # handed on as it is, it stays live
        t0 = time.time()
        with open(os.path.join(d, "gun.values.json.tmp"), "w") as f:
            json.dump({"cooldown": 7, "on": False}, f)
        os.replace(os.path.join(d, "gun.values.json.tmp"), os.path.join(d, "gun.values.json"))
        while values.now(cooldown) != 7 and time.time() - t0 < 2:
            time.sleep(0.02)
        self.assertEqual(values.now(cooldown), 7)
        self.assertLess(time.time() - t0, 1.0)
        self.assertIs(v["on"], False)

    def test_the_meta_as_a_dict_or_a_path_and_none_is_an_error(self):
        v = values(meta={"values": {"n": 3}, "live": []}, run_dir=tempfile.mkdtemp(dir=TMP), instance="x")
        self.assertEqual(dict(v), {"n": 3})
        d = self.copy("other", {"n": 4})
        self.assertEqual(values(meta=os.path.join(d, "other.json"), run_dir=d, instance="other")["n"], 4)
        with self.assertRaises(RuntimeError) as e:
            values(run_dir=tempfile.mkdtemp(dir=TMP), instance="nothing")
        self.assertIn("vfg action run", str(e.exception))

    def test_result_writes_the_copys_result_file_and_the_last_call_wins(self):
        d = tempfile.mkdtemp(dir=TMP)
        src = ("from kit import result\n"
               "import mc\n"
               "result('half way', n=1)\n"
               "result('summoned  100 zombies\\nround Steve', who=['Steve'], at=(197, 68, 251), n=100)\n"
               "mc.log('end')\n")
        proc = start_script("reporter", src, self.fake, self.password, instance="reporter_2", run_dir=d)
        out = proc.communicate(timeout=10)[0].decode()
        self.assertEqual(proc.returncode, 0, out)
        with open(os.path.join(d, "reporter_2.result.json")) as f:
            got = json.load(f)
        self.assertEqual(got["summary"], "summoned 100 zombies round Steve")
        self.assertEqual(got["facts"], {"who": ["Steve"], "at": [197, 68, 251], "n": 100})
        self.assertAlmostEqual(got["at"], time.time(), delta=30)
        self.assertIn("reporter_2 result: summoned 100 zombies round Steve", out)
        self.assertEqual([n for n in os.listdir(d) if ".tmp" in n], [])


class PlaceTests(KitCase):
    """world.place: a Place value (a saved name, x y z [dim], a player) to where it is now."""

    def test_a_saved_place_coordinates_and_a_player(self):
        self.world.storage["castle"] = {"pos": [197, 68, 251], "dim": "minecraft:overworld", "by": "T23"}
        self.world.storage["hell"] = {"pos": [1, 70, -2], "dim": "minecraft:the_nether", "by": ""}
        p = world.place("castle")
        self.assertEqual((tuple(p), p.dim), ((197.0, 68.0, 251.0), None))
        self.assertEqual(world.place("hell").dim, "minecraft:the_nether")
        p = world.place("100 64 -20")
        self.assertEqual((tuple(p), p.dim), ((100.0, 64.0, -20.0), None))
        self.assertEqual(world.place(" 100  64.5 -20 the_nether ").dim, "minecraft:the_nether")
        self.assertEqual(world.place("1 2 3 end").dim, "minecraft:the_end")
        p = world.place("Steve")
        self.assertEqual((tuple(p), p.dim), ((10.5, 64.0, -3.5), None))
        self.assertEqual(tuple(world.place((1, 2, 3))), (1.0, 2.0, 3.0))
        self.assertIs(world.place(p), p)

    def test_a_saved_name_wins_over_a_player_and_nowhere_is_none(self):
        self.world.add_player("builder", (5.5, 70.0, 5.5))
        self.world.storage["builder"] = {"pos": [9, 9, 9], "dim": "minecraft:overworld", "by": ""}
        self.assertEqual(tuple(world.place("builder")), (9.0, 9.0, 9.0))
        self.assertEqual(tuple(world.place("Builder")), (9.0, 9.0, 9.0))   # a saved name, lowercased
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertIsNone(world.place(""))                              # not given: quietly none
            self.assertEqual(out.getvalue(), "")
            self.assertIsNone(world.place("Nobody"))
            self.assertIsNone(world.place("gone_place"))
            self.assertIsNone(world.place("1 2 three"))
        self.assertIn("no saved place and no player online by that name", out.getvalue())


# ---- mc ---------------------------------------------------------------------------------

class McKitTests(KitCase):
    def test_players_strips_a_rank_prefix(self):
        self.world.add_player("Alex", (0.5, 64, 0.5), prefix="[VIP] ")
        self.world.add_player("Sam_9", (0.5, 64, 0.5))
        self.assertEqual(mc.players(), ["Steve", "Alex", "Sam_9"])
        self.assertEqual(loop.who("Alex,Nobody"), ["Alex"])
        for raw, want in (("[Kami-chan] Steve", "Steve"), ("§c[Admin]§r Alex", "Alex"),
                          ("<Mod> Eve_2", "Eve_2"), ("Steve [AFK]", "Steve"), ("Steve", "Steve")):
            self.assertEqual(mc.bare(raw), want)

    def test_tag_comes_from_the_instance(self):
        folder = os.path.join(TMP, "galick_gun")
        os.makedirs(folder, exist_ok=True)
        code = "import mc; print(mc.NAME, mc.INSTANCE, mc.TAG)"
        for inst, want in (("galick-alex", "galick_gun galick-alex vfg_galick-alex"),
                           ("Bad Name!", "galick_gun galick_gun vfg_galick_gun"), ("", "galick_gun galick_gun vfg_galick_gun")):
            env = dict(os.environ, PYTHONPATH=LIB, VFG_INSTANCE=inst)
            out = subprocess.run([sys.executable, "-c", code], env=env, cwd=folder, stdout=subprocess.PIPE,
                                 universal_newlines=True).stdout.strip()
            self.assertEqual(out, want, inst)

    def test_every_command_holds_the_rcon_lock(self):
        """26.3 answers every RCON client from one buffer: two clients at once get each
        other's output. mc holds a lock file from send to reply, so a command waits for it."""
        import fcntl
        fd = os.open(mc.lock_path(), os.O_RDWR | os.O_CREAT, 0o666)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            got = []
            th = threading.Thread(target=lambda: got.append(mc.rcon("list")))
            th.start()
            time.sleep(0.4)
            self.assertEqual(got, [])                 # waiting for the lock
            fcntl.flock(fd, fcntl.LOCK_UN)
            th.join(2)
            self.assertEqual(len(got), 1)
            self.assertIn("players online", got[0])
        finally:
            os.close(fd)

    def test_a_command_too_long_for_rcon_is_refused(self):
        self.assertEqual(mc.rcon("say " + "x" * 1500), "")
        self.assertEqual(self.world.commands, [])


# ---- items: one shared objective ---------------------------------------------------------

class ItemsTests(KitCase):
    def test_each_click_reaches_only_the_script_whose_item_took_it(self):
        self.world.hold("Steve", main="alpha_wand")
        a, _ = collect(items.clicks("@a", "Alpha Wand", every=0.05), 4)
        b, _ = collect(items.clicks("Steve", "Beta Wand", every=0.05), 4)
        time.sleep(0.4)
        self.world.click("Steve")                                  # alpha in hand
        time.sleep(0.4)
        self.world.hold("Steve", main="beta_wand")
        time.sleep(0.3)
        self.world.click("Steve")                                  # beta in hand
        time.sleep(0.4)
        self.world.hold("Steve", main="beta_wand", off="alpha_wand")
        time.sleep(0.3)
        self.world.click("Steve")                                  # beta in hand takes it, not alpha in the off hand
        time.sleep(0.4)
        self.world.hold("Steve", main=None, off="alpha_wand")
        time.sleep(0.3)
        self.world.click("Steve")                                  # alpha in the off hand, nothing in the main
        time.sleep(0.4)
        self.world.hold("Steve", main="plain")
        time.sleep(0.3)
        self.world.click("Steve", 3)                               # a plain carrot on a stick: nobody's
        time.sleep(0.4)
        self.assertEqual(len(a), 2, a)
        self.assertEqual(len(b), 2, b)
        self.assertLess(a[0][1], b[0][1])
        self.assertLess(b[1][1], a[1][1])
        # One objective, made once, never reset or removed.
        self.assertEqual([o for o, c in self.world.objectives.items() if "carrot_on_a_stick" in c], ["vfg_click"])
        for c in self.world.commands:
            self.assertNotRegex(c, r"scoreboard players (set|reset|operation)|objectives remove vfg_click")
        # Beta was given once; Alpha was in his hand already, so not given again.
        self.assertEqual([i.split('"')[1] for _, i in self.world.gives], ["beta_wand"])

    def test_a_server_that_refuses_the_hand_check_falls_back_to_either_hand(self):
        self.world.no_negation = True
        self.world.hold("Steve", main="alpha_wand")
        got, _ = collect(items.clicks("Steve", "Alpha Wand", every=0.05), 3)
        time.sleep(0.4)
        self.world.click("Steve")
        time.sleep(0.4)
        self.assertEqual([p for p, _ in got], ["Steve"])

    def test_a_hold_yields_while_the_button_is_down(self):
        self.world.hold("Steve", main="gatling_gun")
        got, _ = collect(items.holds("Steve", "Gatling Gun", every=0.05), 3)
        time.sleep(0.3)
        first = time.time()
        for _ in range(6):                       # held down: a use every 0.2 s for 1.2 s
            self.world.click("Steve")
            last = time.time()
            time.sleep(0.2)
        time.sleep(1.0)
        times = [t for _, t in got]
        self.assertGreater(len(times), 10)
        self.assertGreaterEqual(min(times), first)
        self.assertLess(max(times), last + 0.9, "kept yielding after it was let go")

    def test_a_charge_fires_once_when_full_and_not_when_let_go_early(self):
        self.world.hold("Steve", main="spirit_bomb")
        got, _ = collect(items.charged("Steve", "Spirit Bomb", 0.6, every=0.05), 5)
        time.sleep(0.3)
        for _ in range(2):                       # 0.4 s: let go before the charge is full
            self.world.click("Steve")
            time.sleep(0.2)
        time.sleep(0.8)
        self.assertEqual(got, [])
        start = time.time()
        for _ in range(8):                       # 1.6 s held: full at 0.6 s, fires once
            self.world.click("Steve")
            time.sleep(0.2)
        time.sleep(0.6)
        self.assertEqual(len(got), 1, got)
        self.assertGreaterEqual(got[0][1] - start, 0.55)
        self.assertLess(got[0][1] - start, 1.3)
        self.assertTrue(self.sent("actionbar"), "no charge shown")

    def test_a_player_with_no_score_hides_nobody_and_a_first_click_counts(self):
        self.world.add_player("Alex", (0.5, 64.0, 0.5))
        self.world.hold("Alex", main="alpha_wand")
        self.world.hold("Steve", main="alpha_wand")
        got, _ = collect(items.clicks("@a", "Alpha Wand", every=0.05), 3)
        time.sleep(0.4)
        self.world.click("Alex")                   # Alex's first click ever, Steve never clicked
        time.sleep(0.4)
        self.assertEqual([p for p, _ in got], ["Alex"])
        self.assertTrue(self.sent("scoreboard players add @a vfg_click 0"))
        s = items.Stat("vfg_x", "dummy")
        self.world.objectives["vfg_x"] = "dummy"
        self.world.scores[("vfg_x", "Alex")] = 4
        self.assertEqual(items._SCORE.findall("Can't get value of x for Steve; none is setAlex has 4 [x]"), [])
        self.assertEqual(s.scores(), {"Steve": 0, "Alex": 4})

    def test_sneaking_from_the_shared_objective(self):
        self.assertEqual(items.sneaking(), set())
        self.world.sneak("Steve", 4)
        self.assertEqual(items.sneaking(), {"Steve"})
        self.assertEqual(items.sneaking(), set())

    def test_give_keep_and_take_back(self):
        self.assertTrue(items.give("Steve", "Ice Staff"))
        self.assertFalse(items.give("Steve", "Ice Staff"))      # has it: not given twice
        self.assertTrue(items.has("Steve", "Ice Staff"))
        items.take("Steve", "Ice Staff")
        self.assertFalse(items.has("Steve", "Ice Staff"))
        self.assertIn('custom_data={vfg_item:"ice_staff"}', items.item("Ice Staff"))


# ---- aim ---------------------------------------------------------------------------------

class AimTests(KitCase):
    def test_a_ray_stops_before_a_block(self):
        self.world.solid.add(("minecraft:overworld", 30, 65, -4))
        r = aim.ray("Steve", 100)
        self.assertEqual(r.why, "block")
        self.assertEqual(tuple(r.hit_block), (30.0, 65.0, -4.0))
        self.assertAlmostEqual(r.end[0], 29.95, places=2)
        self.assertAlmostEqual(r.start[1], 64.0 + aim.EYES)
        self.assertEqual(len(r.path), int(r.length) + 2)

    def test_a_ray_stops_at_an_unloaded_chunk(self):
        self.world.unloaded.add(("minecraft:overworld", 3, -1))   # x 48..63, z -16..-1
        before = len(self.world.commands)
        r = aim.ray("Steve", 120)
        self.assertEqual(r.why, "unloaded")
        self.assertIsNone(r.hit_block)
        self.assertAlmostEqual(r.end[0], 47.95, places=2)
        self.assertLess(len(self.world.commands) - before, 15, "a ray should cost few commands")
        r2 = aim.ray("Steve", 120, blocks=False)               # through blocks, still not past the edge
        self.assertEqual(r2.why, "unloaded")
        self.assertAlmostEqual(r2.end[0], 47.95, places=1)

    def test_a_ray_in_the_nether_asks_the_nether(self):
        self.world.players["Steve"]["dim"] = "minecraft:the_nether"
        r = aim.ray("Steve", 20)
        self.assertEqual(r.dim, "minecraft:the_nether")
        self.assertTrue(all(c.startswith("execute in minecraft:the_nether if block") for c in self.sent("if block")))

    def test_who_is_near_a_line(self):
        on = self.world.add_entity("zombie", (30.5, 64.0, -3.5))
        self.world.add_entity("zombie", (30.5, 64.0, 6.5))                               # off the line
        self.world.add_entity("villager", (20.5, 64.0, -3.5))
        self.world.add_entity("wolf", (25.5, 64.0, -3.5), owner="Steve")                 # a pet
        self.world.add_entity("armor_stand", (26.5, 64.0, -3.5))
        self.world.add_entity("marker", (27.5, 65.0, -3.5), health=None)                 # not alive
        self.world.add_entity("item", (28.5, 64.0, -3.5), health=5.0, name="Rotten Flesh")  # an item has a Health
        self.world.add_player("Alex", (40.5, 64.0, -3.0))
        self.world.add_player("Sam", (45.5, 64.0, -3.0), gamemode="creative")
        r = aim.ray("Steve", 60)
        hits = aim.near_line(r, 1.5, shooter="Steve")
        self.assertEqual([(h.name, h.player) for h in hits], [("Zombie", False), ("Alex", True)])
        self.assertAlmostEqual(hits[0].t, 20.0, delta=1.0)
        for h in hits:
            aim.run_on(h, "damage @s 5 minecraft:player_attack by Steve")
        self.assertEqual(on["health"], 15.0)
        self.assertEqual(self.world.players["Alex"]["health"], 15.0)
        self.assertEqual(self.world.players["Steve"]["health"], 20.0)
        for c in self.world.commands:
            self.assertNotIn("type=!potion", c)
            self.assertNotRegex(c, r"@a\[[^\]]*type=")


class AimNearTests(KitCase):
    def test_who_is_near_a_place_and_marking_each_once(self):
        z1 = self.world.add_entity("zombie", (20.5, 64.0, 0.5))
        z2 = self.world.add_entity("zombie", (22.5, 64.0, 0.5))
        self.world.add_entity("zombie", (40.5, 64.0, 0.5))                       # too far
        self.world.add_entity("villager", (21.5, 64.0, 0.5))
        self.world.add_player("Alex", (19.5, 64.0, 0.5))
        hits = aim.near(world.Pos(20.5, 64.0, 0.5), 4, shooter="Steve")
        self.assertEqual([(h.name, round(h.t, 1)) for h in hits], [("Zombie", 0.0), ("Alex", 1.0), ("Zombie", 2.0)])
        self.assertEqual(aim.mark(hits + hits[:1], "vfg_hit"), 3)                   # listed twice, tagged once
        self.assertIn("vfg_hit", z1["tags"])
        self.assertIn("vfg_hit", z2["tags"])
        self.assertIn("vfg_hit", self.world.players["Alex"]["tags"])

    def test_a_crowded_place_is_asked_in_smaller_boxes(self):
        for i in range(80):
            self.world.add_entity("zombie", (20.5 + (i % 9) * 0.5, 64.0, 0.5 + (i // 9) * 0.5), name="Zombie Number %d" % i)
        hits = aim.near(world.Pos(22.5, 64.0, 2.5), 5)
        self.assertEqual(len(hits), 80)
        self.assertTrue(self.sent("if entity @s[dx="))


# ---- area --------------------------------------------------------------------------------

class AreaTests(KitCase):
    def covered(self, fills):
        cells = set()
        for _, b, _, _ in fills:
            for x in range(b[0], b[3] + 1):
                for y in range(b[1], b[4] + 1):
                    for z in range(b[2], b[5] + 1):
                        self.assertNotIn((x, y, z), cells, "two fills overlap")
                        cells.add((x, y, z))
        return cells

    def test_a_fill_is_split_under_32768_on_chunk_edges_where_loaded(self):
        n = area.fill((0, 0, 0, 99, 99, 99), "stone")
        self.assertEqual(n, 100 ** 3)
        self.assertTrue(all(area.volume(b) <= 32768 for _, b, _, _ in self.world.fills))
        self.assertEqual(len(self.covered(self.world.fills)), 100 ** 3)
        self.assertFalse(self.sent("Too many"))

    def test_a_fill_skips_chunks_that_are_not_loaded(self):
        self.world.unloaded.update({("minecraft:overworld", 1, 1), ("minecraft:overworld", 2, 0)})
        n = area.fill((0, 60, 0, 47, 70, 47), "packed_ice", "replace #minecraft:snow")
        self.assertEqual(n, 48 * 11 * 48 - 2 * 16 * 11 * 16)
        for _, b, block, mode in self.world.fills:
            self.assertEqual((block, mode), ("packed_ice", "replace #minecraft:snow"))
            for cx, cz in area.chunks(b):
                self.assertNotIn((cx, cz), {(1, 1), (2, 0)})

    def test_the_ice_castle_wall_and_a_hollow_box(self):
        n = area.fill((-30, 59, -30, 30, 70, 30), "packed_ice", "hollow")   # 61 x 12 x 61 = 44,652
        self.assertTrue(all(area.volume(b) <= 32768 for _, b, _, _ in self.world.fills))
        shell = 61 * 12 * 61 - 59 * 10 * 59
        self.assertEqual(n, 61 * 12 * 61)
        self.assertEqual(sum(area.volume(b) for _, b, blk, _ in self.world.fills if blk == "packed_ice"), shell)

    def test_a_box_keeps_its_dimension(self):
        b = area.box(world.Pos(0.5, 64, 0.5, "minecraft:the_nether"), 2)
        self.assertEqual((tuple(b), b.dim), ((-2, 62, -2, 2, 66, 2), "minecraft:the_nether"))
        self.assertEqual(area.norm(b).dim, "minecraft:the_nether")
        self.assertEqual(area.chunk_box(1, 2, 0, 10, "minecraft:the_end").dim, "minecraft:the_end")
        self.assertEqual(area.fill(b, "glowstone"), 125)
        self.assertEqual(self.world.fills[0][0], "minecraft:the_nether")
        self.assertTrue(self.sent("execute in minecraft:the_nether run fill"))
        self.assertIsNone(area.box((0, 64, 0), 1).dim)

    def test_never_bedrock(self):
        self.assertEqual(area.fill((0, -64, 0, 5, -60, 5), "packed_ice", "replace minecraft:bedrock"), 0)
        area.fill((0, -64, 0, 5, -50, 5), "air", keep_bedrock=True)
        self.assertEqual(min(b[1] for _, b, _, _ in self.world.fills), -59)

    def test_protected_boxes_are_left_out(self):
        path = os.path.join(TMP, "protected-%d.json" % id(self))
        with open(path, "w") as f:
            json.dump([{"name": "castle", "dim": "minecraft:overworld", "box": [4, 0, 4, 6, 100, 6]}], f)
        old = world.PROTECTED_FILE
        world.PROTECTED_FILE = path
        try:
            n = area.sphere((5, 64, 5), 6, "air")
            cells = self.covered(self.world.fills)
            self.assertTrue(cells)
            self.assertFalse({c for c in cells if 4 <= c[0] <= 6 and 4 <= c[2] <= 6})
            self.assertEqual(n, len(cells))
            self.assertTrue(world.is_protected((5.5, 70.0, 5.5)))
        finally:
            world.PROTECTED_FILE = old

    def test_a_sphere_is_round(self):
        area.sphere((0, 64, 0), 8, "air", spare_protected=False)
        cells = self.covered(self.world.fills)
        ball = {(x, y, z) for x in range(-9, 10) for y in range(55, 74) for z in range(-9, 10)
                if x * x + (y - 64) ** 2 + z * z <= 8.5 ** 2}
        self.assertGreater(len(cells & ball) / float(len(ball)), 0.97)
        self.assertLess(len(cells - ball) / float(len(ball)), 0.08)
        self.assertLess(len(self.world.fills), 120)

    def test_release_only_the_chunks_under_a_box(self):
        area.forceload((0, 0, 0, 40, 0, 40), wait=0)
        area.forceload((200, 0, 200, 210, 0, 210), wait=0)
        self.assertEqual(len(self.world.forced), 13)                # 3 x 3 and 2 x 2 chunks
        area.release((0, 0, 0, 15, 0, 15))
        self.assertEqual(len(self.world.forced), 12)
        area.release(world.Box(195, 0, 195, 215, 0, 215))
        self.assertEqual(len(self.world.forced), 8)
        area.release("minecraft:the_nether")          # nothing there
        self.assertEqual(len(self.world.forced), 8)
        area.release()
        self.assertEqual(self.world.forced, set())

    def test_forceload_owns_what_it_adds_and_releases_it(self):
        self.world.forced.add(("minecraft:overworld", 0, 0))         # someone else's
        self.world.unloaded.update({("minecraft:overworld", cx, cz) for cx in range(0, 3) for cz in range(0, 3)})
        added = area.forceload((0, 0, 0, 40, 0, 40), wait=1.0)
        self.assertEqual(added, 8)
        self.assertEqual(len(self.world.forced), 9)
        area.release()
        self.assertEqual(self.world.forced, {("minecraft:overworld", 0, 0)})


# ---- world, mobs, fx, exclude ------------------------------------------------------------------

class WorldTests(KitCase):
    def test_a_gamerule_is_put_back_as_it_was(self):
        self.world.gamerules["mob_griefing"] = "false"
        world.gamerule("mobGriefing", True)
        self.assertEqual(self.world.gamerules["mob_griefing"], "true")
        world.gamerule("fall_damage", False)
        world.restore()
        self.assertEqual(self.world.gamerules["mob_griefing"], "false")   # what it was, not "true"
        self.assertEqual(self.world.gamerules["fall_damage"], "true")
        self.assertIsNone(world.gamerule("not_a_rule", 1))

    def test_mspt_and_throttle(self):
        self.world.mspt = 61.0
        t = time.time()
        self.assertEqual(world.throttle(45, longest=0.6), 61.0)
        self.assertGreaterEqual(time.time() - t, 0.5)
        self.world.mspt = 20.0
        world._ms["at"] = 0.0
        self.assertEqual(world.throttle(45), 20.0)

    def test_many_conditions_in_few_commands(self):
        conds = ["if loaded %d 64 0" % (i * 16) for i in range(100)]
        self.world.unloaded.update({("minecraft:overworld", 40, 0), ("minecraft:overworld", 41, 0)})
        before = len(self.world.commands)
        ok = world.passes(conds)
        self.assertEqual([i for i, good in enumerate(ok) if not good], [40, 41])
        self.assertLess(len(self.world.commands) - before, 20)
        self.assertEqual(world.first_fail(conds)[0], 40)


class MobsTests(KitCase):
    def test_a_boss_of_three_bodies_with_one_bar(self):
        boss = mobs.Boss("Three-Headed Dragon", "ender_dragon", health=600, bodies=3)
        self.assertEqual(boss.spawn(at=(0, 100, 0)), 3)
        bar = self.world.bossbars[boss.bar]
        self.assertEqual(bar["max"], 1800)
        bodies = [e for e in self.world.entities if boss.tag in e["tags"]]
        self.assertEqual(len(bodies), 3)
        bodies[0]["health"] = 100.0
        self.assertEqual(boss.update(), 3)
        self.assertEqual(bar["value"], 1300)
        self.world.entities = [e for e in self.world.entities if e is not bodies[1]]
        self.assertEqual(boss.update(), 2)
        self.world.entities = [e for e in self.world.entities if boss.tag not in e["tags"]]
        self.assertTrue(boss.update() > 0)          # one empty look is not a death
        self.assertEqual(boss.update(), 0)
        self.assertNotIn(boss.bar, self.world.bossbars)

    def test_mobs_round_a_place_and_a_boss_with_attributes(self):
        self.assertEqual(mobs.around(world.Pos(30.5, 64.0, 30.5), "wither", 2, spread=2), 2)
        ws = [e for e in self.world.entities if e["type"] == "wither"]
        self.assertTrue(all(abs(e["pos"][0] - 30.5) <= 2.1 and abs(e["pos"][2] - 30.5) <= 2.1 for e in ws))
        boss = mobs.Boss("King", "zombie", health=100, attributes={"attack_damage": 12, "generic.movement_speed": 0.3})
        boss.spawn(at=(0, 64, 0))
        summon = self.sent("summon zombie")[-1]
        self.assertIn('{id:"attack_damage",base:12}', summon)
        self.assertIn('{id:"movement_speed",base:0.3}', summon)
        self.assertEqual(boss.total, 100.0)
        [e for e in self.world.entities if boss.tag in e["tags"]][0]["health"] = 40.0
        boss.update()
        self.assertEqual((boss.alive, boss.total), (1, 40.0))

    def test_mobs_around_a_player(self):
        self.assertEqual(mobs.around("Steve", "zombie", 4, spread=6, tags=("horde",)), 4)
        zs = [e for e in self.world.entities if e["type"] == "zombie"]
        self.assertEqual(len(zs), 4)
        self.assertTrue(all({"vfg", mc.TAG, "horde"} <= e["tags"] for e in zs))
        self.assertEqual(mobs.count("horde"), 4)


class FxTests(KitCase):
    def test_for_one_player_or_everyone_in_the_right_dimension(self):
        fx.particle("flame", "Steve", 0.5, 10, to="Steve")
        fx.sound("entity.generic.explode", world.Pos(1, 2, 3, "minecraft:the_end"), volume=2)
        fx.actionbar("Steve", "hi")
        fx.line((0, 64, 0), (10, 64, 0), "end_rod", every=2)
        cs = self.world.commands
        self.assertIn("execute at Steve run particle flame ~ ~ ~ 0.5 0.5 0.5 0 10 force Steve", cs)
        self.assertIn("execute in minecraft:the_end positioned 1.00 2.00 3.00 run playsound "
                      "minecraft:entity.generic.explode master @a ~ ~ ~ 2 1", cs)
        self.assertEqual(len(self.sent("particle end_rod")), 6)

    def test_a_sound_from_a_place_to_one_player(self):
        fx.sound("entity.zombie.step", (5, 64, 5), to="Steve")
        fx.sound("entity.zombie.step", "Steve", to="Steve")
        self.assertEqual(self.sent("playsound")[0],
                         "execute positioned 5.00 64.00 5.00 run playsound minecraft:entity.zombie.step master Steve ~ ~ ~ 1 1")
        self.assertEqual(self.sent("playsound")[1],
                         "execute as Steve at @s run playsound minecraft:entity.zombie.step master @s ~ ~ ~ 1 1")

    def test_a_music_disc_is_stopped_with_its_melody(self):
        fx.melody([(0, "music_disc.pigstep", 12, 2)], "Steve", key="boss").join(2)
        fx.stop_melody("boss")
        self.assertIn("stopsound @a * minecraft:music_disc.pigstep", self.world.commands)

    def test_a_newer_melody_cuts_the_older_off(self):
        fx.melody([(0, "pling", 12, 1), (0.6, "bell", 12, 1)], "Steve", key="t")
        time.sleep(0.2)
        fx.melody([(0, "bass", 0, 1)], "Steve", key="t").join(2)
        time.sleep(0.7)
        notes = self.sent("playsound")
        self.assertEqual(len(notes), 2, notes)
        self.assertIn("minecraft:block.note_block.bass", notes[1])
        self.assertIn(" 0.500", notes[1])


class ExcludeTests(KitCase):
    def test_one_type_alone(self):
        frag = exclude.mobs("type=zombie,distance=..4")
        self.assertNotIn("type=!", frag)
        self.assertEqual(mc.rcon("execute positioned 0 64 0 %s run say x" % frag), "")
        self.assertIn("isn't applicable", mc.rcon("execute if entity @e[type=zombie,type=!villager]"))

    def test_ids_26_3_accepts(self):
        frag = exclude.mobs("distance=..4", spare="Alex")
        for gone in ("type=!potion", "type=!boat", "type=!chest_boat"):
            self.assertNotIn(gone, frag)
        self.assertIn("unless data entity @s Owner", frag)
        self.assertNotIn("type=", exclude.players("Steve", "distance=..4"))
        self.assertIn("name=!Steve", exclude.players("[Owner] Steve"))


class KitPolishTests(KitCase):
    """What the polish pass added: crouching eyes, hits found by uuid, tags taken off first,
    hostile mobs only, the ground's height, @p, and a boss that knows its dimension."""

    def test_a_look_from_crouching_eyes_when_they_crouch(self):
        self.assertAlmostEqual(aim.look("Steve", eyes="auto").origin[1], 64.0 + aim.EYES)
        self.world.players["Steve"]["sneaking"] = True
        self.assertAlmostEqual(aim.look("Steve", eyes="auto").origin[1], 64.0 + aim.SNEAK_EYES)
        self.assertAlmostEqual(aim.look("Steve").origin[1], 64.0 + aim.EYES)      # the default asks nothing more

    def test_a_tag_left_on_a_player_is_taken_off_before_marking(self):
        self.world.add_player("Alex", (12.5, 64.0, -3.5))
        self.world.players["Alex"]["tags"].add("vfg_t_hit1")           # killed while tagged, by an earlier run
        aim._cleared.discard("vfg_t_hit1")                              # which this process has not used
        hit = aim.Hit(2.0, world.Pos(12.5, 64.0, -3.5), "Alex", True)
        self.assertEqual(aim.mark([hit], "vfg_t_hit1"), 1)               # newly tagged, so he is hurt
        self.assertTrue(self.sent("tag @a[tag=vfg_t_hit1] remove vfg_t_hit1"))
        aim.untag("vfg_t_hit1")
        self.assertNotIn("vfg_t_hit1", self.world.players["Alex"]["tags"])

    def test_a_tag_marked_again_in_the_same_process_is_not_taken_off_first(self):
        self.world.add_player("Alex", (12.5, 64.0, -3.5))
        aim._cleared.discard("vfg_t_hit2")
        hit = aim.Hit(2.0, world.Pos(12.5, 64.0, -3.5), "Alex", True)
        self.assertEqual(aim.mark([hit], "vfg_t_hit2"), 1)
        aim.untag("vfg_t_hit2")
        before = len(self.world.commands)
        self.assertEqual(aim.mark([hit], "vfg_t_hit2"), 1)               # tagged newly again: still hurt
        self.assertEqual(self.world.commands[before:], ["tag Alex add vfg_t_hit2"])  # one command, no untag first
        aim.untag("vfg_t_hit2")
        self.assertNotIn("vfg_t_hit2", self.world.players["Alex"]["tags"])

    def test_a_hit_with_a_uuid_is_found_by_it(self):
        u = "00000001-0000-0002-0000-0003ffffffff"
        hit = aim.Hit(1.0, world.Pos(1, 64, 1), "Arrow", False, u)
        aim.mark([hit], "vfg_t_x")
        aim.run_on(hit, "say hi")
        self.assertIn("tag %s add vfg_t_x" % u, self.world.commands)
        self.assertIn("execute as %s at @s run say hi" % u, self.world.commands)
        self.assertIsNone(aim.Hit(1.0, world.Pos(1, 64, 1), "Zombie", False).uuid)

    def test_hostile_mobs_only_never_an_animal(self):
        self.world.add_entity("zombie", (20.5, 64.0, 0.5))
        self.world.add_entity("cow", (21.5, 64.0, 0.5))
        self.world.add_entity("creeper", (22.5, 64.0, 0.5))
        hits = aim.near(world.Pos(21.5, 64.0, 0.5), 4, hostile=True)
        self.assertEqual(sorted(h.name for h in hits), ["Creeper", "Zombie"])
        frag = exclude.mobs("distance=..4", hostile=True)
        self.assertIn("type=!cow", frag)
        self.assertNotIn("type=!zombie", frag)
        self.assertFalse(set(exclude.PEACEFUL_TYPES) & set(exclude.HOSTILE_TYPES))
        longest = "execute positioned 1000.00 64.00 -1000.00 %s run data get entity @s Pos" % exclude.mobs(
            "distance=..30.0", spare=["Steve", "Alex", "Sam"], hostile=True)
        self.assertLess(len(longest), mc.MAX_COMMAND - 200)
        self.assertEqual(aim.near(world.Pos(21.5, 64.0, 0.5), 4, with_mobs=False), [])

    def test_the_ground_height_from_a_marker_with_a_tag_of_its_own(self):
        self.assertEqual(world.ground(10, -3), 64)
        summoned = [c for c in self.sent("summon marker")]
        self.assertEqual(len(summoned), 1)
        tag = summoned[0].split('"')[-2]
        self.assertTrue(tag.startswith(mc.TAG + "_ground"), summoned[0])
        self.assertEqual(self.world.commands[-1], "kill @e[tag=%s]" % tag)
        self.world.solid.add((world.OVERWORLD, 10, 70, -3))
        self.assertEqual(world.ground(10.7, -2.2), 71)
        self.assertEqual([e for e in self.world.entities if e["type"] == "marker"], [])     # killed after
        self.world.unloaded.add((world.OVERWORLD, 6, 6))
        self.assertIsNone(world.ground(100, 100))

    def test_the_ground_marker_goes_whatever_the_reply_and_no_other_marker_does(self):
        other = self.world.add_entity("marker", [10.0, 64.0, -3.0], tags=["vfg_other_anchor"], health=None)
        real = self.world.respond

        def lost(c):
            """The command runs, and its reply is lost on the way (a dropped connection)."""
            reply = real(c)
            return "" if "data get" in c or "summon marker" in c else reply

        self.world.respond = lost
        self.assertIsNone(world.ground(10, -3))
        self.assertEqual([e for e in self.world.entities if e["type"] == "marker"], [other])
        self.world.respond = real
        self.assertEqual(world.ground(10, -3), 64)
        self.assertEqual([e for e in self.world.entities if e["type"] == "marker"], [other])

    def test_at_p_is_the_owner_when_online_else_the_first(self):
        self.world.add_player("Alex", (1, 64, 1))
        self.assertEqual(loop.who("@p"), [loop.online()[0]])
        old, mc.OWNER = mc.OWNER, "Alex"
        try:
            self.assertEqual(loop.who("@p"), ["Alex"])
        finally:
            mc.OWNER = old

    def test_a_boss_knows_its_dimension(self):
        b = mobs.Boss("Ice Queen", "stray", health=50)
        b.spawn(near=world.Pos(0.5, 64, 0.5, "minecraft:the_nether"), spread=3)
        self.assertEqual(b.dim, "minecraft:the_nether")
        c = mobs.Boss("Horde King", "zombie", health=50)
        c.spawn(near="Steve")
        self.assertIsNone(c.dim)


# ---- scripts: two at once, and a stop ------------------------------------------------------

def start_script(name, source, fake, password, instance=None, run_dir=None, vals=None):
    """A script started as the box starts a copy, with the meta file values() reads: vals,
    {name: value} (none live), or a values_meta()."""
    folder = os.path.join(TMP, name)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "main.py"), "w") as f:
        f.write(source)
    meta = vals if vals is None or "values" in vals else {"values": vals, "live": []}
    with open(os.path.join(run_dir or TMP, (instance or name) + ".json"), "w") as f:
        json.dump(meta or {"values": {}, "live": []}, f)
    env = dict(os.environ, PYTHONPATH=LIB, VFG_MC_PROPERTIES=_properties(password, fake.port),
               VFG_RCON_HOST="127.0.0.1", PYTHONDONTWRITEBYTECODE="1", VFG_INSTANCE=instance or name,
               VFG_RUN_DIR=run_dir or TMP, VFG_PROTECTED=os.path.join(TMP, "none.json"))
    return subprocess.Popen([sys.executable, "-u", "main.py"], cwd=folder, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def copied(path):
    """A preset's file as a script on the box holds it: without its values class and its
    import from actions, which only the PC reads (vfg script new --preset leaves them out)."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    drop = set()
    for node in ast.parse(text).body:
        if (isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "actions") or \
                (isinstance(node, ast.ClassDef) and any(getattr(b, "id", None) == "Values" for b in node.bases)):
            drop.update(range(node.lineno, node.end_lineno + 1))
    return "".join(line for n, line in enumerate(text.splitlines(True), 1) if n not in drop)


WAND = '''"""%s: logs each click."""
ASKS = []
from kit import values, items, loop
import mc
v = values()
for p in loop(items.clicks(v["player"], "%s", every=0.05)):
    mc.log("fired", p)
'''


class KitScriptTests(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.world.add_player("Steve", (10.5, 64.0, -3.5), prefix="[Owner] ")
        self.password = secrets.token_hex(8)
        self.fake = FakeRcon(self.password, responder=self.world.respond).start()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait()
        self.fake.stop()

    def test_two_scripts_on_one_player_each_react_to_their_own_item(self):
        a = start_script("alpha", WAND % ("Alpha", "Alpha Wand"), self.fake, self.password, vals={"player": "@a"})
        b = start_script("beta", WAND % ("Beta", "Beta Wand"), self.fake, self.password, vals={"player": "@a"})
        self.procs += [a, b]
        deadline = time.time() + 5
        while len(self.world.gives) < 2 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(len(self.world.gives), 2)
        time.sleep(0.3)
        for held, n in (("alpha_wand", 1), ("beta_wand", 2), ("alpha_wand", 1), ("plain", 3)):
            self.world.hold("Steve", main=held)
            time.sleep(0.25)
            self.world.click("Steve", n)
            time.sleep(0.35)
        for p in (a, b):
            p.send_signal(signal.SIGTERM)
        out_a = a.communicate(timeout=4)[0].decode()
        out_b = b.communicate(timeout=4)[0].decode()
        self.assertEqual(out_a.count("fired Steve"), 2, out_a)
        self.assertEqual(out_b.count("fired Steve"), 1, out_b)
        self.assertNotIn("Traceback", out_a + out_b)
        # Each took its own item back, and neither touched the shared objective.
        self.assertEqual(sorted(p for p, _ in self.world.clears), ["Steve", "Steve"])
        self.assertIn("vfg_click", self.world.objectives)

    def test_a_stop_puts_everything_back_within_two_seconds(self):
        self.world.gamerules["mob_griefing"] = "false"
        src = '''"""Probe: makes one of everything the kit cleans up."""
ASKS = []
from kit import values, items, area, mobs, world, loop
import mc
v = values()
world.gamerule("mob_griefing", True)
world.objective("dummy", "kills")
area.forceload((0, 0, 0, 40, 0, 40), wait=0)
boss = mobs.Boss("Horde King", "zombie", health=800)
boss.spawn(near="Steve")
mobs.around("Steve", "zombie", 2)
mc.on_stop(lambda: mc.log("stop-only cleanup ran"), at_exit=False)
mc.log("ready")
for p in loop(items.clicks(v["player"], "Probe Wand", every=0.05)):
    pass
'''
        proc = start_script("probe", src, self.fake, self.password, vals={"player": "Steve"})
        self.procs.append(proc)
        deadline = time.time() + 6
        while not self.world.gives and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)
        self.assertTrue(self.world.bossbars)
        self.assertTrue(self.world.forced)
        self.assertEqual(self.world.gamerules["mob_griefing"], "true")
        self.assertIn("vfg_probe_kills", self.world.objectives)
        self.assertEqual(len([e for e in self.world.entities if "vfg_probe" in e["tags"]]), 3)
        t = time.time()
        proc.send_signal(signal.SIGTERM)
        out = proc.communicate(timeout=4)[0].decode()
        self.assertLess(time.time() - t, 2.0)
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("Traceback", out)
        self.assertEqual(self.world.bossbars, {})
        self.assertEqual(self.world.forced, set())
        self.assertEqual(self.world.gamerules["mob_griefing"], "false")   # as it was, not "true"
        self.assertNotIn("vfg_probe_kills", self.world.objectives)
        self.assertIn("vfg_click", self.world.objectives)                # shared: stays
        self.assertEqual([e for e in self.world.entities if "vfg_probe" in e["tags"]], [])
        self.assertEqual(self.world.clears, [("Steve", '*[custom_data~{vfg_item:"probe_wand"}]')])
        self.assertEqual(self.world.commands[-1], "kill @e[tag=vfg_probe]")
        self.assertIn("stop-only cleanup ran", out)

    def test_a_script_that_ends_by_itself_keeps_what_it_made_and_undoes_the_rest(self):
        self.world.gamerules["mob_griefing"] = "false"
        src = '''"""Probe: makes what the kit cleans up, then ends."""
ASKS = []
from kit import values, area, mobs, world
import mc
v = values()
world.gamerule("mob_griefing", True)
world.objective("dummy", "kills")
area.forceload((0, 0, 0, 40, 0, 40), wait=0)
mobs.Boss("Horde King", "zombie", health=800).spawn(near="Steve")
mobs.around("Steve", "zombie", 2)
mc.on_stop(lambda: mc.log("own cleanup ran"))
mc.on_stop(lambda: mc.log("stop-only cleanup ran"), at_exit=False)
mc.log("done")
'''
        proc = start_script("ender", src, self.fake, self.password, vals={"player": "Steve"})
        self.procs.append(proc)
        out = proc.communicate(timeout=8)[0].decode()
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("Traceback", out)
        self.assertIn("ended by itself", out)
        self.assertIn("own cleanup ran", out)
        self.assertNotIn("stop-only cleanup ran", out)
        # what was for the while is undone: the bar, the forceload, the gamerule, the objective
        self.assertEqual(self.world.bossbars, {})
        self.assertEqual(self.world.forced, set())
        self.assertEqual(self.world.gamerules["mob_griefing"], "false")
        self.assertNotIn("vfg_ender_kills", self.world.objectives)
        # what it made stays: the boss's body and the two zombies, and no kill was sent; they are
        # handed over to vfg_left_ender, so a later copy named ender never takes them with its stop
        self.assertEqual(sorted(e["type"] for e in self.world.entities if "vfg_left_ender" in e["tags"]),
                         ["zombie", "zombie", "zombie"])
        self.assertEqual([e for e in self.world.entities if "vfg_ender" in e["tags"]], [])
        self.assertEqual([c for c in self.world.commands if c.startswith("kill")], [])
        self.assertEqual(self.world.commands[-2:], ["tag @e[tag=vfg_ender] add vfg_left_ender",
                                                    "tag @e[tag=vfg_ender] remove vfg_ender"])

    def test_a_one_shot_spawner_leaves_its_mobs_when_it_ends(self):
        src = '''"""Spiders: 39 giant spiders round a player, then it is done."""
ASKS = ["spawn 39 giant spiders"]
from kit import values, mobs
import mc
v = values()
n = mobs.around(v["player"], "spider", 38, 'attributes:[{id:"scale",base:3}]', spread=12, tags=("spiders",))
n += 1 if mc.spawn(v["player"], "spider", 'attributes:[{id:"scale",base:3}]') else 0
mc.log("summoned", n)
'''
        proc = start_script("spiders", src, self.fake, self.password, vals={"player": "Steve"})
        self.procs.append(proc)
        out = proc.communicate(timeout=10)[0].decode()
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("Traceback", out)
        self.assertIn("summoned 39", out)
        self.assertIn("ended by itself", out)
        time.sleep(0.2)
        spiders = [e for e in self.world.entities if e["type"] == "spider" and "vfg_left_spiders" in e["tags"]]
        self.assertEqual(len(spiders), 39)
        self.assertEqual([c for c in self.world.commands if c.startswith("kill")], [])

    def test_a_script_that_fails_cleans_up_as_a_stopped_one(self):
        src = '''"""Spiders: 3 spiders round a player, then a bug."""
ASKS = []
from kit import values, mobs
import mc
v = values()
mobs.around(v["player"], "spider", 3)
mc.on_stop(lambda: mc.log("stop-only cleanup ran"), at_exit=False)
oops()
'''
        proc = start_script("broken", src, self.fake, self.password, vals={"player": "Steve"})
        self.procs.append(proc)
        out = proc.communicate(timeout=10)[0].decode()
        self.assertIn("NameError", out)
        self.assertIn("failed, cleaning up as a stop does", out)
        self.assertIn("stop-only cleanup ran", out)
        self.assertEqual([e for e in self.world.entities if "vfg_broken" in e["tags"]], [])
        self.assertEqual(self.world.commands[-1], "kill @e[tag=vfg_broken]")

    def test_ice_shells_still_melt_when_a_script_ends_by_itself(self):
        preset = copied(os.path.join(os.path.dirname(HERE), "_presets", "freeze.py"))
        src = ('''"""Ice Probe: freezes a zombie in a shell of ice for a minute, then it is done."""
ASKS = []
from kit import aim
import mc
''' + preset.split("\n", 1)[1] + '''
hits = aim.near((14.5, 64.0, -3.5), 2)
n = freeze(hits, {"freeze_seconds": 60, "freeze_shell": True, "freeze_block": "packed_ice", "freeze_blindness": False})
mc.log("frozen", n)
''')
        zombie = self.world.add_entity("zombie", (14.5, 64.0, -3.5))
        proc = start_script("iceprobe", src, self.fake, self.password)
        self.procs.append(proc)
        t = time.time()
        out = proc.communicate(timeout=10)[0].decode()
        self.assertLess(time.time() - t, 5.0)           # it did not wait out the minute
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("Traceback", out)
        self.assertIn("frozen 1", out)
        self.assertIn("ended by itself", out)
        put = [c for c in self.world.commands if c.startswith("fill") and c.endswith("packed_ice keep")]
        melted = [c for c in self.world.commands if c.startswith("fill") and c.endswith("air replace packed_ice")]
        self.assertEqual(len(put), 6, self.world.commands)
        self.assertEqual(len(melted), 6, self.world.commands)
        self.assertIn(zombie, self.world.entities)     # not the script's: it stays as it was


if __name__ == "__main__":
    unittest.main(verbosity=2)
