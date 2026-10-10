"""The ongoing presets, and the presets that watch players, on a real Minecraft 26.3 server.

    integration/realmc/up po && eval "$(integration/realmc/env po)"
    uv run --python 3.9 python scripts/_test/live_presets_ongoing.py

Each preset is wired into a minimal script (its own file with a line or two under it, as
`vfg script new --preset` makes one) and run the way the box runs a script; then the world is
asked what it did, with RCON. It runs only against a realmc test server, never the box. The
mobs and blocks it makes are taken away again. Without Carpet (fake players) the boss, the
beam clash and the director's events run; with it, every preset, with Steve and Alex.
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
REALMC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "integration", "realmc")
sys.path.insert(0, LIB)
sys.path.insert(0, REALMC)

PROPS = os.environ.get("VFG_MC_PROPERTIES", "")
if not os.environ.get("REALMC_NAME") or "realmc" not in os.path.realpath(PROPS):
    sys.exit("live_presets_ongoing: point it at a realmc server first: eval \"$(integration/realmc/env po)\"")
os.environ["VFG_INSTANCE"] = "lpresets"         # this process's own tag, for the director's events

import mc  # noqa: E402
from kit import world  # noqa: E402
from test_kit import copied  # noqa: E402  (only the helper: mc stays on the real server)
import players as fake_players  # noqa: E402

signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)

PLAYERS = fake_players.on()
ARENA = PLAYERS.arena()
X, Y, Z = int(ARENA["x"]), int(ARENA["y"]), int(ARENA["z"])     # where a player stands at its centre
SKY = Y + 40                                                    # the clash and the stand-in are up here
TMP = tempfile.mkdtemp(prefix="vfg-live-ongoing-")
RUN = os.path.join(TMP, "run")                                  # the run directory the scripts share
os.makedirs(RUN)
try:
    CARPET = "Unknown or incomplete" not in PLAYERS.cmd("carpet")
except Exception:
    CARPET = False


def script(preset, wiring):
    """A preset's file as a script holds it once copied in, with the wiring under it."""
    text = copied(os.path.join(PRESETS, preset + ".py"))
    return (text.replace("import mc\n", "import mc\nimport sys\nfrom kit import values, aim, loop, world\n", 1)
            + "\n\nv = values(VALUES)\n" + wiring)


def start(preset, instance, wiring, *argv):
    """Run it as the box does: python3 -u main.py key=value ..., VFG_INSTANCE=<instance>."""
    folder = os.path.join(TMP, instance)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "main.py"), "w") as f:
        f.write(script(preset, wiring))
    env = dict(os.environ, PYTHONPATH=LIB, VFG_INSTANCE=instance, VFG_RUN_DIR=RUN, PYTHONDONTWRITEBYTECODE="1",
               VFG_PROTECTED=os.path.join(TMP, "none.json"))
    return subprocess.Popen([sys.executable, "-u", "main.py"] + list(argv), cwd=folder, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def until(test, seconds=10.0):
    end = time.time() + seconds
    got = test()
    while not got and time.time() < end:
        time.sleep(0.2)
        got = test()
    return got


def number(reply):
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*$", reply.strip())
    return float(m.group(1)) if m else None


def gone(instance):
    """Nothing tagged vfg_<instance> is left, and no bar of its."""
    return ("Test failed" in mc.rcon("execute if entity @e[tag=vfg_%s]" % instance)
            and "No bossbar" in mc.rcon("bossbar get vfg:%s_boss1 max" % instance))


class Live(unittest.TestCase):
    def setUp(self):
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait()
        # What the fights dropped: rotten flesh, experience, the boss's loot.
        mc.rcon("kill @e[type=item,x=%d,y=%d,z=%d,distance=..160]" % (X, Y, Z))
        mc.rcon("kill @e[type=experience_orb,x=%d,y=%d,z=%d,distance=..160]" % (X, Y, Z))

    def run_(self, preset, instance, wiring, *argv):
        p = start(preset, instance, wiring, *argv)
        self.procs.append(p)
        return p

    def stop(self, proc):
        t = time.time()
        proc.send_signal(signal.SIGTERM)
        out = proc.communicate(timeout=6)[0].decode()
        took = time.time() - t
        self.assertLess(took, 2.0, out)
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("Traceback", out)
        return out


# ---- without players -----------------------------------------------------------------------------

class LiveBoss(Live):
    WIRING = 'mc.log("result", boss(world.Pos(%.2f, %.2f, %.2f), v))\n' % (X + 0.5, Y, Z + 0.5)

    def test_a_boss_of_two_bodies_fought_to_the_death(self):
        inst, tag = "lpboss", "vfg_lpboss_boss1"
        proc = self.run_("boss", inst, self.WIRING, "boss_bodies=2", "boss_health=40", "boss_mob=husk",
                         "boss_minion=husk", "boss_every=2", "boss_minions=2", "boss_phases=0.5",
                         "boss_reward_count=5", "boss_scale=1.5", "boss_attack=4")
        self.assertTrue(until(lambda: mc.count("@e[tag=%s]" % tag) == 2, 20))
        self.assertTrue(until(lambda: number(mc.rcon("bossbar get vfg:%s max" % tag[4:])) == 80))
        self.assertIn("netherite_helmet", mc.rcon("data get entity @e[tag=%s_0,limit=1] equipment" % tag))
        self.assertIn("is 4.0", mc.rcon("execute as @e[tag=%s_0,limit=1] run attribute @s attack_damage base get" % tag))
        mc.rcon("damage @e[tag=%s_0,limit=1] 15 minecraft:magic" % tag)
        self.assertTrue(until(lambda: number(mc.rcon("bossbar get vfg:%s value" % tag[4:])) == 65),
                        mc.rcon("bossbar get vfg:%s value" % tag[4:]))
        self.assertTrue(until(lambda: mc.count("@e[tag=%s_minion]" % tag) >= 1, 8), "no minions")
        self.assertNotIn("speed", mc.rcon("data get entity @e[tag=%s_0,limit=1] active_effects" % tag))
        mc.rcon("damage @e[tag=%s_1,limit=1] 30 minecraft:magic" % tag)      # 35 of 80: below half
        self.assertTrue(until(lambda: "minecraft:speed" in mc.rcon(
            "data get entity @e[tag=%s_0,limit=1] active_effects" % tag), 6), "it did not enrage")
        mc.rcon("execute as @e[tag=%s] run damage @s 100 minecraft:magic" % tag)
        out = proc.communicate(timeout=20)[0].decode()
        print("\n" + out)
        self.assertEqual(proc.returncode, 0, out)
        self.assertEqual(out.count("result True"), 1, out)                    # the death check fired once
        self.assertEqual(mc.count("@e[type=item,x=%d,y=%d,z=%d,distance=..12,nbt={Item:{id:\"minecraft:diamond\",count:5}}]"
                                  % (X, Y, Z)), 1, "no loot where it died")
        self.assertEqual(mc.count("@e[tag=%s_minion]" % tag), 0)
        self.assertTrue(gone(inst))

    def test_three_dragons_one_bar_of_their_summed_health(self):
        inst, tag = "lpdragon", "vfg_lpdragon_boss1"
        old = world.gamerule("mob_griefing")
        proc = self.run_("boss", inst, 'mc.log("result", boss(world.Pos(%.2f, %.2f, %.2f), v))\n' % (X + 0.5, SKY + 30, Z + 0.5),
                         "boss_name=Three-Headed Dragon", "boss_mob=ender_dragon", "boss_bodies=3", "boss_health=600",
                         "boss_scale=1", "boss_gear=none", "boss_minions=0", "boss_griefing=false")
        self.assertTrue(until(lambda: mc.count("@e[type=ender_dragon,tag=%s]" % tag) == 3, 20))
        self.assertTrue(until(lambda: number(mc.rcon("bossbar get vfg:%s max" % tag[4:])) == 1800))
        self.assertEqual(world.gamerule("mob_griefing"), "false")
        mc.rcon("kill @e[tag=%s_1]" % tag)                                    # one head down
        self.assertTrue(until(lambda: number(mc.rcon("bossbar get vfg:%s value" % tag[4:])) == 1200, 8),
                        mc.rcon("bossbar get vfg:%s value" % tag[4:]))
        self.assertIsNone(proc.poll())
        mc.rcon("kill @e[tag=%s]" % tag)
        out = proc.communicate(timeout=20)[0].decode()
        print("\n" + out)
        self.assertEqual(out.count("result True"), 1, out)
        self.assertEqual(world.gamerule("mob_griefing"), old)
        self.assertTrue(until(lambda: gone(inst), 15))                         # a dragon takes 10 s to die

    def test_a_boss_stopped_mid_fight_leaves_nothing(self):
        inst = "lpboss2"
        old = world.gamerule("mob_griefing")
        proc = self.run_("boss", inst, self.WIRING, "boss_mob=husk", "boss_minion=husk", "boss_every=1",
                         "boss_griefing=false", "boss_music=pigstep", "boss_health=100")
        self.assertTrue(until(lambda: mc.count("@e[tag=vfg_%s_boss1_minion]" % inst) >= 1, 20))
        self.assertEqual(world.gamerule("mob_griefing"), "false")
        self.stop(proc)
        self.assertTrue(gone(inst))
        self.assertEqual(world.gamerule("mob_griefing"), old)


class LiveClash(Live):
    """Two scripts, two shooters, one shared file: no players, so every beam waits for an
    answer (beam_clash_aimed=false) and neither pushes; the point wanders, then explodes."""
    EYES = SKY + 0.5
    WIRING = ('x, dx = (%.2f, 1.0) if sys.argv[-1] == "Steve" else (%.2f, -1.0)\n'
              'mc.log("result", beam_clash(sys.argv[-1], aim.Ray(world.Pos(x, %.2f, %.2f), (dx, 0.0, 0.0), 20), v))\n'
              % (X - 9.5, X + 10.5, SKY + 0.5, Z + 0.5))

    def test_two_scripts_clash_and_it_explodes_where_the_point_stopped(self):
        mc.rcon("fill %d %d %d %d %d %d glass" % (X - 7, SKY, Z, X + 8, SKY, Z))
        try:
            args = ("beam_clash_aimed=false", "beam_clash_window=6", "beam_clash_seconds=3", "beam_clash_blast=2")
            a = self.run_("beam_clash", "lpclash-a", self.WIRING, *(args + ("Steve",)))
            time.sleep(1.0)
            b = self.run_("beam_clash", "lpclash-b", self.WIRING, *(args + ("Alex",)))
            out_a, out_b = a.communicate(timeout=25)[0].decode(), b.communicate(timeout=25)[0].decode()
            print("\n" + out_a + out_b)
            results = re.findall(r"result \((True|False), (Pos\([^)]*\))\)", out_a + out_b)
            self.assertEqual(sorted(r[0] for r in results), ["False", "True"], out_a + out_b)
            self.assertEqual(results[0][1], results[1][1])
            self.assertEqual((out_a + out_b).count("beam clash:"), 1)
            broken = [x for x in range(X - 7, X + 9) if "passed" in mc.rcon("execute if block %d %d %d air" % (x, SKY, Z)).lower()]
            self.assertTrue(broken, "the explosion broke no glass")
            print("  glass broken at x =", broken)
            self.assertTrue(gone("lpclash-a") and gone("lpclash-b"))
            self.assertTrue(os.path.exists(os.path.join(RUN, "beam_clash.json")))
        finally:
            mc.rcon("fill %d %d %d %d %d %d air" % (X - 7, SKY - 3, Z - 3, X + 8, SKY + 3, Z + 3))

    def test_a_clash_stopped_mid_struggle(self):
        args = ("beam_clash_aimed=false", "beam_clash_window=6", "beam_clash_seconds=20")
        a = self.run_("beam_clash", "lpclash-c", self.WIRING, *(args + ("Steve",)))
        time.sleep(1.0)
        b = self.run_("beam_clash", "lpclash-d", self.WIRING, *(args + ("Alex",)))
        time.sleep(3.0)
        out = self.stop(a) + self.stop(b)
        self.assertIn("beam clash:", out)
        self.assertNotIn("result", out)
        self.assertTrue(gone("lpclash-c") and gone("lpclash-d"))


def load(name):
    spec = importlib.util.spec_from_file_location("preset_" + name, os.path.join(PRESETS, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class LiveDirectorEvents(Live):
    """The director's shadow and fake mob, behind an armor stand that stands in for a player."""

    def setUp(self):
        super().setUp()
        self.d = load("director")
        mc.rcon('summon armor_stand %d.5 %d %d.5 {Tags:["vfg_lpstandin"],NoGravity:1b,Invisible:1b,Rotation:[-90f,0f]}'
                % (X, SKY + 1, Z))
        self.p = "@e[tag=vfg_lpstandin,limit=1]"
        from kit import values
        self.v = values(self.d.VALUES, argv=[], run_dir=RUN, instance="lpresets")

    def tearDown(self):
        mc.rcon("kill @e[tag=vfg_lpstandin]")
        mc.rcon("kill @e[tag=%s]" % mc.TAG)
        super().tearDown()

    def test_a_shadow_behind_them_then_gone(self):
        th = threading.Thread(target=self.d._director_shadow, args=(self.p, self.v))
        th.start()
        where = "x=%d.5,y=%d,z=%d.5,distance=..1" % (X - 12, SKY + 1, Z)     # it looks along +x: behind is -x
        self.assertTrue(until(lambda: mc.count("@e[type=armor_stand,tag=%s,%s]" % (mc.TAG, where)) == 1, 5))
        self.assertIn("player_head", mc.rcon("data get entity @e[type=armor_stand,tag=%s,limit=1] equipment" % mc.TAG))
        self.assertRegex(mc.rcon("data get entity @e[type=armor_stand,tag=%s,limit=1] Rotation" % mc.TAG), r"\[-90\.0f")
        th.join(10)
        self.assertEqual(mc.count("@e[tag=%s]" % mc.TAG), 0)

    def test_a_fake_creeper_that_does_nothing(self):
        th = threading.Thread(target=self.d._director_mob, args=(self.p, self.v))
        th.start()
        self.assertTrue(until(lambda: mc.count("@e[type=creeper,tag=%s]" % mc.TAG) == 1, 5))
        self.assertIn("1b", mc.rcon("data get entity @e[type=creeper,tag=%s,limit=1] NoAI" % mc.TAG))
        th.join(5)
        self.assertEqual(mc.count("@e[tag=%s]" % mc.TAG), 0)


# ---- with Carpet's fake players -------------------------------------------------------------------

@unittest.skipUnless(CARPET, "the server has no Carpet: integration/realmc/realmc.py carpet, then up --fresh")
class LiveWithPlayers(Live):
    def setUp(self):
        super().setUp()
        PLAYERS.spawn("Steve", (X + 0.5, Y, Z + 0.5), facing=(-90, 0), gamemode="creative")
        PLAYERS.clear("Steve")

    def tearDown(self):
        PLAYERS.kill_all()
        super().tearDown()

    def near(self, name, x, z, d=1.5):
        at = PLAYERS.pos(name)
        return abs(at[0] - x) <= d and abs(at[2] - z) <= d

    def test_a_horde_round_each_player_then_it_waits_for_them_to_die(self):
        PLAYERS.spawn("Alex", (X + 6.5, Y, Z + 0.5), facing=(90, 0), gamemode="creative")
        inst = "lphorde"
        proc = self.run_("horde_wave", inst, 'mc.log("waves", horde_wave("@a", v))\n', "horde_mobs=husk",
                         "horde_per_player=2", "horde_every=2", "horde_cap=6", "horde_waves=2", "horde_spread=6")
        horde = "@e[tag=vfg_%s_horde]" % inst
        self.assertTrue(until(lambda: mc.count(horde) == 4, 15))
        for p in ("Steve", "Alex"):
            self.assertGreaterEqual(mc.count("@e[tag=vfg_%s_horde,distance=..16,x=%.1f,y=%d,z=%.1f]"
                                             % ((inst,) + tuple(PLAYERS.pos(p)))), 2)
        self.assertTrue(until(lambda: mc.count(horde) == 6, 6))
        time.sleep(2.5)
        self.assertEqual(mc.count(horde), 6)                                  # the cap
        self.assertIsNone(proc.poll())                                        # it waits for them to die
        mc.rcon("kill %s" % horde)
        out = proc.communicate(timeout=10)[0].decode()
        self.assertIn("waves 2", out)
        self.assertTrue(gone(inst))

    def test_a_horde_stopped(self):
        inst = "lphorde2"
        proc = self.run_("horde_wave", inst, 'horde_wave("@a", v)\n', "horde_mobs=husk", "horde_every=1")
        self.assertTrue(until(lambda: mc.count("@e[tag=vfg_%s_horde]" % inst) >= 4, 15))
        self.stop(proc)
        self.assertTrue(gone(inst))

    def test_the_director_on_a_player(self):
        inst = "lpdirector"
        proc = self.run_("director", inst, 'director("Steve", v)\n', "director_events=shadow,dark",
                         "director_weights=1,1", "director_min_s=1", "director_max_s=1")
        self.assertTrue(until(lambda: mc.count("@e[type=armor_stand,tag=vfg_%s]" % inst) >= 1, 15), "no shadow")
        self.assertTrue(until(lambda: "darkness" in mc.rcon("data get entity Steve active_effects"), 15), "no dark")
        out = self.stop(proc)
        self.assertIn("event shadow for Steve", out)
        self.assertTrue(gone(inst))
        self.assertNotIn("darkness", mc.rcon("data get entity Steve active_effects"))     # lifted on the stop

    def test_an_arena_sends_back_anyone_who_leaves_it(self):
        proc = self.run_("leaves_area", "lparena",
                         'for p, back in loop(leaves_area("Steve", v)):\n'
                         '    mc.rcon(world.run(back.dim, "tp %s %.2f %.2f %.2f" % ((p,) + tuple(back))))\n'
                         '    mc.log("sent back", p)\n', "leaves_radius=5")
        time.sleep(2.0)
        PLAYERS.tp("Steve", X + 12.5, Y, Z + 0.5)
        self.assertTrue(until(lambda: self.near("Steve", X + 0.5, Z + 0.5), 5), PLAYERS.pos("Steve"))
        out = self.stop(proc)
        self.assertEqual(out.count("sent back Steve"), 1, out)

    def test_a_tether_keeps_one_player_near_another(self):
        PLAYERS.spawn("Alex", (X + 3.5, Y, Z + 0.5), facing=(90, 0), gamemode="creative")
        proc = self.run_("leaves_area", "lptether",
                         'for p, back in loop(leaves_area("Steve", v)):\n'
                         '    mc.rcon(world.run(back.dim, "tp %s %.2f %.2f %.2f" % ((p,) + tuple(back))))\n'
                         '    mc.log("sent back", p)\n', "leaves_area=player", "leaves_of_player=Alex", "leaves_radius=6")
        time.sleep(2.0)
        PLAYERS.tp("Steve", X - 12.5, Y, Z + 0.5)
        self.assertTrue(until(lambda: self.near("Steve", X + 3.5, Z + 0.5), 5), PLAYERS.pos("Steve"))
        self.stop(proc)

    def test_standing_still_too_long(self):
        proc = self.run_("stands_still", "lpcamp", 'for p in loop(stands_still("Steve", v)):\n    mc.log("camping", p)\n',
                         "stands_seconds=3", "stands_radius=2")
        time.sleep(4.2)
        for i in range(10):                                                   # then it walks: never
            PLAYERS.tp("Steve", X + 0.5 + (3 if i % 2 == 0 else -3), Y, Z + 0.5)
            time.sleep(0.4)
        out = self.stop(proc)
        self.assertEqual(out.count("camping Steve"), 1, out)

    def test_two_players_clash_and_the_one_who_holds_right_click_wins(self):
        PLAYERS.spawn("Steve", (X - 9.5, Y, Z + 0.5), facing=(-90, 0), gamemode="creative")
        PLAYERS.spawn("Alex", (X + 10.5, Y, Z + 0.5), facing=(90, 0), gamemode="creative")
        PLAYERS.give("Steve", "carrot_on_a_stick")
        wiring = ('p = sys.argv[-1]\n'
                  'mc.log("result", beam_clash(p, aim.ray(p, 25, blocks=False), v))\n')
        args = ("beam_clash_window=4", "beam_clash_seconds=6", "beam_clash_blast=0")
        a = self.run_("beam_clash", "lpbeam-a", wiring, *(args + ("Steve",)))
        time.sleep(1.0)
        PLAYERS.use_continuous("Steve")
        b = self.run_("beam_clash", "lpbeam-b", wiring, *(args + ("Alex",)))
        try:
            out = a.communicate(timeout=20)[0].decode() + b.communicate(timeout=20)[0].decode()
        finally:
            PLAYERS.stop("Steve")
        print("\n" + out)
        self.assertIn("lpbeam-a result (True", out)
        self.assertIn("lpbeam-b result (False", out)
        self.assertIn("beam clash won by Steve", out)

    def test_a_boss_near_a_player(self):
        proc = self.run_("boss", "lpboss3", 'boss("Steve", v)\n', "boss_mob=husk", "boss_every=60")
        self.assertTrue(until(lambda: mc.count("@e[tag=vfg_lpboss3_boss1,x=%d,y=%d,z=%d,distance=..16]" % (X, Y, Z)) == 1, 15))
        self.stop(proc)
        self.assertTrue(gone("lpboss3"))


if __name__ == "__main__":
    print("realmc %s: arena at %d %d %d; Carpet %s" % (os.environ.get("REALMC_NAME"), X, Y, Z, "yes" if CARPET else "no"))
    unittest.main(verbosity=2)
